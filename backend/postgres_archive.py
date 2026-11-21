"""Owner-only archival copy of legacy MongoDB databases, never active app data.

Unknown collection names are literal bound data. Every physical record and full
catalog/index definition retains its BSON bytes; no workflow or research consent
is inferred. Only an explicit inventory-bound majority-barrier snapshot may be
used. Existing verified archives are checked, never overwritten or refreshed.
"""
from copy import deepcopy
from datetime import datetime, timezone
import asyncio
import hashlib
import json
import re
import time
from uuid import UUID

from bson import BSON
from bson.raw_bson import RawBSONDocument

from .postgres_migration import (connection_options, document_key, fingerprint_add,
                                 fingerprint_finish, raw_document, validate_name, canonical_document, jsonb_text)
from .postgres_snapshot import PinnedMongoSource


ARCHIVE_NAMESPACE = "imladri-legacy-archive"
SCHEMA_VERSION = 2
TABLES = ("collections", "documents", "receipts")
SCHEMA = [
    "CREATE SCHEMA IF NOT EXISTS imladri_archive",
    """CREATE TABLE IF NOT EXISTS imladri_archive.collections (
        namespace text NOT NULL,source_alias text NOT NULL,source_database text NOT NULL,name text NOT NULL,kind text NOT NULL,
        spec_bson bytea NOT NULL,spec_sha256 text NOT NULL,indexes_bson bytea NOT NULL,indexes_sha256 text NOT NULL,
        record_count bigint NOT NULL,fingerprint text NOT NULL,
        PRIMARY KEY(namespace,source_alias,name))""",
    """CREATE TABLE IF NOT EXISTS imladri_archive.documents (
        namespace text NOT NULL,source_alias text NOT NULL,source_database text NOT NULL,collection text NOT NULL,
        id_key text NOT NULL,id_bson bytea NOT NULL,raw_bson bytea NOT NULL,
        canonical_json text NOT NULL,query_document jsonb,source_sha256 text NOT NULL,
        PRIMARY KEY(namespace,source_alias,collection,id_key),
        FOREIGN KEY(namespace,source_alias,collection) REFERENCES imladri_archive.collections(namespace,source_alias,name))""",
    """CREATE TABLE IF NOT EXISTS imladri_archive.receipts (
        namespace text NOT NULL,source_alias text NOT NULL,source_database text NOT NULL,receipt_bson bytea NOT NULL,receipt_sha256 text NOT NULL,
        completed_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(namespace,source_alias))""",
    """CREATE OR REPLACE FUNCTION imladri_archive.immutable_receipt() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog,pg_temp AS $$BEGIN
        RAISE EXCEPTION 'Verified archive receipts are immutable' USING ERRCODE='55000'; END$$""",
    "DROP TRIGGER IF EXISTS immutable_receipt ON imladri_archive.receipts",
    """CREATE TRIGGER immutable_receipt BEFORE UPDATE OR DELETE ON imladri_archive.receipts
        FOR EACH ROW EXECUTE FUNCTION imladri_archive.immutable_receipt()""",
    "REVOKE ALL ON SCHEMA imladri_archive FROM PUBLIC",
    "REVOKE ALL ON ALL TABLES IN SCHEMA imladri_archive FROM PUBLIC",
    "REVOKE ALL ON FUNCTION imladri_archive.immutable_receipt() FROM PUBLIC",
]


