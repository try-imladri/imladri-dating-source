"""Voluntary, versioned research submissions, separate from private Personal activity."""
import hashlib
import hmac
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from pymongo.errors import DuplicateKeyError

try:
    from . import answer_context, answer_history, intimacy_catalog, quiz_answers
except ImportError:
    import answer_context, answer_history, intimacy_catalog, quiz_answers


CONSENT_VERSION = "research-v1"
CHOICES = ("Interested", "Curious", "It depends", "Not for me", "Off limits")
CONDITIONS = ("With more trust", "With a clear plan", "In private", "In fantasy only")
AGE_BANDS = ("18-24", "25-34", "35-44", "45-54", "55-64", "65+")


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    choice: str = Field(max_length=40)
    conditions: list[str] = Field(default_factory=list, max_length=len(CONDITIONS))


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0, lt=2**53)
    consent_version: str = Field(max_length=64)
    catalog_version: str = Field(max_length=64)
    profile_updated_at: str | None = Field(max_length=64)
    consent: StrictBool
    adult_confirmed: StrictBool
    demographic_fields: list[Literal["education", "sex", "ethnicity"]] = Field(default_factory=list, max_length=3)
    answers: dict[str, list[Selection | None]]


def observation_code(code, *parts):
    message = fingerprint(parts).encode()
    return "o_" + hmac.new(code.encode(), message, hashlib.sha256).hexdigest()[:32]


def fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def everyday_variable(section, question):
    return {"domain":"everyday", "section":section, "label":question["q"],
            "choices":[option["label"] for option in question["options"]]}


def catalog(quiz, overrides):
    """Return the version and response coding for the catalog actually shown to members."""
    edits = {(r["section"], r["topic"]): r for r in overrides}
    variables = {}
    indexes = Counter()
    for (section, topic), (question, phrase) in intimacy_catalog.questions().items():
        edit = edits.get((section, topic), {})
        key = f"i:{section}:{indexes[section]}"
        variables[key] = {"domain": "intimacy", "section": section, "topic": topic,
                          "label": edit.get("question") or question, "phrase": edit.get("phrase") or phrase,
                          "choices": list(CHOICES)}
        indexes[section] += 1
    for section in quiz_answers.SETS:
        for q in quiz[section]:
            variables["e:" + q["id"]] = everyday_variable(section, q)
    for key, options in quiz_answers.DEMOGRAPHIC_OPTIONS.items():
        variables["d:" + key] = {"domain": "demographics", "section": "demographics", "label": key.capitalize(),
                                "choices": [o for o in options if o != "Prefer not to say"]}
    for variable in variables.values():
        coding = [variable, CONDITIONS] if variable["domain"] == "intimacy" else [variable]
        variable["signature"] = fingerprint(coding)
    return fingerprint({key: v["signature"] for key, v in variables.items()}), variables


def intimacy_signature(variables):
    # The browser reconstructs this digest from its locally rendered version.
    sections = {}
    for v in variables.values():
        if v["domain"] == "intimacy":
            sections.setdefault(v["section"], []).append([v["topic"], v["label"], v["phrase"]])
    return fingerprint([CHOICES, CONDITIONS, sorted(sections.items())])


def age_band(member):
    age = ((member.get("about") or {}).get("demographics") or {}).get("age")
    if age is None or age == "":
        return None
    text = str(age)
    if not 1 <= len(text) <= 3 or not text.isascii() or not text.isdigit() or not 18 <= int(text) <= 120:
        raise HTTPException(403, "Adult members can participate in questionnaire research.")
    age = int(age)
    return AGE_BANDS[sum(age >= boundary for boundary in (25, 35, 45, 55, 65))]


