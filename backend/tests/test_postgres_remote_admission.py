"""Client backpressure matches the unchanged, fail-closed gateway admission."""
import asyncio
import hmac
import os
import time
from urllib.parse import urlsplit
from uuid import uuid4

from bson import BSON
import httpx
import pytest
from starlette.requests import Request

from backend import postgres_gateway as gateway, postgres_remote as remote_module
from backend.postgres_remote import RemotePostgresDatabase
from backend.postgres_store import PostgresDatabase, RepositoryUnavailable


SECRET = "only-local-synthetic-admission-gateway-secret"
ORIGIN = "only-local-synthetic-admission-origin"
URL = "https://imladri-fixture.onrender.com"
DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")


def reply(result=1):
    return httpx.Response(200, content=gateway.encode_success(result),
                          headers={"content-type": gateway.CONTENT_TYPE})


def busy():
    return httpx.Response(503, content=BSON.encode({"ok": False, "error": {"kind": "repository_busy"}}),
                          headers={"content-type": gateway.CONTENT_TYPE})


def duplicate_key_busy():
    raw = BSON.encode({"ok": False, "error": {"kind": "repository_busy"}})
    return (len(raw) + 5).to_bytes(4, "little") + raw[4:-1] + b"\x08ok\x00\x00\x00"


def client(transport):
    remote = RemotePostgresDatabase(URL, SECRET, ORIGIN, "fixture_admission", transport=transport)
    remote._verified = True
    return remote


def test_only_two_physical_sends_and_cancelled_waiter_mints_no_request(monkeypatch):
    async def run():
        active = maximum = calls = 0
        entered, release = asyncio.Event(), asyncio.Event()
        async def respond(request):
            nonlocal active, maximum, calls
            calls += 1
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                entered.set()
            try:
                await release.wait()
                return reply()
            finally:
                active -= 1
        remote = client(httpx.MockTransport(respond))
        identifiers = []
        def identifier():
            value = uuid4()
            identifiers.append(value)
            return value
        monkeypatch.setattr(remote_module, "uuid4", identifier)
        tasks = [asyncio.create_task(remote.users.count_documents({})) for _ in range(2)]
        try:
            await asyncio.wait_for(entered.wait(), 1)
            queued = asyncio.create_task(remote.users.count_documents({}))
            await asyncio.sleep(0.01)
            assert calls == len(identifiers) == 2 and remote._pending_requests == 3
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            assert remote._pending_requests == 2 and len(identifiers) == 2
            followup = asyncio.create_task(remote.users.count_documents({}))
            release.set()
            assert await asyncio.gather(*tasks, followup) == [1, 1, 1]
            assert maximum == 2 and calls == len(identifiers) == 3
            assert remote._pending_requests == 0 and remote._send_slots._value == 2
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await remote.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["timeout", "full", "active_cancellation"])
def test_bounded_queue_and_cancellation_release_capacity(monkeypatch, mode):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def respond(request):
            nonlocal calls
            calls += 1
            if calls == 2:
                entered.set()
            await release.wait()
            return reply()
        remote = client(httpx.MockTransport(respond))
        tasks = [asyncio.create_task(remote.users.count_documents({})) for _ in range(2)]
        try:
            await asyncio.wait_for(entered.wait(), 1)
            if mode == "timeout":
                monkeypatch.setattr(remote_module, "QUEUE_TIMEOUT_SECONDS", 0.02)
                with pytest.raises(RepositoryUnavailable, match="queue did not become available"):
                    await remote.users.count_documents({})
                assert calls == 2
            elif mode == "full":
                monkeypatch.setattr(remote_module, "MAX_PENDING_REQUESTS", 2)
                with pytest.raises(RepositoryUnavailable, match="queue is full"):
                    await remote.users.count_documents({})
                assert calls == 2
            else:
                tasks[0].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await tasks[0]
                followup = asyncio.create_task(remote.users.count_documents({}))
                for _ in range(100):
                    if calls == 3:
                        break
                    await asyncio.sleep(0.005)
                assert calls == 3
                tasks.append(followup)
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            assert remote._pending_requests == 0 and remote._send_slots._value == 2
            assert await remote.users.count_documents({}) == 1
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await remote.aclose()
    asyncio.run(run())


def test_queued_request_signs_after_admission(monkeypatch):
    async def run():
        clock = [1800000000]
        monkeypatch.setattr(remote_module.time, "time", lambda: clock[0])
        entered, release = asyncio.Event(), asyncio.Event()
        stamps = []
        async def respond(request):
            gateway.authenticate(request.content, request.headers.raw, SECRET, now=clock[0])
            stamps.append(int(request.headers["x-imladri-pg-timestamp"]))
            if len(stamps) == 2:
                entered.set()
            await release.wait()
            return reply()
        remote = client(httpx.MockTransport(respond))
        tasks = [asyncio.create_task(remote.users.count_documents({})) for _ in range(2)]
        try:
            await asyncio.wait_for(entered.wait(), 1)
            queued = asyncio.create_task(remote.users.count_documents({}))
            await asyncio.sleep(0.01)
            clock[0] += 61
            release.set()
            assert await asyncio.gather(*tasks, queued) == [1, 1, 1]
            assert stamps == [1800000000, 1800000000, 1800000061]
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await remote.aclose()
    asyncio.run(run())


