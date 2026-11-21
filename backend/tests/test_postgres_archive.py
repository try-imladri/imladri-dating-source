"""Real loopback legacy archive: all BSON/catalog data, never active workflows."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import os
import secrets
import ssl
from urllib.parse import urlsplit
from uuid import uuid4

from bson import BSON, Binary, Code, Decimal128, Int64, ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.write_concern import WriteConcern
import pytest

from backend import postgres_archive as archive
from backend.postgres_access import bootstrap_access, identifier
from backend.postgres_migration import connection_options
from backend.postgres_snapshot import create_majority_barrier, pinned_source


DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
RS = os.getenv("IMLADRI_SNAPSHOT_TEST_MONGO_URL")
SECOND_RS = os.getenv("IMLADRI_ARCHIVE_SECOND_MONGO_URL")
integration = pytest.mark.skipif(not DSN or not RS, reason="Needs explicit loopback PostgreSQL and MongoDB replica set.")


def test_archive_rejects_unpinned_sources_without_touching_target():
    class NoConnection:
        def cursor(self): raise AssertionError("Unpinned source touched archive target")
    with pytest.raises(archive.ArchiveBlocked, match="pinned archive"):
        asyncio.run(archive.archive_source(object(), NoConnection()))


def test_collection_names_are_literal_data_and_bounds_fail_closed():
    assert archive._name("旧記録 notes.collection") == "旧記録 notes.collection"
    assert archive._name("system.buckets.legacy") == "system.buckets.legacy"
    for value in ("", "a\x00b", "x" * 1025, None):
        with pytest.raises(archive.ArchiveBlocked): archive._name(value)


def test_archive_connection_keeps_verified_tls_and_never_allows_remote_plaintext(monkeypatch):
    import pg8000.dbapi
    calls = []
    monkeypatch.setattr(pg8000.dbapi, "connect", lambda **kw: calls.append(kw))
    archive.connect_owner("postgresql://fixture:fixture@fixture.virginia-postgres.render.com/fixture?sslmode=require")
    assert calls[0]["ssl_context"].check_hostname is True
    assert calls[0]["ssl_context"].verify_mode == ssl.CERT_REQUIRED
    with pytest.raises(ValueError):
        archive.connect_owner("postgresql://fixture:fixture@remote.example/fixture?sslmode=disable", allow_local_plaintext=True)


@pytest.fixture(scope="module")
def target():
    if not DSN or not RS: pytest.skip("Needs loopback PostgreSQL and replica set")
    assert urlsplit(DSN).hostname in ("127.0.0.1", "localhost", "::1")
    import pg8000.dbapi
    options = connection_options(DSN, allow_local_plaintext=True)
    master = pg8000.dbapi.connect(**options); master.autocommit = True
    database = "fixture_archive_" + uuid4().hex
    role = "imladri_app_archive_" + uuid4().hex[:16]
    password = secrets.token_urlsafe(32)
    cursor = master.cursor(); cursor.execute("CREATE DATABASE " + identifier(database)); cursor.close()
    owner = pg8000.dbapi.connect(**{**options, "database": database})
    try:
        bootstrap_access(owner, {role: "fixture_preview"}, {role: password}, allow_public_acl_hardening=True)
        yield {"owner": owner, "options": {**options, "database": database}, "role": role, "password": password}
    finally:
        owner.close()
        cursor = master.cursor()
        cursor.execute("DROP DATABASE " + identifier(database) + " WITH (FORCE)")
        cursor.execute("DROP ROLE IF EXISTS " + identifier(role)); cursor.close(); master.close()


@asynccontextmanager
async def source_fixture(*, virtual=False, timeseries=False):
    assert urlsplit(RS).hostname in ("127.0.0.1", "localhost", "::1")
    client = AsyncIOMotorClient(RS, serverSelectionTimeoutMS=3000, connectTimeoutMS=20000, socketTimeoutMS=20000)
    db = client.get_database("legacy_archive_fixture_" + uuid4().hex, write_concern=WriteConcern("majority"))
    try:
        collection = "旧記録 notes"
        await db[collection].insert_many([
            {"_id": ObjectId(), "integer": Int64(8), "binary": Binary(b"\x00\xff", 128), "nul": "a\x00b",
             "decimal": Decimal128("12.30"), "code": Code("return value", {"value": 2}), "private": "legacy-fixture"},
            {"_id": {"nested": Int64(4), "order": "preserved"}, "answer": "second"},
        ])
        await db[collection].create_index([("legacy_hash", "hashed")], name="not-an-operational-pg-index", sparse=True)
        await db.create_collection("empty_old_collection", validator={"$jsonSchema": {"bsonType": "object"}}, validationLevel="moderate")
        if virtual:
            await db.create_collection("saved_view_without_id", viewOn=collection, pipeline=[{"$project": {"_id": 0, "private": 1}}])
        if timeseries:
            await db.create_collection("measurements", timeseries={"timeField": "at", "metaField": "site"})
            await db.measurements.insert_one({"at": datetime.now(timezone.utc), "site": "fixture", "reading": 12})
        barrier = await create_majority_barrier(db, fence_reference="local-legacy-archive-fixture",
            application_writers_stopped=True, application_background_writers_stopped=True, ddl_stopped=True,
            allow_local_plaintext=True)
        names = await db.list_collection_names()
        async with pinned_source(db, barrier=barrier, collection_allowlist=names, batch_size=2, allow_local_plaintext=True) as view:
            yield db, view, collection
    finally:
        await client.drop_database(db.name); client.close()


def target_count(target, source):
    cursor = target["owner"].cursor()
    try:
        cursor.execute("SELECT count(*) FROM imladri_archive.receipts WHERE namespace=%s AND source_database=%s", (archive.ARCHIVE_NAMESPACE, source))
        return cursor.fetchone()[0]
    finally:
        cursor.close(); target["owner"].rollback()


@integration
def test_unknown_collections_empty_metadata_raw_types_and_immutable_replay(target):
    async def run():
        async with source_fixture(virtual=True) as (db, view, collection):
            meta = await view.collection_metadata(collection)
            expected = [row.raw for row in await view[collection].find({}).to_list(None)]
            result = await archive.archive_source(view, target["owner"])
            receipt = result["receipt"]
            assert receipt["source_two_pass_pinned_verified"] and receipt["target_two_pass_raw_verified"]
            assert receipt["active_workflows_created"] is False and receipt["research_consent_inferred"] is False
            assert receipt["analysis_text_and_optional_jsonb_verified"] is True
            assert receipt["jsonb_unavailable_records"] == 1
            assert result["logical_target_payload_bytes"] > result["source_bson_bytes_read"] // 2
            assert receipt["virtual_definitions"] == 1 and receipt["virtual_collections_materialized"] is False
            assert next(row for row in receipt["collections"] if row["name"] == "empty_old_collection")["records"] == 0
            cursor = target["owner"].cursor()
            cursor.execute("SELECT raw_bson FROM imladri_archive.documents WHERE source_database=%s AND collection=%s", (db.name, collection))
            assert sorted(bytes(row[0]) for row in cursor.fetchall()) == sorted(expected)
            cursor.execute("SELECT canonical_json,query_document::text FROM imladri_archive.documents WHERE source_database=%s AND collection=%s", (db.name, collection))
            projections = cursor.fetchall()
            assert all(isinstance(row[0], str) for row in projections)
            assert sum(row[1] is None for row in projections) == 1  # NUL keeps raw + text, not JSONB.
            cursor.execute("SELECT spec_bson,indexes_bson FROM imladri_archive.collections WHERE source_database=%s AND name=%s", (db.name, collection))
            spec, indexes = cursor.fetchone()
            assert bytes(spec) == meta["spec_bson"]
            from bson.codec_options import CodecOptions
            from bson.raw_bson import RawBSONDocument
            decoded = BSON(bytes(indexes)).decode(codec_options=CodecOptions(document_class=RawBSONDocument))
            assert [row.raw for row in decoded["indexes"]] == meta["indexes_bson"]
            cursor.execute("SELECT receipt_bson,receipt_sha256 FROM imladri_archive.receipts WHERE source_database=%s", (db.name,))
            original = tuple(bytes(value) if not isinstance(value, str) else value for value in cursor.fetchone())
            cursor.close(); target["owner"].rollback()
            again = await archive.archive_source(view, target["owner"])
            assert again["replay_verified"] and again["receipt"] == receipt
            cursor = target["owner"].cursor()
            cursor.execute("SELECT receipt_bson,receipt_sha256 FROM imladri_archive.receipts WHERE source_database=%s", (db.name,))
            assert tuple(bytes(value) if not isinstance(value, str) else value for value in cursor.fetchone()) == original
            import pg8000.dbapi
            with pytest.raises(pg8000.dbapi.DatabaseError) as error:
                cursor.execute("UPDATE imladri_archive.receipts SET receipt_sha256='changed' WHERE source_database=%s", (db.name,))
            assert error.value.args[0]["C"] == "55000"
            target["owner"].rollback(); cursor.close()
    asyncio.run(run())


@integration
def test_timeseries_preserves_virtual_definition_and_physical_bucket_bytes(target):
    async def run():
        async with source_fixture(timeseries=True) as (db, view, _):
            result = await archive.archive_source(view, target["owner"])
            rows = {row["name"]: row for row in result["receipt"]["collections"]}
            assert rows["measurements"]["kind"] == "timeseries" and rows["measurements"]["records"] == 0
            assert rows["system.buckets.measurements"]["kind"] == "collection"
            assert rows["system.buckets.measurements"]["records"] >= 1
    asyncio.run(run())


@integration
@pytest.mark.parametrize("failure", ["budget", "metadata", "cancel"])
def test_budget_metadata_drift_and_cancellation_rollback_every_source_record(target, monkeypatch, failure):
    async def run():
        async with source_fixture() as (db, view, collection):
            if failure == "metadata":
                original = view.collection_metadata
                calls = {}
                async def changed(name, **kwargs):
                    calls[name] = calls.get(name, 0) + 1
                    if name == collection and calls[name] == 2:
                        await db[collection].create_index("drift", name="metadata-drift")
                    return await original(name, **kwargs)
                monkeypatch.setattr(view, "collection_metadata", changed)
            if failure == "cancel":
                async def cancel(name): raise asyncio.CancelledError()
                monkeypatch.setattr(view, "_find", cancel)
            expected = asyncio.CancelledError if failure == "cancel" else archive.ArchiveBlocked
            with pytest.raises(expected):
                await archive.archive_source(view, target["owner"], max_source_bytes=1 if failure == "budget" else 256 * 1024 * 1024)
            assert target_count(target, db.name) == 0
            cursor = target["owner"].cursor()
            cursor.execute("SELECT count(*) FROM imladri_archive.collections WHERE source_database=%s", (db.name,))
            assert cursor.fetchone()[0] == 0
            cursor.close(); target["owner"].rollback()
    asyncio.run(run())


@integration
def test_changed_target_is_detected_and_never_overwritten(target):
    async def run():
        async with source_fixture() as (db, view, collection):
            await archive.archive_source(view, target["owner"])
            cursor = target["owner"].cursor()
            cursor.execute("SELECT id_key,raw_bson FROM imladri_archive.documents WHERE source_database=%s AND collection=%s LIMIT 1", (db.name, collection))
            key, payload = cursor.fetchone()
            document = BSON(bytes(payload)).decode(); document["tampered"] = True
            changed = BSON.encode(document)
            cursor.execute("UPDATE imladri_archive.documents SET raw_bson=%s WHERE source_database=%s AND collection=%s AND id_key=%s", (changed, db.name, collection, key))
            target["owner"].commit(); cursor.close()
            with pytest.raises(archive.ArchiveBlocked): await archive.archive_source(view, target["owner"])
            cursor = target["owner"].cursor()
            cursor.execute("SELECT raw_bson FROM imladri_archive.documents WHERE source_database=%s AND collection=%s AND id_key=%s", (db.name, collection, key))
            assert bytes(cursor.fetchone()[0]) == changed
            cursor.close(); target["owner"].rollback()
    asyncio.run(run())


@integration
def test_orphan_target_rows_cannot_escape_complete_archive_verification(target):
    async def run():
        async with source_fixture() as (db, view, _):
            await archive.archive_source(view, target["owner"])
            cursor = target["owner"].cursor()
            payload = BSON.encode({"_id": "orphan", "value": "synthetic"})
            identity = BSON.encode({"_id": "orphan"})
            # Deliberately simulate owner-level corruption, not a possible app
            # write. Restore the fixture session setting before committing.
            cursor.execute("SET LOCAL session_replication_role=replica")
            cursor.execute("INSERT INTO imladri_archive.documents VALUES(%s,%s,%s,'uncataloged',%s,%s,%s,'{}','{}',%s)",
                (archive.ARCHIVE_NAMESPACE, db.name, db.name, hashlib.sha256(identity).hexdigest(), identity, payload, hashlib.sha256(payload).hexdigest()))
            cursor.execute("SET LOCAL session_replication_role=origin")
            target["owner"].commit(); cursor.close()
            with pytest.raises(archive.ArchiveBlocked, match="Uncataloged archive records"):
                await archive.archive_source(view, target["owner"])
    asyncio.run(run())


@integration
@pytest.mark.parametrize("projection", ["canonical_json", "query_document"])
def test_analysis_projection_tampering_is_detected_without_repairing_or_overwriting(target, projection):
    async def run():
        async with source_fixture() as (db, view, collection):
            await archive.archive_source(view, target["owner"])
            cursor = target["owner"].cursor()
            cursor.execute("SELECT id_key FROM imladri_archive.documents WHERE source_database=%s AND collection=%s LIMIT 1", (db.name, collection))
            key = cursor.fetchone()[0]
            # Column name is a fixed test parameter, never an untrusted RPC input.
            cursor.execute("UPDATE imladri_archive.documents SET " + projection + "='{}' WHERE source_database=%s AND collection=%s AND id_key=%s", (db.name, collection, key))
            target["owner"].commit(); cursor.close()
            with pytest.raises(archive.ArchiveBlocked, match="analysis projections"):
                await archive.archive_source(view, target["owner"])
    asyncio.run(run())


@integration
def test_projection_payload_budget_rolls_back_instead_of_hiding_extra_storage(target):
    async def run():
        async with source_fixture() as (db, view, _):
            with pytest.raises(archive.ArchiveBlocked, match="projection payload budget"):
                await archive.archive_source(view, target["owner"], max_target_payload_bytes=1)
            assert target_count(target, db.name) == 0
    asyncio.run(run())


@integration
def test_application_login_never_gets_archive_access(target):
    async def run():
        async with source_fixture() as (_, view, _): await archive.archive_source(view, target["owner"])
    asyncio.run(run())
    import pg8000.dbapi
    options = {**target["options"], "user": target["role"], "password": target["password"]}
    with_login = pg8000.dbapi.connect(**options)
    with_login.autocommit = True
    try:
        cursor = with_login.cursor()
        for table in archive.TABLES:
            with pytest.raises(pg8000.dbapi.DatabaseError) as error:
                cursor.execute("SELECT * FROM imladri_archive." + table)
            assert error.value.args[0]["C"] == "42501"
        with pytest.raises(archive.ArchiveBlocked, match="owner connection"):
            archive._initialize(cursor)
        cursor.close()
    finally:
        with_login.close()


@asynccontextmanager
async def two_cluster_same_database(*, identical=False):
    """Two actual loopback replica sets, never two aliases of one physical DB."""
    if not SECOND_RS:
        pytest.skip("Needs a second explicit disposable loopback replica set.")
    assert all(urlsplit(uri).hostname in ("127.0.0.1", "localhost", "::1") for uri in (RS, SECOND_RS))
    assert urlsplit(RS).netloc != urlsplit(SECOND_RS).netloc
    clients = [AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000, connectTimeoutMS=20000,
                                 socketTimeoutMS=20000) for uri in (RS, SECOND_RS)]
    name = "same_legacy_name_" + uuid4().hex
    databases = [client[name] for client in clients]
    try:
        for index, database in enumerate(databases):
            await database.private_answers.insert_one({"_id": "same-record-id",
                "private_answer": "same-fixture-content" if identical else "origin-" + str(index)})
            await database.create_collection("empty_old_collection")
        barriers = [await create_majority_barrier(database, fence_reference="two-source-archive-fixture",
            application_writers_stopped=True, application_background_writers_stopped=True, ddl_stopped=True,
            allow_local_plaintext=True, control_database=client["imladri_migration_control"])
            for client, database in zip(clients, databases)]
        async with pinned_source(databases[0], barrier=barriers[0], allow_local_plaintext=True,
            collection_allowlist=await databases[0].list_collection_names()) as first:
            async with pinned_source(databases[1], barrier=barriers[1], allow_local_plaintext=True,
                collection_allowlist=await databases[1].list_collection_names()) as second:
                yield databases, first, second
    finally:
        for client, database in zip(clients, databases):
            await client.drop_database(database.name)
            client.close()


@integration
def test_actual_distinct_clusters_same_db_and_ids_are_isolated_by_alias(target):
    async def run():
        async with two_cluster_same_database() as (databases, first, second):
            legacy = await archive.archive_source(first, target["owner"], source_alias="legacy_staging")
            recovery = await archive.archive_source(second, target["owner"], source_alias="recovery_staging")
            assert legacy["receipt"]["source_database"] == recovery["receipt"]["source_database"] == databases[0].name
            assert legacy["receipt"]["source_alias"] == "legacy_staging"
            assert recovery["receipt"]["source_alias"] == "recovery_staging"
            assert legacy["receipt"]["source_binding_sha256"] != recovery["receipt"]["source_binding_sha256"]
            cursor = target["owner"].cursor()
            cursor.execute("SELECT source_alias,source_database,raw_bson FROM imladri_archive.documents WHERE source_database=%s ORDER BY source_alias", (databases[0].name,))
            rows = cursor.fetchall()
            assert [(row[0], BSON(bytes(row[2])).decode()["private_answer"]) for row in rows] == [
                ("legacy_staging", "origin-0"), ("recovery_staging", "origin-1")]
            assert all(row[1] == databases[0].name for row in rows)
            cursor.close(); target["owner"].rollback()
            assert (await archive.archive_source(first, target["owner"], source_alias="legacy_staging"))["receipt"] == legacy["receipt"]
            assert (await archive.archive_source(second, target["owner"], source_alias="recovery_staging"))["receipt"] == recovery["receipt"]
            import pg8000.dbapi
            app = pg8000.dbapi.connect(**{**target["options"], "user": target["role"], "password": target["password"]})
            try:
                app.autocommit = True
                cursor = app.cursor()
                for alias in ("legacy_staging", "recovery_staging"):
                    with pytest.raises(pg8000.dbapi.DatabaseError) as error:
                        cursor.execute("SELECT raw_bson FROM imladri_archive.documents WHERE source_alias=%s", (alias,))
                    assert error.value.args[0]["C"] == "42501"
                cursor.close()
            finally:
                app.close()
    asyncio.run(run())


@integration
def test_cross_cluster_same_alias_replay_is_rejected_even_for_identical_data(target):
    async def run():
        async with two_cluster_same_database(identical=True) as (databases, first, second):
            alias = "origin_guard_" + uuid4().hex[:12]
            original = await archive.archive_source(first, target["owner"], source_alias=alias)
            with pytest.raises(archive.ArchiveBlocked, match="fixed target scope"):
                await archive.archive_source(second, target["owner"], source_alias=alias)
            again = await archive.archive_source(first, target["owner"], source_alias=alias)
            assert again["replay_verified"] and again["receipt"] == original["receipt"]
            cursor = target["owner"].cursor()
            cursor.execute("SELECT count(*) FROM imladri_archive.receipts WHERE source_alias=%s", (alias,))
            assert cursor.fetchone()[0] == 1
            cursor.close(); target["owner"].rollback()
    asyncio.run(run())


@integration
@pytest.mark.parametrize("schema_drift", ["column", "primary_key"])
def test_missing_source_key_schema_fails_closed_without_automatic_rewrite(target, schema_drift):
    async def run():
        async with source_fixture() as (_, view, _):
            cursor = target["owner"].cursor()
            archive._initialize(cursor)
            if schema_drift == "column":
                cursor.execute("ALTER TABLE imladri_archive.receipts RENAME COLUMN source_alias TO old_source_key")
            else:
                cursor.execute("ALTER TABLE imladri_archive.receipts DROP CONSTRAINT receipts_pkey")
            target["owner"].commit(); cursor.close()
            try:
                with pytest.raises(archive.ArchiveBlocked, match="explicit source-key schema review"):
                    await archive.archive_source(view, target["owner"])
                cursor = target["owner"].cursor()
                if schema_drift == "column":
                    cursor.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='imladri_archive' AND table_name='receipts' AND column_name='old_source_key'")
                    assert cursor.fetchone()[0] == 1
                else:
                    cursor.execute("SELECT count(*) FROM pg_constraint WHERE conrelid='imladri_archive.receipts'::regclass AND contype='p'")
                    assert cursor.fetchone()[0] == 0
                cursor.close(); target["owner"].rollback()
            finally:
                cursor = target["owner"].cursor()
                if schema_drift == "column":
                    cursor.execute("ALTER TABLE imladri_archive.receipts RENAME COLUMN old_source_key TO source_alias")
                else:
                    cursor.execute("ALTER TABLE imladri_archive.receipts ADD PRIMARY KEY(namespace,source_alias)")
                target["owner"].commit(); cursor.close()
    asyncio.run(run())


@pytest.fixture
def independent_archive_target(target):
    """Catalog corruption must not affect other tests' deliberate orphan rows."""
    import pg8000.dbapi
    options = target["options"]
    master = pg8000.dbapi.connect(**options)
    master.autocommit = True
    database = "fixture_archive_fk_" + uuid4().hex
    cursor = master.cursor(); cursor.execute("CREATE DATABASE " + identifier(database)); cursor.close()
    owner = pg8000.dbapi.connect(**{**options, "database": database})
    try:
        yield {"owner": owner}
    finally:
        owner.close()
        cursor = master.cursor()
        cursor.execute("DROP DATABASE " + identifier(database) + " WITH (FORCE)")
        cursor.close(); master.close()


