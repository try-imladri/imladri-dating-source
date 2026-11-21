"""A bounded, permission-aware staff timeline of retained answer observations.

This is not an expansion of the existing current-record CSV grant. Historical
payloads require an explicit grant recorded both now and on the observation.
Older events remain metadata-only. No historical values are reconstructed from
the current profile, and pending erasure fences are checked before presentation.
"""
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
from typing import Literal

from bson import BSON
from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from pymongo import ReturnDocument

try:
    from . import answer_history, history_lifecycle, questionnaire_report, research, research_rows, trends_export
except ImportError:
    import answer_history, history_lifecycle, questionnaire_report, research, research_rows, trends_export


VERSION = "history-staff-v1"
PREFIX = "answer_history_staff_"
Scope = Literal["member", "couple"]
DOMAINS = {"everyday", "intimacy", "demographics", "profile", "research", "couples", "your_type", "consent",
           "person_intake", "assessment_repeat", "wording_feedback"}
GRANT_FIELDS = tuple(PREFIX + name for name in ("version", "id", "at", "env", "revision"))
MEMBER_PROJECTION = {"_id": 0, "user_id": 1, "about.demographics.age": 1,
                     **{key: 1 for key in answer_history.CONSENT_FIELDS}}
META_PROJECTION = {"_id": 0, **{key: 1 for key in ("id", "at", "domain", "question_id", "baseline", "owner_id", "actor_id", "source", "source_id",
                    "generation", "consent", "pair_id", "visibility", "kind")}}
COLUMNS = ("event_id", "observed_at_utc", "domain", "status", "baseline", "visibility", "seat", "question_id",
           "question", "edition", "attempt_id", "kind", "before", "after", "demographics_at_observation", "withheld_reason")


class Permission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_member: str = Field(min_length=1, max_length=256)
    version: Literal["history-staff-v1"]
    revision: int = Field(ge=0, lt=2**53)
    enabled: StrictBool
    adult_confirmed: StrictBool = False


def _adult(member):
    age = ((member.get("about") or {}).get("demographics") or {}).get("age")
    if type(age) not in (int, str) or not re.fullmatch(r"[0-9]{2,3}", str(age)):
        return False
    return 18 <= int(age) <= 120


def preferences(member, env):
    enabled = (member.get(PREFIX + "version") == VERSION and member.get(PREFIX + "env") == env
               and isinstance(member.get(PREFIX + "id"), str)
               and bool(re.fullmatch(r"[0-9a-f]{32}", member[PREFIX + "id"])))
    return {"version": VERSION, "revision": member.get(PREFIX + "revision", 0), "enabled": enabled,
            "since": member.get(PREFIX + "at") if enabled else None}


def permission_update(member, env, body, *, at=None):
    if isinstance(body, dict):
        body = Permission.model_validate(body)
    uid = member["user_id"]
    if body.expected_member != uid or body.revision != member.get(PREFIX + "revision", 0):
        raise HTTPException(409, "This account or history permission changed. Reopen Settings.")
    criteria = {"user_id": uid, "env": env, "closing": {"$ne": True}, PREFIX + "revision": body.revision or {"$in": [0, None]}}
    mutation = {"$inc": {PREFIX + "revision": 1}}
    if body.enabled:
        if member.get("role") != "member" or not body.adult_confirmed or not _adult(member):
            raise HTTPException(403, "Adult confirmation and an adult profile age are required for staff history access.")
        criteria.update(role="member", **{"about.demographics.age": member["about"]["demographics"]["age"]})
        fields = {PREFIX + "version": VERSION, PREFIX + "env": env}
        if not preferences(member, env)["enabled"]:
            fields.update({PREFIX + "id": secrets.token_hex(16), PREFIX + "at": at or answer_history.now()})
        mutation["$set"] = fields
    else:
        mutation["$unset"] = {key: "" for key in GRANT_FIELDS if key != PREFIX + "revision"}
    return criteria, mutation


