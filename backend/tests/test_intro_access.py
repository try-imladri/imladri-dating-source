"""Introductory grants and spend fences use isolated, fictional identities only."""
import asyncio
import hashlib
import json
import os
import time
from datetime import timedelta
from uuid import uuid4
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ValidationError
from starlette.background import BackgroundTasks
from starlette.requests import Request

from backend import free_plan, intro_access, intro_card, membership_plans, message_charge, message_costs, ops
from backend.tests.test_free_plan_quota import run_case as run_mock_case


def run_case(case):
    dsn = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
    if not dsn:
        return run_mock_case(case)
    from urllib.parse import urlsplit
    from backend.postgres_store import PostgresDatabase
    assert urlsplit(dsn).hostname in ("127.0.0.1", "localhost", "::1")
    async def run():
        db = PostgresDatabase(dsn, "fixture_intro_" + uuid4().hex, allow_local_plaintext=True)
        try:
            for name, indexes in intro_access.INDEXES.items():
                await db[name].create_indexes(indexes)
            await case(db)
        finally:
            def cleanup(cursor, stamp):
                for table in ("rpc_requests", "unique_keys", "indexes", "documents", "collections"):
                    cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (db.name,))
            await db._run(cleanup, write="__fixture_cleanup")
            await db.aclose()
    asyncio.run(run())


