"""Explicit owner-only index metadata reconciliation; never re-copy app records.

The protected operator supplies a fresh externally verified writer fence and a
linked prior unknown-copy identity. Every current source record must already
match PostgreSQL before any SQL mutation. Exact index metadata and all unique
tokens are then replaced in one transaction, with source and target rechecked
before commit. The original unknown journal entry is never changed here.
"""
from dataclasses import asdict
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import UUID

from bson import BSON
from pymongo import IndexModel

from .postgres_codec import document_key, validate_name
from .postgres_cutover import (CopyLimits, FrozenCopyBlocked, FrozenSnapshot, _digest, _freeze,
    _identity, _index_definitions, _limits, _manifest_fingerprint, _same,
    _storage_identity, index_definitions_fingerprint, snapshot)
from .postgres_snapshot import PinnedMongoSource
from .postgres_store import _unique_value, encode_index_definition, decode_index_definition, unique_tokens


class IndexReconciliationBlocked(RuntimeError):
    """Only static codes/stages may leave the protected operator."""
    def __init__(self, reason_code, stage):
        self.reason_code, self.stage = reason_code, stage
        super().__init__(reason_code)


SAFE_RECONCILIATION_STAGES = frozenset({"validate", "source_preflight", "source_barrier_witness",
    "target_preflight", "atomic_metadata_repair", "target_postflight", "source_postflight", "commit"})
SAFE_RECONCILIATION_REASONS = frozenset({
    "index_metadata_reconciliation_not_completed", "reconciliation_pinned_source_required",
    "reconciliation_failed_copy_identity_invalid", "reconciliation_existing_barrier_witness_invalid",
    "reconciliation_server_majority_snapshot_required", "reconciliation_fresh_writer_fence_required",
    "reconciliation_fresh_writer_fence_expired", "reconciliation_barrier_witness_scan_limit",
    "reconciliation_original_barrier_record_invalid", "reconciliation_original_barrier_record_not_present",
    "reconciliation_dedicated_owner_required", "reconciliation_collection_limit",
    "reconciliation_index_catalog_mismatch", "reconciliation_scan_limit",
    "reconciliation_collection_catalog_mismatch", "reconciliation_target_identity_or_BSON_mismatch",
    "reconciliation_duplicate_identity", "reconciliation_prior_application_RPC_writes_blocked",
    "reconciliation_index_catalog_limit", "reconciliation_token_write_limit",
    "reconciliation_source_preflight_changed", "reconciliation_existing_target_data_not_exact",
    "reconciliation_existing_index_names_not_exact", "reconciliation_target_full_parity_failed",
    "reconciliation_target_document_metadata_changed", "reconciliation_source_changed_before_commit"})


def safe_reconciliation_error(error):
    result = {"error_type": type(error).__name__}
    if isinstance(error, IndexReconciliationBlocked):
        result["reason_code"] = (error.reason_code if error.reason_code in SAFE_RECONCILIATION_REASONS
                                 else "index_metadata_reconciliation_not_completed")
        result["stage"] = error.stage if error.stage in SAFE_RECONCILIATION_STAGES else "unspecified"
    return result


def _require(value, code, stage):
    if not value:
        raise IndexReconciliationBlocked(code, stage)


class _TargetIdentity:
    backend = "postgresql"
    def __init__(self, namespace):
        self.name = validate_name(namespace)


def _data_manifest(manifest):
    return {"collections": {name: {key: value for key, value in data.items()
            if key != "index_definitions_fingerprint"} for name, data in manifest["collections"].items()},
            "record_count": manifest["record_count"], "bson_bytes": manifest["bson_bytes"]}


