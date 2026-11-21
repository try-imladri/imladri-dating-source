"""Real SQL cache freshness, single-statement snapshots and private bypass."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import os
from urllib.parse import urlsplit
from uuid import uuid4

from bson import BSON, Int64, ObjectId
import pytest

from backend.postgres_store import (PostgresDatabase, RepositoryUnavailable, MAX_PUBLIC_CATALOG_CACHE_BYTES,
                                    MAX_PUBLIC_CATALOG_CACHE_ENTRIES)


DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
integration = pytest.mark.skipif(not DSN, reason="Needs explicit isolated loopback PostgreSQL.")


def exercise(callback, capacity=MAX_PUBLIC_CATALOG_CACHE_BYTES):
    async def run():
        assert urlsplit(DSN).hostname in ("127.0.0.1", "localhost", "::1")
        namespace = "fixture_public_cache_" + uuid4().hex
        cached = PostgresDatabase(DSN, namespace, allow_local_plaintext=True, public_catalog_cache_bytes=capacity)
        actual = PostgresDatabase(DSN, namespace, allow_local_plaintext=True)
        try:
            await callback(cached, actual)
        finally:
            def cleanup(cursor, _now):
                for table in ("unique_keys", "indexes", "documents", "collections", "rpc_requests"):
                    cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (namespace,))
            await actual._run(cleanup, write="__cleanup")
            await cached.aclose(); await actual.aclose()
    asyncio.run(run())


def message(operation="find", collection="figures", **arguments):
    return {"version": 1, "operation": operation, "collection": collection, "arguments": arguments}


async def rpc(db, body):
    return await db.rpc(body, request_id=str(uuid4()), body_sha256=hashlib.sha256(BSON.encode(body)).hexdigest())


class WireCounter:
    """Independently count actual fetched bytea values, not expected hit counts."""
    def __init__(self, cursor, total): self.cursor, self.total = cursor, total
    def __getattr__(self, key): return getattr(self.cursor, key)
    def fetchall(self):
        rows = self.cursor.fetchall()
        for row in rows:
            if row and isinstance(row[-1], (bytes, bytearray, memoryview)):
                self.total[0] += len(row[-1])
        return rows


@integration
def test_cold_and_warm_actual_SQL_bytes_and_typed_BSON_match_uncached_repository():
    async def run(cached, actual):
        rows = [{"_id": ObjectId(), "published": True, "slug": "figure-" + str(i), "i": Int64(i),
                 "at": datetime(2026, 11, 11), "career": "x" * 4096} for i in range(24)]
        await actual.figures.insert_many(rows)
        total = [0]; original = cached._scan
        cached._scan = lambda cursor, *args, **kwargs: original(WireCounter(cursor, total), *args, **kwargs)
        first = await cached.figures.find({"published": True}).to_list(None)
        cold = total[0]
        second = await cached.figures.find({"published": True}).to_list(None)
        warm = total[0] - cold
        truth = await actual.figures.find({"published": True}).to_list(None)
        assert [BSON.encode(v) for v in first] == [BSON.encode(v) for v in second] == [BSON.encode(v) for v in truth]
        assert cold == sum(len(BSON.encode(v)) for v in rows) and warm == 0
        assert cached.catalog_cache_stats()["metadata_queries"] == 2
        first[0]["career"] = "caller mutation"
        assert (await cached.figures.find_one({"_id": first[0]["_id"]}))["career"] == "x" * 4096
        assert actual.catalog_cache_stats()["enabled"] is False
    exercise(run)


@integration
def test_edits_deletes_withdrawals_and_unlocked_changes_are_immediate_and_hidden_records_not_cached():
    async def run(cached, actual):
        await actual.figures.insert_many([{"_id": "one", "published": True, "label": "before"},
            {"_id": "hidden", "published": False, "label": "draft"}])
        await actual.archetypes.insert_many([{"_id": "type", "unlocked": True}, {"_id": "locked", "unlocked": False}])
        assert len(await cached.figures.find({"published": True}).to_list(None)) == 1
        await cached.archetypes.find({}).to_list(None)
        assert cached.catalog_cache_stats()["entries"] == 2
        await actual.figures.update_one({"_id": "one"}, {"$set": {"label": "after"}})
        assert (await cached.figures.find_one({"_id": "one"}))["label"] == "after"
        await actual.figures.update_one({"_id": "one"}, {"$set": {"published": False}})
        assert await cached.figures.find({"published": True}).to_list(None) == []
        assert ("figures", next(iter(cached._public_catalog_cache.entries))[1]) not in cached._public_catalog_cache.entries
        await actual.archetypes.delete_one({"_id": "type"})
        assert await cached.archetypes.find({"unlocked": True}).to_list(None) == []
        await actual.figures.update_one({"_id": "hidden"}, {"$set": {"published": True}})
        assert [v["_id"] for v in await cached.figures.find({"published": True}).to_list(None)] == ["hidden"]
    exercise(run)


@integration
def test_private_collections_never_enter_cache_even_with_visibility_like_fields():
    async def run(cached, actual):
        for name in ("users", "prompts", "intimacy_answers", "couples_pairs"):
            await actual[name].insert_one({"_id": "private", "published": True, "unlocked": True, "answer": "synthetic"})
            assert (await cached[name].find_one({"_id": "private"}))["answer"] == "synthetic"
        assert cached.catalog_cache_stats()["entries"] == cached.catalog_cache_stats()["metadata_queries"] == 0
    exercise(run)


@integration
def test_read_RPC_uses_cache_but_mutation_RPC_bypasses_and_nonce_journal_has_no_read_payload():
    async def run(cached, actual):
        await actual.figures.insert_one({"_id": "one", "published": True, "label": "before"})
        body = message(filter={"published": True}, length=None)
        assert (await rpc(cached, body))[0]["label"] == "before"
        cold = cached.catalog_cache_stats()["raw_BSON_bytes_read_from_SQL"]
        assert (await rpc(cached, body))[0]["label"] == "before"
        assert cached.catalog_cache_stats()["raw_BSON_bytes_read_from_SQL"] == cold
        before = deepcopy(cached.catalog_cache_stats())
        await rpc(cached, message("update_one", filter={"_id": "one"}, update={"$set": {"label": "after"}}))
        assert cached.catalog_cache_stats() == before
        assert (await rpc(cached, body))[0]["label"] == "after"
        def inspect(cursor, _now):
            cursor.execute("SELECT count(*) FROM imladri_app.rpc_requests WHERE namespace=%s AND compact_result IS NOT NULL", (cached.name,))
            return cursor.fetchone()[0]
        # The one compact mutation result is counts only; the two read payloads
        # were never stored in durable nonce/result metadata.
        assert await actual._run(inspect) == 1
    exercise(run)


@integration
@pytest.mark.parametrize("when", ["before_declare", "after_declare"])
def test_READ_COMMITTED_RPC_update_has_one_consistent_cursor_snapshot(when):
    async def run(cached, actual):
        await actual.figures.insert_one({"_id": "one", "published": True, "version": 0})
        await cached.figures.find({}).to_list(None)
        # Production currently promotes read RPCs to repeatable read. Force an
        # actual READ COMMITTED fixture transaction to prove the single cursor
        # remains correct even with a weaker transaction-wide snapshot.
        pool_connection = cached.pool.connection
        isolation = []
        class IsolationCursor:
            def __init__(self, cursor): self.cursor = cursor
            def __getattr__(self, key): return getattr(self.cursor, key)
            def execute(self, sql, params=()):
                if sql == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ":
                    sql = "SET TRANSACTION ISOLATION LEVEL READ COMMITTED"
                    isolation.append("read committed")
                return self.cursor.execute(sql, params)
        class Connection:
            def __init__(self, connection): self.connection = connection
            def __getattr__(self, key): return getattr(self.connection, key)
            def cursor(self): return IsolationCursor(self.connection.cursor())
        @contextmanager
        def read_committed_connection():
            with pool_connection() as connection:
                yield Connection(connection)
        cached.pool.connection = read_committed_connection
        original, fired = cached._scan, [False]
        class Racing:
            def __init__(self, cursor): self.cursor = cursor
            def __getattr__(self, key): return getattr(self.cursor, key)
            def execute(self, sql, params=()):
                def update():
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        pool.submit(lambda: asyncio.run(actual.figures.update_one({"_id": "one"}, {"$set": {"version": 1}}))).result(timeout=10)
                trigger = sql.startswith("DECLARE public_catalog_") and not fired[0]
                if trigger and when == "before_declare": update()
                result = self.cursor.execute(sql, params)
                if trigger:
                    fired[0] = True
                    if when == "after_declare": update()
                return result
        cached._scan = lambda cursor, *args, **kwargs: original(Racing(cursor), *args, **kwargs)
        result = await rpc(cached, message(filter={}, length=None))
        assert result[0]["version"] == (1 if when == "before_declare" else 0)
        assert (await rpc(cached, message(filter={}, length=None)))[0]["version"] == 1
        assert fired[0]
        assert isolation == ["read committed", "read committed"]
    exercise(run)


@integration
def test_eviction_during_cursor_does_not_invalidate_captured_immutable_hit_bytes():
    async def run(cached, actual):
        await actual.figures.insert_one({"_id": "one", "published": True, "value": Int64(8)})
        expected = await cached.figures.find_one({})
        original = cached._scan
        class Evicting:
            def __init__(self, cursor): self.cursor = cursor
            def __getattr__(self, key): return getattr(self.cursor, key)
            def execute(self, sql, params=()):
                value = self.cursor.execute(sql, params)
                if sql.startswith("DECLARE public_catalog_"):
                    for key in list(cached._public_catalog_cache.entries): cached._public_catalog_cache.discard(key)
                return value
        cached._scan = lambda cursor, *args, **kwargs: original(Evicting(cursor), *args, **kwargs)
        assert BSON.encode(await cached.figures.find_one({})) == BSON.encode(expected)
    exercise(run)


@integration
def test_default_off_verification_reads_actual_changed_BSON_and_cold_corruption_is_not_admitted():
    async def run(cached, actual):
        await actual.figures.insert_one({"_id": "one", "published": True, "label": "before"})
        await cached.figures.find_one({})
        replacement = {"_id": "one", "published": True, "label": "after!"}
        def tamper(cursor, _now):
            cursor.execute("UPDATE imladri_app.documents SET raw_bson=%s WHERE namespace=%s AND collection='figures'", (BSON.encode(replacement), actual.name))
        await actual._run(tamper, write="figures")
        assert actual.catalog_cache_stats()["enabled"] is False
        assert BSON.encode(await actual.figures.find_one({})) == BSON.encode(replacement)
        for key in list(cached._public_catalog_cache.entries): cached._public_catalog_cache.discard(key)
        with pytest.raises(RepositoryUnavailable, match="BSON does not match"):
            await cached.figures.find_one({})
    exercise(run)


@integration
def test_byte_and_entry_caps_are_bounded_without_changing_results():
    async def run(cached, actual):
        await actual.figures.insert_many([{"_id": str(i), "published": True, "value": "x" * 1024} for i in range(5)])
        assert len(await cached.figures.find({}).to_list(None)) == 5
        stats = cached.catalog_cache_stats()
        assert stats["resident_BSON_bytes"] <= 2500 and stats["entries"] <= 2 and stats["evictions"] > 0
        assert len(await cached.figures.find({}).to_list(None)) == 5
    exercise(run, capacity=2500)


@integration
def test_actual_SQL_catalog_larger_than_entry_cap_remains_complete():
    async def run(cached, actual):
        count = MAX_PUBLIC_CATALOG_CACHE_ENTRIES + 3
        await actual.figures.insert_many([{"_id": str(i), "published": True, "value": Int64(i)} for i in range(count)])
        rows = await cached.figures.find({}).to_list(None)
        assert len(rows) == count and len({row["_id"] for row in rows}) == count
        assert cached.catalog_cache_stats()["entries"] == MAX_PUBLIC_CATALOG_CACHE_ENTRIES
        assert len(await cached.figures.find({}).to_list(None)) == count
    exercise(run)


def test_application_factory_enables_only_active_native_PG_cache(monkeypatch):
    from backend import database, postgres_store
    captured = []
    class Store:
        def __init__(self, *args, **kwargs): captured.append(kwargs)
    monkeypatch.setattr(postgres_store, "PostgresDatabase", Store)
    for key in ("MONGO_URL", "IMLADRI_POSTGRES_SHADOW_DSN", "IMLADRI_MIGRATION_SOURCES_JSON",
                "IMLADRI_MIGRATION_ROLE_DSNS_JSON", "IMLADRI_DATABASE_OPERATOR_SECRET", "ATLAS_NETWORK_CLIENT_ID", "ATLAS_NETWORK_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("IMLADRI_DATABASE_BACKEND", "postgresql")
    monkeypatch.setenv("IMLADRI_POSTGRES_DSN", "fixture-only")
    monkeypatch.setenv("IMLADRI_POSTGRES_NAMESPACE", "fixture")
    monkeypatch.setenv("IMLADRI_DATABASE_MAINTENANCE", "off"); database.open_database()
    monkeypatch.setenv("IMLADRI_DATABASE_MAINTENANCE", "paused"); database.open_database()
    assert captured == [{"public_catalog_cache_bytes": 32 * 1024 * 1024}, {}]
