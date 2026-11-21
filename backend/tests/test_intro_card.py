"""Card-first billing on isolated PostgreSQL and explicit fictional Stripe doubles."""
import asyncio
import calendar
from copy import deepcopy
import os
from datetime import timedelta
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
import stripe
from fastapi import HTTPException

from backend import intro_access, intro_card as card, free_plan
from backend.tests.test_research_notices import run_case


class TwoRecoveryTicksComplete(BaseException):
    pass


async def run_two_recovery_ticks(monkeypatch, db):
    original_sleep = asyncio.sleep
    ticks = []
    async def bounded_sleep(seconds):
        if seconds != 30:
            return await original_sleep(seconds)
        ticks.append(seconds)
        if len(ticks) == 2:
            raise TwoRecoveryTicksComplete()
    with monkeypatch.context() as patch:
        patch.setattr(card.asyncio, "sleep", bounded_sleep)
        with pytest.raises(TwoRecoveryTicksComplete):
            await card.recover(db, None)
    assert ticks == [30, 30]


@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    for key, value in {"INTRO_ACCESS_ENABLED": "1", "INTRO_ABUSE_HMAC_SECRET": "fictional-card-first-hmac-01234567890123456789",
        "INTRO_ELIGIBLE_SINCE": "2026-01-01T00:00:00+00:00", "INTRO_TURNSTILE_SITE_KEY": "fictional-site",
        "INTRO_TURNSTILE_SECRET_KEY": "fictional-secret", "INTRO_ALLOWED_HOSTNAMES": "fixture.invalid",
        "INTRO_CARD_FIRST_VERIFIED_STRATEGY": card.STRATEGY, "INTRO_CARD_FIRST_PROVIDER_PROOF_SHA256": "a" * 64,
        "INTRO_CARD_FIRST_COUPON_ID": "coupon_fixture", "STRIPE_CHECKOUT_MODE": "managed", "PERSONAL_BILLING_MODE": "enabled"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(stripe, "api_key", "sk_test_fictional_no_network")
    monkeypatch.setattr(intro_access, "validate_challenge", AsyncMock())


def member(uid="owner", **extra):
    return {"user_id": uid, "role": "member", "tier": "unsubscribed", "env": "fixture",
        "created_at": card.now().isoformat(), **extra}


def identity(uid="owner"):
    return {"id": uid, "created_at": int(card.now().timestamp() * 1000), "primary_email_address_id": "mail",
        "email_addresses": [{"id": "mail", "email_address": uid + "@fixture.invalid", "verification": {"status": "verified"}}]}


class Provider:
    def __init__(self, fingerprint="fictional-card"):
        self.calls = [];self.fingerprint = fingerprint;self.sid = "cs_" + uuid4().hex
        self.sub = {};self.session_value = {};self.fail_convert = False;self.fail_cancel = False

    def price(self, offer):
        amounts = {"standard": 1200, "plus_250": 2000, "plus_500": 4000, "partner_monthly": 1000}
        return {"id": "price_" + offer, "unit_amount": amounts[offer], "currency": "usd", "livemode": False, "product": "prod_fixture"}

    def coupon(self, identifier):
        return {"id": identifier, "valid": True, "percent_off": 100, "duration": "forever", "amount_off": None,
            "livemode": False, "applies_to": {"products": ["prod_fixture"]}}

    def create(self, params, key):
        self.calls.append(("create", deepcopy(params), key))
        metadata = params["subscription_data"]["metadata"]
        self.sub = {"id": "sub_" + self.sid[3:], "customer": "cus_" + self.sid[3:], "metadata": deepcopy(metadata), "status": "active", "livemode": False, "billing_mode": {"type": "classic"},
            "items": {"data": [{"quantity": 1, "price": params["line_items"][0]["price"]}]},
            "discounts": [{"source": {"coupon": "coupon_fixture"}, "end": None}], "trial_end": None,
            "pending_setup_intent": None,
            "default_payment_method": {"id": "pm_fixture", "type": "card", "livemode": False,
                "customer": "cus_" + self.sid[3:], "card": {"fingerprint": self.fingerprint, "checks": {"cvc_check": "pass"}}}}
        self.sub["latest_invoice"] = {"id": "in_zero", "total": 0, "amount_paid": 0, "status": "paid", "subscription": self.sub["id"]}
        self.session_value = {"id": self.sid, "url": "https://checkout.stripe.com/fictional", "status": "open", "mode": "subscription",
            "metadata": deepcopy(metadata), "managed_payments": {"enabled": True}, "payment_method_collection": "always", "livemode": False,
            "payment_status": "paid", "amount_total": 0, "currency": "usd", "subscription": self.sub["id"], "customer": self.sub["customer"]}
        return deepcopy(self.session_value)

    def session(self, sid):
        self.calls.append(("session", sid));return deepcopy(self.session_value)

    def subscription(self, sid):
        self.calls.append(("subscription", sid));return deepcopy(self.sub)

    def convert(self, sid, op):
        self.calls.append(("convert", sid, deepcopy(op)))
        self.sub["discounts"] = [];self.sub["metadata"]["imladri_intro_conversion"] = op["id"]
        if self.fail_convert:
            raise stripe.APIConnectionError("fictional ambiguous transport")
        return deepcopy(self.sub)

    def cancel(self, sid, key):
        self.calls.append(("cancel", sid, key))
        self.sub["status"] = "canceled"
        if self.fail_cancel:
            raise stripe.APIConnectionError("fictional ambiguous cancellation")
        return deepcopy(self.sub)

    def expire(self, sid, key):
        self.calls.append(("expire", sid, key));self.session_value["status"] = "expired";return self.session_value


async def setup(db, provider, uid="owner", *, complete=True, plan="plus_100"):
    current = member(uid)
    await db.users.insert_one(current)
    q = (await card.quotes(db, current))[plan]
    body = card.Setup(expected_member=uid, plan_id=plan, expected_price_cents=q["price_cents"], expected_messages=q["messages"],
        request_id=str(uuid4()), authorization_version=card.VERSION, authorize_after_messages=True, policy_accepted=True,
        authorization_text_sha256=q["authorization_text_sha256"], quote_sha256=q["quote_sha256"],
        origin_url="https://fixture.invalid", challenge_token="fictional-" + uuid4().hex)
    result = await card.setup(db, current, identity(uid), body, "https://fixture.invalid", provider=provider)
    current = await db.users.find_one({"user_id": uid})
    if complete:
        provider.session_value["status"] = "complete"
        current = await card.confirm(db, current, result["session_id"], provider=provider)
    return current, body


def test_cardless_claim_is_retired_without_identity_or_entitlement():
    async def case(db):
        await db.users.insert_one(member())
        with pytest.raises(HTTPException) as error:
            await intro_access.claim(db, member(), identity(), intro_access.Claim(expected_member="owner"))
        assert error.value.status_code == 409 and await db.intro_identities.count_documents({}) == 0
        assert not free_plan.has_personal_access(member(intro_entitlement={"version": intro_access.VERSION,
            "id": str(uuid4()), "active": True, "granted_at": card.now().isoformat()}))
    run_case(case)


@pytest.mark.parametrize("plan,cents,messages", [("plus_100", 1200, 100), ("plus_250", 2000, 250), ("plus_500", 4000, 500)])
def test_hosted_card_before_ten_and_zero_paid_never_grants_paid(plan, cents, messages):
    async def case(db):
        provider = Provider();current, body = await setup(db, provider, plan=plan)
        assert card.active(current) and intro_access.access(current)
        assert current["tier"] == "unsubscribed" and not current.get("subscription_period_start")
        assert current["intro_reply_totals"] == {"claimed": 0, "used": 0}
        row = current["intro_billing"]
        assert row["selected_plan"]["price_cents"] == cents and row["selected_plan"]["messages"] == messages
        assert row["authorization"]["text_sha256"] == body.authorization_text_sha256
        params = provider.calls[0][1]
        assert params["payment_method_collection"] == "always" and params["managed_payments"]["enabled"]
        assert "trial_end" not in str(params) and "allow_promotion_codes" not in params
        tx = await db.payment_transactions.find_one({"session_id": provider.sid})
        assert tx["kind"] == "intro_setup" and tx["payment_status"] == "pending" and tx["amount"] == 0
        again = await card.confirm(db, current, provider.sid, provider=provider)
        assert again["intro_entitlement"] == current["intro_entitlement"]
        assert not any(c[0] == "convert" for c in provider.calls)
    run_case(case)


@pytest.mark.parametrize("field,value", [("managed_payments", {"enabled": False}), ("payment_method_collection", "if_required"), ("amount_total", 1200)])
def test_unverified_checkout_cannot_grant(field, value):
    async def case(db):
        provider = Provider();current, _ = await setup(db, provider, complete=False)
        provider.session_value.update(status="complete", **{field: value})
        with pytest.raises(HTTPException):
            await card.confirm(db, current, provider.sid, provider=provider)
        saved = await db.users.find_one({"user_id": "owner"})
        assert not saved.get("intro_entitlement") and not card.active(saved)
    run_case(case)


def test_duplicate_card_does_not_grant_a_second_account():
    async def case(db):
        first, _ = await setup(db, Provider(), uid="first")
        provider = Provider();second, _ = await setup(db, provider, uid="second", complete=False)
        provider.session_value["status"] = "complete"
        with pytest.raises(HTTPException) as error:
            await card.confirm(db, second, provider.sid, provider=provider)
        assert error.value.status_code == 403
        saved = await db.users.find_one({"user_id": "second"})
        assert not saved.get("intro_entitlement") and saved["intro_billing"]["state"] == "review_required"
        assert saved["intro_billing"]["subscription_id"] == provider.sub["id"]
        assert card.status_fields(saved)["billing"]["can_cancel"]
        cancelled = await card.cancel(db, saved, card.Cancel(expected_member="second", request_id=str(uuid4()),
            revision=saved["intro_billing"]["revision"]), provider=provider)
        assert cancelled["intro_billing"]["state"] == "cancelled" and not card.required(cancelled)
        assert "fictional-card" not in str(await db.intro_identities.find({}).to_list(None))
    run_case(case)


def test_flexible_mode_is_rejected_without_grant_and_remains_cancelable():
    async def case(db):
        provider = Provider();current, _ = await setup(db, provider, complete=False)
        provider.session_value["status"] = "complete";provider.sub["billing_mode"] = {"type": "flexible"}
        with pytest.raises(HTTPException):
            await card.confirm(db, current, provider.sid, provider=provider)
        current = await db.users.find_one({"user_id": "owner"})
        assert not current.get("intro_entitlement") and card.status_fields(current)["billing"]["can_cancel"]
        current = await card.cancel(db, current, card.Cancel(expected_member="owner", request_id=str(uuid4()),
            revision=current["intro_billing"]["revision"]), provider=provider)
        assert current["intro_billing"]["state"] == "cancelled"
    run_case(case)


def test_global_billing_gate_blocks_setup_and_durable_first_charge_but_not_cancellation(monkeypatch):
    async def case(db):
        provider = Provider();current, _ = await setup(db, provider)
        current = await tenth(db, current)
        monkeypatch.setenv("PERSONAL_BILLING_MODE", "disabled")
        await card.dispatch_due(db, "owner", provider=provider)
        assert not any(x[0] == "convert" for x in provider.calls)
        current = await card.cancel(db, current, card.Cancel(expected_member="owner", request_id=str(uuid4()),
            revision=current["intro_billing"]["revision"]), provider=provider)
        assert current["intro_billing"]["state"] == "cancelled"
        fresh = member("new");await db.users.insert_one(fresh)
        assert card.required(fresh) and not card.configured()
        q = (await card.quotes(db, fresh))["plus_100"]
        body = card.Setup(expected_member="new", plan_id="plus_100", expected_price_cents=1200, expected_messages=100,
            request_id=str(uuid4()), authorization_version=card.VERSION, authorize_after_messages=True, policy_accepted=True,
            authorization_text_sha256=q["authorization_text_sha256"], quote_sha256=q["quote_sha256"], origin_url="https://fixture.invalid")
        with pytest.raises(HTTPException) as error:
            await card.setup(db, fresh, identity("new"), body, "https://fixture.invalid", provider=provider)
        assert error.value.status_code == 503
    run_case(case)


def test_expire_completion_race_binds_subscription_for_new_explicit_cancel():
    async def case(db):
        provider = Provider();current, _ = await setup(db, provider, complete=False)
        def completed_during_expire(sid, key):
            provider.calls.append(("expire", sid, key));provider.session_value["status"] = "complete"
            return provider.session_value
        provider.expire = completed_during_expire
        current = await card.cancel(db, current, card.Cancel(expected_member="owner", request_id=str(uuid4()),
            revision=current["intro_billing"]["revision"]), provider=provider)
        assert current["intro_billing"]["cancel_stage"] == "needs_subscription_cancel"
        assert not intro_access.access(current) and card.status_fields(current)["billing"]["can_cancel"]
        current = await card.cancel(db, current, card.Cancel(expected_member="owner", request_id=str(uuid4()),
            revision=current["intro_billing"]["revision"]), provider=provider)
        assert current["intro_billing"]["state"] == "cancelled"
        assert len([x for x in provider.calls if x[0] == "expire"]) == len([x for x in provider.calls if x[0] == "cancel"]) == 1
    run_case(case)


def test_couples_and_admin_internal_projections_preserve_active_intro_without_leaking_agreement():
    from backend import couples
    async def case(db):
        current, _ = await setup(db, Provider())
        await db.users.update_one({"user_id": "owner"}, {"$set": {"intro_reply_totals": {"claimed": 3, "used": 3}}})
        projected = await couples.member_info(db, "owner", "fixture")
        assert free_plan.has_member_access(projected) and free_plan.reply_usage(projected)["messages_remaining"] == 7
        summary = await intro_access.summary(db, "fixture")
        assert summary["totals"]["active_accounts"] == 1 and summary["totals"]["used_messages"] == 3
        assert "card_identity" not in str(summary) and "I authorize Imladri" not in str(summary)
    run_case(case)


def test_setup_confirm_status_cancel_http_uses_canonical_quote_and_reads_only_on_refresh(monkeypatch):
    import httpx
    from fastapi import FastAPI
    async def case(db):
        provider = Provider();monkeypatch.setattr(card, "Provider", lambda: provider)
        await db.users.insert_one(member())
        async def current(): return await db.users.find_one({"user_id": "owner"})
        async def clerk(uid): return identity(uid)
        async def admin(): return {"user_id": "staff", "role": "admin", "env": "fixture"}
        async def refresh(user): return await card.refresh(db, user, provider=provider)
        app = FastAPI();app.include_router(intro_access.make_router(lambda: db, current, admin, clerk, refresh))
        app.include_router(card.make_router(lambda: db, current, clerk, lambda url, req: url, None))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://fixture.invalid") as client:
            initial = await client.get("/me/intro-access");q = initial.json()["quotes"]["plus_100"]
            assert q["authorization_text"] == card.agreement_text(q, 10)
            payload = {"expected_member": "owner", "plan_id": "plus_100", "expected_price_cents": 1200, "expected_messages": 100,
                "request_id": str(uuid4()), "authorization_version": card.VERSION, "authorize_after_messages": True, "policy_accepted": True,
                "authorization_text_sha256": q["authorization_text_sha256"], "quote_sha256": q["quote_sha256"],
                "origin_url": "https://fixture.invalid", "challenge_token": "fictional"}
            started = await client.post("/me/intro-access/setup", json=payload)
            assert started.status_code == 200
            assert (await client.get("/me/intro-access")).json()["messages_remaining"] == 0
            provider.session_value["status"] = "complete"
            confirmed = await client.post("/me/intro-access/confirm", json={"expected_member": "owner", "session_id": provider.sid})
            assert confirmed.status_code == 200 and confirmed.json()["messages_remaining"] == 10
            refreshed = await client.get("/me/intro-access")
            assert refreshed.json()["billing"]["state"] == "active" and not any(x[0] == "convert" for x in provider.calls)
            cancelled = await client.post("/me/intro-access/cancel", json={"expected_member": "owner", "request_id": str(uuid4()),
                "revision": refreshed.json()["billing"]["revision"]})
            assert cancelled.status_code == 200 and cancelled.json()["billing"]["state"] == "cancelled"
            assert cancelled.json()["messages_remaining"] == 0 and not cancelled.json()["billing"]["can_cancel"]
    run_case(case)


def test_provider_uses_only_classic_existing_managed_subscription_with_stable_key(monkeypatch):
    calls = []
    monkeypatch.setattr(stripe.Subscription, "modify", lambda sid, **kw: calls.append((sid, kw)) or {})
    card.Provider().convert("sub_fixture", {"id": "op_fixture", "key": "stable_fixture"})
    assert calls == [("sub_fixture", {"discounts": "", "billing_cycle_anchor": "now", "proration_behavior": "none",
        "payment_behavior": "allow_incomplete", "metadata": {"imladri_intro_conversion": "op_fixture"},
        "idempotency_key": "stable_fixture", "stripe_version": card.API_VERSION})]


def test_legacy_partial_grant_requires_new_authorization_and_preserves_used_counts(monkeypatch):
    async def case(db):
        legacy = {"version": intro_access.VERSION, "id": str(uuid4()), "active": True, "granted_at": card.now().isoformat()}
        current = member(intro_entitlement=legacy, intro_reply_totals={"claimed": 6, "used": 6})
        await db.users.insert_one(current)
        key = intro_access.digest("email", "owner@fixture.invalid")
        await db.intro_identities.insert_one({"_id": key, "owner": intro_access.digest("user", "owner"), "grant": legacy})
        assert not intro_access.access(current) and (await card.quotes(db, current))["plus_100"]["remaining_intro_messages"] == 4
        q = (await card.quotes(db, current))["plus_100"]
        body = card.Setup(expected_member="owner", plan_id="plus_100", expected_price_cents=1200, expected_messages=100,
            request_id=str(uuid4()), authorization_version=card.VERSION, authorize_after_messages=True, policy_accepted=True,
            authorization_text_sha256=q["authorization_text_sha256"], quote_sha256=q["quote_sha256"], origin_url="https://fixture.invalid", challenge_token="fixture")
        provider = Provider();await card.setup(db, current, identity(), body, "https://fixture.invalid", provider=provider)
        provider.session_value["status"] = "complete"
        current = await card.confirm(db, await db.users.find_one({"user_id": "owner"}), provider.sid, provider=provider)
        assert current["intro_entitlement"] == legacy and current["intro_reply_totals"] == {"claimed": 6, "used": 6}
        assert (await intro_access.status(db, current))["messages_remaining"] == 4
        assert "remaining 4" in current["intro_billing"]["authorization"]["text"]
    run_case(case)


def test_closure_cancels_nested_intro_resources_and_unknown_setup_blocks_erasure(monkeypatch):
    from backend import accounts
    async def case(db):
        provider = Provider();current, _ = await setup(db, provider)
        seen = []
        monkeypatch.setattr(accounts, "close_billing", lambda subs, sessions, uid: seen.append((set(subs), set(sessions), uid)))
        await accounts.finish_closure(db, "owner")
        assert seen == [({provider.sub["id"]}, {provider.sid}, "owner")]
        assert await db.users.find_one({"user_id": "owner"}) is None
        await db.users.insert_one(member("unresolved", intro_billing={"version": card.VERSION, "setup_stage": "unknown"}))
        with pytest.raises(HTTPException) as error:
            await accounts.finish_closure(db, "unresolved")
        assert error.value.status_code == 503 and await db.users.find_one({"user_id": "unresolved", "closing": True})
    run_case(case)


def test_calendar_month_and_actual_item_period_are_both_required():
    start = int(card.now().replace(day=15, microsecond=0).timestamp())
    from datetime import datetime, timezone
    initial = datetime.fromtimestamp(start, timezone.utc)
    year, month = initial.year + (initial.month == 12), initial.month % 12 + 1
    end = int(initial.replace(year=year, month=month).timestamp())
    sub = {"items": {"data": [{"current_period_start": start, "current_period_end": end}]}}
    assert card.full_month_period(sub, (start, end))
    assert not card.full_month_period(sub, (start, start + 60))
    sub["items"]["data"][0]["current_period_end"] = start + 60
    assert not card.full_month_period(sub, (start, start + 60))


async def tenth(db, current):
    receipt = {"kind": "exchange", "scope": "fictional", "result": str(uuid4()), "pending": True, "pool": "intro",
        "intro_used_before": 9, "intro_agreement_id": current["intro_billing"]["id"]}
    await db.users.update_one({"user_id": current["user_id"]}, {"$set": {"intro_reply_totals": {"claimed": 10, "used": 9},
        "free_reply_requests.last": receipt, "intro_active_request": {"id": receipt["result"]}}})
    await asyncio.gather(*(free_plan.settle(db, current["user_id"], "last", receipt, True) for _ in range(2)))
    return await db.users.find_one({"user_id": current["user_id"]})


def test_tenth_delivery_atomically_queues_one_conversion_and_unknown_never_reposts():
    async def case(db):
        provider = Provider();current, _ = await setup(db, provider)
        current = await tenth(db, current)
        assert current["intro_reply_totals"]["used"] == 10
        assert current["intro_billing"]["conversion"]["stage"] == "due" and not intro_access.access(current)
        provider.fail_convert = True
        await card.dispatch_due(db, "owner", provider=provider)
        await card.dispatch_due(db, "owner", provider=provider)
        current = await card.refresh(db, await db.users.find_one({"user_id": "owner"}), provider=provider)
        assert current["intro_billing"]["conversion"]["stage"] == "unknown"
        assert len([x for x in provider.calls if x[0] == "convert"]) == 1
        assert current["tier"] == "unsubscribed"  # old initial paid-$0 invoice is ignored
    run_case(case)


def test_cancellation_wins_before_tenth_and_keeps_saved_counts_without_charge():
    async def case(db):
        provider = Provider();current, _ = await setup(db, provider)
        body = card.Cancel(expected_member="owner", request_id=str(uuid4()), revision=current["intro_billing"]["revision"])
        current = await card.cancel(db, current, body, provider=provider)
        assert current["intro_billing"]["state"] == "cancelled" and not intro_access.access(current)
        current = await tenth(db, current)
        assert current["intro_reply_totals"]["used"] == 10 and not current["intro_billing"].get("conversion")
        await card.dispatch_due(db, "owner", provider=provider)
        assert not any(x[0] == "convert" for x in provider.calls)
        assert current["intro_entitlement"]
    run_case(case)


def test_two_accounts_confirming_the_same_card_concurrently_only_one_grant():
    async def case(db):
        providers = [Provider(), Provider()]
        rows = [await setup(db, providers[i], uid="racer" + str(i), complete=False) for i in range(2)]
        for provider in providers:
            provider.session_value["status"] = "complete"
        results = await asyncio.gather(*(card.confirm(db, rows[i][0], providers[i].sid, provider=providers[i]) for i in range(2)), return_exceptions=True)
        assert sum(isinstance(result, dict) and card.active(result) for result in results) == 1
        assert sum(isinstance(result, HTTPException) and result.status_code == 403 for result in results) == 1
        assert await db.users.count_documents({"intro_entitlement.version": intro_access.VERSION}) == 1
    run_case(case)


def test_failed_tenth_ai_refunds_reservation_and_never_queues_financial_operation():
    async def case(db):
        provider = Provider();current, _ = await setup(db, provider)
        receipt = {"kind": "exchange", "scope": "fictional", "result": str(uuid4()), "pending": True, "pool": "intro",
            "intro_used_before": 9, "intro_agreement_id": current["intro_billing"]["id"]}
        await db.users.update_one({"user_id": "owner"}, {"$set": {"intro_reply_totals": {"claimed": 10, "used": 9},
            "free_reply_requests.failure": receipt, "intro_active_request": {"id": receipt["result"]}}})
        await free_plan.settle(db, "owner", "failure", receipt, False)
        current = await db.users.find_one({"user_id": "owner"})
        assert current["intro_reply_totals"] == {"claimed": 9, "used": 9} and not current.get("intro_active_request")
        assert current["intro_billing"]["state"] == "active" and not current["intro_billing"].get("conversion")
        await card.dispatch_due(db, "owner", provider=provider)
        assert not any(x[0] == "convert" for x in provider.calls)
    run_case(case)


def test_cancellation_racing_dispatch_never_confirms_cancellation_and_sends_a_charge():
    async def case(db):
        provider = Provider();current, _ = await setup(db, provider);current = await tenth(db, current)
        body = card.Cancel(expected_member="owner", request_id=str(uuid4()), revision=current["intro_billing"]["revision"])
        outcomes = await asyncio.gather(card.dispatch_due(db, "owner", provider=provider),
            card.cancel(db, current, body, provider=provider), return_exceptions=True)
        assert all(not isinstance(value, Exception) or isinstance(value, HTTPException) and value.status_code == 409 for value in outcomes)
        final = await db.users.find_one({"user_id": "owner"})
        charges = [value for value in provider.calls if value[0] == "convert"]
        assert len(charges) <= 1
        if final["intro_billing"]["state"] == "cancelled":
            assert charges == []
        else:
            assert len(charges) == 1 and not any(value[0] == "cancel" for value in provider.calls)
        assert final["intro_reply_totals"]["used"] == 10
    run_case(case)


@pytest.mark.parametrize("when", ["before_read", "after_provider_read"])
def test_staff_unlimited_transition_blocks_due_conversion_at_atomic_boundary(monkeypatch, when):
    async def case(db):
        provider = Provider();current, _ = await setup(db, provider);await tenth(db, current)
        change = {"complimentary_entitlement": {"version": free_plan.COMPLIMENTARY_VERSION, "id": str(uuid4()), "active": True}}
        if when == "before_read":
            await db.users.update_one({"user_id": "owner"}, {"$set": change})
        else:
            original = card.state_change
            async def mutate(db, row, fields, **kwargs):
                if (fields.get("conversion") or {}).get("stage") == "dispatched":
                    await db.users.update_one({"user_id": "owner"}, {"$set": change})
                return await original(db, row, fields, **kwargs)
            monkeypatch.setattr(card, "state_change", mutate)
        try:
            await card.dispatch_due(db, "owner", provider=provider)
        except HTTPException as error:
            assert error.status_code == 409 and when == "after_provider_read"
        assert not any(value[0] == "convert" for value in provider.calls)
        final = await db.users.find_one({"user_id": "owner"})
        assert final["intro_billing"]["conversion"]["stage"] == "due" and final["complimentary_entitlement"] == change["complimentary_entitlement"]
        assert free_plan.complimentary_access(final)
    run_case(case)


@pytest.mark.parametrize("reversal", ["refunded", "dispute_lost"])
def test_first_intro_conversion_with_cash_already_reversed_has_no_reply_credits(monkeypatch, reversal):
    from backend.tests import test_intro_history_routes as routes
    async def case(db):
        server, _ = routes.configure(monkeypatch, db)
        await routes.seed_people(db, server)
        provider = routes.install_card_provider(monkeypatch, server)
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="https://fixture.invalid") as client:
            await routes.hosted_intro(client, db, provider)
        current = await db.users.find_one({"user_id": "owner"})
        await tenth(db, current);paid = await routes.prepare_conversion(db, provider)
        get_charge = stripe.Charge.retrieve
        if reversal == "refunded":
            def charge(*args, **kwargs):
                value = get_charge(*args, **kwargs);return {**value, "amount_refunded": value["amount"]}
            monkeypatch.setattr(stripe.Charge, "retrieve", charge)
        else:
            monkeypatch.setattr(stripe.Dispute, "list", lambda **kw: {"has_more": False, "data": [{"id": "dp_fixture",
                "charge": kw["charge"], "currency": "usd", "amount": 1200, "status": "lost"}]})
        current = await card.refresh(db, await db.users.find_one({"user_id": "owner"}), provider=provider, fulfill=server.mark_paid)
        assert current["intro_billing"]["state"] == "converted" and not intro_access.access(current)
        assert free_plan.reply_usage(current)["messages_remaining"] == 0
        before = deepcopy(current.get("subscription_allowance_adjustments"))
        await card.refresh(db, current, provider=provider, fulfill=server.mark_paid)
        after = await db.users.find_one({"user_id": "owner"})
        assert after.get("subscription_allowance_adjustments") == before and after["intro_reply_totals"]["used"] == 10
        assert len([value for value in provider.calls if value[0] == "convert"]) == 1
    run_case(case)


