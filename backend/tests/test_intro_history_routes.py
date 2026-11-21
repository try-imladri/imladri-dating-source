"""Production router flows using isolated PostgreSQL and fictional providers.

No real identity, payment, email or AI request is permitted by this fixture.
"""
import hashlib
import hmac
import asyncio
import json
import os
import socket
import time
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import stripe
from fastapi import HTTPException, Request

from backend import answer_context, answer_history, database, intro_access, intro_card, message_costs, stripe_membership
from backend.tests.billing_provider_fixture import install
from backend.tests.test_admin_answer_history import run_case
from backend.tests.test_couples_tone import GROCERY_DRAFTS, GROCERY_LINE, grocery_read
from backend.tests.test_membership_checkout import item
from backend.tests.test_intro_card import Provider as CardProvider, invoice as card_invoice, tenth


def invoice(key, start, reason, invoice_id):
    price = item(key)
    return {"id": invoice_id, "object": "invoice", "status": "paid", "currency": "usd", "billing_reason": reason,
        "total": price.unit_amount, "amount_paid": price.unit_amount, "total_excluding_tax": price.unit_amount,
        "status_transitions": {"paid_at": start},
        "parent": {"subscription_details": {"subscription": "sub_fictional_route"}},
        "lines": {"data": [{"type": "subscription", "amount": price.unit_amount, "price": price.id,
            "period": {"start": start, "end": start + 2592000}}]}}


def configure(monkeypatch, db):
    values = {"PYTHON_DOTENV_DISABLED": "1", "CLERK_PUBLISHABLE_KEY": "pk_test_fixture", "CLERK_SECRET_KEY": "sk_test_fixture",
        "DEEPSEEK_API_KEY": "fictional-route-provider-key", "STRIPE_SECRET_KEY": "sk_test_fixture", "STRIPE_WEBHOOK_SECRET": "fictional-route-webhook-secret",
        "INTRO_ACCESS_ENABLED": "1", "INTRO_ABUSE_HMAC_SECRET": "fictional-route-intro-key-01234567890123456789",
        "INTRO_ELIGIBLE_SINCE": "2026-01-01T00:00:00+00:00", "INTRO_TURNSTILE_SITE_KEY": "fictional-route-site-key",
        "INTRO_TURNSTILE_SECRET_KEY": "fictional-route-server-key", "INTRO_ALLOWED_HOSTNAMES": "fixture.invalid",
        "INTRO_DAILY_TOKEN_LIMIT": "10000000", "INTRO_DAILY_BUDGET_CENTS": "100000", "AI_DAILY_TOKEN_LIMIT": "10000000",
        "AI_MEMBER_HOURLY_CALL_LIMIT": "200", "ANSWER_HISTORY_CURSOR_SECRET": "fictional-route-history-key-0123456789",
        "PERSONAL_BILLING_MODE": "enabled", "STRIPE_CHECKOUT_MODE": "managed", "PUBLIC_APP_URL": "https://fixture.invalid",
        "INTRO_CARD_FIRST_VERIFIED_STRATEGY": intro_card.STRATEGY, "INTRO_CARD_FIRST_PROVIDER_PROOF_SHA256": "a" * 64,
        "INTRO_CARD_FIRST_COUPON_ID": "coupon_fixture"}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DEEPSEEK_PRODUCTION_KEY_ID", raising=False)
    monkeypatch.delenv("DEEPSEEK_PRODUCTION_KEY_NAME", raising=False)
    # Install the bounded fixture before importing the real application. Never
    # read backend/.env or instantiate a connection to an existing datastore.
    monkeypatch.setattr(database, "open_database", lambda: (db, db))
    from backend import server
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "ENV", "preview")
    monkeypatch.setattr(server, "IN_ENV", {"env": {"$in": ["preview", None]}})
    monkeypatch.setattr(stripe, "api_key", "sk_test_fixture")
    async def identity(request: Request):
        uid = request.headers.get("x-fictional-user")
        if uid not in {"owner", "partner", "staff"}:
            raise HTTPException(401, "Fictional fixture sign-in required.")
        return {"user_id": uid}
    monkeypatch.setitem(server.app.dependency_overrides, server.require_user, identity)
    original_connect = socket.create_connection
    def local_only(address, *args, **kwargs):
        if address[0] not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError("Unexpected external network access in fictional route test")
        return original_connect(address, *args, **kwargs)
    monkeypatch.setattr(socket, "create_connection", local_only)
    challenge = AsyncMock()
    monkeypatch.setattr(intro_access, "validate_challenge", challenge)
    def clerk_get(url, **kwargs):
        assert url.startswith("https://api.clerk.com/v1/users/")
        uid = url.rsplit("/", 1)[-1]
        clerk_get.calls.append(uid)
        payload = {"id": uid, "created_at": int(time.time() * 1000), "primary_email_address_id": "primary",
            "email_addresses": [{"id": "primary", "email_address": uid + "@fixture.invalid", "verification": {"status": "verified"}}]}
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: payload)
    clerk_get.calls = []
    monkeypatch.setattr(server.requests, "get", clerk_get)
    return server, challenge


