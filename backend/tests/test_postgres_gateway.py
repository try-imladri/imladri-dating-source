"""The gateway is a fixed authenticated data plane, without provider credentials."""
import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
from types import SimpleNamespace

from bson import BSON, Binary, ObjectId
from pymongo import ReturnDocument
from pymongo.errors import BulkWriteError, DuplicateKeyError, OperationFailure
from pymongo.results import DeleteResult, InsertManyResult, InsertOneResult, UpdateResult
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route
from starlette.testclient import TestClient

from backend import postgres_gateway as gateway


SECRET = "fixture-only-random-32-byte-secret-value"
STAMP = 1800000000
REQUEST_ID = "4724c940-0e25-4cb1-9fd8-f0e2c97d6291"


def message(operation="find", collection="users", **arguments):
    value = {"version": 1, "operation": operation, "arguments": arguments}
    if collection is not None:
        value["collection"] = collection
    return value


def envelope(value=None, *, timestamp=STAMP, request_id=REQUEST_ID):
    body = BSON.encode(value or message())
    headers = gateway.signed_headers(SECRET, timestamp, request_id, body)
    raw = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    return body, headers, raw


def test_authenticated_exact_bson_roundtrip_and_signature_binding():
    value = message("insert_one", document={"_id": ObjectId(), "at": datetime(2026, 11, 10),
                                             "binary": Binary(b"\x00\xff", 0), "answer": "private"})
    body, _, headers = envelope(value)
    accepted = gateway.authenticate(body, headers, SECRET, now=STAMP)
    assert accepted.request == BSON(body).decode()
    assert accepted.request_id == REQUEST_ID
    assert accepted.body_sha256 == hashlib.sha256(body).hexdigest()
    tampered = BSON.encode({**value, "collection": "people"})
    with pytest.raises(gateway.GatewayRejected, match="unauthorized"):
        gateway.authenticate(tampered, headers, SECRET, now=STAMP)
    with pytest.raises(gateway.GatewayRejected, match="unauthorized"):
        gateway.authenticate(body, headers, "different-but-long-enough-secret-value", now=STAMP)


@pytest.mark.parametrize("change", ["expired", "future", "missing", "duplicate", "uuid", "upper_signature", "small_secret"])
def test_envelope_rejects_header_or_time_ambiguity(change):
    stamp = STAMP + (61 if change == "future" else -61 if change == "expired" else 0)
    identifier = "not-a-uuid" if change == "uuid" else REQUEST_ID
    body, _, headers = envelope(timestamp=stamp, request_id=identifier)
    if change == "missing":
        headers = headers[1:]
    if change == "duplicate":
        headers.append((b"X-Imladri-PG-Request-Id", REQUEST_ID.encode()))
    if change == "upper_signature":
        headers = [(key, val.upper() if key.endswith(b"signature") else val) for key, val in headers]
    with pytest.raises(gateway.GatewayRejected) as error:
        gateway.authenticate(body, headers, "short" if change == "small_secret" else SECRET, now=STAMP)
    assert error.value.status == 404


def test_clock_boundary_accepted_but_oversize_rejected_before_bson_decode(monkeypatch):
    body, _, headers = envelope(timestamp=STAMP - 60)
    assert gateway.authenticate(body, headers, SECRET, now=STAMP).timestamp == STAMP - 60
    monkeypatch.setattr(gateway, "MAX_BODY_BYTES", len(body) - 1)
    with pytest.raises(gateway.GatewayRejected) as error:
        gateway.authenticate(body, headers, SECRET, now=STAMP)
    assert error.value.status == 413


