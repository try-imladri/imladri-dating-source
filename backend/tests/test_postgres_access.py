"""Dedicated actual SQL logins cannot cross namespace or acquire owner privileges.

All integration DDL is confined to a uniquely named disposable logical database
on the explicitly supplied loopback PostgreSQL fixture, never a provider.
"""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
import secrets
from urllib.parse import quote, urlsplit
from uuid import uuid4

from bson import BSON
import pytest

from backend import postgres_access as access
from backend.postgres_migration import connection_options
from backend.postgres_store import encode_index_definition


DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
integration = pytest.mark.skipif(not DSN, reason="Requires explicit isolated loopback PostgreSQL.")


def test_role_sql_uses_new_restricted_logins_and_scram_not_cleartext():
    password = "only-synthetic-ascii-password-32-byte-token"
    first = access.role_creation_sql("imladri_app_live", password)
    second = access.role_creation_sql("imladri_app_live", password)
    assert first.startswith('CREATE ROLE "imladri_app_live" LOGIN NOINHERIT NOSUPERUSER')
    assert all(flag in first for flag in ("NOCREATEDB", "NOCREATEROLE", "NOREPLICATION", "NOBYPASSRLS"))
    assert "SCRAM-SHA-256$4096:" in first and password not in first
    assert first != second  # New random salt for every verifier.
    assert "ALTER ROLE" not in first


@pytest.mark.parametrize("role,password", [("owner", "x" * 32), ("imladri_app_bad;DROP ROLE x", "x" * 32),
    ("imladri_app_live", "short"), ("imladri_app_live", "é" * 40), ("imladri_app_live", None)])
def test_role_creation_rejects_injection_weak_or_non_generated_credentials(role, password):
    with pytest.raises(ValueError):
        access.role_creation_sql(role, password)


def test_access_plan_parameterizes_namespace_and_guards_every_namespace_table():
    namespace = "fixture:parameter-only"
    statements = access.access_statements({"imladri_app_live": namespace, "imladri_app_preview": "different"})
    for sql, params in statements:
        assert namespace not in sql
    assert ("imladri_app_live", namespace) in [params for sql, params in statements]
    for table in access.NAMESPACE_TABLES:
        matches = [sql for sql, _ in statements if f'imladri_app."{table}"' in sql]
        assert any("ENABLE ROW LEVEL SECURITY" in sql for sql in matches)
        assert any("FORCE ROW LEVEL SECURITY" in sql for sql in matches)
        assert any("AS RESTRICTIVE" in sql and "WITH CHECK" in sql for sql in matches)
        assert any("GRANT SELECT,INSERT,UPDATE,DELETE" in sql for sql in matches)
        assert not any("GRANT ALL" in sql or "GRANT TRUNCATE" in sql for sql in matches)
    # Distinct services may intentionally share one owner-approved namespace.
    shared = access.access_statements({"imladri_app_api": "same", "imladri_app_worker": "same"})
    assert ("imladri_app_api", "same") in [params for _, params in shared]
    assert ("imladri_app_worker", "same") in [params for _, params in shared]


def test_shared_credentials_are_rejected_before_bootstrap_sql():
    class NoConnection:
        def cursor(self):
            raise AssertionError("Unsafe credential plan reached SQL")
    with pytest.raises(ValueError, match="must not share credentials"):
        access.bootstrap_access(NoConnection(), {"imladri_app_live": "live", "imladri_app_preview": "preview"},
            {"imladri_app_live": "fixture-token-" * 4, "imladri_app_preview": "fixture-token-" * 4})


