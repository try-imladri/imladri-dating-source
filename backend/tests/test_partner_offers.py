"""Paid invite-only partner benefits, independent fixed terms, and monthly usage on annual invoices."""
import asyncio
import os
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from backend.tests.billing_provider_fixture import install


@pytest.fixture(autouse=True)
def verified_billing(monkeypatch):
    return install(monkeypatch, globals(), "invoice")
import stripe
import httpx
from fastapi import FastAPI, Header, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from starlette.requests import Request

from backend import couples_analytics, free_plan, partner_offers


def invoice(sub, price, start, end, amount, reason, paid_at, *, suffix=""):
    return {"id": "in_" + sub + reason + suffix, "status": "paid", "currency": "usd", "amount_paid": amount, "total": amount,
            "billing_reason": reason, "status_transitions": {"paid_at": paid_at},
            "parent": {"subscription_details": {"subscription": sub, "metadata": {"user_id": "guest",
                        "lookup_key": partner_offers.lookup("partner_monthly")}}},
            "lines": {"data": [{"amount": amount, "quantity": 1, "parent": {"subscription_item_details": {"proration": False}},
                "pricing": {"price_details": {"price": price}}, "period": {"start": start, "end": end}}]}}


def test_proof_and_monthly_anniversary(monkeypatch):
    moment = lambda date: int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp())
    anchor = moment("2028-01-31T11:12:13")
    receipt = invoice("sub_1", "price", anchor, free_plan.month_boundary(anchor, 12), 1000, "subscription_create", anchor)
    receipt.update(total_excluding_tax=923)
    assert partner_offers.fully_paid_offer(receipt, "price", 1000)
    assert not partner_offers.fully_paid_offer({**receipt, "amount_paid": 900}, "price", 1000)
    assert not partner_offers.fully_paid_offer({**receipt, "total_discount_amounts": [{"amount": 100}]}, "price", 1000)
    assert not partner_offers.fully_paid_offer(receipt, "another_price", 1000)
    assert not partner_offers.fully_paid_offer(receipt, "price", 8000)
    annual = {"tier": "paid", "stripe_subscription_id": "sub_1", "plan_offer": "couple_annual",
              "subscription_period_start": anchor, "subscription_period_end": free_plan.month_boundary(anchor, 12)}
    feb29, march31 = (moment(d) for d in ("2028-02-29T11:12:13", "2028-03-31T11:12:13"))
    monkeypatch.setattr(free_plan.time, "time", lambda: feb29 - 1)
    first = free_plan.paid_cycle(annual)
    annual["paid_reply_cycles"] = {first: {"claimed": 100, "used": 100}}
    assert free_plan.reply_usage(annual)["messages_remaining"] == 0
    monkeypatch.setattr(free_plan.time, "time", lambda: feb29)
    usage = free_plan.reply_usage(annual)
    assert (usage["period_start"], usage["period_end"], usage["messages_remaining"]) == (feb29, march31, 100)
    assert free_plan.paid_cycle(annual) != first
    monkeypatch.setattr(free_plan.time, "time", lambda: annual["subscription_period_end"])
    assert not free_plan.paid_cycle(annual)


def test_original_qualifying_invoice_recovered_after_later_renewal(monkeypatch):
    initial = {"id": "first", "billing_reason": "subscription_create", "status": "paid"}
    renewal = {"id": "later", "billing_reason": "subscription_cycle", "status": "paid"}
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda *args, **kw: {"latest_invoice": renewal})
    monkeypatch.setattr(stripe.Invoice, "list", lambda **kw: SimpleNamespace(auto_paging_iter=lambda: iter((renewal, initial))))
    assert partner_offers.first_paid_invoice("sub_fixture") == initial


