"""Actual PostgreSQL ownership, pairing gates and consent-bound comparisons."""
import asyncio
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException, Header

from backend import couples_compatibility as compat, workspace_access, intimacy_answers, quiz_answers, your_type
from backend.quiz import default_quiz
from backend.tests.test_research_notices import run_case


async def fixture(db, *, state="active"):
    quiz = default_quiz()
    members = []
    for uid, index in (("a", 0), ("b", 3)):
        about = {"demographics": {"age": 30}, "describe": uid + "_PRIVATE_NOTE",
                 "answers": {q["id"]: {"choice": index, "question": q["q"], "label": q["options"][index]["label"],
                                            "note": uid + "_PRIVATE_ANSWER_NOTE"}
                             for q in quiz_answers.questions_by_id(quiz).values()}}
        member = {"user_id": uid, "env": "preview", "role": "member", "tier": "unsubscribed", "about": about,
                  "couples_adult_at": "2026-11-01T00:00:00+00:00"}
        if uid == "a":
            member["complimentary_entitlement"] = {"version": "complimentary-unlimited-v1", "id": str(uuid4()), "active": True}
        members.append(member)
    await db.users.insert_many(members)
    pair = {"_id": "assessment-pair", "env": "preview", "state": state, "inviter": "a", "invitee": "b",
            "sponsor": "a", "members": ["a", "b"], "benefit_members": ["a", "b"]}
    await db.couples_pairs.insert_one(pair)
    variables = await intimacy_answers.variables(db)
    keys = list(variables)[:3]
    for uid, values in (("a", [0, 2, None]), ("b", [4, 0, 1])):
        await db.intimacy_answers.insert_one({"_id": uid, "user_id": uid, "revision": 1, "adult_confirmed": True,
            "values": dict(zip(keys, values)), "signatures": {k: variables[k]["signature"] for k in keys},
            "conditions": {keys[1]: ["In fantasy only"]} if uid == "a" else {}})
    return members, pair, quiz, keys


def edit(uid, *, rev=0, personality=True, intimacy=True, pair="assessment-pair"):
    return compat.SharingEdit(pair_id=pair, expected_member=uid, expected_revision=rev,
        personality=personality, intimacy=intimacy, notice_version=compat.SHARING_VERSION)


@pytest.mark.parametrize("state,locked", [("invited", False), ("confirming", False), ("active", True), ("closed", False)])
def test_workspace_lock_follows_only_confirmed_live_pair_for_both_partners(state, locked):
    async def case(db):
        members, _, _, _ = await fixture(db, state=state)
        for member in members:
            access = await workspace_access.snapshot(db, member)
            assert access["couples_only"] is locked and access["individual"] is not locked and access["your_type"] is not locked
            if locked:
                with pytest.raises(HTTPException) as error:
                    await workspace_access.require_individual(db, member)
                assert error.value.status_code == 403 and error.value.detail["code"] == "couples_workspace_required"
                assert (await workspace_access.assessment_member(db, member))["user_id"] == member["user_id"]
    run_case(case)


def test_pair_funding_does_not_mint_individual_or_your_type_access_and_ends_cleanly():
    async def case(db):
        members, _, _, _ = await fixture(db)
        with pytest.raises(HTTPException) as blocked:
            await your_type.eligible(db, "a")
        assert blocked.value.status_code == 403
        await db.users.update_one({"user_id": "a"}, {"$unset": {"complimentary_entitlement": ""}})
        with pytest.raises(HTTPException) as unfunded:
            await workspace_access.assessment_member(db, members[1])
        assert unfunded.value.status_code == 402
        await db.couples_pairs.update_one({"_id": "assessment-pair"}, {"$set": {"state": "closed"}})
        assert not (await workspace_access.snapshot(db, members[1]))["couples_only"]
        with pytest.raises(HTTPException) as no_free_access:
            await workspace_access.assessment_member(db, members[1])
        assert no_free_access.value.status_code == 402
    run_case(case)