def _fence(source, target, freeze, witness, failed_copy_id):
    if witness is None:
        _freeze(freeze, source, target)
        return
    stage = "validate"
    source.ensure_active()
    _require(isinstance(witness, dict) and witness.get("kind") == "existing_majority_snapshot_barrier"
        and witness.get("source_namespace") == source.name
        and witness.get("view_identity") == source.view_identity
        and witness.get("linked_failed_copy_id") == failed_copy_id
        and all(isinstance(witness.get(key), str) and re.fullmatch(r"[a-f0-9]{64}", witness[key])
                for key in ("control_record_sha256", "original_request_sha256"))
        and isinstance(witness.get("original_fence_reference"), str)
        and re.fullmatch(r"[A-Za-z0-9_.:/\\-]{1,240}", witness["original_fence_reference"]),
        "reconciliation_existing_barrier_witness_invalid", stage)
    evidence = source.evidence
    _require(evidence["snapshot_session"] is True and evidence["read_concern"] == "snapshot"
        and evidence["server_selected_timestamp"] is True
        and evidence["timestamp_confirmation"] == "returned_atClusterTime",
        "reconciliation_server_majority_snapshot_required", stage)
    _require(isinstance(freeze, dict) and freeze.get("source") == _identity(source)
        and freeze.get("target") == _identity(target)
        and freeze.get("source_data_mode") == "readonly_majority_snapshot_reconciliation"
        and all(freeze.get(key) is True for key in ("source_writers_stopped", "target_writers_stopped",
            "source_application_background_writers_stopped", "source_ddl_stopped", "target_background_writers_stopped")),
        "reconciliation_fresh_writer_fence_required", stage)
    _require(isinstance(freeze.get("evidence_reference"), str)
        and re.fullmatch(r"[A-Za-z0-9_.:/\\-]{1,240}", freeze["evidence_reference"]),
        "reconciliation_fresh_writer_fence_required", stage)
    try:
        until = datetime.fromisoformat(freeze["valid_until"])
    except (KeyError, TypeError, ValueError):
        raise IndexReconciliationBlocked("reconciliation_fresh_writer_fence_expired", stage) from None
    _require(until.tzinfo is not None and until > datetime.now(timezone.utc),
        "reconciliation_fresh_writer_fence_expired", stage)


async def _verify_witness(source, witness, budget, limits):
    if witness is None:
        return
    stage = "source_barrier_witness"
    count, size, matched = 0, 0, 0
    rows = source["database_migration_reports"].find({})
    try:
        async for row in rows:
            raw = getattr(row, "raw", None) or BSON.encode(row)
            count += 1; size += len(raw); budget["scanned_bytes"] += len(raw)
            _require(count <= 1024 and size <= 2 * 1024 * 1024
                and budget["scanned_bytes"] <= limits.max_total_scanned_bytes,
                "reconciliation_barrier_witness_scan_limit", stage)
            if hashlib.sha256(raw).hexdigest() != witness["control_record_sha256"]:
                continue
            value = BSON(raw).decode()
            _require(set(value) == {"_id", "kind", "version", "fence_reference", "committed_at"}
                and isinstance(value.get("_id"), str)
                and re.fullmatch(r"postgres-cutover-barrier:[a-f0-9]{32}", value["_id"])
                and value.get("kind") == "postgres_cutover_majority_barrier" and value.get("version") == 1
                and value.get("fence_reference") == witness["original_fence_reference"]
                and isinstance(value.get("committed_at"), datetime),
                "reconciliation_original_barrier_record_invalid", stage)
            matched += 1
    finally:
        await rows.close()
    _require(matched == 1, "reconciliation_original_barrier_record_not_present", stage)


def _owner_and_locks(cursor, stage):
    # Acquire write exclusion BEFORE the first MVCC query establishes this
    # repeatable-read snapshot. A writer finishing while the lock is acquired
    # must be visible to the subsequent complete target preflight.
    cursor.execute("LOCK TABLE imladri_app.collections, imladri_app.documents, imladri_app.indexes, "
                   "imladri_app.unique_keys, imladri_app.rpc_requests IN SHARE ROW EXCLUSIVE MODE")
    cursor.execute("SELECT current_user=session_user")
    _require(cursor.fetchone()[0] is True, "reconciliation_dedicated_owner_required", stage)
    cursor.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='imladri_app' AND c.relname IN ('collections','documents','indexes','unique_keys','rpc_requests') "
        "AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)")
    _require(cursor.fetchone()[0] == 5, "reconciliation_dedicated_owner_required", stage)
    # This is a bounded maintenance transaction under an explicit writer fence.
    # Locks enforce no SQL writers can invalidate either pre/post verification;
    # no rows in another namespace are read or mutated.


