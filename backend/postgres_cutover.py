"""Explicitly frozen, bounded application copies and reverse reconciliation.

This module does not stop writers, change routing or obtain credentials. The
operator must supply current application-writer barriers for BOTH endpoints.
Pinned snapshot mode permits underlying MongoDB TTL writes; its logical view is
immutable and separately attested. Observed parity never proves a writer fence.
A failed partial copy has no valid completion receipt and is not automatically
overwritten on a later invocation.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import re

from bson import BSON, json_util
from bson.codec_options import CodecOptions
from bson.raw_bson import RawBSONDocument
from pymongo import IndexModel

from .postgres_migration import document_key, validate_name
from .postgres_copy_registry import application_collections
from .postgres_snapshot import PinnedMongoSource


class FrozenCopyBlocked(RuntimeError):
    """A bounded, sanitized failure; never includes document values or credentials."""


@dataclass(frozen=True)
class CopyLimits:
    max_records: int = 100000
    max_bytes: int = 64 * 1024 * 1024
    max_document_bytes: int = 16 * 1024 * 1024
    max_collections: int = 128
    max_total_scanned_bytes: int = 384 * 1024 * 1024
    max_write_operations: int = 200000


@dataclass
class FrozenSnapshot:
    manifest: dict
    records: dict
    indexes: dict


def _require(value, message):
    if not value:
        raise FrozenCopyBlocked(message)


def _digest(value):
    return hashlib.sha256(json_util.dumps(value, json_options=json_util.CANONICAL_JSON_OPTIONS,
                                         sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _identity(db):
    identity = _storage_identity(db)
    return {**identity, **db.view_identity} if isinstance(db, PinnedMongoSource) else identity


def _storage_identity(db):
    return {"backend": getattr(type(db), "backend", "mongodb"), "namespace": validate_name(db.name)}


def _freeze(freeze, source, target):
    _require(isinstance(freeze, dict), "Explicit freeze evidence is required.")
    _require(freeze.get("source") == _identity(source) and freeze.get("target") == _identity(target),
             "Freeze endpoint identities do not match the supplied repositories.")
    _require(freeze.get("source_writers_stopped") is True and freeze.get("target_writers_stopped") is True,
             "Application writers on both endpoints must be explicitly stopped.")
    for prefix, endpoint in (("source", source), ("target", target)):
        mode = freeze.get(prefix + "_data_mode", "frozen_background")
        if isinstance(endpoint, PinnedMongoSource):
            _require(mode == "pinned_snapshot" and freeze.get(prefix + "_application_background_writers_stopped") is True
                     and freeze.get(prefix + "_ddl_stopped") is True,
                     "A pinned source still requires explicit application/background and DDL writer barriers.")
            endpoint.ensure_active()
            barrier = endpoint.evidence["majority_write_barrier"]
            _require(barrier is not None and barrier.get("fence_reference") == freeze["evidence_reference"],
                     "A final pinned copy requires a majority metadata barrier bound to the application writer fence.")
        else:
            _require(mode == "frozen_background" and freeze.get(prefix + "_background_writers_stopped") is True,
                     "Background writers, including TTL and history recovery, must also be explicitly stopped.")
    _require(isinstance(freeze.get("evidence_reference"), str)
             and bool(re.fullmatch(r"[A-Za-z0-9_.:/\\-]{1,240}", freeze["evidence_reference"])),
             "A bounded freeze evidence reference is required.")
    try:
        deadline = datetime.fromisoformat(freeze["valid_until"])
    except (ValueError, TypeError, KeyError):
        raise FrozenCopyBlocked("Freeze evidence requires an explicit UTC expiry.") from None
    _require(deadline.tzinfo is not None and deadline > datetime.now(timezone.utc),
             "Freeze evidence expired or lacks a time zone.")
    _require(_storage_identity(source) != _storage_identity(target), "Source and target must be distinct repositories.")


def _limits(limits):
    _require(isinstance(limits, CopyLimits), "Explicit bounded copy limits are required.")
    _require(all(type(value) is int and value > 0 for value in asdict(limits).values()),
             "All copy limits must be positive integers.")
    _require(limits.max_document_bytes <= 16 * 1024 * 1024, "A reverse-compatible document bound cannot exceed MongoDB's limit.")


def _index_definitions(metadata):
    _require(isinstance(metadata, dict), "Repository index metadata is unavailable.")
    normalized = {}
    for name, raw in metadata.items():
        _require(isinstance(name, str) and isinstance(raw, dict), "Invalid repository index metadata.")
        # Version, namespace and historical background-build flags describe the
        # source engine, not the key/constraint/retention definition.
        options = {key: value for key, value in raw.items() if key not in {"v", "ns", "background"}}
        _require(not set(options) - {"key", "unique", "sparse", "partialFilterExpression", "expireAfterSeconds"},
                 "An index option has no reviewed PostgreSQL equivalent.")
        keys = options.get("key")
        keys = list(keys.items()) if isinstance(keys, dict) else keys
        _require(isinstance(keys, (list, tuple)) and bool(keys)
                 and all(isinstance(pair, (list, tuple)) and len(pair) == 2 and isinstance(pair[0], str)
                         and type(pair[1]) is int and pair[1] in (-1, 1) for pair in keys),
                 "An index key has no reviewed PostgreSQL equivalent.")
        definition = {"key": [[field, direction] for field, direction in keys]}
        for option in ("unique", "sparse"):
            if options.get(option) is True:
                definition[option] = True
            elif option in options:
                _require(options[option] is False, "Invalid index constraint flag.")
        if name == "_id_":
            _require(definition["key"] == [["_id", 1]], "Unexpected implicit identity index.")
            definition["unique"] = True
        for option in ("partialFilterExpression", "expireAfterSeconds"):
            if option in options:
                definition[option] = options[option]
        normalized[name] = definition
    _require("_id_" in normalized, "The source identity constraint is missing.")
    return normalized


def _manifest_fingerprint(manifest):
    return _digest({key: value for key, value in manifest.items() if key != "fingerprint"})


def index_definitions_fingerprint(definitions):
    """Hash exact nested BSON/type/order; only the index-name map is sorted."""
    # _index_definitions fixes root option order and represents compound keys
    # as ordered pairs. Sorting JSON recursively would erase literal-object
    # predicate order, which changes Mongo equality and partial uniqueness.
    return hashlib.sha256(BSON.encode({"indexes": {name: definitions[name] for name in sorted(definitions)}})).hexdigest()


async def snapshot(source, *, limits, budget, freeze=None, other=None, retain_records=False, application="personal"):
    """Read all named collections; never silently omit an unrecognized collection."""
    _limits(limits)
    try:
        allowlist = application_collections(application)
    except ValueError:
        raise FrozenCopyBlocked("Select an explicitly reviewed application copy scope.") from None
    names = sorted(await source.list_collection_names())
    _require(len(names) <= limits.max_collections and len(names) == len(set(names)),
             "Collection inventory exceeds the reviewed copy bounds.")
    _require(not set(names) - allowlist, "An unclassified collection blocks the frozen copy.")
    records, indexes, collections = {}, {}, {}
    count = total_bytes = 0
    for name in names:
        if freeze is not None:
            _freeze(freeze, source, other)
        options = await source[name].options()
        _require(isinstance(options, dict) and not options,
                 "Nondefault collection options have no reviewed PostgreSQL equivalent.")
        definitions = _index_definitions(await source[name].index_information())
        indexes[name] = definitions
        cursor_source = (source.get_collection(name, codec_options=CodecOptions(document_class=RawBSONDocument))
                         if _identity(source)["backend"] == "mongodb" else source[name])
        fingerprints, stored, size = {}, [], 0
        cursor = cursor_source.find({})
        try:
            async for document in cursor:
                if freeze is not None:
                    _freeze(freeze, source, other)
                raw = document.raw if isinstance(document, RawBSONDocument) else BSON.encode(document)
                _require(len(raw) <= limits.max_document_bytes, "A document exceeds the reviewed reverse-compatible byte limit.")
                decoded = BSON(raw).decode()
                _require(BSON.encode(decoded) == raw, "A source BSON record cannot be preserved exactly by this driver.")
                if name == "answer_history":
                    _require(isinstance(decoded.get("id"), str) and decoded.get("_id") == decoded["id"],
                             "An inconsistent answer-history identity or alias blocks the copy.")
                    if decoded.get("purged") is True:
                        _require(set(decoded) <= {"_id", "id", "purged", "purged_at"},
                                 "A non-opaque history tombstone blocks the copy.")
                key = document_key(decoded)
                _require(key not in fingerprints, "Duplicate source identity encountered.")
                size += len(raw)
                total_bytes += len(raw)
                count += 1
                budget["scanned_bytes"] = budget.get("scanned_bytes", 0) + len(raw)
                _require(count <= limits.max_records and total_bytes <= limits.max_bytes
                         and budget["scanned_bytes"] <= limits.max_total_scanned_bytes,
                         "Source/target scan exceeds the reviewed record or byte limits.")
                fingerprints[key] = hashlib.sha256(raw).hexdigest()
                if retain_records:
                    stored.append(raw)
        finally:
            await cursor.close()
        records[name] = stored
        collections[name] = {"records": len(fingerprints), "bson_bytes": size,
            "identities_and_bson_fingerprint": _digest(sorted(fingerprints.items())),
            "collection_options_fingerprint": _digest(options),
            "index_definitions_fingerprint": index_definitions_fingerprint(definitions)}
    manifest = {"collections": collections, "record_count": count, "bson_bytes": total_bytes,
        "index_definition_scope": "Operational keys, uniqueness, sparse/partial predicates and TTL; engine version/background metadata excluded."}
    manifest["fingerprint"] = _manifest_fingerprint(manifest)
    return FrozenSnapshot(manifest, records, indexes)


def _same(left, right):
    return left.manifest == right.manifest and left.indexes == right.indexes


def _expected_target(receipt, identity, manifest, application):
    # The original frozen Mongo source can later be the rollback destination.
    # A completed receipt attests that endpoint's unchanged source-before state
    # as well as its equal target-after state. Neither endpoint may have drifted.
    previous = (receipt.get("target_after") if isinstance(receipt, dict) and receipt.get("target") == identity
                else receipt.get("source_before") if isinstance(receipt, dict)
                and receipt.get("source_storage", receipt.get("source")) == identity else None)
    _require(isinstance(receipt, dict) and receipt.get("status") == "completed_frozen_copy"
             and receipt.get("application", "personal") == application
             and previous == manifest
             and receipt.get("receipt_fingerprint") == _digest({key: value for key, value in receipt.items() if key != "receipt_fingerprint"}),
             "The target no longer matches the exact completed prior copy.")


async def copy_frozen(source, target, *, freeze, limits=CopyLimits(), expected_target=None, application="personal"):
    """Copy current frozen state, or reverse it, without switching application routing.

