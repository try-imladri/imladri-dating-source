import asyncio
import os
import time
from datetime import date, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Header, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from backend.couples import COUPLES_INDEXES
from backend.couples_analytics import report
from backend.couples_journey import Action, JOURNEY_INDEXES, PROMPTS, apply_action, journey_router, new_thread, own_journey_export


def test_adjusting_an_accepted_agreement_preserves_its_history_and_does_not_mix_revisions():
    thread = new_thread("support-1", "prompt", next(p for p in PROMPTS if p["id"] == "support-1"))
    thread["shares"] = {"a": ["Listen"], "b": ["Ask first"]}
    day = "2026-11-09"
    stats = {}

    def act(side, action, **fields):
        apply_action(thread, side, Action(pair_id="pair", version=0, action=action, **fields),
                     stats, "2026-11-08T14:00:00+00:00")

    act("a", "propose", text="Ask first", review_on=day)
    first = thread["agreements"][0]["id"]
    act("b", "accept", agreement_id=first)
    act("a", "checkin", agreement_id=first, result="mixed", share=True)
    act("b", "propose", agreement_id=first, text="Ask after a pause", review_on=day)
    version = thread["agreements"][1]
    assert thread["agreements"][0]["status"] == "retired"
    assert thread["agreements"][0]["checkins"]["a"]["result"] == "mixed"
    assert version["revision"] == 2 and version["checkins"] == {}
    act("a", "accept", agreement_id=version["id"])
    act("b", "checkin", agreement_id=version["id"], result="helped", share=True)
    assert set(stats["first_checkins"]) == {"b"} and "current_outcomes" not in stats
    act("a", "checkin", agreement_id=version["id"], result="helped", share=True)
    assert stats["current_outcomes"] == {"a": "helped", "b": "helped"}


def test_proposal_uses_the_members_calendar_date_at_midnight():
    thread = new_thread("support-1", "prompt", PROMPTS[0])
    thread["shares"] = {"a": ["Talk"], "b": ["Listen"]}
    timestamp = "2026-11-08T01:30:00+00:00"
    def propose(day, offset):
        action = Action(pair_id="pair", version=2, action="propose", text="Talk after dinner",
                        review_on=day, utc_offset_minutes=offset)
        apply_action(thread, "a", action, {}, timestamp)

    propose("2026-11-07", -360)
    assert thread["agreements"][0]["review_on"] == "2026-11-07"
    for day, offset in (("2026-11-06", -360), ("2026-11-07", 540)):
        with pytest.raises(HTTPException) as error:
            propose(day, offset)
        assert error.value.status_code == 422
    propose("2026-11-08", 540)


def test_checkins_shared_at_different_times_require_an_actual_mutual_reveal():
    thread = new_thread("support-1", "prompt", PROMPTS[0])
    thread["shares"] = {"a": ["Talk"], "b": ["Listen"]}
    stats = {}
    def act(minute, side, action, **fields):
        at = f"2026-11-08T14:{minute:02d}:00+00:00"
        apply_action(thread, side, Action(pair_id="pair", version=0, action=action, **fields), stats, at)
        return at

    act(0, "a", "propose", text="Talk after dinner", review_on="2026-11-09")
    aid = thread["agreements"][0]["id"]
    act(1, "b", "accept", agreement_id=aid)
    act(2, "a", "checkin", agreement_id=aid, result="helped", share=True)
    act(3, "a", "withdraw_checkin", agreement_id=aid)
    act(4, "b", "checkin", agreement_id=aid, result="mixed", share=True)
    assert len(stats["first_checkins"]) == 2 and stats["first_both_checked_in_at"] is None
    at = act(5, "a", "checkin", agreement_id=aid, result="helped", share=True)
    assert stats["first_both_checked_in_at"] == at
    act(6, "b", "withdraw_checkin", agreement_id=aid)
    assert stats["first_both_checked_in_at"] == at and "current_outcomes" not in stats


