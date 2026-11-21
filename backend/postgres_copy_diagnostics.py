"""Fixed, bounded copy diagnostics: SELECT-only target and read-only snapshots.

Never invokes copy_frozen, creates a Mongo majority-write barrier, initializes
DDL, rewrites a source identity, or changes an earlier operation journal entry.
Only aggregate counts/fingerprints and explicitly whitelisted static error codes
leave this module. Source sampling is diagnostic, not final freeze/parity proof.
"""
import hashlib
import json


# These messages are production code constants in postgres_cutover.py. Never
# serialize an arbitrary exception string, even for a similarly named class.
FROZEN_COPY_REASONS = {
    "A bounded freeze evidence reference is required.": "freeze_reference_required",
    "A document exceeds the reviewed reverse-compatible byte limit.": "document_byte_limit",
    "A final pinned copy requires a majority metadata barrier bound to the application writer fence.": "majority_barrier_required",
    "A frozen conversion must have one MongoDB and one PostgreSQL endpoint.": "endpoint_backend_pair_invalid",
    "A non-opaque history tombstone blocks the copy.": "history_tombstone_not_opaque",
    "A nonempty target requires the exact completed prior-copy receipt.": "target_nonempty_without_receipt",
    "A pinned source still requires explicit application/background and DDL writer barriers.": "pinned_source_writer_fence_required",
    "A read-only pinned source cannot be a copy destination.": "pinned_source_cannot_be_destination",
    "A reverse-compatible document bound cannot exceed MongoDB's limit.": "reverse_document_limit_invalid",
    "A source BSON record cannot be preserved exactly by this driver.": "source_BSON_not_exactly_roundtrippable",
    "All copy limits must be positive integers.": "copy_limits_not_positive",
    "An existing irreversible history tombstone is absent or resurrected in the source.": "history_tombstone_missing_or_resurrected",
    "An inconsistent answer-history identity or alias blocks the copy.": "history_identity_inconsistent",
    "An index key has no reviewed PostgreSQL equivalent.": "index_key_unsupported",
    "An index option has no reviewed PostgreSQL equivalent.": "index_option_unsupported",
    "An unclassified collection blocks the frozen copy.": "collection_unclassified",
    "Application writers on both endpoints must be explicitly stopped.": "application_writer_fence_required",
    "Background writers, including TTL and history recovery, must also be explicitly stopped.": "background_writer_fence_required",
    "Collection inventory exceeds the reviewed copy bounds.": "collection_inventory_limit",
    "Copied target does not match the complete frozen source; completion is withheld.": "copied_target_parity_failed",
    "Copy exceeds its reviewed write bound.": "copy_write_limit",
    "Copy would exceed its reviewed write bound.": "copy_planned_write_limit",
    "Duplicate source identity encountered.": "duplicate_source_identity",
    "Explicit bounded copy limits are required.": "copy_limits_required",
    "Explicit freeze evidence is required.": "freeze_evidence_required",
    "Freeze endpoint identities do not match the supplied repositories.": "freeze_endpoint_mismatch",
    "Freeze evidence expired or lacks a time zone.": "freeze_expired_or_timezone_missing",
    "Freeze evidence requires an explicit UTC expiry.": "freeze_expiry_invalid",
    "Frozen copy did not complete; target may be partial and requires explicit operator review.": "copy_incomplete_target_requires_review",
    "Frozen source changed during copying; completion is withheld.": "source_changed_during_copy",
    "Invalid index constraint flag.": "index_constraint_flag_invalid",
    "Invalid repository index metadata.": "index_metadata_invalid",
    "Nondefault collection options have no reviewed PostgreSQL equivalent.": "collection_options_unsupported",
    "Post-copy verification would exceed the reviewed total scan-byte bound.": "planned_verification_scan_limit",
    "Repository index metadata is unavailable.": "index_metadata_unavailable",
    "Select an explicitly reviewed application copy scope.": "application_copy_scope_invalid",
    "Source and target must be distinct repositories.": "source_target_identical",
    "Source changed between frozen preflight passes; no target writes were made.": "source_changed_before_target_write",
    "Source/target scan exceeds the reviewed record or byte limits.": "snapshot_record_or_byte_limit",
    "Target changed during frozen preflight; no target writes were made.": "target_changed_before_write",
    "Target index definitions conflict with the frozen source.": "target_index_conflict",
    "The source identity constraint is missing.": "source_identity_constraint_missing",
    "The target contains a collection absent from the frozen source.": "target_has_extra_collection",
    "The target no longer matches the exact completed prior copy.": "target_changed_since_prior_receipt",
    "Unexpected implicit identity index.": "implicit_identity_index_invalid",
}