@pytest.fixture(scope="module")
def protected():
    if not DSN:
        pytest.skip("Requires explicit isolated loopback PostgreSQL.")
    assert urlsplit(DSN).hostname in ("127.0.0.1", "localhost", "::1")
    import pg8000.dbapi
    options = connection_options(DSN, allow_local_plaintext=True)
    admin = pg8000.dbapi.connect(**options)
    admin.autocommit = True
    database = "fixture_access_" + uuid4().hex
    suffix = uuid4().hex[:16]
    roles = {"live": "imladri_app_live_" + suffix, "preview": "imladri_app_preview_" + suffix,
             "worker": "imladri_app_worker_" + suffix,
             "unmapped": "imladri_app_unmapped_" + suffix}
    passwords = {key: secrets.token_urlsafe(32) for key in roles}
    namespaces = {"live": "fixture_live_" + suffix, "preview": "fixture_preview_" + suffix}
    namespaces["worker"] = namespaces["live"]
    cursor = admin.cursor()
    cursor.execute("CREATE DATABASE " + access.identifier(database))
    cursor.close()
    owner_options = {**options, "database": database}
    owner = pg8000.dbapi.connect(**owner_options)
    try:
        bindings = {roles[key]: namespaces[key] for key in ("live", "preview", "worker")}
        credentials = {roles[key]: passwords[key] for key in ("live", "preview", "worker")}
        # Unsafe PUBLIC TEMP default is never silently removed by helper.
        with pytest.raises(access.AccessConfigurationError, match="PUBLIC CREATE/TEMP"):
            access.bootstrap_access(owner, bindings, credentials)
        result = access.bootstrap_access(owner, bindings, credentials, allow_public_acl_hardening=True)
        assert result["roles_created"] == 3 and result["application_data_copied"] is False
        owner.autocommit = True
        cursor = owner.cursor()
        for key in ("live", "preview"):
            ns = namespaces[key]
            raw = BSON.encode({"_id": "member", "private": "synthetic-" + key})
            digest = hashlib.sha256(raw).hexdigest()
            cursor.execute("INSERT INTO imladri_app.collections VALUES(%s,'users')", (ns,))
            cursor.execute("INSERT INTO imladri_app.documents(namespace,collection,id_key,raw_bson,query_document,source_sha256) VALUES(%s,'users',%s,%s,%s::jsonb,%s)",
                (ns, "1" * 64, raw, json.dumps({"_id": "member"}), digest))
            cursor.execute("INSERT INTO imladri_app.indexes VALUES(%s,'users','fixture',%s::jsonb)",
                (ns, json.dumps(encode_index_definition({"key": {"_id": 1}, "name": "fixture"}))))
            cursor.execute("INSERT INTO imladri_app.unique_keys VALUES(%s,'users','fixture',%s,%s)", (ns, "2" * 64, "1" * 64))
            cursor.execute("INSERT INTO imladri_app.rpc_requests(namespace,request_id,body_sha256) VALUES(%s,%s,%s)", (ns, str(uuid4()), "3" * 64))
        cursor.execute(access.role_creation_sql(roles["unmapped"], passwords["unmapped"]))
        role = access.identifier(roles["unmapped"])
        cursor.execute(f"GRANT USAGE ON SCHEMA imladri_app TO {role}")
        cursor.execute(f"GRANT SELECT ON imladri_app.schema_version TO {role}")
        cursor.execute(f"GRANT EXECUTE ON FUNCTION imladri_app.namespace_allowed(text) TO {role}")
        for table in access.NAMESPACE_TABLES:
            cursor.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON imladri_app."{table}" TO {role}')
        cursor.close()
        @contextmanager
        def login(kind):
            # Docker's initialized127.0.0.1/32 rule is trust. A different LOCAL
            # source address reaches its existing SCRAM host rule on Linux;
            # Windows published-loopback connections already reach that rule.
            source = {} if os.name == "nt" else {"source_address": ("127.0.0.2", 0)}
            conn = pg8000.dbapi.connect(**{**owner_options, **source, "user": roles[kind], "password": passwords[kind]})
            conn.autocommit = True
            try:
                yield conn
            finally:
                conn.close()
        yield {"owner": owner, "options": owner_options, "roles": roles, "passwords": passwords,
               "namespaces": namespaces, "login": login, "bindings": bindings}
    finally:
        owner.close()
        cursor = admin.cursor()
        cursor.execute("DROP DATABASE " + access.identifier(database) + " WITH (FORCE)")
        for role in roles.values():
            cursor.execute("DROP ROLE IF EXISTS " + access.identifier(role))
        cursor.close()
        admin.close()