def test_sponsored_seat_once_across_roles_and_after_personal_content_clear(monkeypatch):
    uri = os.getenv("IMLADRI_BILLING_TEST_MONGO")
    if not uri:
        pytest.skip("Set IMLADRI_BILLING_TEST_MONGO to a disposable loopback MongoDB.")
    assert urlsplit(uri).hostname in ("localhost", "127.0.0.1", "::1")
    from backend import couples
    async def run():
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
        db = client["imladri_sponsored_seat_" + uuid.uuid4().hex]
        try:
            for name, models in couples.COUPLES_INDEXES.items():
                await db[name].create_indexes(models)
            now = int(time.time())
            paid = {"tier": "paid", "stripe_subscription_id": "sub", "subscription_period_start": now - 20,
                    "subscription_period_end": now + 2000}
            for uid in ("host", "guest", "other"):
                await db.users.insert_one({"user_id": uid, "env": "preview", "couples_adult_at": couples.now(),
                    **(paid if uid == "host" else {})})
            async def member(identity: str = Header()):
                return await db.users.find_one({"user_id": identity})
            app = FastAPI()
            app.include_router(couples.couples_router(lambda: db, member))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://fixture.invalid") as http:
                async def post(uid, path, body=None):
                    return await http.post("/couples/" + path, json=body or {}, headers={"identity": uid})
                async def prepare(inviter, invitee, *, previous=None):
                    issued = previous or (await post(inviter, "invite")).json()
                    assert (await post(invitee, "invite/accept", {"token": issued["token"]})).status_code == 200
                    return {"pair_id": issued["workspace"]["pair_id"]}
                first = await prepare("host", "guest")
                assert (await post("host", "confirm", first)).status_code == 200
                assert (await post("guest", "leave", first)).status_code == 200
                # Once the guest becomes a paying subscriber, changing roles must not mint another sponsored seat.
                await db.users.update_one({"user_id": "guest"}, {"$set": paid})
                issued = (await post("guest", "invite")).json()
                token = {"token": issued["token"]}
                assert not (await post("other", "invite/preview", token)).json()["can_join"]
                assert (await post("other", "invite/accept", token)).status_code == 409
                await couples.clear_couples(db, "host")
                assert (await db.users.find_one({"user_id": "guest"}))["used_sponsored_seat"]
                assert (await post("other", "invite/accept", token)).status_code == 409
                assert "accepted_at" not in await db.couples_pairs.find_one({"_id": issued["workspace"]["pair_id"]})
                await db.users.update_one({"user_id": "other"}, {"$set": paid})
                second = await prepare("guest", "other", previous=issued)
                await db.users.update_one({"user_id": "other"}, {"$set": {"tier": "unsubscribed"}})
                assert (await post("guest", "confirm", second)).status_code == 409
                await db.users.update_one({"user_id": "other"}, {"$set": paid})
                assert (await post("guest", "confirm", second)).status_code == 200
                assert (await db.couples_pairs.find_one({"_id": second["pair_id"]}))["self_funded"]
                await db.users.update_one({"user_id": "other"}, {"$set": {"tier": "unsubscribed"}})
                with pytest.raises(HTTPException, match="Both members"):
                    await couples.active_pair(db, await member("guest"))
        finally:
            await client.drop_database(db.name)
            client.close()
    asyncio.run(run())


