"""Exact index BSON survives JSONB; legacy metadata never infers lost ordering."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from uuid import uuid4

from bson import BSON, Int64, ObjectId, SON
from pymongo import IndexModel
from pymongo.errors import DuplicateKeyError, OperationFailure
import pytest

from backend.postgres_store import (PostgresDatabase, IndexMetadataRepairRequired,
    encode_index_definition, decode_index_definition, index_definitions_equal)


DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
MONGO = os.getenv("IMLADRI_DOCUMENT_TEST_MONGO_URL") or os.getenv("IMLADRI_TEST_MONGO_URL")
integration = pytest.mark.skipif(not DSN, reason="Needs isolated loopback PostgreSQL.")


def definition():
    return dict(IndexModel([("zulu", 1), ("alpha", -1)], name="nonalphabetical", unique=True,
        partialFilterExpression={"literal": SON([("zulu", Int64(5)),
            ("alpha", datetime(2026, 1, 1)), ("object", ObjectId("123456789012345678901234"))])}).document)


def test_complete_BSON_metadata_survives_JSONB_order_normalization():
    before = definition()
    encoded = encode_index_definition(before)
    saved = json.loads(json.dumps(encoded, sort_keys=True))
    after = decode_index_definition(saved)
    assert BSON.encode(after) == BSON.encode(before)
    assert list(after["key"]) == ["zulu", "alpha"]
    assert list(after["partialFilterExpression"]["literal"]) == ["zulu", "alpha", "object"]
    assert type(after["partialFilterExpression"]["literal"]["zulu"]) is Int64


@pytest.mark.parametrize("change", ["legacy", "hash", "format", "missing", "oversize"])
def test_unproven_or_invalid_metadata_requires_explicit_reconciliation_without_payload(change):
    encoded = encode_index_definition(definition())
    if change == "legacy": encoded = {"name": "old", "key": {"alpha": 1, "zulu": 1}}
    if change == "hash": encoded["sha256"] = "0" * 64
    if change == "format": encoded["format"] = "unknown"
    if change == "missing": encoded.pop("bson_base64")
    if change == "oversize": encoded["bson_base64"] = "a" * 100000
    with pytest.raises(IndexMetadataRepairRequired) as caught:
        decode_index_definition(encoded)
    assert str(caught.value) == "Repository index metadata requires explicit owner reconciliation."


def test_conflict_comparison_preserves_compound_order_literal_order_and_BSON_types():
    original = definition()
    assert index_definitions_equal(original, dict(reversed(list(original.items()))))
    changed = deepcopy(original)
    changed["key"] = dict(reversed(list(changed["key"].items())))
    assert not index_definitions_equal(original, changed)
    changed = deepcopy(original)
    changed["partialFilterExpression"]["literal"] = dict(reversed(list(changed["partialFilterExpression"]["literal"].items())))
    assert not index_definitions_equal(original, changed)
    changed = deepcopy(original)
    changed["partialFilterExpression"]["literal"]["zulu"] = 5
    assert not index_definitions_equal(original, changed)


def test_copy_index_fingerprint_preserves_embedded_literal_order_and_BSON_types():
    from backend.postgres_cutover import _index_definitions, index_definitions_fingerprint
    def normalized(value):
        value = deepcopy(value)
        value.pop("name")
        value["key"] = list(value["key"].items())
        return _index_definitions({"_id_": {"key": [("_id", 1)]}, "named": value})
    original = definition()
    before = index_definitions_fingerprint(normalized(original))
    changed = deepcopy(original)
    changed["partialFilterExpression"]["literal"] = dict(reversed(list(changed["partialFilterExpression"]["literal"].items())))
    assert before != index_definitions_fingerprint(normalized(changed))
    changed = deepcopy(original)
    changed["partialFilterExpression"]["literal"]["zulu"] = 5
    assert before != index_definitions_fingerprint(normalized(changed))
    changed = deepcopy(original)
    changed["key"] = dict(reversed(list(changed["key"].items())))
    assert before != index_definitions_fingerprint(normalized(changed))


def exercise(callback):
    async def run():
        from urllib.parse import urlsplit
        assert urlsplit(DSN).hostname in ("127.0.0.1", "localhost", "::1")
        db = PostgresDatabase(DSN, "fixture_index_bson_" + uuid4().hex, allow_local_plaintext=True)
        other = PostgresDatabase(DSN, db.name, allow_local_plaintext=True)
        try:
            await callback(db, other)
        finally:
            def cleanup(cursor, _now):
                for table in ("unique_keys", "indexes", "documents", "collections"):
                    cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (db.name,))
            await db._run(cleanup, write="__cleanup")
            await db.aclose()
            await other.aclose()
    asyncio.run(run())


@integration
def test_actual_PostgreSQL_metadata_reopens_with_nonalpha_compound_and_partial_BSON():
    async def run(db, other):
        model = IndexModel([("zulu", 1), ("alpha", -1)], name="nonalphabetical", unique=True,
            partialFilterExpression=definition()["partialFilterExpression"])
        await db.docs.create_indexes([model])
        actual = (await other.docs.index_information())["nonalphabetical"]
        assert actual["key"] == [("zulu", 1), ("alpha", -1)]
        assert BSON.encode(actual["partialFilterExpression"]) == BSON.encode(model.document["partialFilterExpression"])
        await other.docs.create_indexes([model])
        with pytest.raises(OperationFailure, match="conflicts"):
            await other.docs.create_indexes([IndexModel([("alpha", -1), ("zulu", 1)], name="nonalphabetical", unique=True,
                partialFilterExpression=definition()["partialFilterExpression"])])
        literal = definition()["partialFilterExpression"]["literal"]
        await db.docs.insert_one({"_id": "first", "zulu": 1, "alpha": 2, "literal": literal})
        with pytest.raises(DuplicateKeyError):
            await other.docs.insert_one({"_id": "duplicate", "zulu": 1, "alpha": 2, "literal": literal})
        # A distinct literal object ordering does not satisfy this exact BSON predicate.
        await other.docs.insert_one({"_id": "outside_partial", "zulu": 1, "alpha": 2,
                                    "literal": dict(reversed(list(literal.items())))})
        assert await db.docs.count_documents({}) == 2
    exercise(run)


@integration
def test_legacy_JSON_metadata_blocks_reads_writes_and_maintenance_without_rewriting_records():
    async def run(db, other):
        await db.docs.insert_one({"_id": "existing", "zulu": 1, "alpha": 2})
        def legacy(cursor, _now):
            cursor.execute("INSERT INTO imladri_app.indexes(namespace,collection,name,definition) VALUES(%s,%s,%s,%s::jsonb)",
                (db.name, "docs", "legacy", json.dumps({"key": {"zulu": 1, "alpha": -1}, "name": "legacy", "unique": True})))
        await db._run(legacy, write="docs")
        with pytest.raises(IndexMetadataRepairRequired):
            await other.docs.index_information()
        with pytest.raises(IndexMetadataRepairRequired):
            await other.docs.insert_one({"_id": "rejected", "zulu": 3, "alpha": 4})
        with pytest.raises(IndexMetadataRepairRequired):
            await db.maintenance_once()
        assert await db.docs.find_one({"_id": "rejected"}) is None
        assert await db.docs.count_documents({}) == 1
    exercise(run)


@integration
def test_TTL_worker_finds_encoded_metadata_and_keeps_future_and_nondates():
    async def run(db, other):
        await db.docs.create_index("expires", expireAfterSeconds=0)
        now = datetime.now(timezone.utc)
        await db.docs.insert_many([{"_id": "past", "expires": now - timedelta(hours=1)},
            {"_id": "future", "expires": now + timedelta(hours=1)}, {"_id": "string", "expires": "past"}])
        await other.maintenance_once()
        assert {row["_id"] for row in await db.docs.find({}).to_list(None)} == {"future", "string"}
    exercise(run)


@pytest.mark.skipif(not DSN or not MONGO, reason="Needs explicit loopback MongoDB and PostgreSQL.")
def test_actual_Mongo_nonalpha_partial_index_copies_with_exact_definition_fingerprint():
    from motor.motor_asyncio import AsyncIOMotorClient
    from backend.postgres_cutover import copy_frozen
    from urllib.parse import urlsplit
    assert urlsplit(MONGO).hostname in ("127.0.0.1", "localhost", "::1")
    async def run(db, _other):
        client = AsyncIOMotorClient(MONGO, serverSelectionTimeoutMS=3000)
        source = client["fixture_index_diff_" + uuid4().hex]
        try:
            model = IndexModel([("zulu", 1), ("alpha", -1)], name="nonalphabetical", unique=True,
                partialFilterExpression={"literal": SON([("zulu", Int64(5)), ("alpha", datetime(2026, 1, 1))])})
            await source.users.create_indexes([model])
            await source.users.insert_one({"_id": "first", "zulu": 1, "alpha": 2,
                                          "literal": model.document["partialFilterExpression"]["literal"]})
            freeze = {"source": {"backend": "mongodb", "namespace": source.name},
                "target": {"backend": "postgresql", "namespace": db.name},
                "source_writers_stopped": True, "target_writers_stopped": True,
                "source_background_writers_stopped": True, "target_background_writers_stopped": True,
                "evidence_reference": "explicit-local-index-fixture", "valid_until": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
            receipt = await copy_frozen(source, db, freeze=freeze)
            assert receipt["source_before"] == receipt["target_after"]
            expected = (await source.users.index_information())["nonalphabetical"]
            actual = (await db.users.index_information())["nonalphabetical"]
            assert actual["key"] == expected["key"] == [("zulu", 1), ("alpha", -1)]
            assert BSON.encode(actual["partialFilterExpression"]) == BSON.encode(expected["partialFilterExpression"])
        finally:
            await client.drop_database(source.name)
            client.close()
    exercise(run)
