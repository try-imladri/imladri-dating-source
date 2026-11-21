"""Operator bootstrap and SELECT-only runtime checks for PostgreSQL isolation.

This module does not connect to a provider, read credentials from files/env, or
run automatically. Bootstrap requires a dedicated owner connection. Runtime
connections are separate NOINHERIT logins with a fixed session_user namespace.
No application role receives the provider owner's credentials or SQL DDL rights.
"""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
from pathlib import Path
import re
import secrets
import ssl

from .postgres_codec import validate_name


NAMESPACE_TABLES = ("collections", "documents", "indexes", "unique_keys", "rpc_requests")
GUARD_POLICY = "app_namespace_guard"
ACCESS_POLICY = "app_namespace_access"
ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
NAMESPACE_FUNCTION_BODY = """SELECT SESSION_USER = CURRENT_USER OR EXISTS (
    SELECT 1 FROM imladri_app.role_namespaces AS allowed
    WHERE allowed.role_name = SESSION_USER::text AND allowed.namespace = candidate
)"""
FUNCTION_SQL = """CREATE OR REPLACE FUNCTION imladri_app.namespace_allowed(candidate text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $imladri_access$""" + NAMESPACE_FUNCTION_BODY + "$imladri_access$"


class AccessConfigurationError(RuntimeError):
    """Sanitized access-policy failure; never include a password or source row."""


def identifier(value):
    if not isinstance(value, str) or not ROLE_PATTERN.fullmatch(value):
        raise ValueError("Expected a reviewed PostgreSQL identifier.")
    return '"' + value + '"'


def _bindings(bindings):
    if not isinstance(bindings, dict) or not bindings or len(bindings) > 8:
        raise ValueError("Expected a bounded application-role namespace map.")
    for role, namespace in bindings.items():
        identifier(role)
        if not role.startswith("imladri_app_"):
            raise ValueError("Application roles need the dedicated role prefix.")
        validate_name(namespace)
    return dict(bindings)


def scram_verifier(password):
    """Make a salted PostgreSQL SCRAM verifier without putting cleartext in SQL.

    Operator-generated passwords must be long random ASCII tokens. The returned
    verifier remains sensitive and must never be logged or saved as evidence.
    """
    if not isinstance(password, str) or not password.isascii() or len(password.encode()) < 32:
        raise ValueError("A new application password must be a long random ASCII token.")
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, 4096)
    client = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored = hashlib.sha256(client).digest()
    server = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    encode = lambda value: base64.b64encode(value).decode("ascii")
    return f"SCRAM-SHA-256$4096:{encode(salt)}${encode(stored)}:{encode(server)}"


def role_creation_sql(role, password):
    """New roles only. Existing roles must never be altered as a bootstrap retry."""
    name = identifier(role)
    if not role.startswith("imladri_app_"):
        raise ValueError("Application roles need the dedicated role prefix.")
    verifier = scram_verifier(password)
    return (f"CREATE ROLE {name} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
            f"NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 8 PASSWORD '{verifier}'")


def access_statements(bindings):
    """Return reviewed static SQL/parameters, excluding role credential creation."""
    bindings = _bindings(bindings)
    roles = ", ".join(identifier(role) for role in bindings)
    statements = [
        ("CREATE TABLE IF NOT EXISTS imladri_app.role_namespaces (role_name text PRIMARY KEY, namespace text NOT NULL)", ()),
        ("REVOKE ALL ON imladri_app.role_namespaces FROM PUBLIC", ()),
        ("REVOKE CREATE ON SCHEMA imladri_app FROM PUBLIC", ()),
        (FUNCTION_SQL, ()),
        ("REVOKE ALL ON FUNCTION imladri_app.namespace_allowed(text) FROM PUBLIC", ()),
        (f"GRANT USAGE ON SCHEMA imladri_app TO {roles}", ()),
        (f"GRANT EXECUTE ON FUNCTION imladri_app.namespace_allowed(text) TO {roles}", ()),
        (f"GRANT SELECT ON imladri_app.schema_version TO {roles}", ()),
    ]
    for role, namespace in bindings.items():
        statements.append(("INSERT INTO imladri_app.role_namespaces(role_name,namespace) VALUES(%s,%s)", (role, namespace)))
    for table in NAMESPACE_TABLES:
        target = "imladri_app." + identifier(table)
        statements.extend([
            (f"REVOKE ALL ON {target} FROM PUBLIC", ()),
            (f"ALTER TABLE {target} ENABLE ROW LEVEL SECURITY", ()),
            (f"ALTER TABLE {target} FORCE ROW LEVEL SECURITY", ()),
            (f"DROP POLICY IF EXISTS {GUARD_POLICY} ON {target}", ()),
            (f"CREATE POLICY {GUARD_POLICY} ON {target} AS RESTRICTIVE FOR ALL TO PUBLIC "
             "USING (imladri_app.namespace_allowed(namespace)) WITH CHECK (imladri_app.namespace_allowed(namespace))", ()),
            (f"DROP POLICY IF EXISTS {ACCESS_POLICY} ON {target}", ()),
            (f"CREATE POLICY {ACCESS_POLICY} ON {target} AS PERMISSIVE FOR ALL TO PUBLIC USING (true) WITH CHECK (true)", ()),
            (f"GRANT SELECT,INSERT,UPDATE,DELETE ON {target} TO {roles}", ()),
        ])
    return statements


