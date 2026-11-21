"""Apply Individual/profile changes and their immutable observations together."""
from copy import deepcopy
from uuid import uuid4

from fastapi import HTTPException
from pymongo import ReturnDocument

try:
    from . import answer_context, answer_history, quiz_answers, research
except ImportError:
    import answer_context, answer_history, quiz_answers, research


MISSING = object()


def get(document, path, default=None):
    value = document
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def set_value(document, path, value):
    pieces = path.split(".")
    target = document
    for key in pieces[:-1]:
        if not isinstance(target.get(key), dict):
            target[key] = {}
        target = target[key]
    target[pieces[-1]] = deepcopy(value)


def remove_value(document, path):
    pieces = path.split(".")
    target = document
    for key in pieces[:-1]:
        if not isinstance(target, dict) or not isinstance(target.get(key), dict):
            return
        target = target[key]
    target.pop(pieces[-1], None)


def preview(previous, update):
    """Only the existing profile operators used by these routes are admitted."""
    result = deepcopy(previous)
    for operator, fields in update.items():
        if operator == "$set":
            for path, value in fields.items():
                set_value(result, path, value)
        elif operator == "$unset":
            for path in fields:
                remove_value(result, path)
        elif operator == "$inc":
            for path, value in fields.items():
                set_value(result, path, (get(result, path, 0) or 0) + value)
        elif operator == "$addToSet":
            for path, value in fields.items():
                items = list(get(result, path, []) or [])
                additions = value.get("$each", []) if isinstance(value, dict) and "$each" in value else [value]
                for item in additions:
                    if item not in items:
                        items.append(item)
                set_value(result, path, items)
        elif operator == "$pull":
            for path, value in fields.items():
                removed = value.get("$in", []) if isinstance(value, dict) and "$in" in value else [value]
                set_value(result, path, [item for item in (get(result, path, []) or []) if item not in removed])
        else:
            raise ValueError("Unsupported Individual history mutation operator")
    return result


def question_state(member, qid):
    value = get(member, "about.answers." + qid, MISSING)
    skipped = qid in (get(member, "about.skipped", []) or [])
    if value is not MISSING:
        return {"state": "answered", **(deepcopy(value) if isinstance(value, dict) else {"raw": deepcopy(value)}), "quick_skip": skipped}
    if skipped:
        return {"state": "skipped", "edition": get(member, "about.skip_editions." + qid)}
    return None


def question_definition(quiz, qid, previous):
    for section in quiz_answers.SETS:
        for question in (quiz or {}).get(section, []):
            if question["id"] == qid:
                meaning = {"section": section, "question": deepcopy(question),
                           "catalog_version": (quiz or {}).get("version")}
                return meaning, research.fingerprint(meaning)
    old = get(previous, "about.answers." + qid, {}) or {}
    meaning = {"section": "earlier", "question": old.get("question"), "label": old.get("label")}
    return meaning, research.fingerprint(meaning)


