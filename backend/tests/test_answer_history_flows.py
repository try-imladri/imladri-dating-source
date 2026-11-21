"""Real Individual/research mutations retain attempts and honor scoped erasure."""
import asyncio
from collections import defaultdict

import pytest
from fastapi import HTTPException

from backend import answer_context, answer_history, intimacy_answers, intimacy_catalog, personal_demographics, research
from backend.quiz import default_quiz
from backend.tests.test_free_plan_quota import run_case


def test_intimacy_changes_skips_editions_and_erasure_have_complete_history():
    async def go(db):
        uid = "history-individual"
        await db.users.insert_one({"user_id": uid, "role": "member", "env": "preview",
            "questionnaire_research_version": "all-answers-v1", "about": {"demographics": {"age": 30, "sex": "Male"}}})
        variables = await intimacy_answers.variables(db)
        key, variable = next(iter(variables.items()))
        def body(revision, choice):
            return intimacy_answers.AnswerEdit(revision=revision, adult_confirmed=True, id=key,
                topic=variable["topic"], question=variable["label"], phrase=variable["phrase"],
                selection=research.Selection(choice=choice) if choice else None)
        assert await intimacy_answers.save(db, uid, body(0, "Curious")) == {"revision": 1}
        with pytest.raises(HTTPException) as stale:
            await intimacy_answers.save(db, uid, body(0, "Not for me"))
        assert stale.value.status_code == 409
        assert await intimacy_answers.save(db, uid, body(1, "Interested")) == {"revision": 2}
        assert await intimacy_answers.save(db, uid, body(2, "Interested")) == {"revision": 2}
        assert await intimacy_answers.save(db, uid, body(2, None)) == {"revision": 3}
        rows = await db.answer_history.find({"source": "intimacy_answers", "owner_id": uid}).sort("source_revision", 1).to_list(10)
        assert len(rows) == 3
        assert [r["after"]["answer"] for r in rows] == ["Curious", "Interested", None]
        assert rows[0]["before_exists"] is False
        assert rows[1]["before"]["answer"] == "Curious"
        assert rows[2]["after"]["skipped"] is True
        assert all(r["question"]["question"] == variable["label"] for r in rows)
        assert all(set(r["demographics"]) == {"snapshot_id", "observed_at"} for r in rows)
        assert len({r["demographics"]["snapshot_id"] for r in rows}) == 1
        assert all("age" not in repr(r["demographics"]) and "sex" not in repr(r["demographics"]) for r in rows)
        second, v2 = next((k, v) for k, v in variables.items() if k != key)
        second_body = intimacy_answers.AnswerEdit(revision=3, adult_confirmed=True, id=second,
            topic=v2["topic"], question=v2["label"], phrase=v2["phrase"], selection=research.Selection(choice="Curious"))
        await intimacy_answers.save(db, uid, second_body)
        assert await intimacy_answers.remove_one(db, uid, key, 4) == {"revision": 5}
        assert await db.answer_history.count_documents({"source": "intimacy_answers", "question_id": key}) == 0
        assert await db.answer_history.count_documents({"source": "intimacy_answers", "question_id": second}) == 1
        for old in rows:
            tombstone = await db.answer_history.find_one({"_id": old["id"]})
            assert set(tombstone) == {"_id", "id", "purged", "purged_at"}
        assert await intimacy_answers.clear(db, uid, revision=5) == {"revision": 6}
        assert await db.answer_history.count_documents({"source": "intimacy_answers"}) == 0
    run_case(go)


