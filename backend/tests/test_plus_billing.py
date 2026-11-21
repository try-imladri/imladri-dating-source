"""Stripe contract regressions. Set IMLADRI_BILLING_TEST_MONGO to a loopback URI for the integration test."""
import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlsplit

import pytest
import stripe
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from starlette.requests import Request

from backend import stripe_plan
from scripts import setup_stripe_test_price
from scripts import setup_stripe_live_price
from backend.tests.billing_provider_fixture import install


@pytest.fixture(autouse=True)
def verified_billing(monkeypatch):
    return install(monkeypatch, globals(), "invoice")


def plan_price(amount=1200):
    return stripe.Price.construct_from({"id": "price_fixture", "object": "price", "currency": "usd",
        "unit_amount": amount, "livemode": False, "billing_scheme": "per_unit",
        "recurring": {"interval": "month", "interval_count": 1, "usage_type": "licensed"}}, None)


def invoice(start, invoice_id="in_initial", price="price_fixture"):
    return {"id": invoice_id, "object": "invoice", "status": "paid", "billing_reason": "subscription_cycle",
            "currency": "usd", "amount_paid": 1200, "subtotal": 1200, "total": 1200,
            "parent": {"subscription_details": {"subscription": "sub_fixture", "metadata": {"lookup_key": stripe_plan.LOOKUP_KEY}}},
            "lines": {"data": [{"amount": 1200, "parent": {"subscription_item_details": {"proration": False}},
                                "pricing": {"price_details": {"price": price}}, "period": {"start": start, "end": start + 2592000}}]}}


def test_sandbox_price_creation_and_reuse(monkeypatch):
    stored = []
    monkeypatch.setattr(stripe.Price, "list", lambda **kw: SimpleNamespace(data=stored[:]))
    def create(**kw):
        assert kw["currency"] == "usd" and kw["unit_amount"] == 1200 and kw["recurring"] == {"interval": "month"}
        assert kw["lookup_key"] == stripe_plan.LOOKUP_KEY and kw["idempotency_key"] == "imladri-plus-monthly-12-v1"
        assert "tax_code" not in kw["product_data"]
        stored.append(plan_price())
        return stored[0]
    monkeypatch.setattr(stripe.Price, "create", create)
    monkeypatch.setattr(stripe, "api_key", None)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_fixture")
    with pytest.raises(ValueError):
        setup_stripe_test_price.setup()
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fixture")
    assert setup_stripe_test_price.setup() == setup_stripe_test_price.setup() == "price_fixture"
    assert len(stored) == 1
    stored[:] = [plan_price(amount=1300)]
    with pytest.raises(ValueError):
        setup_stripe_test_price.setup()
    stored[:] = [plan_price()]
    stored[0]["recurring"]["trial_period_days"] = 14
    with pytest.raises(ValueError):
        setup_stripe_test_price.setup()


def test_period_comes_from_nonprorated_price_line():
    paid = invoice(100)
    assert stripe_plan.paid_period(paid, "sub_fixture", "price_fixture") == (100, 2592100)
    assert stripe_plan.paid_period(paid, "sub_other", "price_fixture") is None
    assert stripe_plan.paid_period(paid, "sub_fixture", "price_other") is None
    paid["lines"]["data"][0]["parent"]["subscription_item_details"]["proration"] = True
    assert stripe_plan.paid_period(paid, "sub_fixture", "price_fixture") is None
    paid["lines"]["data"] = [{"type": "subscription", "amount": 1200, "price": {"id": "old_price"}, "period": {"start": 30, "end": 80}}]
    assert stripe_plan.paid_period(paid, "sub_fixture", "old_price") == (30, 80)
    paid["lines"]["data"][0]["amount"] = 0
    assert stripe_plan.paid_period(paid, "sub_fixture", "old_price") is None
    paid["lines"]["data"][0]["amount"] = 1200
    paid["total"] = 0
    assert stripe_plan.paid_period(paid, "sub_fixture", "old_price") is None


