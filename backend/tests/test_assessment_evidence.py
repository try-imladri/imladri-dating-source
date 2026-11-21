import json
import os
from datetime import datetime, timedelta, timezone

import pytest
import httpx
from fastapi import HTTPException

from backend import assessment_evidence as evidence
from backend import personal_demographics as demographics
from backend.quiz import default_quiz
from backend.tests.test_free_plan_quota import run_case
from backend.tests.test_tone_reporting import ELIGIBLE, member, read, shared_pair

STAMP = datetime(2025, 6, 10, tzinfo=timezone.utc)
mongo = pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires disposable loopback MongoDB.")


def submission(user, quiz, at, choice=0):
    status = evidence.member_view(user, quiz, at)
    return evidence.RepeatSubmission(instrument=status["instrument"], phase=status["phase"], revision=status["revision"],
        expected_member=user["user_id"], consent_revision=status["consent_revision"], publish_group_results=True,
        answers={q["id"]: choice for q in quiz["short"]})


@mongo
def test_two_fresh_sets_window_consent_version_changes_and_withdrawal():
    async def go(db):
        quiz = default_quiz()
        user = member("reader")
        await db.users.insert_one(user.copy())
        first = submission(user, quiz, STAMP)
        assert (await evidence.save_repeat(db, user, quiz, first, STAMP))["phase"] == "waiting"
        assert (await evidence.save_repeat(db, user, quiz, first, STAMP))["revision"] == 1
        conflict = first.model_copy(update={"answers": {key: 1 for key in first.answers}})
        with pytest.raises(HTTPException) as stale:
            await evidence.save_repeat(db, user, quiz, conflict, STAMP)
        assert stale.value.status_code == 409
        saved = await db.users.find_one({"user_id": "reader"})
        week = STAMP + timedelta(days=7)
        second = submission(saved, quiz, week, choice=1)
        with pytest.raises(HTTPException):
            await evidence.save_repeat(db, saved, quiz, second, week - timedelta(seconds=1))
        assert evidence.member_view(saved, quiz, STAMP+timedelta(days=29))["phase"] == "first"
        with pytest.raises(HTTPException) as expired:
            await evidence.save_repeat(db, saved, quiz, second, STAMP + timedelta(days=29))
        assert expired.value.status_code == 409
        unchanged = await db.users.find_one({"user_id": "reader"})
        assert unchanged["assessment_repeat"] == saved["assessment_repeat"]
        published = default_quiz()
        published["short"] = [{**quiz["short"][0], "q": "A revised question"}, *quiz["short"][1:]]
        assert evidence.member_view(saved, published, week)["phase"] == "first"
        with pytest.raises(HTTPException):
            await evidence.save_repeat(db, saved, published, second, week)
        assert (await evidence.save_repeat(db, saved, quiz, second, week))["phase"] == "complete"
        saved = await db.users.find_one({"user_id": "reader"})
        assert (await evidence.save_repeat(db, saved, quiz, second, week))["revision"] == 2
        with pytest.raises(HTTPException):
            await evidence.save_repeat(db, saved, quiz, first, week)
        assert "answers" not in saved["about"]
        assert saved["assessment_repeat"]["second"]["s1"] == quiz["short"][0]["options"][1]["label"]
        assert "first" not in evidence.member_view(saved, quiz, week)
        off = demographics.Participation(version=demographics.CONSENT_VERSION, enabled=False, revision=0)
        await demographics.participate(db, saved, off)
        withdrawn = await db.users.find_one({"user_id": "reader"})
        assert "assessment_repeat" not in withdrawn
        with pytest.raises(HTTPException):
            evidence.member_view(withdrawn, quiz, week)
        with pytest.raises(HTTPException):
            await evidence.save_repeat(db, saved, quiz, second, week)
    run_case(go)


@mongo
def test_return_denominator_excludes_pending_and_restarting_does_not_erase_expiry():
    async def go(db):
        quiz = default_quiz()
        sig = evidence.instrument(quiz)
        today = STAMP+timedelta(days=40)
        for i in range(70):
            user = member("cohort"+str(i))
            if i < 60:
                started = STAMP if i < 40 else today-timedelta(days=2 if i < 50 else 12)
                record = {"instrument":sig,"publish_group_results":True,"first_at":started.isoformat(),"first":{}}
                if i < 20:
                    record["second_at"] = (STAMP+timedelta(days=9)).isoformat()
                user["assessment_repeat"] = record
            await db.users.insert_one(user)
        result = await evidence.enrollment_summary(db, ELIGIBLE, quiz, today)
        assert (result["consenting"],result["first"],result["mature"],result["completion_percent"]) == (70,60,40,50)
        old = await db.users.find_one({"user_id":"cohort25"})
        await evidence.save_repeat(db,old,quiz,submission(old,quiz,today),today)
        assert await evidence.enrollment_summary(db, ELIGIBLE, quiz, today) == result
        await db.users.update_one({"user_id":"cohort60"},{"$unset":{"questionnaire_research_version":""}})
        assert await evidence.enrollment_summary(db, ELIGIBLE, quiz, today) == {"available":False}
    run_case(go)


