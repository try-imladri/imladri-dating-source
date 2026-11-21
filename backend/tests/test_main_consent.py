"""Actual PostgreSQL owner-only comprehensive choices and separate pair phase."""
import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from backend import main_consent as main, personal_demographics, your_type_research
from backend.postgres_store import PostgresCollection
from backend.tests.test_couples_assessments import fixture as pair_fixture
from backend.tests.test_research_notices import run_case


def member(uid="owner", **updates):
    return {"user_id": uid, "env": "preview", "role": "member", "about": {"demographics": {"age": 30}}, **updates}


def decision(state, *, action="save_choices", enable=(), adult=True):
    choices = {scope: {**row["permission"], "enabled": row["eligible"] if action == "agree_all" else scope in enable}
               for scope, row in state["scopes"].items()}
    return main.Decision(expected_member=state["expected_member"], notice_version=state["notice_version"],
        notice_sha256=state["notice_sha256"], revision=state["revision"], expected_pair_id=state["pair_id"],
        request_id=str(uuid4()), action=action, adult_confirmed=adult, choices=choices)


def test_agree_all_is_one_users_mutation_and_creates_only_available_own_grants(monkeypatch):
    async def case(db):
        own = member()
        await db.users.insert_one(own)
        before = await main.snapshot(db, own, "preview")
        assert not before["reviewed"] and all(not r["permission"]["enabled"] for r in before["scopes"].values())
        saves = 0
        original = main.individual_history.save

        async def counted(*args, **kwargs):
            nonlocal saves
            saves += 1
            return await original(*args, **kwargs)

        monkeypatch.setattr(main.individual_history, "save", counted)
        saved = await main.decide(db, own, "preview", decision(before, action="agree_all"))
        assert saves == 1 and saved["revision"] == 1 and saved["welcome_complete"]
        assert all(r["permission"]["enabled"] == r["eligible"] for r in saved["scopes"].values())
        assert not saved["scopes"]["couple_csv"]["permission"]["enabled"]
        assert not saved["scopes"]["couples_return"]["permission"]["enabled"]
        stored = await db.users.find_one({"user_id": "owner"})
        assert stored[main.FIELD]["notice_text"] == main.NOTICE
        assert stored[main.FIELD]["notice_sha256"] == main.NOTICE_SHA
        assert stored[main.FIELD]["action"] == "agree_all"
        assert not stored.get("research_notice_receipts")
        for collection in (db.research_responses, db.intimacy_answers, db.couples_pairs, db.your_type_sessions):
            assert await collection.count_documents({}) == 0
        events = await db.answer_history.find({"question_id": "main_consent"}).to_list(5)
        assert len(events) == 1 and events[0]["domain"] == "consent" and events[0]["kind"] == "consent_notice"

    run_case(case)


def test_all_off_is_explicit_review_without_any_optional_grant_and_replays_once():
    async def case(db):
        own = member()
        await db.users.insert_one(own)
        body = decision(await main.snapshot(db, own, "preview"), adult=False)
        first = await main.decide(db, own, "preview", body)
        assert first["welcome_complete"] and not any(r["permission"]["enabled"] for r in first["scopes"].values())
        before = await db.users.find_one({"user_id": "owner"})
        duplicate = await main.decide(db, own, "preview", body)
        assert duplicate == first and await db.users.find_one({"user_id": "owner"}) == before
        assert await db.answer_history.count_documents({"question_id": "main_consent"}) == 1

    run_case(case)


@pytest.mark.parametrize("scope", main.SCOPES)
def test_one_bad_permission_snapshot_rolls_back_every_choice(scope):
    async def case(db):
        own = member()
        await db.users.insert_one(own)
        body = decision(await main.snapshot(db, own, "preview"), action="agree_all")
        getattr(body.choices, scope).state_sha256 = "0" * 64
        with pytest.raises(HTTPException) as rejected:
            await main.decide(db, own, "preview", body)
        assert rejected.value.status_code == 409
        after = await db.users.find_one({"user_id": "owner"})
        assert main.FIELD not in after and not any(main.permission(after, s, "preview", None)["enabled"] for s in main.SCOPES)

    run_case(case)