def test_ambiguous_cancel_reads_known_objects_and_does_not_repeat_cancel():
    async def case(db):
        provider = Provider();current, _ = await setup(db, provider);provider.fail_cancel = True
        body = card.Cancel(expected_member="owner", request_id=str(uuid4()), revision=current["intro_billing"]["revision"])
        current = await card.cancel(db, current, body, provider=provider)
        assert not intro_access.access(current)
        current = await card.refresh(db, await db.users.find_one({"user_id": "owner"}), provider=provider)
        assert current["intro_billing"]["state"] == "cancelled"
        assert len([x for x in provider.calls if x[0] == "cancel"]) == 1
    run_case(case)


def invoice(row, *, amount=1200, status="paid", proration=False):
    stamp = int(card.now().timestamp())
    start = card.now().replace(microsecond=0)
    year, month = start.year + (start.month == 12), start.month % 12 + 1
    end = int(start.replace(year=year, month=month, day=min(start.day, calendar.monthrange(year, month)[1])).timestamp())
    return {"id": "in_conversion", "subscription": row["subscription_id"], "status": status, "currency": "usd",
        "created": stamp, "billing_reason": "subscription_update", "total": amount, "amount_paid": amount,
        "total_excluding_tax": amount, "parent": {"subscription_details": {"subscription": row["subscription_id"],
            "metadata": {"imladri_intro_conversion": row["conversion"]["id"]}}},
        "lines": {"has_more": False, "data": [{"amount": amount, "type": "subscription", "price": row["price_id"],
            "proration": proration, "period": {"start": stamp, "end": end}}]}}