@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    for name, value in {"INTRO_ACCESS_ENABLED": "1", "INTRO_ABUSE_HMAC_SECRET": "fictional-intro-hmac-key-32-bytes-long",
        "INTRO_ELIGIBLE_SINCE": "2026-01-01T00:00:00+00:00", "INTRO_TURNSTILE_SITE_KEY": "fictional-site-key",
        "INTRO_TURNSTILE_SECRET_KEY": "fictional-server-key", "INTRO_ALLOWED_HOSTNAMES": "fixture.invalid",
        "INTRO_DAILY_TOKEN_LIMIT": "250000", "INTRO_DAILY_BUDGET_CENTS": "100", "PERSONAL_BILLING_MODE": "enabled",
        "INTRO_CARD_FIRST_VERIFIED_STRATEGY": intro_card.STRATEGY, "INTRO_CARD_FIRST_PROVIDER_PROOF_SHA256": "a" * 64,
        "INTRO_CARD_FIRST_COUPON_ID": "coupon_fixture", "STRIPE_CHECKOUT_MODE": "managed"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(intro_card.stripe, "api_key", "sk_test_fictional_no_network")


def member(uid="reader", **extra):
    row = {"user_id": uid, "role": "member", "tier": "unsubscribed", "env": "preview",
           "created_at": intro_access.now().isoformat(), **extra}
    if "intro_entitlement" in extra:
        row["intro_billing"] = authorized_billing(uid, extra["intro_entitlement"])
    return row


def authorized_billing(uid, entitlement):
    """Explicit fictional verified-card agreement for isolated meter fixtures."""
    plan = {key: membership_plans.PLANS["plus_100"][key] for key in ("id", "name", "messages", "price_cents")}
    plan.update(currency="usd", offer="standard", pair_id=None)
    text = intro_card.agreement_text(plan, 10)
    return {"version": intro_card.VERSION, "id": str(uuid4()), "user_id": uid, "state": "active", "revision": 0,
        "strategy": intro_card.STRATEGY, "selected_plan": plan, "card_verified": True,
        "grant_id": entitlement.get("id"), "session_id": "cs_fictional_meter", "subscription_id": "sub_fictional_meter",
        "customer_id": "cus_fictional_meter", "authorization": {"version": intro_card.VERSION,
            "authorize_after_messages": True, "policy_accepted": True, "selected_plan": plan, "text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest()}}


def provider(uid="reader", email="reader@fixture.invalid", **extra):
    return {"id": uid, "created_at": int(intro_access.now().timestamp() * 1000), "primary_email_address_id": "primary",
            "email_addresses": [{"id": "primary", "email_address": email, "verification": {"status": "verified"}}], **extra}


def grant(**extra):
    return {"version": intro_access.VERSION, "id": str(uuid4()), "active": True,
            "granted_at": intro_access.now().isoformat(), **extra}


def claim_body(uid="reader", **extra):
    return intro_access.Claim(expected_member=uid, challenge_token="fictional-challenge-" + uuid4().hex, **extra)


def request(key="one", **headers):
    return Request({"type": "http", "headers": [(key.lower().encode(), value.encode()) for key, value in {
        "Idempotency-Key": key, **headers}.items()]})


class Body(BaseModel):
    text: str = "Fictional conversation"


def test_provider_specific_aliases_and_verified_primary_only():
    assert intro_access.normalized_email("First.Last+demo@googlemail.com") == "firstlast@gmail.com"
    assert intro_access.normalized_email("firstlast@gmail.com") == "firstlast@gmail.com"
    assert intro_access.normalized_email("First.Last+demo@custom.invalid") == "First.Last+demo@custom.invalid"
    assert intro_access.verified_identity(provider(), "reader") == "reader@fixture.invalid"
    for identity in (provider(id="other"), provider(banned=True), provider(locked=True), provider(primary_email_address_id="missing"),
                     provider(email_addresses=[{"id": "primary", "email_address": "reader@fixture.invalid", "verification": {"status": "unverified"}}])):
        with pytest.raises(HTTPException) as denied:
            intro_access.verified_identity(identity, "reader")
        assert denied.value.status_code == 403


def test_admin_review_queue_is_bounded_and_does_not_expose_abuse_identifiers():
    async def scenario(db):
        await db.users.insert_many([member("flagged-" + str(index).zfill(3), nickname="Fictional member",
            intro_risk_flags=["shared_device_grants"], intro_network_digest="private-comparison-sentinel")
            for index in range(22)] + [member("foreign", env="live", intro_risk_flags=["repeat_identity"]),
                member("staff", role="admin", intro_risk_flags=["repeat_identity"]), member("unflagged")])
        report = await intro_access.summary(db, "preview")
        assert report["totals"]["suspected_accounts"] == 22
        assert len(report["review_accounts"]) == 20 and report["review_accounts_more"] is True
        assert all(set(row) == {"user_id", "nickname", "reasons"} for row in report["review_accounts"])
        assert all(row["reasons"] == ["Multiple introductions on a shared device"] for row in report["review_accounts"])
        assert "private-comparison-sentinel" not in json.dumps(report)
        assert not any(row["user_id"] in {"foreign", "staff", "unflagged"} for row in report["review_accounts"])
    run_case(scenario)


def test_a_validated_challenge_cannot_race_across_different_verified_accounts(monkeypatch):
    monkeypatch.setattr(intro_access, "validate_challenge", AsyncMock())
    async def scenario(db):
        users = [member("fictional-A"), member("fictional-B")]
        await db.users.insert_many(users)
        async def start(user):
            return await intro_access.prepare_claim(db, user, provider(user["user_id"], user["user_id"] + "@fixture.invalid"),
                intro_access.Claim(expected_member=user["user_id"], challenge_token="fictional-shared-token"))
        results = await asyncio.gather(*(start(user) for user in users), return_exceptions=True)
        assert sum(isinstance(result, dict) and "grant" in result for result in results) == 1
        errors = [result for result in results if isinstance(result, HTTPException)]
        assert len(errors) == 1 and errors[0].status_code == 403
        assert "already used" in errors[0].detail
        assert await db.users.count_documents({"intro_entitlement": {"$exists": True}}) == 0
        assert await db.intro_identities.count_documents({}) == 1
        records = await db.intro_attempt_usage.find({}).to_list(None)
        assert "fictional-shared-token" not in json.dumps(records, default=str)
        assert sum(row["_id"].startswith("challenge:") for row in records) == 1
    run_case(scenario)


@pytest.mark.parametrize("change", [{"active": 1}, {"version": "wrong"}, {"id": "bad"}, {"extra": True}, {"granted_at": "2026-01-01"}])
def test_malformed_entitlements_fail_closed(change):
    row = member(intro_entitlement=grant(**change))
    assert not intro_access.entitlement(row)
    assert not free_plan.has_personal_access(row)


def test_exact_grant_is_not_staff_and_exhaustion_is_not_a_free_tier():
    row = member(intro_entitlement=grant(), intro_reply_totals={"claimed": 1, "used": 1})
    assert free_plan.has_member_access(row) and free_plan.has_personal_access(row)
    assert free_plan.billing_tier(row) == "intro"
    usage = free_plan.reply_usage(row)
    assert usage["plan"] == "intro" and usage["message_limit"] == 10 and usage["messages_remaining"] == 9
    assert usage["period_start"] is None and not usage["period_verified"]
    row["intro_reply_totals"] = {"claimed": 10, "used": 10}
    assert not free_plan.has_member_access(row) and not free_plan.has_personal_access(row)
    assert free_plan.reply_usage(row)["messages_remaining"] == 0
    assert free_plan.reply_limit_error(row).headers["X-Imladri-Limit"] == "intro"
    for totals in ({"claimed": -1}, {"claimed": 3, "used": 4}, {"claimed": True}, []):
        row["intro_reply_totals"] = totals
        assert not intro_access.access(row)


def test_configuration_and_public_status_fail_closed(monkeypatch):
    async def case(db):
        assert (await intro_access.public_status(db))["enabled"]
        for name in ("INTRO_ABUSE_HMAC_SECRET", "INTRO_TURNSTILE_SECRET_KEY", "INTRO_ALLOWED_HOSTNAMES", "INTRO_ELIGIBLE_SINCE"):
            value = os.environ[name]
            monkeypatch.setenv(name, "")
            result = await intro_access.public_status(db)
            assert result["enabled"] is False and result["messages"] == 0
            assert "secret" not in str(result).lower()
            monkeypatch.setenv(name, value)
        await db.intro_controls.insert_one({"_id": "global", "paused": True})
        assert not (await intro_access.public_status(db))["enabled"]
    run_case(case)


def test_turnstile_checks_hostname_action_success_and_token(monkeypatch):
    async def scenario():
        class Client:
            result = {"success": True, "action": "intro-access", "hostname": "fixture.invalid"}
            def __init__(self, **kwargs):
                assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            async def post(self, url, data):
                assert url == "https://challenges.cloudflare.com/turnstile/v0/siteverify"
                assert data == {"secret": "fictional-server-key", "response": "token"}
                return httpx.Response(200, json=self.result, request=httpx.Request("POST", url))
        monkeypatch.setattr(intro_access.httpx, "AsyncClient", Client)
        await intro_access.validate_challenge("token")
        for fields in ({"hostname": "evil.invalid"}, {"hostname": False}, {"action": "other"}, {"success": False}):
            Client.result = {"success": True, "action": "intro-access", "hostname": "fixture.invalid", **fields}
            with pytest.raises(HTTPException) as denied:
                await intro_access.validate_challenge("token")
            assert denied.value.status_code == 403
        with pytest.raises(HTTPException):
            await intro_access.validate_challenge("")
    asyncio.run(scenario())


def test_grants_are_idempotent_and_email_alias_recreation_cannot_reset(monkeypatch):
    monkeypatch.setattr(intro_access, "validate_challenge", AsyncMock())
    async def case(db):
        owner, other = member(), member("recreated")
        await db.users.insert_many([dict(owner), dict(other)])
        result = await intro_access.prepare_claim(db, owner, provider(email="First.Last+one@gmail.com"), claim_body())
        assert intro_access.entitlement(member(intro_entitlement=result["grant"])) == result["grant"]
        assert not (await db.users.find_one({"user_id": "reader"})).get("intro_entitlement")
        original = await db.users.find_one({"user_id": "reader"})
        assert (await intro_access.prepare_claim(db, original, provider(email="firstlast@googlemail.com"), claim_body()))["grant"] == result["grant"]
        assert await db.intro_identities.count_documents({}) == 1
        # Preserve only the minimal keyed record; neither email nor raw identity is stored.
        record = await db.intro_identities.find_one({})
        assert "reader" not in str(record) and "gmail" not in str(record) and "First" not in str(record)
        assert (record["expires_at"] - record["issued_at"]).days == 365
        await db.users.delete_one({"user_id": "reader"})
        with pytest.raises(HTTPException) as denied:
            await intro_access.prepare_claim(db, other, provider("recreated", "firstlast@googlemail.com"), claim_body("recreated"))
        assert denied.value.status_code == 403
        assert not (await db.users.find_one({"user_id": "recreated"})).get("intro_entitlement")
    run_case(case)


@pytest.mark.parametrize("change", [{"tier": "paid", "stripe_subscription_id": "sub_past", "subscription_period_start": 1, "subscription_period_end": 2},
    {"complimentary_entitlement": grant()}, {"replies_paused": True}, {"closing": True}, {"created_at": "2025-01-01T00:00:00+00:00"}])
def test_ineligible_accounts_never_reach_challenge_or_get_a_grant(monkeypatch, change):
    challenge = AsyncMock()
    monkeypatch.setattr(intro_access, "validate_challenge", challenge)
    async def case(db):
        row = member(**change)
        await db.users.insert_one(dict(row))
        assert not (await intro_access.status(db, row))["can_claim"]
        with pytest.raises(HTTPException):
            await intro_access.prepare_claim(db, row, provider(), claim_body())
        assert await db.intro_identities.count_documents({}) == 0 and challenge.await_count == 0
    run_case(case)


def test_stale_account_provider_age_and_closed_history_cannot_claim(monkeypatch):
    monkeypatch.setattr(intro_access, "validate_challenge", AsyncMock())
    async def case(db):
        row = member()
        await db.users.insert_one(dict(row))
        for identity, body in ((provider(), claim_body("other")), (provider(created_at=1), claim_body())):
            with pytest.raises(HTTPException):
                await intro_access.prepare_claim(db, row, identity, body)
        await db.membership_history.insert_one({"_id": hashlib.sha256(b"imladri-personal:reader").hexdigest(), "closed": False})
        with pytest.raises(HTTPException):
            await intro_access.prepare_claim(db, row, provider(), claim_body())
        assert await db.intro_identities.count_documents({}) == 0
    run_case(case)


def test_canceled_or_incomplete_checkout_keeps_intro_eligibility(monkeypatch):
    monkeypatch.setattr(intro_access, "validate_challenge", AsyncMock())
    async def case(db):
        row = member(stripe_customer_id="cus_checkout", stripe_subscription_id="sub_incomplete", subscription_status="incomplete")
        await db.users.insert_one(dict(row))
        assert intro_access.eligibility(row)
        result = await intro_access.prepare_claim(db, row, provider(), claim_body())
        assert "grant" in result
        assert not free_plan.has_personal_access(await db.users.find_one({"user_id": "reader"}))
        assert not (await db.users.find_one({"user_id": "reader"})).get("intro_entitlement")
    run_case(case)


def test_concurrent_grants_share_the_original_grant(monkeypatch):
    monkeypatch.setattr(intro_access, "validate_challenge", AsyncMock())
    async def case(db):
        row = member()
        await db.users.insert_one(dict(row))
        result = await asyncio.gather(*(intro_access.prepare_claim(db, row, provider(), claim_body()) for _ in range(5)))
        assert all(x["grant"] == result[0]["grant"] for x in result)
        assert await db.intro_identities.count_documents({}) == 1
        final = await db.users.find_one({"user_id": "reader"})
        assert not final.get("intro_entitlement") and not final.get("intro_reply_totals")
    run_case(case)


def test_two_accounts_racing_the_same_verified_mailbox_only_receive_one_grant(monkeypatch):
    monkeypatch.setattr(intro_access, "validate_challenge", AsyncMock())
    async def case(db):
        users = [member("race-one"), member("race-two")]
        await db.users.insert_many([dict(row) for row in users])
        results = await asyncio.gather(*(intro_access.prepare_claim(db, row, provider(row["user_id"],
            "same.mailbox+" + row["user_id"] + "@gmail.com"), claim_body(row["user_id"])) for row in users), return_exceptions=True)
        assert sum(isinstance(value, dict) for value in results) == 1
        assert sum(isinstance(value, HTTPException) and value.status_code == 403 for value in results) == 1
        assert await db.users.count_documents({"intro_entitlement.version": intro_access.VERSION}) == 0
        assert await db.intro_identities.count_documents({}) == 1
    run_case(case)


def test_ten_successful_replies_shared_with_couples_retries_and_failures(monkeypatch):
    async def case(db):
        row = member(intro_entitlement=grant())
        await db.users.insert_one(dict(row))
        async def identity(value): return value
        async def saved(rid):
            doc = {"user_id": "reader", "id": rid, "response": "fictional saved reply"}
            await db.exchanges.insert_one(dict(doc))
            return doc
        async def failed(_): raise HTTPException(502, "Fictional provider refusal")
        async def send(key, generate=saved, kind="exchange", scope="profile"):
            current = await db.users.find_one({"user_id": "reader"})
            return await free_plan.run_metered(db, current, request(key), Body(), kind, scope, generate, identity)
        with pytest.raises(HTTPException):
            await send("failed", failed)
        assert free_plan.reply_usage(await db.users.find_one({"user_id": "reader"}))["messages_remaining"] == 10
        first = await send("failed")
        assert await send("failed") == first
        assert (await db.message_cost_units.find_one({"_id": first["id"]}))["pool"] == "intro"
        scope = {"slot": "tone_coach", "document": "fictional-couple-draft"}
        await db.couples_drafts.insert_one({"_id": scope["document"]})
        async def couple(rid):
            value = {"id": rid, "draft": "fictional couples reply"}
            await db.couples_drafts.update_one({"_id": scope["document"]}, {"$set": {"tone_coach": value}})
            return value
        await send("couple", couple, "couples_tone", scope)
        for n in range(8):
            await send("individual-" + str(n))
        current = await db.users.find_one({"user_id": "reader"})
        assert current["intro_reply_totals"] == {"claimed": 10, "used": 10}
        assert not free_plan.has_personal_access(current)
        assert await send("failed") == first
        with pytest.raises(HTTPException) as rejected:
            await send("eleventh")
        assert rejected.value.status_code == 402 and rejected.value.headers["X-Imladri-Limit"] == "intro"
        assert await db.message_cost_units.count_documents({"pool": "intro", "status": "settled"}) == 10
        # Verified subscription takes precedence and starts with its full allowance.
        await db.users.update_one({"user_id": "reader"}, {"$set": {"tier": "paid", "stripe_subscription_id": "sub_fictional",
            "subscription_period_start": int(time.time()) - 30, "subscription_period_end": int(time.time()) + 3600}})
        paid = await db.users.find_one({"user_id": "reader"})
        assert free_plan.reply_usage(paid)["messages_remaining"] == 100
        await send("paid-message")
        assert (await db.users.find_one({"user_id": "reader"}))["intro_reply_totals"]["used"] == 10
    run_case(case)


def test_verified_paid_conversion_is_durable_but_open_checkout_never_freezes_intro():
    async def case(db):
        row = member(intro_entitlement=grant(), intro_reply_totals={"claimed": 2, "used": 2},
                     stripe_customer_id="cus_unpaid", stripe_subscription_id="sub_incomplete", subscription_status="incomplete")
        await db.users.insert_one(dict(row))
        current = await free_plan.initialize_usage(db, row)
        assert not current.get("intro_converted_at") and intro_access.access(current)
        await db.users.update_one({"user_id": "reader"}, {"$set": {"tier": "paid", "subscription_status": "active",
            "subscription_period_start": int(time.time()) - 20, "subscription_period_end": int(time.time()) + 3600}})
        current = await free_plan.initialize_usage(db, await db.users.find_one({"user_id": "reader"}))
        assert current.get("intro_converted_at") and not intro_access.access(current)
        await db.users.update_one({"user_id": "reader"}, {"$set": {"tier": "unsubscribed", "subscription_status": "canceled"},
            "$unset": {"subscription_period_start": "", "subscription_period_end": "", "stripe_subscription_id": ""}})
        current = await db.users.find_one({"user_id": "reader"})
        assert not intro_access.access(current) and not intro_access.eligibility(current)
        assert free_plan.reply_usage(current)["plan"] == "unsubscribed"
    run_case(case)


def test_one_active_generation_blocks_parallel_calls_without_extra_debits():
    async def case(db):
        row = member(intro_entitlement=grant(), intro_reply_totals={"claimed": 9, "used": 9})
        await db.users.insert_one(dict(row))
        entered, finish = asyncio.Event(), asyncio.Event()
        async def generate(rid):
            entered.set()
            await finish.wait()
            doc = {"user_id": "reader", "id": rid}
            await db.exchanges.insert_one(dict(doc))
            return doc
        async def identity(value): return value
        async def send(key):
            return await free_plan.run_metered(db, await db.users.find_one({"user_id": "reader"}),
                request(key), Body(), "exchange", "profile", generate, identity)
        pending = asyncio.create_task(send("pending"))
        await entered.wait()
        parallel = await asyncio.gather(send("pending"), send("another"), return_exceptions=True)
        assert all(isinstance(x, HTTPException) and x.status_code == 409 for x in parallel)
        finish.set()
        await pending
        final = await db.users.find_one({"user_id": "reader"})
        assert final["intro_reply_totals"] == {"claimed": 10, "used": 10}
        assert not final.get("intro_active_request")
    run_case(case)


def test_malformed_counters_never_reach_the_provider():
    async def case(db):
        row = member(intro_entitlement=grant(), intro_reply_totals={"claimed": -1, "used": 0})
        await db.users.insert_one(dict(row))
        async def forbidden(*args): pytest.fail("Malformed intro counters reached generation")
        with pytest.raises(HTTPException) as stopped:
            await free_plan.run_metered(db, row, request(), Body(), "exchange", "profile", forbidden, forbidden)
        assert stopped.value.status_code == 402
        assert await db.message_cost_units.count_documents({}) == 0
    run_case(case)


def test_interrupted_intro_request_reconciles_saved_or_missing_result():
    async def case(db):
        grant_value = grant()
        rid = str(uuid4())
        row = member(intro_entitlement=grant_value, intro_reply_totals={"claimed": 1, "used": 0},
            intro_active_request={"id": rid, "expires": "2000-01-01"}, free_reply_requests={"key": {
                "kind": "exchange", "scope": "profile", "result": rid, "pool": "intro", "pending": True,
                "expires": "2000-01-01", "cycle": None, "period_end": int(time.time())}})
        await db.users.insert_one(dict(row))
        recovered = await free_plan.initialize_usage(db, row)
        assert recovered["intro_reply_totals"] == {"claimed": 0, "used": 0}
        assert not recovered.get("intro_active_request")
    run_case(case)


def test_intro_budget_counts_every_attempt_and_background_and_does_not_spend_paid_limits(monkeypatch):
    monkeypatch.setenv("INTRO_DAILY_TOKEN_LIMIT", "640")
    monkeypatch.setenv("INTRO_DAILY_BUDGET_CENTS", "1")
    async def case(db):
        with message_costs.message_context("fictional-message", "individual", pool="intro"):
            one = await ops.reserve_tokens(db, "", "", False, 192)
            two = await ops.reserve_tokens(db, "", "", False, 192)
            with pytest.raises(HTTPException) as capped:
                await ops.reserve_tokens(db, "", "", False, 192)
            assert capped.value.status_code == 503
        intro = await db.ai_intro_daily_usage.find_one({})
        assert intro["tokens"] == 640 and intro["attempts"] == 2
        assert (await db.ai_daily_usage.find_one({}))["requests"] == 2
        # Unknown/bad responses do not refund conservative intro spend.
        await ops.settle_tokens(db, one, {"total_tokens": 10})
        assert (await db.ai_intro_daily_usage.find_one({}))["tokens"] == 640
        # Only a provably undispatched attempt releases both reservations.
        await ops.release_undispatched(db, two)
        assert (await db.ai_intro_daily_usage.find_one({}))["tokens"] == 320
        # A paid call remains available when the introductory pool is paused.
        await db.intro_controls.insert_one({"_id": "global", "paused": True})
        with message_costs.message_context("paid-message", "individual", pool="plan"):
            await ops.reserve_tokens(db, "", "", False, 192)
        async def background():
            assert message_costs.current_pool.get() == "intro"
            with pytest.raises(HTTPException):
                await ops.reserve_tokens(db, "", "", False, 192)
        tasks = BackgroundTasks()
        await db.message_cost_units.insert_one({"_id": "intro-message"})
        with message_costs.message_context("intro-message", "couples", pool="intro"):
            await message_charge.enqueue(db, tasks, background)
        await tasks()
        assert (await db.message_cost_units.find_one({"_id": "intro-message"}))["background_pending"] == 0
    run_case(case)


def test_provider_ledger_and_admin_summary_show_intro_costs_without_content():
    async def case(db):
        row = member(intro_entitlement=grant(), intro_reply_totals={"claimed": 2, "used": 2})
        await db.users.insert_one(dict(row))
        with message_costs.message_context("fictional-rid", "couples", pool="intro"):
            with message_costs.generation_context("preview", "couples_tone"):
                async with message_costs.provider_call(db, ("fictional-fingerprint", False), "deepseek-flash", False) as result:
                    result.status = "succeeded"
                    result.envelope = {"model": "deepseek-flash", "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
        call = await db.ai_call_ledger.find_one({})
        assert call["credit_pool"] == "intro" and call["workspace"] == "couples"
        report = await intro_access.summary(db, "preview")
        assert report["totals"]["granted_accounts"] == 1 and report["totals"]["used_messages"] == 2
        assert report["costs"]["attempts"] == 1 and report["costs"]["high_nusd"] > 0
        assert "fictional-rid" not in str(report) and "reader" not in str(report)
    run_case(case)


def test_claim_and_admin_routes_require_real_dependencies_and_safe_fields(monkeypatch):
    monkeypatch.setattr(intro_access, "validate_challenge", AsyncMock())
    async def case(db):
        row = member()
        await db.users.insert_one(dict(row))
        actor = {"user_id": "reader", "role": "member", "env": "preview"}
        async def current():
            return await db.users.find_one({"user_id": actor["user_id"]}) or actor
        async def admin():
            if actor["role"] != "admin":
                raise HTTPException(403, "Not allowed")
            return actor
        app = FastAPI()
        fetches = []
        def clerk(uid):
            fetches.append(uid)
            return provider(uid)
        app.include_router(intro_access.make_router(lambda: db, current, admin, clerk))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://fixture.invalid") as client:
            assert (await client.get("/public/intro-access")).json()["enabled"]
            assert (await client.get("/admin/intro-access/summary")).status_code == 403
            assert (await client.patch("/admin/intro-access/settings", json={"paused": True})).status_code == 403
            assert (await client.post("/me/intro-access/claim", json={"expected_member": "reader", "verified": True})).status_code == 422
            response = await client.post("/me/intro-access/claim", json={"expected_member": "reader", "challenge_token": "fictional"})
            assert response.status_code == 409 and fetches == []
            status = await client.get("/me/intro-access")
            assert status.headers["cache-control"] == "no-store" and status.json()["messages_remaining"] == 0
            assert await db.intro_identities.count_documents({}) == 0
            assert not (await db.users.find_one({"user_id": "reader"})).get("intro_entitlement")
            assert "fictional-server-key" not in response.text and "hmac" not in response.text
            actor.update(user_id="admin", role="admin")
            paused = await client.patch("/admin/intro-access/settings", json={"paused": True})
            assert paused.status_code == 200 and paused.json()["settings"]["paused"]
            assert not (await client.get("/public/intro-access")).json()["enabled"]
            actor.update(user_id="reader", role="member")
            assert (await client.get("/me/intro-access")).json()["state"] == "paused"
    run_case(case)


def test_claim_velocity_and_device_flags_do_not_grant_extra_messages(monkeypatch):
    monkeypatch.setattr(intro_access, "validate_challenge", AsyncMock())
    async def case(db):
        for _ in range(6):
            await intro_access.velocity(db, "one-fictional-user", 6)
        with pytest.raises(HTTPException) as capped:
            await intro_access.velocity(db, "one-fictional-user", 6)
        assert capped.value.status_code == 429
        same_device = str(uuid4())
        for n in range(3):
            uid = "fictional-household-" + str(n)
            row = member(uid)
            await db.users.insert_one(dict(row))
            result = await intro_access.prepare_claim(db, row, provider(uid, uid + "@fixture.invalid"),
                                            claim_body(uid, device_token=same_device))
            assert "grant" in result
            assert not (await db.users.find_one({"user_id": uid})).get("intro_entitlement")
        assert result["flags"] == ["shared_device_grants"]
        assert "fictional-household-2" not in str(await db.intro_identities.find_one({}))
    run_case(case)


def test_saved_and_unbound_background_context_preserve_intro_pool():
    async def case(db):
        await db.message_cost_units.insert_one({"_id": "saved-intro", "workspace": "individual", "pool": "intro", "env": "preview"})
        with await message_charge.saved_context(db, "saved-intro", "preview"):
            assert message_costs.current_pool.get() == "intro"
        assert message_costs.current_pool.get() is None
        tasks, observed = BackgroundTasks(), []
        async def background(): observed.append(message_costs.current_pool.get())
        token = message_costs.current_pool.set("intro")
        try:
            await message_charge.enqueue(db, tasks, background)
        finally:
            message_costs.current_pool.reset(token)
        await tasks()
        assert observed == ["intro"] and message_costs.current_pool.get() is None
    run_case(case)


def test_network_signal_requires_signed_edge_header_and_never_uses_forwarded_ip(monkeypatch):
    monkeypatch.setenv("RENDER_ORIGIN_SECRET", "fictional-origin-secret")
    assert intro_access.network_signal(request(**{"X-Forwarded-For": "203.0.113.1"})) is None
    assert intro_access.network_signal(request(**{"X-Imladri-Client-IP": "203.0.113.1", "X-Imladri-Origin": "wrong"})) is None
    first = intro_access.network_signal(request(**{"X-Imladri-Client-IP": "203.0.113.1", "X-Imladri-Origin": "fictional-origin-secret"}))
    second = intro_access.network_signal(request(**{"X-Imladri-Client-IP": "203.0.113.200", "X-Imladri-Origin": "fictional-origin-secret"}))
    assert first and first == second and "203.0.113" not in first


def test_claim_schema_forbids_entitlements_or_browser_verification_claims():
    for extra in ({"verified": True}, {"intro_entitlement": grant()}, {"messages": 100}):
        with pytest.raises(ValidationError):
            intro_access.Claim(expected_member="reader", **extra)


def test_real_individual_and_couples_http_pipeline_tracks_intro_preparation_retry_and_background(monkeypatch):
    from backend import ai, server
    async def case(db):
        owner = member(env=server.ENV, intro_entitlement=grant(), about={"demographics": {"age": "30"}},
                       couples_adult_at="2026-01-01T00:00:00+00:00")
        guest = member("fictional-partner", env=server.ENV, about={"demographics": {"age": "30"}},
                       couples_adult_at="2026-01-01T00:00:00+00:00")
        await db.users.insert_many([dict(owner), dict(guest)])
        pair = {"_id": "intro-pair", "env": server.ENV, "state": "active",
            "inviter": owner["user_id"], "invitee": guest["user_id"], "members": [owner["user_id"], guest["user_id"]],
            "sponsor": owner["user_id"]}
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fictional-intro-provider-key")
        monkeypatch.delenv("DEEPSEEK_PRODUCTION_KEY_ID", raising=False)
        async def signed_in(): return {"user_id": "reader"}
        monkeypatch.setitem(server.app.dependency_overrides, server.require_user, signed_in)
        original = httpx.AsyncClient.post
        order = []
        lines = [{"speaker": "them", "text": "Can we talk tomorrow?", "sure": True}]
        async def post(client, url, **kwargs):
            if url != ai.DEEPSEEK_CHAT_URL:
                return await original(client, url, **kwargs)
            feature = message_costs._generation.get()[1]
            order.append(feature)
            result = {"transcribe_text": {"lines": lines}, "six_part": {"tone": {"status": "insufficient", "drafts": {}}},
                "couples_tone": {"status": "estimated", "incoming": "neutral", "confidence": "limited", "evidence": [0],
                    "explanation": "The question invites a joint decision.", "drafts": {
                        style: style + " Could we choose a grocery day together?" for style in ("warm", "neutral", "direct", "playful", "cool")}}
            }.get(feature, {})
            content = "invalid JSON" if feature == "six_part" and order.count(feature) == 1 else json.dumps(result)
            return httpx.Response(200, json={"id": str(uuid4()), "created": int(time.time()), "model": "deepseek-flash",
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12,
                          "prompt_cache_hit_tokens": 2, "prompt_cache_miss_tokens": 3}})
        monkeypatch.setattr(httpx.AsyncClient, "post", post)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://fixture.invalid") as client:
                headers = {"Idempotency-Key": "intro-first-message", "X-Upload-Attempt": str(uuid4())}
                parsed = await client.post("/api/quickstart/parse", headers=headers,
                    json={"nickname": "Fictional case", "conversation_text": "Case: Can we talk tomorrow?"})
                assert parsed.status_code == 200, parsed.text
                body = {"nickname": "Fictional case", "what_happened": "What might I ask next?", "lines": parsed.json()["lines"]}
                individual = await client.post("/api/quickstart", headers=headers, json=body)
                assert individual.status_code == 200, individual.text
                assert (await client.post("/api/quickstart", headers=headers, json=body)).json()["exchange"]["id"] == individual.json()["exchange"]["id"]
                # Individual is available before confirmation. Once paired,
                # continue the same real shared meter through Couples.
                await db.couples_pairs.insert_one(pair)
                blocked_individual = await client.post("/api/quickstart", headers=headers, json=body)
                assert blocked_individual.status_code == 403
                assert blocked_individual.json()["detail"]["code"] == "couples_workspace_required"
                draft = await client.put("/api/couples/drafts/support-1", json={"version": 0,
                    "lines": ["Could we choose a grocery day together?"]})
                assert draft.status_code == 200, draft.text
                couple = await client.post("/api/couples/tone/private/support-1",
                    headers={"Idempotency-Key": "intro-couple-message"}, json={"pair_id": "intro-pair", "version": 1})
                assert couple.status_code == 200, couple.text
                usage = (await client.get("/api/me")).json()["usage"]
                assert usage["plan"] == "intro" and usage["messages_used"] == 2 and usage["messages_remaining"] == 8
                assert (await client.get("/api/admin/intro-access/summary")).status_code == 403
            calls = await db.ai_call_ledger.find({}).to_list(None)
            assert order == ["transcribe_text", "six_part", "six_part", "report", "couples_tone"]
            assert len(calls) == 5 and all(call["credit_pool"] == "intro" for call in calls)
            assert sum(call.get("retry") is True for call in calls) == 1
            assert {row["workspace"] for row in await db.message_cost_units.find({}).to_list(None)} == {"individual", "couples"}
            report = await intro_access.summary(db, server.ENV)
            assert report["totals"]["used_messages"] == 2 and report["costs"]["attempts"] == 5
            assert report["today"]["attempts"] == 5
            assert not (await db.users.find_one({"user_id": "fictional-partner"})).get("free_reply_requests")
        finally:
            server.app.dependency_overrides.clear()
    run_case(case)


@pytest.mark.parametrize("mode,paired,self_funded,guest_actor", [
    ("private", False, False, False), ("private", True, True, False),
    ("shared", True, True, False), ("private", True, False, True)])
def test_couples_tenth_intro_reply_remains_cached_without_new_job_or_generation(mode, paired, self_funded, guest_actor):
    from backend import couples_tone
    async def case(db):
        adult = {"couples_adult_at": "2026-01-01T00:00:00+00:00", "about": {"demographics": {"age": 30}}}
        owner = member(intro_entitlement=grant(), intro_reply_totals={"claimed": 9, "used": 9}, **adult)
        peer = member("peer", **adult)
        if self_funded:
            peer.update(intro_entitlement=grant(), intro_reply_totals={"claimed": 9, "used": 9})
            peer["intro_billing"] = authorized_billing("peer", peer["intro_entitlement"])
        await db.users.insert_many([dict(owner), dict(peer)])
        actor = "peer" if guest_actor else "reader"
        if paired:
            await db.couples_pairs.insert_one({"_id": "last-intro-pair", "env": "preview", "state": "active",
                "inviter": "reader", "invitee": "peer", "members": ["reader", "peer"], "sponsor": "reader",
                "self_funded": self_funded, "journey": {"threads": {"support-1": {"version": 2, "words_version": 2,
                    "shares": {"a": ["Could we choose a grocery day together?"], "b": ["Can we choose tomorrow?"]}}}}})
        await db.couples_drafts.insert_one({"_id": "last-draft", "user_id": actor, "key": "support-1", "version": 1,
            "lines": ["Could we choose a grocery day together?", "Could we choose a different grocery day?"],
            "deleted": False, "skipped": False})
        calls = []
        async def current(): return await db.users.find_one({"user_id": actor})
        async def generate(*args):
            calls.append("generation")
            return {"status": "estimated", "incoming": "neutral", "confidence": "limited", "evidence": [0],
                "explanation": "The question invites a joint decision.", "drafts": {
                    style: style + " Could we choose a grocery day together?" for style in ("warm", "neutral", "direct", "playful", "cool")}}
        app = FastAPI()
        app.include_router(couples_tone.tone_router(lambda: db, current, generate), prefix="/couples")
        body = {"version": 2 if mode == "shared" else 1}
        if paired:
            body["pair_id"] = "last-intro-pair"
        url = "/couples/tone/" + mode + "/support-1"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://fixture.invalid") as client:
            first = await client.post(url, json=body, headers={"Idempotency-Key": "last-intro"})
            assert first.status_code == 200, first.text
            used = await db.users.find_one({"user_id": "reader"})
            assert used["intro_reply_totals"] == {"claimed": 10, "used": 10}
            assert not free_plan.has_personal_access(used)
            saved = await client.post(url + "/saved", json=body)
            retried = await client.post(url, json=body, headers={"Idempotency-Key": "last-intro"})
            assert saved.status_code == retried.status_code == 200
            assert saved.json()["id"] == retried.json()["id"] == first.json()["id"]
            changed = {**body, "goal": "Ask a different question."} if mode == "shared" else {**body, "line": 1}
            refused = await client.post(url, json=changed, headers={"Idempotency-Key": "new-after-tenth"})
            assert refused.status_code == 402 and refused.headers["x-imladri-limit"] == "intro", refused.text
            assert calls == ["generation"]
            final = await db.users.find_one({"user_id": "reader"})
            assert final["intro_reply_totals"] == used["intro_reply_totals"] and not final.get("intro_active_request")
            assert await db.message_cost_units.count_documents({}) == 1
            assert not (await db.couples_drafts.find_one({"_id": "last-draft"})).get("tone_job")
            pair = await db.couples_pairs.find_one({"_id": "last-intro-pair"})
            assert not couples_tone.value_at(pair, "tone_jobs.support-1.a")
            assert not couples_tone.value_at(pair, "tone_jobs.support-1.b")
            if paired:
                from backend import couples
                if self_funded:
                    # The exception belongs only to the cached coach read.
                    with pytest.raises(HTTPException) as normal_read:
                        await couples.active_pair(db, used)
                    assert normal_read.value.status_code == 402
                other_uid = "reader" if actor == "peer" else "peer"
                await db.users.update_one({"user_id": other_uid}, {"$set": {"couples_blocked": [couples.block_key(actor)]}})
                denied = await client.post(url, json=body)
                assert denied.status_code == 403 and calls == ["generation"]
            else:
                await db.couples_drafts.update_one({"_id": "last-draft"}, {"$inc": {"version": 1}})
                assert (await client.post(url, json=body)).status_code == 409
    run_case(case)