def _target_state(cursor, namespace, limits, budget, *, decode_indexes, retain_documents, stage):
    cursor.execute("SELECT name FROM imladri_app.collections WHERE namespace=%s ORDER BY name", (namespace,))
    names = [row[0] for row in cursor.fetchall()]
    _require(len(names) <= limits.max_collections, "reconciliation_collection_limit", stage)
    cursor.execute("SELECT collection,name,definition FROM imladri_app.indexes WHERE namespace=%s ORDER BY collection,name", (namespace,))
    index_rows = cursor.fetchall()
    _require(len(index_rows) <= 2048 and all(name in names for name, _, _ in index_rows),
             "reconciliation_index_catalog_mismatch", stage)
    definitions = {name: {"_id_": {"key": [("_id", 1)], "unique": True}} for name in names}
    raw_catalog = []
    for collection, name, stored in index_rows:
        stored = json.loads(stored) if isinstance(stored, str) else stored
        raw_catalog.append([collection, name, stored])
        if decode_indexes:
            value = decode_index_definition(stored)
            _require(value["name"] == name, "reconciliation_index_catalog_mismatch", stage)
            value.pop("name")
            value["key"] = list(value["key"].items())
            definitions[collection][name] = value
    fingerprints = {name: {} for name in names}
    documents, metadata = [], []
    sizes = {name: 0 for name in names}
    cursor.execute("SELECT collection,id_key,raw_bson,source_sha256,revision,updated_at FROM imladri_app.documents "
                   "WHERE namespace=%s ORDER BY collection,id_key", (namespace,))
    count = total = 0
    while True:
        rows = cursor.fetchmany(128)
        if not rows:
            break
        for collection, key, payload, stored_hash, revision, updated in rows:
            raw = bytes(payload)
            total += len(raw); count += 1
            budget["scanned_bytes"] += len(raw)
            _require(count <= limits.max_records and total <= limits.max_bytes and len(raw) <= limits.max_document_bytes
                     and budget["scanned_bytes"] <= limits.max_total_scanned_bytes,
                     "reconciliation_scan_limit", stage)
            _require(collection in fingerprints, "reconciliation_collection_catalog_mismatch", stage)
            document = BSON(raw).decode()
            actual_hash = hashlib.sha256(raw).hexdigest()
            _require(BSON.encode(document) == raw and document_key(document) == key and actual_hash == stored_hash,
                     "reconciliation_target_identity_or_BSON_mismatch", stage)
            _require(key not in fingerprints[collection], "reconciliation_duplicate_identity", stage)
            fingerprints[collection][key] = actual_hash
            sizes[collection] += len(raw)
            metadata.append([collection, key, actual_hash, revision, updated])
            if retain_documents:
                documents.append((collection, key, document))
    collections = {name: {"records": len(fingerprints[name]), "bson_bytes": sizes[name],
        "identities_and_bson_fingerprint": _digest(sorted(fingerprints[name].items())),
        "collection_options_fingerprint": _digest({})} for name in names}
    if decode_indexes:
        for name in names:
            definitions[name] = _index_definitions(definitions[name])
            collections[name]["index_definitions_fingerprint"] = index_definitions_fingerprint(definitions[name])
    manifest = {"collections": collections, "record_count": count, "bson_bytes": total,
        "index_definition_scope": "Operational keys, uniqueness, sparse/partial predicates and TTL; engine version/background metadata excluded."}
    if decode_indexes:
        manifest["fingerprint"] = _manifest_fingerprint(manifest)
    cursor.execute("SELECT count(*) FROM imladri_app.rpc_requests WHERE namespace=%s", (namespace,))
    _require(cursor.fetchone()[0] == 0, "reconciliation_prior_application_RPC_writes_blocked", stage)
    return {"manifest": manifest, "documents": documents, "index_names": {(c, n) for c, n, _ in index_rows},
            "index_catalog_sha256": _digest(raw_catalog), "record_metadata_sha256": _digest(metadata),
            "indexes": definitions}


