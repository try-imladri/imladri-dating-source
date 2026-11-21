"""Private member drafts and mutually released, revision-checked partner conversations."""
import hashlib
import json
import re
import uuid
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints
from pymongo import IndexModel
from pymongo.errors import DocumentTooLarge, DuplicateKeyError

try:
    from . import answer_context, answer_history
    from .research import fingerprint
    from .couples import active_pair, require_adult
    from .couples_analytics import first_joint_checkin, timestamp as checkin_time
    from .couples_catalog import CARDS, card_prompt, read_catalog
    from .free_plan import has_personal_access, subscription_required
except ImportError:
    import answer_context, answer_history
    from research import fingerprint
    from couples import active_pair, require_adult
    from couples_analytics import first_joint_checkin, timestamp as checkin_time
    from couples_catalog import CARDS, card_prompt, read_catalog
    from free_plan import has_personal_access, subscription_required


def prompt(topic, number, question, choices, discussion):
    return {"id": f"{topic}-{number}", "topic": topic, "question": question,
            "choices": [*choices, "It depends"], "discussion": discussion}


PROMPTS = [
    prompt("communication", 1, "When something feels unresolved, how would you like to start the conversation?",
           ["Talk now", "Write my thoughts first", "Pick a time to return"], "What would make it easier for both of you to start?"),
    prompt("communication", 2, "When plans change unexpectedly, how would you like your partner to tell you?",
           ["A quick heads-up", "A call when there are details", "A message with an alternative"], "What information would each of you welcome first?"),
    prompt("communication", 3, "What question helps when your first answer is “I’m fine”?",
           ["Would you like company?", "Would it help to talk?", "Shall we check back later?"], "How would each of you like the other person to check in?"),
    prompt("repair", 1, "When a conversation gets heated, how could either of you ask for a pause?",
           ["Name a return time", "Use an agreed phrase", "Explain what I need"], "How will you both know when to come back?"),
    prompt("repair", 2, "After a disagreement, what would tell you that your partner understood your point?",
           ["Hearing it in their words", "A question about my experience", "A specific change next time"], "What would feeling understood look like to each of you?"),
    prompt("repair", 3, "When you are ready to reconnect, what small first move would you welcome?",
           ["A gentle message", "A conversation in person", "An invitation to do something together"], "How could you signal that you are ready to reconnect?"),
    prompt("support", 1, "After a difficult day, what kind of support would you welcome?",
           ["Listening", "Practical help", "Quiet company", "Time to myself"], "How could you ask what would help today?"),
    prompt("support", 2, "What is an ordinary thing your partner does that helps you feel appreciated?",
           ["Notices something I did", "Makes time for me", "Helps without being asked"], "Which small gesture would you like to repeat?"),
    prompt("support", 3, "When you need some space, how would you like to arrange a check-in?",
           ["Agree on a time", "Say when I feel ready", "Ask for one gentle message"], "What would make the timing feel comfortable for both of you?"),
    prompt("priorities", 1, "If you have one free evening this week, what would you like to protect time for?",
           ["Uninterrupted time together", "Time to recharge", "Something new together"], "How could you make room for both preferences?"),
    prompt("priorities", 2, "When your priorities compete, which decision would you most want to make together?",
           ["How to spend time", "How to share responsibilities", "What can wait"], "What is one decision you would like to make together this week?"),
    prompt("priorities", 3, "What is one shared responsibility you would like to make easier this month?",
           ["Planning ahead", "Day-to-day tasks", "Remembering commitments"], "What small adjustment would you each be willing to try?"),
]

