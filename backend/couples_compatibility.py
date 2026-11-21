"""Current, mutually shared assessment comparisons; no matchmaking prediction."""
from datetime import datetime, timezone
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

try:
    from . import answer_history, intimacy_answers, quiz_answers, research
    from .couples import active_pair, is_adult
    from .quiz import radar, personality, default_quiz, with_quiz_scoring
except ImportError:
    import answer_history, intimacy_answers, quiz_answers, research
    from couples import active_pair, is_adult
    from quiz import radar, personality, default_quiz, with_quiz_scoring

VERSION = "couples-compatibility-v1"
SHARING_VERSION = "couples-assessment-sharing-v1"
NOTICE = ("Share my current answers and results for the selected assessments with my current partner, "
          "and use the assessments we both share in Couples comparisons and coaching. This includes "
          "future edits until I turn sharing off. Private notes, conversations, research permission and "
          "Your Type are excluded. Sharing is optional and can be withdrawn here at any time.")
NOTICE_SHA = hashlib.sha256(NOTICE.encode()).hexdigest()
NOTES = ["These are comparisons of self-reported preferences, not a clinical assessment or a prediction of relationship success.",
         "A shared interest is not consent to an activity. Ask each time and respect every boundary.",
         "Only assessment scopes both partners explicitly share are compared. Your Type, private notes and conversations are excluded."]
AXIS_ADVICE = {
    "Directness": "Agree on a clear format: what happened, how it landed, and one specific request. Ask how much detail each of you wants.",
    "Reassurance": "Agree on a realistic acknowledgement when one of you is busy and a time to reconnect. Check preferences instead of guessing intent.",
    "Conflict comfort": "Choose a calm time, take turns, and agree on a pause with a return time if either of you feels overwhelmed.",
    "Independence": "Discuss the balance of shared and separate time. Make one specific agreement that respects both partners' autonomy.",
    "Openness": "Ask permission before a deeper conversation, agree on its pace, and let either partner decline or return to it later.",
}


class SharingEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pair_id: str = Field(min_length=1, max_length=64)
    expected_member: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0, lt=2**53)
    personality: StrictBool
    intimacy: StrictBool
    notice_version: str = Field(min_length=1, max_length=80)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def load_quiz(db):
    stored = await db.settings.find_one({"key": "global"}, {"quiz": 1}) or {}
    candidate = stored.get("quiz") or {}
    return with_quiz_scoring(candidate) if candidate.get("version") == default_quiz()["version"] else default_quiz()


def intimacy_source(row):
    return {key: row.get(key) for key in ("revision", "values", "conditions", "signatures", "adult_confirmed")}


def side(pair, uid):
    if uid == pair.get("inviter"):
        return "a"
    if uid == pair.get("invitee"):
        return "b"
    raise HTTPException(404, "This shared space is unavailable.")


def sharing(pair, who, member):
    value = (pair.get("compatibility_sharing") or {}).get(who) or {}
    valid = (value.get("version") == SHARING_VERSION and value.get("notice_sha256") == NOTICE_SHA
             and type(value.get("revision")) is int and value["revision"] > 0
             and value.get("actor_id") == pair.get("inviter" if who == "a" else "invitee"))
    current = (valid and is_adult(member) and member.get("user_id") == value.get("actor_id")
               and isinstance(member.get("couples_adult_at"), str)
               and value.get("couples_adult_at") == member["couples_adult_at"])
    # Keep the original revision for a new explicit opt-in, but never revive a
    # grant from before adult eligibility was removed and confirmed again.
    return {"version": SHARING_VERSION, "revision": value.get("revision", 0) if valid else 0,
            "personality": bool(current and value.get("personality") is True),
            "intimacy": bool(current and value.get("intimacy") is True)}


def sharing_reviewed(pair, who, member):
    """A current, explicit decision includes choosing not to share either scope."""
    value = (pair.get("compatibility_sharing") or {}).get(who) or {}
    return bool(sharing(pair, who, member)["revision"] > 0 and is_adult(member)
        and member.get("user_id") == value.get("actor_id")
        and isinstance(member.get("couples_adult_at"), str)
        and value.get("couples_adult_at") == member["couples_adult_at"])