async def set_permission(db, member, env, body):
    criteria, mutation = permission_update(member, env, body)
    changed = await db.users.find_one_and_update(criteria, mutation, return_document=ReturnDocument.AFTER)
    if changed is None:
        raise HTTPException(409, "This permission changed. Refresh Settings before trying again.")
    return preferences(changed, env)


def _stamp(value, end=False):
    if value is None or value == "":
        return None
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError()
        date = datetime.strptime(value, "%Y-%m-%d").date()
        return datetime.combine(date, time.max if end else time.min, timezone.utc).isoformat()
    except ValueError:
        raise HTTPException(422, "Choose a valid UTC date in YYYY-MM-DD format.") from None


def _filters(domain, question_id, from_date, to_date):
    # Old clients called owner observations about another person "people".
    # Normalize before cursor binding so both names describe the same area.
    if domain == "people":
        domain = "person_intake"
    if domain not in (None, "", *DOMAINS):
        raise HTTPException(422, "Choose an available answer-history area.")
    if question_id is not None and (not isinstance(question_id, str) or len(question_id) > 200):
        raise HTTPException(422, "The question identifier is too long.")
    start, end = _stamp(from_date), _stamp(to_date, True)
    if start and end and start > end:
        raise HTTPException(422, "The starting date must be on or before the ending date.")
    return {"domain": domain or None, "question_id": question_id or None, "start": start, "end": end}


def _key():
    raw = os.getenv("ANSWER_HISTORY_CURSOR_SECRET") or os.getenv("OPS_CHECK_SECRET")
    if not raw or len(raw) < 24:
        raise HTTPException(503, "Answer-history pagination is temporarily unavailable.")
    return hmac.new(raw.encode(), b"imladri-staff-answer-history-cursor-v1", hashlib.sha256).digest()


def _binding(env, scope, target, filters):
    return research.fingerprint([env, scope, target, filters])


def _cursor(position, bound, upper):
    raw = json.dumps({"v": 1, "position": position, "bound": bound, "upper": upper}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw + hmac.new(_key(), raw, hashlib.sha256).digest()).decode().rstrip("=")


def _position(value, bound):
    try:
        if not isinstance(value, str) or len(value) > 1600:
            raise ValueError()
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        raw, signature = decoded[:-32], decoded[-32:]
        if not hmac.compare_digest(signature, hmac.new(_key(), raw, hashlib.sha256).digest()):
            raise ValueError()
        payload = json.loads(raw)
        position = payload["position"]
        if (set(payload) != {"v", "position", "bound", "upper"} or payload["v"] != 1
                or payload["bound"] != bound or not isinstance(position, list) or len(position) != 2
                or any(not isinstance(part, str) or not part or len(part) > 250 for part in position)
                or not isinstance(payload["upper"], str) or len(payload["upper"]) > 60):
            raise ValueError()
        return position, payload["upper"]
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, "This history page changed. Reload the selected timeline.") from None


def _authorize_staff(admin):
    if admin.get("role") != "admin" or admin.get("closing"):
        raise HTTPException(403, "Staff access is required.")


def _valid_pair(pair):
    return (isinstance(pair, dict) and isinstance(pair.get("members"), list) and len(pair["members"]) == 2
            and all(isinstance(uid, str) and uid for uid in pair["members"])
            and len(set(pair["members"])) == 2
            and isinstance(pair.get("inviter"), str) and isinstance(pair.get("invitee"), str)
            and set(pair["members"]) == {pair["inviter"], pair["invitee"]})