def bootstrap_access(connection, bindings, passwords, *, allow_public_acl_hardening=False):
    """Explicit owner-only bootstrap, atomic DDL/roles/mappings, with safe rollback.

    Does not perform any implicit application data copy. A caller must review the
    database-wide PUBLIC CREATE/TEMP revocations before enabling the explicit
    flag. Otherwise an unsafe PUBLIC baseline fails closed. Uses a dedicated
    connection because it commits/rolls back the entire bootstrap transaction.
    """
    bindings = _bindings(bindings)
    if not isinstance(passwords, dict) or set(passwords) != set(bindings):
        raise ValueError("Each new application role requires its own credential.")
    if any(not isinstance(value, str) for value in passwords.values()) or len(set(passwords.values())) != len(passwords):
        raise ValueError("Isolated application roles must not share credentials.")
    # Validate credentials before any SQL or roles are created.
    creations = [role_creation_sql(role, passwords[role]) for role in bindings]
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT CURRENT_USER,SESSION_USER,current_database(),rolsuper,rolcreaterole FROM pg_catalog.pg_roles WHERE rolname=CURRENT_USER")
        owner, session, database, superuser, create_role = cursor.fetchone()
        identifier(owner); identifier(database)
        if owner != session or owner in bindings or not (superuser or create_role):
            raise AccessConfigurationError("Bootstrap requires a dedicated database owner with role-creation authority.")
        cursor.execute("SELECT rolname FROM pg_catalog.pg_roles WHERE rolname=ANY(%s)", (list(bindings),))
        if cursor.fetchall():
            raise AccessConfigurationError("Bootstrap will not modify an existing PostgreSQL role.")
        cursor.execute("""SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_database AS db,
            LATERAL pg_catalog.aclexplode(COALESCE(db.datacl,pg_catalog.acldefault('d',db.datdba))) AS acl
            WHERE db.datname=current_database() AND acl.grantee=0 AND acl.privilege_type IN ('CREATE','TEMPORARY'))
            OR EXISTS(SELECT 1 FROM pg_catalog.pg_namespace AS ns,
            LATERAL pg_catalog.aclexplode(COALESCE(ns.nspacl,pg_catalog.acldefault('n',ns.nspowner))) AS acl
            WHERE ns.nspname='public' AND acl.grantee=0 AND acl.privilege_type='CREATE')""")
        if cursor.fetchone()[0] and not allow_public_acl_hardening:
            raise AccessConfigurationError("Database-wide PUBLIC CREATE/TEMP privileges require explicit owner review.")
        if allow_public_acl_hardening:
            cursor.execute("REVOKE CREATE,TEMPORARY ON DATABASE " + identifier(database) + " FROM PUBLIC")
            cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        schema = Path(__file__).with_name("postgres_store_schema.sql").read_text(encoding="utf8")
        for statement in "\n".join(line.split("--", 1)[0] for line in schema.splitlines()).split(";"):
            if statement.strip():
                cursor.execute(statement)
        for statement in creations:
            cursor.execute(statement)
        for sql, params in access_statements(bindings):
            cursor.execute(sql, params)
        connection.commit()
        return {"schema": "imladri_app", "roles_created": len(bindings), "rls_tables": len(NAMESPACE_TABLES),
                "application_data_copied": False}
    except BaseException as exc:
        connection.rollback()
        if isinstance(exc, (AccessConfigurationError, ValueError, KeyboardInterrupt, SystemExit)):
            raise
        raise AccessConfigurationError("PostgreSQL access bootstrap did not complete.") from None
    finally:
        cursor.close()


