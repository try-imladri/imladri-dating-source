import csv
import io
import json
import zipfile
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from backend import deepseek_statements as exports, message_cost_admin as admin, message_costs
from backend.tests.test_free_plan_quota import run_case

SECRET = "sk-private-fixture-88f0"
LABEL = "prod-test"
MASK = "sk-priva****************88f0"
DATE = "2026-11-09"
MODEL = "deepseek-flash"


def instant(hour, day=9):
    return datetime(2026,11,day,hour,tzinfo=timezone.utc)


def statement(slots, key=MASK, delta=0, bounds=None, headers_only=False):
    # Same fields and units as the provider's amount and cost files.
    from datetime import timedelta
    amounts, costs = [], []
    for stamp, hours, token_rows in slots:
        fields = {"user_id":"private-vendor-id","utc_date":stamp.strftime("%Y%m%d"), "start_time_iso":stamp.isoformat(),
                  "end_time_iso":(stamp+timedelta(hours=hours)).isoformat(), "model":MODEL,"name":MODEL}
        value = 0
        for kind, price, amount in token_rows:
            base = {**fields, "api_key_name":LABEL,"api_key":key,"type":kind,"price":"","amount":amount}
            if price is not None:
                base["price"] = str(Decimal(price)/10**9)
                # Official data uses fixed-point decimals.
                base["price"] = format(Decimal(base["price"]), "f")
                value += price*amount
            amounts.append(base)
        costs.append({**fields, "wallet_type":"Paid","cost":format(Decimal(value+delta)/10**9,"f"),"currency":"USD"})
    buf = io.BytesIO()
    first, last = bounds or (min(s[0] for s in slots).date(),max(s[0] for s in slots).date())
    with zipfile.ZipFile(buf,"w") as z:
        for prefix,rows in [("amount",amounts),("cost",costs)]:
            csvbuf=io.StringIO()
            writer=csv.DictWriter(csvbuf,fieldnames=list(rows[0]))
            writer.writeheader()
            if not headers_only:
                writer.writerows(rows)
            z.writestr(f"{prefix}-{first}_{last}.csv", csvbuf.getvalue())
    return buf.getvalue()


def token_rows(n, rates=(3,150,600)):
    return [("request_count",None,n),*( (kind, price, n*tokens)
             for kind,price,tokens in zip(exports.TOKEN_TYPES,rates,(10,20,30)) )]


