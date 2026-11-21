"""Set IMLADRI_TEST_MONGO_URL to a loopback MongoDB, or install mongomock-motor."""
import asyncio
import os
import time
import uuid
from urllib.parse import urlsplit

import pytest
from fastapi import HTTPException
from pydantic import BaseModel
from starlette.requests import Request

from backend.free_plan import initialize_usage, reply_usage, run_metered
from backend.content import migrate_paid_plan

def run_case(case):
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if uri:
        assert urlsplit(uri).hostname in {"127.0.0.1", "localhost", "::1"}
        from motor.motor_asyncio import AsyncIOMotorClient
        make_client = lambda: AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    else:
        make_client = pytest.importorskip("mongomock_motor").AsyncMongoMockClient

    async def go():
        client = make_client()
        db = client["imladri_quota_test_" + uuid.uuid4().hex]
        try:
            await case(db)
        finally:
            await client.drop_database(db.name)
            client.close()
    asyncio.run(go())


class Question(BaseModel):
    question: str


def test_former_free_accounts_never_generate_or_receive_a_trial():
    async def go(db):
        user = {"user_id": "former-free", "tier": "free", "free_replies_used": 0, "free_replies_claimed": 0}
        await db.users.insert_one(dict(user))
        request = Request({"type": "http", "headers": []})
        async def should_never_run(_):
            pytest.fail("unpaid account reached generation")
        member = await initialize_usage(db, user)
        assert reply_usage(member)["messages_remaining"] == 0
        with pytest.raises(HTTPException) as rejected:
            await run_metered(db, member, request, Question(question="test"), "exchange", "profile", should_never_run, should_never_run)
        assert rejected.value.status_code == 402 and rejected.value.headers["X-Imladri-Limit"] == "subscription"
        assert not (await db.users.find_one({"user_id": user["user_id"]})).get("free_reply_requests")
    run_case(go)


def test_rollout_retires_old_offer_while_preserving_current_editorial_copy():
    async def go(db):
        await db.settings.insert_one({"key": "global", "people_limit": 5, "free_plan_version": 2,
            "upgrade": {"price_label": "$8/month", "pitch": "Free includes one active person", "free_features": ["10 replies"],
                        "headline": "Support for your conversations", "paid_features": ["Keep your own notes"]}})
        await migrate_paid_plan(db)
        settings = await db.settings.find_one({"key": "global"})
        offer = settings["upgrade"]
        assert settings["people_limit"] is None and offer["price_label"] == "$12/month"
        assert "free" not in str(offer).lower() and "free_plan_version" not in settings
        assert offer["headline"] == "Support for your conversations" and "Keep your own notes" in offer["paid_features"]
        await migrate_paid_plan(db)
        assert await db.settings.find_one({"key": "global"}) == settings
    run_case(go)


