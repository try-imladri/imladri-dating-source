"""Reconcile every published research variable against a saved member answer."""
import os
import time

import httpx
import pytest

from backend import couples_journey, questionnaire_report, research, research_trends
from backend.tests.test_free_plan_quota import run_case


@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires disposable loopback MongoDB.")
def test_complete_member_questionnaire_and_admin_catalog(monkeypatch):
    from backend import server
    uid = "coverage-member"
    identity = {"user_id": uid}
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_coverage")

    async def check(db):
        monkeypatch.setattr(server, "db", db)
        ts = int(time.time())
        await db.users.insert_many([{"user_id": uid, "role": "member", "env": server.ENV, "tier": "paid",
            "email": "one@research.invalid", "stripe_subscription_id": "sub_fixture",
            "subscription_period_start": ts - 60, "subscription_period_end": ts + 6000, "couples_adult_at": "2026-01-01"},
            {"user_id": "coverage-admin", "role": "admin", "env": server.ENV}])
        quiz = (await server.get_settings())["quiz"]
        state = await research_trends.catalog(db, quiz)
        variables = state[2]
        expected = {}
        path = "/api/admin/research/distribution"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as client:
            async def write(method, route, data):
                response = await client.request(method, "/api" + route, json=data)
                assert response.status_code == 200, response.text
                return response.json()

            assert (await client.get(path, params={"key": "age_band"})).status_code == 401
            monkeypatch.setitem(server.app.dependency_overrides, server.require_user, lambda: identity)
            assert (await client.get(path, params={"key": "age_band"})).status_code == 403
            await write("PATCH", "/me/questionnaire-research", {"version": questionnaire_report.CONSENT,
                        "revision": 0, "enabled": True, "adult_confirmed": True, "expected_member": uid})
            everyday = {}
            for key, v in variables.items():
                if v["domain"] == "everyday":
                    i = len(everyday) % len(v["choices"])
                    everyday[key[2:]] = {"choice": i, "question": v["label"], "label": v["choices"][i]}
                    expected[key] = i
            demo = {"age": 36, "height_cm": 176, "gender": "Non-binary", "sex": "Female",
                    "ethnicity": "Mixed", "education": "High school"}
            await write("PUT", "/me/about", {"expected_member": uid, "answers": everyday, "demographics_patch": demo, "mbti": "INFJ"})
            expected.update(age_band=2, height_band=3)
            expected.update({"d:" + k: variables["d:" + k]["choices"].index(v) for k, v in demo.items()
                             if "d:" + k in variables})
            expected["p:mbti"] = variables["p:mbti"]["choices"].index("INFJ")

            revision = 0
            for key, v in variables.items():
                if v["domain"] == "intimacy":
                    i = revision % len(research.CHOICES)
                    tags = list(research.CONDITIONS) if research.CHOICES[i] == "It depends" else []
                    response = await write("PATCH", "/me/intimacy-answers", {"id": key, "topic": v["topic"],
                        "question": v["label"], "phrase": v["phrase"], "adult_confirmed": True, "revision": revision,
                        "selection": {"choice": research.CHOICES[i], "conditions": tags}})
                    revision = response["revision"]
                    expected[key] = i

            threads = {}
            checkin = {"agreements": [{"checkins": {"a": {"result": "mixed", "updated_at": "2026-11-01"}}}]}
            for key, v in variables.items():
                if key.startswith("c:prompt:"):
                    starter = key.removeprefix("c:prompt:")
                    choice = v["choices"][-1]
                    await write("PUT", "/couples/drafts/" + starter, {"expected_member": uid, "version": 0,
                                "choice": choice, "lines": ["Private written context"]})
                    expected[key] = None  # Private drafts are not released research answers.
                    threads[starter] = {"kind": "prompt", "prompt_id": starter, **checkin}
                    expected["c:checkin:prompt:" + starter] = 1
            for kind in couples_journey.REFLECTIONS:
                threads[kind] = {"kind": kind, **checkin}
                expected["c:checkin:" + kind] = 1
            expected["c:checkin:any"] = 1
            await db.couples_pairs.insert_one({"env": server.ENV, "state": "active", "inviter": uid, "invitee": "partner",
                "members": [uid, "partner"], "journey": {"threads": threads}})

            assert expected.keys() == variables.keys()
            # Read exactly the same projections the reports use, with one synthetic respondent.
            keys = list(expected)
            for start in range(0, len(keys), 24):
                batch = keys[start:start + 24]
                axes = {f"v{i}": (key, None) for i, key in enumerate(batch)}
                stages = questionnaire_report.member_seat(server.IN_ENV, quiz, variables, axes)
                row, = await db.users.aggregate(stages + [{"$project": {"_id": 0, "seat": 1}}]).to_list(2)
                assert row["seat"] == {slot: expected[key] for slot, (key, _) in axes.items()}, batch

            identity["user_id"] = "coverage-admin"
            catalog = (await client.get("/api/admin/research/trends")).json()
            assert {v["id"] for v in catalog["variables"]} == set(expected)
            response = await client.get(path, params={"key": "e:" + next(iter(everyday))})
            assert response.status_code == 200 and response.headers["Cache-Control"] == "private, no-store"
            withheld = response.json()
            assert not withheld["available"] and withheld["rows"] == [] and withheld["sample_size"] is None
            assert "Private written context" not in response.text and uid not in response.text

    run_case(check)
