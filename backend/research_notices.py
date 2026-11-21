"""First-use review receipts, separate from optional research and staff grants."""
from datetime import datetime, timezone
import re
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

try:
    from . import individual_history, personal_demographics, questionnaire_report, research, your_type_research
except ImportError:
    import individual_history, personal_demographics, questionnaire_report, research, your_type_research

Scope = Literal["questionnaire", "your_type"]
DECISIONS = ("allow", "decline", "dismiss")
NOTICES = {"questionnaire": "questionnaire-research-notice-v1", "your_type": your_type_research.NOTICE_VERSION}
PERMISSIONS = {"questionnaire": "questionnaire_research", "your_type": "your_type_research"}
VERSIONS = {"questionnaire": personal_demographics.CONSENT_VERSION, "your_type": your_type_research.VERSION}
FIELD = "research_notice_receipts"
STAMP_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$"


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    notice_version: str = Field(min_length=1, max_length=80)
    revision: int = Field(ge=0, lt=2**53)
    permission_revision: int = Field(ge=0, lt=2**53)
    decision: Literal["allow", "decline", "dismiss"]
    adult_confirmed: StrictBool = False


class Decision(Choice):
    expected_member: str = Field(min_length=1, max_length=256)


class ScopeChoices(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    questionnaire: Choice
    your_type: Choice


class BulkDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_member: str = Field(min_length=1, max_length=256)
    scopes: ScopeChoices


def eligible(member):
    if member.get("role") != "member" or member.get("closing"):
        return False
    try:
        research.age_band(member)  # Optional age, explicit adult confirmation on allow.
        return True
    except HTTPException:
        return False


def receipt(member, scope):
    group = member.get(FIELD)
    value = group.get(scope) if isinstance(group, dict) else None
    return value if isinstance(value, dict) else {}


def permission(member, scope):
    prefix = PERMISSIONS[scope]
    return {"version": VERSIONS[scope], "revision": member.get(prefix + "_revision", 0),
            "enabled": type(member.get(prefix + "_version")) is str and member.get(prefix + "_version") == VERSIONS[scope]}


def valid_stamp(value):
    if type(value) is not str or not re.fullmatch(STAMP_PATTERN, value):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def scope_snapshot(member, scope):
    row = receipt(member, scope)
    revision = row.get("revision", 0)
    revision = revision if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0 else 0
    reviewed = (type(row.get("notice_version")) is str and row.get("notice_version") == NOTICES[scope]
                and type(row.get("decision")) is str and row.get("decision") in DECISIONS
                and revision > 0 and valid_stamp(row.get("reviewed_at")))
    return {"notice_version": NOTICES[scope], "revision": revision, "reviewed": reviewed,
            "decision": row.get("decision") if reviewed else None,
            "reviewed_at": row.get("reviewed_at") if reviewed else None,
            "eligible": eligible(member), "permission": permission(member, scope)}


def snapshot(member):
    scopes = {scope: scope_snapshot(member, scope) for scope in NOTICES}
    return {"expected_member": member["user_id"], "scopes": scopes,
            "welcome_complete": all(value["reviewed"] for value in scopes.values())}


def conflict():
    return HTTPException(409, "Your account, research choice or notice changed. Refresh before continuing.")


def _expected(row, key):
    return row[key] if key in row else {"$exists": False}


async def _current(db, member, env, expected_member):
    uid = member["user_id"]
    if member.get("role") != "member" or member.get("closing"):
        raise HTTPException(403, "These optional research choices belong to an active member account.")
    if expected_member != uid:
        raise conflict()
    current = await db.users.find_one({"user_id": uid, "env": env, "closing": {"$ne": True}})
    if not current or current.get("role") != "member":
        raise HTTPException(403, "These optional research choices belong to an active member account.")
    return current


def _plan(current, env, scope, body):
    uid = current["user_id"]
    if body.expected_member != uid or body.notice_version != NOTICES[scope]:
        raise conflict()
    previous = receipt(current, scope)
    pref = permission(current, scope)
    if body.decision == "allow" and (not body.adult_confirmed or not eligible(current)):
        raise HTTPException(403, "Confirm you are an adult before choosing optional research.")
    if body.decision == "decline" and pref["enabled"]:
        raise conflict()  # Reviewing/dismissing a notice never withdraws an existing grant.
    # Reconcile a lost response using the exact accepted request and unchanged
    # current permission. No extra grant, revision or history event is appended.
    if (previous.get("notice_version") == body.notice_version and previous.get("decision") == body.decision
            and previous.get("request_revision") == body.revision
            and previous.get("permission_revision_before") == body.permission_revision
            and previous.get("permission_revision_after") == pref["revision"]
            and previous.get("permission_enabled") is pref["enabled"]
            and previous.get("revision") == body.revision + 1):
        return None
    actual_revision = previous.get("revision", 0)
    if (not isinstance(actual_revision, int) or isinstance(actual_revision, bool) or actual_revision != body.revision
            or not isinstance(pref["revision"], int) or isinstance(pref["revision"], bool) or pref["revision"] != body.permission_revision):
        raise conflict()
    prefix = PERMISSIONS[scope]
    query = {"user_id": uid, "env": env, "role": "member", "closing": {"$ne": True},
             prefix + "_revision": _expected(current, prefix + "_revision"),
             prefix + "_version": _expected(current, prefix + "_version"),
             FIELD + "." + scope + ".revision": previous["revision"] if "revision" in previous else {"$exists": False}}
    at = datetime.now(timezone.utc).isoformat()
    mutation = {}
    if body.decision == "allow":
        if scope == "questionnaire":
            request = personal_demographics.Participation(expected_member=uid, version=VERSIONS[scope],
                revision=body.permission_revision, enabled=True, adult_confirmed=body.adult_confirmed)
            _, grant_query, grant_update = personal_demographics.participation_update(current, request, at=at)
            query.update(grant_query)
            if not pref["enabled"]:
                mutation = grant_update
        else:
            request = your_type_research.Consent(expected_member=uid, version=VERSIONS[scope],
                notice_version=body.notice_version, revision=body.permission_revision,
                enabled=True, adult_confirmed=body.adult_confirmed)
            mutation = your_type_research.consent_update(current, request, adult_eligible=eligible(current), at=at) or {}
        query["about.demographics.age"] = ((current.get("about") or {}).get("demographics") or {}).get("age")
    changed_permission = body.decision == "allow" and not pref["enabled"]
    stored = {"notice_version": body.notice_version, "revision": body.revision + 1,
              "decision": body.decision, "reviewed_at": at, "request_revision": body.revision,
              "adult_confirmed": body.adult_confirmed if body.decision == "allow" else False,
              "permission_version": pref["version"], "permission_revision_before": body.permission_revision,
              "permission_revision_after": body.permission_revision + int(changed_permission),
              "permission_enabled": pref["enabled"] or changed_permission}
    if FIELD in current and not isinstance(current[FIELD], dict):
        query[FIELD] = current[FIELD]
        mutation.setdefault("$set", {})[FIELD] = {scope: stored}
    else:
        mutation.setdefault("$set", {})[FIELD + "." + scope] = stored
    return query, mutation


async def decide(db, member, env, scope, body):
    current = await _current(db, member, env, body.expected_member)
    plan = _plan(current, env, scope, body)
    if plan is None:
        return {"scope": scope, "expected_member": current["user_id"], **scope_snapshot(current, scope)}
    query, mutation = plan
    updated = await individual_history.save(db, current["user_id"], query, mutation)
    if updated is None:
        raise conflict()
    return {"scope": scope, "expected_member": current["user_id"], **scope_snapshot(updated, scope)}


async def decide_all(db, member, env, body):
    current = await _current(db, member, env, body.expected_member)
    plans = []
    for scope in NOTICES:
        choice = getattr(body.scopes, scope)
        request = Decision(expected_member=body.expected_member, **choice.model_dump())
        plans.append(_plan(current, env, scope, request))
    if all(plan is None for plan in plans):
        return snapshot(current)
    if any(plan is None for plan in plans):
        # A mixed replay/new request must refresh both current choices rather
        # than silently reuse an older partly accepted form.
        raise conflict()
    query, mutation = {}, {}
    for where, update in plans:
        for key, value in where.items():
            if key in query and query[key] != value:
                raise conflict()
            query[key] = value
        for operator, fields in update.items():
            target = mutation.setdefault(operator, {})
            for key, value in fields.items():
                if key == FIELD and operator == "$set" and key in target:
                    target[key] = {**target[key], **value}
                elif key in target:
                    raise conflict()
                else:
                    target[key] = value
    updated = await individual_history.save(db, current["user_id"], query, mutation)
    if updated is None:
        raise conflict()
    return snapshot(updated)


def reviewed_filter(scope, decision):
    path = FIELD + "." + scope
    at = "$" + path + ".reviewed_at"
    at_string = {"$cond": [{"$eq": [{"$type": at}, "string"]}, at, ""]}
    return {"$and": [
        questionnaire_report.exact_fields(**{path + ".notice_version": NOTICES[scope], path + ".decision": decision}),
        {"$expr": {"$and": [
            {"$in": [{"$type": "$" + path + ".revision"}, ["int", "long"]]},
            {"$gte": ["$" + path + ".revision", 1]},
            {"$regexMatch": {"input": at_string, "regex": STAMP_PATTERN}},
            {"$ne": [{"$convert": {"input": at, "to": "date", "onNull": None, "onError": None}}, None]},
        ]}},
    ]}


async def aggregate(db, population):
    base = {"$and": [population, {"role": "member", "closing": {"$ne": True}}]}
    adults = questionnaire_report.adult_filter({})
    facets = {"members": [{"$count": "n"}], "eligible": [{"$match": adults}, {"$count": "n"}]}
    for scope in NOTICES:
        for decision in DECISIONS:
            facets[scope + "_" + decision] = [{"$match": {"$and": [adults, reviewed_filter(scope, decision)]}}, {"$count": "n"}]
        facets[scope + "_permission"] = [{"$match": {"$and": [adults,
            questionnaire_report.exact_fields(**{PERMISSIONS[scope] + "_version": VERSIONS[scope]})]}}, {"$count": "n"}]
    # One aggregation reads one source snapshot. Separate count queries can
    # otherwise disagree while another member changes a permission or closes.
    rows = await db.users.aggregate([{"$match": base}, {"$facet": facets}]).to_list(1)
    if len(rows) != 1 or not isinstance(rows[0], dict) or set(rows[0]) != set(facets):
        raise HTTPException(503, "Notice-review counts are temporarily unverified.")
    result = rows[0]
    def count(key):
        values = result[key]
        if not isinstance(values, list) or len(values) > 1:
            raise HTTPException(503, "Notice-review counts are temporarily unverified.")
        if not values:
            return 0
        if set(values[0]) != {"n"} or type(values[0]["n"]) is not int or values[0]["n"] < 0:
            raise HTTPException(503, "Notice-review counts are temporarily unverified.")
        return values[0]["n"]
    members, eligible_count = count("members"), count("eligible")
    scopes = {}
    for scope, notice in NOTICES.items():
        counts = {decision: count(scope + "_" + decision) for decision in DECISIONS}
        enabled = count(scope + "_permission")
        reviewed = sum(counts.values())
        if not 0 <= reviewed <= eligible_count <= members or not 0 <= enabled <= eligible_count:
            raise HTTPException(503, "Notice-review counts are temporarily unverified.")
        scopes[scope] = {"notice_version": notice, "permission_version": VERSIONS[scope],
            "eligible": eligible_count, "reviewed": reviewed, "unreviewed": eligible_count - reviewed,
            "decisions": counts, "permission_enabled": enabled}
    return {"notice_versions": dict(NOTICES), "members": members, "eligible_members": eligible_count,
            "scopes": scopes, "purpose": "Operational notice-review counts; not research samples or answer payloads."}