def test_paid_cycle_cap_concurrent_retry_renewal_and_downgrade():
    async def go(db):
        uid = "paid-member"
        current = int(time.time())
        user = {"user_id": uid, "role": "member", "tier": "paid", "stripe_subscription_id": "sub_test",
                "subscription_period_start": current - 20, "subscription_period_end": current + 3600,
                "free_replies_used": 4, "free_replies_claimed": 4}
        await db.users.insert_one(dict(user))
        async def saved(rid):
            doc = {"user_id": uid, "id": rid, "response": "saved answer"}
            await db.exchanges.insert_one(dict(doc))
            return doc
        async def restore(doc):
            return doc
        async def read():
            return await db.users.find_one({"user_id": uid}, {"_id": 0})
        async def send(key, make=saved):
            req = Request({"type": "http", "headers": [(b"idempotency-key", key.encode())]})
            return await run_metered(db, await read(), req, Question(question="example"), "exchange", "profile", make, restore)
        async def fail(_):
            raise HTTPException(502, "provider unavailable")
        with pytest.raises(HTTPException) as exc:
            await send("first", fail)
        assert exc.value.status_code == 502
        assert reply_usage(await read())["messages_remaining"] == 100
        generated = await asyncio.gather(*(send(str(i)) for i in range(103)), return_exceptions=True)
        accepted = [(i, d) for i, d in enumerate(generated) if isinstance(d, dict)]
        assert len(accepted) == 100
        assert all(isinstance(d, dict) or isinstance(d, HTTPException) and d.status_code == 402 for d in generated)
        assert reply_usage(await read())["messages_remaining"] == 0
        assert reply_usage(await read())["messages_used"] == 100
        key, answer = accepted[0]
        assert await send(str(key)) == answer
        await db.users.update_one({"user_id": uid}, {"$set": {"subscription_period_end": current - 1}})
        with pytest.raises(HTTPException) as expired:
            await send("expired")
        assert expired.value.status_code == 402
        assert not reply_usage(await read())["period_verified"]
        await db.users.update_one({"user_id": uid}, {"$set": {"subscription_period_start": current - 1,
                                                              "subscription_period_end": current + 7200}})
        assert reply_usage(await read())["messages_remaining"] == 100
        assert await send(str(key)) == answer  # previous cycle replay stays free
        await send("new-cycle")
        assert reply_usage(await read())["messages_remaining"] == 99
        await db.users.update_one({"user_id": uid}, {"$set": {"tier":"free"}})
        usage = reply_usage(await read())
        assert (usage["plan"], usage["message_limit"], usage["messages_remaining"]) == ("unsubscribed", 0, 0)
        for key in ("new-after-expiry", "new-cycle"):
            with pytest.raises(HTTPException) as denied:
                await send(key)
            assert denied.value.status_code == 402
        await db.users.update_one({"user_id": uid}, {"$set": {"tier":"paid"}, "$unset": {"subscription_period_start": ""}})
        with pytest.raises(HTTPException) as missing_period:
            await send("no-period")
        assert missing_period.value.status_code == 402
    run_case(go)

def test_pending_reply_and_retries_stay_with_original_paid_cycle():
    async def go(db):
        t = int(time.time())
        uid = "straddles-renewal"
        initial = {"user_id":uid, "role":"member", "tier":"paid", "stripe_subscription_id":"sub_1",
                   "subscription_period_start":t-20, "subscription_period_end":t+100,
                   "free_replies_claimed":0, "free_replies_used":0}
        await db.users.insert_one(dict(initial))
        begun, release = asyncio.Event(), asyncio.Event()
        async def make(rid):
            begun.set()
            await release.wait()
            record = {"id":rid, "user_id":uid, "response":"saved"}
            await db.exchanges.insert_one(dict(record))
            return record
        async def restore(doc):
            return doc
        req = Request({"type":"http", "headers":[(b"idempotency-key", b"same-on-renewal")]})
        async def call(member):
            return await run_metered(db, member, req, Question(question="same question"), "exchange", "p", make, restore)
        started = asyncio.create_task(call(initial))
        await begun.wait()
        await db.users.update_one({"user_id":uid}, {"$set":{"subscription_period_start":t-1, "subscription_period_end":t+200}})
        renewed = await db.users.find_one({"user_id":uid})
        with pytest.raises(HTTPException) as duplicate:
            await call(renewed)
        assert duplicate.value.status_code == 409
        release.set()
        answer = await started
        assert await call(renewed) == answer
        after = await db.users.find_one({"user_id":uid})
        assert reply_usage(after)["messages_remaining"] == 100
        assert sum(bucket["used"] for bucket in after["paid_reply_cycles"].values()) == 1
    run_case(go)


