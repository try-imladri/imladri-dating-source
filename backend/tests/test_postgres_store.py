"""Real loopback PostgreSQL repository invariants, independent connections."""
import asyncio
from datetime import datetime, timezone
import os
from uuid import uuid4

from bson import BSON, Int64, ObjectId
from pymongo import IndexModel, ReplaceOne, ReturnDocument
from pymongo.errors import DuplicateKeyError
import pytest

from backend.postgres_store import PostgresDatabase, ScanLimitExceeded, candidate_filter


DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
integration = pytest.mark.skipif(not DSN, reason="Needs isolated loopback PostgreSQL.")


def exercise(callback, **options):
    async def run():
        namespace = "fixture_app_" + uuid4().hex
        from urllib.parse import urlsplit
        assert urlsplit(DSN).hostname in ("127.0.0.1", "localhost", "::1")
        db = PostgresDatabase(DSN, namespace, allow_local_plaintext=True, **options)
        other = PostgresDatabase(DSN, namespace, allow_local_plaintext=True, **options)
        try:
            await callback(db, other)
        finally:
            def cleanup(cursor, now):
                for table in ("unique_keys", "indexes", "documents"):
                    cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (namespace,))
            await db._run(cleanup, write="__cleanup")
            await db.aclose()
            await other.aclose()
    asyncio.run(run())


def test_candidate_sql_is_only_an_indexed_superset_and_parameters_are_bound():
    sql, args = candidate_filter({"user_id": "x' OR TRUE --", "count": {"$gt": 1}, "$or": [{"env": "live"}, {"other": None}]})
    assert "x' OR TRUE" not in sql and args[0] == '{"user_id": "x\' OR TRUE --"}'
    assert "OR TRUE" in sql and "'array'" in sql


@integration
def test_exact_bson_partial_multikey_uniqueness_and_rollback():
    async def run(db, other):
        await db.pairs.create_indexes([IndexModel([("env", 1), ("members", 1)], unique=True, name="active_slots",
            partialFilterExpression={"members": {"$exists": True}})])
        original = {"_id": ObjectId(), "env": "live", "members": ["a", "b"], "i": Int64(5), "nul": "a\x00b"}
        await db.pairs.insert_one(original)
        saved = await db.pairs.find_one({"members": "a"})
        assert BSON.encode(saved) == BSON.encode(original)
        with pytest.raises(DuplicateKeyError):
            await other.pairs.insert_one({"_id": "conflict", "env": "live", "members": ["c", "b"]})
        assert await db.pairs.find_one({"_id": "conflict"}) is None
        await other.pairs.insert_one({"_id": "second", "env": "preview", "members": ["a", "b"]})
        await db.pairs.insert_one({"_id": "closed", "env": "live"})
        await db.pairs.insert_one({"_id": "closed2", "env": "live"})
        await db.pairs.update_one({"_id": original["_id"]}, {"$unset": {"members": ""}})
        await other.pairs.insert_one({"_id": "released", "env": "live", "members": ["b"]})
        assert await db.pairs.count_documents({}) == 5
    exercise(run)


@integration
def test_single_index_wrapper_preserves_unique_partial_constraint_and_generated_name():
    async def run(db, other):
        assert await db.docs.create_index("slug", unique=True, partialFilterExpression={"slug": {"$type": "string"}}) == "slug_1"
        await db.docs.insert_one({"_id": "one", "slug": "same"})
        with pytest.raises(DuplicateKeyError):
            await other.docs.insert_one({"_id": "two", "slug": "same"})
        await db.docs.insert_one({"_id": "three"})
        await db.docs.insert_one({"_id": "four"})
    exercise(run)


