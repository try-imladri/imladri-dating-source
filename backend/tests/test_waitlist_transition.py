import os
import httpx
from backend.tests.test_free_plan_quota import run_case


def test_waitlist_closed_while_form_is_open(monkeypatch):
    monkeypatch.setenv("MONGO_URL", os.getenv("IMLADRI_TEST_MONGO_URL", "mongodb://127.0.0.1:27017"))
    monkeypatch.setenv("DB_NAME", "imladri_test_unused")
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        await db.settings.insert_one({"key": "global", "waitlist_mode": True})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            url = "/api/public/waitlist"
            assert (await api.post(url, json={"email": "First@Example.com", "origin": "home"})).is_success
            assert (await api.post(url, json={"email": "first@example.com", "origin": "retry"})).is_success
            assert (await db.waitlist.find_one({"email": "first@example.com"}))["origin"] == "home"
            await db.settings.update_one({"key": "global"}, {"$set": {"waitlist_mode": False}})
            res = await api.post(url, json={"email": "second@example.com"})
            assert res.status_code == 409
            assert await db.waitlist.count_documents({}) == 1
            await db.settings.update_one({"key": "global"}, {"$set": {"waitlist_mode": True}})
            invalid = await api.post(url, json={"email": "someone@@example.com"})
            assert invalid.status_code == 422
            assert await db.waitlist.count_documents({}) == 1
    run_case(scenario)