def verify_runtime(cursor, namespace, *, expected_role=None):
    """SELECT-only verification; application startup must never initialize DDL."""
    validate_name(namespace)
    if expected_role is not None:
        identifier(expected_role)
    try:
        cursor.execute("""SELECT CURRENT_USER,SESSION_USER,rolsuper,rolcreaterole,rolcreatedb,
            rolreplication,rolbypassrls,rolinherit,rolcanlogin FROM pg_catalog.pg_roles WHERE rolname=CURRENT_USER""")
        row = cursor.fetchone()
        if not row or row[0] != row[1] or not row[0].startswith("imladri_app_") or (expected_role and row[0] != expected_role):
            raise AccessConfigurationError("Runtime must connect as its dedicated application login.")
        role = row[0]
        if any(row[2:8]) or row[8] is not True:
            raise AccessConfigurationError("Application login has unsafe role attributes.")
        cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE member=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=CURRENT_USER))")
        if cursor.fetchone()[0]:
            raise AccessConfigurationError("Application login must not inherit or assume another role.")
        cursor.execute("SELECT imladri_app.namespace_allowed(%s)", (namespace,))
        if cursor.fetchone()[0] is not True:
            raise AccessConfigurationError("Application login does not match its fixed namespace.")
        cursor.execute("SELECT version FROM imladri_app.schema_version")
        if [tuple(item) for item in cursor.fetchall()] != [(1,)]:
            raise AccessConfigurationError("Application schema version is not supported.")
        cursor.execute("""SELECT has_schema_privilege(CURRENT_USER,'imladri_app','CREATE'),
            has_schema_privilege(CURRENT_USER,'public','CREATE'),
            has_database_privilege(CURRENT_USER,current_database(),'CREATE'),
            has_database_privilege(CURRENT_USER,current_database(),'TEMP')""")
        if any(cursor.fetchone()):
            raise AccessConfigurationError("Application login must not create database objects.")
        cursor.execute("""SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=CURRENT_USER),
            has_table_privilege(CURRENT_USER,c.oid,'SELECT'),has_table_privilege(CURRENT_USER,c.oid,'INSERT'),
            has_table_privilege(CURRENT_USER,c.oid,'UPDATE'),has_table_privilege(CURRENT_USER,c.oid,'DELETE'),
            has_table_privilege(CURRENT_USER,c.oid,'TRUNCATE'),has_table_privilege(CURRENT_USER,c.oid,'REFERENCES'),
            has_table_privilege(CURRENT_USER,c.oid,'TRIGGER')
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='imladri_app' AND c.relname=ANY(%s)""", (list(NAMESPACE_TABLES),))
        tables = cursor.fetchall()
        if {row[0] for row in tables} != set(NAMESPACE_TABLES) or any(
                not all(row[1:3]) or row[3] or not all(row[4:8]) or any(row[8:]) for row in tables):
            raise AccessConfigurationError("Application namespace tables require forced row security and DML-only grants.")
        cursor.execute("""SELECT c.relname,p.polname,p.polpermissive,p.polcmd,
            pg_catalog.pg_get_expr(p.polqual,p.polrelid),pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid),p.polroles=ARRAY[0::oid]
            FROM pg_catalog.pg_policy p JOIN pg_catalog.pg_class c ON c.oid=p.polrelid
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='imladri_app' AND c.relname=ANY(%s) AND p.polname=%s""", (list(NAMESPACE_TABLES), GUARD_POLICY))
        guards = cursor.fetchall()
        if {row[0] for row in guards} != set(NAMESPACE_TABLES) or any(row[2] is not False or row[3] != "*"
                or row[4] != "imladri_app.namespace_allowed(namespace)" or row[5] != row[4] or row[6] is not True for row in guards):
            raise AccessConfigurationError("Application namespace policies do not match the reviewed guard.")
        cursor.execute("""SELECT p.prosecdef,p.provolatile,p.prosrc,p.proconfig,
            p.proowner=(SELECT relowner FROM pg_catalog.pg_class WHERE oid='imladri_app.role_namespaces'::regclass)
            FROM pg_catalog.pg_proc p WHERE p.oid='imladri_app.namespace_allowed(text)'::regprocedure""")
        function = cursor.fetchone()
        if not function or function[0] is not True or function[1] != "s" or function[2] != NAMESPACE_FUNCTION_BODY \
                or function[3] != ["search_path=pg_catalog, pg_temp"] or function[4] is not True:
            raise AccessConfigurationError("Namespace guard function differs from its reviewed definition.")
        cursor.execute("""SELECT c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
            AND n.nspname NOT LIKE 'pg_temp%%' AND c.relkind IN ('r','p','v','m')
            AND NOT (n.nspname='imladri_app' AND c.relname=ANY(%s))
            AND NOT (n.nspname='imladri_app' AND c.relname='schema_version')
            AND has_table_privilege(CURRENT_USER,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')""", (list(NAMESPACE_TABLES),))
        if cursor.fetchall():
            raise AccessConfigurationError("Application login can access protected or unrelated tables.")
        cursor.execute("SELECT has_table_privilege(CURRENT_USER,'imladri_app.schema_version','INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')")
        if cursor.fetchone()[0]:
            raise AccessConfigurationError("Application login cannot modify schema control records.")
        return {"schema_version": 1, "role": role, "namespace": namespace, "rls_tables": len(NAMESPACE_TABLES),
                "runtime_ddl_executed": False, "provider_owner_role": False}
    except AccessConfigurationError:
        raise
    except Exception:
        raise AccessConfigurationError("PostgreSQL runtime access verification did not complete.") from None


