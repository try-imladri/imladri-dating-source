"""Sharing grants cannot survive a removed/reconfirmed adult-participation epoch."""
from copy import deepcopy

import httpx
import pytest
from fastapi import HTTPException

from backend import couples_compatibility as compat, personal_demographics
from backend.couples_analytics import report
from backend.postgres_store import PostgresCollection
from backend.tests.test_couples_assessments import edit, fixture
from backend.tests.test_research_notices import run_case


@pytest.mark.parametrize("uid", ["a", "b"])
def test_minor_then_new_adult_confirmation_requires_new_explicit_sharing(monkeypatch, uid):
    from backend import server

    async def case(db):
        members, _, quiz, _ = await fixture(db)
        for member in members:
            await compat.set_sharing(db, member, quiz, edit(member["user_id"]))
        old = await compat.view(db, members[0], quiz)
        assert old["domains"]["intimacy"]["items"]
        prior_intimacy = await db.intimacy_answers.find_one({"_id": uid})
        old_epoch = (await db.users.find_one({"user_id": uid}))["couples_adult_at"]
        await personal_demographics.save_own(db, uid, {"age": 17})
        assert not (await db.users.find_one({"user_id": uid})).get("couples_adult_at")
        await personal_demographics.save_own(db, uid, {"age": 30})
        monkeypatch.setattr(server, "db", db)

        async def current():
            return await db.users.find_one({"user_id": uid})

        server.app.dependency_overrides[server.current_member] = current
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as client:
                confirmed = await client.post("/api/couples/adult", json={"confirmed": True})
                assert confirmed.status_code == 200, confirmed.text
        finally:
            server.app.dependency_overrides.clear()
        member = await current()
        assert member["couples_adult_at"] != old_epoch
        # Newly supplied adult answers must not inherit the earlier sharing choice.
        await db.intimacy_answers.update_one({"_id": uid}, {"$set": {
            key: deepcopy(prior_intimacy[key]) for key in ("values", "conditions", "signatures", "adult_confirmed")
        }, "$inc": {"revision": 1}})
        stale = await compat.view(db, member, quiz)
        assert stale["sharing"]["revision"] == 1
        assert not stale["sharing"]["personality"] and not stale["sharing"]["intimacy"]
        assert all(domain["status"] == "awaiting_sharing" and not domain["items"] for domain in stale["domains"].values())
        assert not (await compat.coach_context(db, member, quiz, "assessment-pair"))["comparisons"]
        counts = (await report(db, "preview", {"role": "member"}, 30))["assessment_sharing"]
        assert counts == {"active_pairs": 1, "any_sharing_pairs": 1,
                          "personality_mutual_pairs": 0, "intimacy_mutual_pairs": 0}
        renewed = await compat.set_sharing(db, member, quiz, edit(uid, rev=1))
        assert renewed["sharing"]["revision"] == 2 and renewed["sharing"]["personality"] and renewed["sharing"]["intimacy"]
        assert renewed["domains"]["intimacy"]["items"]
        pair = await db.couples_pairs.find_one({"_id": "assessment-pair"})
        side = "a" if uid == "a" else "b"
        assert pair["compatibility_sharing"][side]["couples_adult_at"] == member["couples_adult_at"]
        counts = (await report(db, "preview", {"role": "member"}, 30))["assessment_sharing"]
        assert counts["personality_mutual_pairs"] == counts["intimacy_mutual_pairs"] == 1

    run_case(case)


@pytest.mark.parametrize("change", ["missing_grant_epoch", "new_current_epoch"])
def test_stale_epoch_has_no_private_answer_reads_and_retains_revision(monkeypatch, change):
    async def case(db):
        members, _, quiz, _ = await fixture(db)
        for member in members:
            await compat.set_sharing(db, member, quiz, edit(member["user_id"]))
        if change == "missing_grant_epoch":
            await db.couples_pairs.update_one({"_id": "assessment-pair"},
                {"$unset": {"compatibility_sharing.b.couples_adult_at": ""}})
        else:
            await db.users.update_one({"user_id": "b"}, {"$set": {"couples_adult_at": "2026-11-16T01:00:00+00:00"}})
        original = PostgresCollection.find_one

        async def guarded(collection, query=None, projection=None, **kwargs):
            if collection.database is db:
                assert collection.name != "intimacy_answers", "Stale consent must not load intimate answer payloads"
                assert not (collection.name == "users" and projection and projection.get("about") == 1), "Only minimal age/adult metadata is authorized"
            return await original(collection, query, projection, **kwargs)

        monkeypatch.setattr(PostgresCollection, "find_one", guarded)
        result = await compat.view(db, members[1], quiz)
        assert result["sharing"] == {"version": compat.SHARING_VERSION, "revision": 1,
                                     "personality": False, "intimacy": False}
        assert all(not domain["items"] for domain in result["domains"].values())

    run_case(case)


def test_inflight_adult_epoch_change_invalidates_prepared_comparison(monkeypatch):
    async def case(db):
        members, _, quiz, _ = await fixture(db)
        for member in members:
            await compat.set_sharing(db, member, quiz, edit(member["user_id"]))
        original = PostgresCollection.find_one
        changed = False

        async def change_epoch(collection, query=None, projection=None, **kwargs):
            nonlocal changed
            row = await original(collection, query, projection, **kwargs)
            if (not changed and collection.database is db and collection.name == "users"
                    and projection == {"about": 1, "couples_adult_at": 1}):
                changed = True
                await db.users.update_one({"user_id": "a"}, {"$set": {"couples_adult_at": "2026-11-16T02:00:00+00:00"}})
            return row

        monkeypatch.setattr(PostgresCollection, "find_one", change_epoch)
        with pytest.raises(HTTPException) as rejected:
            await compat.view(db, members[0], quiz)
        assert changed and rejected.value.status_code == 409

    run_case(case)


def test_epoch_change_before_sharing_cas_rejects_stale_opt_in(monkeypatch):
    async def case(db):
        members, _, quiz, _ = await fixture(db)
        original = compat.answer_history.flush
        changed = False

        async def change_epoch(database, source, query):
            nonlocal changed
            if source == "couples_pairs" and not changed:
                changed = True
                await db.users.update_one({"user_id": "a"}, {"$set": {"couples_adult_at": "2026-11-16T03:00:00+00:00"}})
            return await original(database, source, query)

        monkeypatch.setattr(compat.answer_history, "flush", change_epoch)
        with pytest.raises(HTTPException) as rejected:
            await compat.set_sharing(db, members[0], quiz, edit("a"))
        assert changed and rejected.value.status_code == 409
        assert not (await db.couples_pairs.find_one({"_id": "assessment-pair"})).get("compatibility_sharing")

    run_case(case)
