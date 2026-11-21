"""Only canonical, explicitly released Couples prompt snapshots enter research."""
from copy import deepcopy
import json

import pytest

from backend import couple_research, couples_journey, document_queries, questionnaire_report, research
from backend.quiz import default_quiz
from backend.tests.test_admin_answer_history import run_case


KEY = "support-1"
VARIABLE = "c:prompt:" + KEY
PROMPT = next(row for row in couples_journey.PROMPTS if row["id"] == KEY)


def snapshot():
    signature = research.fingerprint(["prompt:" + KEY, PROMPT["question"], PROMPT["choices"]])
    variable = {"domain": "couples", "label": PROMPT["question"], "choices": PROMPT["choices"], "signature": signature}
    pair = {"_id": "pair", "env": "fixture", "state": "active", "inviter": "one", "invitee": "two",
            "members": ["one", "two"], "journey": {"threads": {KEY: {
                "kind": "prompt", "prompt_id": KEY, "title": PROMPT["question"],
                "shares": {"a": [PROMPT["choices"][0]], "b": [PROMPT["choices"][1]]},
                "shared_choices": {"a": {"index": 0, "edition": signature}, "b": {"index": 1, "edition": signature}}}}}}
    return pair, variable


@pytest.mark.parametrize("change, expected", [
    ("current", 0), ("archived", 0), ("no_snapshot", None), ("old_edition", None),
    ("negative_index", None), ("out_of_range", None), ("boolean_index", None), ("string_index", None),
    ("wrong_actor", None), ("missing_release", None), ("wrong_prompt", None), ("wrong_title", None),
    ("missing_signature", None), ("withdrawn", None), ("non_object_threads", None),
])
def test_shared_prompt_validation_rejects_private_and_malformed_sources(change, expected):
    pair, variable = snapshot()
    thread = pair["journey"]["threads"][KEY]
    owner = "one"
    if change == "archived": variable["label"] = "Archived · " + variable["label"]
    elif change == "no_snapshot": thread["shared_choices"].pop("a")
    elif change == "old_edition": thread["shared_choices"]["a"]["edition"] = "older"
    elif change == "negative_index": thread["shared_choices"]["a"]["index"] = -1
    elif change == "out_of_range": thread["shared_choices"]["a"]["index"] = len(variable["choices"])
    elif change == "boolean_index": thread["shared_choices"]["a"]["index"] = True
    elif change == "string_index": thread["shared_choices"]["a"]["index"] = "0"
    elif change == "wrong_actor": owner = "two"
    elif change == "missing_release": thread["shares"]["a"] = ["Unreleased custom wording"]
    elif change == "wrong_prompt": thread["prompt_id"] = "another-prompt"
    elif change == "wrong_title": thread["title"] = "Different question?"
    elif change == "missing_signature": variable.pop("signature")
    elif change == "withdrawn": thread["shares"].pop("a"); thread["shared_choices"].pop("a")
    elif change == "non_object_threads": pair["journey"]["threads"] = ["malformed"]
    expression = couple_research.shared_prompt_code({"$literal": owner}, "$$ROOT", {"$literal": "a"}, KEY, variable)
    project = document_queries.compile_projection({"_id": 0, "choice": expression})
    assert project(pair) == {"choice": expected}


def test_postgres_research_uses_shared_snapshot_and_ignores_later_private_draft():
    async def check(db):
        pair, variable = snapshot()
        people = [{"user_id": uid, "env": "fixture", "role": "member", "couples_adult_at": "2026-11-01",
                   "questionnaire_research_version": questionnaire_report.CONSENT,
                   "about": {"demographics": {"age": 30}}} for uid in ("one", "two")]
        await db.users.insert_many(people)
        await db.couples_pairs.insert_one(pair)
        private = {"user_id": "one", "key": KEY, "choice": PROMPT["choices"][-1], "lines": ["PRIVATE_DRAFT_MARKER"]}
        await db.couples_drafts.insert_one(deepcopy(private))
        axes = {"x": (VARIABLE, None)}
        variables = {VARIABLE: variable}
        eligible = {"env": "fixture"}

        async def read():
            stages = questionnaire_report.member_seat(eligible, default_quiz(), variables, axes)
            assert "couples_drafts" not in json.dumps(stages)
            rows = await db.users.aggregate(stages + [{"$project": {"_id": 0, "person.user_id": 1, "seat": 1}}]).to_list(2)
            return {row["person"]["user_id"]: row["seat"]["x"] for row in rows}

        assert await read() == {"one": 0, "two": 1}
        stages = questionnaire_report.couple_seats(eligible, default_quiz(), variables, axes)
        row, = await db.couples_pairs.aggregate(stages + [{"$project": {"_id": 0, "a": 1, "b": 1}}]).to_list(1)
        assert row == {"a": {"x": 0}, "b": {"x": 1}}
        await db.couples_drafts.update_one({"user_id": "one", "key": KEY}, {"$set": {"choice": PROMPT["choices"][1]}})
        assert await read() == {"one": 0, "two": 1}
        await db.couples_pairs.update_one({"_id": "pair"}, {"$unset": {
            "journey.threads." + KEY + ".shared_choices.a": "", "journey.threads." + KEY + ".shares.a": ""}})
        assert await read() == {"one": None, "two": 1}
        owner_draft = await db.couples_drafts.find_one({"user_id": "one", "key": KEY})
        assert owner_draft["lines"] == private["lines"]

    run_case(check)
