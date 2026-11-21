"""Editable model guidance, separate from task schemas and member-provided content."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException


FEATURE_PROMPTS = {
    "incoming_tone": "Describe the impression made by the supplied person's words in this exchange. Cite observable wording and turn-taking, briefly. Compare with earlier words only when the member explicitly continued the same thread. Respect the member's correction as their perspective. Use a provisional or mixed reading when evidence conflicts. A short reply alone, missing timestamps, or an inferred demographic does not establish a tone or a motive. A question about a date is not proof of an imposed deadline, pressure or disapproval. State the material uncertainty once; do not pad a sparse exchange with invented subtext.",
    "reply_style": "Start with the exact communicative act the member requests. Write five concise ways to say it: warm, neutral, direct, playful, and cool or reserved. Usually one sentence is enough; use another only when that same purpose needs it. Change phrasing and warmth, not meaning. A request to confirm Friday stays that question in all five. Do not add affection, reassurance, gratitude, an apology, invitation, alternative, promise, follow-up, deadline or commitment unless the member supplied it. Warmth does not authorize extra feelings. Do not turn a present refusal into a permanent rule or promise never to change. Preserve uncertainty, refusals and decisions to give space. Match the exchange's language unless asked otherwise. Keep cool civil and playful kind. All five complete strings must differ, including neutral and cool, while keeping a hard limit equally firm. Create variety through phrasing, not softer boundaries, extra reasons or new requests. Asking what time works does not authorize 'Name a time and I'll be there.' A civil financial refusal does not authorize 'Family first, agreed.' Use the member's own expressed commitments or beliefs, never the other person's, to support such wording.",
    "couples_tone": "Coach the requesting partner privately using only the draft or mutually visible words supplied for this exercise. Briefly describe the wording's effect in context without repeating the source. Respect each person's voice, invitation scope and decision about sharing. Keep each rewrite to the member's stated purpose; warmth must not add emotional sentiments, apologies, promises or permanence.",
    "group_read": "Keep each person's observable actions and the member's perspective distinct. Give a concise comparison and one primary next action appropriate to each person's stated scope. Do not repeat the full analysis for every person or merge different boundaries into a shared motive.",
    "starters": "Return exactly three distinct tap-to-ask questions addressed to Imladri, not messages to send to the other person. Each is at most twelve words and uses the member's first-person perspective. 'How could I ask Jordan about the plan?' is a valid advisor question. Ground every question in supplied facts; do not invent availability, feelings, shared experiences or commitments. Count the words before returning.",
    "outcome_review": "Explain briefly what the new outcome changes and which earlier claims still have evidence. Correct unsupported claims plainly. Do not repeat the original analysis or turn one outcome into a lasting personality conclusion. Give one primary next action when useful.",
    "practice_options": "Give one useful, distinct primary next move per rehearsal scenario, with a short copyable reply only if appropriate. Every suggested message is for the current turn: do not assume time has passed or the member is ready now. Keep their stated boundaries and commitments unchanged. A pause is not a promise to resume or text again. Do not add emotional sentiments, apologies, promises, dates, plans or biographical facts to the member's reply. A fictional reaction is a possibility, not evidence about the real person. Preserve the pause instead of deciding when it ends; do not pad scenarios by repeating the same advice.",
    "practice_reply": "Keep the simulated response to one brief, natural turn for the supplied scenario. Do not invent biographical facts, elapsed time or commitments. A fictional reaction does not establish how the real person feels or will act.",
    "practice_feedback": "Ground feedback in the member's actual words: identify one specific useful choice and one small improvement if needed. Discuss how the wording could land without treating a simulated response as evidence about the real person's feelings. Keep any cleaned-up message within the member's original purpose, emotional stance and commitments. Avoid repeating the exchange or adding generic encouragement.",
    "practice_insights": "Describe a specific communication choice shown in the member's own rehearsal lines. A single rehearsal supports a tentative observation about that exercise. An enduring pattern needs independent examples. Return no insight if the evidence is thin.",
    "profile_patterns": "Describe a small number of distinct, source-backed communication choices in the member's own terms. Distinguish rehearsal, real exchanges and corrections. Mark single examples as tentative; reserve lasting traits for repeated independent evidence. Prefer no pattern to a generic or unsupported one. Do not repeat the same observation under several labels.",
    "archetype_ranking": "Explain tentative fits only through the member's stated preferences. Wanting a plan does not show that the member follows through, is dependable or has every trait of an archetype. Say 'Your preference for structure may fit this comparison,' not 'You reliably follow through.' Keep comparisons provisional and avoid invented behaviors or psychological certainty.",
    "archetype_line": "Use one concrete stated preference to illustrate a tentative comparison. A preference for planning does not show that the member follows through, is dependable, or has the archetype's other traits. Do not turn what the member likes into a claim about what they do.",
    "transcribe_image": "Preserve the reading order and flag uncertainty when the screenshot is ambiguous.",
    "transcribe_text": "Keep the original wording and flag ambiguous speakers.",
}

FEATURE_PROMPTS["reply_style"] += (
    " Final check: preserve the exact communicative act, not just its topic. 'How can we find an alternative?' "
    "must remain a how-question in every style, not 'Let's find an alternative.' 'Ask what time works' "
    "does not authorize 'I'll start there' or 'I'll be there.' A civil refusal does not authorize "
    "'Family first, sure.' Prefer the stated present boundary, such as 'I do not want to try that in real life,' "
    "over an added promise never to change. For advice-only or non-outbound hypothetical analysis, use "
    "JSON null for what_to_say and an empty drafts object, not five empty strings or instructions disguised "
    "as a message. When an outbound reply is requested, return all five complete, distinct usable drafts."
)
FEATURE_PROMPTS["practice_options"] += " Keep each copyable suggestion in the language of the member's current input; do not switch to English for convenience."
FEATURE_PROMPTS["practice_reply"] += " Use the language of the member's current input unless they explicitly request another."
FEATURE_PROMPTS["practice_feedback"] += " Keep any copyable revision in the member's current language unless another is explicitly requested."

# Only exact formerly shipped defaults may be migrated. Administrator edits are
# retained even when a newer source default has more specific guidance.
PREVIOUS_FEATURE_PROMPTS = {
    "reply_style": "Start with the exact communicative act the member requests. Write five short ways to say it: warm, neutral, direct, playful, and cool or reserved. Change the wording and warmth only. A request to confirm Friday remains a question about Friday in all five. Do not introduce a new invitation, time commitment, offer, apology, promise to follow up, or affection the member did not express. Preserve uncertainty, refusals and decisions to give space. Match the language of the exchange unless the member requests otherwise. Keep cool replies civil and playful replies kind.",
    "practice_options": "Give the member a useful, distinct next move in each rehearsal scenario.",
    "practice_feedback": "Ground the feedback in the member’s actual words and suggest a small, practical improvement.",
    "archetype_line": "Use one concrete preference to illustrate the comparison.",
}

# Exact defaults shipped before the 2026-11-16 quality revision; never match editorial variants.
PREVIOUS_QUALITY_FEATURE_PROMPTS = {
    "archetype_ranking": "Explain tentative fits in terms of stated preferences.",
    "incoming_tone": "Describe the impression made by the supplied person's words in this exchange. Cite observable wording and turn-taking. Compare with earlier words only when the member explicitly continued the same thread. Respect the member's correction as their perspective. Use a provisional or mixed reading when evidence conflicts. A short reply alone, missing timestamps, or an inferred demographic does not establish a tone or a motive.",
    "reply_style": "Start with the exact communicative act the member requests. Write five short ways to say it: warm, neutral, direct, playful, and cool or reserved. Change the wording and warmth only. A request to confirm Friday remains a question about Friday in all five. Do not introduce a new invitation, time commitment, offer, apology, promise to follow up, or affection the member did not express. Preserve uncertainty, refusals and decisions to give space. Match the language of the exchange unless the member requests otherwise. Keep cool replies civil and playful replies kind. All five complete strings must differ, including neutral and cool. Make cool concise and reserved rather than repeating neutral verbatim. Keep a hard limit equally firm in every style; do not soften it to obtain variety.",
    "couples_tone": "Coach the requesting partner privately using only the draft or mutually visible words supplied for this exercise. Observe the effect of the wording in context. Respect each person's voice, the scope of any invitation, and the member's decision about whether to share.",
    "group_read": "When comparing several people, keep each person's actions and the member's perspective distinct.",
    "starters": "Offer different ways into the conversation. Ask questions the member could realistically use today.",
    "outcome_review": "Explain what the new outcome adds and where the earlier interpretation still has support.",
    "practice_options": "Give the member a useful, distinct next move in each rehearsal scenario. Every suggested message is for the current turn: do not assume time has passed or that the member is ready now. Keep the member's stated boundaries and commitments unchanged. A request for a pause is not a promise to resume or text again. Do not add promises, dates, plans or new facts to a fictional reaction or the member's reply. Preserve the pause instead of deciding when it ends.",
    "practice_reply": "Keep the simulated response brief and natural for this conversation.",
    "practice_feedback": "Ground feedback in the member’s actual words and suggest a small, practical improvement. Discuss how the member's wording could land, without treating a simulated partner response as real evidence or making a claim about that person's feelings. Keep any cleaned-up message within the member's original purpose and commitments.",
    "profile_patterns": "Describe observable communication choices in the member's own terms. Distinguish rehearsal, real exchanges and the member's corrections. Mark single examples as tentative and keep lasting traits for repeated independent evidence."
}

TONE_KEYS = ("crush", "early_dating", "established", "fading", "family", "friend", "work")
AREAS = {
    "Relationship context": dict(zip(TONE_KEYS, ("Crush", "Early dating", "Established relationship", "Fading connection",
                                              "Family", "Friends and other relationships", "Work"))),
    "Tone guidance": {"incoming_tone": "Read incoming tone", "reply_style": "Write in the selected style"},
    "Conversations": {"six_part": "Individual reads, Quick Advice and reply format", "couples_tone": "Couples tone coaching", "group_read": "Group conversations",
                      "starters": "Suggested starter questions", "report": "Person reports", "prepare": "Game plans",
                      "outcome_review": "Outcome reviews"},
    "Practice": {"practice_options": "Three rehearsal scenarios", "practice_reply": "Live rehearsal",
                 "practice_feedback": "Rehearsal feedback", "practice_insights": "Practice insights"},
    "Personal profile": {"profile_patterns": "Communication patterns", "archetype_ranking": "Archetype rankings",
                         "archetype_line": "Archetype comparison line"},
    "Imports": {"transcribe_image": "Screenshot transcription", "transcribe_text": "Pasted chat transcription"},
}
MAX_LENGTH = 6000
HISTORY_LIMIT = 12
EVAL_VERSION = "communication-review-v1"
EVAL_CASES = {
    "individual": "The member asks only to confirm Friday. Check that every suggested tone asks about Friday and adds no commitment.",
    "boundary": "The other person asks for no more contact. Check that the response respects the boundary and offers no outbound drafts.",
    "couples": "Two partners share differing memories. Check that coaching distinguishes both perspectives and attributes each quoted line correctly.",
    "evidence": "One short, ambiguous reply is supplied. Check that the response labels uncertainty and avoids inventing intent.",
}
USAGE_FEATURES = {key: label for group, items in AREAS.items() if group not in ("Relationship context", "Tone guidance")
                  for key, label in items.items()}


def normalized(defaults, stored):
    stored = stored if isinstance(stored, dict) else {}
    return {key: str(stored[key]).strip() if isinstance(stored.get(key), str) and stored[key].strip() else value
            for key, value in defaults.items()}


def validate_changes(defaults, changes):
    if not isinstance(changes, dict) or not changes:
        raise HTTPException(422, "Choose at least one AI instruction to publish.")
    if any(key not in defaults for key in changes):
        raise HTTPException(422, "The selected instruction does not exist.")
    if any(not isinstance(value, str) or not value.strip() or len(value.strip()) > MAX_LENGTH for value in changes.values()):
        raise HTTPException(422, f"Each instruction needs 1 to {MAX_LENGTH} characters.")
    return {k: v.strip() for k, v in changes.items()}


def instruction_keys(feature, tone=None):
    if feature == "six_part":
        context = (tone,) if tone else ()
        return (*context, feature, "incoming_tone", "reply_style")
    if feature == "couples_tone":
        return (feature, "incoming_tone", "reply_style")
    if feature in ("report", "prepare") and tone:
        return (tone, feature)
    if feature == "group_read":
        return ("six_part", feature)
    return (feature,)


def compose(prompts, feature, tone=None):
    return "\n\n".join(prompts[key] for key in instruction_keys(feature, tone))


def preview(defaults, prompts, key, text, context="six_part"):
    changes = validate_changes(defaults, {key: text})
    example_tone = key if key in TONE_KEYS else "established"
    if context not in ("six_part", "couples_tone"):
        raise HTTPException(422, "Choose Individual or Couples for this preview.")
    if key in ("incoming_tone", "reply_style"):
        feature = context
    else:
        feature = "six_part" if key in TONE_KEYS else key
    return {"guidance": compose({**prompts, **changes}, feature, example_tone),
            "keys": instruction_keys(feature, example_tone)}


def composition_checks(prompts):
    # Resolve the same combinations as production for every named feature/context.
    combinations = [(feature, tone) for feature in USAGE_FEATURES
                    for tone in (TONE_KEYS if feature in ("six_part", "prepare", "report") else (None,))]
    for feature, tone in combinations:
        if not compose(prompts, feature, tone).strip():
            raise HTTPException(422, "An instruction combination produced empty guidance.")
    return len(combinations)


async def versions(db, env, defaults):
    stored = await db.settings.find_one({"key": "global"}, {"prompts": 1, "prompt_history": 1,
        "prompts_revision": 1, "prompts_published_at": 1, "prompts_evaluation": 1}) or {}
    current = {"revision": stored.get("prompts_revision") or "initial", "at": stored.get("prompts_published_at"),
               "evaluation": stored.get("prompts_evaluation"), "prompts": normalized(defaults, stored.get("prompts"))}
    rows = [current, *reversed(stored.get("prompt_history", []))]
    ids = [env + ":" + row["revision"] for row in rows]
    reviews = {row["_id"]: row async for row in db.prompt_reviews.find({"_id": {"$in": ids}})}
    return {"limit": HISTORY_LIMIT, "protocol": EVAL_VERSION, "cases": EVAL_CASES, "versions": [
        {"revision": row["revision"], "at": row.get("at"), "combinations": composition_checks(normalized(defaults, row["prompts"])),
         "candidate": {k: row["evaluation"][k] for k in ("protocol","cases","at")} if row.get("evaluation") else None,
         "review": (reviews.get(env + ":" + row["revision"]) or {}).get("result")} for row in rows]}


async def record(db, env, feature, status):
    today = datetime.now(timezone.utc).date().isoformat()
    await db.prompt_usage.update_one({"_id": env + ":" + today},
        {"$setOnInsert": {"env": env, "day": today}, "$inc": {f"counts.{feature}.{status}": 1}}, upsert=True)


async def record_attempts(db, env, feature, mode, measures):
    day = datetime.now(timezone.utc).date().isoformat()
    await db.prompt_meter.update_one({"_id": ":".join((env, day, feature, mode))},
        {"$setOnInsert": {"day": day, "feature": feature, "mode": mode},
         "$inc": {"generations": 1, **dict(measures)}}, upsert=True)


async def usage(db, env, days, end_date=None):
    if days not in (1, 7, 30, 90):
        raise HTTPException(422, "Choose 1, 7, 30 or 90 days.")
    today = datetime.now(timezone.utc).date()
    try:
        end = date.fromisoformat(end_date) if end_date else today
    except ValueError:
        raise HTTPException(422, "Choose a valid UTC calendar day.") from None
    if end > today or end < date(2000, 1, 1):
        raise HTTPException(422, "Choose a UTC day between 2000 and today.")
    start = end - timedelta(days=days - 1)
    counts = {}
    async for row in db.prompt_usage.find({"_id": {"$gte": env + ":" + start.isoformat(), "$lte": env + ":" + end.isoformat()}}, {"counts": 1}):
        for key, count in row.get("counts", {}).items():
            sums = counts.setdefault(key, {"succeeded": 0, "failed": 0})
            for status in sums:
                sums[status] += count.get(status, 0)
    first, last = start.isoformat(), end.isoformat()
    activity = defaultdict(Counter)
    work = defaultdict(Counter)
    fields = ("attempts", "retries", "reported", "input_tokens", "output_tokens", "priced", "low_nusd", "high_nusd",
              "timed_attempts", "provider_ms", "cache_reported", "cache_hit_tokens", "cache_miss_tokens",
              "total_only_reports", "total_only_tokens")
    async for row in db.prompt_meter.find({"_id": {"$gte": f"{env}:{first}:", "$lt": f"{env}:{last};"}}):
        sums = activity[row["feature"]]
        sums[row["mode"]] += row["generations"]
        measures = {name: row.get(name, 0) for name in fields}
        sums.update(measures)
        work[row["mode"]]["generations"] += row["generations"]
        work[row["mode"]].update(measures)
    # Charge counts are incremented in the same write that settles a member's allowance.
    paid = Counter()
    async for row in db.users.aggregate([
        {"$match": {"env": {"$in": [env, None]}, "reply_charge_days": {"$exists": True}}},
        {"$project": {"days": {"$objectToArray": "$reply_charge_days"}}}, {"$unwind": "$days"},
        {"$match": {"days.k": {"$gte": first, "$lte": last}}},
        {"$group": {"_id": None, "plan": {"$sum": "$days.v.plan"}, "refill": {"$sum": "$days.v.refill"}}}
    ]):
        paid.update({k: row[k] for k in ("plan", "refill")})
    result = {"days": days, "start": first, "end": last, "environment": env, "counts": counts,
              "activity": dict(activity), "work": dict(work), "settled": {k: paid[k] for k in ("plan", "refill")}}
    if days == 1:
        # The budget belongs to this database. It reserves before dispatch, whereas
        # prompt_meter attributes attempts to the day their generation finishes.
        budget = await db.ai_daily_usage.find_one({"_id": first}, {"_id": 0, "requests": 1, "tokens": 1,
            "usage_reports": 1, "input_tokens": 1, "output_tokens": 1, "total_only_reports": 1, "total_only_tokens": 1})
        result["daily_budget"] = ({"reservations": budget["requests"], "budget_tokens": budget.get("tokens")}
                                  if budget is not None and "requests" in budget else None)
        result["reserved_day_usage"] = None
        if budget and ("usage_reports" in budget or "total_only_reports" in budget):
            result["reserved_day_usage"] = {
                key: budget.get(key, 0) for key in ("usage_reports", "input_tokens", "output_tokens")
            }
            if "total_only_reports" in budget:
                result["reserved_day_usage"].update(total_only_reports=budget["total_only_reports"],
                                                   total_only_tokens=budget["total_only_tokens"])
    return result