@integration
@pytest.mark.parametrize("kind", ["live", "preview"])
def test_runtime_login_verifies_using_selects_only(protected, kind):
    with protected["login"](kind) as conn:
        cursor = conn.cursor()
        class SelectOnly:
            def execute(self, sql, params=None):
                assert sql.lstrip().startswith("SELECT"), "Runtime access verification attempted DDL"
                try:
                    return cursor.execute(sql) if params is None else cursor.execute(sql, params)
                except Exception as exc:
                    code = exc.args[0].get("C") if exc.args and isinstance(exc.args[0], dict) else type(exc).__name__
                    raise AssertionError("Verification SQL failed: " + str(code) + ": " + sql) from None
            def fetchone(self): return cursor.fetchone()
            def fetchall(self): return cursor.fetchall()
        result = access.verify_runtime(SelectOnly(), protected["namespaces"][kind], expected_role=protected["roles"][kind])
        assert result["runtime_ddl_executed"] is False and result["provider_owner_role"] is False
        cursor.close()


@integration
@pytest.mark.parametrize("kind", ["live", "preview"])
def test_all_namespace_tables_hide_peer_rows_and_deny_peer_mutations(protected, kind):
    import pg8000.dbapi
    peer = "preview" if kind == "live" else "live"
    own, other = protected["namespaces"][kind], protected["namespaces"][peer]
    with protected["login"](kind) as conn:
        cursor = conn.cursor()
        for table in access.NAMESPACE_TABLES:
            target = 'imladri_app."' + table + '"'
            cursor.execute("SELECT DISTINCT namespace FROM " + target)
            assert list(cursor.fetchall()) == [[own]]
            cursor.execute("UPDATE " + target + " SET namespace=namespace WHERE namespace=%s", (other,))
            assert cursor.rowcount == 0
            cursor.execute("DELETE FROM " + target + " WHERE namespace=%s", (other,))
            assert cursor.rowcount == 0
            with pytest.raises(pg8000.dbapi.DatabaseError) as error:
                cursor.execute("UPDATE " + target + " SET namespace=%s WHERE namespace=%s", (other, own))
            assert error.value.args[0]["C"] == "42501"
            cursor.execute("SELECT namespace FROM " + target)
            rows = cursor.fetchall()
            assert rows and all(row[0] == own for row in rows)
        cursor.execute("SET app.namespace='attacker-controlled-namespace'")
        cursor.execute("SELECT DISTINCT namespace FROM imladri_app.documents")
        assert list(cursor.fetchall()) == [[own]]
        cursor.execute("SET row_security=off")
        with pytest.raises(pg8000.dbapi.DatabaseError):
            cursor.execute("SELECT * FROM imladri_app.documents")
        cursor.close()


@integration
def test_cross_namespace_inserts_are_checked_including_request_and_index_tables(protected):
    import pg8000.dbapi
    other = protected["namespaces"]["live"]
    inserts = [
        ("INSERT INTO imladri_app.collections VALUES(%s,'attack')", (other,)),
        ("INSERT INTO imladri_app.documents(namespace,collection,id_key,raw_bson,source_sha256) VALUES(%s,'users',%s,%s,%s)", (other, "4" * 64, BSON.encode({"_id": "attack"}), "5" * 64)),
        ("INSERT INTO imladri_app.indexes VALUES(%s,'users','attack','{}')", (other,)),
        ("INSERT INTO imladri_app.unique_keys VALUES(%s,'users','attack',%s,%s)", (other, "6" * 64, "1" * 64)),
        ("INSERT INTO imladri_app.rpc_requests(namespace,request_id,body_sha256) VALUES(%s,%s,%s)", (other, str(uuid4()), "7" * 64)),
    ]
    with protected["login"]("preview") as conn:
        cursor = conn.cursor()
        for sql, params in inserts:
            with pytest.raises(pg8000.dbapi.DatabaseError) as error:
                cursor.execute(sql, params)
            assert error.value.args[0]["C"] == "42501"
        cursor.close()


