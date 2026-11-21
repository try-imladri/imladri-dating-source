import asyncio
import os
import time
import uuid
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from backend import couples
from backend.couples_analytics import report


def test_pairing_consent_exclusivity_and_sponsor_transfer():
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Set IMLADRI_TEST_MONGO_URL to a disposable loopback MongoDB.")
    assert urlsplit(uri).hostname in ("localhost", "127.0.0.1", "::1")

    async def run():
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        db = client["couples_pairing_fixture_" + uuid.uuid4().hex]
        try:
            for name, models in couples.COUPLES_INDEXES.items():
                await db[name].create_indexes(models)
            paid = {"tier": "paid", "stripe_subscription_id": "verified-test", "subscription_period_start": int(time.time()) - 1000,
                    "subscription_period_end": int(time.time()) + 1000}
            for uid in ("ada", "lea", "max", "friend", "young"):
                await db.users.insert_one({"user_id": uid, "env": "preview", "nickname": uid.title(), "tier": "free",
                                           "email": f"{uid}@fixture.invalid", **(paid if uid in ("ada", "max") else {})})
            await db.users.update_one({"user_id": "young"}, {"$set": {"about.demographics.age": 17}})

            async def member(identity: str = Header()):
                return await db.users.find_one({"user_id": identity})

            app = FastAPI()
            app.include_router(couples.couples_router(lambda: db, member))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as http:
                async def req(uid, method, endpoint, body=None, status=200):
                    res = await http.request(method, "/couples" + endpoint, headers={"identity": uid}, json=body)
                    assert res.status_code == status, (uid, method, endpoint, res.status_code, res.text)
                    return res.json()

                await req("young", "POST", "/adult", {"confirmed": True}, 403)
                await req("ada", "POST", "/invite", {}, 403)
                # Existing deployments may have paused invitations and denied this account.
                await db.couples_settings.insert_one({"_id": "pilot:preview", "mode": "paused", "revision": 3})
                await db.users.update_one({"user_id": "ada"}, {"$set": {"couples_pilot_enrolled": False}})
                for uid in ("ada", "lea", "max", "friend"):
                    await req(uid, "POST", "/adult", {"confirmed": True})
                await req("lea", "POST", "/invite", {}, 402)
                workspace = await req("ada", "GET", "")
                assert workspace["can_sponsor"] and workspace["invite_access"] == {"sponsored_seat_used": False}
                first_offer = (await db.users.find_one({"user_id": "ada"}))["couples_first_offer_at"]
                issued = await req("ada", "POST", "/invite")
                first = issued["token"]
                issued = await req("ada", "POST", "/invite")
                token = issued["token"]
                pair = {"pair_id": issued["workspace"]["pair_id"]}
                assert await db.couples_pairs.count_documents({}) == 1
                assert token not in str(await db.couples_pairs.find_one({}))
                for _ in range(2):
                    assert (await req("friend", "POST", "/invite/decline", {"token": token}))["ok"]
                await req("friend", "POST", "/invite/preview", {"token": token}, 404)
                await req("friend", "POST", "/invite/accept", {"token": token}, 404)
                await req("lea", "POST", "/invite/preview", {"token": first}, 404)
                await req("ada", "POST", "/invite/accept", {"token": token}, 404)
                assert (await req("lea", "POST", "/invite/preview", {"token": token}))["inviter_name"] == "Ada"
                await db.users.update_one({"user_id": "ada"}, {"$set": {"subscription_period_end": int(time.time()) - 1}})
                await req("lea", "POST", "/invite/accept", {"token": token}, 409)
                assert "accepted_at" not in await db.couples_pairs.find_one({"_id": pair["pair_id"]})
                await db.users.update_one({"user_id": "ada"}, {"$set": paid})
                await db.couples_settings.update_one({"_id": "pilot:preview"}, {"$set": {"mode": "pilot"}})
                two = await req("max", "POST", "/invite")
                assert (await db.users.find_one({"user_id": "ada"}))["couples_first_offer_at"] == first_offer
                accepted = await asyncio.gather(*[http.post("/couples/invite/accept", headers={"identity": "lea"}, json={"token": t})
                                                   for t in (token, two["token"])])
                assert sorted(r.status_code for r in accepted) == [200, 409]
                # Leave the winning pending pair and set up a deterministic example.
                state = await req("lea", "GET", "")
                assert state["state"] == "confirming"
                await req("lea", "POST", "/leave", {"pair_id": state["pair_id"]})
                for uid in ("ada", "max"):
                    state = await req(uid, "GET", "")
                    if state["pair_id"]:
                        await req(uid, "POST", "/leave", {"pair_id": state["pair_id"]})
                issued = await req("ada", "POST", "/invite")
                token, pair = issued["token"], {"pair_id": issued["workspace"]["pair_id"]}
                accepted = await req("lea", "POST", "/invite/accept", {"token": token})
                assert accepted["state"] == "confirming" and not accepted["writable"]
                await req("lea", "POST", "/invite/decline", {"token": token}, 409)
                await req("friend", "POST", "/invite/accept", {"token": token}, 404)
                await req("lea", "POST", "/confirm", pair, 404)
                await req("friend", "POST", "/leave", pair, 404)
                assert (await req("ada", "POST", "/confirm", pair))["writable"]
                assert (await req("lea", "GET", ""))["writable"]
                await req("lea", "POST", "/invite/accept", {"token": token}, 404)
                await req("lea", "PATCH", "/preferences", {"timezone": "invalid/bad"}, 422)
                pref = await req("lea", "PATCH", "/preferences", {"reminders": True, "timezone": "America/Chicago"})
                assert pref["reminders"] and not (await req("ada", "GET", ""))["reminders"]
                assert not pref["review_return_opt_in"]
                consent = await req("lea", "PATCH", "/preferences", {"review_return_opt_in":True})
                repeated = await req("lea", "PATCH", "/preferences", {"review_return_opt_in":True})
                assert consent["review_return_opt_in"] and repeated["review_return_since"] == consent["review_return_since"]
                withdrawn = await req("lea","PATCH","/preferences",{"review_return_opt_in":False})
                assert not withdrawn["review_return_opt_in"] and "review_return_since" not in (await req("lea","GET",""))
                await db.users.update_one({"user_id": "ada"}, {"$set": {"subscription_period_end": int(time.time()) - 1}})
                assert not (await req("lea", "GET", ""))["writable"]
                with pytest.raises(HTTPException) as err:
                    await couples.active_pair(db, await db.users.find_one({"user_id": "lea"}), write=True)
                assert err.value.status_code == 402
                await req("lea", "POST", "/sponsor", pair, 402)
                await db.users.update_one({"user_id": "lea"}, {"$set": paid})
                assert (await req("lea", "POST", "/sponsor", pair))["sponsor_me"]
                await db.couples_pairs.update_one({"_id": pair["pair_id"]}, {"$set": {"journey": {"private": "example"}}})
                await req("lea", "POST", "/leave", {**pair, "block": True})
                assert (await req("ada", "GET", ""))["state"] == "solo"
                closed = await db.couples_pairs.find_one({"_id": pair["pair_id"]})
                assert "journey" not in closed and "members" not in closed and closed["joined_at"]
                await db.users.update_one({"user_id": "ada"}, {"$set": paid})
                fresh = await req("ada", "POST", "/invite")
                await req("lea", "POST", "/invite/preview", {"token": fresh["token"]}, 404)
                # Choose a known expiry within this report window, both before and
                # after lazy expiry cleanup. Both views must agree on its date.
                await db.couples_pairs.update_one({"_id": fresh["workspace"]["pair_id"]}, {"$set": {"expires_at": couples.now()}})
                await req("friend", "POST", "/invite/accept", {"token": fresh["token"]}, 404)
                async def lifecycle():
                    return (await report(db, "preview", {"role": {"$ne": "admin"}}, 7))["connections"]
                pending_cleanup = await lifecycle()
                assert pending_cleanup == {"invitations_accepted": 2, "invitations_canceled": 2,
                                           "pairs_ended": 1, "invitations_expired": 1}
                assert (await req("ada", "GET", ""))["state"] == "solo"
                assert await lifecycle() == pending_cleanup
                await couples.clear_couples(db, "ada")
                assert not await db.couples_pairs.count_documents({"inviter": "ada"})
        finally:
            await client.drop_database(db.name)
            client.close()
    asyncio.run(run())
