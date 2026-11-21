"""Owner exports, erasure and bounded recovery for accepted answer journals."""
import asyncio
import logging

try:
    from . import answer_context, answer_history
except ImportError:
    import answer_context, answer_history


SOURCES = ("users", "intimacy_answers", "research_responses", "couples_drafts", "couples_pairs", "people", "your_type_sessions")
PRIVATE_FIELDS = (*answer_history.FIELDS, *answer_context.PRIVATE_FIELDS)
log = logging.getLogger(__name__)


def owner_query(source, uid):
    return {"_id": uid} if source in ("intimacy_answers", "research_responses") else {"user_id": uid}


async def prepare_export(db, uid):
    handled = set()
    for source in SOURCES:
        criteria = {"members": uid, "state": "active"} if source == "couples_pairs" else owner_query(source, uid)
        async for row in db[source].find(criteria, {"_id": 1}):
            await answer_history.flush(db, source, {"_id": row["_id"]}, strict=True)
    # A closed pair can retain a durable erasure queue while cleanup retries.
    # Its former membership is intentionally absent from the live access path.
    async for identity in db.answer_history.aggregate([
        {"$match": {"owner_id": uid, "purged": {"$ne": True}}},
        {"$group": {"_id": {"source": "$source", "source_id": "$source_id"}}}]):
        item = identity["_id"]
        key = (item["source"], item["source_id"])
        if key in handled or item["source"] not in SOURCES:
            continue
        handled.add(key)
        await answer_history.flush(db, item["source"], {
            answer_history.SOURCE + ".source": item["source"],
            answer_history.SOURCE + ".source_id": item["source_id"]}, strict=True)


async def own_events(db, uid):
    # Shared events contain only the actor's own contribution; never a peer's private check-in.
    async for row in db.answer_history.find({"owner_id": uid, "actor_id": uid, "purged": {"$ne": True}},
        {"_id": 0, "generation": 0, "source_id": 0}).sort([("at", 1), ("id", 1)]):
        if row.get("domain") == "your_type":
            from .your_type import expand_event
            row = expand_event(row)
        yield row


async def purge_owner(db, uid):
    """Caller first holds the account closure/content-clear writer barrier.

    Couples close and erase their own shared sources before this operation.
    Keep opaque central tombstones, so a previously started archive cannot
    restore erased payload after the source document is physically deleted.
    """
    for source in SOURCES:
        if source == "couples_pairs":
            continue
        async for row in db[source].find(owner_query(source, uid), {"_id": 1}):
            await answer_history.purge(db, source, {"_id": row["_id"]}, strict=True)
    await db.users.update_one({"user_id": uid}, {"$unset": {field: "" for field in answer_context.PRIVATE_FIELDS}})


async def recover_once(db, limit=50):
    result = {"sources": 0, "archived": 0, "redacted": 0, "failed": 0}
    for source in SOURCES:
        remaining = limit - result["sources"]
        if remaining <= 0:
            break
        query = {"$or": [{answer_history.PENDING + ".0": {"$exists": True}},
                         {answer_history.REDACTIONS + ".0": {"$exists": True}}]}
        async for row in db[source].find(query, {"_id": 1}).limit(remaining):
            status = await answer_history.flush(db, source, {"_id": row["_id"]})
            result["sources"] += 1
            for key in ("archived", "redacted"):
                result[key] += status[key]
            result["failed"] += bool(status["failed"])
    return result


async def recover(db):
    while True:
        try:
            await recover_once(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Answer-history recovery will retry; source journals remain intact.")
        await asyncio.sleep(15)
