"""A member can remove their own pair sharing even when product access expires."""
from copy import deepcopy

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend import couples_compatibility as compat, main_consent as main
from backend.postgres_store import PostgresCollection
from backend.tests.test_couples_assessments import edit, fixture
from backend.tests.test_research_notices import run_case


@pytest.mark.parametrize("uid", ["a", "b"])
@pytest.mark.parametrize("condition", ["expired_self_funded", "age_removed"])
def test_own_withdrawal_works_without_funding_or_adult_access(monkeypatch, uid, condition):
    async def case(db):
        members, _, quiz, _ = await fixture(db)
        for person in members:
            await compat.set_sharing(db, person, quiz, edit(person["user_id"]))
        if condition == "expired_self_funded":
            await db.couples_pairs.update_one({"_id": "assessment-pair"}, {"$set": {"self_funded": True}})
        else:
            await db.users.update_one({"user_id": uid}, {"$set": {"about.demographics.age": 17}, "$unset": {"couples_adult_at": ""}})
        member = await db.users.find_one({"user_id": uid})
        state = await main.snapshot(db, member, "preview")
        assert not state["pair_sharing"]["available"] and state["pair_sharing"]["revocable"]
        before = await db.couples_pairs.find_one({"_id": "assessment-pair"})
        other = "b" if uid == "a" else "a"
        other_choice = deepcopy(before["compatibility_sharing"][other])
        original = PostgresCollection.find_one

        async def no_private_assessments(collection, query=None, projection=None, **kwargs):
            if collection.database is db:
                assert collection.name != "intimacy_answers"
                assert not (collection.name == "users" and (projection is None or projection.get("about") == 1))
            return await original(collection, query, projection, **kwargs)

        monkeypatch.setattr(PostgresCollection, "find_one", no_private_assessments)
        result = await main.revoke_partner(db, member, "preview", main.PartnerRevocation(
            expected_member=uid, pair_id="assessment-pair", expected_revision=1,
            notice_version=compat.SHARING_VERSION))
        assert not result["pair_sharing"]["revocable"] and result["pair_sharing"]["revocation_revision"] == 2
        monkeypatch.setattr(PostgresCollection, "find_one", original)
        pair = await db.couples_pairs.find_one({"_id": "assessment-pair"})
        own = pair["compatibility_sharing"][uid]
        assert own["revision"] == 2 and own["personality"] is own["intimacy"] is False
        assert pair["compatibility_sharing"][other] == other_choice
        assert await db.answer_history.count_documents({"actor_id": uid, "kind": "participation_changed",
                                                       "question_id": "couples.assessment-sharing"}) == 1

    run_case(case)


@pytest.mark.parametrize("change", ["other_member", "other_pair", "stale_revision"])
def test_revocation_cannot_touch_another_actor_pair_or_stale_revision(change):
    async def case(db):
        members, _, quiz, _ = await fixture(db)
        await compat.set_sharing(db, members[0], quiz, edit("a"))
        before = await db.couples_pairs.find_one({"_id": "assessment-pair"})
        values = dict(expected_member="a", pair_id="assessment-pair", expected_revision=1, notice_version=compat.SHARING_VERSION)
        values[{"other_member": "expected_member", "other_pair": "pair_id", "stale_revision": "expected_revision"}[change]] = 0 if change == "stale_revision" else "outsider"
        with pytest.raises(HTTPException) as rejected:
            await main.revoke_partner(db, members[0], "preview", main.PartnerRevocation(**values))
        assert rejected.value.status_code in (403, 409)
        assert await db.couples_pairs.find_one({"_id": "assessment-pair"}) == before

    run_case(case)


def test_revocation_schema_has_no_way_to_request_a_new_grant():
    with pytest.raises(ValidationError):
        main.PartnerRevocation(expected_member="a", pair_id="assessment-pair", expected_revision=1,
                               notice_version=compat.SHARING_VERSION, personality=True)


def test_expired_self_funded_member_can_withdraw_through_the_private_http_route(monkeypatch):
    from backend import server

    async def case(db):
        members, _, quiz, _ = await fixture(db)
        await compat.set_sharing(db, members[0], quiz, edit("a"))
        await db.couples_pairs.update_one({"_id": "assessment-pair"}, {"$set": {"self_funded": True}})
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setattr(server, "ENV", "preview")

        async def current():
            return await db.users.find_one({"user_id": "a"})

        server.app.dependency_overrides[server.current_member] = current
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as client:
                response = await client.post("/api/me/main-consent/partner-revocation", json={
                    "expected_member": "a", "pair_id": "assessment-pair", "expected_revision": 1,
                    "notice_version": compat.SHARING_VERSION})
                assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
                assert not response.json()["pair_sharing"]["revocable"]
                assert response.json()["pair_sharing"]["revocation_revision"] == 2
                assert "PRIVATE_NOTE" not in response.text and "PRIVATE_ANSWER_NOTE" not in response.text
        finally:
            server.app.dependency_overrides.clear()

    run_case(case)
