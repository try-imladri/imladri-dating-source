"""First-use consent on actual isolated PostgreSQL, with no provider calls."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import os
import time
from urllib.parse import urlsplit
from uuid import uuid4
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend import admin_answer_history, answer_history, individual_history, intimacy_answers, research, research_notices as notices, your_type, your_type_research

ENV = "notice-fixture"


def run_case(callback):
    dsn = os.environ.get("IMLADRI_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Requires the owned isolated loopback PostgreSQL fixture.")
    assert urlsplit(dsn).hostname in ("localhost", "127.0.0.1", "::1")
    from backend.postgres_store import PostgresDatabase
    async def exercise():
        db = PostgresDatabase(dsn, "fixture_notice_tests_" + uuid4().hex, allow_local_plaintext=True)
        try:
            await callback(db)
        finally:
            def cleanup(cursor, _now):
                for table in ("rpc_requests", "unique_keys", "indexes", "documents"):
                    cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (db.name,))
            await db._run(cleanup, write="__fixture_cleanup")
            await db.aclose()
    asyncio.run(exercise())


def member(uid="owner", **values):
    return {"user_id": uid, "env": ENV, "role": "member", "about": {"demographics": {"age": 30}}, **values}


def body(scope="questionnaire", decision="allow", **values):
    return notices.Decision(expected_member="owner", notice_version=notices.NOTICES[scope], revision=0,
        permission_revision=0, decision=decision, adult_confirmed=decision == "allow", **values)


def bulk(questionnaire="allow", your_type="allow"):
    return notices.BulkDecision(expected_member="owner", scopes={
        scope: {key: value for key, value in body(scope, decision).model_dump().items() if key != "expected_member"}
        for scope, decision in (("questionnaire", questionnaire), ("your_type", your_type))})


def test_snapshots_have_no_default_grant_and_receipts_validate_scalars():
    initial = notices.snapshot(member())
    assert all(not x["reviewed"] and not x["permission"]["enabled"] for x in initial["scopes"].values())
    valid = {"notice_version": notices.NOTICES["questionnaire"], "decision": "dismiss", "revision": 1,
             "reviewed_at": "2026-11-15T12:00:00+00:00"}
    for changed in ({"decision": ["allow"]}, {"notice_version": [valid["notice_version"]]},
                    {"revision": True}, {"revision": -1}, {"reviewed_at": "yesterday"},
                    {"reviewed_at": "2026-99-99T12:00:00Z"}, {"reviewed_at": [valid["reviewed_at"]]}):
        state = notices.scope_snapshot(member(research_notice_receipts={"questionnaire": {**valid, **changed}}), "questionnaire")
        assert not state["reviewed"] and state["decision"] is None
    assert notices.scope_snapshot(member(research_notice_receipts={"questionnaire": valid}), "questionnaire")["reviewed"]
    with pytest.raises(ValidationError):
        notices.Decision(**{**body().model_dump(), "row_member_version": "row-member-v1"})


@pytest.mark.parametrize("scope", ["questionnaire", "your_type"])
def test_allow_is_atomic_idempotent_and_never_bundles_staff_access(scope):
    async def case(db):
        owner = member(); await db.users.insert_one(deepcopy(owner))
        request = body(scope)
        accepted = await notices.decide(db, owner, ENV, scope, request)
        assert accepted["reviewed"] and accepted["permission"]["enabled"] and accepted["permission"]["revision"] == 1
        row = await db.users.find_one({"user_id": "owner"})
        assert not any(key.startswith(("row_member_", "row_couple_", "answer_history_staff_")) for key in row)
        assert notices.receipt(row, scope)["adult_confirmed"] is True
        count = await db.answer_history.count_documents({"domain": "consent", "question_id": "research_notice." + scope})
        assert count == 1
        again = await notices.decide(db, row, ENV, scope, request)
        assert again == accepted
        assert await db.answer_history.count_documents({"domain": "consent", "question_id": "research_notice." + scope}) == 1
        assert (await db.users.find_one({"user_id": "owner"}))[notices.PERMISSIONS[scope] + "_revision"] == 1
    run_case(case)


def test_dismiss_existing_choice_and_decline_off_never_revoke_or_enrol():
    async def case(db):
        active = member(questionnaire_research_version="all-answers-v1", questionnaire_research_revision=2,
                        questionnaire_research_at="2026-01-01T00:00:00+00:00")
        await db.users.insert_one(deepcopy(active))
        request = notices.Decision(**{**body(decision="dismiss").model_dump(), "permission_revision": 2})
        result = await notices.decide(db, active, ENV, "questionnaire", request)
        assert result["permission"] == {"version": "all-answers-v1", "revision": 2, "enabled": True}
        with pytest.raises(HTTPException) as error:
            await notices.decide(db, active, ENV, "questionnaire", notices.Decision(**{**request.model_dump(), "revision": 1, "decision": "decline"}))
        assert error.value.status_code == 409
        other = member("other"); await db.users.insert_one(deepcopy(other))
        declined = await notices.decide(db, other, ENV, "questionnaire",
            notices.Decision(**{**body(decision="decline").model_dump(), "expected_member": "other"}))
        assert declined["reviewed"] and not declined["permission"]["enabled"] and declined["permission"]["revision"] == 0
    run_case(case)


def test_identity_version_environment_roles_and_known_minor_fail_closed():
    async def case(db):
        owner = member(); await db.users.insert_one(deepcopy(owner))
        for changed in ({"expected_member": "other"}, {"notice_version": "old"}, {"permission_revision": 9}, {"revision": 8}):
            with pytest.raises(HTTPException) as error:
                await notices.decide(db, owner, ENV, "questionnaire", notices.Decision(**{**body().model_dump(), **changed}))
            assert error.value.status_code == 409
        for auth in (member(role="admin"), member(closing=True)):
            with pytest.raises(HTTPException) as error:await notices.decide(db, auth, ENV, "questionnaire", body())
            assert error.value.status_code == 403
        with pytest.raises(HTTPException):await notices.decide(db, owner, "other-env", "questionnaire", body())
        await db.users.update_one({"user_id": "owner"}, {"$set": {"about.demographics.age": 17}})
        with pytest.raises(HTTPException) as error:await notices.decide(db, owner, ENV, "questionnaire", body())
        assert error.value.status_code == 403
        dismissed = await notices.decide(db, owner, ENV, "questionnaire", body(decision="dismiss"))
        assert dismissed["reviewed"] and not dismissed["eligible"] and not dismissed["permission"]["enabled"]
    run_case(case)


def test_racing_allow_and_decline_keep_receipt_and_grant_in_one_CAS():
    async def case(db):
        owner = member(); await db.users.insert_one(deepcopy(owner))
        result = await asyncio.gather(*(notices.decide(db, owner, ENV, "questionnaire", body(decision=x))
                                       for x in ("allow", "decline")), return_exceptions=True)
        assert sum(isinstance(r, dict) for r in result) == 1
        assert sum(isinstance(r, HTTPException) and r.status_code == 409 for r in result) == 1
        row = await db.users.find_one({"user_id": "owner"}); receipt = notices.receipt(row, "questionnaire")
        assert notices.permission(row, "questionnaire")["enabled"] == (receipt["decision"] == "allow")
        assert await db.answer_history.count_documents({"question_id": "research_notice.questionnaire"}) == 1
    run_case(case)


@pytest.mark.parametrize("change", ["age", "closing", "permission"])
def test_concurrent_age_closure_or_settings_change_cannot_partially_grant(monkeypatch, change):
    original = individual_history.save
    async def case(db):
        owner = member(); await db.users.insert_one(deepcopy(owner)); called = False
        async def interleave(*args, **kwargs):
            nonlocal called
            if not called:
                called = True
                fields = {"about.demographics.age": 17} if change == "age" else {"closing": True} if change == "closing" else {"questionnaire_research_revision": 1}
                await db.users.update_one({"user_id": "owner"}, {"$set": fields})
            return await original(*args, **kwargs)
        monkeypatch.setattr(individual_history, "save", interleave)
        with pytest.raises(HTTPException) as error:await notices.decide(db, owner, ENV, "questionnaire", body())
        assert error.value.status_code == 409
        row = await db.users.find_one({"user_id": "owner"})
        assert not notices.receipt(row, "questionnaire") and not notices.permission(row, "questionnaire")["enabled"]
        assert await db.answer_history.count_documents({"question_id": "research_notice.questionnaire"}) == 0
    run_case(case)


def test_lost_response_refresh_reconciles_without_duplicate_grant(monkeypatch):
    original = individual_history.save
    async def case(db):
        owner = member(); await db.users.insert_one(deepcopy(owner))
        async def commit_then_lose(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("fictional lost response after commit")
        monkeypatch.setattr(individual_history, "save", commit_then_lose)
        with pytest.raises(RuntimeError):await notices.decide(db, owner, ENV, "questionnaire", body())
        refreshed = await db.users.find_one({"user_id": "owner"})
        assert notices.snapshot(refreshed)["scopes"]["questionnaire"]["permission"]["enabled"]
        monkeypatch.setattr(individual_history, "save", original)
        assert (await notices.decide(db, refreshed, ENV, "questionnaire", body()))["revision"] == 1
        assert await db.answer_history.count_documents({"question_id": "research_notice.questionnaire"}) == 1
    run_case(case)


def test_partial_intimacy_answer_carries_all_grants_and_private_staff_gate():
    async def case(db):
        at = datetime.now(timezone.utc).isoformat()
        owner = member(answer_history_staff_version="history-staff-v1", answer_history_staff_id="a" * 32,
            answer_history_staff_at=at, answer_history_staff_env=ENV, answer_history_staff_revision=1,
            row_member_version="row-member-v1", row_member_code="b" * 32, row_member_at=at, row_member_env=ENV)
        await db.users.insert_one(deepcopy(owner)); await notices.decide(db, owner, ENV, "questionnaire", body())
        key, variable = next(iter((await intimacy_answers.variables(db)).items()))
        edit = intimacy_answers.AnswerEdit(revision=0, adult_confirmed=True, id=key, topic=variable["topic"],
            question=variable["label"], phrase=variable["phrase"], selection={"choice": research.CHOICES[0], "conditions": []})
        assert (await intimacy_answers.save(db, "owner", edit))["revision"] == 1
        current = await db.users.find_one({"user_id": "owner"})
        event = await db.answer_history.find_one({"domain": "intimacy", "owner_id": "owner"})
        assert event["consent"]["questionnaire_research_at"] == current["questionnaire_research_at"]
        assert event["consent"]["answer_history_staff_id"] == "a" * 32
        assert event["consent"]["row_member_code"] == "b" * 32
        assert admin_answer_history._reason(current, event, ENV, "member") is None
        without_staff = {k: v for k, v in current.items() if not k.startswith("answer_history_staff_")}
        assert admin_answer_history._reason(without_staff, event, ENV, "member") is not None
        assert await db.research_responses.count_documents({}) == 0
    run_case(case)


def test_your_type_attempts_before_grant_remain_private_after_new_attempt_eligible():
    async def case(db):
        owner = member(tier="paid", stripe_subscription_id="sub_fixture", subscription_period_start=int(time.time()) - 60,
                       subscription_period_end=int(time.time()) + 3600)
        await db.users.insert_one(deepcopy(owner))
        before = await your_type.start(db, "owner", your_type.Start(expected_member="owner", request_id=str(uuid4()), audience="women", adult_confirmed=True))
        await notices.decide(db, owner, ENV, "your_type", body("your_type"))
        first = await your_type_research.report(db, {"env": ENV})
        assert first["denominators"].get("attempts", 0) == 0
        after = await your_type.start(db, "owner", your_type.Start(expected_member="owner", request_id=str(uuid4()), audience="women", adult_confirmed=True))
        assert after["id"] != before["id"]
        report = await your_type_research.report(db, {"env": ENV})
        assert report["denominators"]["attempts"] == 1
        current = await db.users.find_one({"user_id": "owner"})
        events = await db.answer_history.find(your_type_research.event_query(current, "owner")).to_list(None)
        assert len(events) == 1 and events[0]["attempt_id"] == after["id"]
    run_case(case)


def test_admin_aggregate_coherent_snapshot_no_answer_or_identity_content():
    async def case(db):
        await db.users.insert_many([member(), member("other"), member("minor", about={"demographics": {"age": 17}}),
                                   member("staff", role="admin"), member("foreign", env="foreign")])
        valid = {"notice_version": notices.NOTICES["questionnaire"], "decision": "allow", "revision": 1,
                 "reviewed_at": "2026-11-15T12:00:00+00:00"}
        malformed = [{"decision": ["allow"]}, {"notice_version": [valid["notice_version"]]},
                     {"revision": True}, {"reviewed_at": "yesterday"},
                     {"reviewed_at": "2026-99-99T12:00:00Z"}, {"reviewed_at": [valid["reviewed_at"]]}]
        for index, change in enumerate(malformed):
            row = member("malformed-" + str(index), research_notice_receipts={"questionnaire": {**valid, **change}})
            assert not notices.scope_snapshot(row, "questionnaire")["reviewed"]
            await db.users.insert_one(row)
        await notices.decide(db, member(), ENV, "questionnaire", body())
        await notices.decide(db, member("other"), ENV, "questionnaire", notices.Decision(**{**body(decision="decline").model_dump(), "expected_member": "other"}))
        for _ in range(3):
            result = await notices.aggregate(db, {"env": ENV})
            assert result["members"] == 3 + len(malformed) and result["eligible_members"] == 2 + len(malformed)
            for scope in result["scopes"].values():
                assert sum(scope["decisions"].values()) == scope["reviewed"]
                assert scope["reviewed"] + scope["unreviewed"] == scope["eligible"] <= result["members"]
            assert result["scopes"]["questionnaire"]["decisions"] == {"allow": 1, "decline": 1, "dismiss": 0}
            assert "user_id" not in str(result) and "about" not in str(result)
    run_case(case)


def test_bulk_two_choices_commit_once_and_exact_replay_is_idempotent():
    async def case(db):
        owner = member(research_notice_receipts=None); await db.users.insert_one(deepcopy(owner))
        request = bulk()
        result = await notices.decide_all(db, owner, ENV, request)
        assert result["welcome_complete"] and all(x["permission"]["enabled"] for x in result["scopes"].values())
        row = await db.users.find_one({"user_id": "owner"})
        assert set(row[notices.FIELD]) == set(notices.NOTICES)
        assert not any(key.startswith(("row_member_", "row_couple_", "answer_history_staff_")) for key in row)
        events = await db.answer_history.find({"kind": "consent_notice"}).to_list(None)
        assert len(events) == 2 and len({e["source_revision"] for e in events}) == 1
        again = await notices.decide_all(db, row, ENV, request)
        assert again == result and await db.answer_history.count_documents({"kind": "consent_notice"}) == 2
        with pytest.raises(ValidationError):notices.BulkDecision(expected_member="owner", scopes={"questionnaire": request.scopes.questionnaire})
        with pytest.raises(ValidationError):notices.BulkDecision(expected_member="owner", scopes={**request.scopes.model_dump(), "staff_exports": {}})
    run_case(case)


def test_bulk_stale_second_choice_leaves_first_grant_and_receipt_untouched():
    async def case(db):
        owner = member(); await db.users.insert_one(deepcopy(owner))
        request = bulk().model_dump(); request["scopes"]["your_type"]["permission_revision"] = 1
        with pytest.raises(HTTPException) as error:await notices.decide_all(db, owner, ENV, notices.BulkDecision(**request))
        assert error.value.status_code == 409
        row = await db.users.find_one({"user_id": "owner"})
        assert not row.get(notices.FIELD) and not row.get("questionnaire_research_version") and not row.get("your_type_research_version")
        assert await db.answer_history.count_documents({"kind": "consent_notice"}) == 0
    run_case(case)


def test_bulk_competing_whole_forms_never_mix_partial_decisions():
    async def case(db):
        owner = member(); await db.users.insert_one(deepcopy(owner))
        result = await asyncio.gather(*(notices.decide_all(db, owner, ENV, request) for request in
                                       (bulk(), bulk("decline", "decline"))), return_exceptions=True)
        assert sum(isinstance(x, dict) for x in result) == 1
        assert sum(isinstance(x, HTTPException) and x.status_code == 409 for x in result) == 1
        row = await db.users.find_one({"user_id": "owner"})
        assert len({row[notices.FIELD][scope]["decision"] for scope in notices.NOTICES}) == 1
        assert notices.permission(row, "questionnaire")["enabled"] == notices.permission(row, "your_type")["enabled"]
        assert await db.answer_history.count_documents({"kind": "consent_notice"}) == 2
    run_case(case)


def test_bulk_reviewed_existing_grant_dismissal_does_not_regrant_or_withdraw():
    async def case(db):
        owner = member(); await db.users.insert_one(deepcopy(owner))
        await notices.decide(db, owner, ENV, "questionnaire", body())
        row = await db.users.find_one({"user_id": "owner"})
        request = bulk("dismiss", "decline").model_dump()
        request["scopes"]["questionnaire"].update(revision=1, permission_revision=1)
        result = await notices.decide_all(db, row, ENV, notices.BulkDecision(**request))
        assert result["welcome_complete"] and result["scopes"]["questionnaire"]["revision"] == 2
        assert result["scopes"]["questionnaire"]["permission"] == {"enabled": True, "version": "all-answers-v1", "revision": 1}
        assert not result["scopes"]["your_type"]["permission"]["enabled"]
    run_case(case)


def test_unexpected_aggregate_result_fails_closed_instead_of_reporting_zero():
    class Cursor:
        async def to_list(self, _):return []
    async def case():
        with pytest.raises(HTTPException) as error:await notices.aggregate(SimpleNamespace(users=SimpleNamespace(aggregate=lambda _:Cursor())), {})
        assert error.value.status_code == 503
    asyncio.run(case())