class RouteCardProvider(CardProvider):
    """Fictional transport with the same canonical price as the billing fixture."""
    def price(self, offer):
        price = super().price(offer)
        return {**price, "id": item("plus_100" if offer == "standard" else offer).id}


def install_card_provider(monkeypatch, server):
    provider = RouteCardProvider()
    monkeypatch.setattr(intro_card, "Provider", lambda: provider)
    plans = {key: item(key) for key in stripe_membership.PRODUCTS}
    monkeypatch.setattr(stripe_membership, "catalog", lambda: plans)
    monkeypatch.setattr(server.stripe_plan, "catalog_price", lambda: plans["plus_100"])
    monkeypatch.setattr(server.partner_offers, "price_for", lambda key: plans["plus_100" if key == "standard" else key])
    install(monkeypatch, globals(), "invoice")
    return provider


async def card_setup_body(db):
    current = await db.users.find_one({"user_id": "owner"})
    quote = (await intro_card.quotes(db, current))["plus_100"]
    return {"expected_member": "owner", "plan_id": "plus_100", "expected_price_cents": 1200, "expected_messages": 100,
        "request_id": str(uuid4()), "authorization_version": intro_card.VERSION, "authorize_after_messages": True,
        "policy_accepted": True, "authorization_text_sha256": quote["authorization_text_sha256"],
        "quote_sha256": quote["quote_sha256"], "origin_url": "https://fixture.invalid", "challenge_token": "fixture"}


