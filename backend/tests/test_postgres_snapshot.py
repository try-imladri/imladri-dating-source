"""Real loopback replica-set snapshot semantics; no managed database access."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from bson import BSON
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import IndexModel, monitoring, timeout as driver_timeout
from pymongo.write_concern import WriteConcern
import pytest

from backend.postgres_cutover import FrozenCopyBlocked, copy_frozen
from backend.postgres_snapshot import SnapshotUnavailable, _source_transport, create_majority_barrier, pinned_source
from backend.postgres_store import PostgresDatabase
from backend.tests.test_postgres_operator_control import owner_database


RS = os.getenv("IMLADRI_SNAPSHOT_TEST_MONGO_URL")
STANDALONE = os.getenv("IMLADRI_TEST_MONGO_URL")
DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
integration = pytest.mark.skipif(not RS, reason="Requires a disposable loopback MongoDB replica set.")


def test_source_transport_checks_actual_tls_context_and_plaintext_is_explicit_loopback_only():
    cases = [("mongodb://example.invalid:27017", {"tls": True}, False, True),
             ("mongodb://example.invalid:27017", {"tls": True, "tlsAllowInvalidCertificates": True}, False, False),
             ("mongodb://example.invalid:27017", {"tls": True, "tlsAllowInvalidHostnames": True}, False, False),
             ("mongodb://example.invalid:27017", {}, True, False),
             ("mongodb://127.0.0.1:62973", {}, False, False),
             ("mongodb://127.0.0.1:62973", {}, True, True)]
    for uri, options, allow_local, allowed in cases:
        client = AsyncIOMotorClient(uri, connect=False, **options)
        try:
            if allowed:
                result = _source_transport(client.fixture, allow_local_plaintext=allow_local)
                assert result["verified_tls"] is bool(options.get("tls"))
                assert len(result["configured_endpoints_sha256"]) == 64
            else:
                with pytest.raises(SnapshotUnavailable):
                    _source_transport(client.fixture, allow_local_plaintext=allow_local)
        finally:
            client.close()


class Commands(monitoring.CommandListener):
    def __init__(self):
        self.finds = []
        self.get_more = 0
        self.barriers = []
    def started(self, event):
        if event.command_name == "find":
            self.finds.append({"read_concern": deepcopy(event.command.get("readConcern")), "session": "lsid" in event.command})
        elif event.command_name == "getMore":
            self.get_more += 1
        elif event.command_name == "update" and event.command.get("update") == "database_migration_reports":
            self.barriers.append({"write_concern": deepcopy(event.command.get("writeConcern")), "session": "lsid" in event.command})
    def succeeded(self, event):
        pass
    def failed(self, event):
        pass


@asynccontextmanager
async def source_fixture(uri=RS, *, listener=None, client_options=None):
    assert urlsplit(uri).hostname in {"127.0.0.1", "localhost", "::1"}
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000, connectTimeoutMS=20000, socketTimeoutMS=20000,
                               event_listeners=[listener] if listener else [], **(client_options or {}))
    database = client.get_database("imladri_pinned_snapshot_" + uuid4().hex, write_concern=WriteConcern("majority"))
    try:
        yield database
    finally:
        await client.drop_database(database.name)
        client.close()


def freeze(view, target):
    return {"source": {"backend": "mongodb", "namespace": view.name, **view.view_identity},
        "target": {"backend": "postgresql", "namespace": target.name},
        "source_writers_stopped": True, "target_writers_stopped": True,
        "source_data_mode": "pinned_snapshot", "source_background_writers_stopped": False,
        "source_application_background_writers_stopped": True, "source_ddl_stopped": True,
        "target_background_writers_stopped": True, "evidence_reference": "replicaset-fixture-explicit-app-barrier",
        "valid_until": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}


async def wait_until_deleted(collection):
    for _ in range(40):
        if await collection.count_documents({}) == 0:
            return
        await asyncio.sleep(0.2)
    pytest.fail("The disposable fixture TTL monitor did not delete the expired row in its bounded wait.")


@integration
def test_snapshot_session_pins_all_collection_finds_and_getmore_despite_underlying_changes():
    async def run():
        commands = Commands()
        async with source_fixture(listener=commands) as db:
            await db.users.insert_many([{"_id": str(i), "value": i} for i in range(5)])
            await db.couples_pairs.insert_one({"_id": "pair", "value": "before"})
            async with pinned_source(db, allow_local_plaintext=True, batch_size=2) as view:
                timestamp = view.evidence["at_cluster_time"]
                await db.users.update_one({"_id": "0"}, {"$set": {"value": 99}})
                await db.couples_pairs.delete_one({"_id": "pair"})
                users = [BSON(row.raw).decode() for row in await view["users"].find({}).to_list(None)]
                pair = [BSON(row.raw).decode() for row in await view["couples_pairs"].find({}).to_list(None)]
                assert next(row for row in users if row["_id"] == "0")["value"] == 0
                assert pair == [{"_id": "pair", "value": "before"}]
                assert view.evidence["at_cluster_time"] == timestamp
                assert view.evidence["native_ttl_monitor_stopped"] is False
                assert commands.get_more >= 2
                # First find asks the server to select a timestamp; each later
                # data find explicitly includes that exact selected timestamp.
                assert commands.finds[0]["read_concern"] == {"level": "snapshot"}
                for command in commands.finds[1:]:
                    assert command["session"] is True
                    assert command["read_concern"]["level"] == "snapshot"
                    stamp = command["read_concern"]["atClusterTime"]
                    assert {"time": stamp.time, "increment": stamp.inc} == timestamp
    asyncio.run(run())


@integration
def test_native_ttl_can_delete_live_data_while_the_pinned_view_remains_immutable():
    async def run():
        async with source_fixture() as db:
            await db.client.admin.command({"setParameter": 1, "ttlMonitorSleepSecs": 1})
            await db.prompt_candidates.create_indexes([IndexModel("expires_at", expireAfterSeconds=0)])
            await db.prompt_candidates.insert_one({"_id": "temporary", "expires_at": datetime.now(timezone.utc) + timedelta(seconds=2)})
            async with pinned_source(db, allow_local_plaintext=True) as view:
                assert len(await view["prompt_candidates"].find({}).to_list(None)) == 1
                await wait_until_deleted(db.prompt_candidates)
                assert len(await view["prompt_candidates"].find({}).to_list(None)) == 1
                assert view.evidence["native_ttl_monitor_stopped"] is False
    asyncio.run(run())


@integration
def test_explicit_server_writer_cutoff_is_used_instead_of_a_newer_snapshot():
    async def run():
        commands = Commands()
        async with source_fixture(listener=commands) as db:
            await db.users.insert_one({"_id": "owner", "value": "at-cutoff"})
            barrier = (await db.client.admin.command("hello"))["lastWrite"]["opTime"]["ts"]
            await db.users.update_one({"_id": "owner"}, {"$set": {"value": "after-cutoff"}})
            async with pinned_source(db, allow_local_plaintext=True, barrier_time=barrier) as view:
                rows = await view["users"].find({}).to_list(None)
                assert BSON(rows[0].raw).decode()["value"] == "at-cutoff"
                assert view.evidence["server_selected_timestamp"] is False
                assert view.evidence["writer_barrier_cluster_time"] == {"time": barrier.time, "increment": barrier.inc}
                assert all(command["read_concern"]["atClusterTime"] == barrier for command in commands.finds)
    asyncio.run(run())


@integration
def test_real_majority_metadata_barrier_covers_previous_w1_writes_and_retains_session_time():
    async def run():
        commands = Commands()
        async with source_fixture(listener=commands) as db:
            await db.users.with_options(write_concern=WriteConcern(w=1)).insert_one({"_id": "owner", "answer": 1})
            await db.couples_pairs.with_options(write_concern=WriteConcern(w=1)).insert_one({"_id": "pair", "answer": 2})
            with pytest.raises(SnapshotUnavailable, match="writer fence"):
                await create_majority_barrier(db, allow_local_plaintext=True, fence_reference="fixture-fence", application_writers_stopped=True,
                    application_background_writers_stopped=True, ddl_stopped=False)
            assert await db.database_migration_reports.count_documents({}) == 0
            barrier = await create_majority_barrier(db, allow_local_plaintext=True, fence_reference="fixture-fence", application_writers_stopped=True,
                application_background_writers_stopped=True, ddl_stopped=True)
            assert barrier["session_operation_time_verified"] is True
            assert commands.barriers == [{"write_concern": {"w": "majority", "wtimeout": 20000}, "session": True}]
            control = await db.database_migration_reports.find_one({"_id": barrier["control_record_id"]})
            assert set(control) == {"_id", "kind", "version", "fence_reference", "committed_at"}
            async with pinned_source(db, allow_local_plaintext=True, barrier=barrier) as view:
                assert len(await view["users"].find({}).to_list(None)) == 1
                assert len(await view["couples_pairs"].find({}).to_list(None)) == 1
                assert view.evidence["writer_barrier_cluster_time"] == barrier["operation_time"]
                assert view.evidence["majority_write_barrier"]["fence_reference"] == "fixture-fence"
            changed = {**barrier, "acknowledged": False}
            with pytest.raises(SnapshotUnavailable, match="barrier receipt"):
                async with pinned_source(db, allow_local_plaintext=True, barrier=changed):
                    pytest.fail("A changed majority-barrier receipt exposed a view.")
    asyncio.run(run())


@integration
def test_majority_barrier_can_write_only_the_fixed_isolated_control_database():
    async def run():
        async with source_fixture() as db:
            await db.users.insert_one({"_id": "owner", "value": "source-remains-read-only"})
            control = db.client["imladri_migration_control"]
            barrier = None
            try:
                barrier = await create_majority_barrier(db, control_database=control, allow_local_plaintext=True,
                    fence_reference="separate-control-database", application_writers_stopped=True,
                    application_background_writers_stopped=True, ddl_stopped=True)
                assert barrier["control_database"] == control.name
                assert barrier["source_database_write_operations"] == 0
                assert "database_migration_reports" not in await db.list_collection_names()
                assert await control.database_migration_reports.count_documents({"_id": barrier["control_record_id"]}) == 1
                async with pinned_source(db, barrier=barrier, allow_local_plaintext=True) as view:
                    assert len(await view["users"].find({}).to_list(None)) == 1
                with pytest.raises(SnapshotUnavailable, match="fixed control"):
                    await create_majority_barrier(db, control_database=db.client["some_other_database"],
                        allow_local_plaintext=True, fence_reference="separate-control-database",
                        application_writers_stopped=True, application_background_writers_stopped=True, ddl_stopped=True)
            finally:
                if barrier:
                    await control.database_migration_reports.delete_one({"_id": barrier["control_record_id"]})
    asyncio.run(run())


@integration
def test_explicit_archive_inventory_preserves_raw_unknown_metadata_without_materializing_views():
    async def run():
        commands = Commands()
        async with source_fixture(listener=commands) as db:
            await db.create_collection("legacy notes", validator={"$jsonSchema": {"bsonType": "object"}})
            await db["legacy notes"].insert_one({"_id": "one", "record": "fixture legacy"})
            await db["legacy notes"].create_indexes([IndexModel("record", name="legacy named index", unique=True)])
            await db.command({"create": "legacy view", "viewOn": "legacy notes", "pipeline": [{"$match": {"record": "fixture legacy"}}]})
            barrier = await create_majority_barrier(db, allow_local_plaintext=True, fence_reference="replicaset-fixture-explicit-app-barrier",
                application_writers_stopped=True, application_background_writers_stopped=True, ddl_stopped=True)
            inventory = await db.list_collection_names()
            with pytest.raises(SnapshotUnavailable, match="classified"):
                async with pinned_source(db, allow_local_plaintext=True, barrier=barrier):
                    pytest.fail("The default Personal source accepted unknown legacy names.")
            async with pinned_source(db, allow_local_plaintext=True, barrier=barrier, collection_allowlist=inventory) as view:
                with pytest.raises(SnapshotUnavailable, match="inspected physical metadata"):
                    view["legacy notes"].find({})
                metadata = await view.collection_metadata("legacy notes")
                spec = BSON(metadata["spec_bson"]).decode()
                assert spec["name"] == "legacy notes" and spec["options"]["validator"]
                assert "info" in spec and "idIndex" in spec
                assert any(BSON(raw).decode().get("name") == "legacy named index" for raw in metadata["indexes_bson"])
                assert len(await view["legacy notes"].find({}).to_list(None)) == 1
                before = len(commands.finds)
                definition = await view.collection_metadata("legacy view")
                assert definition["data_kind"] == "view" and definition["indexes_bson"] == []
                assert BSON(definition["spec_bson"]).decode()["options"]["viewOn"] == "legacy notes"
                with pytest.raises(SnapshotUnavailable, match="never materialized"):
                    view["legacy view"].find({})
                assert len(commands.finds) == before
                assert view.evidence["inventory_scope"] == "explicit_archive_inventory"
                # The separate archive allowance must never weaken the active
                # Personal copy's own known-collection registry.
                class NeverTouchedTarget:
                    backend = "postgresql"
                    name = "fixture_never_touched"
                target = NeverTouchedTarget()
                with pytest.raises(FrozenCopyBlocked, match="unclassified"):
                    await copy_frozen(view, target, freeze=freeze(view, target))
    asyncio.run(run())


@integration
def test_snapshot_cannot_repin_expire_or_continue_after_context_exit():
    async def run():
        async with source_fixture() as db:
            await db.users.insert_one({"_id": "one"})
            async with pinned_source(db, allow_local_plaintext=True, max_seconds=1) as view:
                with pytest.raises(SnapshotUnavailable, match="repinned"):
                    await view._pin()
                await asyncio.sleep(1.05)
                with pytest.raises(SnapshotUnavailable, match="expired"):
                    await view["users"].find({}).to_list(None)
            with pytest.raises(SnapshotUnavailable, match="unavailable"):
                view.ensure_active()
    asyncio.run(run())


@integration
@pytest.mark.parametrize("mode", ["configured", "inherited", "entered_after_pin"])
def test_snapshot_rejects_configured_or_ambient_csot_before_it_can_alter_getmore(mode):
    async def run():
        async with source_fixture(client_options={"timeoutMS": 1000} if mode == "configured" else None) as db:
            await db.users.insert_one({"_id": "one"})
            if mode == "configured":
                with pytest.raises(SnapshotUnavailable, match="CSOT"):
                    async with pinned_source(db, allow_local_plaintext=True):
                        pytest.fail("A configured CSOT client exposed a view.")
            elif mode == "inherited":
                with driver_timeout(1):
                    with pytest.raises(SnapshotUnavailable, match="CSOT"):
                        async with pinned_source(db, allow_local_plaintext=True):
                            pytest.fail("An inherited CSOT context exposed a view.")
            else:
                async with pinned_source(db, allow_local_plaintext=True) as view:
                    with driver_timeout(1):
                        with pytest.raises(SnapshotUnavailable, match="CSOT"):
                            await view["users"].find({}).to_list(None)
    asyncio.run(run())


@pytest.mark.skipif(not STANDALONE, reason="Requires the separate disposable standalone MongoDB.")
def test_standalone_snapshot_unsupported_fails_before_exposing_a_view():
    async def run():
        async with source_fixture(STANDALONE) as db:
            await db.users.insert_one({"_id": "one"})
            with pytest.raises(SnapshotUnavailable, match="supported pinned snapshot"):
                async with pinned_source(db, allow_local_plaintext=True):
                    pytest.fail("A standalone server exposed an unsupported snapshot.")
    asyncio.run(run())


@integration
@pytest.mark.skipif(not DSN, reason="Requires disposable PostgreSQL for pinned-copy integration.")
def test_pinned_copy_is_truthful_and_reverse_guard_rejects_underlying_ttl_drift(owner_database):
    _, options = owner_database
    parsed = urlsplit(DSN)
    isolated_dsn = urlunsplit((parsed.scheme, parsed.netloc, "/" + options["database"], parsed.query, parsed.fragment))
    async def run():
        assert urlsplit(DSN).hostname in {"127.0.0.1", "localhost", "::1"}
        async with source_fixture() as db:
            pg = PostgresDatabase(isolated_dsn, "fixture_pinned_copy_" + uuid4().hex, allow_local_plaintext=True)
            try:
                await db.client.admin.command({"setParameter": 1, "ttlMonitorSleepSecs": 1})
                await db.users.insert_one({"_id": "owner"})
                await db.prompt_candidates.create_indexes([IndexModel("expires_at", expireAfterSeconds=0)])
                await db.prompt_candidates.insert_one({"_id": "temporary", "expires_at": datetime.now(timezone.utc) + timedelta(seconds=3)})
                async with pinned_source(db, allow_local_plaintext=True) as without_cutoff:
                    with pytest.raises(FrozenCopyBlocked, match="majority metadata barrier"):
                        await copy_frozen(without_cutoff, pg, freeze=freeze(without_cutoff, pg))
                barrier = await create_majority_barrier(db, allow_local_plaintext=True, fence_reference="replicaset-fixture-explicit-app-barrier",
                    application_writers_stopped=True, application_background_writers_stopped=True, ddl_stopped=True)
                async with pinned_source(db, allow_local_plaintext=True, barrier=barrier) as view:
                    evidence = freeze(view, pg)
                    bad = {**evidence, "source_ddl_stopped": False}
                    with pytest.raises(FrozenCopyBlocked, match="DDL"):
                        await copy_frozen(view, pg, freeze=bad)
                    receipt = await copy_frozen(view, pg, freeze=evidence)
                    assert receipt["source_data_mode"] == "pinned_snapshot"
                    assert receipt["native_source_ttl_monitor_stopped"] is False
                    assert receipt["source_snapshot"]["snapshot_session"] is True
                    assert receipt["source_before"] == receipt["target_after"]
                    await wait_until_deleted(db.prompt_candidates)
                # Only this disposable replica set's TTL worker is stopped to
                # create an honest destination barrier for the reverse attempt.
                await db.client.admin.command({"setParameter": 1, "ttlMonitorEnabled": False})
                reverse = {"source": {"backend": "postgresql", "namespace": pg.name},
                    "target": {"backend": "mongodb", "namespace": db.name}, "source_writers_stopped": True,
                    "target_writers_stopped": True, "source_background_writers_stopped": True,
                    "target_background_writers_stopped": True, "evidence_reference": "fixture-local-ttl-paused",
                    "valid_until": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()}
                with pytest.raises(FrozenCopyBlocked, match="no longer matches"):
                    await copy_frozen(pg, db, freeze=reverse, expected_target=receipt)
            finally:
                await db.client.admin.command({"setParameter": 1, "ttlMonitorEnabled": True})
                def cleanup(cursor, now):
                    for table in ("unique_keys", "indexes", "documents", "collections", "rpc_requests"):
                        cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (pg.name,))
                await pg._run(cleanup, write="__fixture_cleanup")
                await pg.aclose()
    asyncio.run(run())
