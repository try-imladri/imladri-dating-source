import csv
import io
import os
from datetime import datetime

import httpx
import pytest
from fastapi import Request

from backend import personal_demographics, research_rows
from backend.tests.test_free_plan_quota import run_case


@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires an isolated MongoDB.")
def test_feedback_rows_require_current_grants_valid_instants_actor_and_shared_revision(monkeypatch):
    from backend import server
    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        async def staff(request: Request):
            return {"role": "admin", "user_id": "fixture"}
        monkeypatch.setitem(server.app.dependency_overrides, server.current_member, staff)
        end = "2026-11-08T17:00:00+00:00"
        monkeypatch.setattr(server, "now", lambda: end)
        original = "2026-11-08T11:00:00+00:00"
        latest = "2026-11-08T12:01:00+00:00"
        same = "2026-11-08T07:01:00-05:00"
        prior = "2026-11-08T12:00:59.999999+00:00"
        wrong = "BAD-PRIVATE-DATE"
        secret = "DO-NOT-EXPORT-PRIVATE-WORDS"
        env, a, b, pair = server.ENV, "synthetic-owner", "synthetic-peer", "raw-pair"
        key = "support-1"
        for i, uid in enumerate((a, b)):
            await db.users.insert_one({"user_id":uid, "env":env, "role":"member", "couples_adult_at":original,
                "row_member_code":str(i+3)*32, "row_member_at":original, "row_member_env":env,
                "row_couple_code":str(i+1)*32, "row_couple_at":original if i==0 else latest, "row_couple_env":env,
                "row_couple_pair_id":pair, "row_member_version":research_rows.VERSIONS["member"],
                "row_couple_version":research_rows.VERSIONS["couple"], "tone_research_version":personal_demographics.TONE_CONSENT,
                "tone_research_at":latest if i==0 else original})
        fb = {"fit":"corrected", "sent":True, "helped":False, "note":secret, "style":secret}
        def read(eid, at, feedback=fb, actor=a):
            return {"user_id":actor,"id":eid,"created_at":at,"tone":{"status":"estimated","drafts":{"neutral":secret},
                    "incoming":secret},"tone_feedback":feedback,"conversation":secret}
        await db.exchanges.insert_many([read("good",same), read("early",prior), read("malformed",wrong),
                    {**read("foreign",same),"env":"other"},
                    read("no-timezone", "2026-11-08T16:00:00"), read("future","2027-01-01T00:00:00Z"),
                    read("unsent",datetime(2026,11,8,12,2),{"fit":"unknown","sent":False,"helped":True}),
                    read("neither",same,{"sent":1,"helped":True}), read("absent",same,{})])
        source = {**read("private-coach-id",same), "pair_id":pair,"env":env,"mode":"shared","source_version":2}
        peer_source = {**source, "id":"peer-coach", "user_id": b, "tone_feedback":{"fit":"fits"}}
        def thread(shares, **extra):
            return {"kind":"prompt", "prompt_id":key,"shares":shares,"version":5,"words_version":2,**extra}
        await db.couples_pairs.insert_one({"_id":pair,"env":env,"members":[a,b],"state":"active","inviter":a,"invitee":b,
               "journey":{"threads":{
                   key: thread({"a":[secret],"b":[secret]}),
                   "withdrawn":thread({"a":[secret]}),
                   "stale":thread({"a":[secret],"b":[secret]},words_version=4)}},
               "tone_sessions":{key:{"a":source,"b":peer_source},"withdrawn":{"a":source},"stale":{"a":source},
                                "removed":{"a":source}}})
        await db.couples_drafts.insert_one({"key":key,"user_id":a,"tone_coach":source,"lines":[secret]})
        mc=research_rows.member_code("3"*32)
        pc=research_rows.pair_code(pair,"1"*32,"2"*32)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://fixture") as api:
            async def rows(scope):
                r=await api.get("/api/admin/research/exports/"+scope+".csv",params={"unit_code":mc if scope=="member" else pc})
                assert r.is_success and "no-store" in r.headers["cache-control"],r.text
                assert all(raw not in r.text for raw in (secret, wrong, a, b, "private-coach-id", "peer-coach", pair))
                return [x for x in csv.DictReader(io.StringIO(r.text)) if x["domain"]=="tone_feedback"]
            data=await rows("member")
            from collections import Counter
            assert Counter((x["variable_id"], x["answer"]) for x in data)=={
                ("tone:fit","corrected"):1,("tone:sent","Yes"):1,("tone:sent","No"):1,("tone:helped","No"):1}
            assert len({x["observation_code"] for x in data})==2
            assert {x["member_code"] for x in data}=={mc} and {x["observed_on_utc"] for x in data}=={"2026-11-08"}
            shared=await rows("couple")
            assert len(shared)==4 and {x["variable_id"] for x in shared}=={"tone:fit","tone:sent","tone:helped"}
            assert {x["member_code"] for x in shared if x["seat"]=="inviter"}=={mc}
            assert len({x["observation_code"] for x in shared})==2
            await db.users.update_one({"user_id":b},{"$unset":{"row_member_code":""}})
            assert {x["member_code"] for x in await rows("couple")}=={""}
            base="tone_sessions."+key+".a."
            for field, value in (("user_id",b),("env","wrong"),("pair_id","different"),("mode","private"),
                                 ("source_version",True),("source_version",4),("created_at",prior)):
                await db.couples_pairs.update_one({"_id":pair},{"$set":{base+field:value}})
                assert [x["seat"] for x in await rows("couple")]==["invitee"]
                await db.couples_pairs.update_one({"_id":pair},{"$set":{base+field:source[field]}})
            await db.exchanges.update_one({"id":"good"},{"$unset":{"tone_feedback.fit":"","tone_feedback.helped":""}})
            assert {x["variable_id"] for x in await rows("member")}=={"tone:sent"}
            for uid,scope,field in ((a,"member","row_member_at"),(a,"member","tone_research_at"),
                                    (b,"couple","row_couple_at"),(a,"couple","tone_research_at")):
                previous=(await db.users.find_one({"user_id":uid}))[field]
                await db.users.update_one({"user_id":uid},{"$set":{field:wrong}})
                assert await rows(scope)==[]
                await db.users.update_one({"user_id":uid},{"$set":{field:previous}})
            await db.users.update_one({"user_id":a},{"$unset":{"tone_research_version":""}})
            assert await rows("member")==[] and await rows("couple")==[]
    run_case(scenario)
