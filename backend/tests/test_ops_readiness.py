"""Readiness deadlines against the real repository cancellation contract; no providers."""
import asyncio
import gc
import logging
import threading
import time

from fastapi import HTTPException
import httpx
from pymongo.errors import PyMongoError
import pytest

from backend import ops
from backend.postgres_store import PostgresDatabase, RepositoryUnavailable


def test_native_transaction_cannot_extend_http_deadline_or_multiply_probes(monkeypatch, caplog):
    """Use actual command/_run, including its necessary transaction shield."""
    monkeypatch.setattr(ops, "READINESS_TIMEOUT_SECONDS", .03)
    caplog.set_level(logging.WARNING, logger=ops.__name__)
    release = threading.Event()
    entered = threading.Event()
    transactions = []
    db = object.__new__(PostgresDatabase)
    db.name = "only_local_readiness_fixture"

    def transaction(callback, *, write=None):
        transactions.append(write)
        entered.set()
        assert release.wait(5), "Fixture must release its admitted transaction"
        return callback(None, None)

    db._transaction = transaction

    async def run():
        probe = None
        try:
            started = time.monotonic()
            checks = await asyncio.gather(*(ops.readiness(db) for _ in range(30)), return_exceptions=True)
            assert time.monotonic() - started < .5
            assert entered.is_set()
            assert all(isinstance(exc, HTTPException) and exc.status_code == 503 for exc in checks)
            probe = ops._READINESS_PROBES[asyncio.get_running_loop()][id(db)]
            assert not probe.task.done()
            for _ in range(3):
                with pytest.raises(HTTPException, match="temporarily unavailable") as raised:
                    await ops.readiness(db)
                assert raised.value.status_code == 503
            assert transactions == [None]
            assert len(caplog.records) == 1
            assert "timed out" in caplog.records[0].message
        finally:
            release.set()
            if probe is not None:
                await probe.task
            await asyncio.sleep(0)
        assert id(db) not in ops._READINESS_PROBES.get(asyncio.get_running_loop(), {})
        await ops.readiness(db)
        assert transactions == [None, None], "Completed success must not become a cached pass"

    asyncio.run(run())


def test_cancelled_http_waiter_leaves_one_probe_available_to_other_waiters(monkeypatch):
    monkeypatch.setattr(ops, "READINESS_TIMEOUT_SECONDS", .1)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        class Database:
            async def command(self, command):
                calls.append(command)
                entered.set()
                await release.wait()
                return {"ok": 1}

        db = Database()
        first = asyncio.create_task(ops.readiness(db))
        await entered.wait()
        probe = ops._READINESS_PROBES[asyncio.get_running_loop()][id(db)]
        second = asyncio.create_task(ops.readiness(db))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not probe.task.done()
        release.set()
        await second
        assert calls == ["ping"]
        assert not probe.task.cancelled()

    asyncio.run(run())


def test_database_command_may_return_a_future_instead_of_coroutine():
    async def run():
        class Database:
            def command(self, command):
                assert command == "ping"
                future = asyncio.get_running_loop().create_future()
                future.set_result({"ok": 1})
                return future

        await ops.readiness(Database())

    asyncio.run(run())


@pytest.mark.parametrize("error", [TimeoutError, PyMongoError, RepositoryUnavailable])
def test_actual_database_failures_are_sanitized_and_next_probe_is_fresh(monkeypatch, caplog, error):
    monkeypatch.setattr(ops, "READINESS_TIMEOUT_SECONDS", .1)
    caplog.set_level(logging.WARNING, logger=ops.__name__)

    async def run():
        class Database:
            calls = 0

            async def command(self, command):
                assert command == "ping"
                self.calls += 1
                if self.calls == 1:
                    raise error("do-not-log-connection-secret")
                return {"ok": 1}

        db = Database()
        with pytest.raises(HTTPException) as raised:
            await ops.readiness(db)
        assert raised.value.status_code == 503
        assert raised.value.detail == "Service starting or temporarily unavailable."
        assert "do-not-log" not in caplog.text
        assert len(caplog.records) == 1
        await ops.readiness(db)
        assert db.calls == 2

    asyncio.run(run())


def test_late_failure_is_consumed_after_timed_out_or_cancelled_waiters(monkeypatch, caplog):
    monkeypatch.setattr(ops, "READINESS_TIMEOUT_SECONDS", .02)
    caplog.set_level(logging.WARNING, logger=ops.__name__)

    async def run():
        loop = asyncio.get_running_loop()
        unhandled = []
        old_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _, context: unhandled.append(context))
        try:
            for cancel_waiter in (False, True):
                entered, release = asyncio.Event(), asyncio.Event()

                class Database:
                    async def command(self, command):
                        entered.set()
                        await release.wait()
                        raise RepositoryUnavailable("do-not-log-late-secret")

                db = Database()
                waiter = asyncio.create_task(ops.readiness(db))
                await entered.wait()
                if cancel_waiter:
                    waiter.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await waiter
                else:
                    with pytest.raises(HTTPException):
                        await waiter
                release.set()
                for _ in range(3):
                    await asyncio.sleep(0)
                assert id(db) not in ops._READINESS_PROBES.get(loop, {})
                del waiter, db
                gc.collect()
                await asyncio.sleep(0)
            assert not unhandled
            assert len(caplog.records) == 2
            assert "do-not-log" not in caplog.text
        finally:
            loop.set_exception_handler(old_handler)

    asyncio.run(run())


