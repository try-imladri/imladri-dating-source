"""Real loopback PostgreSQL through the signed HTTPS-shaped repository transport.

ASGITransport replaces only the network socket. BSON, signatures, HTTP errors,
native PostgreSQL transactions and independent connection pools are real.
"""
import asyncio
from datetime import datetime
import hmac
import os
import ssl
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from bson import BSON, Binary, Int64, ObjectId
import httpx
from pymongo import IndexModel, ReturnDocument
from pymongo.errors import BulkWriteError, DuplicateKeyError
import pytest
from starlette.requests import Request
from starlette.responses import Response

from backend import postgres_gateway as gateway
from backend.postgres_migration import connection_options
from backend.postgres_remote import RemotePostgresDatabase
from backend.postgres_store import PostgresDatabase, RepositoryUnavailable


DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
integration = pytest.mark.skipif(not DSN, reason="Needs explicitly configured isolated loopback PostgreSQL.")
SECRET = "only-synthetic-test-32-byte-gateway-secret"
ORIGIN = "only-synthetic-origin-gate"
URL = "https://imladri-fixture.onrender.com"


def app_for(db):
    async def app(scope, receive, send):
        supplied = [value for key, value in scope["headers"] if key.lower() == b"x-imladri-origin"]
        if len(supplied) != 1 or not hmac.compare_digest(supplied[0], ORIGIN.encode()):
            return await Response(status_code=404)(scope, receive, send)
        response = await gateway.endpoint(Request(scope, receive), db)
        return await response(scope, receive, send)
    return app


async def cleanup(db):
    def erase(cursor, now):
        for table in ("rpc_requests", "unique_keys", "indexes", "documents"):
            cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (db.name,))
    await db._run(erase, write="__fixture_cleanup")


def exercise(callback, monkeypatch):
    assert DSN and urlsplit(DSN).hostname in ("127.0.0.1", "localhost", "::1")
    monkeypatch.setenv("IMLADRI_POSTGRES_GATEWAY_SECRET", SECRET)
    async def run():
        namespace = "fixture_rpc_" + uuid4().hex
        first = PostgresDatabase(DSN, namespace, allow_local_plaintext=True)
        second = PostgresDatabase(DSN, namespace, allow_local_plaintext=True)
        try:
            await callback(first, second)
        finally:
            await cleanup(first)
            await first.aclose()
            await second.aclose()
    asyncio.run(run())


class DropFirstCommittedResponse(httpx.AsyncBaseTransport):
    """Lose the HTTP response AFTER the native transaction has committed."""
    def __init__(self, app):
        self.inner = httpx.ASGITransport(app=app)
        self.requests = []

    async def handle_async_request(self, request):
        body = await request.aread()
        mutation = BSON(body).decode().get("operation") == "update_one"
        if mutation:
            self.requests.append((dict(request.headers), body))
        response = await self.inner.handle_async_request(request)
        await response.aread()
        if mutation and len(self.requests) == 1:
            assert response.status_code == 200
            raise httpx.ReadError("Synthetic committed response loss", request=request)
        return response

    async def aclose(self):
        await self.inner.aclose()


async def signed_call(client, request, *, request_id=None, secret=SECRET, origin=ORIGIN):
    body = BSON.encode(request)
    headers = gateway.signed_headers(secret, int(time.time()), request_id or str(uuid4()), body)
    headers["X-Imladri-Origin"] = origin
    return await client.post(URL + gateway.PATH, content=body, headers=headers)


