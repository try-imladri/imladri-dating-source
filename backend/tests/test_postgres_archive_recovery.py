"""Explicit manual archive recovery proves the complete old-request chain."""
import asyncio
from copy import deepcopy
import json
import os
from uuid import uuid4

from bson import BSON
from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from backend import postgres_archive as archive, postgres_archive_recovery as recovery, postgres_operator as worker
from backend.tests.test_postgres_operator_control import environment, request, fence, owner_database
from backend.tests.test_postgres_archive_rehearsal import recovery_source, local_snapshot_adapters


DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
RS = os.getenv("IMLADRI_REHEARSAL_TEST_MONGO_URL")
integration = pytest.mark.skipif(not DSN or not RS, reason="Needs explicit isolated PostgreSQL/Recovery replica set.")


def inputs(source="recovery_production"):
    env = environment()
    original = request("archive_legacy", source=source, fence=fence(env, source))
    prior = request("rehearse_archive", source=source, failed_archive_id=original["operation_id"],
        fence=fence(env, source, ["rehearse_archive"]))
    current = request("recover_archive", source=source, failed_archive_id=original["operation_id"],
        failed_rehearsal_id=prior["operation_id"], fence=fence(env, source, ["recover_archive"]))
    evidence = {"original_archive_request": original, "original_archive_configuration_sha256": "c" * 64,
        "prior_rehearsal_request": prior, "prior_rehearsal_configuration_sha256": "c" * 64}
    trusted = json.loads(env["IMLADRI_MIGRATION_FENCE_JSON"])
    trusted["manual_archive_recovery"] = evidence
    env["IMLADRI_MIGRATION_FENCE_JSON"] = json.dumps(trusted)
    env["IMLADRI_MIGRATION_FENCE_SHA256"] = worker.digest(trusted)
    current["fence"]["proof_sha256"] = worker.digest(trusted)
    finding = {"error_type": "ArchiveRehearsalBlocked", "reason_code": "rehearsal_failed", "stage": "archive_core",
        "archive_path_error": {"error_type": "ArchiveBlocked", "reason_code": "archive_source_target_parity_failed",
            "stage": "target_verification"}, "diagnostic_only": True,
        "target_schema_absent_independently_verified": True, "prior_unknown_unchanged": True,
        "archive_committed": False, "target_commit_operations": 0, "source_application_write_operations": 0}
    rows = {}
    for body, result in ((original, {"error_type": "ArchiveBlocked"}), (prior, finding)):
        rows[body["operation_id"]] = {"request_sha256": worker.digest({**body, "configuration_sha256": "c" * 64}),
            "command": body["command"], "source": body["source"], "release_sha": body["expected_release_sha"],
            "state": "unknown", "result": result}
    return env, current, rows, evidence


def test_recovery_requires_own_fresh_scope_three_ids_and_rejects_caller_internal_hash():
    env, body, _rows, _evidence = inputs()
    assert worker.validate_request(body, env)["command"] == "recover_archive"
    for change in ({"source": "legacy_production"}, {"source": "market_live"},
        {"failed_rehearsal_id": body["failed_archive_id"]}, {"failed_rehearsal_id": body["operation_id"]},
        {"recovery_links_sha256": "0" * 64}, {"prior_copy_id": str(uuid4())}):
        with pytest.raises(worker.OperatorBlocked): worker.validate_request({**body, **change}, env)
    for field in ("failed_archive_id", "failed_rehearsal_id"):
        with pytest.raises(worker.OperatorBlocked): worker.validate_request({k: v for k, v in body.items() if k != field}, env)
    mixed = {**body, "fence": fence(env, "recovery_production", ["archive_legacy", "recover_archive"])}
    with pytest.raises(worker.OperatorBlocked, match="own_fence"): worker.validate_request(mixed, env)


@pytest.mark.parametrize("change", ["missing", "old_state", "origin", "digest", "wrong_link", "cfg", "SQLerror",
    "schema", "committed", "source_write", "wrong_stage", "hash_after_fence", "current_cfg"])
