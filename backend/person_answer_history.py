"""Private history of a member's observations about another person's intake.

These are owner-supplied third-party observations, never first-party research
answers. The owner demographic reference is labelled explicitly and remains
independently erasable.
"""
from copy import deepcopy

from fastapi import HTTPException
from pymongo import ReturnDocument

try:
    from . import answer_context, answer_history, research
except ImportError:
    import answer_context, answer_history, research


def observation(pid, member, key, before, after, definitions, context, stamp, *, created=False):
    erasing = not after
    definition = {"id": key, "label": definitions[key], "subject": "owner_observation_about_another_person"}
    value = answer_history.event(source="people", source_id=pid, owner_id=member["user_id"],
        domain="person_intake", question_id="intake:" + key,
        after={"state": "removed"} if erasing else {"state": "answered", "text": after},
        question=definition, signature=research.fingerprint(definition), demographics=context,
        consent=answer_history.consent(member), visibility="private",
        kind="answer_removed" if erasing else "answer_created" if created else "answer_changed", at=stamp,
        attempt_id="person-created:" + pid if created else None,
        before={"text": before} if before and not erasing else None,
        before_exists=bool(before) and not erasing,
        before_observed_at=stamp if before and not erasing else None)
    value.update(observation_subject="third_party", demographics_subject="owner", research_eligible=False)
    return value


async def prepare_insert(db, person, member, definitions):
    """Prepare an INSERT document; no answer is archived until that insert succeeds."""
    values = [(key, value) for key, value in (person.get("intake") or {}).items()
              if key in definitions and isinstance(value, str) and value.strip()]
    result = deepcopy(person)
    if not values:
        return result
    context = await answer_context.for_member(db, member["user_id"])
    events = [observation(person["id"], member, key, None, value, definitions, context,
                          person.get("created_at") or answer_history.now(), created=True)
              for key, value in values]
    planned = answer_history.append({}, events, {})
    result.update(planned["$set"])
    result[answer_history.PENDING] = planned["$push"][answer_history.PENDING]["$each"]
    result[answer_history.REVISION] = 1
    return result


async def save(db, pid, member, query, updates, definitions, stamp, *, extra_update=None,
               conflict_returns_none=False):
    uid = member["user_id"]
    owner = {"id": pid, "user_id": uid, "deleting": {"$ne": True}}
    await answer_history.flush(db, "people", owner)
    for _ in range(5):
        source = await db.people.find_one(owner)
        if source is None:
            return None
        mutation = deepcopy(extra_update or {})
        if any(key not in {"$set", "$unset", "$inc"} for key in mutation):
            raise ValueError("Unsupported trusted profile update")
        mutation.setdefault("$set", {}).update({**updates, "last_activity": stamp})
        touched = []
        for path, after in updates.items():
            if not path.startswith("intake."):
                raise ValueError("Person history accepts only validated intake fields")
            key = path[len("intake."):]
            if key not in definitions:
                raise ValueError("Unknown intake question")
            before = (source.get("intake") or {}).get(key)
            if (before or "").strip() != after:
                touched.append((key, before, after))
        context = await answer_context.for_member(db, uid) if touched else None
        events = []
        for key, before, after in touched:
            erasing = not after
            qid = "intake:" + key
            if erasing:
                answer_history.redact(mutation, source, question_id=qid,
                                      source="people", source_id=pid)
            events.append(observation(pid, member, key, before, after, definitions, context, stamp))
        if events:
            answer_history.append(mutation, events, source)
        updated = await db.people.find_one_and_update(
            answer_history.guard({**query, **owner}, source), mutation,
            return_document=ReturnDocument.AFTER)
        if updated is not None:
            await answer_history.flush(db, "people", owner, strict=any(not row[2] for row in touched))
            return updated
    if conflict_returns_none:
        return None
    raise HTTPException(409, "These profile details changed. Review the saved answers before saving again.")