@pytest.mark.parametrize("value", [
    {"version": True, "operation": "ping"},
    message("command", sql="SELECT private"),
    message("find", collection="unregistered_collection"),
    {**message(), "database": "another-database"},
    message("find", filter={}, arbitrary_sql="no"),
    message("update_one", filter={}, update={}, upsert="true"),
    message("find", limit=True),
    message("find", limit=100001),
    message("create_indexes", models=[{"key": {"id": 1}, "arbitrary": "x"}]),
    message("aggregate", pipeline=[{"$lookup": {"from": "unregistered_collection", "as": "stolen"}}]),
    message("aggregate", pipeline=[{"$facet": {"hidden": [{"$unionWith": "unregistered_collection"}]}}]),
    message("aggregate", pipeline=[{"$out": "users"}]),
    message("aggregate", pipeline=[{"$merge": {"into": "users"}}]),
    message("bulk_write", requests=[{"operation": "command", "arguments": {}}]),
    message("bulk_write", requests=[{"operation": "update_one", "arguments": {"filter": {}}}]),
])
def test_fixed_protocol_rejects_unknown_commands_collections_and_argument_shapes(value):
    body, _, headers = envelope(value)
    with pytest.raises(gateway.GatewayRejected):
        gateway.authenticate(body, headers, SECRET, now=STAMP)


def test_registered_nested_lookup_and_raw_historical_collection_allowed_without_workflows():
    value = message("aggregate", pipeline=[{"$lookup": {"from": "people", "pipeline": [
        {"$unionWith": {"coll": "users", "pipeline": [{"$match": {"user_id": "fixture"}}]}}
    ], "as": "matched"}}])
    assert gateway.validate_request(value) is value
    assert gateway.validate_request(message("find", collection="field_study_links"))
    assert gateway.validate_request(message("find", collection="database_migration_reports"))


class Cursor:
    def __init__(self, rows):
        self.rows, self.closed = rows, False

    async def to_list(self, length):
        return self.rows if length is None else self.rows[:length]

    async def close(self):
        self.closed = True


class Collection:
    def __init__(self):
        self.calls = []
        self.cursor = None

    def find(self, **args):
        self.calls.append(("find", args))
        self.cursor = Cursor([{"_id": "one"}, {"_id": "two"}])
        return self.cursor

    def aggregate(self, **args):
        self.calls.append(("aggregate", args))
        self.cursor = Cursor([{"total": 2}])
        return self.cursor

    async def count_documents(self, **args):
        self.calls.append(("count", args))
        return 2

    async def create_indexes(self, models):
        self.calls.append(("indexes", [dict(model.document) for model in models]))
        return [model.document["name"] for model in models]

    async def bulk_write(self, requests, ordered):
        self.calls.append(("bulk", requests, ordered))
        return SimpleNamespace(bulk_api_result={"nMatched": 1, "nModified": 1, "writeErrors": []})

    def __getattr__(self, name):
        async def perform(**args):
            self.calls.append((name, args))
            if name == "insert_one":
                return InsertOneResult("fixture-id", True)
            if name == "insert_many":
                return InsertManyResult(["fixture-id"], True)
            if name in ("update_one", "update_many", "replace_one"):
                return UpdateResult({"n": 1, "nModified": 1, "updatedExisting": True}, True)
            if name.startswith("delete_"):
                return DeleteResult({"n": 1}, True)
            if name == "distinct":
                return ["a", "b"]
            return {"_id": "fixture-id", "private": "original-image"}
        return perform


class Database:
    def __init__(self):
        self.collection = Collection()

    def __getitem__(self, name):
        assert name in gateway.COLLECTIONS
        return self.collection

    async def command(self, name):
        assert name == "ping"
        return {"ok": 1}