async def _target(db, env, scope, target):
    if scope not in ("member", "couple") or not isinstance(target, str) or not 1 <= len(target) <= 256:
        raise HTTPException(422, "Choose a member or couple timeline.")
    if scope == "member":
        member = await db.users.find_one({"user_id": target, "env": env, "closing": {"$ne": True}, "role": "member"}, MEMBER_PROJECTION)
        if member is None:
            raise HTTPException(404, "Member timeline not found.")
        return {target: member}, None
    pair = await db.couples_pairs.find_one({"_id": target, "env": env, "state": "active"},
        {"_id": 1, "env": 1, "state": 1, "members": 1, "inviter": 1, "invitee": 1})
    if not _valid_pair(pair):
        raise HTTPException(404, "Active couple timeline not found.")
    rows = await db.users.find({"user_id": {"$in": pair["members"]}, "env": env, "closing": {"$ne": True}, "role": "member"},
        MEMBER_PROJECTION).limit(3).to_list(3)
    people = {person["user_id"]: person for person in rows}
    if set(people) != set(pair["members"]):
        raise HTTPException(404, "Active couple timeline not found.")
    return people, pair


def _history_granted(member, recorded, env):
    current = preferences(member, env)
    return (current["enabled"] and _adult(member) and recorded.get(PREFIX + "version") == VERSION
            and recorded.get(PREFIX + "env") == env
            and recorded.get(PREFIX + "id") == member.get(PREFIX + "id"))


def _row_granted(member, recorded, scope, env, pair_id=None):
    prefix = "row_" + scope + "_"
    return (research_rows.grant_active(member, scope) and member.get(prefix + "env") == env
            and recorded.get(prefix + "version") == research_rows.VERSIONS[scope]
            and recorded.get(prefix + "code") == member.get(prefix + "code")
            and recorded.get(prefix + "env") == env
            and (scope != "couple" or member.get("row_couple_pair_id") == pair_id == recorded.get("row_couple_pair_id")))


def _not_before(observed, granted):
    try:
        if not isinstance(observed, str) or not isinstance(granted, str):
            return False
        left, right = (datetime.fromisoformat(value.replace("Z", "+00:00")) for value in (observed, granted))
        return left.tzinfo is not None and right.tzinfo is not None and left >= right
    except (ValueError, TypeError):
        return False


def _reason(member, event, env, scope, pair=None, peers=None):
    if event.get("kind") == "your_type.stated_preferences" or event.get("question_id") == "your_type.stated_preferences":
        return "Private stated preferences remain available only to their owner."
    # Draft retention is for the owner. Separate staff/research grants never
    # authorize unfinished private Couples wording, including older journals.
    if event.get("source") == "couples_drafts":
        return "Private Couples drafts remain available only to their owner."
    recorded = event.get("consent") or {}
    if not _history_granted(member, recorded, env):
        return "Staff history permission was not recorded for this observation or is no longer active."
    if not _not_before(event.get("at"), member.get(PREFIX + "at")):
        return "This observation predates the recorded staff-history permission."
    if (member.get("questionnaire_research_version") != questionnaire_report.CONSENT
            or recorded.get("questionnaire_research_version") != questionnaire_report.CONSENT
            or member.get("questionnaire_research_at") != recorded.get("questionnaire_research_at")):
        return "Questionnaire research permission is missing or has been withdrawn."
    row_scope = "couple" if scope == "couple" else "member"
    if not _row_granted(member, recorded, row_scope, env, pair["_id"] if pair else None):
        return "The applicable record-export permission was not recorded or is no longer active."
    if event.get("domain") == "your_type":
        if scope == "couple":
            return "Your Type preferences do not belong in a Couples timeline."
        if (member.get("your_type_research_version") != "your-type-research-v2"
                or recorded.get("your_type_research_version") != "your-type-research-v2"
                or member.get("your_type_research_at") != recorded.get("your_type_research_at")):
            return "Your Type research permission was not recorded or is no longer active."
    if pair is not None:
        for peer in (peers or {}).values():
            if (not preferences(peer, env)["enabled"] or not _adult(peer)
                    or not research_rows.grant_active(peer, "couple") or peer.get("row_couple_env") != env
                    or peer.get("row_couple_pair_id") != pair["_id"]
                    or peer.get("questionnaire_research_version") != questionnaire_report.CONSENT):
                return "Both current partners must enable history, questionnaire and Couples record permissions."
    if recorded.get("env") != env or recorded.get("role") != "member" or recorded.get("closing"):
        return "This observation was not recorded as an eligible member record."
    return None