def test_comparison_requires_both_scoped_choices_and_never_uses_private_notes_or_your_type():
    async def case(db):
        members, _, quiz, keys = await fixture(db)
        await db.your_type_profiles.insert_one({"_id": "a", "user_id": "a", "private_marker": "NEVER_SHARE_YOUR_TYPE"})
        initial = await compat.view(db, members[0], quiz)
        assert all(d["status"] == "awaiting_sharing" and d["items"] == [] for d in initial["domains"].values())
        first = await compat.set_sharing(db, members[0], quiz, edit("a"))
        assert all(d["status"] == "awaiting_sharing" for d in first["domains"].values())
        second = await compat.set_sharing(db, members[1], quiz, edit("b", intimacy=False))
        assert second["domains"]["personality"]["status"] == "ready"
        assert second["domains"]["intimacy"]["items"] == []
        both = await compat.set_sharing(db, members[1], quiz, edit("b", rev=1))
        assert both["domains"]["intimacy"]["compared_questions"] == 2
        assert all(item["kind"] == "boundary_difference" for item in both["domains"]["intimacy"]["items"])
        payload = str(both)
        assert "PRIVATE_NOTE" not in payload and "PRIVATE_ANSWER_NOTE" not in payload and "NEVER_SHARE_YOUR_TYPE" not in payload
        assert "overall_score" not in both
        context = await compat.coach_context(db, members[0], quiz, "assessment-pair")
        assert context["comparisons"] and "PRIVATE" not in str(context)
        assert any("fantasy" in (r["requesting_partner"] + r["other_partner"]).lower() for r in context["comparisons"])
        assert await db.research_responses.count_documents({}) == 0
        assert not (await db.users.find_one({"user_id": "a"})).get("questionnaire_research_version")
    run_case(case)


def test_withdrawal_answer_changes_catalog_edits_and_skips_invalidate_comparisons():
    async def case(db):
        members, _, quiz, keys = await fixture(db)
        for member in members:
            await compat.set_sharing(db, member, quiz, edit(member["user_id"]))
        before = await compat.view(db, members[0], quiz)
        await db.intimacy_answers.update_one({"_id": "b"}, {"$set": {"values." + keys[0]: None}, "$inc": {"revision": 1}})
        changed = await compat.view(db, members[0], quiz)
        assert changed["context_signature"] != before["context_signature"]
        assert changed["domains"]["intimacy"]["compared_questions"] == 1
        await db.intimacy_answers.update_one({"_id": "b"}, {"$set": {"signatures." + keys[1]: "old-edition"}})
        assert (await compat.view(db, members[0], quiz))["domains"]["intimacy"]["items"] == []
        withdrawn = await compat.set_sharing(db, members[1], quiz, edit("b", rev=1, personality=False, intimacy=False))
        assert all(d["items"] == [] for d in withdrawn["domains"].values())
        assert not (await compat.coach_context(db, members[0], quiz, "assessment-pair"))["comparisons"]
        await db.couples_pairs.update_one({"_id": "assessment-pair"}, {"$set": {"state": "closed"}})
        with pytest.raises(HTTPException): await compat.view(db, members[0], quiz)
    run_case(case)


def test_owner_pair_revision_and_cas_prevent_foreign_or_stale_sharing():
    async def case(db):
        members, _, quiz, _ = await fixture(db)
        for bad in (edit("b"), edit("a", pair="another-pair"), edit("a", rev=4)):
            with pytest.raises(HTTPException) as error: await compat.set_sharing(db, members[0], quiz, bad)
            assert error.value.status_code == 409
        outcomes = await asyncio.gather(*(compat.set_sharing(db, members[0], quiz, edit("a")) for _ in range(2)), return_exceptions=True)
        assert sum(isinstance(r, dict) for r in outcomes) == 1
        assert sum(isinstance(r, HTTPException) and r.status_code == 409 for r in outcomes) == 1
        outsider = {**members[0], "user_id": "outsider"}
        with pytest.raises(HTTPException) as foreign: await compat.view(db, outsider, quiz)
        assert foreign.value.status_code == 404
    run_case(case)