async def hosted_intro(api, db, provider):
    headers = {"x-fictional-user": "owner"}
    start = await api.post("/api/me/intro-access/setup", json=await card_setup_body(db), headers=headers)
    assert start.status_code == 200, start.text
    assert not (await db.users.find_one({"user_id": "owner"})).get("intro_entitlement")
    assert not (await db.users.find_one({"user_id": "owner"})).get("intro_converted_at")
    assert (await db.payment_transactions.find_one({"session_id": provider.sid}))["kind"] == "intro_setup"
    provider.session_value["status"] = "complete"
    confirmed = await api.post("/api/me/intro-access/confirm", json={"expected_member": "owner", "session_id": provider.sid}, headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["state"] == "active" and confirmed.json()["messages_remaining"] == 10
    assert confirmed.json()["billing"]["card_verified"] and confirmed.json()["automatic_charge"]
    assert (await db.users.find_one({"user_id": "owner"}))["tier"] == "unsubscribed"
    return confirmed


async def prepare_conversion(db, provider):
    await intro_card.dispatch_due(db, "owner", provider=provider)
    current = await db.users.find_one({"user_id": "owner"})
    assert current["intro_billing"]["conversion"]["stage"] == "awaiting_invoice"
    paid = globals()["invoice"]("plus_100", int(time.time()), "subscription_update", "in_conversion_" + uuid4().hex)
    exact = card_invoice(current["intro_billing"])
    paid.update({key: value for key, value in exact.items() if key != "id"})
    provider.sub["latest_invoice"] = paid
    period = paid["lines"]["data"][0]["period"]
    provider.sub["items"]["data"][0].update(current_period_start=period["start"], current_period_end=period["end"])
    assert sum(call[0] == "convert" for call in provider.calls) == 1
    return paid


async def seed_people(db, server):
    stamp = datetime.now(timezone.utc).isoformat()
    for index, uid in enumerate(("owner", "partner", "staff")):
        await db.users.insert_one({"user_id": uid, "email": uid + "@fixture.invalid", "role": "admin" if uid == "staff" else "member",
            "env": "preview", "tier": "unsubscribed", "created_at": stamp, "nickname": uid.capitalize(), "couples_adult_at": stamp,
            "about": {"answers": {}, "demographics": {"age": 30, "height_cm": 175}},
            "questionnaire_research_version": "all-answers-v1", "questionnaire_research_at": stamp,
            "row_member_version": "row-member-v1", "row_member_code": str(index + 1) * 32,
            "row_member_env": "preview", "row_member_at": stamp,
            "row_couple_version": "row-couple-v1", "row_couple_code": str(index + 4) * 32,
            "row_couple_env": "preview", "row_couple_at": stamp, "row_couple_pair_id": "fictional-pair"})
    await server.ensure_indexes()


def test_real_router_intro_shared_meter_history_admin_and_paid_transition(monkeypatch):
    async def scenario(db):
        server, challenge = configure(monkeypatch, db)
        await seed_people(db, server)
        billing_provider = install_card_provider(monkeypatch, server)
        original_post = httpx.AsyncClient.post
        calls = []
        invalid_first = True
        async def provider_post(client, url, **kwargs):
            nonlocal invalid_first
            from backend import ai
            if str(url) != ai.DEEPSEEK_CHAT_URL:
                return await original_post(client, url, **kwargs)
            feature = message_costs._generation.get()[1]
            calls.append({"feature": feature, "pool": message_costs.current_pool.get(), "message": message_costs.current_message.get()})
            if feature == "six_part":
                result = {"seen": [], "explanations": [], "unknowns": [], "actions": [], "confidence": "low",
                    "what_it_means": "The fictional message asks for a shared plan.", "what_to_say": GROCERY_DRAFTS["neutral"],
                    "proposed_memories": [], "tone": grocery_read(GROCERY_DRAFTS)}
            elif feature == "couples_tone":
                result = grocery_read(GROCERY_DRAFTS)
            else:
                result = {"sections": [], "change_note": "Fictional route fixture only."}
            body = "not-json-once" if feature == "six_part" and invalid_first else json.dumps(result)
            if feature == "six_part":
                invalid_first = False
            return httpx.Response(200, request=httpx.Request("POST", str(url)), json={"id": "fictional-call-" + str(len(calls)),
                "model": "deepseek-flash", "created": int(time.time()), "choices": [{"finish_reason": "stop", "message": {"content": body}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200, "prompt_cache_hit_tokens": 0,
                          "prompt_cache_miss_tokens": 120}})
        monkeypatch.setattr(httpx.AsyncClient, "post", provider_post)
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="https://fixture.invalid") as api:
            async def call(method, path, body=None, *, who="owner", key=None, expected=200):
                headers = {"x-fictional-user": who} if who else {}
                if key:
                    headers["Idempotency-Key"] = key
                response = await api.request(method, "/api" + path, json=body, headers=headers)
                assert response.status_code == expected, (method, path, response.status_code, response.text)
                return response
            assert (await call("GET", "/public/intro-access", who=None)).json()["enabled"]
            await call("GET", "/admin/intro-access/summary", expected=403)
            await call("GET", "/admin/answer-history/member/owner", who=None, expected=401)
            await call("POST", "/me/intro-access/claim", {"expected_member": "partner", "challenge_token": "fixture"}, expected=409)
            await call("POST", "/me/intro-access/claim", {"expected_member": "owner", "verified": True}, expected=422)
            denied = await call("POST", "/me/intro-access/claim", {"expected_member": "owner", "challenge_token": "fixture"}, expected=409)
            assert challenge.await_count == 0 and server.requests.get.calls == []
            claimed = await hosted_intro(api, db, billing_provider)
            assert challenge.await_count == 1 and server.requests.get.calls == ["owner"]
            again = await call("POST", "/me/intro-access/confirm", {"expected_member": "owner", "session_id": billing_provider.sid})
            assert again.json()["messages_remaining"] == 10 and await db.intro_identities.count_documents({}) == 2
            assert not any(secret in claimed.text for secret in ("fictional-route-intro-key", "fictional-route-server-key"))
            intro_members = (await call("GET", "/users?status=intro", who="staff")).json()["users"]
            intro_owner = next(row for row in intro_members if row["user_id"] == "owner")
            assert intro_owner["membership_status"] == "intro" and intro_owner["usage"]["messages_remaining"] == 10
            quiz = (await server.get_settings())["quiz"]
            q1, q2 = quiz["short"][:2]
            await call("POST", "/me/quick-answer", {"qid": q1["id"], "choice": 0})
            old_snapshot = (await db.users.find_one({"user_id": "owner"}))[answer_context.SNAPSHOT]["snapshot_id"]
            permission = await call("PATCH", "/me/answer-history-permission", {"expected_member": "owner", "version": "history-staff-v1",
                "revision": 0, "enabled": True, "adult_confirmed": True})
            assert permission.json()["enabled"]
            await call("POST", "/me/quick-answer", {"qid": q2["id"], "choice": 1})
            new_snapshot = (await db.users.find_one({"user_id": "owner"}))[answer_context.SNAPSHOT]["snapshot_id"]
            assert old_snapshot != new_snapshot
            timeline = (await call("GET", "/admin/answer-history/member/owner", who="staff")).json()
            old_answer = next(row for row in timeline["items"] if row.get("domain") == "everyday" and row["status"] == "withheld")
            assert "question_id" not in old_answer and "after" not in old_answer
            fresh = next(row for row in timeline["items"] if row.get("question_id") == "e:" + q2["id"])
            assert fresh["demographics_at_observation"]["age"] == 30
            csv = await call("GET", "/admin/answer-history/member/owner.csv", who="staff")
            assert csv.headers["content-type"].startswith("text/csv") and "no-store" in csv.headers["cache-control"]
            person = (await call("POST", "/people", {"nickname": "Fictional Alex", "rel_type": "partner", "stage": "early",
                "background": "A fictional adult discussion about groceries.", "goal": "Choose a shopping day."})).json()
            pid = person["id"]
            body = {"conversation": [{"speaker": "them", "text": GROCERY_LINE}], "question": "Help me ask to choose a shopping day."}
            first = (await call("POST", f"/people/{pid}/exchanges", body, key="individual-first")).json()
            assert (await db.users.find_one({"user_id": "owner"}))["intro_reply_totals"]["used"] == 1
            assert any(record["feature"] == "six_part" for record in calls)
            first_calls = await db.ai_call_ledger.find({"message_id": first["id"]}).to_list(None)
            assert any(row["retry"] for row in first_calls) and any(row["mode"] == "background" for row in first_calls)
            assert all(row["credit_pool"] == "intro" for row in first_calls)
            for n in range(8):
                last = (await call("POST", f"/people/{pid}/exchanges", body, key="individual-" + str(n))).json()
            before_pair_calls = len(calls)
            cached_individual = await call("POST", f"/people/{pid}/exchanges", body, key="individual-7")
            assert cached_individual.json()["id"] == last["id"] and len(calls) == before_pair_calls
            # A sponsored partner has no personal credits. Their coaching draws
            # the tenth unit from the same owner after full pair confirmation.
            await db.couples_pairs.insert_one({"_id": "fictional-pair", "env": "preview", "state": "active", "inviter": "owner", "invitee": "partner",
                "members": ["owner", "partner"], "sponsor": "owner"})
            saved = await call("PUT", "/couples/drafts/support-1", {"version": 0,
                "lines": [GROCERY_LINE, "Could we choose a grocery day together tomorrow?"]}, who="partner")
            version = saved.json().get("version", 1)
            couple_body = {"pair_id": "fictional-pair", "version": version}
            couple = (await call("POST", "/couples/tone/private/support-1", couple_body, who="partner", key="couple-first")).json()
            assert (await db.users.find_one({"user_id": "owner"}))["intro_reply_totals"]["used"] == 10
            assert not (await db.users.find_one({"user_id": "partner"})).get("intro_entitlement")
            couple_unit = await db.message_cost_units.find_one({"_id": couple["id"]})
            assert couple_unit["workspace"] == "couples" and couple_unit["pool"] == "intro"
            used = (await call("GET", "/me")).json()
            assert used["usage"]["messages_used"] == 10 and used["usage"]["messages_remaining"] == 0
            exhausted_members = (await call("GET", "/users?status=intro_exhausted", who="staff")).json()["users"]
            exhausted_owner = next(row for row in exhausted_members if row["user_id"] == "owner")
            assert exhausted_owner["membership_status"] == "intro_exhausted" and exhausted_owner["usage"]["messages_remaining"] == 0
            count_before = len(calls)
            repeated = await call("POST", f"/people/{pid}/exchanges", body, key="individual-7", expected=403)
            assert repeated.json()["detail"]["code"] == "couples_workspace_required" and len(calls) == count_before
            repeated_couple = await call("POST", "/couples/tone/private/support-1", couple_body, who="partner", key="couple-first")
            assert repeated_couple.json()["id"] == couple["id"] and len(calls) == count_before
            blocked = await call("POST", "/couples/tone/private/support-1", {**couple_body, "line": 1},
                                 who="partner", key="eleventh", expected=402)
            assert blocked.headers["x-imladri-limit"] == "intro" and len(calls) == count_before
            owner_history = await call("GET", "/me/answer-history")
            assert owner_history.json()["items"]
            assert await db.exchanges.count_documents({"user_id": "owner"}) == 9
            for rid in (first["id"], couple["id"]):
                receipt = (await call("GET", "/admin/prompts/message-costs/receipts/" + rid, who="staff")).json()
                assert receipt["message"]["pool"] == "intro" and receipt["calls"]
                assert not any("prompt" in key for row in receipt["calls"] for key in row)
            summary = (await call("GET", "/admin/intro-access/summary", who="staff")).json()
            assert summary["totals"]["used_messages"] == 10 and summary["costs"]["attempts"] == len(calls)
            # The tenth delivered reply queues one conversion. Only an exact
            # paid full-month invoice, received through the signed webhook,
            # begins the selected monthly allowance.
            pending = await db.users.find_one({"user_id": "owner"})
            assert pending["intro_billing"]["state"] == "billing_pending"
            await call("POST", "/payments/checkout", {"expected_member": "owner", "offer": "plus_100", "origin_url": "https://fixture.invalid",
                "purchase_policy_accepted": True, "purchase_policy_version": "imladri-purchases-2026-11-v1"}, expected=409)
            paid = await prepare_conversion(db, billing_provider)
            assert (await call("GET", "/me")).json()["usage"]["messages_remaining"] == 0
            event = {"id": "evt_fictional_route", "object": "event", "type": "invoice.paid", "data": {"object": paid}}
            payload = json.dumps(event).encode()
            stamp = str(int(time.time()))
            signature = hmac.new(b"fictional-route-webhook-secret", stamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
            delivered = await api.post("/api/stripe/webhook", content=payload, headers={"stripe-signature": "t=" + stamp + ",v1=" + signature})
            assert delivered.status_code == 200, delivered.text
            # This must be durable in the paid webhook itself, before /me or
            # initialize_usage can observe the paid account.
            assert (await db.users.find_one({"user_id": "owner"})).get("intro_converted_at")
            paid_me = (await call("GET", "/me")).json()
            assert paid_me["usage"]["message_limit"] == 100 and paid_me["usage"]["messages_remaining"] == 100
            assert paid_me["tier"] == "paid" and paid_me["intro_access"]["state"] == "converted"
            paid_members = (await call("GET", "/users?status=active", who="staff")).json()["users"]
            paid_owner = next(row for row in paid_members if row["user_id"] == "owner")
            assert paid_owner["membership_status"] == "active" and paid_owner["usage"]["messages_remaining"] == 100
            await call("POST", "/couples/tone/private/support-1", {**couple_body, "line": 1},
                       who="partner", key="paid-first")
            assert (await call("GET", "/me")).json()["usage"]["messages_remaining"] == 99
            current = await db.users.find_one({"user_id": "owner"})
            assert current["intro_reply_totals"]["used"] == 10 and current.get("intro_converted_at")
            await call("PATCH", "/me/answer-history-permission", {"expected_member": "owner", "version": "history-staff-v1",
                "revision": 1, "enabled": False, "adult_confirmed": False})
            revoked = (await call("GET", "/admin/answer-history/member/owner", who="staff")).json()
            assert all(row["status"] == "withheld" for row in revoked["items"])
            assert "Fictional Alex" not in str(revoked)
    run_case(scenario)


def test_paid_webhook_wins_while_card_setup_is_waiting_for_provider_challenge(monkeypatch):
    async def scenario(db):
        server, _ = configure(monkeypatch, db)
        await seed_people(db, server)
        provider = install_card_provider(monkeypatch, server)
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed_challenge(_token):
            entered.set()
            await release.wait()
        monkeypatch.setattr(intro_access, "validate_challenge", delayed_challenge)
        sessions = {}
        def create(**opts):
            session = stripe.checkout.Session.construct_from({"id": "cs_race_route", "object": "checkout.session", "mode": opts["mode"],
                "url": "https://checkout.stripe.com/fictional", "status": "open", "metadata": opts["metadata"]}, None)
            sessions[session.id] = session
            return session
        monkeypatch.setattr(stripe.checkout.Session, "create", create)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="https://fixture.invalid") as api:
            headers = {"x-fictional-user": "owner"}
            # Preserve the historical open-checkout race without allowing a new
            # card-first member to bypass setup through the old purchase route.
            fresh = (await db.users.find_one({"user_id": "owner"}))["created_at"]
            await db.users.update_one({"user_id": "owner"}, {"$set": {"created_at": "2025-01-01T00:00:00+00:00"}})
            checkout = await api.post("/api/payments/checkout", json={"expected_member": "owner", "offer": "plus_100", "origin_url": "https://fixture.invalid",
                "purchase_policy_accepted": True, "purchase_policy_version": "imladri-purchases-2026-11-v1"}, headers=headers)
            assert checkout.status_code == 200, checkout.text
            await db.users.update_one({"user_id": "owner"}, {"$set": {"created_at": fresh}})
            setup = await card_setup_body(db)
            claiming = asyncio.create_task(api.post("/api/me/intro-access/setup", json=setup, headers=headers))
            await asyncio.wait_for(entered.wait(), timeout=5)
            session = sessions["cs_race_route"]
            paid = globals()["invoice"]("plus_100", int(time.time()) - 1, "subscription_create", "in_race_route")
            session.update({"subscription": "sub_fictional_route", "payment_status": "paid", "customer": "cus_fictional_route", "invoice": paid})
            event = {"id": "evt_race_route", "object": "event", "type": "checkout.session.completed", "data": {"object": session}}
            payload = json.dumps(event).encode()
            stamp = str(int(time.time()))
            signature = hmac.new(b"fictional-route-webhook-secret", stamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
            try:
                delivered = await api.post("/api/stripe/webhook", content=payload, headers={"stripe-signature": "t=" + stamp + ",v1=" + signature})
                assert delivered.status_code == 200, delivered.text
            finally:
                release.set()
            result = await claiming
            assert result.status_code == 409, result.text
            current = await db.users.find_one({"user_id": "owner"})
            assert current.get("intro_converted_at") and not current.get("intro_entitlement") and not current.get("intro_billing")
            assert not any(call[0] == "create" for call in provider.calls)
            me = await api.get("/api/me", headers=headers)
            assert me.status_code == 200 and me.json()["usage"]["messages_remaining"] == 100
    run_case(scenario)


def test_paid_webhook_then_immediate_cancellation_cannot_restore_intro_without_member_reload(monkeypatch):
    async def scenario(db):
        server, _ = configure(monkeypatch, db)
        await seed_people(db, server)
        provider = install_card_provider(monkeypatch, server)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="https://fixture.invalid") as api:
            headers = {"x-fictional-user": "owner"}
            await hosted_intro(api, db, provider)
            current = await db.users.find_one({"user_id": "owner"})
            await tenth(db, current)
            paid = await prepare_conversion(db, provider)
            async def webhook(kind, value):
                event = {"id": "evt_" + str(uuid4()), "object": "event", "type": kind, "data": {"object": value}}
                payload = json.dumps(event).encode()
                stamp = str(int(time.time()))
                signature = hmac.new(b"fictional-route-webhook-secret", stamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
                response = await api.post("/api/stripe/webhook", content=payload, headers={"stripe-signature": "t=" + stamp + ",v1=" + signature})
                assert response.status_code == 200, response.text
            await webhook("invoice.paid", paid)
            before = await db.users.find_one({"user_id": "owner"})
            first_conversion = before.get("intro_converted_at")
            assert first_conversion
            # No authenticated member request occurs between the two webhooks.
            await webhook("customer.subscription.deleted", {"id": provider.sub["id"], "metadata": {"user_id": "owner"}, "status": "canceled"})
            canceled = await db.users.find_one({"user_id": "owner"})
            assert canceled["intro_converted_at"] == first_conversion and not intro_access.access(canceled)
            assert not intro_access.eligibility(canceled)
            current = await api.get("/api/me", headers=headers)
            assert current.status_code == 200 and current.json()["intro_access"]["state"] == "converted"
            assert current.json()["usage"]["messages_remaining"] == 0
            # Replaying the original zero-dollar completion cannot erase the
            # first paid conversion marker or grant another allowance.
            await webhook("checkout.session.completed", provider.session_value)
            assert (await db.users.find_one({"user_id": "owner"}))["intro_converted_at"] == first_conversion
    run_case(scenario)
