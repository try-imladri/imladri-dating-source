"""Use IMLADRI_BILLING_TEST_MONGO for isolated account lifecycle integration checks."""
import asyncio
import base64
import json
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
import stripe
import httpx
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from standardwebhooks import Webhook

from backend import accounts, stripe_plan
from scripts import setup_stripe_live_price


def test_clerk_webhook_only_accepts_signed_user_deletions(monkeypatch):
    key = "whsec_" + base64.b64encode(b"local test signing secret").decode()
    monkeypatch.setenv("CLERK_WEBHOOK_SIGNING_SECRET", key)
    payload = json.dumps({"type": "user.deleted", "data": {"id": "user_abc"}})
    now = datetime.now(timezone.utc)
    signature = Webhook(key).sign("msg_fixture", now, payload)
    headers = {"svix-id": "msg_fixture", "svix-timestamp": str(int(now.timestamp())), "svix-signature": signature}
    assert accounts.deleted_identity(payload.encode(), headers) == "user_abc"
    with pytest.raises(HTTPException) as error:
        accounts.deleted_identity(payload.replace("abc", "def").encode(), headers)
    assert error.value.status_code == 400


def test_live_catalog_default_verify_explicit_create(monkeypatch):
    stored = []
    class Price(dict):
        __getattr__ = dict.__getitem__
    def create(**kw):
        assert kw["lookup_key"] == stripe_plan.LOOKUP_KEY and kw["unit_amount"] == 1200
        assert kw["product_data"]["tax_code"] == "txcd_10105001"
        stored.append(Price(id="price_live", livemode=True, currency="usd", unit_amount=1200, billing_scheme="per_unit",
                        recurring={"interval":"month", "interval_count":1, "usage_type":"licensed"}))
        return stored[0]
    monkeypatch.setattr(stripe, "api_key", None)
    monkeypatch.setattr(stripe.Account, "retrieve", lambda: SimpleNamespace(id="acct_fixture"))
    monkeypatch.setattr(stripe.Price, "list", lambda **kw: SimpleNamespace(data=stored[:]))
    monkeypatch.setattr(stripe.Price, "create", create)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_test_fixture")
    with pytest.raises(ValueError): setup_stripe_live_price.setup("acct_fixture", create=True)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_live_fixture")
    with pytest.raises(ValueError): setup_stripe_live_price.setup("acct_wrong", create=True)
    with pytest.raises(ValueError): setup_stripe_live_price.setup("acct_fixture")
    assert not stored
    assert setup_stripe_live_price.setup("acct_fixture", create=True) == setup_stripe_live_price.setup("acct_fixture")
    assert setup_stripe_live_price.setup("acct_fixture", create=True) == "price_live" and len(stored) == 1


