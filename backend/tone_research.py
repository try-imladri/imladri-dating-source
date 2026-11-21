"""Prospective, consented tone comparisons from current source records."""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

try:
    from . import couple_research, personal_demographics, questionnaire_report, research, research_trends
    from .report_dates import instant_micros
    from .conversation_tone import INCOMING, STYLES
except ImportError:
    import couple_research, personal_demographics, questionnaire_report, research, research_trends
    from report_dates import instant_micros
    from conversation_tone import INCOMING, STYLES


VARIABLES = {
    "incoming": ("Perceived incoming tone", list(INCOMING)),
    "style": ("Last reply style selected", list(STYLES)),
    "sent_style": ("Reply style reported sent", list(STYLES)),
    "fit": ("Member’s assessment of the read", ["fits", "corrected"]),
    "sent": ("Member reported sending", ["Yes", "No"]),
    "helped": ("Sent reply helped, as reported", ["Yes", "No"]),
    "age_band": ("Respondent’s age range", list(research.AGE_BANDS)),
    "height_band": ("Respondent’s height range", questionnaire_report.HEIGHT_BANDS),
    **{"d:" + key: ("Respondent’s " + key, [v for v in values if v != "Prefer not to say"])
       for key, values in personal_demographics.OWN_OPTIONS.items()},
}
DEMOGRAPHICS = {"age_band", "height_band", *("d:" + key for key in personal_demographics.OWN_OPTIONS)}
PATHS = {"id": "id", "actor": "user_id", "at": "created_at", "status": "tone.status",
         "incoming": "tone.incoming", **{k: "tone_feedback." + k for k in ("style", "sent_style", "fit", "sent", "helped")}}


def source_projection(prefix):
    # Project the explicitly approved codes even when a source also has a correction note or generated drafts.
    return {**{name: prefix + path for name, path in PATHS.items()},
            "at": instant_micros(prefix + "created_at"),
            "can_reply": {"$eq": [{"$type": prefix + "tone.drafts.neutral"}, "string"]}}