def verify_runtime_access(connection, namespace, *, expected_role, ssl_context,
                          allow_local_plaintext=False):
    """Verify on a separate bounded read-only snapshot, never commit app work.

    Call only on an exclusively checked-out connection before its application
    transaction. The caller's DSN parser must have restricted plaintext to an
    explicitly enabled loopback fixture. A proof exists only after rollback and
    cursor cleanup have both succeeded.
    """
    class SelectOnly:
        def __init__(self, cursor):
            self.cursor, self.count = cursor, 0

        def execute(self, statement, params=None):
            if (not statement.lstrip().upper().startswith("SELECT ") or ";" in statement
                    or self.count >= 32):
                raise AccessConfigurationError("Runtime access verification requires bounded SELECT statements.")
            self.count += 1
            return self.cursor.execute(statement) if params is None else self.cursor.execute(statement, params)

        def fetchone(self):
            return self.cursor.fetchone()

        def fetchall(self):
            return self.cursor.fetchall()

    cursor = None
    try:
        try:
            # Pool cleanup normally leaves no transaction. Explicitly fence the
            # check so its rollback can never end the later business transaction.
            connection.rollback()
            cursor = connection.cursor()
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor.execute("SET LOCAL statement_timeout = '5s'")
            cursor.execute("SET LOCAL lock_timeout = '2s'")
            cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '10s'")
            checked = SelectOnly(cursor)
            proof = verify_runtime(checked, namespace, expected_role=expected_role)
            checked.execute("""SELECT clock_timestamp(),CURRENT_USER,SESSION_USER,
                current_setting('transaction_read_only'),pg_backend_pid(),
                COALESCE((SELECT ssl FROM pg_catalog.pg_stat_ssl WHERE pid=pg_backend_pid()),false)""")
            checked_at, role, session, read_only, backend_pid, tls_active = checked.fetchone()
            tls_verified = (isinstance(ssl_context, ssl.SSLContext) and ssl_context.check_hostname
                            and ssl_context.verify_mode == ssl.CERT_REQUIRED)
            local_plaintext = allow_local_plaintext is True and ssl_context is False
            if (role != expected_role or session != expected_role or role != proof["role"]
                    or read_only != "on" or type(backend_pid) is not int or backend_pid <= 0
                    or not isinstance(checked_at, datetime) or checked_at.utcoffset() is None
                    or (not local_plaintext and (tls_active is not True or not tls_verified))):
                raise AccessConfigurationError("Runtime access snapshot identity or transport is invalid.")
            proof = {**proof, "checked_at": checked_at.astimezone(timezone.utc).isoformat(),
                     "policy_version": 1, "catalog_select_statements": checked.count,
                     "read_only": True, "backend_pid": backend_pid,
                     "tls_active": tls_active is True, "tls_verified_hostname": bool(tls_verified)}
        finally:
            try:
                connection.rollback()
            finally:
                if cursor is not None:
                    cursor.close()
        return proof
    except AccessConfigurationError:
        raise
    except Exception:
        raise AccessConfigurationError("PostgreSQL runtime access snapshot did not complete.") from None
