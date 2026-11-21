import asyncio
import hashlib
import json
import time
from collections import defaultdict

import httpx
import pytest
from fastapi import HTTPException

from backend import answer_history, intimacy_catalog, research
from backend.quiz import default_quiz
from backend.tests.test_free_plan_quota import run_case


def complete_answers():
    sections = defaultdict(list)
    for section, topic in intimacy_catalog.questions():
        sections[section].append(None)
    return dict(sections)


def rendered_digest(edits):
    sections = defaultdict(list)
    for (section, topic), original in intimacy_catalog.questions().items():
        q, phrase = edits.get((section, topic), original)
        sections[section].append([topic, q, phrase])
    data = [list(research.CHOICES), list(research.CONDITIONS), sorted(sections.items())]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def test_member_sharing_export_withdrawal_and_lifecycle(monkeypatch):
    from backend import server
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_research_fixture")
    uid = "research_member_fixture"

    async def exercise(db):
        monkeypatch.setattr(server, "db", db)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as client:
            path = "/api/me/research"
            assert (await client.get(path)).status_code == 401
            monkeypatch.setitem(server.app.dependency_overrides, server.require_user, lambda: {"user_id": uid})
            q1, q2 = default_quiz()["short"][:2]
            await db.users.insert_one({"user_id": uid, "role": "member", "env": server.ENV, "tier": "paid",
                "stripe_subscription_id": "sub_research_fixture", "subscription_period_start": int(time.time())-3600,
                "subscription_period_end": int(time.time())+3600, "about": {
                    "demographics": {"age": 32, "sex": "Female", "education": "Doctorate", "ethnicity": "Prefer not to say"},
                    "answers": {q1["id"]: {"choice": 0, "note": "unshared private note"},
                                q2["id"]: {"choice": 1, "question": "Previous wording", "label": "Previous choice"}}}})
            initial_response = await client.get(path)
            assert initial_response.headers["Cache-Control"] == "private, no-store"
            initial = initial_response.json()
            assert initial == {"submitted": False, "consent_version": research.CONSENT_VERSION, "revision": 0}
            quiz = (await client.get("/api/quiz")).json()
            assert quiz["research_intimacy_signature"] == rendered_digest({})
            assert await db.research_responses.count_documents({}) == 0
            answers = complete_answers()
            (section, topic), (original, phrase) = next(iter(intimacy_catalog.questions().items()))
            answers[section][0] = {"choice": "It depends", "conditions": ["In private"]}
            body = {"answers": answers, "revision": 0, "consent_version": initial["consent_version"],
                    "catalog_version": quiz["research_catalog_version"], "consent": True, "adult_confirmed": True,
                    "demographic_fields": ["sex", "ethnicity"], "profile_updated_at": None}
            for changes, code in [
                ({"consent": False}, 422), ({"adult_confirmed": False}, 422), ({"consent": 1}, 422),
                ({"consent_version": "old"}, 409), ({"catalog_version": "old"}, 409),
                ({"answers": {**answers, section: answers[section][:-1]}}, 422),
                ({"answers": {**answers, "invented": []}}, 422), ({"user_id": "other"}, 422),
                ({"demographic_fields": ["age_exact"]}, 422), ({"profile_updated_at": "stale"}, 409),
                ({"answers": {**answers, section: [{"choice": "Free text"}, *answers[section][1:]]}}, 422),
                ({"answers": {**answers, section: [{"choice": "Curious", "conditions": ["In private"]}, *answers[section][1:]]}}, 422),
                ({"answers": {**answers, section: [{"choice": "Curious", "note": "private"}, *answers[section][1:]]}}, 422),
            ]:
                assert (await client.put(path, json={**body, **changes})).status_code == code
            assert await db.research_responses.count_documents({}) == 0
            results = await asyncio.gather(*(client.put(path, json=body) for _ in range(2)))
            assert sorted(r.status_code for r in results) == [200, 409]
            status = (await client.get(path)).json()
            assert (status["revision"], status["answered"], status["skipped"], status["everyday_answered"]) == (1, 1, 175, 1)
            assert status["age_band"] == "25-34" and status["demographic_fields"] == ["sex"]
            doc = await db.research_responses.find_one({"_id": uid})
            assert doc["conditions"] == {f"i:{section}:0": ["In private"]}
            assert len(doc["values"]) == len(doc["signatures"]) == 178
            assert doc["values"]["e:" + q1["id"]] == 0 and doc["values"]["d:sex"] == 0
            assert "unshared private note" not in repr(doc) and "Doctorate" not in repr(doc)
            edit = intimacy_catalog.QuestionEdit(section=section, topic=topic, question="Revised question?",
                                                phrase=phrase, revision=0)
            await intimacy_catalog.publish(db, edit)
            new_quiz = (await client.get("/api/quiz")).json()
            assert new_quiz["research_intimacy_signature"] == rendered_digest({(section, topic): (edit.question, phrase)})
            assert new_quiz["research_catalog_version"] != body["catalog_version"]
            assert (await client.put(path, json={**body, "revision": 1})).status_code == 409
            exported = (await client.get("/api/me/export")).json()["research_responses"]
            responses = {question["id"]: question for question in exported[0]["questions"]}
            assert responses[f"i:{section}:0"]["question"] == original
            assert responses[f"i:{section}:0"]["conditions"] == ["In private"]
            assert responses["d:sex"]["answer"] == "Female" and responses["e:" + q1["id"]]["answer"] == q1["options"][0]["label"]
            assert sum(q["skipped"] for q in responses.values()) == 175
            await db.users.update_one({"user_id": uid}, {"$set": {"tier": "unsubscribed"}})
            assert (await client.put(path, json=body)).status_code == 402
            assert (await client.get(path)).json() == status
            withdrawn = (await client.delete(path + "?revision=1")).json()
            assert withdrawn == {**initial, "revision": 2}
            assert (await client.get("/api/me/export")).json()["research_responses"] == []
            assert (await client.delete(path + "?revision=1")).status_code == 409
            await db.users.update_one({"user_id": uid}, {"$set": {"tier": "paid"}})
            body.update(catalog_version=new_quiz["research_catalog_version"], revision=1)
            assert (await client.put(path, json=body)).status_code == 409
            body.update(revision=2, answers=complete_answers(), demographic_fields=[])
            all_skipped = (await client.put(path, json=body)).json()
            assert (all_skipped["answered"], all_skipped["skipped"], all_skipped["demographic_fields"]) == (0, 176, [])
            assert (await client.put("/api/me/about", json={"demographics_patch": {"age": 17}})).status_code == 200
            assert (await client.get(path)).json() == {**initial, "revision": 4}
            assert (await client.put(path, json={**body, "revision": 4})).status_code == 403
            adult = await client.put("/api/me/about", json={"demographics_patch": {"age": ""}})
            assert adult.status_code == 200
            body["profile_updated_at"] = adult.json()["about"]["updated_at"]
            unknown_age = (await client.put(path, json={**body, "revision": 4})).json()
            assert unknown_age["age_band"] is None
            assert (await client.delete("/api/me")).status_code == 200
            assert (await client.get(path)).json() == {**initial, "revision": 6}
            body["profile_updated_at"] = None
            assert (await client.put(path, json={**body, "revision": 5})).status_code == 409
            erased = await db.research_responses.find_one({"_id": uid})
            assert set(erased) - set(answer_history.FIELDS) == {"_id", "user_id", "revision", "submitted"}
            assert not erased.get(answer_history.PENDING) and not erased.get(answer_history.REDACTIONS)
            assert await db.answer_history.count_documents({"source": "research_responses", "source_id": uid}) == 0
            await db.users.update_one({"user_id": uid}, {"$set": {"role": "admin"}})
            assert (await client.put(path, json={**body, "revision": 6})).status_code == 403
            await db.users.update_one({"user_id": uid}, {"$unset": {"stripe_subscription_id": ""}})
            monkeypatch.setattr(server.accounts, "close_billing", lambda *args: None)
            assert (await client.delete("/api/me/account")).status_code == 200
            assert await db.research_responses.find_one({"_id": uid}) is None
    run_case(exercise)


def test_age_correction_during_submission_rejects_before_a_new_record(monkeypatch):
    async def exercise(db):
        uid = "research_age_race_fixture"
        await db.users.insert_one({"user_id": uid, "role": "member", "env": "preview"})
        quiz = default_quiz()
        version, _ = research.catalog(quiz, [])
        body = research.Submission(revision=0, consent_version=research.CONSENT_VERSION,
            catalog_version=version, profile_updated_at=None, consent=True, adult_confirmed=True,
            answers=complete_answers())
        original_save = research.save_revision

        async def change_age_then_save(db, uid, expected, fields):
            await db.users.update_one({"user_id": uid}, {"$set": {"about.demographics.age": 17}})
            await research.clear(db, uid)
            return await original_save(db, uid, expected, fields)

        monkeypatch.setattr(research, "save_revision", change_age_then_save)
        with pytest.raises(HTTPException) as rejected:
            await research.submit(db, uid, "preview", body, quiz, [])
        assert rejected.value.status_code == 403
        assert await db.research_responses.find_one({"_id": uid}) is None
        assert await db.answer_history.count_documents({"source": "research_responses", "source_id": uid}) == 0

    run_case(exercise)
