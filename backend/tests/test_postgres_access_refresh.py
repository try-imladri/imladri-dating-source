"""Fresh role admission precedes app work and cannot be renewed by failures."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import threading
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from backend import postgres_access as access
from backend import postgres_store as store
from backend.tests.test_postgres_access import protected, integration


ROLE = "imladri_app_refresh_fixture"
NAMESPACE = "fixture_refresh"
SOURCE = "a" * 40
NOW = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)


class Catalog:
    def __init__(self):
        self.role = self.session = ROLE
        self.rls = True
        self.tls = True
        self.verifications = 0
        self.connections = []
        self.entered = self.release = None


class Cursor:
    def __init__(self, connection):
        self.connection, self.rows, self.closed = connection, [], False

    def execute(self, statement, params=None):
        conn, catalog = self.connection, self.connection.catalog
        conn.statements.append(statement)
        if statement.startswith("SET TRANSACTION"):
            conn.read_only = "READ ONLY" in statement
            return
        if statement.startswith("SET LOCAL"):
            return
        if not statement.lstrip().startswith("SELECT "):
            raise AssertionError("Unexpected non-SELECT statement")
        if "rolsuper,rolcreaterole,rolcreatedb" in statement:
            assert conn.read_only
            catalog.verifications += 1
            if catalog.entered is not None:
                catalog.entered.set()
                assert catalog.release.wait(5)
            self.rows = [(catalog.role, catalog.session, False, False, False, False, False, False, True)]
        elif "pg_auth_members" in statement:
            self.rows = [(False,)]
        elif statement.startswith("SELECT imladri_app.namespace_allowed"):
            self.rows = [(True,)]
        elif statement.startswith("SELECT version"):
            self.rows = [(1,)]
        elif "has_schema_privilege" in statement:
            self.rows = [(False,) * 4]
        elif "c.relrowsecurity" in statement:
            self.rows = [(name, catalog.rls, True, False, True, True, True, True, False, False, False)
                         for name in access.NAMESPACE_TABLES]
        elif "p.polname" in statement:
            self.rows = [(name, access.GUARD_POLICY, False, "*", "imladri_app.namespace_allowed(namespace)",
                          "imladri_app.namespace_allowed(namespace)", True) for name in access.NAMESPACE_TABLES]
        elif "p.prosecdef" in statement:
            self.rows = [(True, "s", access.NAMESPACE_FUNCTION_BODY, ["search_path=pg_catalog, pg_temp"], True)]
        elif statement.startswith("SELECT c.relname"):
            self.rows = []
        elif statement.startswith("SELECT has_table_privilege"):
            self.rows = [(False,)]
        elif "current_setting('transaction_read_only')" in statement:
            self.rows = [(NOW, catalog.role, catalog.session, "on" if conn.read_only else "off", 1234, catalog.tls)]
        elif statement == "SELECT clock_timestamp()":
            self.rows = [(NOW,)]
        else:
            raise AssertionError("Unexpected SELECT")

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True
        if self.connection.fail_close:
            raise RuntimeError("sensitive cursor contents")


class Connection:
    def __init__(self, catalog):
        self.catalog, self.statements, self.cursors = catalog, [], []
        self.read_only = False
        self.rollbacks = self.commits = 0
        self.fail_rollback = None
        self.fail_close = False
        catalog.connections.append(self)

    def cursor(self):
        cursor = Cursor(self)
        self.cursors.append(cursor)
        return cursor

    def rollback(self):
        self.rollbacks += 1
        if self.rollbacks == self.fail_rollback:
            raise RuntimeError("sensitive rollback contents")
        self.read_only = False

    def commit(self):
        self.commits += 1
        self.read_only = False


class Pool:
    def __init__(self, catalog, options):
        self.catalog, self.options = catalog, options

    @contextmanager
    def connection(self):
        conn = Connection(self.catalog)
        try:
            yield conn
        finally:
            conn.rollback()


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", SOURCE)
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(store, "time", SimpleNamespace(monotonic=lambda: clock.now))
    database = store.PostgresDatabase(
        "postgresql://" + ROLE + ":never-log-fixture-password@fixture.invalid/fixture?sslmode=verify-full",
        NAMESPACE, initialize_schema=False, public_catalog_cache_bytes=4096)
    catalog = Catalog()
    database.pool = Pool(catalog, database.pool.options)
    return database, catalog, clock


def logs(capsys):
    lines = capsys.readouterr().out.splitlines()
    assert all(line.startswith(store.RUNTIME_ACCESS_LOG_PREFIX) for line in lines)
    return [json.loads(line[len(store.RUNTIME_ACCESS_LOG_PREFIX):]) for line in lines]


def test_access_snapshot_is_select_only_readonly_rollback_and_content_free(runtime, capsys):
    database, catalog, _ = runtime
    connection = Connection(catalog)
    database._initialize(connection)
    assert connection.commits == 0 and connection.rollbacks == 2
    assert all(cursor.closed for cursor in connection.cursors)
    assert connection.statements[:4] == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        "SET LOCAL statement_timeout = '5s'", "SET LOCAL lock_timeout = '2s'",
        "SET LOCAL idle_in_transaction_session_timeout = '10s'"]
    assert all(statement.lstrip().startswith("SELECT ") for statement in connection.statements[4:])
    proof, = logs(capsys)
    assert proof["role"] == ROLE and proof["namespace"] == NAMESPACE
    assert proof["source_commit"] == SOURCE and proof["pid"] == os.getpid() and proof["backend_pid"] == 1234
    assert proof["checked_at"] == NOW.isoformat() and proof["policy_version"] == 1
    assert proof["read_only"] and proof["tls_active"] and proof["tls_verified_hostname"]
    assert proof["catalog_select_statements"] == len(connection.statements) - 4
    encoded = json.dumps(proof)
    assert len(encoded) < 2048
    assert all(value not in encoded for value in ("never-log-fixture-password", "fixture.invalid", "SELECT", "postgresql://"))


def test_expiry_revalidates_before_business_despite_initialized_store(runtime, capsys):
    database, catalog, clock = runtime
    callbacks = []
    for clock.now in (0, 59.999, 60):
        database._transaction(lambda cursor, now: callbacks.append(now))
    assert catalog.verifications == 2 and len(callbacks) == 3
    assert len(logs(capsys)) == 2


@pytest.mark.parametrize("duration", [59, 60])
def test_slow_verification_does_not_extend_admission_window(runtime, monkeypatch, capsys, duration):
    database, catalog, clock = runtime
    verify = access.verify_runtime_access
    def slow(*args, **kwargs):
        proof = verify(*args, **kwargs)
        clock.now += duration
        return proof
    monkeypatch.setattr(access, "verify_runtime_access", slow)
    callbacks = []
    if duration == 60:
        with pytest.raises(store.RepositoryUnavailable, match="expired before admission"):
            database._transaction(lambda cursor, now: callbacks.append(now))
        assert not callbacks and not logs(capsys)
    else:
        database._transaction(lambda cursor, now: callbacks.append(now))
        clock.now = 60
        database._transaction(lambda cursor, now: callbacks.append(now))
        assert len(callbacks) == 2 and catalog.verifications == 2 and len(logs(capsys)) == 2


def test_failed_refresh_blocks_business_keeps_expired_proof_and_retries(runtime, capsys):
    database, catalog, clock = runtime
    database._transaction(lambda cursor, now: None)
    previous = database._runtime_access
    clock.now, catalog.rls = 60, False
    callbacks = []
    with pytest.raises(store.RepositoryUnavailable):
        database._transaction(lambda cursor, now: callbacks.append(now))
    assert not callbacks and database._runtime_access is previous
    catalog.rls = True
    database._transaction(lambda cursor, now: callbacks.append(now))
    assert len(callbacks) == 1 and catalog.verifications == 3
    assert len(logs(capsys)) == 2


def test_simultaneous_expiry_runs_one_refresh_before_all_callbacks(runtime, capsys):
    database, catalog, clock = runtime
    database._transaction(lambda cursor, now: None)
    clock.now = 60
    catalog.entered, catalog.release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(database._transaction, lambda cursor, now: catalog.verifications) for _ in range(4)]
        assert catalog.entered.wait(5)
        catalog.release.set()
        assert [future.result(timeout=5) for future in futures] == [2] * 4
    assert catalog.verifications == 2 and len(logs(capsys)) == 2


@pytest.mark.parametrize("failure", ["role", "session", "tls", "rollback", "cursor_close", "logging"])
def test_bad_identity_transport_or_cleanup_never_publishes_admission(runtime, monkeypatch, capsys, failure):
    database, catalog, _ = runtime
    connection = Connection(catalog)
    if failure in ("role", "session"):
        setattr(catalog, failure, "imladri_app_wrong")
    elif failure == "tls":
        catalog.tls = False
    elif failure == "rollback":
        connection.fail_rollback = 2
    elif failure == "cursor_close":
        connection.fail_close = True
    else:
        def fail_print(*args, **kwargs):
            raise OSError("sensitive stdout contents")
        monkeypatch.setattr("builtins.print", fail_print)
    with pytest.raises((access.AccessConfigurationError, OSError)):
        database._initialize(connection)
    assert database._runtime_access is None and not database._initialized
    assert connection.commits == 0 and connection.rollbacks == 2
    assert not logs(capsys)


def test_source_and_process_changes_force_new_verification(runtime, monkeypatch, capsys):
    database, catalog, _ = runtime
    database._transaction(lambda cursor, now: None)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "b" * 64)
    database._transaction(lambda cursor, now: None)
    monkeypatch.setattr(store, "os", SimpleNamespace(environ=os.environ, getpid=lambda: 8888))
    database._transaction(lambda cursor, now: None)
    proofs = logs(capsys)
    assert catalog.verifications == 3
    assert [proof["source_commit"] for proof in proofs] == [SOURCE, "b" * 64, "b" * 64]
    assert proofs[-1]["pid"] == 8888
    monkeypatch.setenv("RENDER_GIT_COMMIT", "secret-with-newline\n")
    with pytest.raises(store.RepositoryUnavailable):
        database._transaction(lambda cursor, now: pytest.fail("Invalid source admitted business work"))
    assert not logs(capsys)


def test_guard_rejects_non_select_even_if_verifier_regresses(runtime, monkeypatch):
    database, catalog, _ = runtime
    def unsafe_verifier(cursor, *args, **kwargs):
        cursor.execute("DELETE FROM fixture")
    monkeypatch.setattr(access, "verify_runtime", unsafe_verifier)
    connection = Connection(catalog)
    with pytest.raises(access.AccessConfigurationError, match="bounded SELECT"):
        database._initialize(connection)
    assert not any("DELETE" in statement for statement in connection.statements)
    assert connection.commits == 0 and connection.rollbacks == 2


def test_plaintext_requires_existing_explicit_fixture_permission(runtime):
    _, catalog, _ = runtime
    catalog.tls = False
    for allowed in (False, True):
        connection = Connection(catalog)
        if allowed:
            proof = access.verify_runtime_access(connection, NAMESPACE, expected_role=ROLE,
                ssl_context=False, allow_local_plaintext=True)
            assert not proof["tls_active"] and not proof["tls_verified_hostname"]
        else:
            with pytest.raises(access.AccessConfigurationError):
                access.verify_runtime_access(connection, NAMESPACE, expected_role=ROLE, ssl_context=False)


def test_bound_operations_keep_outer_transaction_then_refresh_at_next_boundary(runtime, capsys):
    database, catalog, clock = runtime
    def outer(cursor, now):
        token = store._BOUND_TRANSACTION.set((database, cursor, now))
        try:
            clock.now = 60
            return asyncio.run(database._run(lambda nested, stamp: nested is cursor and stamp == now))
        finally:
            store._BOUND_TRANSACTION.reset(token)
    assert database._transaction(outer) is True
    assert catalog.verifications == 1
    database._transaction(lambda cursor, now: None)
    assert catalog.verifications == 2 and len(logs(capsys)) == 2


def test_cancellation_waits_for_refresh_and_transaction_cleanup(runtime, capsys):
    database, catalog, _ = runtime
    catalog.entered, catalog.release = threading.Event(), threading.Event()
    async def run():
        operation = asyncio.create_task(database.command("ping"))
        assert await asyncio.to_thread(catalog.entered.wait, 5)
        operation.cancel()
        catalog.release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
    asyncio.run(run())
    connection, = catalog.connections
    assert all(cursor.closed for cursor in connection.cursors)
    assert connection.rollbacks == 3 and connection.commits == 1
    assert catalog.verifications == 1 and len(logs(capsys)) == 1


@integration
def test_expired_real_role_rechecks_forced_rls_before_business(protected, monkeypatch, capsys):
    options = protected["options"]
    dsn = (f"postgresql://{quote(protected['roles']['preview'])}:{quote(protected['passwords']['preview'])}"
           f"@{options['host']}:{options['port']}/{options['database']}?sslmode=disable")
    database = store.PostgresDatabase(dsn, protected["namespaces"]["preview"],
        allow_local_plaintext=True, initialize_schema=False)
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(store, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setenv("RENDER_GIT_COMMIT", SOURCE)
    owner = protected["owner"].cursor()
    try:
        database._transaction(lambda cursor, now: None)
        clock.now = 60
        owner.execute("ALTER TABLE imladri_app.rpc_requests DISABLE ROW LEVEL SECURITY")
        with pytest.raises(store.RepositoryUnavailable):
            database._transaction(lambda cursor, now: pytest.fail("Unsafe RLS admitted business work"))
        owner.execute("ALTER TABLE imladri_app.rpc_requests ENABLE ROW LEVEL SECURITY")
        database._transaction(lambda cursor, now: None)
        assert len(logs(capsys)) == 2
    finally:
        owner.execute("ALTER TABLE imladri_app.rpc_requests ENABLE ROW LEVEL SECURITY")
        owner.close()
        database.pool.close()
