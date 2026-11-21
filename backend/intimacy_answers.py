"""Private, resumable Sex & Intimacy answers belonging to one member."""
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

try:
    from . import answer_context, answer_history, intimacy_catalog, research
except ImportError:
    import answer_context, answer_history, intimacy_catalog, research


class AnswerEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0, lt=2**53)
    adult_confirmed: StrictBool
    id: str = Field(max_length=150)
    topic: str = Field(max_length=160)
    question: str = Field(max_length=650)
    phrase: str = Field(max_length=350)
    selection: research.Selection | None


def conflict():
    return HTTPException(409, "Your saved answers changed. Reopen the questionnaire to see the latest answers.")


async def adult(db, uid):
    member = await db.users.find_one({"user_id": uid, "closing": {"$ne": True}},
        {"about.demographics.age": 1, **{field: 1 for field in answer_history.CONSENT_FIELDS}})
    if member is None:
        raise HTTPException(403, "These activities are available to adults with an active account.")
    try:
        research.age_band(member)
    except HTTPException:
        raise HTTPException(403, "These activities are for adults aged 18 and over.") from None
    return member


def catalog_variables(overrides):
    _, catalog = research.catalog({key: [] for key in ("short", "extended", "further")},
                                  overrides)
    return {key: v for key, v in catalog.items() if v["domain"] == "intimacy"}


async def variables(db):
    return catalog_variables(await intimacy_catalog.overrides(db))


def own_item(row, key, value):
    wording = (row.get("wording") or {}).get(key, {})
    return {"id": key, "topic": wording.get("topic"), "question": wording.get("question"),
            "answer": wording.get("answer"), "skipped": value is None,
            "conditions": (row.get("conditions") or {}).get(key, [])}


async def read(db, uid):
    member = await adult(db, uid)
    row = await db.intimacy_answers.find_one({"_id": uid}) or {}
    catalog = await variables(db)
    return display(row, catalog)


def display(row, catalog):
    answers = {}
    previous = []
    for key, value in row.get("values", {}).items():
        variable = catalog.get(key)
        if variable is None or (row.get("signatures") or {}).get(key) != variable["signature"]:
            previous.append(own_item(row, key, value))
            continue
        answers[key] = None if value is None else {"choice": research.CHOICES[value],
                                                    "conditions": (row.get("conditions") or {}).get(key, [])}
    return {"revision": row.get("revision", 0), "answers": answers, "previous": len(previous),
            **({"previous_answers": previous} if previous else {})}


