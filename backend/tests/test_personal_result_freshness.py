import asyncio
from collections import Counter
import time

import httpx


def test_downstream_results_follow_the_saved_profile(monkeypatch):
    from backend import database
    from backend.tests.test_prompt_default_revisions import run_case
    monkeypatch.setattr(database, "open_database", lambda: (None, None))
    from backend import server
    from backend import archetype_copy

    async def run(db):
        monkeypatch.setattr(server, "db", db)
        uid = "synthetic-member"
        calls = Counter()
        deeper = []
        report_blocked, release = asyncio.Event(), asyncio.Event()
        wait_for_report = False
        discard_ranking = False

        async def current_member():
            return await db.users.find_one({"user_id": uid}, {"_id": 0})

        async def provider(instruction, prompt, image=None):
            nonlocal wait_for_report
            updated = "NEW_DEEP_ANSWER" in prompt
            label = "current" if updated else "previous"
            expected = "NEW_DEEP_ANSWER" if updated else "OLD_DEEP_ANSWER"
            assert all(f"{expected}_{q['id']}_z" in prompt for q in deeper)
            if "PREVIOUS REPORT:" in prompt:
                calls["report"] += 1
                if wait_for_report:
                    wait_for_report = False
                    report_blocked.set()
                    await release.wait()
                if updated:
                    assert "OLD_DEEP_ANSWER" not in prompt
                assert "[S1]" in prompt
                return {"sections": [], "compatibility": {"summary": label, "summary_source_ids": ["S1"]},
                        "change_note": expected, "change_source_ids": ["S1"]}
            if "Write exactly 3 short questions" in instruction:
                calls["starters"] += 1
                return {"starters": [label, "another", "third"]}
            if "covering every archetype" in instruction:
                assert "Unreleased" not in prompt
                if discard_ranking:
                    await db.users.update_one({"user_id": uid}, {"$unset": {"about": ""}})
                calls["ranking"] += 1
                return {"items": [{"number": 1, "line": label, "score": 65}]}
            if "ONE short, punchy line" in instruction:
                calls["comparison"] += 1
                return {"line": label}
            raise AssertionError(instruction)

        monkeypatch.setattr(server, "provider_json", provider)
        monkeypatch.setitem(server.app.dependency_overrides, server.current_member, current_member)
        try:
            await db.users.insert_one({"user_id": uid, "role": "member", "tier": "paid", "onboarded": True,
                                       "stripe_subscription_id": "synthetic", "subscription_period_start": int(time.time()-60),
                                       "subscription_period_end": int(time.time()+3600)})
            await db.archetypes.insert_many([
                {"number": 1, "name": "Example", "slug": "example", "gender": "male", "one_liner": "Example description", "unlocked": True},
                {"number": 2, "name": "Unreleased", "slug": "future", "gender": "male", "one_liner": "Future description", "unlocked": False},
            ])
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://local.test") as client:
                async def api(method, path, data=None):
                    response = await client.request(method, "/api" + path, json=data)
                    response.raise_for_status()
                    return response.json()

                quiz = await api("GET", "/quiz")
                deeper = quiz["extended"] + quiz["further"]
                assert len(deeper) == 96

                async def save(marker):
                    choice = 0 if marker == "OLD_DEEP_ANSWER" else 3
                    answers = {q["id"]: {"choice": choice, "note": f"{marker}_{q['id']}_z"} for q in deeper}
                    await api("PUT", "/me/about", {"mbti": "ISFP", "answers": answers})

                await save("OLD_DEEP_ANSWER")
                first, second = [(await api("POST", "/people", {"nickname": nick, "rel_type": "partner", "stage": "established", "background": "Synthetic example"}))["id"]
                                 for nick in ("Alex", "Lee")]
                person = "/people/" + first
                await api("PATCH", person, {"archetype_tag": 1})
                await api("GET", "/people")
                catalog = {"name": "Example", "one_liner": "Example description"}
                caption = archetype_copy.catalog_line(catalog)
                old_match = (await db.people.find_one({"id": first}))["type_match"]
                assert (await api("GET", person))["type_match"]["line"] == caption
                assert old_match["historical_unverified_rationales"][0]["line"] == "previous"
                assert old_match["line"] == archetype_copy.catalog_line(catalog, 200)
                assert old_match["code"] == "ISFP" and old_match["tag"] == 1 and old_match["started"]
                profile = await server.saved_profile(await current_member(), (await server.get_settings())["quiz"])
                assert old_match["profile_sig"] == server.context_signature(server.saved_profile_text(*profile))
                assert (await api("POST", person + "/report/refresh"))["report_status"] == "ready"

                async def starters_and_ranking(expected):
                    assert (await api("GET", person + "/starters"))["starters"][0] == expected
                    assert (await api("POST", "/group-chat/starters", {"person_ids": [second, first]}))["starters"][0] == expected
                    ranking = await api("GET", "/me/type-compare")
                    assert ranking["items"][0]["line"] == caption
                    raw = (await db.users.find_one({"user_id": uid}))["about"]["type_compare"]
                    assert raw["items"][0]["historical_unverified_rationales"][0]["line"] == expected
                    assert raw["items"][0]["line"] == caption
                    assert [(row["number"], row["score"]) for row in raw["items"]] == [(1, 65)]
                    assert raw["sig"] == ranking["sig"] and raw["started"] and raw["code"] == "ISFP"
                    return raw

                old_ranking = await starters_and_ranking("previous")
                assert await starters_and_ranking("previous") == old_ranking
                assert calls == {"report": 1, "comparison": 1, "starters": 2, "ranking": 1}
                wait_for_report = True
                old_refresh = asyncio.create_task(api("POST", person + "/report/refresh"))
                await asyncio.wait_for(report_blocked.wait(), timeout=10)
                try:
                    await save("NEW_DEEP_ANSWER")
                    assert calls == {"report": 2, "comparison": 1, "starters": 2, "ranking": 1}
                    assert (await api("GET", person))["report_stale"]
                    assert (await api("GET", person))["type_match"] == {"stale": True}
                    assert (await db.people.find_one({"id": first}))["type_match"] == old_match
                    await api("GET", "/people")
                    assert calls["comparison"] == 1
                    fresh = await api("POST", person + "/report/refresh")
                    assert fresh["report_status"] == "ready" and not fresh.get("report_stale")
                    assert fresh["report"]["compatibility"]["summary"] == "current"
                    assert (await api("POST", person + "/type-match/refresh"))["type_match"]["line"] == caption
                    new_match = (await db.people.find_one({"id": first}))["type_match"]
                    assert new_match["historical_unverified_rationales"][0]["line"] == "current"
                    assert new_match["code"] == old_match["code"] and new_match["tag"] == old_match["tag"]
                    assert new_match["started"] >= old_match["started"]
                    profile = await server.saved_profile(await current_member(), (await server.get_settings())["quiz"])
                    assert new_match["profile_sig"] == server.context_signature(server.saved_profile_text(*profile))
                    assert new_match["profile_sig"] != old_match["profile_sig"]
                    new_ranking = await starters_and_ranking("current")
                    assert new_ranking["sig"] != old_ranking["sig"]
                    assert new_ranking["started"] >= old_ranking["started"]
                finally:
                    release.set()
                    await old_refresh
                assert (await api("GET", person))["report"]["compatibility"]["summary"] == "current"
                assert (await db.people.find_one({"id": first}))["type_match"] == new_match
                assert (await db.people.find_one({"id": first}))["report"]["profile_sig"] == new_match["profile_sig"]
                assert await starters_and_ranking("current") == new_ranking
                assert calls == {"report": 3, "comparison": 2, "starters": 4, "ranking": 2}
                await db.people.update_one({"id": first}, {"$unset": {"report.profile_sig": "", "type_match.profile_sig": ""}})
                legacy = await api("GET", person)
                assert legacy["report_status"] == "stale" and legacy["type_match"] == {"stale": True}
                await db.users.update_one({"user_id":uid},{"$unset":{"about.type_compare":""}})
                discard_ranking = True
                interrupted = await client.get("/api/me/type-compare")
                assert interrupted.status_code == 409 and "items" not in interrupted.json()
                assert "about" not in await db.users.find_one({"user_id":uid})
                assert (await api("GET","/me/type-compare"))["items"] == []
                discard_ranking = False
                await save("NEW_DEEP_ANSWER")
                await db.people.update_one({"id":first},{"$set":{"deleting":True}})
                await db.people.insert_many([{"id":f"extra-{i}", "user_id":uid,"nickname":f"Extra {i}",
                    "rel_type":"partner","archetype_tag":1} for i in range(201)])
                people = (await api("GET","/me/type-compare"))["items"][0]["people"]
                assert len(people) == 201 and first not in {p["id"] for p in people}
        finally:
            server.app.dependency_overrides.pop(server.current_member, None)

    run_case(run)
