"""One explicit notice and atomic owner-only optional data-permission choices.

Current-partner sharing remains a separately confirmed pair-document phase.
This module never submits feedback, copies partner answers or enrolls matching.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

try:
    from . import admin_answer_history, answer_history, couples_compatibility, individual_history, personal_demographics, research_notices, research_rows, your_type_research
    from .couples import active_pair, MEMBER_FIELDS
except ImportError:
    import admin_answer_history, answer_history, couples_compatibility, individual_history, personal_demographics, research_notices, research_rows, your_type_research
    from couples import active_pair, MEMBER_FIELDS

VERSION = "main-consent-v1"
FIELD = "main_consent_receipt"
SCOPES = ("questionnaire", "your_type", "tone", "member_csv", "couple_csv", "staff_history", "trends", "couples_statistics", "couples_return")
VERSIONS = {"questionnaire": personal_demographics.CONSENT_VERSION, "your_type": your_type_research.VERSION,
    "tone": personal_demographics.TONE_CONSENT, "member_csv": research_rows.VERSIONS["member"],
    "couple_csv": research_rows.VERSIONS["couple"], "staff_history": admin_answer_history.VERSION,
    "trends": "everyday-trends-v1", "couples_statistics": "couples-statistics-v1", "couples_return": "couples-return-v1"}
NOTICE = (
    "Imladri saves the profile details, personality and Sex & Intimacy answers, Your Type preferences, "
    "unfinished answers, conversations and answer history you choose to provide. Relevant saved profile "
    "and conversation details can be sent to DeepSeek to personalize the advice you request; screenshot "
    "processing uses the preview you choose to send. Optional research and staff access are separate "
    "choices below. Questionnaire research can compare past and future structured answers, including "
    "Sex & Intimacy and volunteered age, height, gender, sex, ethnicity and education, with age and "
    "height grouped into ranges in reports. Your Type research includes image choices, visual "
    "features, sexuality, viewer gender and rated audience recorded when an attempt starts; demographic "
    "comparisons also require questionnaire research. Tone research uses new coded estimates and "
    "fixed-choice feedback, not message wording. Individual and current-Couples CSV permissions allow "
    "staff downloads of coded structured records; written answers and conversations are excluded. "
    "Staff answer-history access can include written answers and private Couples drafts recorded from "
    "that grant onward, subject to the other required permissions. Everyday statistics use selected "
    "fixed-choice answers and follow-up outcomes. Couples statistics can include your coded activity "
    "and outcomes; optional return tracking counts eligible visits after you opt in, without pretending "
    "past agreements were newly enrolled. Optional refusal does not prevent unrelated app use. "
    "Change each choice later in Settings; earlier staff downloads may still exist. You cannot consent "
    "for your partner. Sharing assessments with a current partner requires its own specific choice, "
    "does not apply to future pairs, and excludes Your Type and private notes. No research feedback "
    "submission or future matchmaking enrollment is created by this screen."
)
NOTICE_SHA = hashlib.sha256(NOTICE.encode()).hexdigest()
PURPOSES = {
    "questionnaire": "Group comparisons of structured Individual, Sex & Intimacy and Couples answers and volunteered age/height ranges, gender, sex, ethnicity and education; written answers stay outside the group report.",
    "your_type": "Research on image choices and authored visual features, including explicitly supplied sexuality/viewer-gender/audience metadata; only eligible attempts begun after permission qualify.",
    "tone": "Group comparisons of new tone estimates, reply styles and optional fixed-choice feedback; wording, drafts and written corrections stay private.",
    "member_csv": "Staff coded CSV downloads of structured Individual and intimacy answers, conditions, skips and profile categories; separately enabled Your Type/tone data remain subject to their own permissions.",
    "couple_csv": "Staff coded downloads for the current pair only, requiring both members; no permission for another or future pair.",
    "staff_history": "Staff review/download of retained answers and changes recorded from this grant onward, potentially including written answers/private Couples drafts, only with all other required grants.",
    "trends": "Group statistics from three everyday choices and selected follow-up outcomes; small samples are hidden.",
    "couples_statistics": "Group Couples statistics about coded activity and shared outcomes; private messages and drafts are not exposed.",
    "couples_return": "Prospective Couples return measurements beginning when permission is enabled; old agreements are not newly enrolled.",
}
RAW_FIELDS = {
    "questionnaire": tuple("questionnaire_research_" + k for k in ("version", "revision", "at")),
    "your_type": tuple("your_type_research_" + k for k in ("version", "revision", "at")),
    "tone": tuple("tone_research_" + k for k in ("version", "revision", "at")),
    "member_csv": tuple("row_member_" + k for k in ("version", "revision", "at", "env", "code")),
    "couple_csv": (*tuple("row_couple_" + k for k in ("version", "revision", "at", "env", "code")), "row_couple_pair_id"),
    "staff_history": admin_answer_history.GRANT_FIELDS,
    "trends": ("everyday_trends_opt_in", "everyday_trends_revision"),
    "couples_statistics": ("couples_preferences.analytics_opt_in", "couples_statistics_revision", "couples_adult_at"),
    "couples_return": ("couples_preferences.review_return_opt_in", "couples_preferences.review_return_since", "couples_return_revision", "couples_adult_at"),
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: str = Field(min_length=1, max_length=80)
    revision: int = Field(ge=0, lt=2**53)
    enabled: StrictBool
    state_sha256: str = Field(pattern="^[0-9a-f]{64}$")


class Choices(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    questionnaire: Choice
    your_type: Choice
    tone: Choice
    member_csv: Choice
    couple_csv: Choice
    staff_history: Choice
    trends: Choice
    couples_statistics: Choice
    couples_return: Choice


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_member: str = Field(min_length=1, max_length=256)
    notice_version: str = Field(min_length=1, max_length=80)
    notice_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    revision: int = Field(ge=0, lt=2**53)
    expected_pair_id: str | None = Field(default=None, min_length=1, max_length=128)
    request_id: str
    action: Literal["agree_all", "save_choices"]
    adult_confirmed: StrictBool = False
    choices: Choices

    @field_validator("request_id")
    @classmethod
    def uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("Use a canonical request UUID")
        return value


class PartnerRevocation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_member: str = Field(min_length=1, max_length=256)
    pair_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0, lt=2**53 - 1)
    notice_version: Literal["couples-assessment-sharing-v1"]


def conflict():
    return HTTPException(409, "Your account, notice, permissions or current pair changed. Check the saved choices before continuing.")


def raw_fields(member, scope):
    missing = object()
    result = {}
    for key in RAW_FIELDS[scope]:
        value = individual_history.get(member, key, missing)
        result[key] = {"present": value is not missing, "value": None if value is missing else value}
    return result


def permission(member, scope, env, pair_id):
    if scope == "questionnaire":
        pref = personal_demographics.preference(member)
    elif scope == "tone":
        pref = personal_demographics.preference(member, "tone_research")
    elif scope == "your_type":
        pref = your_type_research.preferences(member)
    elif scope in ("member_csv", "couple_csv"):
        pref = research_rows.preference(member, "member" if scope == "member_csv" else "couple", env, pair_id)
    elif scope == "staff_history":
        pref = admin_answer_history.preferences(member, env)
    elif scope == "trends":
        pref = {"version": "everyday-trends-v1", "revision": member.get("everyday_trends_revision", 0),
                "enabled": member.get("everyday_trends_opt_in") is True}
    else:
        field = "analytics_opt_in" if scope == "couples_statistics" else "review_return_opt_in"
        pref = {"version": "couples-statistics-v1" if scope == "couples_statistics" else "couples-return-v1",
                "revision": member.get(scope + "_revision", 0),
                "enabled": (member.get("couples_preferences") or {}).get(field) is True}
    revision = pref.get("revision")
    if type(revision) is not int or not 0 <= revision < 2**53:
        raise HTTPException(503, "A saved data permission needs reconciliation.")
    raw = raw_fields(member, scope)
    return {"version": pref["version"], "revision": revision, "enabled": pref["enabled"] is True,
            "state_sha256": digest(raw)}


def scopes_snapshot(member, env, pair_id):
    adult = research_notices.eligible(member)
    result = {}
    for scope in SCOPES:
        available = adult
        reason = None if adult else "Adult member eligibility is required for optional data permissions."
        if scope == "staff_history" and not admin_answer_history._adult(member):
            available, reason = False, "A saved profile age of 18 or older is required before enabling staff answer history."
        if scope == "couple_csv" and pair_id is None:
            available, reason = False, "This permission is available only for your current active pair."
        if scope in ("couples_statistics", "couples_return") and not couples_compatibility.is_adult(member):
            available, reason = False, "Current Couples adult confirmation is required before enabling this preference."
        result[scope] = {"eligible": available, "unavailable_reason": reason,
                         "purpose": PURPOSES[scope], "permission": permission(member, scope, env, pair_id)}
    return result


def receipt(member):
    value = member.get(FIELD)
    return value if isinstance(value, dict) else {}


def reviewed(member):
    row = receipt(member)
    def safe_revision(value):
        return type(value) is int and 0 <= value < 2**53
    def hex_sha(value):
        return type(value) is str and bool(re.fullmatch("[0-9a-f]{64}", value))
    try:
        request_valid = type(row.get("request_id")) is str and str(UUID(row["request_id"])) == row["request_id"]
    except (ValueError, TypeError, AttributeError):
        request_valid = False
    choices = row.get("choices")
    states = row.get("permission_states_after")
    if not (isinstance(choices, dict) and set(choices) == set(SCOPES)
            and isinstance(states, dict) and set(states) == set(SCOPES) and all(hex_sha(value) for value in states.values())):
        return False
    for scope, choice in choices.items():
        if (not isinstance(choice, dict) or set(choice) != {"version", "enabled", "decision", "permission_revision_before", "permission_revision_after"}
                or choice["version"] != VERSIONS[scope] or type(choice["enabled"]) is not bool
                or choice["decision"] not in ("allow", "decline", "unavailable")
                or choice["enabled"] != (choice["decision"] == "allow")
                or not all(safe_revision(choice[key]) for key in ("permission_revision_before", "permission_revision_after"))
                or not choice["permission_revision_before"] <= choice["permission_revision_after"] <= choice["permission_revision_before"] + 1):
            return False
    return (row.get("notice_version") == VERSION and row.get("notice_sha256") == NOTICE_SHA and row.get("notice_text") == NOTICE
            and safe_revision(row.get("revision")) and row["revision"] > 0 and request_valid and hex_sha(row.get("request_sha256"))
            and type(row.get("adult_confirmed")) is bool
            and (row.get("pair_id") is None or type(row.get("pair_id")) is str and 1 <= len(row["pair_id"]) <= 128)
            and row.get("action") in ("agree_all", "save_choices")
            and research_notices.valid_stamp(row.get("reviewed_at")))


def owner_snapshot(member, env, pair_id):
    row = receipt(member)
    revision = row.get("revision", 0)
    if type(revision) is not int or not 0 <= revision < 2**53 - 1:
        revision = 0  # A fresh explicit form can repair a malformed receipt by exact raw-row CAS.
    return {"expected_member": member["user_id"], "notice_version": VERSION, "notice_sha256": NOTICE_SHA,
            "notice": NOTICE, "revision": revision, "reviewed": reviewed(member),
            "welcome_complete": bool(reviewed(member) and row.get("pair_id") == pair_id),
            "reviewed_at": row.get("reviewed_at") if reviewed(member) else None,
            "pair_id": pair_id, "scopes": scopes_snapshot(member, env, pair_id),
            "separate_actions": ["Current-partner personality/intimacy sharing requires a separate confirmed pair-specific save on this screen.",
                "Couples reminders remain separate optional notification settings.",
                "One-time questionnaire submissions, repeat checks and wording feedback require selecting and submitting actual data; nothing is submitted here.",
                "Purchases and automatic billing still require their own exact price/trigger authorization."]}


async def pair_snapshot(db, member, env, pair_id):
    out = {"pair_id": pair_id, "eligible": False, "available": False, "reviewed": False,
           "partner_display_name": "your current partner", "notice_version": couples_compatibility.SHARING_VERSION,
           "notice": couples_compatibility.NOTICE, "notice_sha256": couples_compatibility.NOTICE_SHA,
           "expected_revision": 0, "personality": False, "intimacy": False,
           "state_sha256": None, "revocable": False, "revocation_revision": 0,
           "unavailable_reason": "No eligible current pair needs a sharing decision."}
    if not pair_id:
        return out
    stored = await db.couples_pairs.find_one({"_id": pair_id, "env": env, "state": "active", "members": member["user_id"]},
        {"members": 1, "inviter": 1, "invitee": 1, "compatibility_sharing": 1})
    if not admin_answer_history._valid_pair(stored):
        raise conflict()
    who = couples_compatibility.side(stored, member["user_id"])
    raw = (stored.get("compatibility_sharing") or {}).get(who) or {}
    revision = raw.get("revision", 0)
    if type(revision) is not int or not 0 <= revision < 2**53 - 1:
        raise HTTPException(503, "The current sharing revision needs reconciliation.")
    out.update(revocable=any(raw.get(scope) is True for scope in ("personality", "intimacy")),
               revocation_revision=revision)
    try:
        pair = await active_pair(db, member)
    except HTTPException as error:
        if error.status_code not in (402, 403, 404):
            raise
        return {**out, "unavailable_reason": "Current pair access and adult confirmation must be available before sharing assessments."}
    if pair["_id"] != pair_id:
        raise conflict()
    try:
        adults = await couples_compatibility.adult_members(db, pair, env)
    except HTTPException as error:
        if error.status_code != 403:
            raise
        return {**out, "unavailable_reason": "Current adult confirmation is required before sharing assessments."}
    side = couples_compatibility.side(pair, member["user_id"])
    current = adults[0 if side == "a" else 1]
    own = couples_compatibility.sharing(pair, side, current)
    is_reviewed = couples_compatibility.sharing_reviewed(pair, side, current)
    # An unfunded read may allow a withdrawal/decline but never a new grant.
    can_enable = True
    try:
        await active_pair(db, member, write=True)
    except HTTPException as error:
        if error.status_code not in (402, 403, 404):
            raise
        can_enable = False
    return {**out, "available": True, "eligible": can_enable, "reviewed": is_reviewed,
            "expected_revision": own["revision"], "personality": own["personality"], "intimacy": own["intimacy"],
            "state_sha256": digest([pair_id, pair.get("compatibility_sharing"), [a["couples_adult_at"] for a in adults]]),
            "unavailable_reason": None if can_enable else "Current membership access is required to enable new assessment sharing."}


async def snapshot(db, member, env):
    pair_id = await research_rows.current_pair(db, member["user_id"], env) if member.get("role") == "member" else None
    return {**owner_snapshot(member, env, pair_id), "pair_sharing": await pair_snapshot(db, member, env, pair_id)}


async def revoke_partner(db, member, env, body):
    """Remove only this actor's sharing; no funding, age bypass or answer result."""
    uid = member.get("user_id")
    if member.get("role") != "member" or member.get("closing") or body.expected_member != uid:
        raise HTTPException(403, "Only the signed-in member can withdraw their sharing.")
    current = await db.users.find_one({"user_id": uid, "env": env, "role": "member", "closing": {"$ne": True}},
        {"user_id": 1, "env": 1, "role": 1, "couples_adult_at": 1, **dict.fromkeys(answer_history.CONSENT_FIELDS, 1)})
    if not current:
        raise conflict()
    query = {"_id": body.pair_id, "env": env, "state": "active", "members": uid}
    # Flush retained observations for bounded journaling, never read assessment
    # documents or render a comparison on the withdrawal path.
    await answer_history.flush(db, "couples_pairs", query)
    pair = await db.couples_pairs.find_one(query, {"members": 1, "inviter": 1, "invitee": 1,
        "compatibility_sharing": 1, **dict.fromkeys(answer_history.FIELDS, 1)})
    if not admin_answer_history._valid_pair(pair):
        raise conflict()
    who = couples_compatibility.side(pair, uid)
    before = (pair.get("compatibility_sharing") or {}).get(who) or {}
    if type(before.get("revision", 0)) is not int or before.get("revision", 0) != body.expected_revision:
        raise conflict()
    if not any(before.get(scope) is True for scope in ("personality", "intimacy")):
        return await snapshot(db, member, env)
    after = {"version": couples_compatibility.SHARING_VERSION, "revision": body.expected_revision + 1,
        "actor_id": uid, "notice_sha256": couples_compatibility.NOTICE_SHA,
        "couples_adult_at": current.get("couples_adult_at"), "personality": False, "intimacy": False,
        "updated_at": datetime.now(timezone.utc).isoformat()}
    event = answer_history.event(source="couples_pairs", source_id=body.pair_id, owner_id=uid,
        actor_id=uid, pair_id=body.pair_id, domain="consent", question_id="couples.assessment-sharing",
        question={"label": "Withdraw all assessment sharing with my current partner"},
        signature=couples_compatibility.NOTICE_SHA, before=before, after=after,
        revision=after["revision"], consent=answer_history.consent(current), kind="participation_changed")
    path = "compatibility_sharing." + who
    update = answer_history.append({"$set": {path: after}}, event, pair)
    accepted = await db.couples_pairs.update_one(answer_history.guard({**query, path: before}, pair), update)
    if not accepted.matched_count:
        raise conflict()
    await answer_history.flush(db, "couples_pairs", query)
    fresh = await db.users.find_one({"user_id": uid, "env": env, "role": "member", "closing": {"$ne": True}},
        {**MEMBER_FIELDS, FIELD: 1, **dict.fromkeys((key for fields in RAW_FIELDS.values() for key in fields), 1)})
    if not fresh:
        raise conflict()
    return await snapshot(db, fresh, env)


