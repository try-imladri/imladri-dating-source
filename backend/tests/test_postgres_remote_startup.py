"""Real signed repository bootstrap, including PyMongo's optional bulk flags."""
import asyncio
from collections import Counter
import hashlib
import hmac
import importlib
import os
from urllib.parse import urlsplit
from uuid import uuid4

from bson import BSON
import httpx
from pymongo import ReplaceOne, UpdateMany, UpdateOne
import pytest
from starlette.requests import Request

from backend import database, postgres_gateway as gateway
from backend.postgres_remote import RemotePostgresDatabase
from backend.postgres_store import PostgresDatabase


DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
SECRET = "only-local-synthetic-startup-gateway-secret"
ORIGIN = "only-local-synthetic-startup-origin"
URL = "https://imladri-fixture.onrender.com"


@pytest.mark.parametrize("model_class", [UpdateOne, UpdateMany, ReplaceOne])
@pytest.mark.parametrize("upsert", [None, False, True], ids=["omitted", "false", "true"])
def test_bulk_transport_preserves_optional_upsert_semantics(model_class, upsert):
    """Default None is omission; explicit false/true remain typed signed BSON."""
    async def run():
        captured = []
        def respond(request):
            envelope = gateway.authenticate(request.content, request.headers.raw, SECRET)
            value = envelope.request
            if value["operation"] == "ping":
                result = {"ok": 1, "backend": "postgresql", "namespace": "fixture_bulk_flags"}
            else:
                assert value["operation"] == "bulk_write"
                captured.append(value)
                result = {"nInserted": 0, "nMatched": 1, "nModified": 1, "nRemoved": 0,
                          "nUpserted": 0, "upserted": [], "writeErrors": [], "writeConcernErrors": []}
            return httpx.Response(200, content=gateway.encode_success(result),
                                  headers={"content-type": gateway.CONTENT_TYPE})
        remote = RemotePostgresDatabase(URL, SECRET, ORIGIN, "fixture_bulk_flags",
                                        transport=httpx.MockTransport(respond))
        kwargs = {} if upsert is None else {"upsert": upsert}
        payload = {"value": 2} if model_class is ReplaceOne else {"$set": {"value": 2}}
        model = model_class({"_id": "synthetic"}, payload, **kwargs)
        assert model._upsert is upsert
        try:
            result = await remote.users.bulk_write([model], ordered=False)
            assert result.matched_count == result.modified_count == 1
            assert len(captured) == 1
            arguments = captured[0]["arguments"]["requests"][0]["arguments"]
            if upsert is None:
                assert "upsert" not in arguments
            else:
                assert arguments["upsert"] is upsert
                assert type(arguments["upsert"]) is bool
            assert captured[0]["arguments"]["ordered"] is False
        finally:
            await remote.aclose()
    asyncio.run(run())


def test_gateway_still_rejects_explicit_null_upsert():
    request = {"version": 1, "operation": "bulk_write", "collection": "users", "arguments": {
        "requests": [{"operation": "update_one", "arguments": {
            "filter": {"_id": "synthetic"}, "update": {"$set": {"value": 2}}, "upsert": None}}]}}
    with pytest.raises(gateway.GatewayRejected):
        gateway.validate_request(request)


