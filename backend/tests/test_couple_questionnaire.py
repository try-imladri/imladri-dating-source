"""Verify both Couples seats' saved profile and questionnaire codes against a real MongoDB."""
import asyncio
import os
import time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, Header
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from backend import couple_research, couples, couples_journey


@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires disposable loopback MongoDB.")
def test_sponsored_profile_and_private_couples_answers():
    async def run():
        uri = os.environ["IMLADRI_TEST_MONGO_URL"]
        assert urlsplit(uri).hostname in ("localhost", "127.0.0.1", "::1")
        mongo = AsyncIOMotorClient(uri)
        db = mongo["couples_questionnaire_" + uuid4().hex]
        app = FastAPI()

        async def current(x_account: str = Header()):
            return await db.users.find_one({"user_id": x_account})

        app.include_router(couples.couples_router(lambda: db, current))
        app.include_router(couples_journey.journey_router(lambda: db, current))
        ts = int(time.time())
        try:
            await db.couples_drafts.create_indexes(couples_journey.JOURNEY_INDEXES["couples_drafts"])
            person = {"env": "preview", "role": "member", "couples_adult_at": "2026-01-01T00:00:00+00:00"}
            await db.users.insert_many([{**person, "user_id": "inviter", "tier": "paid", "stripe_subscription_id": "sub_test",
                                        "subscription_period_start": ts - 500, "subscription_period_end": ts + 5000},
                                       {**person, "user_id": "guest"}])
            pair = {"_id": "pair-1", "state": "active", "env": "preview", "members": ["inviter","guest"],
                    "inviter": "inviter", "invitee": "guest", "sponsor": "inviter"}
            await db.couples_pairs.insert_one(pair)
            catalog = await couple_research.variables(db)
            assert len([k for k in catalog if k.startswith("c:prompt:")]) == 12
            keys = ["c:prompt:support-1", "c:checkin:any", "c:checkin:prompt:support-1", "c:checkin:positive"]
            axes = {k: catalog[k] for k in keys}

            async def codes(uid, side):
                stages = [{"$match": {"_id": "pair-1", "state": "active", "members": uid}},
                          *couple_research.seat_stages({"$literal":uid}, "$$ROOT", {"$literal":side}, axes, "v"),
                          {"$project": {"_id": 0, "v": 1}}]
                return await db.couples_pairs.aggregate(stages).to_list(1)

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as api:
                async def call(who, method, path, body=None, status=200):
                    r = await api.request(method, "/couples" + path, headers={"x-account":who}, json=body)
                    assert r.status_code == status, r.text
                    return r.json()

                details = {"age": 31, "height_cm": 178, "gender": "Woman", "sex": "Female",
                           "ethnicity": "Mixed", "education": "High school"}
                await call("guest","PATCH","/profile", {**details, "expected_member":"guest"})
                await call("inviter","PATCH","/profile", {**details, "expected_member":"guest"},409)
                await call("inviter","PATCH","/profile", details,422)
                assert (await call("guest","GET","/profile"))["demographics"] == details
                assert (await call("inviter","GET","/profile"))["demographics"] == {}
                await call("guest","PATCH","/profile",{"height_cm": 500, "expected_member":"guest"},422)
                await call("guest","PATCH","/profile",{"height_cm": None, "expected_member":"guest"})
                assert "height_cm" not in (await call("guest","GET","/profile"))["demographics"]

                body = {"version": 0, "choice": "Listening", "lines": ["Private example nobody else should see"]}
                saved = await call("guest","PUT","/drafts/support-1",body)
                assert saved["drafts"][0]["choice"]=="Listening"
                assert not (await call("inviter","GET","/journey"))["drafts"]
                assert (await codes("guest","b"))[0]["v"].get("c:prompt:support-1") is None
                await call("guest","PUT","/drafts/support-1",{**body,"choice":"Quiet company"},409)
                await call("guest","PUT","/drafts/support-1",{**body,"choice":"unpublished","version":1},422)
                # New clients distinguish typed custom words from an actual choice.
                await call("guest","PUT","/drafts/support-1",{"choice":None,"lines":["Listening"],"version":1})
                assert (await codes("guest","b"))[0]["v"].get("c:prompt:support-1") is None
                await call("guest","PUT","/drafts/support-1",{"skipped":True,"lines":[],"version":2})
                assert (await codes("guest","b"))[0]["v"].get("c:prompt:support-1") is None
                await call("guest","DELETE","/drafts/support-1?version=3")
                assert not (await call("guest","GET","/journey"))["drafts"]
                await db.couples_drafts.insert_one({"user_id":"inviter","key":"support-1","version":1,"lines":["Quiet company"]})
                assert (await codes("inviter","a"))[0]["v"].get("c:prompt:support-1") is None
                # Legacy clients can still save their text without creating a research selection.
                await call("inviter","PUT","/drafts/support-1",{"version":1,"lines":["Quiet company"]})
                assert (await codes("inviter","a"))[0]["v"].get("c:prompt:support-1") is None
                await call("inviter","PUT","/drafts/support-1",{"version":2,"choice":"Quiet company","lines":["Quiet company"]})
                assert (await codes("inviter","a"))[0]["v"].get("c:prompt:support-1") is None

                def check(a,b,when=None):
                    return {"checkins":{"a":{"result":a,"note":"private A"},
                                        "b":{"result":b,"note":"private B",**({"updated_at":when} if when else {})}}}
                threads={"support-1":{"kind":"prompt","prompt_id":"support-1","agreements":[check("mixed","not_tried"),
                         check("mixed","helped","2026-11-08T12:00:00+00:00")]},
                         "positive-moment":{"kind":"positive","agreements":[check("not_tried","mixed","2026-11-08T13:00:00+00:00")]}}
                await db.couples_pairs.update_one({"_id":"pair-1"},{"$set":{"journey.threads":threads}})
                guest = (await codes("guest","b"))[0]["v"]
                assert guest=={"c:prompt:support-1":None,"c:checkin:any":1,"c:checkin:prompt:support-1":0,"c:checkin:positive":1}, guest
                inviter = (await codes("inviter","a"))[0]["v"]
                assert inviter["c:checkin:any"]==-1 and inviter["c:checkin:prompt:support-1"]==1
                await call("guest","POST","/leave",{"pair_id":"pair-1"})
                assert not await codes("inviter","a")
                assert "c:checkin:any" in catalog and all("private" not in str(x) for x in catalog.values())
        finally:
            await mongo.drop_database(db.name)
            mongo.close()
    asyncio.run(run())
