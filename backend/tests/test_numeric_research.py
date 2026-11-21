import os
from statistics import correlation

import pytest

from backend import questionnaire_report, research_trends
from backend.quiz import default_quiz
from backend.tests.test_free_plan_quota import run_case


@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires disposable loopback MongoDB")
def test_numeric_member_and_partner_correlations_and_disclosure():
    async def check(db):
        age, height = questionnaire_report.AGE, questionnaire_report.HEIGHT
        quiz = default_quiz()
        members, pairs, observations = [], [], []
        for i in range(80):
            first_age = (20 if i < 40 else 50) + i % 3
            second_age = (30 if i < 40 else 60) + i % 3
            first_height, second_height = 284 - 2 * second_age, 105 + 2 * first_age
            left, right = "left-" + str(i), "right-" + str(i)
            for uid, years, cm in [(left, first_age, first_height), (right, second_age, second_height)]:
                members.append({"user_id": uid, "role": "member", "env": "test", "couples_adult_at": "confirmed",
                    "questionnaire_research_version": questionnaire_report.CONSENT,
                    "about": {"demographics": {"age": years, "height_cm": cm}}})
            pairs.append({"_id": str(i), "env": "test", "state": "active", "inviter": left, "invitee": right,
                          "members": [left, right]})
            observations.append(((first_age, first_height), (second_age, second_height)))
        await db.users.insert_many(members)
        await db.couples_pairs.insert_many(pairs)
        # Same-person and the two directed comparisons reference different people and different quantities.
        async def report(unit, x=age, y=height):
            return await research_trends.report(db, {"env": "test"}, quiz, x, y, unit=unit)

        def expected(points, n):
            x, y = zip(*points)
            return {"r": round(correlation(x, y), 3), "sample_size": n}

        personal = [v for pair in observations for v in pair]
        forwards = [(a[0], b[1]) for a, b in observations]
        backwards = [(b[0], a[1]) for a, b in observations]
        for unit, values, n in [("member", personal, 160), ("couple_inviter", forwards, 80),
                                ("couple_invitee", backwards, 80), ("couple", forwards + backwards, 80)]:
            data = await report(unit)
            assert data["available"] and data["pearson"] == expected(values, n)
        assert expected(forwards, 80)["r"] == 1
        assert expected(backwards, 80)["r"] == -1
        assert (await report("couple_inviter", age, age))["pearson"]["r"] == 1

        # Missing one orientation still contributes one full vote for each couple.
        await db.users.update_many({"user_id": {"$in": ["left-" + str(i) for i in range(10)]}},
                                   {"$unset": {"about.demographics.age": ""}})
        symmetric = await report("couple")
        assert symmetric["pearson"] == expected(forwards[10:] + backwards + backwards[:10], 80)
        # The table can still show legacy digit strings in bands. Only actual integer
        # measurements are used for r, and a cell of nine suppresses that whole subset.
        for i in range(10, 31):
            await db.users.update_one({"user_id": "left-" + str(i)},
                                      {"$set": {"about.demographics.age": str(observations[i][0][0])}})
        hidden = await report("couple_inviter")
        assert hidden["available"] and hidden["pearson"] is None
        # A second published band can come solely from historical numeric strings.
        # With one remaining exact height there is no linear correlation to release.
        await db.users.update_many({"user_id": {"$regex": "^left-"}}, {"$set": {
            "about.demographics.height_cm": 162, "about.demographics.age": 23}})
        await db.users.update_many({"user_id": {"$regex": "^right-"}}, {"$set": {
            "about.demographics.height_cm": "184", "about.demographics.age": 48}})
        constant = await report("member")
        assert constant["available"] and constant["pearson"] is None
        await db.users.update_many({}, {"$unset": {"questionnaire_research_version": ""}})
        empty = await report("member")
        assert not empty["available"] and empty["pearson"] is None
    run_case(check)
