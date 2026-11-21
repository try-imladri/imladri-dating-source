"""PostgreSQL document repository for the existing application domain services.

No MongoDB server or wire protocol is used. Original BSON is retained for exact
types; JSONB is an indexed candidate selector, never the authority for matching.
Mutations serialize on a PostgreSQL transaction-level collection lock, including
absent-row upserts. Unique (including multikey) constraints are native SQL keys.
Unknown document operators fail closed in the shared, separately tested engines.
"""
import asyncio
import base64
import binascii
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from itertools import product
import json
import os
from pathlib import Path
import queue
import re
import threading
import time
from uuid import uuid4

from bson import BSON, Decimal128, ObjectId, Regex, json_util
from bson.errors import BSONError
from pymongo import ReturnDocument
from pymongo.errors import BulkWriteError, DocumentTooLarge, DuplicateKeyError, OperationFailure
from pymongo.results import DeleteResult, InsertManyResult, InsertOneResult, UpdateResult

from . import document_queries as dq
from .document_updates import apply_update, upsert_document, validate_update
from .postgres_codec import connection_options, document_key, jsonb_text, validate_name
from .postgres_protocol import GatewayCommittedResultUnavailable


_BOUND_TRANSACTION = ContextVar("postgres_bound_transaction", default=None)
_BOUND_PUBLIC_READ = ContextVar("postgres_bound_public_catalog_read", default=False)
_WRITE_TRANSACTION = ContextVar("postgres_mutating_transaction", default=False)


class RepositoryUnavailable(RuntimeError):
    """Sanitized operational failure, without SQL, connection strings or values."""


class ScanLimitExceeded(RepositoryUnavailable):
    pass


MAX_PUBLIC_CATALOG_CACHE_BYTES = 32 * 1024 * 1024
MAX_PUBLIC_CATALOG_CACHE_ENTRIES = 1024
PUBLIC_CATALOG_FLAGS = {"figures": "published", "archetypes": "unlocked"}
RUNTIME_ACCESS_TTL_SECONDS = 60
RUNTIME_ACCESS_LOG_PREFIX = "IMLADRI_PG_ACCESS_v1 "


class _PublicCatalogCache:
    """Immutable raw BSON only; every lookup is fenced by fresh SQL metadata."""
    def __init__(self, maximum):
        self.maximum, self.bytes = maximum, 0
        self.entries = OrderedDict()
        self.lock = threading.RLock()
        self.observations = {"metadata_queries": 0, "hits": 0, "misses": 0,
                             "raw_BSON_bytes_read_from_SQL": 0, "evictions": 0}

    def observe(self, key, amount=1):
        with self.lock:
            self.observations[key] += amount

    def get(self, key, version):
        with self.lock:
            item = self.entries.get(key)
            if item is not None and item[0] == version:
                self.entries.move_to_end(key)
                self.observations["hits"] += 1
                return item[1]
            if item is not None:
                self.bytes -= len(self.entries.pop(key)[1])
            self.observations["misses"] += 1
            return None

    def put(self, key, version, raw):
        with self.lock:
            if len(raw) > self.maximum:
                return
            prior = self.entries.pop(key, None)
            if prior is not None:
                self.bytes -= len(prior[1])
            while self.entries and (self.bytes + len(raw) > self.maximum or len(self.entries) >= MAX_PUBLIC_CATALOG_CACHE_ENTRIES):
                _, old = self.entries.popitem(last=False)
                self.bytes -= len(old[1]); self.observations["evictions"] += 1
            self.entries[key] = (version, raw)
            self.bytes += len(raw)

    def snapshot(self, collection):
        # Local immutable references remain valid even if a concurrent request
        # updates or evicts the corresponding LRU entry during the SQL cursor.
        with self.lock:
            return {key[1]: value for key, value in self.entries.items() if key[0] == collection}

    def hit(self, key):
        with self.lock:
            self.observations["hits"] += 1
            if key in self.entries:
                self.entries.move_to_end(key)

    def discard(self, key):
        with self.lock:
            prior = self.entries.pop(key, None)
            if prior is not None:
                self.bytes -= len(prior[1])

    def stats(self):
        with self.lock:
            return {"enabled": True, "capacity_bytes": self.maximum, "maximum_entries": MAX_PUBLIC_CATALOG_CACHE_ENTRIES,
                    "resident_BSON_bytes": self.bytes,
                    "entries": len(self.entries), **self.observations, "exact_wire_bandwidth_measured": False}


class IndexMetadataRepairRequired(OperationFailure):
    """Unversioned metadata cannot prove original Mongo key order or BSON types."""


INDEX_METADATA_FORMAT = "imladri-index-bson-v1"
MAX_INDEX_METADATA_BYTES = 64 * 1024


def validate_index_definition(definition):
    """Validate the reviewed index vocabulary before encoding or applying it."""
    allowed = {"key", "name", "unique", "sparse", "partialFilterExpression", "expireAfterSeconds", "background"}
    if not isinstance(definition, dict) or set(definition) - allowed or not isinstance(definition.get("name"), str) or not 1 <= len(definition["name"]) <= 128:
        raise OperationFailure("Unsupported repository index definition.")
    keys = definition.get("key")
    if not isinstance(keys, dict) or not keys or any(not isinstance(path, str) or not path or type(order) is not int or order not in (-1, 1) for path, order in keys.items()):
        raise OperationFailure("Only ascending/descending repository index fields are supported.")
    for flag in ("unique", "sparse", "background"):
        if flag in definition and type(definition[flag]) is not bool:
            raise OperationFailure("Invalid repository index option.")
    if "expireAfterSeconds" in definition and (type(definition["expireAfterSeconds"]) is not int or definition["expireAfterSeconds"] < 0):
        raise OperationFailure("Invalid repository TTL index option.")
    if "partialFilterExpression" in definition:
        dq.validate_query(definition["partialFilterExpression"])
    try:
        raw = BSON.encode(definition)
    except Exception:
        raise OperationFailure("Index definition is not supported BSON.") from None
    if len(raw) > MAX_INDEX_METADATA_BYTES:
        raise OperationFailure("Index definition exceeds the reviewed byte bound.")
    return raw