@pytest.mark.parametrize("change", ["age", "role", "closing", "env", "boolean_setting"])
def test_concurrent_account_or_settings_change_cannot_install_stale_bulk_grants(monkeypatch, change):
    async def case(db):
        own = member()
        await db.users.insert_one(own)
        body = decision(await main.snapshot(db, own, "preview"), action="agree_all")
        original = main.individual_history.save

        async def changed(*args, **kwargs):
            fields = {"age": {"about.demographics.age": 17}, "role": {"role": "admin"},
                "closing": {"closing": True}, "env": {"env": "live"},
                "boolean_setting": {"everyday_trends_opt_in": True}}[change]
            await db.users.update_one({"user_id": "owner"}, {"$set": fields})
            return await original(*args, **kwargs)

        monkeypatch.setattr(main.individual_history, "save", changed)
        with pytest.raises(HTTPException) as rejected:
            await main.decide(db, own, "preview", body)
        assert rejected.value.status_code == 409
        after = await db.users.find_one({"user_id": "owner"})
        assert main.FIELD not in after and not after.get("questionnaire_research_version")

    run_case(case)


def test_concurrent_whole_forms_accept_one_and_never_partially_merge_choices():
    async def case(db):
        own = member()
        await db.users.insert_one(own)
        state = await main.snapshot(db, own, "preview")
        both = await asyncio.gather(main.decide(db, own, "preview", decision(state, enable=("tone",))),
            main.decide(db, own, "preview", decision(state, enable=("questionnaire",))), return_exceptions=True)
        assert sum(isinstance(r, dict) for r in both) == 1
        assert sum(isinstance(r, HTTPException) and r.status_code == 409 for r in both) == 1
        current = await db.users.find_one({"user_id": "owner"})
        assert bool(current.get("tone_research_version")) != bool(current.get("questionnaire_research_version"))

    run_case(case)


def test_unknown_age_defers_staff_and_minor_cannot_enable_any_optional_scope():
    async def case(db):
        for uid, age in (("unknown", None), ("minor", 17)):
            own = member(uid, about={"demographics": {"age": age}})
            await db.users.insert_one(own)
            state = await main.snapshot(db, own, "preview")
            assert not state["scopes"]["staff_history"]["eligible"]
            if uid == "minor":
                assert not any(r["eligible"] for r in state["scopes"].values())
            accepted = await main.decide(db, own, "preview", decision(state, action="agree_all"))
            assert accepted["welcome_complete"] and not accepted["scopes"]["staff_history"]["permission"]["enabled"]
            if uid == "minor":
                assert not any(r["permission"]["enabled"] for r in accepted["scopes"].values())

    run_case(case)


def test_pair_csv_does_not_grant_partner_or_pair_answer_sharing_and_new_pair_needs_review():
    async def case(db):
        members, _, _, _ = await pair_fixture(db)
        state = await main.snapshot(db, members[0], "preview")
        assert state["pair_sharing"]["available"] and not state["pair_sharing"]["reviewed"]
        saved = await main.decide(db, members[0], "preview", decision(state, action="agree_all"))
        assert saved["welcome_complete"] and not saved["pair_sharing"]["reviewed"]
        partner = await db.users.find_one({"user_id": "b"})
        assert not partner.get("row_couple_version") and not partner.get("questionnaire_research_version")
        assert not (await db.couples_pairs.find_one({"_id": "assessment-pair"})).get("compatibility_sharing")
        await db.couples_pairs.update_one({"_id": "assessment-pair"}, {"$set": {"state": "closed"}})
        updated = await main.snapshot(db, await db.users.find_one({"user_id": "a"}), "preview")
        assert not updated["welcome_complete"] and not updated["scopes"]["couple_csv"]["permission"]["enabled"]

    run_case(case)


def test_pair_change_during_bulk_save_leaves_only_dormant_old_pair_grant(monkeypatch):
    async def case(db):
        members, _, _, _ = await pair_fixture(db)
        body = decision(await main.snapshot(db, members[0], "preview"), action="agree_all")
        original = main.individual_history.save

        async def unpaired(*args, **kwargs):
            await db.couples_pairs.update_one({"_id": "assessment-pair"}, {"$set": {"state": "closed"}})
            return await original(*args, **kwargs)

        monkeypatch.setattr(main.individual_history, "save", unpaired)
        with pytest.raises(HTTPException) as changed:
            await main.decide(db, members[0], "preview", body)
        assert changed.value.status_code == 409
        current = await db.users.find_one({"user_id": "a"})
        assert current["row_couple_pair_id"] == "assessment-pair"
        state = await main.snapshot(db, current, "preview")
        assert not state["welcome_complete"] and not state["scopes"]["couple_csv"]["permission"]["enabled"]

    run_case(case)


