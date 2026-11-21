"""Cancellation must leave no partial shadow, locks or Mongo cursors."""
import asyncio
from contextlib import aclosing
from types import SimpleNamespace

import pytest

from backend import postgres_migration as pg
from backend.tests.test_postgres_migration import PG_DSN, integration, with_database


def test_source_rows_closes_cursor_after_success_and_cancellation(monkeypatch):
    class Cursor:
        def __init__(self, block=False):
            self.block, self.closed, self.sent = block, False, False
            self.waiting = asyncio.Event()
        def find(self, *_): return self
        def sort(self, *_): return self
        def batch_size(self, *_): return self
        def max_time_ms(self, *_): return self
        def __aiter__(self): return self
        async def __anext__(self):
            if not self.sent:
                self.sent = True
                return {"_id": "fixture"}
            if self.block:
                self.waiting.set()
                await asyncio.Event().wait()
            raise StopAsyncIteration
        async def close(self): self.closed = True
    async def exercise():
        cursor = Cursor()
        monkeypatch.setattr(pg, "source_collection", lambda *_: cursor)
        assert [doc async for doc in pg.source_rows(None, "users")] == [{"_id": "fixture"}]
        assert cursor.closed
        cursor = Cursor(block=True)
        async def consume():
            async for _ in pg.source_rows(None, "users"): pass
        task = asyncio.create_task(consume())
        await cursor.waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert cursor.closed
    asyncio.run(exercise())


def test_start_failure_rolls_back_and_shadow_close_closes_connection():
    rolled_back, closed = [], []
    class Source:
        async def list_collection_names(self): return []
    def fail(*_): raise RuntimeError("fixture begin failure")
    target = SimpleNamespace(begin=fail, connection=SimpleNamespace(rollback=lambda: rolled_back.append(True)))
    with pytest.raises(RuntimeError, match="fixture begin failure"):
        asyncio.run(pg.reconcile(Source(), target, namespace="fixture-start", environment="fixture"))
    assert rolled_back == [True]
    connection = SimpleNamespace(cursor=lambda: SimpleNamespace(close=fail), close=lambda: closed.append(True))
    with pytest.raises(RuntimeError): pg.PostgresShadow(connection).close()
    assert closed == [True]


def test_failed_destination_write_closes_accepted_source_iterator(monkeypatch):
    closed, rolled_back = [], []
    class Source:
        async def list_collection_names(self): return ["users"]
    async def rows(*_):
        try: yield {"_id": "fixture", "user_id": "fixture"}
        finally: closed.append(True)
    def fail(*_): raise RuntimeError("fixture destination write failure")
    target = SimpleNamespace(begin=lambda *_: None, existing_collections=lambda *_: [], tombstones=lambda *_: set(),
        finish_collection=lambda *_: 0, put=fail, connection=SimpleNamespace(rollback=lambda: rolled_back.append(True)))
    monkeypatch.setattr(pg, "source_rows", rows)
    with pytest.raises(RuntimeError, match="destination write failure"):
        asyncio.run(pg.reconcile(Source(), target, namespace="fixture-write", environment="fixture"))
    assert closed == [True] and rolled_back == [True]


@integration
def test_real_task_cancellation_rolls_back_releases_advisory_lock_and_restarts(monkeypatch):
    async def exercise(db, target, namespace):
        await db.users.insert_one({"_id": "one", "user_id": "one", "env": "fixture", "role": "member"})
        baseline = await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert baseline["observed_parity"]
        prior = target.execute("SELECT stored_sha256 FROM imladri_shadow.documents WHERE namespace=%s AND collection='users'", (namespace,)).fetchone()[0]
        prior_runs = target.execute("SELECT count(*) FROM imladri_shadow.runs WHERE namespace=%s", (namespace,)).fetchone()[0]
        target.connection.commit()
        await db.users.update_one({"_id": "one"}, {"$set": {"fixture_revision": 2}})
        original, waiting, closed = pg.source_rows, asyncio.Event(), []
        async def blocked(source, name):
            try:
                async with aclosing(original(source, name)) as documents:
                    async for document in documents: yield document
                if name == "users":
                    waiting.set()
                    await asyncio.Event().wait()
            finally: closed.append(name)
        monkeypatch.setattr(pg, "source_rows", blocked)
        task = asyncio.create_task(pg.reconcile(db, target, namespace=namespace, environment="fixture"))
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert "users" in closed
        assert target.execute("SELECT stored_sha256 FROM imladri_shadow.documents WHERE namespace=%s AND collection='users'", (namespace,)).fetchone()[0] == prior
        assert target.execute("SELECT count(*) FROM imladri_shadow.runs WHERE namespace=%s", (namespace,)).fetchone()[0] == prior_runs
        target.connection.rollback()
        observer = pg.PostgresShadow.connect(PG_DSN, allow_local_plaintext=True)
        try:
            assert observer.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))", ("imladri-shadow:" + namespace,)).fetchone()[0] is True
            observer.connection.rollback()
        finally: observer.close()
        monkeypatch.setattr(pg, "source_rows", original)
        resumed = await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert resumed["observed_parity"] and resumed["cutover_eligible"] is False
        assert target.execute("SELECT stored_sha256 FROM imladri_shadow.documents WHERE namespace=%s AND collection='users'", (namespace,)).fetchone()[0] != prior
        assert (await db.users.find_one({"_id": "one"}))["fixture_revision"] == 2
    asyncio.run(with_database(exercise))