@pytest.mark.parametrize("operation,arguments,expected", [
    ("find", {"filter": {"user_id": "fixture"}, "length": 1}, [{"_id": "one"}]),
    ("aggregate", {"pipeline": [{"$count": "total"}]}, [{"total": 2}]),
    ("count", {"filter": {}}, 2),
    ("distinct", {"key": "gender"}, ["a", "b"]),
    ("insert_one", {"document": {"id": "x"}}, {"inserted_id": "fixture-id"}),
    ("insert_many", {"documents": [{"id": "x"}]}, {"inserted_ids": ["fixture-id"]}),
    ("update_one", {"filter": {}, "update": {"$inc": {"used": 1}}}, {"n": 1, "nModified": 1, "updatedExisting": True}),
    ("update_many", {"filter": {}, "update": {"$set": {"flag": True}}}, {"n": 1, "nModified": 1, "updatedExisting": True}),
    ("replace_one", {"filter": {}, "replacement": {"id": "x"}}, {"n": 1, "nModified": 1, "updatedExisting": True}),
    ("delete_one", {"filter": {}}, {"n": 1}),
    ("delete_many", {"filter": {}}, {"n": 1}),
    ("find_one_and_update", {"filter": {}, "update": {"$inc": {"used": 1}}, "return_document": True},
     {"_id": "fixture-id", "private": "original-image"}),
])
def test_mapper_preserves_result_shapes_and_releases_cursors(operation, arguments, expected):
    db = Database()
    original = deepcopy(arguments)
    assert asyncio.run(gateway.execute_operation(db, message(operation, **arguments))) == expected
    if operation in ("find", "aggregate"):
        assert db.collection.cursor.closed
    if operation == "find_one_and_update":
        assert db.collection.calls[-1][1]["return_document"] == ReturnDocument.AFTER
    assert arguments == original


def test_index_and_bulk_mapping_uses_typed_driver_models():
    db = Database()
    request = message("create_indexes", models=[{"key": {"user_id": 1}, "name": "owner", "unique": True,
                                                "partialFilterExpression": {"active": True}}])
    original = deepcopy(request)
    assert asyncio.run(gateway.execute_operation(db, request)) == ["owner"]
    assert request == original
    assert db.collection.calls[-1][1][0]["partialFilterExpression"] == {"active": True}
    bulk = message("bulk_write", ordered=False, requests=[
        {"operation": "update_one", "arguments": {"filter": {"id": "x"}, "update": {"$inc": {"used": 1}}, "upsert": True}},
        {"operation": "delete_many", "arguments": {"filter": {"expired": True}}},
    ])
    assert asyncio.run(gateway.execute_operation(db, bulk))["nMatched"] == 1
    assert [type(item).__name__ for item in db.collection.calls[-1][1]] == ["UpdateOne", "DeleteMany"]
    assert db.collection.calls[-1][2] is False
    assert asyncio.run(gateway.execute_operation(db, message("ping", None))) == {"ok": 1}


def test_compact_dedup_never_caches_read_or_find_update_private_images():
    assert gateway.compact_result(message(), [{"private": "answer"}]) is None
    image = {"private": "old-erased-answer", "_id": "fixture"}
    compact = gateway.compact_result(message("find_one_and_update", filter={}, update={}), image)
    assert compact == gateway.COMMITTED_RESULT_UNAVAILABLE
    assert b"old-erased-answer" not in BSON.encode(compact)
    result = {"n": 1, "nModified": 1, "updatedExisting": True, "private": image}
    assert gateway.compact_result(message("update_one", filter={}, update={}), result) == {
        "n": 1, "nModified": 1, "updatedExisting": True}


def test_dispatch_passes_identical_durable_request_identity_without_a_second_mutation():
    body, _, headers = envelope(message("update_one", filter={}, update={"$inc": {"used": 1}}))
    accepted = gateway.authenticate(body, headers, SECRET, now=STAMP)
    state = {"used": 0, "cache": {}}

    async def atomic_rpc(request, *, request_id, body_sha256):
        assert request == accepted.request
        if request_id in state["cache"]:
            old_hash, result = state["cache"][request_id]
            assert old_hash == body_sha256
            return result
        state["used"] += 1
        result = {"n": 1, "nModified": 1}
        state["cache"][request_id] = body_sha256, gateway.compact_result(request, result)
        return result

    first = asyncio.run(gateway.dispatch(accepted, atomic_rpc))
    replay = asyncio.run(gateway.dispatch(accepted, atomic_rpc))
    assert first == replay and state["used"] == 1
    assert len(state["cache"]) == 1


