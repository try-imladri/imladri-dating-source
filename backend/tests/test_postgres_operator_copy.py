"""Fixed operator business paths against disposable Mongo replica set and PG."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
import secrets
from urllib.parse import quote
from uuid import uuid4

from bson import BSON, Decimal128
from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from backend import postgres_operator as worker, postgres_snapshot, postgres_store
from backend.postgres_cutover import snapshot
from backend.tests.test_postgres_operator_control import owner_database, environment, request, fence
from backend.tests.test_postgres_snapshot import source_fixture


RS = os.getenv("IMLADRI_SNAPSHOT_TEST_MONGO_URL")
DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
integration = pytest.mark.skipif(not RS or not DSN, reason="Requires disposable loopback Mongo replica set and PG.")


def local_config(database, options, logical):
    password = quote(options["password"], safe="")
    dsn = f'postgresql://{options["user"]}:{password}@127.0.0.1:{options["port"]}/{options["database"]}?sslmode=disable'
    return {"owner_dsn": dsn, "owner_options": options,
            "sources": {logical: {"mongo_url": RS, "database": database.name}},
            "role_options": {}, "role_dsns": {}}


def local_only(monkeypatch):
    original_barrier, original_view = postgres_snapshot.create_majority_barrier, postgres_snapshot.pinned_source
    original_store = postgres_store.PostgresDatabase

    def mongo(config, logical):
        client = AsyncIOMotorClient(RS, serverSelectionTimeoutMS=3000, connectTimeoutMS=20000, socketTimeoutMS=20000)
        return client, client[config["sources"][logical]["database"]], {"explicit_loopback_plaintext_fixture": True}

    async def barrier(*args, **kwargs):
        return await original_barrier(*args, **kwargs, allow_local_plaintext=True)

    @asynccontextmanager
    async def view(*args, **kwargs):
        async with original_view(*args, **kwargs, allow_local_plaintext=True) as result:
            yield result

    monkeypatch.setattr(worker, "_mongo", mongo)
    monkeypatch.setattr(postgres_snapshot, "create_majority_barrier", barrier)
    monkeypatch.setattr(postgres_snapshot, "pinned_source", view)
    monkeypatch.setattr(postgres_store, "PostgresDatabase", lambda *args, **kwargs:
                        original_store(*args, **kwargs, allow_local_plaintext=True))
    return original_store


@integration
@pytest.mark.parametrize("application,logical,collection", [
    ("personal", "personal_live", "users"), ("market", "market_live", "listings")])
def test_fixed_operator_copies_all_active_records_with_real_barrier_and_bson_parity(
        owner_database, monkeypatch, application, logical, collection):
    _, options = owner_database
    store_type = local_only(monkeypatch)

    async def run():
        async with source_fixture() as source:
            await source[collection].insert_one({"_id": "one", "value": "synthetic", "nested": {"ordered": [1, 2]}})
            config = local_config(source, options, logical)
            env = environment()
            body = worker.validate_request(request("copy_" + application, source=logical, fence=fence(env, logical)), env)
            result = await worker.copy_application(config, body, None, application=application)
            assert result["status"] == "completed_frozen_copy" and result["application"] == application
            if application == "market":
                from backend.postgres_cutover import _digest
                counts = result["market_coordinate_compatibility"]
                assert counts["lat_types"] == counts["lng_types"] == {"missing": 1}
                assert counts["pinned_final_copy_source"] is True and counts["decimal_coordinate_records"] == 0
                assert result["receipt_fingerprint"] == _digest({key: value for key, value in result.items()
                                                               if key != "receipt_fingerprint"})
            assert worker.safe_result(result)
            target = store_type(config["owner_dsn"], worker.SOURCE_DATABASES[logical],
                                allow_local_plaintext=True, initialize_schema=True)
            try:
                actual = await target[collection].find_one({"_id": "one"})
                expected = await source[collection].find_one({"_id": "one"})
                assert BSON.encode(actual) == BSON.encode(expected)
                assert "database_migration_reports" in await target.list_collection_names()
            finally:
                await target.aclose()
    asyncio.run(run())


@integration
def test_fixed_archive_keeps_unknown_collection_and_view_definition_out_of_active_namespace(owner_database, monkeypatch):
    connection, options = owner_database
    local_only(monkeypatch)

    async def run():
        async with source_fixture() as source:
            await source["legacy unknown records"].insert_one({"_id": "one", "answer": "synthetic archive only"})
            await source.command({"create": "saved legacy view", "viewOn": "legacy unknown records", "pipeline": []})
            config = local_config(source, options, "legacy_staging")
            config["sources"]["legacy_staging"]["control_database"] = "imladri_migration_control"
            env = environment()
            body = worker.validate_request(request("archive_legacy", source="legacy_staging", fence=fence(env, "legacy_staging")), env)
            result = await worker.archive_legacy(config, body)
            assert result["receipt"]["status"] == "verified_legacy_archive"
            assert result["active_application_records_written"] == 0 and worker.safe_result(result)
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT raw_bson FROM imladri_archive.documents WHERE source_database=%s AND collection=%s",
                               (source.name, "legacy unknown records"))
                assert BSON(bytes(cursor.fetchone()[0])).decode()["answer"] == "synthetic archive only"
                cursor.execute("SELECT to_regclass('imladri_app.documents')")
                assert cursor.fetchone()[0] is None
                connection.rollback()
            finally:
                cursor.close()
            replay = await worker.archive_legacy(config, body)
            assert replay["replay_verified"] is True and replay["receipt_unchanged"] is True
    asyncio.run(run())


def test_discovery_is_authorized_name_only_and_does_not_claim_cluster_wide_access(monkeypatch):
    commands, closed = [], []

    class Admin:
        async def command(self, command):
            commands.append(command)
            return {"databases": [{"name": "imladri_staging"}, {"name": "imladri_production"}]}

    class Client:
        admin = Admin()
        def close(self):
            closed.append(True)

    monkeypatch.setattr(worker, "_mongo", lambda *_: (Client(), None, {"verified_tls": True}))
    result = asyncio.run(worker.discover_sources({"sources": {"legacy_staging": {}}}))
    assert commands == [{"listDatabases": 1, "nameOnly": True, "authorizedDatabases": True, "maxTimeMS": 20000}]
    assert result["sources"]["legacy_staging"]["application_records_read"] == 0
    assert result["all_account_clusters_discovered"] is False and closed == [True]
    assert worker.safe_result(result) and "mongodb://" not in json.dumps(result)


@integration
def test_market_decimal_coordinate_correction_after_live_inventory_blocks_before_target_writes(owner_database, monkeypatch):
    connection, options = owner_database
    local_only(monkeypatch)

    async def run():
        async with source_fixture() as source:
            await source.listings.insert_one({"_id": "one", "lat": 41.0, "lng": -87.0})
            preliminary = await worker.market_coordinate_counts(source)
            assert preliminary["lat_types"] == {"double": 1} and preliminary["decimal_coordinate_records"] == 0
            await source.listings.update_one({"_id": "one"}, {"$set": {"lat": Decimal128("41.25")}})
            config = local_config(source, options, "market_live")
            env = environment()
            with pytest.raises(worker.OperatorBlocked, match="decimal_coordinate"):
                await worker.copy_application(config, request("copy_market", source="market_live", fence=fence(env, "market_live")),
                                              None, application="market")
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT to_regclass('imladri_app.documents')")
                assert cursor.fetchone()[0] is None
                connection.rollback()
            finally:
                cursor.close()
    asyncio.run(run())


@integration
def test_market_verification_uses_both_scoped_runtime_roles_and_completed_copy_receipt(owner_database, monkeypatch):
    from backend.postgres_access import identifier
    connection, options = owner_database
    local_only(monkeypatch)
    suffix = uuid4().hex[:12]
    roles = {logical: {**options, "user": "imladri_app_" + logical + "_" + suffix,
                       "password": secrets.token_urlsafe(32)} for logical in worker.ROLE_NAMESPACES}

    async def run():
        async with source_fixture() as source:
            await source.listings.insert_one({"_id": "one", "lat": 41.0, "lng": -87.0})
            config = local_config(source, options, "market_live")
            config["role_options"] = roles
            for logical, role in roles.items():
                password = quote(role["password"], safe="")
                config["role_dsns"][logical] = (f'postgresql://{role["user"]}:{password}@127.0.0.1:{options["port"]}/'
                                                f'{options["database"]}?sslmode=disable')
            worker.prepare_access(config, request("prepare_access", approved_workflow=True, allow_public_acl_hardening=True))
            env = environment()
            body = request("copy_market", source="market_live", fence=fence(env, "market_live"))
            copied = await worker.copy_application(config, body, None, application="market")

            class Journal:
                def lookup(self, identity):
                    return {"state": "completed", "command": "copy_market", "source": "market_live", "result": copied}

            verified = await worker.verify_copy(config, request("verify", source="market_live", fence=fence(env, "market_live"),
                                                prior_copy_id=str(uuid4())), Journal())
            assert set(verified["runtime_roles"]) == {"market_api", "market_worker"}
            assert verified["exact_copy_parity"] is True and verified["application_data_mutated"] is False
            assert worker.safe_result(verified)
    try:
        asyncio.run(run())
    finally:
        cursor = connection.cursor()
        for role in roles.values():
            cursor.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=%s", (role["user"],))
            if cursor.fetchone():
                cursor.execute("DROP OWNED BY " + identifier(role["user"]))
                cursor.execute("DROP ROLE " + identifier(role["user"]))
        connection.commit(); cursor.close()