ARCHIVE_REASONS = {
    "A source collection name is invalid or exceeds its bound.": "archive_collection_name_invalid",
    "Exact archive catalog metadata is unavailable.": "archive_exact_metadata_unavailable",
    "Archive metadata types or bounds are invalid.": "archive_metadata_types_or_bounds_invalid",
    "Archive collection metadata changed or exceeds its bound.": "archive_collection_metadata_changed_or_limit",
    "A pinned archive snapshot source is required.": "archive_pinned_source_required",
    "Archive copy requires an explicit inventory and majority application/background/DDL barrier.": "archive_explicit_writer_and_majority_barrier_required",
    "The archive source origin has no verified transport identity.": "archive_source_transport_identity_missing",
    "Legacy archives require a dedicated database owner connection.": "archive_dedicated_owner_required",
    "Existing archives require an explicit source-key schema review; no automatic rewrite is allowed.": "archive_existing_source_key_schema_requires_review",
    "An application login can access the owner archive; explicit remediation is required.": "archive_application_privilege_requires_review",
    "The archive source-key foreign key is missing or changed; no automatic repair is allowed.": "archive_source_key_foreign_key_requires_review",
    "The archive source alias contains a different actual database.": "archive_alias_database_mismatch",
    "Archived catalog inventory exceeds its bounded verification budget.": "archive_target_catalog_limit",
    "Archived BSON exceeds its bounded verification budget.": "archive_target_BSON_limit",
    "Archived analysis projections exceed their bounded verification budget.": "archive_target_projection_limit",
    "Archived catalog bytes no longer match their receipt.": "archive_target_catalog_hash_mismatch",
    "Archived BSON identity or record bytes no longer match their receipt.": "archive_target_identity_or_BSON_mismatch",
    "Archived analysis projections differ from authoritative BSON.": "archive_target_projection_mismatch",
    "Archived counts or record fingerprint changed.": "archive_target_count_or_fingerprint_changed",
    "Uncataloged archive records cannot be omitted from verification.": "archive_target_uncataloged_records",
    "Archive budgets are invalid.": "archive_budget_invalid",
    "The bounded archive deadline expired.": "archive_deadline_expired",
    "The aggregate source archive budget was exceeded.": "archive_source_aggregate_budget_exceeded",
    "The archive analysis projection payload budget was exceeded.": "archive_projection_payload_budget_exceeded",
    "Aggregate archive metadata budget is invalid.": "archive_aggregate_metadata_budget_invalid",
    "Archive analysis projection payload budget is invalid.": "archive_projection_budget_invalid",
    "Archive metadata budget is invalid.": "archive_metadata_budget_invalid",
    "Archive inventory is empty, duplicated or exceeds its bound.": "archive_inventory_invalid",
    "The source inventory differs from its reviewed snapshot allowlist.": "archive_source_inventory_not_reviewed",
    "The immutable archive receipt exceeds its verification bound.": "archive_receipt_limit",
    "The immutable archive receipt was changed.": "archive_receipt_hash_mismatch",
    "The immutable archive receipt does not match its fixed target scope.": "archive_receipt_scope_mismatch",
    "An unreceipted archive target is not empty; it will not be overwritten.": "archive_unreceipted_target_not_empty",
    "Aggregate source catalog metadata exceeds its budget.": "archive_source_catalog_budget_exceeded",
    "Time-series archive requires its exact physical bucket collection.": "archive_timeseries_bucket_required",
    "The pinned source data changed between complete archive passes.": "archive_source_data_changed",
    "Source collection/index metadata changed during the archive.": "archive_source_metadata_changed",
    "The pinned archive inventory or view identity changed.": "archive_source_inventory_or_view_changed",
    "The complete target archive differs from its pinned source.": "archive_source_target_parity_failed",
    "A different source cannot overwrite the immutable verified archive.": "archive_immutable_receipt_different_source",
    "The owner-only legacy archive outcome requires explicit review.": "archive_outcome_requires_review",
    "The explicit archive recovery linkage is invalid.": "archive_manual_recovery_link_invalid",
    "The immutable archive recovery linkage differs.": "archive_manual_recovery_link_changed",
}
ARCHIVE_STAGES = frozenset({"validate", "source_inventory", "target_schema", "target_existing_receipt",
    "source_metadata", "first_source_pass", "second_source_pass", "target_verification", "receipt_finalize", "commit"})


class ArchiveBlocked(RuntimeError):
    """Sanitized failure without connection strings, BSON or source contents."""
    def __init__(self, message, *, stage="validate"):
        super().__init__(message)
        self.stage = stage


def safe_archive_error(error):
    result = {"error_type": type(error).__name__}
    if isinstance(error, ArchiveBlocked):
        message = error.args[0] if len(error.args) == 1 else None
        result["reason_code"] = (ARCHIVE_REASONS.get(message, "unclassified_archive_block")
                                 if isinstance(message, str) else "unclassified_archive_block")
        result["stage"] = error.stage if error.stage in ARCHIVE_STAGES else "unspecified"
    return result


def connect_owner(dsn, *, allow_local_plaintext=False):
    """Explicit caller-owned connection, with normal external verified TLS."""
    import pg8000.dbapi
    options = connection_options(dsn, allow_local_plaintext=allow_local_plaintext)
    options["application_name"] = "imladri-legacy-archive"
    return pg8000.dbapi.connect(**options)


