"""Staff registrations for comparing an existing association in a future, disjoint cohort."""
from datetime import datetime

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

try:
    from . import questionnaire_report, research, research_trends
except ImportError:
    import questionnaire_report, research, research_trends


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    unit: Literal["member", "couple", "couple_inviter", "couple_invitee"] = "member"
    x: str = Field(min_length=1, max_length=256)
    y: str = Field(min_length=1, max_length=256)
    x_condition: str | None = Field(default=None, max_length=40)
    y_condition: str | None = Field(default=None, max_length=40)
    expected_instrument: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def axes_for(selection):
    return {axis: (selection[axis], selection.get(axis+"_condition")) for axis in ("x", "y")}


def design(state, selection):
    axes = axes_for(selection)
    if not research_trends.comparable(axes["x"], axes["y"], selection["unit"]):
        raise HTTPException(422, "Choose two distinct measures.")
    return research_trends.comparison_fields(state[2], axes)


def account_cohort(eligible, cutoff, after=False):
    # Old accounts may use BSON dates or ISO strings with a non-UTC offset.
    at = {"$convert": {"input": "$created_at", "to": "date", "onNull": None, "onError": None}}
    return {**eligible, "$expr": {"$let": {"vars": {"at": at}, "in": {"$and": [
        {"$in": [{"$type": "$created_at"}, ["string", "date"]]}, {"$ne": ["$$at", None]},
        {"$gt" if after else "$lte": ["$$at", datetime.fromisoformat(cutoff)]},
    ]}}}}


def brief(row):
    return {"id": row["_id"], **{k:row[k] for k in ("selection","labels","instrument","registered_at")}}


def response_patterns(baseline, future, fields):
    """Align the two released tables by option index, even when a group lacks a category."""
    def by_row(group):
        return {row["index"]: dict(zip(group["columns"], row["cells"])) for row in group["rows"]}

    before, after = by_row(baseline), by_row(future)
    columns = sorted(set(baseline["columns"]) | set(future["columns"]))
    result = []
    for i in sorted(before.keys() | after.keys()):
        for j in columns:
            entry = {"row": fields["x"]["choices"][i], "column": fields["y"]["choices"][j]}
            for field, rows in (("baseline", before), ("future", after)):
                entry[field] = {"count": rows[i].get(j, 0), "total": sum(rows[i].values())} if i in rows else None
            result.append(entry)
    return result


async def register(db, env, eligible, quiz, body, stamp):
    selected = body.model_dump()
    expected = selected.pop("expected_instrument")
    state = await research_trends.catalog(db, quiz)
    fields = design(state, selected)
    form = research.fingerprint(fields)
    if expected is not None and form != expected:
        raise HTTPException(409, "These measures changed. Refresh the comparison before registering it.")
    key = research.fingerprint([env, selected, form])
    existing = await db.research_registrations.find_one({"_id":key})
    if existing:
        return brief(existing)
    # Freeze the baseline to accounts created by this instant. Some older account rows have no creation date.
    baseline_filter = account_cohort(eligible, stamp)
    group = (await research_trends.comparisons(db, baseline_filter, quiz, state, axes_for(selected), selected["unit"]))["y"]
    if not group["available"]:
        raise HTTPException(422,"The baseline needs a reportable group before registering a future comparison.")
    row = {"_id":key,"env":env,"selection":selected,"instrument":form,"registered_at":stamp,
           "labels":{axis:value["label"] for axis,value in fields.items()}}
    await db.research_registrations.update_one({"_id":key},{"$setOnInsert":row},upsert=True)
    return brief(await db.research_registrations.find_one({"_id":key}))


async def follow_up(db, env, eligible, quiz, key):
    record = await db.research_registrations.find_one({"_id":key,"env":env})
    if not record:
        raise HTTPException(404,"The registered comparison could not be found.")
    selection = record["selection"]
    state = await research_trends.catalog(db, quiz)
    try:
        fields = design(state, selection)
        form = research.fingerprint(fields)
    except HTTPException:
        form = None
    if form != record["instrument"]:
        return {"state":"changed"}
    baseline_filter = account_cohort(eligible, record["registered_at"])
    baseline = (await research_trends.comparisons(db, baseline_filter, quiz, state, axes_for(selection), selection["unit"]))["y"]
    previous = {k:baseline[k] for k in ("sample_size","cramers_v")} if baseline["available"] else None
    future = account_cohort(eligible, record["registered_at"], after=True)
    group = (await research_trends.comparisons(db, future, quiz, state, axes_for(selection), selection["unit"]))["y"]
    if not group["available"]:
        return {"state":"collecting","baseline":previous}
    result = {"state":"available","sample_size":group["sample_size"],"cramers_v":group["cramers_v"],"baseline":previous}
    if previous:
        result["patterns"] = response_patterns(baseline, group, fields)
    return result


async def coverage(db, eligible):
    adults = questionnaire_report.adult_filter(eligible)
    possible = await db.users.count_documents(adults)
    count = await db.users.count_documents(questionnaire_report.ongoing_members(eligible))
    if possible < research_trends.MIN_PAIRED or any(0 < n < research_trends.MIN_CELL for n in (count,possible-count)):
        return {"available":False}
    step = research_trends.ROUND_TO
    rounded = lambda n: step * ((n + step // 2) // step)
    return {"available":True,"eligible":rounded(possible),"participating":rounded(count)}