@integration
def test_bulk_replacement_upsert_returns_exact_counts_and_identifier():
    async def run(db, other):
        result = await db.docs.bulk_write([ReplaceOne({"_id": "new"}, {"_id": "new", "answer": 1}, upsert=True)])
        assert result.upserted_count == 1 and result.upserted_ids == {0: "new"}
        assert result.matched_count == 0 and result.modified_count == 0
        updated = await db.docs.bulk_write([ReplaceOne({"_id": "new"}, {"_id": "new", "answer": 2}, upsert=True)])
        assert updated.upserted_count == 0 and updated.matched_count == 1 and updated.modified_count == 1
        assert (await other.docs.find_one({"_id": "new"}))["answer"] == 2
    exercise(run)


@integration
def test_independent_connections_serialize_absent_upsert_and_conditional_credit_debits():
    async def run(db, other):
        await db.users.create_indexes([IndexModel("user_id", unique=True, name="member")])
        await asyncio.gather(*[(db if i % 2 else other).users.update_one({"user_id": "one"},
            {"$inc": {"visits": 1}, "$setOnInsert": {"credits": 12}}, upsert=True) for i in range(40)])
        assert await db.users.count_documents({}) == 1
        assert (await db.users.find_one({"user_id": "one"}))["visits"] == 40
        async def debit(i):
            store = db if i % 2 else other
            return await store.users.find_one_and_update({"user_id": "one", "credits": {"$gt": 0}},
                [{"$set": {"credits": {"$subtract": ["$credits", 1]}, "charged": {"$add": [{"$ifNull": ["$charged", 0]}, 1]}}}],
                return_document=ReturnDocument.AFTER, projection={"_id": 0, "credits": 1})
        results = await asyncio.gather(*(debit(i) for i in range(30)))
        assert sum(row is not None for row in results) == 12
        saved = await db.users.find_one({"user_id": "one"})
        assert saved["credits"] == 0 and saved["charged"] == 12
    exercise(run)


@integration
def test_cas_guard_tombstone_erasure_and_projection_preserve_missing_vs_null():
    async def run(db, other):
        await db.users.insert_one({"_id": "one", "revision": 1, "nested": {"safe": 1, "secret": 2}, "nullable": None})
        first, second = await asyncio.gather(*[store.users.update_one({"_id": "one", "revision": 1},
            {"$inc": {"revision": 1}, "$push": {"journal": {"id": str(i)}}}) for i, store in enumerate((db, other))])
        assert first.matched_count + second.matched_count == 1
        assert await db.users.count_documents({"absent": {"$exists": False}}) == 1
        assert await db.users.count_documents({"absent": None}) == 1
        assert await db.users.count_documents({"nullable": {"$exists": False}}) == 0
        row = await db.users.find_one({"_id": "one"}, {"_id": 0, "nested.safe": 1})
        assert row == {"nested": {"safe": 1}}
        await db.history.replace_one({"_id": "event"}, {"_id": "event", "purged": True}, upsert=True)
        await other.history.update_one({"_id": "event"}, {"$setOnInsert": {"private": "must not return"}}, upsert=True)
        assert await db.history.find_one({"_id": "event"}) == {"_id": "event", "purged": True}
    exercise(run)


@integration
def test_native_id_uniqueness_numeric_types_and_uncommitted_unique_token_rollback():
    async def run(db, other):
        await db.docs.insert_one({"_id": 3, "value": "keep"})
        with pytest.raises(DuplicateKeyError):
            await other.docs.insert_one({"_id": Int64(3), "value": "reject"})
        assert await db.docs.count_documents({}) == 1
        await db.docs.insert_one({"_id": "3"})
        assert await db.docs.count_documents({}) == 2
    exercise(run)


@integration
def test_scan_budget_fails_explicitly_without_silently_truncating_counts():
    async def run(db, other):
        await db.docs.insert_one({"_id": "one", "user_id": "owner"})
        await db.docs.insert_one({"_id": "two", "user_id": "other"})
        with pytest.raises(ScanLimitExceeded):
            await db.docs.count_documents({})
        assert await db.docs.count_documents({"user_id": "owner"}) == 1
    exercise(run, max_scan_records=1)