def _name(value):
    if not isinstance(value, str) or not value or "\x00" in value or len(value.encode("utf8")) > 1024:
        raise ArchiveBlocked("A source collection name is invalid or exceeds its bound.")
    return value


def _digest(payload):
    return hashlib.sha256(payload).hexdigest()


def _metadata(value, name, max_bytes):
    if not isinstance(value, dict) or value.get("metadata_outside_snapshot") is not True:
        raise ArchiveBlocked("Exact archive catalog metadata is unavailable.")
    spec, indexes, kind = value.get("spec_bson"), value.get("indexes_bson"), value.get("data_kind")
    if not isinstance(spec, bytes) or not isinstance(indexes, list) or any(not isinstance(row, bytes) for row in indexes) \
            or len(indexes) > 256 or kind not in {"collection", "view", "timeseries"}:
        raise ArchiveBlocked("Archive metadata types or bounds are invalid.")
    if len(spec) + sum(map(len, indexes)) > max_bytes or BSON(spec).decode().get("name") != name:
        raise ArchiveBlocked("Archive collection metadata changed or exceeds its bound.")
    for row in indexes:
        BSON(row).decode()
    packed = BSON.encode({"indexes": [RawBSONDocument(row) for row in indexes]})
    return {"name": name, "kind": kind, "spec": spec, "indexes": packed,
            "spec_sha256": _digest(spec), "indexes_sha256": _digest(packed)}


def _same_metadata(first, second):
    return all(first[key] == second[key] for key in ("name", "kind", "spec", "indexes"))


def _manifest_hash(rows):
    return _digest(json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())


def _validate_source(source):
    if not isinstance(source, PinnedMongoSource):
        raise ArchiveBlocked("A pinned archive snapshot source is required.")
    source.ensure_active()
    evidence = source.evidence
    barrier = evidence.get("majority_write_barrier") or {}
    if (evidence.get("inventory_scope") != "explicit_archive_inventory" or evidence.get("snapshot_session") is not True
            or evidence.get("read_concern") != "snapshot" or evidence.get("subsequent_data_finds_use_same_explicit_timestamp") is not True
            or barrier.get("acknowledged") is not True or barrier.get("kind") != "majority_write_barrier"
            or any(barrier.get(key) is not True for key in ("application_writers_stopped_asserted",
                       "application_background_writers_stopped_asserted", "ddl_stopped_asserted"))):
        raise ArchiveBlocked("Archive copy requires an explicit inventory and majority application/background/DDL barrier.")
    return deepcopy(source.view_identity)