def safe_error(error):
    from .postgres_cutover import FrozenCopyBlocked
    from .postgres_index_reconciliation import IndexReconciliationBlocked, safe_reconciliation_error
    from .postgres_archive import ArchiveBlocked, safe_archive_error
    if isinstance(error, ArchiveBlocked):
        return safe_archive_error(error)
    if isinstance(error, IndexReconciliationBlocked):
        return safe_reconciliation_error(error)
    result = {"error_type": type(error).__name__}
    if isinstance(error, FrozenCopyBlocked):
        message = error.args[0] if len(error.args) == 1 else None
        result["reason_code"] = (FROZEN_COPY_REASONS.get(message, "unclassified_frozen_copy_block")
                                 if isinstance(message, str) else "unclassified_frozen_copy_block")
    return result


def _digest(value):
    from .postgres_cutover import _digest as bson_digest
    return bson_digest(value)


def _role_counts(options, namespace):
    import pg8000.dbapi
    from .postgres_access import NAMESPACE_TABLES, verify_runtime
    connection = pg8000.dbapi.connect(**{**options, "timeout": 20,
        "application_name": "imladri-copy-state-readonly"})
    cursor = connection.cursor()
    try:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute("SET LOCAL statement_timeout='20s'")
        cursor.execute("SET LOCAL lock_timeout='10s'")
        role = verify_runtime(cursor, namespace, expected_role=options["user"])
        counts = {}
        for table in NAMESPACE_TABLES:
            cursor.execute("SELECT count(*) FROM imladri_app." + table + " WHERE namespace=%s", (namespace,))
            counts[table] = int(cursor.fetchone()[0])
        cursor.execute("SELECT collection,count(*) FROM imladri_app.documents WHERE namespace=%s GROUP BY collection ORDER BY collection", (namespace,))
        collections = [(str(name), int(count)) for name, count in cursor.fetchall()]
        cursor.execute("SELECT name FROM imladri_app.collections WHERE namespace=%s ORDER BY name", (namespace,))
        catalog = [row[0] for row in cursor.fetchall()]
        cursor.execute("SELECT collection,name,definition FROM imladri_app.indexes WHERE namespace=%s ORDER BY collection,name", (namespace,))
        indexes = cursor.fetchall()
        return {"table_row_counts": counts, "namespace_completely_empty": not any(counts.values()),
            "document_collection_counts_sha256": _digest(collections),
            "collection_catalog_entries": len(catalog), "collection_catalog_sha256": _digest(catalog),
            "index_catalog_entries": len(indexes), "index_catalog_sha256": _digest(indexes),
            "runtime_role_check_sha256": _digest(role), "forced_RLS_verified": True,
            "transaction_read_only": True, "row_values_or_ids_returned": False}
    finally:
        connection.rollback()
        cursor.close()
        connection.close()


async def _preflight(repository, application):
    from .postgres_cutover import CopyLimits, snapshot
    try:
        result = await snapshot(repository, limits=CopyLimits(), budget={"scanned_bytes": 0},
                                freeze=None, retain_records=False, application=application)
        return {"complete": True, "manifest": result.manifest}
    except Exception as error:
        return {"complete": False, **safe_error(error)}


