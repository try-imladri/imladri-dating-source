"""Exercise both coach entry points using real source revisions and two signed-in members."""
import asyncio
import json
import time
import uuid

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException

from backend import couples_tone
from backend.couples import couples_router, COUPLES_INDEXES
from backend.couples_journey import journey_router, JOURNEY_INDEXES, own_journey_export
from backend.free_plan import initialize_usage, paid_cycle, reply_usage
from backend.message_costs import current_message, current_run
from backend.tests.test_free_plan_quota import run_case


GROCERY_LINE = "Could we decide this evening which day to buy groceries?"
GROCERY_DRAFTS = {
    "warm": "I'd like to pick a grocery day together this evening. Would that work?",
    "neutral": "Can we pick a day to buy groceries this evening?",
    "direct": "Let's decide this evening which day we'll shop for groceries.",
    "playful": "Can we pencil in a grocery day together this evening?",
    "cool": "Can we settle on a grocery day this evening?",
}


def grocery_read(drafts):
    return {"status": "estimated", "incoming": "neutral", "confidence": "limited", "evidence": [0],
            "explanation": "The question invites a joint decision; the wording alone gives limited evidence.",
            "drafts": drafts}


def parse_grocery(raw, mode="private"):
    target = "me" if mode == "private" else "them"
    return couples_tone.coach_tone(raw, [{"speaker": target, "text": GROCERY_LINE}], target,
                                  "Pick a day for groceries.", mode=mode, boundary=False)


def test_couples_tone_preserves_wrapped_content_and_the_explicit_shared_no_reply():
    flat = parse_grocery(grocery_read(GROCERY_DRAFTS))
    assert flat["drafts"] == GROCERY_DRAFTS and flat["evidence"] == [0]
    alias = dict(GROCERY_DRAFTS)
    alias["reserved"] = alias.pop("cool")
    assert parse_grocery({"tone": grocery_read(alias), "boundary_evidence": None}) == flat
    assert parse_grocery(grocery_read({}), mode="shared")["drafts"] == {}
    assert len(parse_grocery(grocery_read({**GROCERY_DRAFTS, "warm": "x"*900}))["drafts"]["warm"]) == 900


@pytest.mark.parametrize(("raw", "mode"), [
    ({"tone": grocery_read(GROCERY_DRAFTS), "drafts": GROCERY_DRAFTS}, "private"),
    ({"tone": grocery_read(GROCERY_DRAFTS), "boundary_evidence": "Please stop calling me."}, "shared"),
    ({"recommendation": grocery_read(GROCERY_DRAFTS)}, "private"),
    ({"tone": json.dumps(grocery_read(GROCERY_DRAFTS))}, "private"),
    (grocery_read({}), "private"),
    (grocery_read({"warm": {"text": GROCERY_LINE}}), "shared"),
    (grocery_read({"unexpected_style": GROCERY_LINE}), "shared"),
    (grocery_read({**GROCERY_DRAFTS, "reserved": GROCERY_LINE}), "private"),
    (grocery_read({**GROCERY_DRAFTS, "neutral": "   "}), "private"),
    (grocery_read({**GROCERY_DRAFTS, "neutral": " " + GROCERY_DRAFTS["warm"].upper() + " "}), "private"),
    (grocery_read({**GROCERY_DRAFTS, "playful": "x"*901}), "shared"),
])
def test_couples_tone_rejects_ambiguous_or_unusable_drafts(raw, mode):
    with pytest.raises(HTTPException) as response:
        parse_grocery(raw, mode)
    assert response.value.status_code == 502


