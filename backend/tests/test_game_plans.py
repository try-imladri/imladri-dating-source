"""Observable Game Plan branches across generation, old records, retries and exports."""
import asyncio
import os
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorClient

from backend import content, game_plans


def test_forecasts_are_removed_but_real_quantities_and_contact_boundaries_survive():
    raw = {"opening_line": "Could we discuss a 10% raise?", "exit_line": "Let's take a break.",
           "reactions": [{"reaction": "A 72% chance they change the subject.", "likelihood": 72},
                         {"reaction": "They ask to see your figures.", "what_it_tells_you": "They secretly agree.",
                          "how_to_handle": "Offer the 10% comparison from your notes."}]}
    assert game_plans.clean_plan(raw)["reactions"] == [
        {"reaction": "They ask to see your figures.", "how_to_handle": "Offer the 10% comparison from your notes."}]
    with pytest.raises(HTTPException) as exc:
        game_plans.clean_plan(raw, generated=True)
    assert exc.value.status_code == 502
    blocked = game_plans.clean_plan({**raw, "contact_boundary": True, "avoid": ["Give them space."]}, generated=True)
    assert blocked["opening_line"] is None and blocked["exit_line"] is None and blocked["reactions"] == []
    assert blocked["avoid"] == ["Give them space."]


def test_game_plan_flow_normalizes_saved_predictions_and_migrates_only_exact_defaults(monkeypatch):
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("A disposable loopback MongoDB is required.")
    assert urlsplit(uri).hostname in {"localhost", "127.0.0.1", "::1"}
    name = "game_plan_test_" + uuid4().hex
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", name)
    from backend import server

    async def run():
        mongo = AsyncIOMotorClient(uri)
        db = mongo[name]
        monkeypatch.setattr(server, "db", db)
        uid = "plan-member"
        now = int(time.time())
        async def member(request: Request):
            return await db.users.find_one({"user_id": uid}, {"_id": 0})
        monkeypatch.setitem(server.app.dependency_overrides, server.current_member, member)
        calls = []
        async def provider(feature, system, prompt, **kwargs):
            assert feature == "prepare" and '"likelihood":' not in prompt
            calls.append(prompt)
            if "UPCOMING ENCOUNTER: Unavailable provider" in prompt:
                raise HTTPException(503, "Upstream unavailable.")
            if "UPCOMING ENCOUNTER: Repeated malformed plan" in prompt or len(calls) == 1:
                return {"reactions": [{"reaction": "They pause."}]}
            return {"opening_line": "When could we talk through the schedule?", "exit_line": "I can wait until Friday.",
                    "reactions": [{"reaction": reaction, "how_to_handle": response, "likelihood": 42}
                                  for reaction, response in [("They offer a time.", "Put it on the calendar."),
                                      ("They ask for details.", "Share one specific example."),
                                      ("They say they are busy.", "Ask for a better time.")]]}
        monkeypatch.setattr(server, "ai_json", provider)
        try:
            await db.users.insert_one({"user_id": uid, "role": "member", "tier": "paid", "env": server.ENV,
                                       "stripe_subscription_id": "sub_test", "subscription_period_start": now-120,
                                       "subscription_period_end": now+3000})
            for version in (content.LEGACY_MEMBER_PROMPTS, content.PREVIOUS_PROMPT_DEFAULTS):
                await db.settings.replace_one({"key":"global"}, {"key":"global", "prompts":version}, upsert=True)
                await content.migrate_prompt_defaults(db)
                actual = (await db.settings.find_one({"key":"global"}))["prompts"]
                assert all(actual[key] == content.DEFAULT_PROMPTS[key] for key in version)
            customized = {"prepare":"Keep my own examples.", "profile_patterns":"My wording.", "practice_insights":"My examples."}
            await db.settings.update_one({"key":"global"},{"$set":{"prompts":customized}})
            await content.migrate_prompt_defaults(db)
            assert (await db.settings.find_one({"key":"global"}))["prompts"] == customized
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://local.test/api/") as client:
                res = await client.post("people",json={"nickname":"Jo","rel_type":"coworker","stage":"good","background":"We meet to coordinate our shifts."})
                assert res.is_success, res.text
                pid = res.json()["id"]
                path = f"people/{pid}/preps"
                form = {"json":{"situation":"A scheduling conversation tomorrow."}, "headers":{"Idempotency-Key":"prepare-once"}}
                fresh = await client.post(path, **form)
                assert fresh.is_success, fresh.text
                prep = fresh.json()
                assert len(prep["plan"]["reactions"]) == 3
                assert all(set(branch) == {"reaction","how_to_handle"} for branch in prep["plan"]["reactions"])
                archived = {"opening_line":"May we talk later?", "exit_line":"Let's pause.", "reactions":[
                    {"reaction":"They offer Friday.", "likelihood":70, "what_it_tells_you":"A private motive.",
                     "how_to_handle":"Check your calendar."}, "They pause."]}
                await db.preps.update_one({"id":prep["id"]},{"$set":{"plan":archived, "outcome":"Booked Friday."}})
                expected = game_plans.clean_plan(archived)
                repeat = await client.post(path, **form)
                listing = await client.get(path)
                export = await client.get(f"people/{pid}/export")
                account = await client.get("me/export")
                for response in (repeat, listing, export, account):
                    assert response.is_success, response.text
                for item in (repeat.json(), listing.json()[0], export.json()["preps"][0], account.json()["preps"][0]):
                    assert item["plan"] == expected and item["outcome"] == "Booked Friday."
                assert len(calls) == 2 and "Return a complete plan" in calls[1]
                assert (await client.get("me")).json()["usage"]["messages_remaining"] == 99
                malformed = await client.post(path, json={"situation": "Repeated malformed plan"}, headers={"Idempotency-Key": "bad-shape"})
                assert malformed.status_code == 502 and len(calls) == 4
                failed = await client.post(path, json={"situation": "Unavailable provider"}, headers={"Idempotency-Key": "bad-provider"})
                assert failed.status_code == 503 and len(calls) == 5
                assert (await client.get("me")).json()["usage"]["messages_remaining"] == 99
                assert await db.preps.count_documents({"user_id": uid}) == 1
        finally:
            await mongo.drop_database(name)
            mongo.close()
    asyncio.run(run())
