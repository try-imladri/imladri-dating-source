"""Account demographics and participation in ongoing questionnaire comparisons."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument

try:
    from . import intimacy_answers, quiz_answers, research, individual_history
except ImportError:
    import intimacy_answers, quiz_answers, research, individual_history


CONSENT_VERSION = "all-answers-v1"
TONE_CONSENT = "conversation-tone-v1"
ROW_REVISIONS = {f"row_{scope}_revision": 1 for scope in ("member", "couple")}
ROW_CLEAR_FIELDS = {**{f"row_{scope}_{key}": "" for scope in ("member", "couple")
                        for key in ("code", "at", "version", "env")}, "row_couple_pair_id": ""}
SEXUALITY_OPTIONS = ("straight", "bi", "gay", "lesbian")
OWN_OPTIONS = {**quiz_answers.DEMOGRAPHIC_OPTIONS, "gender": quiz_answers.GENDER_OPTIONS,
               "sexuality": SEXUALITY_OPTIONS}


class Participation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal["all-answers-v1"]
    enabled: bool
    expected_member: str | None = Field(default=None, max_length=256)
    adult_confirmed: bool = False
    revision: int = Field(ge=0, lt=2**53)


class ToneParticipation(Participation):
    version: Literal["conversation-tone-v1"]


def number(value, lower, upper):
    if type(value) not in (int, str):
        return None
    digits = str(value).strip()
    if digits.isascii() and digits.isdigit() and 1 <= len(digits) <= 3 and lower <= int(digits) <= upper:
        return int(digits)
    return None


def clean_demo(d, *, own=False):
    out = {}
    for key, options in (OWN_OPTIONS if own else quiz_answers.DEMOGRAPHIC_OPTIONS).items():
        if d.get(key) in options:
            out[key] = d[key]
        elif own and d.get(key) not in (None, ""):
            raise HTTPException(422, f"Choose one of the available options for {key}.")
    for key, lo, hi in [("age", 13, 120), ("height_cm", quiz_answers.HEIGHT_MIN_CM, quiz_answers.HEIGHT_MAX_CM)]:
        if key == "height_cm" and not own:
            continue
        value = number(d.get(key), lo, hi)
        if value is not None:
            out[key] = value
        elif own and d.get(key) not in (None, ""):
            raise HTTPException(422, f"Enter a whole number between {lo} and {hi} for {key.replace('_cm', ' (cm)')}.")
    return out


def demographics_patch(fields):
    values = clean_demo(fields, own=True)
    changes = {f"about.demographics.{key}": value for key, value in values.items()}
    removals = {f"about.demographics.{key}": "" for key in ("age", "height_cm", *OWN_OPTIONS)
                if key in fields and fields[key] in (None, "")}
    return changes, removals


def protect_minor(changes, removals):
    changes.update({"couples_preferences.analytics_opt_in": False, "couples_preferences.reminders": False,
                    "questionnaire_research_managed": True})
    removals.update(ROW_CLEAR_FIELDS)
    removals.update({"couples_adult_at": "", "questionnaire_research_version": "", "questionnaire_research_at": ""})
    removals.update({"tone_research_version": "", "tone_research_at": "", "assessment_repeat": "", "intimacy_wording_review": ""})
    removals.update({"your_type_research_version": "", "your_type_research_at": ""})
    removals.update({"answer_history_staff_" + key: "" for key in ("version", "id", "at", "env")})


async def save_own(db, uid, fields):
    changes, removals = demographics_patch(fields)
    minor = changes.get("about.demographics.age", 18) < 18
    changes["about.updated_at"] = datetime.now(timezone.utc).isoformat()
    if minor:
        protect_minor(changes, removals)
    update = {"$set": changes}
    if removals:
        update["$unset"] = removals
    if minor:
        update["$inc"] = {**ROW_REVISIONS, "questionnaire_research_revision": 1, "tone_research_revision": 1, "intimacy_wording_revision": 1, "your_type_research_revision": 1, "answer_history_staff_revision": 1}
    for path in ("about", "about.demographics"):
        await db.users.update_one({"user_id": uid, path: {"$type": "null"}}, {"$set": {path: {}}})
    stored = await individual_history.save(db, uid, {"user_id": uid, "closing": {"$ne": True}}, update)
    if not stored:
        raise HTTPException(409, "Your account changed. Reload to continue.")
    if minor:
        await research.clear(db, uid)
        await intimacy_answers.clear(db, uid)
        from .your_type import clear_member
        await clear_member(db, uid)
    return stored.get("about", {}).get("demographics", {})


def preference(member, scope="questionnaire_research"):
    version = TONE_CONSENT if scope == "tone_research" else CONSENT_VERSION
    return {"enabled": member.get(scope + "_version") == version,
            "revision": member.get(scope + "_revision", 0), "version": version}


def participation_update(member, body, *, at=None):
    """Build the existing validated grant CAS without writing it separately."""
    uid = member["user_id"]
    scope = "tone_research" if isinstance(body, ToneParticipation) else "questionnaire_research"
    if body.expected_member is not None and body.expected_member != uid:
        raise HTTPException(409, "The signed-in account changed. Reopen Settings.")
    where = {"user_id": uid, "closing": {"$ne": True},
             scope + "_revision": body.revision if body.revision else {"$in": [None, 0]}}
    fields = {"questionnaire_research_managed": True} if scope == "questionnaire_research" else {}
    update = {"$inc": {scope + "_revision": 1}}
    if body.enabled:
        if member.get("role") != "member" or not body.adult_confirmed:
            raise HTTPException(403, "Confirm you are an adult to participate in research.")
        research.age_band(member)
        where["role"] = "member"
        where["about.demographics.age"] = ((member.get("about") or {}).get("demographics") or {}).get("age")
        fields.update({scope + "_version": body.version, scope + "_at": at or datetime.now(timezone.utc).isoformat()})
    else:
        update["$unset"] = {scope + "_version": "", scope + "_at": ""}
        if scope == "questionnaire_research":
            update["$unset"]["assessment_repeat"] = ""
    if fields:
        update["$set"] = fields
    return scope, where, update


async def participate(db, member, body):
    scope, where, update = participation_update(member, body)
    uid = member["user_id"]
    updated = await individual_history.save(db, uid, where, update)
    if updated is None:
        raise HTTPException(409, "Your account or preference changed. Refresh to see its current status.")
    return preference(updated, scope)