@integration
def test_lost_committed_response_reuses_uuid_body_and_debits_exactly_once(monkeypatch):
    async def run(db, second):
        await db.users.insert_one({"_id": "member", "credits": 100, "used": 0})
        transport = DropFirstCommittedResponse(app_for(db))
        remote = RemotePostgresDatabase(URL, SECRET, ORIGIN, db.name, transport=transport)
        try:
            result = await remote.users.update_one({"_id": "member", "credits": {"$gte": 1}},
                {"$inc": {"credits": -1, "used": 1}})
            assert result.matched_count == result.modified_count == 1
            saved = await second.users.find_one({"_id": "member"})
            assert (saved["credits"], saved["used"]) == (99, 1)
            assert len(transport.requests) == 2
            headers0, body0 = transport.requests[0]
            headers1, body1 = transport.requests[1]
            assert body0 == body1 and headers0 == headers1
            def journal(cursor, now):
                cursor.execute("SELECT count(*),min(body_sha256) FROM imladri_app.rpc_requests WHERE namespace=%s AND request_id=%s",
                               (db.name, headers0["x-imladri-pg-request-id"]))
                return cursor.fetchone()
            count, digest = await db._run(journal)
            assert count == 1 and digest == __import__("hashlib").sha256(body0).hexdigest()
        finally:
            await remote.aclose()
    exercise(run, monkeypatch)


@integration
def test_gateway_aggregate_keeps_one_snapshot_across_consent_and_lookup(monkeypatch):
    async def run(db, second):
        await db.users.insert_one({"_id": "member", "user_id": "fixture", "consent": True})
        await db.people.insert_one({"_id": "profile", "user_id": "fixture", "private": "synthetic-profile"})
        # Initialize the independent writer before holding the read snapshot;
        # one-time schema DDL must not become the barrier being exercised.
        await second.command("ping")
        first_scan, resume = threading.Event(), threading.Event()
        original_scan = db._scan
        def scan(cursor, collection, query=None):
            rows = original_scan(cursor, collection, query)
            if collection == "users" and not first_scan.is_set():
                first_scan.set()
                assert resume.wait(5), "Fixture did not release lookup barrier"
            return rows
        monkeypatch.setattr(db, "_scan", scan)
        pipeline = [{"$match": {"user_id": "fixture"}}, {"$lookup": {
            "from": "people", "localField": "user_id", "foreignField": "user_id", "as": "profiles"}}]
        remote = RemotePostgresDatabase(URL, SECRET, ORIGIN, db.name, transport=httpx.ASGITransport(app=app_for(db)))
        task = asyncio.create_task(remote.users.aggregate(pipeline).to_list(None))
        try:
            assert await asyncio.to_thread(first_scan.wait, 4), "Aggregate did not reach real PostgreSQL scan"
            await second.users.update_one({"_id": "member"}, {"$set": {"consent": False}})
            await second.people.delete_one({"_id": "profile"})
            resume.set()
            result = await task
            assert result[0]["consent"] is True
            assert [row["_id"] for row in result[0]["profiles"]] == ["profile"]
            latest = await remote.users.aggregate(pipeline).to_list(None)
            assert latest[0]["consent"] is False and latest[0]["profiles"] == []
        finally:
            resume.set()
            await asyncio.gather(task, return_exceptions=True)
            await remote.aclose()
    exercise(run, monkeypatch)


@integration
def test_remote_handshake_rejects_misbound_namespace_before_application_write(monkeypatch):
    async def run(db, second):
        await db.users.insert_one({"_id": "member", "used": 0})
        remote = RemotePostgresDatabase(URL, SECRET, ORIGIN, "a_different_fixture_namespace",
            transport=httpx.ASGITransport(app=app_for(db)))
        try:
            with pytest.raises(RepositoryUnavailable):
                await remote.users.update_one({"_id": "member"}, {"$inc": {"used": 1}})
            assert (await second.users.find_one({"_id": "member"}))["used"] == 0
        finally:
            await remote.aclose()
    exercise(run, monkeypatch)


