"""Protected operator history linkage and actual metadata-only recovery flow."""
import asyncio
from copy import deepcopy
import hashlib
import json
import os
import secrets
from urllib.parse import quote
from uuid import uuid4

from bson import BSON, SON
from pymongo import IndexModel
import pytest

from backend import postgres_operator as worker, postgres_operator_api as api
from backend.tests.test_postgres_operator_control import environment, fence, request, owner_database
from backend.tests.test_postgres_operator_copy import local_config, local_only
from backend.tests.test_postgres_snapshot import source_fixture


DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
RS = os.getenv("IMLADRI_SNAPSHOT_TEST_MONGO_URL")
integration = pytest.mark.skipif(not DSN or not RS, reason="Requires explicit disposable loopback PG and Mongo replica set.")


class LookupJournal:
    def __init__(self, records):
        self.records, self.lookups = deepcopy(records), []
    def lookup(self, identity):
        self.lookups.append(identity)
        return deepcopy(self.records.get(identity))


def linked_request(source="personal_preview", *, original=None):
    if original is None:
        old_env = {**environment(), "RENDER_GIT_COMMIT": "b" * 40}
        original = request("copy_personal" if source in worker.NAMESPACES else "copy_market",
            source=source, fence=fence(old_env, source), expected_release_sha="b" * 40)
        original["configuration_sha256"] = "c" * 64
    env = environment()
    value = fence(env, source, ["reconcile_indexes", "verify"])
    trusted = json.loads(env["IMLADRI_MIGRATION_FENCE_JSON"])
    trusted["reconciliation"] = {"linked_failed_copy_id": original["operation_id"], "original_request": original,
        "source_application_writers_continuously_paused": True, "target_application_writers_continuously_paused": True}
    set_trusted(env, trusted)
    body = request("reconcile_indexes", source=source, failed_copy_id=original["operation_id"],
                   fence={**value, "proof_sha256": worker.digest(trusted)})
    prior = {"state": "unknown", "command": original["command"], "source": source,
             "release_sha": original["expected_release_sha"], "request_sha256": worker.digest(original),
             "result": {"error_type": "FrozenCopyBlocked", "needs_manual_review": True}}
    return env, body, original, LookupJournal({original["operation_id"]: prior})


def set_trusted(env, value):
    env["IMLADRI_MIGRATION_FENCE_JSON"] = json.dumps(value)
    env["IMLADRI_MIGRATION_FENCE_SHA256"] = worker.digest(value)


def test_manual_reconciliation_requires_its_own_identity_and_fresh_command_scope():
    env, body, original, _ = linked_request()
    assert worker.validate_request(body, env)["failed_copy_id"] == original["operation_id"]
    for change in ({"failed_copy_id": None}, {"operation_id": original["operation_id"]},
                   {"prior_copy_id": str(uuid4())}, {"source": "recovery_staging"}):
        with pytest.raises(worker.OperatorBlocked):
            worker.validate_request({**body, **change}, env)
    env = environment()
    with pytest.raises(worker.OperatorBlocked, match="scope_mismatch"):
        worker.validate_request({**body, "fence": fence(env, "personal_preview")}, env)


@pytest.mark.parametrize("field,value", [("state", "completed"), ("state", "running"),
    ("source", "personal_live"), ("command", "copy_market"), ("release_sha", "d" * 40),
    ("request_sha256", "e" * 64)])
def test_unknown_original_must_match_actual_durable_request_exactly(field, value):
    env, body, original, journal = linked_request()
    journal.records[original["operation_id"]][field] = value
    before = deepcopy(journal.records)
    with pytest.raises(worker.OperatorBlocked, match="history_does_not_match"):
        worker.reconciliation_link(body, env, journal)
    assert journal.records == before


