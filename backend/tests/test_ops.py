"""Run against a loopback MongoDB. All provider traffic uses local fakes."""
import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from backend import ops, stripe_plan


def test_paid_upgrade_invoice_reconciles_against_the_priced_plan(monkeypatch):
    invoice = {"id": "in_upgrade", "status": "paid", "total": 899, "billing_reason": "subscription_update",
               "subscription": "sub_member", "lines": {"data": [
                   {"type": "subscription", "amount": 2000, "price": "price_250", "period": {"start": 10, "end": 20}},
                   {"type": "subscription", "amount": -1101, "price": "price_100", "proration": True}]}}
    monkeypatch.setattr(ops, "recent_invoices", lambda *_: [invoice])
    monkeypatch.setattr(ops.stripe_membership, "catalog", lambda: {"plus_250": {"id": "price_250"}, "plus_500": {"id": "price_500"}})

    async def scenario(db):
        await db.users.insert_one({"user_id": "member", "stripe_subscription_id": "sub_member"})
        assert not await ops.invoices_recorded(db)
        await db.payment_transactions.insert_one({"kind": "upgrade", "user_id": "member", "payment_status": "paid",
                                                  "invoice_id": "in_upgrade", "stripe_subscription_id": "sub_member"})
        assert await ops.invoices_recorded(db)

    run_with_db(scenario)


def test_live_billing_requires_explicit_mode_but_existing_sandbox_still_works(monkeypatch):
    monkeypatch.delenv("PERSONAL_BILLING_MODE", raising=False)
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_fixture")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_test_fixture")
    assert ops.billing_enabled()
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_live_fixture")
    assert not ops.billing_enabled()
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_fixture")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_live_fixture")
    assert not ops.billing_enabled()
    monkeypatch.setenv("PERSONAL_BILLING_MODE", "enabled")
    assert ops.billing_enabled()
    monkeypatch.setenv("PERSONAL_BILLING_MODE", "disabled")
    assert not ops.billing_enabled()


def run_with_db(case):
    uri = os.environ.get("IMLADRI_BILLING_TEST_MONGO")
    if not uri:
        pytest.skip("Set IMLADRI_BILLING_TEST_MONGO for a local MongoDB")
    assert urlsplit(uri).hostname in ("localhost", "127.0.0.1", "::1")

    async def run():
        client = AsyncIOMotorClient(uri)
        db = client["imladri_ops_test_" + uuid.uuid4().hex]
        try:
            await case(db)
        finally:
            await client.drop_database(db.name)
            client.close()
    asyncio.run(run())


def test_concurrent_daily_and_per_member_ai_limits(monkeypatch):
    monkeypatch.setenv("AI_DAILY_TOKEN_LIMIT", "960")  # 3 x (128 framing + 192 output)
    monkeypatch.setenv("AI_MEMBER_HOURLY_CALL_LIMIT", "40")

    async def test(db):
        reservations = await asyncio.gather(*(ops.reserve_tokens(db, "", "", False, 192) for _ in range(25)),
                                             return_exceptions=True)
        allowed = [r for r in reservations if isinstance(r, tuple)]
        assert len(allowed) == 3
        assert all(r.status_code == 503 for r in reservations if isinstance(r, HTTPException))
        await ops.settle_tokens(db, allowed[0], {"total_tokens": 10})
        assert (await db.ai_daily_usage.find_one({}))["tokens"] == 650
        with pytest.raises(HTTPException):
            await ops.reserve_tokens(db, "", "", True, 192)
        monkeypatch.setenv("AI_MEMBER_HOURLY_CALL_LIMIT", "2")
        attempts = await asyncio.gather(*(ops.reserve_member_call(db, "a") for _ in range(12)), return_exceptions=True)
        assert len([bucket for bucket in attempts if isinstance(bucket, str)]) == 2
        assert all(exc.status_code == 429 for exc in attempts if isinstance(exc, Exception))
        await ops.reserve_member_call(db, "another")
    run_with_db(test)


