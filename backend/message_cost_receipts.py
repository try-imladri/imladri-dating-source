"""Read-only, content-free message receipts for the Admin cost report."""
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response

try:
    from .message_costs import TONE_REJECTIONS
except ImportError:
    from message_costs import TONE_REJECTIONS

WORKSPACES = Literal["individual", "couples"]
UNIT_FIELDS = {k: 1 for k in ("workspace", "status", "kind", "pool", "started_at", "settled_at", "background_pending")}
CALL_FIELDS = {k: 1 for k in (
    "feature", "mode", "status", "started_at", "finished_at", "model", "requested_model", "retry", "message_run",
    "http_status", "input_tokens", "output_tokens", "total_tokens", "cache_hit_tokens", "cache_miss_tokens",
    "low_nusd", "high_nusd", "message_id"
)}


def utc_time(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return value


def unit_summary(row):
    return {"id": row["_id"], **{k: utc_time(row[k]) for k in UNIT_FIELDS if k in row}}


async def page(collection, query, fields, cursor, limit, descending):
    direction = -1 if descending else 1
    operator = "$lt" if descending else "$gt"
    if cursor is not None:
        anchor = await collection.find_one({"$and": [query, {"_id": str(cursor)}]}, {"started_at": 1})
        if anchor is None:
            raise HTTPException(422, "The page cursor is outside this report. Open the first page again.")
        stamp = anchor["started_at"]
        query = {"$and": [query, {"$or": [{"started_at": {operator: stamp}},
                                         {"started_at": stamp, "_id": {operator: str(cursor)}}]}]}
    rows = await collection.find(query, fields).sort([("started_at", direction), ("_id", direction)]).limit(limit + 1).to_list(limit + 1)
    return rows[:limit], rows[limit-1]["_id"] if len(rows) > limit else None


def make_receipts_router(get_db, env):
    router = APIRouter()

    @router.get("/receipts")
    async def list_receipts(response: Response, start: date, end: date, workspace: WORKSPACES | None = None,
                            cursor: UUID | None = None):
        response.headers["Cache-Control"] = "no-store"
        if end > datetime.now(timezone.utc).date() or not 0 <= (end-start).days < 31:
            raise HTTPException(422, "Choose a UTC date range of up to 31 days ending today or earlier.")
        first = datetime.combine(start, time.min, timezone.utc)
        until = datetime.combine(end + timedelta(days=1), time.min, timezone.utc)
        query = {"env": env, "started_at": {"$gte": first, "$lt": until}}
        if workspace:
            query["workspace"] = workspace
        rows, next_cursor = await page(get_db().message_cost_units, query, UNIT_FIELDS, cursor, 25, True)
        return {"items": [unit_summary(row) for row in rows], "next_cursor": next_cursor}

    @router.get("/receipts/{message_id}")
    async def get_receipt(response: Response, message_id: UUID, cursor: UUID | None = None):
        response.headers["Cache-Control"] = "no-store"
        db, rid = get_db(), str(message_id)
        unit = await db.message_cost_units.find_one({"env": env, "_id": rid}, {**UNIT_FIELDS, "preparation_id": 1})
        if not unit:
            raise HTTPException(404, "Message receipt not found.")
        links = [{"message_id": rid}]
        if unit.get("preparation_id"):
            links.append({"message_id": None, "preparation_id": unit["preparation_id"]})
        rows, next_cursor = await page(db.ai_call_ledger, {"environment": env, "$or": links},
            {**CALL_FIELDS, "application_rejection": 1}, cursor, 50, False)
        calls = []
        for row in rows:
            call = {key: utc_time(row[key]) for key in CALL_FIELDS if key in row and key != "message_id"}
            if isinstance(row.get("application_rejection"), str) and row["application_rejection"] in TONE_REJECTIONS:
                call["application_rejection"] = row["application_rejection"]
            call.update(id=row["_id"], preparation=not row.get("message_id"))
            calls.append(call)
        return {"message": unit_summary(unit), "calls": calls, "next_cursor": next_cursor}

    return router
