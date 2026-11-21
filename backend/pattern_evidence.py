"""Which member observations have evidence to personalize future advice."""
import hashlib
import re
import uuid

from pymongo.errors import DuplicateKeyError


def source_ids(pattern):
    return list(dict.fromkeys(value for value in pattern.get("sources") or [] if isinstance(value, str) and value))


def transcript_lines(exchange):
    """Compare member-authored words, without the follow-up question, timestamps, or model replies."""
    lines = set()
    for line in exchange.get("conversation") or []:
        if isinstance(line, dict) and line.get("speaker") == "me" and isinstance(line.get("text"), str):
            normalized = " ".join(re.findall(r"[^\W_]+", line["text"].casefold()))
            if normalized:
                lines.add(normalized)
    return lines


def independent_examples(transcripts):
    # If one saved extract is contained in another, they can describe the same incident.
    # Keep all extracts in each group so a later partial copy cannot count again.
    groups = []
    for lines in {frozenset(lines) for lines in transcripts if lines}:
        group = [lines]
        separate = []
        for previous in groups:
            if any(lines <= seen or seen <= lines for seen in previous):
                group.extend(previous)
            else:
                separate.append(previous)
        groups = [*separate, group]
    return len(groups)


async def assess(db, uid, patterns):
    """Resolve sources within their owner's account, including documents created before evidence states."""
    if not patterns:
        return []
    ids = {source for row in patterns for source in source_ids(row)}
    real_ids = [source for source in ids if not source.startswith("practice:")]
    practice_ids = [source[9:] for source in ids if source.startswith("practice:")]
    sources = {}
    if real_ids:
        async for doc in db.exchanges.find({"user_id": uid, "id": {"$in": real_ids}},
                                           {"_id": 0, "id": 1, "person_id": 1, "conversation": 1, "created_at": 1}):
            sources[doc["id"]] = (doc.get("person_id"), transcript_lines(doc), doc.get("created_at"))
    if practice_ids:
        async for doc in db.practice.find({"user_id": uid, "id": {"$in": practice_ids}}, {"_id": 0, "id": 1, "person_id": 1, "created_at": 1}):
            sources["practice:" + doc["id"]] = (doc.get("person_id"), set(), doc.get("created_at"))

    assessed = []
    for row in patterns:
        linked = [source for source in source_ids(row) if source in sources]
        real = [source for source in linked if not source.startswith("practice:")]
        # On old edited records the cited exchanges could predate the member's new wording.
        past_edit = row.get("edited") and not row.get("wording_evidence_version")
        examples = 0 if past_edit else independent_examples(sources[source][1] for source in real)
        status = "confirmed" if row.get("member_confirmed") is True else "supported" if examples >= 2 else "tentative"
        people = {sources[source][0] for source in linked if sources[source][0]}
        evidence = [{"id": src.removeprefix("practice:"), "kind": "practice" if src.startswith("practice:") else "exchange",
                     "person_id": sources[src][0], "created_at": sources[src][2]} for src in linked]
        assessed.append({**row, "evidence": evidence, "sources": linked, "source_count": len(linked), "practice_count": len(linked) - len(real),
                         "example_count": examples, "evidence_status": status, "source_people": sorted(people)})
    return assessed


def description(pattern):
    labels = []
    if pattern["evidence_status"] == "confirmed":
        labels.append("member confirmed this wording")
    examples = pattern["example_count"]
    if examples:
        labels.append(f"{examples} distinct saved conversation" + ("s" if examples != 1 else ""))
    practice = pattern["practice_count"]
    if practice:
        labels.append(f"{practice} practice rehearsal" + ("s" if practice != 1 else ""))
    if pattern.get("edited"):
        labels.append("wording edited by member")
    return f"{pattern['evidence_status'].capitalize()} observation ({', '.join(labels) or 'no independent conversation evidence'}): {pattern['text']}"


def wording_key(value):
    return " ".join(re.findall(r"[^\W_]+", value.casefold()))


async def save(db, uid, text, sources, timestamp, suggested_id=None, existing=None):
    text = text.strip()[:300]
    canonical = wording_key(text)
    if not canonical or not sources:
        return None, False
    if existing is None:
        existing = await db.patterns.find({"user_id": uid, "generated_text_key": {"$exists": False}},
                                          {"_id": 0, "id": 1, "text": 1}).to_list(100)
    match = next((row for row in existing if row["id"] == suggested_id), None)
    if match is None:
        match = next((row for row in existing if wording_key(row["text"]) == canonical), None)
    add = {"sources": {"$each": list(dict.fromkeys(sources))}}
    if match:
        owner = {"id": match["id"], "user_id": uid, "text": match["text"]}
        updated = await db.patterns.update_one(owner, {"$addToSet": add})
        return (match if updated.matched_count else None), False

    # Identical concurrent generations rely on a unique member+wording index.
    key = {"user_id": uid, "generated_text_key": hashlib.sha256(canonical.encode()).hexdigest()}
    fields = {"id": str(uuid.uuid4()), "text": text, "edited": False, "created_at": timestamp}
    try:
        result = await db.patterns.update_one(key, {"$setOnInsert": fields, "$addToSet": add}, upsert=True)
    except DuplicateKeyError:
        result = await db.patterns.update_one(key, {"$addToSet": add})
    return await db.patterns.find_one(key, {"_id": 0}), result.upserted_id is not None
