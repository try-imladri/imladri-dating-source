import csv
import io
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Request

from backend import intimacy_catalog, personal_demographics, research, research_rows
from backend.quiz import default_quiz
from backend.tests.test_free_plan_quota import run_case


def test_csv_distinguishes_removed_editions_from_current_and_rejects_unpublished_free_text():
    quiz = default_quiz()
    q = quiz["short"][0]
    old_id = q["id"]
    quiz["legacy_wording"] = {old_id: {"q": "=Previous published prompt", "labels": ["@First", "Second"]}}
    about = {"answers": {old_id: {"choice": 0, "note": "Do not export me"}, "unknown": {"question":"unpublished","label":"private"}}}
    _, variables = research.catalog(quiz, [])
    entries = list(research_rows.everyday_rows(about, quiz, variables, {}))
    group = [e for e in entries if e["variable_id"] == "e:"+old_id]
    assert [r["status"] for r in group] == ["unanswered", "archived_answered"]
    assert group[-1]["answer"] == "@First" and group[0]["edition"] != group[-1]["edition"]
    assert "unpublished" not in str(entries) and "Do not export" not in str(entries)
    about["answers"][old_id] = {"question":"personal text", "label":"another private field"}
    assert list(research_rows.everyday_rows(about, quiz, variables, {}))[-1]["status"] == "unavailable"