def _source_binding(source, source_alias):
    """Bind a trusted logical key to the actual database and verified origin."""
    database = validate_name(source.name)
    alias = validate_name(database if source_alias is None else source_alias)
    transport = source.evidence.get("source_transport") or {}
    endpoint_hash = transport.get("configured_endpoints_sha256")
    if (not isinstance(endpoint_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", endpoint_hash)
            or not (transport.get("verified_tls") is True
                    or transport.get("explicit_loopback_plaintext_fixture") is True)):
        raise ArchiveBlocked("The archive source origin has no verified transport identity.")
    return {"source_alias": alias, "source_database": database,
            "source_transport": {key: transport[key] for key in (
                "verified_tls", "explicit_loopback_plaintext_fixture", "configured_endpoints_sha256")}}


def _initialize(cursor):
    cursor.execute("""SELECT CURRENT_USER=SESSION_USER,rolsuper OR oid=(SELECT datdba FROM pg_catalog.pg_database
        WHERE datname=current_database()),rolname FROM pg_catalog.pg_roles WHERE rolname=CURRENT_USER""")
    same, owner, role = cursor.fetchone()
    if not same or not owner or role.startswith("imladri_app_"):
        raise ArchiveBlocked("Legacy archives require a dedicated database owner connection.")
    cursor.execute("""SELECT c.relname,EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a
        WHERE a.attrelid=c.oid AND a.attname='source_alias' AND a.attnotnull AND NOT a.attisdropped),
        (SELECT array_agg(a.attname ORDER BY k.position) FROM pg_catalog.pg_constraint p
        CROSS JOIN LATERAL unnest(p.conkey) WITH ORDINALITY k(attnum,position)
        JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
        WHERE p.conrelid=c.oid AND p.contype='p')
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='imladri_archive' AND c.relname IN ('collections','documents','receipts')""")
    existing = cursor.fetchall()
    keys = {"collections": ["namespace", "source_alias", "name"],
            "documents": ["namespace", "source_alias", "collection", "id_key"],
            "receipts": ["namespace", "source_alias"]}
    if existing and (len(existing) != 3 or not all(row[1] and row[2] == keys[row[0]] for row in existing)):
        raise ArchiveBlocked("Existing archives require an explicit source-key schema review; no automatic rewrite is allowed.")
    if existing:
        _validate_foreign_key(cursor)
    for statement in SCHEMA:
        cursor.execute(statement)
    if not existing:
        _validate_foreign_key(cursor)
    cursor.execute("""SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname LIKE 'imladri_app_%'
        AND (has_schema_privilege(r.oid,'imladri_archive','USAGE,CREATE') OR EXISTS(
        SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='imladri_archive' AND has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))))""")
    if cursor.fetchone()[0]:
        raise ArchiveBlocked("An application login can access the owner archive; explicit remediation is required.")


def _validate_foreign_key(cursor):
    cursor.execute("""SELECT f.convalidated,f.condeferrable,f.condeferred,
        (SELECT array_agg(a.attname ORDER BY k.position) FROM unnest(f.conkey) WITH ORDINALITY k(attnum,position)
         JOIN pg_catalog.pg_attribute a ON a.attrelid=f.conrelid AND a.attnum=k.attnum),
        n.nspname,c.relname,
        (SELECT array_agg(a.attname ORDER BY k.position) FROM unnest(f.confkey) WITH ORDINALITY k(attnum,position)
         JOIN pg_catalog.pg_attribute a ON a.attrelid=f.confrelid AND a.attnum=k.attnum),
        f.confupdtype,f.confdeltype,f.confmatchtype
        FROM pg_catalog.pg_constraint f JOIN pg_catalog.pg_class c ON c.oid=f.confrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE f.conrelid='imladri_archive.documents'::regclass AND f.contype='f'""")
    expected = (True, False, False, ["namespace", "source_alias", "collection"],
                "imladri_archive", "collections", ["namespace", "source_alias", "name"], "a", "a", "s")
    rows = cursor.fetchall()
    if len(rows) != 1 or tuple(rows[0]) != expected:
        raise ArchiveBlocked("The archive source-key foreign key is missing or changed; no automatic repair is allowed.")


def _target_manifest(cursor, alias, database, budget):
    for table in TABLES:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM imladri_archive." + table +
            " WHERE namespace=%s AND source_alias=%s AND source_database<>%s)", (ARCHIVE_NAMESPACE, alias, database))
        if cursor.fetchone()[0]:
            raise ArchiveBlocked("The archive source alias contains a different actual database.")
    cursor.execute("""SELECT count(*),COALESCE(sum(octet_length(spec_bson)+octet_length(indexes_bson)),0)
        FROM imladri_archive.collections WHERE namespace=%s AND source_alias=%s""", (ARCHIVE_NAMESPACE, alias))
    count, metadata_bytes = cursor.fetchone()
    if count > 2048 or metadata_bytes > budget.max_metadata_bytes:
        raise ArchiveBlocked("Archived catalog inventory exceeds its bounded verification budget.")
    cursor.execute("""SELECT count(*),COALESCE(sum(octet_length(raw_bson)),0),COALESCE(max(octet_length(raw_bson)),0),
        COALESCE(sum(octet_length(raw_bson)+octet_length(id_bson)+octet_length(canonical_json)+COALESCE(octet_length(query_document::text),0)),0)
        FROM imladri_archive.documents WHERE namespace=%s AND source_alias=%s""", (ARCHIVE_NAMESPACE, alias))
    records, source_bytes, largest, projected_bytes = cursor.fetchone()
    if records * 2 > budget.max_records or source_bytes * 2 > budget.max_bytes or largest > 16 * 1024 * 1024:
        raise ArchiveBlocked("Archived BSON exceeds its bounded verification budget.")
    if projected_bytes + metadata_bytes > budget.max_target_bytes:
        raise ArchiveBlocked("Archived analysis projections exceed their bounded verification budget.")
    budget.verified_target_bytes = max(budget.verified_target_bytes, int(projected_bytes + metadata_bytes))
    cursor.execute("""SELECT name,kind,spec_bson,spec_sha256,indexes_bson,indexes_sha256,record_count,fingerprint
        FROM imladri_archive.collections WHERE namespace=%s AND source_alias=%s ORDER BY name""", (ARCHIVE_NAMESPACE, alias))
    manifests = []
    for name, kind, spec, spec_hash, indexes, indexes_hash, count, expected in cursor.fetchall():
        budget.check()
        spec, indexes = bytes(spec), bytes(indexes)
        if _digest(spec) != spec_hash or _digest(indexes) != indexes_hash:
            raise ArchiveBlocked("Archived catalog bytes no longer match their receipt.")
        BSON(spec).decode(); BSON(indexes).decode()
        cursor.execute("""DECLARE imladri_archive_verify NO SCROLL CURSOR FOR
            SELECT id_key,id_bson,raw_bson,canonical_json,query_document::text,source_sha256 FROM imladri_archive.documents
            WHERE namespace=%s AND source_alias=%s AND collection=%s ORDER BY id_key""", (ARCHIVE_NAMESPACE, alias, name))
        actual_count, total, unavailable = 0, 0, 0
        while True:
            # A server cursor avoids buffering an entire large legacy collection
            # in pg8000 before our byte checks can inspect it.
            cursor.execute("FETCH FORWARD 1 FROM imladri_archive_verify")
            rows = cursor.fetchall()
            if not rows:
                break
            for key, identity, payload, canonical, projection, digest in rows:
                budget.check()
                identity, payload = bytes(identity), bytes(payload)
                document = RawBSONDocument(payload)
                if _digest(payload) != digest or _digest(identity) != key or document_key(document) != key \
                        or BSON.encode({"_id": document["_id"]}) != identity:
                    raise ArchiveBlocked("Archived BSON identity or record bytes no longer match their receipt.")
                projected = jsonb_text(document)
                if canonical != canonical_document(document) or (projection is None) != (projected is None) \
                        or (projection is not None and json.loads(projection) != json.loads(projected)):
                    raise ArchiveBlocked("Archived analysis projections differ from authoritative BSON.")
                unavailable += int(projected is None)
                actual_count += 1
                total = fingerprint_add(total, key, digest)
        cursor.execute("CLOSE imladri_archive_verify")
        actual = fingerprint_finish(total, actual_count)
        if actual_count != count or actual != expected:
            raise ArchiveBlocked("Archived counts or record fingerprint changed.")
        manifests.append({"name": name, "kind": kind, "records": count, "fingerprint": actual,
                          "spec_sha256": spec_hash, "indexes_sha256": indexes_hash,
                          "jsonb_unavailable_records": unavailable})
    if sum(row["records"] for row in manifests) != records:
        raise ArchiveBlocked("Uncataloged archive records cannot be omitted from verification.")
    # Source manifests use Python's exact Unicode code-point ordering. The
    # owner's PostgreSQL database may use ICU/en_US collation, where underscores,
    # case and accents sort differently. Preserve every verified field and use
    # the same explicit name order before source/receipt equality comparison.
    return sorted(manifests, key=lambda row: row["name"])