def test_couples_return_start_is_preserved_until_withdrawal_then_restarts_prospectively():
    async def case(db):
        own = member(couples_adult_at="2026-11-01T00:00:00+00:00", couples_preferences={
            "review_return_opt_in": True, "review_return_since": "2026-11-03T00:00:00+00:00"})
        await db.users.insert_one(own)
        await main.decide(db, own, "preview", decision(await main.snapshot(db, own, "preview"), action="agree_all"))
        current = await db.users.find_one({"user_id": "owner"})
        assert current["couples_preferences"]["review_return_since"] == "2026-11-03T00:00:00+00:00"
        await main.decide(db, current, "preview", decision(await main.snapshot(db, current, "preview")))
        current = await db.users.find_one({"user_id": "owner"})
        assert "review_return_since" not in current["couples_preferences"]
        await main.decide(db, current, "preview", decision(await main.snapshot(db, current, "preview"), enable=("couples_return",)))
        current = await db.users.find_one({"user_id": "owner"})
        assert current["couples_preferences"]["review_return_since"] > "2026-11-03T00:00:00+00:00"

    run_case(case)


def test_get_never_reads_private_answers_and_admin_counts_separate_receipts_from_current_grants(monkeypatch):
    async def case(db):
        own, other = member(), member("other")
        await db.users.insert_many([own, other])
        original = PostgresCollection.find_one

        async def guarded(collection, query=None, projection=None, **kwargs):
            if collection.database is db:
                assert collection.name not in ("intimacy_answers", "your_type_sessions", "couples_drafts")
                assert not (collection.name == "users" and projection and projection.get("about") == 1)
            return await original(collection, query, projection, **kwargs)

        monkeypatch.setattr(PostgresCollection, "find_one", guarded)
        state = await main.snapshot(db, own, "preview")
        await main.decide(db, own, "preview", decision(state, action="agree_all"))
        current = await db.users.find_one({"user_id": "owner"})
        await personal_demographics.participate(db, current, personal_demographics.Participation(
            expected_member="owner", enabled=False, version="all-answers-v1", revision=1))
        updated = await db.users.find_one({"user_id": "owner"})
        assert (await main.snapshot(db, updated, "preview"))["welcome_complete"]
        counts = await main.aggregate(db, {"env": "preview"}, "preview")
        assert counts["members"] == 2 and counts["reviewed"] == 1 and counts["unreviewed"] == 1
        assert counts["scopes"]["questionnaire"] == {"eligible": 2, "enabled": 0, "reviewed": 1,
            "unreviewed": 1, "decisions": {"allow": 1, "decline": 0, "unavailable": 0}}
        assert "user_id" not in str(counts) and "member_id" not in str(counts)

    run_case(case)


def test_stale_actor_age_snapshot_never_blocks_main_notice_or_loads_private_answers():
    async def case(db):
        members, _, _, _ = await pair_fixture(db)
        await personal_demographics.save_own(db, "a", {"age": 17})
        # A request that started before the correction may retain the old actor.
        state = await main.snapshot(db, members[0], "preview")
        assert state["pair_sharing"]["available"] is False
        assert state["pair_sharing"]["reviewed"] is False

    run_case(case)


def test_missing_extra_or_nonboolean_scope_cannot_form_bulk_body():
    own = member()
    state = main.owner_snapshot(own, "preview", None)
    payload = decision(state).model_dump()
    for change in ("missing", "extra", "nonboolean"):
        broken = deepcopy(payload)
        if change == "missing":
            del broken["choices"]["tone"]
        elif change == "extra":
            broken["choices"]["future_matchmaking"] = broken["choices"]["tone"]
        else:
            broken["choices"]["tone"]["enabled"] = 1
        with pytest.raises(ValidationError):
            main.Decision.model_validate(broken)


@pytest.mark.parametrize("tamper", ["owner", "notice_hash", "pair", "revision", "all_action"])
def test_owner_notice_pair_revision_and_all_action_are_bound(tamper):
    async def case(db):
        own = member()
        await db.users.insert_one(own)
        body = decision(await main.snapshot(db, own, "preview"), action="agree_all")
        if tamper == "owner":
            body.expected_member = "different-member"
        elif tamper == "notice_hash":
            body.notice_sha256 = "0" * 64
        elif tamper == "pair":
            body.expected_pair_id = "new-pair"
        elif tamper == "revision":
            body.revision = 4
        else:
            body.choices.questionnaire.enabled = False
        with pytest.raises(HTTPException) as rejected:
            await main.decide(db, own, "preview", body)
        assert rejected.value.status_code in (403, 409)
        assert main.FIELD not in await db.users.find_one({"user_id": "owner"})

    run_case(case)