@pytest.mark.parametrize("replaced", [False, True], ids=["cancelled", "replacement"])
def test_delayed_paid_renewal_is_counted_once_on_payment_date_after_cancellation(monkeypatch, replaced):
    uri = os.environ.get("IMLADRI_BILLING_TEST_MONGO")
    if not uri:
        pytest.skip("Set IMLADRI_BILLING_TEST_MONGO for the local Mongo integration")
    assert urlsplit(uri).hostname in ("localhost", "127.0.0.1", "::1")
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", "imladri_unused")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "fixture")
    from backend import server, trends
    monkeypatch.setattr(stripe_plan, "catalog_price", plan_price)
    paid_at = int(time.time()) - 20 * 86400
    paid = invoice(paid_at, "in_delayed")
    paid["status_transitions"] = {"paid_at": paid_at}
    event = {"id": "evt_delayed", "object": "event", "type": "invoice.paid", "data": {"object": paid}}

    async def run():
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
        db = client["imladri_delayed_invoice_" + uuid.uuid4().hex]
        monkeypatch.setattr(server, "db", db)
        member = {"user_id": "fixture", "stripe_subscription_id": "sub_replacement" if replaced else "sub_fixture",
                  "tier": "paid" if replaced else "unsubscribed", "subscription_status": "active" if replaced else "cancelled",
                  "stripe_price_id": "price_fixture", "cancelled_stripe_subscriptions": ["sub_fixture"],
                  "subscription_period_start": paid_at - 100, "subscription_period_end": paid_at + 100}
        try:
            await db.users.insert_one(member.copy())
            await db.payment_transactions.insert_one({"kind": "purchase", "payment_status": "paid", "user_id": "fixture",
                "stripe_subscription_id": "sub_fixture", "stripe_price_id": "price_fixture"})
            async def deliver():
                timestamp = int(time.time())
                payload = json.dumps(event).encode()
                signature = hmac.new(b"fixture", str(timestamp).encode() + b"." + payload, hashlib.sha256).hexdigest()
                return await server.stripe_webhook(Mock(headers={"stripe-signature": f"t={timestamp},v1={signature}"},
                                                        body=AsyncMock(return_value=payload)))
            await asyncio.gather(deliver(), deliver(), deliver())
            timestamp = datetime.fromtimestamp(paid_at, timezone.utc).isoformat()
            assert await db.payment_transactions.count_documents({"kind": "renewal"}) == 1
            assert await db.payment_transactions.count_documents({"invoice_id": "in_delayed", "created_at": timestamp}) == 1
            assert await db.events.count_documents({"type": "renewal", "ts": timestamp, "user_id": "fixture"}) == 1
            eligible = {"user_id": "fixture"}
            quiz = server.default_quiz()
            recent = await trends.report(db, eligible, quiz, 7)
            month = await trends.report(db, eligible, quiz, 30)
            assert recent["totals"]["paid_renewals"] == 0
            assert [(d["day"], d["paid_renewals"]) for d in month["daily"] if d["paid_renewals"]] == [(timestamp[:10], 1)]
            stored = await db.users.find_one({"user_id": "fixture"})
            assert all(stored[key] == value for key, value in member.items())
            # A removed account's remaining purchase record cannot recreate its renewal history.
            await db.users.delete_one({"user_id": "fixture"})
            paid["id"] = "in_closed_member"
            await deliver()
            assert not await db.payment_transactions.count_documents({"invoice_id": "in_closed_member"})
        finally:
            await client.drop_database(db.name)
            client.close()

    asyncio.run(run())


