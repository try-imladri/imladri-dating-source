"""Atomic answer journals with resumable archival and irreversible erasure fences.

Accepted source mutations append their event in the same MongoDB update. A
failed archive leaves that event on its source. Privacy mutations increment a
source generation and atomically queue redactions. Archival is insert-only;
opaque per-event tombstones therefore also fence delayed/in-flight archives.
"""
from copy import deepcopy
from datetime import datetime, timezone
import asyncio
import re
from uuid import uuid4

from bson import BSON
from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


PENDING = "answer_history_pending"
REDACTIONS = "answer_history_redactions"
GENERATION = "answer_history_generation"
REVISION = "answer_history_revision"
SOURCE = "answer_history_source"
FIELDS = (PENDING, REDACTIONS, GENERATION, REVISION, SOURCE)
MAX_PENDING = 256
MAX_EVENT_BYTES = 48 * 1024
MAX_JOURNAL_BYTES = 10 * 1024 * 1024
ARCHIVE_CONCURRENCY = 4
CONSENT_FIELDS = (
    "questionnaire_research_version", "questionnaire_research_revision", "questionnaire_research_at",
    "env", "role", "closing", "answer_history_staff_version", "answer_history_staff_id",
    "answer_history_staff_at", "answer_history_staff_env", "answer_history_staff_revision",
    "row_member_version", "row_member_code", "row_member_env", "row_member_at",
    "row_couple_version", "row_couple_code", "row_couple_env", "row_couple_at", "row_couple_pair_id",
    "your_type_research_version", "your_type_research_revision", "your_type_research_at")


def now():
    return datetime.now(timezone.utc).isoformat()


def event(*, source, source_id, owner_id, domain, question_id, after,
          question=None, signature=None, demographics=None, consent=None,
          revision=None, attempt_id=None, pair_id=None, actor_id=None,
          visibility="private", kind="answer_changed", at=None, baseline=False,
          before=None, before_exists=False, before_observed_at=None):
    if visibility not in {"private", "shared", "research"}:
        raise ValueError("Unknown answer-history visibility")
    value = {"id": uuid4().hex, "source": source, "source_id": str(source_id),
             "owner_id": owner_id, "actor_id": actor_id or owner_id,
             "domain": domain, "question_id": question_id, "after": deepcopy(after),
             "question": deepcopy(question), "signature": signature,
             "demographics": deepcopy(demographics), "consent": deepcopy(consent or {}),
             "revision": revision, "attempt_id": attempt_id or uuid4().hex,
             "pair_id": pair_id, "visibility": visibility, "kind": kind,
             "at": at or now(), "baseline": bool(baseline)}
    value.update(before=deepcopy(before), before_exists=bool(before_exists),
                 before_observed_at=before_observed_at)
    if len(BSON.encode(value)) > MAX_EVENT_BYTES:
        raise HTTPException(413, "This answer-history observation is too large to save safely.")
    return value


def consent(member):
    """Snapshot eligibility flags, never contact details or demographic values."""
    return {key: deepcopy(member[key]) for key in CONSENT_FIELDS if key in member}


def _expected(row, field):
    return row[field] if field in row else {"$exists": False}


def guard(query, previous):
    """CAS both history state and privacy generation alongside application CAS."""
    previous = previous or {}
    guarded = deepcopy(query)
    for field in (GENERATION, REVISION):
        if field in guarded:
            raise ValueError("History guards must not be supplied twice")
        guarded[field] = _expected(previous, field)
    return guarded


def append(update, events, previous=None):
    previous = previous or {}
    events = events if isinstance(events, list) else [events]
    if not events:
        return update
    if len(previous.get(PENDING, [])) + len(events) > MAX_PENDING:
        raise HTTPException(503, "Answer history is temporarily busy. Try again shortly.")
    identity = {"source": events[0]["source"], "source_id": str(events[0]["source_id"])}
    if any({"source": e["source"], "source_id": str(e["source_id"])} != identity for e in events):
        raise ValueError("A source journal must contain only its own history")
    if previous.get(SOURCE) and previous[SOURCE] != identity:
        raise ValueError("Answer-history source identity changed")
    generation = int(previous.get(GENERATION, 0)) + int(update.get("$inc", {}).get(GENERATION, 0))
    revision = int(previous.get(REVISION, 0)) + 1
    copies = [{**deepcopy(e), "generation": generation, "source_revision": revision} for e in events]
    if len(BSON.encode({PENDING: [*previous.get(PENDING, []), *copies],
                        REDACTIONS: previous.get(REDACTIONS, [])})) > MAX_JOURNAL_BYTES:
        raise HTTPException(503, "Answer history is temporarily busy. Try again shortly.")
    pushes = update.setdefault("$push", {})
    if PENDING in pushes:
        raise ValueError("Append one batch of answer-history events per mutation")
    pushes[PENDING] = {"$each": copies}
    update.setdefault("$set", {})[SOURCE] = identity
    update.setdefault("$inc", {})[REVISION] = 1
    return update


def _matches(value, question_id, actor_id, question_prefix=None):
    questions = question_id if isinstance(question_id, (list, tuple)) else [question_id]
    return ((question_id is None or value.get("question_id") in questions)
            and (actor_id is None or value.get("actor_id") == actor_id)
            and (question_prefix is None or str(value.get("question_id", "")).startswith(question_prefix)))