def client(monkeypatch, rpc):
    monkeypatch.setenv("IMLADRI_POSTGRES_GATEWAY_SECRET", SECRET)
    monkeypatch.setattr(gateway.time, "time", lambda: STAMP)
    async def handle(request: Request):
        return await gateway.endpoint(request, SimpleNamespace(rpc=rpc))
    return TestClient(Starlette(routes=[Route(gateway.PATH, handle, methods=["GET", "POST"])]))


@pytest.mark.parametrize("error,status,kind", [
    (gateway.GatewayCommittedResultUnavailable("secret preimage"), 409, "committed_result_unavailable"),
    (DuplicateKeyError("secret unique-value", 11000), 409, "duplicate_key"),
    (OperationFailure("SQL contains private answer", 66), 400, "invalid_operation"),
    (RuntimeError("postgres://user:password@private"), 503, "repository_unavailable"),
])
def test_http_errors_are_sanitized_and_never_retry_mutations(monkeypatch, error, status, kind):
    calls = []
    async def rpc(*args, **kwargs):
        calls.append(kwargs)
        raise error
    body, headers, _ = envelope(message("find_one_and_update", filter={}, update={"$inc": {"used": 1}}))
    with client(monkeypatch, rpc) as http:
        response = http.post(gateway.PATH, content=body, headers=headers)
    assert response.status_code == status
    assert BSON(response.content).decode()["error"]["kind"] == kind
    assert all(token not in response.content for token in (b"secret", b"SQL", b"password"))
    assert len(calls) == 1


