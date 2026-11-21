"""Only an open $10 checkout may be expired when a pair ends."""

import asyncio
import os
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import stripe
from motor.motor_asyncio import AsyncIOMotorClient

from backend import couples, partner_checkout as checkout, stripe_partner_prices


def session(sid, state):
    return {"id": sid, "status": state, "mode": "subscription", "metadata": {
        "user_id": "partner", "lookup_key": stripe_partner_prices.LOOKUP_KEYS["partner_monthly"],
    }}


def test_expire_race_honors_completed_session(monkeypatch):
    sid = "cs_test_race"
    observed = iter([session(sid, "open"), session(sid, "complete")])
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda _: next(observed))

    def paid_concurrently(_):
        raise stripe.InvalidRequestError("The session is not open", "id")

    monkeypatch.setattr(stripe.checkout.Session, "expire", paid_concurrently)
    assert checkout.expire_at_stripe({"session_id": sid, "user_id": "partner"}) == "complete"


def test_foreign_checkout_is_never_expired(monkeypatch):
    sid = "cs_test_foreign"
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda _: session(sid, "open"))
    monkeypatch.setattr(stripe.checkout.Session, "expire", lambda _: pytest.fail("Foreign checkout"))
    assert checkout.expire_at_stripe({"session_id": sid, "user_id": "other"}) is None


def test_cleanup_retries_provider_failure_and_scopes_to_pair_and_mode(monkeypatch):
    uri = os.environ.get("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Set IMLADRI_TEST_MONGO_URL for the loopback MongoDB fixture.")
    assert urlsplit(uri).hostname in ("localhost", "127.0.0.1", "::1")
    monkeypatch.setattr(stripe, "api_key", "sk_test_fixture")
    states = {"open": "open", "waiting": "open", "complete": "complete", "standard": "open", "foreign": "open"}
    failures = {"waiting"}

    def current(sid):
        if sid in failures:
            raise stripe.APIConnectionError("Fixture connection unavailable")
        return session(sid, states[sid])

    def expire(sid):
        states[sid] = "expired"
        return session(sid, "expired")

    monkeypatch.setattr(stripe.checkout.Session, "retrieve", current)
    monkeypatch.setattr(stripe.checkout.Session, "expire", expire)

    async def run():
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        db = client["partner_checkout_fixture_" + uuid4().hex]
        try:
            await db.users.insert_one({"user_id": "partner", "env": "preview", "checkout_attempt": {"id": "open"}})
            await db.couples_pairs.insert_one({"_id": "pair", "env": "preview", "inviter": "sponsor", "invitee": "partner",
                                               "state": "active", "benefit_members": ["partner", "sponsor"]})
            for sid in states:
                await db.payment_transactions.insert_one({"_id": sid, "session_id": sid, "checkout_attempt_id": sid,
                    "user_id": "partner", "env": "other" if sid == "foreign" else "preview", "pair_id": "pair",
                    "kind": "purchase", "plan_offer": "standard" if sid == "standard" else "partner_monthly",
                    "payment_status": "pending"})
            await couples.clear_couples(db, "sponsor")
            assert not await db.couples_pairs.find_one({"_id": "pair"})
            assert states == {"open": "expired", "waiting": "open", "complete": "complete", "standard": "open", "foreign": "open"}
            assert (await db.payment_transactions.find_one({"_id": "open"}))["payment_status"] == "expired"
            assert "checkout_attempt" not in await db.users.find_one({"user_id": "partner"})
            no_member = AsyncMock(return_value=None)
            no_pair = AsyncMock(return_value=None)
            assert not await checkout.reconcile(db, "preview", no_member, no_pair)
            failures.clear()
            assert await checkout.reconcile(db, "preview", no_member, no_pair)
            assert states["waiting"] == "expired" and states["complete"] == "complete"
            assert (await db.payment_transactions.find_one({"_id": "complete"}))["payment_status"] == "pending"
            assert all(states[key] == "open" for key in ("standard", "foreign"))
        finally:
            await client.drop_database(db.name)
            client.close()

    asyncio.run(run())