@integration
def test_missing_foreign_key_and_orphan_cannot_escape_independent_parity_or_be_overwritten(independent_archive_target):
    target = independent_archive_target
    async def run():
        async with source_fixture() as (db, view, _):
            await archive.archive_source(view, target["owner"])
            cursor = target["owner"].cursor()
            cursor.execute("SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='imladri_archive.documents'::regclass AND contype='f'")
            constraint, definition = cursor.fetchone()
            cursor.execute("SELECT receipt_bson,receipt_sha256 FROM imladri_archive.receipts WHERE source_alias=%s", (db.name,))
            original_receipt = cursor.fetchone()
            original_receipt = (bytes(original_receipt[0]), original_receipt[1])
            cursor.execute("ALTER TABLE imladri_archive.documents DROP CONSTRAINT " + identifier(constraint))
            payload, identity = BSON.encode({"_id": "missing-fk-orphan", "private": "synthetic-only"}), BSON.encode({"_id": "missing-fk-orphan"})
            key = hashlib.sha256(identity).hexdigest()
            cursor.execute("INSERT INTO imladri_archive.documents VALUES(%s,%s,%s,'uncataloged',%s,%s,%s,'{}','{}',%s)",
                (archive.ARCHIVE_NAMESPACE, db.name, db.name, key, identity, payload, hashlib.sha256(payload).hexdigest()))
            target["owner"].commit(); cursor.close()
            try:
                # Count parity is independent of the schema/FK gate.
                cursor = target["owner"].cursor()
                budget = archive._Budget(200000, 256 * 1024 * 1024, 500, 16 * 1024 * 1024, 512 * 1024 * 1024)
                with pytest.raises(archive.ArchiveBlocked, match="Uncataloged archive records"):
                    archive._target_manifest(cursor, db.name, db.name, budget)
                cursor.close(); target["owner"].rollback()
                with pytest.raises(archive.ArchiveBlocked, match="foreign key is missing or changed"):
                    await archive.archive_source(view, target["owner"])
                cursor = target["owner"].cursor()
                cursor.execute("SELECT raw_bson FROM imladri_archive.documents WHERE source_alias=%s AND collection='uncataloged' AND id_key=%s", (db.name, key))
                assert bytes(cursor.fetchone()[0]) == payload
                cursor.execute("SELECT receipt_bson,receipt_sha256 FROM imladri_archive.receipts WHERE source_alias=%s", (db.name,))
                saved = cursor.fetchone()
                assert (bytes(saved[0]), saved[1]) == original_receipt
                cursor.execute("SELECT count(*) FROM pg_constraint WHERE conrelid='imladri_archive.documents'::regclass AND contype='f'")
                assert cursor.fetchone()[0] == 0  # No implicit repair or deletion.
                cursor.close(); target["owner"].rollback()
            finally:
                # Restore only this test's deliberately corrupted private fixture.
                cursor = target["owner"].cursor()
                cursor.execute("DELETE FROM imladri_archive.documents WHERE source_alias=%s AND collection='uncataloged' AND id_key=%s", (db.name, key))
                cursor.execute("ALTER TABLE imladri_archive.documents ADD CONSTRAINT " + identifier(constraint) + " " + definition)
                target["owner"].commit(); cursor.close()
    asyncio.run(run())