async def _history_counts(view, names):
    from bson import BSON
    counts = {"records": 0, "identity_consistent": 0, "identity_inconsistent": 0,
              "opaque_tombstones": 0, "nonopaque_tombstones": 0}
    if "answer_history" not in names:
        return counts
    rows = view["answer_history"].find({})
    size = 0
    try:
        async for row in rows:
            raw = getattr(row, "raw", None) or BSON.encode(row)
            size += len(raw)
            counts["records"] += 1
            if counts["records"] > 100000 or size > 64 * 1024 * 1024:
                raise RuntimeError("history_diagnostic_bound")
            consistent = isinstance(row.get("id"), str) and row.get("_id") == row.get("id")
            counts["identity_consistent" if consistent else "identity_inconsistent"] += 1
            if row.get("purged") is True:
                opaque = set(row) <= {"_id", "id", "purged", "purged_at"}
                counts["opaque_tombstones" if opaque else "nonopaque_tombstones"] += 1
        return counts
    finally:
        await rows.close()


async def inspect_copy_state(config, request, journal, *, mongo_factory, inventory):
    import asyncio
    from .postgres_operator import NAMESPACES, MARKET_SOURCES, SOURCE_DATABASES, require
    from .postgres_snapshot import pinned_source
    from .postgres_store import PostgresDatabase
    logical = request["source"]
    require(logical in NAMESPACES or logical in MARKET_SOURCES, "active_source_required_for_copy_diagnostic")
    prior = None
    if request.get("failed_copy_id"):
        prior = journal.lookup(request["failed_copy_id"])
        expected = "copy_personal" if logical in NAMESPACES else "copy_market"
        require(prior and prior["state"] == "unknown" and prior["source"] == logical
                and prior["command"] == expected, "failed_copy_reference_scope_or_state_mismatch")
    application = "personal" if logical in NAMESPACES else "market"
    namespace = SOURCE_DATABASES[logical]
    roles = [logical] if logical in NAMESPACES else ["market_api", "market_worker"]
    require(all(role in config["role_options"] and role in config["role_dsns"] for role in roles),
            "scoped_application_roles_required_for_readonly_copy_diagnostic")
    result = {"diagnostic_only": True, "source_alias": logical, "target_namespace": namespace,
        "linked_prior_unknown": prior is not None, "prior_intent_modified": False,
        "source_barrier_created": False, "source_application_write_operations": 0,
        "target_application_write_operations": 0, "final_freeze_or_parity_claimed": False, "target": {}}
    for role in roles:
        try:
            result["target"][role] = await asyncio.to_thread(_role_counts, config["role_options"][role], namespace)
        except Exception as error:
            result["target"][role] = {"counts_complete": False, **safe_error(error)}
    target = PostgresDatabase(config["role_dsns"][roles[0]], namespace, initialize_schema=False)
    try:
        result["target_preflight"] = await _preflight(target, application)
    finally:
        await target.aclose()
    client = None
    try:
        client, source, _ = mongo_factory(config, logical)
        names = await inventory(source)
        async with pinned_source(source, max_seconds=300, collection_allowlist=names) as view:
            catalog = []
            for name in names:
                metadata = await view.collection_metadata(name)
                catalog.append({"collection_name_sha256": hashlib.sha256(name.encode()).hexdigest(),
                    "spec_sha256": hashlib.sha256(metadata["spec_bson"]).hexdigest(),
                    "index_catalog_sha256": _digest(metadata["indexes_bson"]),
                    "index_count": len(metadata["indexes_bson"]), "kind": metadata["data_kind"]})
            result["source_catalog"] = {"collection_count": len(catalog), "entries": catalog,
                                         "catalog_sha256": _digest(catalog)}
            result["source_view_sha256"] = _digest(view.view_identity)
            result["source_history_identity_counts"] = await _history_counts(view, names)
            result["source_preflight"] = await _preflight(view, application)
    except Exception as error:
        result["source_preflight"] = {"complete": False, **safe_error(error)}
    finally:
        if client is not None:
            client.close()
    return result
