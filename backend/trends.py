"""Aggregate member activity and the narrowly scoped, opt-in everyday report."""
import asyncio
import re
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

try:
    from . import report_dates
    from .conversation_tone import STYLES
    from .free_plan import paid_cycle
    from .membership_plans import TOPUP
    from .quiz import SHORT
    from .quiz_answers import questions_by_id, resolve_answer, wording
except ImportError:
    import report_dates
    from conversation_tone import STYLES
    from free_plan import paid_cycle
    from membership_plans import TOPUP
    from quiz import SHORT
    from quiz_answers import questions_by_id, resolve_answer, wording


MINIMUM_RESPONDENTS = 10
ENTRY_PAGES = {"direct", "home", "types", "figures", "plans", "how-it-works", "about", "billing", "support", "terms", "privacy",
               "navigation", "demo", "waitlist", "sign-up", "sign-in", "couples/invite"}


def entry_page(value, catalog_pages):
    if not value:
        return "direct"
    if not isinstance(value, str):
        return "other"
    route = value.partition("?")[0].partition("#")[0]
    if route == "/":
        return "home"
    page = route.strip("/")
    if page in ENTRY_PAGES or page in catalog_pages:
        return page
    section = page.split("/", 1)[0]
    return section if section in ("figures", "types") else "other"