def _replace_metadata_and_tokens(cursor, namespace, definitions, documents, limits, budget):
    stage = "atomic_metadata_repair"
    encoded = []
    for collection in sorted(definitions):
        for name, definition in definitions[collection].items():
            if name == "_id_":
                continue
            model = IndexModel([(field, direction) for field, direction in definition["key"]], name=name,
                               **{key: value for key, value in definition.items() if key != "key"})
            encoded.append((namespace, collection, name, json.dumps(encode_index_definition(dict(model.document)))))
    _require(len(encoded) <= 2048, "reconciliation_index_catalog_limit", stage)
    cursor.execute("DELETE FROM imladri_app.unique_keys WHERE namespace=%s", (namespace,))
    cursor.execute("DELETE FROM imladri_app.indexes WHERE namespace=%s", (namespace,))
    if encoded:
        cursor.executemany("INSERT INTO imladri_app.indexes(namespace,collection,name,definition) VALUES(%s,%s,%s,%s::jsonb)", encoded)
    tokens = []
    for collection, key, document in documents:
        implicit = hashlib.sha256(json.dumps(_unique_value(document["_id"]), separators=(",", ":")).encode()).hexdigest()
        tokens.append((namespace, collection, "_id_", implicit, key))
        for name, definition in definitions[collection].items():
            if name == "_id_" or not definition.get("unique"):
                continue
            operational = {**definition, "key": dict(definition["key"])}
            tokens.extend((namespace, collection, name, token, key) for token in unique_tokens(document, operational))
        _require(len(tokens) + len(encoded) + 2 <= limits.max_write_operations,
                 "reconciliation_token_write_limit", stage)
    if tokens:
        cursor.executemany("INSERT INTO imladri_app.unique_keys(namespace,collection,index_name,token,id_key) VALUES(%s,%s,%s,%s,%s)", tokens)
    budget.update(index_metadata_rows_written=len(encoded), unique_key_rows_rebuilt=len(tokens),
                  application_document_rows_written=0, application_document_rows_deleted=0)


