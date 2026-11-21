import json
import os
import re
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from backend import couple_research, questionnaire_report, research, research_trends
from backend.quiz import default_quiz
from backend.tests.test_free_plan_quota import run_case


@pytest.mark.parametrize("cells", [
    [[15, 8], [10, 20]], [[11, 12], [12, 13]], [[30, 20], [0, 0]], [[50]], [[0, 0], [0, 0]],
])
def test_hidden_table_exposes_no_counts_labels_margins_or_statistic(cells):
    assert research_trends.summarize(cells) == {
        "available": False, "sample_size": None, "rows": [], "columns": [], "cramers_v": None}


def test_sample_size_percentages_and_association_share_one_rounded_source():
    visible = research_trends.summarize([[32, 11, 0], [12, 34, 0], [0, 0, 0]])
    assert visible["rows"] == [{"index": 0, "cells": [30, 10], "total": 40},
                               {"index": 1, "cells": [10, 35], "total": 45}]
    assert visible["columns"] == [0, 1] and visible["sample_size"] == 85
    assert visible["cramers_v"] == 0.528
    assert research_trends.summarize([[25, 25], [25, 25]])["cramers_v"] == 0
    assert research_trends.summarize([[25, 0], [0, 25]])["cramers_v"] == 1
    assert research_trends.summarize([[30, 10], [10, 30]])["cramers_v"] == 0.5


@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires disposable loopback MongoDB.")
def test_single_question_includes_partial_and_legacy_once_and_withholds_rare_answers():
    async def check(db):
        quiz = default_quiz()
        version, variables = research.catalog(quiz, [])
        key = "i:conversations:0"
        tag = research.CONDITIONS[0]
        eligible = {"env": "preview", "email": {"$ne": "qa-fixture"}}
        members, current, snapshots = [], [], []
        for i in range(66):
            uid = f"private-account-{i}"
            member = {"user_id": uid, "env": "preview", "role": "member"}
            snapshot = {"_id": uid, "user_id": uid, "env": "preview", "submitted": True,
                        "consent_version": research.CONSENT_VERSION, "catalog_version": version,
                        "values": {key: 0}, "conditions": {}}
            if i < 61:
                member.update(questionnaire_research_version=questionnaire_report.CONSENT,
                              questionnaire_research_managed=True)
                answer = 1 if i < 17 else 2
                current.append({"_id": uid, "values": {key: answer}, "conditions": {key: [tag] if i % 2 else []},
                                "signatures": {key: variables[key]["signature"]}})
                snapshot["values"][key] = 4  # A superseded contribution cannot add a second vote.
            elif i == 61:
                member.update(questionnaire_research_managed=True)
            elif i == 62:
                member["about"] = {"demographics": {"age": 17}}
            elif i == 63:
                member["role"] = "admin"
            elif i == 64:
                member["email"] = "qa-fixture"
            else:
                snapshot["values"][key] = 1
            members.append(member)
            snapshots.append(snapshot)
        await db.users.insert_many(members)
        await db.intimacy_answers.insert_many(current)
        await db.research_responses.insert_many(snapshots)

        async def load(condition=None):
            return await research_trends.distribution(db, eligible, quiz, key, condition)

        report = await load()
        assert report["rows"] == [{"label": "Curious", "count": 20}, {"label": "It depends", "count": 45}]
        assert report["sample_size"] == 65 and "private-account" not in json.dumps(report)
        tags = await load(tag)
        assert not tags["available"] and not tags["rows"] and tags["sample_size"] is None  # 44 eligible
        changed = {"$set": {"values." + key: 4}}
        await db.intimacy_answers.update_one({"_id": current[0]["_id"]}, changed)
        assert not (await load())["available"]  # Even one rare category suppresses every margin.
        # Withdrawals, skips, missing answers and incompatible historical wording cannot become a "no".
        await db.intimacy_answers.update_one({"_id": current[0]["_id"]}, {"$set": {"values." + key: None}})
        await db.intimacy_answers.update_one({"_id": current[1]["_id"]}, {"$unset": {"values." + key: ""}})
        await db.intimacy_answers.update_one({"_id": current[2]["_id"]}, {"$set": {"signatures." + key: "old"}})
        await db.users.update_one({"user_id": current[3]["_id"]}, {"$unset": {"questionnaire_research_version": ""}})
        assert (await load())["rows"] == [{"label": "Curious", "count": 15}, {"label": "It depends", "count": 45}]
        await db.intimacy_answers.update_many({}, {"$set": {"values." + key: 2}})
        await db.research_responses.update_one({"_id": snapshots[-1]["_id"]}, {"$set": {"values." + key: 2}})
        tags = await load(tag)
        assert tags["available"] and [r["label"] for r in tags["rows"]] == ["Tag not selected", "Tag selected"]
        with pytest.raises(HTTPException) as invalid:
            await research_trends.distribution(db, eligible, quiz, "d:height_cm")
        assert invalid.value.status_code == 422
    run_case(check)


