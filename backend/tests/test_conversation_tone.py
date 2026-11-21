import time
import uuid
from unittest.mock import AsyncMock

import httpx

from backend import conversation_tone as ct
from backend.free_plan import reply_usage
from backend.tests.test_free_plan_quota import run_case


def test_tone_requires_target_words_and_deduplicates_source_evidence():
    me = {"speaker":"me", "text":"I have been looking forward to our holiday."}
    brief = {"speaker":"them", "text":"Okay"}
    earlier = {"speaker":"them", "text":"Which afternoon is easiest for you?"}
    raw = {"status":"estimated", "incoming":"warm", "confidence":"moderate",
           "explanation":"Their invitation is specific.", "evidence":[0, 1, 1, 99, False, -1]}
    assert ct.normalize(raw, [me, brief, brief])["status"] == "insufficient"
    assert ct.normalize(raw, [me, earlier])["evidence"] == [1]
    assert ct.normalize(raw, [me, earlier], target="me")["evidence"] == [0]
    rows = [{"id":"recent", "conversation":[earlier, brief], "tone_feedback":{"fit":"corrected","note":"They type briefly at work."}},
            {"id":"old", "conversation":[earlier]}]
    past, notes, versions = ct.historical_context(rows, [brief])
    assert len(past) == 1 and past[0]["text"] == earlier["text"] and past[0]["exchange_id"] == "recent"
    assert notes == ["They type briefly at work."] and versions == {"recent":0}


