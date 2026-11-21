"""Fixed paused-standby migration worker. Secrets come only from trusted env.

The CLI hardens its Linux process before loading drivers or making any network
call. Business steps have a durable owner-only intent journal; uncertain or
interrupted steps never automatically repeat. This is not an arbitrary job API.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import sys
from urllib.parse import unquote, urlsplit
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ID = "srv-dbntn8tg1s2s73dllgs0"
SERVICE_NAME = "imladri-personal-live-api"
MAX_BODY = 16 * 1024
MAX_OUTPUT = 256 * 1024
MAX_SECONDS = 600
COMMANDS = {"inspect", "inspect_copy_state", "inspect_archive_state", "inspect_completed_operation", "rehearse_archive", "recover_archive", "discover_sources", "prepare_access", "copy_personal", "copy_market", "archive_legacy", "reconcile_indexes", "verify"}
NAMESPACES = {"personal_live": "imladri_personal_live", "personal_preview": "imladri_personal_pilot"}
MARKET_SOURCES = {"market_live": "imladri_production"}
RECOVERY_SOURCES = {"recovery_staging": "imladri_staging", "recovery_production": "imladri_production"}
ARCHIVE_SOURCES = {"legacy_staging": "imladri_staging", "legacy_production": "imladri_production", **RECOVERY_SOURCES}
SOURCE_DATABASES = {**NAMESPACES, **MARKET_SOURCES, **ARCHIVE_SOURCES}
ROLE_NAMESPACES = {**NAMESPACES, "market_api": "imladri_production", "market_worker": "imladri_production"}
CHILD_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TZ", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED",
    "RENDER_SERVICE_ID", "RENDER_SERVICE_NAME", "RENDER_GIT_COMMIT", "IMLADRI_DATABASE_MAINTENANCE",
    "IMLADRI_MIGRATION_SOURCES_JSON", "IMLADRI_MIGRATION_ROLE_DSNS_JSON", "IMLADRI_POSTGRES_SHADOW_DSN",
    "IMLADRI_MIGRATION_FENCE_SHA256", "IMLADRI_MIGRATION_FENCE_JSON", "IMLADRI_MIGRATION_CONFIG_SHA256", "PYTHON_DOTENV_DISABLED")


class OperatorBlocked(RuntimeError):
    """A fixed, content-free reason for refusing a maintenance operation."""


def require(value, reason):
    if not value:
        raise OperatorBlocked(reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def utc_now():
    return datetime.now(timezone.utc)


def deployment_gate(environ, expected_release_sha=None):
    require(environ.get("IMLADRI_DATABASE_MAINTENANCE") == "paused", "standby_not_paused")
    require(environ.get("RENDER_SERVICE_ID") == SERVICE_ID and environ.get("RENDER_SERVICE_NAME") == SERVICE_NAME,
            "not_the_approved_standby")
    current = environ.get("RENDER_GIT_COMMIT", "")
    require(bool(re.fullmatch(r"[a-f0-9]{40}", current)), "release_identity_missing")
    if expected_release_sha is not None:
        require(hmac.compare_digest(current, expected_release_sha), "release_identity_changed")
    return current


def child_environment(environ):
    deployment_gate(environ)
    result = {key: environ[key] for key in CHILD_ENV_KEYS if key in environ}
    result.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1", PYTHON_DOTENV_DISABLED="1")
    return result


def safe_result(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return False
            lower = key.lower()
            forbidden = (lower in {"after", "before", "answers", "demographics", "raw", "source_document"}
                or any(word in lower for word in ("password", "secret", "credential", "api_key", "token", "authorization", "mongo_url", "dsn", "raw_bson", "raw_records")))
            if forbidden and item is not False and item is not None:
                return False
            if not safe_result(item):
                return False
        return True
    if isinstance(value, list):
        return all(safe_result(item) for item in value)
    if isinstance(value, str):
        return len(value.encode()) <= 8192 and not value.startswith(("mongodb://", "mongodb+srv://", "postgres://", "postgresql://", "sk-", "sk_live_", "Bearer "))
    return value is None or type(value) in (int, float, bool)


def _uuid(value):
    require(isinstance(value, str), "invalid_operation_identity")
    try:
        return str(UUID(value))
    except (ValueError, AttributeError):
        raise OperatorBlocked("invalid_operation_identity") from None


def validate_request(value, environ, *, status=False):
    require(isinstance(value, dict), "invalid_request")
    allowed = {"operation_id", "expected_release_sha"} if status else {
        "operation_id", "expected_release_sha", "command", "source", "approved_workflow",
        "allow_public_acl_hardening", "fence", "prior_copy_id", "failed_copy_id", "failed_archive_id", "failed_rehearsal_id",
        "completed_operation_id", "completion_proof"}
    require(not set(value) - allowed, "unexpected_request_field")
    request = deepcopy(value)
    request["operation_id"] = _uuid(value.get("operation_id"))
    sha = value.get("expected_release_sha")
    require(isinstance(sha, str) and bool(re.fullmatch(r"[a-f0-9]{40}", sha)), "invalid_expected_release")
    deployment_gate(environ, sha)
    if status:
        return request
    require(isinstance(value.get("command"), str) and value["command"] in COMMANDS, "unsupported_command")
    for flag in ("approved_workflow", "allow_public_acl_hardening"):
        require(flag not in value or type(value[flag]) is bool, "invalid_approval_flag")
    require(value.get("source") is None or (isinstance(value["source"], str) and value["source"] in SOURCE_DATABASES),
            "unsupported_fixed_source")
    if value["command"] == "inspect_completed_operation":
        from .postgres_completed_operation import validate_completed_request
        validate_completed_request(request)
    else:
        require("completed_operation_id" not in value and "completion_proof" not in value,
                "completed_history_fields_outside_readonly_command")
    if value["command"] in {"copy_personal", "copy_market", "archive_legacy", "rehearse_archive", "recover_archive", "reconcile_indexes", "verify"}:
        allowed_sources = (NAMESPACES if value["command"] == "copy_personal" else MARKET_SOURCES
                           if value["command"] == "copy_market" else ARCHIVE_SOURCES
                           if value["command"] == "archive_legacy" else RECOVERY_SOURCES
                           if value["command"] in {"rehearse_archive", "recover_archive"} else {**NAMESPACES, **MARKET_SOURCES})
        require(value.get("source") in allowed_sources, "fixed_source_required")
        validate_fence(value.get("fence"), environ, source=value["source"], command=value["command"])
    else:
        require("fence" not in value and "prior_copy_id" not in value, "unexpected_fence_or_receipt")
    if "prior_copy_id" in value:
        request["prior_copy_id"] = _uuid(value["prior_copy_id"])
        require(value["command"] not in {"archive_legacy", "rehearse_archive", "recover_archive"}, "archive_uses_immutable_database_receipt")
    if "failed_copy_id" in value:
        require(value["command"] in {"inspect_copy_state", "reconcile_indexes"}, "failed_copy_reference_outside_reviewed_command")
        request["failed_copy_id"] = _uuid(value["failed_copy_id"])
        require(request["failed_copy_id"] != request["operation_id"], "diagnostic_requires_its_own_operation_identity")
    if value["command"] == "reconcile_indexes":
        require("failed_copy_id" in request and "prior_copy_id" not in request,
                "manual_reconciliation_requires_a_distinct_failed_copy_link")
    if value["command"] == "inspect_copy_state":
        require(value.get("source") in NAMESPACES or value.get("source") in MARKET_SOURCES,
                "active_source_required_for_copy_diagnostic")
    if "failed_archive_id" in value:
        require(value["command"] in {"inspect_archive_state", "rehearse_archive", "recover_archive"}, "failed_archive_reference_outside_readonly_diagnostic")
        request["failed_archive_id"] = _uuid(value["failed_archive_id"])
        require(request["failed_archive_id"] != request["operation_id"], "diagnostic_requires_its_own_operation_identity")
    if value["command"] == "inspect_archive_state":
        require(value.get("source") in ARCHIVE_SOURCES and "failed_archive_id" in request,
                "fixed_archive_source_and_failed_intent_required")
    if value["command"] == "rehearse_archive":
        require("failed_archive_id" in request and "prior_copy_id" not in request,
                "archive_rehearsal_requires_a_distinct_failed_archive_link")
    if "failed_rehearsal_id" in value:
        require(value["command"] == "recover_archive", "prior_rehearsal_reference_is_manual_recovery_only")
        request["failed_rehearsal_id"] = _uuid(value["failed_rehearsal_id"])
    if value["command"] == "recover_archive":
        require("failed_archive_id" in request and "failed_rehearsal_id" in request
            and len({request["operation_id"], request["failed_archive_id"], request["failed_rehearsal_id"]}) == 3,
            "manual_archive_recovery_requires_three_distinct_intent_ids")
    if value["command"] == "discover_sources":
        require(value.get("source") is None, "discovery_covers_only_trusted_configured_credentials")
    if value["command"] == "prepare_access":
        require(value.get("approved_workflow") is True, "owner_workflow_approval_required")
        require(value.get("source") is None, "access_step_covers_all_approved_roles")
    else:
        require(not value.get("approved_workflow") and not value.get("allow_public_acl_hardening"),
                "approval_flag_outside_access_step")
    return request


def validate_fence(value, environ, *, source, command):
    allowed = {"proof_sha256", "reference", "verified_at", "valid_until", "old_runtime_writers_fenced",
               "application_background_fenced", "ddl_fenced", "capacity_verified", "capacity_evidence_sha256",
               "snapshot_payload_budget_bytes"}
    require(isinstance(value, dict) and set(value) == allowed, "explicit_distributed_fence_required")
    expected = environ.get("IMLADRI_MIGRATION_FENCE_SHA256", "")
    proof = value.get("proof_sha256")
    require(isinstance(proof, str) and bool(re.fullmatch(r"[a-f0-9]{64}", expected))
            and hmac.compare_digest(proof, expected), "distributed_fence_not_verified")
    trusted_raw = environ.get("IMLADRI_MIGRATION_FENCE_JSON", "")
    require(isinstance(trusted_raw, str), "trusted_fence_claims_missing")
    trusted = decode_json(trusted_raw.encode(), maximum=64 * 1024)
    claims = {key: item for key, item in value.items() if key != "proof_sha256"}
    require(isinstance(trusted, dict) and isinstance(trusted.get("claims"), dict)
            and hmac.compare_digest(digest(trusted), expected) and trusted["claims"] == claims,
            "trusted_fence_claims_changed")
    scope = trusted.get("scope")
    valid_commands = ({"copy_personal", "reconcile_indexes", "verify"} if source in NAMESPACES else {"copy_market", "reconcile_indexes", "verify"}
                      if source in MARKET_SOURCES else {"archive_legacy", "rehearse_archive", "recover_archive"}
                      if source in RECOVERY_SOURCES else {"archive_legacy"} if source in ARCHIVE_SOURCES else set())
    target = "imladri-legacy-archive" if source in ARCHIVE_SOURCES else SOURCE_DATABASES.get(source)
    require(isinstance(scope, dict) and set(scope) == {
        "source_alias", "source_namespace", "target_namespace", "allowed_commands"}
        and scope.get("source_alias") == source and scope.get("source_namespace") == SOURCE_DATABASES.get(source)
        and scope.get("target_namespace") == target and isinstance(scope.get("allowed_commands"), list)
        and bool(scope["allowed_commands"]) and all(isinstance(item, str) for item in scope["allowed_commands"])
        and len(scope["allowed_commands"]) == len(set(scope["allowed_commands"]))
        and set(scope["allowed_commands"]) <= valid_commands and command in scope["allowed_commands"],
        "trusted_fence_scope_mismatch")
    if command == "rehearse_archive":
        require(scope["allowed_commands"] == ["rehearse_archive"], "rehearsal_requires_its_own_fence")
    if command == "recover_archive":
        require(scope["allowed_commands"] == ["recover_archive"], "archive_recovery_requires_its_own_fence")
    require(all(value.get(flag) is True for flag in ("old_runtime_writers_fenced", "application_background_fenced", "ddl_fenced")),
            "distributed_writers_not_fenced")
    require(value.get("capacity_verified") is True and isinstance(value.get("capacity_evidence_sha256"), str)
            and bool(re.fullmatch(r"[a-f0-9]{64}", value["capacity_evidence_sha256"]))
            and type(value.get("snapshot_payload_budget_bytes")) is int
            and 0 < value["snapshot_payload_budget_bytes"] <= 64 * 1024 * 1024,
            "external_capacity_approval_required")
    reference = value.get("reference")
    require(isinstance(reference, str) and bool(re.fullmatch(r"[A-Za-z0-9_.:/\\-]{1,240}", reference))
            and "://" not in reference, "invalid_fence_reference")
    try:
        verified, until = (datetime.fromisoformat(value[key]) for key in ("verified_at", "valid_until"))
    except (TypeError, ValueError):
        raise OperatorBlocked("invalid_fence_time") from None
    now = utc_now()
    require(verified.tzinfo is not None and until.tzinfo is not None and -30 <= (now - verified).total_seconds() <= 900
            and 0 < (until - now).total_seconds() <= 1800, "distributed_fence_expired_or_unbounded")
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_field")
        result[key] = value
    return result


def decode_json(data, *, maximum=MAX_BODY):
    require(isinstance(data, bytes) and len(data) <= maximum, "request_too_large")
    try:
        return json.loads(data, object_pairs_hook=_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except OperatorBlocked:
        raise
    except (ValueError, UnicodeDecodeError):
        raise OperatorBlocked("invalid_json") from None


def _parse_config(environ):
    """Trusted env only. No request can supply connection strings or SQL names."""
    from .postgres_codec import connection_options, validate_name
    from .postgres_access import identifier
    owner = environ.get("IMLADRI_POSTGRES_SHADOW_DSN", "")
    require(isinstance(owner, str) and 1 <= len(owner) <= 4096, "owner_connection_missing")
    owner_options = connection_options(owner)
    require(not owner_options["user"].startswith("imladri_app_"), "owner_connection_is_application_role")
    require(owner_options["database"] == "imladri_personal", "not_the_approved_dedicated_database")
    sources_raw = environ.get("IMLADRI_MIGRATION_SOURCES_JSON", "")
    roles_raw = environ.get("IMLADRI_MIGRATION_ROLE_DSNS_JSON", "")
    require(isinstance(sources_raw, str) and isinstance(roles_raw, str), "trusted_configuration_missing")
    sources = decode_json(sources_raw.encode(), maximum=64 * 1024)
    roles = decode_json(roles_raw.encode(), maximum=32 * 1024)
    require(isinstance(sources, dict) and 1 <= len(sources) <= 16, "invalid_source_configuration")
    for logical, source in sources.items():
        require(isinstance(logical, str) and bool(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", logical))
                and isinstance(source, dict) and set(source) in ({"mongo_url", "database"},
                    {"mongo_url", "database", "control_database"}), "invalid_source_configuration")
        require(isinstance(source["mongo_url"], str) and 1 <= len(source["mongo_url"]) <= 4096
                and urlsplit(source["mongo_url"]).scheme in {"mongodb", "mongodb+srv"}, "invalid_source_configuration")
        validate_name(source["database"])
        control = source.get("control_database", source["database"])
        require(control in {source["database"], "imladri_migration_control"}, "unapproved_barrier_control_database")
        if logical in RECOVERY_SOURCES:
            require(control == "imladri_migration_control", "recovery_requires_separate_control_database")
        if logical in SOURCE_DATABASES:
            require(source["database"] == SOURCE_DATABASES[logical], "personal_source_database_changed")
    require(isinstance(roles, dict) and bool(roles) and not set(roles) - set(ROLE_NAMESPACES), "invalid_role_configuration")
    role_options = {}
    for logical, dsn in roles.items():
        require(isinstance(dsn, str) and 1 <= len(dsn) <= 4096, "invalid_role_configuration")
        options = connection_options(dsn)
        identifier(options["user"])
        require(options["user"].startswith("imladri_app_") and len(options["password"]) >= 32
                and options["password"].isascii()
                and all(options[key] == owner_options[key] for key in ("host", "port", "database")), "role_target_changed")
        role_options[logical] = options
    require(len({item["user"] for item in role_options.values()}) == len(roles)
            and len({item["password"] for item in role_options.values()}) == len(roles), "roles_share_credentials")
    config = {"owner_dsn": owner, "owner_options": owner_options, "sources": sources,
              "role_dsns": roles, "role_options": role_options}
    return config


def configuration_proof_input(environ):
    """Offline review input only; does not authorize or execute any operation."""
    metadata = configuration_metadata(_parse_config(environ))
    return {"metadata": metadata, "configuration_sha256": digest(metadata), "external_provider_review_required": True,
            "network_calls": 0, "passwords_or_connection_strings_included": False}


def load_config(environ):
    config = _parse_config(environ)
    fingerprint = digest(configuration_metadata(config))
    approved = environ.get("IMLADRI_MIGRATION_CONFIG_SHA256", "")
    require(isinstance(approved, str) and bool(re.fullmatch(r"[a-f0-9]{64}", approved))
            and hmac.compare_digest(fingerprint, approved), "provider_configuration_not_approved")
    config["configuration_sha256"] = fingerprint
    return config


def configuration_metadata(config):
    """Safe proof input; controller must independently review provider identity."""
    return {"sources": {logical: {"authority": urlsplit(source["mongo_url"]).netloc.rsplit("@", 1)[-1].lower(),
                                  "database": source["database"],
                                  "control_database": source.get("control_database", source["database"])}
                        for logical, source in config["sources"].items()},
            "target": {key: config["owner_options"][key] for key in ("host", "port", "database", "user")},
            "roles": {logical: {"role": item["user"], "namespace": ROLE_NAMESPACES[logical]}
                      for logical, item in config["role_options"].items()}}


class OperatorJournal:
    """Owner-only metadata. Running/uncertain intent is never automatically reset."""
    def __init__(self, connection):
        self.connection = connection
        cursor = connection.cursor()
        try:
            cursor.execute("SET statement_timeout = '20s'")
            cursor.execute("SET lock_timeout = '10s'")
            cursor.execute("SET idle_in_transaction_session_timeout = '30s'")
            connection.commit()
        finally:
            cursor.close()

    def _initialize(self):
        cursor = self.connection.cursor()
        try:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (727364018264907,))
            cursor.execute("CREATE SCHEMA IF NOT EXISTS imladri_operator")
            cursor.execute("REVOKE ALL ON SCHEMA imladri_operator FROM PUBLIC")
            cursor.execute("""CREATE TABLE IF NOT EXISTS imladri_operator.operations (
                operation_id uuid PRIMARY KEY,request_sha256 text NOT NULL,command text NOT NULL,source text NOT NULL,
                release_sha text NOT NULL,state text NOT NULL,result jsonb,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                finished_at timestamptz)""")
            cursor.execute("REVOKE ALL ON imladri_operator.operations FROM PUBLIC")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        finally:
            cursor.close()

    def lookup(self, operation_id):
        cursor = self.connection.cursor()
        try:
            cursor.execute("SELECT to_regclass('imladri_operator.operations')")
            if cursor.fetchone()[0] is None:
                self.connection.rollback()
                return None
            cursor.execute("SELECT request_sha256,command,source,release_sha,state,result FROM imladri_operator.operations WHERE operation_id=%s",
                           (operation_id,))
            row = cursor.fetchone()
            self.connection.rollback()
            if not row:
                return None
            return {"request_sha256": row[0], "command": row[1], "source": row[2], "release_sha": row[3], "state": row[4],
                    "result": json.loads(row[5]) if isinstance(row[5], str) else row[5]}
        finally:
            cursor.close()

    def reserve(self, request):
        # A known UUID is SELECT-only replay, including after a process restart.
        # Infrastructure CREATE/REVOKE is only considered for a new intent.
        previous = self.lookup(request["operation_id"])
        if previous is not None:
            require(previous["request_sha256"] == digest(request), "operation_id_reused_for_different_request")
            return {"state": previous["state"], "result": previous["result"], "execute": False}
        self._initialize()
        cursor = self.connection.cursor()
        try:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (727364018264907,))
            cursor.execute("SELECT request_sha256,state,result FROM imladri_operator.operations WHERE operation_id=%s FOR UPDATE",
                           (request["operation_id"],))
            row = cursor.fetchone()
            if row:
                require(row[0] == digest(request), "operation_id_reused_for_different_request")
                self.connection.rollback()
                return {"state": row[1], "result": json.loads(row[2]) if isinstance(row[2], str) else row[2], "execute": False}
            source = request.get("source") or "all"
            cursor.execute("SELECT operation_id FROM imladri_operator.operations WHERE command=%s AND source=%s AND state IN ('running','unknown') LIMIT 1",
                           (request["command"], source))
            require(cursor.fetchone() is None, "earlier_step_has_unknown_outcome")
            cursor.execute("INSERT INTO imladri_operator.operations(operation_id,request_sha256,command,source,release_sha,state) VALUES(%s,%s,%s,%s,%s,'running')",
                           (request["operation_id"], digest(request), request["command"], source, request["expected_release_sha"]))
            self.connection.commit()
            return {"state": "running", "result": None, "execute": True}
        except BaseException:
            self.connection.rollback()
            raise
        finally:
            cursor.close()

    def finish(self, request, result, *, success):
        require(safe_result(result), "unsafe_operation_result")
        payload = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        require(len(payload.encode()) <= MAX_OUTPUT - 4096, "operation_result_exceeds_bound")
        cursor = self.connection.cursor()
        try:
            cursor.execute("UPDATE imladri_operator.operations SET state=%s,result=%s::jsonb,finished_at=clock_timestamp() WHERE operation_id=%s AND request_sha256=%s AND state='running'",
                           ("completed" if success else "unknown", payload, request["operation_id"], digest(request)))
            require(cursor.rowcount == 1, "journal_completion_not_accepted")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        finally:
            cursor.close()


def _mongo(config, logical):
    from motor.motor_asyncio import AsyncIOMotorClient
    from .postgres_snapshot import _client_bounds
    require(logical in config["sources"], "source_configuration_missing")
    source = config["sources"][logical]
    client = AsyncIOMotorClient(source["mongo_url"], connect=False, serverSelectionTimeoutMS=15000, connectTimeoutMS=15000,
                               socketTimeoutMS=20000, retryWrites=False, readPreference="primary")
    db = client[source["database"]]
    try:
        # Pinned PyMongo defers SRV/TXT parsing, including the default TLS
        # context and concrete seed ports, even when Motor has a client object.
        # Resolve configuration only; _get_topology/_init_background would open
        # Mongo connections before the strict transport guard and are forbidden.
        # Each DNS query retains the fixed 15s connect bound; the entire worker
        # (including DNS) remains inside both independent 600s hard deadlines.
        if client.delegate._resolve_srv_info["is_srv"]:
            require(client.delegate._opened is False, "source_client_opened_before_transport_verification")
            client.delegate._resolve_srv()
            require(client.delegate._opened is False, "source_client_opened_before_transport_verification")
        transport = _client_bounds(db, 20000)
    except BaseException:
        client.close()
        raise
    return client, db, transport


def _control_database(config, logical, client):
    source = config["sources"][logical]
    return client[source.get("control_database", source["database"])]


async def inspect_sources(config, request, journal):
    reports = {}
    logicals = [request["source"]] if request.get("source") else sorted(config["sources"])
    for logical in logicals:
        client, db, transport = _mongo(config, logical)
        try:
            hello = await asyncio.wait_for(client.admin.command("hello"), timeout=20)
            stats = await asyncio.wait_for(db.command({"dbStats": 1, "scale": 1}), timeout=20)
            names = await _inventory(db)
            reports[logical] = {"collections": int(stats.get("collections", 0)), "records": int(stats.get("objects", 0)),
                "logical_bytes": int(stats.get("dataSize", 0)), "source_transport": transport,
                "collection_names": names, "collection_inventory_sha256": digest(names),
                "replica_set_topology": bool(hello.get("setName")) and hello.get("msg") != "isdbgrid",
                "source_application_data_mutated": False}
            if logical == "market_live":
                reports[logical]["coordinate_type_counts"] = await market_coordinate_counts(db)
        finally:
            client.close()
    cursor = journal.connection.cursor()
    try:
        cursor.execute("SELECT rolcreaterole,rolcreatedb,rolsuper FROM pg_catalog.pg_roles WHERE rolname=CURRENT_USER")
        capabilities = cursor.fetchone()
        cursor.execute("SELECT pg_database_size(current_database())")
        database_bytes = int(cursor.fetchone()[0])
        cursor.execute("SELECT COALESCE(sum(pg_total_relation_size(c.oid)),0) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%' AND c.relkind IN ('r','m')")
        relation_bytes = int(cursor.fetchone()[0])
        journal.connection.rollback()
    finally:
        cursor.close()
    return {"sources": reports, "phase": "metadata_and_structural_counts_only", "source_record_values_returned": False,
            "target": {"configured_provider_sha256": digest({key: config["owner_options"][key] for key in ("host", "port", "database")}),
                       "owner_role_can_create_roles": bool(capabilities and capabilities[0]),
                       "owner_role_can_create_databases": bool(capabilities and capabilities[1]),
                       "owner_role_superuser": bool(capabilities and capabilities[2]), "verified_tls_connection": True,
                       "current_database_bytes": database_bytes, "user_relation_bytes": relation_bytes,
                       "provider_free_capacity_measured": False, "external_capacity_approval_required_before_copy": True},
            "static_limits": {"maximum_seconds": MAX_SECONDS, "snapshot_bytes": 64 * 1024 * 1024,
                              "snapshot_records": 100000}}


async def market_coordinate_counts(database):
    """Preliminary live counts only; never authorizes a later cutover."""
    rows = database.listings.aggregate([{"$group": {"_id": {"lat": {"$type": "$lat"},
        "lng": {"$type": "$lng"}}, "records": {"$sum": 1}}}], maxTimeMS=20000, batchSize=128)
    try:
        groups = await asyncio.wait_for(rows.to_list(513), timeout=20)
        require(len(groups) <= 512, "coordinate_type_inventory_exceeds_bound")
        pairs = {}
        for group in groups:
            pair, count = group.get("_id"), group.get("records")
            require(isinstance(pair, dict) and set(pair) == {"lat", "lng"}
                    and all(isinstance(item, str) and bool(re.fullmatch(r"[A-Za-z]{1,32}", item)) for item in pair.values())
                    and type(count) is int and count >= 0, "invalid_coordinate_type_inventory")
            pairs[(pair["lat"], pair["lng"])] = count
        return _coordinate_summary(pairs, pinned=False)
    finally:
        await rows.close()


def _coordinate_summary(pairs, *, pinned):
    lat, lng = {}, {}
    for (left, right), count in pairs.items():
        lat[left] = lat.get(left, 0) + count
        lng[right] = lng.get(right, 0) + count
    decimal_records = sum(count for (left, right), count in pairs.items() if "decimal" in (left, right))
    return {"lat_types": lat, "lng_types": lng, "coordinate_pair_types": [
        {"lat_type": left, "lng_type": right, "records": count} for (left, right), count in sorted(pairs.items())],
        "decimal_coordinate_records": decimal_records, "records": sum(pairs.values()),
        "pinned_final_copy_source": pinned, "decimal_coordinates_block_copy": True,
        "source_values_or_ids_returned": False}


async def market_coordinate_preflight(view, names, *, byte_limit):
    from bson import BSON
    from .document_queries import MISSING, bson_type
    pairs, records, total = {}, 0, 0
    if "listings" in names:
        rows = view["listings"].find({})
        try:
            async for row in rows:
                raw = getattr(row, "raw", None) or BSON.encode(row)
                records += 1
                total += len(raw)
                require(records <= 100000 and total <= byte_limit and len(raw) <= 16 * 1024 * 1024,
                        "coordinate_preflight_exceeds_fixed_budget")
                pair = (bson_type(row.get("lat", MISSING)), bson_type(row.get("lng", MISSING)))
                pairs[pair] = pairs.get(pair, 0) + 1
        finally:
            await rows.close()
    result = {**_coordinate_summary(pairs, pinned=True), "source_bson_bytes_scanned": total,
              "snapshot_view": view.view_identity, "target_write_operations_before_check": 0}
    require(result["decimal_coordinate_records"] == 0, "market_decimal_coordinate_requires_review_before_copy")
    return result


async def _inventory(database):
    names = await asyncio.wait_for(database.list_collection_names(), timeout=20)
    require(isinstance(names, list) and len(names) <= 2048 and all(isinstance(name, str)
            and 0 < len(name.encode()) <= 1024 and "\x00" not in name for name in names)
            and len(names) == len(set(names)) and sum(len(name.encode()) for name in names) <= 128 * 1024,
            "source_catalog_exceeds_fixed_inventory_bound")
    return sorted(names)


async def discover_sources(config):
    """Authorized name-only discovery; never reads records or expands permissions."""
    reports = {}
    for logical in sorted(config["sources"]):
        client, _, transport = _mongo(config, logical)
        try:
            result = await asyncio.wait_for(client.admin.command({"listDatabases": 1, "nameOnly": True,
                "authorizedDatabases": True, "maxTimeMS": 20000}), timeout=20)
            rows = result.get("databases")
            require(isinstance(rows, list) and len(rows) <= 128, "authorized_database_inventory_exceeds_bound")
            names = []
            for row in rows:
                name = row.get("name") if isinstance(row, dict) else None
                require(isinstance(name, str) and 0 < len(name.encode()) <= 128 and "\x00" not in name,
                        "authorized_database_name_invalid")
                names.append(name)
            require(len(names) == len(set(names)), "authorized_database_inventory_duplicated")
            reports[logical] = {"authorized_database_names": sorted(names), "inventory_sha256": digest(sorted(names)),
                "source_transport": transport, "application_records_read": 0, "source_write_operations": 0,
                "permissions_expanded": False, "discovery_complete_beyond_current_permissions": False}
        finally:
            client.close()
    return {"sources": reports, "metadata_only": True, "all_account_clusters_discovered": False,
            "credentials_or_record_values_included": False}


def prepare_access(config, request):
    import pg8000.dbapi
    from .postgres_access import bootstrap_access
    require(request.get("approved_workflow") is True, "owner_workflow_approval_required")
    options = {**config["owner_options"], "timeout": 20, "application_name": "imladri-migration-bootstrap"}
    connection = pg8000.dbapi.connect(**options)
    try:
        bindings = {item["user"]: ROLE_NAMESPACES[logical] for logical, item in config["role_options"].items()}
        passwords = {item["user"]: item["password"] for item in config["role_options"].values()}
        return bootstrap_access(connection, bindings, passwords,
            allow_public_acl_hardening=request.get("allow_public_acl_hardening") is True)
    finally:
        connection.close()


async def copy_personal(config, request, journal):
    return await copy_application(config, request, journal, application="personal")


async def copy_application(config, request, journal, *, application):
    from .postgres_cutover import CopyLimits, copy_frozen, _digest as receipt_digest
    from .postgres_copy_registry import application_collections
    from .postgres_snapshot import create_majority_barrier, pinned_source
    from .postgres_store import PostgresDatabase
    logical = request["source"]
    selected = NAMESPACES if application == "personal" else MARKET_SOURCES if application == "market" else {}
    require(logical in selected, "fixed_application_source_required")
    client, source, _ = _mongo(config, logical)
    target = PostgresDatabase(config["owner_dsn"], selected[logical], initialize_schema=True)
    try:
        hello = await asyncio.wait_for(client.admin.command("hello"), timeout=20)
        require(bool(hello.get("setName")) and hello.get("msg") != "isdbgrid", "source_topology_requires_new_barrier_design")
        fence = request["fence"]
        barrier = await create_majority_barrier(source, fence_reference=fence["reference"],
            application_writers_stopped=True, application_background_writers_stopped=True, ddl_stopped=True,
            control_database=(_control_database(config, logical, client)
                              if config["sources"][logical].get("control_database", source.name) != source.name else None))
        names = await _inventory(source)
        require(bool(names) and not set(names) - application_collections(application), "unclassified_active_source_collection")
        prior = None
        if request.get("prior_copy_id"):
            previous = journal.lookup(request["prior_copy_id"])
            require(previous and previous["state"] == "completed" and previous["command"] == request["command"]
                    and previous["source"] == logical, "prior_copy_receipt_unavailable")
            prior = previous["result"]
        async with pinned_source(source, barrier=barrier, max_seconds=MAX_SECONDS, collection_allowlist=names) as view:
            for name in names:
                metadata = await view.collection_metadata(name)
                require(metadata["data_kind"] == "collection", "active_source_virtual_collection_not_supported")
            compatibility = (await market_coordinate_preflight(view, names,
                byte_limit=fence["snapshot_payload_budget_bytes"]) if application == "market" else None)
            freeze = {"source": {"backend": "mongodb", "namespace": source.name, **view.view_identity},
                "target": {"backend": "postgresql", "namespace": target.name}, "source_data_mode": "pinned_snapshot",
                "source_writers_stopped": True, "target_writers_stopped": True,
                "source_application_background_writers_stopped": True, "source_ddl_stopped": True,
                "target_background_writers_stopped": True, "evidence_reference": fence["reference"], "valid_until": fence["valid_until"]}
            receipt = await copy_frozen(view, target, freeze=freeze, expected_target=prior,
                limits=CopyLimits(max_bytes=fence["snapshot_payload_budget_bytes"]), application=application)
            if compatibility is not None:
                receipt["market_coordinate_compatibility"] = compatibility
                receipt["receipt_fingerprint"] = receipt_digest({key: value for key, value in receipt.items()
                                                               if key != "receipt_fingerprint"})
            return receipt
    finally:
        client.close()
        await target.aclose()


async def archive_legacy(config, request, *, _connection_factory=None, _stage=None, _manual_recovery=None):
    import pg8000.dbapi
    from .postgres_archive import archive_source
    from .postgres_snapshot import create_majority_barrier, pinned_source
    logical = request["source"]
    require(logical in ARCHIVE_SOURCES, "fixed_legacy_source_required")
    progress = _stage or (lambda _value: None)
    progress("source_topology")
    client, source, _ = _mongo(config, logical)
    connection = None
    try:
        hello = await asyncio.wait_for(client.admin.command("hello"), timeout=20)
        require(bool(hello.get("setName")) and hello.get("msg") != "isdbgrid", "source_topology_requires_new_barrier_design")
        fence = request["fence"]
        progress("source_barrier")
        barrier = await create_majority_barrier(source, fence_reference=fence["reference"],
            application_writers_stopped=True, application_background_writers_stopped=True, ddl_stopped=True,
            control_database=(_control_database(config, logical, client)
                              if config["sources"][logical].get("control_database", source.name) != source.name else None))
        progress("source_inventory")
        names = await _inventory(source)
        require(bool(names), "legacy_source_has_no_persisted_collections")
        progress("target_connect")
        connection = (_connection_factory or pg8000.dbapi.connect)(**{**config["owner_options"], "timeout": 20,
                                             "application_name": "imladri-fixed-legacy-archive"})
        progress("source_pin")
        async with pinned_source(source, barrier=barrier, max_seconds=MAX_SECONDS, collection_allowlist=names) as view:
            progress("archive_core")
            return await archive_source(view, connection, source_alias=logical, max_records=100000,
                max_source_bytes=fence["snapshot_payload_budget_bytes"], max_seconds=500,
                max_target_payload_bytes=512 * 1024 * 1024, manual_recovery=_manual_recovery)
    finally:
        if connection is not None:
            connection.close()
        client.close()


def reconciliation_link(request, environ, journal):
    """Bind manual recovery to an actual unknown copy without resetting it."""
    trusted = decode_json(environ.get("IMLADRI_MIGRATION_FENCE_JSON", "").encode(), maximum=64 * 1024)
    require(digest(trusted) == request["fence"]["proof_sha256"], "reconciliation_fence_changed")
    metadata = trusted.get("reconciliation")
    require(isinstance(metadata, dict) and set(metadata) == {"linked_failed_copy_id", "original_request",
        "source_application_writers_continuously_paused", "target_application_writers_continuously_paused"},
        "trusted_reconciliation_history_required")
    require(metadata["source_application_writers_continuously_paused"] is True
            and metadata["target_application_writers_continuously_paused"] is True,
            "continuous_application_writer_pause_required")
    failed = request["failed_copy_id"]
    require(metadata["linked_failed_copy_id"] == failed, "reconciliation_failed_copy_link_changed")
    original = metadata["original_request"]
    required = {"operation_id", "expected_release_sha", "command", "source", "fence", "configuration_sha256"}
    require(isinstance(original, dict) and required <= set(original)
            and set(original) <= required | {"prior_copy_id", "approved_workflow", "allow_public_acl_hardening"},
            "bounded_original_copy_request_required")
    expected_command = "copy_personal" if request["source"] in NAMESPACES else "copy_market"
    require(original["operation_id"] == failed and original["command"] == expected_command
            and original["source"] == request["source"]
            and isinstance(original["expected_release_sha"], str)
            and bool(re.fullmatch(r"[a-f0-9]{40}", original["expected_release_sha"]))
            and isinstance(original["configuration_sha256"], str)
            and bool(re.fullmatch(r"[a-f0-9]{64}", original["configuration_sha256"]))
            and not original.get("approved_workflow") and not original.get("allow_public_acl_hardening"),
            "original_copy_identity_or_scope_changed")
    old_fence = original.get("fence")
    reference = old_fence.get("reference") if isinstance(old_fence, dict) else None
    require(isinstance(reference, str) and bool(re.fullmatch(r"[A-Za-z0-9_.:/\\-]{1,240}", reference))
            and "://" not in reference, "original_copy_fence_reference_invalid")
    prior = journal.lookup(failed)
    require(prior and prior["state"] == "unknown" and prior["source"] == request["source"]
            and prior["command"] == expected_command and prior["release_sha"] == original["expected_release_sha"]
            and hmac.compare_digest(prior["request_sha256"], digest(original)),
            "unknown_copy_request_history_does_not_match")
    return {"linked_failed_copy_id": failed, "original_request_sha256": prior["request_sha256"],
            "original_fence_reference": reference, "original_release_sha": prior["release_sha"],
            "source_application_writers_continuously_paused": True,
            "target_application_writers_continuously_paused": True}


_RECONCILIATION_FAILURE_STAGES = {
    **{reason: "validate_link" for reason in (
        "reconciliation_fence_changed", "trusted_reconciliation_history_required",
        "continuous_application_writer_pause_required", "reconciliation_failed_copy_link_changed",
        "bounded_original_copy_request_required", "original_copy_identity_or_scope_changed",
        "original_copy_fence_reference_invalid", "unknown_copy_request_history_does_not_match",
        "durable_reconciliation_history_not_verified")},
    **{reason: "source_barrier_witness" for reason in (
        "reconciliation_requires_pinned_source", "readonly_server_selected_majority_snapshot_required",
        "reconciliation_control_scan_exceeds_bound", "original_control_record_shape_invalid",
        "original_control_record_is_not_unique", "original_control_record_not_in_majority_snapshot")},
}


def reconciliation_failure(error):
    reason = error.args[0] if isinstance(error, OperatorBlocked) and len(error.args) == 1 else None
    if isinstance(reason, str) and reason in _RECONCILIATION_FAILURE_STAGES:
        return {"reason_code": reason, "stage": _RECONCILIATION_FAILURE_STAGES[reason]}
    return {}


async def reconciliation_witness(view, link, *, maximum_bytes):
    """Witness the actual original control row in a majority-committed snapshot.

    This is neither a new majority write nor reconstruction of an old timestamp.
    Target equality is independently required by the atomic repair core.
    """
    from bson import BSON
    from .postgres_migration import document_key
    from .postgres_snapshot import PinnedMongoSource
    require(isinstance(view, PinnedMongoSource), "reconciliation_requires_pinned_source")
    view.ensure_active()
    evidence = view.evidence
    require(evidence.get("snapshot_session") is True and evidence.get("read_concern") == "snapshot"
            and evidence.get("server_selected_timestamp") is True and evidence.get("majority_write_barrier") is None
            and evidence.get("subsequent_data_finds_use_same_explicit_timestamp") is True,
            "readonly_server_selected_majority_snapshot_required")
    rows = view["database_migration_reports"].find({})
    found, count, size = [], 0, 0
    try:
        async for row in rows:
            payload = getattr(row, "raw", None) or BSON.encode(row)
            count += 1
            size += len(payload)
            require(count <= 1024 and size <= min(maximum_bytes, 2 * 1024 * 1024), "reconciliation_control_scan_exceeds_bound")
            if row.get("kind") != "postgres_cutover_majority_barrier" or row.get("fence_reference") != link["original_fence_reference"]:
                continue
            require(len(payload) <= 64 * 1024 and set(row) == {"_id", "kind", "version", "fence_reference", "committed_at"}
                    and isinstance(row.get("_id"), str)
                    and bool(re.fullmatch(r"postgres-cutover-barrier:[a-f0-9]{32}", row["_id"]))
                    and type(row.get("version")) is int and row["version"] == 1
                    and isinstance(row.get("committed_at"), datetime), "original_control_record_shape_invalid")
            found.append((hashlib.sha256(payload).hexdigest(), document_key(row)))
            require(len(found) <= 1, "original_control_record_is_not_unique")
    finally:
        await rows.close()
    require(len(found) == 1, "original_control_record_not_in_majority_snapshot")
    return {"kind": "existing_majority_snapshot_barrier", "source_namespace": view.name,
        "view_identity": view.view_identity, "linked_failed_copy_id": link["linked_failed_copy_id"],
        "original_fence_reference": link["original_fence_reference"],
        "original_request_sha256": link["original_request_sha256"],
        "control_record_sha256": found[0][0], "control_identity_sha256": found[0][1],
        "control_scan_records": count, "control_scan_bson_bytes": size,
        "source_write_operations": 0, "new_majority_write_barrier_created": False,
        "original_timestamp_reconstructed": False,
        "source_application_writers_continuously_paused": True,
        "target_application_writers_continuously_paused": True}


async def reconcile_indexes(config, request, journal):
    import pg8000.dbapi
    from .postgres_copy_registry import application_collections
    from .postgres_cutover import CopyLimits
    from .postgres_index_reconciliation import reconcile_index_metadata
    from .postgres_snapshot import pinned_source
    logical = request["source"]
    require(logical in NAMESPACES or logical in MARKET_SOURCES, "active_source_required_for_index_reconciliation")
    application = "personal" if logical in NAMESPACES else "market"
    link = config.get("_verified_reconciliation_link")
    require(isinstance(link, dict) and link.get("linked_failed_copy_id") == request["failed_copy_id"],
            "durable_reconciliation_history_not_verified")
    client, source, _ = _mongo(config, logical)
    connection = None
    try:
        names = await _inventory(source)
        require(bool(names) and not set(names) - application_collections(application), "unclassified_active_source_collection")
        async with pinned_source(source, max_seconds=MAX_SECONDS, collection_allowlist=names) as view:
            for name in names:
                metadata = await view.collection_metadata(name)
                require(metadata["data_kind"] == "collection", "active_source_virtual_collection_not_supported")
            witness = await reconciliation_witness(view, link, maximum_bytes=request["fence"]["snapshot_payload_budget_bytes"])
            fence = request["fence"]
            freeze = {"source": {"backend": "mongodb", "namespace": source.name, **view.view_identity},
                "target": {"backend": "postgresql", "namespace": SOURCE_DATABASES[logical]},
                "source_data_mode": "readonly_majority_snapshot_reconciliation",
                "source_writers_stopped": True, "target_writers_stopped": True,
                "source_application_background_writers_stopped": True, "source_ddl_stopped": True,
                "target_background_writers_stopped": True, "evidence_reference": fence["reference"],
                "valid_until": fence["valid_until"]}
            connection = pg8000.dbapi.connect(**{**config["owner_options"], "timeout": 20,
                                                 "application_name": "imladri-manual-index-reconciliation"})
            return await reconcile_index_metadata(view, connection, namespace=SOURCE_DATABASES[logical],
                freeze=freeze, failed_copy_id=request["failed_copy_id"], application=application,
                limits=CopyLimits(max_bytes=fence["snapshot_payload_budget_bytes"]), reconciliation_witness=witness)
    finally:
        if connection is not None:
            connection.close()
        client.close()


async def verify_copy(config, request, journal):
    import pg8000.dbapi
    from .postgres_access import verify_runtime
    from .postgres_cutover import CopyLimits, snapshot
    from .postgres_store import PostgresDatabase
    logical = request["source"]
    application = "personal" if logical in NAMESPACES else "market"
    namespace = SOURCE_DATABASES[logical]
    role_logicals = [logical] if application == "personal" else ["market_api", "market_worker"]
    require(all(item in config["role_dsns"] for item in role_logicals) and request.get("prior_copy_id"),
            "completed_copy_and_runtime_role_required")
    previous = journal.lookup(request["prior_copy_id"])
    require(previous and previous["state"] == "completed" and previous["command"] in {"copy_" + application, "reconcile_indexes"}
            and previous["source"] == logical, "completed_copy_receipt_unavailable")
    if previous["command"] == "reconcile_indexes":
        receipt = previous["result"]
        require(isinstance(receipt, dict) and receipt.get("status") == "completed_frozen_copy"
                and receipt.get("kind") == "manual_index_metadata_reconciliation"
                and receipt.get("source_data_mode") == "readonly_majority_snapshot_reconciliation"
                and receipt.get("application") == application
                and receipt.get("target") == {"backend": "postgresql", "namespace": namespace}
                and receipt.get("source_application_write_operations") == 0
                and receipt.get("target_application_document_write_operations") == 0,
                "completed_reconciliation_receipt_invalid")
        linked = journal.lookup(receipt.get("linked_failed_copy_id"))
        require(linked and linked["state"] == "unknown" and linked["source"] == logical
                and linked["command"] == "copy_" + application, "reconciliation_prior_unknown_link_invalid")
    roles = {}
    for alias in role_logicals:
        options = {**config["role_options"][alias], "timeout": 20, "application_name": "imladri-migration-role-verification"}
        connection = pg8000.dbapi.connect(**options)
        cursor = connection.cursor()
        try:
            roles[alias] = verify_runtime(cursor, namespace, expected_role=options["user"])
            connection.rollback()
        finally:
            cursor.close(); connection.close()
    store = PostgresDatabase(config["role_dsns"][role_logicals[0]], namespace, initialize_schema=False)
    try:
        current = await snapshot(store, limits=CopyLimits(max_bytes=request["fence"]["snapshot_payload_budget_bytes"]),
                                 budget={"scanned_bytes": 0}, application=application)
        require(current.manifest == previous["result"]["target_after"], "runtime_namespace_changed_since_copy")
        return {"runtime_roles": roles, "snapshot": current.manifest, "exact_copy_parity": True,
                "routing_changed": False, "application_data_mutated": False}
    finally:
        await store.aclose()


async def execute(request, config, journal):
    if request["command"] == "inspect_completed_operation":
        from .postgres_completed_operation import inspect_completed_operation
        return await inspect_completed_operation(config, request, journal)
    if request["command"] == "inspect":
        return await inspect_sources(config, request, journal)
    if request["command"] == "inspect_copy_state":
        from .postgres_copy_diagnostics import inspect_copy_state
        return await inspect_copy_state(config, request, journal, mongo_factory=_mongo, inventory=_inventory)
    if request["command"] == "inspect_archive_state":
        from .postgres_archive_diagnostics import inspect_archive_state
        return await inspect_archive_state(config, request, journal, mongo_factory=_mongo, inventory=_inventory)
    if request["command"] == "rehearse_archive":
        from .postgres_archive_rehearsal import rehearse_archive
        return await rehearse_archive(config, request, journal, archive_runner=archive_legacy)
    if request["command"] == "recover_archive":
        from .postgres_archive_recovery import recover_archive
        return await recover_archive(config, request, journal, archive_runner=archive_legacy)
    if request["command"] == "discover_sources":
        return await discover_sources(config)
    if request["command"] == "prepare_access":
        return prepare_access(config, request)
    if request["command"] == "copy_personal":
        return await copy_personal(config, request, journal)
    if request["command"] == "copy_market":
        return await copy_application(config, request, journal, application="market")
    if request["command"] == "archive_legacy":
        return await archive_legacy(config, request)
    if request["command"] == "reconcile_indexes":
        return await reconcile_indexes(config, request, journal)
    if request["command"] == "verify":
        return await verify_copy(config, request, journal)
    raise OperatorBlocked("unsupported_command")


def harden():
    path = ROOT / "entrypoint.py"
    if not path.is_file():
        path = ROOT / "cloudflare/entrypoint.py"
    spec = importlib.util.spec_from_file_location("imladri_operator_hardening", path)
    require(spec is not None and spec.loader is not None, "hardening_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = module.harden_process()
    require(all(state.get(key) == "0000000000000000" for key in ("CapEff", "CapPrm", "CapInh"))
            and state.get("NoNewPrivs") == "1", "hardening_postcondition_failed")
    return state


def load_driver_bundle():
    from importlib import metadata
    path = ROOT / "postgres-operator/run_postgres_shadow.py"
    spec = importlib.util.spec_from_file_location("imladri_reviewed_driver_bundle", path)
    require(spec is not None and spec.loader is not None, "reviewed_driver_bundle_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    proof = module.bundle_proof()
    require(proof["native_or_compiled_files"] == 0 and proof["operator_distribution_count"] == 3,
            "reviewed_driver_bundle_changed")
    installed = {}
    for name in module.PACKAGES:
        try:
            installed[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    if installed:
        require(installed == module.PACKAGES, "installed_driver_versions_differ_from_review")
        mode = "normal_verified_distributions"
    else:
        sys.path.insert(0, str(path.parent / "drivers.zip"))
        mode = "isolated_reviewed_bundle"
    return {"zip_sha256": proof["zip_sha256"], "mode": mode, "distribution_count": 3}


async def run(envelope, environ, *, config_loader=load_config, journal_factory=OperatorJournal):
    import pg8000.dbapi
    action = envelope.get("action")
    require(action in {"execute", "status"}, "unsupported_internal_action")
    request = validate_request(envelope.get("request"), environ, status=action == "status")
    config = config_loader(environ)
    # Bind durable retry identities to safe endpoint/role metadata, never to
    # passwords or entire connection strings. Configuration rotation cannot
    # silently reinterpret an existing operation as a new source/namespace.
    request["configuration_sha256"] = config.get("configuration_sha256") or digest(configuration_metadata(config))
    connection = pg8000.dbapi.connect(**{**config["owner_options"], "timeout": 20,
                                         "application_name": "imladri-migration-journal"})
    journal = journal_factory(connection)
    try:
        if action == "status":
            previous = journal.lookup(request["operation_id"])
            if not previous:
                return {"status": "not_found", "operation_id": request["operation_id"]}
            require(previous["release_sha"] == request["expected_release_sha"], "journal_release_mismatch")
            require(safe_result(previous["result"]), "unsafe_cached_operation_result")
            return {"status": previous["state"], "operation_id": request["operation_id"],
                    "result": previous["result"], "needs_manual_review": previous["state"] in {"running", "unknown"}
                    or previous["command"] == "rehearse_archive"}
        if request["command"] in {"inspect_copy_state", "inspect_archive_state", "inspect_completed_operation"}:
            # A diagnostic must not mutate the unknown intent it investigates,
            # create a journal table/row, or be blocked by another failed read.
            result = await execute(request, config, journal)
            require(safe_result(result), "unsafe_readonly_copy_diagnostic_result")
            return {"status": "completed", "operation_id": request["operation_id"], "result": result,
                    "read_only_diagnostic": True, "diagnostic_result_durable_in_operator_journal": False,
                    "needs_manual_review": True}
        if request["command"] == "reconcile_indexes":
            config = {**config, "_verified_reconciliation_link": reconciliation_link(request, environ, journal)}
        if request["command"] == "rehearse_archive":
            from .postgres_archive_rehearsal import link_unknown
            config = {**config, "_verified_archive_rehearsal_link": link_unknown(request, journal)}
        if request["command"] == "recover_archive":
            from .postgres_archive_recovery import recovery_link
            link = recovery_link(request, environ, journal)
            config = {**config, "_verified_archive_recovery_link": link}
            request["recovery_links_sha256"] = digest(link)
        reservation = journal.reserve(request)
        if not reservation["execute"]:
            require(safe_result(reservation["result"]), "unsafe_cached_operation_result")
            return {"status": reservation["state"], "operation_id": request["operation_id"],
                    "result": reservation["result"], "replayed_without_execution": True,
                    "needs_manual_review": reservation["state"] != "completed" or request["command"] == "rehearse_archive"}
        try:
            validate_request({key: value for key, value in request.items()
                if key not in {"configuration_sha256", "recovery_links_sha256"}}, environ)
            result = await execute(request, config, journal)
            require(safe_result(result), "unsafe_operation_result")
            journal.finish(request, result, success=True)
            return {"status": "completed", "operation_id": request["operation_id"], "result": result,
                    "needs_manual_review": request["command"] == "rehearse_archive"}
        except Exception as error:
            failure = {"error_type": type(error).__name__, "needs_manual_review": True}
            from .postgres_copy_diagnostics import safe_error
            failure.update(safe_error(error))
            if request["command"] == "reconcile_indexes":
                failure.update(reconciliation_failure(error))
            if request["command"] == "rehearse_archive":
                from .postgres_archive_rehearsal import safe_rehearsal_error
                failure.update(safe_rehearsal_error(error))
            journal.finish(request, failure, success=False)
            return {"status": "unknown", "operation_id": request["operation_id"], "result": failure,
                    "needs_manual_review": True}
    finally:
        connection.close()


def main():
    security = None
    try:
        # A standalone invocation must have its own process group before the
        # hard deadline can ever signal it. No shell or caller-owned group.
        require(sys.platform == "linux", "linux_hardening_required")
        if os.getsid(0) != os.getpid():
            os.setsid()
        require(os.getpgrp() == os.getpid(), "private_process_group_required")
        security = harden()  # BEFORE drivers, configuration or any networking.
        signal.signal(signal.SIGALRM, lambda *_: os.killpg(os.getpgrp(), signal.SIGKILL))
        signal.setitimer(signal.ITIMER_REAL, MAX_SECONDS)
        require(len(sys.argv) == 1, "worker_arguments_are_fixed")
        envelope = decode_json(sys.stdin.buffer.read(MAX_BODY + 1))
        require(isinstance(envelope, dict) and set(envelope) == {"action", "request"}, "invalid_worker_envelope")
        deployment_gate(os.environ)
        driver = load_driver_bundle()
        result = asyncio.run(run(envelope, os.environ))
        result.update(version=1, security=security, driver=driver)
    except Exception as error:
        result = {"version": 1, "status": "blocked", "error_type": type(error).__name__,
                  "security": security, "needs_manual_review": True}
        if isinstance(error, OperatorBlocked):
            result["reason"] = str(error)
            result.update(reconciliation_failure(error))
    payload = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(payload) > MAX_OUTPUT:
        payload = json.dumps({"version": 1, "status": "blocked", "error_type": "output_bound", "security": security,
                              "needs_manual_review": True}, separators=(",", ":")).encode()
    sys.stdout.buffer.write(payload + b"\n")
    sys.stdout.buffer.flush()
    return 0 if result.get("status") in {"completed", "not_found", "running", "unknown"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
