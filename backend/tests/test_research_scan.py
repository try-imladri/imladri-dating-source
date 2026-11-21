import os

import pytest

from backend import couple_research, questionnaire_report as qr, research_scan as scan, research_trends as trends
from backend.quiz import default_quiz
from backend.tests.test_free_plan_quota import run_case


@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires a disposable loopback MongoDB.")
def test_empty_catalog_scan_uses_count_only_and_creates_no_plan(monkeypatch):
    async def no_catalog(*args):
        pytest.fail("A group below threshold triggered a questionnaire scan")
    monkeypatch.setattr(trends, "catalog", no_catalog)
    async def check(db):
        for unit in qr.UNITS:
            result = await scan.page(db, {"env": "preview"}, None, unit)
            assert result == {"phase": "done", "matches": [], "next_cursor": None, "tested": 0}
        assert await db.questionnaire_scan_plans.count_documents({}) == 0
    run_case(check)


@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires a disposable loopback MongoDB.")
def test_whole_catalog_discovers_without_a_chosen_axis_and_rechecks_withdrawals(monkeypatch):
    # Force multiple comparison blocks for a deliberately sparse, known population.
    monkeypatch.setattr(scan, "BLOCK_SIZE", 2)
    async def check(db):
        eligible = {"env": "preview"}
        quiz = default_quiz()
        q = quiz["short"][0]
        ckey = "c:prompt:communication-1"
        c = (await couple_research.variables(db))[ckey]
        users, pairs, drafts = [], [], []
        count = 0
        for i, group in enumerate(((30, 10), (10, 30))):
            for j, n in enumerate(group):
                for _ in range(n):
                    count += 1
                    a, b = f"m{count}a", f"m{count}b"
                    pairs.append({"_id": f"p{count}", "env": "preview", "state": "active", "members": [a,b],
                                  "inviter": a, "invitee": b})
                    for uid, val in ((a,i),(b,j)):
                        demo={"age": [22,32][val], "height_cm": [158,188][val], "gender": ["Woman","Man"][val]}
                        # One seat lacks height. This exercises partial and dual-orientation weights.
                        if count <= 5 and uid == b:
                            del demo["height_cm"]
                        users.append({"user_id": uid, "env": "preview", "role": "member", "couples_adult_at": "now",
                                      "questionnaire_research_version": qr.CONSENT,
                                      "about": {"demographics": demo, "answers": {q["id"]: {"choice": val}}}})
                        drafts.append({"user_id": uid, "key": "communication-1", "choice": c["choices"][val],
                                       "lines": ["secret private words"]})
        await db.users.insert_many(users)
        await db.couples_pairs.insert_many(pairs)
        await db.couples_drafts.insert_many(drafts)

        async def sweep(unit):
            cursor, token, hits, tested = 0, None, {}, 0
            while True:
                data = await scan.page(db,eligible,quiz,unit,token,cursor)
                assert "secret private words" not in str(data)
                token, cursor = data["plan"], data["next_cursor"]
                for item in data.get("matches", []):
                    key=tuple(item["indices"])
                    assert key not in hits
                    hits[key]=item
                tested += data.get("tested",0)
                if data.get("verify"):
                    break
            return token,cursor,hits,tested
        for unit in ("member","couple","couple_inviter"):
            token, cursor, hits, tested = await sweep(unit)
            assert tested < 50 and any({r["x"],r["y"]}=={"height_band",ckey} for r in hits.values())
            assert not any(row["x"].startswith("i:") or row["y"].startswith("i:") for row in hits.values())
            selected = [r for r in hits.values() if {r["x"],r["y"]} in ({"height_band",ckey},{"age_band","d:gender"})]
            official = await scan.page(db,eligible,quiz,unit,token,cursor,[r["indices"] for r in selected])
            assert official["phase"] == "done" and official["matches"] == sorted(selected,key=scan.ranking)
            for r in official["matches"]:
                direct=await trends.report(db,eligible,quiz,r["x"],r["y"],unit=unit)
                assert (direct["sample_size"],direct["cramers_v"])==(r["sample_size"],r["cramers_v"])
            if unit=="couple_inviter":
                # This was a ten-couple directional cell at scoring time.
                await db.users.update_one({"user_id":"m31a"},{"$unset":{"questionnaire_research_version":""}})
                rechecked=await scan.page(db,eligible,quiz,unit,token,cursor,[r["indices"] for r in selected])
                assert not rechecked["matches"]
        stored=await db.questionnaire_scan_plans.find_one({})
        assert set(stored)=={"_id","env","unit","catalog","expires_at","screening"}
        assert "private words" not in str(stored) and "m1a" not in str(stored)
    run_case(check)
