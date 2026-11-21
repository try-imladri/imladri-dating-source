"""A current pair may withdraw while the comparison rechecks saved versions."""
import pytest
from fastapi import HTTPException

from backend import couples_compatibility as compat
from backend.postgres_store import PostgresCollection
from backend.tests.test_couples_assessments import edit, fixture
from backend.tests.test_research_notices import run_case


@pytest.mark.parametrize("change", ["withdrawal", "unpair"])
def test_final_comparison_authorization_follows_the_last_assessment_fetch(monkeypatch, change):
    async def case(db):
        members, _, quiz, _ = await fixture(db)
        for member in members:
            await compat.set_sharing(db, member, quiz, edit(member["user_id"]))
        before = await compat.view(db, members[0], quiz)
        assert before["domains"]["personality"]["items"]
        original = PostgresCollection.find_one
        triggered = False

        async def concurrent_change(collection, query=None, projection=None, **kwargs):
            nonlocal triggered
            row = await original(collection, query, projection, **kwargs)
            # Initial profile reads include user_id/env. This exact projection
            # identifies the later source-version check, after the first ACL read.
            if (not triggered and collection.database is db and collection.name == "users"
                    and projection == {"about": 1, "couples_adult_at": 1}):
                triggered = True
                if change == "withdrawal":
                    await compat.set_sharing(db, members[1], quiz,
                        edit("b", rev=1, personality=False, intimacy=False))
                else:
                    await db.couples_pairs.update_one({"_id": "assessment-pair", "state": "active"},
                        {"$set": {"state": "closed"}})
            return row

        monkeypatch.setattr(PostgresCollection, "find_one", concurrent_change)
        with pytest.raises(HTTPException) as denied:
            await compat.view(db, members[0], quiz)
        assert triggered and denied.value.status_code == 409
        if change == "withdrawal":
            after = await compat.view(db, members[0], quiz)
            assert all(not value["items"] for value in after["domains"].values())
            assert not (await compat.coach_context(db, members[0], quiz, "assessment-pair"))["comparisons"]
        else:
            with pytest.raises(HTTPException) as closed:
                await compat.view(db, members[0], quiz)
            assert closed.value.status_code == 404
        assert not (await db.users.find_one({"user_id": "a"})).get("questionnaire_research_version")
    run_case(case)
