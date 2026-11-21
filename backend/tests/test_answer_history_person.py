"""Third-party intake history stays private and honors normal conflict/erase paths."""
import pytest
from fastapi import HTTPException
import time
import json

from backend import answer_history, person_answer_history
from backend.tests.test_free_plan_quota import run_case


def test_person_intake_changes_are_recorded_without_first_party_research_misclassification():
    async def go(db):
        member = {"user_id": "intake-owner", "role": "member", "env": "preview",
                  "questionnaire_research_version": "all-answers-v1"}
        await db.users.insert_one({**member, "about": {"demographics": {"age": 30}}})
        await db.people.insert_one({"id": "person", "user_id": member["user_id"], "intake": {}})
        owner = {"id": "person", "user_id": member["user_id"]}
        labels = {"communication": "How do they communicate?"}
        for text in ("They often call after work.", "They prefer a message before a call."):
            await person_answer_history.save(db, "person", member, owner,
                {"intake.communication": text}, labels, answer_history.now())
        await person_answer_history.save(db, "person", member, owner,
            {"intake.communication": "They prefer a message before a call."}, labels, answer_history.now())
        rows = await db.answer_history.find({"source": "people"}).sort("source_revision", 1).to_list(10)
        assert len(rows) == 2
        assert rows[1]["before"]["text"] == rows[0]["after"]["text"]
        assert all(row["visibility"] == "private" and row["research_eligible"] is False for row in rows)
        assert all(row["observation_subject"] == "third_party" and row["demographics_subject"] == "owner" for row in rows)
        assert all(row["demographics"]["snapshot_id"] for row in rows)
        with pytest.raises(HTTPException) as stale:
            await person_answer_history.save(db, "person", member,
                {**owner, "intake.communication": "A different earlier answer"},
                {"intake.communication": "Stale tab must not replace the saved text."}, labels, answer_history.now())
        assert stale.value.status_code == 409
        assert await db.answer_history.count_documents({"source": "people"}) == 2
        await person_answer_history.save(db, "person", member, owner,
            {"intake.communication": ""}, labels, answer_history.now())
        current = await db.answer_history.find({"source": "people"}).to_list(10)
        assert len(current) == 1 and current[0]["kind"] == "answer_removed"
        assert current[0]["after"] == {"state": "removed"} and current[0]["before"] is None
        for row in rows:
            assert (await db.answer_history.find_one({"_id": row["id"]}))["purged"]
    run_case(go)


def test_person_intake_cannot_write_another_owners_profile():
    async def go(db):
        await db.users.insert_one({"user_id": "owner", "about": {"demographics": {"age": 30}}})
        await db.people.insert_one({"id": "person", "user_id": "owner", "intake": {"q": "Original"}})
        result = await person_answer_history.save(db, "person", {"user_id": "intruder"}, {"id": "person"},
            {"intake.q": "Must not be accepted"}, {"q": "Question"}, answer_history.now())
        assert result is None and await db.answer_history.count_documents({}) == 0
        assert (await db.people.find_one({"id": "person"}))["intake"]["q"] == "Original"
    run_case(go)