@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires disposable loopback MongoDB.")
def test_member_and_pair_grants_download_current_rows_and_revoke_on_ordinary_routes(monkeypatch):
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        codes = iter(["3"*32, "5"*32, "6"*32, "7"*32])
        monkeypatch.setattr(research_rows,"secrets", SimpleNamespace(token_hex=lambda _: next(codes)))
        async def identity(request: Request):
            return await db.users.find_one({"user_id": request.headers.get("x-member", "member-fixture")}, {"_id":0})
        monkeypatch.setitem(server.app.dependency_overrides, server.current_member, identity)
        monkeypatch.setitem(server.app.dependency_overrides, server.subscribed_member, identity)
        monkeypatch.setattr(server, "plan_usage", AsyncMock(return_value={}))
        await server.ensure_indexes()
        quiz = default_quiz()
        await db.settings.insert_one({"key":"global", "quiz":quiz})
        q, skipped = quiz["short"][:2]
        person = {"user_id":"member-fixture","email":"owner@fixture.invalid","role":"member","env":server.ENV,
                  "tier":"paid", "stripe_subscription_id":"sub_research_rows_fixture", "subscription_status":"active",
                  "subscription_period_start":int(time.time())-300, "subscription_period_end":int(time.time())+3600,
                  "couples_adult_at":server.now(), "questionnaire_research_version":personal_demographics.CONSENT_VERSION,
                  "about":{"demographics":{"age":30,"height_cm":171,"gender":"Woman","sex":"Female","ethnicity":"Hispanic / Latino","education":"Bachelor's"},
                           "mbti":"INFP","describe":"Private life story","answers":{q["id"]:{"choice":0,"note":"Very private note"}},
                           "skipped":[skipped["id"]]}}
        await db.users.insert_many([person,{"user_id":"staff-fixture","role":"admin","env":server.ENV}])
        _, variables = research.catalog(quiz, await intimacy_catalog.overrides(db))
        intime = [(k,v) for k,v in variables.items() if v["domain"]=="intimacy"][:3]
        (first, f), (second, s), (third, t) = intime
        await db.intimacy_answers.insert_one({"_id":person["user_id"],"adult_confirmed":True,
            "values":{first:2, second:None, third:1}, "conditions":{first:["In private", "Private sentence"]},
            "signatures":{first:f["signature"], second:s["signature"], third:"f"*64},
            "wording":{third:{"topic":t["topic"], "question":"Earlier published item?", "answer":"Curious"}}})
        exports = "/api/admin/research/exports/"
        staff = {"x-member":"staff-fixture"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url="http://fixture.local") as api:
            async def download(code=None):
                return await api.get(exports+"member.csv",headers=staff,params={"unit_code":code} if code else {})
            async def change(scope, revision, enabled, **fields):
                return await api.patch("/api/me/research-exports/"+scope,json={
                    "expected_member":person["user_id"],"revision":revision,"enabled":enabled,"adult_confirmed":True,
                    "version":research_rows.VERSIONS[scope],**fields})
            empty = await download()
            assert empty.is_success and empty.headers["x-research-empty"]=="1" and list(csv.DictReader(io.StringIO(empty.text)))==[]
            assert (await api.get(exports+"member.csv")).status_code == 403
            assert (await api.get(exports+"member/units")).status_code == 403
            assert (await change("member",0,True,adult_confirmed=False)).status_code==403
            assert (await change("member",0,True,expected_member="other")).status_code==409
            grant = await change("member",0,True)
            assert grant.json() == {"enabled":True,"revision":1,"version":"row-member-v1"}
            listed=(await api.get(exports+"member/units", headers=staff)).json()
            assert len(listed["items"])==1 and listed["next"] is None
            code=listed["items"][0]
            response=await download(code)
            assert response.is_success and response.headers["x-research-empty"]=="0" and "no-store" in response.headers["cache-control"]
            rows=list(csv.DictReader(io.StringIO(response.text)))
            assert set(rows[0])==set(research_rows.COLUMNS)
            assert all(r["member_code"]==code==r["unit_code"] and not r["seat"] for r in rows)
            selected={r["variable_id"]:r for r in rows if r["status"]=="answered"}
            assert selected["age_band"]["answer"]=="25-34" and selected["height_band"]["answer"]=="170–179 cm"
            assert selected["e:"+q["id"]]["answer"]==q["options"][0]["label"]
            assert selected[first]["conditions"]=='["In private"]' and selected[first]["edition"]==f["signature"]
            assert selected["p:mbti"]["answer"]=="INFP"
            assert {r["status"] for r in rows if r["variable_id"]==third}=={"unanswered","archived_answered"}
            assert [r["status"] for r in rows if r["variable_id"]==second]==["skipped"]
            assert [r["status"] for r in rows if r["variable_id"]=="e:"+skipped["id"]]==["skipped"]
            assert all(value not in response.text for value in ["member-fixture","owner@fixture.invalid","Private life story",
                                                                "Very private note","Private sentence"])
            assert (await download("couple-id")).status_code==422 and (await download("m_"+"0"*32)).status_code==404
            previous_skip = next(r for r in rows if r["variable_id"]=="e:"+skipped["id"])
            assert previous_skip["edition"] == ""
            assert (await api.post("/api/me/quick-answer",json={"qid":skipped["id"],"choice":None})).is_success
            recorded = list(csv.DictReader(io.StringIO((await download(code)).text)))
            recorded_skip = next(r for r in recorded if r["variable_id"]=="e:"+skipped["id"])
            assert recorded_skip["status"]=="skipped" and recorded_skip["edition"]==variables["e:"+skipped["id"]]["signature"]
            assert (await api.put("/api/me/about",json={"answers":{skipped["id"]:None}})).is_success
            updated = list(csv.DictReader(io.StringIO((await download(code)).text)))
            assert next(r for r in updated if r["variable_id"]=="e:"+skipped["id"])["status"] == "unanswered"
            assert (await change("member",0,False)).status_code==409
            assert (await change("member",1,False)).is_success and (await download(code)).status_code==404
            assert (await change("member",2,True)).is_success
            fresh=(await api.get(exports+"member/units",headers=staff)).json()["items"][0]
            assert fresh!=code and (await download(code)).status_code==404

            await db.users.insert_one({"user_id":"partner-fixture", "role":"member", "env":server.ENV,"couples_adult_at":server.now()})
            pair={"_id":"first-pair", "inviter":person["user_id"], "invitee":"partner-fixture", "state":"active","env":server.ENV,
                  "members":[person["user_id"], "partner-fixture"], "sponsor":person["user_id"]}
            await db.couples_pairs.insert_one(pair)
            assert (await change("couple",0,True,expected_pair_id="old-pair")).status_code==409
            assert (await change("couple",0,True,expected_pair_id="first-pair")).json()["enabled"]
            assert (await api.get(exports+"couple/units", headers=staff)).json()["items"]==[]
            await db.couples_pairs.delete_one({"_id":"first-pair"})
            before=(await api.get("/api/me")).json()["research_exports"]["couple"]
            assert not before["enabled"] and before["previous_pair_grant"] and before["pair_id"] is None
            assert not (await change("couple",1,False)).json()["previous_pair_grant"]

            await personal_demographics.save_own(db, person["user_id"], {"age":16})
            assert (await download(fresh)).status_code==404
            me=(await api.get("/api/me")).json()["research_exports"]
            assert not me["member"]["enabled"] and me["member"]["revision"]==4
            assert (await change("member",4,True)).status_code==403
            await personal_demographics.save_own(db, person["user_id"], {"age":30})
            assert (await change("member",4,True)).is_success
            final_code = (await api.get(exports+"member/units",headers=staff)).json()["items"][0]
            assert (await api.delete("/api/me")).is_success
            assert (await download(final_code)).status_code==404 and (await download()).headers["x-research-empty"]=="1"
            assert (await change("member",5,True)).status_code==409

    run_case(scenario)