async def reconcile_index_metadata(source, owner_connection, *, namespace, freeze, failed_copy_id,
                                   application="personal", limits=CopyLimits(), reconciliation_witness=None):
    """Return a new full-parity receipt after an atomic metadata-only repair.

The caller must use a separate dedicated owner connection and persist this new
receipt under a new linked operation. Commit ambiguity is an unknown outcome,
never authority to rerun or clear the earlier failed copy.
"""
    stage, cursor = "validate", None
    try:
        _limits(limits)
        _require(isinstance(source, PinnedMongoSource), "reconciliation_pinned_source_required", stage)
        _require(str(UUID(failed_copy_id)) == failed_copy_id, "reconciliation_failed_copy_identity_invalid", stage)
        target = _TargetIdentity(namespace)
        _fence(source, target, freeze, reconciliation_witness, failed_copy_id)
        budget = {"scanned_bytes": 0, "application_document_rows_written": 0}
        stage = "source_preflight"
        snapshot_freeze = freeze if reconciliation_witness is None else None
        first = await snapshot(source, limits=limits, budget=budget, freeze=snapshot_freeze, other=target,
                               application=application)
        repeat = await snapshot(source, limits=limits, budget=budget, freeze=snapshot_freeze, other=target,
                                application=application)
        _require(_same(first, repeat), "reconciliation_source_preflight_changed", stage)
        await _verify_witness(source, reconciliation_witness, budget, limits)
        _fence(source, target, freeze, reconciliation_witness, failed_copy_id)
        stage = "target_preflight"
        cursor = owner_connection.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cursor.execute("SET LOCAL statement_timeout='20s'")
        cursor.execute("SET LOCAL lock_timeout='10s'")
        cursor.execute("SET LOCAL idle_in_transaction_session_timeout='90s'")
        _owner_and_locks(cursor, stage)
        before = _target_state(cursor, namespace, limits, budget, decode_indexes=False,
                               retain_documents=True, stage=stage)
        _require(_data_manifest(first.manifest) == _data_manifest(before["manifest"]),
                 "reconciliation_existing_target_data_not_exact", stage)
        expected_names = {(collection, name) for collection, definitions in first.indexes.items()
                          for name in definitions if name != "_id_"}
        _require(before["index_names"] == expected_names, "reconciliation_existing_index_names_not_exact", stage)
        _fence(source, target, freeze, reconciliation_witness, failed_copy_id)
        stage = "atomic_metadata_repair"
        _replace_metadata_and_tokens(cursor, namespace, first.indexes, before["documents"], limits, budget)
        stage = "target_postflight"
        after = _target_state(cursor, namespace, limits, budget, decode_indexes=True,
                              retain_documents=False, stage=stage)
        _require(first.manifest == after["manifest"] and first.indexes == after["indexes"],
                 "reconciliation_target_full_parity_failed", stage)
        _require(before["record_metadata_sha256"] == after["record_metadata_sha256"],
                 "reconciliation_target_document_metadata_changed", stage)
        stage = "source_postflight"
        final_source = await snapshot(source, limits=limits, budget=budget, freeze=snapshot_freeze, other=target,
                                      application=application)
        _require(_same(first, final_source), "reconciliation_source_changed_before_commit", stage)
        _fence(source, target, freeze, reconciliation_witness, failed_copy_id)
        result = {"version": 1, "status": "completed_frozen_copy", "application": application,
            "kind": "manual_index_metadata_reconciliation", "linked_failed_copy_id": failed_copy_id,
            "prior_unknown_intent_modified": False, "recopy_or_document_replacement_performed": False,
            "source": _identity(source), "target": _identity(target), "source_storage": _storage_identity(source),
            "source_data_mode": ("readonly_majority_snapshot_reconciliation" if reconciliation_witness is not None else "pinned_snapshot"),
            "source_snapshot": source.evidence,
            "existing_majority_snapshot_barrier_witness": reconciliation_witness,
            "new_source_majority_barrier_created": False,
            "freeze_evidence_reference": freeze["evidence_reference"], "freeze_was_explicit_not_inferred": True,
            "source_writers_stopped_asserted": True, "target_writers_stopped_asserted": True,
            "source_application_writers_stopped_asserted": True,
            "source_writer_fence_scope": "application/background and DDL; underlying native TTL may continue",
            "native_source_ttl_monitor_stopped": False, "background_and_ttl_writers_stopped_asserted": False,
            "source_before": first.manifest, "target_before_data_only": _data_manifest(before["manifest"]),
            "target_before_index_catalog_sha256": before["index_catalog_sha256"],
            "target_after": after["manifest"], "target_document_metadata_unchanged": True,
            "exact_bson_and_identity_parity": True, "operational_index_parity": True,
            "all_metadata_and_unique_keys_one_transaction": True, "source_application_write_operations": 0,
            "target_application_document_write_operations": 0,
            "limits": asdict(limits), "budget": budget, "routing_changed": False, "cutover_performed": False,
            "credentials_or_record_values_included": False}
        result["receipt_fingerprint"] = _digest(result)
        stage = "commit"
        owner_connection.commit()
        return result
    except BaseException as error:
        owner_connection.rollback()
        if isinstance(error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        if isinstance(error, IndexReconciliationBlocked):
            raise
        # Never forward arbitrary driver/record/SQL text. A failed or ambiguous
        # commit remains a separate unknown journal outcome for manual review.
        raise IndexReconciliationBlocked("index_metadata_reconciliation_not_completed", stage) from None
    finally:
        if cursor is not None:
            cursor.close()