@pytest.mark.parametrize("amount,proration,fulfilled", [(0, False, False), (100, False, False), (1200, True, False), (1200, False, True)])
def test_conversion_waits_for_exact_positive_full_period_invoice(amount, proration, fulfilled):
    async def case(db):
        provider = Provider();current, _ = await setup(db, provider)
        current = await tenth(db, current);await card.dispatch_due(db, "owner", provider=provider)
        current = await db.users.find_one({"user_id": "owner"});row = current["intro_billing"]
        provider.sub["latest_invoice"] = invoice(row, amount=amount, proration=proration)
        period = provider.sub["latest_invoice"]["lines"]["data"][0]["period"]
        provider.sub["items"]["data"][0].update(current_period_start=period["start"], current_period_end=period["end"])
        delivered = []
        async def fulfill(tx, session):
            delivered.append((tx, session))
            await db.payment_transactions.update_one({"_id": tx["_id"]}, {"$set": {"payment_status": "paid", "credit_delivery_status": "fulfilled"}})
        current = await card.refresh(db, current, provider=provider, fulfill=fulfill)
        assert bool(delivered) is fulfilled
        assert (current["intro_billing"]["state"] == "converted") is fulfilled
        assert len([x for x in provider.calls if x[0] == "convert"]) == 1
    run_case(case)


