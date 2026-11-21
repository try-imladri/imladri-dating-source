"""Read-only archive failure evidence never retries or changes unknown intents."""
import asyncio
import ast
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from backend import postgres_archive as archive, postgres_archive_diagnostics as diagnostics, postgres_operator as worker
from backend.postgres_copy_diagnostics import safe_error
from backend.tests.test_postgres_operator_control import owner_database
from backend.tests.test_postgres_archive import source_fixture


DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
RS = os.getenv("IMLADRI_SNAPSHOT_TEST_MONGO_URL")
integration = pytest.mark.skipif(not DSN or not RS, reason="Needs explicit loopback PostgreSQL and Mongo replica set.")


def test_static_archive_reason_map_covers_every_current_constant():
    tree = ast.parse(Path(__file__).parents[1].joinpath("postgres_archive.py").read_text())
    messages = {node.args[0].value for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "ArchiveBlocked" and node.args
        and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)}
    assert messages == set(archive.ARCHIVE_REASONS)
    assert len(set(archive.ARCHIVE_REASONS.values())) == len(messages)


def test_only_fixed_archive_reasons_and_stage_leave_error_handler():
    actual = safe_error(archive.ArchiveBlocked("Existing archives require an explicit source-key schema review; no automatic rewrite is allowed.", stage="target_schema"))
    assert actual == {"error_type": "ArchiveBlocked", "reason_code": "archive_existing_source_key_schema_requires_review", "stage": "target_schema"}
    unknown = safe_error(archive.ArchiveBlocked("ordinary nonallowlisted provider detail", stage="nonallowlisted"))
    assert unknown == {"error_type": "ArchiveBlocked", "reason_code": "unclassified_archive_block", "stage": "unspecified"}


@pytest.mark.parametrize("change", [{"state": "completed"}, {"source": "legacy_production"}, {"command": "copy_personal"}])
def test_unknown_archive_reference_must_match_source_command_and_state_before_any_access(change):
    class Journal:
        def lookup(self, _identity):
            return {"state": "unknown", "source": "recovery_production", "command": "archive_legacy", **change}
    def forbidden(*_args):
        pytest.fail("Mismatched old intent reached source or target")
    async def run():
        with pytest.raises(worker.OperatorBlocked, match="reference_scope_or_state"):
            await diagnostics.inspect_archive_state({}, {"source": "recovery_production", "failed_archive_id": str(uuid4())},
                Journal(), mongo_factory=forbidden, inventory=forbidden)
    asyncio.run(run())


@integration
def test_absent_schema_diagnostic_is_SELECT_only_and_does_not_initialize(owner_database):
    connection, options = owner_database
    result = diagnostics._owner_state(options, "recovery_production", "imladri_production")
    assert result["transaction_read_only"] and result["dedicated_database_owner"]
    assert result["schema_assessment"] == "absent" and result["tables"] == {}
    cursor = connection.cursor(); cursor.execute("SELECT to_regnamespace('imladri_archive') IS NULL")
    assert cursor.fetchone()[0] is True
    connection.rollback(); cursor.close()


@integration
def test_existing_V1_shape_is_reported_without_implicit_conversion(owner_database):
    connection, options = owner_database
    cursor = connection.cursor()
    cursor.execute("CREATE SCHEMA imladri_archive")
    cursor.execute("CREATE TABLE imladri_archive.collections(namespace text,source_database text,name text,PRIMARY KEY(namespace,source_database,name))")
    cursor.execute("CREATE TABLE imladri_archive.documents(namespace text,source_database text,collection text,id_key text,PRIMARY KEY(namespace,source_database,collection,id_key))")
    cursor.execute("CREATE TABLE imladri_archive.receipts(namespace text,source_database text,PRIMARY KEY(namespace,source_database))")
    connection.commit(); cursor.close()
    result = diagnostics._owner_state(options, "recovery_production", "imladri_production")
    assert result["schema_assessment"] == "requires_explicit_source_key_schema_review"
    assert result["existing_schema_validation"]["reason_code"] == "archive_existing_source_key_schema_requires_review"
    assert result["existing_schema_validation"]["stage"] == "target_schema"
    cursor = connection.cursor(); cursor.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='imladri_archive' AND column_name='source_alias'")
    assert cursor.fetchone()[0] == 0
    connection.rollback(); cursor.close()


@integration
def test_actual_verified_archive_counts_receipt_hash_and_schema_are_read_without_changes(owner_database):
    connection, options = owner_database
    async def run():
        async with source_fixture() as (source, view, _collection):
            result = await archive.archive_source(view, connection, source_alias="recovery_production")
            before = result["receipt"]
            state = diagnostics._owner_state(options, "recovery_production", source.name)
            assert state["schema_assessment"] == "v2_keys_present"
            assert state["existing_schema_validation"]["source_key_schema_verified"] is True
            assert state["selected_alias_receipt_count"] == 1
            assert state["selected_alias_receipts"][0]["stored_hash_matches"] is True
            assert state["tables"]["documents"]["selected_alias_row_count"] == before["physical_records"]
            assert state["tables"]["collections"]["selected_alias_row_count"] == before["collection_count"]
            assert worker.safe_result(state)
            cursor = connection.cursor()
            cursor.execute("SELECT receipt_sha256 FROM imladri_archive.receipts WHERE namespace=%s AND source_alias=%s", (archive.ARCHIVE_NAMESPACE, "recovery_production"))
            assert cursor.fetchone()[0] == state["selected_alias_receipts"][0]["actual_sha256"]
            connection.rollback(); cursor.close()
    asyncio.run(run())