def merge(target, update):
    for operator, fields in update.items():
        destination = target.setdefault(operator, {})
        for key, value in fields.items():
            if key in destination:
                raise conflict()
            destination[key] = value


def change(member, env, pair_id, scope, choice, at):
    pref = permission(member, scope, env, pair_id)
    if choice.enabled == pref["enabled"]:
        return {}, {}
    base = dict(expected_member=member["user_id"], revision=choice.revision, enabled=choice.enabled,
                adult_confirmed=choice.enabled, version=choice.version)
    if scope in ("questionnaire", "tone"):
        cls = personal_demographics.Participation if scope == "questionnaire" else personal_demographics.ToneParticipation
        _, query, update = personal_demographics.participation_update(member, cls(**base), at=at)
        return query, update
    if scope == "your_type":
        body = your_type_research.Consent(**base, notice_version=your_type_research.NOTICE_VERSION)
        return {}, your_type_research.consent_update(member, body, adult_eligible=research_notices.eligible(member), at=at) or {}
    if scope in ("member_csv", "couple_csv"):
        body = research_rows.ExportConsent(**base, expected_pair_id=pair_id if scope == "couple_csv" else None)
        return research_rows.consent_update(member, env, "member" if scope == "member_csv" else "couple", body, pair_id=pair_id, at=at)
    if scope == "staff_history":
        return admin_answer_history.permission_update(member, env, admin_answer_history.Permission(**base), at=at)
    if scope == "trends":
        return {}, {"$set": {"everyday_trends_opt_in": choice.enabled}, "$inc": {"everyday_trends_revision": 1}}
    field = "analytics_opt_in" if scope == "couples_statistics" else "review_return_opt_in"
    update = {"$set": {"couples_preferences." + field: choice.enabled}, "$inc": {scope + "_revision": 1}}
    if scope == "couples_return":
        if choice.enabled:
            update["$set"]["couples_preferences.review_return_since"] = at
        else:
            update["$unset"] = {"couples_preferences.review_return_since": ""}
    return {}, update