class _Budget:
    def __init__(self, max_records, max_bytes, max_seconds, max_metadata_bytes, max_target_bytes):
        if (type(max_records) is not int or not 1 <= max_records <= 1000000 or type(max_bytes) is not int
                or not 1 <= max_bytes <= 1024 * 1024 * 1024 or type(max_seconds) is not int or not 1 <= max_seconds <= 600):
            raise ArchiveBlocked("Archive budgets are invalid.")
        self.max_records, self.max_bytes = max_records, max_bytes
        self.max_metadata_bytes = max_metadata_bytes
        self.max_target_bytes = max_target_bytes
        self.target_bytes = self.verified_target_bytes = 0
        self.records, self.bytes, self.deadline = 0, 0, time.monotonic() + max_seconds
    def check(self, payload=None):
        if time.monotonic() >= self.deadline:
            raise ArchiveBlocked("The bounded archive deadline expired.")
        if payload is not None:
            self.records += 1
            self.bytes += len(payload)
            if self.records > self.max_records or self.bytes > self.max_bytes or len(payload) > 16 * 1024 * 1024:
                raise ArchiveBlocked("The aggregate source archive budget was exceeded.")
    def target(self, count):
        self.target_bytes += count
        if self.target_bytes > self.max_target_bytes:
            raise ArchiveBlocked("The archive analysis projection payload budget was exceeded.")


