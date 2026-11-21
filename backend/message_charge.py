"""Content-free link between an allowance settlement and its provider work."""
import hashlib
from contextlib import nullcontext
from datetime import datetime, timezone

from pymongo import IndexModel
from pymongo.errors import DuplicateKeyError

try:
    from . import ai_meter, message_costs
except ImportError:
    import ai_meter
    import message_costs


INDEXES = {
    "message_cost_units": [
        IndexModel([("env", 1), ("key_id", 1), ("status", 1), ("settled_at", 1)], name="cost_settlements"),
        IndexModel([("env", 1), ("started_at", -1), ("_id", -1)], name="cost_receipt_order"),
        IndexModel("preparation_id", unique=True, sparse=True, name="cost_upload_once"),
    ],
}


def upload_id(member_id, attempt):
    if attempt is None:
        return None
    # Never accept another account's or another environment's message ID from the client.
    return hashlib.sha256(f"imladri:upload:{member_id}:{attempt}".encode()).hexdigest()


def starter_id(member_id, people, batch):
    if batch is None:
        return None
    return hashlib.sha256(f"imladri:starters:{member_id}:{people}:{batch}".encode()).hexdigest()


def unit_event(receipt, status):
    event = {"_id": receipt["result"], "env": receipt["cost_env"], "key_id": receipt["cost_key_id"],
             "workspace": "couples" if receipt["kind"] in ("couples_tone", "couples_quick_advice") else "individual",
             "kind": receipt["kind"], "pool": "intro" if receipt.get("pool") == "intro" else "complimentary" if receipt.get("pool") == "complimentary" else "refill" if receipt.get("pool") == "topup" else "plan",
             "started_at": receipt["cost_started_at"], "status": status,
             "sequence": receipt["cost_run"] * 2 + int(status != "pending")}
    if status != "pending":
        event["settled_at"] = datetime.now(timezone.utc)
    if status == "settled" and receipt.get("cost_upload"):
        event["preparation_id"] = receipt["cost_upload"]
    return event


async def persist(db, event):
    unit = db.message_cost_units
    rid = event["_id"]
    # Reserve the ID first. A delayed flush from a refunded run cannot downgrade its successful retry.
    await unit.update_one({"_id": rid}, {"$setOnInsert": {"sequence": 0}}, upsert=True)
    values = {k: v for k, v in event.items() if k not in ("_id", "preparation_id")}
    await unit.update_one({"_id": rid, "sequence": {"$lt": event["sequence"]}}, {"$set": values})
    if "preparation_id" in event:
        try:
            await unit.update_one({"_id": rid, "sequence": event["sequence"], "preparation_id": {"$exists": False}},
                                  {"$set": {"preparation_id": event["preparation_id"]}})
        except DuplicateKeyError:
            # A member can submit the same upload twice. Only its first attachment owns the parse cost.
            pass


async def flush_event(db, uid, event):
    await persist(db, event)
    field = "message_cost_outbox." + event["_id"]
    await db.users.update_one({"user_id": uid, field + ".sequence": event["sequence"]}, {"$unset": {field: ""}})


async def flush_message_cost_outbox(db, env, uid=None):
    query = {"env": {"$in": [env, None]}, "message_cost_outbox": {"$gt": {}}}
    if uid is not None:
        query["user_id"] = uid
    async for member in db.users.find(query, {"_id": 0, "user_id": 1, "message_cost_outbox": 1}):
        for event in member["message_cost_outbox"].values():
            if event["env"] == env:
                await flush_event(db, member["user_id"], event)


async def saved_context(db, rid, env):
    unit = await db.message_cost_units.find_one({"_id": rid, "env": env}, {"workspace": 1, "pool": 1, "kind": 1})
    return message_costs.message_context(rid, unit["workspace"], run_number=None, pool=unit.get("pool"), kind=unit.get("kind")) if unit else nullcontext()


async def enqueue(db, bg, func, *args, **kwargs):
    context = message_costs.current_message.get()
    if not context:
        pool = message_costs.current_pool.get()
        async def unbound_run():
            token = message_costs.current_pool.set(pool)
            try:
                return await ai_meter.in_context("background", func, *args, **kwargs)
            finally:
                message_costs.current_pool.reset(token)
        bg.add_task(unbound_run)
        return
    rid, workspace = context
    run_number = message_costs.current_run.get()
    pool = message_costs.current_pool.get()
    kind = message_costs.current_kind.get()
    await db.message_cost_units.update_one({"_id": rid}, {"$inc": {"background_pending": 1}})

    async def run():
        # Starlette starts BackgroundTasks after the originating request scope has exited.
        try:
            with message_costs.message_context(rid, workspace, run_number=run_number, pool=pool, kind=kind):
                return await ai_meter.in_context("background", func, *args, **kwargs)
        finally:
            await db.message_cost_units.update_one({"_id": rid}, {"$inc": {"background_pending": -1}})

    bg.add_task(run)