def test_exact_busy_retry_keeps_uuid_body_and_refreshes_aged_signature(monkeypatch):
    async def run():
        clock, sent = [1800000000], []
        monkeypatch.setattr(remote_module.time, "time", lambda: clock[0])
        async def respond(request):
            gateway.authenticate(request.content, request.headers.raw, SECRET, now=clock[0])
            sent.append((request.content, dict(request.headers)))
            if len(sent) == 1:
                clock[0] += 61
                return busy()
            return reply({"n": 1, "nModified": 1, "updatedExisting": True})
        remote = client(httpx.MockTransport(respond))
        try:
            result = await remote.users.update_one({"_id": "synthetic"}, {"$inc": {"used": 1}})
            assert result.modified_count == 1 and len(sent) == 2
            assert sent[0][0] == sent[1][0]
            assert sent[0][1]["x-imladri-pg-request-id"] == sent[1][1]["x-imladri-pg-request-id"]
            assert sent[0][1]["x-imladri-pg-signature"] != sent[1][1]["x-imladri-pg-signature"]
            assert int(sent[1][1]["x-imladri-pg-timestamp"]) == clock[0]
        finally:
            await remote.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("deadline", [False, True], ids=["attempt_bound", "deadline"])
def test_busy_retry_is_bounded(monkeypatch, deadline):
    async def run():
        if deadline:
            monkeypatch.setattr(remote_module, "BUSY_RETRY_SECONDS", 0.01)
        sent = []
        async def respond(request):
            sent.append((request.content, request.headers["x-imladri-pg-request-id"]))
            return busy()
        remote = client(httpx.MockTransport(respond))
        try:
            with pytest.raises(RepositoryUnavailable, match="admission remained busy"):
                await remote.users.count_documents({})
            assert len(sent) == (1 if deadline else 4)
            assert len(set(sent)) == 1
            assert remote._pending_requests == 0 and remote._send_slots._value == 2
        finally:
            await remote.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("status,payload,content_type", [
    (503, {"ok": False, "error": {"kind": "repository_unavailable"}}, gateway.CONTENT_TYPE),
    (503, {"ok": 0, "error": {"kind": "repository_busy"}}, gateway.CONTENT_TYPE),
    (503, {"ok": False, "error": {"kind": "repository_busy", "extra": True}}, gateway.CONTENT_TYPE),
    (503, {"ok": False, "error": {"kind": "repository_busy"}, "extra": True}, gateway.CONTENT_TYPE),
    (503, {"ok": False, "error": "repository_busy"}, gateway.CONTENT_TYPE),
    (503, {"ok": False, "error": {"kind": "repository_busy"}}, "application/json"),
    (200, {"ok": False, "error": {"kind": "repository_busy"}}, gateway.CONTENT_TYPE),
    (409, {"ok": False, "error": {"kind": "committed_result_unavailable"}}, gateway.CONTENT_TYPE),
    (503, b"not-bson", gateway.CONTENT_TYPE),
    (503, duplicate_key_busy(), gateway.CONTENT_TYPE),
])
def test_other_or_malformed_outcomes_are_never_retried(status, payload, content_type):
    async def run():
        calls = []
        async def respond(request):
            calls.append(request)
            raw = payload if isinstance(payload, bytes) else BSON.encode(payload)
            return httpx.Response(status, content=raw, headers={"content-type": content_type})
        remote = client(httpx.MockTransport(respond))
        try:
            with pytest.raises(RepositoryUnavailable):
                await remote.users.update_one({"_id": "synthetic"}, {"$inc": {"used": 1}})
            assert len(calls) == 1
            assert remote._pending_requests == 0 and remote._send_slots._value == 2
        finally:
            await remote.aclose()
    asyncio.run(run())


def test_response_byte_cap_is_not_retried(monkeypatch):
    async def run():
        monkeypatch.setattr(remote_module, "LIMIT", 256)
        calls = []
        async def respond(request):
            calls.append(request)
            return httpx.Response(503, content=b"x" * 512, headers={"content-type": gateway.CONTENT_TYPE})
        remote = client(httpx.MockTransport(respond))
        try:
            with pytest.raises(RepositoryUnavailable, match="response exceeds"):
                await remote.users.count_documents({})
            assert len(calls) == 1
            assert remote._pending_requests == 0 and remote._send_slots._value == 2
        finally:
            await remote.aclose()
    asyncio.run(run())