def test_stale_price_text_or_quote_never_reaches_provider():
    async def case(db):
        provider = Provider();current = member();await db.users.insert_one(current)
        quote = (await card.quotes(db, current))["plus_100"]
        base = dict(expected_member="owner", plan_id="plus_100", expected_price_cents=1200, expected_messages=100,
            request_id=str(uuid4()), authorization_version=card.VERSION, authorize_after_messages=True, policy_accepted=True,
            authorization_text_sha256=quote["authorization_text_sha256"], quote_sha256=quote["quote_sha256"], origin_url="https://fixture.invalid")
        for change in ({"expected_price_cents": 1000}, {"authorization_text_sha256": "b" * 64}, {"quote_sha256": "c" * 64}):
            with pytest.raises(HTTPException) as error:
                await card.setup(db, current, identity(), card.Setup(**{**base, **change}), "https://fixture.invalid", provider=provider)
            assert error.value.status_code == 409 and provider.calls == []
        assert await db.intro_identities.count_documents({}) == 0
    run_case(case)


@pytest.mark.parametrize("reason", ["old_Clerk_identity", "blocked_disposable_email"])
def test_verified_permanent_intro_denial_opens_only_fresh_full_price_checkout(monkeypatch, reason):
    from backend.tests import test_intro_history_routes as routes
    from starlette.requests import Request
    async def case(db):
        server, _ = routes.configure(monkeypatch, db)
        owner = member(env="preview");await db.users.insert_one(owner)
        clerk = identity()
        if reason == "old_Clerk_identity":
            clerk["created_at"] = 1
        else:
            clerk["email_addresses"][0]["email_address"] = "owner@mailinator.com"
        q = (await card.quotes(db, owner))["plus_100"]
        body = card.Setup(expected_member="owner", plan_id="plus_100", expected_price_cents=1200, expected_messages=100,
            request_id=str(uuid4()), authorization_version=card.VERSION, authorize_after_messages=True, policy_accepted=True,
            authorization_text_sha256=q["authorization_text_sha256"], quote_sha256=q["quote_sha256"],
            origin_url="https://fixture.invalid", challenge_token="fictional")
        provider = Provider()
        with pytest.raises(HTTPException) as denial:
            await card.setup(db, owner, clerk, body, "https://fixture.invalid", provider=provider)
        assert denial.value.status_code == 403 and provider.calls == []
        current = await db.users.find_one({"user_id": "owner"})
        assert card.permanently_ineligible(current) and not card.required(current) and not card.eligible(current)
        assert not current.get("intro_billing") and not current.get("intro_entitlement")
        assert not (await intro_access.status(db, current))["can_claim"]
        routes.install_card_provider(monkeypatch, server)
        calls = []
        def create(**params):
            calls.append(params)
            return stripe.checkout.Session.construct_from({"id": "cs_regular", "url": "https://checkout.stripe.com/fictional",
                "mode": "subscription", "status": "open", "metadata": params["metadata"]}, None)
        monkeypatch.setattr(stripe.checkout.Session, "create", create)
        req = Request({"type": "http", "scheme": "https", "server": ("fixture.invalid", 443), "path": "/", "headers": []})
        with pytest.raises(HTTPException) as policy:
            await server.create_checkout(server.CheckoutIn(expected_member="owner", origin_url="https://fixture.invalid"), req, current)
        assert policy.value.status_code == 422 and calls == []
        paid = server.CheckoutIn(expected_member="owner", origin_url="https://fixture.invalid", purchase_policy_accepted=True,
            purchase_policy_version=server.purchase_policy.VERSION)
        response = await server.create_checkout(paid, req, current)
        assert response["checkout_url"] == "https://checkout.stripe.com/fictional" and len(calls) == 1
        assert calls[0]["line_items"] == [{"price": "price_plus_100", "quantity": 1}]
        assert "discounts" not in calls[0] and "trial_end" not in str(calls[0])
        tx = await db.payment_transactions.find_one({"session_id": "cs_regular"})
        assert tx["kind"] == "purchase" and tx["payment_status"] == "pending" and tx["amount"] == 12
        assert not (await db.users.find_one({"user_id": "owner"})).get("intro_entitlement")
    run_case(case)


