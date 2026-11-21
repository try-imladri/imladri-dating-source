import asyncio
import ast
import json
import os
import time
from types import SimpleNamespace
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi import Request
from motor.motor_asyncio import AsyncIOMotorClient


def test_all_member_generation_features_have_a_named_admin_instruction_and_usage_row():
    from backend import couples_tone, prompt_library, server

    features = set()
    for module, generator in ((server, "ai_json"), (couples_tone, "generate")):
        source = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for call in ast.walk(source):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == generator:
                arg = call.args[0]
                if isinstance(arg, ast.Starred):  # Router dependency forwards the actual call in couples_tone.
                    continue
                assert isinstance(arg, ast.Constant)
                features.add(arg.value)
    assert features == set(prompt_library.USAGE_FEATURES)
    assert set(prompt_library.USAGE_FEATURES) <= {key for group in prompt_library.AREAS.values() for key in group}
    assert prompt_library.instruction_keys("couples_tone") == ("couples_tone", "incoming_tone", "reply_style")


def test_admin_prompt_publication_reaches_member_generation_and_aggregate_reporting(monkeypatch):
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("A disposable loopback MongoDB is required.")
    assert urlsplit(uri).hostname in {"localhost", "127.0.0.1", "::1"}
    name = "prompt_flow_" + uuid4().hex
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", name)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test")
    from backend import ai, server

    async def run():
        mongo = AsyncIOMotorClient(uri)
        db = mongo[name]
        monkeypatch.setattr(server, "db", db)
        calls = []
        def provider(request):
            assert request.url.host == "api.deepseek.com"
            data = json.loads(request.content)
            system, user = [m["content"] for m in data["messages"]]
            calls.append((system, user))
            if "synthetic pre-publication" in system:
                result = {"answer":"I’ll give them the space they asked for." if "stop messaging" in user else "Is Friday still good for you?",
                          "would_send":"stop messaging" not in user,"reply_options":[]}
            elif "Write exactly 3 short questions" in system:
                result = {"starters": ["New draft?" if "STARTER_MARKER" in system else "Original?", "Second?", "Third?"]}
            elif "PRACTICE rehearsal" in system:
                if "TRY_LATER" in user:
                    return httpx.Response(503)
                result = {"reply": "Let's talk after dinner."}
            elif "PREVIOUS REPORT" in user:
                result = {"sections": []}
            else:
                result = {"what_it_means": "A synthetic read", "tone": {"status": "insufficient", "drafts": {}}}
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]})
        transport = httpx.MockTransport(provider)
        monkeypatch.setattr(ai, "httpx", SimpleNamespace(AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
                          Timeout=httpx.Timeout, TimeoutException=httpx.TimeoutException, RequestError=httpx.RequestError))
        async def member(request: Request):
            return await db.users.find_one({"user_id": request.headers["x-member"]}, {"_id": 0})
        monkeypatch.setitem(server.app.dependency_overrides, server.current_member, member)
        uid = "person-qa"
        now = int(time.time())
        try:
            await db.users.insert_many([{"user_id": "staff-qa", "role": "admin", "env": server.ENV},
                {"user_id": uid, "role": "member", "tier": "paid", "env": server.ENV,
                 "stripe_subscription_id": "sub_fixture", "subscription_period_start": now-100, "subscription_period_end": now+3600}])
            await db.settings.insert_one({"key": "global", "prompts": {"work": "A legacy published instruction."}})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://local.test") as client:
                async def call(method, path, body=None, who=uid, status=200):
                    response = await client.request(method, "/api"+path, json=body, headers={"x-member":who})
                    assert response.status_code == status, response.text
                    return response.json()

                pid = (await call("POST", "/people", {"nickname":"Jordan","rel_type":"partner","stage":"established",
                    "background":"PRIVATE_MEMBER_CONTEXT"}))["id"]
                starters = f"/people/{pid}/starters"
                assert (await call("GET", starters))["starters"][0] == "Original?"
                assert (await call("GET", starters))["starters"][0] == "Original?"
                assert sum("Write exactly 3 short questions" in s for s, _ in calls) == 1

                await call("GET","/admin/prompts",status=403)
                await call("GET","/admin/prompts/usage",status=403)
                await call("POST","/admin/prompts/preview",{"key":"practice_reply","text":"hidden"},status=403)
                staff = "staff-qa"
                library = await call("GET","/admin/prompts",who=staff)
                assert set(library["prompts"]) == {k for group in library["areas"].values() for k in group}
                assert library["prompts"]["work"] == "A legacy published instruction."
                assert not library["revision"]
                patch = {"six_part":"SIX_PART_MARKER", "starters":"STARTER_MARKER", "practice_reply":"REHEARSAL_MARKER"}
                prior = len(calls)
                preview = await call("POST","/admin/prompts/preview",{"key":"six_part","text":patch["six_part"]},staff)
                assert preview["keys"] == ["established","six_part","incoming_tone","reply_style"]
                for context in ("six_part", "couples_tone"):
                    staged = await call("POST", "/admin/prompts/preview", {"key":"reply_style", "text":"PRIVATE_STYLE_EDIT", "context":context}, staff)
                    assert context in staged["keys"] and "PRIVATE_STYLE_EDIT" in staged["guidance"]
                assert len(calls) == prior
                assert "SIX_PART_MARKER" in preview["guidance"] and len(calls) == prior
                await call("PUT","/admin/settings",{"prompts":patch},status=403)
                await call("PUT","/admin/settings",{"prompts":{"starters":" "}},staff,422)
                await call("PUT","/admin/settings",{"prompts":{"unknown":"editor error"}},staff,422)
                await call("PUT","/admin/settings",{"prompts":patch,"prompts_revision":None},staff,409)
                candidate = await call("POST","/admin/prompts/evaluate",{"prompts":patch,"prompts_revision":None},staff)
                assert candidate["eligible"] and all(r["input"] and r["answer"] for r in candidate["cases"])
                saved = await call("PUT","/admin/settings",{"prompts":patch,"prompts_revision":None,"reviewed_candidate":candidate["id"]},staff)
                assert saved["prompts_revision"] and saved["prompts"]["work"] == library["prompts"]["work"]
                await call("PUT","/admin/settings",{"prompts":{"starters":"stale edit"},"prompts_revision":None},staff,409)

                assert (await call("GET",starters))["starters"][0] == "New draft?"
                await call("GET",starters)
                await call("POST",f"/people/{pid}/exchanges",{"question":"PRIVATE_MEMBER_QUESTION"})
                assert any(preview["guidance"] in s and "PRIVATE_MEMBER_QUESTION" in u for s,u in calls)
                session = await call("POST","/practice",{"person_id":pid,"topic":"Practice talking","mode":"live"})
                turn = f"/practice/{session['id']}/say"
                reply = await call("POST",turn,{"text":"PRIVATE_PRACTICE_DETAIL"})
                assert reply["messages"][-1]["text"] == "Let's talk after dinner."
                assert any("REHEARSAL_MARKER" in s and "PRIVATE_PRACTICE_DETAIL" in u for s,u in calls)
                await call("POST",turn,{"text":"TRY_LATER"},status=502)
                report = await call("GET","/admin/prompts/usage?days=7",who=staff)
                assert report["counts"]["starters"] == {"succeeded":2,"failed":0}
                assert report["counts"]["six_part"] == {"succeeded":1,"failed":0}
                assert report["counts"]["practice_reply"] == {"succeeded":1,"failed":1}
                assert all(word not in str(report) for word in ("PRIVATE", "MARKER", uid, "Jordan", "Let's talk"))
                stored = await db.prompt_usage.find_one({})
                assert set(stored) == {"_id", "env", "day", "counts"}
                assert all(word not in str(stored) for word in ("PRIVATE", "MARKER", uid, "Jordan"))
        finally:
            await mongo.drop_database(name)
            mongo.close()
    asyncio.run(run())