def test_operation_endpoints_and_invoice_gap(monkeypatch):
    from backend import server
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_fixture")
    monkeypatch.setenv("OPS_CHECK_SECRET", "ops-test-key")
    monkeypatch.setenv("PERSONAL_BILLING_MODE", "enabled")
    monkeypatch.setattr(ops, "check_ai", AsyncMock(return_value=True))
    monkeypatch.setattr(ops, "check_stripe", lambda: True)
    invoice = {"id": "in_renew", "billing_reason": "subscription_cycle", "subscription": "sub_personal",
               "subscription_details":{"metadata":{"user_id":"member", "lookup_key":stripe_plan.LOOKUP_KEY}}}
    foreign = {"id":"in_other", "billing_reason":"subscription_cycle", "subscription":"sub_market",
               "subscription_details":{"metadata":{"user_id":"member", "lookup_key":"market_monthly"}}}
    monkeypatch.setattr(ops, "recent_invoices", lambda since, until: [foreign, invoice])
    headers = {"Authorization": "Bearer ops-test-key"}

    async def test(db):
        monkeypatch.setattr(server, "db", db)
        await db.users.insert_many([{"user_id":"member","role":"member","stripe_subscription_id":"sub_personal"},
                                    {"user_id":"staff","role":"admin"}])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as http:
            assert (await http.get("/api/ready")).status_code == 200
            assert (await http.get("/api/admin/operations")).status_code == 401
            dependency = server.app.dependency_overrides
            monkeypatch.setitem(dependency, server.current_member, lambda: {"user_id":"staff","role":"admin"})
            pending = await http.get("/api/admin/operations")
            assert pending.headers["cache-control"] == "no-store" and pending.json()["checked_at"] is None
            assert not pending.json()["checks"]
            assert (await http.post("/api/ops/check")).status_code == 401
            assert (await http.post("/api/ops/check",headers={"Authorization":"Bearer wrong"})).status_code == 401
            for _ in range(2):
                result = await http.post("/api/ops/check",headers=headers)
                assert result.status_code == 503 and result.json()["checks"]["stripe_invoices"] is False
            assert await db.notifications.count_documents({"user_id":"staff","type":"operations"}) == 1
            alert = await db.notifications.find_one({"user_id":"staff","type":"operations"})
            assert alert["link"] == "/admin?view=operations"
            latest = (await http.get("/api/admin/operations")).json()
            assert latest["checked_at"] and not latest["stale"] and latest["checks"]["stripe_invoices"] is False
            assert await db.notifications.count_documents({"user_id":"member"}) == 0
            await db.payment_transactions.insert_one({"user_id":"member","stripe_subscription_id":"sub_personal",
                                                     "invoice_id":"in_renew","payment_status":"paid"})
            assert (await http.post("/api/ops/check",headers=headers)).status_code == 200
            await db.payment_transactions.delete_many({})
            monkeypatch.setenv("PERSONAL_BILLING_MODE", "disabled")
            monkeypatch.setattr(ops, "check_stripe", lambda: pytest.fail("billing disabled"))
            result = await http.post("/api/ops/check",headers=headers)
            assert result.status_code == 200 and "stripe" not in result.json()["checks"]
            disabled = (await http.get("/api/admin/operations")).json()
            assert not disabled["billing_enabled"] and "stripe" not in disabled["checks"]
            await db.ops_state.update_one({"_id": f"service_checks:{server.ENV}"}, {"$set": {
                "checked_at": datetime.now(timezone.utc) - timedelta(hours=1)}})
            assert (await http.get("/api/admin/operations")).json()["stale"] is True
            monkeypatch.setitem(dependency, server.current_member, lambda: {"user_id":"member","role":"member"})
            assert (await http.get("/api/admin/operations")).status_code == 403
            for url in ("/api/payments/checkout", "/api/payments/portal"):
                assert (await http.post(url,json={"origin_url":"http://localhost"})).status_code == 503
            plan = (await http.get("/api/upgrade")).json()
            assert plan["billing_enabled"] is False and plan["checkout_available"] is False
    run_with_db(test)