async def adult_members(db, pair, env):
    # This eligibility read deliberately excludes private answer payloads.
    people = [await db.users.find_one({"user_id": uid, "env": {"$in": [env, None]}, "closing": {"$ne": True}},
        {"user_id": 1, "env": 1, "couples_adult_at": 1, "about.demographics.age": 1})
        for uid in (pair["inviter"], pair["invitee"])]
    if not all(is_adult(person) for person in people):
        raise HTTPException(403, "This shared space is unavailable.")
    return people


def same_adult_epochs(before, after):
    return all(a["user_id"] == b["user_id"] and a["couples_adult_at"] == b["couples_adult_at"]
               for a, b in zip(before, after))


def empty_domain(ready):
    return {"status": "insufficient_answers" if ready else "awaiting_sharing", "compared_questions": 0,
            "items": [], "notes": []}


def compare_personality(mine, partner, quiz):
    out = empty_domain(True)
    left, right = mine.get("about") or {}, partner.get("about") or {}
    questions = quiz_answers.questions_by_id(quiz)
    common = [q for q in questions.values() if quiz_answers.resolve_answer(left, quiz, q) is not None
              and quiz_answers.resolve_answer(right, quiz, q) is not None]
    out["compared_questions"] = len(common)
    # Compare the same current questions for both people. Different incomplete
    # subsets must not masquerade as a difference in personality.
    shared_quiz = {**quiz, **{s: [q for q in quiz.get(s, []) if q in common] for s in quiz_answers.SETS}}
    other_axes = {row["axis"]: row for row in radar(right, shared_quiz)}
    for row in radar(left, shared_quiz):
        peer = other_axes[row["axis"]]
        if row["answered"] < 3 or peer["answered"] < 3:
            continue
        gap = abs(row["score"] - peer["score"])
        differs = gap >= 34
        out["items"].append({"id": "axis:" + row["axis"], "title": row["axis"],
            "kind": "difference" if differs else "similar",
            "me": str(row["score"]) + "/100 on the questionnaire scale",
            "partner": str(peer["score"]) + "/100 on the questionnaire scale",
            "summary": ("Your answers suggest different preferences here." if differs else
                        "Your answers fall in a similar part of this scale. This does not guarantee agreement."),
            "suggestion": AXIS_ADVICE.get(row["axis"], "Ask how this preference shows up for each of you in a recent situation."),
            "evidence": {"me": row["answered"], "partner": peer["answered"]}})
    a, b = personality(left, quiz), personality(right, quiz)
    if a["code"] and b["code"]:
        out["items"].append({"id": "personality-label", "title": "Personality labels", "kind": "similar" if a["code"] == b["code"] else "difference",
            "me": a["code"] + (" (self-selected)" if a["self_set"] else " (questionnaire estimate)"),
            "partner": b["code"] + (" (self-selected)" if b["self_set"] else " (questionnaire estimate)"),
            "summary": "These labels are discussion aids. They do not establish compatibility or explain a partner's motives.",
            "suggestion": "Compare a concrete preference such as planning or time to think, rather than assuming a label describes every situation.",
            "evidence": {"me": a["answered"], "partner": b["answered"]}})
    if out["items"]:
        out["status"] = "ready"
    out["items"].sort(key=lambda item: (item["kind"] != "difference", item["id"] == "personality-label", item["id"]))
    out["notes"] = ["Scales use only questions you both answered in the current edition; at least three scored answers per axis are required.",
                    "The difference marker is a descriptive one-scale-step threshold, not a validated incompatibility cutoff."]
    return out


def answer_text(choice, conditions):
    return research.CHOICES[choice] + (" — " + ", ".join(conditions) if conditions else "")