def test_actual_http_routes_keep_guest_answers_owned_and_block_individual_deep_links(monkeypatch):
    from backend import server
    async def case(db):
        members, _, _, _ = await fixture(db)
        monkeypatch.setattr(server, "db", db)
        identity = {"uid": "b"}
        async def current(): return await db.users.find_one({"user_id": identity["uid"]})
        server.app.dependency_overrides[server.current_member] = current
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as client:
                for path in ("/api/people", "/api/overview", "/api/your-type"):
                    response = await client.get(path)
                    assert response.status_code == 403, (path, response.text)
                    assert response.json()["detail"]["code"] == "couples_workspace_required"
                assert (await client.get("/api/quiz")).status_code == 200
                variable = (await intimacy_answers.variables(db))
                intimate_id = list(variable)[0]
                question = variable[intimate_id]
                intimate_save = await client.patch("/api/me/intimacy-answers", json={"revision": 1,
                    "adult_confirmed": True, "id": intimate_id, "topic": question["topic"],
                    "question": question["label"], "phrase": question["phrase"], "selection": {"choice": "Not for me"}})
                assert intimate_save.status_code == 200, intimate_save.text
                assert (await db.intimacy_answers.find_one({"_id": "a"}))["values"][intimate_id] == 0
                own = await client.get("/api/me/about")
                assert own.status_code == 200 and own.json()["about"]["describe"] == "b_PRIVATE_NOTE"
                assert "a_PRIVATE_NOTE" not in own.text
                saved = await client.put("/api/me/about", json={"expected_member": "b", "describe": "My own new description"})
                assert saved.status_code == 200, saved.text
                assert (await db.users.find_one({"user_id": "a"}))["about"]["describe"] == "a_PRIVATE_NOTE"
                me = await client.get("/api/me")
                assert me.status_code == 200 and me.json()["workspace_access"]["couples_only"]
                assert (await client.get("/api/me/export")).status_code == 200
                initial = await client.get("/api/couples/compatibility")
                assert initial.status_code == 200 and initial.headers["cache-control"] == "private, no-store"
                assert (await client.put("/api/couples/compatibility/sharing", json=edit("b").model_dump())).status_code == 200
                assert (await client.get("/api/admin/billing-risk/reviews")).status_code == 403
        finally:
            server.app.dependency_overrides.clear()
    run_case(case)


def test_admin_analytics_counts_sharing_without_exposing_answers_or_enrolling_research():
    from backend.couples_analytics import report
    async def case(db):
        members, _, quiz, _ = await fixture(db)
        for member in members:
            await compat.set_sharing(db, member, quiz, edit(member["user_id"]))
        summary = await report(db, "preview", {"role": "member"}, 30)
        assert summary["assessment_sharing"] == {"active_pairs": 1, "any_sharing_pairs": 1,
            "personality_mutual_pairs": 1, "intimacy_mutual_pairs": 1}
        assert "PRIVATE" not in str(summary)
        for uid in ("a", "b"):
            assert not (await db.users.find_one({"user_id": uid})).get("questionnaire_research_version")
        assert await db.research_responses.count_documents({}) == 0
    run_case(case)


def test_no_mutual_permission_never_loads_either_partners_private_answer_payload(monkeypatch):
    from backend.postgres_store import PostgresCollection
    async def case(db):
        members, _, quiz, _ = await fixture(db)
        original = PostgresCollection.find_one
        async def guarded(collection, query=None, projection=None, **kwargs):
            if collection.database is db:
                assert collection.name != "intimacy_answers"
                assert not (collection.name == "users" and projection and projection.get("about") == 1)
            return await original(collection, query, projection, **kwargs)
        monkeypatch.setattr(PostgresCollection, "find_one", guarded)
        result = await compat.view(db, members[0], quiz)
        assert all(not d["items"] for d in result["domains"].values())
    run_case(case)


def test_pre_upgrade_cached_read_stays_readable_without_shared_context_and_spends_nothing():
    import hashlib
    import json
    from starlette.requests import Request
    from backend import couples_tone
    async def case(db):
        members, _, quiz, _ = await fixture(db)
        text = "Could we choose a grocery day together?"
        lines = [{"speaker": "me", "text": text}]
        intent = "Keep the meaning of my draft and adjust its tone."
        previous_signature = hashlib.sha256(json.dumps([1, "assessment-pair", lines, intent], sort_keys=True).encode()).hexdigest()
        await db.couples_drafts.insert_one({"user_id": "a", "key": "support-1", "version": 1,
            "lines": [text], "tone_coach": {"id": "previously-delivered", "signature": previous_signature,
                "tone": {"status": "insufficient", "drafts": {}}, "tone_feedback": {}, "boundary": False}})
        body = couples_tone.Start(pair_id="assessment-pair", version=1)
        async def never(*args, **kwargs): pytest.fail("Reading a previously delivered result called AI")
        saved = await couples_tone.coach(db, members[0], "private", "support-1", body,
            Request({"type": "http", "headers": []}), never, saved_only=True)
        assert saved["id"] == "previously-delivered"
        assert not (await db.users.find_one({"user_id": "a"})).get("free_reply_requests")
        for member in members:
            await compat.set_sharing(db, member, quiz, edit(member["user_id"], intimacy=False))
        newer_context = await couples_tone.coach(db, members[0], "private", "support-1", body,
            Request({"type": "http", "headers": []}), never, saved_only=True)
        assert newer_context == {"available": False}
    run_case(case)
