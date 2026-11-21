"""Complimentary member Couples access, sponsorship and genuine metering on PG."""
import asyncio
from datetime import datetime, timezone
import os
import time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
import httpx
import pytest

from backend import couples, couples_tone, free_plan, partner_offers
from backend.couples_journey import journey_router
from backend.message_costs import current_message
from backend.postgres_store import PostgresDatabase


def exercise(callback):
    dsn = os.environ.get("IMLADRI_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Requires an isolated loopback PostgreSQL fixture.")
    assert urlsplit(dsn).hostname in {"127.0.0.1", "localhost", "::1"}
    async def run():
        db = PostgresDatabase(dsn, "fixture_comp_couples_" + uuid4().hex, allow_local_plaintext=True)
        try:
            await callback(db)
        finally:
            def clear(cursor, now):
                for table in ("rpc_requests", "unique_keys", "indexes", "documents"):
                    cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (db.name,))
            await db._run(clear, write="__fixture_cleanup")
            await db.aclose()
    asyncio.run(run())


def grant():
    return {"version": "complimentary-unlimited-v1", "id": str(uuid4()), "active": True}


def member(uid, **fields):
    return {"user_id": uid, "role": "member", "tier": "free", "env": "preview", "nickname": uid,
            "couples_adult_at": "2026-01-01T00:00:00+00:00", "about": {"demographics": {"age": 30}}, **fields}


def paid():
    now = int(time.time())
    return {"tier": "paid", "stripe_subscription_id": "fixture_subscription",
            "subscription_period_start": now - 60, "subscription_period_end": now + 86400}


async def pair(db, owner=None, guest=None, **fields):
    owner = owner or member("comp-owner", complimentary_entitlement=grant())
    guest = guest or member("comp-guest")
    await db.users.insert_many([owner, guest])
    record = {"_id": "comp-pair", "members": [owner["user_id"], guest["user_id"]], "inviter": owner["user_id"],
              "invitee": guest["user_id"], "sponsor": owner["user_id"], "state": "active", "env": "preview", **fields}
    await db.couples_pairs.insert_one(record)
    return owner, guest, record


def test_comp_member_invites_confirms_sponsors_and_revocation_blocks_shared_writes():
    async def case(db):
        for collection, indexes in couples.COUPLES_INDEXES.items():
            await db[collection].create_indexes(indexes)
        await db.users.insert_many([member("comp-owner", complimentary_entitlement=grant()), member("comp-guest"),
                                    member("normal"), member("staff", role="admin", complimentary_entitlement=grant())])
        async def current(x_member: str = Header()):
            return await db.users.find_one({"user_id": x_member})
        app = FastAPI(); app.include_router(couples.couples_router(lambda: db, current))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            async def call(uid, method, path, body=None, status=200):
                response = await client.request(method, "/couples" + path, headers={"x-member": uid}, json=body)
                assert response.status_code == status, response.text
                return response.json()
            await call("normal", "POST", "/invite", {}, 402)
            await call("staff", "POST", "/invite", {}, 402)
            issued = await call("comp-owner", "POST", "/invite", {})
            assert issued["workspace"]["can_sponsor"] and issued["workspace"]["usage"]["unlimited"]
            preview = await call("comp-guest", "POST", "/invite/preview", {"token": issued["token"]})
            assert preview["sponsored"] and preview["can_join"]
            accepted = await call("comp-guest", "POST", "/invite/accept", {"token": issued["token"]})
            target = {"pair_id": accepted["pair_id"]}
            assert (await call("comp-owner", "POST", "/confirm", target))["writable"]
            assert (await call("comp-guest", "GET", ""))["writable"]
            await call("comp-guest", "POST", "/sponsor", target, 402)
            assert (await call("comp-owner", "POST", "/sponsor", target))["sponsor_me"]
            projected = await couples.member_info(db, "comp-owner", "preview")
            assert projected["role"] == "member" and free_plan.complimentary_access(projected)
            await db.users.update_one({"user_id": "comp-owner"}, {"$set": {"complimentary_entitlement.active": False}})
            assert not (await call("comp-guest", "GET", ""))["writable"]
            with pytest.raises(HTTPException) as denied:
                await couples.active_pair(db, await current("comp-guest"), write=True)
            assert denied.value.status_code == 402
            owner = await db.users.find_one({"user_id": "comp-owner"})
            assert owner["role"] == "member" and owner["tier"] == "free" and not owner.get("stripe_subscription_id")
    exercise(case)


