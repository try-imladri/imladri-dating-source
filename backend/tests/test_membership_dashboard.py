import time
import csv
from io import StringIO
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import HTTPException, Request

from backend import accounts, membership_admin
from backend.free_plan import paid_cycle, run_metered
from backend.tests.test_free_plan_quota import Question, run_case


def test_monthly_cash_uses_paid_dates_and_survives_an_account_closure(monkeypatch):
    monkeypatch.setattr(accounts, "close_billing", lambda subscriptions, sessions, uid: None)
    async def scenario(db):
        at = datetime(2026, 12, 20, tzinfo=timezone.utc)
        env = "preview"
        scope = {"env": {"$in": [env, None]}}
        await db.users.insert_many([{"user_id": "member", "env": env}, {"user_id": "legacy"},
                                    {"user_id": "other", "env": "live"}])
        dec = "2026-12-01T00:00:00+00:00"
        nov = "2026-11-30T23:59:59+00:00"
        jan = "2027-01-01T00:00:00+00:00"
        basic = {"kind": "purchase", "currency": "usd", "payment_status": "paid", "user_id": "member",
                 "created_at": nov, "fulfilled_at": dec, "amount": 12.0}
        await db.payment_transactions.insert_many([
            {**basic, "session_id": "cs_1", "subscription_revenue_cents": 1200, "received_cents": 1324},
            {**basic, "session_id": "cs_old", "fulfilled_at": nov},
            {**basic, "session_id": "cs_new", "fulfilled_at": jan},
            {**basic, "session_id": "cs_legacy", "fulfilled_at": None, "created_at": dec, "user_id": "legacy"},
            {**basic, "session_id": "cs_pending", "payment_status": "pending"},
            {**basic, "session_id": "cs_failed", "payment_status": "failed"},
            {**basic, "session_id": "cs_currency", "currency": "eur"},
            {**basic, "session_id": "cs_other", "user_id": "other"},
            {**basic, "session_id": "cs_orphan", "user_id": "orphan"},
            {**basic, "session_id": "cs_tagged_other", "env": "live"},
            {**basic, "kind": "renewal", "invoice_id": "in_2", "created_at": dec, "subscription_revenue_cents": 1100},
            {**basic, "session_id": "cs_credited", "subscription_revenue_cents": 0},
            {**basic, "kind": "upgrade", "invoice_id": "in_upgrade", "created_at": dec, "subscription_revenue_cents": 800},
            {**basic, "kind": "topup", "session_id": "cs_refill", "created_at": dec, "amount": 12.0},
        ])
        async def read():
            return await membership_admin.summary(db, env, scope, at)

        summary = await read()
        assert summary == {"total_users": 2, "monthly_revenue_cents": 4300, "currency": "usd",
                           "monthly_gross_revenue_cents": 4300, "monthly_refund_dispute_adjustments_cents": 0,
                           "monthly_net_revenue_cents": 4300, "monthly_refill_gross_revenue_cents": 1200,
                           "monthly_refill_adjustments_cents": 0, "monthly_refill_net_revenue_cents": 1200,
                           "net_revenue_verified": True, "pending_financial_reviews": 0,
                           "month_start": "2026-12-01", "month_end_exclusive": "2027-01-01", "environment": env}
        # An overlapping directory read can see both the original and the archive during closure.
        await membership_admin.retain_closed_revenue(db, "legacy", env)
        assert await read() == summary
        await accounts.finish_closure(db, "legacy")
        assert await read() == {**summary, "total_users": 1}
        archived = await db.closed_membership_revenue.find({}).to_list(None)
        assert len(archived) == 1 and set(archived[0]) == {"_id", "month", "env", "cents"}
        assert len(archived[0]["_id"]) == 64 and archived[0]["cents"] == 1200

    run_case(scenario)


def test_month_end_uses_the_same_utc_day_for_revenue_activity_and_closed_accounts():
    from datetime import date
    from backend import trends
    from backend.quiz import default_quiz

    async def scenario(db):
        env = "preview"
        scope = {"env": env}
        start, end = date(2026, 12, 1), date(2027, 1, 1)
        await db.users.insert_one({"user_id": "member", **scope})
        base = {"user_id": "member", "kind": "purchase", "currency": "usd", "payment_status": "paid", "amount": 12, **scope}
        for ref, stamp in [
            ("nov", "2026-12-01T00:10:00+02:00"),
            ("dec", "2026-11-30T23:40:00-05:00"),
            ("bson", datetime(2026, 12, 1, 5, tzinfo=timezone.utc)),
            ("array", ["2026-12-01"]),
            ("invalid", "2026-12-01-invalid"),
            ("num", 1796101200000),
            ("jan", "2026-12-31T23:30:00-05:00"),
        ]:
            await db.payment_transactions.insert_one({**base, "session_id": ref, "fulfilled_at": stamp})
        await db.payment_transactions.insert_one({**base, "kind": "topup", "session_id": "refill", "fulfilled_at": "2027-01-01T00:10:00+02:00"})
        await db.payment_transactions.insert_one({**base, "kind": "topup", "session_id": "legacy_refill",
                                                  "created_at": "2026-11-30T23:40:00-05:00", "topup_credits": 50})
        async def cash():
            return [await membership_admin.monthly_payments(db, env, scope, start, end, refills=refills)
                    for refills in (False, True)]
        assert await cash() == [2400, 2400]
        for stamp in (["2026-12-01"], 1796101200000, None, "2026-12-01-invalid",
                      "2026-11-30T23:40:00-05:00", datetime(2026, 12, 2, tzinfo=timezone.utc)):
            await db.events.insert_one({"type": "quiz_short", "user_id": "member", "ts": stamp})
        report = await trends.report(db, scope, default_quiz(), 7, today=date(2026, 12, 7))
        assert report["daily"][0]["paid_purchases"] == 2
        assert report["daily"][0]["paid_refills"] == report["daily"][0]["adjusted_refills"] == 1
        assert report["daily"][0]["basics_completed"] == report["totals"]["basics_completed"] == 1
        await membership_admin.retain_closed_revenue(db, "member", env)
        await db.payment_transactions.delete_many({"user_id": "member"})
        assert await cash() == [2400, 2400]

    run_case(scenario)


