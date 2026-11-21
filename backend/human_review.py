"""Fixed, blinded editorial study with staff-imported independent human ballots."""
from collections import Counter
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

SEED = Path(__file__).with_name("seed")
INSUFFICIENT = "Insufficient evidence"
SKIP = "Unable to assess"
TONE = ["warm", "neutral", "reserved", "tense", "playful", "mixed"]


@lru_cache(maxsize=1)
def study():
    raw = (SEED / "independent_reader_study.json").read_bytes()
    data = json.loads(raw)
    # Only the presented evidence determines the edition (the hidden system read is scored independently).
    fields = ("id", "kind", "name", "context", "lines", "sources")
    shown = [{k: row[k] for k in fields if k in row} for row in data["items"]]
    version = hashlib.sha256(json.dumps([data["edition"], data["definitions"], shown], sort_keys=True).encode()).hexdigest()[:20]
    return data, shown, version


def reader_html(slot):
    data, items, version = study()
    order = sorted(items, key=lambda row: hashlib.sha256((version + slot + row["id"]).encode()).digest())
    payload = {"version": version, "slot": slot, "items": order, "definitions": data["definitions"], "tones": TONE}
    return (SEED / "reader_ballot.html").read_text(encoding="utf8").replace("__PACKET__", json.dumps(payload).replace("<", "\\u003c"))


class Vote(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    choice: str = Field(min_length=1, max_length=150)
    reason: str = Field(default="", max_length=500)


class Ballot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: str = Field(max_length=64)
    slot: Literal["A", "B"]
    independent: bool
    votes: dict[str, Vote] = Field(max_length=300)


def validate(ballot):
    data, items, version = study()
    if ballot.version != version or not ballot.independent:
        raise HTTPException(422, "Use an independently completed ballot from this edition.")
    if ballot.votes.keys() != {row["id"] for row in items}:
        raise HTTPException(422, "The ballot must include every case. Readers can choose Unable to assess.")
    if all(vote.choice == SKIP for vote in ballot.votes.values()):
        raise HTTPException(422, "A returned ballot needs at least one assessed case. You can withdraw without submitting.")
    figures = {a["name"] for a in data["definitions"]}
    for row in items:
        vote = ballot.votes[row["id"]]
        choices = TONE if row["kind"] == "tone" else figures
        if vote.choice not in (*choices, INSUFFICIENT, SKIP) or (vote.choice != SKIP and len(vote.reason) < 5):
            raise HTTPException(422, "Each rated case needs a listed choice and a short reason.")


async def save(db, env, ballot, staff, stamp):
    validate(ballot)
    votes = {key: vote.model_dump() for key, vote in ballot.votes.items()}
    key = f"{env}:{ballot.version}:{ballot.slot}"
    try:
        await db.independent_ballots.insert_one({"_id": key, "env": env, "version": ballot.version,
            "slot": ballot.slot, "votes": votes, "verified_by": staff, "recorded_at": stamp})
    except DuplicateKeyError:
        previous = await db.independent_ballots.find_one({"_id": key}, {"votes": 1})
        if previous is None or previous.get("votes") != votes:
            raise HTTPException(409, "A different ballot is already recorded for this reader.")
    return await results(db, env)


async def results(db, env):
    data, _, version = study()
    records = {r["slot"]: r["votes"] async for r in db.independent_ballots.find({"env": env, "version": version, "withdrawn": {"$ne": True}}, {"slot": 1, "votes": 1})}
    definitions = Counter(row["name"] for row in data["definitions"])
    groups = []
    cases = []
    for group in ("tentative", "holdout", "tone"):
        items = [row for row in data["items"] if row["group"] == group]
        matched = assessed = available_votes = system_matches = both_uncertain = 0
        for row in items:
            readings = {slot: votes[row["id"]] for slot, votes in records.items()}
            choices = [r["choice"] for r in readings.values()]
            if len(choices) == 2 and SKIP not in choices:
                assessed += 1
                matched += choices[0] == choices[1]
                both_uncertain += all(c == INSUFFICIENT for c in choices)
            if len(records) == 2 and row.get("reference"):
                for choice in choices:
                    if choice != SKIP:
                        available_votes += 1
                        system_matches += choice == row["reference"]
            if len(records) == 2:
                cases.append({"name": row["name"], "group": group, "slug": row.get("slug"),
                    "reference": row.get("reference"), "readings": readings})
        groups.append({"group": group, "total": len(items), "both_assessed": assessed, "agree": matched,
                       "both_uncertain": both_uncertain, "rated_votes": available_votes, "reference_matches": system_matches,
                       "shared_reference_names": sum(definitions[row.get("reference")] > 1 for row in items)})
    return {"version": version, "edition": data["edition"], "received": sorted(records), "groups": groups, "cases": cases,
            "guide_entries": len(data["definitions"]), "selectable_names": len(definitions)}