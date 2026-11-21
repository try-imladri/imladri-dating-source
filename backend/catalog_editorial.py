"""Schemas and optimistic single-document publication for the two public catalogs."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Literal, TypeVar
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator
from pymongo import ReturnDocument

T = TypeVar("T")
Items = Annotated[list[T], Field(min_length=1, max_length=80)]
Words = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]
Number = Annotated[int, Field(ge=1, le=10000)]
Group = Literal["archetypes", "figures"]
HISTORY_LIMIT = 12


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class Point(Record):
    point: Words
    detail: Words
    # Earlier figure editions carry these optional annotations.
    x: str | None = None
    test: str | None = None
    how: str | None = None
    why: str | None = None


class Line(Record):
    line: Words
    why: Words


class Move(Record):
    name: Words
    how_it_works: Words
    defense: Words


class Bubble(Record):
    speaker: Literal["you", "him", "her", "them"] = Field(alias="from")
    text: Words


class Thread(Record):
    title: Words
    note: Words
    bubbles: Items[Bubble]


class Character(Record):
    name: Words
    show: Words
    why: Words
    actor: str | None = None
    wiki_title: str | None = None
    actor_wiki_title: str | None = None
    image_url: str | None = None

    @field_validator("image_url")
    @classmethod
    def image_path(cls, value):
        if value and not (value.startswith(("/api/media/", "/img/")) or valid_web_url(value)):
            raise ValueError("Use an app image path or an HTTPS image URL.")
        return value


def valid_web_url(value):
    try:
        url = urlsplit(value)
        return (
            url.scheme == "https"
            and bool(url.hostname)
            and not url.username
            and not url.password
            and not any(c.isspace() for c in value)
        )
    except ValueError:
        return False


class Texting(Record):
    text: Items[Words]
    bubbles: list[Bubble] | None = None


class Cousin(Record):
    number: Number
    text: Words
    tell: Words


class ArchetypeSections(Record):
    identity: Words
    summary: Items[Words]
    texting_patterns: Texting
    threads: Items[Thread]
    characters: list[Character] = Field(default_factory=list, max_length=80)
    interested: Items[Point]
    not_interested: Items[Point]
    red_flags: Items[Point]
    green_flags: Items[Point]
    what_to_say: Items[Line]
    never_say: Items[Line]
    manipulation: Items[Move]
    leverage: Items[Point]
    closest_cousin: Cousin
    v: Number = 1
    dating: list[Words] = Field(default_factory=list, max_length=40)


class Archetype(Record):
    name: Words
    identity: Words
    one_liner: Words
    wave: Number
    texting_tags: list[Words] = Field(max_length=80)
    red_flag_tags: list[Words] = Field(max_length=80)
    sections: ArchetypeSections


class Source(Record):
    label: Words
    url: Words

    @field_validator("url")
    @classmethod
    def source_url(cls, value):
        if not valid_web_url(value):
            raise ValueError("Use an HTTPS source URL.")
        return value


class Citation(Source):
    example: Words


class Match(Record):
    number: Number
    why: Words


class Communication(Record):
    text: Items[Words]
    examples: Items[Words]
    example_sources: Items[Citation]


class Spectrum(Record):
    interested: Words
    invested: Words
    done: Words


class Handling(Record):
    works: Items[Point]
    never: Items[Point]


class Closest(Record):
    number: Number
    rationale: Words


class Near(Record):
    number: Number
    tell: Words


class Basis(Record):
    style: Words
    observation: Words
    sources: Items[Source]


class FigureMap(Record):
    closest: Closest
    cousin: Near
    secondary: list[Match] = Field(max_length=80)
    basis: Basis


class FigureSections(Record):
    identity: Words
    matches: Items[Match]
    pattern: Items[Words]
    communicates: Communication
    spectrum: Spectrum
    handling: Handling
    power_moves: Items[Move]
    leverage: Items[Point]
    map: FigureMap
    v: Number = 1
    dating: list[Words] = Field(default_factory=list, max_length=40)


class Figure(Record):
    name: Words
    identity: Words
    sources: Items[Source]
    matches: Items[Match]
    sections: FigureSections


MODELS = {"archetypes": Archetype, "figures": Figure}
FLAGS = {"archetypes": "unlocked", "figures": "published"}


class Version(Record):
    version: str = Field(pattern=r"^[a-f0-9]{64}$")


class Draft(Version):
    content: dict


class Restore(Version):
    revision: int = Field(ge=0)


def content(group, row):
    return {k: row[k] for k in MODELS[group].model_fields if k in row}


def check_draft(group, article):
    if (
        article.keys() - MODELS[group].model_fields.keys()
        or len(json.dumps(article, ensure_ascii=False).encode()) > 200_000
    ):
        raise HTTPException(422, "Unknown article fields or article exceeds 200 KB.")

    def safe(value, depth=0):
        if depth > 20:
            return False
        if isinstance(value, dict):
            return all(
                k and "." not in k and "$" not in k and "\0" not in k and safe(v, depth + 1)
                for k, v in value.items()
            )
        if isinstance(value, list):
            return all(safe(v, depth + 1) for v in value)
        return True

    if not safe(article):
        raise HTTPException(422, "Invalid field name or article nesting.")


async def validate(db, group, article, number=None):
    check_draft(group, article)
    try:
        record = MODELS[group].model_validate(article)
    except ValidationError as exc:
        problems = [".".join(map(str, e["loc"])) + " — " + e["msg"] for e in exc.errors()[:6]]
        raise HTTPException(422, "Check " + " · ".join(problems)) from None
    if group == "archetypes":
        nums = {record.sections.closest_cousin.number}
        if number in nums:
            raise HTTPException(422, "Choose a different archetype as closest cousin.")
    else:
        mapping, comm = record.sections.map, record.sections.communicates
        if record.matches != record.sections.matches or record.matches[0].number != mapping.closest.number:
            raise HTTPException(422, "The directory lead, article matches and closest archetype must agree.")
        if mapping.closest.number == mapping.cousin.number:
            raise HTTPException(422, "Choose a neighboring archetype distinct from the closest match.")
        if len(comm.examples) != len(set(comm.examples)):
            raise HTTPException(422, "Give each career example its own distinct text.")
        if len(comm.example_sources) != len({(ref.example, ref.url) for ref in comm.example_sources}):
            raise HTTPException(422, "List each source only once per career example.")
        cited = {ref.url for ref in comm.example_sources}
        if {ref.example for ref in comm.example_sources} != set(comm.examples) or any(
            ref.url not in cited for ref in mapping.basis.sources
        ):
            raise HTTPException(422, "Link each career example and each lead observation to its source.")
        nums = {m.number for m in [*record.matches, mapping.closest, mapping.cousin, *mapping.secondary]}
    known = await db.archetypes.distinct("number", {"number": {"$in": list(nums)}})
    if nums - set(known):
        raise HTTPException(422, "Referenced archetype does not exist.")


def signature(group, row):
    data = [content(group, row), (row.get("editorial") or {}).get("version", 0)]
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def changed(before, after, path=""):
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            item
            for k in sorted(before.keys() | after.keys())
            for item in changed(before.get(k), after.get(k), path + "." + k if path else k)
        ]
    return [] if before == after else [path]


def view(group, row):
    editorial = row.get("editorial") or {}
    published = content(group, row)
    draft = editorial.get("draft")
    return {
        "version": signature(group, row),
        "published": published,
        "draft": draft,
        "live_revision": editorial.get("live_revision", 0),
        "visible": bool(row.get(FLAGS[group])),
        "changes": changed(published, draft) if draft is not None else [],
        "history": [
            {k: r[k] for k in ("revision", "archived_at", "changes")}
            for r in reversed(editorial.get("history", []))
        ],
    }


async def load(db, group, slug):
    row = await db[group].find_one({"slug": slug}, {"_id": 0})
    if row is None:
        raise HTTPException(404, "Article not found.")
    return row


def conflict():
    raise HTTPException(409, "Another edit changed this article. Review the latest draft and your changes.")


async def change(db, group, slug, body, action, actor):
    row = await load(db, group, slug)
    if body.version != signature(group, row):
        conflict()
    data = row.get("editorial") or {}
    current = content(group, row)
    version = data.get("version", 0)
    guard = {"slug": slug, "editorial.version": version if version else {"$in": [0, None]}}
    # A seed migration or an older server may have touched content without an editorial revision.
    guard.update({key: row.get(key) for key in MODELS[group].model_fields})
    if action == "restore":
        saved = next((r for r in data.get("history", []) if r["revision"] == body.revision), None)
        if saved is None:
            raise HTTPException(404, "This revision is no longer in the saved history.")
        update = {"$set": {"editorial.draft": saved["content"]}}
    elif action == "draft":
        check_draft(group, body.content)
        update = {"$set": {"editorial.draft": body.content}}
    else:
        draft = data.get("draft")
        if draft is None or draft == current:
            raise HTTPException(422, "Save a changed draft before publishing.")
        await validate(db, group, draft, row.get("number"))
        old = {
            "revision": data.get("live_revision", 0),
            "content": current,
            "changes": changed(current, draft),
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
        }
        update = {
            "$set": {**draft, "sections_edited": True, "editorial.live_revision": old["revision"] + 1},
            "$push": {"editorial.history": {"$each": [old], "$slice": -HISTORY_LIMIT}},
            "$unset": {"editorial.draft": ""},
        }
    update["$inc"] = {"editorial.version": 1}
    updated = await db[group].find_one_and_update(
        guard, update, projection={"_id": 0}, return_document=ReturnDocument.AFTER
    )
    if updated is None:
        conflict()
    return view(group, updated)