async def decide(db, member, env, body):
    if member.get("role") != "member" or member.get("closing") or body.expected_member != member.get("user_id"):
        raise HTTPException(403, "These data choices belong to the signed-in member.")
    if body.notice_version != VERSION or body.notice_sha256 != NOTICE_SHA:
        raise conflict()
    current = await db.users.find_one({"user_id": member["user_id"], "env": env, "role": "member", "closing": {"$ne": True}})
    if not current:
        raise conflict()
    pair_id = await research_rows.current_pair(db, member["user_id"], env)
    if pair_id != body.expected_pair_id:
        raise conflict()
    state = owner_snapshot(current, env, pair_id)
    prior = receipt(current)
    signature = digest(body.model_dump())
    if prior.get("request_id") == body.request_id:
        if (not reviewed(current) or prior.get("request_sha256") != signature or prior.get("pair_id") != pair_id
                or any(prior.get("permission_states_after", {}).get(scope) != state["scopes"][scope]["permission"]["state_sha256"] for scope in SCOPES)):
            raise conflict()
        return await snapshot(db, current, env)
    if state["revision"] != body.revision or body.revision >= 2**53 - 1:
        raise conflict()
    where = {"user_id": current["user_id"], "env": env, "role": "member", "closing": {"$ne": True},
             FIELD: current[FIELD] if FIELD in current else {"$exists": False},
             "about.demographics.age": ((current.get("about") or {}).get("demographics") or {}).get("age")}
    clauses, mutation = [where], {}
    stamp = datetime.now(timezone.utc).isoformat()
    decisions = {}
    for scope in SCOPES:
        choice = getattr(body.choices, scope)
        scope_state = state["scopes"][scope]
        pref = scope_state["permission"]
        if any(getattr(choice, key) != pref[key] for key in ("version", "revision", "state_sha256")):
            raise conflict()
        if body.action == "agree_all" and choice.enabled != scope_state["eligible"]:
            raise conflict()
        if choice.enabled and not pref["enabled"] and (not body.adult_confirmed or not scope_state["eligible"]):
            raise HTTPException(403, "An eligible adult must explicitly choose each new optional data permission.")
        clauses.append({key: value["value"] if value["present"] else {"$exists": False}
                        for key, value in raw_fields(current, scope).items()})
        query, update = change(current, env, pair_id, scope, choice, stamp)
        clauses.append(query)
        merge(mutation, update)
        decisions[scope] = {"enabled": choice.enabled, "version": choice.version,
            "permission_revision_before": choice.revision,
            "decision": "allow" if choice.enabled else "decline" if scope_state["eligible"] else "unavailable"}
    after = individual_history.preview(current, mutation)
    states = {scope: permission(after, scope, env, pair_id)["state_sha256"] for scope in SCOPES}
    for scope in SCOPES:
        decisions[scope]["permission_revision_after"] = permission(after, scope, env, pair_id)["revision"]
    saved_receipt = {"notice_version": VERSION, "notice_sha256": NOTICE_SHA, "notice_text": NOTICE,
        "revision": body.revision + 1, "reviewed_at": stamp, "action": body.action,
        "adult_confirmed": body.adult_confirmed, "pair_id": pair_id, "choices": decisions,
        "request_id": body.request_id, "request_sha256": signature, "permission_states_after": states}
    mutation.setdefault("$set", {})[FIELD] = saved_receipt
    updated = await individual_history.save(db, current["user_id"], {"$and": clauses}, mutation)
    if not updated:
        raise conflict()
    # A pair change can leave only a dormant old-pair grant, never a future-pair
    # grant. Refresh before claiming that the whole screen has completed.
    if pair_id != await research_rows.current_pair(db, member["user_id"], env):
        raise conflict()
    return await snapshot(db, updated, env)