async def _live_event(db, event):
    current = await db.answer_history.find_one({"id": event["id"], "purged": {"$ne": True}})
    if current is None:
        return None
    source = current.get("source")
    if source not in history_lifecycle.SOURCES:
        return None
    query = {answer_history.SOURCE + ".source": source,
             answer_history.SOURCE + ".source_id": str(current.get("source_id"))}
    if source == "users":
        query["closing"] = {"$ne": True}
    elif source == "couples_pairs":
        query["state"] = "active"
    source_row = await db[source].find_one(query, {key: 1 for key in answer_history.FIELDS})
    if source_row is None:
        return None
    for fence in source_row.get(answer_history.REDACTIONS, []):
        if (current.get("generation", 0) <= fence.get("through_generation", -1)
                and answer_history._matches(current, fence.get("question_id"), fence.get("actor_id"), fence.get("question_prefix"))):
            return None
    return current


def _your_type_payload(payload):
    # Full latent models and packed state are owner-only exports. Staff receives
    # authored actions/editions, never a compressed model or inferred identity.
    if not isinstance(payload, dict):
        return None
    keys = ("request", "effects", "before_status", "preference_snapshot", "asset_edition", "asset_manifest_sha256", "model_edition",
            "action", "audience", "status", "retake_of", "snapshot")
    return {key: deepcopy(payload[key]) for key in keys if key in payload}


def _metadata(event, reason):
    return {"event_id": event["id"], "observed_at_utc": event.get("at"), "domain": event.get("domain"),
            "baseline": bool(event.get("baseline")), "status": "withheld" if reason else "available",
            **({"withheld_reason": reason} if reason else {})}


def _source_permits(event, sources, retained):
    if event.get("id") not in retained:
        return False
    row = sources.get((event.get("source"), str(event.get("source_id"))))
    if row is None or row.get("closing") or (event.get("source") == "couples_pairs" and row.get("state") != "active"):
        return False
    generation, source_generation = event.get("generation", 0), row.get(answer_history.GENERATION, 0)
    if (type(generation) is not int or type(source_generation) is not int
            or not 0 <= generation <= source_generation):
        return False
    fences = row.get(answer_history.REDACTIONS, [])
    if not isinstance(fences, list):
        return False
    for fence in fences:
        through = fence.get("through_generation") if isinstance(fence, dict) else None
        if type(through) is not int:
            return False
        if (event.get("generation", 0) <= through and answer_history._matches(event,
                fence.get("question_id"), fence.get("actor_id"), fence.get("question_prefix"))):
            return False
    return True


def _predecessor_query(event):
    return {"owner_id": event.get("owner_id"), "actor_id": event.get("actor_id"), "source": event.get("source"),
        "source_id": event.get("source_id"), "question_id": event.get("question_id"), "id": {"$ne": event["id"]},
        "at": {"$lte": event.get("at", "")}, "after": event.get("before"), "purged": {"$ne": True},
        "consent." + PREFIX + "id": (event.get("consent") or {}).get(PREFIX + "id")}


def _predecessor_matches(previous, event):
    return (previous.get("id") != event["id"] and all(previous.get(field) == event.get(field)
            for field in ("owner_id", "actor_id", "source", "source_id", "question_id"))
            and previous.get("at", "") <= event.get("at", "")
            and BSON.encode({"v": previous.get("after")}) == BSON.encode({"v": event.get("before")})
            and (previous.get("consent") or {}).get(PREFIX + "id") == (event.get("consent") or {}).get(PREFIX + "id"))


def _identity_clause(field, identities):
    values = list(identities)
    # The PostgreSQL repository can push scalar equality (including each OR
    # branch) into its indexed SQL candidate selector. A bare $in alone cannot
    # do that; keep large multi-account collections out of the bounded scan.
    return {field: values[0]} if len(values) == 1 else {"$or": [{field: value} for value in values]}


