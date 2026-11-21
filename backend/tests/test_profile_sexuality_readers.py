"""The optional profile field follows current research/export permissions and editions."""
import csv
import io
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend import personal_demographics, questionnaire_report, research_rows, research_trends
from backend import research_couple_export as couple_export
from backend import your_type_research
from backend import your_type
from backend.quiz import default_quiz
from backend.tests.test_postgres_store import exercise, integration
from backend.tests.test_your_type import member


def test_current_profile_and_original_attempt_sexuality_remain_separate():
    member = {"about": {"demographics": {"age": 30, "gender": "Man", "sexuality": "gay"}}}
    session = {"audience": "women", "state": {}, "preference_snapshot": {
        "version": "your-type-preferences-v1", "sexuality": "straight", "viewer_gender": "Man", "rated_audience": "women"}}
    values = your_type_research.latest_values(member, session)
    assert values["sexuality"] == "straight" and "d:sexuality" not in values
    member["questionnaire_research_version"] = personal_demographics.CONSENT_VERSION
    values = your_type_research.latest_values(member, session)
    assert values["sexuality"] == "straight" and values["d:sexuality"] == "gay"
    session.pop("preference_snapshot")
    assert "sexuality" not in your_type_research.latest_values(member, session)
    member["about"]["demographics"].pop("sexuality")
    row = next(row for row in research_rows.demographic_rows(member) if row["variable_id"] == "d:sexuality")
    assert row["status"] == "unanswered" and row["answer"] is None


@integration
def test_real_legacy_start_retry_keeps_pre_saved_profile_request_hash():
    async def scenario(db, _other):
        await member(db)
        old_body = your_type.legacy.Start(expected_member="owner", request_id=str(uuid4()), audience="women", adult_confirmed=True)
        row = await your_type.legacy.start(db, "owner", old_body)
        before = await db[your_type.COLLECTION].find_one({"_id": row["id"]})
        assert before["start_request_hash"] == your_type.legacy.digest(your_type.legacy.start_request(old_body))
        assert "use_saved_profile" not in your_type.legacy.start_request(old_body)
        await db.users.update_one({"user_id": "owner"}, {"$set": {"about.demographics.sexuality": "gay", "about.demographics.gender": "Man"}})
        retried = await your_type.start(db, "owner", your_type.Start(**old_body.model_dump()))
        assert retried["id"] == row["id"] and retried["duplicate"] and retried["preference_snapshot"] is None
        assert (await db[your_type.COLLECTION].find_one({"_id": row["id"]}))["start_request_hash"] == before["start_request_hash"]
        with pytest.raises(HTTPException) as changed_request:
            await your_type.start(db, "owner", your_type.Start(**old_body.model_dump(), use_saved_profile=True))
        assert changed_request.value.status_code == 409
    exercise(scenario)


@integration
def test_catalog_projection_and_couple_csv_keep_existing_adult_and_separate_permission_gates():
    async def scenario(db, _other):
        env = "preview"
        pair_id = "private-sexuality-reader-pair"
        stamp = "2026-11-19T00:00:00+00:00"
        users = []
        for uid, digit, sexuality in (("private-left", "1", "bi"), ("private-right", "2", "gay")):
            users.append({"user_id": uid, "env": env, "role": "member", "couples_adult_at": stamp,
                          "questionnaire_research_version": personal_demographics.CONSENT_VERSION,
                          "row_couple_pair_id": pair_id, "row_couple_version": research_rows.VERSIONS["couple"],
                          "row_couple_env": env, "row_couple_code": digit * 32,
                          "about": {"demographics": {"age": 30, "gender": "Man", "sexuality": sexuality}}})
        await db.users.insert_many(users)
        await db.couples_pairs.insert_one({"_id": pair_id, "env": env, "state": "active",
                                          "members": [user["user_id"] for user in users],
                                          "inviter": users[0]["user_id"], "invitee": users[1]["user_id"], "joined_at": stamp})
        quiz = default_quiz()
        _, _, variables = await research_trends.catalog(db, quiz)
        assert variables["d:sexuality"] == {"domain": "demographics", "label": "Sexuality",
                                            "choices": list(personal_demographics.SEXUALITY_OPTIONS)}
        assert questionnaire_report.member_projection({"x": ("d:sexuality", None)}, variables) == {
            "_id": 0, "user_id": 1, "about.demographics.sexuality": 1}
        async def comparable():
            pipeline = questionnaire_report.member_seat({"env": env}, quiz, variables, {"x": ("d:sexuality", None)})
            return await db.users.aggregate(pipeline).to_list(10)
        assert sorted(row["seat"]["x"] for row in await comparable()) == [1, 2]
        async def csv_rows():
            response = await research_rows.export_response(couple_export.iter_rows(db, {"env": env}, stamp), "couple", stamp, None)
            content = "".join([chunk async for chunk in response.body_iterator])
            assert all(user["user_id"] not in content for user in users)
            assert pair_id not in content
            return [row for row in csv.DictReader(io.StringIO(content)) if row["variable_id"] == "d:sexuality"]
        rows = await csv_rows()
        assert {(row["seat"], row["answer"]) for row in rows} == {("inviter", "bi"), ("invitee", "gay")}
        # Aggregate questionnaire permission and current-record pair permission remain independent.
        await db.users.update_one({"user_id": users[1]["user_id"]}, {"$unset": {"questionnaire_research_version": ""}})
        assert [row["seat"]["x"] for row in await comparable()] == [1]
        assert len(await csv_rows()) == 2
        await db.users.update_one({"user_id": users[1]["user_id"]}, {"$unset": {"row_couple_version": ""}})
        assert not await csv_rows()
        await db.users.update_one({"user_id": users[1]["user_id"]}, {"$set": {
            "row_couple_version": research_rows.VERSIONS["couple"], "about.demographics.age": 17}})
        assert not await csv_rows()
        await db.users.update_one({"user_id": users[1]["user_id"]}, {"$set": {"about.demographics.age": 30},
                                                                 "$unset": {"about.demographics.sexuality": ""}})
        rows = await csv_rows()
        assert next(row for row in rows if row["seat"] == "invitee")["status"] == "unanswered"
        assert next(row for row in rows if row["seat"] == "invitee")["answer"] == ""
    exercise(scenario)
