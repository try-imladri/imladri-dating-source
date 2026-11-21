import asyncio
import time

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend import accounts, membership_plans
from backend.free_plan import initialize_usage, paid_cycle, reply_usage, run_metered
from backend.tests.test_free_plan_quota import Question, run_case


def active_member(uid="member", **extra):
    now = int(time.time())
    return {"user_id": uid, "tier": "paid", "stripe_subscription_id": "sub_fixture",
            "subscription_period_start": now-200, "subscription_period_end": now+86400, **extra}


def test_public_catalog_and_legacy_or_discounted_plans():
    assert [(p["id"], p["messages"], p["price_cents"]) for p in membership_plans.public_plans()] == [
        ("plus_100", 100, 1200), ("plus_250", 250, 2000), ("plus_500", 500, 4000)]
    assert reply_usage(active_member())["plan_id"] == "plus_100"
    assert reply_usage(active_member(plan_offer="plus_500"))["message_limit"] == 500
    for offer in ("partner_monthly", "couple_annual"):
        assert reply_usage(active_member(plan_offer=offer, membership_plan_id="plus_500"))["message_limit"] == 100


def test_monthly_first_topup_concurrency_retry_refund_and_upgrade():
    async def run(db):
        user = active_member(membership_plan_id="plus_250")
        cycle = paid_cycle(user)
        user["paid_reply_cycles"] = {cycle: {"claimed": 249, "used": 249}}
        await db.users.insert_one(dict(user))

        async def row():
            return await db.users.find_one({"user_id": user["user_id"]})

        async def grant(session, net):
            return await membership_plans.apply_topup(db, user["user_id"], session, net)

        async def saved(identifier):
            doc = {"id": identifier, "user_id": user["user_id"], "response": "saved"}
            await db.exchanges.insert_one(dict(doc))
            return doc

        async def identity(doc):
            return doc

        async def send(key, generate=saved):
            req = Request({"type": "http", "headers": [(b"idempotency-key", key.encode())]})
            return await run_metered(db, await row(), req, Question(question="fixture"), "exchange",
                                     "profile", generate, identity)

        assert all(await asyncio.gather(*(grant("purchase", 100) for _ in range(4))))
        assert reply_usage(await row())["messages_remaining"] == 101
        first = await send("one")
        assert (await send("one"))["id"] == first["id"]
        assert reply_usage(await row())["topup_remaining"] == 100
        await asyncio.gather(grant("purchase", 100), grant("purchase", 1), grant("purchase", 12))
        final = await asyncio.gather(send("last-1"), send("last-2"), return_exceptions=True)
        assert sorted(x.status_code if isinstance(x, HTTPException) else 200 for x in final) == [200, 402]
        usage = reply_usage(await row())
        assert (usage["messages_used"], usage["monthly_messages_used"], usage["messages_remaining"]) == (251, 250, 0)
        # A full refund after the final credit was spent must survive future checkout retries.
        await asyncio.gather(grant("purchase", 0), grant("purchase", 100))
        await grant("new-purchase", 100)
        assert reply_usage(await row())["topup_remaining"] == 99

        async def failed(_):
            raise HTTPException(502, "Model unavailable")
        with pytest.raises(HTTPException, match="Model unavailable"):
            await send("failed", failed)
        assert reply_usage(await row())["topup_remaining"] == 99
        # An upgrade in the current period adds only the difference in monthly capacity.
        await db.users.update_one({"user_id": "member"}, {"$set": {"membership_plan_id": "plus_500"}})
        assert reply_usage(await row())["messages_remaining"] == 349
        await send("after-upgrade")
        assert reply_usage(await row())["topup_remaining"] == 99
        await db.users.update_one({"user_id": "member"}, {"$inc": {"subscription_period_start": 50}})
        assert reply_usage(await row())["messages_remaining"] == 599
        await db.users.update_one({"user_id": "member"}, {"$set": {"subscription_status": "canceled"}})
        usage = reply_usage(await row())
        assert usage["plan_id"] is None and usage["topup_remaining"] == 99 and usage["messages_remaining"] == 0
        with pytest.raises(HTTPException) as stopped:
            await send("inactive")
        assert stopped.value.status_code == 402 and stopped.value.headers["X-Imladri-Limit"] == "subscription"
    run_case(run)


def test_expired_topup_reservations_settle_in_the_original_period():
    async def run(db):
        member = active_member()
        old = "previous-cycle"
        receipts = {key: {"kind": "exchange", "result": key, "pending": True, "expires": "2000-01-01",
                          "cycle": old, "period_end": int(time.time()), "pool": "topup"}
                    for key in ("saved", "interrupted")}
        member.update(topup_balance=3, free_reply_requests=receipts)
        await db.users.insert_one(dict(member))
        await db.exchanges.insert_one({"id": "saved", "user_id": member["user_id"]})
        result = await initialize_usage(db, member)
        assert result["topup_balance"] == 4 and "interrupted" not in result["free_reply_requests"]
        assert result["paid_reply_cycles"][old] == {"used": 1, "topup_used": 1}
        assert reply_usage(result)["messages_remaining"] == 104
        assert await initialize_usage(db, result) == result
    run_case(run)


def test_account_closure_expires_open_refills_and_cancels_only_the_owned_membership(monkeypatch):
    plan_key = membership_plans.PLANS["plus_500"]["lookup_key"]
    refill_meta = {"user_id": "member", "lookup_key": membership_plans.TOPUP["lookup_key"]}
    plan_meta = {"user_id": "member", "lookup_key": plan_key}
    sessions = {
        "cs_paid": {"metadata": refill_meta, "mode": "payment", "status": "complete"},
        "cs_open": {"metadata": refill_meta, "mode": "payment", "status": "open"},
        "cs_foreign": {"metadata": {**refill_meta, "user_id": "other"}, "mode": "payment", "status": "open"},
    }
    actions = []
    monkeypatch.setattr(accounts.stripe, "api_key", "fixture")
    monkeypatch.setattr(accounts.stripe.checkout.Session, "retrieve", sessions.__getitem__)
    monkeypatch.setattr(accounts.stripe.checkout.Session, "expire", lambda sid: actions.append(sid) or {"status": "expired"})
    monkeypatch.setattr(accounts.stripe.Subscription, "retrieve", lambda sub: {
        "status": "active", "metadata": plan_meta, "items": {"data": [{"price": {"lookup_key": plan_key}}]}})
    monkeypatch.setattr(accounts.stripe.Subscription, "cancel", lambda sub, **_: actions.append(sub) or {"status": "canceled"})
    accounts.close_billing({"sub_owned"}, set(sessions), "member")
    assert sorted(actions) == ["cs_open", "sub_owned"]
