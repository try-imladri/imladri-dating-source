"""Exercise purchase, plan change, refill and refund fulfillment using isolated Mongo and Stripe doubles."""
import asyncio
import time
from types import SimpleNamespace

import pytest
import stripe
from fastapi import HTTPException
from starlette.requests import Request

from backend import membership_plans, stripe_membership, stripe_refills
from backend.tests.test_free_plan_quota import run_case
from backend.tests.billing_provider_fixture import install


@pytest.fixture(autouse=True)
def verified_billing(monkeypatch):
    return install(monkeypatch, globals(), "bill")


def item(key):
    spec = stripe_membership.PRODUCTS[key]
    return stripe.Price.construct_from({"id": "price_" + key, "object": "price", "active": True, "metadata": {"upgrade_portal": "bpc_" + key},
        "product": "prod_" + key, "type": "one_time" if key == "topup_100" else "recurring", "billing_scheme": "per_unit",
        "unit_amount": spec["price_cents"], "currency": "usd", "lookup_key": spec["lookup_key"], "livemode": False,
        "recurring": None if key == "topup_100" else {"interval": "month", "interval_count": 1, "usage_type": "licensed"}}, None)


def bill(key, when, reason, invoice_id):
    price = item(key)
    return {"id": invoice_id, "object": "invoice", "status": "paid", "currency": "usd", "billing_reason": reason,
        "total": price.unit_amount, "amount_paid": price.unit_amount, "total_excluding_tax": price.unit_amount,
        "parent": {"subscription_details": {"subscription": "sub_one"}},
        "lines": {"data": [{"type": "subscription", "amount": price.unit_amount, "price": price.id,
                            "period": {"start": when, "end": when + 2592000}}]}}


