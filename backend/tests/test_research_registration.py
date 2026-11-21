import os
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException
import pytest

from backend import questionnaire_report as qr, research_registration as studies
from backend.quiz import default_quiz
from backend.tests.test_free_plan_quota import run_case


@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires disposable loopback MongoDB.")
def test_registered_comparison_future_cohort_and_current_withdrawal(monkeypatch):
    async def scenario(db):
        stamp=datetime.now(timezone.utc)
        from fastapi import Response
        from backend import server
        monkeypatch.setattr(server, "db", db)
        eligible={**server.IN_ENV,"role":{"$ne":"admin"},"closing":{"$ne":True}}
        form=default_quiz()
        design=studies.Registration(x="age_band",y="d:gender")
        async def insert(prefix, registered):
            users=[]
            for i in range(50):
                users.append({"user_id":prefix+str(i),"env":"preview","role":"member","created_at":registered if i % 2 else registered.astimezone(timezone(timedelta(hours=-5))).isoformat(),
                              "questionnaire_research_version":qr.CONSENT,"about":{"demographics":{
                              "age":22 if i%2 else 40,"gender":"Woman" if i%2 else "Man"}}})
            await db.users.insert_many(users)
        with pytest.raises(HTTPException) as error:
            await studies.register(db,server.ENV,eligible,form,design,stamp.isoformat())
        assert error.value.status_code==422
        await insert("past",stamp-timedelta(days=1))
        comparison = await studies.research_trends.report(db,eligible,form,design.x,design.y)
        design = design.model_copy(update={"expected_instrument": comparison["instrument"]})
        first=await studies.register(db,server.ENV,eligible,form,design,stamp.isoformat())
        again=await studies.register(db,server.ENV,eligible,form,design,(stamp+timedelta(hours=1)).isoformat())
        api_result = await server.register_research_comparison(design, Response(), {})
        listed = await server.research_registrations(Response(), "", {})
        assert api_result == first and listed["items"] == [first]
        assert (await server.research_followup(first["id"], Response(), {}))["state"] == "collecting"
        assert first==again and set(first)=={"id","selection","labels","instrument","registered_at"}
        holding=await studies.follow_up(db,server.ENV,eligible,form,first["id"])
        assert holding=={"state":"collecting","baseline":{"sample_size":50,"cramers_v":1.0}}
        assert (await studies.coverage(db,eligible))=={"available":True,"eligible":50,"participating":50}
        await insert("future",stamp+timedelta(days=1))
        verifying=await studies.follow_up(db,server.ENV,eligible,form,first["id"])
        assert verifying["state"]=="available" and verifying["sample_size"]==50 and verifying["cramers_v"]==1.0
        assert all(row["baseline"] == row["future"] for row in verifying["patterns"])
        await db.users.update_many({"user_id":{"$regex":"^future"}}, [{"$set":{
            "about.demographics.gender":{"$cond":[{"$eq":["$about.demographics.gender","Man"]},"Woman","Man"]}
        }}])
        reversed_group = await studies.follow_up(db,server.ENV,eligible,form,first["id"])
        assert reversed_group["baseline"]["cramers_v"] == reversed_group["cramers_v"] == 1.0
        assert len(reversed_group["patterns"]) == 4
        for row in reversed_group["patterns"]:
            assert row["baseline"]["total"] == row["future"]["total"] == 25
            assert {row["baseline"]["count"], row["future"]["count"]} == {0, 25}
        await db.users.update_one({"user_id":"past0"}, {"$set":{"about.demographics.gender":"Woman"}})
        suppressed = await studies.follow_up(db,server.ENV,eligible,form,first["id"])
        assert suppressed["state"] == "available" and suppressed["baseline"] is None and "patterns" not in suppressed
        await db.users.update_one({"user_id":"past0"}, {"$set":{"about.demographics.gender":"Man"}})
        await db.users.update_many({"user_id":{"$in":["future0","past0"]}},{"$unset":{"questionnaire_research_version":""}})
        assert await studies.follow_up(db,server.ENV,eligible,form,first["id"])=={"state":"collecting","baseline":None}
        assert (await studies.coverage(db,eligible))=={"available":False}
        # Changing the options of a registered instrument invalidates the old comparison.
        from backend import quiz_answers
        monkeypatch.setattr(quiz_answers, "GENDER_OPTIONS", [*quiz_answers.GENDER_OPTIONS, "New category"])
        assert await studies.follow_up(db,server.ENV,eligible,form,first["id"]) == {"state":"changed"}
        with pytest.raises(HTTPException) as stale:
            await studies.register(db,server.ENV,eligible,form,design,stamp.isoformat())
        assert stale.value.status_code == 409
        assert "answers" not in str(await db.research_registrations.find_one({})) and "past0" not in str(first)
    run_case(scenario)


def test_followup_aligns_different_represented_categories_without_a_false_zero_denominator():
    group = lambda ids, cells: {"rows":[{"index":i,"cells":counts} for i,counts in cells],"columns":ids}
    before = group([0,2], [(0,[10,15]),(1,[15,10])])
    after = group([1,2], [(1,[20,10]),(2,[10,10])])
    labels = {"x":{"choices":["a","b","c"]},"y":{"choices":["one","two","three"]}}
    data = {(r["row"],r["column"]):r for r in studies.response_patterns(before, after, labels)}
    assert data["a","one"]["future"] is None and data["c","two"]["baseline"] is None
    assert data["b","one"]["future"] == {"count":0,"total":30}
    assert data["b","one"]["baseline"] == {"count":15,"total":25}