@integration
def test_private_find_update_image_erased_then_replay_is409_without_cache(monkeypatch):
    async def run(db, second):
        private = "synthetic-erased-intimacy-answer"
        await db.users.insert_one({"_id": "member", "private": private, "used": 0})
        value = {"version": 1, "operation": "find_one_and_update", "collection": "users", "arguments": {
            "filter": {"_id": "member"}, "update": {"$inc": {"used": 1}}, "return_document": True}}
        identifier = str(uuid4())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(db))) as client:
            first = await signed_call(client, value, request_id=identifier)
            assert first.status_code == 200
            assert BSON(first.content).decode()["result"]["private"] == private
            await second.users.update_one({"_id": "member"}, {"$unset": {"private": ""}})
            again = await signed_call(client, value, request_id=identifier)
        assert again.status_code == 409
        assert BSON(again.content).decode()["error"]["kind"] == "committed_result_unavailable"
        assert private.encode() not in again.content
        assert (await db.users.find_one({"_id": "member"}))["used"] == 1
        def raw_journal(cursor, now):
            cursor.execute("SELECT compact_result,compact_error FROM imladri_app.rpc_requests WHERE namespace=%s", (db.name,))
            return cursor.fetchall()
        journal = await db._run(raw_journal)
        assert len(journal) == 1 and journal[0][1] is None
        assert BSON(bytes(journal[0][0])).decode()["result"] == gateway.COMMITTED_RESULT_UNAVAILABLE
        assert all(private.encode() not in bytes(cell) for row in journal for cell in row if cell)
    exercise(run, monkeypatch)


@integration
def test_same_uuid_concurrency_and_changed_body_cannot_reapply_or_rebind(monkeypatch):
    async def run(db, second):
        await db.users.insert_one({"_id": "member", "used": 0})
        identifier = str(uuid4())
        value = {"version": 1, "operation": "update_one", "collection": "users", "arguments": {
            "filter": {"_id": "member"}, "update": {"$inc": {"used": 1}}}}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(db))) as client:
            responses = await asyncio.gather(*[signed_call(client, value, request_id=identifier) for _ in range(8)])
            assert all(response.status_code == 200 for response in responses)
            altered = {**value, "arguments": {"filter": {"_id": "member"}, "update": {"$inc": {"used": 100}}}}
            conflict = await signed_call(client, altered, request_id=identifier)
        assert conflict.status_code == 409
        assert BSON(conflict.content).decode()["error"]["kind"] == "request_id_conflict"
        assert (await second.users.find_one({"_id": "member"}))["used"] == 1
    exercise(run, monkeypatch)


@integration
def test_bson_types_unique_errors_partial_bulk_and_snapshot_reads_roundtrip(monkeypatch):
    async def run(db, second):
        remote = RemotePostgresDatabase(URL, SECRET, ORIGIN, db.name, transport=httpx.ASGITransport(app=app_for(db)))
        try:
            await remote.users.create_indexes([IndexModel("user_id", unique=True, name="user_id")])
            original = {"_id": ObjectId(), "user_id": "one", "at": datetime(2026, 11, 10),
                        "n": Int64(7), "blob": Binary(b"\0fixture"), "nul": "a\x00b"}
            inserted = await remote.users.insert_one(original)
            assert inserted.inserted_id == original["_id"]
            assert BSON.encode(await remote.users.find_one({"user_id": "one"})) == BSON.encode(original)
            with pytest.raises(DuplicateKeyError):
                await remote.users.insert_one({"_id": "duplicate", "user_id": "one"})
            from pymongo import InsertOne
            with pytest.raises(BulkWriteError) as error:
                await remote.users.bulk_write([InsertOne({"_id": "two", "user_id": "two"}),
                    InsertOne({"_id": "conflict", "user_id": "one"}), InsertOne({"_id": "three", "user_id": "three"})], ordered=False)
            assert error.value.details["nInserted"] == 2
            assert error.value.details["writeErrors"][0]["index"] == 1
            assert await remote.users.count_documents({}) == 3
            assert await remote.users.distinct("user_id") == await second.users.distinct("user_id")
        finally:
            await remote.aclose()
    exercise(run, monkeypatch)