def coded_answers(submission, variables, quiz, about):
    sizes = Counter(v["section"] for v in variables.values() if v["domain"] == "intimacy")
    answers = submission.answers
    if answers.keys() != sizes.keys() or any(len(answers[k]) != n for k, n in sizes.items()):
        raise HTTPException(422, "Answer or explicitly skip every intimacy question before sharing.")
    values, conditions = {}, {}
    for section, selections in answers.items():
        for i, item in enumerate(selections):
            key = f"i:{section}:{i}"
            if item is None:
                values[key] = None
                continue
            if (item.choice not in CHOICES or len(set(item.conditions)) != len(item.conditions)
                    or any(c not in CONDITIONS for c in item.conditions)
                    or (item.conditions and item.choice != "It depends")):
                raise HTTPException(422, "Choose a published answer and its available conditions.")
            values[key] = CHOICES.index(item.choice)
            if item.conditions:
                conditions[key] = [c for c in CONDITIONS if c in item.conditions]
    for qid, question in quiz_answers.questions_by_id(quiz).items():
        answer = quiz_answers.resolve_answer(about, quiz, question)
        if answer is not None:
            values["e:" + qid] = answer
    demographics = (about or {}).get("demographics") or {}
    for key in submission.demographic_fields:
        variable_id = "d:" + key
        options = variables[variable_id]["choices"]
        if demographics.get(key) in options:
            values[variable_id] = options.index(demographics[key])
    return values, conditions


def receipt(row=None):
    row = row or {}
    result = {"consent_version": CONSENT_VERSION, "revision": row.get("revision", 0),
              "submitted": bool(row.get("submitted"))}
    if result["submitted"]:
        values = row["values"]
        intimacy = [v for k, v in values.items() if k.startswith("i:")]
        result.update(answered=len(intimacy) - intimacy.count(None), skipped=intimacy.count(None),
                      everyday_answered=sum(k.startswith("e:") for k in values),
                      demographic_fields=sorted(k[2:] for k in values if k.startswith("d:")))
        result.update({k: row[k] for k in ("age_band", "submitted_at", "catalog_version")})
        result["submitted_consent_version"] = row["consent_version"]
    return result


def conflict():
    return HTTPException(409, "Your research settings changed. Reload them before continuing.")


def _history_submission(row):
    # Demographics live once in an independently erasable snapshot. Preserve the
    # volunteered field selection here, never duplicate age/sex/ethnicity values.
    result = {key: value for key, value in row.items()
              if key not in {"_id", "user_id", "revision", "age_band", *answer_history.FIELDS}}
    for field in ("values", "signatures"):
        if field in result:
            result[field] = {key: value for key, value in result[field].items() if not key.startswith("d:")}
    result["demographic_fields"] = sorted(key[2:] for key in row.get("values", {}) if key.startswith("d:"))
    return result


async def save_revision(db, uid, revision, fields, *, member=None):
    await answer_history.flush(db, "research_responses", {"_id": uid})
    previous = await db.research_responses.find_one({"_id": uid}) or {}
    if previous.get("revision", 0) != revision:
        raise conflict()
    row = {"_id": uid, "user_id": uid, "revision": revision + 1, **fields}
    update = {"$set": {"user_id": uid, **fields}, "$inc": {"revision": 1},
              "$unset": {key: "" for key in previous
                          if key not in {"_id", "user_id", "revision", *fields, *answer_history.FIELDS}}}
    if fields.get("submitted"):
        if member is None:
            member = await db.users.find_one({"user_id": uid},
                {"questionnaire_research_version": 1, "questionnaire_research_revision": 1,
                 "env": 1, "role": 1, "closing": 1}) or {}
        observation = answer_history.event(source="research_responses", source_id=uid, owner_id=uid,
            domain="research", question_id="submission", after=_history_submission(fields),
            question={"catalog_version": fields.get("catalog_version")}, signature=fields.get("catalog_version"),
            demographics=await answer_context.for_member(db, uid),
            consent={**answer_history.consent(member or {}), "consent_version": fields.get("consent_version"), "submitted": True},
            visibility="research", revision=revision + 1, attempt_id=f"research:{uid}:{revision + 1}",
            at=fields.get("submitted_at"), before=_history_submission(previous) if previous.get("submitted") else None,
            before_exists=bool(previous.get("submitted")), before_observed_at=previous.get("submitted_at"))
        answer_history.append(update, observation, previous)
    else:
        answer_history.redact(update, previous)
    if fields.get("submitted"):
        # Pair the owner check with the source generation read above. A minor
        # correction before this check rejects; subsequent cleanup fences/erases
        # the accepted source generation. The post-write check remains as well.
        await eligible_member(db, uid, fields.get("env"))
    try:
        result = await db.research_responses.update_one(
            answer_history.guard({"_id": uid, "revision": revision}, previous), update, upsert=revision == 0)
    except DuplicateKeyError:
        raise conflict() from None
    if not result.matched_count and result.upserted_id is None:
        raise conflict()
    await answer_history.flush(db, "research_responses", {"_id": uid}, strict=not fields.get("submitted"))
    return receipt(row)