def test_sponsored_private_grocery_retry_preserves_the_receipt_and_charges_once():
    async def scenario(db):
        now = int(time.time())
        common = {"role": "member", "env": "preview", "couples_adult_at": "2026-01-01T00:00:00+00:00"}
        await db.users.insert_many([
            {**common, "user_id": "grocery-owner", "tier": "paid", "stripe_subscription_id": "fixture",
             "subscription_period_start": now-60, "subscription_period_end": now+86400},
            {**common, "user_id": "grocery-guest"},
        ])
        await db.couples_pairs.insert_one({"_id": "grocery-pair", "members": ["grocery-owner", "grocery-guest"],
            "inviter": "grocery-owner", "invitee": "grocery-guest", "sponsor": "grocery-owner",
            "state": "active", "env": "preview"})
        calls = []

        async def member():
            return await db.users.find_one({"user_id": "grocery-guest"})

        async def generate(feature, system, prompt):
            assert feature == "couples_tone"
            assert json.loads(prompt)["source_lines"] == [{"index": 0, "speaker": "me", "text": GROCERY_LINE}]
            calls.append((current_message.get(), current_run.get()))
            if len(calls) == 1:
                return {"tone": grocery_read({"warm": {"text": GROCERY_LINE}})}
            return {"tone": grocery_read(GROCERY_DRAFTS)}

        app = FastAPI()
        app.include_router(journey_router(lambda: db, member, generate), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as api:
            saved = await api.put("/api/couples/drafts/support-1", json={"version": 0, "lines": [GROCERY_LINE, "Another private line."]})
            assert saved.status_code == 200
            submit = {"json": {"pair_id": "grocery-pair", "version": 1}, "headers": {"Idempotency-Key": "grocery-private"}}
            path = "/api/couples/tone/private/support-1"
            assert (await api.post(path, **submit)).status_code == 502
            rid = calls[0][0][0]
            assert (await db.message_cost_units.find_one({"_id": rid}))["status"] == "refunded"
            assert reply_usage(await db.users.find_one({"user_id": "grocery-owner"}))["messages_used"] == 0
            success = await api.post(path, **submit)
            assert success.status_code == 200 and success.json()["tone"]["drafts"] == GROCERY_DRAFTS
            assert success.json()["id"] == (await api.post(path, **submit)).json()["id"] == rid
            assert calls == [((rid, "couples"), 1), ((rid, "couples"), 2)]
            assert (await db.message_cost_units.find_one({"_id": rid}))["status"] == "settled"
            assert reply_usage(await db.users.find_one({"user_id": "grocery-owner"}))["messages_used"] == 1
            assert reply_usage(await db.users.find_one({"user_id": "grocery-guest"}))["messages_used"] == 0

    run_case(scenario)


def test_private_and_mutual_coaching_end_to_end():
    async def scenario(db):
        for collection, indexes in {**JOURNEY_INDEXES, **COUPLES_INDEXES}.items():
            await db[collection].create_indexes(indexes)
        t = int(time.time())
        host, guest, outsider, teen = ["tone-" + uid for uid in ("host", "guest", "outside", "teen")]
        pair = "pair-tone"
        users = [{"user_id": uid, "env": "preview", "role": "member", "couples_adult_at": "2026-01-01T00:00:00+00:00"}
                 for uid in (host, guest, outsider, teen)]
        users[0].update(tier="paid", stripe_subscription_id="fixture", subscription_period_start=t-60, subscription_period_end=t+86400)
        users[-1]["about"] = {"demographics": {"age": "16"}}
        await db.users.insert_many(users)
        app = FastAPI()

        async def member(x_member: str = Header()):
            return await db.users.find_one({"user_id": x_member})

        calls, wait, resume = [], asyncio.Event(), asyncio.Event()
        cost_messages = []

        async def generate(feature, system, prompt):
            assert feature == "couples_tone"
            cost_messages.append(current_message.get())
            data = json.loads(prompt)
            calls.append(data)
            if data["purpose"] in ("Wait for withdrawal.", "Wait for agreement."):
                wait.set()
                await resume.wait()
            if data["purpose"] == "Broken upstream.":
                return {"status": "insufficient", "drafts": {"direct": "Shall we pick a time?"}}
            target = data["focus_speaker"]
            return {"status": "estimated", "incoming": "warm", "confidence": "moderate",
                    "explanation": "The wording invites a conversation.", "evidence": [item["index"] for item in data["source_lines"] if item["speaker"] == target],
                    "drafts": {style: style + " clarification" for style in ("warm", "neutral", "direct", "playful", "cool")}}

        app.include_router(journey_router(lambda: db, member, generate), prefix="/api")
        app.include_router(couples_router(lambda: db, member), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as api:
            async def call(uid, method, path, data=None, status=200, request_key=None):
                headers = {"x-member": uid}
                if request_key:
                    headers["Idempotency-Key"] = request_key
                response = await api.request(method, "/api/couples" + path, headers=headers, json=data)
                assert response.status_code == status, (path, response.text)
                return response.json()

            async def used(uid):
                return reply_usage(await db.users.find_one({"user_id": uid}))["messages_used"]

            key = "support-1"
            private_path, shared_path = ["/tone/" + kind + "/" + key for kind in ("private", "shared")]
            draft = "/drafts/" + key
            private_secret = "Private extra line about a gift."
            await call(host, "PUT", draft, {"version": 0, "lines": ["I would like to listen to you.", private_secret]})
            assert await call(host, "POST", private_path + "/saved", {"version": 1}) == {"available": False}
            assert not calls and await used(host) == 0
            solo = await call(host, "POST", private_path, {"version": 1})
            assert (await call(host, "POST", private_path + "/saved", {"version": 1}))["id"] == solo["id"]
            assert await call(host, "POST", private_path + "/saved", {"version": 1, "line":1}) == {"available": False}
            assert solo["tone"]["status"] == "estimated" and solo["tone"]["evidence"] == [0]
            assert calls[-1]["source_lines"] == [{"index": 0, "speaker": "me", "text": "I would like to listen to you."}]
            assert (await call(host, "POST", private_path, {"version": 1}))["id"] == solo["id"] and len(calls) == 1
            assert await used(host) == 1
            for action in ("sent", "helped"):
                await call(host, "PATCH", private_path, {"version": 1, "coach_id": solo["id"],
                           "change": {"action":action, "value":True, "style":"warm"}}, 422)
            await call(guest, "POST", private_path, {"version": 1}, 402)
            await call(teen, "POST", private_path, {"version": 1}, 403)
            await db.couples_pairs.insert_one({"_id": pair, "state": "active", "env": "preview",
                "members": [host, guest], "inviter": host, "invitee": guest, "sponsor": host})
            await call(guest, "PUT", draft, {"version": 0, "lines": ["Can we speak about tomorrow together?", "Other private line."]})
            input_private = {"version": 1, "pair_id": pair}
            invited = await call(guest, "POST", private_path, input_private, request_key="guest-private")
            assert len(calls) == 2 and invited["id"] != solo["id"]
            assert (await call(guest, "POST", private_path, input_private, request_key="guest-private"))["id"] == invited["id"]
            assert await used(host) == 2 and await used(guest) == 0
            assert cost_messages == [(solo["id"], "couples"), (invited["id"], "couples")]
            sponsor_receipt = await db.message_cost_units.find_one({"_id": invited["id"]})
            assert (sponsor_receipt["status"], sponsor_receipt["workspace"], sponsor_receipt["pool"]) == (
                "settled", "couples", "plan")
            assert await db.message_cost_units.count_documents({"status":"settled"}) == 2
            assert host not in str(sponsor_receipt) and guest not in str(sponsor_receipt)
            assert (await call(host, "GET", ""))["usage"]["messages_remaining"] == 98
            assert "usage" not in await call(guest, "GET", "")
            await call(host, "PATCH", private_path, {**input_private, "coach_id": invited["id"],
                                                    "change": {"action": "fit", "fit": "fits"}}, 409)
            await call(outsider, "POST", shared_path, input_private, 409)

            async def share(uid, lines, version):
                return await call(uid, "POST", "/threads/" + key, {"pair_id": pair, "action": "share",
                                  "kind": "prompt", "lines": lines, "version": version, "request_id": str(uuid.uuid4())})

            await share(host, ["I would like to listen to you."], 0)
            await call(guest, "POST", shared_path, input_private, 409)
            await share(guest, ["Can we speak about tomorrow together?"], 1)
            current = {"pair_id": pair, "version": 2, "goal": "Agree a time for tomorrow."}
            await call(host, "POST", shared_path, {**current, "goal": "Broken upstream."}, 502)
            assert await used(host) == 2
            read = await call(host, "POST", shared_path, current)
            assert read["tone"]["evidence"] == [1] and len(calls[-1]["source_lines"]) == 2
            assert private_secret not in str(calls[-1]) and "Other private line." not in str(calls[-1])
            assert (await call(host, "POST", shared_path, current))["id"] == read["id"]
            assert calls[-1]["purpose"] == current["goal"] and len(calls) == 4
            assert await call(guest, "POST", shared_path + "/saved", current) == {"available": False}
            assert (await call(host, "POST", shared_path + "/saved", current))["id"] == read["id"]
            assert await call(host, "POST", shared_path + "/saved", {**current,"goal":"Another goal."}) == {"available": False}
            peer = await call(guest, "POST", shared_path, current)
            assert peer["id"] != read["id"] and peer["tone_feedback"] == {}
            assert calls[-1]["source_lines"][1]["text"] == "I would like to listen to you."
            async def change(who, read_id, data, status=200):
                return await call(who, "PATCH", shared_path, {"pair_id": pair, "version": 2,
                                  "coach_id": read_id, "change": data}, status)
            await change(guest, read["id"], {"action": "select", "style": "cool"}, 409)
            await change(host, read["id"], {"action": "copy", "style": "direct"}, 409)
            await change(host, read["id"], {"action": "helped", "value": True}, 409)
            await change(host, read["id"], {"action": "select", "style": "direct"})
            await change(host, read["id"], {"action": "copy", "style": "direct"})
            await change(host, read["id"], {"action": "fit", "fit": "corrected", "note": "We say this kindly."})
            sent = await change(host, read["id"], {"action": "sent", "style": "direct", "value": True})
            helped = await change(host, read["id"], {"action": "helped", "value": True})
            assert sent["sent_style"] == "direct" and helped["helped"] and len(calls) == 5
            await change(host, read["id"], {"action": "select", "style": "warm"})
            revised = await change(host, read["id"], {"action": "sent", "style": "warm", "value": True})
            assert revised["sent_style"] == "warm" and "helped" not in revised
            assert "We say this kindly." not in str(await call(guest, "GET", "/journey"))
            assert "We say this kindly." not in str(await own_journey_export(db, guest))
            assert "We say this kindly." in str(await own_journey_export(db, host))
            pair_doc = await db.couples_pairs.find_one({"_id": pair})
            assert set(pair_doc["stats"]["tone_usage"]) == {"private_last_at", "shared_last_at"}
            assert (await call(guest, "POST", shared_path, current))["tone_feedback"] == {}
            assert await used(host) == 4 and await used(guest) == 0

            # Existing pairs may predate separate revisions for shared words and agreement activity.
            await db.couples_pairs.update_one({"_id": pair}, {"$unset": {f"journey.threads.{key}.words_version": ""}})
            await call(guest, "POST", "/threads/" + key, {"pair_id": pair, "action": "propose", "version": 2,
                       "text": "Check in next week", "review_on": "2099-01-01"})
            proposed = (await call(host, "GET", "/journey"))["threads"][0]
            updated = {**current, "version": proposed["version"]}
            reopened = await call(host, "POST", shared_path, updated)
            assert reopened["id"] == read["id"] and reopened["tone_feedback"]["sent_style"] == "warm"
            assert (await call(guest, "POST", shared_path, current))["id"] == peer["id"]
            assert len(calls) == 5 and await used(host) == 4
            assert "We say this kindly." in str(await own_journey_export(db, host))
            assert (await change(host, read["id"], {"action": "select", "style": "direct"}))["sent_style"] == "warm"
            removed = await change(host, read["id"], {"action":"clear_fit"})
            assert removed["sent_style"] == "warm" and "fit" not in removed and "note" not in removed
            assert "We say this kindly." not in str(await own_journey_export(db, host))
            await change(host, read["id"], {"action":"helped", "style":"warm", "value":False})
            await change(host, read["id"], {"action":"clear_sent", "style":"direct", "value":True}, 409)
            helpfulness = await change(host, read["id"], {"action":"clear_helped", "style":"warm"})
            assert helpfulness["sent"] and "helped" not in helpfulness
            await change(host, read["id"], {"action":"helped", "style":"warm", "value":True})
            for _ in range(2):
                cleared = await change(host, read["id"], {"action":"clear_sent", "value":True, "style":"warm"})
                assert not {"sent", "helped", "sent_style"}.intersection(cleared)
                assert cleared["copied_styles"] == ["direct"]

            # An agreement changing during a new read must not discard the model response or charge twice.
            waiting_goal = {**updated, "goal": "Wait for agreement."}
            in_flight = asyncio.create_task(call(host, "POST", shared_path, waiting_goal))
            await asyncio.wait_for(wait.wait(), 5)
            await call(host, "POST", "/threads/" + key, {"pair_id": pair, "action": "accept",
                       "agreement_id": proposed["agreements"][0]["id"], "version": proposed["version"]})
            resume.set()
            finished = await in_flight
            accepted = (await call(host, "GET", "/journey"))["threads"][0]
            assert accepted["words_version"] == 2 and accepted["version"] > proposed["version"]
            assert (await call(host, "POST", shared_path, {**waiting_goal, "version": accepted["version"]}))["id"] == finished["id"]
            assert len(calls) == 6 and await used(host) == 5
            wait.clear()
            resume.clear()

            await db.users.update_one({"user_id": host}, {"$set": {"subscription_period_end": t-1}})
            await call(guest, "POST", shared_path, current, 402)
            await db.users.update_one({"user_id": host}, {"$set": {"subscription_period_end": t+86400}})
            pending = asyncio.create_task(call(host, "POST", shared_path, {**current, "goal": "Wait for withdrawal."}, 409))
            await asyncio.wait_for(wait.wait(), 5)
            await call(host, "POST", shared_path, {**current, "goal": "Wait for withdrawal."}, 409)
            await call(guest, "POST", "/threads/" + key, {"pair_id": pair, "action": "withdraw", "version": accepted["version"]})
            resume.set()
            await pending
            assert await used(host) == 5
            await call(host, "POST", shared_path, current, 409)
            pair_doc = await db.couples_pairs.find_one({"_id": pair})
            assert not pair_doc.get("tone_sessions", {}).get(key) and not pair_doc.get("tone_jobs", {}).get(key)
            assert "We say this kindly." not in str(pair_doc)

            await call(guest, "PUT", draft, {"version": 1, "lines": ["Please do not contact me again."]})
            assert "tone_coach" not in await db.couples_drafts.find_one({"user_id": guest, "key": key})
            withdrawn = accepted["version"] + 1
            await share(guest, ["Please do not contact me again."], withdrawn)
            n = len(calls)
            await call(host, "POST", shared_path, updated, 409)
            paused = await call(host, "POST", shared_path, {**current, "version": withdrawn + 1})
            assert paused["boundary"] and paused["tone"]["drafts"] == {} and len(calls) == n
            assert await used(host) == 5
            await call(guest, "POST", "/leave", {"pair_id": pair})
            await call(guest, "POST", shared_path, {**current, "version": withdrawn + 1}, 409)
            closed = await db.couples_pairs.find_one({"_id": pair})
            assert "tone_sessions" not in closed and "journey" not in closed
            own_read = await call(host, "POST", private_path, {"version":1})
            await call(host, "PATCH", private_path, {"version":1, "coach_id":own_read["id"],
                       "change":{"action":"fit", "fit":"corrected", "note":"This first line is an apology."}})
            another_line = await call(host, "POST", private_path, {"version":1, "line":1})
            assert another_line["id"] != own_read["id"]
            assert calls[-1]["source_lines"][0]["text"] == private_secret
            assert "my_context" not in calls[-1]
            await call(host, "DELETE", draft+"?version=1")
            erased = await db.couples_drafts.find_one({"user_id": host, "key": key})
            assert "tone_coach" not in erased and private_secret not in str(erased)

    run_case(scenario)


def test_interrupted_sponsored_reservations_recover_from_the_source_without_reading_its_words():
    async def scenario(db):
        t = int(time.time())
        sponsor = {"user_id": "sponsor", "tier": "paid", "stripe_subscription_id": "sub_one",
                   "plan_offer": "couple_annual", "subscription_period_start": t-60, "subscription_period_end": t+31536000}
        cycle = paid_cycle(sponsor)
        source_id = (await db.couples_drafts.insert_one({"user_id": "guest", "key": "support-1",
                     "tone_coach": {"id": "delivered-private", "tone": {"intent": "Private words"}}})).inserted_id
        await db.couples_pairs.insert_one({"_id": "pair", "tone_sessions": {"support-1": {
            "b": {"id": "delivered-shared", "tone": {"intent": "A private goal"}}}}})
        requests = {}
        for result, document, slot in (("delivered-private", source_id, "tone_coach"),
                                       ("delivered-shared", "pair", "tone_sessions.support-1.b"),
                                       ("interrupted", source_id, "tone_coach")):
            requests[result] = {"kind": "couples_tone", "scope": {"document": document, "slot": slot},
                                "pending": True, "expires": "2000-01-01T00:00:00+00:00", "cycle": cycle,
                                "period_end": t+86400, "result": result}
        sponsor.update(free_reply_requests=requests, paid_reply_cycles={cycle: {"claimed": 3, "used": 0}})
        await db.users.insert_one(dict(sponsor))
        recovered = await initialize_usage(db, sponsor)
        assert reply_usage(recovered)["messages_used"] == 2 and reply_usage(recovered)["messages_remaining"] == 98
        assert "interrupted" not in recovered["free_reply_requests"]
        assert all(not entry["pending"] for entry in recovered["free_reply_requests"].values())
        assert "Private words" not in str(recovered) and "A private goal" not in str(recovered)
        assert await initialize_usage(db, recovered) == recovered

    run_case(scenario)


@pytest.mark.parametrize("closing", [False, True])
def test_member_export_and_data_removal_are_isolated_between_seats(monkeypatch, closing):
    from backend import server

    async def scenario(db):
        owner, peer = "tone-export-a", "tone-export-b"
        pair_id, key = "pair-export", "repair-1"
        async def signed_in(x_member: str = Header()):
            return await db.users.find_one({"user_id": x_member})
        monkeypatch.setattr(server, "db", db)
        for dependency in (server.current_member, server.require_user):
            monkeypatch.setitem(server.app.dependency_overrides, dependency, signed_in)
        await db.users.insert_many([{"user_id": uid, "env": "preview", "role": "member"} for uid in (owner, peer)])
        coaches = {}
        for side, uid in (("a", owner), ("b", peer)):
            coach = {"id": str(uuid.uuid4()), "source_version": 2, "user_id": uid, "pair_id": pair_id,
                     "tone": {"intent": "MY_GOAL_" + side, "status": "estimated", "incoming": "warm",
                              "explanation": "MODEL_REPEATING_SHARED_WORDS_" + side, "drafts": {"warm": "private rewrite " + side}},
                     "tone_feedback": {"note": "PRIVATE_FEEDBACK_" + side}, "signature": "INTERNAL_SIGNATURE"}
            coaches[side] = coach
            await db.couples_drafts.insert_one({"user_id": uid, "key": key, "version": 2,
                "lines": ["PRIVATE_LINE_" + side], "tone_coach": coach, "tone_job": {"id": "INTERNAL_LOCK"}})
        await db.couples_pairs.insert_one({"_id": pair_id, "env": "preview", "state": "active", "members": [owner, peer],
            "inviter": owner, "invitee": peer, "sponsor": owner, "tone_sessions": {key: coaches},
            "journey": {"threads": {key: {"id": key, "title": "A repair", "version": 2,
                "shares": {"a": ["SHARED_A"], "b": ["SHARED_B"]}, "agreements": []}}}})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            for uid, mine, theirs in ((owner, "a", "b"), (peer, "b", "a")):
                response = await api.get("/api/me/export", headers={"x-member": uid})
                assert response.status_code == 200
                own = response.json()
                shared = own["couples_shared_contributions"][0]
                assert shared["own_share"] == ["SHARED_" + mine.upper()]
                assert shared["my_tone_coaching"]["feedback"]["note"] == "PRIVATE_FEEDBACK_" + mine
                assert "MODEL_REPEATING_SHARED_WORDS" not in str(shared)
                assert own["couples_drafts"][0]["lines"] == ["PRIVATE_LINE_" + mine]
                for private in ("INTERNAL_LOCK", "INTERNAL_SIGNATURE", "PRIVATE_LINE_" + theirs, "PRIVATE_FEEDBACK_" + theirs,
                                "SHARED_" + theirs.upper(), "MY_GOAL_" + theirs):
                    assert private not in response.text
            route = "/api/me/account" if closing else "/api/me"
            response = await api.delete(route, headers={"x-member": owner})
            assert response.is_success, response.text
            assert not await db.couples_pairs.find_one({"_id": pair_id})
            assert not await db.couples_drafts.find_one({"user_id": owner})
            assert await db.couples_drafts.find_one({"user_id": peer})
            remaining = (await api.get("/api/me/export", headers={"x-member": peer})).json()
            assert remaining["couples_shared_contributions"] == []
            assert remaining["couples_drafts"][0]["lines"] == ["PRIVATE_LINE_b"]

    run_case(scenario)