@pytest.mark.parametrize("condition", ["bad_challenge", "unverified_email", "malformed_Clerk_age", "paused", "member_paused"])
def test_temporary_denials_never_open_the_paid_checkout_escape(monkeypatch, condition):
    async def case(db):
        owner = member();await db.users.insert_one(owner)
        clerk = identity()
        if condition == "unverified_email":
            clerk["email_addresses"][0]["verification"]["status"] = "unverified"
        elif condition == "malformed_Clerk_age":
            clerk["created_at"] = "not-a-timestamp"
        elif condition == "paused":
            await db.intro_controls.insert_one({"_id": "global", "paused": True})
        elif condition == "member_paused":
            await db.users.update_one({"user_id": "owner"}, {"$set": {"intro_access_paused": True}})
        else:
            monkeypatch.setattr(intro_access, "validate_challenge", AsyncMock(side_effect=HTTPException(403, "fictional challenge failure")))
        q = (await card.quotes(db, owner))["plus_100"]
        body = card.Setup(expected_member="owner", plan_id="plus_100", expected_price_cents=1200, expected_messages=100,
            request_id=str(uuid4()), authorization_version=card.VERSION, authorize_after_messages=True, policy_accepted=True,
            authorization_text_sha256=q["authorization_text_sha256"], quote_sha256=q["quote_sha256"],
            origin_url="https://fixture.invalid", challenge_token="fictional")
        provider = Provider()
        with pytest.raises(HTTPException):
            await card.setup(db, owner, clerk, body, "https://fixture.invalid", provider=provider)
        current = await db.users.find_one({"user_id": "owner"})
        assert not current.get("intro_ineligibility") and card.required(current) and provider.calls == []
    run_case(case)