async def _present_batch(db, events, env, scope, target):
    """Revalidate one bounded page, without an RPC for every individual row.

    No grant/source cache survives this call. Metadata, linked observations,
    source erasure fences and current members are read again after the access
    audit. Full answer payloads are fetched only for potentially authorized IDs.
    """
    if not events:
        await _target(db, env, scope, target)
        return []
    ids = [event["id"] for event in events]
    people, pair = await _target(db, env, scope, target)
    owners = _identity_clause("owner_id", people)
    current = await db.answer_history.find({"id": {"$in": ids}, "purged": {"$ne": True}, "$and": [owners]},
        META_PROJECTION).limit(len(ids)).to_list(len(ids))
    possible = [row["id"] for row in current if row.get("owner_id") in people
        and row.get("actor_id") == row.get("owner_id")
        and _reason(people[row["owner_id"]], row, env, scope, pair, people) is None]
    payloads = await db.answer_history.find({"id": {"$in": possible}, "purged": {"$ne": True}, "$and": [owners]}, {"_id": 0}).limit(len(possible)).to_list(len(possible)) if possible else []
    by_id = {row["id"]: row for row in payloads}
    snapshot_ids = sorted({reference["snapshot_id"] for row in payloads
        if isinstance((reference := row.get("demographics")), dict) and isinstance(reference.get("snapshot_id"), str)})
    snapshots = await db.answer_history.find({"id": {"$in": snapshot_ids}, "owner_id": {"$in": list(people)},
        "domain": "demographics", "purged": {"$ne": True}, "$and": [owners]}, {"_id": 0}).limit(len(snapshot_ids)).to_list(len(snapshot_ids)) if snapshot_ids else []
    # Predecessor matches are optional context, not an excuse for an unbounded
    # history scan/response. Oversized lookups remain explicitly unavailable.
    clauses, encoded_size = [], 0
    for row in payloads:
        if row.get("before_exists"):
            clause = _predecessor_query(row)
            size = len(BSON.encode(clause))
            if encoded_size + size <= 128 * 1024:
                clauses.append(clause)
                encoded_size += size
    previous = await db.answer_history.find({"$or": clauses}, {"_id": 0}).sort([("at", -1), ("id", -1)]).limit(100).to_list(100) if clauses else []
    linked = [*current, *snapshots, *previous]
    source_ids = {}
    for row in linked:
        if row.get("source") in history_lifecycle.SOURCES:
            source_ids.setdefault(row["source"], set()).add(str(row.get("source_id")))
    sources = {}
    source_projection = {"_id": 0, answer_history.SOURCE: 1, answer_history.REDACTIONS: 1,
                         answer_history.GENERATION: 1, "closing": 1, "state": 1}
    for source, identities in source_ids.items():
        indexed = (_identity_clause("_id", identities) if source in ("couples_pairs", "intimacy_answers", "research_responses")
                   else _identity_clause("user_id", people))
        rows = await db[source].find({answer_history.SOURCE + ".source": source,
            answer_history.SOURCE + ".source_id": {"$in": list(identities)}, "$and": [indexed]}, source_projection).limit(len(identities) + 1).to_list(len(identities) + 1)
        for row in rows:
            identity = row.get(answer_history.SOURCE)
            if (isinstance(identity, dict) and identity.get("source") == source
                    and isinstance(identity.get("source_id"), str) and identity["source_id"] in identities):
                key = (source, identity["source_id"])
                sources[key] = None if key in sources else row
    # A queue may already have drained since a linked payload was prefetched.
    # Re-read central tombstones and current grants just before formatting.
    linked_ids = list({row["id"] for row in linked})
    live_rows = await db.answer_history.find({"id": {"$in": linked_ids}, "purged": {"$ne": True}, "$and": [owners]},
        {"_id": 0, "id": 1}).limit(len(linked_ids)).to_list(len(linked_ids))
    retained = {row["id"] for row in live_rows}
    people, pair = await _target(db, env, scope, target)
    metadata = {row["id"]: row for row in current}
    snapshot_by_id = {row["id"]: row for row in snapshots}
    output = []
    for selected in events:
        row = metadata.get(selected["id"])
        if (not row or row.get("owner_id") not in people or row.get("actor_id") != row.get("owner_id")
                or not _source_permits(row, sources, retained)):
            continue
        member = people[row["owner_id"]]
        reason = _reason(member, row, env, scope, pair, people)
        event = by_id.get(row["id"])
        if reason is None and event is None:
            reason = "History permissions changed while loading. Refresh this timeline."
        base = _metadata(row, reason)
        if reason:
            output.append(base)
            continue
        result = {**base, "visibility": event.get("visibility", "private"), "question_id": event.get("question_id"),
            "question": deepcopy(event.get("question")), "edition": event.get("signature"), "attempt_id": event.get("attempt_id"),
            "kind": event.get("kind"), "after": deepcopy(event.get("after")), "before_exists": bool(event.get("before_exists")),
            "before": None, "demographics_at_observation": None}
        if pair:
            result["seat"] = "inviter" if event["owner_id"] == pair["inviter"] else "invitee"
        if event.get("domain") == "your_type":
            result["after"] = _your_type_payload(event.get("after"))
        if result["before_exists"]:
            prior = next((candidate for candidate in previous if _predecessor_matches(candidate, event)
                and _source_permits(candidate, sources, retained) and _reason(member, candidate, env, scope, pair, people) is None), None)
            if prior:
                result["before"] = deepcopy(event.get("before"))
            else:
                result["before_withheld"] = "Earlier value has no matching authorized observation in this bounded page."
        reference = event.get("demographics") or {}
        snapshot = snapshot_by_id.get(reference.get("snapshot_id")) if isinstance(reference, dict) else None
        if (snapshot and snapshot.get("owner_id") == event["owner_id"] and _source_permits(snapshot, sources, retained)
                and _reason(member, snapshot, env, "member") is None):
            result["demographics_at_observation"] = deepcopy(snapshot.get("after"))
            result["demographics_observed_at"] = snapshot.get("at")
        output.append(result)
    return output