def test_application_charges_standalone_parse_and_ocr_to_shared_limit(monkeypatch):
    from backend import ai, server
    monkeypatch.setenv("AI_MEMBER_HOURLY_CALL_LIMIT", "1")
    monkeypatch.setenv("AI_DAILY_TOKEN_LIMIT", "1000000")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-key")
    monkeypatch.setattr(server, "_clerk_user", lambda uid: {"first_name":"Test","email_addresses":[]})
    monkeypatch.setitem(server.app.dependency_overrides, server.require_user, lambda: {"user_id":"user_ai"})
    client_type = httpx.AsyncClient
    calls = []

    def provider(request):
        calls.append(1)
        return httpx.Response(200, json={"choices":[{"finish_reason":"stop","message":{"content":'{"lines":[]}'}}],
                                         "usage":{"total_tokens":23}})
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kw: client_type(transport=httpx.MockTransport(provider), **kw))

    async def test(db):
        monkeypatch.setattr(server, "db", db)
        async with client_type(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as http:
            denied = await http.post("/api/quickstart/parse",json={"nickname":"Sample","conversation_text":"Hello"})
            assert denied.status_code == 402 and not calls
            now = int(time.time())
            await db.users.update_one({"user_id":"user_ai"}, {"$set": {"tier":"paid","stripe_subscription_id":"sub_fixture",
                "subscription_period_start":now-60, "subscription_period_end":now+86400}})
            parsed = await http.post("/api/quickstart/parse",json={"nickname":"Sample","conversation_text":"Hello"})
            assert parsed.status_code == 200
            ocr = await http.post("/api/ocr",json={"image_base64":"iVBORw0KGgo="})
            assert ocr.status_code == 429 and calls == [1]
            assert (await db.ai_daily_usage.find_one({}))["tokens"] == 23
    run_with_db(test)


def test_failed_monitor_alert_keeps_results_and_replaced_invoice_is_reconciled(monkeypatch):
    from backend import server
    monkeypatch.setenv("OPS_CHECK_SECRET", "ops-test-key")
    monkeypatch.setenv("PERSONAL_BILLING_MODE", "enabled")
    monkeypatch.setattr(ops, "check_ai", AsyncMock(return_value=True))
    monkeypatch.setattr(ops, "check_stripe", lambda: True)
    monkeypatch.setattr(ops, "recent_invoices", lambda *_: [
        {"id":"late-invoice","billing_reason":"subscription_cycle","subscription":"sub_former"}])
    monkeypatch.setattr(server, "notify_ops_admins", AsyncMock(side_effect=RuntimeError("fixture delivery error")))
    async def test(db):
        monkeypatch.setattr(server, "db", db)
        await db.users.insert_one({"user_id":"member","stripe_subscription_id":"sub_replacement"})
        await db.payment_transactions.insert_one({"user_id":"member","stripe_subscription_id":"sub_former","kind":"purchase","payment_status":"paid"})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app, raise_app_exceptions=False), base_url="http://localhost") as http:
            assert (await http.post("/api/ops/check", headers={"Authorization":"Bearer ops-test-key"})).status_code == 500
            monkeypatch.setitem(server.app.dependency_overrides, server.current_member, lambda: {"user_id":"staff","role":"admin"})
            status = (await http.get("/api/admin/operations")).json()
            assert status["checked_at"] and status["checks"]["stripe_invoices"] is False
            await db.payment_transactions.insert_one({"user_id":"member","stripe_subscription_id":"sub_former",
                "kind":"renewal", "invoice_id":"late-invoice","payment_status":"paid"})
            assert (await http.post("/api/ops/check", headers={"Authorization":"Bearer ops-test-key"})).status_code == 200
    run_with_db(test)

def test_global_token_refusal_does_not_spend_the_members_hourly_allowance(monkeypatch):
    monkeypatch.setenv("AI_DAILY_TOKEN_LIMIT", "450")
    monkeypatch.setenv("AI_MEMBER_HOURLY_CALL_LIMIT", "1")

    async def check(db):
        first = await ops.reserve_tokens(db, "", "", False, 192)
        token = ops.member_id.set("waiting-member")
        try:
            for _ in range(2):
                with pytest.raises(HTTPException) as refused:
                    await ops.reserve_tokens(db, "", "", False, 192)
                assert refused.value.status_code == 503
            row = await db.ai_request_usage.find_one({"user_id": "waiting-member"})
            assert row["calls"] == 0
            await ops.settle_tokens(db, first, {"total_tokens": 10})
            await ops.reserve_tokens(db, "", "", False, 192)
            assert (await db.ai_request_usage.find_one({"_id": row["_id"]}))["calls"] == 1
            assert (await db.ai_daily_usage.find_one({}))["requests"] == 2
        finally:
            ops.member_id.reset(token)
    run_with_db(check)


def test_zero_total_renewal_does_not_mask_a_missing_positive_invoice(monkeypatch):
    zero = {"id": "in_covered", "total": 0, "subscription": "sub_one", "billing_reason": "subscription_cycle"}
    paid = {**zero, "id": "in_paid", "total": 1200}
    invoices = [zero]
    monkeypatch.setattr(ops, "recent_invoices", lambda *_: list(invoices))

    async def check(db):
        await db.users.insert_one({"user_id": "member", "stripe_subscription_id":"sub_one"})
        assert await ops.invoices_recorded(db)
        invoices.append(paid)
        assert not await ops.invoices_recorded(db)
        await db.payment_transactions.insert_one({"user_id": "member", "stripe_subscription_id":"sub_one",
                                                  "invoice_id": "in_paid", "payment_status": "paid"})
        assert await ops.invoices_recorded(db)
    run_with_db(check)
