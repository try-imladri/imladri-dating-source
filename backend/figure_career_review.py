"""Versioned editorial readings of documented public work, distinct from staff decisions."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from pymongo import UpdateOne


@lru_cache(maxsize=1)
def editions():
    directory = Path(__file__).with_name("seed")
    return tuple(json.loads((directory / name).read_text(encoding="utf-8")) for name in (
        "figure_career_reviews.json", "figure_career_second_read.json", "figure_career_final_read.json",
        "figure_career_morisot_read.json"))


@lru_cache(maxsize=1)
def batch():
    profiles = {}
    for edition in editions():
        profiles.update({slug: {**entry, "reviewed_on": edition["reviewed_on"]}
                         for slug, entry in edition["profiles"].items()})
    return {"profiles": profiles, "edition": editions()[-1]["edition"], "reviewed_on": editions()[-1]["reviewed_on"]}


def version(figure):
    sections = figure.get("sections") or {}
    comm = sections.get("communicates") or {}
    fields = [figure.get("name"), figure.get("matches"), sections.get("matches"), sections.get("map"),
              comm.get("examples"), comm.get("example_sources")]
    data = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def public_record(entry):
    record = {key: entry[key] for key in ("result", "rationale", "scope", "alternative", "sources", "human_reason") if entry.get(key)}
    return {**record, "reviewed_on": entry["reviewed_on"], "reviewer": "AI editorial review"}


def value_at(doc, path):
    for key in path.split("."):
        if isinstance(doc, list):
            if not key.isdigit() or int(key) >= len(doc):
                return None
            doc = doc[int(key)]
        elif isinstance(doc, dict):
            doc = doc.get(key)
        else:
            return None
        if doc is None:
            break
    return doc


async def apply(db):
    history = editions()
    for i, edition in enumerate(history):
        await apply_edition(db, edition, [item["edition"] for item in history[i:]])


async def apply_edition(db, edition, applied_editions):
    plans = {slug: item for slug, item in edition["profiles"].items() if item.get("patch")}
    if not plans:
        return
    # A newly seeded article and a prior installation follow the same migration.
    # Check the edition and the exact fields again at write time in case a staff edit raced the read.
    pending = {"slug": {"$in": list(plans)}, "lead_review_decision": {"$exists": False},
               "career_review_applied": {"$nin": applied_editions}}
    projection = {"name": 1, "slug": 1, "identity": 1, "matches": 1, "sections": 1, "sources": 1}
    writes = []
    async for row in db.figures.find(pending, projection):
        plan = plans[row["slug"]]
        if version(row) != plan["before"]:
            continue
        guard = {"_id": row["_id"], "lead_review_decision": {"$exists": False}}
        for path in ("name", "matches", "sections.matches", "sections.map", "sections.communicates.examples",
                     "sections.communicates.example_sources", *plan["patch"]):
            guard[path] = value_at(row, path)
        # Prose outside the evidence fingerprint may have been independently edited.
        if any(value_at(row, path) != prior for path, prior in plan.get("previous_copy", {}).items()):
            continue
        update = {"$set": {**plan["patch"], "career_review_applied": edition["edition"]}}
        if plan.get("add_sources"):
            missing = [r for r in plan["add_sources"] if r["url"] not in {s["url"] for s in row.get("sources", [])}]
            if missing:
                guard["sources"] = row.get("sources")
                update["$addToSet"] = {"sources": {"$each": missing}}
        writes.append(UpdateOne(guard, update))
    if writes:
        await db.figures.bulk_write(writes, ordered=False)