async def public_entry_pages(db):
    types, figures = await asyncio.gather(db.archetypes.distinct("slug"), db.figures.distinct("slug"))
    return {f"{section}/{slug}" for section, slugs in (("types", types), ("figures", figures))
            for slug in slugs if isinstance(slug, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", slug)}


async def capture_entry_page(db, value):
    # Ordinary signup clicks have a static label. Only detail-page origins require a catalog lookup.
    label = entry_page(value, ())
    if label in ("types", "figures") and isinstance(value, str) and "/" in value.strip("/"):
        return entry_page(value, await public_entry_pages(db))
    return label


EVERYDAY = tuple(row for row in SHORT if row["id"] in {"s1", "s9", "s12"})
RESULT_LABEL = {"better": "It went better", "same": "About the same", "worse": "It went worse", "no_reply": "No reply yet"}
EVENT_METRICS = {"memory_edit": "memory_edits", "session": "sessions", "upload_started": "uploads_started",
                 "refill_refunded": "refill_refund_events", "refill_dispute_lost": "refill_lost_disputes",
                 "upload_completed": "uploads_completed", "couples_opened": "couples_opened",
                 "couples_sample_completed": "couples_sample_completed", "deepen": "profile_updates"}
COUPLES_EVENTS = ("couples_opened", "couples_sample_completed")
QUIZ_COMPLETIONS = {"quiz_short": "basics_completed", "quiz_extended_complete": "deeper_completed",
                    "quiz_further_complete": "further_completed"}
SAVED_RECORDS = (
    ("quick_advice", "quick_advice", "created_at", {"deleted": {"$ne": True}, "workspace": "individual"}),
    ("couples_quick_advice", "quick_advice", "created_at", {"deleted": {"$ne": True}, "workspace": "couples"}),
    ("quick_advice_outcomes", "quick_advice", "outcome_at", {"deleted": {"$ne": True}, "outcome": {"$nin": ["", None]}}),
    ("your_type_attempts", "your_type_sessions", "created_at", {"deleted": {"$ne": True}}),
    ("reads", "exchanges", "created_at", {}),
    ("outcomes", "exchanges", "outcome_at", {"outcome": {"$nin": ["", None]}}),
    ("reply_choices", "exchanges", "choice.at", {}),
    ("corrections", "corrections", "created_at", {}),
    ("people_added", "people", "created_at", {}),
    ("person_reports", "people", "report.updated_at", {}),
    ("approved_memories", "memories", "created_at", {"exchange_id": {"$type": "string"},
                                                    "kind": {"$in": ["event", "interpretation", "assessment"]}}),
    ("patterns_saved", "patterns", "created_at", {"$or": [{"sources.0": {"$exists": True}}, {"edited": True},
                                                         {"member_confirmed": True}]}),
    ("practice", "practice", "created_at", {}),
    ("practice_finished", "practice", "created_at", {"ended": True}),
    ("saved_groups", "chat_groups", "created_at", {}),
    ("group_chats", "group_chats", "created_at", {}),
    ("game_plans", "preps", "created_at", {}),
    ("plan_followups", "preps", "outcome_at", {"outcome": {"$nin": ["", None]}}),
)
# In display order. The API, chart selector, and CSV share the same labels and counting rules.
ACTIVITY = {
    "quick_advice": ("Individual Quick Advice", "Saved situation advice without a People profile. Deleted conversations are excluded."),
    "couples_quick_advice": ("Couples Quick Advice", "Private situation advice requested inside Couples; only counts are reported."),
    "quick_advice_outcomes": ("Quick Advice follow-ups", "Advice with a saved member outcome, counted on the latest follow-up date."),
    "your_type_attempts": ("Your Type attempts", "Saved visual preference attempts, including immediate retakes. Each attempt counts once; choices and portraits remain private."),
    "signups": ("Signups", "New member accounts."),
    "reads": ("Individual reads", "Saved reads, counted on the day each was created."),
    "outcomes": ("Read follow-ups", "Reads with a saved follow-up, counted on the date of their latest outcome."),
    "practice": ("Practice sessions", "Saved individual practice sessions, counted on the day they started."),
    "couples_opened": ("Couples visits", "One visit per member per UTC day."),
    "couples_sample_completed": ("Couples samples finished", "Members who finished a sample, counted once per UTC day."),
    "people_added": ("People profiles added", "Saved People profiles, counted on their creation date."),
    "profile_updates": ("Profile intake updates", "Substantial intake edits recorded by the app. Updating several fields together counts once."),
    "person_reports": ("Profile reports updated", "One per saved profile report, on the date of its most recent update."),
    "reply_choices": ("Reply choices saved", "One per read with a saved reply choice, on the date of its latest selection."),
    "corrections": ("Read corrections saved", "Current member corrections to generated advice, on the date each correction was saved."),
    "approved_memories": ("Conversation memories saved", "Suggested conversation memories the member accepted and still has saved."),
    "patterns_saved": ("Personal observations saved", "Observations currently available in Your patterns, on their original creation date."),
    "practice_finished": ("Practice sessions now finished", "Currently finished sessions, grouped by start date. Restarting removes a session from this count until it is finished again."),
    "saved_groups": ("Named groups saved", "Saved groups of People profiles, counted on the date each group was created."),
    "group_chats": ("Group chat exchanges", "Saved exchanges from a member’s individual group chat."),
    "game_plans": ("Game plans", "Saved game plans, counted on their creation date."),
    "plan_followups": ("Game plan follow-ups", "Game plans with a saved follow-up, counted on the date of their latest outcome."),
    "basics_completed": ("The basics completed", "Members reaching their first recorded completion of The basics, by UTC day."),
    "deeper_completed": ("Go deeper completed", "Members reaching their first recorded completion of Go deeper, by UTC day."),
    "further_completed": ("Values & communication completed", "Members reaching their first recorded completion of Values & communication, by UTC day."),
    "checkouts_started": ("Subscription checkouts opened", "Initial subscription checkout sessions created during the period."),
    "paid_purchases": ("New subscriptions paid", "Initial subscription purchases by fulfillment date, or creation date when unavailable."),
    "paid_renewals": ("Paid renewals", "Paid renewal transactions by their recorded date."),
    "paid_upgrades": ("Subscription upgrades paid", "One verified subscription upgrade invoice on its payment date."),
    "refill_checkouts": ("Refill checkouts opened", "One-time message refill checkout sessions created during the period."),
    "paid_refills": ("Message refills paid", "One per paid refill on its first fulfillment date, including purchases subsequently refunded."),
    "refill_refund_events": ("Refill refund events", "Newly recorded cumulative refill refunds, counted once on the UTC date of the Stripe event. Tracking starts with this release."),
    "refill_lost_disputes": ("Lost refill disputes", "Refill payments with a verified lost dispute, counted once on the UTC date of the Stripe event. Tracking starts with this release."),
    "adjusted_refills": ("Paid refills with a credit reversal", "Paid refills whose credit grant is currently reduced after a refund or lost dispute. Counted once on the original fulfillment day, regardless of when or how many adjustments occurred."),
    "memory_edits": ("Memory edits", "Recorded changes to saved memories, including deletions."),
    "sessions": ("Sessions", "Recorded member visits. A new tab or returning on a different UTC day starts a visit."),
    "uploads_started": ("Uploads started", "Eligible conversation wizards opened and first-read conversation imports started. First-read imports are recorded from this release."),
    "uploads_completed": ("Uploads completed", "Conversation imports that produced a saved read. First-read imports are recorded from this release."),
}


async def onboarding_progress(db, users):
    milestones = {r["_id"]: r async for r in db.events.aggregate([
        {"$match": {"user_id": {"$in": [u["user_id"] for u in users]}, "type": "onboarding_step", "data.step": {"$in": [1, 2, 3, 4, 5]}}},
        {"$group": {"_id": "$user_id", "highest": {"$max": "$data.step"},
                    "before_completion": {"$max": {"$cond": [{"$lt": ["$data.step", 5]}, "$data.step", 0]}}}},
    ])}
    counts = dict.fromkeys(map(str, range(1, 6)), 0)
    skipped = 0
    for member in users:
        row = milestones.get(member["user_id"], {})
        progress = member.get("onboarding_step") or 1
        # Older clients recorded a completion event for Skip but left the guide step unchanged.
        legacy_skip = member.get("onboarded") and progress < 5 and row.get("highest") == 5
        skipped += bool(member.get("onboarding_skipped") or legacy_skip)
        recorded = row.get("before_completion" if legacy_skip else "highest", 0)
        highest = max(progress if progress > 1 else 0, recorded)
        for step in range(1, min(5, highest) + 1):
            counts[str(step)] += 1
    return counts, skipped


class CouplesActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["opened", "sample_completed"]


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool
    expected_member: str | None = Field(default=None, max_length=200)


async def record_couples_activity(db, uid, action, timestamp):
    # Every client opening the same workspace on this day hits the same record.
    day = timestamp[:10]
    kind = f"couples_{action}"
    await db.events.update_one({"_id": f"{kind}:{day}:{uid}"}, {"$setOnInsert": {
        "type": kind, "user_id": uid, "ts": timestamp,
    }}, upsert=True)


def disclosure(labels, counts):
    total = sum(counts)
    available = total >= MINIMUM_RESPONDENTS and all(n == 0 or n >= MINIMUM_RESPONDENTS for n in counts)
    return {"available": available, "respondents": total if available else None,
            "options": [{"label": label, "count": n} for label, n in zip(labels, counts)] if available else []}


async def everyday_report(db, eligible, quiz):
    # Consent covers only these published choices. An editor cannot expand the
    # scope by replacing one with a different question under the same ID.
    published = questions_by_id(quiz)
    current = {}
    counts = {}
    projection = {"_id": 0}
    for original in EVERYDAY:
        qid = original["id"]
        counts[qid] = dict.fromkeys(wording(original)["labels"], 0)
        row = published.get(qid)
        if row and row["q"] == original["q"] and sorted(wording(row)["labels"]) == sorted(counts[qid]):
            current[qid] = row
            for field in ("choice", "label", "question"):
                projection[f"about.answers.{qid}.{field}"] = 1
    if current:
        async for member in db.users.find({**eligible, "everyday_trends_opt_in": True}, projection):
            for qid, row in current.items():
                index = resolve_answer(member.get("about"), quiz, row)
                if index is not None:
                    counts[qid][row["options"][index]["label"]] += 1
    return {"minimum": MINIMUM_RESPONDENTS, "questions": [
        {"id": row["id"], "prompt": row["q"], "current": row["id"] in current,
         **disclosure(counts[row["id"]].keys(), counts[row["id"]].values())}
        for row in EVERYDAY]}


def eligible_members(eligible, user_field, fields=None):
    return [
        {"$lookup": {"from": "users", "localField": user_field, "foreignField": "user_id",
                     "pipeline": [{"$match": eligible}, {"$project": fields or {"_id": 1}}], "as": "member"}},
        {"$match": {"member.0": {"$exists": True}}},
    ]


async def hourly_couples_activity(db, eligible, start, end):
    # Legacy records can repeat a daily visit. Read from the first UTC midnight
    # touched by the window so a later duplicate cannot move it into the last 24h.
    start_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = await db.events.aggregate([
        {"$match": {"type": {"$in": list(COUPLES_EVENTS)}}},
        *report_dates.instant_stages("ts", start_day, end),
        {"$group": {"_id": {"user": "$user_id", "day": {"$dateToString": {"date": "$report_time", "format": "%Y-%m-%d"}}, "type": "$type"},
                    "first": {"$min": "$report_time"}}},
        {"$match": {"first": {"$gte": start, "$lte": end}}},
        *eligible_members(eligible, "_id.user"),
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%dT%H", "date": "$first"}},
                    "visits": {"$sum": {"$cond": [{"$eq": ["$_id.type", "couples_opened"]}, 1, 0]}},
                    "samples": {"$sum": {"$cond": [{"$eq": ["$_id.type", "couples_sample_completed"]}, 1, 0]}}}},
        {"$sort": {"_id": -1}},
    ]).to_list(25)
    return [{"type": "couples", "at": row["_id"] + ":00:00+00:00",
             "visits": row["visits"], "samples": row["samples"]} for row in rows]


