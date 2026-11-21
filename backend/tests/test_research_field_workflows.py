"""Field-study regressions use disposable records on loopback MongoDB."""
import hashlib
import json
import os
import re
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend import assessment_evidence as evidence, rehearsal_review as review, wording_feedback
from backend.tests.test_free_plan_quota import run_case
from backend.tests.test_tone_reporting import ELIGIBLE, member, read, shared_pair

mongo = pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires a disposable local MongoDB.")


def make_ballot(slot):
    answers = {key: "2" for keys in review.groups().values() for key in keys}
    for case in review.cases():
        answers[case["id"]+"-critical"] = "No"
        answers[case["id"]+"-reason"] = ""
    return {"verified": True, "ballot": {"reader": slot, "study": review.EDITION, "answers": answers}}


def test_frozen_rehearsal_evidence_and_ballot_fields():
    a, b = [json.loads(re.search(r'id="packet" type="application/json">(.*?)</script>', review.reader_html(slot)).group(1)) for slot in ("A", "B")]
    assert a["items"] != b["items"] and len(a["items"]) == 6
    def options(packet):
        return {o["id"]: o for case in packet["items"] for o in case["options"]}
    assert len(options(a)) == 18 and options(a) == options(b)
    assert all(set(o) == {"id","reaction","meaning","outcome","suggested"} for o in options(a).values())
    assert hashlib.sha256(json.dumps(review.cases(), sort_keys=True).encode()).hexdigest() == "745d35ec94ea424c71fe6ac2eab89f40ac7230c9f50030529bb0427389bc4757"
    body = make_ballot("A")
    review.validate(review.Submission(**body).ballot)
    with pytest.raises(ValidationError):
        review.Submission(**{**body, "verified": False})
    for answers in ({}, {**body["ballot"]["answers"], "R04-b1-context": "7"}, {**body["ballot"]["answers"], "R04-critical": "Yes"}):
        with pytest.raises(HTTPException):
            review.validate(review.Ballot(**{**body["ballot"], "answers": answers}))


@mongo
def test_staff_rehearsal_two_independent_imports_withdrawal_and_blinding(monkeypatch):
    from backend import server
    async def go(db):
        monkeypatch.setattr(server, "db", db)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url="http://fixture") as client:
            path="/api/admin/research/rehearsals"
            assert (await client.get(path)).status_code == 401
            monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"user_id":"fixture-staff"})
            packet=await client.get(path+"/A")
            assert packet.is_success and "attachment" in packet.headers["content-disposition"] and "no-store" in packet.headers["cache-control"]
            a, b = make_ballot("A"), make_ballot("B")
            first=await client.post(path,json=a)
            assert first.is_success and first.json()["received"]==["A"] and all(not r["readers"] for r in first.json()["criteria"])
            assert first.json()==(await client.post(path,json=a)).json()
            b["ballot"]["answers"].update({"R04-b1-context":"", "R04-b2-context":"unknown", "R04-b3-context":"0", "R04-critical":"Yes", "R04-reason":"Fixture-critical"})
            full=(await client.post(path,json=b)).json()
            context=full["criteria"][0]
            assert context["paired"]==16 and context["matching"]==15 and context["readers"]["B"]=={"scored":16,"full":15,"mixed":0,"unmet":1,"skipped":1,"unknown":1}
            assert full["status"]=="critical concerns" and full["concerns"]==[{"case":"R04","reader":"B","reason":"Fixture-critical"}]
            assert (await review.results(db,"unrelated"))["received"] == []
            assert (await client.delete(path+"/older/B")).status_code == 409
            removed=await client.delete(path+"/"+review.EDITION+"/B")
            assert removed.json()["withdrawn"]==["B"] and not removed.json()["concerns"]
            assert "Fixture-critical" not in str(await db.rehearsal_ballots.find({}).to_list(None))
            assert (await client.post(path,json=b)).status_code == 409
    run_case(go)