@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires a disposable loopback MongoDB.")
def test_two_variable_comparisons_eligibility_suppression_and_question_versions():
    async def check(db):
        quiz = default_quiz()
        version, variables = research.catalog(quiz, [])
        first, second, day, demo = "i:conversations:0", "i:conversations:1", "e:s1", "d:education"
        tagged = "i:conversations:2"
        tag_a, tag_b = research.CONDITIONS[:2]
        eligible = {"env": {"$in": ["preview", None]}, "email": {"$not": re.compile("staff-fixture")}}
        protected = "unpublished-private-marker"
        members, responses = [], []

        def add(name, row, column):
            uid = protected + "-" + name
            members.append({"user_id": uid, "email": "member@example.com", "env": "preview", "role": "member",
                            "about": {"demographics": {"age": 22 if row == 0 else 34}}})
            values = {first: column, second: row, day: row, demo: row}
            responses.append({"_id": uid, "user_id": uid, "env": "preview", "submitted": True,
                              "consent_version": research.CONSENT_VERSION, "catalog_version": version,
                              "values": values, "age_band": research.AGE_BANDS[row],
                              "signatures": {k: variables[k]["signature"] for k in values},
                              "conditions": {first: [protected]}})

        for row, cells in enumerate([[30, 10], [10, 30]]):
            for col, count in enumerate(cells):
                for i in range(count):
                    add(f"{row}-{col}-{i}", row, col)
                    response = responses[-1]
                    response["values"][tagged] = research.CHOICES.index("It depends")
                    response["signatures"][tagged] = variables[tagged]["signature"]
                    tags = ([tag_a] if col else []) + ([tag_b] if row else [])
                    if tags:
                        response["conditions"][tagged] = tags
        for name in ("withdrawn", "stale", "minor", "invalid-age", "closing", "admin", "archived",
                     "other-member-env", "other-response-env", "internal", "deleted", "skipped", "missing",
                     "out-of-range", "boolean"):
            add(name, 0, 4)  # A single wrongly included member suppresses the whole report.
            member, response = members[-1], responses[-1]
            if name == "withdrawn":
                response["submitted"] = False
            elif name == "stale":
                response["consent_version"] = "old"
            elif name in ("minor", "invalid-age"):
                member["about"]["demographics"]["age"] = 17 if name == "minor" else 130
            elif name == "closing":
                member["closing"] = True
            elif name == "admin":
                member["role"] = "admin"
            elif name == "archived":
                response.update(catalog_version="older wording", signatures={})
            elif name == "other-member-env":
                member["env"] = "another-env"
            elif name == "other-response-env":
                response["env"] = "another-env"
            elif name == "internal":
                member["email"] = "staff-fixture@example.com"
            elif name == "deleted":
                members.pop()
            elif name == "skipped":
                response["values"][first] = None
            elif name == "missing":
                del response["values"][first]
                # Choosing another answer cannot count as a negative vote on any tag.
                response["values"][tagged] = research.CHOICES.index("Curious")
            elif name == "out-of-range":
                response["values"][first] = 99
            elif name == "boolean":
                response["values"][first] = True
        await db.users.insert_many(members)
        await db.research_responses.insert_many(responses)

        report = await research_trends.report(db, eligible, quiz)
        assert report["x"] == "age_band" and report["y"] == first
        assert report["available"] and report["sample_size"] == 80 and report["cramers_v"] == 0.5
        assert protected not in json.dumps(report)
        assert {v["id"] for v in report["variables"]} == {"age_band", *variables, *questionnaire_report.additional_variables(),
                                                            *await couple_research.variables(db)}
        for x, y in ((day, first), (first, day), (second, first), (demo, first)):
            pair = await research_trends.report(db, eligible, quiz, x, y)
            assert pair["sample_size"] == 80 and pair["cramers_v"] == 0.5
        for x, y, xt, yt in ((day, tagged, None, tag_a), (tagged, tagged, tag_b, tag_a)):
            pair = await research_trends.report(db, eligible, quiz, x, y, xt, yt)
            assert pair["sample_size"] == 80 and pair["cramers_v"] == 0.5
            assert pair["y_choices"] == ["Tag not selected", "Tag selected"]
        for x, y, xt, yt in ((tagged, tagged, tag_a, tag_a), (tagged, tagged, None, tag_a),
                             (day, tagged, tag_b, tag_a), (day, tagged, None, "not a published tag")):
            with pytest.raises(HTTPException) as exc:
                await research_trends.report(db, eligible, quiz, x, y, xt, yt)
            assert exc.value.status_code == 422
        for x, y in ((first, first), ("$where", day), (None, "e:s1")):
            with pytest.raises(HTTPException) as exc:
                await research_trends.report(db, eligible, quiz, x, y)
            assert exc.value.status_code == 422

        topic = variables[second]["topic"]
        await db.intimacy_question_copy.insert_one({"section": "conversations", "topic": topic,
                                                     "question": "A new wording?", "changed": True})
        # Edits affect only pairs whose coding changed.
        assert (await research_trends.report(db, eligible, quiz, day, first))["sample_size"] == 80
        assert not (await research_trends.report(db, eligible, quiz, first, second))["available"]
        await db.research_responses.update_one({"_id": protected + "-0-1-0"}, {"$set": {"submitted": False}})
        after = await research_trends.report(db, eligible, quiz, day, first)
        assert not after["available"] and after["sample_size"] is None and after["rows"] == []

    run_case(check)