def test_paid_plan_transitions_and_refill_receipts(monkeypatch, verified_billing):
    from backend import server
    plans = {key: item(key) for key in stripe_membership.PRODUCTS}
    monkeypatch.setattr(stripe, "api_key", "sk_test_fixture")
    monkeypatch.setenv("PERSONAL_BILLING_MODE", "enabled")
    monkeypatch.setenv("STRIPE_CHECKOUT_MODE", "managed")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://test.invalid")
    monkeypatch.setattr(stripe_membership, "catalog", lambda: plans)
    monkeypatch.setattr(server.stripe_plan, "catalog_price", lambda: plans["plus_100"])
    monkeypatch.setattr(server.partner_offers, "price_for", lambda key: plans["plus_100" if key == "standard" else key])
    sessions = {}
    def create(**opts):
        assert opts["line_items"][0]["quantity"] == 1 and opts["managed_payments"]["enabled"]
        key = opts["idempotency_key"]
        if key not in sessions:
            sid = opts["mode"] + str(len(sessions))
            sessions[key] = stripe.checkout.Session.construct_from({"id": sid, "object": "checkout.session", "url": "https://checkout.test/" + sid,
                "status": "open", **{field: opts[field] for field in ("mode", "metadata")}}, None)
        return sessions[key]
    monkeypatch.setattr(stripe.checkout.Session, "create", create)
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: next(s for s in sessions.values() if s.id == sid))
    monkeypatch.setattr(stripe.checkout.Session, "list_line_items", lambda sid, limit: SimpleNamespace(data=[{"price": plans["topup_100"], "quantity": 1}]))
    start = int(time.time()) - 400
    req = Request({"type": "http", "scheme": "https", "server": ("test.invalid", 443), "path": "/", "headers": [(b"idempotency-key", b"refill-a")]})

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        member = {"user_id": "member", "env": server.ENV, "role": "member", "tier": "unsubscribed"}
        await db.users.insert_one(dict(member))
        async def reload(): return await db.users.find_one({"user_id": "member"})
        with pytest.raises(HTTPException) as e:
            await server.purchase_refill(server.RefillIn(purchase_policy_accepted=True, purchase_policy_version="imladri-purchases-2026-11-v1", origin_url="https://test.invalid"), req, member)
        assert e.value.status_code == 402 and not sessions
        choice = server.CheckoutIn(purchase_policy_accepted=True, purchase_policy_version="imladri-purchases-2026-11-v1", offer="plus_250", origin_url="https://test.invalid")
        result = await server.create_checkout(choice, req, member)
        assert len(sessions) == 1 and result["session_id"] == "subscription0"
        purchase = next(iter(sessions.values()))
        assert purchase.metadata.lookup_key == membership_plans.PLANS["plus_250"]["lookup_key"]
        purchase.update({"subscription": "sub_one", "payment_status": "paid", "customer": "cus_one",
                         "invoice": bill("plus_250", start, "subscription_create", "in_open")})
        await server.handle_stripe_event("checkout.session.completed", purchase)
        paid = await reload()
        assert paid["membership_plan_id"] == "plus_250" and server.reply_usage(paid)["message_limit"] == 250
        invoice = bill("plus_500", start + 100, "subscription_update", "in_upgrade")
        await server.renew_subscription(invoice)
        assert (await reload())["membership_plan_id"] == "plus_250"
        monkeypatch.setattr(stripe_membership, "upgrade_session", lambda member, target, origin: (SimpleNamespace(url="https://stripe.test/review"), plans[target].id))
        assert (await server.upgrade_subscription(server.UpgradeIn(purchase_policy_accepted=True, purchase_policy_version="imladri-purchases-2026-11-v1", offer="plus_500", origin_url="https://test.invalid"), req, paid))["checkout_url"].endswith("review")
        assert (await reload())["membership_plan_id"] == "plus_250"
        prorated = bill("plus_500", start + 100, "subscription_update", "in_proration")
        prorated["lines"]["data"][0]["proration"] = True
        await server.renew_subscription(prorated)
        assert (await reload())["membership_plan_id"] == "plus_250"
        invoice["lines"]["data"].append({"price": plans["plus_250"].id, "type": "subscription", "amount": -1750, "proration": True})
        invoice.update(total_excluding_tax=2250, total=2300, amount_paid=2300)
        await asyncio.gather(*(server.renew_subscription(invoice) for _ in range(2)))
        paid = await reload()
        assert paid["membership_plan_id"] == "plus_500" and paid["subscription_period_start"] == start + 100
        assert (await db.payment_transactions.find_one({"kind": "upgrade"}))["subscription_revenue_cents"] == 2250
        await server.renew_subscription(bill("plus_250", start, "subscription_cycle", "in_late"))
        assert (await reload())["membership_plan_id"] == "plus_500"
        assert await db.payment_transactions.count_documents({"kind": "upgrade"}) == 1

        body = server.RefillIn(purchase_policy_accepted=True, purchase_policy_version="imladri-purchases-2026-11-v1", origin_url="https://test.invalid")
        first, second = await asyncio.gather(server.purchase_refill(body, req, paid), server.purchase_refill(body, req, paid))
        assert first == second and len(sessions) == 2
        refill = list(sessions.values())[-1]
        refill.update({"amount_subtotal": 1200, "amount_total": 1320, "currency": "usd", "status": "complete",
                       "payment_status": "paid", "payment_intent": "pi_pack", "customer": "cus_one"})
        charge = {"id": "ch1", "object": "charge", "payment_intent": "pi_pack", "currency": "usd", "amount": 1320,
                  "amount_refunded": 0, "paid": True, "captured": True, "amount_captured": 1320, "metadata": refill.metadata}
        verified_billing["charges"]["ch1"] = charge
        monkeypatch.setattr(stripe.PaymentIntent, "retrieve", lambda *args, **kw: {"id": "pi_pack", "amount_received": 1320, "latest_charge": charge})
        await asyncio.gather(*(server.handle_stripe_event("checkout.session.completed", refill) for _ in range(3)))
        assert (await reload())["topup_balance"] == 100 and (await reload())["membership_plan_id"] == "plus_500"
        assert (await server.purchase_refill(body, req, await reload()))["checkout_url"].endswith(refill.id)
        assert (await server.payment_status(refill.id, await reload()))["kind"] == "topup"
        charge["amount_refunded"] = 660
        reversed_at = int(time.time())
        await asyncio.gather(server.handle_stripe_event("charge.refunded", charge,event_created=reversed_at),
                             server.handle_stripe_event("charge.refunded", charge,event_created=reversed_at+60),
                             server.handle_stripe_event("checkout.session.completed", refill))
        assert await db.events.count_documents({"type":"refill_refunded"}) == 1
        assert (await reload())["topup_balance"] == 50
        verified_billing["disputes"]["ch1"] = [{"id": "dp_pack", "charge": "ch1", "currency": "usd", "amount": 1320, "status": "lost"}]
        await server.handle_stripe_event("charge.dispute.closed", {"id": "dp_pack", "charge": "ch1", "status": "lost", "payment_intent": "pi_pack"},event_created=reversed_at)
        assert await db.events.count_documents({"type":"refill_dispute_lost"}) == 1
        from backend import trends
        from datetime import datetime, timezone
        day = datetime.fromtimestamp(reversed_at,timezone.utc).date()
        activity = await trends.report(db,{"env":server.ENV,"role":"member"},server.default_quiz(),7,day)
        assert activity["totals"]["refill_refund_events"] == activity["totals"]["refill_lost_disputes"] == 1
        assert next(row for row in activity["daily"] if row["day"]==day.isoformat())["refill_refund_events"] == 1
        # A duplicate notification arriving later can establish the original event's earlier date.
        first_stamp = reversed_at - 86400
        await server.handle_stripe_event("charge.refunded", charge, event_created=first_stamp)
        from io import StringIO
        import csv
        refund_day = datetime.fromtimestamp(first_stamp, timezone.utc).date().isoformat()
        raw = await server.download_workspace_report(days=7, member={"role":"admin"})
        rows = [row for row in csv.DictReader(StringIO(raw.body.decode())) if row["section"]=="Activity"]
        for metric, expected_day in (("refill_refund_events",refund_day),("refill_lost_disputes",day.isoformat())):
            period = [row for row in rows if row["metric"]==metric and row["scope"]=="period"]
            dates = [row for row in rows if row["metric"]==metric and row["scope"]=="UTC day" and row["value"]!="0"]
            assert len(period)==len(dates)==1 and period[0]["value"]==dates[0]["value"]=="1"
            assert dates[0]["through_utc"]==expected_day
        await server.handle_stripe_event("checkout.session.completed", refill)
        assert (await reload())["topup_balance"] == 0

    run_case(scenario)


def test_billing_actions_reject_a_different_signed_in_member_before_contacting_stripe(monkeypatch):
    import httpx
    from backend import server

    async def check():
        member = {"user_id":"current-account"}
        monkeypatch.setitem(server.app.dependency_overrides, server.current_member, lambda: member)
        actions = {
            "checkout":{"origin_url":"https://test.invalid", "offer":"plus_250"},
            "upgrade":{"origin_url":"https://test.invalid", "offer":"plus_500"},
            "topup":{"origin_url":"https://test.invalid"},
            "portal":{"origin_url":"https://test.invalid"},
            "partner-annual":{"accept":True},
            "cancel-partner-year":{"accept":True},
        }
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url="http://localhost") as client:
            for action, payload in actions.items():
                resp = await client.post("/api/payments/"+action,json={**payload,"expected_member":"earlier-account"})
                assert resp.status_code == 409 and "signed-in account changed" in resp.json()["detail"]
    asyncio.run(check())