def _recovery_link(value, alias, database):
    if value is None:
        return None
    required = {"version", "kind", "source_alias", "source_database", "operation_id", "original_archive_id",
        "original_archive_request_sha256", "original_archive_state_sha256", "prior_rehearsal_id",
        "prior_rehearsal_request_sha256", "prior_rehearsal_state_sha256"}
    try:
        valid = (isinstance(value, dict) and set(value) == required and type(value["version"]) is int
            and value["version"] == 1 and value["kind"] == "manual_archive_recovery"
            and alias in {"recovery_production", "recovery_staging"}
            and value["source_alias"] == alias and value["source_database"] == database)
        identities = [value[key] for key in ("operation_id", "original_archive_id", "prior_rehearsal_id")]
        valid = valid and len(set(identities)) == 3 and all(isinstance(item, str) and str(UUID(item)) == item for item in identities)
        valid = valid and all(isinstance(value[key], str) and re.fullmatch(r"[a-f0-9]{64}", value[key])
            for key in required if key.endswith("_sha256"))
    except (TypeError, ValueError, KeyError, AttributeError):
        valid = False
    if not valid:
        raise ArchiveBlocked("The explicit archive recovery linkage is invalid.")
    return deepcopy(value)


async def archive_source(source, connection, *, source_alias=None, max_records=200000, max_source_bytes=256 * 1024 * 1024,
                         max_seconds=500, max_metadata_bytes=2 * 1024 * 1024,
                         max_total_metadata_bytes=16 * 1024 * 1024,
                         max_target_payload_bytes=512 * 1024 * 1024, manual_recovery=None):
    """Atomic all-collection archive with two pinned source and two actual target passes.

    Source record/byte limits count BOTH passes. Metadata is outside the data
    snapshot and therefore requires the explicit DDL barrier plus byte comparison.
    A replay returns the ORIGINAL immutable receipt and fresh verification facts.
    """
    identity = _validate_source(source)
    binding = _source_binding(source, source_alias)
    database, alias = binding["source_database"], binding["source_alias"]
    recovery_link = _recovery_link(manual_recovery, alias, database)
    binding_hash = _manifest_hash(binding)
    if type(max_total_metadata_bytes) is not int or not 1 <= max_total_metadata_bytes <= 64 * 1024 * 1024:
        raise ArchiveBlocked("Aggregate archive metadata budget is invalid.")
    if type(max_target_payload_bytes) is not int or not 1 <= max_target_payload_bytes <= 1024 * 1024 * 1024:
        raise ArchiveBlocked("Archive analysis projection payload budget is invalid.")
    budget = _Budget(max_records, max_source_bytes, max_seconds, max_total_metadata_bytes, max_target_payload_bytes)
    if type(max_metadata_bytes) is not int or not 1 <= max_metadata_bytes <= 16 * 1024 * 1024:
        raise ArchiveBlocked("Archive metadata budget is invalid.")
    stage = "source_inventory"
    names = sorted(await source.list_collection_names())
    if not names or len(names) > 2048 or len(set(names)) != len(names):
        raise ArchiveBlocked("Archive inventory is empty, duplicated or exceeds its bound.", stage=stage)
    for name in names:
        _name(name)
    inventory_hash = _digest(json.dumps(names, separators=(",", ":")).encode())
    if source.evidence.get("allowlist_inventory_fingerprint") != inventory_hash:
        raise ArchiveBlocked("The source inventory differs from its reviewed snapshot allowlist.", stage=stage)
    cursor = connection.cursor()
    try:
        stage = "target_schema"
        cursor.execute("SET LOCAL statement_timeout='55s'")
        cursor.execute("SET LOCAL lock_timeout='10s'")
        cursor.execute("SET LOCAL idle_in_transaction_session_timeout='120s'")
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (int.from_bytes(hashlib.sha256((ARCHIVE_NAMESPACE + ':' + alias).encode()).digest()[:8], "big", signed=True),))
        _initialize(cursor)
        cursor.execute("LOCK TABLE imladri_archive.collections,imladri_archive.documents,imladri_archive.receipts IN SHARE ROW EXCLUSIVE MODE")
        stage = "target_existing_receipt"
        cursor.execute("SELECT CASE WHEN octet_length(receipt_bson)<=2097152 THEN receipt_bson END,receipt_sha256 FROM imladri_archive.receipts WHERE namespace=%s AND source_alias=%s", (ARCHIVE_NAMESPACE, alias))
        saved = cursor.fetchone()
        replay, original = saved is not None, None
        if saved:
            if saved[0] is None:
                raise ArchiveBlocked("The immutable archive receipt exceeds its verification bound.")
            receipt_bytes = bytes(saved[0])
            if _digest(receipt_bytes) != saved[1]:
                raise ArchiveBlocked("The immutable archive receipt was changed.")
            original = BSON(receipt_bytes).decode()
            if recovery_link is not None and original.get("manual_recovery") != recovery_link:
                raise ArchiveBlocked("The immutable archive recovery linkage differs.")
            if (original.get("version") != SCHEMA_VERSION or original.get("status") != "verified_legacy_archive"
                    or original.get("schema") != "imladri_archive" or original.get("namespace") != ARCHIVE_NAMESPACE
                    or original.get("source_alias") != alias or original.get("source_binding") != binding
                    or original.get("source_binding_sha256") != binding_hash
                    or original.get("source_database") != database or original.get("source_two_pass_pinned_verified") is not True
                    or original.get("target_two_pass_raw_verified") is not True):
                raise ArchiveBlocked("The immutable archive receipt does not match its fixed target scope.")
        else:
            cursor.execute("SELECT EXISTS(SELECT 1 FROM imladri_archive.collections WHERE namespace=%s AND source_alias=%s) OR EXISTS(SELECT 1 FROM imladri_archive.documents WHERE namespace=%s AND source_alias=%s)", (ARCHIVE_NAMESPACE, alias, ARCHIVE_NAMESPACE, alias))
            if cursor.fetchone()[0]:
                raise ArchiveBlocked("An unreceipted archive target is not empty; it will not be overwritten.")
        stage = "source_metadata"
        metadata, manifests, metadata_bytes = {}, [], 0
        for name in names:
            budget.check()
            metadata[name] = _metadata(await source.collection_metadata(name, max_bytes=max_metadata_bytes), name, max_metadata_bytes)
            metadata_bytes += len(metadata[name]["spec"]) + len(metadata[name]["indexes"])
            budget.target(len(metadata[name]["spec"]) + len(metadata[name]["indexes"]))
            if metadata_bytes > max_total_metadata_bytes:
                raise ArchiveBlocked("Aggregate source catalog metadata exceeds its budget.")
        stage = "first_source_pass"
        for name in names:
            item = metadata[name]
            if item["kind"] == "timeseries":
                bucket = metadata.get("system.buckets." + name)
                if not bucket or bucket["kind"] != "collection":
                    raise ArchiveBlocked("Time-series archive requires its exact physical bucket collection.")
            if not replay:
                cursor.execute("INSERT INTO imladri_archive.collections VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s)",
                    (ARCHIVE_NAMESPACE, alias, database, name, item["kind"], item["spec"], item["spec_sha256"], item["indexes"], item["indexes_sha256"], "0" * 64))
            count, total, unavailable = 0, 0, 0
            if item["kind"] == "collection":
                rows = source[name].find({})
                try:
                    async for document in rows:
                        payload = raw_document(document)
                        budget.check(payload)
                        key, digest = document_key(document), _digest(payload)
                        id_bson = BSON.encode({"_id": document["_id"]})
                        canonical, projection = canonical_document(document), jsonb_text(document)
                        unavailable += int(projection is None)
                        budget.target(len(payload) + len(id_bson) + len(canonical.encode())
                                      + (len(projection.encode()) if projection is not None else 0))
                        if not replay:
                            cursor.execute("INSERT INTO imladri_archive.documents VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
                                (ARCHIVE_NAMESPACE, alias, database, name, key, id_bson, payload, canonical, projection, digest))
                        count += 1; total = fingerprint_add(total, key, digest)
                finally:
                    await rows.close()
            fingerprint = fingerprint_finish(total, count)
            manifests.append({"name": name, "kind": item["kind"], "records": count, "fingerprint": fingerprint,
                              "spec_sha256": item["spec_sha256"], "indexes_sha256": item["indexes_sha256"],
                              "jsonb_unavailable_records": unavailable})
            if not replay:
                cursor.execute("UPDATE imladri_archive.collections SET record_count=%s,fingerprint=%s WHERE namespace=%s AND source_alias=%s AND name=%s", (count, fingerprint, ARCHIVE_NAMESPACE, alias, name))
        stage = "second_source_pass"
        for manifest in manifests:
            name, count, total = manifest["name"], 0, 0
            if manifest["kind"] == "collection":
                rows = source[name].find({})
                try:
                    async for document in rows:
                        payload = raw_document(document); budget.check(payload)
                        count += 1; total = fingerprint_add(total, document_key(document), _digest(payload))
                finally:
                    await rows.close()
            if count != manifest["records"] or fingerprint_finish(total, count) != manifest["fingerprint"]:
                raise ArchiveBlocked("The pinned source data changed between complete archive passes.")
            latest = _metadata(await source.collection_metadata(name, max_bytes=max_metadata_bytes), name, max_metadata_bytes)
            if not _same_metadata(metadata[name], latest):
                raise ArchiveBlocked("Source collection/index metadata changed during the archive.")
        if sorted(await source.list_collection_names()) != names or source.view_identity != identity or _source_binding(source, alias) != binding:
            raise ArchiveBlocked("The pinned archive inventory or view identity changed.")
        stage = "target_verification"
        for _ in range(2):
            if _target_manifest(cursor, alias, database, budget) != manifests:
                raise ArchiveBlocked("The complete target archive differs from its pinned source.")
        stage = "receipt_finalize"
        manifest_hash = _manifest_hash({"source_binding": binding, "collections": manifests})
        if original:
            if original.get("manifest_sha256") != manifest_hash or original.get("collections") != manifests:
                raise ArchiveBlocked("A different source cannot overwrite the immutable verified archive.")
            receipt = original
        else:
            receipt = {"version": SCHEMA_VERSION, "status": "verified_legacy_archive", "schema": "imladri_archive",
                "namespace": ARCHIVE_NAMESPACE, "source_alias": alias, "source_database": database,
                "source_binding": binding, "source_binding_sha256": binding_hash, "source_view": deepcopy(source.evidence),
                "collections": manifests, "manifest_sha256": manifest_hash, "collection_count": len(names),
                "physical_records": sum(row["records"] for row in manifests),
                "jsonb_unavailable_records": sum(row["jsonb_unavailable_records"] for row in manifests),
                "virtual_definitions": sum(row["kind"] != "collection" for row in manifests),
                "virtual_collections_materialized": False, "metadata_outside_snapshot": True,
                "source_two_pass_pinned_verified": True, "target_two_pass_raw_verified": True,
                "active_workflows_created": False, "research_consent_inferred": False,
                "analysis_text_and_optional_jsonb_verified": True,
                "logical_target_payload_bytes": budget.target_bytes,
                "verified_target_payload_bytes": budget.verified_target_bytes,
                "physical_pg_disk_bytes_measured": False, "exact_wire_bytes_measured": False,
                "completed_at": datetime.now(timezone.utc).isoformat()}
            if recovery_link is not None:
                receipt["manual_recovery"] = recovery_link
            encoded = BSON.encode(receipt)
            cursor.execute("INSERT INTO imladri_archive.receipts(namespace,source_alias,source_database,receipt_bson,receipt_sha256) VALUES(%s,%s,%s,%s,%s)",
                           (ARCHIVE_NAMESPACE, alias, database, encoded, _digest(encoded)))
        budget.check(); source.ensure_active()
        stage = "commit"
        connection.commit()
        return {"receipt": receipt, "replay_verified": replay, "receipt_unchanged": replay,
                "verification_source_view": deepcopy(source.view_identity), "source_records_read": budget.records,
                "source_bson_bytes_read": budget.bytes, "source_write_operations": 0, "active_application_records_written": 0,
                "logical_target_payload_bytes": budget.target_bytes, "verified_target_payload_bytes": budget.verified_target_bytes}
    except BaseException as exc:
        connection.rollback()
        if isinstance(exc, ArchiveBlocked):
            if exc.stage == "validate":
                exc.stage = stage
            raise
        if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        raise ArchiveBlocked("The owner-only legacy archive outcome requires explicit review.", stage=stage) from None
    finally:
        cursor.close()