def test_agreement_kappa_and_internal_consistency_against_known_counts():
    answer = evidence.repeat_item_summary({(0,0): 20, (1,1): 20, (0,1): 10, (1,0): 10})
    assert answer == {"sample": 60, "same_percent": 67, "kappa": 0.33}
    assert evidence.repeat_item_summary({(0,0): 60})["kappa"] is None
    assert evidence.repeat_item_summary({(0,0): 60, (1,0): 1})["sample"] is None
    assert evidence.repeat_item_summary({(0,0): 49})["same_percent"] is None
    # Four rounded cells add to 45, but 50 people completed both sets.
    assert evidence.repeat_item_summary({(0,0): 12, (1,1): 12, (0,1): 12, (1,0): 14})["sample"] == 50
    identical = evidence.ScaleMoments(["one", "two"])
    flat = evidence.ScaleMoments(["one", "two"])
    for i in range(50):
        identical.observe([i%2, i%2])
        flat.observe([1,1])
    assert identical.summary() == {"items": 2, "sample":50, "alpha":1.0}
    assert flat.summary()["alpha"] is None


@mongo
def test_only_compatible_consented_real_members_enter_reliability_and_public_agreement():
    async def go(db):
        quiz = default_quiz()
        sig = evidence.instrument(quiz)
        q0 = quiz["short"][0]
        for i in range(61):
            user = member(str(i))
            choices = {q["id"]: {"question":q["q"], "label":q["options"][i%2]["label"], "note":"PRIVATE"}
                       for s in ("short","extended","further") for q in quiz[s]}
            user["about"]["answers"] = choices
            selected = {q["id"]: q["options"][i%2]["label"] for q in quiz["short"]}
            user["assessment_repeat"] = {"instrument":sig,"publish_group_results":True,"first_at":STAMP.isoformat(),
                "second_at":(STAMP+timedelta(days=8)).isoformat(), "first":selected, "second":selected}
            if i == 60:
                user["role"] = "admin"
                user["assessment_repeat"]["second"] = {**selected,q0["id"]:q0["options"][2]["label"]}
            await db.users.insert_one(user)
        result = await evidence.questionnaire_summary(db, ELIGIBLE, quiz)
        assert result["repeat"][0]["same_percent"] == 100 and result["repeat"][0]["sample"] == 60
        assert all(r["sample"] == 60 for r in result["scales"])
        assert "PRIVATE" not in json.dumps(result)
        await db.users.update_many({"user_id":{"$in":[str(i) for i in range(11)]}}, {"$unset":{"assessment_repeat.publish_group_results":""}})
        assert all(r["sample"] is None for r in await evidence.repeated_items(db, ELIGIBLE, quiz))
        await db.users.update_many({}, {"$set":{"about.demographics.age":17}})
        assert all(s["sample"] is None for s in (await evidence.questionnaire_summary(db, ELIGIBLE, quiz))["scales"])
    run_case(go)


