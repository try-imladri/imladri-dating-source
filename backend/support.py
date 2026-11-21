"""Public support receipts and the staff inbox. Only a receipt holder can read a request."""
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, StringConstraints
from pymongo import DESCENDING, IndexModel
from pymongo.errors import DuplicateKeyError


SUPPORT_INDEXES = {
    "support_requests": [
        IndexModel("expires_at", expireAfterSeconds=0, name="support_expiry"),
        IndexModel([("status", 1), ("created_at", DESCENDING)], name="support_inbox"),
    ],
    "support_rate": [IndexModel("expires_at", expireAfterSeconds=0, name="support_rate_expiry")],
}
Receipt = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)]
Message = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2400)]
Category = Literal["Account", "Billing", "Refund", "Privacy", "Other"]
PUBLIC_FIELDS = {"_id": 0, "category": 1, "message": 1, "answer": 1, "status": 1, "created_at": 1, "expires_at": 1, "thread": 1, "round": 1}


class SupportReceipt(BaseModel):
    receipt: Receipt


class SupportSubmission(SupportReceipt):
    category: Category
    message: Message


class SupportAnswer(BaseModel):
    answer: Message
    round: int = Field(default=0, ge=0, le=12)


class FollowUp(SupportReceipt):
    message: Message
    round: int = Field(ge=0, le=12)


class Assignment(BaseModel):
    claim: bool
    expected_owner: str | None = Field(default=None, max_length=128)


def round_filter(number):
    return number if number else {"$in": [0, None]}


def messages(row):
    if "thread" in row:
        return row["thread"]
    if row.get("answer"):
        return [{"speaker": "support", "text": row["answer"], "at": row.get("answered_at")}]
    return []



def display_record(row):
    # PyMongo returns stored UTC timestamps without tzinfo unless its client enables tz_aware.
    def utc(value):
        return value.replace(tzinfo=timezone.utc) if isinstance(value, datetime) and value.tzinfo is None else value
    return {**{k: utc(v) for k, v in row.items()}, "round": row.get("round", 0),
            "thread": [{**event, "at": utc(event.get("at"))} for event in messages(row)]}


def receipt_id(receipt: str) -> str:
    return hashlib.sha256(bytes.fromhex(receipt)).hexdigest()


