"""Published question wording for the on-device intimacy questionnaire."""
import json
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


class QuestionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    section: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=160)
    question: str = Field(min_length=1, max_length=650)
    phrase: str = Field(min_length=1, max_length=350)
    revision: int = Field(ge=0, strict=True)


class DraftEdit(QuestionEdit):
    draft_revision: int = Field(ge=0, strict=True)


@lru_cache(maxsize=1)
def questions():
    groups = json.loads((Path(__file__).with_name("seed") / "intimacy.json").read_text(encoding="utf-8"))
    return {(section["id"], topic): (question, phrase)
            for sections in groups.values() for section in sections for topic, question, phrase in section["prompts"]}


async def overrides(db, *, staff=False):
    query = {"revision": {"$gt": 0}} if staff else {"changed": True}
    fields = {"_id": 0, "section": 1, "topic": 1, "question": 1, "phrase": 1}
    if staff:
        fields["revision"] = 1
        fields["previous"] = 1
    return await db.intimacy_question_copy.find(query, fields).to_list(None)


async def drafts(db):
    return [d["draft"] async for d in db.intimacy_question_copy.find(
        {"draft": {"$exists": True}}, {"_id": 0, "draft": 1})]


async def save_draft(db, edit):
    key = (edit.section, edit.topic)
    if key not in questions():
        raise HTTPException(422, "Choose a question from the current catalog.")
    # Publish revision and draft revision are compared on the same document.
    query = {"_id": "/".join(key), "revision": edit.revision}
    query["draft.draft_revision"] = edit.draft_revision or {"$exists": False}
    saved = {**edit.model_dump(), "draft_revision": edit.draft_revision + 1}
    try:
        row = await db.intimacy_question_copy.find_one_and_update(
            query, {"$set": {"draft": saved}, "$setOnInsert": {"section": edit.section, "topic": edit.topic}},
            upsert=edit.revision == 0 and edit.draft_revision == 0,
            return_document=ReturnDocument.AFTER, projection={"draft": 1})
    except DuplicateKeyError:
        row = None
    if row is None:
        raise HTTPException(409, "Another editor changed the draft or published wording. Reload before saving.")
    return row["draft"]


async def publish(db, edit):
    key = (edit.section, edit.topic)
    if key not in questions():
        raise HTTPException(422, "Choose a question from the current catalog.")
    question, phrase = questions()[key]
    stored = {"section": edit.section, "topic": edit.topic,
              "question": edit.question if edit.question != question else None,
              "phrase": edit.phrase if edit.phrase != phrase else None,
              "changed": (edit.question, edit.phrase) != (question, phrase)}
    prior = await db.intimacy_question_copy.find_one({"_id": "/".join(key), "revision": edit.revision}, {"question": 1, "phrase": 1}) or {}
    stored["previous"] = {"question": prior.get("question") or question, "phrase": prior.get("phrase") or phrase}
    try:
        result = await db.intimacy_question_copy.find_one_and_update(
            {"_id": "/".join(key), "revision": edit.revision},
            {"$set": stored, "$inc": {"revision": 1}},
            upsert=edit.revision == 0, return_document=ReturnDocument.AFTER,
            projection={"_id": 0, "changed": 0, "draft": 0})
    except DuplicateKeyError:
        result = None
    if result is None:
        raise HTTPException(409, "Another editor published this question. Reload its wording before saving.")
    # Preserve conflicting staff work for review, even if someone else published.
    await db.intimacy_question_copy.update_one(
        {"_id": "/".join(key), "draft.revision": edit.revision,
         "draft.question": edit.question, "draft.phrase": edit.phrase}, {"$unset": {"draft": ""}})
    return result