JOURNEY_INDEXES = {"couples_drafts": [IndexModel([("user_id", 1), ("key", 1)], name="member_prompt", unique=True)]}
REFLECTIONS = json.loads((Path(__file__).with_name("seed") / "couples_reflections.json").read_text(encoding="utf-8"))
WITHDRAWALS = {"withdraw", "withdraw_checkin", "unpin"}
Line = Annotated[str, StringConstraints(max_length=900)]
Key = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,80}$")]
CARD_KEY = re.compile(r"card-[a-z]+-[a-f0-9]{24}")
Result = Literal["helped", "mixed", "didnt_help", "not_tried"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftIn(Input):
    lines: list[Line] = Field(max_length=3)
    choice: str | None = Field(default=None, max_length=180)
    expected_member: str | None = Field(default=None, max_length=128)
    skipped: StrictBool = False
    version: int = Field(ge=0)


class StartCard(Input):
    pair_id: Key
    origin: Literal["card", "sample"] = "card"


class Action(Input):
    pair_id: Key
    version: int = Field(ge=0)
    action: Literal["share", "withdraw", "propose", "accept", "pass", "retire",
                    "checkin", "withdraw_checkin", "pin", "accept_pin", "unpin"]
    kind: Literal["prompt", "moment", "positive"] | None = None
    prompt_id: Key | None = None
    lines: list[Line] = Field(default_factory=list, max_length=3)
    text: Line = ""
    review_on: date | None = None
    utc_offset_minutes: int = Field(default=0, ge=-720, le=840)
    agreement_id: Key | None = None
    result: Result | None = None
    note: Line = ""
    share: bool = False
    request_id: Key | None = None


def now():
    return datetime.now(timezone.utc).isoformat()


def conflict():
    raise HTTPException(409, "This conversation changed. Refresh and review the current version.")


def side_of(pair, uid):
    if pair.get("inviter") == uid:
        return "a"
    if pair.get("invitee") == uid:
        return "b"
    raise HTTPException(404, "Shared space unavailable.")


def other(side):
    return "b" if side == "a" else "a"


def current_threads(pair):
    return (pair.get("journey") or {}).get("threads", {})


def history_prefix(key):
    return "thread:" + key + ":"


def history_question(key, question=None, *, kind=None):
    """Freeze only published wording; private lines never become question metadata."""
    if question is not None:
        value = {name: deepcopy(question[name]) for name in
                 ("question", "choices", "discussion", "topic", "card") if name in question}
    elif kind in REFLECTIONS:
        value = deepcopy(REFLECTIONS[kind])
    else:
        value = {}
    return {"id": key, **value}


def thread_history_observation(thread, side, body, previous=None):
    """One actor's accepted contribution; never copy the partner's private feedback."""
    action = body.action
    key = thread["id"]
    question = {"id": key, "question": thread["title"], "discussion": thread["discussion"],
                "kind": thread["kind"]}
    after = {"action": action, "side": side, "thread_id": key}
    scope = history_prefix(key)
    if action in {"share", "withdraw"}:
        scope += "share"
        if action == "share":
            after.update(lines=deepcopy(thread["shares"].get(side, [])),
                         selected=deepcopy(thread.get("shared_choices", {}).get(side)))
        else:
            after["withdrawn"] = True
    else:
        agreement_id = body.agreement_id
        if action == "propose":
            old_ids = {a["id"] for a in (previous or {}).get("agreements", [])}
            new_ids = [a["id"] for a in thread["agreements"] if a["id"] not in old_ids]
            if new_ids:
                agreement_id = new_ids[-1]
        agreement = agreement_for(thread, agreement_id)
        after.update(agreement_id=agreement_id, agreement_revision=agreement["revision"])
        if action in {"checkin", "withdraw_checkin"}:
            scope += "checkin:" + agreement_id
            if action == "checkin":
                after["checkin"] = deepcopy(agreement["checkins"][side])
            else:
                after["withdrawn"] = True
        elif action in {"pin", "accept_pin", "unpin"}:
            scope += "learning:" + agreement_id
            if action == "pin":
                after["text"] = body.text.strip()
            elif action == "unpin":
                after["withdrawn"] = True
            else:
                after["accepted_by_me"] = side in (agreement.get("pin") or {}).get("accepted", [])
        else:
            scope += "agreement:" + agreement_id
            after.update(status=agreement["status"], accepted_by_me=side in agreement["accepted"])
            if action == "propose":
                after.update(text=agreement["text"], review_on=agreement["review_on"])
    return scope, question, after


def available_cards(pair, uid):
    if not pair:
        return {}
    side = side_of(pair, uid)
    threads = current_threads(pair)
    prompts = {}
    for run in (pair.get("journey") or {}).get("cards", {}).values():
        question = run["prompt"]
        key = question["id"]
        if side in run.get("opened", []) or threads.get(key, {}).get("shares"):
            prompts[key] = question
    return prompts


async def start_card(db, member, card_id, body):
    for _ in range(5):
        pair = await active_pair(db, member, write=True)
        if pair["_id"] != body.pair_id:
            conflict()
        side = side_of(pair, member["user_id"])
        run = (pair.get("journey") or {}).get("cards", {}).get(card_id)
        if not run and card_id not in CARDS:
            raise HTTPException(404, "Card unavailable.")
        if run and side in run["opened"]:
            return {"key": run["prompt"]["id"]}
        path = "journey.cards." + card_id
        if run:
            patch = {"$addToSet": {path + ".opened": side}}
            question = run["prompt"]
        else:
            question = card_prompt(CARDS[card_id], pair["_id"])
            patch = {"$set": {path: {"prompt": question, "opened": [side]},
                f"stats.cards.{card_id}": {"started_at": now(), "origin": body.origin}}}
        result = await db.couples_pairs.update_one({"_id": pair["_id"], "state": "active", "members": member["user_id"],
            "journey_version": pair.get("journey_version")}, {**patch, "$inc": {"journey_version": 1}})
        if result.matched_count:
            return {"key": question["id"]}
    conflict()


def words_version(thread):
    # Earlier releases discarded all tone reads on every thread edit, so their
    # surviving reads were always made against the current thread version.
    return thread.get("words_version", thread["version"])


def public_agreement(agreement, side):
    peer = other(side)
    checks = agreement.get("checkins", {})
    own_check = checks.get(side)
    peer_check = checks.get(peer)
    pin = agreement.get("pin")
    accepts = agreement.get("accepted", [])
    return {
        "id": agreement["id"], "revision": agreement["revision"], "text": agreement["text"],
        "review_on": agreement["review_on"], "status": agreement["status"],
        "accepted_by_me": side in accepts, "accepted_by_partner": peer in accepts,
        "my_checkin": {k: own_check[k] for k in ("result", "note", "shared")} if own_check else None,
        "partner_checkin": {k: peer_check[k] for k in ("result", "note")}
        if peer_check and peer_check["shared"] and own_check and own_check["shared"] else None,
        "pin": {"text": pin["text"], "accepted_by_me": side in pin["accepted"],
                "accepted_by_partner": peer in pin["accepted"], "active": len(pin["accepted"]) == 2} if pin else None,
    }


def public_thread(thread, side):
    peer = other(side)
    shares = thread["shares"]
    mutual = bool(shares.get(side)) and bool(shares.get(peer))
    return {
        "id": thread["id"], "kind": thread["kind"], "prompt_id": thread["prompt_id"],
        "title": thread["title"], "discussion": thread["discussion"], "version": thread["version"],
        **({"card": thread["card"], "example_agreement": thread["example_agreement"]} if thread.get("card") else {}),
        "words_version": words_version(thread),
        "my_share": shares.get(side, []), "partner_shared": bool(shares.get(peer)), "mutual": mutual,
        "perspectives": [{"mine": author == side, "lines": shares[author]} for author in (side, peer)] if mutual else [],
        "agreements": [public_agreement(ag, side) for ag in thread["agreements"]] if mutual else [],
    }


def visible_threads(pair, uid):
    if not pair:
        return []
    side = side_of(pair, uid)
    return [public_thread(thread, side) for thread in current_threads(pair).values() if thread.get("shares")]


async def own_journey_export(db, uid):
    owner = await db.users.find_one({"user_id": uid}, {"env": 1})
    if not owner:
        return []
    pair = await db.couples_pairs.find_one({"members": uid, "state": "active", "env": owner.get("env")})
    if not pair:
        return []
    side = side_of(pair, uid)
    contributions = []
    for thread in current_threads(pair).values():
        if side not in thread["shares"]:
            continue
        item = {"prompt": thread["title"], "own_share": thread["shares"][side], "contributions": []}
        coach = ((pair.get("tone_sessions") or {}).get(thread["id"]) or {}).get(side)
        if coach and coach.get("source_version") == words_version(thread):
            item["my_tone_coaching"] = {
                "intent": coach["tone"].get("intent"), "status": coach["tone"]["status"],
                "incoming": coach["tone"].get("incoming"), "feedback": coach.get("tone_feedback", {}),
            }
        for agreement in thread["agreements"]:
            entry = {}
            if agreement["author"] == side:
                entry.update(text=agreement["text"], review_on=agreement["review_on"])
            if side in agreement.get("checkins", {}):
                entry["own_checkin"] = agreement["checkins"][side]
            if (agreement.get("pin") or {}).get("author") == side:
                entry["own_learning"] = agreement["pin"]["text"]
            if entry:
                item["contributions"].append(entry)
        contributions.append(item)
    return contributions


def moment_kind(key):
    for kind in REFLECTIONS:
        if re.fullmatch(kind + r"-[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", key):
            return kind
    return None


async def member_pair(db, member, *, write=False, allow_intro_read=False):
    try:
        return await active_pair(db, member, write=write, allow_intro_read=allow_intro_read)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return None


async def catalog(db, retained=()):
    return await read_catalog(db, PROMPTS, retained)


async def view(db, member, *, after_delete=False):
    require_adult(member)
    uid = member["user_id"]
    rows = await db.couples_drafts.find({"user_id": uid}, {"_id": 0, "key": 1, "lines": 1, "choice": 1, "skipped": 1, "version": 1, "card_prompt": 1}).to_list(None)
    drafts = [row for row in rows if row.get("lines") or row.get("skipped")]
    draft_versions = {row["key"]: row["version"] for row in rows}
    for _attempt in range(3):
        pair = await member_pair(db, member)
        if not pair and not has_personal_access(member):
            if after_delete:
                return {"pair_id": None, "prompts": [], "drafts": [], "threads": [], "share_versions": {}, "draft_versions": {}}
            raise subscription_required()
        threads = visible_threads(pair, uid)
        retained = {d["key"] for d in drafts} | {t["prompt_id"] for t in threads if t["prompt_id"]}
        published, by_id = await catalog(db, (key for key in retained if not CARD_KEY.fullmatch(key)))
        active_ids = {row["id"] for row in published}
        prompts = [*published, *(dict(by_id[k], retired=True) for k in sorted(retained - active_ids) if k in by_id)]
        owned_cards = {d["key"]: dict(d["card_prompt"], retired=True) for d in drafts if d.get("card_prompt")}
        prompts.extend({**owned_cards, **available_cards(pair, uid)}.values())
        stored_threads = current_threads(pair or {})
        share_versions = {d["key"]: stored_threads[d["key"]]["version"] for d in drafts if d["key"] in stored_threads}
        # An unshare or unpair may have committed while the catalogue was loading.
        if not pair or await db.couples_pairs.find_one({"_id": pair["_id"], "members": uid, "state": "active",
                                                        "journey_version": pair.get("journey_version")}, {"_id": 1}):
            return {"pair_id": pair["_id"] if pair else None, "prompts": prompts, "drafts": drafts,
                    "threads": threads, "share_versions": share_versions, "draft_versions": draft_versions}
    conflict()


async def save_draft(db, member, key, body):
    if body.expected_member and body.expected_member != member["user_id"]:
        raise HTTPException(409, "Your signed-in account changed. Open your own questionnaire again.")
    require_adult(member)
    if not has_personal_access(member) and not await member_pair(db, member, write=True):
        raise subscription_required()
    uid = member["user_id"]
    lookup = {"user_id": uid, "key": key}
    await answer_history.flush(db, "couples_drafts", lookup)
    lines = [line.strip() for line in body.lines]
    if body.skipped and (any(lines) or body.choice is not None):
        raise HTTPException(422, "Clear the answer before skipping this question.")
    if not body.skipped and not any(lines):
        raise HTTPException(422, "Write one line to save an answer.")
    options = []
    card = None
    question = None
    if CARD_KEY.fullmatch(key):
        pair = await member_pair(db, member, write=True)
        card = available_cards(pair, uid).get(key)
        if not card:
            old = await db.couples_drafts.find_one({**lookup, "deleted": {"$ne": True}}, {"card_prompt": 1})
            card = (old or {}).get("card_prompt")
        if not card:
            raise HTTPException(404, "Open this card in your current shared space first.")
        question = card
    elif not moment_kind(key):
        published, by_id = await catalog(db, [key])
        if key not in {row["id"] for row in published}:
            pair = await member_pair(db, member)
            existing_thread = current_threads(pair or {}).get(key, {})
            if key not in by_id or not (existing_thread.get("shares") or await db.couples_drafts.find_one(
                    {**lookup, "deleted": {"$ne": True}}, {"_id": 1})):
                raise HTTPException(404, "Question unavailable.")
        options = by_id[key]["choices"]
        question = by_id[key]
    selection = body.choice
    if selection is not None and selection not in options:
        raise HTTPException(422, "Choose one of the published options for this question.")
    fields = {"lines": [] if body.skipped else lines, "choice": selection, "skipped": body.skipped}
    if card:
        fields["card_prompt"] = card
    timestamp = now()
    previous = await db.couples_drafts.find_one(lookup) or {}
    if previous and (body.version == 0 or previous.get("version") != body.version):
        if previous.get("version") == body.version + 1 and all(previous.get(k) == v for k, v in fields.items()):
            await answer_history.flush(db, "couples_drafts", lookup)
            return
        conflict()
    if not previous and body.version:
        conflict()
    pair_context = await db.couples_pairs.find_one(
        {"state": "active", "members": uid, "env": member["env"]}, {"inviter": 1, "invitee": 1})
    wording = history_question(key, question, kind=moment_kind(key))
    observation = answer_history.event(source="couples_drafts", source_id=uid + ":" + key,
        owner_id=uid, actor_id=uid, domain="couples", question_id=key,
        question=wording, signature=fingerprint(wording),
        after={**fields, "pair_side": side_of(pair_context, uid) if pair_context else None},
        demographics=await answer_context.for_member(db, member), consent=answer_history.consent(member),
        revision=body.version + 1, attempt_id="draft:" + key + ":" + str(body.version + 1),
        pair_id=pair_context["_id"] if pair_context else None,
        visibility="private", at=timestamp, kind="answer_skipped" if body.skipped else "answer_changed")
    if body.version == 0:
        journal = answer_history.append({}, observation)
        document = {**lookup, **fields, "version": 1, "updated_at": timestamp,
                    **journal["$set"], answer_history.REVISION: journal["$inc"][answer_history.REVISION],
                    answer_history.PENDING: journal["$push"][answer_history.PENDING]["$each"]}
        try:
            await db.couples_drafts.insert_one(document)
        except DuplicateKeyError:
            if not await db.couples_drafts.find_one({**lookup, **fields, "version": 1}, {"_id": 1}):
                conflict()
    else:
        update = {"$set": {**fields, "updated_at": timestamp},
                  "$unset": {"deleted": "", "tone_coach": "", "tone_job": ""}, "$inc": {"version": 1}}
        answer_history.append(update, observation, previous)
        result = await db.couples_drafts.update_one(
            answer_history.guard({**lookup, "version": body.version}, previous), update)
        if not result.matched_count and not await db.couples_drafts.find_one(
                {**lookup, **fields, "version": body.version + 1}, {"_id": 1}):
            conflict()
    await answer_history.flush(db, "couples_drafts", lookup)
    await db.users.update_one({"user_id": uid, "couples_first_draft_at": None}, {"$set": {"couples_first_draft_at": timestamp}})


def new_thread(key, kind, question):
    return {"id": key, "kind": kind, "prompt_id": question["id"] if question else None,
            **({"card": question["card"], "example_agreement": question["example_agreement"]} if question and question.get("card") else {}),
            "title": question["question"] if question else REFLECTIONS[kind]["title"],
            "discussion": question["discussion"] if question else REFLECTIONS[kind]["discussion"],
            "version": 0, "shares": {}, "agreements": []}


def agreement_for(thread, aid):
    for ag in thread["agreements"]:
        if ag["id"] == aid:
            return ag
    raise HTTPException(404, "Agreement unavailable.")


def require_mutual(thread):
    if len(thread["shares"]) != 2:
        raise HTTPException(409, "Both partners must share their words first.")


def accepted(agreement):
    if agreement["status"] not in {"active", "retired"} or len(agreement["accepted"]) != 2:
        raise HTTPException(409, "Both partners must accept this agreement first.")


def record_acceptance(stats, thread, agreement, timestamp):
    if not stats.get("first_agreement_id"):
        stats.update(first_agreement_id=agreement["id"], first_agreement_origin=agreement["id"],
                     first_agreement_thread_id=thread["id"], first_agreement_at=timestamp)
    origin = agreement.get("origin_id", agreement["id"])
    if stats.get("first_agreement_origin", stats["first_agreement_id"]) == origin and not first_joint_checkin(stats):
        stats.update(first_agreement_id=agreement["id"], first_agreement_revision=agreement["revision"],
                     first_review_date=agreement["review_on"], first_checkins={}, first_both_checked_in_at=None,
                     first_due_visits={}, first_due_tracking=True)
        stats.pop("current_outcomes", None)


def update_outcome_stats(stats, agreement, timestamp):
    if stats.get("first_agreement_id") != agreement["id"] or stats.get("first_agreement_revision") != agreement["revision"]:
        return
    # Keep the first actual reveal even if a partner later withdraws their words.
    joint = first_joint_checkin(stats)
    first = stats.setdefault("first_checkins", {})
    current = {}
    for author, check in agreement.get("checkins", {}).items():
        if check["shared"]:
            first.setdefault(author, timestamp)
            current[author] = check["result"]
    if len(current) == 2:
        joint = joint or timestamp
        stats["current_outcomes"] = current
    else:
        stats.pop("current_outcomes", None)
    stats["first_both_checked_in_at"] = joint


def apply_action(thread, side, body, stats, timestamp):
    action = body.action
    shares = thread["shares"]
    if action in {"share", "withdraw"}:
        previous = shares.get(side)
        if action == "withdraw":
            shares.pop(side, None)
            thread.pop("mutual_at", None)
        else:
            shares[side] = body.lines
            thread.setdefault("first_share_at", timestamp)
            stats.setdefault("first_share_at", timestamp)
            if len(shares) == 2 and not previous:
                thread["mutual_at"] = timestamp
                stats.setdefault("first_mutual_at", timestamp)
                stats.setdefault("first_thread_id", thread["id"])
                joint = checkin_time(first_joint_checkin(stats))
                shared = checkin_time(thread["first_share_at"])
                if joint and shared and shared > joint and thread["id"] != stats.get("first_agreement_thread_id"):
                    stats.setdefault("second_mutual_at", timestamp)
        if previous and previous != shares.get(side):
            if any(ag["id"] == stats.get("first_agreement_id") for ag in thread["agreements"]):
                stats.pop("current_outcomes", None)
            thread["agreements"] = []
        return
    require_mutual(thread)
    if action == "propose":
        text = body.text.strip()
        local_zone = timezone(timedelta(minutes=body.utc_offset_minutes))
        if not text or not body.review_on or body.review_on < datetime.fromisoformat(timestamp).astimezone(local_zone).date():
            raise HTTPException(422, "Give the agreement words and a review date of today or later.")
        if body.agreement_id:
            ag = agreement_for(thread, body.agreement_id)
            if ag["status"] == "retired":
                raise HTTPException(409, "Start a new agreement to revisit a retired one.")
            if ag["status"] == "active":
                # Preserve the accepted version and each member’s feedback when
                # proposing an adjustment, including privately saved check-ins.
                ag["status"] = "retired"
                ag = {"id": str(uuid.uuid4()), "revision": ag["revision"] + 1, "origin_id": ag.get("origin_id", ag["id"])}
                thread["agreements"].append(ag)
            else:
                ag["revision"] += 1
        else:
            ag = {"id": str(uuid.uuid4()), "revision": 1}
            thread["agreements"].append(ag)
        ag.update(text=text, review_on=body.review_on.isoformat(), accepted=[side], status="proposed",
                  author=side, checkins={}, pin=None)
        return
    ag = agreement_for(thread, body.agreement_id)
    if action in {"accept", "pass"}:
        if ag["status"] != "proposed":
            raise HTTPException(409, "This proposal is no longer awaiting a response.")
        if action == "pass":
            ag.update(status="passed", accepted=[])
        elif side not in ag["accepted"]:
            ag["accepted"].append(side)
            if len(ag["accepted"]) == 2:
                ag["status"] = "active"
                ag["accepted_at"] = timestamp
                record_acceptance(stats, thread, ag, timestamp)
    elif action == "retire":
        ag["status"] = "retired"
    elif action in {"checkin", "withdraw_checkin"}:
        previous_check = ag["checkins"].get(side)
        if action == "withdraw_checkin":
            ag["checkins"].pop(side, None)
        else:
            accepted(ag)
            if not body.result:
                raise HTTPException(422, "Choose a follow-up result.")
            next_check = {"result": body.result, "note": body.note.strip(), "shared": body.share}
            if not previous_check or any(previous_check.get(k) != v for k, v in next_check.items()):
                ag["checkins"][side] = {**next_check, "updated_at": timestamp}
        if previous_check != ag["checkins"].get(side):
            ag["pin"] = None
        update_outcome_stats(stats, ag, timestamp)
    elif action == "unpin":
        ag["pin"] = None
    elif action in {"pin", "accept_pin"}:
        if not all((ag["checkins"].get(s) or {}).get("shared") for s in ("a", "b")):
            raise HTTPException(409, "Both partners must share a check-in before saving a learning.")
        if action == "pin":
            if not body.text.strip():
                raise HTTPException(422, "Write a sentence to save together.")
            ag["pin"] = {"text": body.text.strip(), "author": side, "accepted": [side]}
        elif not ag.get("pin"):
            raise HTTPException(409, "Propose a learning first.")
        elif side not in ag["pin"]["accepted"]:
            ag["pin"]["accepted"].append(side)


async def change_thread(db, member, key, body):
    uid = member["user_id"]
    digest = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    await answer_history.flush(db, "couples_pairs", {"_id": body.pair_id, "members": uid, "env": member["env"]})
    for _attempt in range(5):
        pair = await active_pair(db, member, write=body.action not in WITHDRAWALS)
        if pair["_id"] != body.pair_id:
            conflict()
        side = side_of(pair, uid)
        thread = current_threads(pair).get(key)
        previous_version = thread["version"] if thread else 0
        last = (thread or {}).get("last_request", {}).get(side, {})
        if body.request_id and last.get("id") == body.request_id:
            if last["digest"] != digest:
                conflict()
            await answer_history.flush(db, "couples_pairs", {"_id": pair["_id"]}, strict=body.action in WITHDRAWALS)
            return
        if previous_version != body.version:
            conflict()
        shared_choice = None
        if body.action == "share":
            question = None
            draft = await db.couples_drafts.find_one({"user_id": uid, "key": key, "deleted": {"$ne": True}}, {"lines": 1, "choice": 1})
            if not draft or not body.lines or any(not line.strip() or line not in draft["lines"] for line in body.lines):
                raise HTTPException(409, "Save and preview your selected words again before sharing.")
            if thread is None:
                if CARD_KEY.fullmatch(key):
                    question = available_cards(pair, uid).get(key)
                    if not question:
                        raise HTTPException(404, "Open this card in your current shared space first.")
                else:
                    _, by_id = await catalog(db, [key])
                    question = by_id.get(key)
                kind = "prompt" if question else moment_kind(key)
                if not kind or body.kind != kind or (kind == "prompt" and body.prompt_id and body.prompt_id != key):
                    raise HTTPException(422, "Choose a published question or a valid new moment.")
                thread = new_thread(key, kind, question)
            if thread["kind"] == "prompt" and draft.get("choice") in body.lines:
                # A typed phrase can match a published label. Retain a code only when the
                # member explicitly selected that option and released the matching line.
                if question is None:
                    _, by_id = await catalog(db, [key])
                    question = by_id.get(key)
                if question and draft["choice"] in question["choices"]:
                    shared_choice = {"index": question["choices"].index(draft["choice"]),
                                     "edition": fingerprint(["prompt:" + key, question["question"], question["choices"]])}
        if thread is None:
            raise HTTPException(404, "Conversation unavailable.")
        previous_thread = deepcopy(thread)
        previous_shares = thread["shares"]
        previous_words_version = words_version(thread)
        thread = deepcopy(thread)
        stats = deepcopy(pair.get("stats", {}))
        timestamp = now()
        apply_action(thread, side, body, stats, timestamp)
        if body.action in {"share", "withdraw"}:
            if shared_choice is None:
                thread.get("shared_choices", {}).pop(side, None)
            else:
                thread.setdefault("shared_choices", {})[side] = {**shared_choice, "at": timestamp}
        thread["version"] += 1
        shared_words_changed = previous_shares != thread["shares"]
        thread["words_version"] = thread["version"] if shared_words_changed else previous_words_version
        if body.request_id:
            thread.setdefault("last_request", {})[side] = {"id": body.request_id, "digest": digest}
        pair_version = pair.get("journey_version")
        # Coach usage timestamps can advance independently. Update only the journey stats this action changed.
        before = pair.get("stats", {})
        changed = {f"stats.{field}": value for field, value in stats.items() if before.get(field) != value}
        card = thread.get("card")
        if card:
            recorded = before.get("cards", {}).get(card, {})
            agreements = thread.get("agreements", [])
            stages = {"mutual_at": len(thread["shares"]) == 2,
                      "agreed_at": any(len(ag["accepted"]) == 2 for ag in agreements),
                      "reviewed_at": any(all(ag["checkins"].get(s, {}).get("shared") for s in ("a", "b")) for ag in agreements)}
            changed.update({f"stats.cards.{card}.{stage}": timestamp for stage, reached in stages.items()
                            if reached and not recorded.get(stage)})
        removed = {f"stats.{field}": "" for field in before if field not in stats}
        if shared_words_changed:
            removed.update({f"tone_sessions.{key}": "", f"tone_jobs.{key}": ""})
        update = {"$set": {f"journey.threads.{key}": thread, **changed},
                  **({"$unset": removed} if removed else {}), "$inc": {"journey_version": 1}}
        if body.action == "withdraw":
            answer_history.redact(update, pair, question_prefix=history_prefix(key))
        elif body.action == "withdraw_checkin":
            answer_history.redact(update, pair, question_id=history_prefix(key) + "checkin:" + body.agreement_id, actor_id=uid)
            answer_history.redact(update, pair, question_id=history_prefix(key) + "learning:" + body.agreement_id)
        elif body.action == "unpin":
            answer_history.redact(update, pair, question_id=history_prefix(key) + "learning:" + body.agreement_id)
        history_key, wording, after = thread_history_observation(thread, side, body, previous_thread)
        observation = answer_history.event(source="couples_pairs", source_id=pair["_id"],
            owner_id=uid, actor_id=uid, domain="couples", question_id=history_key, question=wording,
            signature=fingerprint(wording), after=after,
            demographics=await answer_context.for_member(db, member), consent=answer_history.consent(member),
            revision=thread["version"], attempt_id="thread:" + pair["_id"] + ":" + key + ":" + str(thread["version"]),
            pair_id=pair["_id"], visibility="private", kind="couples_" + body.action, at=timestamp)
        answer_history.append(update, observation, pair)
        try:
            result = await db.couples_pairs.update_one(answer_history.guard(
                {"_id": pair["_id"], "state": "active", "members": uid, "journey_version": pair_version}, pair), update)
        except DocumentTooLarge:
            raise HTTPException(413, "This space is full. Withdraw an older shared conversation to make room.") from None
        if result.matched_count:
            await answer_history.flush(db, "couples_pairs", {"_id": pair["_id"]}, strict=body.action in WITHDRAWALS)
            return
    conflict()


def journey_router(get_db, current_member, generate=None):
    def no_store(response: Response):
        response.headers["Cache-Control"] = "private, no-store"

    router = APIRouter(prefix="/couples", dependencies=[Depends(no_store)])

    @router.post("/cards/{card_id}/start")
    async def open_card(card_id: str, body: StartCard, member: dict = Depends(current_member)):
        return await start_card(get_db(), member, card_id, body)

    @router.post("/threads/{key}/review-visit/{agreement_id}/{revision}")
    async def review_visit(key: Key, agreement_id: Key, revision: int, member: dict = Depends(current_member)):
        db = get_db()
        pair = await active_pair(db, member, write=True)
        thread = current_threads(pair).get(key, {})
        ag = next((a for a in thread.get("agreements", []) if a["id"] == agreement_id), None)
        stats = pair.get("stats") or {}
        stamp = now()
        if (stats.get("first_due_tracking") and stats.get("first_agreement_id") == agreement_id
                and stats.get("first_agreement_revision") == revision and ag and ag["status"] == "active"
                and ag["revision"] == revision and all(thread.get("shares", {}).get(s) for s in ("a", "b"))):
            due = date.fromisoformat(ag["review_on"])
            if due <= datetime.fromisoformat(stamp).date() <= due + timedelta(days=7):
                eligible = {"user_id": {"$in": pair["members"]}, "env": pair["env"], "closing": {"$ne": True},
                            "couples_preferences.review_return_opt_in": True,
                            "couples_preferences.review_return_since": {"$type":"string", "$lte": stats.get("first_agreement_at","")}}
                if await db.users.count_documents(eligible, limit=2) != 2:
                    return {"ok": True}
                side = side_of(pair, member["user_id"])
                path = f"stats.first_due_visits.{side}"
                await db.couples_pairs.update_one({"_id": pair["_id"], "state": "active", "journey_version": pair.get("journey_version"),
                    "stats.first_agreement_id": agreement_id, "stats.first_agreement_revision": revision, path: None},
                    {"$set": {path: stamp}})
        return {"ok": True}

    @router.get("/journey")
    async def get_journey(member: dict = Depends(current_member)):
        return await view(get_db(), member)

    @router.put("/drafts/{key}")
    async def put_draft(key: Key, body: DraftIn, member: dict = Depends(current_member)):
        db = get_db()
        await save_draft(db, member, key, body)
        return await view(db, member)

    @router.delete("/drafts/{key}")
    async def delete_draft(key: Key, version: int, member: dict = Depends(current_member)):
        require_adult(member)
        db = get_db()
        lookup = {"user_id": member["user_id"], "key": key}
        previous = await db.couples_drafts.find_one(lookup) or {}
        # Erase every word and retain the owner-only version so a delayed save
        # from another tab cannot restore words the member already removed.
        update = {"$unset": {"lines": "", "choice": "", "skipped": "", "updated_at": "", "tone_coach": "", "tone_job": "", "card_prompt": ""},
                  "$set": {"deleted": True}, "$inc": {"version": 1}}
        answer_history.redact(update, previous)
        result = await db.couples_drafts.update_one(
            answer_history.guard({**lookup, "version": version, "deleted": {"$ne": True}}, previous), update)
        if not result.matched_count and await db.couples_drafts.find_one({**lookup, "deleted": {"$ne": True}}, {"_id": 1}):
            conflict()
        await answer_history.flush(db, "couples_drafts", lookup, strict=True)
        return await view(db, member, after_delete=True)

    @router.post("/threads/{key}")
    async def mutate_thread(key: Key, body: Action, member: dict = Depends(current_member)):
        db = get_db()
        await change_thread(db, member, key, body)
        return await view(db, member)

    if generate is not None:
        try:
            from .couples_tone import tone_router
        except ImportError:
            from couples_tone import tone_router
        def allow_coaching_source(key: Key):
            if CARD_KEY.fullmatch(key):
                raise HTTPException(422, "Conversation cards use your own words without AI coaching.")

        router.include_router(tone_router(get_db, current_member, generate), dependencies=[Depends(allow_coaching_source)])
    return router