def test_busy_retry_does_not_reset_existing_single_transport_retry():
    async def run():
        sent = []
        async def respond(request):
            sent.append((request.content, request.headers["x-imladri-pg-request-id"]))
            if len(sent) == 1:
                return busy()
            raise httpx.ReadError("synthetic lost acknowledgement", request=request)
        remote = client(httpx.MockTransport(respond))
        try:
            with pytest.raises(RepositoryUnavailable, match="connection did not complete"):
                await remote.users.update_one({"_id": "synthetic"}, {"$inc": {"used": 1}})
            assert len(sent) == 3 and len(set(sent)) == 1
        finally:
            await remote.aclose()
    asyncio.run(run())


@pytest.mark.skipif(not DSN, reason="Needs explicitly configured isolated loopback PostgreSQL.")
@pytest.mark.parametrize("competing_clients", [False, True], ids=["one_client_fanout", "external_busy"])
def test_actual_gateway_native_pg_admission_and_exactly_once_mutation(monkeypatch, competing_clients):
    assert urlsplit(DSN).hostname in ("127.0.0.1", "localhost", "::1")
    monkeypatch.setenv("IMLADRI_POSTGRES_GATEWAY_SECRET", SECRET)
    async def run():
        native = PostgresDatabase(DSN, "fixture_admission_" + uuid4().hex, allow_local_plaintext=True)
        maximum = active = blocked = 0
        hold, entered, release, rejected = False, asyncio.Event(), asyncio.Event(), asyncio.Event()
        outcomes, sent = [], []
        original = native.rpc
        async def rpc(request, **kwargs):
            nonlocal maximum, active, blocked
            active += 1
            maximum = max(maximum, active)
            try:
                if hold and request["operation"] == "ping":
                    blocked += 1
                    if blocked == 2:
                        entered.set()
                    await release.wait()
                else:
                    await asyncio.sleep(0.04)
                return await original(request, **kwargs)
            finally:
                active -= 1
        native.rpc = rpc
        async def app(scope, receive, send):
            supplied = [value for key, value in scope["headers"] if key.lower() == b"x-imladri-origin"]
            assert len(supplied) == 1 and hmac.compare_digest(supplied[0], ORIGIN.encode())
            request = Request(scope, receive)
            raw = await request.body()
            value = BSON(raw).decode()
            if value["operation"] == "update_one":
                sent.append((raw, request.headers["x-imladri-pg-request-id"]))
            response = await gateway.endpoint(request, native)
            outcomes.append(response.status_code)
            if response.status_code == 503:
                assert BSON(response.body).decode() == {"ok": False, "error": {"kind": "repository_busy"}}
                rejected.set()
            await response(scope, receive, send)
        remote = RemotePostgresDatabase(URL, SECRET, ORIGIN, native.name, transport=httpx.ASGITransport(app=app))
        competitors = []
        external = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
        try:
            await native.users.insert_one({"_id": "synthetic", "used": 0})
            await remote.command("ping")
            if competing_clients:
                hold = True
                body = BSON.encode({"version": 1, "operation": "ping", "arguments": {}})
                for _ in range(2):
                    headers = gateway.signed_headers(SECRET, int(time.time()), str(uuid4()), body)
                    headers["X-Imladri-Origin"] = ORIGIN
                    competitors.append(asyncio.create_task(external.post(URL + gateway.PATH, content=body, headers=headers)))
                await asyncio.wait_for(entered.wait(), 2)
                mutation = asyncio.create_task(remote.users.update_one({"_id": "synthetic"}, {"$inc": {"used": 1}}))
                await asyncio.wait_for(rejected.wait(), 2)
                assert (await native.users.find_one({"_id": "synthetic"}))["used"] == 0
                release.set()
                assert all(response.status_code == 200 for response in await asyncio.gather(*competitors))
                assert (await mutation).modified_count == 1
                assert len(sent) == 2 and sent[0] == sent[1]
                assert (await native.users.find_one({"_id": "synthetic"}))["used"] == 1
                def claims(cursor, now):
                    cursor.execute("SELECT count(*) FROM imladri_app.rpc_requests WHERE namespace=%s AND request_id=%s",
                                   (native.name, sent[0][1]))
                    return cursor.fetchone()[0]
                assert await native._run(claims) == 1
            else:
                assert await asyncio.gather(*(remote.users.count_documents({}) for _ in range(48))) == [1] * 48
                assert set(outcomes) == {200}
            assert maximum == 2 and remote._pending_requests == 0 and remote._send_slots._value == 2
        finally:
            release.set()
            await asyncio.gather(*competitors, return_exceptions=True)
            await external.aclose()
            await remote.aclose()
            def cleanup(cursor, now):
                for table in ("rpc_requests", "unique_keys", "indexes", "documents", "collections"):
                    cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (native.name,))
            await native._run(cleanup, write="__fixture_cleanup")
            await native.aclose()
    asyncio.run(run())