def compare_intimacy(mine, partner, variables):
    out = empty_domain(True)
    for key, variable in variables.items():
        if variable.get("domain") != "intimacy":
            continue
        values = [row.get("values", {}).get(key) for row in (mine, partner)]
        if (any(type(value) is not int or not 0 <= value < len(research.CHOICES) for value in values)
                or any(row.get("signatures", {}).get(key) != variable["signature"] for row in (mine, partner))):
            continue
        conditions = [row.get("conditions", {}).get(key, []) for row in (mine, partner)]
        if any(not isinstance(c, list) or any(x not in research.CONDITIONS for x in c) for c in conditions):
            continue
        a, b = values
        boundary = [value in (3, 4) or "In fantasy only" in cond for value, cond in zip(values, conditions)]
        if any(boundary):
            kind = "shared_boundary" if all(boundary) else "boundary_difference"
            summary = ("You both report a limit here." if all(boundary) else "One partner reports a limit that the other does not. Respect the limit.")
            suggestion = "Keep this outside real-life activity whenever either partner declines or limits it to fantasy. Explore only alternatives you both freely want; no persuasion is needed."
        elif a == b and conditions[0] == conditions[1]:
            kind = "shared_interest"
            summary = "You report a similar interest or level of curiosity. This is not permission or proof that your preferred roles fit."
            suggestion = "Ask what this means to each of you, confirm roles and conditions, and check consent before any activity. Either person can change their mind."
        elif a == 2 or b == 2:
            kind = "conditions"
            summary = "At least one answer depends on particular conditions."
            suggestion = "Discuss the conditions in each answer without assuming agreement. If either person is unsure, pause and choose an alternative you both want."
        else:
            kind = "difference"
            summary = "Your reported levels of interest differ. That does not establish overall sexual incompatibility."
            suggestion = "Ask about the difference without pressure. Choose shared interests and leave this optional if either partner is not ready."
        out["items"].append({"id": key, "title": variable["label"], "kind": kind,
            "me": answer_text(a, conditions[0]), "partner": answer_text(b, conditions[1]),
            "summary": summary, "suggestion": suggestion, "evidence": {"me": 1, "partner": 1}})
    out["compared_questions"] = len(out["items"])
    priority = {"boundary_difference": 0, "conditions": 1, "difference": 2, "shared_boundary": 3, "shared_interest": 4}
    out["items"].sort(key=lambda item: (priority[item["kind"]], item["id"]))
    if out["items"]:
        out["status"] = "ready"
    out["notes"] = ["Skipped, deleted and older-edition answers are excluded. Similar answers do not establish compatible roles or ongoing consent."]
    return out