def pinned(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY",SECRET)
    monkeypatch.setenv("DEEPSEEK_PRODUCTION_KEY_NAME",LABEL)
    key = message_costs.key_id(SECRET)
    monkeypatch.setenv("DEEPSEEK_PRODUCTION_KEY_ID",key)
    return key


def test_import_validates_wallet_scope_and_discards_vendor_identifiers():
    slots = [(instant(7),1,token_rows(3))]
    rows = exports.parse_statement(statement(slots), LABEL, SECRET)
    assert rows[DATE]["totals"][MODEL]["billed"] == 63090
    assert all(v not in json.dumps(rows) for v in (SECRET,MASK,"private-vendor-id"))
    for payload in (statement(slots, key="sk-other********88f0"), statement(slots,delta=1),b"{}"):
        with pytest.raises(HTTPException) as e:
            exports.parse_statement(payload,LABEL,SECRET)
        assert e.value.status_code == 422


def test_cost_report_tracks_settlement_cohorts_across_days_retries_preparations_and_overhead(monkeypatch):
    key = pinned(monkeypatch)
    async def scenario(db):
        units=[
            {"_id":"a","workspace":"individual","status":"settled","preparation_id":"upload"},
            {"_id":"b","workspace":"couples","status":"settled"},
            {"_id":"c","workspace":"couples","status":"refunded"},
        ]
        await db.message_cost_units.insert_many([{**u,"env":"live","key_id":key,"settled_at":instant(9)} for u in units])
        calls = [{"message_id":"a","billing_at":instant(23,8)}, {"preparation_id":"upload","billing_at":instant(23,8)},
                 {"message_id":"a","retry":True,"message_run":2}, {"message_id":"a","mode":"background","message_run":2}, {"message_id":"b","message_run":1},
                 {"message_id":"c"}, {"mode":"evaluation"}, {"preparation_id":"unused"}]
        for i,overrides in enumerate(calls):
            await db.ai_call_ledger.insert_one({"environment":"live","key_id":key,"dedicated_key":True,"model":MODEL,
                "billing_at":instant(9),"billing_hour_certain":True,"mode":"metered","message_id":None,
                "status":"succeeded","cache_hit_tokens":10,"cache_miss_tokens":20,"output_tokens":30,
                "low_nusd":21030,"high_nusd":42060, **overrides})
        pre=await admin.report(db,"live",1,DATE)
        assert pre["cohorts"]["all"]["estimated_per_100_usd"]==pytest.approx([.0052575,.010515])
        assert pre["cohorts"]["all"]["actual_per_100_usd"] is None
        slots=[(instant(23,8),1,token_rows(2)),(instant(9),1,token_rows(6,(6,300,1200)))]
        imported=exports.parse_statement(statement(slots),LABEL,SECRET)
        for expected in (2,0):
            assert (await admin.import_days(db,"live",key,imported))["changed"]==expected
        result=await admin.report(db,"live",1,DATE)
        assert [(d["day"],d["status"]) for d in result["coverage"]]==[("2026-11-08","matched"),(DATE,"matched")]
        a,b=result["cohorts"]["individual"],result["cohorts"]["couples"]
        assert (a["messages"],a["calls"],a["retries"],a["background"],a["preparation"])==(1,4,1,1,1)
        assert (a["customer_retry_calls"],b["customer_retry_calls"],result["cohorts"]["all"]["customer_retry_calls"])==(2,0,2)
        assert a["actual_per_100_usd"] == pytest.approx(.012618)
        assert b["actual_per_100_usd"] == pytest.approx(.004206)
        assert result["keywide_per_100_usd"]==pytest.approx(.012618)
        period=result["coverage"][1]["work"]
        assert {k:v["attempts"] for k,v in period.items()}=={"settled":3,"unclaimed_upload":1,"unlinked":1,"refunded":1}
        assert all(v["billed_nusd"]==v["attempts"]*42060 for v in period.values())
        await db.message_cost_units.update_one({"_id":"a"},{"$set":{"background_pending":1}})
        assert (await admin.report(db,"live",1,DATE))["cohorts"]["individual"]["actual_per_100_usd"] is None
        await db.ai_call_ledger.update_one({"message_id":"b"},{"$set":{"billing_hour_certain":False}})
        mismatched=await admin.report(db,"live",1,DATE)
        assert mismatched["coverage"][-1]["status"]=="mismatch"
        assert mismatched["cohorts"]["all"]["actual_per_100_usd"] is None
        assert mismatched["keywide_per_100_usd"] is None
        # A conflicting import must preserve the first verified statement.
        with pytest.raises(HTTPException) as err:
            await admin.import_days(db,"live",key,exports.parse_statement(statement([(instant(9),1,token_rows(5))]),LABEL,SECRET))
        assert err.value.status_code==409
        assert (await db.ai_billing_days.find_one({"_id":f"live:{key}:{DATE}"}))["totals"][MODEL]["requests"]==6
    run_case(scenario)


def test_mixed_rates_require_hourly_allocation_and_denominator_cannot_be_zero(monkeypatch):
    key=pinned(monkeypatch)
    async def scenario(db):
        for hour in (9,10):
            await db.ai_call_ledger.insert_one({"environment":"live","key_id":key,"dedicated_key":True,"model":MODEL,
                "billing_at":instant(hour),"billing_hour_certain":True,"cache_hit_tokens":10,"cache_miss_tokens":20,
                "output_tokens":30,"message_id":"one","mode":"metered"})
        await db.message_cost_units.insert_one({"_id":"one","env":"live","workspace":"individual","key_id":key,"status":"settled","settled_at":instant(10)})
        daily=token_rows(2)
        daily[1:] = token_rows(1)[1:]+token_rows(1,(6,300,1200))[1:]
        await admin.import_days(db,"live",key,exports.parse_statement(statement([(instant(0),24,daily)]),LABEL,SECRET))
        initial=await admin.report(db,"live",1,DATE)
        assert initial["coverage"][0]["status"]=="mixed_rates"
        assert initial["cohorts"]["individual"]["actual_per_100_usd"] is None
        assert initial["cohorts"]["couples"]["actual_per_100_usd"] is None
        assert initial["keywide_per_100_usd"]==pytest.approx(.006309)
        await admin.import_days(db,"live",key,exports.parse_statement(statement([(instant(9),1,token_rows(1,(6,300,1200))),
                                                                             (instant(10),1,token_rows(1))]),LABEL,SECRET))
        after=await admin.report(db,"live",1,DATE)
        assert after["coverage"][0]["status"]=="matched"
        assert after["cohorts"]["individual"]["actual_per_100_usd"]==pytest.approx(.006309)
        app=FastAPI()
        def deny(): raise HTTPException(403)
        app.include_router(admin.make_router(lambda:db,deny,"live"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            assert (await client.get("/admin/prompts/message-costs")).status_code==403
            assert (await client.post("/admin/prompts/message-costs/statement",files={"statement":("x.zip",b"x")})).status_code==403
    run_case(scenario)


def test_admin_can_import_a_vendor_zip_and_read_coverage_through_the_http_routes(monkeypatch):
    pinned(monkeypatch)
    async def scenario(db):
        app = FastAPI()
        app.include_router(admin.make_router(lambda: db, lambda: {"role": "admin"}, "live"), prefix="/api")
        url = "/api/admin/prompts/message-costs"
        content = statement([(instant(9),1,token_rows(3))])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            for changed in (1,0):
                accepted = await client.post(url+"/statement",content=content,headers={"Content-Type":"application/zip"})
                assert accepted.status_code == 200
                assert accepted.json()["changed"] == changed
            check = await client.get(url,params={"days":1,"end_date":DATE})
            assert check.status_code == 200 and check.headers["cache-control"] == "no-store"
            result = check.json()
            assert result["dedicated"] and result["key_name"] == LABEL
            assert result["coverage"][0]["status"] == "mismatch"
            assert result["coverage"][0]["billed_nusd"] == 63090
            assert result["cohorts"]["all"]["actual_per_100_usd"] is None
            assert (await client.post(url+"/statement",content=content,headers={"Content-Type":"application/octet-stream"})).status_code==415
        preview=FastAPI()
        preview.include_router(admin.make_router(lambda:db,lambda:{"role":"admin"},"preview"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=preview),base_url="http://test") as client:
            assert (await client.post("/admin/prompts/message-costs/statement",content=content,
                                      headers={"Content-Type":"application/zip"})).status_code==409
    run_case(scenario)


def test_verified_multiday_export_covers_quiet_closed_days_and_reconciles_full_period(monkeypatch):
    key = pinned(monkeypatch)
    slots = [(instant(0),24,token_rows(1))]
    bounds = ("2026-11-03","2026-11-10")
    at = instant(1,10)
    def parse(**kwargs):
        return exports.parse_statement(statement(slots,bounds=bounds,**kwargs),LABEL,SECRET,at)
    data = parse()
    assert sorted(data) == [f"2026-11-{n:02}" for n in range(3,10)]
    assert data["2026-11-03"] == {"totals":{},"hours":{}}
    for options in ({"headers_only":True},{"key":"sk-other********88f0"}):
        with pytest.raises(HTTPException):
            parse(**options)
    short = exports.parse_statement(statement([(instant(9),1,token_rows(1))],bounds=bounds),LABEL,SECRET,at)
    assert list(short) == [DATE]
    async def scenario(db):
        await admin.import_days(db,"live",key,data)
        await db.message_cost_units.insert_one({"_id":"one","env":"live","workspace":"individual","key_id":key,
                                                "status":"settled","settled_at":instant(9)})
        call={"environment":"live","key_id":key,"dedicated_key":True,"model":MODEL,
              "billing_at":instant(9),"billing_hour_certain":True,"cache_hit_tokens":10,
              "cache_miss_tokens":20,"output_tokens":30,"message_id":"one","mode":"metered"}
        await db.ai_call_ledger.insert_one(dict(call))
        verified=await admin.report(db,"live",7,DATE)
        assert all(day["status"]=="matched" for day in verified["coverage"])
        assert verified["coverage"][0]["provider_requests"] == verified["coverage"][0]["billed_nusd"] == 0
        assert verified["cohorts"]["individual"]["actual_per_100_usd"] == pytest.approx(.002103)
        assert verified["keywide_per_100_usd"] == pytest.approx(.002103)
        await db.ai_call_ledger.insert_one({**call,"billing_at":instant(12,4),"message_id":None})
        unexpected=await admin.report(db,"live",7,DATE)
        assert unexpected["coverage"][1]["status"]=="mismatch" and unexpected["keywide_per_100_usd"] is None
        assert unexpected["cohorts"]["individual"]["actual_per_100_usd"]==pytest.approx(.002103)
        positive=exports.parse_statement(statement([(instant(0,4),24,token_rows(1))]),LABEL,SECRET,at)
        with pytest.raises(HTTPException) as err:
            await admin.import_days(db,"live",key,positive)
        assert err.value.status_code==409
        # A later export with an omitted date may never erase earlier recorded spend.
        await db.ai_billing_days.delete_many({})
        await admin.import_days(db,"live",key,positive)
        with pytest.raises(HTTPException) as err:
            await admin.import_days(db,"live",key,data)
        assert err.value.status_code==409
        assert await db.ai_billing_days.count_documents({})==1
        assert (await db.ai_billing_days.find_one({}))["totals"][MODEL]["billed"]==21030
    run_case(scenario)


def test_settled_credit_with_only_preparation_or_background_withholds_per_message_actual(monkeypatch):
    key = pinned(monkeypatch)
    async def scenario(db):
        await db.message_cost_units.insert_many([
            {"_id":"a","preparation_id":"file","workspace":"individual","status":"settled","env":"live","key_id":key,"settled_at":instant(9)},
            {"_id":"b","workspace":"couples","status":"settled","env":"live","key_id":key,"settled_at":instant(9)},
        ])
        measured={"environment":"live","key_id":key,"dedicated_key":True,"model":MODEL,
                  "billing_at":instant(9),"billing_hour_certain":True,"cache_hit_tokens":10,
                  "cache_miss_tokens":20,"output_tokens":30,"low_nusd":21030,"high_nusd":21030}
        await db.ai_call_ledger.insert_many([{**measured,"preparation_id":"file","mode":"metered"},
                                             {**measured,"message_id":"b","mode":"background"}])
        await admin.import_days(db,"live",key,exports.parse_statement(statement([(instant(9),1,token_rows(2))]),LABEL,SECRET))
        report=await admin.report(db,"live",1,DATE)
        assert report["coverage"][0]["status"]=="matched"
        for workspace in ("individual","couples"):
            row=report["cohorts"][workspace]
            assert row["credits_without_calls"]==row["reconciled_calls"]==1
            assert row["actual_per_100_usd"] is None and row["estimated_per_100_usd"] is None
        assert report["cohorts"]["all"]["credits_without_calls"]==2
    run_case(scenario)