def test_personal_and_sponsored_workspace_gates_on_normal_http_routes(monkeypatch):
    import httpx
    from fastapi import Request as HttpRequest
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Set IMLADRI_TEST_MONGO_URL to a disposable local MongoDB.")
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", "imladri_unused")
    monkeypatch.setenv("PERSONAL_BILLING_MODE", "disabled")
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        async def current(request: HttpRequest):
            return await db.users.find_one({"user_id": request.headers["x-member"]}, {"_id": 0})
        monkeypatch.setitem(server.app.dependency_overrides, server.current_member, current)
        at = int(time.time())
        await db.users.insert_many([
            {"user_id": "sponsor", "role": "member", "env": server.ENV, "tier": "paid", "subscription_status": "active",
             "stripe_subscription_id": "sub_fixture", "subscription_period_start": at - 60,
             "subscription_period_end": at + 3600, "couples_adult_at": "2026-01-01"},
            {"user_id": "partner", "role": "member", "env": server.ENV, "tier": "free", "onboarded": True,
             "free_replies_used": 0, "couples_adult_at": "2026-01-01"},
            {"user_id": "staff", "role": "admin", "env": server.ENV}])
        await db.settings.insert_one({"key":"global", "waitlist_mode": False, "people_limit": 1, "upgrade": {
                                     "price_label":"$8/month", "pitch":"Free trial available", "free_features":["Ten free messages"]}})
        await db.people.insert_one({"user_id":"partner", "id":"older", "nickname":"old private data"})

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as client:
            async def call(uid, path, method="GET", body=None):
                return await client.request(method, "/api" + path, headers={"x-member":uid}, json=body)
            me = (await call("partner", "/me")).json()
            assert not me["personal_access"] and me["tier"] == "unsubscribed" and me["usage"]["messages_remaining"] == 0
            offer = (await call("partner", "/upgrade")).json()
            assert offer["price_label"] == "$12/month" and not offer["checkout_available"]
            assert "free_features" not in offer and not any(t in offer["pitch"].lower() for t in ("trial", "free"))
            personal_paths = ["/quiz", "/intake-schema", "/me/about", "/me/type-compare", "/overview", "/people",
                              "/me/next-question", "/practice", "/patterns", "/chat-groups", "/people/older", "/group-chat?ids=older"]
            for path in personal_paths:
                resp = await call("partner", path)
                assert (resp.status_code, resp.headers.get("x-imladri-limit")) == (402,"subscription"), path
            assert (await call("partner", "/me/export")).json()["people"][0]["id"] == "older"
            assert (await call("partner", "/admin/trends")).status_code == 403
            assert (await call("partner", "/me", "PATCH", {"onboarded":True})).status_code == 402
            assert (await call("partner", "/me/event", "POST", {"type":"upload_completed"})).status_code == 402
            assert (await call("partner", "/me/couples-activity", "POST", {"action":"sample_completed"})).status_code == 402
            assert not await db.events.count_documents({"type":"couples_sample_completed"})
            assert (await call("partner", "/couples/journey")).status_code == 402
            assert (await call("partner", "/couples/drafts/communication-1", "PUT", {"version":0,"lines":["blocked"]})).status_code == 402
            assert (await call("sponsor", "/me")).json()["personal_access"]
            assert (await call("sponsor", "/quiz")).is_success
            assert (await call("staff", "/me")).json()["personal_access"]
            assert (await call("staff", "/users/partner/tier", "PATCH", {"tier":"paid"})).status_code in (404, 405)
            users = {u["user_id"]: u for u in (await call("staff","/users")).json()["users"]}
            assert users["partner"]["tier"] == "unsubscribed" and users["sponsor"]["tier"] == "paid"
            assert "stripe_subscription_id" not in users["sponsor"]
            await db.couples_pairs.insert_one({"_id":"pair","env":server.ENV,"members":["sponsor","partner"],"state":"active",
                                               "sponsor":"sponsor","inviter":"sponsor","invitee":"partner"})
            assert (await call("partner", "/couples")).json()["writable"]
            assert (await call("partner", "/couples/journey")).is_success
            assert (await call("partner", "/couples/drafts/communication-1", "PUT", {"version":0,"lines":["covered"]})).is_success
            assert (await call("partner", "/me/couples-activity", "POST", {"action":"sample_completed"})).is_success
            assert (await call("partner", "/me/about")).status_code == 200
            for uid in ("partner", "sponsor"):
                blocked = await call(uid, "/people")
                assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "couples_workspace_required"
            await db.users.update_one({"user_id":"sponsor"}, {"$set":{"subscription_period_end":at - 1}})
            assert not (await call("partner", "/couples")).json()["writable"]
            assert (await call("sponsor", "/quiz")).status_code == 402
            assert (await call("partner", "/couples/journey")).is_success
            assert (await call("partner", "/couples/drafts/communication-1", "PUT", {"version":1,"lines":["expired"]})).status_code == 402
            assert (await call("partner", "/couples/drafts/communication-1?version=1", "DELETE")).is_success
            assert (await call("partner", "/couples/leave", "POST", {"pair_id":"pair"})).is_success
            assert (await call("partner", "/couples/journey")).status_code == 402
    run_case(scenario)