@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires a disposable loopback MongoDB.")
def test_partial_individual_and_couple_answers_join_each_respondent_and_pair_only_once():
    async def check(db):
        quiz = default_quiz()
        version, catalog = research.catalog(quiz, [])
        options = quiz["short"][0]["options"]
        qid, individual, intimate = quiz["short"][0]["id"], "e:" + quiz["short"][0]["id"], "i:conversations:0"
        couple = "c:prompt:communication-1"
        prompt = (await couple_research.variables(db))[couple]
        eligible = {"env": {"$in": ["preview", None]}}
        people, pairs, drafts, answers = [], [], [], []
        tag = research.CONDITIONS[0]
        count = 0
        for row, cells in enumerate([[40, 10], [20, 30]]):
            for col, n in enumerate(cells):
                for _ in range(n):
                    count += 1
                    a, b = f"fixture-a{count}", f"fixture-b{count}"
                    pairs.append({"_id": f"pair{count}", "state": "active", "env": "preview", "inviter": a, "invitee": b,
                                  "members": [a, b], "journey": {"threads": {"communication-1": {
                                      "kind": "prompt", "prompt_id": "communication-1", "agreements": [{"checkins": {
                                          side: {"result": ["helped", "mixed"][value], "updated_at": "2026-01-01"}
                                          for side, value in [("a", row), ("b", col)]}}]}}}})
                    for uid, own, other in [(a, row, col), (b, col, row)]:
                        people.append({"user_id": uid, "role": "member", "env": "preview",
                                       "questionnaire_research_managed": True,
                                       "questionnaire_research_version": questionnaire_report.CONSENT,
                                       "couples_adult_at": "2026-01-01", "about": {"mbti": ["INTJ", "INTP"][own],
                                       "demographics": {"age": [21, 29][own], "gender": ["Woman", "Man"][own],
                                                        "sex": ["Female", "Male"][own], "height_cm": [156, 178][own],
                                                        "education": ["High school", "Some college"][own],
                                                        "ethnicity": ["Mixed", "White"][own]},
                                       "answers": {qid: {"question": quiz["short"][0]["q"], "label": options[own]["label"],
                                                         "choice": own, "note": "fixture sensitive note"}}}})
                        drafts.append({"user_id": uid, "key": "communication-1", "choice": prompt["choices"][own],
                                       "lines": ["private drawer"]})
                        answers.append({"_id": uid, "values": {intimate: other},
                                        "signatures": {intimate: catalog[intimate]["signature"]}})
        await db.users.insert_many(people)
        await db.couples_pairs.insert_many(pairs)
        await db.couples_drafts.insert_many(drafts)
        await db.intimacy_answers.insert_many(answers)
        # This previously-contributed snapshot must never count as a second respondent.
        await db.research_responses.insert_one({"user_id": "fixture-a1", "submitted": True, "consent_version": research.CONSENT_VERSION,
                                                "env": "preview", "catalog_version": version, "values": {individual: 3, intimate: 4}})
        async def report(x, y, unit="member", **conditions):
            return await research_trends.report(db, eligible, quiz, x, y, unit=unit, **conditions)

        async def matrix(x, y, unit="member", **conditions):
            data = await report(x, y, unit, **conditions)
            assert "fixture sensitive note" not in json.dumps(data) and "fixture-a" not in json.dumps(data)
            return [r["cells"] for r in data["rows"]]

        for key in ["height_band", "age_band", "d:gender", "d:sex", "d:ethnicity", "d:education", "p:mbti",
                    "c:checkin:prompt:communication-1", "c:checkin:any"]:
            comparison = await report(individual, key)
            assert comparison["sample_size"] == 200 and comparison["cramers_v"] == 1, key
        assert await matrix(individual, intimate) == [[80, 30], [30, 60]]
        # A time stored as a BSON date or a text timestamp attests the same adult seat.
        threshold_seat = {"user_id": "fixture-a41"}
        for invalid in (None, "", True, ["2026-01-01"]):
            await db.users.update_one(threshold_seat, {"$set": {"couples_adult_at": invalid}})
            assert not (await report("d:gender", "d:gender", "couple_inviter"))["available"]
        await db.users.update_one(threshold_seat, {"$set": {"couples_adult_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}})
        assert await matrix("d:gender", "d:gender", "couple_inviter") == [[40, 10], [20, 30]]
        assert await matrix("d:gender", "d:gender", "couple_invitee") == [[40, 20], [10, 30]]
        assert await matrix("d:gender", "d:gender", "couple") == [[40, 15], [15, 30]]
        assert not (await report(individual, couple, "couple"))["available"]

        # Searching a full page emits only released associations, ordered against the same underlying pair tables.
        first_page = await research_trends.discover(db, eligible, quiz, individual)
        assert first_page["next_cursor"] == research_trends.DISCOVERY_PAGE
        assert [r["y"] for r in first_page["matches"]] == ["age_band", intimate]
        assert first_page["matches"][0] == {"y": "age_band", "y_condition": None, "sample_size": 200, "cramers_v": 1.0}
        assert first_page["matches"][1]["cramers_v"] == (await report(individual, intimate))["cramers_v"]
        assert "fixture sensitive note" not in json.dumps(first_page)
        # Selecting the final partial page checks every published Couples question and terminates.
        last = ((first_page["total"] - 1) // research_trends.DISCOVERY_PAGE) * research_trends.DISCOVERY_PAGE
        tail = await research_trends.discover(db, eligible, quiz, individual, cursor=last, catalog_version=first_page["catalog_version"])
        assert tail["next_cursor"] is None and tail["checked"] == tail["total"]
        with pytest.raises(HTTPException) as stale:
            await research_trends.discover(db, eligible, quiz, individual, cursor=last, catalog_version="old wording")
        assert stale.value.status_code == 409
        across = await research_trends.discover(db, eligible, quiz, "d:gender", unit="couple")
        match = next(r for r in across["matches"] if r["y"] == "age_band")
        assert match["sample_size"] == 100 and match["cramers_v"] == (await report("d:gender", "age_band", "couple"))["cramers_v"]

        # The member's private unanswered/older intake is excluded, even if a former snapshot contains an answer.
        await db.intimacy_answers.update_many({}, {"$set": {"values." + intimate: 2}})
        await db.intimacy_answers.update_many({"_id": {"$in": [p["user_id"] for p in people
             if p["about"]["demographics"]["gender"] == "Man"]}}, {"$set": {"conditions." + intimate: [tag]}})
        await db.intimacy_answers.update_one({"_id": "fixture-a1"}, {"$set": {"signatures." + intimate: "edited"}})
        data = await report(individual, intimate, y_condition=tag)
        assert data["sample_size"] == 200 and data["cramers_v"] == 1  # 199 paired, rounded to the nearest five.
        await db.users.update_one({"user_id": "fixture-a1"}, {"$unset": {"questionnaire_research_version": ""}})
        tags = await research_trends.discover(db, eligible, quiz, individual)
        assert next(r for r in tags["matches"] if r["y_condition"] == tag)["cramers_v"] == 1.0
        # A directional 10-member cell drops to 9; the entire table disappears.
        await db.users.update_one({"user_id": "fixture-a41"}, {"$unset": {"questionnaire_research_version": ""}})
        assert not (await report("d:gender", "d:gender", "couple_inviter"))["available"]
        hidden = await research_trends.discover(db, eligible, quiz, "d:gender", unit="couple_inviter")
        assert all(m["y"] != "age_band" for m in hidden["matches"])
        await db.couples_pairs.update_many({}, {"$set": {"state": "closed"}})
        assert not (await report(individual, couple, "couple"))["available"]
        assert not (await report(individual, "c:checkin:any"))["available"]
        assert not (await report(individual, couple))["available"]  # Private drafts never become report records.
    run_case(check)


@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires disposable loopback MongoDB.")
def test_latest_couples_outcome_compares_instants_and_withholds_disputed_ties():
    async def scenario(db):
        first = datetime(2026, 1, 1, tzinfo=timezone.utc)
        later = "2025-12-31T19:00:00.000005-05:00"
        instant = "2026-01-01T00:00:00.000005Z"
        cases = [
            ([(first, "helped"), (later, "mixed")], "mixed"),
            ([("2026-01-01T01:00:00.000004+01:00", "helped"), (later, "mixed")], "mixed"),
            ([(later, "helped"), (instant, "mixed")], None),
            ([(later, "helped"), (instant, "helped")], "helped"),
            ([("2026-01-01", "mixed"), ("2025-12-31T18:59:00-05:00", "helped")], "mixed"),
            ([(None, "helped"), ("invalid", "mixed")], "helped"),
            ([(None, "helped"), (None, "mixed")], None),
            ([("invalid", "helped")], None),
        ]
        docs = []
        for i, (items, expected) in enumerate(cases):
            agreements = [{"checkins": {"a": {"result": result, **({"updated_at": stamp} if stamp else {})}}}
                          for stamp, result in items]
            docs.append({"_id": i, "journey": {"threads": {"fixture": {"agreements": agreements}}}})
        await db.couples_pairs.insert_many(docs)
        value = couple_research.checkin_code("$$ROOT", {"$literal": "a"}, "any")
        actual = await db.couples_pairs.aggregate([{"$sort": {"_id": 1}}, {"$project": {"value": value}}]).to_list(None)
        assert [r["value"] for r in actual] == [couple_research.RESULTS.index(v) if v else -1 for _, v in cases]
    run_case(scenario)