@integration
def test_apps_cannot_ddl_truncate_mapping_change_role_or_read_owner_shadow(protected):
    import pg8000.dbapi
    with protected["login"]("preview") as conn:
        cursor = conn.cursor()
        statements = ["CREATE SCHEMA attack", "CREATE TABLE public.attack(id integer)",
            "CREATE TABLE imladri_app.attack(id integer)", "CREATE TEMP TABLE attack(id integer)",
            "TRUNCATE imladri_app.documents", "SELECT * FROM imladri_app.role_namespaces",
            "UPDATE imladri_app.role_namespaces SET namespace='attack'", "ALTER TABLE imladri_app.documents DISABLE ROW LEVEL SECURITY",
            "SET ROLE " + access.identifier(protected["roles"]["live"]),
            "SET SESSION AUTHORIZATION " + access.identifier(protected["roles"]["live"]),
            "ALTER ROLE " + access.identifier(protected["roles"]["preview"]) + " CREATEROLE",
            "UPDATE imladri_app.schema_version SET version=2"]
        for statement in statements:
            with pytest.raises(pg8000.dbapi.DatabaseError) as error:
                cursor.execute(statement)
            assert error.value.args[0]["C"] == "42501"
        cursor.close()


@integration
def test_unmapped_role_sees_nothing_and_wrong_namespace_or_owner_fail_runtime(protected):
    import pg8000.dbapi
    with protected["login"]("unmapped") as conn:
        cursor = conn.cursor()
        with pytest.raises(access.AccessConfigurationError, match="fixed namespace"):
            access.verify_runtime(cursor, protected["namespaces"]["live"])
        cursor.execute("SELECT * FROM imladri_app.documents")
        assert not cursor.fetchall()
        with pytest.raises(pg8000.dbapi.DatabaseError):
            cursor.execute("INSERT INTO imladri_app.collections VALUES(%s,'attack')", (protected["namespaces"]["live"],))
        cursor.close()
    with protected["login"]("preview") as conn:
        cursor = conn.cursor()
        with pytest.raises(access.AccessConfigurationError, match="fixed namespace"):
            access.verify_runtime(cursor, protected["namespaces"]["live"])
        cursor.close()
    cursor = protected["owner"].cursor()
    try:
        with pytest.raises(access.AccessConfigurationError, match="dedicated application login"):
            access.verify_runtime(cursor, protected["namespaces"]["live"])
    finally:
        cursor.close()


@integration
def test_runtime_rejects_disabled_guard_and_bootstrap_never_alters_existing_role(protected):
    owner = protected["owner"]
    cursor = owner.cursor()
    try:
        cursor.execute("ALTER TABLE imladri_app.rpc_requests DISABLE ROW LEVEL SECURITY")
        with protected["login"]("preview") as conn:
            check = conn.cursor()
            with pytest.raises(access.AccessConfigurationError, match="forced row security"):
                access.verify_runtime(check, protected["namespaces"]["preview"])
            check.close()
    finally:
        cursor.execute("ALTER TABLE imladri_app.rpc_requests ENABLE ROW LEVEL SECURITY")
        cursor.close()
    # Use a dedicated owner connection: bootstrap owns its complete transaction.
    import pg8000.dbapi
    dedicated = pg8000.dbapi.connect(**protected["options"])
    try:
        with pytest.raises(access.AccessConfigurationError, match="existing PostgreSQL role"):
            access.bootstrap_access(dedicated, protected["bindings"],
                {protected["roles"][kind]: secrets.token_urlsafe(32) for kind in ("live", "preview", "worker")},
                allow_public_acl_hardening=True)
    finally:
        dedicated.close()