@integration
def test_archive_schema_failure_now_retains_fixed_reason_and_actual_stage(owner_database):
    connection, _options = owner_database
    async def run():
        async with source_fixture() as (_source, view, _collection):
            cursor = connection.cursor(); archive._initialize(cursor); connection.commit(); cursor.close()
            cursor = connection.cursor()
            cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid='imladri_archive.documents'::regclass AND contype='f'")
            name = cursor.fetchone()[0]
            from backend.postgres_access import identifier
            cursor.execute("ALTER TABLE imladri_archive.documents DROP CONSTRAINT " + identifier(name))
            connection.commit(); cursor.close()
            with pytest.raises(archive.ArchiveBlocked) as caught:
                await archive.archive_source(view, connection, source_alias="recovery_production")
            error = safe_error(caught.value)
            assert error["reason_code"] == "archive_source_key_foreign_key_requires_review"
            assert error["stage"] == "target_schema"
            state = diagnostics._owner_state(_options, "recovery_production", _source.name)
            assert state["foreign_keys"] == []
            assert all(row["selected_alias_row_count"] == 0 for row in state["tables"].values())
    asyncio.run(run())


@integration
def test_malformed_missing_receipt_PK_cannot_make_diagnostic_buffer_unbounded_receipts(owner_database):
    from bson import BSON
    from backend.postgres_access import identifier
    connection, options = owner_database
    cursor = connection.cursor(); archive._initialize(cursor)
    cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid='imladri_archive.receipts'::regclass AND contype='p'")
    name = cursor.fetchone()[0]
    cursor.execute("ALTER TABLE imladri_archive.receipts DROP CONSTRAINT " + identifier(name))
    raw = BSON.encode({"version": 2})
    for _ in range(3):
        cursor.execute("INSERT INTO imladri_archive.receipts(namespace,source_alias,source_database,receipt_bson,receipt_sha256) VALUES(%s,%s,%s,%s,%s)",
            (archive.ARCHIVE_NAMESPACE, "recovery_production", "imladri_production", raw, hashlib.sha256(raw).hexdigest()))
    connection.commit(); cursor.close()
    result = diagnostics._owner_state(options, "recovery_production", "imladri_production")
    assert result["selected_alias_receipt_count"] == 3
    assert result["selected_alias_receipts_read"] == len(result["selected_alias_receipts"]) == 2
    assert result["selected_alias_receipt_inventory_truncated"] is True
    assert result["existing_schema_validation"]["complete"] is False
    assert worker.safe_result(result)


@integration
def test_current_readonly_source_two_passes_are_diagnostic_without_new_barrier():
    from backend.postgres_snapshot import pinned_source
    async def run():
        async with source_fixture(virtual=True, timeseries=True) as (source, _old_view, _collection):
            names = sorted(await source.list_collection_names())
            async with pinned_source(source, collection_allowlist=names, allow_local_plaintext=True) as view:
                before = await source.database_migration_reports.count_documents({})
                result = await diagnostics._source_preflight(view, names)
                assert result["complete"] and result["current_pinned_two_pass_verified"]
                assert result["final_writer_fence_or_archive_parity_claimed"] is False
                assert result["physical_records_read_including_both_passes"] == result["physical_records"] * 2
                assert worker.safe_result(result)
                assert await source.database_migration_reports.count_documents({}) == before
    asyncio.run(run())


@integration
def test_target_counts_survive_source_diagnostic_failure_and_unknown_intent_stays_untouched(owner_database):
    _connection, options = owner_database
    old = {"state": "unknown", "command": "archive_legacy", "source": "recovery_production", "result": {"error_type": "ArchiveBlocked"}}
    original = json.dumps(old, sort_keys=True)
    class Journal:
        def lookup(self, _identity): return old
        def reserve(self, *_args): pytest.fail("Read-only archive diagnosis reserved a write")
        def finish(self, *_args): pytest.fail("Read-only archive diagnosis changed an intent")
    def unavailable(*_args): raise RuntimeError("ordinary provider details withheld")
    config = {"owner_options": options, "sources": {"recovery_production": {"database": "imladri_production"}}}
    result = asyncio.run(diagnostics.inspect_archive_state(config, {"source": "recovery_production", "failed_archive_id": str(uuid4())},
        Journal(), mongo_factory=unavailable, inventory=unavailable))
    assert result["target"]["schema_assessment"] == "absent"
    assert result["source"] == {"complete": False, "error_type": "RuntimeError"}
    assert result["prior_intent_modified"] is False and result["source_barrier_created"] is False
    assert original == json.dumps(old, sort_keys=True)
    assert worker.safe_result(result)
