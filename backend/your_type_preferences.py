"""Private, explicitly stated ranges, separate from inferred choice profiles."""
from copy import deepcopy
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pymongo import ReturnDocument

from . import answer_history, your_type


VERSION = "your-type-stated-preferences-v1"
FIELD = "your_type_stated_preferences"
QUESTION = "your_type.stated_preferences"
NOTICE = ("These are private preferences you state yourself, not inferred from portrait choices or used for automatic matching. "
          "They are not included in existing research permissions or staff history exports.")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Ranges(Input):
    age_min: int | None = Field(ge=18, le=100)
    age_max: int | None = Field(ge=18, le=100)
    height_min_cm: int | None = Field(ge=120, le=230)
    height_max_cm: int | None = Field(ge=120, le=230)

    @model_validator(mode="after")
    def paired_ordered_ranges(self):
        for lower, upper in ((self.age_min, self.age_max), (self.height_min_cm, self.height_max_cm)):
            if (lower is None) != (upper is None) or (lower is not None and lower > upper):
                raise ValueError("Provide both ordered range endpoints, or leave both empty.")
        return self


class Audiences(Input):
    men: Ranges
    women: Ranges


class Change(Input):
    expected_member: str = Field(min_length=1, max_length=200)
    version: Literal["your-type-stated-preferences-v1"]
    revision: int = Field(ge=0, lt=2**53 - 1)
    by_audience: Audiences


class Saved(Input):
    version: Literal["your-type-stated-preferences-v1"]
    revision: int = Field(ge=0, lt=2**53)
    by_audience: Audiences
    updated_at: str | None = Field(default=None, max_length=80)


def empty(revision=0, at=None):
    return {"version": VERSION, "revision": revision, "by_audience": {audience: {
        "age_min": None, "age_max": None, "height_min_cm": None, "height_max_cm": None}
        for audience in ("men", "women")}, "updated_at": at}


def stored(member):
    if FIELD not in member:
        return empty()
    try:
        return Saved.model_validate(member[FIELD]).model_dump()
    except ValidationError:
        raise HTTPException(409, "These saved preferences use an unavailable format. Reload before making changes.") from None


def present(value):
    return {**deepcopy(value), "private": True, "research_included": False,
        "inferred_from_choices": False, "automatic_matching": False, "notice": NOTICE}


def conflict():
    return HTTPException(409, "Your account or stated preferences changed. Reload before saving.")


def owner_query(member):
    query = {"_id": member["_id"], "user_id": member["user_id"], "closing": {"$ne": True},
        FIELD: {"$exists": FIELD in member}}
    if FIELD in member:
        query["$expr"] = {"$eq": ["$" + FIELD, {"$literal": member[FIELD]}]}
    return query


async def read(db, uid):
    # Management remains available after paid access or profile age is removed.
    await your_type.account_owner(db, uid)
    member = await db.users.find_one({"user_id": uid, "closing": {"$ne": True}}, {FIELD: 1})
    if member is None:
        raise conflict()
    return present(stored(member))


async def save(db, uid, body):
    if body.expected_member != uid:
        raise conflict()
    member = await your_type.eligible(db, uid)
    previous = stored(member)
    if previous["revision"] != body.revision:
        raise conflict()
    value = {"version": VERSION, "revision": body.revision + 1,
        "by_audience": body.by_audience.model_dump(), "updated_at": answer_history.now()}
    event = answer_history.event(source="users", source_id=uid, owner_id=uid, actor_id=uid,
        domain="your_type", question_id=QUESTION, kind=QUESTION, after=value,
        before=previous if FIELD in member else None, before_exists=FIELD in member,
        question={"label": "Private stated age and height preferences"}, signature=VERSION,
        consent=answer_history.consent(member), revision=value["revision"], visibility="private")
    update = answer_history.append({"$set": {FIELD: value}}, event, member)
    query = owner_query(member)
    # A concurrent content clear or adult-profile removal cannot be undone by
    # the row read before the Individual/adult/paid eligibility checks.
    query["content_revision"] = ({"$eq": member["content_revision"], "$exists": True}
        if "content_revision" in member else {"$exists": False})
    query["about.demographics.age"] = {"$eq": member["about"]["demographics"]["age"], "$exists": True}
    accepted = await db.users.find_one_and_update(answer_history.guard(query, member), update,
        return_document=ReturnDocument.AFTER)
    if accepted is None:
        raise conflict()
    await answer_history.flush(db, "users", {"_id": member["_id"]})
    return present(value)


async def clear(db, uid):
    """Erase values/history without adult, paid, or active-account admission."""
    for _ in range(5):
        member = await db.users.find_one({"user_id": uid})
        if member is None:
            return
        old = member.get(FIELD)
        if FIELD not in member:
            return
        revision = old.get("revision") if isinstance(old, dict) else None
        revision = revision + 1 if type(revision) is int and 0 <= revision < 2**53 - 1 else 0
        update = answer_history.redact({"$set": {FIELD: empty(revision, answer_history.now())}}, member,
            source="users", source_id=uid, question_id=QUESTION, actor_id=uid)
        query = owner_query(member)
        query.pop("closing")
        accepted = await db.users.update_one(answer_history.guard(query, member), update)
        if accepted.matched_count:
            # A private-range erasure must not wait for unrelated questionnaire
            # inserts before the caller can install its content-clear fence.
            await answer_history.flush(db, "users", {"_id": member["_id"]}, strict=True, redactions_only=True)
            return
    raise conflict()
