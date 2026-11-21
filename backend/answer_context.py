"""Version demographic observations once; answer events retain opaque references."""
from copy import deepcopy
import hashlib
import json

from fastapi import HTTPException

try:
    from . import answer_history
except ImportError:
    import answer_history


SNAPSHOT = "answer_demographics_snapshot"
DEMOGRAPHIC_REVISION = "answer_demographics_revision"
PRIVATE_FIELDS = (SNAPSHOT, DEMOGRAPHIC_REVISION)
KEYS = ("age", "height_cm", "gender", "sex", "ethnicity", "sexuality", "education")


def values(member):
    demographics = ((member.get("about") or {}).get("demographics") or {})
    return {key: deepcopy(demographics[key]) for key in KEYS if key in demographics}


def fingerprint(demographics):
    return hashlib.sha256(json.dumps(demographics, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def permission_signature(member):
    # A permission grant observes the current values from that point onward.
    # Reusing a snapshot captured before that grant would either hide legitimate
    # new answer context or retroactively expand access to older observations.
    scopes = {key: value for key, value in answer_history.consent(member).items()
              if not key.endswith("_revision")}
    return fingerprint(scopes)


def pointer(snapshot):
    return {key: snapshot[key] for key in ("snapshot_id", "observed_at")}


def prepare(member, demographics=None, *, at=None, generation=None):
    """Plan a snapshot for the caller's atomic user mutation, without writing.

    Initial observations are explicitly baselines, never invented earlier
    questionnaire attempts. Revision plus the privacy generation separates a
    return to a previous value from the original observation.
    """
    data = values(member) if demographics is None else {
        key: deepcopy(demographics[key]) for key in KEYS if key in demographics}
    signature = fingerprint(data)
    permission = permission_signature(member)
    generation = int(member.get(answer_history.GENERATION, 0)) if generation is None else generation
    previous = member.get(SNAPSHOT) or {}
    if (previous.get("signature") == signature and previous.get("generation") == generation
            and previous.get("permission_signature") == permission):
        return [], {}, pointer(previous)
    revision = int(member.get(DEMOGRAPHIC_REVISION, 0)) + 1
    uid = member["user_id"]
    identity = hashlib.sha256(json.dumps([uid, generation, revision, signature],
                                         separators=(",", ":")).encode()).hexdigest()
    stamp = at or answer_history.now()
    snapshot = {"snapshot_id": identity, "observed_at": stamp, "signature": signature,
                "revision": revision, "generation": generation, "permission_signature": permission}
    observation = answer_history.event(source="users", source_id=uid,
        owner_id=uid, domain="demographics", question_id="demographics", after=data,
        question={"label": "Demographics at this observation", "fields": list(data)},
        signature=signature, consent=answer_history.consent(member), revision=revision,
        kind="demographics_snapshot", at=stamp,
        baseline=not bool(previous) or previous.get("signature") == signature)
    observation["id"] = identity
    return [observation], {SNAPSHOT: snapshot, DEMOGRAPHIC_REVISION: revision}, pointer(snapshot)


async def for_member(db, member_or_uid):
    """Create/reuse an observation without copying demographic PI into answers."""
    uid = member_or_uid["user_id"] if isinstance(member_or_uid, dict) else member_or_uid
    await answer_history.flush(db, "users", {"user_id": uid})
    for _ in range(5):
        member = await db.users.find_one({"user_id": uid, "closing": {"$ne": True}})
        if not member:
            raise HTTPException(409, "Your profile changed. Reopen the questionnaire.")
        events, fields, reference = prepare(member)
        if not events:
            return reference
        update = answer_history.append({"$set": fields}, events, member)
        criteria = {"user_id": uid, "closing": {"$ne": True}}
        about = member.get("about") or {}
        criteria["about.demographics"] = ({"$eq": about["demographics"], "$exists": True}
                                           if "demographics" in about else {"$exists": False})
        for key in answer_history.CONSENT_FIELDS:
            if not key.endswith("_revision"):
                criteria[key] = ({"$eq": member[key], "$exists": True}
                                 if key in member else {"$exists": False})
        result = await db.users.update_one(answer_history.guard(criteria, member), update)
        if result.matched_count:
            await answer_history.flush(db, "users", {"user_id": uid})
            return reference
    raise HTTPException(409, "Your profile changed. Reload before saving this answer.")
