"""Editorial checks tied to the exact career examples used by the readers."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

try:
    from . import figure_career_review
except ImportError:
    import figure_career_review


@lru_cache(maxsize=1)
def review_batch():
    path = Path(__file__).with_name("seed") / "figure_evidence_review.json"
    return json.loads(path.read_text(encoding="utf-8"))


def evidence_version(figure):
    sections = figure.get("sections") or {}
    comm = sections.get("communicates") or {}
    mapping = sections.get("map") or {}
    payload = [figure.get("name"), figure.get("matches"), mapping.get("closest"), mapping.get("basis"),
               comm.get("examples"), comm.get("example_sources")]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def assessment(figure, *, include_unassessed=False):
    row = review_batch()["profiles"].get(figure["slug"])
    entry = figure_career_review.batch()["profiles"].get(figure["slug"])
    staff = figure.get("lead_review_decision") or {}
    if not (row or entry or staff or include_unassessed):
        return None
    version = evidence_version(figure)
    status = row["versions"].get(version) if row else None
    staff_version = figure_career_review.version(figure) if entry else version
    decision = staff if staff.get("version") in (staff_version, version) else None
    career = figure_career_review.public_record(entry) if entry and entry["version"] == staff_version else None
    previous_review = bool(row or entry or (staff and not decision))
    return {"version": staff_version, "status": career["result"] if career else
            ("changed" if entry else (status or ("changed" if previous_review else "unassessed"))),
            "readers": row["readers"] if status else [], "decision": decision, "career": career}


FIELDS = {"_id": 0, "name": 1, "slug": 1, "matches": 1, "sections.matches": 1, "sections.map": 1,
          "sections.communicates.examples": 1, "sections.communicates.example_sources": 1, "lead_review_decision": 1}


def directory_projection():
    # Only previously assessed profiles need their source text for a freshness check.
    reviewed = {"$or": [{"$in": ["$slug", list(review_batch()["profiles"].keys() | figure_career_review.batch()["profiles"].keys())]},
                        {"$ne": [{"$ifNull": ["$lead_review_decision.version", None]}, None]}]}
    sections = {
        "matches": "$sections.matches", "map": "$sections.map",
        "communicates": {"examples": "$sections.communicates.examples",
                         "example_sources": "$sections.communicates.example_sources"},
    }
    return {"_id": 0, "slug": 1, "name": 1, "identity": 1, "photo_url": 1, "matches": 1, "gender": 1,
            "sections": {"$cond": [reviewed, sections, "$$REMOVE"]},
            "lead_review_decision.version": 1, "lead_review_decision.result": 1}


def public_lead_status(review):
    if review["decision"]:
        return review["decision"]["result"]
    if review.get("career") or review["status"] == "changed":
        return review["status"]
    readers = review["readers"]
    if len(readers) == 2 and all(r["label"] == "Insufficient evidence" for r in readers):
        return "insufficient"
    return review["status"]


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version: str = Field(pattern=r"^[a-f0-9]{64}$")
    previous_id: str | None = Field(default=None, max_length=64)
    result: Literal["retain", "revise"]
    note: str = Field(min_length=8, max_length=500)


async def staff_queue(db):
    cursor = db.figures.find({"published": True}, FIELDS).sort("name", 1)
    return [{"slug": row["slug"], "name": row["name"], **assessment(row, include_unassessed=True)} async for row in cursor]


async def record_decision(db, slug, body, timestamp, decision_id):
    row = await db.figures.find_one({"slug": slug, "published": True}, FIELDS)
    if not row:
        raise HTTPException(404, "Review unavailable.")
    if assessment(row, include_unassessed=True)["version"] != body.version:
        raise HTTPException(409, "The evidence changed. Reload the review.")
    guard = {"slug": slug, "published": True, "name": row["name"], "matches": row.get("matches")}
    for field in ("sections.matches", "sections.map", "sections.communicates.examples", "sections.communicates.example_sources"):
        value = row
        for part in field.split("."):
            value = (value or {}).get(part)
        guard[field] = value
    previous = row.get("lead_review_decision") or {}
    if previous.get("version") == body.version and previous.get("id") != body.previous_id:
        raise HTTPException(409, "Another editor saved a decision. Reload the review.")
    guard["lead_review_decision.id"] = previous.get("id")
    decision = {"id": decision_id, "version": body.version, "result": body.result, "note": body.note, "at": timestamp}
    result = await db.figures.update_one(guard, {"$set": {"lead_review_decision": decision}})
    if not result.matched_count:
        raise HTTPException(409, "The evidence or decision changed. Reload the review.")
    return decision