@integration
def test_real_repository_runs_as_preview_role_without_schema_ddl_and_blocks_live_namespace(protected):
    from backend.postgres_store import PostgresDatabase, RepositoryUnavailable
    options = protected["options"]
    role, password = protected["roles"]["preview"], protected["passwords"]["preview"]
    dsn = f"postgresql://{quote(role)}:{quote(password)}@{options['host']}:{options['port']}/{options['database']}?sslmode=disable"
    async def run():
        good = PostgresDatabase(dsn, protected["namespaces"]["preview"], allow_local_plaintext=True, initialize_schema=False)
        bad = PostgresDatabase(dsn, protected["namespaces"]["live"], allow_local_plaintext=True, initialize_schema=False)
        try:
            await good.users.insert_one({"_id": "runtime", "private": "preview-only"})
            assert (await good.users.find_one({"_id": "runtime"}))["private"] == "preview-only"
            with pytest.raises(RepositoryUnavailable):
                await bad.users.find_one({})
        finally:
            await good.aclose()
            await bad.aclose()
    asyncio.run(run())


@integration
def test_preview_credential_cannot_authenticate_as_live_login(protected):
    import pg8000.dbapi
    source = {} if os.name == "nt" else {"source_address": ("127.0.0.2", 0)}
    with pytest.raises(pg8000.dbapi.DatabaseError) as error:
        pg8000.dbapi.connect(**{**protected["options"], **source, "user": protected["roles"]["live"],
            "password": protected["passwords"]["preview"]})
    assert error.value.args[0]["C"] == "28P01"


@integration
def test_extra_permissive_policy_cannot_or_around_restrictive_namespace_guard(protected):
    owner = protected["owner"].cursor()
    try:
        owner.execute("CREATE POLICY fixture_permissive ON imladri_app.documents AS PERMISSIVE FOR ALL TO PUBLIC USING(true) WITH CHECK(true)")
        with protected["login"]("preview") as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT namespace FROM imladri_app.documents")
            assert list(cursor.fetchall()) == [[protected["namespaces"]["preview"]]]
            assert access.verify_runtime(cursor, protected["namespaces"]["preview"])["provider_owner_role"] is False
            cursor.close()
    finally:
        owner.execute("DROP POLICY IF EXISTS fixture_permissive ON imladri_app.documents")
        owner.close()


@integration
def test_distinct_api_and_worker_logins_share_only_the_owner_mapped_namespace(protected):
    from backend.postgres_store import PostgresDatabase, RepositoryUnavailable
    from pymongo import ReturnDocument
    options = protected["options"]
    def store(kind, namespace=None):
        role, password = protected["roles"][kind], protected["passwords"][kind]
        dsn = f"postgresql://{quote(role)}:{quote(password)}@{options['host']}:{options['port']}/{options['database']}?sslmode=disable"
        return PostgresDatabase(dsn, namespace or protected["namespaces"][kind], allow_local_plaintext=True, initialize_schema=False)
    async def run():
        api, worker = store("live"), store("worker")
        wrong = store("worker", protected["namespaces"]["preview"])
        try:
            await api.ops_state.insert_one({"_id": "shared-lease", "owner": None, "state": "pending"})
            claimed = await worker.ops_state.find_one_and_update({"_id": "shared-lease", "owner": None},
                {"$set": {"owner": "worker"}}, return_document=ReturnDocument.AFTER)
            assert claimed["owner"] == "worker"
            assert (await api.ops_state.find_one({"_id": "shared-lease"}))["owner"] == "worker"
            assert await api.ops_state.find_one_and_update({"_id": "shared-lease", "owner": None},
                {"$set": {"owner": "api"}}, return_document=ReturnDocument.AFTER) is None
            with pytest.raises(RepositoryUnavailable):
                await wrong.ops_state.find_one({})
        finally:
            await api.aclose(); await worker.aclose(); await wrong.aclose()
    asyncio.run(run())
    with protected["login"]("worker") as conn:
        cursor = conn.cursor()
        assert access.verify_runtime(cursor, protected["namespaces"]["live"])["provider_owner_role"] is False
        cursor.execute("SELECT count(*) FROM imladri_app.documents WHERE namespace=%s", (protected["namespaces"]["preview"],))
        assert cursor.fetchone()[0] == 0
        import pg8000.dbapi
        with pytest.raises(pg8000.dbapi.DatabaseError) as error:
            cursor.execute("INSERT INTO imladri_app.collections VALUES(%s,'cross-namespace-attack')", (protected["namespaces"]["preview"],))
        assert error.value.args[0]["C"] == "42501"
        cursor.close()