def test_each_same_edition_research_completion_is_distinct_and_withdrawal_erases_history():
    async def go(db):
        uid = "history-research"
        await db.users.insert_one({"user_id": uid, "role": "member", "env": "preview",
            "about": {"demographics": {"age": 34, "sex": "Female", "ethnicity": "Other"}}})
        quiz = default_quiz()
        version, _ = research.catalog(quiz, [])
        answers = defaultdict(list)
        for section, topic in intimacy_catalog.questions():
            answers[section].append(None)
        def submission(revision):
            return research.Submission(revision=revision, consent_version=research.CONSENT_VERSION,
                catalog_version=version, profile_updated_at=None, consent=True, adult_confirmed=True,
                answers=dict(answers), demographic_fields=["sex"])
        await research.submit(db, uid, "preview", submission(0), quiz, [])
        section = next(iter(answers))
        answers[section][0] = research.Selection(choice="Curious")
        outcomes = await asyncio.gather(research.submit(db, uid, "preview", submission(1), quiz, []),
                                        research.submit(db, uid, "preview", submission(1), quiz, []),
                                        return_exceptions=True)
        assert sum(isinstance(item, HTTPException) and item.status_code == 409 for item in outcomes) == 1
        rows = await db.answer_history.find({"source": "research_responses", "owner_id": uid}).sort("source_revision", 1).to_list(10)
        assert len(rows) == 2 and len({row["attempt_id"] for row in rows}) == 2
        assert rows[0]["after"]["catalog_version"] == rows[1]["after"]["catalog_version"] == version
        assert rows[0]["after"]["values"][f"i:{section}:0"] is None
        assert rows[1]["after"]["values"][f"i:{section}:0"] == research.CHOICES.index("Curious")
        assert all("age_band" not in row["after"] and not any(key.startswith("d:") for key in row["after"]["values"]) for row in rows)
        assert rows[0]["after"]["demographic_fields"] == ["sex"]
        assert all(row["visibility"] == "research" and row["consent"]["submitted"] for row in rows)
        await research.withdraw(db, uid, 2)
        assert await db.answer_history.count_documents({"source": "research_responses"}) == 0
        for row in rows:
            assert (await db.answer_history.find_one({"_id": row["id"]}))["purged"]
        current = await db.research_responses.find_one({"_id": uid})
        assert current["submitted"] is False and not current.get(answer_history.PENDING) and not current.get(answer_history.REDACTIONS)
    run_case(go)


def test_unversioned_privacy_clear_retries_a_concurrent_accepted_write():
    async def go(db):
        uid = "clear-race"
        await db.intimacy_answers.insert_one({"_id": uid, "revision": 0})
        first = answer_history.event(source="intimacy_answers", source_id=uid, owner_id=uid,
            domain="intimacy", question_id="q1", after={"choice": 1})
        row = await db.intimacy_answers.find_one({"_id": uid})
        await db.intimacy_answers.update_one(answer_history.guard({"_id": uid}, row),
            answer_history.append({"$set": {"values.q1": 1}, "$inc": {"revision": 1}}, first, row))
        class Collection:
            raced = False
            def __getattr__(self, key):
                return getattr(db.intimacy_answers, key)
            async def find_one_and_update(self, *args, **kwargs):
                if not self.raced:
                    self.raced = True
                    current = await db.intimacy_answers.find_one({"_id": uid})
                    second = answer_history.event(source="intimacy_answers", source_id=uid, owner_id=uid,
                        domain="intimacy", question_id="q1", after={"choice": 2})
                    await db.intimacy_answers.update_one(answer_history.guard({"_id": uid}, current),
                        answer_history.append({"$set": {"values.q1": 2}, "$inc": {"revision": 1}}, second, current))
                return await db.intimacy_answers.find_one_and_update(*args, **kwargs)
        source = Collection()
        class Database:
            answer_history = db.answer_history
            intimacy_answers = source
            def __getitem__(self, key):
                return source if key == "intimacy_answers" else db[key]
        assert await intimacy_answers.clear(Database(), uid) == {"revision": 3}
        final = await db.intimacy_answers.find_one({"_id": uid})
        assert "values" not in final and not final.get(answer_history.PENDING) and not final.get(answer_history.REDACTIONS)
        assert await db.answer_history.count_documents({"purged": True}) == 2
    run_case(go)


def test_age_correction_after_intimacy_source_read_rejects_before_cas(monkeypatch):
    async def go(db):
        uid = "age-correction-before-answer-cas"
        await db.users.insert_one({"user_id": uid, "role": "member", "env": "preview",
                                  "about": {"demographics": {"age": 30}}})
        key, variable = next(iter((await intimacy_answers.variables(db)).items()))
        body = intimacy_answers.AnswerEdit(revision=0, adult_confirmed=True, id=key,
            topic=variable["topic"], question=variable["label"], phrase=variable["phrase"],
            selection=research.Selection(choice="Curious"))
        original = answer_context.for_member
        async def correct_age_after_source_read(db, owner):
            reference = await original(db, owner)
            await personal_demographics.save_own(db, uid, {"age": 17})
            return reference
        monkeypatch.setattr(answer_context, "for_member", correct_age_after_source_read)
        with pytest.raises(HTTPException) as rejected:
            await intimacy_answers.save(db, uid, body)
        assert rejected.value.status_code == 403
        source = await db.intimacy_answers.find_one({"_id": uid})
        assert source and not source.get("values") and not source.get(answer_history.PENDING)
        assert await db.answer_history.count_documents({"source": "intimacy_answers", "source_id": uid}) == 0
    run_case(go)