@pytest.mark.parametrize("change", ["old_reference", "old_configuration", "source_continuity", "target_continuity"])
def test_hashed_fence_cannot_rewrite_old_request_or_skip_continuity(change):
    env, body, _, journal = linked_request()
    trusted = json.loads(env["IMLADRI_MIGRATION_FENCE_JSON"])
    if change == "old_reference": trusted["reconciliation"]["original_request"]["fence"]["reference"] = "different-valid-reference"
    elif change == "old_configuration": trusted["reconciliation"]["original_request"]["configuration_sha256"] = "d" * 64
    else: trusted["reconciliation"][change.replace("_continuity", "_application_writers_continuously_paused")] = False
    set_trusted(env, trusted)
    body["fence"]["proof_sha256"] = worker.digest(trusted)
    with pytest.raises(worker.OperatorBlocked): worker.reconciliation_link(body, env, journal)


def test_history_link_returns_hashes_and_never_mutates_old_unknown():
    env, body, original, journal = linked_request()
    before = deepcopy(journal.records)
    linked = worker.reconciliation_link(body, env, journal)
    assert linked["original_request_sha256"] == worker.digest(original)
    assert linked["original_fence_reference"] == original["fence"]["reference"]
    assert journal.records == before and worker.safe_result(linked)
    assert worker.reconciliation_failure(worker.OperatorBlocked("original_control_record_not_in_majority_snapshot")) == {
        "reason_code": "original_control_record_not_in_majority_snapshot", "stage": "source_barrier_witness"}
    assert worker.reconciliation_failure(worker.OperatorBlocked("unreviewed private provider output")) == {}


@pytest.mark.parametrize("durable_status,expected", [("not_found", "blocked"), ("completed", "completed")])
def test_validation_failure_keeps_reason_unless_durable_completion_exists(durable_status, expected):
    async def run():
        async def runner(envelope, _environ):
            if envelope["action"] == "execute":
                return {"status": "blocked", "reason_code": "unknown_copy_request_history_does_not_match", "stage": "validate_link"}
            return {"status": durable_status}
        controller = api.OperatorController(environment(), runner=runner)
        body = request()
        await controller.submit(body)
        await controller.operations[body["operation_id"]]["task"]
        result = await controller.status(body["operation_id"])
        assert result["status"] == expected
        if expected == "blocked": assert result["stage"] == "validate_link"
    asyncio.run(run())


@integration
@pytest.mark.parametrize("failure", [None, "missing", "duplicate", "shape", "limit"])
def test_actual_snapshot_witness_is_readonly_unique_and_bounded(failure):
    from backend.postgres_snapshot import create_majority_barrier, pinned_source
    async def run():
        async with source_fixture() as source:
            await source.users.insert_one({"_id": "one"})
            env, body, _, journal = linked_request()
            link = worker.reconciliation_link(body, env, journal)
            await create_majority_barrier(source, fence_reference=link["original_fence_reference"],
                application_writers_stopped=True, application_background_writers_stopped=True, ddl_stopped=True,
                allow_local_plaintext=True)
            row = await source.database_migration_reports.find_one({})
            if failure == "missing": await source.database_migration_reports.delete_many({})
            elif failure == "duplicate":
                await source.database_migration_reports.insert_one({**row, "_id": "postgres-cutover-barrier:" + uuid4().hex})
            elif failure == "shape": await source.database_migration_reports.update_one({"_id": row["_id"]}, {"$set": {"unexpected": "synthetic-only"}})
            before = [BSON.encode(x) for x in await source.database_migration_reports.find({}).to_list(None)]
            async with pinned_source(source, allow_local_plaintext=True) as view:
                if failure:
                    with pytest.raises(worker.OperatorBlocked):
                        await worker.reconciliation_witness(view, link, maximum_bytes=1 if failure == "limit" else 32 * 1024 * 1024)
                else:
                    witness = await worker.reconciliation_witness(view, link, maximum_bytes=32 * 1024 * 1024)
                    assert witness["control_record_sha256"] == hashlib.sha256(before[0]).hexdigest()
                    assert witness["source_write_operations"] == 0
                    assert view.evidence["majority_write_barrier"] is None
                    assert witness["original_timestamp_reconstructed"] is False
                    assert worker.safe_result(witness)
            assert [BSON.encode(x) for x in await source.database_migration_reports.find({}).to_list(None)] == before
    asyncio.run(run())