@mongo
def test_usefulness_denominators_and_pair_consent_are_enforced():
    async def go(db):
        now = STAMP
        for i in range(50):
            uid,other = "a"+str(i),"b"+str(i)
            await db.users.insert_many([member(uid),member(other)])
            source = read(uid,i)
            # Members can rate a read within its first day. Production also contains BSON UTC dates.
            source["created_at"] = now if i % 2 else (now-timedelta(minutes=1)).astimezone(timezone(timedelta(hours=3))).isoformat()
            source["tone"]["drafts"] = {"neutral":"PRIVATE-DRAFT"}
            source["tone_feedback"] = [{},{"sent":False},{"sent":True},{"sent":True,"helped":True},
                                       {"sent":True,"helped":False}][i//10]
            await db.exchanges.insert_one(source.copy())
            await db.exchanges.insert_one({**source,"id":source["id"]+"old","created_at":"2025-06-02T00:00:00+00:00"})
            await db.couples_pairs.insert_one(shared_pair(uid,uid,other,{"prompt":{"a":source}}))
        for unit in ["member","couple"]:
            result = await evidence.outcome_summary(db,"preview",ELIGIBLE,unit,now)
            assert result["sample"] == 50 and result["answered"] == 20 and result["helped_percent"] == 50
            assert set(result["groups"].values()) == {10}
            fit = await evidence.fit_summary(db,"preview",ELIGIBLE,unit,now)
            assert fit["groups"]["unanswered"] == 50 and fit["corrected_percent"] is None
            assert "PRIVATE" not in json.dumps(result)
        await db.users.update_one({"user_id":"b1"},{"$unset":{"tone_research_version":""}})
        assert await evidence.outcome_summary(db,"preview",ELIGIBLE,"couple",now) == {"available":False}
        await db.users.update_one({"user_id":"a0"},{"$set":{"tone_research_at":STAMP.isoformat()}})
        assert await evidence.outcome_summary(db,"preview",ELIGIBLE,"member",now) == {"available":False}
    run_case(go)


@mongo
def test_real_http_study_to_private_and_public_reports_export_and_delete(monkeypatch):
    from backend import server

    async def go(db):
        monkeypatch.setattr(server, "db", db)
        user = member("qa-reader")
        current_seconds = int(datetime.now(timezone.utc).timestamp())
        user.update(tier="paid", subscription_status="active", membership_plan_id="plus_100", plan_offer="standard",
                    stripe_subscription_id="sub_assessment_evidence_fixture", stripe_price_id="price_assessment_evidence_fixture",
                    subscription_period_start=current_seconds - 60, subscription_period_end=current_seconds + 3600)
        await db.users.insert_one(user)
        async def current():
            return await db.users.find_one({"user_id": "qa-reader"}, {"_id": 0})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://local") as client:
            for path in ("/api/me/assessment-repeat", "/api/admin/research/evidence"):
                assert (await client.get(path)).status_code == 401
            public = await client.get("/api/public/research/repeat")
            assert public.status_code == 200 and public.json()["repeat"] == []
            assert "no-store" in public.headers["Cache-Control"]
            monkeypatch.setitem(server.app.dependency_overrides, server.current_member, current)
            monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, current)
            uri = "/api/me/assessment-repeat"
            form_response = await client.get(uri)
            assert form_response.status_code == 200, form_response.text
            form = form_response.json()
            payload = {k: form[k] for k in ("phase", "revision", "consent_revision", "instrument")}
            payload.update(expected_member="qa-reader", publish_group_results=True, answers={q["id"]: 0 for q in form["questions"]})
            assert (await client.post(uri, json={**payload,"publish_group_results":False})).status_code == 422
            first = await client.post(uri, json=payload)
            assert first.status_code == 200 and first.json()["phase"] == "waiting"
            assert (await client.post(uri, json=payload)).json() == first.json()
            export = await client.get("/api/me/export")
            assert export.status_code == 200 and export.json()["account"]["assessment_repeat"]["first"]["s1"]
            report = await client.get("/api/admin/research/evidence")
            assert report.status_code == 200 and len(report.json()["questionnaire"]["scales"]) == 5
            assert "first_at" not in report.text and "qa-reader" not in report.text
            assert (await client.get("/api/public/research/repeat")).json()["repeat"] == []
            remove = {"expected_member":"qa-reader","revision":first.json()["revision"]}
            assert (await client.request("DELETE",uri,json={**remove,"revision":0})).status_code == 409
            assert (await client.request("DELETE",uri,json=remove)).status_code == 200
            assert (await client.post(uri, json=payload)).status_code == 409
            assert (await client.get(uri)).json()["phase"] == "first"
            assert "assessment_repeat" not in (await client.get("/api/me/export")).json()["account"]
    run_case(go)


@mongo
def test_report_ignores_future_invalid_and_unconsented_repeat_records():
    async def go(db):
        quiz = default_quiz()
        signature = evidence.instrument(quiz)
        first_at, cutoff = STAMP, STAMP + timedelta(days=10)
        answers = {q["id"]: q["options"][0]["label"] for q in quiz["short"]}
        valid = {"instrument": signature, "first": answers, "second": answers, "first_at": first_at,
                 "second_at": (first_at + timedelta(days=8)).isoformat(), "publish_group_results": True}
        people = []
        for i in range(49):
            people.append({**member(f"valid-{i}"), "assessment_repeat": valid})
        invalid = [{"second_at": (cutoff + timedelta(days=1)).isoformat()}, {"first_at": "not a timestamp"},
                   {"second_at": ["invalid"]}, {"first": []}, {"second": {q: [] for q in answers}},
                   {"publish_group_results": [True]}, {"instrument": [signature]}]
        for i, changed in enumerate(invalid):
            people.append({**member(f"bad-{i}"), "assessment_repeat": {**valid, **changed}})
        people.append({**member("bad-grant"), "assessment_repeat": valid,
                       "questionnaire_research_version": [evidence.questionnaire_report.CONSENT]})
        await db.users.insert_many(people)
        summary = await evidence.questionnaire_summary(db, ELIGIBLE, quiz, cutoff)
        assert all(row["sample"] is None for row in summary["repeat"])
        await db.users.insert_one({**member("last-eligible"), "assessment_repeat": valid})
        summary = await evidence.questionnaire_summary(db, ELIGIBLE, quiz, cutoff)
        assert all(row["sample"] == 50 and row["same_percent"] == 100 for row in summary["repeat"])
    run_case(go)