def live_source_filter(kind):
    # An interrupted erasure fences the attempt before removing its owner/date.
    # Keep live entries and counts consistent with the saved-attempt period report.
    return {"deleted": {"$ne": True}} if kind in ("your_type", "quick_advice") else {}


async def latest_activity_rows(collection, eligible, kind, description, end):
    limit = 60
    is_signup = kind == "signup"
    local = {**(eligible if is_signup else {}), **live_source_filter(kind)}
    owner = [] if is_signup else eligible_members(eligible, "user_id", {"email": 1, "_id": 0})
    email = "$email" if is_signup else {"$arrayElemAt": ["$member.email", 0]}
    output = [{"$limit": limit}, {"$project": {"_id": 0, "at": "$report_time", "type": {"$literal": kind},
                                             "detail": description, "user": {"$ifNull": [email, "member"]}}}]
    async def shortlist(bounds):
        # Raw index order is chronological for BSON, and gives a lower bound for ISO strings.
        return await collection.aggregate([
            {"$match": {**local, "created_at": bounds}}, {"$sort": {"created_at": -1}},
            {"$set": {"report_time": report_dates.mongo_timestamp("$created_at")}},
            {"$match": {"report_time": {"$type": "date", "$lte": end}}}, *owner, *output,
        ]).to_list(limit)
    text_end = (end.date() + timedelta(days=2)).isoformat()
    native, strings = await asyncio.gather(shortlist({"$type": "date", "$lte": end}),
                                            shortlist({"$type": "string", "$lt": text_end}))
    if len(strings) == limit:
        # Offsets can reorder a string past the cutoff. Re-sort only candidate dates
        # close enough to beat the oldest of the 60 already eligible observations.
        cutoff = min(row["at"] for row in strings)
        earliest = (cutoff.date() - timedelta(days=1)).isoformat()
        strings = await collection.aggregate([
            {"$match": {**local, "created_at": {"$gte": earliest, "$lt": text_end}}},
            {"$set": {"report_time": report_dates.mongo_timestamp("$created_at")}},
            {"$match": {"report_time": {"$gte": cutoff, "$lte": end}}}, {"$sort": {"report_time": -1}},
            *owner, *output,
        ]).to_list(limit)
    return sorted(native + strings, key=lambda row: row["at"], reverse=True)[:limit]


