import os

import httpx
from backend.tests.test_free_plan_quota import run_case


def test_person_export_includes_every_owned_record_and_excludes_deleted_group_members(monkeypatch):
    monkeypatch.setenv("MONGO_URL", os.getenv("IMLADRI_TEST_MONGO_URL", "mongodb://127.0.0.1:27017"))
    monkeypatch.setenv("DB_NAME", "imladri_test_unused")
    from backend import server

    async def scenario(db):
        uid, pid = "test-export-owner", "subject"
        member = {"user_id": uid, "role": "member"}
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.subscribed_member, lambda: member)
        await db.people.insert_many([{"id": pid, **member}, {"id": "partner", **member}])
        await db.memories.insert_many([{"id": str(i), "user_id": uid, "person_id": pid} for i in range(10001)])
        await db.group_chats.insert_many([{"id": str(i), "user_id": uid, "person_ids": [pid, "partner"]} for i in range(1001)])
        await db.group_chats.insert_one({"id": "obsolete", "user_id": uid, "person_ids": [pid, "deleted"]})
        await db.memories.insert_one({"id": "another-owner", "user_id": "other", "person_id": pid})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            res = await api.get(f"/api/people/{pid}/export")
            assert res.status_code == 200
            doc = res.json()
            assert {v["id"] for v in doc["memories"]} == {str(i) for i in range(10001)}
            assert {v["id"] for v in doc["group_chats"]} == {str(i) for i in range(1001)}
            assert res.headers.get("cache-control") == "no-store"
            assert set(doc) == {"person", "memories", "exchanges", "corrections", "practice", "preps", "group_chats"}
    run_case(scenario)
