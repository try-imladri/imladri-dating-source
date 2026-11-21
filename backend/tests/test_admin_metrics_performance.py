"""Dashboard reads keep exact results while overlapping independent gateway waits."""
from datetime import datetime, timedelta, timezone
import os
from types import FunctionType, SimpleNamespace

import pytest

from backend.postgres_remote import RemotePostgresDatabase
from backend.tests.test_individual_answer_history import run_case
from backend.tests.test_loading_performance import GatewayTimings, evidence, timed
from backend.tests.test_postgres_rpc import ORIGIN, SECRET, URL

pytestmark = pytest.mark.skipif(not os.getenv("IMLADRI_TEST_POSTGRES_DSN"),
                                reason="Requires disposable loopback PostgreSQL.")


def test_dashboard_overlaps_only_independent_reads_and_keeps_all_metrics(monkeypatch):
    async def scenario(db, call, quiz, server):
        at = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
        stamp = lambda days: (at + timedelta(days=days)).isoformat()
        monkeypatch.setattr(server, "now", lambda: at.isoformat())
        for uid in ("owner", "peer", "unpaid"):
            await db.users.update_one({"user_id": uid}, {"$set": {
                "created_at": stamp(-20), "email": uid + "@fixture.invalid"}})
        await db.users.insert_many([
            {"user_id": "excluded-closing", "role": "member", "env": server.ENV,
             "email": "closing@fixture.invalid", "closing": True},
            {"user_id": "excluded-test", "role": "member", "env": server.ENV,
             "email": "fixture+test@fixture.invalid"}])
        await db.people.insert_many([
            {"id": "p1", "user_id": "owner", "rel_type": "friend", "intake": {"about": "long enough detail"}},
            {"id": "p2", "user_id": "peer", "rel_type": "partner", "intake": {}},
            {"id": "private", "user_id": "excluded-closing", "rel_type": "friend", "intake": {}}])
        await db.exchanges.insert_many([
            {"id": "a", "user_id": "owner", "person_id": "p1", "created_at": stamp(-10),
             "has_conversation": True, "outcome": "worked", "outcome_at": stamp(-9)},
            {"id": "b", "user_id": "owner", "person_id": "p1", "created_at": stamp(-9)},
            {"id": "c", "user_id": "peer", "person_id": "p2", "created_at": at - timedelta(days=3)},
            {"id": "future", "user_id": "unpaid", "created_at": stamp(1)},
            {"id": "invalid", "user_id": "unpaid", "created_at": [stamp(-3)]},
            {"id": "excluded", "user_id": "excluded-closing", "person_id": "private", "created_at": stamp(-9)}])
        await db.events.insert_many([
            {"user_id": "owner", "type": "session", "ts": stamp(-10)},
            {"user_id": "owner", "type": "session", "ts": at - timedelta(days=9)},
            {"user_id": "peer", "type": "session", "ts": stamp(-3)},
            {"user_id": "owner", "type": "deepen", "ts": stamp(-8)},
            *({"user_id": "owner", "type": kind, "ts": stamp(-5)}
              for kind in ("upload_started", "upload_completed", "memory_edit")),
            {"user_id": "peer", "type": "upload_started", "ts": stamp(-4)},
            {"user_id": "peer", "type": "memory_edit", "ts": stamp(-4)},
            {"user_id": "owner", "type": "memory_edit", "ts": stamp(1)},
            {"user_id": ["owner", "excluded-closing"], "type": "memory_edit", "ts": stamp(-4)},
            {"user_id": "excluded-closing", "type": "upload_started", "ts": stamp(-4)}])
        await db.memories.insert_many([{"id": "m1", "user_id": "owner", "text": "private"},
                                      {"id": "m2", "user_id": ["peer", "excluded-closing"], "text": "not a scalar owner"}])
        await db.payment_transactions.insert_many([
            {"user_id": "owner", "kind": "purchase", "payment_status": "paid"},
            {"user_id": "peer", "kind": "renewal", "payment_status": "paid"},
            {"user_id": "excluded-closing", "kind": "purchase", "payment_status": "paid"}])
        await db.waitlist.insert_many([{"email": "one@fixture.invalid", "env": server.ENV},
                                      {"email": "two@fixture.invalid", "env": "other-environment"}])

        transport = GatewayTimings(db)
        remote = RemotePostgresDatabase(URL, SECRET, ORIGIN, db.name, transport=transport)
        monkeypatch.setenv("IMLADRI_POSTGRES_GATEWAY_SECRET", SECRET)
        monkeypatch.setattr(server, "db", remote)
        parallel = server.admin_metric_read_rows
        # Serialize exactly the same query objects to model the prior wait
        # phases. Nested helpers retain their original own implementation.
        async def serial(*reads):
            return [await read for read in reads]
        serial_phase = FunctionType(parallel.__code__,
            {**parallel.__globals__, "asyncio": SimpleNamespace(gather=serial)},
            parallel.__name__, parallel.__defaults__, parallel.__closure__)
        try:
            await remote.command("ping")
            monkeypatch.setattr(server, "admin_metric_read_rows", serial_phase)
            before, old_stats = await timed(transport, lambda: call("GET", "/admin/metrics", uid="admin"))
            monkeypatch.setattr(server, "admin_metric_read_rows", parallel)
            after, new_stats = await timed(transport, lambda: call("GET", "/admin/metrics", uid="admin"))
            assert before == after
            assert old_stats["operations"] == new_stats["operations"]
            assert after["users"] == 3 and after["waitlist"] == 1
            assert after["returning_days"] == 1
            assert after["upload_completion"] == {"started": 2, "completed": 1, "rate": 50}
            assert after["users_with_memories"] == 1 and after["memory_edits"] == 2
            assert after["memory_edits_by_current_holders"] == 1
            assert after["deepening_window"] == {"complete": 1, "deepened": 1, "open": 1}
            assert [row["value"] for row in after["activation"]] == [3, 2, 1, 1, 1]
            assert new_stats["peak_physical_requests"] <= 2
            await call("GET", "/admin/metrics", uid="owner", status=403)
            await call("GET", "/admin/metrics", uid=None, status=401)
            evidence("admin-dashboard-gateway-01.json", {
                "scope": "Same dashboard queries serialized vs concurrent; 25ms synthetic RTT, real signed gateway and PostgreSQL.",
                "before": old_stats, "after": new_stats, "responses_equal": True,
                "query_operations_equal": True, "private_cross_request_cache": False})
        finally:
            await remote.aclose()
    run_case(monkeypatch, scenario)
