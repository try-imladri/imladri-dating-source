"""Voluntary ratings of displayed intimacy wording, collected separately from questionnaire responses."""
from collections import Counter
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument

try:
    from . import answer_context, answer_history, assessment_evidence, questionnaire_report, research, intimacy_answers
except ImportError:
    import answer_context, answer_history, assessment_evidence, questionnaire_report, research, intimacy_answers

VERSION = "intimacy-summary-feedback-v2"
Rating = Literal["faithful", "partly", "missed", "unsure", "inapplicable"]
VALUES = ("faithful", "partly", "missed", "unsure", "inapplicable", "skipped")
FIELDS = ("conditions", "boundaries")


class Revision(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    owner: str = Field(min_length=1, max_length=256)
    account_created: str | None = Field(max_length=80)
    revision: int = Field(ge=0, lt=2**53)


class Ratings(Revision):
    edition: str = Field(min_length=1, max_length=200)
    adult_and_consented: Literal[True]
    conditions: Rating | None = None
    boundaries: Rating | None = None


async def edition(db):
    return VERSION + "/" + research.intimacy_signature(await intimacy_answers.variables(db))


def view(member):
    return {"owner": member["user_id"], "account_created": member.get("created_at"),
            "revision": member.get("intimacy_wording_revision", 0), "response": member.get("intimacy_wording_review")}


async def update(db, member, body, deleting, stamp):
    if body.owner != member["user_id"] or body.account_created != member.get("created_at"):
        raise HTTPException(409, "Your account changed. Reopen the wording feedback.")
    match = {"user_id": body.owner, "created_at": body.account_created, "closing": {"$ne": True}}
    if not deleting:
        research.age_band(member)
        if body.edition != await edition(db):
            raise HTTPException(409, "The displayed wording changed. Reopen the summary to rate the latest edition.")
        if body.conditions is None and body.boundaries is None:
            raise HTTPException(422, "Rate at least one part of the summary.")
        response = {"edition": body.edition, "conditions": body.conditions, "boundaries": body.boundaries,
                    "consent_version": VERSION, "submitted_at": stamp}
        match = questionnaire_report.adult_filter(match)
        fields = {"$set": {"intimacy_wording_review": response}}
    else:
        fields = {"$unset": {"intimacy_wording_review": ""}}
    # CAS on the member document protects withdrawal, an age correction and a concurrent edit.
    match["intimacy_wording_revision"] = body.revision if body.revision else {"$in": [None, 0]}
    await answer_history.flush(db, "users", {"user_id": body.owner})
    source = await db.users.find_one(match)
    if source is None:
        raise HTTPException(409, "This response or its eligibility changed. Check your saved feedback.")
    mutation = {**fields, "$inc": {"intimacy_wording_revision": 1}}
    if deleting:
        answer_history.redact(mutation, source, question_prefix="wording:", source="users", source_id=body.owner)
    else:
        context = await answer_context.for_member(db, body.owner)
        source = await db.users.find_one(match)
        if source is None:
            raise HTTPException(409, "This response or its eligibility changed. Check your saved feedback.")
        previous = source.get("intimacy_wording_review") or {}
        events = []
        for key in FIELDS:
            definition = {"summary_part": key, "edition": body.edition, "ratings": list(VALUES)}
            events.append(answer_history.event(source="users", source_id=body.owner, owner_id=body.owner,
                domain="wording_feedback", question_id="wording:" + key,
                after={"rating": response[key], "skipped": response[key] is None},
                question=definition, signature=research.fingerprint(definition), demographics=context,
                consent={**answer_history.consent(source), "intimacy_wording_version": VERSION,
                         "adult_and_consented": True}, visibility="research", revision=body.revision + 1,
                attempt_id=f"wording:{body.owner}:{body.revision + 1}", at=stamp,
                before={"rating": previous.get(key), "edition": previous.get("edition")} if key in previous else None,
                before_exists=key in previous, before_observed_at=previous.get("submitted_at")))
        answer_history.append(mutation, events, source)
    updated = await db.users.find_one_and_update(answer_history.guard(match, source), mutation,
        projection={"user_id": 1, "created_at": 1, "intimacy_wording_revision": 1, "intimacy_wording_review": 1},
        return_document=ReturnDocument.AFTER)
    if not updated:
        raise HTTPException(409, "This response or its eligibility changed. Check your saved feedback.")
    await answer_history.flush(db, "users", {"user_id": body.owner}, strict=deleting)
    return view(updated)


async def summary(db, eligible):
    instrument = await edition(db)
    match = questionnaire_report.adult_filter({**eligible, "intimacy_wording_review.edition": instrument,
                                               "intimacy_wording_review.consent_version": VERSION})
    pipeline = [{"$match": match}, {"$group": {"_id": {k: "$intimacy_wording_review." + k for k in FIELDS}, "n": {"$sum": 1}}}]
    counts = {field: Counter() for field in FIELDS}
    total = 0
    async for row in db.users.aggregate(pipeline):
        total += row["n"]
        for field in FIELDS:
            counts[field][row["_id"].get(field) or "skipped"] += row["n"]
    fields = {}
    for field, bins in counts.items():
        if total < assessment_evidence.MINIMUM or any(0 < n < assessment_evidence.MIN_CELL for n in bins.values()):
            fields[field] = None
        else:
            fields[field] = {k: assessment_evidence.rounded_count(bins[k]) for k in VALUES}
    return {"edition": instrument, "measures": fields}