@pytest.mark.parametrize("changed", ["agreement", "paid", "active_request", "checkout"])
def test_permanent_denial_cas_cannot_override_new_billing_or_open_duplicate_checkout(changed):
    async def case(db):
        owner = member();await db.users.insert_one(owner)
        updates = {"agreement": {"intro_billing": {"id": "new-agreement"}},
            "paid": {"tier": "paid", "stripe_subscription_id": "sub_new"},
            "active_request": {"intro_active_request": {"id": "new-message"}},
            "checkout": {"checkout_attempt": {"id": "new-checkout"}}}[changed]
        await db.users.update_one({"user_id": "owner"}, {"$set": updates})
        with pytest.raises(HTTPException) as denial:
            await card.retain_permanent_denial(db, owner, "identity_precedes_offer")
        assert denial.value.status_code == 409
        current = await db.users.find_one({"user_id": "owner"})
        assert not current.get("intro_ineligibility") and all(current[key] == value for key, value in updates.items())
    run_case(case)


@pytest.mark.parametrize("condition", ["missing_read", "malformed_owner", "malformed_grant"])
def test_missing_or_malformed_identity_reservation_never_mints_permanent_exemption(monkeypatch, condition):
    async def case(db):
        owner = member();await db.users.insert_one(owner)
        key = intro_access.digest("email", "owner@fixture.invalid")
        if condition == "missing_read":
            kind = type(db.intro_identities);original = kind.find_one
            async def missing(self, query, *args, **kwargs):
                if self.name == "intro_identities" and query.get("_id") == key:
                    return None
                return await original(self, query, *args, **kwargs)
            monkeypatch.setattr(kind, "find_one", missing)
        else:
            grant = {"version": intro_access.VERSION, "id": str(uuid4()), "active": True, "granted_at": card.now().isoformat()}
            await db.intro_identities.insert_one({"_id": key,
                "owner": "unverified-other-owner" if condition == "malformed_owner" else "a" * 64,
                "grant": grant if condition == "malformed_owner" else {"id": "not-verified"}})
        q = (await card.quotes(db, owner))["plus_100"]
        body = card.Setup(expected_member="owner", plan_id="plus_100", expected_price_cents=1200, expected_messages=100,
            request_id=str(uuid4()), authorization_version=card.VERSION, authorize_after_messages=True, policy_accepted=True,
            authorization_text_sha256=q["authorization_text_sha256"], quote_sha256=q["quote_sha256"],
            origin_url="https://fixture.invalid", challenge_token="fictional")
        provider = Provider()
        with pytest.raises(HTTPException) as error:
            await card.setup(db, owner, identity(), body, "https://fixture.invalid", provider=provider)
        assert error.value.status_code == 503 and provider.calls == []
        current = await db.users.find_one({"user_id": "owner"})
        assert not current.get("intro_ineligibility") and card.required(current) and not current.get("intro_entitlement")
    run_case(case)