@mongo
def test_owner_feedback_edition_scope_privacy_threshold_and_withdrawal(monkeypatch):
    from backend import server
    async def go(db):
        uid="subject"; user=member(uid); user["created_at"]="first-account"; user["env"]=server.ENV
        stamp=int(datetime.now(timezone.utc).timestamp())
        user.update(tier="paid", stripe_subscription_id="sub_wording_fixture", subscription_status="active",
                    subscription_period_start=stamp-300, subscription_period_end=stamp+3600)
        await db.users.insert_one(user.copy()); monkeypatch.setattr(server,"db",db)
        async def mine(): return await db.users.find_one({"user_id":uid})
        for dep in (server.subscribed_member, server.current_member):
            monkeypatch.setitem(server.app.dependency_overrides, dep, mine)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url="http://fixture") as client:
            path="/api/me/intimacy-wording"
            edition=await wording_feedback.edition(db)
            body={"owner":uid,"account_created":"first-account","revision":0,"edition":edition,"adult_and_consented":True,"conditions":"faithful","boundaries":"unsure"}
            for wrong in ({"owner":"different"},{"account_created":"old-account"},{"edition":"other"},{"revision":2}):
                assert (await client.put(path,json={**body,**wrong})).status_code==409
            assert (await client.put(path,json={**body,"adult_and_consented":False})).status_code==422
            assert (await client.put(path,json={**body,"private_words":"never store this"})).status_code==422
            result=await client.put(path,json=body);assert result.is_success, result.text
            assert result.json()["response"]["boundaries"]=="unsure"
            assert "intimacy_wording_review" in (await client.get("/api/me/export")).json()["account"]
            eligible={**ELIGIBLE,"env":server.ENV}
            assert all(v is None for v in (await wording_feedback.summary(db,eligible))["measures"].values())
            saved=(await mine())["intimacy_wording_review"]
            for i in range(49):
                u={**member("threshold"+str(i)),"env":server.ENV,"intimacy_wording_review":dict(saved)};await db.users.insert_one(u)
            summary=await wording_feedback.summary(db,eligible)
            assert summary["measures"]["conditions"]["faithful"]==50 and summary["measures"]["boundaries"]["unsure"]==50
            await db.users.update_one({"user_id":"threshold0"},{"$set":{"intimacy_wording_review.boundaries":"missed"}})
            assert (await wording_feedback.summary(db,eligible))["measures"]["boundaries"] is None
            remove={k:result.json()[k] for k in ("owner","account_created","revision")}
            assert (await client.request("DELETE",path,json=remove)).is_success
            assert (await client.put(path,json=body)).status_code==409
            assert (await client.get(path)).json()["response"] is None
            assert all(v is None for v in (await wording_feedback.summary(db,eligible))["measures"].values())
            await db.users.update_one({"user_id":uid},{"$set":{"about.demographics.age":17}})
            assert (await client.put(path,json={**body,"revision":2})).status_code==403
    run_case(go)


@mongo
def test_pair_feedback_counts_same_current_revision_and_consent_per_pair():
    async def go(db):
        date=datetime(2025,6,10,tzinfo=timezone.utc)
        for i in range(60):
            a,b="author"+str(i),"partner"+str(i)
            sides = {"a":{**read(a,i),"source_version":2,"tone_feedback":{}}}
            await db.users.insert_many([member(a),member(b)])
            if i>=20:sides["a"]["tone_feedback"]={"fit":"fits"}
            if i>=40:sides["b"]={**read(b,i),"source_version":2,"tone_feedback":{"fit":"corrected"}}
            # A rating by the partner on another topic does not become a second rating of the current thread.
            older={**read(b,i),"source_version":1,"tone_feedback":{"fit":"fits"},"created_at":"2025-06-02T00:00:00+00:00"}
            await db.couples_pairs.insert_one(shared_pair(str(i), a, b, {"latest":sides,"other":{"b":older}}))
        await db.couples_drafts.insert_one({"user_id":"author0","tone_coach":read("author0",0)})
        result=await evidence.pair_feedback_coverage(db,"preview",ELIGIBLE,date)
        assert result=={"available":True,"sample":60,"groups":{"neither":20,"one":20,"both":20}}
        await db.users.update_many({"user_id":{"$in":["partner"+str(i) for i in range(20)]}},{"$unset":{"tone_research_version":""}})
        assert not (await evidence.pair_feedback_coverage(db,"preview",ELIGIBLE,date))["available"]
    run_case(go)
