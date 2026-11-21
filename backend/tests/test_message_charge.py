import json
import time
import uuid
from unittest.mock import patch

import httpx
import pytest
from fastapi import BackgroundTasks, HTTPException
from pymongo.errors import AutoReconnect
from starlette.requests import Request

from backend import free_plan, message_charge, message_costs
from backend.tests.test_free_plan_quota import Question, run_case


def paid_member():
    now = int(time.time())
    return {"user_id": "synthetic-member", "tier": "paid", "role": "member", "env": "preview",
            "stripe_subscription_id": "sub_fixture", "subscription_period_start": now - 60,
            "subscription_period_end": now + 3600}


def test_refunded_retry_and_settlement_recovery_keep_one_cost_identity():
    async def check(db):
        user = paid_member()
        await db.users.insert_one(dict(user))
        request = Request({"type": "http", "headers": [(b"idempotency-key", b"one-message")]})
        seen = []

        async def send(generate):
            async def restore(doc):
                return doc["id"]
            return await free_plan.run_metered(db, user, request, Question(question="private"),
                                               "exchange", "person", generate, restore)

        async def failed(rid):
            assert message_costs.current_message.get() == (rid, "individual")
            assert message_costs.current_run.get() == 1
            seen.append(rid)
            raise HTTPException(502)

        async def saved(rid):
            assert message_costs.current_run.get() == 2
            seen.append(rid)
            await message_charge.enqueue(db, bg, background)
            await db.exchanges.insert_one({"id": rid, "user_id": user["user_id"]})
            return rid

        async def background():
            assert message_costs.current_message.get() == (seen[0], "individual")
            assert message_costs.current_run.get() == 2

        bg = BackgroundTasks()

        with pytest.raises(HTTPException):
            await send(failed)
        rid = seen[0]
        first = await db.message_cost_units.find_one({"_id": rid})
        assert first["status"] == "refunded"
        # The debit is durable; a lost reporting acknowledgement must still return the saved reply.
        with patch.object(message_charge, "flush_event", side_effect=AutoReconnect()):
            assert await send(saved) == rid
        charged = await db.users.find_one({"user_id": user["user_id"]})
        assert free_plan.reply_usage(charged)["messages_used"] == 1
        assert charged["message_cost_outbox"][rid]["status"] == "settled"
        await free_plan.flush_message_cost_outbox(db, "preview")
        await message_charge.persist(db, first)  # delayed first-refund delivery
        await bg()
        with await message_charge.saved_context(db, rid, "preview"):
            assert message_costs.current_message.get() == (rid, "individual")
            assert message_costs.current_run.get() is None
        assert await send(saved) == rid
        assert seen == [rid, rid]
        assert await db.message_cost_units.count_documents({}) == 1
        final = await db.message_cost_units.find_one({"_id": rid})
        assert (final["status"], final["pool"], final["workspace"]) == ("settled", "plan", "individual")
        assert final["background_pending"] == 0
        assert "private" not in str(final) and user["user_id"] not in str(final)
        assert not (await db.users.find_one({"user_id": user["user_id"]}))["message_cost_outbox"]
    run_case(check)


def test_paid_upload_retries_background_and_replay_hit_one_unit(monkeypatch):
    from backend import ai, server

    async def check(db):
        await db.message_cost_units.create_indexes(message_charge.INDEXES["message_cost_units"])
        member = paid_member()
        member["env"] = server.ENV
        await db.users.insert_one(dict(member))
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.subscribed_member, lambda: member)
        monkeypatch.setitem(server.app.dependency_overrides, server.reply_member, lambda: member)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk_test_isolated")
        monkeypatch.delenv("DEEPSEEK_PRODUCTION_KEY_ID", raising=False)
        original = httpx.AsyncClient.post
        order = []
        lines = [{"speaker":"them", "text":"Can we talk tomorrow?", "sure":True}]

        # Exercise the entire application. Only the upstream HTTP transport is mocked.
        async def post(client, url, **kwargs):
            if url != ai.DEEPSEEK_CHAT_URL:
                return await original(client, url, **kwargs)
            feature = message_costs._generation.get()[1]
            order.append(feature)
            result = {
                "transcribe_text":{"lines":lines}, "six_part":{"tone":{"status":"insufficient","drafts":{}}},
                "starters":{"starters":["Which next step makes sense?"]},
            }.get(feature, {})
            text = "invalid JSON" if feature=="six_part" and order.count(feature)==1 else json.dumps(result)
            return httpx.Response(200, json={"id":str(uuid.uuid4()), "created":int(time.time()), "model":"deepseek-flash",
                "choices":[{"finish_reason":"stop","message":{"content":text}}],
                "usage":{"prompt_tokens":5,"completion_tokens":7,"prompt_cache_hit_tokens":2,
                         "prompt_cache_miss_tokens":3,"total_tokens":12}})

        monkeypatch.setattr(httpx.AsyncClient, "post", post)
        upload = str(uuid.uuid4())
        headers = {"X-Upload-Attempt":upload,"Idempotency-Key":"customer-reply"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url="http://local.test") as http:
            response = await http.post("/api/quickstart/parse", headers=headers,
                                       json={"nickname":"Case","conversation_text":"Case: Can we talk tomorrow?"})
            response.raise_for_status()
            body = {"nickname":"Case","what_happened":"What might I ask next?","lines":response.json()["lines"]}
            result = await http.post("/api/quickstart", headers=headers, json=body)
            result.raise_for_status()
            rid = result.json()["exchange"]["id"]
            again = await http.post("/api/quickstart", headers=headers, json=body)
            assert again.status_code == 200 and again.json()["exchange"]["id"] == rid
            pid = result.json()["person"]["id"]
            url = "/api/people/" + pid
            loaded = (await http.get(url + "/starters")).json()
            assert (await http.get(url + "/starters")).json()["batch_id"] == loaded["batch_id"]
            for n in range(2):
                chosen = await http.post(url + "/exchanges", json={"question":loaded["starters"][0]},
                                         headers={"Idempotency-Key":f"new-message-{n}","X-Starter-Batch":loaded["batch_id"]})
                chosen.raise_for_status()
                if n == 0:
                    first_pick = chosen.json()["id"]
        assert order == ["transcribe_text","six_part","six_part","report","starters","six_part","six_part"]
        unit = await db.message_cost_units.find_one({"_id":rid})
        assert unit["status"] == "settled" and unit["background_pending"] == 0
        rows = await db.ai_call_ledger.find().to_list(None)
        assert len(rows) == 7
        prep, = [r for r in rows if r["feature"]=="transcribe_text"]
        assert prep["message_id"] is None and prep["preparation_id"]==unit["preparation_id"]
        assert prep["preparation_id"] != message_charge.upload_id("another-account", upload)
        assert len([r for r in rows if r["message_id"]==rid and r["workspace"]=="individual"]) == 3
        assert sum(r["retry"] for r in rows)==1
        assert any(r["mode"]=="background" and r["message_id"]==rid for r in rows)
        starter, = [r for r in rows if r["feature"]=="starters"]
        chosen_units = await db.message_cost_units.find({"preparation_id":starter["preparation_id"]}).to_list(None)
        assert [u["_id"] for u in chosen_units] == [first_pick]
        assert free_plan.reply_usage(await db.users.find_one({"user_id":member["user_id"]}))["messages_used"] == 3

    run_case(check)
