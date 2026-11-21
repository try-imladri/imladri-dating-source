import asyncio
import os
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI

from backend.support import support_router
from backend.tests.test_free_plan_quota import run_case


def test_concurrent_role_changes_keep_an_available_admin(monkeypatch):
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin", "user_id": "one"})
        await db.users.insert_many([{"user_id": uid, "role": "admin", "env": server.ENV}
                                    for uid in ("one", "two", "unavailable")])
        await db.users.update_one({"user_id": "unavailable"}, {"$set": {"closing": True}})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            def demote(uid):
                return api.patch(f"/api/users/{uid}/role", json={"role": "member"})

            responses = await asyncio.gather(demote("one"), demote("two"))
            assert all(response.status_code in (200, 409) for response in responses)
            active = await db.users.find({"role": "admin", "closing": {"$ne": True}}).to_list(None)
            assert active and any(response.status_code == 409 for response in responses)
            if len(active) == 2:
                assert (await demote("two")).status_code == 200
            last = await db.users.find_one({"role": "admin", "closing": {"$ne": True}})
            assert (await demote(last["user_id"])).status_code == 409
            assert await db.users.count_documents({"admin_demotion": {"$exists": True}}) == 0

            # An interrupted, expired demotion cannot block a later change.
            await db.users.update_one({"role": "member"}, {"$set": {"role": "admin", "admin_demotion": {
                "token": "old", "until": datetime.now(timezone.utc) - timedelta(seconds=10)}}})
            assert (await demote(last["user_id"])).status_code == 200
    run_case(scenario)


def test_search_and_cursor_reach_members_after_the_first_thousand(monkeypatch):
    monkeypatch.setenv("MONGO_URL", os.getenv("IMLADRI_TEST_MONGO_URL", "mongodb://127.0.0.1:27017"))
    monkeypatch.setenv("DB_NAME", "imladri_unused")
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
        await db.users.insert_many([{"user_id": f"account-{i:04}", "role": "member", "env": server.ENV}
                                    for i in range(1005)])
        await db.users.update_one({"user_id": "account-1004"}, {"$set": {"nickname": "Unique.Test", "role": "legacy"}})
        await db.users.insert_one({"user_id": "hidden", "env": "other", "nickname": "Unique.Test"})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as client:
            first = (await client.get("/api/users")).json()
            assert len(first["users"]) == 1000 and first["next_cursor"] == "account-0999"
            last = (await client.get("/api/users", params={"after": first["next_cursor"]})).json()
            assert [u["user_id"] for u in last["users"]] == [f"account-{i:04}" for i in range(1000, 1005)]
            assert last["next_cursor"] is None
            match = (await client.get("/api/users", params={"q": "UNIQUE.TEST"})).json()["users"]
            assert len(match) == 1 and match[0]["role"] == "member" and match[0]["tier"] == "unsubscribed"
            assert not (await client.get("/api/users", params={"q": "UNIQUE+TEST"})).json()["users"]
            page = (await client.get("/api/users", params={"q": "unsubscribed", "limit": 1})).json()
            assert len(page["users"]) == 1 and page["next_cursor"]
    run_case(scenario)


def test_staff_inbox_can_read_older_answers_when_timestamps_match():
    async def scenario(db):
        app = FastAPI()
        app.include_router(support_router(db, lambda: {"role": "admin"}))
        time = datetime.now(timezone.utc)
        await db.support_requests.insert_many([{"_id": f"{i:064x}", "status": "answered", "created_at": time,
                                                "expires_at": time + timedelta(days=1)} for i in range(56)])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            path = "/api/admin/support"
            first = (await client.get(path, params={"status": "answered", "limit": 51})).json()
            assert len(first) == 51
            anchor = first[49]
            await db.support_requests.delete_one({"_id": anchor["id"]})
            more = (await client.get(path, params={"status": "answered", "after_id": anchor["id"],
                                                   "after_time": anchor["created_at"]})).json()
            assert [r["id"] for r in more] == [f"{i:064x}" for i in reversed(range(6))]
            assert (await client.get(path, params={"after_id": anchor["id"]})).status_code == 422
    run_case(scenario)
