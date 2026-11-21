"""Complimentary product access uses ordinary member authorization and no checkout."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend import free_plan, membership_admin
from backend.tests.test_free_plan_quota import run_case


def account(uid="complimentary", active=True):
    return {"user_id": uid, "email": uid + "@fixture.invalid", "env": "preview", "role": "member",
            "tier": "unsubscribed", "about": {"demographics": {"age": "30"}},
            "complimentary_entitlement": {"version": free_plan.COMPLIMENTARY_VERSION,
                                           "id": str(uuid4()), "active": active}}


def test_comp_member_has_real_product_routes_and_cannot_manage_accounts(monkeypatch):
    from backend import server

    async def scenario(db):
        member = account()
        await db.users.insert_one(member)
        monkeypatch.setattr(server, "db", db)
        async def current():
            return await db.users.find_one({"user_id": member["user_id"]})
        server.app.dependency_overrides[server.current_member] = current
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="https://fixture.invalid") as client:
                response = await client.get("/api/me")
                assert response.status_code == 200
                me = response.json()
                assert me["role"] == "member" and me["tier"] == "complimentary"
                assert me["personal_access"] and me["complimentary_access"]
                assert me["usage"]["unlimited"] and me["membership_plan_id"] is None
                assert (await client.get("/api/quiz")).status_code == 200
                assert (await client.get("/api/your-type")).status_code == 200
                assert (await client.get("/api/users")).status_code == 403
                assert (await client.get("/api/admin/membership/summary")).status_code == 403
                body = {"expected_member": "other", "request_id": str(uuid4()), "enabled": True, "reason": "Unauthorized fixture"}
                assert (await client.patch("/api/users/other/complimentary-access", json=body)).status_code == 403
        finally:
            server.app.dependency_overrides.clear()
    run_case(scenario)


def test_me_update_cannot_self_grant_or_set_admin_role(monkeypatch):
    from backend import server

    async def scenario(db):
        member = {"user_id": "ordinary", "role": "member", "env": server.ENV, "tier": "unsubscribed"}
        await db.users.insert_one(member)
        monkeypatch.setattr(server, "db", db)
        async def current():
            return await db.users.find_one({"user_id": "ordinary"})
        server.app.dependency_overrides[server.current_member] = current
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="https://fixture.invalid") as client:
                response = await client.patch("/api/me", json={"role": "admin", "complimentary_entitlement": account()["complimentary_entitlement"]})
                assert response.status_code in (200, 422)
            saved = await db.users.find_one({"user_id": "ordinary"})
            assert saved["role"] == "member" and not free_plan.has_personal_access(saved)
            assert "complimentary_entitlement" not in saved
        finally:
            server.app.dependency_overrides.clear()
    run_case(scenario)


def test_comp_member_cannot_create_any_payable_session(monkeypatch):
    from backend import server
    member = account()
    request = Request({"type": "http", "scheme": "https", "server": ("fixture.invalid", 443), "path": "/", "headers": []})
    def forbidden(*args, **kwargs):
        pytest.fail("Complimentary access must not reach Stripe")
    monkeypatch.setattr(server.stripe.checkout.Session, "create", forbidden)
    monkeypatch.setattr(server.stripe_membership, "catalog", forbidden)

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        operations = (
            lambda: server.create_checkout(server.CheckoutIn(purchase_policy_accepted=True, purchase_policy_version="imladri-purchases-2026-11-v1", origin_url="https://fixture.invalid"), request, member),
            lambda: server.upgrade_subscription(server.UpgradeIn(origin_url="https://fixture.invalid", offer="plus_250"), request, member),
            lambda: server.purchase_refill(server.RefillIn(origin_url="https://fixture.invalid"), request, member),
            lambda: server.accept_partner_year(server.AnnualAcceptance(accept=True), member),
        )
        for operation in operations:
            with pytest.raises(HTTPException) as error:
                await operation()
            assert error.value.status_code == 409
    run_case(scenario)


def test_upgrade_info_reports_comp_access_without_price_api_calls(monkeypatch):
    from backend import server
    async def settings():
        return {"upgrade": {}, "people_limit": None}
    def forbidden(*args, **kwargs):
        pytest.fail("Complimentary plan display must not fetch sale prices")
    monkeypatch.setattr(server, "get_settings", settings)
    monkeypatch.setattr(server.stripe_membership, "catalog", forbidden)

    async def scenario(db):
        member = account()
        await db.users.insert_one(member)
        monkeypatch.setattr(server, "db", db)
        result = await server.upgrade_info(member)
        assert result["complimentary_access"] and result["usage"]["unlimited"]
        assert result["tier"] == "complimentary" and result["membership_plan_id"] is None
        assert not result["checkout_available"] and not result["topup_available"] and not result["upgrade_available"]
        assert all(not row["available"] and not row["upgrade_available"] for row in result["plans"])
    run_case(scenario)


def test_admin_grant_and_directory_keep_usage_pause_and_role_truthful(monkeypatch):
    from backend import server

    async def scenario(db):
        member = account(active=False)
        member.update(env=server.ENV, replies_paused=True)
        await db.users.insert_one(member)
        monkeypatch.setattr(server, "db", db)
        admin = {"user_id": "fixture-staff", "role": "admin", "env": server.ENV}
        request_id = str(uuid4())
        body = membership_admin.ComplimentaryAccess(expected_member=member["user_id"], request_id=request_id,
                                                   enabled=True, reason="Owner authorized fixture grant")
        result = await server.change_complimentary_access(member["user_id"], body, admin)
        assert result["complimentary"]
        page = await server.list_users(server.Response(), admin, q="", after="", limit=10, status="complimentary")
        assert len(page["users"]) == 1
        row = page["users"][0]
        assert row["role"] == "member" and row["membership_status"] == "complimentary"
        assert row["usage"]["unlimited"] and row["replies_paused"]
        assert row["complimentary_entitlement"]["id"] == request_id
        saved = await db.users.find_one({"user_id": member["user_id"]})
        assert saved["role"] == "member" and saved["tier"] == "unsubscribed"
        assert saved["admin_member_actions"][-1]["actor"] == admin["user_id"]
        assert "stripe_subscription_id" not in saved
    run_case(scenario)


def test_grant_winning_checkout_race_prevents_payable_session(monkeypatch):
    from backend import server
    import stripe
    monkeypatch.setenv("PERSONAL_BILLING_MODE", "enabled")
    monkeypatch.setenv("STRIPE_CHECKOUT_MODE", "managed")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://fixture.invalid")
    monkeypatch.setattr(stripe, "api_key", "sk_test_fixture")
    price = SimpleNamespace(id="price_fixture", unit_amount=1200, currency="usd")
    monkeypatch.setattr(server.partner_offers, "price_for", lambda _: price)
    def forbidden(*args, **kwargs):
        pytest.fail("A grant winning the atomic checkout race must block Stripe Session.create")
    monkeypatch.setattr(server, "make_checkout_session", forbidden)

    async def scenario(db):
        member = {"user_id": "race", "role": "member", "tier": "unsubscribed", "env": server.ENV}
        await db.users.insert_one(member)
        original = db.users
        grant_id = str(uuid4())
        class Users:
            def __getattr__(self, name):
                return getattr(original, name)
            async def update_one(self, query, update, **kwargs):
                if "checkout_attempt" in update.get("$set", {}):
                    body = membership_admin.ComplimentaryAccess(expected_member="race", request_id=grant_id,
                        enabled=True, reason="Synthetic race")
                    await membership_admin.set_complimentary_access(db, {"env": server.ENV}, "race", body, "fixture-staff")
                return await original.update_one(query, update, **kwargs)
        class Database:
            users = Users()
            def __getattr__(self, name):
                return getattr(db, name)
        monkeypatch.setattr(server, "db", Database())
        request = Request({"type": "http", "scheme": "https", "server": ("fixture.invalid", 443), "path": "/", "headers": []})
        with pytest.raises(HTTPException) as error:
            await server.create_checkout(server.CheckoutIn(purchase_policy_accepted=True, purchase_policy_version="imladri-purchases-2026-11-v1", origin_url="https://fixture.invalid"), request, member)
        assert error.value.status_code == 409
        saved = await original.find_one({"user_id": "race"})
        assert free_plan.complimentary_access(saved) and "checkout_attempt" not in saved
    run_case(scenario)