def test_complete_export_and_resumable_deletion(monkeypatch):
    uri = os.getenv("IMLADRI_BILLING_TEST_MONGO")
    if not uri:
        pytest.skip("Set IMLADRI_BILLING_TEST_MONGO for the local Mongo integration")
    assert urlsplit(uri).hostname in ("localhost", "127.0.0.1", "::1")
    actions = []
    status = {"sub_owned": "active", "sub_checkout": "active", "sub_market": "active"}
    mine = {"user_id": "user_fixture", "lookup_key": stripe_plan.LOOKUP_KEY}
    market = {"user_id": "user_fixture", "lookup_key": "market"}
    def read_sub(s):
        meta = market if s == "sub_market" else mine
        return {"status": status[s], "metadata": meta, "items": {"data": [{"price": {"lookup_key": meta["lookup_key"]}}]}}
    monkeypatch.setattr(stripe, "api_key", "sk_test_local")
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda s: {"metadata": mine, "status": "open"} if s == "cs_open" else {"metadata": mine, "status": "complete", "subscription": "sub_checkout"})
    monkeypatch.setattr(stripe.checkout.Session, "expire", lambda s: actions.append(s) or {"status": "expired"})
    monkeypatch.setattr(stripe.Subscription, "retrieve", read_sub)
    def cancel(s, **opts):
        assert opts == {"invoice_now": False, "prorate": False}
        status[s] = "canceled"; actions.append(s)
        return {"status": "canceled"}
    monkeypatch.setattr(stripe.Subscription, "cancel", cancel)
    monkeypatch.setattr(requests, "delete", lambda *a, **k: pytest.fail("Shared identity deletion attempted"))

    async def run():
        client = AsyncIOMotorClient(uri)
        db = client["imladri_lifecycle_test_" + uuid.uuid4().hex]
        member = {"user_id": "user_fixture", "stripe_subscription_id": "sub_owned", "about": {"quiz": "answer"}}
        try:
            await db.users.insert_one(member.copy())
            await db.people.insert_many([{"user_id":"user_fixture", "n":i} for i in range(1012)])
            await db.users.insert_one({"user_id":"user_other"})
            for name in accounts.ACCOUNT_COLLECTIONS:
                await db[name].insert_many([{"user_id":"user_other", "note":"private"}, {"user_id":"user_fixture", "note":"mine"}])
            await db.payment_transactions.insert_one({"user_id": "user_fixture", "stripe_subscription_id": "sub_market"})
            for sid in ("cs_open", "cs_done"):
                await db.payment_transactions.insert_one({"kind":"purchase", "session_id":sid, "user_id":"user_fixture", "payment_status":"pending"})
            data = json.loads(''.join([chunk async for chunk in accounts.export_json(db, member)]))
            assert data["account"]["about"] == {"quiz":"answer"} and len(data["people"]) == 1013
            for collection in accounts.ACCOUNT_COLLECTIONS:
                assert all(doc["user_id"] == "user_fixture" and "_id" not in doc for doc in data[collection])
            assert data["group_chats"][0]["note"] == "mine"
            lease = await accounts.reserve_request(db, "user_fixture")
            with pytest.raises(HTTPException): await accounts.finish_closure(db, "user_fixture")
            assert not actions and not await accounts.reserve_request(db, "user_fixture")
            await db.users.update_one({"user_id":"user_fixture"},{"$unset":{"request_leases." + lease:""}})
            with monkeypatch.context() as m:
                m.setattr(stripe, "api_key", None)
                with pytest.raises(HTTPException): await accounts.finish_closure(db, "user_fixture")
            assert not actions and await db.people.count_documents({"user_id":"user_fixture"}) == 1013
            await accounts.finish_closure(db, "user_fixture")
            await accounts.finish_closure(db, "user_fixture",identity_gone=True)
            assert not await accounts.membership_history(db, "user_fixture")
            assert sorted(actions) == ["cs_open", "sub_checkout", "sub_owned"] and status["sub_market"] == "active"
            for collection in ("users", *accounts.ACCOUNT_COLLECTIONS):
                assert await db[collection].count_documents({"user_id":"user_fixture"}) == 0
                assert await db[collection].count_documents({"user_id":"user_other"}) == 1
        finally:
            await client.drop_database(db.name)
            client.close()
    asyncio.run(run())