@pytest.mark.parametrize("reason", ["previous_membership", "repeat_email_identity"])
def test_other_positive_permanent_denials_allow_paid_checkout_without_new_intro(reason):
    from backend import accounts
    async def case(db):
        if reason == "repeat_email_identity":
            await setup(db, Provider(), uid="prior")
        owner = member();await db.users.insert_one(owner);clerk = identity()
        if reason == "previous_membership":
            await db.membership_history.insert_one({"_id": accounts.identity_key("owner"), "closed": False})
        else:
            clerk["email_addresses"][0]["email_address"] = "prior@fixture.invalid"
        q = (await card.quotes(db, owner))["plus_100"]
        body = card.Setup(expected_member="owner", plan_id="plus_100", expected_price_cents=1200, expected_messages=100,
            request_id=str(uuid4()), authorization_version=card.VERSION, authorize_after_messages=True, policy_accepted=True,
            authorization_text_sha256=q["authorization_text_sha256"], quote_sha256=q["quote_sha256"],
            origin_url="https://fixture.invalid", challenge_token="fictional")
        provider = Provider()
        with pytest.raises(HTTPException) as denial:
            await card.setup(db, owner, clerk, body, "https://fixture.invalid", provider=provider)
        assert denial.value.status_code == 403 and provider.calls == []
        current = await db.users.find_one({"user_id": "owner"})
        assert current["intro_ineligibility"]["reason"] == reason and not card.required(current)
        assert not current.get("intro_entitlement") and not current.get("intro_reply_totals")
    run_case(case)