async def _audit(db, admin, env, scope, target, action, count, withheld, filters):
    await db.admin_answer_history_access.insert_one({"actor_id": admin["user_id"], "env": env, "scope": scope,
        "target": target, "action": action, "at": answer_history.now(), "candidate_count": count,
        "candidate_withheld": withheld, "phase": "before_final_revalidation",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
        "filter_fields": sorted(key for key, value in filters.items() if value is not None)})


async def targets(db, admin, env, *, q="", limit=50, before=None):
    """Browse existing active pairs without downloading their questionnaire data."""
    _authorize_staff(admin)
    if not isinstance(q, str) or len(q) > 128 or type(limit) is not int or not 1 <= limit <= 100:
        raise HTTPException(422, "Choose a search up to 128 characters and a page size up to 100.")
    bound = research.fingerprint(["history-pair-picker-v1", env, q])
    position, upper = _position(before, bound) if before else (None, answer_history.now())
    query = {"env": env, "state": "active"}
    if q:
        query["_id"] = {"$regex": re.escape(q), "$options": "i"}
    if position:
        query.setdefault("_id", {})["$gt"] = position[1]
    raw = await db.couples_pairs.find(query, {"_id": 1, "state": 1, "inviter": 1, "invitee": 1, "members": 1}).sort("_id", 1).limit(limit + 1).to_list(limit + 1)
    pairs = [pair for pair in raw[:limit] if isinstance(pair.get("_id"), str) and _valid_pair(pair)]
    uids = sorted({uid for pair in pairs for uid in pair["members"]})
    people = await db.users.find({"user_id": {"$in": uids}, "env": env, "role": "member", "closing": {"$ne": True}},
        {"_id": 0, "user_id": 1, "nickname": 1}).limit(len(uids)).to_list(len(uids)) if uids else []
    people = {person["user_id"]: person for person in people}
    items = []
    for pair in pairs:
        labels = []
        for uid in (pair["inviter"], pair["invitee"]):
            person = people.get(uid)
            if person is None:
                break
            nickname = person.get("nickname")
            labels.append(nickname[:80] if isinstance(nickname, str) and nickname else ("Inviter" if uid == pair["inviter"] else "Invited partner"))
        if len(labels) == 2:
            items.append({"pair_id": pair["_id"], "state": "active", "label": " · ".join(labels)})
    next_page = _cursor(["targets", raw[limit - 1]["_id"]], bound, upper) if len(raw) > limit else None
    return {"items": items, "next": next_page}