def test_distinct_equal_database_objects_never_share_probe(monkeypatch):
    monkeypatch.setattr(ops, "READINESS_TIMEOUT_SECONDS", .1)

    async def run():
        release = asyncio.Event()

        class Database:
            __hash__ = None

            def __init__(self):
                self.calls = 0

            def __eq__(self, other):
                return isinstance(other, Database)

            async def command(self, command):
                self.calls += 1
                await release.wait()
                return {"ok": 1}

        first, second = Database(), Database()
        waiters = [asyncio.create_task(ops.readiness(db)) for db in (first, second)]
        for _ in range(3):
            await asyncio.sleep(0)
        assert len(ops._READINESS_PROBES[asyncio.get_running_loop()]) == 2
        release.set()
        await asyncio.gather(*waiters)
        assert first.calls == second.calls == 1

    asyncio.run(run())


def test_database_reused_across_event_loop_restarts_gets_fresh_probe(monkeypatch):
    monkeypatch.setattr(ops, "READINESS_TIMEOUT_SECONDS", .1)

    class Database:
        def __init__(self):
            self.loops = []

        async def command(self, command):
            self.loops.append(asyncio.get_running_loop())
            return {"ok": 1}

    db = Database()
    for _ in range(2):
        asyncio.run(ops.readiness(db))
    assert len(db.loops) == 2 and db.loops[0] is not db.loops[1]
    assert all(loop not in ops._READINESS_PROBES for loop in db.loops)


def test_pending_probe_never_reuses_previous_success(monkeypatch):
    monkeypatch.setattr(ops, "READINESS_TIMEOUT_SECONDS", .02)

    async def run():
        release = asyncio.Event()

        class Database:
            calls = 0

            async def command(self, command):
                self.calls += 1
                if self.calls > 1:
                    await release.wait()
                return {"ok": 1}

        db = Database()
        await ops.readiness(db)
        try:
            for _ in range(2):
                with pytest.raises(HTTPException) as raised:
                    await ops.readiness(db)
                assert raised.value.status_code == 503
            assert db.calls == 2
        finally:
            probe = ops._READINESS_PROBES[asyncio.get_running_loop()][id(db)]
            release.set()
            await probe.task

    asyncio.run(run())


def test_actual_standby_readiness_route_returns_bounded_503_then_fresh_200(monkeypatch):
    from backend import database
    placeholder = object()
    monkeypatch.setattr(database, "open_database", lambda: (placeholder, placeholder))
    from backend import server, standby

    monkeypatch.setattr(ops, "READINESS_TIMEOUT_SECONDS", .03)
    monkeypatch.setattr(server, "database_paused", lambda: False)

    async def run():
        release = asyncio.Event()

        class Database:
            calls = 0

            async def command(self, command):
                assert command == "ping"
                self.calls += 1
                await release.wait()
                return {"ok": 1}

        db = Database()
        monkeypatch.setattr(server, "db", db)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=standby.app),
                                     base_url="https://only-local-standby") as http:
            try:
                started = time.monotonic()
                result = await http.get("/api/ready")
                assert time.monotonic() - started < .5
                assert result.status_code == 503
                assert result.json() == {"detail": "Service starting or temporarily unavailable."}
                assert db.calls == 1
            finally:
                probe = ops._READINESS_PROBES[asyncio.get_running_loop()][id(db)]
                release.set()
                await probe.task
            result = await http.get("/api/ready")
            assert result.status_code == 200 and result.json() == {"ready": True}
            assert result.headers["cache-control"] == "no-store"
            assert db.calls == 2

    asyncio.run(run())


def test_loop_shutdown_consumes_cancelled_probe_and_drops_database_reference(monkeypatch):
    monkeypatch.setattr(ops, "READINESS_TIMEOUT_SECONDS", .01)

    async def run():
        class Database:
            async def command(self, command):
                await asyncio.Event().wait()

        db = Database()
        with pytest.raises(HTTPException):
            await ops.readiness(db)
        loop = asyncio.get_running_loop()
        assert not ops._READINESS_PROBES[loop][id(db)].task.done()
        return loop

    loop = asyncio.run(run())
    assert loop not in ops._READINESS_PROBES
