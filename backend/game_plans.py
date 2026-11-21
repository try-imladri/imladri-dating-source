"""Member-facing Game Plan branches, including plans saved before odds were retired."""
import re

from fastapi import HTTPException

try:
    from . import contact_boundary
except ImportError:
    import contact_boundary


# Keep concrete amounts in the situation or proposed conversation, e.g. a 10% raise.
# Omit percentages presented as forecasts, even inside a model-generated text field.
PERCENT = r"\d+(?:\.\d+)?\s*(?:%|percent\b)"
ODDS = re.compile(
    rf"(?:{PERCENT}\s*(?:chance|likel(?:y|ihood)|probab(?:le|ility)|odds)\b|"
    rf"\b(?:chance|probability|likelihood|odds)(?:\s+(?:of|is|are|at|about|roughly))?\s*[:=~-]?\s*{PERCENT}|"
    rf"^\s*\(?{PERCENT}\)?\s*[:–—-]|\(\s*{PERCENT}\s*\))",
    re.IGNORECASE,
)


def plan_text(value):
    if not isinstance(value, str) or ODDS.search(value):
        return ""
    return value.strip()


class IncompletePlan(HTTPException):
    def __init__(self):
        super().__init__(502, "The game plan was incomplete. Please try again.")


def clean_plan(raw, *, generated=False):
    raw = raw if isinstance(raw, dict) else {}
    avoid = raw.get("avoid") or []
    avoid = avoid if isinstance(avoid, list) else [avoid]
    plan = {"opening_line": plan_text(raw.get("opening_line")), "reactions": [],
            "avoid": [text for item in avoid if (text := plan_text(item))],
            "exit_line": plan_text(raw.get("exit_line")), "uncertainty": plan_text(raw.get("uncertainty"))}
    if raw.get("contact_boundary") is True:
        return {**plan, "contact_boundary": True, "opening_line": None, "exit_line": None}
    reactions = raw.get("reactions") or []
    for entry in reactions if isinstance(reactions, list) else [reactions]:
        branch = entry if isinstance(entry, dict) else {"reaction": entry}
        reaction = plan_text(branch.get("reaction"))
        response = plan_text(branch.get("how_to_handle"))
        if reaction and (response or not generated):
            plan["reactions"].append({"reaction": reaction, "how_to_handle": response})
    if generated and (not plan["opening_line"] or not plan["exit_line"] or not 3 <= len(plan["reactions"]) <= 4):
        raise IncompletePlan()
    return plan


def saved_plan(document):
    document = contact_boundary.saved_plan(document)
    return {**document, "plan": clean_plan(document.get("plan"))}


async def clear_derived_memories(db, owner, source_ids):
    """Keep independently edited notes, removing their broken links to automatic sources."""
    pending = set(source_ids)
    visited = set()
    removed = 0
    while pending:
        parents = list(pending)
        visited.update(pending)
        query = {**owner, "derived_from": {"$in": parents}}
        children = await db.memories.distinct("id", {**query, "edited_at": None})
        await db.memories.update_many({**query, "edited_at": {"$ne": None}}, {
            "$pull": {"derived_from": {"$in": parents}}, "$set": {"source": "Member-edited note"}})
        deleted = await db.memories.delete_many({**query, "edited_at": None, "id": {"$in": children}})
        removed += deleted.deleted_count
        pending = set(children) - visited
    return removed


async def clear_outcome_memories(db, query):
    owner = {key: query[key] for key in ("user_id", "person_id")}
    automatic = await db.memories.distinct("id", {**query, "edited_at": None})
    derived = await clear_derived_memories(db, owner, automatic)
    deleted = await db.memories.delete_many({**query, "id": {"$in": automatic}, "edited_at": None})
    edited = await db.memories.update_many({**query, "edited_at": {"$ne": None}}, {
        "$set": {"source": "Member-edited outcome"}, "$unset": {"prep_id": ""}})
    return edited.modified_count + deleted.deleted_count + derived


async def clear_deleted_plan(db, owner, prep_id, *, had_outcome):
    query = {**owner, "prep_id": prep_id, "kind": "outcome"}
    removed = await clear_outcome_memories(db, query)
    await db.notifications.delete_many({"user_id": owner["user_id"], "key": f"checkin:{prep_id}"})
    if removed or had_outcome:
        await db.people.update_one({"user_id": owner["user_id"], "id": owner["person_id"]},
            {"$set": {"report": None, "working_read": None, "report_status": "empty"}, "$unset": {"report_run": ""}})


async def repair_deleted_outcomes(db):
    # Finish an interrupted deletion. A retry is safe from another API instance.
    async for prep in db.preps.find({"deleting": True}, {"id": 1, "user_id": 1, "person_id": 1, "outcome": 1}):
        owner = {"user_id": prep["user_id"], "person_id": prep["person_id"]}
        await clear_deleted_plan(db, owner, prep["id"], had_outcome=bool(prep.get("outcome")))
        await db.preps.delete_one({"_id": prep["_id"], "deleting": True})
    marker = {"_id": "repair_deleted_game_plan_outcomes_v1"}
    if await db.settings.find_one(marker, {"_id": 1}):
        return
    groups = db.memories.aggregate([
        {"$match": {"kind": "outcome", "prep_id": {"$type": "string"}, "user_id": {"$type": "string"},
                    "person_id": {"$type": "string"}}},
        {"$group": {"_id": {"user_id": "$user_id", "person_id": "$person_id", "prep_id": "$prep_id"}}},
    ])
    async for row in groups:
        uid, pid, xid = (row["_id"][key] for key in ("user_id", "person_id", "prep_id"))
        owner = {"user_id": uid, "person_id": pid}
        if not await db.preps.find_one({**owner, "id": xid}, {"_id": 1}):
            await clear_deleted_plan(db, owner, xid, had_outcome=True)
    await db.settings.update_one(marker, {"$set": {"done": True}}, upsert=True)