def test_permanent_denial_receipt_is_internal_operational_metadata_in_owner_export():
    import json
    from backend import accounts
    async def case(db):
        owner = member(nickname="Fictional owner", intro_ineligibility={"version": card.INELIGIBILITY_VERSION,
            "reason": "identity_precedes_offer", "verified_at": card.now().isoformat(),
            "cohort_start": intro_access.eligible_since().isoformat()})
        await db.users.insert_one(owner)
        value = json.loads("".join([chunk async for chunk in accounts.export_json(db, owner)]))
        assert value["account"]["nickname"] == "Fictional owner"
        assert "intro_ineligibility" not in value["account"] and "identity_precedes_offer" not in str(value)
        assert card.permanently_ineligible(await db.users.find_one({"user_id": "owner"}))
    run_case(case)


@pytest.mark.parametrize("persistent_handler_error", [False, True])
def test_recovery_reaches_26th_member_after_25_persistent_failures(monkeypatch, persistent_handler_error):
    """Actual PG selection; the two modeled handlers never contact a provider."""
    async def case(db):
        users = [member("recovery-" + str(index).zfill(2), intro_billing={"version": card.VERSION,
            "state": "requires_action" if index % 2 == 0 else "payment_failed"}) for index in range(25)]
        users.append(member("recovery-25", intro_billing={"version": card.VERSION, "state": "billing_pending",
            "conversion": {"stage": "due"}}))
        await db.users.insert_many(users)
        visited, due = [], []
        async def dispatch(_db, uid):
            if uid == "recovery-25":
                due.append(uid)
                await db.users.update_one({"user_id": uid}, {"$set": {"intro_billing.conversion.stage": "awaiting_invoice"}})
        async def refresh(_db, current, *, fulfill):
            visited.append(current["user_id"])
            if current["user_id"] != "recovery-25" and persistent_handler_error:
                raise RuntimeError("Fictional persistent provider-read failure")
            return current
        monkeypatch.setattr(card, "dispatch_due", dispatch)
        monkeypatch.setattr(card, "refresh", refresh)
        await run_two_recovery_ticks(monkeypatch, db)
        assert visited == ["recovery-" + str(index).zfill(2) for index in range(26)]
        assert due == ["recovery-25"]
        assert (await db.users.find_one({"user_id": "recovery-25"}))["intro_billing"]["conversion"]["stage"] == "awaiting_invoice"
        assert await db.users.count_documents({"intro_billing.state": {"$in": ["requires_action", "payment_failed"]}}) == 25
    run_case(case)


def test_recovery_wraps_unknown_operation_with_read_only_reconciliation_and_no_dispatch(monkeypatch):
    async def case(db):
        provider = Provider()
        current, _ = await setup(db, provider)
        current = await tenth(db, current)
        operation = current["intro_billing"]["conversion"]
        await db.users.update_one({"user_id": "owner"}, {"$set": {"intro_billing.conversion.stage": "unknown",
            "intro_billing.conversion.dispatched_at": card.now().isoformat()}})
        provider.sub["metadata"]["imladri_intro_conversion"] = operation["id"]
        provider.sub["discounts"] = []
        before = len([call for call in provider.calls if call[0] == "subscription"])
        monkeypatch.setattr(card, "Provider", lambda: provider)
        await run_two_recovery_ticks(monkeypatch, db)
        assert len([call for call in provider.calls if call[0] == "subscription"]) - before == 2
        assert not any(call[0] == "convert" for call in provider.calls)
        saved = await db.users.find_one({"user_id": "owner"})
        assert saved["intro_billing"]["conversion"]["id"] == operation["id"]
        assert saved["intro_billing"]["conversion"]["stage"] == "unknown"
        assert saved["intro_billing"]["state"] == "billing_pending" and saved["tier"] == "unsubscribed"
    run_case(case)


def test_staff_purchase_hold_blocks_due_conversion_but_keeps_cancellation_available():
    from backend import billing_risk
    async def case(db):
        provider = Provider(); current, _ = await setup(db, provider); current = await tenth(db, current)
        for index in range(3):
            await billing_risk.observe(db, "owner", "fixture", "identity_conflict", "fictional-review-" + str(index), "conflict")
        current = await db.users.find_one({"user_id": "owner"})
        action = billing_risk.ReviewAction(expected_member="owner", revision=current["billing_risk"]["revision"],
            request_id=str(uuid4()), action="hold", scopes=["new_purchase"], reason="manual_payment_review")
        await billing_risk.review(db, {"env": "fixture"}, current["billing_risk"]["id"], action, {"user_id": "staff", "role": "admin"})
        await card.dispatch_due(db, "owner", provider=provider)
        assert not [call for call in provider.calls if call[0] == "convert"]
        current = await db.users.find_one({"user_id": "owner"})
        result = await card.cancel(db, current, card.Cancel(expected_member="owner", request_id=str(uuid4()),
            revision=current["intro_billing"]["revision"]), provider=provider)
        assert result["intro_billing"]["state"] == "cancelled"
        assert result["intro_reply_totals"]["used"] == 10 and not intro_access.access(result)
    run_case(case)