def redact(update, previous, *, question_id=None, actor_id=None, question_prefix=None, source=None, source_id=None):
    """Queue erasure in the SAME accepted source mutation; never before a CAS.

    Keep all FIELDS on replacement/clear until flush has acknowledged this queue.
    Scope is exact; already accepted later-generation observations are untouched.
    """
    previous = previous or {}
    identity = previous.get(SOURCE) or ({"source": source, "source_id": str(source_id)}
                                      if source is not None and source_id is not None else None)
    update.setdefault("$inc", {})[GENERATION] = 1
    update["$inc"][REVISION] = 1
    if identity is None:
        return update  # No prior journal ever existed on this legacy source.
    request = {"id": uuid4().hex, **identity, "question_id": question_id,
               "actor_id": actor_id, "question_prefix": question_prefix,
               "through_generation": int(previous.get(GENERATION, 0)),
               "pending_ids": [e["id"] for e in previous.get(PENDING, [])
                               if _matches(e, question_id, actor_id, question_prefix)], "at": now()}
    pushes = update.setdefault("$push", {})
    existing = pushes.get(REDACTIONS)
    pushes[REDACTIONS] = {"$each": [*(existing.get("$each", []) if existing and "$each" in existing
                                     else [existing] if existing else []), request]}
    # If a deletion marker/new answer accompanies erasure, it belongs to the new generation.
    for value in update.get("$push", {}).get(PENDING, {}).get("$each", []):
        value["generation"] = request["through_generation"] + 1
    return update


def preserve(row):
    """Fields that source replacement/clear must retain until erasures drain."""
    return {field: deepcopy(row[field]) for field in FIELDS if field in row}


async def _tombstone(db, event_id, at):
    # Full replacement intentionally removes owner, source, question, PI and answer payload.
    await db.answer_history.replace_one({"_id": event_id},
        {"_id": event_id, "id": event_id, "purged": True, "purged_at": at}, upsert=True)


async def flush(db, source, query, *, strict=False, redactions_only=False):
    """Archive accepted events only; every failure leaves a resumable source queue.

    Scoped lifecycle erasure can drain tombstones without awaiting unrelated
    pending inserts by setting redactions_only. The default still drains both.
    """
    result = {"archived": 0, "redacted": 0, "failed": False}
    collection = db[source]
    try:
        row = await collection.find_one(query, {field: 1 for field in FIELDS})
        if not row:
            return result
        for request in row.get(REDACTIONS, []):
            if request.get("source") != source:
                raise ValueError("Unexpected source in answer-history erasure")
            criteria = {"source": source, "source_id": request["source_id"],
                        "generation": {"$lte": request["through_generation"]}}
            if request.get("question_id") is not None:
                selected = request["question_id"]
                criteria["question_id"] = {"$in": selected} if isinstance(selected, list) else selected
            if request.get("question_prefix") is not None:
                prefix = {"question_id": {"$regex": "^" + re.escape(request["question_prefix"])}}
                criteria = {"$and": [criteria, prefix]}
            if request.get("actor_id") is not None:
                criteria["actor_id"] = request["actor_id"]
            ids = set(request["pending_ids"])
            async for found in db.answer_history.find(criteria, {"_id": 1}):
                ids.add(found["_id"])
            for identity in ids:
                await _tombstone(db, identity, request["at"])
            # Any late archive was either in pending_ids or already in central history.
            # The matching per-ID tombstone now makes its insert-only archive harmless.
            await collection.update_one(query, {"$pull": {
                PENDING: {"id": {"$in": list(ids)}}, REDACTIONS: {"id": request["id"]}}})
            result["redacted"] += len(ids)
        row = await collection.find_one(query, {field: 1 for field in FIELDS})
        if not row or row.get(REDACTIONS) or redactions_only:
            return result
        semaphore = asyncio.Semaphore(ARCHIVE_CONCURRENCY)
        async def archive(value):
            async with semaphore:
                if value.get("source") != source or row.get(SOURCE) != {
                        "source": source, "source_id": str(value.get("source_id"))}:
                    raise ValueError("Unexpected source in answer-history journal")
                identity = value["id"]
                saved = await db.answer_history.update_one({"_id": identity},
                    {"$setOnInsert": {"_id": identity, **value}}, upsert=True)
                # A new acknowledged upsert persisted precisely this event. For
                # an existing ID, verify immutable equality or an erasure fence.
                if saved.upserted_id is None:
                    stored = await db.answer_history.find_one({"_id": identity})
                    if not stored or (not stored.get("purged")
                            and any(stored.get(key) != val for key, val in value.items())):
                        raise ValueError("Conflicting answer-history identity")
                return identity
        archived = await asyncio.gather(*(archive(value) for value in row.get(PENDING, [])),
                                        return_exceptions=True)
        acknowledged = [value for value in archived if not isinstance(value, BaseException)]
        failures = [value for value in archived if isinstance(value, BaseException)]
        if acknowledged:
            await collection.update_one(query, {"$pull": {PENDING: {"id": {"$in": acknowledged}}}})
            result["archived"] += len(acknowledged)
        if failures:
            raise failures[0]
    except Exception as error:
        result.update(failed=True, error_type=type(error).__name__)
        if strict:
            raise HTTPException(503, "Answer-history cleanup is pending. Please retry shortly.",
                                headers={"Retry-After": "5"}) from None
    return result


async def purge(db, source, query, *, question_id=None, actor_id=None, question_prefix=None, strict=True):
    """Standalone journal erasure for lifecycle code after it has closed writers.

    User-facing answer removal should use redact in its accepted answer CAS.
    Do not physically delete a source until this returns without failed=True.
    """
    for _ in range(8):
        row = await db[source].find_one(query)
        if row is None:
            return {"archived": 0, "redacted": 0, "failed": False}
        update = redact({}, row, question_id=question_id, actor_id=actor_id, question_prefix=question_prefix,
                        source=source, source_id=(row.get(SOURCE) or {}).get("source_id", str(row["_id"])))
        changed = await db[source].update_one(guard(query, row), update)
        if changed.matched_count:
            return await flush(db, source, query, strict=strict)
    raise HTTPException(409, "These answers changed. Reload before removing their history.")
