"""Protected SELECT-only archive diagnosis; never retries an unknown archive.

Only fixed-schema structure, owner checks, counts and fingerprints leave this
module. Source verification uses a current server-selected read-only snapshot;
it creates no metadata barrier and makes no final writer-fence/parity claim.
"""
import asyncio
import hashlib
import json
import re

from bson import BSON

from . import postgres_archive as archive
from .postgres_copy_diagnostics import safe_error


def _catalog_collation_order(options, names):
    """Measure database-default ordering of literal source names, SELECT only."""
    import pg8000.dbapi
    if (not isinstance(names, list) or not 1 <= len(names) <= 2048
            or any(not isinstance(name, str) or not name or len(name.encode()) > 1024 for name in names)
            or len(set(names)) != len(names)):
        raise ValueError("Bounded catalog names required.")
    connection = pg8000.dbapi.connect(**{**options, "timeout": 20,
        "application_name": "imladri-archive-collation-readonly"})
    cursor = connection.cursor()
    try:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute("SET LOCAL statement_timeout='20s'")
        cursor.execute("SET LOCAL lock_timeout='10s'")
        cursor.execute("SELECT CURRENT_USER=SESSION_USER,rolsuper OR oid=(SELECT datdba FROM pg_database "
            "WHERE datname=current_database()),rolname LIKE 'imladri_app_%' FROM pg_roles WHERE rolname=CURRENT_USER")
        same, owner, app_role = cursor.fetchone()
        if not same or not owner or app_role:
            raise ValueError("Dedicated owner required.")
        cursor.execute("SELECT datlocprovider,datcollate,to_jsonb(d)->>'datlocale',"
            "to_jsonb(d)->>'daticulocale' FROM pg_database d WHERE datname=current_database()")
        provider, collate, locale, prior_locale = cursor.fetchone()
        if provider not in ("i", "c", "b") or any(value is not None and
                (not isinstance(value, str) or len(value.encode()) > 256) for value in (collate, locale, prior_locale)):
            raise ValueError("Unsupported collation metadata.")
        cursor.execute("SELECT name FROM unnest(%s::text[]) AS name ORDER BY name", (names,))
        ordered = [row[0] for row in cursor.fetchall()]
        if sorted(ordered) != sorted(names):
            raise ValueError("Catalog order input changed.")
        return {"complete": True, "transaction_read_only": True, "database_locale_provider": provider,
            "database_collate": collate, "database_locale": locale or prior_locale,
            "collection_count": len(names), "database_default_order_sha256": archive._manifest_hash(ordered),
            "codepoint_order_sha256": archive._manifest_hash(sorted(names)),
            "database_order_differs_from_codepoint": ordered != sorted(names), "record_values_read": False}
    finally:
        connection.rollback(); cursor.close(); connection.close()