def test_directory_filters_usage_access_actions_and_staff_boundaries(monkeypatch):
    from backend import server
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_fixture")

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        timestamp = int(time.time())
        active = {"user_id": "paid", "role": "member", "tier": "paid", "stripe_subscription_id": "sub_paid",
                  "subscription_period_start": timestamp - 86400, "subscription_period_end": timestamp + 86400}
        active["paid_reply_cycles"] = {paid_cycle(active): {"used": 11, "claimed": 12}, "prior": {"used": 75}}
        await db.users.insert_many([active.copy(), {"user_id": "staff", "role": "admin"},
                                    {"user_id": "staff2", "role": "admin"}, {"user_id": "new"},
                                    {**active, "user_id": "expired", "subscription_period_end": timestamp-1},
                                    {**active, "user_id": "due", "subscription_status": "past_due"},
                                    {"user_id": "cancelled", "subscription_status": "cancelled"},
                                    {"user_id": "unevidenced", "tier": "paid"}, {"user_id": "closing", "closing": True},
                                    {"user_id": "other", "env": "different"}])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            for verb, path in (("get", "/api/users"), ("get", "/api/admin/membership/summary"),
                               ("patch", "/api/users/paid/reply-access")):
                response = await api.request(verb, path, json={"paused": True} if verb == "patch" else None)
                assert response.status_code == 401
            monkeypatch.setitem(server.app.dependency_overrides, server.current_member, lambda: {"role": "member", "user_id": "paid"})
            assert (await api.get("/api/users")).status_code == 403
            assert (await api.get("/api/admin/membership/summary")).status_code == 403
            assert (await api.patch("/api/users/new/reply-access", json={"paused": True})).status_code == 403
            monkeypatch.setitem(server.app.dependency_overrides, server.current_member, lambda: {"role": "admin", "user_id": "staff"})
            data = (await api.get("/api/users")).json()
            rows = {u["user_id"]: u for u in data["users"]}
            assert {k: v["membership_status"] for k, v in rows.items()} == {
                "paid": "active", "due": "past_due", "expired": "expired", "unevidenced": "unverified",
                "staff": "staff", "staff2": "staff", "new": "unsubscribed", "cancelled": "cancelled", "closing": "closing"}
            assert rows["paid"]["usage"]["messages_used"] == 11 and rows["paid"]["usage"]["messages_remaining"] == 88
            assert rows["paid"]["usage"]["message_limit"] == 100 and rows["staff"]["usage"]["unlimited"]
            assert rows["expired"]["usage"]["messages_used"] == 0 and "stripe_subscription_id" not in rows["paid"]
            assert (await api.get("/api/users", params={"q": "plus", "status": "past_due"})).json()["users"] == [rows["due"]]
            assert (await api.get("/api/users", params={"status": "invalid"})).status_code == 422
            assert (await api.get("/api/admin/membership/summary")).json()["total_users"] == 9
            await db.payment_transactions.insert_one({"kind": "purchase", "user_id": "paid", "session_id": "test-only",
                "currency": "usd", "payment_status": "paid", "created_at": datetime.now(timezone.utc).isoformat(),
                "subscription_revenue_cents": 1187})
            snapshot = (await api.get("/api/admin/membership/summary")).json()
            export = await api.get("/api/admin/reports/workspace.csv?days=90")
            assert export.status_code == 200
            rows = {r["metric"]: r for r in csv.DictReader(StringIO(export.text)) if r["section"] == "Membership"}
            assert int(rows["total_accounts"]["value"]) == snapshot["total_users"]
            revenue = rows["subscription_revenue_usd"]
            assert revenue["value"] == "11.87" and snapshot["monthly_revenue_cents"] == 1187
            assert revenue["from_utc"] == snapshot["month_start"] and revenue["scope"] == "calendar month to date"
            for uid, status in [("staff", 403), ("staff2", 409), ("closing", 409), ("other", 404)]:
                assert (await api.patch(f"/api/users/{uid}/reply-access", json={"paused": True})).status_code == status
            assert (await api.patch("/api/users/paid/reply-access", json={"paused": "false"})).status_code == 422

            async def generate(rid):
                doc = {"user_id": "paid", "id": rid, "response": "saved"}
                await db.exchanges.insert_one(dict(doc))
                return doc

            async def restore(doc):
                return doc

            def send(key):
                req = Request({"type": "http", "headers": [(b"idempotency-key", key.encode())]})
                return run_metered(db, active, req, Question(question="example"), "exchange", "profile", generate, restore)

            first = await send("first")
            assert (await api.patch("/api/users/paid/reply-access", json={"paused": True})).status_code == 200
            assert (await api.get("/api/users", params={"q": "paid", "status": "active"})).json()["users"][0]["replies_paused"]
            assert (await send("first"))["id"] == first["id"]
            with pytest.raises(HTTPException) as paused:
                await send("second")
            assert paused.value.status_code == 403
            assert (await api.patch("/api/users/paid/reply-access", json={"paused": False})).status_code == 200
            assert (await send("second"))["id"] != first["id"]
            final = (await api.get("/api/users", params={"status": "active"})).json()["users"][0]
            assert final["usage"]["messages_used"] == 13 and not final["replies_paused"]

    run_case(scenario)


