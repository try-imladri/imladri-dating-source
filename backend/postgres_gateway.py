"""Bounded service-to-service PostgreSQL repository RPC, never a SQL interface.

The outer Render origin gate remains required. This module authenticates the
dedicated data-plane envelope and validates a fixed BSON protocol. The database
owns durable request deduplication in the SAME transaction as each mutation.
No read payload or find-and-update image belongs in that replay journal.
"""
from copy import deepcopy
import asyncio
from dataclasses import dataclass
import hashlib
import hmac
import inspect
import os
import re
import time
from uuid import UUID
from weakref import WeakKeyDictionary

from bson import BSON
from bson.errors import BSONError
from pymongo import (DeleteMany, DeleteOne, IndexModel, InsertOne, ReplaceOne,
                     ReturnDocument, UpdateMany, UpdateOne)
from pymongo.errors import BulkWriteError, DuplicateKeyError, OperationFailure
from starlette.responses import Response

from .postgres_migration import COLLECTION_DOMAINS
from .postgres_protocol import GatewayCommittedResultUnavailable


PATH = "/api/internal/postgres"
CONTENT_TYPE = "application/bson"
MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_CLOCK_SKEW_SECONDS = 60
COLLECTIONS = frozenset(COLLECTION_DOMAINS) | {"database_migration_reports"}
MUTATING_OPERATIONS = frozenset({
    "insert_one", "insert_many", "update_one", "update_many", "find_one_and_update",
    "replace_one", "delete_one", "delete_many", "create_indexes", "bulk_write",
})
ARGUMENTS = {
    "ping": (set(), set()),
    "find": ({"filter", "projection", "sort", "skip", "limit", "batch_size", "max_time_ms", "length"}, set()),
    "aggregate": ({"pipeline", "allowDiskUse", "batchSize", "maxTimeMS", "length"}, {"pipeline"}),
    "count": ({"filter", "skip", "limit"}, {"filter"}),
    "distinct": ({"key", "filter"}, {"key"}),
    "insert_one": ({"document"}, {"document"}),
    "insert_many": ({"documents", "ordered"}, {"documents"}),
    "update_one": ({"filter", "update", "upsert"}, {"filter", "update"}),
    "update_many": ({"filter", "update", "upsert"}, {"filter", "update"}),
    "find_one_and_update": ({"filter", "update", "projection", "sort", "upsert", "return_document"}, {"filter", "update"}),
    "replace_one": ({"filter", "replacement", "upsert"}, {"filter", "replacement"}),
    "delete_one": ({"filter"}, {"filter"}),
    "delete_many": ({"filter"}, {"filter"}),
    "create_indexes": ({"models"}, {"models"}),
    "bulk_write": ({"requests", "ordered"}, {"requests"}),
}
BULK_OPERATIONS = frozenset({"insert_one", "update_one", "update_many", "replace_one", "delete_one", "delete_many"})
READ_ONLY_STAGES = frozenset({"$match", "$set", "$addFields", "$project", "$unset", "$sort", "$skip", "$limit",
    "$count", "$group", "$unwind", "$lookup", "$unionWith", "$facet", "$replaceRoot", "$replaceWith"})
COMMITTED_RESULT_UNAVAILABLE = {"committed": True, "result_unavailable": True}
HEADER_NAMES = (b"x-imladri-pg-timestamp", b"x-imladri-pg-request-id", b"x-imladri-pg-signature")
_ADMISSION = WeakKeyDictionary()


class GatewayRejected(ValueError):
    def __init__(self, kind="invalid_request", status=400):
        self.kind, self.status = kind, status
        super().__init__(kind)


@dataclass(frozen=True)
class Envelope:
    request: dict
    request_id: str
    timestamp: int
    body_sha256: str


def _secret_bytes(secret):
    if not isinstance(secret, str) or len(secret.encode("utf8")) < 32:
        raise GatewayRejected("unauthorized", 404)
    return secret.encode("utf8")


def signature(secret, timestamp, request_id, body):
    """Sign the exact BSON bytes, including their order and binary types."""
    digest = hashlib.sha256(body).hexdigest()
    message = f"v1\n{timestamp}\n{request_id}\n{digest}".encode("ascii")
    return hmac.new(_secret_bytes(secret), message, hashlib.sha256).hexdigest()