def _owner_state(options, alias, database):
    import pg8000.dbapi
    connection = pg8000.dbapi.connect(**{**options, "timeout": 20,
        "application_name": "imladri-archive-state-readonly"})
    cursor = connection.cursor()
    try:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute("SET LOCAL statement_timeout='20s'")
        cursor.execute("SET LOCAL lock_timeout='10s'")
        cursor.execute("SELECT CURRENT_USER=SESSION_USER,rolsuper OR oid=(SELECT datdba FROM pg_database "
            "WHERE datname=current_database()),rolname LIKE 'imladri_app_%' FROM pg_roles WHERE rolname=CURRENT_USER")
        same, owner, app_role = cursor.fetchone()
        result = {"transaction_read_only": True, "dedicated_database_owner": bool(same and owner and not app_role),
                  "source_alias": alias, "source_database": database, "schema_exists": False, "tables": {}}
        cursor.execute("SELECT to_regnamespace('imladri_archive') IS NOT NULL")
        result["schema_exists"] = cursor.fetchone()[0]
        if not result["dedicated_database_owner"]:
            result.update(schema_assessment="dedicated_owner_requires_review", existing_schema_validation={"complete": False,
                **archive.safe_archive_error(archive.ArchiveBlocked(
                    "Legacy archives require a dedicated database owner connection.", stage="target_schema"))})
            return result
        if not result["schema_exists"]:
            result.update(schema_assessment="absent", existing_schema_validation={"complete": True, "existing_schema_absent": True})
            return result
        cursor.execute("SELECT c.relname,c.relkind,pg_get_userbyid(c.relowner)=CURRENT_USER "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='imladri_archive' AND c.relname IN ('collections','documents','receipts') ORDER BY c.relname")
        tables = cursor.fetchall()
        for name, kind, owned in tables:
            cursor.execute("SELECT a.attname,a.attnotnull,format_type(a.atttypid,a.atttypmod) FROM pg_attribute a "
                "WHERE a.attrelid=('imladri_archive.' || %s)::regclass AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum", (name,))
            columns = cursor.fetchall()
            cursor.execute("SELECT array_agg(a.attname::text ORDER BY k.position) FROM pg_constraint p "
                "CROSS JOIN LATERAL unnest(p.conkey) WITH ORDINALITY k(attnum,position) "
                "JOIN pg_attribute a ON a.attrelid=p.conrelid AND a.attnum=k.attnum "
                "WHERE p.conrelid=('imladri_archive.' || %s)::regclass AND p.contype='p' GROUP BY p.oid", (name,))
            primary = cursor.fetchall()
            row = {"relation_kind": kind, "owned_by_current_role": bool(owned), "columns": [list(v) for v in columns],
                   "primary_keys": [v[0] for v in primary]}
            names = {v[0] for v in columns}
            if kind in ("r", "p"):
                cursor.execute("SELECT count(*) FROM imladri_archive." + name)
                row["all_source_row_count"] = int(cursor.fetchone()[0])
                if {"namespace", "source_alias", "source_database"} <= names:
                    cursor.execute("SELECT count(*) FROM imladri_archive." + name +
                        " WHERE namespace=%s AND source_alias=%s", (archive.ARCHIVE_NAMESPACE, alias))
                    row["selected_alias_row_count"] = int(cursor.fetchone()[0])
                    cursor.execute("SELECT count(*) FROM imladri_archive." + name +
                        " WHERE namespace=%s AND source_alias=%s AND source_database<>%s", (archive.ARCHIVE_NAMESPACE, alias, database))
                    row["selected_alias_other_database_rows"] = int(cursor.fetchone()[0])
            result["tables"][name] = row
        cursor.execute("SELECT count(*) FROM pg_roles r WHERE r.rolname LIKE 'imladri_app_%' AND "
            "(has_schema_privilege(r.oid,'imladri_archive','USAGE,CREATE') OR EXISTS(SELECT 1 FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='imladri_archive' "
            "AND has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')))")
        result["application_roles_with_archive_privilege_count"] = int(cursor.fetchone()[0])
        expected_keys = {"collections": ["namespace", "source_alias", "name"],
            "documents": ["namespace", "source_alias", "collection", "id_key"], "receipts": ["namespace", "source_alias"]}
        valid_keys = len(tables) == 3 and all(row["primary_keys"] == [expected_keys[name]]
            and any(c[0] == "source_alias" and c[1] is True for c in row["columns"])
            for name, row in result["tables"].items())
        result["schema_assessment"] = "v2_keys_present" if valid_keys else "requires_explicit_source_key_schema_review"
        if "documents" in result["tables"]:
            cursor.execute("SELECT convalidated,condeferrable,condeferred,pg_get_constraintdef(oid) FROM pg_constraint "
                           "WHERE conrelid='imladri_archive.documents'::regclass AND contype='f' ORDER BY oid")
            foreign_keys = cursor.fetchall()
            result["foreign_keys"] = [{"validated": bool(v[0]), "deferrable": bool(v[1]), "initially_deferred": bool(v[2]),
                                       "definition_sha256": hashlib.sha256(v[3].encode()).hexdigest()} for v in foreign_keys]
        # Run the exact SELECT-only gate, never _initialize/SCHEMA/CREATE/REVOKE.
        try:
            if not result["dedicated_database_owner"]:
                raise archive.ArchiveBlocked("Legacy archives require a dedicated database owner connection.", stage="target_schema")
            if not valid_keys:
                raise archive.ArchiveBlocked("Existing archives require an explicit source-key schema review; no automatic rewrite is allowed.", stage="target_schema")
            archive._validate_foreign_key(cursor)
            if result["application_roles_with_archive_privilege_count"]:
                raise archive.ArchiveBlocked("An application login can access the owner archive; explicit remediation is required.", stage="target_schema")
            result["existing_schema_validation"] = {"complete": True, "source_key_schema_verified": True}
        except archive.ArchiveBlocked as error:
            error.stage = "target_schema"
            result["existing_schema_validation"] = {"complete": False, **archive.safe_archive_error(error)}
        receipts = result["tables"].get("receipts", {})
        if {"namespace", "source_alias", "receipt_bson", "receipt_sha256"} <= {v[0] for v in receipts.get("columns", [])}:
            cursor.execute("SELECT count(*) FROM imladri_archive.receipts WHERE namespace=%s AND source_alias=%s",
                           (archive.ARCHIVE_NAMESPACE, alias))
            result["selected_alias_receipt_count"] = int(cursor.fetchone()[0])
            cursor.execute("SELECT CASE WHEN octet_length(receipt_bson)<=2097152 THEN receipt_bson END,receipt_sha256,octet_length(receipt_bson) "
                "FROM imladri_archive.receipts WHERE namespace=%s AND source_alias=%s LIMIT 2", (archive.ARCHIVE_NAMESPACE, alias))
            rows = cursor.fetchall()
            result["selected_alias_receipts_read"] = len(rows)
            result["selected_alias_receipt_inventory_truncated"] = result["selected_alias_receipt_count"] > len(rows)
            result["selected_alias_receipts"] = []
            for payload, digest, size in rows:
                item = {"bytes": int(size), "stored_sha256": digest if isinstance(digest, str)
                    and re.fullmatch(r"[a-f0-9]{64}", digest) else None, "within_read_bound": payload is not None}
                if payload is not None:
                    raw = bytes(payload); value = BSON(raw).decode()
                    item.update(actual_sha256=hashlib.sha256(raw).hexdigest(), stored_hash_matches=hashlib.sha256(raw).hexdigest() == digest,
                        version=value.get("version") if type(value.get("version")) is int else None,
                        status_matches=value.get("status") == "verified_legacy_archive",
                        source_alias_matches=value.get("source_alias") == alias, source_database_matches=value.get("source_database") == database,
                        collection_count=value.get("collection_count") if type(value.get("collection_count")) is int else None,
                        physical_record_count=value.get("physical_records") if type(value.get("physical_records")) is int else None)
                result["selected_alias_receipts"].append(item)
        result["schema_metadata_sha256"] = hashlib.sha256(json.dumps(result["tables"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return result
    finally:
        connection.rollback(); cursor.close(); connection.close()


async def _source_preflight(view, names):
    from .postgres_migration import (raw_document, document_key, fingerprint_add, fingerprint_finish,
                                     canonical_document, jsonb_text)
    budget = archive._Budget(100000, 64 * 1024 * 1024, 300, 16 * 1024 * 1024, 256 * 1024 * 1024)
    metadata, first = {}, []
    metadata_size = 0
    for name in names:
        item = archive._metadata(await view.collection_metadata(name), name, 2 * 1024 * 1024)
        metadata_size += len(item["spec"]) + len(item["indexes"])
        if metadata_size > 16 * 1024 * 1024:
            raise archive.ArchiveBlocked("Aggregate source catalog metadata exceeds its budget.", stage="source_metadata")
        metadata[name] = item
    for pass_number in range(2):
        manifests = []
        for name in names:
            item, count, total, unavailable = metadata[name], 0, 0, 0
            if item["kind"] == "timeseries" and ("system.buckets." + name not in metadata
                    or metadata["system.buckets." + name]["kind"] != "collection"):
                raise archive.ArchiveBlocked("Time-series archive requires its exact physical bucket collection.", stage="source_metadata")
            if item["kind"] == "collection":
                rows = view[name].find({})
                try:
                    async for document in rows:
                        raw = raw_document(document); budget.check(raw)
                        count += 1; total = fingerprint_add(total, document_key(document), hashlib.sha256(raw).hexdigest())
                        canonical, projection = canonical_document(document), jsonb_text(document)
                        budget.target(len(canonical.encode()) + (len(projection.encode()) if projection is not None else 0))
                        unavailable += int(projection is None)
                finally:
                    await rows.close()
            manifests.append({"collection_name_sha256": hashlib.sha256(name.encode()).hexdigest(), "kind": item["kind"],
                "records": count, "fingerprint": fingerprint_finish(total, count), "jsonb_unavailable_records": unavailable,
                "spec_sha256": item["spec_sha256"], "indexes_sha256": item["indexes_sha256"]})
            latest = archive._metadata(await view.collection_metadata(name), name, 2 * 1024 * 1024)
            if not archive._same_metadata(item, latest):
                raise archive.ArchiveBlocked("Source collection/index metadata changed during the archive.", stage="second_source_pass")
        if pass_number == 0:
            first = manifests
        elif manifests != first:
            raise archive.ArchiveBlocked("The pinned source data changed between complete archive passes.", stage="second_source_pass")
    if sorted(await view.list_collection_names()) != names:
        raise archive.ArchiveBlocked("The pinned archive inventory or view identity changed.", stage="second_source_pass")
    return {"complete": True, "collection_count": len(names), "physical_records": sum(v["records"] for v in first),
        "physical_records_read_including_both_passes": budget.records, "source_bson_bytes_read": budget.bytes,
        "current_pinned_two_pass_verified": True, "metadata_outside_snapshot": True, "collections": first,
        "manifest_sha256": archive._manifest_hash(first), "final_writer_fence_or_archive_parity_claimed": False}


async def inspect_archive_state(config, request, journal, *, mongo_factory, inventory):
    from .postgres_operator import ARCHIVE_SOURCES, require
    from .postgres_snapshot import pinned_source
    logical = request["source"]
    require(logical in ARCHIVE_SOURCES, "fixed_archive_source_required_for_diagnostic")
    require(request.get("failed_archive_id"), "failed_archive_reference_required")
    prior = journal.lookup(request["failed_archive_id"])
    require(prior and prior["state"] == "unknown" and prior["command"] == "archive_legacy" and prior["source"] == logical,
            "failed_archive_reference_scope_or_state_mismatch")
    database = config["sources"][logical]["database"]
    result = {"diagnostic_only": True, "source_alias": logical, "source_database": database,
        "linked_prior_unknown": True, "prior_intent_modified": False, "source_barrier_created": False,
        "source_application_write_operations": 0, "target_application_write_operations": 0,
        "final_writer_fence_or_archive_parity_claimed": False}
    try:
        result["target"] = await asyncio.to_thread(_owner_state, config["owner_options"], logical, database)
    except Exception as error:
        result["target"] = {"complete": False, **safe_error(error)}
    client = None
    try:
        client, source, _ = mongo_factory(config, logical)
        names = sorted(await inventory(source))
        async with pinned_source(source, collection_allowlist=names, max_seconds=300) as view:
            result["source"] = await _source_preflight(view, names)
            result["source_view_sha256"] = archive._manifest_hash(view.view_identity)
        try:
            result["catalog_collation"] = await asyncio.to_thread(_catalog_collation_order, config["owner_options"], names)
        except Exception as error:
            result["catalog_collation"] = {"complete": False, **safe_error(error)}
    except Exception as error:
        result["source"] = {"complete": False, **safe_error(error)}
    finally:
        if client is not None:
            client.close()
    return result