def test_invited_partner_checkout_webhooks_and_annual_agreements(monkeypatch):
    uri = os.getenv("IMLADRI_BILLING_TEST_MONGO")
    if not uri:
        pytest.skip("Set IMLADRI_BILLING_TEST_MONGO to a disposable loopback MongoDB.")
    assert urlsplit(uri).hostname in ("localhost", "127.0.0.1", "::1")
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", "imladri_unused")
    monkeypatch.setenv("PERSONAL_BILLING_MODE", "enabled")
    monkeypatch.setenv("STRIPE_CHECKOUT_MODE", "managed")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://fixture.invalid")
    from backend import server

    monkeypatch.setattr(stripe, "api_key", "sk_test_fixture")
    prices = {k: SimpleNamespace(id="price_" + k, unit_amount=v, currency="usd")
              for k, v in (("standard", 1200), ("partner_monthly", 1000), ("couple_annual", 8000))}
    monkeypatch.setattr(partner_offers, "price_for", prices.__getitem__)
    monkeypatch.setattr(server.stripe_membership, "catalog", lambda: {})
    moment = int(time.time())
    paid_end = {"host": moment + 10 * 86400, "guest": moment + 30 * 86400}
    monthly = {"host": "price_standard", "guest": "price_partner_monthly"}
    subs = {"sub_" + uid: {"id": "sub_" + uid, "customer": "cus_" + uid, "status": "active", "metadata": {"user_id": uid},
            "collection_method": "charge_automatically", "items": {"data": [{"quantity": 1, "price": monthly[uid],
            "current_period_end": paid_end[uid]}]}} for uid in paid_end}
    session = stripe.checkout.Session.construct_from({"id": "cs_partner", "object": "checkout.session", "url": "https://fixture.invalid/pay",
        "status": "complete", "subscription": "sub_guest", "customer": "cus_guest", "payment_status": "paid"}, None)
    checkouts = set()
    def checkout(**kwargs):
        assert kwargs["managed_payments"] == {"enabled": True}
        assert kwargs["line_items"] == [{"price": "price_partner_monthly", "quantity": 1}]
        checkouts.add(kwargs["idempotency_key"])
        return session
    monkeypatch.setattr(stripe.checkout.Session, "create", checkout)
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: session)
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid, **kw: deepcopy(subs[sid]))
    schedules, changes, lock = {}, set(), Lock()
    def new_schedule(*, from_subscription, idempotency_key):
        with lock:
            sid = "sched_" + from_subscription
            if sid not in schedules:
                schedules[sid] = {"id": sid, "status": "active", "subscription": from_subscription, "end_behavior": "release",
                    "phases": [{"start_date": moment - 1000, "items": subs[from_subscription]["items"]["data"]}]}
                subs[from_subscription]["schedule"] = sid
            return deepcopy(schedules[sid])
    def modify(sid, **kw):
        assert kw["end_behavior"] == "cancel" and kw["proration_behavior"] == "none"
        if len(kw["phases"]) == 2:
            assert kw["phases"][1]["billing_cycle_anchor"] == "phase_start"
        else:
            assert kw["phases"][0]["items"] == [{"price": monthly["host"], "quantity": 1}]
        with lock:
            changes.add(kw["idempotency_key"])
            schedules[sid].update(end_behavior="cancel", phases=kw["phases"])
            return deepcopy(schedules[sid])
    monkeypatch.setattr(stripe.SubscriptionSchedule, "create", new_schedule)
    monkeypatch.setattr(stripe.SubscriptionSchedule, "modify", modify)
    monkeypatch.setattr(stripe.SubscriptionSchedule, "retrieve", lambda id: deepcopy(schedules[id]))

    async def run():
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
        db = client["imladri_partner_test_" + uuid.uuid4().hex]
        monkeypatch.setattr(server, "db", db)
        owner = lambda uid: db.users.find_one({"user_id": uid})
        try:
            await server.ensure_indexes()
            base = {"env": server.ENV, "role": "member", "couples_adult_at": server.now()}
            await db.users.insert_many([{**base, "user_id": "host", "tier": "paid", "stripe_customer_id": "cus_host",
                "stripe_price_id": monthly["host"], "stripe_subscription_id": "sub_host", "subscription_status": "active",
                "subscription_period_start": moment - 1000, "subscription_period_end": paid_end["host"]},
                {**base, "user_id": "guest", "tier": "unsubscribed"}, {**base, "user_id": "outsider", "tier": "unsubscribed"}])
            pair = {"_id": "pair", "env": server.ENV, "state": "active", "members": ["host", "guest"], "benefit_members": ["host", "guest"],
                    "inviter": "host", "sponsor": "host", "invitee": "guest"}
            await db.couples_pairs.insert_one(pair.copy())
            eligible = lambda uid: server.upgrade_info(uid)
            host, guest, out = await asyncio.gather(*(owner(uid) for uid in ("host", "guest", "outsider")))
            assert (await eligible(guest))["partner_offer"]["monthly_available"]
            assert not (await eligible(host))["partner_offer"]["monthly_eligible"]
            req = Request({"type": "http", "scheme": "https", "server": ("fixture.invalid", 443), "path": "/", "headers": []})
            checkout_body = server.CheckoutIn(purchase_policy_accepted=True, purchase_policy_version="imladri-purchases-2026-11-v1", origin_url="https://fixture.invalid", offer="partner_monthly")
            with pytest.raises(HTTPException, match="confirmed sponsored couple"):
                await server.create_checkout(checkout_body, req, out)
            await asyncio.gather(*(server.create_checkout(checkout_body, req, guest) for _ in range(4)))
            assert len(checkouts) == 1
            before = await eligible(guest)
            assert before["partner_offer"]["annual_expires_at"] is None
            receipt = invoice("sub_guest", monthly["guest"], moment - 3, paid_end["guest"], 1000, "subscription_create", moment - 3)
            # The paid invoice arrives first; the Checkout session has no expanded invoice pointer.
            await server.handle_stripe_event("invoice.paid", receipt)
            await asyncio.gather(*(server.handle_stripe_event("invoice.paid", receipt) for _ in range(4)))
            await server.handle_stripe_event("checkout.session.completed", session)
            assert await db.payment_transactions.count_documents({"payment_status": "paid"}) == 1
            assert await db.notifications.count_documents({"key": "partner-year:pair"}) == 2
            host, guest = await asyncio.gather(owner("host"), owner("guest"))
            await server.notify("guest", "billing", "Account update", "Membership updated.", "/app/settings/upgrade", "ordinary-billing")
            host_notice, = await server.couples_inbox_items(host)
            guest_notice, = await server.couples_inbox_items(guest)
            assert host_notice["id"] != guest_notice["id"] and not guest_notice["read"]
            assert not await server.couples_inbox_items(out)
            await server.read_couples_notifications(host_notice["id"], guest)
            assert not (await server.couples_inbox_items(host))[0]["read"]
            await server.read_couples_notifications(None, guest)
            assert (await server.couples_inbox_items(guest))[0]["read"]
            assert not (await db.notifications.find_one({"key": "ordinary-billing"}))["read"]
            expiry = (await db.couples_pairs.find_one({"_id": "pair"}))["annual_expires_at"]
            assert expiry == partner_offers.utc_iso(moment - 3 + 86400)
            assert (await eligible(host))["partner_offer"]["annual_starts_at"] == partner_offers.utc_iso(paid_end["host"])
            assert (await eligible(guest))["price_label"] == "$10/month"
            assert free_plan.reply_usage(guest)["messages_remaining"] == 100
            membership = (await couples_analytics.report(db, server.ENV, {"env": server.ENV}, 30))["membership"]
            assert membership == {"sponsored_pairs": 1, "partner_upgrades": 1, "annual_offers": 1,
                                  "annual_authorizations": 0, "annual_payments": 0}
            with pytest.raises(HTTPException, match="charge date changed"):
                await partner_offers.accept(db, guest, expected_starts_at=partner_offers.utc_iso(moment))
            with pytest.raises(HTTPException, match="unavailable or has expired"):
                await partner_offers.accept(db, out)
            results = await asyncio.gather(*(partner_offers.accept(db, guest) for _ in range(4)))
            assert all(result["accepted"] and not result["renews"] for result in results)
            assert len(changes) == 1 and "schedule" not in subs["sub_host"]
            assert (await eligible(await owner("guest")))["partner_offer"]["annual_accepted"]
            assert not await server.couples_inbox_items(await owner("guest"))
            await db.couples_pairs.update_one({"_id": "pair"}, {"$set": {"annual_expires_at": partner_offers.utc_iso(moment - 1)}})
            assert not await server.couples_inbox_items(host)
            with pytest.raises(HTTPException, match="expired"):
                await partner_offers.accept(db, host)
            await db.couples_pairs.update_one({"_id": "pair"}, {"$set": {"annual_expires_at": expiry}})
            assert (await partner_offers.accept(db, host))["accepted"] and len(changes) == 2
            host = await owner("host")
            assert (await eligible(host))["partner_offer"]["annual_cancel_available"]
            assert (await partner_offers.cancel_before_charge(db, host))["cancelled"]
            assert (await partner_offers.cancel_before_charge(db, await owner("host")))["ends_at"] == partner_offers.utc_iso(paid_end["host"])
            assert len(changes) == 3 and free_plan.paid_cycle(await owner("host"))
            assert (await eligible(await owner("host")))["partner_offer"]["annual_cancelled"]
            start = paid_end["guest"]
            ends = free_plan.month_boundary(start, 12)
            annual = invoice("sub_guest", "price_couple_annual", start, ends, 8000, "subscription_update", start)
            for invalid in ({**annual, "amount_paid": 4000}, {**annual, "status": "open"}, {**annual, "total_discount_amounts": [{"amount": 100}]}):
                await server.handle_stripe_event("invoice.paid", invalid)
                assert (await owner("guest"))["plan_offer"] == "partner_monthly"
            await asyncio.gather(*(server.handle_stripe_event("invoice.paid", annual) for _ in range(5)))
            assert (await owner("guest"))["plan_offer"] == "couple_annual"
            assert await db.payment_transactions.count_documents({"kind": "renewal", "subscription_revenue_cents": 8000}) == 1
            after_invoice = datetime.fromtimestamp(start + 1, timezone.utc)
            membership = (await couples_analytics.report(db, server.ENV, {"env": server.ENV}, 90, after_invoice))["membership"]
            assert membership["annual_authorizations"] == 2 and membership["annual_payments"] == 1
            extra = invoice("sub_guest", "price_couple_annual", ends, free_plan.month_boundary(ends, 12), 8000, "subscription_cycle", ends)
            await server.handle_stripe_event("invoice.paid", extra)
            assert (await owner("guest"))["subscription_period_end"] == ends
            await server.handle_stripe_event("customer.subscription.deleted", subs["sub_guest"])
            assert not free_plan.paid_cycle(await owner("guest"))
        finally:
            await client.drop_database(db.name)
            client.close()
    asyncio.run(run())