def signed_headers(secret, timestamp, request_id, body):
    return {"X-Imladri-PG-Timestamp": str(timestamp), "X-Imladri-PG-Request-Id": request_id,
            "X-Imladri-PG-Signature": signature(secret, timestamp, request_id, body),
            "Content-Type": CONTENT_TYPE}


def _collection(name):
    if not isinstance(name, str) or name not in COLLECTIONS:
        raise GatewayRejected()


def _pipeline_collections(pipeline):
    if not isinstance(pipeline, list):
        raise GatewayRejected()
    for stage in pipeline:
        if not isinstance(stage, dict) or len(stage) != 1:
            raise GatewayRejected()
        name, value = next(iter(stage.items()))
        if name not in READ_ONLY_STAGES:
            raise GatewayRejected()
        if name == "$lookup":
            if not isinstance(value, dict):
                raise GatewayRejected()
            _collection(value.get("from"))
            if "pipeline" in value:
                _pipeline_collections(value["pipeline"])
        elif name == "$unionWith":
            if isinstance(value, str):
                _collection(value)
            elif isinstance(value, dict):
                _collection(value.get("coll"))
                if "pipeline" in value:
                    _pipeline_collections(value["pipeline"])
            else:
                raise GatewayRejected()
        elif name == "$facet":
            if not isinstance(value, dict):
                raise GatewayRejected()
            for nested in value.values():
                _pipeline_collections(nested)


def validate_request(request):
    if not isinstance(request, dict) or set(request) - {"version", "operation", "collection", "arguments"}:
        raise GatewayRejected()
    if type(request.get("version")) is not int or request["version"] != 1:
        raise GatewayRejected()
    operation = request.get("operation")
    if not isinstance(operation, str) or operation not in ARGUMENTS:
        raise GatewayRejected()
    if operation == "ping":
        if "collection" in request:
            raise GatewayRejected()
    else:
        _collection(request.get("collection"))
    arguments = request.get("arguments", {})
    allowed, required = ARGUMENTS[operation]
    if not isinstance(arguments, dict) or set(arguments) - allowed or required - set(arguments):
        raise GatewayRejected()
    for key in ("filter", "document", "replacement"):
        if key in arguments and not isinstance(arguments[key], dict):
            raise GatewayRejected()
    if "update" in arguments and not isinstance(arguments["update"], (dict, list)):
        raise GatewayRejected()
    for key in ("upsert", "ordered", "return_document", "allowDiskUse"):
        if key in arguments and type(arguments[key]) is not bool:
            raise GatewayRejected()
    for key in ("skip", "limit", "batch_size", "max_time_ms", "length", "batchSize", "maxTimeMS"):
        value = arguments.get(key)
        if key in arguments and value is not None and (type(value) is not int or value < 0 or value > 100000):
            raise GatewayRejected()
    if "key" in arguments and (not isinstance(arguments["key"], str) or not arguments["key"] or len(arguments["key"]) > 512):
        raise GatewayRejected()
    if operation == "aggregate":
        _pipeline_collections(arguments["pipeline"])
    if operation == "insert_many" and (not isinstance(arguments["documents"], list) or
            not arguments["documents"] or any(not isinstance(row, dict) for row in arguments["documents"])):
        raise GatewayRejected()
    if operation == "create_indexes":
        models = arguments["models"]
        if not isinstance(models, list) or not models or len(models) > 256:
            raise GatewayRejected()
        for model in models:
            if not isinstance(model, dict) or set(model) - {"key", "name", "unique", "sparse", "partialFilterExpression", "expireAfterSeconds", "background"}:
                raise GatewayRejected()
            if not isinstance(model.get("key"), (dict, list)) or not model["key"]:
                raise GatewayRejected()
    if operation == "bulk_write":
        requests = arguments["requests"]
        if not isinstance(requests, list) or not requests or len(requests) > 10000:
            raise GatewayRejected()
        for item in requests:
            if not isinstance(item, dict) or set(item) != {"operation", "arguments"} or item.get("operation") not in BULK_OPERATIONS:
                raise GatewayRejected()
            validate_request({"version": 1, "collection": request["collection"], **item})
    return request