def test_both_original_requests_and_safe_rollback_finding_are_required_before_intent(monkeypatch, change):
    import pg8000.dbapi
    env, body, rows, _evidence = inputs()
    trusted = json.loads(env["IMLADRI_MIGRATION_FENCE_JSON"])
    finding = rows[body["failed_rehearsal_id"]]["result"]
    if change == "missing": trusted.pop("manual_archive_recovery")
    if change == "old_state": rows[body["failed_archive_id"]]["state"] = "completed"
    if change == "origin": rows[body["failed_rehearsal_id"]]["source"] = "recovery_staging"
    if change == "digest": rows[body["failed_archive_id"]]["request_sha256"] = "0" * 64
    if change == "wrong_link": trusted["manual_archive_recovery"]["prior_rehearsal_request"]["failed_archive_id"] = str(uuid4())
    if change == "cfg": trusted["manual_archive_recovery"]["original_archive_configuration_sha256"] = "d" * 64
    if change == "SQLerror": finding["sql_failure"] = {"sqlstate": "23505"}
    if change == "schema": finding["target_schema_absent_independently_verified"] = False
    if change == "committed": finding["target_commit_operations"] = 1
    if change == "source_write": finding["source_application_write_operations"] = 1
    if change == "wrong_stage": finding["archive_path_error"]["stage"] = "first_source_pass"
    env["IMLADRI_MIGRATION_FENCE_JSON"] = json.dumps(trusted)
    env["IMLADRI_MIGRATION_FENCE_SHA256"] = worker.digest(trusted)
    body["fence"]["proof_sha256"] = worker.digest(trusted)
    if change == "hash_after_fence": env["IMLADRI_MIGRATION_FENCE_SHA256"] = "0" * 64
    class Connection:
        def close(self): pass
    class Journal:
        def lookup(self, identity): return deepcopy(rows.get(identity))
        def reserve(self, *_): pytest.fail("Unproved recovery reserved a new intent")
    monkeypatch.setattr(pg8000.dbapi, "connect", lambda **_: Connection())
    async def forbidden(*_args, **_kwargs): pytest.fail("Unproved recovery reached source")
    monkeypatch.setattr(worker, "execute", forbidden)
    async def run():
        with pytest.raises(worker.OperatorBlocked):
            await worker.run({"action": "execute", "request": body}, env,
                config_loader=lambda _: {"configuration_sha256": ("d" if change == "current_cfg" else "c") * 64,
                    "owner_options": {}},
                journal_factory=lambda _: Journal())
    asyncio.run(run())


@integration
def test_actual_RS_PG_manual_recovery_commits_V2_linked_receipt_and_keeps_both_unknowns(owner_database, monkeypatch):
    connection, options = owner_database
    local_snapshot_adapters(monkeypatch)
    actual_recover = recovery.recover_archive
    async def local_recover(*args, **kwargs):
        return await actual_recover(*args, **kwargs, allow_local_plaintext=True)
    monkeypatch.setattr(recovery, "recover_archive", local_recover)
    async def run():
        async with recovery_source(database_name="imladri_staging") as (seed, database, collection, empty, reference, original):
            accesses = []
            def mongo(_config, logical):
                accesses.append(logical)
                client = AsyncIOMotorClient(RS, serverSelectionTimeoutMS=5000, connectTimeoutMS=20000, socketTimeoutMS=20000)
                return client, client[database.name], {}
            monkeypatch.setattr(worker, "_mongo", mongo)
            env, body, rows, evidence = inputs(source="recovery_staging")
            source = body["source"]
            trusted = json.loads(env["IMLADRI_MIGRATION_FENCE_JSON"])
            trusted["claims"]["reference"] = reference
            env["IMLADRI_MIGRATION_FENCE_JSON"] = json.dumps(trusted)
            env["IMLADRI_MIGRATION_FENCE_SHA256"] = worker.digest(trusted)
            body["fence"] = {**trusted["claims"], "proof_sha256": worker.digest(trusted)}
            config = {"configuration_sha256": "c" * 64, "owner_options": options,
                "sources": {source: {"database": database.name, "control_database": "imladri_migration_control"}}}
            journal = worker.OperatorJournal(connection)
            for key, prior in (("original_archive_request", rows[body["failed_archive_id"]]),
                    ("prior_rehearsal_request", rows[body["failed_rehearsal_id"]])):
                old = {**evidence[key], "configuration_sha256": "c" * 64}
                journal.reserve(old); journal.finish(old, prior["result"], success=False)
            before = {identity: journal.lookup(identity) for identity in rows}
            result = await worker.run({"action": "execute", "request": body}, env, config_loader=lambda _: config)
            assert result["status"] == "completed" and result["result"]["manual_recovery_completed"] is True
            receipt = result["result"]["receipt"]
            assert receipt["version"] == 2 and receipt["physical_records"] == 2
            assert receipt["manual_recovery"]["original_archive_id"] == body["failed_archive_id"]
            assert receipt["manual_recovery"]["prior_rehearsal_id"] == body["failed_rehearsal_id"]
            assert all(journal.lookup(identity) == before[identity] for identity in rows)
            cursor = connection.cursor()
            cursor.execute("SELECT receipt_bson FROM imladri_archive.receipts WHERE source_alias=%s", (source,))
            assert BSON(bytes(cursor.fetchone()[0])).decode() == receipt
            cursor.execute("SELECT raw_bson FROM imladri_archive.documents WHERE source_alias=%s AND collection=%s", (source, collection))
            assert sorted(bytes(row[0]) for row in cursor.fetchall()) == sorted(original)
            connection.rollback(); cursor.close()
            repeated = await worker.run({"action": "execute", "request": body}, env, config_loader=lambda _: config)
            assert repeated["replayed_without_execution"] is True and accesses == [source]
            normal = request("archive_legacy", source=source, fence=fence(env, source))
            with pytest.raises(worker.OperatorBlocked, match="earlier_step_has_unknown_outcome"):
                await worker.run({"action": "execute", "request": normal}, env, config_loader=lambda _: config)
            assert accesses == [source]
            actual = [BSON.encode(row) for row in await database[collection].find({}).sort("_id").to_list(None)]
            assert actual == original
    asyncio.run(run())
