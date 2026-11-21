"""Version-matched, consented repeat observations and staff-only measurement summaries."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument

try:
    from . import answer_context, answer_history, questionnaire_report, quiz_answers, report_dates, research, research_trends, tone_research
except ImportError:
    import answer_context, answer_history, questionnaire_report, quiz_answers, report_dates, research, research_trends, tone_research

MINIMUM = research_trends.MIN_PAIRED
MIN_CELL = research_trends.MIN_CELL
STEP = research_trends.ROUND_TO
REPEAT_MIN = timedelta(days=7)
REPEAT_MAX = timedelta(days=28)


class RepeatRemoval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)
    expected_member: str = Field(max_length=256)


class RepeatSubmission(RepeatRemoval):
    instrument: str = Field(max_length=100)
    phase: Literal["first", "second"]
    consent_revision: int = Field(ge=0)
    publish_group_results: bool
    answers: dict[str, int] = Field(max_length=500)


def instrument(quiz):
    return research.fingerprint([{"id": q["id"], "text": q["q"], "axis": q.get("axis"), "dim": q.get("dim"),
        "options": [(o["label"], o.get("axis_score"), o.get("type_score")) for o in q["options"]]}
        for q in quiz.get("short", [])])


def rounded_count(n):
    return STEP * ((n + STEP // 2) // STEP)


def repeat_status(member, quiz, now):
    signature = instrument(quiz)
    previous = member.get("assessment_repeat") or {}
    revision = member.get("assessment_repeat_revision", 0)
    output = {"instrument": signature, "revision": revision, "phase": "first",
              "consent_revision": member.get("questionnaire_research_revision", 0),
              "has_record": bool(previous)}
    if not previous or previous["instrument"] != signature:
        return output
    first_at = datetime.fromisoformat(previous["first_at"])
    opens, closes = first_at + REPEAT_MIN, first_at + REPEAT_MAX
    output.update(opens=opens.isoformat(), closes=closes.isoformat())
    if "second" in previous:
        output["phase"] = "complete"
    elif now < opens:
        output["phase"] = "waiting"
    elif now <= closes:
        output["phase"] = "second"
    return output


def check_access(member):
    research.age_band(member)
    if member.get("role") != "member" or member.get("questionnaire_research_version") != questionnaire_report.CONSENT:
        raise HTTPException(403, "This optional repeat is available to adults participating in questionnaire research.")


def member_view(member, quiz, now):
    check_access(member)
    status = repeat_status(member, quiz, now)
    if status["phase"] in ("first", "second"):
        status["questions"] = [{"id": q["id"], "q": q["q"], "labels": [o["label"] for o in q["options"]]}
                               for q in quiz["short"]]
    return status


async def save_repeat(db, member, quiz, body, now):
    check_access(member)
    current = repeat_status(member, quiz, now)
    if (body.expected_member != member["user_id"] or body.consent_revision != current["consent_revision"]
            or body.instrument != current["instrument"]):
        raise HTTPException(409, "The form or timing changed. Reload the optional repeat.")
    questions = {q["id"]: q for q in quiz["short"]}
    if not questions or questions.keys() != body.answers.keys() or any(
        not 0 <= choice < len(questions[key]["options"]) for key, choice in body.answers.items()
    ):
        raise HTTPException(422, "Choose one current answer to every question before submitting.")
    if not body.publish_group_results:
        raise HTTPException(422, "Confirm the use of these optional answers for group results before submitting.")
    # Save selected labels for a legible member export even if the published form changes later.
    answers = {key: questions[key]["options"][choice]["label"] for key, choice in body.answers.items()}
    eligibility = {"user_id": member["user_id"], "role": "member", "closing": {"$ne": True},
        "questionnaire_research_version": questionnaire_report.CONSENT,
        "questionnaire_research_revision": body.consent_revision if body.consent_revision else {"$in": [0, None]}}
    projection = {"assessment_repeat": 1, "assessment_repeat_revision": 1, "questionnaire_research_revision": 1,
                  "role": 1, "questionnaire_research_version": 1}
    await answer_history.flush(db, "users", {"user_id": member["user_id"]})
    source = await db.users.find_one(questionnaire_report.adult_filter(eligibility))
    if (body.revision == current["revision"] and body.phase == current["phase"]
            and source is not None and source.get("assessment_repeat_revision", 0) == body.revision):
        context = await answer_context.for_member(db, member["user_id"])
        # Creating the demographic observation itself advances the source's history CAS.
        source = await db.users.find_one(questionnaire_report.adult_filter(eligibility))
        if source is None:
            raise HTTPException(409, "Your participation changed. Reopen the optional repeat.")
        stamp = now.isoformat()
        previous = source.get("assessment_repeat") or {}
        anchor = (previous.get("cohort_first_at") or previous.get("first_at")) if previous.get("instrument") == body.instrument else None
        if current["phase"] == "first":
            fields = {"assessment_repeat": {"instrument": body.instrument, "first": answers, "first_at": stamp,
                                            "cohort_first_at": anchor or stamp, "publish_group_results": True}}
        else:
            fields = {"assessment_repeat.second": answers, "assessment_repeat.second_at": stamp}
            if anchor == previous.get("first_at"):
                fields["assessment_repeat.cohort_second_at"] = stamp
        query = questionnaire_report.adult_filter({**eligibility,
            "assessment_repeat_revision": body.revision if body.revision else {"$in": [0, None]}})
        events = []
        for key, choice in body.answers.items():
            definition = {"id": key, "question": questions[key]["q"],
                          "options": [option["label"] for option in questions[key]["options"]],
                          "instrument": body.instrument}
            earlier = (previous.get(body.phase) or {}).get(key)
            events.append(answer_history.event(source="users", source_id=member["user_id"],
                owner_id=member["user_id"], domain="assessment_repeat", question_id="assessment-repeat:" + key,
                after={"choice": choice, "answer": answers[key], "phase": body.phase},
                question=definition, signature=research.fingerprint(definition), demographics=context,
                consent={**answer_history.consent(source), "publish_group_results": True}, visibility="research",
                revision=body.revision + 1, attempt_id=f"repeat:{member['user_id']}:{body.instrument}:{body.revision + 1}:{body.phase}",
                at=stamp, before={"answer": earlier, "instrument": previous.get("instrument")} if earlier is not None else None,
                before_exists=earlier is not None, before_observed_at=previous.get(body.phase + "_at")))
        mutation = answer_history.append({"$set": fields, "$inc": {"assessment_repeat_revision": 1}}, events, source)
        updated = await db.users.find_one_and_update(answer_history.guard(query, source), mutation,
            projection=projection, return_document=ReturnDocument.AFTER)
        if updated:
            await answer_history.flush(db, "users", {"user_id": member["user_id"]})
            return repeat_status(updated, quiz, now)
    # A lost response or concurrent identical request can replay the last committed set.
    # Re-read its consent and revision so withdrawal or a later write always wins.
    saved = await db.users.find_one(questionnaire_report.adult_filter({**eligibility,
        "assessment_repeat_revision": body.revision + 1, "assessment_repeat.instrument": body.instrument,
        "assessment_repeat.publish_group_results": True}), projection)
    if saved:
        study = saved["assessment_repeat"]
        last_phase = "second" if "second" in study else "first"
        if last_phase == body.phase and study[last_phase] == answers:
            return member_view(saved, quiz, now)
    raise HTTPException(409, "Your participation, form or timing changed. Reload the optional repeat.")


async def remove_repeat(db, member, body):
    if member["user_id"] != body.expected_member:
        raise HTTPException(409, "Your account changed. Reload before removing these answers.")
    uid = member["user_id"]
    for _ in range(5):
        source = await db.users.find_one({"user_id": uid})
        if source is None or "assessment_repeat" not in source:
            await answer_history.flush(db, "users", {"user_id": uid}, strict=True)
            return {"ok": True}
        if source.get("assessment_repeat_revision", 0) != body.revision:
            raise HTTPException(409, "The repeat entry changed. Reload before removing it.")
        mutation = answer_history.redact({"$unset": {"assessment_repeat": ""},
            "$inc": {"assessment_repeat_revision": 1}}, source, question_prefix="assessment-repeat:",
            source="users", source_id=uid)
        saved = await db.users.update_one(answer_history.guard({"user_id": uid,
            "assessment_repeat_revision": body.revision, "assessment_repeat": {"$exists": True}}, source), mutation)
        if saved.matched_count:
            await answer_history.flush(db, "users", {"user_id": uid}, strict=True)
            return {"ok": True}
    raise HTTPException(409, "The repeat entry changed. Reload before removing it.")


class ScaleMoments:
    """Maintain sums for the unstandardized coefficient alpha, without retaining member observations."""
    def __init__(self, questions):
        self.questions = questions
        self.n = 0
        self.sums = [0] * (len(questions) + 1)
        self.squares = self.sums.copy()

    def observe(self, scores):
        self.n += 1
        for i, x in enumerate([*scores, sum(scores)]):
            self.sums[i] += x
            self.squares[i] += x * x

    def summary(self):
        k = len(self.questions)
        result = {"items": k, "sample": None, "alpha": None}
        if self.n >= MINIMUM and k >= 2:
            result["sample"] = rounded_count(self.n)
            variance = [self.n * s2 - s1 * s1 for s1, s2 in zip(self.sums, self.squares)]
            if variance[-1] > 0:
                result["alpha"] = round(k / (k - 1) * (1 - sum(variance[:-1]) / variance[-1]), 2)
        return result


def repeat_item_summary(matrix):
    n = sum(matrix.values())
    hidden = {"sample": None, "same_percent": None, "kappa": None}
    if n < MINIMUM or any(0 < v < MIN_CELL for v in matrix.values()):
        return hidden
    sample = rounded_count(n)
    rounded = {key: rounded_count(value) for key, value in matrix.items()}
    n = sum(rounded.values())
    first, second = Counter(), Counter()
    agree = 0
    for (a, b), count in rounded.items():
        first[a] += count
        second[b] += count
        if a == b:
            agree += count
    chance = sum(count * second[key] for key, count in first.items()) / (n * n)
    return {"sample": sample, "same_percent": round(100 * agree / n),
            "kappa": round((agree / n - chance) / (1 - chance), 2) if chance < 1 else None}


async def enrollment_summary(db, eligible, quiz, now):
    """First attempt per consenting adult on this edition, including volunteers who never returned."""
    criteria = questionnaire_report.ongoing_members(eligible)
    total = await db.users.count_documents(criteria)
    fields = {f"assessment_repeat.{k}": 1 for k in ("first_at","second_at","cohort_first_at","cohort_second_at")}
    groups = dict(waiting=0, open=0, completed_early=0, completed_mature=0, expired=0)
    criteria = {"$and": [criteria, questionnaire_report.exact_fields(**{
        "assessment_repeat.instrument": instrument(quiz), "assessment_repeat.publish_group_results": True})]}
    async for member in db.users.find(criteria, {"_id":0, **fields}):
        record = member["assessment_repeat"]
        first = report_dates.instant(record.get("first_at"))
        start = report_dates.instant(record.get("cohort_first_at", record.get("first_at")))
        if start is None or start > now:
            continue
        second_at = record.get("cohort_second_at") or (record.get("second_at") if start == first else None)
        second = report_dates.instant(second_at)
        completed = second is not None and start+REPEAT_MIN <= second <= min(now,start+REPEAT_MAX)
        if now > start+REPEAT_MAX:
            groups["completed_mature" if completed else "expired"] += 1
        elif completed:
            groups["completed_early"] += 1
        else:
            groups["waiting" if now < start+REPEAT_MIN else "open"] += 1
    n = sum(groups.values())
    if n < MINIMUM or any(0 < value < MIN_CELL for value in (*groups.values(), total-n)):
        return {"available":False}
    values = {k:rounded_count(v) for k,v in groups.items()}
    mature = values["completed_mature"]+values["expired"]
    return {"available":True,"consenting":rounded_count(total),"first":rounded_count(n),"groups":values,
            "mature":rounded_count(groups["completed_mature"]+groups["expired"]),
            "completion_percent":round(100*values["completed_mature"]/mature) if mature else None}


async def questionnaire_summary(db, eligible, quiz, now=None):
    questions = quiz_answers.questions_by_id(quiz).values()
    axes = {axis: ScaleMoments([q for q in questions if q.get("axis") == axis and q.get("options") and
        all(type(o.get("axis_score")) is int for o in q["options"])]) for axis in quiz["axes"]}
    signature = instrument(quiz)
    projection = {"_id": 0, "questionnaire_research_version": 1}
    for scale in axes.values():
        for q in scale.questions:
            for field in ("choice", "question", "label"):
                projection[f"about.answers.{q['id']}.{field}"] = 1
    consenting = questionnaire_report.ongoing_members(eligible)
    async for member in db.users.find(consenting, projection):
        about = member.get("about") or {}
        for scale in axes.values():
            indices = [quiz_answers.resolve_answer(about, quiz, q) for q in scale.questions]
            if len(indices) >= 2 and all(i is not None for i in indices):
                scale.observe([q["options"][i]["axis_score"] for q, i in zip(scale.questions, indices)])
    scale_version = research.fingerprint({axis: [(q["id"], q["q"], [(o["label"], o["axis_score"]) for o in q["options"]])
                                                 for q in scale.questions] for axis, scale in axes.items()})
    now = now or datetime.now(timezone.utc)
    return {"instrument": signature, "scale_version": scale_version,
            "scales": [{"name": name, **value.summary()} for name, value in axes.items()],
            "repeat": await repeated_items(db, eligible, quiz, now),
            "enrollment": await enrollment_summary(db, eligible, quiz, now)}


async def repeated_items(db, eligible, quiz, now=None):
    now = now or datetime.now(timezone.utc)
    matrices = {q["id"]: Counter() for q in quiz["short"]}
    signature = instrument(quiz)
    consenting = {"$and": [questionnaire_report.ongoing_members(eligible), questionnaire_report.exact_fields(**{
        "assessment_repeat.instrument": signature, "assessment_repeat.publish_group_results": True})]}
    consenting["assessment_repeat.second_at"] = {"$exists": True}
    async for row in db.users.find(consenting, {"_id": 0, "assessment_repeat": 1}):
        pair = row["assessment_repeat"]
        first, second = (report_dates.instant(pair.get(key)) for key in ("first_at", "second_at"))
        if not first or not second or second > now or not all(isinstance(pair.get(k), dict) for k in ("first", "second")):
            continue
        if REPEAT_MIN <= second - first <= REPEAT_MAX:
            for q in quiz["short"]:
                key = q["id"]
                options = {o["label"] for o in q["options"]}
                a, b = pair["first"].get(key), pair["second"].get(key)
                if isinstance(a, str) and isinstance(b, str) and a in options and b in options:
                    matrices[key][a, b] += 1
    return [{"number": i, "axis": q.get("axis"), **repeat_item_summary(matrices[q["id"]])}
            for i, q in enumerate(quiz["short"], start=1)]


def recent_reads(now):
    return {"$gte": tone_research.epoch_micros(now - timedelta(days=90)), "$lte": tone_research.epoch_micros(now)}


async def pair_feedback_coverage(db, env, eligible, now):
    collection, pipeline, consenting = tone_research.population_source(db, env, eligible, "couple")
    # Latest eligible shared conversation per pair. Two scores must refer to the very same shared wording revision.
    pipeline += [{"$match": {"at": recent_reads(now)}},
                 *tone_research.lookup_member(consenting, "actor", "author"),
                 {"$sort": {"at": -1, "id": -1}},
                 {"$group": {"_id": {"pair": "$unit", "thread": "$thread", "revision": "$revision", "actor": "$actor"},
                             "at": {"$first": "$at"}, "rated": {"$first": {"$cond": [
                                 {"$and": [{"$eq": ["$status", "estimated"]}, {"$in": ["$fit", ["fits", "corrected"]]}]}, 1, 0]}}}},
                 {"$group": {"_id": {"pair": "$_id.pair", "thread": "$_id.thread", "revision": "$_id.revision"},
                             "at": {"$max": "$at"}, "rated": {"$sum": "$rated"}}},
                 {"$sort": {"at": -1, "_id.thread": 1, "_id.revision": -1}},
                 {"$group": {"_id": "$_id.pair", "rated": {"$first": "$rated"}}},
                 {"$group": {"_id": "$rated", "n": {"$sum": 1}}}]
    counts = [0, 0, 0]
    async for row in collection.aggregate(pipeline, allowDiskUse=True):
        counts[row["_id"]] = row["n"]
    if sum(counts) < MINIMUM or any(0 < n < MIN_CELL for n in counts):
        return {"available": False}
    return {"available": True, "sample": rounded_count(sum(counts)),
            "groups": {key: rounded_count(n) for key, n in zip(("neither", "one", "both"), counts)}}


async def fit_summary(db, env, eligible, unit, now):
    collection, pipeline, consenting = tone_research.population_source(db, env, eligible, unit)
    pipeline += [{"$match": {"at": recent_reads(now)}},
                 *tone_research.lookup_member(consenting,"actor","author"),
                 {"$sort":{"at":-1, "id":-1, "actor":1}},
                 {"$group":{"_id":"$unit","fit":{"$first":"$fit"},"status":{"$first":"$status"}}},
                 {"$group":{"_id":{"fit":"$fit","status":"$status"},"n":{"$sum":1}}}]
    counts = dict(insufficient=0, fits=0, corrected=0, unanswered=0)
    async for row in collection.aggregate(pipeline, allowDiskUse=True):
        result = row["_id"]
        key = "insufficient" if result["status"]=="insufficient" else result.get("fit")
        counts[key if key in counts else "unanswered"] += row["n"]
    if sum(counts.values()) < MINIMUM or any(0 < n < MIN_CELL for n in counts.values()):
        return {"available":False}
    groups = {k:rounded_count(n) for k,n in counts.items()}
    answered = groups["fits"]+groups["corrected"]
    return {"available":True,"sample":rounded_count(sum(counts.values())),"groups":groups,
            "corrected_percent":round(100*groups["corrected"]/answered) if answered else None}


async def outcome_summary(db, env, eligible, unit, now):
    collection, pipeline, consenting = tone_research.population_source(db, env, eligible, unit)
    pipeline += [{"$match": {"can_reply": True, "at": recent_reads(now)}},
                 *tone_research.lookup_member(consenting, "actor", "author"),
                 {"$sort": {"at": -1, "id": -1, "actor": 1}},
                 {"$group": {"_id": "$unit", "sent": {"$first": "$sent"}, "helped": {"$first": "$helped"}}},
                 {"$group": {"_id": {"sent": "$sent", "helped": "$helped"}, "n": {"$sum": 1}}}]
    counts = {name: 0 for name in ("send_unknown", "not_sent", "awaiting_outcome", "helped", "did_not_help")}
    async for row in collection.aggregate(pipeline, allowDiskUse=True):
        sent, helped = row["_id"].get("sent"), row["_id"].get("helped")
        key = "send_unknown"
        if sent is False:
            key = "not_sent"
        elif sent is True:
            key = "awaiting_outcome" if type(helped) is not bool else ("helped" if helped else "did_not_help")
        counts[key] += row["n"]
    if sum(counts.values()) < MINIMUM or any(0 < n < MIN_CELL for n in counts.values()):
        return {"available": False}
    groups = {key: rounded_count(n) for key, n in counts.items()}
    rounded_outcomes = groups["helped"] + groups["did_not_help"]
    return {"available": True, "groups": groups, "sample": rounded_count(sum(counts.values())),
            "answered": rounded_count(counts["helped"] + counts["did_not_help"]),
            "helped_percent": round(100 * groups["helped"] / rounded_outcomes) if rounded_outcomes else None}