def encode_index_definition(definition):
    """JSONB carries an opaque exact BSON envelope, never authoritative objects."""
    raw = validate_index_definition(definition)
    return {"format": INDEX_METADATA_FORMAT, "bson_base64": base64.b64encode(raw).decode("ascii"),
            "sha256": hashlib.sha256(raw).hexdigest()}


def decode_index_definition(stored):
    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except (ValueError, TypeError):
            raise IndexMetadataRepairRequired("Repository index metadata requires explicit owner reconciliation.") from None
    if not isinstance(stored, dict) or set(stored) != {"format", "bson_base64", "sha256"} or stored.get("format") != INDEX_METADATA_FORMAT:
        raise IndexMetadataRepairRequired("Repository index metadata requires explicit owner reconciliation.")
    encoded, digest = stored.get("bson_base64"), stored.get("sha256")
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_INDEX_METADATA_BYTES + 2) // 3) or not isinstance(digest, str):
        raise IndexMetadataRepairRequired("Repository index metadata requires explicit owner reconciliation.")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_INDEX_METADATA_BYTES or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError
        definition = BSON(raw).decode()
        if validate_index_definition(definition) != raw:
            raise ValueError
        return definition
    except (ValueError, TypeError, binascii.Error, BSONError, OperationFailure):
        raise IndexMetadataRepairRequired("Repository index metadata requires explicit owner reconciliation.") from None


def index_definitions_equal(left, right):
    # Root option order is irrelevant. Compound field order and every embedded
    # literal BSON object/type remain significant, including partial predicates.
    def normalized(value):
        return BSON.encode({"key": [[field, direction] for field, direction in value["key"].items()],
                            "options": {key: value[key] for key in sorted(value) if key != "key"}})
    return normalized(left) == normalized(right)


def _unique_value(value):
    # Mongo unique indexes compare numeric types by value, but bools separately.
    if value is dq.MISSING or value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, (int, float, Decimal, Decimal128)):
        number = value.to_decimal() if isinstance(value, Decimal128) else Decimal(str(value))
        return ["number", "0" if number.is_zero() else str(number.normalize()) if number.is_finite() else str(number)]
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return ["date", int(value.timestamp() * 1000)]
    return ["bson", BSON.encode({"v": value}).hex()]


def unique_tokens(document, definition):
    partial = definition.get("partialFilterExpression")
    if partial and not dq.matches(document, partial):
        return set()
    paths = list(definition["key"])
    values = [dq.path_values(document, path) for path in paths]
    if definition.get("sparse") and all(not items or all(v is dq.MISSING for v in items) for items in values):
        return set()
    axes, arrays = [], 0
    for items in values:
        expanded = []
        had_array = False
        for item in items or [dq.MISSING]:
            if isinstance(item, list):
                had_array = True
                expanded.extend(item or [dq.MISSING])
            else:
                expanded.append(item)
        arrays += int(had_array)
        axes.append(expanded)
    if arrays > 1:
        raise OperationFailure("Parallel array index fields are unsupported.", 171)
    return {hashlib.sha256(json.dumps([_unique_value(v) for v in row], separators=(",", ":")).encode()).hexdigest()
            for row in product(*axes)}


def candidate_filter(query):
    """Return an indexed SQL superset. Final filtering always uses exact BSON.

    Only root string equality is pruned. Arrays, JSONB-incompatible documents,
    unsupported clauses, nulls and non-string values remain candidates. Parameters
    are bound, including field names; query text never becomes SQL syntax.
    """
    if not isinstance(query, dict):
        return "TRUE", []
    clauses, args = [], []
    for key, value in query.items():
        if key == "_id" and isinstance(value, (str, ObjectId)):
            clauses.append("id_key=%s")
            args.append(document_key({"_id": value}))
        elif key in ("$and", "$or") and isinstance(value, list) and value:
            parts = [candidate_filter(part) for part in value]
            clauses.append("(" + (" AND " if key == "$and" else " OR ").join(sql for sql, _ in parts) + ")")
            args.extend(arg for _, params in parts for arg in params)
        elif not key.startswith("$") and "." not in key and isinstance(value, str):
            clauses.append("(query_document IS NULL OR query_document @> %s::jsonb OR jsonb_typeof(query_document -> %s) = 'array')")
            args.extend([json.dumps({key: value}), key])
    return (" AND ".join(clauses) or "TRUE"), args


class _Pool:
    def __init__(self, options, size):
        self.options, self.size = options, size
        self.idle = queue.LifoQueue()
        self.slots = threading.BoundedSemaphore(size)
        self.closed = False
        self.guard = threading.Lock()

    @contextmanager
    def connection(self):
        import pg8000.dbapi
        if not self.slots.acquire(timeout=20):
            raise RepositoryUnavailable("PostgreSQL connection capacity is temporarily busy.")
        conn = None
        healthy = False
        try:
            with self.guard:
                if self.closed:
                    raise RepositoryUnavailable("PostgreSQL repository is closed.")
                try:
                    conn = self.idle.get_nowait()
                except queue.Empty:
                    pass
            if conn is None:
                conn = pg8000.dbapi.connect(**self.options)
            yield conn
            healthy = True
        finally:
            try:
                if conn is not None:
                    try:
                        conn.rollback()
                    except Exception:
                        healthy = False
                    with self.guard:
                        if healthy and not self.closed:
                            self.idle.put_nowait(conn)
                        else:
                            conn.close()
            finally:
                self.slots.release()

    def close(self):
        with self.guard:
            self.closed = True
            while not self.idle.empty():
                self.idle.get_nowait().close()