def test_self_funded_pair_still_requires_both_entitlements_and_adult_block_guards():
    async def case(db):
        owner, guest, _ = await pair(db, guest=member("comp-guest", **paid()), self_funded=True)
        assert (await couples.active_pair(db, guest, write=True))["_id"] == "comp-pair"
        await db.users.update_one({"user_id": owner["user_id"]}, {"$set": {"complimentary_entitlement.active": False}})
        with pytest.raises(HTTPException) as denied:
            await couples.active_pair(db, guest, write=True)
        assert denied.value.status_code == 402
        await db.users.update_one({"user_id": owner["user_id"]}, {"$set": {"complimentary_entitlement.active": True, "about.demographics.age": 17}})
        with pytest.raises(HTTPException) as minor:
            await couples.active_pair(db, guest, write=True)
        assert minor.value.status_code == 403
    exercise(case)


def test_comp_member_never_gets_new_paid_offers_even_with_existing_subscription(monkeypatch):
    async def case(db):
        owner = member("comp-owner", **paid())
        guest = member("comp-guest", complimentary_entitlement=grant(), **paid())
        owner, guest, record = await pair(db, owner, guest, benefit_members=["comp-owner", "comp-guest"],
                                        annual_expires_at="2099-01-01T00:00:00+00:00")
        calls = []
        monkeypatch.setattr(partner_offers.stripe.Subscription, "retrieve", lambda *a, **k: calls.append("unexpected Stripe"))
        _, offer = await partner_offers.details(db, guest)
        assert not offer["monthly_eligible"] and not offer["annual_eligible"]
        assert await partner_offers.inbox_notice(db, guest) is None
        with pytest.raises(HTTPException) as denied:
            await partner_offers.accept(db, guest)
        assert denied.value.status_code == 409 and calls == []
        # Revoking the gift restores the existing paid-plan offer rules.
        guest["complimentary_entitlement"]["active"] = False
        assert (await partner_offers.details(db, guest))[1]["annual_eligible"]
    exercise(case)


def test_qualifying_invoice_notice_skips_comp_recipient_only():
    async def case(db):
        owner, guest, _ = await pair(db, benefit_members=["comp-owner", "comp-guest"])
        notifications = []
        async def notify(uid, *args): notifications.append(uid)
        await partner_offers.unlock(db, {"pair_id": "comp-pair", "env": "preview", "user_id": guest["user_id"]},
                                    "genuine_fixture_subscription", datetime.now(timezone.utc).isoformat(), notify)
        assert notifications == [guest["user_id"]]
    exercise(case)


def test_free_guest_uses_comp_sponsor_meter_once_and_sponsor_pause_blocks_new_generation():
    async def case(db):
        owner, guest, _ = await pair(db)
        calls = []
        async def current(): return await db.users.find_one({"user_id": guest["user_id"]})
        async def generate(feature, system, prompt):
            calls.append(current_message.get())
            return {"status": "estimated", "incoming": "neutral", "confidence": "limited", "evidence": [0],
                    "explanation": "The question invites a joint decision.",
                    "drafts": {style: style + " Could we choose a grocery day together?" for style in ("warm", "neutral", "direct", "playful", "cool")}}
        app = FastAPI(); app.include_router(journey_router(lambda: db, current, generate), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            draft = "/api/couples/drafts/support-1"; tone = "/api/couples/tone/private/support-1"
            saved = await client.put(draft, json={"version": 0, "lines": ["Could we choose a grocery day together?"]})
            assert saved.status_code == 200, saved.text
            body = {"pair_id": "comp-pair", "version": 1}; headers = {"Idempotency-Key": "comp-sponsored-once"}
            first = await client.post(tone, json=body, headers=headers)
            assert first.status_code == 200, first.text
            rid = first.json()["id"]
            assert (await client.post(tone, json=body, headers=headers)).json()["id"] == rid
            assert calls == [(rid, "couples")]
            ledger = await db.message_cost_units.find_one({"_id": rid})
            assert (ledger["status"], ledger["workspace"], ledger["pool"]) == ("settled", "couples", "complimentary")
            payer = await db.users.find_one({"user_id": owner["user_id"]})
            assert any(row["result"] == rid for row in payer["free_reply_requests"].values())
            assert free_plan.reply_usage(payer)["messages_used"] == 1 and not payer.get("paid_reply_cycles")
            assert not (await current()).get("free_reply_requests")
            await db.users.update_one({"user_id": owner["user_id"]}, {"$set": {"replies_paused": True}})
            changed = await client.put(draft, json={"version": 1, "lines": ["Could we choose a different grocery day together?"]})
            assert changed.status_code == 200, changed.text
            denied = await client.post(tone, json={**body, "version": 2}, headers={"Idempotency-Key": "comp-paused"})
            assert denied.status_code == 403, denied.text
            assert len(calls) == 1
    exercise(case)
