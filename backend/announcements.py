"""Durable staff broadcasts and aggregate counts from recipients' current inboxes."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ReturnDocument


class Announcement(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", max_length=400)
    link: str = Field(default="/app", max_length=200)
    request_id: UUID | None = None
    audience_as_of: datetime | None = None

    @field_validator("link")
    @classmethod
    def application_link(cls, value):
        if not value:
            return "/app"
        if not value.startswith("/") or value.startswith("//") or "\\" in value or any(ord(c) < 32 for c in value):
            raise ValueError("Choose a page path on Imladri, such as /app.")
        return value


def recipients(env_query, through=None):
    query = {**env_query, "closing": {"$ne": True}}
    if through:
        query["$or"] = [{"created_at": {"$lte": through}}, {"created_at": None}]
    return query


async def preview(db, env_query, message, timestamp):
    size = await db.users.count_documents(recipients(env_query, timestamp))
    return {**message.model_dump(exclude={"request_id", "audience_as_of"}), "as_of": timestamp, "eligible_inboxes": size}


async def send(db, env, message, timestamp, notify_all):
    key = f"announce:{message.request_id or uuid4()}"
    content = message.model_dump(exclude={"request_id", "audience_as_of"})
    identity = {"_id": env + ":" + key}
    cutoff = timestamp
    if message.audience_as_of is not None:
        stamp = datetime.fromisoformat(timestamp)
        if message.audience_as_of.tzinfo is None:
            raise HTTPException(422, "Refresh the audience preview.")
        cutoff = message.audience_as_of.astimezone(timezone.utc).isoformat()
        if not stamp - timedelta(minutes=15) <= message.audience_as_of <= stamp:
            if not await db.announcements.find_one(identity, {"_id": 1}):
                raise HTTPException(409, "The audience preview expired. Preview it again before sending.")
    record = await db.announcements.find_one_and_update(identity, {"$setOnInsert": {
        "id": key, "env": env, **content, "created_at": timestamp, "audience_as_of": cutoff, "status": "pending",
    }}, upsert=True, return_document=ReturnDocument.AFTER)
    if any(record[field] != value for field, value in content.items()):
        raise HTTPException(409, "This send already belongs to another announcement.")
    if record["status"] == "pending":
        await notify_all("announcement", record["title"], record["body"], record["link"], key,
                         created_through=record.get("audience_as_of") or record["created_at"])
        inboxes = await db.notifications.count_documents({"type": "announcement", "key": key})
        await db.announcements.update_one({**identity, "status": "pending"},
            {"$set": {"status": "sent", "inboxes_at_completion": inboxes, "completed_at": datetime.now(timezone.utc).isoformat()}})
    return {"ok": True, "id": key}


async def history(db, env_query, limit, after_time, after_id):
    query = dict(env_query)
    if bool(after_time) != bool(after_id):
        raise HTTPException(422, "Include the announcement time and ID to continue.")
    if after_id:
        query["$or"] = [{"created_at": {"$lt": after_time}}, {"created_at": after_time, "id": {"$lt": after_id}}]
    rows = await db.announcements.find(query, {"_id": 0, "env": 0}).sort([("created_at", -1), ("id", -1)]).limit(limit).to_list(limit)
    if rows:
        counts = {r["_id"]: r async for r in db.notifications.aggregate([
            {"$match": {"key": {"$in": [r["id"] for r in rows]}, "type": "announcement"}},
            {"$group": {"_id": "$key", "inboxes": {"$sum": 1}, "read": {"$sum": {"$cond": ["$read", 1, 0]}}}},
        ])}
        for row in rows:
            count = counts.get(row["id"], {})
            row.update(inboxes=count.get("inboxes", 0), read=count.get("read", 0))
            row.setdefault("status", "sent")  # Historical records were written after delivery.
    return rows