def test_read_only_live_release_verification(monkeypatch):
    account = stripe.Account.construct_from({"id": "acct_fixture", "charges_enabled": True}, None)
    price = plan_price()
    price.update({"livemode": True, "product": "prod_fixture"})
    product = {"active": True, "livemode": True, "tax_code": stripe_plan.LIVE_PRODUCT_TAX_CODE}
    features = {name: {"enabled": True} for name in ("subscription_cancel", "invoice_history", "payment_method_update")}
    portal = {"active": True, "livemode": True, "features": features}
    hook = {"url": setup_stripe_live_price.WEBHOOK_URL, "livemode": True, "status": "enabled",
            "enabled_events": sorted(setup_stripe_live_price.WEBHOOK_EVENTS)}
    hooks = [hook]
    monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_live_fixture")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_fixture")
    monkeypatch.setenv("STRIPE_PORTAL_CONFIGURATION", "bpc_fixture")
    monkeypatch.setattr(stripe, "api_key", None)
    monkeypatch.setattr(stripe.Account, "retrieve", lambda: account)
    monkeypatch.setattr(stripe.Price, "list", lambda **kwargs: SimpleNamespace(data=[price]))
    monkeypatch.setattr(stripe.Price, "create", lambda **kwargs: pytest.fail("read-only verification created a price"))
    monkeypatch.setattr(stripe.Product, "retrieve", lambda product_id: product)
    monkeypatch.setattr(stripe.billing_portal.Configuration, "retrieve", lambda portal_id: portal)
    monkeypatch.setattr(stripe.WebhookEndpoint, "list", lambda **kwargs: SimpleNamespace(auto_paging_iter=lambda: iter(hooks)))

    def verify():
        return setup_stripe_live_price.setup("acct_fixture", check_release=True)
    assert verify() == price.id
    with monkeypatch.context() as m:
        m.setitem(account, "charges_enabled", False)
        with pytest.raises(ValueError, match="live charges"):
            verify()
    with monkeypatch.context() as m:
        m.setitem(product, "active", False)
        with pytest.raises(ValueError, match="active live product"):
            verify()
    for current in (None, "txcd_other"):
        with monkeypatch.context() as m:
            m.setitem(product, "tax_code", current)
            with pytest.raises(ValueError, match=f"tax code is {current or 'unset'}"):
                verify()
    with monkeypatch.context() as m:
        m.setitem(features["subscription_cancel"], "enabled", False)
        with pytest.raises(ValueError, match="cancellation"):
            verify()
    with monkeypatch.context() as m:
        m.setitem(hook, "enabled_events", ["invoice.paid"])
        with pytest.raises(ValueError, match="checkout.session.completed"):
            verify()
    with monkeypatch.context() as m:
        m.setitem(hook, "url", "https://imladri-personal-pilot.onrender.com/api/stripe/webhook")
        with pytest.raises(ValueError, match="one enabled live webhook"):
            verify()
    with monkeypatch.context() as m:
        m.delenv("STRIPE_WEBHOOK_SECRET")
        with pytest.raises(ValueError, match="signing secret"):
            verify()
    hooks.append(hook.copy())
    with pytest.raises(ValueError, match="one enabled live webhook"):
        verify()


def test_subscription_revenue_excludes_tax_discounts_and_unrelated_items(monkeypatch):
    sub, price = "sub_fixture", "price_fixture"
    payment = invoice(100)
    payment.update(total=1296, amount_paid=1296, total_excluding_tax=1200)
    assert stripe_plan.paid_plan_charge(payment, sub, price)[1] == 1200
    payment.update(total=1080, amount_paid=1080, total_excluding_tax=1000)
    assert stripe_plan.paid_plan_charge(payment, sub, price)[1] == 1000
    payment["amount_paid"] = 0
    assert stripe_plan.paid_plan_charge(payment, sub, price)[1] == 0
    payment["amount_paid"] = 540
    assert stripe_plan.paid_plan_charge(payment, sub, price)[1] == 500
    # A mixed invoice's header includes unrelated products. Only the Plus line's allocated credits and tax count.
    other = invoice(100, price="price_unrelated")["lines"]["data"][0]
    line = payment["lines"]["data"][0]
    line.update(amount=1296, discount_amounts=[{"amount": 216}], taxes=[{"amount": 80, "tax_behavior": "inclusive"}])
    payment.update(total=2280, amount_paid=2280, total_excluding_tax=2200)
    payment["lines"]["data"].append(other)
    assert stripe_plan.paid_plan_charge(payment, sub, price)[1] == 1000
    line.update(subtotal=1200, discount_amounts=[{"amount": 200}], pretax_credit_amounts=[{"amount": 200}])
    assert stripe_plan.paid_plan_charge(payment, sub, price)[1] == 1000
    line.pop("subtotal")
    line.pop("taxes")
    line.update(amount=1200, tax_amounts=[{"amount": 80, "inclusive": False}])
    assert stripe_plan.paid_plan_charge(payment, sub, price)[1] == 1000
    line.update(amount=1280, tax_amounts=[{"amount": 80, "inclusive": True}])
    assert stripe_plan.paid_plan_charge(payment, sub, price)[1] == 1000
    # Paged invoices can place an unrelated line outside the webhook's embedded page.
    payment["lines"] = {"data": [line], "has_more": True}
    calls = []
    def list_lines(invoice_id, limit):
        calls.append(invoice_id)
        return SimpleNamespace(auto_paging_iter=lambda: iter([line, other]))
    monkeypatch.setattr(stripe.Invoice, "list_lines", list_lines)
    assert stripe_plan.paid_plan_charge(payment, sub, price)[1] == 1000 and calls == [payment["id"]]