async def view(db, member, quiz, *, expected_pair=None, allow_intro_read=False):
    pair = await active_pair(db, member, allow_intro_read=allow_intro_read)
    if expected_pair is not None and pair["_id"] != expected_pair:
        raise HTTPException(409, "Your pairing changed. Reopen Compatibility.")
    mine_side = side(pair, member["user_id"])
    peer_side = "b" if mine_side == "a" else "a"
    adults = await adult_members(db, pair, member["env"])
    mine_adult, peer_adult = adults if mine_side == "a" else adults[::-1]
    own, peer = sharing(pair, mine_side, mine_adult), sharing(pair, peer_side, peer_adult)
    domains = {name: empty_domain(own[name] and peer[name]) for name in ("personality", "intimacy")}
    source = {"pair": pair["_id"], "sharing": pair.get("compatibility_sharing", {}),
              "adult_epochs": [person["couples_adult_at"] for person in adults], "scopes": {}}

    def result():
        return {"version": VERSION, "pair_id": pair["_id"], "sharing": own,
                "partner_sharing": {name: peer[name] for name in ("personality", "intimacy")},
                "notice": NOTICE, "notice_version": SHARING_VERSION, "domains": domains, "notes": NOTES,
                "context_signature": digest(source)}

    if not any(own[name] and peer[name] for name in domains):
        # No mutually granted scope means there is nothing to compare or send
        # to a model. Do not even read either partner's private answer documents.
        if not same_adult_epochs(adults, await adult_members(db, pair, member["env"])):
            raise HTTPException(409, "Adult participation changed. Refresh Compatibility.")
        if not await db.couples_pairs.find_one({"_id": pair["_id"], "env": member["env"], "state": "active",
                "members": member["user_id"], "compatibility_sharing": pair.get("compatibility_sharing", {"$exists": False})}, {"_id": 1}):
            raise HTTPException(409, "Assessment sharing changed. Refresh Compatibility.")
        return result()
    uids = [pair["inviter"], pair["invitee"]]
    users = [await db.users.find_one({"user_id": person["user_id"], "env": {"$in": [member["env"], None]},
             "closing": {"$ne": True}, "couples_adult_at": person["couples_adult_at"],
             "about.demographics.age": ((person.get("about") or {}).get("demographics") or {}).get("age")},
             {"user_id": 1, "env": 1, "couples_adult_at": 1, "about": 1}) for person in adults]
    if not all(is_adult(person) for person in users):
        raise HTTPException(403, "This shared space is unavailable.")
    mine, partner = (users if mine_side == "a" else users[::-1])
    if own["personality"] and peer["personality"]:
        domains["personality"] = compare_personality(mine, partner, quiz)
        source["scopes"]["personality"] = {"quiz": quiz, "answers": [
            {"choices": {q["id"]: quiz_answers.resolve_answer(p.get("about"), quiz, q)
                         for q in quiz_answers.questions_by_id(quiz).values()},
             "mbti": p.get("about", {}).get("mbti")} for p in users]}
    intimacy_rows = []
    if own["intimacy"] and peer["intimacy"]:
        variables = await intimacy_answers.variables(db)
        intimacy_rows = [(await db.intimacy_answers.find_one({"_id": uid})) or {} for uid in uids]
        left, right = intimacy_rows if mine_side == "a" else intimacy_rows[::-1]
        domains["intimacy"] = compare_intimacy(left, right, variables)
        source["scopes"]["intimacy"] = {"variables": variables, "rows": [intimacy_source(r) for r in intimacy_rows]}
    # A withdrawal, account closure, age edit, answer deletion or unpairing must
    # invalidate an in-flight read. Nothing is persisted as a copied partner profile.
    if not await db.couples_pairs.find_one({"_id": pair["_id"], "state": "active", "members": member["user_id"],
            "compatibility_sharing": pair.get("compatibility_sharing", {"$exists": False})}, {"_id": 1}):
        raise HTTPException(409, "Assessment sharing changed. Refresh Compatibility.")
    for person in users:
        current = await db.users.find_one({"user_id": person["user_id"], "env": {"$in": [member["env"], None]},
                                          "closing": {"$ne": True}}, {"about": 1, "couples_adult_at": 1})
        if (not current or not is_adult(current) or current.get("about") != person.get("about")
                or current.get("couples_adult_at") != person["couples_adult_at"]):
            raise HTTPException(409, "An assessment changed. Refresh Compatibility.")
    for uid, row in zip(uids, intimacy_rows):
        current = await db.intimacy_answers.find_one({"_id": uid}) or {}
        if intimacy_source(current) != intimacy_source(row):
            raise HTTPException(409, "An intimacy answer changed. Refresh Compatibility.")
    if not same_adult_epochs(adults, await adult_members(db, pair, member["env"])):
        raise HTTPException(409, "Adult participation changed. Refresh Compatibility.")
    # Authorization is the LAST I/O boundary, after every source re-read. A
    # partner withdrawing while those reads run must not receive a stale response.
    if not await db.couples_pairs.find_one({"_id": pair["_id"], "env": member["env"], "state": "active",
            "members": member["user_id"], "compatibility_sharing": pair.get("compatibility_sharing", {"$exists": False})}, {"_id": 1}):
        raise HTTPException(409, "Assessment sharing changed. Refresh Compatibility.")
    return result()