async def save(db, uid, body):
    if not body.adult_confirmed:
        raise HTTPException(422, "Confirm you are 18 or older before saving an answer.")
    member = await adult(db, uid)
    variable = (await variables(db)).get(body.id)
    if variable is None or (body.topic, body.question, body.phrase) != (
            variable["topic"], variable["label"], variable["phrase"]):
        raise HTTPException(412, "This question changed. Refresh the questionnaire to see its current wording.")
    key = body.id
    choice, conditions = None, []
    if body.selection is not None:
        selection = body.selection
        if (selection.choice not in research.CHOICES or len(selection.conditions) != len(set(selection.conditions))
                or any(tag not in research.CONDITIONS for tag in selection.conditions)
                or (selection.conditions and selection.choice != "It depends")):
            raise HTTPException(422, "Choose a published answer and conditions.")
        choice = research.CHOICES.index(selection.choice)
        conditions = [tag for tag in research.CONDITIONS if tag in selection.conditions]
    await answer_history.flush(db, "intimacy_answers", {"_id": uid})
    previous = await db.intimacy_answers.find_one({"_id": uid}) or {}
    if previous.get("revision", 0) != body.revision:
        raise conflict()
    if (key in previous.get("values", {}) and previous["values"][key] == choice
            and previous.get("conditions", {}).get(key, []) == conditions
            and previous.get("signatures", {}).get(key) == variable["signature"]):
        return {"revision": body.revision}
    fields = {"user_id": uid, "adult_confirmed": True, "updated_at": datetime.now(timezone.utc).isoformat(),
              f"values.{key}": choice, f"conditions.{key}": conditions,
              f"signatures.{key}": variable["signature"],
              f"wording.{key}": {"question": variable["label"], "topic": variable["topic"],
                                  "answer": body.selection.choice if body.selection else None}}
    observation = answer_history.event(source="intimacy_answers", source_id=uid, owner_id=uid,
        domain="intimacy", question_id=key,
        after={"choice": choice, "answer": body.selection.choice if body.selection else None,
               "conditions": conditions, "skipped": body.selection is None},
        question={"id": key, "question": variable["label"], "topic": variable["topic"], "phrase": variable["phrase"]},
        signature=variable["signature"], demographics=await answer_context.for_member(db, uid),
        consent=answer_history.consent(member), revision=body.revision + 1,
        attempt_id=f"intimacy:{uid}:{body.revision + 1}",
        before=own_item(previous, key, previous["values"][key]) if key in previous.get("values", {}) else None,
        before_exists=key in previous.get("values", {}), before_observed_at=previous.get("updated_at"))
    update = answer_history.append({"$set": fields, "$inc": {"revision": 1}}, observation, previous)
    # Read the source generation before rechecking the owner. A completed minor
    # cleanup is then either visible here or fences this CAS through generation.
    await adult(db, uid)
    try:
        result = await db.intimacy_answers.update_one(
            answer_history.guard({"_id": uid, "revision": body.revision}, previous), update,
            upsert=body.revision == 0)
    except DuplicateKeyError:
        raise conflict() from None
    if not result.matched_count and result.upserted_id is None:
        raise conflict()
    revision = body.revision + 1
    # A concurrent age correction may finish between the original check and this write.
    try:
        await adult(db, uid)
    except HTTPException:
        await clear(db, uid, revision=revision, require_match=False)
        raise
    await answer_history.flush(db, "intimacy_answers", {"_id": uid})
    return {"revision": revision}


async def remove_one(db, uid, key, revision):
    # A recorded skip has a null value. Existence, rather than truthiness, distinguishes it.
    previous = await db.intimacy_answers.find_one({"_id": uid, "revision": revision})
    if previous is None:
        raise conflict()
    update = answer_history.redact(
        {"$unset": {f"{field}.{key}": "" for field in ("values", "conditions", "signatures", "wording")},
         "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}, "$inc": {"revision": 1}},
        previous, question_id=key)
    row = await db.intimacy_answers.find_one_and_update(
        answer_history.guard({"_id": uid, "revision": revision, f"values.{key}": {"$exists": True}}, previous), update,
        projection={"revision": 1}, return_document=ReturnDocument.AFTER)
    if row is None:
        raise conflict()
    await answer_history.flush(db, "intimacy_answers", {"_id": uid}, strict=True)
    return {"revision": row["revision"]}


async def clear(db, uid, revision=None, require_match=True):
    criteria = {"_id": uid}
    if revision is not None:
        criteria["revision"] = revision
    for _ in range(8):
        previous = await db.intimacy_answers.find_one(criteria)
        if previous is None and revision not in (None, 0):
            if require_match:
                raise conflict()
            return None
        previous = previous or {}
        update = answer_history.redact({"$set": {"user_id": uid}, "$inc": {"revision": 1},
            "$unset": {key: "" for key in previous if key not in {
                "_id", "user_id", "revision", *answer_history.FIELDS}}}, previous)
        try:
            row = await db.intimacy_answers.find_one_and_update(answer_history.guard(criteria, previous), update,
                upsert=not previous and revision in (None, 0), return_document=ReturnDocument.AFTER)
        except DuplicateKeyError:
            if revision is None:
                continue
            raise conflict() from None
        if row:
            await answer_history.flush(db, "intimacy_answers", {"_id": uid}, strict=True)
            return {"revision": row["revision"]}
        if revision is not None:
            if require_match:
                raise conflict()
            return None
    raise conflict()


async def own_export(db, uid):
    row = await db.intimacy_answers.find_one({"_id": uid})
    if not row or not row.get("values"):
        return []
    return [own_item(row, key, value) for key, value in row["values"].items()]
