"""Host routing, account lifecycle and repository registration for the dating pivot."""
import json
import os
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import Header, HTTPException

from backend.tests.test_free_plan_quota import run_case


def host_case(monkeypatch, exercise):
    # The test runner supplies loopback endpoints; never load a developer .env.
    monkeypatch.setenv("MONGO_URL", os.environ.get("IMLADRI_TEST_MONGO_URL", "mongodb://127.0.0.1:27017"))
    monkeypatch.setenv("DB_NAME", "dating_host_fixture_unused")
    from backend import server

    async def run(db):
        monkeypatch.setattr(server, "db", db)
        await server.ensure_indexes()
        await db.users.insert_many([
            {"user_id": "host-member", "role": "member", "tier": "unsubscribed", "env": server.ENV, "nickname": "Legacy private name", "about": {"private": "prior assessment"}},
            {"user_id": "other-member", "role": "member", "tier": "unsubscribed", "env": server.ENV},
        ])

        async def identity(x_member: str = Header(default="")):
            if x_member not in {"host-member", "other-member"}:
                raise HTTPException(401, "Authentication required.")
            return {"user_id": x_member}

        previous = dict(server.app.dependency_overrides)
        server.app.dependency_overrides[server.require_user] = identity
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://loopback.test") as client:
                await exercise(db, client, server)
        finally:
            server.app.dependency_overrides.clear()
            server.app.dependency_overrides.update(previous)

    run_case(run)


async def enroll_and_draft(client):
    headers = {"x-member": "host-member"}
    response = await client.post("/api/dating/enrollment", headers=headers, json={"version":"dating-enrollment-v1", "accepted":True, "adult_confirmed":True})
    assert response.status_code == 200, response.text
    response = await client.put("/api/dating/profile", headers=headers, json={"display_name":"Chosen dating name", "date_of_birth":"1990-04-10", "gender":"woman", "city":"Chicago", "bio":"A freshly written dating introduction.", "expected_revision":0})
    assert response.status_code == 200, response.text
    return response.json()["profile"]


def test_new_unpaid_member_can_choose_dating_without_publishing_legacy_data(monkeypatch):
    async def run(db, client, server):
        denied = await client.get("/api/dating/bootstrap")
        assert denied.status_code == 401
        assert denied.headers["cache-control"] == "private, no-store"
        result = await client.get("/api/dating/bootstrap", headers={"x-member":"host-member"})
        assert result.status_code == 200
        data = result.json()
        assert data["enrollment"] is None and data["profile"] is None
        assert not data["membership"]["checkout_enabled"]
        assert "prior assessment" not in result.text
        assert result.headers["x-robots-tag"] == "noindex, nofollow"
        profile = await enroll_and_draft(client)
        assert profile["display_name"] == "Chosen dating name" and profile["state"] == "draft"
        assert (await db.users.find_one({"user_id":"host-member"}))["about"] == {"private":"prior assessment"}
        admin = await client.get("/api/dating/admin/queue", headers={"x-member":"host-member"})
        assert admin.status_code == 403
    host_case(monkeypatch, run)


def test_leave_waits_for_inflight_writes_then_keeps_earlier_tools(monkeypatch):
    async def run(db, client, server):
        await enroll_and_draft(client)
        await db.people.insert_one({"user_id":"host-member", "id":"legacy-person", "name":"Private earlier note"})
        await db.users.update_one({"user_id":"host-member"}, {"$set":{"request_leases.concurrent":datetime.now(timezone.utc) + timedelta(minutes=5)}})
        response = await client.request("DELETE", "/api/dating/enrollment", headers={"x-member":"host-member"}, json={"confirmed":True})
        assert response.status_code == 409
        assert await db.dating_profiles.count_documents({"user_id":"host-member"}) == 1
        await db.users.update_one({"user_id":"host-member"}, {"$unset":{"request_leases.concurrent":""}})
        response = await client.request("DELETE", "/api/dating/enrollment", headers={"x-member":"host-member"}, json={"confirmed":True})
        assert response.status_code == 200, response.text
        assert await db.dating_profiles.count_documents({"user_id":"host-member"}) == 0
        assert await db.people.count_documents({"user_id":"host-member"}) == 1
        assert (await client.get("/api/dating/bootstrap", headers={"x-member":"host-member"})).json()["enrollment"] is None
    host_case(monkeypatch, run)


def test_full_account_export_and_closure_cover_all_owner_environments(monkeypatch):
    async def run(db, client, server):
        await enroll_and_draft(client)
        await db.dating_preferences.insert_one({"env":"separate-test-env", "user_id":"host-member", "intimacy":{"private":"owner only"}})
        await db.dating_preferences.insert_one({"env":server.ENV, "user_id":"other-member", "intimacy":{"private":"someone else's secret"}})
        await db.dating_photos.insert_one({"env":server.ENV, "user_id":"host-member", "id":"fixture-photo", "data":"private photo payload"})
        member = await db.users.find_one({"user_id":"host-member"})
        document = json.loads("".join([part async for part in server.accounts.export_json(db, member)]))
        assert document["dating"]["dating_profiles"]
        assert len(document["dating"]["dating_preferences"]) == 1
        assert "someone else's secret" not in json.dumps(document)
        monkeypatch.setattr(server.accounts, "close_billing", lambda *args: None)
        await server.accounts.finish_closure(db, "host-member")
        for name in ("dating_profiles", "dating_preferences", "dating_photos", "dating_enrollments"):
            assert await db[name].count_documents({"user_id":"host-member"}) == 0
        assert await db.dating_preferences.count_documents({"user_id":"other-member"}) == 1
        assert await db.users.find_one({"user_id":"host-member"}) is None
    host_case(monkeypatch, run)


def test_every_dating_collection_is_registered_for_postgres_rpc_and_backup():
    from backend import dating
    from backend.postgres_migration import COLLECTION_DOMAINS
    from backend.postgres_gateway import COLLECTIONS
    from backend.postgres_copy_registry import PERSONAL_COLLECTIONS
    assert dating.INDEXES
    for name in dating.INDEXES:
        assert COLLECTION_DOMAINS[name] == "dating"
        assert name in COLLECTIONS and name in PERSONAL_COLLECTIONS