def test_actual_http_main_routes_admin_guards_and_later_settings_withdrawal(monkeypatch):
    from backend import server

    async def case(db):
        await db.users.insert_many([member(), {"user_id": "staff", "role": "admin", "env": "preview"}])
        monkeypatch.setattr(server, "db", db)
        identity = {"uid": "owner"}

        async def current():
            return await db.users.find_one({"user_id": identity["uid"]})

        server.app.dependency_overrides[server.current_member] = current
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as client:
                initial = await client.get("/api/me/main-consent")
                assert initial.status_code == 200 and initial.headers["cache-control"] == "private, no-store"
                body = decision(initial.json(), action="agree_all")
                accepted = await client.patch("/api/me/main-consent", json=body.model_dump())
                assert accepted.status_code == 200 and accepted.json()["welcome_complete"]
                assert (await client.get("/api/admin/main-consent")).status_code == 403
                for path, payload in (
                    ("/api/me/questionnaire-research", {"version": "all-answers-v1", "revision": 1}),
                    ("/api/your-type/research", {"version": "your-type-research-v2", "revision": 1}),
                    ("/api/me/research-exports/member", {"version": "row-member-v1", "revision": 1}),
                    ("/api/me/answer-history-permission", {"version": "history-staff-v1", "revision": 1}),
                ):
                    response = await client.patch(path, json={**payload, "expected_member": "owner", "enabled": False})
                    assert response.status_code == 200, (path, response.text)
                refreshed = await client.get("/api/me/main-consent")
                assert refreshed.status_code == 200 and refreshed.json()["welcome_complete"]
                assert all(not refreshed.json()["scopes"][key]["permission"]["enabled"] for key in (
                    "questionnaire", "your_type", "member_csv", "staff_history"))
                # A lost earlier response cannot replay an opt-in over later withdrawals.
                replay = await client.patch("/api/me/main-consent", json=body.model_dump())
                assert replay.status_code == 409
                identity["uid"] = "staff"
                counts = await client.get("/api/admin/main-consent")
                assert counts.status_code == 200 and counts.headers["cache-control"] == "private, no-store"
                assert counts.json()["members"] == 1 and counts.json()["scopes"]["staff_history"]["enabled"] == 0
                assert (await client.patch("/api/me/main-consent", json=body.model_dump())).status_code == 403
        finally:
            server.app.dependency_overrides.clear()

    run_case(case)


@pytest.mark.parametrize("corrupt", ["missing_version", "wrong_version", "boolean_revision", "revision_jump",
    "enabled_decline", "notice_text", "timestamp", "request_uuid", "request_sha", "states", "receipt_revision", "action"])
def test_incomplete_or_corrupt_receipt_is_never_a_completed_welcome(corrupt):
    async def case(db):
        own = member()
        await db.users.insert_one(own)
        await main.decide(db, own, "preview", decision(await main.snapshot(db, own, "preview"), action="agree_all"))
        current = await db.users.find_one({"user_id": "owner"})
        receipt = deepcopy(current[main.FIELD])
        scope = receipt["choices"]["questionnaire"]
        if corrupt == "missing_version":
            del scope["version"]
        elif corrupt == "wrong_version":
            scope["version"] = "old-version"
        elif corrupt == "boolean_revision":
            scope["permission_revision_before"] = False
        elif corrupt == "revision_jump":
            scope["permission_revision_after"] = 3
        elif corrupt == "enabled_decline":
            scope["decision"] = "decline"
        elif corrupt == "notice_text":
            receipt["notice_text"] = "Different terms"
        elif corrupt == "timestamp":
            receipt["reviewed_at"] = "2026-99-99T00:00:00Z"
        elif corrupt == "request_uuid":
            receipt["request_id"] = "not-a-request-uuid"
        elif corrupt == "request_sha":
            receipt["request_sha256"] = "x"
        elif corrupt == "states":
            del receipt["permission_states_after"]["tone"]
        elif corrupt == "receipt_revision":
            receipt["revision"] = True
        else:
            receipt["action"] = "implicitly-opted-in"
        await db.users.update_one({"user_id": "owner"}, {"$set": {main.FIELD: receipt}})
        current = await db.users.find_one({"user_id": "owner"})
        view = await main.snapshot(db, current, "preview")
        assert not view["reviewed"] and not view["welcome_complete"]
        assert view["scopes"]["questionnaire"]["permission"]["enabled"]  # Receipt corruption is not withdrawal.
        counts = await main.aggregate(db, {"env": "preview"}, "preview")
        assert counts["reviewed"] == 0 and counts["scopes"]["questionnaire"]["enabled"] == 1

    run_case(case)