def test_checkout_periods_and_retries(monkeypatch):
    uri = os.getenv("IMLADRI_BILLING_TEST_MONGO")
    if not uri:
        pytest.skip("Set IMLADRI_BILLING_TEST_MONGO for the local Mongo integration")
    assert urlsplit(uri).hostname in ("localhost", "127.0.0.1", "::1")
    monkeypatch.delenv("STRIPE_CHECKOUT_MODE", raising=False)
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", "imladri_unused")
    from backend import server
    from backend.free_plan import reply_usage
    monkeypatch.setattr(stripe, "api_key", "sk_test_fixture")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.invalid")
    monkeypatch.setattr(stripe.Price, "list", lambda **kw: SimpleNamespace(data=[plan_price()]))
    start = int(time.time()) - 50
    first_invoice = invoice(start)
    first_invoice.update(amount_paid=1296, total=1296, total_excluding_tax=1200)
    first_invoice["status_transitions"] = {"paid_at": start + 1}
    def retrieve_invoice(invoice_id, *, expand):
        assert invoice_id == "in_initial" and expand == ["payments"]
        return first_invoice
    monkeypatch.setattr(stripe.Invoice, "retrieve", retrieve_invoice)
    session = stripe.checkout.Session.construct_from({"id": "cs_fixture", "url": "https://checkout.example.invalid/",
        "object": "checkout.session", "subscription": "sub_fixture", "invoice": "in_initial",
        "customer": "cus_fixture", "status": "open", "payment_status": "unpaid"}, None)
    created = []
    def checkout(**kw):
        assert not any("trial" in key for key in (*kw, *kw["subscription_data"]))
        assert kw["line_items"] == [{"price": "price_fixture", "quantity": 1}]
        assert time.time() + 22 * 3600 < kw["expires_at"] < time.time() + 23.5 * 3600
        assert kw["success_url"].startswith("https://app.example.invalid/app/upgrade")
        if kw.get("managed_payments"):
            raise stripe.InvalidRequestError("Managed payments unavailable", None)
        if kw.get("automatic_tax"):
            raise stripe.InvalidRequestError("A head office address is required for automatic tax in test mode", None)
        assert kw["idempotency_key"].endswith(":sandbox")
        created.append(kw["idempotency_key"])
        return session
    monkeypatch.setattr(stripe.checkout.Session, "create", checkout)
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: session)
    monkeypatch.setattr(stripe.billing_portal.Session, "create", lambda **kw: SimpleNamespace(url="https://portal.example.invalid/"))
    member = {"user_id": "u_fixture", "tier": "free", "role": "member"}

    async def run():
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
        db = client["imladri_billing_test_" + uuid.uuid4().hex]
        monkeypatch.setattr(server, "db", db)
        try:
            await db.users.insert_one(member.copy())
            req = Request({"type": "http", "scheme": "https", "server": ("app.example.invalid", 443), "path": "/", "headers": []})
            body = server.CheckoutIn(purchase_policy_accepted=True, purchase_policy_version="imladri-purchases-2026-11-v1", origin_url="https://app.example.invalid")
            await asyncio.gather(*(server.create_checkout(body, req, member) for _ in range(12)))
            assert len(set(created)) == 1 and await db.payment_transactions.count_documents({}) == 1
            assert (await server.payment_status("cs_fixture", member))["payment_status"] == "pending"
            session["status"] = "complete"
            assert (await server.payment_status("cs_fixture", member))["tier"] == "unsubscribed"
            session["payment_status"] = "paid"
            assert (await server.payment_status("cs_fixture", member))["tier"] == "paid"
            paid_at = datetime.fromtimestamp(start + 1, timezone.utc).isoformat()
            payment = await db.payment_transactions.find_one({"session_id": "cs_fixture"})
            assert payment["fulfilled_at"] == paid_at and payment["subscription_revenue_cents"] == 1200
            assert payment["env"] == server.ENV
            assert (await db.events.find_one({"type": "purchase"}))["ts"] == paid_at
            updated = await db.users.find_one({"user_id": member["user_id"]})
            assert updated["subscription_period_start"] == start and reply_usage(updated)["messages_remaining"] == 100
            assert (await server.billing_portal(body, req, updated))["portal_url"].startswith("https://portal.")
            tx = await db.payment_transactions.find_one({"session_id": session.id})
            # Legacy hydration requires the same canonical Stripe price as a new subscriber.
            legacy = {"user_id": "old_member", "tier": "paid", "stripe_subscription_id": "sub_old"}
            await db.users.insert_one(legacy.copy())
            legacy_invoice = invoice(start, "in_old_member", "price_old_member")
            legacy_invoice["parent"]["subscription_details"]["subscription"] = "sub_old"
            subscription = {"status": "active", "items": {"data": [{"price": {**plan_price(), "id": "price_old_member"}}]},
                            "latest_invoice": legacy_invoice, "customer": "cus_old"}
            monkeypatch.setattr(stripe.Subscription, "retrieve", lambda *args, **kwargs: subscription)
            subscription["items"]["data"][0]["price"]["unit_amount"] = 500
            assert not stripe_plan.subscription_receipt("sub_old")
            subscription["items"]["data"][0]["price"]["unit_amount"] = 1200
            assert not stripe_plan.subscription_receipt("sub_old")  # same terms, different product/price
            subscription["items"]["data"][0]["price"]["id"] = "price_fixture"
            legacy_invoice["lines"]["data"][0]["pricing"]["price_details"]["price"] = "price_fixture"
            legacy = await server.hydrate_subscription_period(legacy)
            assert legacy["stripe_price_id"] == "price_fixture" and reply_usage(legacy)["messages_remaining"] == 100
            renew = invoice(start + 2592000, "in_renew")
            renew.update(amount_paid=1080, total=1080, total_excluding_tax=1000)
            attempts = await asyncio.gather(*(server.renew_subscription(renew) for _ in range(12)), return_exceptions=True)
            overlaps = [value for value in attempts if isinstance(value, Exception)]
            assert all(isinstance(value, HTTPException) and value.status_code == 503
                       and "another reconciliation is running" in value.detail for value in overlaps)
            # Stripe retries a 503. Wait for the whole first delivery wave to
            # finish before one bounded replay; do not destroy its live lease.
            await server.renew_subscription(renew)
            assert (await db.payment_transactions.find_one({"invoice_id": "in_renew"}))["subscription_revenue_cents"] == 1000
            # A pre-pilot invoice was saved with Mongo's default ObjectId.
            old = await db.payment_transactions.insert_one({"invoice_id": "in_old", "kind": "renewal", "user_id": member["user_id"]})
            await server.renew_subscription(invoice(start, "in_old"))
            await server.renew_subscription(invoice(start + 5184000, "in_wrong", price="price_other"))
            await server.mark_paid(tx, session)
            updated = await db.users.find_one({"user_id": member["user_id"]})
            assert updated["subscription_period_start"] == start + 2592000
            assert await db.payment_transactions.count_documents({"invoice_id": "in_renew"}) == 1
            assert await db.payment_transactions.count_documents({"invoice_id": "in_old"}) == 1
            assert (await db.payment_transactions.find_one({"invoice_id": "in_old"}))["_id"] == old.inserted_id
            assert await db.events.count_documents({"_id": "stripe:renewal:in_renew"}) == 1
            assert await db.events.count_documents({"type": "purchase"}) == 1
            monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "fixture")
            event = stripe.Event.construct_from({"id": "evt_fixture", "type": "customer.subscription.deleted",
                "data": {"object": {"id": "sub_fixture", "metadata": {"user_id": member["user_id"], "lookup_key": stripe_plan.LOOKUP_KEY}}}}, None)
            monkeypatch.setattr(stripe.Webhook, "construct_event", lambda *args: event)
            await server.stripe_webhook(Mock(headers={}, body=AsyncMock(return_value=b"")))
            await server.renew_subscription(renew)
            await server.mark_paid(tx, session)
            assert (await db.users.find_one({"user_id": member["user_id"]}))["tier"] == "unsubscribed"
        finally:
            await client.drop_database(db.name)
            client.close()
    asyncio.run(run())


