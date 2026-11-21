"""Versioned publishing of everyday Couples prompts."""
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


# A card exercise keeps its own question even when staff publish a new starter catalog.
CARDS = {c["id"]: c for c in json.loads((Path(__file__).with_name("seed") / "couples_cards.json").read_text(encoding="utf-8"))}


def card_prompt(card, pair_id):
    suffix = hashlib.sha256(str(pair_id).encode()).hexdigest()[:24]
    return {"id": f"card-{card['id']}-{suffix}", "card": card["id"], "topic": card["id"],
            "question": card["question"], "choices": [], "discussion": card["checkIn"],
            "example_agreement": card["agreement"]}


class Prompt(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    topic: str = Field(min_length=1, max_length=90)
    question: str = Field(min_length=1, max_length=320)
    choices: list[str] = Field(max_length=12)
    discussion: str = Field(min_length=1, max_length=500)

    @field_validator("choices")
    @classmethod
    def distinct_choices(cls, items):
        cleaned = [value.strip() for value in items]
        if any(not value or len(value) > 180 for value in cleaned) or len(set(cleaned)) != len(cleaned):
            raise ValueError("Use distinct, nonempty choices of up to 180 characters.")
        return cleaned


class CatalogEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["save", "publish", "rollback"]
    revision: int = Field(ge=0)
    prompts: list[Prompt] | None = Field(default=None, max_length=64)


def config_key():
    env = "live" if os.environ.get("CLERK_PUBLISHABLE_KEY", "").startswith("pk_live_") else "preview"
    return "catalog:" + env


async def read_catalog(db, defaults, retained_ids=()):
    row = await db.couples_settings.find_one({"_id": config_key()}, {"published": 1})
    current = (row or {}).get("published", defaults)
    by_id = {p["id"]: p for p in current}
    missing = set(retained_ids or ()) - by_id.keys()
    for p in defaults:
        if p["id"] in missing:
            by_id[p["id"]] = {**p, "retired": True}
    missing -= by_id.keys()
    if missing:
        query = {"_id": {"$in": [config_key() + ":" + pid for pid in missing]}}
        async for archived in db.couples_prompt_versions.find(query, {"prompt": 1}):
            p = archived["prompt"]
            by_id[p["id"]] = {**p, "retired": True}
    return current, by_id


async def editor(db, defaults):
    row = await db.couples_settings.find_one({"_id": config_key()})
    row = row or {}
    published = row.get("published", defaults)
    return {"revision": row.get("revision", 0), "published": published, "draft": row.get("draft", published),
            "can_rollback": "previous" in row}


async def edit(db, defaults, body, actor, now):
    key = config_key()
    row = await db.couples_settings.find_one({"_id": key}) or {}
    if row.get("revision", 0) != body.revision:
        raise HTTPException(409, "Another editor changed the catalog. Reload before saving.")
    published = row.get("published", defaults)
    if body.action == "save":
        if not body.prompts:
            raise HTTPException(422, "Keep at least one starter question.")
        draft = [p.model_dump() for p in body.prompts]
        if len({p["id"] for p in draft}) != len(draft):
            raise HTTPException(422, "Each starter needs a different ID.")
        changes = {"draft": draft}
    elif body.action == "rollback":
        if "previous" not in row:
            raise HTTPException(409, "There is no earlier publication yet.")
        changes = {"draft": row["previous"], "published": row["previous"], "previous": published}
    else:
        draft = row.get("draft", published)
        if draft == published:
            raise HTTPException(409, "Save a changed draft before publishing.")
        known = {p["id"]: p for p in [*defaults, *published]}
        release = []
        for p in draft:
            if known.get(p["id"]) == p:
                release.append(p)
                continue
            fields = {name: val for name, val in p.items() if name != "id"}
            digest = hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
            version = p["id"].split("--v", 1)[0][:44] + "--v" + digest
            release.append({**p, "id": version})
        if len({p["id"] for p in release}) != len(release):
            raise HTTPException(422, "Two entries would publish as the same version.")
        for p in release:
            await db.couples_prompt_versions.update_one({"_id": key + ":" + p["id"]},
                                                        {"$setOnInsert": {"prompt": p}}, upsert=True)
        changes = {"published": release, "draft": release, "previous": published}
    published_ids = {}
    if "published" in changes:
        ids = sorted({p["id"] for p in [*published, *changes["published"]]})
        published_ids = {"$addToSet": {"research_published_ids": {"$each": ids}}}
    try:
        saved = await db.couples_settings.find_one_and_update({"_id": key, "revision": body.revision},
            {"$set": changes, "$inc": {"revision": 1}, **published_ids,
             "$push": {"changes": {"$each": [{"action": body.action, "actor": actor, "at": now}], "$slice": -50}}},
            upsert=body.revision == 0, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        saved = None
    if not saved:
        raise HTTPException(409, "Another editor changed the catalog. Reload before saving.")
    return await editor(db, defaults)