def support_router(db, admin_dep):
    router = APIRouter(prefix="/api")
    rate_secret = (os.getenv("CLERK_SECRET_KEY") or secrets.token_hex(32)).encode()

    @router.post("/support/requests", status_code=201)
    async def submit(body: SupportSubmission, request: Request):
        now = datetime.now(timezone.utc)
        key = receipt_id(body.receipt)
        # A client-generated receipt makes a retry after a lost response safe.
        existing = await db.support_requests.find_one({"_id": key}, {"category": 1, "message": 1})
        if existing:
            if existing["category"] != body.category or existing["message"] != body.message:
                raise HTTPException(409, "This receipt already belongs to another request.")
            return {"received": True}
        host = request.client.host if request.client else "unknown"
        window = now.strftime("%Y-%m-%dT%H")
        bucket = hmac.new(rate_secret, f"support:{window}:{host}".encode(), hashlib.sha256).hexdigest()
        try:
            await db.support_rate.update_one({"_id": bucket, "count": {"$lt": 12}},
                {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": now + timedelta(hours=2)}}, upsert=True)
        except DuplicateKeyError:
            # Another first submission can create this bucket after the upsert
            # checks for a match. Retry only a guarded increment: a duplicate
            # key does not itself establish that the hourly limit is exhausted.
            retry = await db.support_rate.update_one({"_id": bucket, "count": {"$lt": 12}},
                {"$inc": {"count": 1}})
            if not retry.modified_count:
                raise HTTPException(429, "Too many requests. Try again next hour.") from None
        record = {"_id": key, "category": body.category, "message": body.message, "status": "open",
                  "created_at": now, "expires_at": now + timedelta(days=90)}
        try:
            await db.support_requests.insert_one(record)
        except DuplicateKeyError:
            match = await db.support_requests.find_one({"_id": key, "category": body.category, "message": body.message}, {"_id": 1})
            if not match:
                raise HTTPException(409, "This receipt already belongs to another request.") from None
        return {"received": True}

    @router.post("/support/status")
    async def status(body: SupportReceipt, response: Response):
        response.headers["Cache-Control"] = "no-store"
        record = await db.support_requests.find_one(
            {"_id": receipt_id(body.receipt), "expires_at": {"$gt": datetime.now(timezone.utc)}}, PUBLIC_FIELDS)
        if not record:
            raise HTTPException(404, "This support request has expired or was deleted.")
        return display_record(record)

    @router.post("/support/follow-up")
    async def follow_up(body: FollowUp):
        stamp = datetime.now(timezone.utc)
        key = {"_id": receipt_id(body.receipt), "expires_at": {"$gt": stamp}}
        row = await db.support_requests.find_one(key, PUBLIC_FIELDS)
        if not row:
            raise HTTPException(404, "This support request has expired or was deleted.")
        history = messages(row)
        current = row.get("round", 0)
        if current == body.round + 1 and any(e["speaker"] == "member" and e.get("round") == current and e["text"] == body.message for e in history):
            return {"received": True}
        if current != body.round or row["status"] != "answered":
            raise HTTPException(409, "Refresh this thread before following up.")
        if current >= 12:
            raise HTTPException(422, "This thread is full. Start a new support request.")
        entry = {"speaker": "member", "text": body.message, "at": stamp, "round": current + 1}
        result = await db.support_requests.update_one({**key, "status": "answered", "round": round_filter(current)},
            {"$set": {"thread": [*history, entry], "status": "open", "open_since": stamp, "round": current + 1},
             "$unset": {"answer": "", "answered_at": ""}})
        if not result.modified_count:
            raise HTTPException(409, "The thread changed. Refresh to see the latest message.")
        return {"received": True}

    @router.post("/support/delete")
    async def delete(body: SupportReceipt):
        await db.support_requests.delete_one({"_id": receipt_id(body.receipt)})
        return {"deleted": True}

    @router.get("/admin/support/summary")
    async def summary(response: Response, _: dict = Depends(admin_dep)):
        now = datetime.now(timezone.utc)
        response.headers["Cache-Control"] = "no-store"
        active = {"expires_at": {"$gt": now}}
        open_count = await db.support_requests.count_documents({**active, "status": "open"})
        pipeline = [{"$match": {**active, "created_at": {"$gte": now - timedelta(days=30)},
                    "first_answered_at": {"$type": "date"}}},
                    {"$group": {"_id": None, "samples": {"$sum": 1},
                     "mean_ms": {"$avg": {"$subtract": ["$first_answered_at", "$created_at"]}}}}]
        completed = await db.support_requests.aggregate(pipeline).to_list(1)
        return {"as_of": now, "open": open_count, "answered_with_time": completed[0]["samples"] if completed else 0,
                "mean_first_response_minutes": round(completed[0]["mean_ms"]/60000, 1) if completed else None}

    @router.get("/admin/support")
    async def inbox(response: Response, status: Literal["open", "answered"] = "open", admin: dict = Depends(admin_dep),
                    after_time: datetime | None = None, after_id: Receipt | None = None,
                    limit: int = Query(50, ge=1, le=51)):
        response.headers["Cache-Control"] = "no-store"
        query = {"status": status, "expires_at": {"$gt": datetime.now(timezone.utc)}}
        direction = 1 if status == "open" else -1
        if (after_time is None) != (after_id is None):
            raise HTTPException(422, "Include both the request time and ID to continue.")
        if after_time is not None:
            operator = "$gt" if direction == 1 else "$lt"
            query["$or"] = [{"created_at": {operator: after_time}},
                            {"created_at": after_time, "_id": {operator: after_id}}]
        rows = await db.support_requests.find(query, {"reply_receipts": 0}).sort([("created_at", direction), ("_id", direction)]).limit(limit).to_list(limit)
        return [{"id": row.pop("_id"), **display_record(row),
                 "assigned_to_me": bool(admin.get("user_id") and row.get("assigned_to") == admin["user_id"])} for row in rows]

    @router.put("/admin/support/{request_id}/assignment")
    async def assign(request_id: Receipt, body: Assignment, admin: dict = Depends(admin_dep)):
        uid = admin.get("user_id")
        if not uid:
            raise HTTPException(403, "An identified staff account is required for assignment.")
        stamp = datetime.now(timezone.utc)
        guard = {"_id": request_id, "status": "open", "expires_at": {"$gt": stamp}, "assigned_to": body.expected_owner}
        if not body.claim and body.expected_owner != uid:
            raise HTTPException(409, "Only the assigned staff member can release this request.")
        update = {"$set": {"assigned_to": uid, "assigned_name": admin.get("nickname") or "Staff", "assigned_at": stamp}} if body.claim else {
            "$unset": {"assigned_to": "", "assigned_name": "", "assigned_at": ""}}
        if not (await db.support_requests.update_one(guard, update)).matched_count:
            raise HTTPException(409, "Assignment changed. Refresh before claiming this request.")
        return {"assigned": body.claim}


    @router.put("/admin/support/{request_id}")
    async def answer(request_id: str, body: SupportAnswer, admin: dict = Depends(admin_dep)):
        stamp = datetime.now(timezone.utc)
        key = {"_id": request_id, "round": round_filter(body.round), "status": "open", "expires_at": {"$gt": stamp}}
        author = admin.get("user_id")
        receipt_field = f"reply_receipts.r{body.round}"
        digest = hashlib.sha256(json.dumps([author, body.answer], ensure_ascii=False).encode()).hexdigest()
        async def already_sent():
            return bool(author and await db.support_requests.find_one(
                {"_id": request_id, "expires_at": {"$gt": stamp}, receipt_field: digest}, {"_id": 1}))

        row = await db.support_requests.find_one(key)
        if not row:
            if await already_sent():
                return {"answered": True}
            raise HTTPException(409, "The support thread changed or closed. Refresh before replying.")
        if row.get("assigned_to") and row["assigned_to"] != admin.get("user_id"):
            raise HTTPException(409, "Claim this request before replying.")
        update = {"answer": body.answer, "answered_at": stamp, "status": "answered",
                  "thread": [*messages(row), {"speaker": "support", "text": body.answer, "at": stamp, "round": body.round}]}
        if author:
            update[receipt_field] = digest
        if body.round == 0:
            update["first_answered_at"] = stamp
        result = await db.support_requests.update_one({**key, "assigned_to": row.get("assigned_to")}, {"$set": update})
        if not result.modified_count and not await already_sent():
            raise HTTPException(409, "The thread or assignment changed. Refresh before replying.")
        return {"answered": True}

    return router
