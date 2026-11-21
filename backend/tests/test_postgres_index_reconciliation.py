"""Actual pinned Mongo + PostgreSQL metadata-only manual reconciliation."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from urllib.parse import urlsplit
from uuid import uuid4

from bson import BSON, SON
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import IndexModel
from pymongo.errors import DuplicateKeyError
import pytest

from backend import postgres_index_reconciliation as repair
from backend.postgres_codec import connection_options
from backend.postgres_cutover import _identity, copy_frozen
from backend.postgres_operator import safe_result
from backend.postgres_snapshot import create_majority_barrier, pinned_source
from backend.postgres_store import PostgresDatabase, decode_index_definition, unique_tokens


DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
RS = os.getenv("IMLADRI_SNAPSHOT_TEST_MONGO_URL")
integration = pytest.mark.skipif(not DSN or not RS, reason="Needs explicit loopback PostgreSQL and MongoDB replica set.")


def test_reconciliation_errors_publish_only_static_reason_and_stage():
    from backend.postgres_copy_diagnostics import safe_error
    error = repair.IndexReconciliationBlocked("reconciliation_existing_target_data_not_exact", "target_preflight")
    assert safe_error(error) == {"error_type": "IndexReconciliationBlocked",
        "reason_code": "reconciliation_existing_target_data_not_exact", "stage": "target_preflight"}
    assert safe_error(RuntimeError("ordinary provider details are withheld")) == {"error_type": "RuntimeError"}
    assert safe_error(repair.IndexReconciliationBlocked("unknown-code", "unknown-stage")) == {
        "error_type": "IndexReconciliationBlocked", "reason_code": "index_metadata_reconciliation_not_completed", "stage": "unspecified"}


def freeze(view, target, reference):
    return {"source": _identity(view), "target": _identity(target),
        "source_data_mode": ("pinned_snapshot" if view.evidence["majority_write_barrier"] is not None
                             else "readonly_majority_snapshot_reconciliation"),
        "source_writers_stopped": True, "target_writers_stopped": True,
        "source_application_background_writers_stopped": True, "source_ddl_stopped": True,
        "target_background_writers_stopped": True, "evidence_reference": reference,
        "valid_until": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}


@asynccontextmanager
async def fixture():
    import pg8000.dbapi
    assert all(urlsplit(v).hostname in ("127.0.0.1", "localhost", "::1") for v in (DSN, RS))
    client = AsyncIOMotorClient(RS, serverSelectionTimeoutMS=3000, connectTimeoutMS=20000, socketTimeoutMS=20000)
    source = client["fixture_index_reconcile_" + uuid4().hex]
    target = PostgresDatabase(DSN, "fixture_index_reconcile_" + uuid4().hex, allow_local_plaintext=True)
    owner = None
    try:
        model = IndexModel([("zulu", 1), ("alpha", -1)], name="nonalpha", unique=True,
                           partialFilterExpression={"literal": SON([("zulu", 5), ("alpha", 6)])})
        await source.users.create_indexes([model])
        await source.users.insert_many([{"_id": "one", "zulu": 1, "alpha": 2, "literal": {"zulu": 5, "alpha": 6}},
                                       {"_id": "outside", "zulu": 1, "alpha": 2, "literal": {"alpha": 6, "zulu": 5}}])
        await source.answer_history.insert_one({"_id": "erased", "id": "erased", "purged": True})
        await source.create_collection("media")
        barrier = await create_majority_barrier(source, fence_reference="original-failed-copy-fence",
            application_writers_stopped=True, application_background_writers_stopped=True, ddl_stopped=True,
            allow_local_plaintext=True)
        async with pinned_source(source, barrier=barrier, allow_local_plaintext=True) as view:
            await copy_frozen(view, target, freeze=freeze(view, target, "original-failed-copy-fence"))
        # Reproduce only the old JSONB index storage and wrong tuple-key order.
        # Existing app BSON/revisions/timestamps are untouched.
        def legacy(cursor, _now):
            cursor.execute("UPDATE imladri_app.indexes SET definition=%s::jsonb WHERE namespace=%s AND collection='users' AND name='nonalpha'",
                           (json.dumps(dict(model.document)), target.name))
            cursor.execute("DELETE FROM imladri_app.unique_keys WHERE namespace=%s AND index_name='nonalpha'", (target.name,))
            cursor.execute("SELECT definition FROM imladri_app.indexes WHERE namespace=%s AND collection='users'", (target.name,))
            stored = cursor.fetchone()[0]
            cursor.execute("SELECT id_key,raw_bson FROM imladri_app.documents WHERE namespace=%s AND collection='users'", (target.name,))
            for key, raw in cursor.fetchall():
                for value in unique_tokens(BSON(bytes(raw)).decode(), stored):
                    cursor.execute("INSERT INTO imladri_app.unique_keys(namespace,collection,index_name,token,id_key) VALUES(%s,%s,%s,%s,%s)",
                                   (target.name, "users", "nonalpha", value, key))
        await target._run(legacy, write="users")
        owner = pg8000.dbapi.connect(**connection_options(DSN, allow_local_plaintext=True))
        async with pinned_source(source, allow_local_plaintext=True) as view:
            failed_id = str(uuid4())
            raw = next(row.raw for row in await view["database_migration_reports"].find({}).to_list(None)
                       if row["kind"] == "postgres_cutover_majority_barrier")
            witness = {"kind": "existing_majority_snapshot_barrier", "source_namespace": source.name,
                "view_identity": view.view_identity, "linked_failed_copy_id": failed_id,
                "original_fence_reference": "original-failed-copy-fence",
                "control_record_sha256": hashlib.sha256(raw).hexdigest(), "original_request_sha256": "a" * 64}
            yield source, view, target, owner, failed_id, witness
    finally:
        if owner is not None:
            owner.close()
        await client.drop_database(source.name)
        client.close()
        def cleanup(cursor, _now):
            for table in ("unique_keys", "indexes", "documents", "collections", "rpc_requests"):
                cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (target.name,))
        await target._run(cleanup, write="__cleanup")
        await target.aclose()


def state(connection, namespace):
    cursor = connection.cursor()
    try:
        result = {}
        for table in ("documents", "indexes", "unique_keys", "collections"):
            cursor.execute("SELECT row_to_json(t) FROM imladri_app." + table + " t WHERE namespace=%s ORDER BY row_to_json(t)::text", (namespace,))
            result[table] = cursor.fetchall()
        return result
    finally:
        connection.rollback(); cursor.close()


@integration
def test_manual_repair_preserves_all_records_metadata_tombstones_and_repairs_exact_indexes():
    async def run():
        async with fixture() as (_source, view, target, owner, failed_id, witness):
            before = state(owner, target.name)
            result = await repair.reconcile_index_metadata(view, owner, namespace=target.name,
                freeze=freeze(view, target, "fresh-reconciliation-fence"), failed_copy_id=failed_id,
                reconciliation_witness=witness)
            after = state(owner, target.name)
            assert before["documents"] == after["documents"] and before["collections"] == after["collections"]
            assert before["indexes"] != after["indexes"]
            assert result["source_before"] == result["target_after"]
            assert result["target_application_document_write_operations"] == 0
            assert result["source_application_write_operations"] == 0
            assert result["source_snapshot"]["majority_write_barrier"] is None
            assert result["new_source_majority_barrier_created"] is False
            assert result["source_data_mode"] == "readonly_majority_snapshot_reconciliation"
            assert result["prior_unknown_intent_modified"] is False
            assert result["budget"]["unique_key_rows_rebuilt"] > 0
            assert safe_result(result)
            actual = (await target.users.index_information())["nonalpha"]
            assert actual["key"] == [("zulu", 1), ("alpha", -1)]
            assert list(actual["partialFilterExpression"]["literal"]) == ["zulu", "alpha"]
            with pytest.raises(DuplicateKeyError):
                await target.users.insert_one({"_id": "blocked", "zulu": 1, "alpha": 2, "literal": {"zulu": 5, "alpha": 6}})
            assert (await target.answer_history.find_one({"_id": "erased"}))["purged"] is True
    asyncio.run(run())


@integration
def test_any_existing_target_record_drift_blocks_before_metadata_or_key_repair():
    async def run():
        async with fixture() as (_source, view, target, owner, failed_id, witness):
            def drift(cursor, _now):
                from backend.postgres_codec import document_key
                row = {"_id": "one", "corrected": True}
                raw = BSON.encode(row)
                cursor.execute("UPDATE imladri_app.documents SET raw_bson=%s,source_sha256=%s WHERE namespace=%s AND collection='users' AND id_key=%s",
                    (raw, hashlib.sha256(raw).hexdigest(), target.name, document_key(row)))
            await target._run(drift, write="users")
            before = state(owner, target.name)
            with pytest.raises(repair.IndexReconciliationBlocked) as caught:
                await repair.reconcile_index_metadata(view, owner, namespace=target.name,
                    freeze=freeze(view, target, "fresh-reconciliation-fence"), failed_copy_id=failed_id,
                    reconciliation_witness=witness)
            assert caught.value.reason_code == "reconciliation_existing_target_data_not_exact"
            assert caught.value.stage == "target_preflight"
            assert before == state(owner, target.name)
    asyncio.run(run())


@integration
def test_failure_after_metadata_and_key_changes_rolls_back_entire_repair(monkeypatch):
    original = repair._replace_metadata_and_tokens
    def fail(*args):
        original(*args)
        raise repair.IndexReconciliationBlocked("synthetic_atomic_test_failure", "atomic_metadata_repair")
    monkeypatch.setattr(repair, "_replace_metadata_and_tokens", fail)
    async def run():
        async with fixture() as (_source, view, target, owner, failed_id, witness):
            before = state(owner, target.name)
            with pytest.raises(repair.IndexReconciliationBlocked):
                await repair.reconcile_index_metadata(view, owner, namespace=target.name,
                    freeze=freeze(view, target, "fresh-reconciliation-fence"), failed_copy_id=failed_id,
                    reconciliation_witness=witness)
            assert before == state(owner, target.name)
    asyncio.run(run())


@integration
@pytest.mark.parametrize("change", ["view", "hash", "reference", "source", "expired", "writers"])
def test_existing_barrier_or_fresh_fence_mismatch_rejects_without_SQL_changes(change):
    async def run():
        async with fixture() as (_source, view, target, owner, failed_id, witness):
            fresh = freeze(view, target, "fresh-reconciliation-fence")
            if change == "view": witness["view_identity"] = {"view_id": "different"}
            if change == "hash": witness["control_record_sha256"] = "b" * 64
            if change == "reference": witness["original_fence_reference"] = "different-original-fence"
            if change == "source": witness["source_namespace"] = "different_namespace"
            if change == "expired": fresh["valid_until"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            if change == "writers": fresh["source_writers_stopped"] = False
            before = state(owner, target.name)
            with pytest.raises(repair.IndexReconciliationBlocked):
                await repair.reconcile_index_metadata(view, owner, namespace=target.name,
                    freeze=fresh, failed_copy_id=failed_id, reconciliation_witness=witness)
            assert before == state(owner, target.name)
    asyncio.run(run())
