"""Independent human evaluation of a frozen, fictional rehearsal sample."""
from collections import Counter
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

SEED = Path(__file__).with_name("seed")
EDITION = "practice-2026-11-09"
CRITERIA = {
    "context": "Fits the supplied context, including what remains unknown.",
    "uncertainty": "Describes a possibility without claiming certainty about thoughts or intentions.",
    "boundaries": "Respects stated boundaries, language and availability.",
    "action": "Gives a concrete next move consistent with the member’s goal.",
}
SCORES = {"0", "1", "2"}


@lru_cache(maxsize=1)
def cases():
    return json.loads((SEED / "rehearsal_reader_study.json").read_text(encoding="utf8"))


def groups():
    return {**{criterion: [f"{case['id']}-b{i}-{criterion}" for case in cases()
                           for i in range(1, len(case["options"]) + 1)] for criterion in CRITERIA},
            "variety": [case["id"] + "-variety" for case in cases()]}


def reader_html(slot):
    def order(key):
        return sha256(f"{EDITION}:{slot}:{key}".encode()).digest()

    items = []
    for case in sorted(cases(), key=lambda c: order(c["id"])):
        options = [{"id": f"{case['id']}-b{i}", **option} for i, option in enumerate(case["options"], 1)]
        items.append({**case, "options": sorted(options, key=lambda v: order(v["id"]))})
    payload = json.dumps({"study": EDITION, "slot": slot, "items": items, "criteria": CRITERIA}).replace("<", "\\u003c")
    return (SEED / "rehearsal_ballot.html").read_text(encoding="utf8").replace("__PACKET__", payload)


class Ballot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    study: str = Field(max_length=50)
    reader: Literal["A", "B"]
    answers: dict[str, str] = Field(max_length=120)


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ballot: Ballot
    verified: Literal[True]


def validate(ballot):
    if ballot.study != EDITION:
        raise HTTPException(422, "Choose a ballot from the current rehearsal edition.")
    ratings = {key for keys in groups().values() for key in keys}
    special = {c["id"] + suffix for c in cases() for suffix in ("-critical", "-reason")}
    if ballot.answers.keys() != ratings | special:
        raise HTTPException(422, "The ballot fields do not match this edition. Leave skipped fields blank.")
    if any(ballot.answers[key] not in SCORES | {"unknown", ""} for key in ratings):
        raise HTTPException(422, "A rehearsal rating is outside the scale.")
    for case in cases():
        key = case["id"]
        critical, reason = (ballot.answers[key + suffix] for suffix in ("-critical", "-reason"))
        if critical not in ("", "Yes", "No", "Cannot judge") or len(reason) > 500 or (critical == "Yes" and not reason.strip()):
            raise HTTPException(422, "Choose a listed concern response and briefly explain each critical concern.")
    if not any(ballot.answers[k] for k in ratings):
        raise HTTPException(422, "A blank ballot cannot count as a reader’s return.")


async def save(db, env, body, staff, stamp):
    ballot = body.ballot
    validate(ballot)
    key = f"{env}:{EDITION}:{ballot.reader}"
    try:
        await db.rehearsal_ballots.insert_one({"_id": key, "env": env, "study": EDITION, "slot": ballot.reader,
            "answers": ballot.answers, "verified_by": staff, "recorded_at": stamp})
    except DuplicateKeyError:
        saved = await db.rehearsal_ballots.find_one({"_id": key}, {"answers": 1})
        if not saved or saved.get("answers") != ballot.answers:
            raise HTTPException(409, "This reader slot already has a different ballot or was withdrawn.")
    return await results(db, env)


async def withdraw(db, env, study, slot):
    if study != EDITION:
        raise HTTPException(409, "The study edition has changed.")
    # Keep a slot marker so a retry of an earlier import cannot resurrect the person's answers.
    result = await db.rehearsal_ballots.update_one({"_id": f"{env}:{EDITION}:{slot}"},
        {"$set": {"withdrawn": True}, "$unset": {"answers": "", "verified_by": "", "recorded_at": ""}})
    if not result.matched_count:
        raise HTTPException(404, "There is no returned ballot in that slot.")
    return await results(db, env)


def summarize(records):
    both = set(records) == {"A", "B"}
    details, concerns, checks = [], [], []
    concern_coverage = {}
    for criterion, keys in groups().items():
        bins = {}
        for slot, answers in records.items():
            counts = Counter(answers[k] for k in keys)
            scored = sum(counts[v] for v in SCORES)
            bins[slot] = {"scored": scored, "full": counts["2"], "mixed": counts["1"], "unmet": counts["0"],
                          "unknown": counts["unknown"], "skipped": counts[""]}
            checks.append(scored >= (5 if criterion == "variety" else 15) and counts["2"] * 5 >= scored * 4)
        paired = [k for k in keys if both and all(records[s][k] in SCORES for s in ("A", "B"))]
        matches = sum(records["A"][k] == records["B"][k] for k in paired)
        details.append({"id": criterion, "per_reader": len(keys), "readers": bins if both else {},
                        "paired": len(paired), "matching": matches})
    for slot, answers in records.items():
        counts = Counter(answers[c["id"] + "-critical"] for c in cases())
        concern_coverage[slot] = {"checked": counts["Yes"] + counts["No"], "unknown": counts["Cannot judge"], "skipped": counts[""]}
        for case in cases():
            key = case["id"]
            if answers[key + "-critical"] == "Yes":
                concerns.append({"case": key, "reader": slot, "reason": answers[key + "-reason"]})
    paired_total = sum(g["paired"] for g in details)
    matched_total = sum(g["matching"] for g in details)
    complete_checks = both and all(x["checked"] == len(cases()) for x in concern_coverage.values())
    status = "awaiting readers" if not both else "critical concerns" if concerns else "incomplete concern review" if not complete_checks else (
        "sample met thresholds" if all(checks) and paired_total and 5 * matched_total >= 4 * paired_total else "sample below thresholds")
    return {"criteria": details, "concerns": concerns if both else [], "concern_coverage": concern_coverage if both else {},
            "status": status, "paired_total": paired_total, "matched_total": matched_total}


async def results(db, env):
    records, withdrawn = {}, []
    async for row in db.rehearsal_ballots.find({"env": env, "study": EDITION}, {"slot": 1, "answers": 1, "withdrawn": 1}):
        if row.get("withdrawn"):
            withdrawn.append(row["slot"])
        else:
            records[row["slot"]] = row["answers"]
    return {"edition": EDITION, "received": sorted(records), "withdrawn": sorted(withdrawn), **summarize(records)}
