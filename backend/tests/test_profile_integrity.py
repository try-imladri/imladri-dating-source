import asyncio
import copy
import os
import uuid
from urllib.parse import urlsplit

import pytest
from fastapi import HTTPException

from backend import quiz_answers
from backend.quiz import about_summary, default_quiz


def test_prior_wording_survives_reorder_rewrite_and_deletion():
    initial = default_quiz()
    edited = copy.deepcopy(initial)
    row = edited["short"][0]
    original = row["q"], row["options"][0]["label"]
    member = {"answers": {"s1": {"choice": 0, "note": "old note"}}}
    row["options"].reverse()
    edited["legacy_wording"] = quiz_answers.remember_published_wording(initial, edited)
    saved = quiz_answers.display_about(member, edited)["answers"]["s1"]
    assert (saved["question"], saved["label"], saved["choice"]) == (*original, 3)
    assert original[1] in about_summary(member, edited)["lines"][0]
    assert quiz_answers.selected_answer(edited, row, 0, *original)["choice"] == 3
    with pytest.raises(ValueError):
        quiz_answers.selected_answer(edited, row, 0, None, None)
    rewrite = copy.deepcopy(edited)
    rewrite["short"][0]["q"] = "A different question"
    rewrite["legacy_wording"] = quiz_answers.remember_published_wording(edited, rewrite)
    assert quiz_answers.display_about(member, rewrite)["answers"] == {}
    assert quiz_answers.display_about(member, rewrite)["previous_answers"]["s1"]["label"] == original[1]
    assert about_summary(member, rewrite)["short_done"] == 0
    removed = {**rewrite, "short": []}
    assert quiz_answers.display_about(member, removed)["previous_answers"]["s1"]["question"] == original[0]


def test_field_updates_and_catalog_edits_against_local_mongo(monkeypatch):
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Set IMLADRI_TEST_MONGO_URL to a loopback MongoDB for simultaneous-write checks.")
    assert urlsplit(uri).hostname in {"localhost", "127.0.0.1", "::1"}
    from motor.motor_asyncio import AsyncIOMotorClient
    database = "imladri_profile_integrity_" + uuid.uuid4().hex
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", database)
    from backend import server

    async def scenario():
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        db = client[database]
        monkeypatch.setattr(server, "db", db)
        uid = "profile-integrity-fixture"
        member = {"user_id": uid, "onboarded": True, "onboarding_step": 4}
        try:
            await db.users.insert_one(dict(member))
            await db.settings.insert_one({"key": "global", "quiz": default_quiz()})
            async def put(**body):
                return await server.put_about(server.AboutIn(**body), member)
            for _ in range(12):
                await db.users.update_one({"user_id": uid}, {"$unset": {"about": ""}})
                await asyncio.gather(put(answers={"s1": {"choice": 0}}, demographics_patch={"age": 30}),
                                     put(answers={"f2": {"choice": 2}}, demographics_patch={"sex": "Non-binary"}),
                                     put(describe="synthetic description"), put(mbti="ISFP"))
                saved = (await db.users.find_one({"user_id": uid}))["about"]
                assert set(saved["answers"]) == {"s1", "f2"}
                assert saved["demographics"] == {"age": 30, "sex": "Non-binary"}
                assert saved["describe"] == "synthetic description" and saved["mbti"] == "ISFP"
            await asyncio.gather(put(answers={"s1": None}), put(answers={"s2": {"choice": 1}}))
            saved = (await db.users.find_one({"user_id": uid}))["about"]
            assert set(saved["answers"]) == {"f2", "s2"}
            await db.users.update_one({"user_id": uid}, {"$set": {"about.answers.s1": {"choice": 0, "note": "legacy"}}})
            published = default_quiz()
            changed = copy.deepcopy(published)
            row = changed["short"][0]
            original = row["options"][0]["label"]
            row["options"].reverse()
            revision = await server.admin_put_settings({"quiz": changed, "quiz_revision": None}, member)
            assert revision["quiz_revision"]
            with pytest.raises(HTTPException) as conflict:
                await server.admin_put_settings({"quiz": published, "quiz_revision": None}, member)
            assert conflict.value.status_code == 409
            after = await server.get_about(await db.users.find_one({"user_id": uid}))
            legacy = after["about"]["answers"]["s1"]
            assert (legacy["choice"], legacy["label"]) == (3, original)
            await server.quick_answer(server.QuickAnswerIn(qid="s1", choice=0, question=row["q"], label=original), member)
            with pytest.raises(HTTPException) as conflict:
                await put(answers={"s1": {"choice": 1}})
            assert conflict.value.status_code == 409
            stored = (await db.users.find_one({"user_id": uid}))["about"]
            assert stored["answers"]["s1"]["label"] == original
            old = {"choice": 0, "question": "Earlier wording?", "label": "Earlier answer", "note": ""}
            await db.users.update_one({"user_id": uid}, {"$set":{"about.answers.s1":old}})
            opened = (await server.get_about(await db.users.find_one({"user_id":uid})))["about"]["previous_answers"]["s1"]
            await put(answers={"s1":{"choice":0, "question":row["q"], "label":original}})
            with pytest.raises(HTTPException) as stale_removal:
                await put(answers={"s1": None}, expected_earlier_answer={"s1":opened})
            assert stale_removal.value.status_code == 409
            assert (await db.users.find_one({"user_id":uid}))["about"]["answers"]["s1"]["label"] == original
            await db.users.update_one({"user_id": uid}, {"$set":{"about.answers.s1":old}})
            assert "s1" not in (await put(answers={"s1":None}, expected_earlier_answer={"s1":opened}))["about"]["previous_answers"]
            for legacy in (None, {"answers": None, "demographics": None, "skipped": None}):
                await db.users.update_one({"user_id": uid}, {"$set": {"about": legacy}})
                previous_member = {**member, "about": legacy}
                record = await server.put_about(server.AboutIn(answers={"s2": {"choice": 0}},
                                                               demographics_patch={"age": 30}), previous_member)
                assert record["about"]["answers"]["s2"]["choice"] == 0
                assert record["about"]["demographics"] == {"age": 30}
        finally:
            await client.drop_database(database)
            client.close()
    asyncio.run(scenario())