def test_two_partners_share_agree_review_withdraw_and_unpair():
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Set IMLADRI_TEST_MONGO_URL to a disposable loopback MongoDB.")
    assert urlsplit(uri).hostname in {"localhost", "127.0.0.1", "::1"}

    async def scenario():
        mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        db = mongo["couples_journey_" + uuid4().hex]
        app = FastAPI()

        async def member(x_member: str = Header()):
            return await db.users.find_one({"user_id": x_member})

        router = APIRouter(prefix="/api")
        router.include_router(journey_router(lambda: db, member))
        app.include_router(router)
        try:
            for collection, indexes in {**JOURNEY_INDEXES, **COUPLES_INDEXES}.items():
                await db[collection].create_indexes(indexes)
            t = int(time.time())
            people = [{"user_id": uid, "env": "preview", "role": "member",
                       "couples_adult_at": "2026-01-01T10:00:00+00:00"} for uid in ("alex", "jordan", "guest", "teen")]
            people[0].update(tier="paid", stripe_subscription_id="fixture", subscription_period_start=t - 60,
                             subscription_period_end=t + 86400)
            people[-1].update(about={"demographics": {"age": "17"}})
            await db.users.insert_many(people)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as api:
                async def call(uid, method, path, body=None, status=200):
                    response = await api.request(method, "/api/couples"+path, headers={"x-member": uid}, json=body)
                    assert response.status_code == status, response.text
                    return response.json()

                async def view(uid):
                    return await call(uid, "GET", "/journey")

                key = "support-1"
                alex = "I want a listening ear."
                secret = "Private note about my day."
                jordan = "Ask which kind of help I want."
                day = (date.today() + timedelta(days=5)).isoformat()
                pair = "synthetic-pair"
                await call("teen", "GET", "/journey", status=403)
                overview = await view("alex")
                assert len(overview["prompts"]) == 12
                assert len({row["topic"] for row in overview["prompts"]}) == 4
                assert overview["pair_id"] is None

                async def draft(uid, lines, version=0, thread=key):
                    return await call(uid, "PUT", "/drafts/"+thread, {"version": version, "lines": lines})

                await draft("alex", ["ERASE THIS"], thread="repair-1")
                erased = await call("alex", "DELETE", "/drafts/repair-1?version=1")
                assert "ERASE THIS" not in str(erased) and not erased["drafts"]
                await call("alex", "PUT", "/drafts/repair-1", {"version": 0, "lines": ["ERASE THIS"]}, 409)
                await call("alex", "PUT", "/drafts/repair-1", {"version": 1, "lines": ["ERASE THIS"]}, 409)
                await draft("alex", ["New words"], erased["draft_versions"]["repair-1"], thread="repair-1")
                await draft("alex", [alex, secret])
                await call("jordan", "PUT", "/drafts/"+key, {"version": 0, "lines": [jordan]}, 402)
                await call("alex", "PUT", "/drafts/"+key, {"version": 0, "lines": ["stale draft"]}, 409)
                await call("jordan", "GET", "/journey", status=402)
                assert (await db.users.find_one({"user_id": "alex"})).get("couples_first_draft_at")
                await db.couples_pairs.insert_one({"_id": pair, "state": "active", "env": "preview",
                    "members": ["alex", "jordan"], "inviter": "alex", "invitee": "jordan", "sponsor": "alex"})

                async def shared_features():
                    data = await report(db, "preview", {"role": "member"}, 7)
                    assert not any(value in str(data) for value in (alex, jordan, secret, "Ask first.", "PRIVATE REVIEW"))
                    return data["shared_features"]

                zero = {"prompt": 0, "moment": 0, "positive": 0, "learning": 0}
                assert await shared_features() == {"active_pairs": 1, "counts": zero}
                await draft("jordan", [jordan])
                assert secret not in str(await view("jordan"))

                async def command(uid, version, action, *, thread=key, status=200, **fields):
                    return await call(uid, "POST", "/threads/"+thread, {"pair_id": pair, "version": version,
                                    "action": action, "request_id": uuid4().hex, **fields}, status)

                def active_thread(response, tid=key):
                    return next(item for item in response["threads"] if item["id"] == tid)

                await call("alex", "DELETE", "/drafts/repair-1?version=3")
                await command("alex", 0, "share", thread="repair-1", kind="prompt", lines=["New words"], status=409)
                await command("alex", 0, "share", kind="prompt", lines=["not in draft"], status=409)
                await command("alex", 0, "share", kind="prompt", lines=[alex])
                assert (await shared_features())["counts"] == zero
                one = await view("jordan")
                assert active_thread(one)["partner_shared"] and not active_thread(one)["mutual"]
                assert alex not in str(one) and secret not in str(one)
                await call("guest", "GET", "/journey", status=402)
                await command("guest", 1, "propose", text="private", review_on=day, status=404)
                await command("jordan", 1, "propose", text="premature", review_on=day, status=409)
                result = await command("jordan", 1, "share", kind="prompt", lines=[jordan])
                assert active_thread(result)["perspectives"] == [{"mine": True, "lines": [jordan]}, {"mine": False, "lines": [alex]}]
                assert (await shared_features())["counts"] == {**zero, "prompt": 1}
                assert secret not in str(result)
                await draft("alex", ["A new private wording", secret], 1)
                assert "A new private wording" not in str(await view("jordan"))
                proposal = {"pair_id": pair, "version": 2, "action": "propose", "request_id": uuid4().hex,
                            "text": "Ask what support would help first.", "review_on": day}
                duplicates = await asyncio.gather(*(call("alex", "POST", "/threads/"+key, proposal) for _ in range(3)))
                listing = active_thread(duplicates[0])["agreements"]
                assert len(listing) == 1
                aid = listing[0]["id"]
                assert listing[0]["accepted_by_me"] and not listing[0]["accepted_by_partner"]
                await command("alex", 3, "checkin", agreement_id=aid, result="mixed", status=409)
                result = await command("jordan", 3, "propose", agreement_id=aid, text="Ask before starting to help.", review_on=day)
                assert active_thread(result)["agreements"][0]["revision"] == 2
                await command("alex", 3, "accept", agreement_id=aid, status=409)
                await command("alex", 4, "accept", agreement_id=aid)
                await command("alex", 5, "checkin", agreement_id=aid, result="mixed", note="PRIVATE REVIEW", share=False)
                assert "PRIVATE REVIEW" not in str(await view("jordan"))
                await command("jordan", 6, "checkin", agreement_id=aid, result="helped", note="SHARED JORDAN", share=True)
                assert "SHARED JORDAN" not in str(await view("alex"))
                full = await command("alex", 7, "checkin", agreement_id=aid, result="mixed", note="SHARED ALEX", share=True)
                assert active_thread(full)["agreements"][0]["partner_checkin"]["result"] == "helped"
                stats = (await db.couples_pairs.find_one({"_id": pair}))["stats"]
                assert stats["first_agreement_revision"] == 2
                assert stats["current_outcomes"] == {"a": "mixed", "b": "helped"}
                assert set(stats["first_checkins"]) == {"a", "b"} and "SHARED" not in str(stats)
                await command("jordan", 8, "pin", agreement_id=aid, text="Ask first.")
                assert (await shared_features())["counts"]["learning"] == 0
                pinned = await command("alex", 9, "accept_pin", agreement_id=aid)
                assert (await shared_features())["counts"]["learning"] == 1
                assert active_thread(pinned)["agreements"][0]["pin"]["active"]
                exported = str(await own_journey_export(db, "alex"))
                assert alex in exported and "SHARED ALEX" in exported and "SHARED JORDAN" not in exported and "Ask first." not in exported

                moment = "positive-"+str(uuid4())
                await draft("alex", ["Good morning", "", "Make tea"], thread=moment)
                await command("alex", 0, "share", thread=moment, kind="positive", lines=["Make tea"])
                pending = active_thread(await view("jordan"), moment)
                assert pending["kind"] == "positive" and "Make tea" not in str(pending)
                await draft("jordan", ["The break helped"], thread=moment)
                await command("jordan", 1, "share", thread=moment, kind="positive", lines=["The break helped"])
                assert (await shared_features())["counts"] == {**zero, "prompt": 1, "positive": 1, "learning": 1}
                assert (await db.couples_pairs.find_one({"_id": pair}))["stats"]["second_mutual_at"]

                await db.users.update_one({"user_id": "alex"}, {"$set": {"subscription_period_end": t - 1}})
                await command("alex", 10, "propose", text="Expired", review_on=day, status=402)
                await command("alex", 10, "withdraw_checkin", agreement_id=aid)
                assert (await shared_features())["counts"]["learning"] == 0
                assert active_thread(await view("jordan"))["agreements"][0]["pin"] is None
                assert "current_outcomes" not in (await db.couples_pairs.find_one({"_id": pair}))["stats"]
                await command("alex", 11, "withdraw")
                assert (await shared_features())["counts"] == {**zero, "positive": 1}
                assert "Ask before starting" not in str(await view("jordan"))
                assert "SHARED ALEX" not in str(await view("jordan"))
                await command("jordan", 12, "withdraw")
                assert key not in [thread["id"] for thread in (await view("alex"))["threads"]]
                assert (await view("alex"))["share_versions"][key] == 13
                await db.couples_pairs.update_one({"_id": pair}, {"$set": {"state": "closed"}, "$unset": {"journey": "", "members": ""}})
                await call("alex", "GET", "/journey", status=402)
                assert secret in str(await db.couples_drafts.find_one({"user_id":"alex", "key":key}))
                assert not await own_journey_export(db, "jordan")
                await command("alex", 13, "withdraw", status=404)
        finally:
            await mongo.drop_database(db.name)
            mongo.close()

    asyncio.run(scenario())