@pytest.mark.skipif(not DSN, reason="Needs explicitly configured isolated loopback PostgreSQL.")
def test_full_remote_application_startup_cold_and_warm_native_postgres(monkeypatch):
    """Only sockets are replaced: real startup, RPC, indexes, seed and SQL run."""
    assert urlsplit(DSN).hostname in ("127.0.0.1", "localhost", "::1")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("IMLADRI_DATABASE_MAINTENANCE", "off")
    monkeypatch.setenv("IMLADRI_POSTGRES_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_fixture")
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_fixture")

    async def run():
        namespace = "fixture_remote_startup_" + uuid4().hex
        native = PostgresDatabase(DSN, namespace, allow_local_plaintext=True)
        counts, statuses, stages = Counter(), [], []
        async def app(scope, receive, send):
            supplied = [value for key, value in scope["headers"] if key.lower() == b"x-imladri-origin"]
            assert len(supplied) == 1 and hmac.compare_digest(supplied[0], ORIGIN.encode())
            request = Request(scope, receive)
            value = BSON(await request.body()).decode()
            counts[(value["operation"], value.get("collection"))] += 1
            response = await gateway.endpoint(request, native)
            statuses.append(response.status_code)
            await response(scope, receive, send)
        remote = RemotePostgresDatabase(URL, SECRET, ORIGIN, namespace,
                                        transport=httpx.ASGITransport(app=app))
        monkeypatch.setattr(database, "open_database", lambda: (remote, remote))
        server = importlib.import_module("backend.server")
        monkeypatch.setattr(server, "db", remote)
        monkeypatch.setattr(server, "client", remote)
        prior_maintenance = getattr(server.app.state, "postgres_maintenance", None)
        # Public network APIs are never part of startup. Make an accidental call fail.
        def forbidden(*args, **kwargs):
            raise AssertionError("Application startup attempted an external provider call")
        monkeypatch.setattr(server.requests.sessions.Session, "request", forbidden)
        for target, name in ((server, "ensure_indexes"), (server, "seed_base"),
                             (server.game_plans, "repair_deleted_outcomes")):
            original = getattr(target, name)
            def wrap(original=original, name=name):
                async def checked(*args, **kwargs):
                    result = await original(*args, **kwargs)
                    stages.append(name)
                    return result
                return checked
            monkeypatch.setattr(target, name, wrap())

        async def stop_background():
            tasks = [getattr(server.app.state, name, None) for name in
                     ("closure_recovery", "answer_history_recovery")]
            assert all(isinstance(task, asyncio.Task) for task in tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            assert all(task.done() for task in tasks)
            assert getattr(server.app.state, "postgres_maintenance", None) is prior_maintenance

        async def ready():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                         base_url="http://only-local-asgi") as client:
                response = await client.get("/api/ready")
                assert response.status_code == 200 and response.json() == {"ready": True}

        def data_snapshot(cursor, now):
            cursor.execute("SELECT collection,raw_bson FROM imladri_app.documents WHERE namespace=%s "
                           "ORDER BY collection COLLATE \"C\",id_key", (namespace,))
            rows = cursor.fetchall()
            digest = hashlib.sha256()
            counts = Counter()
            for collection, raw in rows:
                counts[collection] += 1
                digest.update(BSON.encode({"collection": collection, "bson": bytes(raw)}))
            cursor.execute("SELECT count(*) FROM imladri_app.indexes WHERE namespace=%s", (namespace,))
            return dict(counts), digest.hexdigest(), cursor.fetchone()[0]

        try:
            await asyncio.wait_for(server.startup(), 300)
            await ready()
            await stop_background()
            cold = await native._run(data_snapshot)
            assert cold[0]["archetypes"] == 121 and cold[0]["figures"] == 500
            assert cold[2] >= 100
            assert (await native.settings.find_one({"_id": "repair_deleted_game_plan_outcomes_v1"}))["done"] is True
            await asyncio.wait_for(server.startup(), 300)
            await ready()
            await server.shutdown_db_client()
            assert await native._run(data_snapshot) == cold
            assert stages == ["ensure_indexes", "seed_base", "repair_deleted_outcomes"] * 2
            assert statuses and set(statuses) == {200}
            assert counts[("bulk_write", "archetypes")] >= 4
            assert counts[("bulk_write", "figures")] >= 4
            assert counts[("update_many", "couples_pairs")] == 2
            assert all(getattr(server.app.state, name).done() for name in
                       ("closure_recovery", "answer_history_recovery"))
        finally:
            for name in ("closure_recovery", "answer_history_recovery"):
                task = getattr(server.app.state, name, None)
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            await remote.aclose()
            def cleanup(cursor, now):
                for table in ("rpc_requests", "unique_keys", "indexes", "documents", "collections"):
                    cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (namespace,))
            await native._run(cleanup, write="__fixture_cleanup")
            await native.aclose()
    asyncio.run(run())