def test_http_success_no_store_and_bad_auth_never_reaches_database(monkeypatch):
    calls = []
    async def rpc(*args, **kwargs):
        calls.append(kwargs)
        return [{"_id": ObjectId(), "typed": Binary(b"fixture")}]
    body, headers, _ = envelope()
    with client(monkeypatch, rpc) as http:
        response = http.post(gateway.PATH, content=body, headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert BSON(response.content).decode()["result"][0]["typed"] == b"fixture"
        assert http.get(gateway.PATH).status_code == 404
        assert http.post(gateway.PATH, content=body, headers={"Content-Type": gateway.CONTENT_TYPE}).status_code == 404
    assert len(calls) == 1


def test_stream_and_response_bounds_fail_closed(monkeypatch):
    async def rpc(*args, **kwargs):
        raise AssertionError("oversized input reached SQL")
    body, headers, _ = envelope()
    with client(monkeypatch, rpc) as http:
        monkeypatch.setattr(gateway, "MAX_BODY_BYTES", len(body) - 1)
        response = http.post(gateway.PATH, content=body, headers=headers)
    assert response.status_code == 413
    with pytest.raises(gateway.GatewayRejected) as error:
        gateway.encode_success({"private": "a" * len(body)})
    assert error.value.kind == "response_too_large"


def test_valid_signature_cannot_smuggle_duplicate_bson_keys_or_invalid_bson():
    import struct
    # Two version keys decode ambiguously in BSON, so canonical roundtrip rejects.
    original = BSON.encode(message("ping", None))
    field = b"\x10version\x00\x01\x00\x00\x00"
    duplicate = struct.pack("<i", len(original) + len(field)) + original[4:-1] + field + b"\x00"
    for body in (duplicate, b"\x05\x00\x00\x00\x01"):
        signed = gateway.signed_headers(SECRET, STAMP, REQUEST_ID, body)
        raw = [(key.lower().encode(), value.encode()) for key, value in signed.items()]
        with pytest.raises(gateway.GatewayRejected) as error:
            gateway.authenticate(body, raw, SECRET, now=STAMP)
        assert error.value.status == 400


def test_partial_bulk_error_preserves_counts_without_private_sql_or_operations(monkeypatch):
    private = "erased-answer-must-never-be-cached"
    async def rpc(*args, **kwargs):
        raise BulkWriteError({"nMatched": 2, "nModified": 1, "nInserted": 0, "upserted": [],
            "writeErrors": [{"index": 1, "code": 11000, "errmsg": private, "op": {"private": private}}],
            "writeConcernErrors": [{"errmsg": private}], "private": private})
    body, headers, _ = envelope(message("bulk_write", requests=[
        {"operation": "delete_one", "arguments": {"filter": {"id": "fixture"}}}]))
    with client(monkeypatch, rpc) as http:
        response = http.post(gateway.PATH, content=body, headers=headers)
    payload = BSON(response.content).decode()
    assert response.status_code == 409 and payload["error"]["kind"] == "bulk_write"
    details = payload["error"]["details"]
    assert details["nMatched"] == 2 and details["nModified"] == 1
    assert details["writeErrors"] == [{"index": 1, "code": 11000, "errmsg": "Repository bulk write failed."}]
    assert details["writeConcernErrors"] == []
    assert private.encode() not in response.content


def test_gateway_admits_two_requests_and_rejects_third_before_reading_its_body(monkeypatch):
    import time
    monkeypatch.setenv("IMLADRI_POSTGRES_GATEWAY_SECRET", SECRET)
    monkeypatch.setattr(gateway.time, "time", lambda: STAMP)
    body, _, headers = envelope(message("ping", None))
    class Incoming:
        method = "POST"
        url = SimpleNamespace(path=gateway.PATH)
        scope = {"headers": headers}
        def __init__(self):
            self.body_reads = 0
        async def stream(self):
            self.body_reads += 1
            yield body
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def rpc(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                entered.set()
            await release.wait()
            return {"ok": 1}
        db = SimpleNamespace(rpc=rpc)
        requests = [Incoming(), Incoming(), Incoming()]
        first = [asyncio.create_task(gateway.endpoint(request, db)) for request in requests[:2]]
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            started = time.monotonic()
            rejected = await gateway.endpoint(requests[2], db)
            elapsed = time.monotonic() - started
            assert rejected.status_code == 503
            assert BSON(rejected.body).decode()["error"]["kind"] == "repository_busy"
            assert requests[2].body_reads == 0 and len(calls) == 2
            assert 0.15 <= elapsed < 1.5
        finally:
            release.set()
            results = await asyncio.gather(*first)
        assert all(response.status_code == 200 for response in results)
        accepted = await gateway.endpoint(requests[2], db)
        assert accepted.status_code == 200 and requests[2].body_reads == 1
    asyncio.run(run())


def test_cancelled_gateway_request_releases_admission_capacity(monkeypatch):
    monkeypatch.setenv("IMLADRI_POSTGRES_GATEWAY_SECRET", SECRET)
    monkeypatch.setattr(gateway.time, "time", lambda: STAMP)
    body, _, headers = envelope(message("ping", None))
    class Incoming:
        method = "POST"
        url = SimpleNamespace(path=gateway.PATH)
        scope = {"headers": headers}
        async def stream(self):
            yield body
    async def run():
        entered, blocked = asyncio.Event(), asyncio.Event()
        calls = 0
        async def rpc(*args, **kwargs):
            nonlocal calls
            calls += 1
            entered.set()
            await blocked.wait()
            return {"ok": 1}
        db = SimpleNamespace(rpc=rpc)
        original = asyncio.create_task(gateway.endpoint(Incoming(), db))
        await asyncio.wait_for(entered.wait(), 2)
        original.cancel()
        with pytest.raises(asyncio.CancelledError):
            await original
        entered.clear()
        followups = [asyncio.create_task(gateway.endpoint(Incoming(), db)) for _ in range(2)]
        try:
            for _ in range(100):
                if calls == 3:
                    break
                await asyncio.sleep(0.005)
            assert calls == 3, "Cancelled request leaked a gateway admission slot"
        finally:
            blocked.set()
            results = await asyncio.gather(*followups)
        assert all(response.status_code == 200 for response in results)
    asyncio.run(run())
