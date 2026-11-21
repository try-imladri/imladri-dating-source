"""Existing repeat/wording flows also use the unified durable answer journal."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from backend import assessment_evidence as evidence, wording_feedback
from backend.quiz import default_quiz
from backend.tests.test_free_plan_quota import run_case
from backend.tests.test_tone_reporting import member


STAMP = datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_repeat_attempts_are_versioned_once_and_stale_removal_cannot_erase_them():
    async def go(db):
        quiz = default_quiz()
        user = member("history-repeat")
        await db.users.insert_one(user.copy())
        def body(user, at, choice):
            status = evidence.member_view(user, quiz, at)
            return evidence.RepeatSubmission(expected_member=user["user_id"], revision=status["revision"],
                instrument=status["instrument"], phase=status["phase"], consent_revision=status["consent_revision"],
                publish_group_results=True, answers={q["id"]: choice for q in quiz["short"]})
        first = body(user, STAMP, 0)
        one = await evidence.save_repeat(db, user, quiz, first, STAMP)
        assert await evidence.save_repeat(db, user, quiz, first, STAMP) == one
        count = len(quiz["short"])
        assert await db.answer_history.count_documents({"domain": "assessment_repeat"}) == count
        refreshed = await db.users.find_one({"user_id": user["user_id"]})
        second_at = STAMP + timedelta(days=7)
        second = body(refreshed, second_at, 1)
        await evidence.save_repeat(db, refreshed, quiz, second, second_at)
        history = await db.answer_history.find({"domain": "assessment_repeat"}).to_list(100)
        assert len(history) == count * 2
        assert len({row["attempt_id"] for row in history}) == 2
        assert {row["after"]["phase"] for row in history} == {"first", "second"}
        assert all(row["question"]["question"] and row["signature"] for row in history)
        with pytest.raises(HTTPException) as conflict:
            await evidence.remove_repeat(db, refreshed, evidence.RepeatRemoval(expected_member=user["user_id"], revision=1))
        assert conflict.value.status_code == 409
        assert await db.answer_history.count_documents({"domain": "assessment_repeat"}) == count * 2
        latest = await db.users.find_one({"user_id": user["user_id"]})
        await evidence.remove_repeat(db, latest, evidence.RepeatRemoval(expected_member=user["user_id"], revision=2))
        assert await db.answer_history.count_documents({"domain": "assessment_repeat"}) == 0
        for row in history:
            assert (await db.answer_history.find_one({"_id": row["id"]}))["purged"]
    run_case(go)


def test_wording_rating_changes_and_explicit_skips_preserved_until_withdrawal():
    async def go(db):
        user = member("history-wording")
        user["created_at"] = STAMP.isoformat()
        await db.users.insert_one(user.copy())
        edition = await wording_feedback.edition(db)
        def body(revision, conditions, boundaries):
            return wording_feedback.Ratings(owner=user["user_id"], account_created=user["created_at"],
                revision=revision, edition=edition, adult_and_consented=True,
                conditions=conditions, boundaries=boundaries)
        await wording_feedback.update(db, user, body(0, "faithful", None), False, STAMP.isoformat())
        fresh = await db.users.find_one({"user_id": user["user_id"]})
        await wording_feedback.update(db, fresh, body(1, "partly", "faithful"), False, (STAMP + timedelta(minutes=1)).isoformat())
        rows = await db.answer_history.find({"domain": "wording_feedback"}).to_list(10)
        assert len(rows) == 4 and len({row["attempt_id"] for row in rows}) == 2
        assert sum(row["after"]["skipped"] for row in rows) == 1
        with pytest.raises(HTTPException) as stale:
            await wording_feedback.update(db, fresh, body(0, "missed", None), False, STAMP.isoformat())
        assert stale.value.status_code == 409
        assert await db.answer_history.count_documents({"domain": "wording_feedback"}) == 4
        request = wording_feedback.Revision(owner=user["user_id"], account_created=user["created_at"], revision=2)
        await wording_feedback.update(db, fresh, request, True, STAMP.isoformat())
        assert await db.answer_history.count_documents({"domain": "wording_feedback"}) == 0
        assert (await db.users.find_one({"user_id": user["user_id"]})).get("intimacy_wording_review") is None
    run_case(go)