@integration
def test_actual_operator_repair_creates_new_receipt_and_verifies_runtime_without_recopy(owner_database, monkeypatch):
    from backend.postgres_access import bootstrap_access, identifier
    from backend.postgres_codec import connection_options
    import pg8000.dbapi
    owner, options = owner_database
    store_type = local_only(monkeypatch)
    role = "imladri_app_reconcile_" + uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    async def run():
        async with source_fixture() as source:
            logical = "personal_preview"
            monkeypatch.setitem(worker.NAMESPACES, logical, source.name)
            monkeypatch.setitem(worker.SOURCE_DATABASES, logical, source.name)
            model = IndexModel([("zulu", 1), ("alpha", -1)], name="ordered", unique=True,
                               partialFilterExpression={"literal": SON([("zulu", 5), ("alpha", 6)])})
            await source.users.create_indexes([model])
            await source.users.insert_one({"_id": "one", "zulu": 1, "alpha": 2, "literal": {"zulu": 5, "alpha": 6}})
            config = local_config(source, options, logical)
            config["configuration_sha256"] = "c" * 64
            original_env = {**environment(), "RENDER_GIT_COMMIT": "b" * 40}
            original = request("copy_personal", source=logical, expected_release_sha="b" * 40,
                               fence=fence(original_env, logical))
            original["configuration_sha256"] = "c" * 64
            await worker.copy_application(config, original, None, application="personal")
            cursor = owner.cursor()
            cursor.execute("UPDATE imladri_app.indexes SET definition=%s::jsonb WHERE namespace=%s AND name='ordered'",
                           (json.dumps(dict(model.document)), source.name))
            owner.commit(); cursor.close()
            bootstrap_access(owner, {role: source.name}, {role: password}, allow_public_acl_hardening=True)
            dsn = f'postgresql://{role}:{quote(password,safe="")}@127.0.0.1:{options["port"]}/{options["database"]}?sslmode=disable'
            config["role_dsns"][logical] = dsn
            config["role_options"][logical] = connection_options(dsn, allow_local_plaintext=True)
            journal = worker.OperatorJournal(owner)
            journal.reserve(original)
            journal.finish(original, {"error_type": "FrozenCopyBlocked", "needs_manual_review": True}, success=False)
            old_saved = journal.lookup(original["operation_id"])
            env, body, _, _ = linked_request(logical, original=original)
            worker.validate_request(body, env)
            config["_verified_reconciliation_link"] = worker.reconciliation_link(body, env, journal)
            target = store_type(config["owner_dsn"], source.name, allow_local_plaintext=True, initialize_schema=True)
            try:
                async def records():
                    cursor = owner.cursor()
                    try:
                        cursor.execute("SELECT collection,id_key,raw_bson,source_sha256,revision,updated_at FROM imladri_app.documents WHERE namespace=%s ORDER BY collection,id_key", (source.name,))
                        return [(a, b, bytes(c), d, e, f) for a, b, c, d, e, f in cursor.fetchall()]
                    finally:
                        cursor.close(); owner.rollback()
                before = await records()
                journal.reserve({**body, "configuration_sha256": "c" * 64})
                result = await worker.reconcile_indexes(config, body, journal)
                journal.finish({**body, "configuration_sha256": "c" * 64}, result, success=True)
                assert journal.lookup(original["operation_id"]) == old_saved
                assert result["target_application_document_write_operations"] == 0 and result["source_application_write_operations"] == 0
                assert result["source_snapshot"]["majority_write_barrier"] is None and worker.safe_result(result)
                assert await records() == before
                check = request("verify", source=logical, prior_copy_id=body["operation_id"], fence=body["fence"])
                verified = await worker.verify_copy(config, check, journal)
                assert verified["exact_copy_parity"] is True and set(verified["runtime_roles"]) == {logical}
            finally:
                await target.aclose()
    try:
        asyncio.run(run())
    finally:
        cursor = owner.cursor()
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
        if cursor.fetchone():
            cursor.execute("DROP OWNED BY " + identifier(role)); cursor.execute("DROP ROLE " + identifier(role))
        owner.commit(); cursor.close()