For rollback after PostgreSQL writes, source MUST be the newly frozen current
PostgreSQL repository. The former MongoDB target must still match a supplied
completed-copy receipt. This reconciles changed and deleted records, including
irreversible history tombstones, rather than restoring an old source snapshot.
"""
    _limits(limits)
    _freeze(freeze, source, target)
    _require(not isinstance(target, PinnedMongoSource), "A read-only pinned source cannot be a copy destination.")
    _require({_identity(source)["backend"], _identity(target)["backend"]} == {"mongodb", "postgresql"},
             "A frozen conversion must have one MongoDB and one PostgreSQL endpoint.")
    budget = {"scanned_bytes": 0, "write_operations": 0}
    first = await snapshot(source, limits=limits, budget=budget, freeze=freeze, other=target, retain_records=True,
                           application=application)
    second = await snapshot(source, limits=limits, budget=budget, freeze=freeze, other=target, application=application)
    _require(_same(first, second), "Source changed between frozen preflight passes; no target writes were made.")
    target_freeze = {}
    for key, value in freeze.items():
        if key == "source" or key.startswith("source_"):
            target_freeze["target" + key[len("source"):]] = value
        elif key == "target" or key.startswith("target_"):
            target_freeze["source" + key[len("target"):]] = value
        else:
            target_freeze[key] = value
    before = await snapshot(target, limits=limits, budget=budget, freeze=target_freeze, other=source, retain_records=True,
                            application=application)
    before_repeat = await snapshot(target, limits=limits, budget=budget, freeze=target_freeze, other=source,
                                   application=application)
    _require(_same(before, before_repeat), "Target changed during frozen preflight; no target writes were made.")
    if expected_target is None:
        _require(before.manifest["record_count"] == 0, "A nonempty target requires the exact completed prior-copy receipt.")
    else:
        _expected_target(expected_target, _identity(target), before.manifest, application)
    _require(not set(before.indexes) - set(first.indexes), "The target contains a collection absent from the frozen source.")
    for name, definitions in before.indexes.items():
        _require(all(first.indexes[name].get(index) == definition for index, definition in definitions.items()),
                 "Target index definitions conflict with the frozen source.")
    source_fences = {}
    for raw in first.records.get("answer_history", []):
        value = BSON(raw).decode()
        source_fences[document_key(value)] = value
    for raw in before.records.get("answer_history", []):
        existing = BSON(raw).decode()
        if existing.get("purged") is True:
            _require(source_fences.get(document_key(existing), {}).get("purged") is True,
                     "An existing irreversible history tombstone is absent or resurrected in the source.")
    existing_names = set(before.indexes)
    _require(budget["scanned_bytes"] + 2 * first.manifest["bson_bytes"] <= limits.max_total_scanned_bytes,
             "Post-copy verification would exceed the reviewed total scan-byte bound.")
    expected_keys = {name: {document_key(BSON(raw).decode()) for raw in raws} for name, raws in first.records.items()}
    stale_count = sum(document_key(BSON(raw).decode()) not in expected_keys[name]
                      for name, raws in before.records.items() for raw in raws)
    planned_writes = (first.manifest["record_count"] + stale_count + len(set(first.indexes) - existing_names)
                      + sum(any(index != "_id_" for index in definitions) for definitions in first.indexes.values()))
    _require(planned_writes <= limits.max_write_operations, "Copy would exceed its reviewed write bound.")
    try:
        for name in sorted(first.indexes):
            _freeze(freeze, source, target)
            if name not in existing_names:
                budget["write_operations"] += 1
                await target.create_collection(name)
            models = [IndexModel([(field, direction) for field, direction in definition["key"]], name=index,
                                 **{key: value for key, value in definition.items() if key != "key"})
                      for index, definition in first.indexes[name].items() if index != "_id_"]
            if models:
                budget["write_operations"] += 1
                await target[name].create_indexes(models)
            expected_ids = set()
            for raw in first.records[name]:
                _freeze(freeze, source, target)
                document = BSON(raw).decode()
                expected_ids.add(document_key(document))
                budget["write_operations"] += 1
                _require(budget["write_operations"] <= limits.max_write_operations, "Copy exceeds its reviewed write bound.")
                await target[name].replace_one({"_id": document["_id"]}, document, upsert=True)
            for raw in before.records.get(name, []):
                document = BSON(raw).decode()
                if document_key(document) not in expected_ids:
                    _freeze(freeze, source, target)
                    budget["write_operations"] += 1
                    _require(budget["write_operations"] <= limits.max_write_operations, "Copy exceeds its reviewed write bound.")
                    await target[name].delete_one({"_id": document["_id"]})
        final = await snapshot(target, limits=limits, budget=budget, freeze=target_freeze, other=source,
                               application=application)
        unchanged_source = await snapshot(source, limits=limits, budget=budget, freeze=freeze, other=target,
                                          application=application)
        _require(_same(first, unchanged_source), "Frozen source changed during copying; completion is withheld.")
        _require(_same(first, final), "Copied target does not match the complete frozen source; completion is withheld.")
    except FrozenCopyBlocked:
        raise
    except Exception:
        raise FrozenCopyBlocked("Frozen copy did not complete; target may be partial and requires explicit operator review.") from None
    pinned = isinstance(source, PinnedMongoSource)
    result = {"version": 1, "status": "completed_frozen_copy", "application": application,
        "source": _identity(source), "target": _identity(target),
        "source_storage": _storage_identity(source), "source_data_mode": "pinned_snapshot" if pinned else "frozen_background",
        "source_snapshot": source.evidence if pinned else None,
        "freeze_evidence_reference": freeze["evidence_reference"], "freeze_was_explicit_not_inferred": True,
        "source_writers_stopped_asserted": True, "target_writers_stopped_asserted": True,
        "source_writer_fence_scope": "application/background and DDL; underlying native TTL may continue" if pinned else "all source writers",
        "source_application_writers_stopped_asserted": True,
        "background_and_ttl_writers_stopped_asserted": not pinned,
        "native_source_ttl_monitor_stopped": False if pinned else True,
        "source_before": first.manifest, "target_before": before.manifest, "target_after": final.manifest,
        "limits": asdict(limits), "budget": budget, "exact_bson_and_identity_parity": True,
        "operational_index_parity": True, "routing_changed": False, "cutover_performed": False,
        "credentials_or_record_values_included": False}
    result["receipt_fingerprint"] = _digest(result)
    return result