async def set_sharing(db, member, quiz, body):
    if body.expected_member != member["user_id"] or body.notice_version != SHARING_VERSION:
        raise HTTPException(409, "Your account or the sharing notice changed. Reopen Compatibility.")
    pair = await active_pair(db, member)
    if body.pair_id != pair["_id"]:
        raise HTTPException(409, "Your pairing changed. Reopen Compatibility.")
    who = side(pair, member["user_id"])
    adults = await adult_members(db, pair, member["env"])
    current_member = adults[0 if who == "a" else 1]
    before = sharing(pair, who, current_member)
    if before["revision"] != body.expected_revision:
        raise HTTPException(409, "Your sharing choice changed. Refresh before saving.")
    if any(getattr(body, scope) and not before[scope] for scope in ("personality", "intimacy")):
        await active_pair(db, member, write=True)
    path = "compatibility_sharing." + who
    if sharing_reviewed(pair, who, current_member) and all(
            before[scope] == getattr(body, scope) for scope in ("personality", "intimacy")):
        return await view(db, member, quiz, expected_pair=pair["_id"])
    await answer_history.flush(db, "couples_pairs", {"_id": pair["_id"]})
    pair = await active_pair(db, member)
    current_adults = await adult_members(db, pair, member["env"])
    if (body.pair_id != pair["_id"] or not same_adult_epochs(adults, current_adults)
            or sharing(pair, who, current_adults[0 if who == "a" else 1])["revision"] != body.expected_revision):
        raise HTTPException(409, "Your pairing or sharing choice changed. Refresh before saving.")
    current = (pair.get("compatibility_sharing") or {}).get(who)
    after = {"version": SHARING_VERSION, "revision": body.expected_revision + 1,
             "actor_id": member["user_id"], "notice_sha256": NOTICE_SHA,
             "couples_adult_at": current_member["couples_adult_at"],
             "personality": body.personality, "intimacy": body.intimacy,
             "updated_at": datetime.now(timezone.utc).isoformat()}
    event = answer_history.event(source="couples_pairs", source_id=pair["_id"], owner_id=member["user_id"],
        actor_id=member["user_id"], pair_id=pair["_id"], domain="consent", question_id="couples.assessment-sharing",
        question={"label": NOTICE}, signature=NOTICE_SHA, before=before, after=after,
        revision=after["revision"], consent=answer_history.consent(member))
    update = answer_history.append({"$set": {path: after}}, event, pair)
    query = {"_id": pair["_id"], "env": member["env"], "state": "active", "members": member["user_id"],
             path: current if current is not None else {"$exists": False}}
    saved = await db.couples_pairs.update_one(answer_history.guard(query, pair), update)
    if not saved.matched_count:
        raise HTTPException(409, "Your sharing choice changed. Refresh before saving.")
    await answer_history.flush(db, "couples_pairs", {"_id": pair["_id"]})
    return await view(db, member, quiz, expected_pair=pair["_id"])


async def coach_context(db, member, quiz, pair_id):
    quiz = quiz if quiz is not None else await load_quiz(db)
    result = await view(db, member, quiz, expected_pair=pair_id, allow_intro_read=True)
    rows, coverage = [], {}
    for scope, domain in result["domains"].items():
        # Bound the extra input; include limits first rather than dropping them
        # behind a long list of common interests. Never include free-form notes.
        items = sorted(domain["items"], key=lambda x: (x["kind"] not in ("boundary_difference", "shared_boundary"), x["id"]))
        coverage[scope] = {"compared_questions": domain["compared_questions"],
                           "available_items": len(items), "included_items": min(12, len(items))}
        for item in items[:12]:
            rows.append({"scope": scope, "topic": item["title"], "kind": item["kind"],
                         "requesting_partner": item["me"], "other_partner": item["partner"], "guidance": item["suggestion"]})
    return {"version": VERSION, "signature": result["context_signature"],
            "meaning": "A bounded excerpt of mutually shared self-reports, not an exhaustive list of boundaries. These are preferences, not observed behavior, diagnosis, consent or relationship predictions.",
            "coverage": coverage, "comparisons": rows}


def make_router(get_db, current_member, get_quiz):
    router = APIRouter(prefix="/couples/compatibility", tags=["Couples compatibility"])

    @router.get("")
    async def read(response: Response, member=Depends(current_member)):
        response.headers["Cache-Control"] = "private, no-store"
        return await view(get_db(), member, await get_quiz())

    @router.put("/sharing")
    async def share(body: SharingEdit, response: Response, member=Depends(current_member)):
        response.headers["Cache-Control"] = "private, no-store"
        return await set_sharing(get_db(), member, await get_quiz(), body)

    return router