class PostgresDatabase:
    backend = "postgresql"

    def __init__(self, dsn, namespace, *, allow_local_plaintext=False, pool_size=2,
                 max_scan_records=100000, max_scan_bytes=64 * 1024 * 1024,
                 initialize_schema=None, public_catalog_cache_bytes=0):
        self.name = validate_name(namespace)
        if type(pool_size) is not int or not 1 <= pool_size <= 4:
            raise ValueError("PostgreSQL pool size must be between one and four.")
        options = connection_options(dsn, allow_local_plaintext=allow_local_plaintext)
        self._role = options["user"]
        self._schema_bootstrap = allow_local_plaintext if initialize_schema is None else initialize_schema
        if type(self._schema_bootstrap) is not bool:
            raise ValueError("Schema bootstrap must be explicitly enabled or disabled.")
        options.update(timeout=30, application_name="imladri-application")
        self.pool = _Pool(options, pool_size)
        self.max_scan_records, self.max_scan_bytes = max_scan_records, max_scan_bytes
        self._initialized = False
        self._runtime_access = None
        self._allow_local_plaintext = allow_local_plaintext
        self._initialize_lock = threading.Lock()
        if type(public_catalog_cache_bytes) is not int or not 0 <= public_catalog_cache_bytes <= MAX_PUBLIC_CATALOG_CACHE_BYTES:
            raise ValueError("Public catalog cache must be explicitly bounded to at most thirty-two MiB.")
        self._public_catalog_cache = _PublicCatalogCache(public_catalog_cache_bytes) if public_catalog_cache_bytes else None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def __getitem__(self, name):
        return PostgresCollection(self, validate_name(name))

    @staticmethod
    def _runtime_access_identity():
        source = os.environ.get("RENDER_GIT_COMMIT")
        if source and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", source):
            raise RepositoryUnavailable("PostgreSQL deployment identity is invalid.")
        return os.getpid(), source or "unknown"

    def _runtime_access_fresh(self, identity):
        checked = self._runtime_access
        if checked is None or checked[1] != identity:
            return False
        age = time.monotonic() - checked[0]
        return 0 <= age < RUNTIME_ACCESS_TTL_SECONDS

    def _initialize(self, conn):
        identity = None if self._schema_bootstrap else self._runtime_access_identity()
        if ((self._schema_bootstrap and self._initialized)
                or (not self._schema_bootstrap and self._runtime_access_fresh(identity))):
            return
        with self._initialize_lock:
            if not self._schema_bootstrap:
                identity = self._runtime_access_identity()
                if self._runtime_access_fresh(identity):
                    return
                from .postgres_access import verify_runtime_access
                started = time.monotonic()
                proof = verify_runtime_access(conn, self.name, expected_role=self._role,
                    ssl_context=self.pool.options["ssl_context"],
                    allow_local_plaintext=self._allow_local_plaintext)
                elapsed = time.monotonic() - started
                if not 0 <= elapsed < RUNTIME_ACCESS_TTL_SECONDS:
                    raise RepositoryUnavailable("PostgreSQL access snapshot expired before admission.")
                if identity != self._runtime_access_identity():
                    raise RepositoryUnavailable("PostgreSQL deployment identity changed during verification.")
                observation = {key: proof[key] for key in ("schema_version", "role", "namespace", "rls_tables",
                    "runtime_ddl_executed", "provider_owner_role", "checked_at", "policy_version",
                    "catalog_select_statements", "read_only", "backend_pid", "tls_active", "tls_verified_hostname")}
                observation.update(pid=identity[0], source_commit=identity[1], event="periodic_runtime_verification")
                message = RUNTIME_ACCESS_LOG_PREFIX + json.dumps(observation, sort_keys=True, separators=(",", ":"))
                if len(message) > 2048:
                    raise RepositoryUnavailable("PostgreSQL access observation exceeds its fixed bound.")
                # A failed check, rollback, cleanup, or log cannot renew admission.
                print(message, flush=True)
                if not 0 <= time.monotonic() - started < RUNTIME_ACCESS_TTL_SECONDS:
                    raise RepositoryUnavailable("PostgreSQL access snapshot expired before admission.")
                self._runtime_access = (started, identity, observation)
                self._initialized = True
                return
            if self._initialized:
                return
            cursor = conn.cursor()
            try:
                # Cross-process schema initialization; no implicit source copy.
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (671409172002614,))
                schema = Path(__file__).with_name("postgres_store_schema.sql").read_text(encoding="utf8")
                for statement in "\n".join(line.split("--", 1)[0] for line in schema.splitlines()).split(";"):
                    if statement.strip():
                        cursor.execute(statement)
                conn.commit()
                self._initialized = True
            finally:
                cursor.close()

    def _transaction(self, callback, *, write=None):
        try:
            with self.pool.connection() as conn:
                self._initialize(conn)
                cursor = conn.cursor()
                try:
                    if not write:
                        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    elif write.startswith("__rpc_read:"):
                        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    cursor.execute("SET LOCAL statement_timeout = '20s'")
                    cursor.execute("SET LOCAL lock_timeout = '10s'")
                    cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '30s'")
                    if write:
                        token = int.from_bytes(hashlib.sha256((self.name + ":" + write).encode()).digest()[:8], "big", signed=True)
                        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (token,))
                    cursor.execute("SELECT clock_timestamp()")
                    now = cursor.fetchone()[0]
                    write_token = _WRITE_TRANSACTION.set(_WRITE_TRANSACTION.get() or bool(write and not write.startswith("__rpc_read:")))
                    try:
                        result = callback(cursor, now)
                    finally:
                        _WRITE_TRANSACTION.reset(write_token)
                    conn.commit()
                    return result
                except BaseException:
                    conn.rollback()
                    raise
                finally:
                    cursor.close()
        except (DuplicateKeyError, BulkWriteError, DocumentTooLarge, OperationFailure, ValueError, TypeError, RepositoryUnavailable, GatewayCommittedResultUnavailable):
            raise
        except Exception as exc:
            # Never return PG exception detail: it may contain a document/DSN.
            raise RepositoryUnavailable("PostgreSQL operation did not complete.") from None

    async def _run(self, callback, *, write=None):
        bound = _BOUND_TRANSACTION.get()
        if bound is not None and bound[0] is self:
            _, cursor, now = bound
            if write:
                cursor.execute("SAVEPOINT repository_operation")
            try:
                result = callback(cursor, now)
                if write:
                    cursor.execute("RELEASE SAVEPOINT repository_operation")
                return result
            except BaseException:
                if write:
                    cursor.execute("ROLLBACK TO SAVEPOINT repository_operation")
                    cursor.execute("RELEASE SAVEPOINT repository_operation")
                raise
        task = asyncio.create_task(asyncio.to_thread(self._transaction, callback, write=write))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Do not leave an unobserved committing transaction behind cancellation.
            try:
                await asyncio.shield(task)
            except Exception:
                pass
            raise

    async def rpc(self, request, *, request_id, body_sha256):
        from .postgres_gateway import (COMMITTED_RESULT_UNAVAILABLE, GatewayCommittedResultUnavailable,
            GatewayRejected, MUTATING_OPERATIONS, compact_result, encode_success, execute_operation, validate_request)
        validate_request(request)
        mutating = request["operation"] in MUTATING_OPERATIONS
        collection = request.get("collection", "__rpc")
        def execute(cursor, now):
            # The write's collection lock and dedup claim share this transaction.
            # A retransmitted HTTP request cannot reapply a committed increment.
            cursor.execute("SELECT body_sha256,compact_result,compact_error FROM imladri_app.rpc_requests WHERE namespace=%s AND request_id=%s",
                           (self.name, request_id))
            saved = cursor.fetchone()
            if saved:
                if saved[0] != body_sha256:
                    raise GatewayRejected("request_id_conflict", 409)
                if saved[2] is not None:
                    details = BSON(bytes(saved[2])).decode()
                    raise BulkWriteError(details)
                if saved[1] is None:
                    # Reads deliberately have no cached private payload.
                    raise GatewayCommittedResultUnavailable()
                result = BSON(bytes(saved[1])).decode()["result"]
                if result == COMMITTED_RESULT_UNAVAILABLE:
                    raise GatewayCommittedResultUnavailable()
                return result
            # Empty reads also claim a nonce, preventing a signed replay from
            # being used to re-read a different, subsequently changed account.
            cursor.execute("INSERT INTO imladri_app.rpc_requests(namespace,request_id,body_sha256) VALUES(%s,%s,%s)",
                           (self.name, request_id, body_sha256))
            token = _BOUND_TRANSACTION.set((self, cursor, now))
            public_read_token = _BOUND_PUBLIC_READ.set(not mutating)
            error = None
            try:
                try:
                    result = asyncio.run(execute_operation(self, request))
                except BulkWriteError as exc:
                    # Preserve Mongo's committed partial-success semantics, but
                    # retain only fixed counts/codes, never errmsg or source op.
                    details = exc.details
                    error = {key: details.get(key, 0) for key in ("nInserted", "nMatched", "nModified", "nRemoved", "nUpserted")}
                    error.update(upserted=details.get("upserted", []), writeConcernErrors=[],
                        writeErrors=[{"index": int(row.get("index", 0)), "code": int(row.get("code", 11000)), "errmsg": "Repository bulk write failed."} for row in details.get("writeErrors", [])])
                    result = None
                if error is not None:
                    cursor.execute("UPDATE imladri_app.rpc_requests SET compact_error=%s WHERE namespace=%s AND request_id=%s",
                                   (BSON.encode(error), self.name, request_id))
                else:
                    encode_success(result)  # Check serialization and bounds BEFORE commit.
                    compact = compact_result(request, result)
                    if compact is not None:
                        cursor.execute("UPDATE imladri_app.rpc_requests SET compact_result=%s WHERE namespace=%s AND request_id=%s",
                                       (BSON.encode({"result": compact}), self.name, request_id))
            finally:
                _BOUND_PUBLIC_READ.reset(public_read_token)
                _BOUND_TRANSACTION.reset(token)
            return {"result": result, "bulk_error": error}
        # Reads also need nonce writes. Different collections use a second
        # request-ID lock first, so cross-collection UUID reuse cannot race.
        def locked(cursor, now):
            token = int.from_bytes(hashlib.sha256((self.name + ":rpc:" + request_id).encode()).digest()[:8], "big", signed=True)
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (token,))
            return execute(cursor, now)
        outcome = await self._run(locked, write=collection if mutating else "__rpc_read:" + request_id)
        # Compact replay returns the exact result directly, not the local wrapper.
        if isinstance(outcome, dict) and set(outcome) == {"result", "bulk_error"}:
            if outcome["bulk_error"] is not None:
                raise BulkWriteError(outcome["bulk_error"])
            return outcome["result"]
        return outcome

    def catalog_cache_stats(self):
        return self._public_catalog_cache.stats() if self._public_catalog_cache is not None else {"enabled": False, "capacity_bytes": 0}

    def _scan_public_catalog(self, cursor, collection, query):
        cache = self._public_catalog_cache
        captured = cache.snapshot(collection)
        metadata = [{"cached_id": key, "cached_revision": value[0][0], "cached_sha256": value[0][1],
                     "cached_bytes": value[0][2]} for key, value in captured.items()]
        sql, params = candidate_filter(query or {})
        name = "public_catalog_" + uuid4().hex
        # The fresh version/hash and either exact raw miss bytes or a cache-hit
        # decision belong to ONE SQL statement/cursor snapshot, even within a
        # READ COMMITTED read-RPC nonce transaction. No metadata/raw query race.
        cursor.execute("DECLARE " + name + " NO SCROLL CURSOR FOR SELECT id_key,revision,source_sha256,octet_length(raw_bson), "
            "CASE WHEN cached_id IS NOT NULL AND cached_revision=revision AND cached_sha256=source_sha256 "
            "AND cached_bytes=octet_length(raw_bson) THEN NULL ELSE raw_bson END "
            "FROM imladri_app.documents LEFT JOIN jsonb_to_recordset(%s::jsonb) "
            "AS cached(cached_id text,cached_revision bigint,cached_sha256 text,cached_bytes bigint) ON cached_id=id_key "
            "WHERE namespace=%s AND collection=%s AND (" + sql + ") ORDER BY id_key",
            (json.dumps(metadata, separators=(",", ":")), self.name, collection, *params))
        cache.observe("metadata_queries")
        rows, byte_count = [], 0
        try:
            while True:
                cursor.execute("FETCH FORWARD 128 FROM " + name)
                batch = cursor.fetchall()
                if not batch:
                    break
                for key, revision, sha, size, payload in batch:
                    byte_count += size
                    if len(rows) >= self.max_scan_records or byte_count > self.max_scan_bytes:
                        raise ScanLimitExceeded("This query exceeds the bounded document scan; narrow its scope.")
                    version = (revision, sha, size)
                    if payload is None:
                        prior = captured.get(key)
                        if prior is None or prior[0] != version or len(prior[1]) != size:
                            raise RepositoryUnavailable("The public catalog snapshot metadata is inconsistent.")
                        raw = prior[1]
                        cache.hit((collection, key))
                    else:
                        raw = bytes(payload)
                        cache.observe("misses"); cache.observe("raw_BSON_bytes_read_from_SQL", len(raw))
                        if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha:
                            raise RepositoryUnavailable("The public catalog BSON does not match its snapshot metadata.")
                    document = BSON(raw).decode()
                    if document_key(document) != key:
                        raise RepositoryUnavailable("The public catalog BSON does not match its snapshot metadata.")
                    if payload is not None:
                        if document.get(PUBLIC_CATALOG_FLAGS[collection]) is True:
                            cache.put((collection, key), version, raw)
                        else:
                            cache.discard((collection, key))
                    rows.append(document)
        finally:
            cursor.execute("CLOSE " + name)
        return rows

    def _scan(self, cursor, collection, query=None, *, public_catalog_read=False):
        if (public_catalog_read and self._public_catalog_cache is not None
                and collection in PUBLIC_CATALOG_FLAGS and not _WRITE_TRANSACTION.get()
                and (_BOUND_TRANSACTION.get() is None or _BOUND_PUBLIC_READ.get() is True)):
            return self._scan_public_catalog(cursor, collection, query)
        sql, params = candidate_filter(query or {})
        name = "repository_" + uuid4().hex
        cursor.execute("DECLARE " + name + " NO SCROLL CURSOR FOR SELECT raw_bson FROM imladri_app.documents WHERE namespace=%s AND collection=%s AND (" + sql + ") ORDER BY id_key",
                       (self.name, collection, *params))
        rows, byte_count = [], 0
        try:
            while True:
                cursor.execute("FETCH FORWARD 128 FROM " + name)
                batch = cursor.fetchall()
                if not batch:
                    break
                for (payload,) in batch:
                    byte_count += len(payload)
                    if len(rows) >= self.max_scan_records or byte_count > self.max_scan_bytes:
                        raise ScanLimitExceeded("This query exceeds the bounded document scan; narrow its scope.")
                    rows.append(BSON(bytes(payload)).decode())
        finally:
            cursor.execute("CLOSE " + name)
        return rows

    async def command(self, command, *args, **kwargs):
        if command != "ping":
            raise OperationFailure("Unsupported repository command.")
        return await self._run(lambda cursor, now: {"ok": 1, "backend": "postgresql", "namespace": self.name})

    async def list_collection_names(self):
        def read(cursor, now):
            cursor.execute("SELECT name FROM imladri_app.collections WHERE namespace=%s UNION SELECT collection FROM imladri_app.documents WHERE namespace=%s UNION SELECT collection FROM imladri_app.indexes WHERE namespace=%s ORDER BY 1", (self.name, self.name, self.name))
            return [row[0] for row in cursor.fetchall()]
        return await self._run(read)

    async def create_collection(self, name):
        validate_name(name)
        def create(cursor, now):
            cursor.execute("INSERT INTO imladri_app.collections(namespace,name) VALUES(%s,%s) ON CONFLICT DO NOTHING", (self.name, name))
        await self._run(create, write=name)
        return self[name]

    async def aclose(self):
        await asyncio.to_thread(self.pool.close)

    async def maintenance_once(self):
        """Native bounded nonce retention plus Mongo-equivalent date TTL cleanup."""
        def metadata(cursor, now):
            cursor.execute("DELETE FROM imladri_app.rpc_requests WHERE namespace=%s AND request_id IN (SELECT request_id FROM imladri_app.rpc_requests WHERE namespace=%s AND committed_at < clock_timestamp()-interval '7 days' LIMIT 500)",
                           (self.name, self.name))
            cursor.execute("SELECT collection,definition FROM imladri_app.indexes WHERE namespace=%s", (self.name,))
            decoded = [(name, decode_index_definition(row)) for name, row in cursor.fetchall()]
            return [(name, definition) for name, definition in decoded if "expireAfterSeconds" in definition]
        indexes = await self._run(metadata, write="__maintenance")
        for name, definition in indexes:
            field = next(iter(definition["key"]))
            seconds = definition["expireAfterSeconds"]
            if type(seconds) is not int or seconds < 0:
                raise ValueError("Invalid repository TTL index.")
            def expire(cursor, now):
                count = 0
                for row in self._scan(cursor, name):
                    value = dq.get_path(row, field)
                    dates = value if isinstance(value, list) else [value]
                    dates = [item.replace(tzinfo=timezone.utc) if item.tzinfo is None else item for item in dates if isinstance(item, datetime)]
                    if dates and min(dates) + timedelta(seconds=seconds) <= now and count < 500:
                        cursor.execute("DELETE FROM imladri_app.documents WHERE namespace=%s AND collection=%s AND id_key=%s", (self.name, name, document_key(row)))
                        count += 1
                return count
            await self._run(expire, write=name)

    async def maintain(self):
        import logging
        while True:
            try:
                await self.maintenance_once()
            except Exception:
                logging.getLogger(__name__).warning("PostgreSQL maintenance did not finish; retrying next interval.")
            await asyncio.sleep(60)

    def close(self):
        self.pool.close()