async def status(db, uid):
    return receipt(await db.research_responses.find_one({"_id": uid}))


async def withdraw(db, uid, revision):
    return await save_revision(db, uid, revision, {"submitted": False})


async def clear(db, uid, *, include_empty=False, revision=None):
    # An empty revision prevents a save from an older tab re-creating cleared answers.
    criteria = {"_id": uid} if include_empty else {"_id": uid, "submitted": True}
    if revision is not None:
        criteria["revision"] = revision
    for _ in range(8):
        previous = await db.research_responses.find_one(criteria)
        if previous is None and not include_empty:
            return
        previous = previous or {}
        update = answer_history.redact({"$set": {"user_id": uid, "submitted": False}, "$inc": {"revision": 1},
            "$unset": {key: "" for key in previous
                       if key not in {"_id", "user_id", "submitted", "revision", *answer_history.FIELDS}}}, previous)
        try:
            result = await db.research_responses.update_one(answer_history.guard(criteria, previous), update,
                                                            upsert=include_empty and not previous)
        except DuplicateKeyError:
            if revision is not None:
                raise conflict() from None
            continue
        if result.matched_count or result.upserted_id is not None:
            await answer_history.flush(db, "research_responses", {"_id": uid}, strict=True)
            return
        if revision is not None:
            return
    raise conflict()


async def eligible_member(db, uid, env):
    member = await db.users.find_one({"user_id": uid, "role": "member", "closing": {"$ne": True},
                                      "env": {"$in": [env, None]}},
                                     {"_id": 0, "about.answers": 1, "about.demographics": 1, "about.updated_at": 1,
                                      "questionnaire_research_version": 1, "questionnaire_research_revision": 1,
                                      "env": 1, "role": 1, "closing": 1})
    if member is None:
        raise HTTPException(403, "Adult members can participate in questionnaire research.")
    return member, age_band(member)


async def submit(db, uid, env, submission, quiz, overrides):
    if not submission.consent or not submission.adult_confirmed:
        raise HTTPException(422, "Confirm adult participation and agree to this research submission.")
    version, variables = catalog(quiz, overrides)
    if submission.consent_version != CONSENT_VERSION or submission.catalog_version != version:
        raise HTTPException(409, "The questionnaire or research terms changed. Review the current version.")
    member, band = await eligible_member(db, uid, env)
    about = member.get("about") or {}
    if submission.profile_updated_at != about.get("updated_at"):
        raise HTTPException(409, "Your saved Personal answers changed. Reopen the questionnaire to review them before sharing.")
    values, conditions = coded_answers(submission, variables, quiz, about)
    # Preserve public wording separately so the member's export remains meaningful after an edit.
    await db.research_catalogs.update_one({"_id": version}, {"$setOnInsert": {"variables": variables}}, upsert=True)
    result = await save_revision(db, uid, submission.revision, {
        "submitted": True, "adult_confirmed": True, "consent_version": CONSENT_VERSION,
        "catalog_version": version, "env": env,
        "submitted_at": datetime.now(timezone.utc).isoformat(), "age_band": band,
        "values": values, "conditions": conditions, "signatures": {k: variables[k]["signature"] for k in values}})
    # Age corrections and sharing can arrive from separate tabs. Recheck after the write as well.
    try:
        await eligible_member(db, uid, env)
    except HTTPException:
        await clear(db, uid, revision=result["revision"])
        raise
    return result


async def own_export(db, uid):
    row = await db.research_responses.find_one({"_id": uid, "submitted": True})
    if row is None:
        return []
    archived = await db.research_catalogs.find_one({"_id": row["catalog_version"]})
    questions = []
    for key, value in row["values"].items():
        variable = archived["variables"][key]
        question = {"id": key, "domain": variable["domain"], "question": variable["label"],
                    "skipped": value is None, "answer": None if value is None else variable["choices"][value]}
        if key in row["conditions"]:
            question["conditions"] = row["conditions"][key]
        questions.append(question)
    return [{**{k: row[k] for k in ("consent_version", "catalog_version", "submitted_at", "age_band", "adult_confirmed")},
             "questions": questions}]