def test_paid_quickstart_and_continuations_keep_evidence_quota_and_feedback_separate(monkeypatch):
    from backend import server

    async def scenario(db):
        uid = "tone-member"
        clock = int(time.time())
        member = {"user_id":uid, "role":"member", "tier":"paid", "stripe_subscription_id":"sub_tone_qa",
                  "subscription_period_start":clock-100, "subscription_period_end":clock+10000}
        await db.users.insert_one(dict(member))
        monkeypatch.setattr(server,"db",db)
        monkeypatch.setitem(server.app.dependency_overrides, server.subscribed_member, lambda: member)
        monkeypatch.setitem(server.app.dependency_overrides, server.reply_member, lambda: member)
        monkeypatch.setattr(server,"regenerate_report",AsyncMock())
        monkeypatch.setattr(server,"full_context",AsyncMock(return_value=("Avery","")))
        prompts = []
        incomplete = False
        async def model(feature, system, text, **kwargs):
            prompts.append(text)
            drafts = {s: s + " wording about Friday?" for s in ct.STYLES if not incomplete or s == "neutral"}
            return {"what_to_say":"Are we meeting Friday?", "what_it_means":"A specific plan.",
                    "tone":{"status":"estimated", "incoming":"warm","confidence":"moderate","explanation":"They ask about the Friday plan.",
                            "evidence":[0,1,2,5],"baseline_evidence":[0,1],"intent":"Confirm Friday.","drafts":drafts}}
        monkeypatch.setattr(server, "ai_json", model)
        def request_id(): return {"Idempotency-Key":str(uuid.uuid4())}
        async def used(): return reply_usage(await db.users.find_one({"user_id":uid}))["messages_used"]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url="http://test") as api:
            first = [{"speaker":"them","text":"Would Friday at the cafe work for you?"},
                     {"speaker":"me","text":"I'll check what time I finish work."},
                     {"speaker":"them","text":"I can meet by the patio."}]
            initial = {"nickname":"Avery", "what_happened":"Can we confirm Friday?", "lines":first}
            key = request_id()
            res = await api.post("/api/quickstart",json=initial,headers=key)
            assert res.is_success,res.text
            result = res.json()
            ex = result["exchange"]
            pid, eid = result["person"]["id"], ex["id"]
            assert ex["tone"]["evidence"] == [0,2] and set(ex["tone"]["drafts"]) == set(ct.STYLES)
            replay = await api.post("/api/quickstart",json=initial,headers=key)
            assert replay.json()["exchange"]["id"] == eid and await used() == 1 and len(prompts) == 1
            url = "/api/exchanges/" + eid + "/tone-feedback"
            assert (await api.post(url,json={"action":"helped","value":True})).status_code == 404
            assert (await api.post(url,json={"action":"copy","style":"warm"})).status_code == 404
            for style in ("warm","cool","warm"):
                selected = await api.post(url,json={"action":"select","style":style})
                assert selected.is_success
            assert selected.json()["styles"] == ["warm","cool"] and "sent" not in selected.json()
            copied = await api.post(url,json={"action":"copy","style":"warm"})
            assert copied.json()["copied_styles"] == ["warm"] and "sent" not in copied.json()
            await api.post(url,json={"action":"sent","value":True,"style":"warm"})
            await api.post(url,json={"action":"select","style":"cool"})
            outcome = await api.post(url,json={"action":"helped","value":False})
            assert outcome.json()["sent_style"] == "warm" and outcome.json()["helped"] is False
            changed = await api.post(url,json={"action":"sent","value":True,"style":"cool"})
            assert changed.json()["sent_style"] == "cool" and "helped" not in changed.json()
            assert (await api.post(url,json={"action":"helped","style":"warm","value":True})).status_code == 404
            await api.post(url,json={"action":"helped","style":"cool","value":False})
            assert (await api.post(url,json={"action":"clear_helped","style":"warm"})).status_code == 404
            assert (await api.post(url,json={"action":"clear_sent","style":"warm","value":True})).status_code == 404
            removed_help = await api.post(url,json={"action":"clear_helped","style":"cool"})
            assert removed_help.json()["sent"] and "helped" not in removed_help.json()
            await api.post(url,json={"action":"helped","style":"cool","value":True})
            for _ in range(2):
                removed_send = await api.post(url,json={"action":"clear_sent","style":"cool","value":True})
                assert not {"sent", "sent_style", "helped"}.intersection(removed_send.json())
                assert removed_send.json()["copied_styles"] == ["warm"]
            for _ in range(2): await api.post(url,json={"action":"fit","fit":"corrected","note":"The patio is our usual spot."})
            assert (await db.exchanges.find_one({"id":eid}))["tone_revision"] == 1 and await used() == 1
            chat = "/api/people/" + pid + "/exchanges"
            followup = {"conversation":[first[-1],{"speaker":"them","text":"Does six o'clock still work for you?"}],
                        "continue_exchange_id":eid,"context_goal":"Confirm Friday."}
            saved = await api.post(chat,json=followup,headers=request_id())
            assert saved.is_success,saved.text
            second = saved.json()
            assert second["tone_thread_id"] == eid and second["tone"]["intent"] == "Confirm Friday."
            assert second["tone"]["baseline_evidence"] == [{"exchange_id":eid,"index":0}]
            assert "The patio is our usual spot." in prompts[-1]
            assert (await api.get("/api/exchanges/" + second["id"] + "/tone-evidence")).json() == [first[0]["text"]]
            separate = await api.post(chat,json={"conversation":followup["conversation"]},headers=request_id())
            assert separate.json()["tone_thread_id"] != eid
            assert "The patio is our usual spot." not in prompts[-1]
            assert (await api.post(chat,json={"conversation":first,"continue_exchange_id":"nobody"},headers=request_id())).status_code == 404
            assert await used() == 3
            incomplete = True
            failed = await api.post(chat,json={"conversation":first},headers=request_id())
            assert failed.status_code == 502 and await used() == 3
            incomplete = False
            timing = await api.post(chat,json={"conversation":[{"speaker":"them","text":"Can you work Friday?"}],
                                    "question":"Confirm the shift hours?"},headers=request_id())
            assert timing.is_success, timing.text
            assert timing.json()["tone"]["drafts"] == ct.TIME_QUESTIONS["What time does the shift start and finish?"]
            remove_rating = "/api/exchanges/" + eid + "/tone-feedback"
            removed = await api.post(remove_rating,json={"action":"clear_fit"})
            assert removed.is_success and "fit" not in removed.json() and "note" not in removed.json()
            prior = await db.exchanges.find_one({"id":eid})
            repeated = await api.post(remove_rating,json={"action":"clear_fit"})
            assert repeated.json() == removed.json()
            assert (await db.exchanges.find_one({"id":eid}))["tone_revision"] == prior["tone_revision"]
            assert (await db.exchanges.find_one({"id":second["id"]}))["tone_invalidated"]
            assert (await api.delete("/api/exchanges/" + eid)).is_success
            rows = {row["id"]:row for row in (await api.get(chat)).json()}
            assert "tone" not in rows[second["id"]] and rows[second["id"]]["tone_invalidated"]
            assert "tone" in rows[separate.json()["id"]]
            stopped = await api.post(chat,json={"conversation":[{"speaker":"them","text":"Please stop contacting me."}]},headers=request_id())
            assert stopped.json()["response"]["contact_boundary"] and "tone" not in stopped.json()
            archived = {row["id"]:row for row in (await api.get(chat)).json()}[separate.json()["id"]]
            assert archived["tone_contact_paused"] and not archived["tone"]["drafts"]
            blocked = await api.post("/api/exchanges/" + archived["id"] + "/tone-feedback",json={"action":"select","style":"cool"})
            assert blocked.status_code == 409
            original_uid = member["user_id"]
            member["user_id"] = "someone-else"
            try:
                assert (await api.get("/api/exchanges/" + second["id"] + "/tone-evidence")).status_code == 404
                assert (await api.post("/api/exchanges/" + archived["id"] + "/tone-feedback",json={"action":"fit","fit":"fits"})).status_code == 404
            finally:
                member["user_id"] = original_uid
    run_case(scenario)