class PostgresCursor:
    def __init__(self, collection, query=None, projection=None, pipeline=None):
        self.collection, self.query, self.projection = collection, deepcopy(query or {}), deepcopy(projection)
        self.pipeline = deepcopy(pipeline)
        self.order, self.offset, self.maximum, self.position = None, 0, 0, 0
        self.rows, self.closed = None, False

    def sort(self, key, direction=None):
        if self.rows is not None:
            raise OperationFailure("Cursor already executed.")
        self.order = list(key.items()) if isinstance(key, dict) else ([(key, direction or 1)] if isinstance(key, str) else list(key))
        return self

    def limit(self, count):
        if type(count) is not int:
            raise TypeError("limit requires an integer.")
        self.maximum = abs(count)
        return self

    def skip(self, count):
        if type(count) is not int or count < 0:
            raise ValueError("skip requires a nonnegative integer.")
        self.offset = count
        return self

    def batch_size(self, size):
        return self

    def max_time_ms(self, milliseconds):
        if type(milliseconds) is not int or milliseconds < 0:
            raise ValueError("max_time_ms requires a nonnegative integer.")
        return self

    async def _load(self):
        if self.rows is None and not self.closed:
            def read(cursor, now):
                db = self.collection.database
                first_match = self.pipeline[0].get("$match", {}) if self.pipeline else self.query
                if db._public_catalog_cache is not None and self.collection.name in PUBLIC_CATALOG_FLAGS:
                    rows = db._scan(cursor, self.collection.name, first_match, public_catalog_read=True)
                else:
                    rows = db._scan(cursor, self.collection.name, first_match)
                if self.pipeline is not None:
                    rows = dq.aggregate_documents(rows, self.pipeline, now=now, lookup=lambda name: db._scan(cursor, validate_name(name)))
                else:
                    matcher = dq.compile_query(self.query, now=now)
                    rows = [row for row in rows if matcher(row)]
                    if self.order:
                        rows = dq.sort_documents(rows, self.order)
                    rows = rows[self.offset: self.offset + self.maximum if self.maximum else None]
                    if self.projection is not None:
                        projector = dq.compile_projection(self.projection, now=now)
                        rows = [projector(row) for row in rows]
                return rows
            self.rows = await self.collection.database._run(read)
        if self.rows is None:
            self.rows = []

    async def to_list(self, length=None):
        if length is not None and (type(length) is not int or length < 0):
            raise ValueError("to_list length requires a nonnegative integer or None.")
        await self._load()
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
        self.closed = True
        self.rows = []