def test_authenticated_account_http_routes(monkeypatch):
    uri = os.getenv("IMLADRI_BILLING_TEST_MONGO")
    if not uri:
        pytest.skip("Set IMLADRI_BILLING_TEST_MONGO for the local Mongo integration")
    assert urlsplit(uri).hostname in ("localhost", "127.0.0.1", "::1")
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", "imladri_unused")
    from backend import server
    uid = "user_httpfixture"
    profile = {"first_name": "Fixture", "primary_email_address_id": "primary",
               "email_addresses": [{"id": "old", "email_address": "old@example.invalid"},
                                   {"id": "primary", "email_address": "qa@example.invalid"}]}
    responses = iter([requests.Timeout(), {"email_addresses": [None]}])

    def clerk_profile(*args, **kwargs):
        result = next(responses, profile)
        if isinstance(result, requests.RequestException):
            raise result
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: result)

    monkeypatch.setenv("CLERK_SECRET_KEY", "test-profile")
    monkeypatch.setattr(requests, "get", clerk_profile)
    monkeypatch.setattr(requests, "delete", lambda *a, **k: pytest.fail("Shared identity deletion attempted"))
    monkeypatch.setitem(server.app.dependency_overrides, server.require_user, lambda: {"user_id":uid})

    async def run():
        client = AsyncIOMotorClient(uri)
        db = client["imladri_lifecycle_test_" + uuid.uuid4().hex]
        monkeypatch.setattr(server, "db", db)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as http:
                for _ in range(2):
                    unavailable = await http.get("/api/me")
                    assert unavailable.status_code == 503
                    assert (await db.users.find_one({"user_id": uid})).get("role") is None
                new_member = (await http.get("/api/me")).json()
                assert new_member["email"] == "qa@example.invalid"
                assert await db.users.count_documents({"user_id": uid}) == 1
                assert not new_member["personal_access"] and new_member["usage"]["messages_remaining"] == 0
                assert (await http.get("/api/people")).status_code == 402
                assert (await http.patch("/api/me",json={"nickname":"Edited"})).status_code == 200
                assert not (await db.users.find_one({"user_id":uid})).get("request_leases")
                assert (await http.post("/api/couples/adult", json={"confirmed": True})).json()["adult"]
                assert (await http.patch("/api/couples/preferences", json={"analytics_opt_in": True, "reminders": True})).is_success
                # A member can update the Individual profile during a paid billing period.
                timestamp = int(datetime.now(timezone.utc).timestamp())
                await db.users.update_one({"user_id": uid}, {"$set": {"tier": "paid", "stripe_subscription_id": "sub_fixture",
                    "subscription_period_start": timestamp - 30, "subscription_period_end": timestamp + 3600}})
                assert (await http.put("/api/me/about", json={"demographics_patch": {"age": 17}})).is_success
                young = (await http.get("/api/couples")).json()
                assert young["underage"] and not any(young[key] for key in ("adult", "analytics_opt_in", "reminders"))
                assert (await http.put("/api/me/about", json={"demographics_patch": {"age": 30}})).is_success
                assert not (await http.get("/api/couples")).json()["adult"]
                resp = await http.get("/api/me/export")
                assert resp.status_code == 200 and resp.json()["account"]["nickname"] == "Edited" and resp.json()["group_chats"] == []
                await db.couples_drafts.insert_one({"user_id": uid, "key": "example", "text": "my draft"})
                await db.couples_drafts.insert_one({"user_id": uid, "key": "erased", "deleted": True})
                await db.couples_pairs.insert_one({"_id": "fixture-pair", "env": server.ENV, "inviter": uid, "invitee": "another",
                                                   "benefit_members": [uid, "another"], "members": [uid, "another"], "state": "active"})
                await db.users.update_one({"user_id": uid}, {"$set": {"couples_pilot_admin_change": "staff-uid", "couples_reminders_seen": ["private-key"]}})
                await db.couples_drafts.insert_one({"user_id":"another","key":"private","text":"Partner private text"})
                await db.users.update_one({"user_id":uid},{"$set":{"everyday_trends_opt_in":True}})
                impact = await http.get("/api/me/content-impact")
                assert impact.headers["cache-control"] == "private, no-store"
                assert impact.json()["counts"]["couples_drafts"] == 1 and impact.json()["shared_space_active"]
                assert "text" not in impact.text
                exported = (await http.get("/api/me/export")).json()
                assert len(exported["couples_drafts"]) == 1 and exported["couples_drafts"][0]["text"] == "my draft"
                assert "couples_pilot_admin_change" not in exported["account"] and "couples_reminders_seen" not in exported["account"]
                await db.users.update_one({"user_id": uid}, {"$set": {"tier": "unsubscribed"}, "$unset": {"stripe_subscription_id": ""}})
                lease = await accounts.reserve_request(db, uid)
                assert (await http.delete("/api/me")).status_code == 409
                await db.users.update_one({"user_id": uid}, {"$unset": {"request_leases." + lease: ""}})
                await db.users.update_one({"user_id":uid}, {"$set":{"free_replies_used":4,"free_replies_claimed":4}})
                assert (await http.delete("/api/me")).status_code == 200
                assert not await db.couples_pairs.find_one({"_id": "fixture-pair"}) and not await db.couples_drafts.find_one({"user_id": uid})
                after = (await http.get("/api/me/content-impact")).json()
                assert not after["shared_space_active"] and not any(after["counts"].values())
                assert not (await http.get("/api/me")).json()["everyday_trends_opt_in"]
                assert (await db.couples_drafts.find_one({"user_id":"another"}))["text"] == "Partner private text"
                assert not await accounts.reserve_request(db, uid, revision=None)
                assert (await http.patch("/api/me", json={"nickname":"Fresh"})).is_success
                assert (await http.delete("/api/me/account")).status_code == 200
                assert not await db.users.count_documents({"user_id":uid})
                assert (await http.get("/api/me")).status_code == 410
                assert (await http.patch("/api/me",json={"nickname":"Stale"})).status_code == 410
                assert (await http.delete("/api/me/account")).status_code == 200
                assert (await http.post("/api/me/rejoin")).status_code == 200
                me = (await http.get("/api/me")).json()
                assert me["usage"]["messages_remaining"] == 0 and me["people_count"] == 0
                assert (await db.users.find_one({"user_id": uid}))["used_sponsored_seat"] is True
        finally:
            await client.drop_database(db.name)
            client.close()
    asyncio.run(run())
