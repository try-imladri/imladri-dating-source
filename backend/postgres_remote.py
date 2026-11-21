"""Signed private repository connection from Cloudflare to the Render data plane.

No database credentials or provider API credentials leave Render. Retries retain
the same request UUID/body. PostgreSQL deduplication prevents a second mutation;
an unavailable committed document image is an explicit uncertain-outcome error.
"""
import asyncio
from copy import deepcopy
import hashlib
import hmac
import os
import time
from urllib.parse import urlsplit
from uuid import uuid4

from bson import BSON
import httpx
from pymongo import ReturnDocument
from pymongo.errors import BulkWriteError, DuplicateKeyError, OperationFailure
from pymongo.results import BulkWriteResult, DeleteResult, InsertManyResult, InsertOneResult, UpdateResult

from .postgres_codec import validate_name
from .postgres_store import RepositoryUnavailable


LIMIT = 16 * 1024 * 1024
# Includes waiting calls: enough headroom for the app's nested report fanout.
# Only two admitted calls encode BSON or retain an HTTP response at a time.
MAX_PENDING_REQUESTS = 128
QUEUE_TIMEOUT_SECONDS = 30
BUSY_MAX_RETRIES = 3
BUSY_RETRY_SECONDS = 3


class RemotePostgresDatabase:
    backend = "postgresql"
    transport = "render_https"

    def __init__(self, url, secret, origin_secret, namespace, *, transport=None):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("The Render repository requires an HTTPS origin.")
        if not parsed.hostname or not parsed.hostname.endswith(".onrender.com"):
            raise ValueError("Use the verified Render repository hostname.")
        if len(secret.encode()) < 32 or not origin_secret:
            raise ValueError("Private repository credentials are missing.")
        self.url = url.rstrip("/") + "/api/internal/postgres"
        self.secret, self.origin_secret, self.name = secret.encode(), origin_secret, validate_name(namespace)
        self._verified = False
        self._verify_lock = asyncio.Lock()
        self._send_slots = asyncio.Semaphore(2)
        self._pending_requests = 0
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(35, connect=8), follow_redirects=False,
                                      transport=transport, limits=httpx.Limits(max_connections=8, max_keepalive_connections=4))

    @classmethod
    def from_environment(cls):
        return cls(os.environ["IMLADRI_POSTGRES_GATEWAY_URL"], os.environ["IMLADRI_POSTGRES_GATEWAY_SECRET"],
                   os.environ["RENDER_ORIGIN_SECRET"], os.environ["IMLADRI_POSTGRES_NAMESPACE"])

    def __getitem__(self, name):
        return RemoteCollection(self, validate_name(name))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    async def _verify_target(self):
        async with self._verify_lock:
            if not self._verified:
                result = await self._send("ping")
                if not isinstance(result, dict) or result.get("ok") != 1 or result.get("namespace") != self.name or result.get("backend") != "postgresql":
                    raise RepositoryUnavailable("The Render repository does not match the configured application namespace.")
                self._verified = True

    async def call(self, operation, collection=None, **arguments):
        await self._verify_target()
        if operation == "ping":
            # Ping always probes the provider connection, even after handshake.
            result = await self._send("ping")
            if result.get("namespace") != self.name or result.get("backend") != "postgresql":
                self._verified = False
                raise RepositoryUnavailable("The Render repository namespace changed.")
            return result
        return await self._send(operation, collection, **arguments)

    async def _send(self, operation, collection=None, **arguments):
        # Match the server's two-request admission limit before encoding a body.
        # Handshake calls use this same physical-send gate without nesting it.
        if self._pending_requests >= MAX_PENDING_REQUESTS:
            raise RepositoryUnavailable("Repository request queue is full.")
        self._pending_requests += 1
        acquired = False
        try:
            try:
                await asyncio.wait_for(self._send_slots.acquire(), QUEUE_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                raise RepositoryUnavailable("Repository request queue did not become available.") from None
            acquired = True
            return await self._send_admitted(operation, collection, **arguments)
        finally:
            if acquired:
                self._send_slots.release()
            self._pending_requests -= 1

    async def _send_admitted(self, operation, collection=None, **arguments):
        request = {"version": 1, "operation": operation, "arguments": arguments}
        if collection is not None:
            request["collection"] = collection
        body = BSON.encode(request)
        if len(body) > LIMIT:
            raise RepositoryUnavailable("Repository request exceeds the allowed size.")
        request_id = str(uuid4())
        digest = hashlib.sha256(body).hexdigest()
        headers, transport_retries, busy_retries = None, 0, 0
        busy_deadline = time.monotonic() + BUSY_RETRY_SECONDS
        while True:
            timestamp = str(int(time.time()))
            if headers is None or abs(int(timestamp) - int(headers["X-Imladri-PG-Timestamp"])) >= 30:
                signature = hmac.new(self.secret, ("v1\n" + timestamp + "\n" + request_id + "\n" + digest).encode(), hashlib.sha256).hexdigest()
                headers = {"Content-Type": "application/bson", "X-Imladri-Origin": self.origin_secret,
                           "X-Imladri-PG-Timestamp": timestamp, "X-Imladri-PG-Request-Id": request_id,
                           "X-Imladri-PG-Signature": signature}
            try:
                async with self.http.stream("POST", self.url, content=body, headers=headers) as response:
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > LIMIT:
                            raise RepositoryUnavailable("Repository response exceeds the allowed size.")
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    if response.status_code not in (200, 400, 409, 413, 503) or response.headers.get("content-type", "").split(";", 1)[0] != "application/bson":
                        raise RepositoryUnavailable("Private Render repository did not accept the request.")
                    try:
                        result = BSON(raw).decode()
                    except Exception:
                        raise RepositoryUnavailable("Private Render repository returned an invalid response.") from None
                    if result.get("ok") is True and response.status_code == 200:
                        return result.get("result")
                    error = result.get("error", {})
                    if not isinstance(error, dict):
                        raise RepositoryUnavailable("Private Render repository returned an invalid response.")
                    kind = error.get("kind")
                    if (response.status_code == 503 and set(result) == {"ok", "error"}
                            and result["ok"] is False and error == {"kind": "repository_busy"}
                            and BSON.encode(result) == raw):
                        # This exact server response is admission-before-execution.
                        # Retain the UUID/body; never retry an unknown 503 outcome.
                        delay = 0.15 * (2 ** busy_retries)
                        if busy_retries >= BUSY_MAX_RETRIES or time.monotonic() + delay >= busy_deadline:
                            raise RepositoryUnavailable("Private repository admission remained busy.")
                        busy_retries += 1
                        await asyncio.sleep(delay)
                        continue
                    if kind == "duplicate_key":
                        raise DuplicateKeyError("A repository unique key already exists.", 11000)
                    if kind == "bulk_write":
                        raise BulkWriteError(error.get("details", {"writeErrors": [{"code": 11000, "errmsg": "Repository bulk write failed."}]}))
                    if kind == "invalid_operation":
                        raise OperationFailure("Private repository rejected an unsupported operation.")
                    raise RepositoryUnavailable("Private repository outcome is unavailable; refresh before retrying.")
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                if transport_retries:
                    raise RepositoryUnavailable("Private Render repository connection did not complete.") from None
                transport_retries += 1
                await asyncio.sleep(0.15)

    async def command(self, command, *args, **kwargs):
        if command != "ping" or args or kwargs:
            raise OperationFailure("Unsupported repository command.")
        return await self.call("ping")

    async def aclose(self):
        await self.http.aclose()


class RemoteCursor:
    def __init__(self, collection, query=None, projection=None, pipeline=None):
        self.collection = collection
        self.arguments = {"filter": deepcopy(query or {})}
        if projection is not None:
            self.arguments["projection"] = deepcopy(projection)
        self.pipeline = deepcopy(pipeline)
        self.rows, self.position, self.closed = None, 0, False

    def sort(self, key, direction=None):
        self.arguments["sort"] = list(key.items()) if isinstance(key, dict) else ([(key, direction or 1)] if isinstance(key, str) else list(key))
        return self

    def limit(self, count):
        if type(count) is not int:
            raise TypeError("limit requires an integer.")
        self.arguments["limit"] = abs(count)
        return self

    def skip(self, count):
        if type(count) is not int or count < 0:
            raise ValueError("skip requires a nonnegative integer.")
        self.arguments["skip"] = count
        return self

    def batch_size(self, size):
        if type(size) is not int or size < 0:
            raise ValueError("batch size requires a nonnegative integer.")
        return self

    def max_time_ms(self, milliseconds):
        if type(milliseconds) is not int or milliseconds < 0:
            raise ValueError("max time requires a nonnegative integer.")
        return self

    async def to_list(self, length=None):
        if length is not None and (type(length) is not int or length < 0):
            raise ValueError("to_list length requires a nonnegative integer or None.")
        if self.rows is None and not self.closed:
            if self.pipeline is not None:
                self.rows = await self.collection.database.call("aggregate", self.collection.name, pipeline=self.pipeline)
            else:
                self.rows = await self.collection.database.call("find", self.collection.name, **self.arguments)
        self.rows = self.rows or []
        end = len(self.rows) if length is None else self.position + length
        result = deepcopy(self.rows[self.position:end])
        self.position += len(result)
        return result

    def __aiter__(self):
        return self

    async def __anext__(self):
        rows = await self.to_list(1)
        if not rows:
            raise StopAsyncIteration
        return rows[0]

    async def close(self):
        self.closed, self.rows = True, []


class RemoteCollection:
    def __init__(self, database, name):
        self.database, self.name = database, name

    def find(self, filter=None, projection=None, **kwargs):
        cursor = RemoteCursor(self, filter, projection)
        for option, value in kwargs.items():
            if option not in ("sort", "limit", "skip"):
                raise OperationFailure("Unsupported repository find option.")
            getattr(cursor, option)(value)
        return cursor

    async def find_one(self, filter=None, projection=None, **kwargs):
        if filter is not None and not isinstance(filter, dict):
            filter = {"_id": filter}
        rows = await self.find(filter, projection, **kwargs).limit(1).to_list(1)
        return rows[0] if rows else None

    def aggregate(self, pipeline, **kwargs):
        if set(kwargs) - {"allowDiskUse", "maxTimeMS", "batchSize"}:
            raise OperationFailure("Unsupported repository aggregate option.")
        return RemoteCursor(self, pipeline=pipeline)

    async def count_documents(self, filter, **kwargs):
        return await self.database.call("count", self.name, filter=filter, **kwargs)

    async def distinct(self, key, filter=None):
        return await self.database.call("distinct", self.name, key=key, filter=filter or {})

    async def insert_one(self, document):
        from bson import ObjectId
        document.setdefault("_id", ObjectId())
        result = await self.database.call("insert_one", self.name, document=document)
        return InsertOneResult(result["inserted_id"], True)

    async def insert_many(self, documents, ordered=True):
        from bson import ObjectId
        for document in documents:
            document.setdefault("_id", ObjectId())
        result = await self.database.call("insert_many", self.name, documents=documents, ordered=ordered)
        return InsertManyResult(result["inserted_ids"], True)

    async def update_one(self, filter, update, upsert=False):
        return UpdateResult(await self.database.call("update_one", self.name, filter=filter, update=update, upsert=upsert), True)

    async def update_many(self, filter, update, upsert=False):
        return UpdateResult(await self.database.call("update_many", self.name, filter=filter, update=update, upsert=upsert), True)

    async def replace_one(self, filter, replacement, upsert=False):
        return UpdateResult(await self.database.call("replace_one", self.name, filter=filter, replacement=replacement, upsert=upsert), True)

    async def find_one_and_update(self, filter, update, projection=None, sort=None, upsert=False, return_document=ReturnDocument.BEFORE, **kwargs):
        if kwargs:
            raise OperationFailure("Unsupported repository find-and-update option.")
        args = {"filter": filter, "update": update, "upsert": upsert, "return_document": bool(return_document)}
        if projection is not None:
            args["projection"] = projection
        if sort is not None:
            args["sort"] = sort
        return await self.database.call("find_one_and_update", self.name, **args)

    async def delete_one(self, filter):
        return DeleteResult(await self.database.call("delete_one", self.name, filter=filter), True)

    async def delete_many(self, filter):
        return DeleteResult(await self.database.call("delete_many", self.name, filter=filter), True)

    async def create_indexes(self, models):
        return await self.database.call("create_indexes", self.name, models=[dict(model.document) for model in models])

    async def create_index(self, keys, **options):
        from pymongo import IndexModel
        return (await self.create_indexes([IndexModel(keys, **options)]))[0]

    async def bulk_write(self, requests, ordered=True):
        encoded = []
        for item in requests:
            name = type(item).__name__
            operations = {"InsertOne": "insert_one", "UpdateOne": "update_one", "UpdateMany": "update_many", "ReplaceOne": "replace_one", "DeleteOne": "delete_one", "DeleteMany": "delete_many"}
            if name not in operations:
                raise OperationFailure("Unsupported repository bulk request.")
            args = {}
            if hasattr(item, "_filter"):
                args["filter"] = item._filter
            if hasattr(item, "_doc"):
                args["document" if name == "InsertOne" else "replacement" if name == "ReplaceOne" else "update"] = item._doc
            if hasattr(item, "_upsert") and item._upsert is not None:
                args["upsert"] = item._upsert
            encoded.append({"operation": operations[name], "arguments": args})
        return BulkWriteResult(await self.database.call("bulk_write", self.name, requests=encoded, ordered=ordered), True)