async def save(db, uid, query, update, quiz=None):
    """Return the accepted current user document; retries do not create attempts.

    The source history CAS serializes answer changes without a cross-database
    transaction. Archival failure retains the complete event on the user row.
    Explicit removals queue payload erasure in that same successful mutation.
    """
    await answer_history.flush(db, "users", {"user_id": uid})
    for _ in range(5):
        previous = await db.users.find_one({"user_id": uid, "closing": {"$ne": True}})
        if previous is None:
            return None
        replacement = preview(previous, update)
        mutation = deepcopy(update)
        old_demo, new_demo = answer_context.values(previous), answer_context.values(replacement)
        demographics_removed = any(key not in new_demo or
            (new_demo[key] == "Prefer not to say" and old_demo[key] != new_demo[key]) for key in old_demo)
        removed = []
        touched = set()
        for operator, fields in update.items():
            for path in fields:
                if path.startswith("about.answers."):
                    qid = path[len("about.answers."):]
                    touched.add(qid)
                    if operator == "$unset":
                        removed.append("e:" + qid)
                elif path.startswith("about.skip_editions."):
                    touched.add(path[len("about.skip_editions."):])
            if operator == "$addToSet" and "about.skipped" in fields:
                value = fields["about.skipped"]
                touched.update(value.get("$each", []) if isinstance(value, dict) else [value])
        for path, qid in (("about.mbti", "p:mbti"), ("about.describe", "p:describe")):
            if path in update.get("$unset", {}) or (path in update.get("$set", {}) and not get(replacement, path)):
                removed.append(qid)
        if demographics_removed:
            removed.append("demographics")
        prefixes = []
        for field, prefix in (("assessment_repeat", "assessment-repeat:"),
                              ("intimacy_wording_review", "wording:")):
            if field in update.get("$unset", {}):
                prefixes.append(prefix)
        if removed:
            answer_history.redact(mutation, previous, question_id=sorted(set(removed)),
                                  source="users", source_id=uid)
        for prefix in prefixes:
            answer_history.redact(mutation, previous, question_prefix=prefix, source="users", source_id=uid)
        generation = int(previous.get(answer_history.GENERATION, 0)) + int(bool(removed or prefixes))
        events = []
        # Baselines are observations of existing state, never invented past attempts.
        basis = deepcopy(previous)
        if old_demo != new_demo and not demographics_removed:
            baseline, fields, _ = answer_context.prepare(previous)
            events.extend(baseline)
            basis.update(fields)
        snapshots, fields, context = answer_context.prepare(basis, new_demo, generation=generation)
        events.extend(snapshots)
        if fields:
            mutation.setdefault("$set", {}).update(fields)
        elif basis is not previous:
            for key in answer_context.PRIVATE_FIELDS:
                if key in basis and basis.get(key) != previous.get(key):
                    mutation.setdefault("$set", {})[key] = basis[key]
        before_context = (answer_context.pointer(previous[answer_context.SNAPSHOT])
                          if previous.get(answer_context.SNAPSHOT) else None)
        stamp = answer_history.now()
        attempt_id = uuid4().hex
        for qid in sorted(touched):
            before, after = question_state(previous, qid), question_state(replacement, qid)
            erasing = "e:" + qid in removed
            if before == after:
                continue
            question, signature = question_definition(quiz, qid, previous)
            observation = answer_history.event(source="users", source_id=uid, owner_id=uid,
                domain="everyday", question_id="e:" + qid,
                after={"state": "removed"} if erasing else after,
                question=question, signature=signature, demographics=context,
                consent=answer_history.consent(replacement),
                attempt_id=attempt_id,
                kind="answer_removed" if erasing else "answer_skipped" if after and after["state"] == "skipped" else "answer_changed",
                at=stamp, before=None if erasing else before, before_exists=before is not None and not erasing,
                before_observed_at=stamp if before is not None and not erasing else None)
            observation["before_demographics"] = before_context
            events.append(observation)
        for path, qid, domain, label in (("about.mbti", "p:mbti", "everyday", "Self-reported personality type"),
                                         ("about.describe", "p:describe", "profile", "Personal description")):
            before, after = get(previous, path), get(replacement, path)
            if before == after:
                continue
            erasing = qid in removed
            events.append(answer_history.event(source="users", source_id=uid, owner_id=uid,
                domain=domain, question_id=qid, after={"state": "removed"} if erasing else {"value": after},
                question={"label": label}, signature=research.fingerprint([qid, label]),
                demographics=context, consent=answer_history.consent(replacement),
                attempt_id=attempt_id,
                kind="answer_removed" if erasing else "answer_changed", before=None if erasing else before,
                before_exists=before is not None and not erasing, at=stamp))
        for scope in ("questionnaire_research", "tone_research", "your_type_research"):
            fields = (scope + "_version", scope + "_revision", scope + "_at")
            before = {key: previous.get(key) for key in fields}
            after = {key: replacement.get(key) for key in fields}
            if before != after:
                events.append(answer_history.event(source="users", source_id=uid, owner_id=uid,
                    domain="consent", question_id=scope, after=after, before=before,
                    before_exists=True, consent=answer_history.consent(replacement),
                    question={"label": scope.replace("_", " ")}, kind="participation_changed", at=stamp,
                    attempt_id=attempt_id))
        for scope in ("questionnaire", "your_type"):
            path = "research_notice_receipts." + scope
            before, after = get(previous, path), get(replacement, path)
            if before != after:
                events.append(answer_history.event(source="users", source_id=uid, owner_id=uid,
                    domain="consent", question_id="research_notice." + scope,
                    question={"label": "Optional research notice review", "scope": scope},
                    signature=research.fingerprint(["research_notice", scope, (after or {}).get("notice_version")]),
                    after=after, before=before, before_exists=before is not None,
                    consent=answer_history.consent(replacement), kind="consent_notice", at=stamp,
                    attempt_id=attempt_id))
        before, after = previous.get("main_consent_receipt"), replacement.get("main_consent_receipt")
        if before != after:
            events.append(answer_history.event(source="users", source_id=uid, owner_id=uid,
                domain="consent", question_id="main_consent",
                question={"label": "Main data and research consent"},
                signature=(after or {}).get("notice_sha256"),
                after=after, before=before, before_exists=before is not None,
                consent=answer_history.consent(replacement), kind="consent_notice", at=stamp,
                attempt_id=attempt_id))
        if events:
            answer_history.append(mutation, events, previous)
        accepted = await db.users.find_one_and_update(
            answer_history.guard({**query, "closing": {"$ne": True}}, previous), mutation,
            return_document=ReturnDocument.AFTER)
        if accepted is None:
            continue
        await answer_history.flush(db, "users", {"user_id": uid}, strict=bool(removed or prefixes))
        return accepted
    raise HTTPException(409, "These answers changed. Reload Personal before saving again.")