def authenticate(body, headers, secret, *, now=None):
    if not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES:
        raise GatewayRejected("request_too_large", 413)
    key = _secret_bytes(secret)
    values = {}
    for name in HEADER_NAMES:
        matched = [value for field, value in headers if field.lower() == name]
        if len(matched) != 1:
            raise GatewayRejected("unauthorized", 404)
        try:
            values[name] = matched[0].decode("ascii")
        except (UnicodeError, AttributeError):
            raise GatewayRejected("unauthorized", 404) from None
    stamp, request_id, supplied = (values[name] for name in HEADER_NAMES)
    if not re.fullmatch(r"[0-9]{1,12}", stamp) or not re.fullmatch(r"[0-9a-f]{64}", supplied):
        raise GatewayRejected("unauthorized", 404)
    try:
        parsed = UUID(request_id)
        valid_id = parsed.version == 4 and str(parsed) == request_id
    except (ValueError, AttributeError):
        valid_id = False
    timestamp = int(stamp)
    if not valid_id or abs((time.time() if now is None else now) - timestamp) > MAX_CLOCK_SKEW_SECONDS:
        raise GatewayRejected("unauthorized", 404)
    expected = hmac.new(key, f"v1\n{stamp}\n{request_id}\n{hashlib.sha256(body).hexdigest()}".encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise GatewayRejected("unauthorized", 404)
    try:
        request = BSON(body).decode()
        # Duplicate BSON keys and alternate encodings are not protocol messages.
        if BSON.encode(request) != body:
            raise GatewayRejected()
        validate_request(request)
    except (BSONError, RecursionError, TypeError, OverflowError):
        raise GatewayRejected() from None
    return Envelope(request, request_id, timestamp, hashlib.sha256(body).hexdigest())


async def execute_operation(db, request):
    """Map validated RPC to existing repository methods inside the caller's txn."""
    validate_request(request)
    operation, args = request["operation"], deepcopy(request.get("arguments", {}))
    if operation == "ping":
        return await db.command("ping")
    collection = db[request["collection"]]
    if operation in ("find", "aggregate"):
        length = args.pop("length", None)
        cursor = collection.find(**args) if operation == "find" else collection.aggregate(**args)
        try:
            return await cursor.to_list(length)
        finally:
            await cursor.close()
    if operation == "count":
        return await collection.count_documents(**args)
    if operation == "create_indexes":
        models = []
        for original in args["models"]:
            model = dict(original)
            key = model.pop("key")
            models.append(IndexModel(list(key.items()) if isinstance(key, dict) else key, **model))
        return await collection.create_indexes(models)
    if operation == "bulk_write":
        constructors = {"insert_one": InsertOne, "update_one": UpdateOne, "update_many": UpdateMany,
                        "replace_one": ReplaceOne, "delete_one": DeleteOne, "delete_many": DeleteMany}
        requests = [constructors[item["operation"]](**item["arguments"]) for item in args["requests"]]
        return (await collection.bulk_write(requests, ordered=args.get("ordered", True))).bulk_api_result
    if operation == "find_one_and_update":
        args["return_document"] = ReturnDocument.AFTER if args.get("return_document", False) else ReturnDocument.BEFORE
    result = await getattr(collection, operation)(**args)
    if operation == "insert_one":
        return {"inserted_id": result.inserted_id}
    if operation == "insert_many":
        return {"inserted_ids": result.inserted_ids}
    if operation in {"update_one", "update_many", "replace_one", "delete_one", "delete_many"}:
        return result.raw_result
    return result


def compact_result(request, result):
    """The durable dedup journal must never retain private document images."""
    operation = request["operation"]
    if operation == "find_one_and_update":
        return dict(COMMITTED_RESULT_UNAVAILABLE)
    if operation not in MUTATING_OPERATIONS:
        return None
    if operation == "insert_one":
        compact = {"inserted_id": result["inserted_id"]}
    elif operation == "insert_many":
        compact = {"inserted_ids": result["inserted_ids"]}
    elif operation == "create_indexes":
        compact = result
    else:
        keys = {"n", "nModified", "updatedExisting", "upserted", "nInserted", "nMatched", "nRemoved", "nUpserted"}
        compact = {key: value for key, value in result.items() if key in keys}
    # A compact cache is bounded independently of the 16MiB response limit.
    if len(BSON.encode({"result": compact})) > 256 * 1024:
        return dict(COMMITTED_RESULT_UNAVAILABLE)
    return deepcopy(compact)


def encode_success(result):
    body = BSON.encode({"ok": True, "result": result})
    if len(body) > MAX_BODY_BYTES:
        raise GatewayRejected("response_too_large", 413)
    return body


async def dispatch(envelope, rpc_callback):
    """The callback must commit operation + request-id/body-hash atomically."""
    validate_request(envelope.request)
    result = rpc_callback(envelope.request, request_id=envelope.request_id, body_sha256=envelope.body_sha256)
    if inspect.isawaitable(result):
        result = await result
    return result


def sanitized_bulk_details(details):
    """Expose partial success without server errmsg, query, or document contents."""
    details = details if isinstance(details, dict) else {}
    safe = {key: details[key] for key in ("nInserted", "nMatched", "nModified", "nRemoved", "nUpserted")
            if type(details.get(key)) is int and details[key] >= 0}
    safe["upserted"] = [{"index": row["index"], "_id": row["_id"]} for row in details.get("upserted", [])
                        if isinstance(row, dict) and type(row.get("index")) is int and "_id" in row]
    safe["writeErrors"] = [{"index": row["index"], "code": row["code"], "errmsg": "Repository bulk write failed."}
                           for row in details.get("writeErrors", []) if isinstance(row, dict)
                           and type(row.get("index")) is int and type(row.get("code")) is int]
    safe["writeConcernErrors"] = []
    return safe


def _failure(kind, status, code=None, details=None):
    error = {"kind": kind}
    if code is not None:
        error["code"] = code
    if details is not None:
        error["details"] = details
    return Response(BSON.encode({"ok": False, "error": error}), status_code=status, media_type=CONTENT_TYPE,
                    headers={"Cache-Control": "no-store"})


async def endpoint(request, db):
    # Admit before buffering/decoding the body. Connection pooling alone does
    # not bound the memory retained by queued HTTP requests and responses.
    loop = asyncio.get_running_loop()
    admission = _ADMISSION.setdefault(loop, asyncio.Semaphore(2))
    try:
        await asyncio.wait_for(admission.acquire(), timeout=0.25)
    except asyncio.TimeoutError:
        return _failure("repository_busy", 503)
    try:
        return await _endpoint(request, db)
    finally:
        admission.release()


async def _endpoint(request, db):
    """Fixed ASGI handler. No secrets, SQL, documents or exception text are logged."""
    if request.method != "POST" or request.url.path != PATH:
        return _failure("not_found", 404)
    try:
        _secret_bytes(os.environ.get("IMLADRI_POSTGRES_GATEWAY_SECRET"))
        content_types = [value for field, value in request.scope["headers"] if field.lower() == b"content-type"]
        if len(content_types) != 1 or content_types[0].lower() != CONTENT_TYPE.encode("ascii"):
            raise GatewayRejected()
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                raise GatewayRejected("request_too_large", 413)
            body.extend(chunk)
        envelope = authenticate(bytes(body), request.scope["headers"], os.environ["IMLADRI_POSTGRES_GATEWAY_SECRET"])
        result = await dispatch(envelope, db.rpc)
        return Response(encode_success(result), media_type=CONTENT_TYPE, headers={"Cache-Control": "no-store"})
    except GatewayRejected as exc:
        return _failure(exc.kind, exc.status)
    except GatewayCommittedResultUnavailable:
        return _failure("committed_result_unavailable", 409)
    except DuplicateKeyError:
        return _failure("duplicate_key", 409, 11000)
    except BulkWriteError as exc:
        return _failure("bulk_write", 409, details=sanitized_bulk_details(exc.details))
    except OperationFailure as exc:
        return _failure("invalid_operation", 400, exc.code if isinstance(exc.code, int) else None)
    except (ValueError, TypeError, BSONError, RecursionError):
        return _failure("invalid_request", 400)
    except Exception:
        return _failure("repository_unavailable", 503)