def test_standard_checkout_retries_and_mode_changes(monkeypatch):
    uri = os.getenv("IMLADRI_BILLING_TEST_MONGO")
    if not uri:
        pytest.skip("Set IMLADRI_BILLING_TEST_MONGO for the local Mongo integration")
    assert urlsplit(uri).hostname in ("localhost", "127.0.0.1", "::1")
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", "imladri_unused")
    monkeypatch.setenv("PERSONAL_BILLING_MODE", "enabled")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.invalid")
    monkeypatch.setenv("STRIPE_CHECKOUT_MODE", "standard")
    from backend import server
    monkeypatch.setattr(stripe, "api_key", "sk_test_fixture")
    monkeypatch.setattr(stripe_plan, "catalog_price", plan_price)
    calls = []
    def checkout(**options):
        calls.append(options)
        assert "managed_payments" not in options
        assert options["automatic_tax"] == {"enabled": True}
        assert options["billing_address_collection"] == "required"
        assert time.time() + 1800 < options["expires_at"] <= time.time() + 86400
        if len(calls) == 1:
            raise stripe.APIConnectionError("An ambiguous network timeout")
        if len(calls) == 2:
            raise stripe.InvalidRequestError("A head office address is required in test mode", None)
        return SimpleNamespace(id="cs_standard_fixture", url="https://checkout.example.invalid/standard")
    monkeypatch.setattr(stripe.checkout.Session, "create", checkout)

    async def run():
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
        db = client["imladri_checkout_mode_test_" + uuid.uuid4().hex]
        monkeypatch.setattr(server, "db", db)
        member = {"user_id": "fixture", "tier": "free", "role": "member"}
        req = Request({"type": "http", "scheme": "https", "server": ("app.example.invalid", 443), "path": "/", "headers": []})
        body = server.CheckoutIn(purchase_policy_accepted=True, purchase_policy_version="imladri-purchases-2026-11-v1", origin_url="https://app.example.invalid")
        try:
            await db.users.insert_one(member.copy())
            for _ in range(2):
                with pytest.raises(HTTPException) as unavailable:
                    await server.create_checkout(body, req, member)
                assert unavailable.value.status_code == 503
            replies = await asyncio.gather(*(server.create_checkout(body, req, member) for _ in range(6)))
            assert {row["session_id"] for row in replies} == {"cs_standard_fixture"}
            assert all(call == calls[0] for call in calls)
            assert len(calls) == 8 and calls[0]["idempotency_key"].endswith(":tax")
            assert await db.payment_transactions.count_documents({}) == 1
            monkeypatch.setenv("STRIPE_CHECKOUT_MODE", "auto")
            with pytest.raises(HTTPException) as other_mode:
                await server.create_checkout(body, req, member)
            assert other_mode.value.status_code == 409
            # A pre-release attempt with no stored mode must also block switching to standard.
            await db.users.update_one({"user_id": "fixture"}, {"$unset": {"checkout_attempt.mode": ""}})
            monkeypatch.setenv("STRIPE_CHECKOUT_MODE", "standard")
            with pytest.raises(HTTPException) as legacy:
                await server.create_checkout(body, req, member)
            assert legacy.value.status_code == 409
            monkeypatch.setenv("STRIPE_CHECKOUT_MODE", "typo")
            with pytest.raises(HTTPException) as invalid:
                await server.create_checkout(body, req, member)
            assert invalid.value.status_code == 503 and len(calls) == 8

            managed_member = {**member, "user_id": "managed-fixture"}
            await db.users.insert_one(managed_member.copy())
            monkeypatch.setenv("STRIPE_CHECKOUT_MODE", "managed")
            managed_calls = []
            def managed_checkout(**options):
                managed_calls.append(options)
                assert options["managed_payments"] == {"enabled": True}
                assert "automatic_tax" not in options and "billing_address_collection" not in options
                if len(managed_calls) == 1:
                    raise stripe.InvalidRequestError("Managed payments unavailable", None)
                return SimpleNamespace(id="cs_managed_fixture", url="https://checkout.example.invalid/managed")
            monkeypatch.setattr(stripe.checkout.Session, "create", managed_checkout)
            with pytest.raises(HTTPException) as ineligible:
                await server.create_checkout(body, req, managed_member)
            assert ineligible.value.status_code == 503 and len(managed_calls) == 1
            retry = await server.create_checkout(body, req, managed_member)
            assert retry["session_id"] == "cs_managed_fixture"
            assert managed_calls[0] == managed_calls[1] and managed_calls[0]["idempotency_key"].endswith(":managed")
        finally:
            await client.drop_database(db.name)
            client.close()
    asyncio.run(run())