async def aggregate(db, population, env):
    fields = {"user_id", "role", "closing", "about.demographics.age", FIELD,
              *(key for values in RAW_FIELDS.values() for key in values)}
    pipeline = [{"$match": {"$and": [population, {"role": "member", "closing": {"$ne": True}}]}},
        {"$lookup": {"from": "couples_pairs", "let": {"uid": "$user_id"}, "pipeline": [
            {"$match": {"env": env, "state": "active", "$expr": {"$and": [
                {"$in": ["$$uid", {"$ifNull": ["$members", []]}]},
                {"$or": [{"$eq": ["$inviter", "$$uid"]}, {"$eq": ["$invitee", "$$uid"]}]}]}}},
            {"$project": {"_id": 1}}, {"$limit": 2}], "as": "current_pairs"}},
        {"$project": {"_id": 0, **dict.fromkeys(fields, 1), "current_pairs": 1}}, {"$limit": 50001}]
    rows = await db.users.aggregate(pipeline).to_list(50001)
    if len(rows) > 50000:
        raise HTTPException(503, "Main consent counts exceed the bounded operational scan.")
    scopes = {scope: {"eligible": 0, "enabled": 0, "reviewed": 0, "unreviewed": 0,
                      "decisions": {"allow": 0, "decline": 0, "unavailable": 0}} for scope in SCOPES}
    reviewed_count = context_complete = 0
    for member in rows:
        pairs = member.pop("current_pairs", [])
        if len(pairs) > 1:
            raise HTTPException(503, "Current pair counts need reconciliation.")
        pair_id = pairs[0]["_id"] if pairs else None
        state = owner_snapshot(member, env, pair_id)
        reviewed_count += state["reviewed"]
        context_complete += state["welcome_complete"]
        for scope in SCOPES:
            row, source = scopes[scope], state["scopes"][scope]
            row["eligible"] += source["eligible"]
            row["enabled"] += bool(source["eligible"] and source["permission"]["enabled"])
            record = receipt(member).get("choices", {}).get(scope, {})
            valid = state["reviewed"] and (scope != "couple_csv" or receipt(member).get("pair_id") == pair_id)
            if source["eligible"] and valid:
                row["reviewed"] += 1
                row["decisions"][record["decision"]] += 1
            row["unreviewed"] = row["eligible"] - row["reviewed"]
    return {"notice_version": VERSION, "notice_sha256": NOTICE_SHA, "members": len(rows),
            "reviewed": reviewed_count, "unreviewed": len(rows) - reviewed_count,
            "current_context_complete": context_complete, "scopes": scopes,
            "purpose": "Operational owner-notice receipts and current optional permissions, not research samples or partner-sharing consent. No answer or user-ID payloads."}
