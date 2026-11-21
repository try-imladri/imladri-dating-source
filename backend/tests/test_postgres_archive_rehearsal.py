"""Fixed Recovery rehearsal exercises real archive SQL without saving an archive."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import json
import os
import ssl
from urllib.parse import urlsplit
from uuid import uuid4

from bson import BSON, Int64
from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from backend import postgres_archive as archive, postgres_archive_rehearsal as rehearsal
from backend import postgres_operator as worker, postgres_snapshot as snapshot
from backend.tests.test_postgres_operator_control import environment, request, fence, owner_database


DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
RS = os.getenv("IMLADRI_REHEARSAL_TEST_MONGO_URL")
integration = pytest.mark.skipif(not DSN or not RS,
    reason="Needs explicit disposable PostgreSQL and a dedicated rehearsal Mongo replica set.")


def body_and_environment():
    env = environment()
    body = request("rehearse_archive", source="recovery_production", failed_archive_id=str(uuid4()),
        fence=fence(env, "recovery_production", ["rehearse_archive"]))
    return body, env


def test_rehearsal_requires_its_own_Recovery_only_fence_and_distinct_unknown_link():
    body, env = body_and_environment()
    assert worker.validate_request(body, env)["command"] == "rehearse_archive"
    for change in ({"source": "legacy_production"}, {"source": "personal_preview"},
        {"failed_archive_id": None}, {"operation_id": body["failed_archive_id"]},
        {"prior_copy_id": str(uuid4())}, {"failed_copy_id": str(uuid4())}, {"fence": {}}):
        with pytest.raises(worker.OperatorBlocked): worker.validate_request({**body, **change}, env)
    with pytest.raises(worker.OperatorBlocked):
        worker.validate_request({key: value for key, value in body.items() if key != "failed_archive_id"}, env)
    both = {**body, "fence": fence(env, "recovery_production", ["archive_legacy", "rehearse_archive"])}
    with pytest.raises(worker.OperatorBlocked, match="own_fence"): worker.validate_request(both, env)
    old = {**body, "fence": fence(env, "recovery_production", ["archive_legacy"])}
    with pytest.raises(worker.OperatorBlocked, match="scope_mismatch"): worker.validate_request(old, env)


@pytest.mark.parametrize("change", [{"state": "completed"}, {"source": "recovery_staging"},
    {"command": "copy_personal"}, {"request_sha256": "not-a-digest"}])
def test_wrong_prior_unknown_cannot_reserve_new_diagnostic_or_access_source(monkeypatch, change):
    import pg8000.dbapi
    class Connection:
        def close(self): pass
    class Journal:
        def lookup(self, _identity):
            return {"command": "archive_legacy", "state": "unknown", "source": "recovery_production",
                "request_sha256": "a" * 64, **change}
        def reserve(self, _request): pytest.fail("Mismatched unknown reached reservation")
    monkeypatch.setattr(pg8000.dbapi, "connect", lambda **_kwargs: Connection())
    async def forbidden(*_args, **_kwargs): pytest.fail("Mismatched unknown reached source")
    monkeypatch.setattr(worker, "execute", forbidden)
    async def run():
        body, env = body_and_environment()
        with pytest.raises(rehearsal.ArchiveRehearsalBlocked, match="unknown_archive_link"):
            await worker.run({"action": "execute", "request": body}, env,
                config_loader=lambda _: {"configuration_sha256": "c" * 64, "owner_options": {}},
                journal_factory=lambda _: Journal())
    asyncio.run(run())


@pytest.mark.parametrize("sql", ["COMMIT", "ROLLBACK", "BEGIN", "END", "START TRANSACTION",
    "PREPARE TRANSACTION 'x'", "SELECT 1; /* comment */ COMMIT", "SET SESSION autocommit=on",
    "SAVEPOINT x", "RELEASE SAVEPOINT x"])
def test_rollback_guard_forbids_transaction_escape_and_never_calls_real_commit(sql):
    class Cursor:
        def execute(self, *_args): pytest.fail("Transaction control reached PostgreSQL")
        def close(self): pass
    class Connection:
        autocommit = False
        def cursor(self): return Cursor()
        def commit(self): pytest.fail("Rehearsal called a real commit")
        def rollback(self): pass
        def close(self): pass
    guarded = rehearsal.RollbackOnlyConnection(Connection())
    with pytest.raises(rehearsal.ArchiveRehearsalBlocked, match="transaction_control"):
        guarded.cursor().execute(sql)
    with pytest.raises(AttributeError): guarded.autocommit = True
    guarded.commit()
    assert guarded.commit_interceptions == 1 and guarded.rollback_completed
    with pytest.raises(rehearsal.ArchiveRehearsalBlocked): guarded.cursor()
    guarded.close()


def test_SQL_guard_allows_exact_function_bodies_and_CASE_but_not_unclosed_literals():
    for sql in archive.SCHEMA:
        assert rehearsal._visible_statements(sql)
    assert rehearsal._visible_statements("SELECT CASE WHEN true THEN 'COMMIT' ELSE 'x' END")
    for sql in ("SELECT 'unclosed", "SELECT $$unclosed", "SELECT 1 /* unclosed"):
        with pytest.raises(rehearsal.ArchiveRehearsalBlocked): rehearsal._visible_statements(sql)


@pytest.mark.parametrize("state", ["23505", "ZZ999", "23505 private", "lower", b"23505", ["23505"],
    {"private": "state-body"}, None])
def test_SQL_failure_only_exposes_fixed_template_hash_sequence_known_class_and_strict_SQLSTATE(state):
    import pg8000.dbapi
    template = "INSERT INTO imladri_archive.documents VALUES(%s)"
    error = pg8000.dbapi.IntegrityError({"C": state, "M": "private message", "D": "private source answer",
        "n": "private constraint", "W": "private context", "q": "private query"})
    class Cursor:
        def execute(self, *_args): raise error
        def close(self): pass
    class Connection:
        autocommit = False
        def cursor(self): return Cursor()
        def commit(self): pytest.fail("SQL diagnostic called a real commit")
        def rollback(self): pass
        def close(self): pass
    guarded = rehearsal.RollbackOnlyConnection(Connection())
    with pytest.raises(pg8000.dbapi.IntegrityError):
        guarded.cursor().execute(template, ("private source parameter",))
    guarded.close()
    summary = rehearsal.safe_rehearsal_error(rehearsal.ArchiveRehearsalBlocked("rehearsal_failed",
        cleanup={"sql_failure": guarded.sql_failure}))
    assert summary["sql_failure"] == {"driver_class": "IntegrityError", "statement_sequence": 1,
        "statement_sha256": hashlib.sha256(template.encode()).hexdigest(),
        "sqlstate": state if state in ("23505", "ZZ999") else None}
    assert "private" not in json.dumps(summary)
    assert worker.safe_result(summary)


def test_untrusted_driver_subclasses_cannot_spoof_allowed_SQL_failure_metadata():
    import pg8000.dbapi
    class UntrustedError(pg8000.dbapi.DatabaseError): pass
    class Connection:
        autocommit = False
    guarded = rehearsal.RollbackOnlyConnection(Connection())
    guarded.record_sql_failure(UntrustedError({"C": "23505", "M": "private"}), "SELECT 1")
    assert guarded.sql_failure is None


def test_strict_TLS_is_required_and_plaintext_fixture_escape_is_explicit_loopback_only():
    context = ssl.create_default_context()
    rehearsal._verified_owner_options({"host": "pg.example.invalid", "user": "owner", "ssl_context": context})
    for options, allow in (({"host": "pg.example.invalid", "ssl_context": False}, True),
        ({"host": "127.0.0.1", "ssl_context": False}, False),
        ({"host": "127.0.0.1", "ssl_context": context, "user": "imladri_app_live"}, False)):
        with pytest.raises(rehearsal.ArchiveRehearsalBlocked):
            rehearsal._verified_owner_options({"user": "owner", **options}, allow_local_plaintext=allow)
    rehearsal._verified_owner_options({"host": "127.0.0.1", "user": "owner", "ssl_context": False},
        allow_local_plaintext=True)


@integration
def test_existing_archive_schema_blocks_rehearsal_before_any_source_access(owner_database):
    connection, options = owner_database
    cursor = connection.cursor()
    cursor.execute("CREATE SCHEMA imladri_archive")
    connection.commit(); cursor.close()
    body, _env = body_and_environment()
    class Journal:
        def lookup(self, _identity):
            return {"state": "unknown", "source": "recovery_production", "command": "archive_legacy",
                "request_sha256": "a" * 64}
    async def forbidden(*_args, **_kwargs): pytest.fail("Existing archive reached source/barrier")
    async def run():
        with pytest.raises(rehearsal.ArchiveRehearsalBlocked) as caught:
            await rehearsal.rehearse_archive({"owner_options": options,
                "sources": {"recovery_production": {"database": "imladri_production",
                    "control_database": "imladri_migration_control"}}}, body, Journal(),
                archive_runner=forbidden, allow_local_plaintext=True)
        assert rehearsal.safe_rehearsal_error(caught.value)["stage"] == "target_before"
    asyncio.run(run())
    cursor = connection.cursor(); cursor.execute("SELECT to_regnamespace('imladri_archive') IS NOT NULL")
    assert cursor.fetchone()[0] is True
    connection.rollback(); cursor.close()


@asynccontextmanager
async def recovery_source(*, database_name="imladri_production"):
    parsed = urlsplit(RS)
    assert parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    client = AsyncIOMotorClient(RS, serverSelectionTimeoutMS=5000, connectTimeoutMS=20000,
        socketTimeoutMS=20000)
    assert database_name in {"imladri_production", "imladri_staging"}
    database = client[database_name]
    # The fixed Recovery namespace must be in a dedicated explicitly supplied
    # fixture. Never clear pre-existing catalog data to make a test pass.
    assert await database.list_collection_names() == []
    collection = "rehearsal_fixture_" + uuid4().hex
    empty = collection + "_empty"
    reference = "rehearsal-control-" + uuid4().hex
    try:
        await database[collection].insert_many([
            {"_id": "first", "private": "fixture-only-private", "number": Int64(9), "nul": "x\x00y"},
            {"_id": "second", "answer": {"zulu": 1, "alpha": 2}},
        ])
        await database[collection].create_index([("zulu", 1), ("alpha", -1)], name="ordered_compound")
        await database.create_collection(empty)
        original = [BSON.encode(row) for row in await database[collection].find({}).sort("_id").to_list(None)]
        yield client, database, collection, empty, reference, original
    finally:
        await database.drop_collection(collection)
        await database.drop_collection(empty)
        await client.imladri_migration_control.database_migration_reports.delete_many({"fence_reference": reference})
        client.close()


def local_snapshot_adapters(monkeypatch):
    create, pin = snapshot.create_majority_barrier, snapshot.pinned_source
    async def barrier(*args, **kwargs): return await create(*args, **kwargs, allow_local_plaintext=True)
    @asynccontextmanager
    async def pinned(*args, **kwargs):
        async with pin(*args, **kwargs, allow_local_plaintext=True) as view: yield view
    monkeypatch.setattr(snapshot, "create_majority_barrier", barrier)
    monkeypatch.setattr(snapshot, "pinned_source", pinned)


@integration
@pytest.mark.parametrize("failure", [None, "record_SQL", "cancel"])
def test_real_RS_PG_exact_archive_rehearsal_rolls_back_and_preserves_old_unknown(owner_database, monkeypatch, failure):
    import pg8000.dbapi
    connection, options = owner_database
    local_snapshot_adapters(monkeypatch)
    real_rehearsal = rehearsal.rehearse_archive
    async def fixture_rehearsal(*args, **kwargs):
        return await real_rehearsal(*args, **kwargs, allow_local_plaintext=True)
    monkeypatch.setattr(rehearsal, "rehearse_archive", fixture_rehearsal)
    real_execute = rehearsal._RollbackCursor.execute
    observed_inserts = []
    def fault(cursor, sql, args=()):
        if sql.startswith("INSERT INTO imladri_archive.documents"):
            observed_inserts.append(True)
            if failure == "record_SQL":
                native = cursor._cursor
                class DuplicateInsert:
                    def execute(self, statement, values):
                        native.execute(statement, values)
                        # Generate an ordinary actual PostgreSQL unique violation;
                        # private driver fields/row values must stay inside SQL.
                        return native.execute(statement, values)
                    def close(self): return native.close()
                cursor._cursor = DuplicateInsert()
            if failure == "cancel": raise asyncio.CancelledError()
        return real_execute(cursor, sql, args)
    monkeypatch.setattr(rehearsal._RollbackCursor, "execute", fault)
    async def run():
        async with recovery_source() as (client, database, collection, empty, reference, original):
            # The production path closes its own Mongo client. Give it a second
            # fixture client while retaining the seed connection for assertions.
            def mongo(_config, logical):
                other = AsyncIOMotorClient(RS, serverSelectionTimeoutMS=5000, connectTimeoutMS=20000,
                    socketTimeoutMS=20000)
                return other, other["imladri_production"], {}
            monkeypatch.setattr(worker, "_mongo", mongo)
            body, env = body_and_environment()
            trusted = json.loads(env["IMLADRI_MIGRATION_FENCE_JSON"])
            trusted["claims"]["reference"] = reference
            env["IMLADRI_MIGRATION_FENCE_JSON"] = json.dumps(trusted)
            env["IMLADRI_MIGRATION_FENCE_SHA256"] = worker.digest(trusted)
            body["fence"] = {**trusted["claims"], "proof_sha256": worker.digest(trusted)}
            config = {"configuration_sha256": "c" * 64, "owner_options": options,
                "sources": {"recovery_production": {"database": "imladri_production",
                    "control_database": "imladri_migration_control"}}}
            journal = worker.OperatorJournal(connection)
            old = request("archive_legacy", source="recovery_production",
                operation_id=body["failed_archive_id"], configuration_sha256="d" * 64)
            journal.reserve(old)
            journal.finish(old, {"error_type": "ArchiveBlocked", "needs_manual_review": True}, success=False)
            prior = deepcopy(journal.lookup(body["failed_archive_id"]))
            if failure == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await worker.run({"action": "execute", "request": body}, env,
                        config_loader=lambda _: config)
                current = journal.lookup(body["operation_id"])
                assert current["state"] == "running"
            else:
                result = await worker.run({"action": "execute", "request": body}, env,
                    config_loader=lambda _: config)
                assert worker.safe_result(result)
                rendered = json.dumps(result)
                assert "fixture-only-private" not in rendered and "private-error-must-not-escape" not in rendered
                assert "verified_legacy_archive" not in rendered and '"receipt"' not in rendered
                assert result["result"]["archive_committed"] is False
                assert result["result"]["target_schema_absent_independently_verified"] is True
                if failure:
                    assert result["status"] == "unknown"
                    assert result["result"]["archive_path_error"]["stage"] == "first_source_pass"
                    sql = result["result"]["sql_failure"]
                    assert sql["sqlstate"] == "23505" and sql["statement_sequence"] > 1
                    assert sql["driver_class"] in {"DatabaseError", "IntegrityError"}
                    assert len(sql["statement_sha256"]) == 64
                else:
                    assert result["status"] == "completed"
                    assert "sql_failure" not in result["result"]
                    assert result["needs_manual_review"] is True
                    assert result["result"]["diagnostic_status"] == "rollback_rehearsal_validated"
                    assert result["result"]["physical_records"] == 2 and result["result"]["collection_count"] == 2
                    assert result["result"]["final_commit_intercepted_and_rolled_back"] is True
                    second = await worker.run({"action": "execute", "request": body}, env,
                        config_loader=lambda _: config)
                    assert second["replayed_without_execution"] is True
                    assert second["needs_manual_review"] is True
                    assert len(observed_inserts) == 2
            assert journal.lookup(body["failed_archive_id"]) == prior
            cursor = connection.cursor(); cursor.execute("SELECT to_regnamespace('imladri_archive') IS NULL")
            assert cursor.fetchone()[0] is True
            connection.rollback(); cursor.close()
            actual = [BSON.encode(row) for row in await database[collection].find({}).sort("_id").to_list(None)]
            assert actual == original and sorted(await database.list_collection_names()) == sorted([collection, empty])
            assert await client.imladri_migration_control.database_migration_reports.count_documents({"fence_reference": reference}) == 1
    asyncio.run(run())
    assert observed_inserts
