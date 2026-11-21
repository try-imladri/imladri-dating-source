from datetime import datetime, timezone
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Request
from backend.message_cost_admin import make_router
from backend.tests.test_free_plan_quota import run_case


def test_private_receipt_pages_preserve_scope_pagination_and_preparation_ownership():
    def ident(n):
        return str(UUID(int=n))

    async def check(db):
        began = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
        units = [{"_id":ident(i), "env":"live", "workspace":"individual", "kind":"exchange",
                  "pool":"plan", "started_at":began, "status":"settled", "private_details":"hidden"}
                 for i in range(1,28)]
        units[-1]["preparation_id"] = "upload-1"
        target = units[-1]["_id"]
        units.extend([{**units[0], "_id":ident(81), "env":"preview"},
                      {**units[0], "_id":ident(82), "workspace":"couples"}])
        await db.message_cost_units.insert_many(units)
        calls = [{"_id":ident(100+i), "environment":"live", "started_at":began, "message_id":target,
                  "feature":"six_part", "mode":"metered", "status":"ok", "private_details":"hidden",
                  "retry":i>0, "input_tokens":8, "output_tokens":4} for i in range(51)]
        calls.extend([{**calls[0], "_id":ident(900), "message_id":None, "preparation_id":"upload-1", "feature":"parse"},
                      {**calls[0], "_id":ident(901), "environment":"preview"},
                      {**calls[0], "_id":ident(902), "message_id":ident(1), "preparation_id":"upload-1"}])
        calls[0]["application_rejection"] = "tone_length"
        calls[1]["application_rejection"] = "accidental-private-field"
        await db.ai_call_ledger.insert_many(calls)

        async def admin(req: Request):
            if req.headers.get("Authorization") != "Bearer fixture-admin":
                raise HTTPException(403)
        app = FastAPI()
        app.include_router(make_router(lambda:db, admin, "live"))
        prefix = "/admin/prompts/message-costs/receipts"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(prefix+"/"+target)).status_code==403
            client.headers["Authorization"]="Bearer fixture-admin"
            params={"start":"2026-01-02","end":"2026-01-02","workspace":"individual"}
            first=await client.get(prefix,params=params)
            assert first.headers["cache-control"]=="no-store"
            batch=first.json()
            assert len(batch["items"])==25 and batch["items"][0]["id"]==target
            assert "private_details" not in first.text and "upload-1" not in first.text
            rest=(await client.get(prefix,params={**params,"cursor":batch["next_cursor"]})).json()
            assert [row["id"] for row in rest["items"]]==[ident(2),ident(1)] and rest["next_cursor"] is None
            detail=(await client.get(prefix+"/"+target)).json()
            assert len(detail["calls"])==50
            assert detail["calls"][0]["application_rejection"] == "tone_length"
            assert "accidental-private-field" not in str(detail)
            assert not any("message_id" in row or "private_details" in row for row in detail["calls"])
            next_page=(await client.get(prefix+"/"+target,params={"cursor":detail["next_cursor"]})).json()
            assert [row["id"] for row in next_page["calls"]]==[ident(150),ident(900)]
            assert next_page["calls"][-1]["preparation"] and next_page["next_cursor"] is None
            assert (await client.get(prefix+"/"+ident(81))).status_code==404
            assert (await client.get(prefix,params={**params,"cursor":ident(82)})).status_code==422
    run_case(check)
