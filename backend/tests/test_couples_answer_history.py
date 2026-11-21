"""Couples history uses real isolated Mongo writes and preserves owner boundaries."""
import asyncio
from copy import deepcopy
from datetime import date, timedelta
import json
import os
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Header, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from backend import answer_history
from backend.couples import COUPLES_INDEXES, clear_couples, couples_router
from backend.couples_journey import (
    Action, JOURNEY_INDEXES, PROMPTS, apply_action, history_prefix,
    history_question, journey_router, new_thread, thread_history_observation,
)


def test_shared_observation_contains_only_the_actors_own_private_checkin():
    thread = new_thread("support-1", "prompt", PROMPTS[0])
    thread["shares"] = {"a": ["Own released words"], "b": ["Partner released words"]}
    thread["agreements"] = [{"id": "agreement", "revision": 2, "status": "active", "accepted": ["a", "b"],
        "text": "An already shared agreement", "author": "b", "checkins": {
            "a": {"result": "mixed", "note": "My private note", "shared": False},
            "b": {"result": "helped", "note": "PARTNER PRIVATE NOTE", "shared": False}}}]
    body = Action(pair_id="pair", version=4, action="checkin", agreement_id="agreement", result="mixed")
    key, question, after = thread_history_observation(thread, "a", body)
    assert key == "thread:support-1:checkin:agreement"
    assert after["checkin"]["note"] == "My private note"
    assert "PARTNER PRIVATE NOTE" not in json.dumps([question, after])
    assert "Partner released words" not in json.dumps(after)


def test_adjusted_agreement_history_points_to_the_new_revision_and_question_is_published_only():
    question = PROMPTS[0]
    thread = new_thread(question["id"], "prompt", question)
    thread["shares"] = {"a": ["One"], "b": ["Two"]}
    thread["agreements"] = [{"id": "old", "revision": 1, "origin_id": "old", "text": "Earlier", "author": "b",
        "review_on": "2026-12-01", "status": "active", "accepted": ["a", "b"], "checkins": {}, "pin": None}]
    before = deepcopy(thread)
    body = Action(pair_id="pair", version=3, action="propose", agreement_id="old", text="A new version",
                  review_on="2026-12-02")
    apply_action(thread, "a", body, {}, "2026-11-10T12:00:00+00:00")
    key, wording, after = thread_history_observation(thread, "a", body, before)
    assert after["agreement_id"] != "old" and after["agreement_revision"] == 2
    assert key.endswith(":agreement:" + after["agreement_id"])
    assert after["text"] == "A new version"
    published = history_question(question["id"], {**question, "lines": ["PRIVATE"], "hidden": "SECRET"})
    assert "PRIVATE" not in json.dumps(published) and "SECRET" not in json.dumps(published)
    assert published["question"] == question["question"]


def run_case(scenario):
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Needs a disposable loopback MongoDB.")
    assert urlsplit(uri).hostname in {"localhost", "127.0.0.1", "::1"}

    async def run():
        mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        db = mongo["couples_answer_history_" + uuid4().hex]
        try:
            for name, indexes in {**COUPLES_INDEXES, **JOURNEY_INDEXES}.items():
                await db[name].create_indexes(indexes)
            stamp = int(time.time())
            members = [{"user_id": uid, "role": "member", "env": "preview",
                        "couples_adult_at": "2026-01-01T00:00:00+00:00",
                        "about": {"demographics": {"age": 30, "height_cm": 175}}}
                       for uid in ("host", "guest", "outsider")]
            for member in (members[0], members[2]):
                member.update(tier="paid", stripe_subscription_id="sub_fixture",
                              subscription_period_start=stamp - 300, subscription_period_end=stamp + 86400)
            await db.users.insert_many(members)
            await db.couples_pairs.insert_one({"_id": "pair", "env": "preview", "state": "active",
                "inviter": "host", "invitee": "guest", "members": ["host", "guest"], "sponsor": "host"})
            app = FastAPI()

            async def identity(x_member: str = Header()):
                member = await db.users.find_one({"user_id": x_member, "closing": {"$ne": True}})
                if not member:
                    raise HTTPException(401)
                return member

            router = APIRouter(prefix="/api")
            router.include_router(couples_router(lambda: db, identity))
            router.include_router(journey_router(lambda: db, identity))
            app.include_router(router)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as api:
                async def call(uid, method, path, body=None, status=200):
                    response = await api.request(method, "/api/couples" + path, headers={"x-member": uid}, json=body)
                    assert response.status_code == status, (path, response.text)
                    return response.json()
                await scenario(db, call)
        finally:
            await mongo.drop_database(db.name)
            mongo.close()

    asyncio.run(run())