@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires disposable loopback MongoDB.")
def test_research_code_paging_excludes_ineligible_grants_and_bulk_escapes_cells():
    async def scenario(db):
        base = {"env": {"$in":["live", None]}, "email":{"$not":{"$regex":"@qa-fixture", "$options":"i"}}}
        cases = [{}, {}, {"role":"admin"}, {"closing":True}, {"email":"hidden@qa-fixture"},
                 {"env":"preview"}, {"env":None,"row_member_env":"preview"}, {"about":{"demographics":{"age":17}}},
                 {"row_member_version":"older"}, {"row_member_code":"malformed"},
                 {"about":{"demographics":{"age":[None, 17]}}}, {"about":{"demographics":{"age":["", 17]}}},
                 {"about":{"demographics":{"age":"00030"}}}, {"about":{"demographics":{"age":25.0}}},
                 {"row_member_code":["a"*32]}, {"row_member_version":["row-member-v1"]}, {"row_member_env":["live"]}]
        for number, changed in enumerate(cases, 1):
            await db.users.insert_one({"user_id":str(number),"role":"member","env":"live","email":"local@member.invalid",
                                       "row_member_code":f"{number:032x}","row_member_env":"live","row_member_version":"row-member-v1",**changed})
        first = await research_rows.page(db, base, "member", None, 1, None)
        assert first["items"] == [f"m_{1:032x}"] and first["next"] == first["items"][0]
        assert await research_rows.page(db, base, "member", first["next"], 1, None) == {"items":[f"m_{2:032x}"],"next":None}
        async def source():
            yield {"question":"  =spreadsheet formula", "answer":"=1+1"}
        response = await research_rows.export_response(source(),"member","2026-11-09T12:00:00Z",None)
        content = "".join([chunk async for chunk in response.body_iterator])
        record = next(csv.DictReader(io.StringIO(content)))
        assert record["question"].startswith("'") and record["answer"].startswith("'")
    run_case(scenario)

@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires disposable loopback MongoDB.")
def test_bulk_member_csv_reloads_selections_and_permission_before_each_member():
    async def scenario(db):
        quiz = default_quiz()
        question = quiz["short"][0]
        base = {"role": "member", "env": "preview", "row_member_env": "preview",
                "row_member_version": research_rows.VERSIONS["member"],
                "about": {"demographics": {"age": 30}, "answers": {question["id"]: {"choice": 0}}}}
        await db.users.insert_many([{**base, "user_id": str(i), "row_member_code": f"{i:032x}"} for i in range(3)])
        _, variables = research.catalog(quiz, [])
        key = next(k for k, v in variables.items() if v["domain"] == "intimacy")
        await db.intimacy_answers.insert_one({"_id": "1", "adult_confirmed": [True], "values": {key: 0},
                                              "signatures": {key: variables[key]["signature"]}})
        source = research_rows.member_rows(db, {"env": "preview"}, "2026-11-09T12:00:00Z", {"quiz": quiz})
        first = await anext(source)
        assert first["unit_code"] == f"m_{0:032x}"
        await db.users.update_one({"user_id": "1"}, {"$set": {"about.demographics.age": 51,
            "about.answers." + question["id"] + ".choice": 1}})
        await db.users.update_one({"user_id": "2"}, {"$unset": {"row_member_version": ""}})
        rest = [r async for r in source]
        assert all(r["unit_code"] != f"m_{2:032x}" for r in rest)
        second = {r["variable_id"]: r for r in rest if r["unit_code"] == f"m_{1:032x}"}
        assert second["age_band"]["answer"] == "45-54"
        assert second["e:" + question["id"]]["answer"] == question["options"][1]["label"]
        assert second[key]["status"] == "unanswered"

    run_case(scenario)
