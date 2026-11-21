"""An explicit no-sharing choice is durable for exactly the current pair/age epoch."""
import pytest

from backend import couples_compatibility as compat
from backend.tests.test_couples_assessments import edit, fixture
from backend.tests.test_research_notices import run_case


@pytest.mark.parametrize("uid", ["a", "b"])
def test_explicit_decline_is_reviewed_without_sharing_or_duplicate_events(uid):
    async def case(db):
        members, _, quiz, _ = await fixture(db)
        member = next(row for row in members if row["user_id"] == uid)
        body = edit(uid).model_copy(update={"personality": False, "intimacy": False})
        initial = await db.couples_pairs.find_one({"_id": "assessment-pair"})
        who = compat.side(initial, uid)
        assert not compat.sharing_reviewed(initial, who, member)
        result = await compat.set_sharing(db, member, quiz, body)
        assert result["sharing"] == {"version": compat.SHARING_VERSION, "revision": 1,
                                     "personality": False, "intimacy": False}
        pair = await db.couples_pairs.find_one({"_id": "assessment-pair"})
        assert compat.sharing_reviewed(pair, who, member)
        assert all(not scope["items"] for scope in result["domains"].values())
        again = await compat.set_sharing(db, member, quiz, body.model_copy(update={"expected_revision": 1}))
        assert again["sharing"]["revision"] == 1
        assert (await db.couples_pairs.find_one({"_id": "assessment-pair"}))["compatibility_sharing"] == pair["compatibility_sharing"]
        await db.users.update_one({"user_id": uid}, {"$set": {"couples_adult_at": "2026-11-16T10:00:00+00:00"}})
        member = await db.users.find_one({"user_id": uid})
        assert not compat.sharing_reviewed(pair, who, member)
        reviewed = await compat.set_sharing(db, member, quiz, body.model_copy(update={"expected_revision": 1}))
        assert reviewed["sharing"]["revision"] == 2
        assert compat.sharing_reviewed(await db.couples_pairs.find_one({"_id": "assessment-pair"}), who, member)

    run_case(case)