def test_private_answers_journal_atomic_versions_retries_skips_and_erasure(monkeypatch):
    async def scenario(db, call):
        original = answer_history.flush

        async def unavailable(database, source, query, **kwargs):
            if source == "couples_drafts":
                return {"failed": True, "archived": 0, "redacted": 0}
            return await original(database, source, query, **kwargs)

        monkeypatch.setattr(answer_history, "flush", unavailable)
        first = {"version": 0, "choice": "Listening", "lines": ["Listening", "Original private words"]}
        await call("host", "PUT", "/drafts/support-1", first)
        source = await db.couples_drafts.find_one({"user_id": "host", "key": "support-1"})
        assert source["version"] == 1 and len(source[answer_history.PENDING]) == 1
        assert await db.answer_history.count_documents({"domain": "couples"}) == 0
        pending = source[answer_history.PENDING][0]
        assert pending["after"]["lines"] == first["lines"] and pending["pair_id"] == "pair"
        assert pending["after"]["pair_side"] == "a" and pending["visibility"] == "private"
        assert "age" not in pending["demographics"] and pending["demographics"]["snapshot_id"]
        monkeypatch.setattr(answer_history, "flush", original)
        await call("host", "PUT", "/drafts/support-1", first)
        assert await db.answer_history.count_documents({"domain": "couples"}) == 1
        second = {"version": 1, "choice": "Quiet company", "lines": ["Quiet company", "Revised private words"]}
        await call("host", "PUT", "/drafts/support-1", second)
        await call("host", "PUT", "/drafts/support-1", first, 409)
        # Two competing writes from the same revision produce exactly one new observation.
        member = await db.users.find_one({"user_id": "host"})
        from backend.couples_journey import DraftIn, save_draft
        writes = await asyncio.gather(*[save_draft(db, member, "support-1", DraftIn(version=2, lines=[words]))
                                       for words in ("Concurrent A", "Concurrent B")], return_exceptions=True)
        assert sum(not isinstance(value, Exception) for value in writes) == 1
        assert [value.status_code for value in writes if isinstance(value, HTTPException)] == [409]
        await call("host", "PUT", "/drafts/support-1", {"version": 3, "lines": [], "skipped": True})
        rows = await db.answer_history.find({"source": "couples_drafts", "owner_id": "host"}).sort("revision", 1).to_list(None)
        assert [row["revision"] for row in rows] == [1, 2, 3, 4]
        assert rows[0]["after"]["lines"] == first["lines"] and rows[-1]["after"]["skipped"] is True
        assert all(row["question"]["question"] == "After a difficult day, what kind of support would you welcome?" for row in rows)
        await call("guest", "PUT", "/drafts/support-1", {"version": 0, "lines": ["Other person's private words"]})
        erased_ids = [row["_id"] for row in rows]
        await call("host", "DELETE", "/drafts/support-1?version=4")
        assert await db.answer_history.count_documents({"source": "couples_drafts", "owner_id": "host"}) == 0
        assert await db.answer_history.count_documents({"source": "couples_drafts", "owner_id": "guest"}) == 1
        for row in await db.answer_history.find({"_id": {"$in": erased_ids}}).to_list(None):
            assert row["purged"] is True and "after" not in row and "owner_id" not in row
        await call("host", "PUT", "/drafts/support-1", {"version": 4, "lines": ["Delayed resurrection"]}, 409)

    run_case(scenario)


def test_shared_actions_record_actor_only_content_and_scoped_withdrawals_and_leave():
    async def scenario(db, call):
        key = "support-1"
        for uid, choice, secret in (("host", "Listening", "HOST PRIVATE"), ("guest", "Quiet company", "GUEST PRIVATE")):
            await call(uid, "PUT", "/drafts/" + key, {"version": 0, "choice": choice, "lines": [choice, secret]})

        async def act(uid, version, action, request_id=None, **fields):
            return await call(uid, "POST", "/threads/" + key,
                              {"pair_id": "pair", "version": version, "action": action,
                               **({"request_id": request_id} if request_id else {}), **fields})

        await act("host", 0, "share", kind="prompt", lines=["Listening"])
        await act("guest", 1, "share", kind="prompt", lines=["Quiet company"])
        proposal = await act("host", 2, "propose", text="Ask about support before offering it.",
                             review_on=(date.today() + timedelta(days=5)).isoformat())
        agreement = proposal["threads"][0]["agreements"][0]["id"]
        await act("guest", 3, "accept", agreement_id=agreement)
        await act("host", 4, "checkin", agreement_id=agreement, result="mixed", note="HOST ONLY NOTE", share=False)
        other_view = await call("guest", "GET", "/journey")
        assert other_view["threads"][0]["agreements"][0]["partner_checkin"] is None
        await act("guest", 5, "checkin", agreement_id=agreement, result="helped", note="GUEST ONLY NOTE", share=False)
        guest_history = await db.answer_history.find({"source": "couples_pairs", "owner_id": "guest"}).to_list(None)
        assert "HOST ONLY NOTE" not in json.dumps(guest_history, default=str)
        all_shared = await db.answer_history.find({"source": "couples_pairs"}).to_list(None)
        assert "HOST PRIVATE" not in json.dumps(all_shared, default=str) and "GUEST PRIVATE" not in json.dumps(all_shared, default=str)
        await act("host", 6, "checkin", agreement_id=agreement, result="mixed", note="HOST ONLY NOTE", share=True)
        await act("guest", 7, "checkin", agreement_id=agreement, result="helped", note="GUEST ONLY NOTE", share=True)
        await act("host", 8, "pin", agreement_id=agreement, text="A learning to remove")
        await act("guest", 9, "accept_pin", agreement_id=agreement)
        await act("host", 10, "withdraw_checkin", request_id="remove-host-checkin", agreement_id=agreement)
        remaining = await db.answer_history.find({"source": "couples_pairs"}).to_list(None)
        assert "HOST ONLY NOTE" not in json.dumps(remaining, default=str)
        assert "GUEST ONLY NOTE" in json.dumps(remaining, default=str)
        assert "A learning to remove" not in json.dumps(remaining, default=str)
        before_retry = len(remaining)
        await act("host", 10, "withdraw_checkin", request_id="remove-host-checkin", agreement_id=agreement)
        assert await db.answer_history.count_documents({"source": "couples_pairs"}) == before_retry
        await call("outsider", "POST", "/threads/" + key,
                   {"pair_id": "pair", "version": 11, "action": "withdraw"}, 404)
        await act("host", 11, "withdraw")
        remaining = await db.answer_history.find({"source": "couples_pairs"}).to_list(None)
        assert len(remaining) == 1 and remaining[0]["after"]["withdrawn"] is True
        assert "GUEST ONLY NOTE" not in json.dumps(remaining, default=str)
        await call("host", "POST", "/leave", {"pair_id": "pair"})
        assert await db.answer_history.count_documents({"source": "couples_pairs"}) == 0
        pair = await db.couples_pairs.find_one({"_id": "pair"})
        assert pair["state"] == "closed" and "journey" not in pair and "members" not in pair
        await clear_couples(db, "host")
        assert await db.couples_pairs.count_documents({"_id": "pair"}) == 0
        assert await db.couples_drafts.count_documents({"user_id": "guest"}) == 1

    run_case(scenario)