class PostgresCollection:
    def __init__(self, database, name):
        self.database, self.name = database, name

    def find(self, filter=None, projection=None, **kwargs):
        dq.validate_query(filter or {})
        if projection is not None:
            dq.validate_projection(projection)
        cursor = PostgresCursor(self, filter, projection)
        if kwargs.get("sort"):
            cursor.sort(kwargs.pop("sort"))
        for option, value in kwargs.items():
            if option == "limit":
                cursor.limit(value)
            elif option == "skip":
                cursor.skip(value)
            elif option not in ("batch_size", "max_time_ms"):
                raise OperationFailure("Unsupported find option.")
        return cursor

    async def find_one(self, filter=None, projection=None, **kwargs):
        if filter is not None and not isinstance(filter, dict):
            filter = {"_id": filter}
        rows = await self.find(filter, projection, **kwargs).limit(1).to_list(1)
        return rows[0] if rows else None

    def aggregate(self, pipeline, **kwargs):
        dq.validate_pipeline(pipeline)
        if set(kwargs) - {"allowDiskUse", "batchSize", "maxTimeMS"}:
            raise OperationFailure("Unsupported aggregate option.")
        return PostgresCursor(self, pipeline=pipeline)

    async def count_documents(self, filter, **kwargs):
        return len(await self.find(filter, **kwargs).to_list(None))

    async def distinct(self, key, filter=None):
        rows = await self.find(filter).to_list(None)
        out, seen = [], set()
        for row in rows:
            for value in dq.path_values(row, key):
                for item in value if isinstance(value, list) else [value]:
                    if item is dq.MISSING:
                        continue
                    token = json_util.dumps(item, json_options=json_util.CANONICAL_JSON_OPTIONS)
                    if token not in seen:
                        seen.add(token)
                        out.append(item)
        return out

    def _indexes(self, cursor):
        cursor.execute("SELECT name, definition FROM imladri_app.indexes WHERE namespace=%s AND collection=%s", (self.database.name, self.name))
        result = []
        for name, definition in cursor.fetchall():
            value = decode_index_definition(definition)
            if value["name"] != name:
                raise IndexMetadataRepairRequired("Repository index metadata requires explicit owner reconciliation.")
            result.append((name, value))
        return result

    def _write_document(self, cursor, document, *, inserting=False):
        if isinstance(document.get("_id"), (list, Regex)):
            raise OperationFailure("Array and regex document identifiers are unsupported.", 2)
        raw, key = BSON.encode(document), document_key(document)
        if len(raw) > 16_777_216:
            raise DocumentTooLarge("Repository documents must not exceed sixteen MiB.")
        namespace = self.database.name
        digest = hashlib.sha256(raw).hexdigest()
        try:
            cursor.execute("INSERT INTO imladri_app.collections(namespace,name) VALUES(%s,%s) ON CONFLICT DO NOTHING", (namespace, self.name))
            if inserting:
                cursor.execute("INSERT INTO imladri_app.documents(namespace,collection,id_key,raw_bson,query_document,source_sha256) VALUES(%s,%s,%s,%s,%s::jsonb,%s)",
                               (namespace, self.name, key, raw, jsonb_text(document), digest))
            else:
                cursor.execute("UPDATE imladri_app.documents SET raw_bson=%s,query_document=%s::jsonb,source_sha256=%s,revision=revision+1,updated_at=clock_timestamp() WHERE namespace=%s AND collection=%s AND id_key=%s",
                               (raw, jsonb_text(document), digest, namespace, self.name, key))
            cursor.execute("DELETE FROM imladri_app.unique_keys WHERE namespace=%s AND collection=%s AND id_key=%s", (namespace, self.name, key))
            implicit_id = hashlib.sha256(json.dumps(_unique_value(document["_id"]), separators=(",", ":")).encode()).hexdigest()
            cursor.execute("INSERT INTO imladri_app.unique_keys(namespace,collection,index_name,token,id_key) VALUES(%s,%s,%s,%s,%s)",
                           (namespace, self.name, "_id_", implicit_id, key))
            for name, definition in self._indexes(cursor):
                if definition.get("unique"):
                    for token in unique_tokens(document, definition):
                        cursor.execute("INSERT INTO imladri_app.unique_keys(namespace,collection,index_name,token,id_key) VALUES(%s,%s,%s,%s,%s)",
                                       (namespace, self.name, name, token, key))
        except Exception as exc:
            detail = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {}
            if detail.get("C") == "23505":
                raise DuplicateKeyError("A repository unique key already exists.", 11000) from None
            raise

    async def insert_one(self, document):
        if "_id" not in document:
            document["_id"] = ObjectId()
        value = deepcopy(document)
        await self.database._run(lambda cursor, now: self._write_document(cursor, value, inserting=True), write=self.name)
        return InsertOneResult(value["_id"], True)

    async def insert_many(self, documents, ordered=True):
        documents = list(documents)
        if not documents:
            raise TypeError("insert_many requires at least one document.")
        if len(documents) > self.database.max_scan_records:
            raise ScanLimitExceeded("This insert batch exceeds the repository record limit.")
        total = 0
        for document in documents:
            document.setdefault("_id", ObjectId())
            if isinstance(document["_id"], (list, Regex)):
                raise OperationFailure("Array and regex document identifiers are unsupported.", 2)
            size = len(BSON.encode(document))
            if size > 16_777_216:
                raise DocumentTooLarge("Repository documents must not exceed sixteen MiB.")
            total += size
        if total > self.database.max_scan_bytes:
            raise ScanLimitExceeded("This insert batch exceeds the repository byte limit.")
        documents = deepcopy(documents)
        def mutate(cursor, now):
            namespace = self.database.name
            indexes = self._indexes(cursor)
            prepared, tokens = [], []
            for document in documents:
                raw, key = BSON.encode(document), document_key(document)
                prepared.append((namespace, self.name, key, raw, jsonb_text(document), hashlib.sha256(raw).hexdigest()))
                implicit = hashlib.sha256(json.dumps(_unique_value(document["_id"]), separators=(",", ":")).encode()).hexdigest()
                tokens.append((namespace, self.name, "_id_", implicit, key))
                for name, definition in indexes:
                    if definition.get("unique"):
                        tokens.extend((namespace, self.name, name, token, key) for token in unique_tokens(document, definition))
            cursor.execute("INSERT INTO imladri_app.collections(namespace,name) VALUES(%s,%s) ON CONFLICT DO NOTHING", (namespace, self.name))
            cursor.execute("SAVEPOINT repository_batch")
            try:
                for offset in range(0, len(prepared), 128):
                    chunk = prepared[offset:offset + 128]
                    cursor.execute("INSERT INTO imladri_app.documents(namespace,collection,id_key,raw_bson,query_document,source_sha256) VALUES " + ",".join(["(%s,%s,%s,%s,%s::jsonb,%s)"] * len(chunk)),
                                   tuple(value for row in chunk for value in row))
                for offset in range(0, len(tokens), 256):
                    chunk = tokens[offset:offset + 256]
                    cursor.execute("INSERT INTO imladri_app.unique_keys(namespace,collection,index_name,token,id_key) VALUES " + ",".join(["(%s,%s,%s,%s,%s)"] * len(chunk)),
                                   tuple(value for row in chunk for value in row))
                cursor.execute("RELEASE SAVEPOINT repository_batch")
                return [document["_id"] for document in documents], []
            except Exception as exc:
                detail = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {}
                cursor.execute("ROLLBACK TO SAVEPOINT repository_batch")
                cursor.execute("RELEASE SAVEPOINT repository_batch")
                if detail.get("C") != "23505":
                    raise
            # A rare conflicting batch falls back to exact ordered/unordered
            # per-document success semantics, without committing duplicate rows.
            identifiers, errors = [], []
            for index, document in enumerate(documents):
                cursor.execute("SAVEPOINT repository_insert")
                try:
                    self._write_document(cursor, document, inserting=True)
                    cursor.execute("RELEASE SAVEPOINT repository_insert")
                    identifiers.append(document["_id"])
                except DuplicateKeyError:
                    cursor.execute("ROLLBACK TO SAVEPOINT repository_insert")
                    cursor.execute("RELEASE SAVEPOINT repository_insert")
                    errors.append({"index": index, "code": 11000, "errmsg": "Duplicate repository key."})
                    if ordered:
                        break
            return identifiers, errors
        identifiers, errors = await self.database._run(mutate, write=self.name)
        if errors:
            raise BulkWriteError({"writeErrors": errors, "nInserted": len(identifiers), "writeConcernErrors": []})
        return InsertManyResult(identifiers, True)

    async def _update(self, query, update, *, upsert=False, many=False, before_after=None, projection=None, sort=None, replacement=False):
        dq.validate_query(query)
        if not replacement:
            validate_update(update)
        def mutate(cursor, now):
            candidates = self.database._scan(cursor, self.name, query)
            matcher = dq.compile_query(query, now=now)
            found = [row for row in candidates if matcher(row)]
            if sort:
                found = dq.sort_documents(found, sort)
            if not many:
                found = found[:1]
            modified, before, after, inserted = 0, None, None, None
            for row in found:
                before = deepcopy(row)
                after = deepcopy(update) if replacement else apply_update(row, update, inserting=False, now=now)
                after.setdefault("_id", row["_id"])
                if document_key(after) != document_key(row):
                    raise OperationFailure("The document identifier is immutable.", 66)
                if BSON.encode(row) != BSON.encode(after):
                    self._write_document(cursor, after)
                    modified += 1
            if not found and upsert:
                seed = upsert_document(query)
                after = deepcopy(update) if replacement else apply_update(seed, update, inserting=True, now=now)
                after.setdefault("_id", seed.get("_id", ObjectId()))
                self._write_document(cursor, after, inserting=True)
                inserted = after["_id"]
            if before_after is not None:
                chosen = after if before_after == ReturnDocument.AFTER else before
                return dq.project(chosen, projection, now=now) if chosen is not None and projection is not None else chosen
            result = {"n": len(found) if inserted is None else 1, "nModified": modified, "updatedExisting": bool(found)}
            if inserted is not None:
                result["upserted"] = inserted
            return UpdateResult(result, True)
        return await self.database._run(mutate, write=self.name)

    async def update_one(self, filter, update, upsert=False, **kwargs):
        if kwargs:
            raise OperationFailure("Unsupported update option.")
        return await self._update(filter, update, upsert=upsert)

    async def update_many(self, filter, update, upsert=False, **kwargs):
        if kwargs:
            raise OperationFailure("Unsupported update option.")
        return await self._update(filter, update, upsert=upsert, many=True)

    async def replace_one(self, filter, replacement, upsert=False):
        return await self._update(filter, replacement, upsert=upsert, replacement=True)

    async def find_one_and_update(self, filter, update, projection=None, sort=None, upsert=False, return_document=ReturnDocument.BEFORE, **kwargs):
        if kwargs:
            raise OperationFailure("Unsupported find-and-update option.")
        return await self._update(filter, update, upsert=upsert, before_after=return_document, projection=projection, sort=sort)

    async def _delete(self, query, many):
        dq.validate_query(query)
        def mutate(cursor, now):
            matcher = dq.compile_query(query, now=now)
            found = [row for row in self.database._scan(cursor, self.name, query) if matcher(row)]
            if not many:
                found = found[:1]
            for row in found:
                cursor.execute("DELETE FROM imladri_app.documents WHERE namespace=%s AND collection=%s AND id_key=%s",
                               (self.database.name, self.name, document_key(row)))
            return DeleteResult({"n": len(found)}, True)
        return await self.database._run(mutate, write=self.name)

    async def delete_one(self, filter):
        return await self._delete(filter, False)

    async def delete_many(self, filter):
        return await self._delete(filter, True)

    async def create_indexes(self, models):
        definitions = [dict(model.document) for model in models]
        for definition in definitions:
            validate_index_definition(definition)
        def mutate(cursor, now):
            cursor.execute("INSERT INTO imladri_app.collections(namespace,name) VALUES(%s,%s) ON CONFLICT DO NOTHING", (self.database.name, self.name))
            names = []
            for definition in definitions:
                name = definition["name"]
                existing = dict(self._indexes(cursor)).get(name)
                if existing is not None and index_definitions_equal(existing, definition):
                    names.append(name)
                    continue
                if existing is not None:
                    raise OperationFailure("Index definition conflicts with the existing repository index.", 85)
                # Validate unique values before publishing the index definition.
                if definition.get("unique"):
                    for row in self.database._scan(cursor, self.name):
                        for token in unique_tokens(row, definition):
                            try:
                                cursor.execute("INSERT INTO imladri_app.unique_keys(namespace,collection,index_name,token,id_key) VALUES(%s,%s,%s,%s,%s)",
                                               (self.database.name, self.name, name, token, document_key(row)))
                            except Exception as exc:
                                detail = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {}
                                if detail.get("C") == "23505":
                                    raise DuplicateKeyError("Duplicate data prevents creation of the unique index.", 11000) from None
                                raise
                cursor.execute("INSERT INTO imladri_app.indexes(namespace,collection,name,definition) VALUES(%s,%s,%s,%s::jsonb)",
                               (self.database.name, self.name, name, json.dumps(encode_index_definition(definition))))
                names.append(name)
            return names
        return await self.database._run(mutate, write=self.name)

    async def create_index(self, keys, **options):
        from pymongo import IndexModel
        return (await self.create_indexes([IndexModel(keys, **options)]))[0]

    async def index_information(self):
        def read(cursor, now):
            result = {"_id_": {"key": [("_id", 1)], "v": 2, "unique": True}}
            for name, definition in self._indexes(cursor):
                value = deepcopy(definition)
                value.pop("name", None)
                value["key"] = list(value["key"].items())
                result[name] = value
            return result
        return await self.database._run(read)

    async def options(self):
        # The application repository implements ordinary collections. Migration
        # rejects capped/time-series/validation semantics rather than losing them.
        return {}

    async def bulk_write(self, requests, ordered=True):
        bound = _BOUND_TRANSACTION.get()
        if bound is None:
            def batch(cursor, now):
                token = _BOUND_TRANSACTION.set((self.database, cursor, now))
                try:
                    try:
                        return {"result": asyncio.run(self._bulk_write(requests, ordered)), "error": None}
                    except BulkWriteError as exc:
                        return {"result": None, "error": exc.details}
                finally:
                    _BOUND_TRANSACTION.reset(token)
            outcome = await self.database._run(batch, write=self.name)
            if outcome["error"] is not None:
                raise BulkWriteError(outcome["error"])
            return outcome["result"]
        return await self._bulk_write(requests, ordered)

    async def _bulk_write(self, requests, ordered):
        # Each item has its own savepoint. One transaction avoids hundreds of
        # connection/commit round trips while retaining partial-success results.
        counts = {"nInserted": 0, "nMatched": 0, "nModified": 0, "nRemoved": 0, "nUpserted": 0, "upserted": [], "writeErrors": [], "writeConcernErrors": []}
        for index, request in enumerate(requests):
            name = type(request).__name__
            try:
                if name in ("UpdateOne", "UpdateMany"):
                    result = await self._update(request._filter, request._doc, upsert=request._upsert, many=name == "UpdateMany")
                    counts["nMatched"] += result.matched_count
                    counts["nModified"] += result.modified_count
                    if result.upserted_id is not None:
                        counts["nUpserted"] += 1
                        counts["upserted"].append({"index": index, "_id": result.upserted_id})
                elif name == "InsertOne":
                    await self.insert_one(request._doc)
                    counts["nInserted"] += 1
                elif name == "ReplaceOne":
                    result = await self.replace_one(request._filter, request._doc, upsert=request._upsert)
                    counts["nMatched"] += result.matched_count
                    counts["nModified"] += result.modified_count
                    if result.upserted_id is not None:
                        counts["nUpserted"] += 1
                        counts["upserted"].append({"index": index, "_id": result.upserted_id})
                elif name in ("DeleteOne", "DeleteMany"):
                    result = await self._delete(request._filter, name == "DeleteMany")
                    counts["nRemoved"] += result.deleted_count
                else:
                    raise OperationFailure("Unsupported bulk request.")
            except DuplicateKeyError:
                counts["writeErrors"].append({"index": index, "code": 11000, "errmsg": "Duplicate repository key."})
                if ordered:
                    break
        if counts["writeErrors"]:
            raise BulkWriteError(counts)
        from pymongo.results import BulkWriteResult
        return BulkWriteResult(counts, True)