def test_initial_goal_activation_and_profile_goal_edits_share_the_journal(monkeypatch):
    from backend import server
    async def go(db):
        monkeypatch.setattr(server, "db", db)
        uid = "activation-owner"
        stamp = int(time.time())
        member = {"user_id": uid, "role": "member", "env": "preview", "tier": "paid",
                  "stripe_subscription_id": "sub_fixture", "subscription_period_start": stamp - 60,
                  "subscription_period_end": stamp + 3600, "about": {"demographics": {"age": 30}}}
        await db.users.insert_one(member.copy())
        person = {"id": "activated-person", "user_id": uid, "nickname": "Fictional person",
                  "rel_type": next(iter(server.ROMANTIC)), "stage": server.ROMANTIC_STAGES[0],
                  "background": "Fictional background", "goal": "Understand their communication",
                  "intake": {"her_goal": "Understand their communication"}, "archived": False,
                  "created_at": answer_history.now(), "last_activity": answer_history.now()}
        with pytest.raises(HTTPException) as denied:
            await server.activate_person({**member, "tier": "free"}, person=person)
        assert denied.value.status_code == 402
        assert await db.people.count_documents({}) == 0 and await db.answer_history.count_documents({}) == 0
        await server.activate_person(member, person=person)
        rows = await db.answer_history.find({"domain": "person_intake"}).to_list(10)
        assert len(rows) == 1 and rows[0]["kind"] == "answer_created"
        assert rows[0]["after"]["text"] == person["goal"]
        changed = server.PersonPatch(goal="Keep a clearer boundary", expected_profile_revision=0)
        await server.patch_person(person["id"], changed, member)
        assert await db.answer_history.count_documents({"domain": "person_intake"}) == 2
        # An identical replay keeps the existing profile-revision fallback and cannot create an extra attempt.
        await server.patch_person(person["id"], changed, member)
        assert await db.answer_history.count_documents({"domain": "person_intake"}) == 2
        saved = await db.people.find_one({"id": person["id"]})
        assert saved["goal"] == saved["intake"]["her_goal"] == "Keep a clearer boundary"
        await server.patch_person(person["id"], server.PersonPatch(goal="", expected_profile_revision=1), member)
        remaining = await db.answer_history.find({"domain": "person_intake"}).to_list(10)
        assert len(remaining) == 1 and remaining[0]["kind"] == "answer_removed"
        assert remaining[0]["before"] is None
    run_case(go)


def test_person_export_never_serializes_internal_journal_or_demographic_pointers():
    from backend import accounts, history_lifecycle
    async def go(db):
        person = {"id": "export-person", "user_id": "export-owner", "nickname": "Fictional person",
                  **{field: {"hidden": "internal-journal-canary"} for field in history_lifecycle.PRIVATE_FIELDS}}
        await db.people.insert_one(person.copy())
        output = "".join([chunk async for chunk in accounts.person_export_json(db, person)])
        decoded = json.loads(output)
        assert decoded["person"]["nickname"] == "Fictional person"
        assert not set(history_lifecycle.PRIVATE_FIELDS).intersection(decoded["person"])
        assert "internal-journal-canary" not in output
    run_case(go)


def test_person_export_fails_before_response_when_pending_erasure_cannot_finish(monkeypatch):
    from backend import server
    async def go(db):
        monkeypatch.setattr(server, "db", db)
        uid, pid = "export-owner", "export-person"
        member = {"user_id": uid, "role": "member", "env": "preview"}
        await db.users.insert_one(member.copy())
        await db.people.insert_one({"id": pid, "user_id": uid, "nickname": "Fictional person"})
        row = await db.people.find_one({"id": pid})
        observation = answer_history.event(source="people", source_id=pid, owner_id=uid,
            domain="person_intake", question_id="intake:communication", after={"text": "deleted-intake-canary"})
        await db.people.update_one({"_id": row["_id"]}, answer_history.append({}, observation, row))
        row = await db.people.find_one({"id": pid})
        await db.people.update_one(answer_history.guard({"_id": row["_id"]}, row),
            answer_history.redact({}, row, question_id="intake:communication"))
        real = answer_history._tombstone
        async def outage(*args, **kwargs):
            raise ConnectionError("fixture redaction outage")
        monkeypatch.setattr(answer_history, "_tombstone", outage)
        with pytest.raises(HTTPException) as unavailable:
            await server.person_export(pid, member)
        assert unavailable.value.status_code == 503
        assert "deleted-intake-canary" not in str(unavailable.value.detail)
        queued = await db.people.find_one({"id": pid})
        assert queued[answer_history.REDACTIONS] and queued[answer_history.PENDING]
        monkeypatch.setattr(answer_history, "_tombstone", real)
        response = await server.person_export(pid, member)
        output = "".join([chunk async for chunk in response.body_iterator])
        assert "deleted-intake-canary" not in output and answer_history.PENDING not in output
        assert (await db.answer_history.find_one({"id": observation["id"]}))["purged"] is True
    run_case(go)
