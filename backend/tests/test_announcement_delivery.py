import asyncio
from uuid import uuid4

import httpx
import pytest

from backend.tests.test_free_plan_quota import run_case


def test_interrupted_announcement_retries_once_per_inbox_and_reads_reach_staff(monkeypatch):
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        await db.notifications.create_indexes(server.INDEXES["notifications"])
        await db.users.insert_many([{"user_id": "first"}, {"user_id": "second", "env": server.ENV},
                                    {"user_id": "other", "env": "other"}, {"user_id": "closing", "closing": True}])
        message = {"request_id": str(uuid4()), "title": "An update", "link": "/app"}
        path = "/api/admin/announce"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            assert (await api.post(path, json=message)).status_code == 401
            monkeypatch.setitem(server.app.dependency_overrides, server.current_member, lambda: {"user_id": "first", "role": "member"})
            assert (await api.post(path, json=message)).status_code == 403
            monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
            for change in ({"title": "  "}, {"link": "//outside.example"}, {"link": "https://example.org"}):
                assert (await api.post(path, json={**message, **change})).status_code == 422
            preview = (await api.post(path+"/preview", json=message)).json()
            assert preview["eligible_inboxes"] == 2 and preview["title"] == message["title"]
            assert await db.notifications.count_documents({}) == await db.announcements.count_documents({}) == 0
            message["audience_as_of"] = preview["as_of"]
            deliver = server.notify_all

            async def interrupted(kind, title, text, link, key, **kwargs):
                await server.notify("first", kind, title, text, link, key)
                raise RuntimeError("fixture interrupted delivery")

            monkeypatch.setattr(server, "notify_all", interrupted)
            with pytest.raises(RuntimeError, match="fixture interrupted delivery"):
                await api.post(path, json=message)
            history = (await api.get(path + "ments")).json()
            assert len(history) == 1 and history[0]["status"] == "pending"
            assert history[0]["inboxes"] == 1 and history[0]["read"] == 0
            await db.users.insert_one({"user_id": "later", "created_at": server.now()})
            monkeypatch.setattr(server, "notify_all", deliver)
            sends = await asyncio.gather(*(api.post(path, json=message) for _ in range(3)))
            assert all(r.is_success for r in sends)
            assert (await api.post(path, json={**message, "title": "Different"})).status_code == 409
            assert sorted(await db.notifications.distinct("user_id")) == ["first", "second"]
            bell = (await api.get("/api/notifications")).json()
            assert len(bell["items"]) == bell["unread"] == 1
            assert bell["items"][0]["title"] == "An update"
            await api.post("/api/notifications/" + bell["items"][0]["id"] + "/read")
            history = (await api.get(path + "ments")).json()
            assert len(history) == 1 and history[0]["status"] == "sent"
            assert history[0]["inboxes"] == 2 and history[0]["read"] == 1
            await db.notifications.delete_many({"user_id": "first"})
            summary = (await api.get(path + "ments")).json()[0]
            assert summary["inboxes"] == 1 and summary["read"] == 0 and summary["inboxes_at_completion"] == 2
            assert summary["audience_as_of"] == message["audience_as_of"]
    run_case(scenario)


def test_members_and_staff_can_page_past_fifty_with_equal_timestamps(monkeypatch):
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.current_member, lambda: {"user_id": "reader"})
        monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
        stamp = server.now()
        for i in range(57):
            key = f"announce:{i:03}"
            await db.announcements.insert_one({"id": key, "title": key, "body": "", "link": "/app", "created_at": stamp})
            await db.notifications.insert_one({"id": str(i).zfill(3), "key": key, "type": "announcement",
                                               "user_id": "reader", "created_at": stamp, "read": False})
        await db.announcements.insert_one({"id": "hidden", "env": "other", "created_at": stamp})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            path = "/api/admin/announcements"
            first = (await api.get(path, params={"limit": 51})).json()
            assert len(first) == 51 and first[0]["id"] == "announce:056"
            second = (await api.get(path, params={"after_time": stamp, "after_id": first[49]["id"]})).json()
            assert [r["id"] for r in second] == [f"announce:{i:03}" for i in reversed(range(7))]
            assert all(r["status"] == "sent" and r["inboxes"] == 1 for r in first + second)
            assert (await api.get(path, params={"after_time": stamp})).status_code == 422
            bell = (await api.get("/api/notifications")).json()
            assert len(bell["items"]) == 50 and bell["unread"] == 57
            older = (await api.get("/api/notifications", params=bell["next"])).json()
            assert [r["id"] for r in older["items"]] == [f"{i:03}" for i in reversed(range(7))]
            assert older["next"] is None
            await api.post("/api/notifications/read-all")
            assert (await api.get("/api/notifications")).json()["unread"] == 0
            assert all(r["read"] == 1 for r in (await api.get(path)).json())
    run_case(scenario)
