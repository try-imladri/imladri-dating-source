"""Group suggestions and replies must follow a member's deletion of a person."""
import asyncio
import json
import os
import time
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import httpx
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient


def test_deleting_person_clears_group_suggestions_and_pending_generations(monkeypatch):
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Set IMLADRI_TEST_MONGO_URL to run against disposable local MongoDB")
    assert urlsplit(uri).hostname in {"localhost", "127.0.0.1", "::1"}
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", "imladri_test_unused")
    from backend import accounts, server

    async def run():
        client = AsyncIOMotorClient(uri)
        db = client["imladri_group_deletion_" + uuid4().hex]
        monkeypatch.setattr(server, "db", db)
        member = {"user_id": "fixture-owner"}
        ids = ["alpha", "beta"]
        arrivals = asyncio.Queue()
        release = asyncio.Event()

        async def starters(*args, **kwargs):
            arrivals.put_nowait("starters")
            await release.wait()
            return ["A suggestion about both people"]

        async def reply(*args, **kwargs):
            arrivals.put_nowait("reply")
            await release.wait()
            return {"what_it_means": "A reply about both people"}

        async def context(*args, **kwargs):
            return "Synthetic context", []

        monkeypatch.setattr(server, "make_starters", starters)
        monkeypatch.setattr(server, "ai_json", reply)
        monkeypatch.setattr(server, "full_context", context)
        pending = []
        try:
            await db.people.insert_many([{"id": pid, "nickname": pid, **member} for pid in ["alpha", "beta", "alphabet", "gamma"]])
            await db.group_starters.insert_many([
                {"key": "alpha,gamma", **member, "items": ["Old suggestion from a deleted profile"]},
                {"key": "alphabet,beta", **member, "items": ["Keep this unrelated suggestion"]},
                {"key": "alpha,gamma", "user_id": "other", "items": ["Keep another member's row"]},
            ])
            pending.extend([
                asyncio.create_task(server.group_starters(server.GroupIdsIn(person_ids=ids), member)),
                asyncio.create_task(server.generate_group_answer(ids, "A fictional question", member, "fixture-reply")),
            ])
            assert {await asyncio.wait_for(arrivals.get(), 5), await asyncio.wait_for(arrivals.get(), 5)} == {"reply", "starters"}
            assert (await server.delete_person("alpha", member))["ok"]
            release.set()
            results = await asyncio.gather(*pending, return_exceptions=True)
            rows = [row["key"] async for row in db.group_starters.find(member)]
            assert sorted(rows) == ["alphabet,beta"]
            assert await db.group_starters.count_documents({"user_id": "other"}) == 1
            assert await db.group_chats.count_documents(member) == 0
            assert all(isinstance(result, HTTPException) and result.status_code == 404 for result in results)
            await db.group_starters.insert_one({"key": "old-missing,gamma", **member, "items": ["Historical orphan"]})
            export = json.loads("".join([chunk async for chunk in accounts.export_json(db, member)]))
            assert [r["key"] for r in export["group_starters"]] == ["alphabet,beta"]
            assert export["group_chats"] == []
        finally:
            release.set()
            await asyncio.gather(*pending, return_exceptions=True)
            await client.drop_database(db.name)
            client.close()

    asyncio.run(run())


def test_interrupted_group_read_returns_the_reserved_reply(monkeypatch):
    from backend.tests.test_free_plan_quota import run_case

    monkeypatch.setenv("MONGO_URL", os.getenv("IMLADRI_TEST_MONGO_URL", "mongodb://127.0.0.1:27017"))
    monkeypatch.setenv("DB_NAME", "imladri_test_unused")
    from backend import server

    async def run(db):
        uid = "fixture-group-quota"
        now = int(time.time())
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.require_user, lambda: {"user_id": uid})
        await db.users.insert_one({"user_id": uid, "role": "member", "env": server.ENV, "tier": "paid",
                                   "stripe_subscription_id": "sub_fixture", "subscription_period_start": now - 60,
                                   "subscription_period_end": now + 3600})
        await db.people.insert_many([{"id": pid, "user_id": uid, "nickname": pid, "rel_type": "friend",
                                      "stage": "good"} for pid in ("first", "second")])
        started, release = asyncio.Event(), asyncio.Event()

        async def model(*args, **kwargs):
            started.set()
            await release.wait()
            return {"what_it_means": "Synthetic interpretation."}

        monkeypatch.setattr(server, "ai_json", model)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            assert (await api.get("/api/me")).json()["usage"]["messages_remaining"] == 100
            pending = asyncio.create_task(api.post("/api/group-chat", json={"person_ids": ["first", "second"], "question": "A fictional question"}))
            try:
                await asyncio.wait_for(started.wait(), 5)
                assert (await api.delete("/api/people/first")).status_code == 200
            finally:
                release.set()
            assert (await pending).status_code == 404
            assert (await api.get("/api/me")).json()["usage"]["messages_remaining"] == 100
            assert await db.group_chats.count_documents({"user_id": uid}) == 0
    run_case(run)