@integration
def test_cross_namespace_data_and_request_ids_are_isolated(monkeypatch):
    async def run(db, second):
        other = PostgresDatabase(DSN, "fixture_rpc_peer_" + uuid4().hex, allow_local_plaintext=True)
        try:
            await db.users.insert_one({"_id": "same", "private": "namespace-one", "used": 0})
            await other.users.insert_one({"_id": "same", "private": "namespace-two", "used": 0})
            value = {"version": 1, "operation": "update_one", "collection": "users", "arguments": {
                "filter": {"_id": "same"}, "update": {"$inc": {"used": 1}}}}
            identifier = str(uuid4())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(db))) as first_client:
                assert (await signed_call(first_client, value, request_id=identifier)).status_code == 200
                namespace_attack = {**value, "namespace": other.name}
                assert (await signed_call(first_client, namespace_attack)).status_code == 400
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(other))) as second_client:
                assert (await signed_call(second_client, value, request_id=identifier)).status_code == 200
            assert (await db.users.find_one({"_id": "same"})) == {"_id": "same", "private": "namespace-one", "used": 1}
            assert (await other.users.find_one({"_id": "same"})) == {"_id": "same", "private": "namespace-two", "used": 1}
        finally:
            await cleanup(other)
            await other.aclose()
    exercise(run, monkeypatch)


@integration
def test_invalid_gateway_auth_cannot_claim_nonce_or_write_postgres(monkeypatch):
    async def run(db, second):
        await db.users.insert_one({"_id": "member", "used": 0})
        value = {"version": 1, "operation": "update_one", "collection": "users", "arguments": {
            "filter": {"_id": "member"}, "update": {"$inc": {"used": 1}}}}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(db))) as client:
            denied = await signed_call(client, value, secret="wrong-but-32-byte-fixture-secret-value")
            origin_denied = await signed_call(client, value, origin="incorrect-origin")
        assert denied.status_code == origin_denied.status_code == 404
        assert (await second.users.find_one({"_id": "member"}))["used"] == 0
        def nonce_count(cursor, now):
            cursor.execute("SELECT count(*) FROM imladri_app.rpc_requests WHERE namespace=%s", (db.name,))
            return cursor.fetchone()[0]
        assert await db._run(nonce_count) == 0
    exercise(run, monkeypatch)


@pytest.mark.parametrize("url", ["http://imladri-fixture.onrender.com", "https://evil.example", "https://x.onrender.com.evil.example",
    "https://user:secret@imladri-fixture.onrender.com", "https://imladri-fixture.onrender.com/path", "https://imladri-fixture.onrender.com?secret=x"])
def test_remote_transport_rejects_non_https_or_ambiguous_target(url):
    with pytest.raises(ValueError):
        RemotePostgresDatabase(url, SECRET, ORIGIN, "fixture")


def test_external_pg_profile_cannot_disable_ca_or_hostname_verification():
    with pytest.raises(ValueError):
        connection_options("postgresql://fixture:fixture@untrusted.example/fixture?sslmode=disable", allow_local_plaintext=True)
    for mode in ("require", "verify-full"):
        settings = connection_options("postgresql://fixture:fixture@fixture.onrender.com/fixture?sslmode=" + mode)
        assert settings["ssl_context"].verify_mode == ssl.CERT_REQUIRED
        assert settings["ssl_context"].check_hostname is True


def test_redirects_never_send_gateway_credential_to_another_origin():
    async def run():
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(302, headers={"Location": "https://another-origin.example/private"})
        remote = RemotePostgresDatabase(URL, SECRET, ORIGIN, "fixture", transport=httpx.MockTransport(respond))
        try:
            with pytest.raises(RepositoryUnavailable):
                await remote.command("ping")
            assert len(requests) == 1 and requests[0].url.host == "imladri-fixture.onrender.com"
        finally:
            await remote.aclose()
    asyncio.run(run())