async def live_activity(db, eligible, selected="all", include_couples=False):
    current = datetime.now(timezone.utc)
    start = current - timedelta(hours=24)
    sources = [
        ("signup", db.users, {"$literal": "New account"}),
        ("chat", db.exchanges, {"$cond": ["$has_conversation", "Shared a conversation", "Asked a question"]}),
        ("group", db.group_chats, {"$concat": ["Group chat about ", {"$toString": {"$size": {"$ifNull": ["$person_ids", []]}}}, " people"]}),
        ("practice", db.practice, {"$cond": [{"$eq": ["$mode", "live"]}, "Practice: live", "Practice: likely replies"]}),
        ("your_type", db.your_type_sessions, {"$literal": "Started a Your Type attempt"}),
        ("quick_advice", db.quick_advice, {"$cond": [{"$eq": ["$workspace", "couples"]}, "Requested Couples Quick Advice", "Requested Individual Quick Advice"]}),
    ]
    async def read_source(kind, collection, description):
        stages = report_dates.instant_stages("created_at", start, current)
        if source_filter := live_source_filter(kind):
            stages.insert(0, {"$match": source_filter})
        if kind == "signup":
            stages.extend([{"$match": eligible}, {"$count": "count"}])
        else:
            stages.extend([{"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
                           *eligible_members(eligible, "_id"), {"$group": {"_id": None, "count": {"$sum": "$count"}}}])
        totals = collection.aggregate(stages).to_list(1)
        if selected in ("all", kind):
            events, total = await asyncio.gather(latest_activity_rows(collection, eligible, kind, description, current), totals)
        else:
            events, total = [], await totals
        for row in events:
            row["at"] = row["at"].replace(tzinfo=timezone.utc).isoformat()
        return kind, events, total[0]["count"] if total else 0
    items, counts = [], {}
    for kind, events, count in await asyncio.gather(*(read_source(*source) for source in sources)):
        items.extend(events)
        counts[kind] = count
    if include_couples or selected == "couples":
        hours = await hourly_couples_activity(db, eligible, start, current)
        counts["couples"] = sum(hour["visits"] + hour["samples"] for hour in hours)
        if selected in ("couples", "all"):
            items.extend(hours)
    items.sort(key=lambda item: item["at"], reverse=True)
    return {"items": items[:60], "last24": counts, "from_utc": start.isoformat(), "now": current.isoformat()}


def daily_count_pipeline(metric, stamp, dates, eligible, filters=None, users=False):
    query = {**(filters or {}), **(eligible if users else {})}
    pipeline = [{"$match": query}, *report_dates.calendar_stages(stamp, dates)]
    day = {"$dateToString": {"format": "%Y-%m-%d", "date": "$report_time"}}
    if users:
        pipeline.append({"$group": {"_id": {"day": day, "metric": metric}, "n": {"$sum": 1}}})
    else:
        # Group before joining so repeated events by one account do not
        # repeatedly fetch member eligibility. No events' text is projected.
        key = {"uid": "$user_id", "day": day, "metric": metric}
        pipeline.append({"$group": {"_id": key, "n": {"$sum": 1}}})
        pipeline += eligible_members(eligible, "_id.uid")
        count = {"$cond": [{"$in": ["$_id.metric", list(COUPLES_EVENTS)]}, 1, "$n"]} if metric == "$type" else "$n"
        pipeline.append({"$group": {"_id": {"day": "$_id.day", "metric": "$_id.metric"}, "n": {"$sum": count}}})
    return pipeline


async def daily_counts(collection, metric, stamp, dates, eligible, filters=None, users=False):
    return await collection.aggregate(daily_count_pipeline(metric, stamp, dates, eligible, filters, users)).to_list(None)


async def daily_counts_group(collection, records, dates, eligible, *, users=False):
    if len(records) == 1:
        metric, stamp, filters = records[0]
        return await daily_counts(collection, metric, stamp, dates, eligible, filters, users)
    # Payment metrics deliberately occur twice (fulfilled-at and legacy date
    # fallback). Unique aliases retain both independent counting predicates.
    facets = {"m" + str(index): daily_count_pipeline(metric, stamp, dates, eligible, filters, users)
              for index, (metric, stamp, filters) in enumerate(records)}
    scopes = [{**(filters or {}), **(eligible if users else {})} for _, _, filters in records]
    outer = {} if any(not scope for scope in scopes) else {"$or": scopes}
    rows = await collection.aggregate([{"$match": outer}, {"$facet": facets}]).to_list(1)
    return [row for alias in facets for row in rows[0][alias]]


async def saved_daily_counts(db, metrics, dates, eligible, *, users=True):
    # Overview needs only owner-scoped counts. Metrics from one collection can
    # share a repository read while retaining their original filters and UTC
    # date-normalization pipelines inside separate facets.
    collections = {}
    for metric, name, stamp, filters in SAVED_RECORDS:
        if metric in metrics:
            collections.setdefault(name, []).append((metric, stamp, filters))
    async def read(name, records):
        return await daily_counts_group(db[name], records, dates, eligible, users=users)
    return await asyncio.gather(*(read(name, records) for name, records in collections.items()))


async def outcome_report(db, eligible, dates):
    # Each opting-in member gets one vote, even if they log many outcomes.
    # Select the latest still-existing categorised outcome in the UTC window.
    pipeline = [
        {"$match": {"outcome": {"$nin": ["", None]}, "outcome_result": {"$in": list(RESULT_LABEL)}}},
        *report_dates.calendar_stages("outcome_at", dates),
        {"$sort": {"report_time": -1, "_id": -1}},
        {"$group": {"_id": "$user_id", "result": {"$first": "$outcome_result"}}},
        *eligible_members({**eligible, "everyday_trends_opt_in": True}, "_id"),
        {"$group": {"_id": "$result", "n": {"$sum": 1}}},
    ]
    rows = {row["_id"]: row["n"] async for row in db.exchanges.aggregate(pipeline)}
    return {"minimum": MINIMUM_RESPONDENTS, **disclosure(RESULT_LABEL.values(), [rows.get(key, 0) for key in RESULT_LABEL])}


async def quiz_first_completions(db, eligible, dates):
    # Older events can repeat. Pick the first recorded milestone before filtering by the report window.
    _, bounds = report_dates.calendar_ranges(date.fromisoformat(dates["$gte"]), date.fromisoformat(dates["$lt"]))
    return await db.events.aggregate([
        {"$match": {"type": {"$in": list(QUIZ_COMPLETIONS)}}},
        {"$set": {"report_time": report_dates.mongo_timestamp("$ts")}},
        {"$match": {"report_time": {"$type": "date", "$lt": bounds["$lt"]}}},
        {"$group": {"_id": {"uid": "$user_id", "metric": "$type"}, "first": {"$min": "$report_time"}}},
        {"$match": {"first": bounds}},
        *eligible_members(eligible, "_id.uid"),
        {"$group": {"_id": {"day": {"$dateToString": {"format": "%Y-%m-%d", "date": "$first"}},
                            "metric": "$_id.metric"}, "n": {"$sum": 1}}},
    ]).to_list(None)


async def current_subscriptions(db, eligible):
    # Use the same entitlement and cycle key as member access. Expired periods and
    # pending reservations do not count as active subscriptions or settled replies.
    active = used = 0
    now = int(datetime.now(timezone.utc).timestamp())
    live = {**eligible, "tier": "paid", "subscription_period_start": {"$lte": now},
            "subscription_period_end": {"$gt": now}}
    fields = {"_id": 0, "tier": 1, "stripe_subscription_id": 1, "subscription_period_start": 1,
              "subscription_period_end": 1, "paid_reply_cycles": 1, "subscription_status": 1, "plan_offer": 1}
    async for member in db.users.find(live, fields):
        cycle = paid_cycle(member)
        if cycle:
            active += 1
            used += member.get("paid_reply_cycles", {}).get(cycle, {}).get("used", 0)
    return {"active": active, "replies_used": used}


async def saved_features(db, eligible):
    features = [
        ("people_archived", "Archived People profiles", db.people, {"archived": True}),
        ("plans_pinned", "Pinned game plans", db.preps, {"pinned": True}),
        ("patterns_confirmed", "Member-confirmed observations", db.patterns, {"member_confirmed": True}),
        ("practice_patterns", "Observations linked to practice", db.patterns, {"sources": {"$regex": "^practice:"}}),
    ]
    async def count(key, label, collection, filters):
        rows = await collection.aggregate([
            {"$match": filters}, {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
            *eligible_members(eligible, "_id"), {"$group": {"_id": None, "n": {"$sum": "$n"}}},
        ]).to_list(1)
        return {"key": key, "label": label, "count": rows[0]["n"] if rows else 0}
    return await asyncio.gather(*(count(*feature) for feature in features))


async def individual_tone_activity(db, eligible, dates):
    # Count actions once per stored read. This projection never reads conversation or correction wording.
    flags = {
        "estimated": {"$eq": ["$tone.status", "estimated"]},
        "insufficient": {"$eq": ["$tone.status", "insufficient"]},
        "selected": {"$in": ["$tone_feedback.style", STYLES]},
        "switched": {"$gt": [{"$size": {"$ifNull": ["$tone_feedback.styles", []]}}, 1]},
        "copied": {"$gt": [{"$size": {"$ifNull": ["$tone_feedback.copied_styles", []]}}, 0]},
        "reviewed": {"$in": ["$tone_feedback.fit", ["fits", "corrected"]]},
        "corrected": {"$eq": ["$tone_feedback.fit", "corrected"]},
        "reported_sending": {"$eq": [{"$type": "$tone_feedback.sent"}, "bool"]},
        "sent": {"$eq": ["$tone_feedback.sent", True]},
        "rated_helpfulness": {"$and": [{"$eq": ["$tone_feedback.sent", True]},
                                        {"$eq": [{"$type": "$tone_feedback.helped"}, "bool"]}]},
    }
    for style in STYLES:
        flags["style_" + style] = {"$in": [style, {"$ifNull": ["$tone_feedback.styles", []]}]}
    pipeline = [
        {"$match": {"tone.status": {"$in": ["estimated", "insufficient"]}}},
        *report_dates.calendar_stages("created_at", dates),
        {"$project": {"user_id": 1, **flags}},
        {"$group": {"_id": "$user_id", "reads": {"$sum": 1},
                    **{k: {"$sum": {"$cond": ["$" + k, 1, 0]}} for k in flags}}},
        *eligible_members(eligible, "_id"),
        {"$group": {"_id": None, "members": {"$sum": 1}, **{k: {"$sum": "$" + k} for k in ("reads", *flags)}}},
        {"$project": {"_id": 0}},
    ]
    result = await db.exchanges.aggregate(pipeline).to_list(1)
    summary = result[0] if result else dict.fromkeys(("members", "reads", *flags), 0)
    summary["styles"] = {style: summary.pop("style_" + style) for style in STYLES}
    return summary


async def report(db, eligible, quiz, days, today: date | None = None):
    if days not in (7, 30, 90):
        raise HTTPException(422, "Choose a 7, 30 or 90 day window.")
    end = today or datetime.now(timezone.utc).date()
    start = end - timedelta(days=days - 1)
    dates = {"$gte": start.isoformat(), "$lt": (end + timedelta(days=1)).isoformat()}
    eligible = {**eligible, "closing": {"$ne": True}}
    daily = {(start + timedelta(days=i)).isoformat(): dict.fromkeys(ACTIVITY, 0) for i in range(days)}
    payment_scope = {"env": eligible["env"]} if "env" in eligible else {}
    paid = {"payment_status": "paid"}
    payments = (
        ("checkouts_started", "created_at", {"kind": "purchase"}),
        ("paid_renewals", "created_at", {"kind": "renewal", **paid}),
        ("paid_purchases", "fulfilled_at", {"kind": "purchase", **paid}),
        # Legacy purchases without a fulfillment timestamp use the original transaction date.
        ("paid_purchases", "created_at", {"kind": "purchase", **paid, "fulfilled_at": None}),
        ("paid_upgrades", "created_at", {"kind": "upgrade", **paid}),
        ("refill_checkouts", "created_at", {"kind": "topup"}),
        ("paid_refills", "fulfilled_at", {"kind": "topup", **paid}),
        ("paid_refills", "created_at", {"kind": "topup", **paid, "fulfilled_at": None}),
        ("adjusted_refills", "fulfilled_at", {"kind": "topup", **paid, "topup_credits": {"$lt": TOPUP["messages"]}}),
        ("adjusted_refills", "created_at", {"kind": "topup", **paid, "fulfilled_at": None, "topup_credits": {"$lt": TOPUP["messages"]}}),
    )
    async def saved_counts():
        grouped = await saved_daily_counts(db, {metric for metric, _, _, _ in SAVED_RECORDS}, dates, eligible, users=False)
        return [row for rows in grouped for row in rows]
    work = [
        daily_counts(db.users, "signups", "created_at", dates, eligible, users=True),
        saved_counts(),
        quiz_first_completions(db, eligible, dates),
        daily_counts(db.events, "$type", "ts", dates, eligible, {"type": {"$in": list(EVENT_METRICS)}}),
        daily_counts_group(db.payment_transactions, [(metric, stamp, {**payment_scope, **filters})
            for metric, stamp, filters in payments], dates, eligible),
    ]
    preferences, results, subscriptions, features, tone_usage, *counts_by_source = await asyncio.gather(
        everyday_report(db, eligible, quiz), outcome_report(db, eligible, dates), current_subscriptions(db, eligible),
        saved_features(db, eligible), individual_tone_activity(db, eligible, dates), *work)
    for results_by_day in counts_by_source:
        for row in results_by_day:
            day, key = row["_id"]["day"], row["_id"]["metric"]
            if day in daily:
                daily[day][EVENT_METRICS.get(key, QUIZ_COMPLETIONS.get(key, key))] += row["n"]
    return {"days": days, "start": start.isoformat(), "end": end.isoformat(),
            "measures": [{"key": key, "label": label, "unit": unit} for key, (label, unit) in ACTIVITY.items()],
            "daily": [{"day": day, **counts} for day, counts in daily.items()],
            "totals": {metric: sum(row[metric] for row in daily.values()) for metric in ACTIVITY},
            "everyday": preferences, "outcomes": results, "subscriptions": subscriptions, "saved_features": features,
            "tone_usage": tone_usage}