def test_staff_actions_are_recorded_once_and_visible_only_in_account_management(monkeypatch):
    from backend import server
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_fixture")
    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        await db.users.insert_many([{"user_id": "staff", "role": "admin"}, {"user_id": "member", "role": "member",
            "admin_member_actions": [{"action": "couples_enrolled", "actor": "archived-staff", "at": "2025-02-10T00:00:00+00:00"}],
            "reply_access_admin_change": {"by": "earlier-staff", "at": "2025-01-10T00:00:00+00:00"}}])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as client:
            path = "/api/users/member/actions"
            assert (await client.get(path)).status_code == 401
            monkeypatch.setitem(server.app.dependency_overrides, server.current_member, lambda: {"user_id":"member", "role":"member"})
            assert (await client.get(path)).status_code == 403
            monkeypatch.setitem(server.app.dependency_overrides, server.current_member, lambda: {"user_id":"staff", "role":"admin"})
            for method, route, body in (("PATCH","/api/users/member/reply-access",{"paused":True}),
                     ("PATCH","/api/users/member/role",{"role":"admin"})):
                for _ in range(2):
                    assert (await client.request(method, route,json=body)).status_code == 200
            history = await client.get(path)
            assert history.headers["cache-control"] == "no-store"
            actions = history.json()["actions"]
            assert [v["action"] for v in actions] == ["made_admin","replies_paused","couples_enrolled","reply_access_changed"]
            assert [v["actor"] for v in actions] == ["staff", "staff", "archived-staff", "earlier-staff"]
            assert (await client.get("/api/users/absent/actions")).status_code == 404
            person = await db.users.find_one({"user_id":"member"})
            exported = "".join([part async for part in accounts.export_json(db,person)])
            assert "admin_member_actions" not in exported and "earlier-staff" not in exported
    run_case(scenario)


def test_subscription_status_reconciles_provider_and_member_without_granting_replies(monkeypatch):
    from backend import server
    from unittest.mock import Mock
    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setenv("PERSONAL_BILLING_MODE", "disabled")
        timestamp = int(time.time())
        await db.users.insert_one({"user_id": "member", "role": "member", "tier":"paid", "stripe_subscription_id":"sub_known",
            "subscription_period_start":timestamp - 200, "subscription_period_end":timestamp - 100, "subscription_status":"active"})
        provider = Mock(return_value={"id":"sub_known","status":"past_due"})
        monkeypatch.setattr(server.stripe.Subscription, "retrieve", provider)
        await server.handle_stripe_event("customer.subscription.updated", {"id":"sub_other","status":"past_due"})
        provider.assert_not_called()
        for _ in range(2):
            await server.handle_stripe_event("customer.subscription.updated", {"id":"sub_known","status":"active"})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as client:
            monkeypatch.setitem(server.app.dependency_overrides, server.current_member, lambda: {"user_id":"staff", "role":"admin"})
            due = (await client.get("/api/users", params={"status":"past_due"})).json()["users"]
            assert len(due) == 1 and due[0]["usage"]["messages_remaining"] == 0
            member = await db.users.find_one({"user_id":"member"})
            monkeypatch.setitem(server.app.dependency_overrides, server.current_member, lambda:member)
            upgrade = (await client.get("/api/upgrade")).json()
            assert upgrade["existing_subscription"] and upgrade["subscription_status"] == "past_due" and not upgrade["checkout_available"]
            provider.return_value = {"id":"sub_known","status":"active"}
            await server.handle_stripe_event("customer.subscription.updated", {"id":"sub_known","status":"past_due"})
            latest = await db.users.find_one({"user_id":"member"})
            assert latest["subscription_status"] == "active" and latest["subscription_period_end"] == timestamp - 100
            assert await db.payment_transactions.count_documents({}) == 0
    run_case(scenario)