def test_adult_and_preferences_history_are_versioned_and_retries_do_not_duplicate():
    async def scenario(db, call):
        await db.users.update_one({"user_id": "host"}, {"$unset": {"couples_adult_at": ""}})
        await asyncio.gather(*[call("host", "POST", "/adult", {"confirmed": True}) for _ in range(2)])
        assert await db.answer_history.count_documents({"question_id": "couples.adult", "owner_id": "host"}) == 1
        for _ in range(2):
            await call("host", "PATCH", "/preferences", {"analytics_opt_in": False})
        assert await db.answer_history.count_documents({"question_id": "couples.preferences", "owner_id": "host"}) == 1
        await call("host", "PATCH", "/preferences", {"analytics_opt_in": True, "review_return_opt_in": True})
        rows = await db.answer_history.find({"question_id": "couples.preferences", "owner_id": "host"}).sort("source_revision", 1).to_list(None)
        assert [row["after"]["analytics_opt_in"] for row in rows] == [False, True]
        assert all(row["source_id"] == "host" and row["domain"] == "consent" for row in rows)
        since = rows[-1]["after"]["review_return_since"]
        await call("host", "PATCH", "/preferences", {"review_return_opt_in": True})
        member = await db.users.find_one({"user_id": "host"})
        assert member["couples_preferences"]["review_return_since"] == since
        assert await db.answer_history.count_documents({"question_id": "couples.preferences", "owner_id": "host"}) == 2

    run_case(scenario)


def test_leave_tombstone_fences_an_archive_already_in_flight(monkeypatch):
    async def scenario(db, call):
        from backend.couples_journey import change_thread
        for uid, words in (("host", "Host released answer"), ("guest", "Guest released answer")):
            await call(uid, "PUT", "/drafts/support-1", {"version": 0, "lines": [words]})
        await call("host", "POST", "/threads/support-1",
                   {"pair_id": "pair", "version": 0, "action": "share", "kind": "prompt", "lines": ["Host released answer"]})
        entered, release = asyncio.Event(), asyncio.Event()
        collection_type = type(db.answer_history)
        original_update = collection_type.update_one

        async def held_archive(collection, query, update, *args, **kwargs):
            record = update.get("$setOnInsert", {})
            if (collection.database.name == db.name and collection.name == "answer_history"
                    and record.get("source") == "couples_pairs" and record.get("actor_id") == "guest"):
                entered.set()
                await release.wait()
            return await original_update(collection, query, update, *args, **kwargs)

        monkeypatch.setattr(collection_type, "update_one", held_archive)
        member = await db.users.find_one({"user_id": "guest"})
        writer = asyncio.create_task(change_thread(db, member, "support-1", Action(
            pair_id="pair", version=1, action="share", kind="prompt", lines=["Guest released answer"])))
        await asyncio.wait_for(entered.wait(), timeout=5)
        try:
            await call("host", "POST", "/leave", {"pair_id": "pair"})
            assert await db.answer_history.count_documents({"source": "couples_pairs"}) == 0
        finally:
            release.set()
            await asyncio.wait_for(writer, timeout=5)
        assert await db.answer_history.count_documents({"source": "couples_pairs"}) == 0
        pair = await db.couples_pairs.find_one({"_id": "pair"})
        assert pair["state"] == "closed" and "journey" not in pair
        assert not pair.get(answer_history.PENDING) and not pair.get(answer_history.REDACTIONS)

    run_case(scenario)