def epoch_micros(value):
    return (value - datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(microseconds=1)


def lookup_member(eligible, source, alias, demographic_axes=()):
    fields = {"_id": 0, "tone_research_at": 1}
    if demographic_axes:
        eligible = {"$and": [eligible, questionnaire_report.exact_fields(questionnaire_research_version=questionnaire_report.CONSENT)]}
    for key in demographic_axes:
        field = {"age_band": "age", "height_band": "height_cm"}.get(key, key[2:])
        fields["about.demographics." + field] = 1
    return [
        {"$lookup": {"from": "users", "localField": source, "foreignField": "user_id", "as": alias,
                     "pipeline": [{"$match": eligible}, {"$project": fields}]}},
        {"$unwind": "$" + alias},
        {"$match": {"$expr": {"$let": {"vars": {"grant": instant_micros("$" + alias + ".tone_research_at")}, "in":
            {"$and": [{"$ne": ["$$grant", None]}, {"$gte": ["$at", "$$grant"]}]}}}}},
    ]


def axis(key):
    if key in DEMOGRAPHICS:
        variable = {"choices": VARIABLES[key][1], "domain": "demographics"}
        return questionnaire_report.value_for(key, None, variable, None, "$author", None, None)
    if key in ("helped", "sent"):
        valid = {"$eq": [{"$type": "$" + key}, "bool"]}
        if key == "helped":
            valid = {"$and": [valid, {"$eq": ["$sent", True]}]}
        return {"$cond": [valid, {"$cond": ["$" + key, 0, 1]}, None]}
    index = {"$indexOfArray": [VARIABLES[key][1], "$" + key]}
    if key in ("incoming", "fit"):
        return {"$cond": [{"$eq": ["$status", "estimated"]}, index, None]}
    if key == "sent_style":
        return {"$cond": [{"$eq": ["$sent", True]}, index, None]}
    return index


def pairs_source(env):
    # Extract metadata for at most two seats per live conversation. Private draft coaching stays outside pair research.
    threads = couple_research.object_entries("$journey.threads")
    mutual = {"$filter": {"input": threads, "as": "t", "cond": {"$and": [
        {"$gt": [{"$size": {"$cond": [{"$isArray": f"$$t.v.shares.{side}"}, f"$$t.v.shares.{side}", []]}}, 0]}
        for side in ("a", "b")]}}}
    return [
        {"$match": {"env": env, "state": "active", "tone_sessions": {"$type": "object"}, "$expr": {"$and": [
            {"$ne": ["$inviter", "$invitee"]}, {"$setEquals": [["$inviter", "$invitee"], {"$cond": [{"$isArray": "$members"}, "$members", []]}]}]}}},
        {"$project": {"inviter": 1, "invitee": 1, "revisions": {"$map": {"input": mutual, "as": "t",
            "in": {"key": "$$t.k", "version": {"$ifNull": ["$$t.v.words_version", "$$t.v.version"]}}}}, "seats": {"$map": {
            "input": couple_research.object_entries("$tone_sessions"), "as": "thread",
            "in": [{**source_projection(f"$$thread.v.{side}."), "thread": "$$thread.k",
                    "owner": owner, **{key: f"$$thread.v.{side}.{path}" for key, path in
                    (("mode","mode"), ("env","env"), ("pair","pair_id"), ("revision","source_version"))}}
                   for side, owner in (("a", "$inviter"), ("b", "$invitee"))],
        }}}},
        {"$unwind": "$seats"}, {"$unwind": "$seats"},
        {"$set": {"current": {"$arrayElemAt": [{"$filter": {"input": "$revisions", "as": "t", "cond":
                    {"$eq": ["$$t.key", "$seats.thread"]}}}, 0]}}},
        {"$match": {"seats.status": {"$in": ["insufficient", "estimated"]}, "seats.mode": "shared", "seats.env": env,
            "$expr": {"$and": [{"$eq": ["$seats.actor", "$seats.owner"]}, {"$eq": ["$seats.pair", "$_id"]},
                {"$in": [{"$type": "$seats.revision"}, ["int","long"]]}, {"$in": [{"$type": "$current.version"}, ["int","long"]]},
                {"$gt": ["$current.version", 0]}, {"$eq": ["$seats.revision", "$current.version"]}]}}},
        {"$project": {"unit": "$_id", "other": {"$cond": [{"$eq": ["$seats.actor", "$inviter"]}, "$invitee", "$inviter"]},
                      **{key: "$seats." + key for key in (*PATHS, "can_reply", "thread", "revision")}}},
    ]


def population_source(db, env, eligible, unit):
    consenting = questionnaire_report.adult_filter({"$and": [
        eligible, questionnaire_report.exact_fields(tone_research_version=personal_demographics.TONE_CONSENT)]})
    if unit == "couple":
        consenting = {"$and": [consenting, questionnaire_report.couples_adult_attestation()]}
        return db.couples_pairs, pairs_source(env) + lookup_member(consenting, "other", "partner"), consenting
    return db.exchanges, [
        {"$match": {"env": {"$in": [env, None]}, "tone.status": {"$in": ["insufficient", "estimated"]}}},
        {"$project": {"unit": "$user_id", **source_projection("$")}},
    ], consenting


async def report(db, env, eligible, unit, x, y, incoming=None):
    if (unit not in ("member", "couple") or x not in VARIABLES or y not in VARIABLES or x == y
            or (incoming is not None and incoming not in INCOMING) or (x in DEMOGRAPHICS and y in DEMOGRAPHICS)):
        raise HTTPException(422, "Choose two different variables with at least one tone or feedback measure.")
    collection, pipeline, consenting = population_source(db, env, eligible, unit)
    observed_at = datetime.now(timezone.utc)
    pipeline.append({"$match": {"at": {"$ne": None, "$lte": epoch_micros(observed_at)}}})
    pipeline += lookup_member(consenting, "actor", "author", [k for k in (x, y) if k in DEMOGRAPHICS])
    if incoming is not None:
        pipeline.append({"$match": {"status": "estimated", "incoming": incoming}})
    pipeline += [
        {"$project": {"unit": 1, "at": 1, "id": 1, "actor": 1, "x": axis(x), "y": axis(y)}},
        {"$match": {"x": {"$gte": 0}, "y": {"$gte": 0}}},
        {"$sort": {"at": -1, "id": -1, "actor": 1}},
        {"$group": {"_id": "$unit", "cell": {"$first": {"x": "$x", "y": "$y"}}}},
        {"$group": {"_id": "$cell", "count": {"$sum": 1}}},
    ]
    counts = [[0 for _ in VARIABLES[y][1]] for _ in VARIABLES[x][1]]
    async for row in collection.aggregate(pipeline, allowDiskUse=True):
        counts[row["_id"]["x"]][row["_id"]["y"]] = row["count"]
    instrument = research.fingerprint([personal_demographics.TONE_CONSENT, (x, VARIABLES[x]), (y, VARIABLES[y])])
    return {"unit": unit, "x": x, "y": y, "incoming": incoming,
            "observed_at": observed_at.isoformat(), "instrument": instrument,
            "scope_label": "Individual conversations" if unit == "member" else "Shared Couples conversations",
            "variables": [{"id": key, "label": label} for key, (label, _) in VARIABLES.items()],
            "x_choices": VARIABLES[x][1], "y_choices": VARIABLES[y][1], "tones": list(INCOMING),
            "minimum": research_trends.MIN_PAIRED, "minimum_cell": research_trends.MIN_CELL, "round_to": research_trends.ROUND_TO,
            **research_trends.summarize(counts)}