async def page(db, admin, env, scope, target, *, limit=50, before=None, domain=None, question_id=None,
               from_date=None, to_date=None, _action="view"):
    _authorize_staff(admin)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise HTTPException(422, "Choose between 1 and 100 history records per page.")
    filters = _filters(domain, question_id, from_date, to_date)
    people, pair = await _target(db, env, scope, target)
    # Customer writers/recovery own archival. A staff page must not traverse and
    # rewrite every source in the account. Its bounded source checks below deny
    # pending erasures even before central tombstones have been drained.
    bound = _binding(env, scope, target, filters)
    position, upper = _position(before, bound) if before else (None, answer_history.now())
    query = {"owner_id": {"$in": list(people)}, "purged": {"$ne": True}, "kind": {"$ne": "your_type.stated_preferences"},
             "at": {"$lte": min(upper, filters["end"] or upper)},
             "$expr": {"$eq": ["$owner_id", "$actor_id"]}, "$and": [_identity_clause("owner_id", people)]}
    if pair:
        query.update(pair_id=target, domain="couples")
    if filters["domain"]:
        if pair and filters["domain"] != "couples":
            raise HTTPException(422, "A Couples timeline contains Couples observations only.")
        query["domain"] = {"$in": ["person_intake", "people"]} if filters["domain"] == "person_intake" else filters["domain"]
    if filters["question_id"]:
        query["question_id"] = filters["question_id"]
    if filters["start"]:
        query["at"]["$gte"] = filters["start"]
    if position:
        query["$or"] = [{"at": {"$lt": position[0]}}, {"at": position[0], "id": {"$lt": position[1]}}]
    raw = await db.answer_history.find(query, META_PROJECTION).sort([("at", -1), ("id", -1)]).limit(limit + 1).to_list(limit + 1)
    next_page = _cursor([raw[limit - 1]["at"], raw[limit - 1]["id"]], bound, upper) if len(raw) > limit else None
    selected = raw[:limit]
    withheld = sum(event.get("owner_id") not in people or _reason(people[event["owner_id"]], event, env, scope, pair, people) is not None for event in selected)
    await _audit(db, admin, env, scope, target, _action, len(selected), withheld, filters)
    final = await _present_batch(db, selected, env, scope, target)
    return {"items": final, "next": next_page, "limit": limit, "scope": scope,
            "notice": "Only retained observations are shown; accepted answers still awaiting archival appear when recovery completes. Pending erasures are withheld. Baselines are observations of existing values, not invented earlier attempts. Older answers without recorded staff-history permission remain withheld."}


async def export_csv(db, admin, env, scope, target, **kwargs):
    result = await page(db, admin, env, scope, target, _action="csv_page", **kwargs)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(COLUMNS)
    for item in result["items"]:
        values = []
        for key in COLUMNS:
            value = item.get(key, "")
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            values.append(trends_export.spreadsheet_cell(value))
        writer.writerow(values)
    return Response(content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers={
        "Cache-Control": "private, no-store", "Content-Disposition": 'attachment; filename="imladri-answer-history-page.csv"',
        "X-History-Has-More": "1" if result["next"] else "0", "X-Content-Type-Options": "nosniff"})
