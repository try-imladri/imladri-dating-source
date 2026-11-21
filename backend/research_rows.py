"""Explicit grants and pseudonymous exports of current, structured questionnaire records."""
import csv
import hashlib
import io
import json
import re
import secrets
from bisect import bisect_right, insort
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument

try:
    from . import couple_research, intimacy_catalog, personal_demographics, questionnaire_report, quiz_answers, research, research_row_tone, trends_export, your_type_research
    from .quiz import TYPES
except ImportError:
    import couple_research, intimacy_catalog, personal_demographics, questionnaire_report, quiz_answers, research, research_row_tone, trends_export, your_type_research
    from quiz import TYPES


Scope = Literal["member", "couple"]
VERSIONS = {"member": "row-member-v1", "couple": "row-couple-v1"}
HEX = re.compile(r"[0-9a-f]{32}")
COLUMNS = ("exported_at_utc", "scope", "unit_code", "seat", "member_code", "observation_code", "observed_on_utc",
           "domain", "section", "variable_id", "edition", "question", "status", "answer", "conditions")


class ExportConsent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_member: str = Field(min_length=1, max_length=256)
    expected_pair_id: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool
    adult_confirmed: bool = False
    revision: int = Field(ge=0, lt=2**53)
    version: str = Field(min_length=1, max_length=40)


def grant_active(member, scope):
    prefix = "row_" + scope + "_"
    return (member.get(prefix + "version") == VERSIONS[scope]
            and isinstance(member.get(prefix + "code"), str) and bool(HEX.fullmatch(member[prefix + "code"])))


def member_code(raw):
    return "m_" + raw


def pair_code(pair_id, inviter_code, invitee_code):
    payload = json.dumps(["pair-row-v1", str(pair_id), inviter_code, invitee_code], separators=(",", ":"))
    return "c_" + hashlib.sha256(payload.encode()).hexdigest()[:32]


def check_code(scope, code):
    if code is not None and not re.fullmatch(("m_" if scope == "member" else "c_") + "[0-9a-f]{32}", code):
        raise HTTPException(422, "Choose a research code from this export.")


def eligible_members(base, scope):
    env = base["env"]
    if isinstance(env, dict):
        env = next(value for value in env["$in"] if value is not None)
    prefix = "row_" + scope + "_"
    grant = {prefix + "version": VERSIONS[scope], prefix + "env": env,
             prefix + "code": {"$regex": "^[0-9a-f]{32}$"}}
    # An indexed field predicate also matches elements of arrays. A grant must contain scalars.
    scalar = {"$and": [{"$eq": [{"$type": "$" + key}, "string"]} for key in grant]}
    return questionnaire_report.adult_filter({"$and": [base, grant, {"$expr": scalar}]})


async def current_pair(db, uid, env):
    pair = await db.couples_pairs.find_one({"env": env, "state": "active", "members": uid,
                                            "$or": [{"inviter": uid}, {"invitee": uid}]}, {"_id": 1})
    return pair["_id"] if pair else None


def preference(member, scope, env, pair_id=None):
    prefix = "row_" + scope + "_"
    active = grant_active(member, scope) and member.get(prefix + "env") == env
    result = {"version": VERSIONS[scope], "revision": member.get(prefix + "revision", 0), "enabled": active}
    if scope == "couple":
        current = bool(active and pair_id and pair_id == member.get("row_couple_pair_id"))
        result.update(enabled=current, pair_id=pair_id, previous_pair_grant=bool(active and not current))
    return result


async def preferences(db, member, env):
    pair = await current_pair(db, member["user_id"], env) if member.get("role") == "member" else None
    return {scope: preference(member, scope, env, pair) for scope in VERSIONS}


def consent_update(member, env, scope, body, *, pair_id=None, at=None):
    uid = member["user_id"]
    if uid != body.expected_member or body.version != VERSIONS[scope]:
        raise HTTPException(409, "The account or permission changed. Reopen Settings.")
    prefix = "row_" + scope + "_"
    if body.revision != member.get(prefix + "revision", 0):
        raise HTTPException(409, "This permission changed. Refresh to see its current status.")
    query = {"user_id": uid, "closing": {"$ne": True}, prefix + "revision": body.revision or {"$in": [0, None]}}
    update = {"$inc": {prefix + "revision": 1}}
    if body.enabled:
        if not body.adult_confirmed or member.get("role") != "member":
            raise HTTPException(403, "Adult confirmation is required to share coded records with staff.")
        research.age_band(member)
        if scope == "couple" and (pair_id is None or pair_id != body.expected_pair_id):
            raise HTTPException(409, "Your active couple changed. Review the permission for your current couple.")
        query.update(role="member")
        query["about.demographics.age"] = ((member.get("about") or {}).get("demographics") or {}).get("age")
        # Preserve the public code when explicitly resaving an already active grant.
        active = preference(member, scope, env, pair_id)["enabled"]
        fields = {prefix + "version": body.version, prefix + "env": env}
        if not active:
            fields.update({prefix + "code": secrets.token_hex(16), prefix + "at": at or datetime.now(timezone.utc).isoformat()})
        if scope == "couple":
            fields["row_couple_pair_id"] = pair_id
        update["$set"] = fields
    else:
        update["$unset"] = {key: "" for key in personal_demographics.ROW_CLEAR_FIELDS if key.startswith(prefix)}
    return query, update


async def consent(db, member, env, scope, body):
    pair_id = await current_pair(db, member["user_id"], env) if scope == "couple" else None
    query, update = consent_update(member, env, scope, body, pair_id=pair_id)
    prefix = "row_" + scope + "_"
    result = await db.users.find_one_and_update(query, update, return_document=ReturnDocument.AFTER,
        projection={**{k: 1 for k in personal_demographics.ROW_CLEAR_FIELDS if k.startswith(prefix)}, prefix + "revision": 1})
    if not result:
        raise HTTPException(409, "This permission or account changed. Refresh to see its current status.")
    return preference(result, scope, env, pair_id)


def demographic_rows(member):
    about = member.get("about") or {}
    demo = about.get("demographics") or {}
    for key, name, choices, raw, bounds, cuts in [
        ("age_band", "Age range", research.AGE_BANDS, demo.get("age"), (18, 120), [25, 35, 45, 55, 65]),
        ("height_band", "Height range", questionnaire_report.HEIGHT_BANDS, demo.get("height_cm"),
         (quiz_answers.HEIGHT_MIN_CM, quiz_answers.HEIGHT_MAX_CM), [150, 160, 170, 180, 190, 200]),
        *(("d:" + k, k.capitalize(), options, demo.get(k), (), []) for k, options in personal_demographics.OWN_OPTIONS.items()),
        ("p:mbti", "Self-reported personality type", list(TYPES), about.get("mbti"), (), []),
    ]:
        if bounds:
            number = personal_demographics.number(raw, *bounds)
            answer = choices[bisect_right(cuts, number)] if number is not None else None
        else:
            answer = raw if raw != "Prefer not to say" and raw in choices else None
        status = "answered" if answer is not None else ("withheld" if raw == "Prefer not to say" else
                 "unanswered" if raw is None or raw == "" else "unavailable")
        domain = "personal" if key == "p:mbti" else "demographics"
        yield {"domain":domain, "section":domain, "variable_id":key, "edition":research.fingerprint([key, list(choices), cuts]),
               "question":name, "status":status, "answer":answer}


def form_row(key, section, domain, edition, question, status, answer=None, conditions=None):
    return {"variable_id":key, "section":section, "domain":domain, "edition":edition, "question":question,
            "status":status, "answer":answer, "conditions":json.dumps(conditions, ensure_ascii=False) if conditions else ""}


def everyday_rows(about, quiz, variables, previous):
    answers = about.get("answers") or {}
    skipped = set(about.get("skipped") or [])
    skip_editions = about.get("skip_editions") or {}
    current = quiz_answers.questions_by_id(quiz)
    history = quiz.get("legacy_wording") or {}
    for qid, q in current.items():
        key = "e:" + qid
        v = variables[key]
        selected = quiz_answers.resolve_answer(about, quiz, q)
        skip = skip_editions.get(qid, {})
        unchanged_skip = qid in skipped and (not skip or skip.get("edition") == v["signature"])
        status = "answered" if selected is not None else "skipped" if unchanged_skip else "unanswered"
        # Historical skip records have no instrument. Keep that uncertainty visible.
        edition = "" if status == "skipped" and not skip else v["signature"]
        yield form_row(key, v["section"], v["domain"], edition, v["label"], status,
                       v["choices"][selected] if selected is not None else None)
    for qid in skipped:
        old = skip_editions.get(qid) or {}
        key = "e:" + qid
        if (old.get("section") in quiz_answers.SETS and isinstance(old.get("question"), str)
                and re.fullmatch(r"[0-9a-f]{64}", str(old.get("edition", "")))
                and old["edition"] != variables.get(key, {}).get("signature")):
            yield form_row(key, old["section"], "everyday", old["edition"], old["question"], "archived_skipped")
    for qid, raw in answers.items():
        if not isinstance(raw, dict) or (qid in current and quiz_answers.resolve_answer(about, quiz, current[qid]) is not None):
            continue
        # Use only known published wording, even for legacy rows which also contain a freeform note.
        old = history.get(qid)
        previous_q = previous.get(qid)
        candidates = [c for c in (old, quiz_answers.wording(previous_q) if previous_q else None) if c]
        for candidate in candidates:
            if "question" in raw or "label" in raw:
                if raw.get("question") != candidate["q"] or raw.get("label") not in candidate["labels"]:
                    continue
                choice = raw["label"]
            elif candidate == old and type(raw.get("choice")) is int and 0 <= raw["choice"] < len(candidate["labels"]):
                choice = candidate["labels"][raw["choice"]]
            else:
                continue
            yield form_row("e:"+qid, "archived", "everyday", research.fingerprint([qid, candidate]),
                           candidate["q"], "archived_answered", choice)
            break
        else:
            if qid in current or qid in history or qid in previous:
                yield form_row("e:"+qid, "archived", "everyday", "", "", "unavailable")


def intimacy_rows(doc, variables):
    doc = doc or {}
    values = doc.get("values") or {}
    signatures = doc.get("signatures") or {}
    current = {k:v for k,v in variables.items() if v["domain"] == "intimacy"}
    for key in dict.fromkeys([*current, *values]):
        variable = current.get(key)
        entry = re.fullmatch(r"i:([a-zA-Z0-9_-]{1,100}):[0-9]{1,6}", key)
        if not variable and not entry:
            continue
        section = variable["section"] if variable else entry[1]
        found = key in values
        same = bool(found and variable and signatures.get(key) == variable["signature"])
        selected = values.get(key)
        valid = type(selected) is int and 0 <= selected < len(research.CHOICES)
        answer = research.CHOICES[selected] if valid else None
        tags = (doc.get("conditions") or {}).get(key, []) if answer == "It depends" else []
        tags = [tag for tag in research.CONDITIONS if isinstance(tags, list) and tag in tags]
        if variable:
            status = "unanswered"
            if same:
                status = "skipped" if selected is None else "answered" if valid else "unavailable"
            yield form_row(key, section, "intimacy", variable["signature"], variable["label"], status,
                           answer if same else None, tags if same else None)
        if found and not same:
            # The application saves this wording straight from the published catalog, separately from member text.
            old = (doc.get("wording") or {}).get(key) or {}
            archived = signatures.get(key, "")
            if (re.fullmatch(r"[0-9a-f]{64}", str(archived)) and isinstance(old.get("topic"), str)
                    and isinstance(old.get("question"), str) and 0 < len(old["question"]) <= 650
                    and ((selected is None and old.get("answer") is None) or (valid and old.get("answer") == answer))):
                yield form_row(key, section, "intimacy", archived, old["question"],
                               "archived_skipped" if selected is None else "archived_answered", answer, tags)
            else:
                yield form_row(key, section, "intimacy", "", "", "unavailable")


async def member_rows(db, eligible, stamp, settings, unit_code=None):
    quiz = settings["quiz"]
    _, variables = research.catalog(quiz, await intimacy_catalog.overrides(db))
    published = await db.settings.find_one({"key":"global"}, {"everyday_previous.sets":1,"_id":0}) or {}
    previous = quiz_answers.questions_by_id((published.get("everyday_previous") or {}).get("sets") or {})
    query = eligible_members(eligible, "member")
    if unit_code:
        query = {"$and": [query, {"row_member_code":unit_code[2:]}]}
    # Look up only structured selections. The export cursor never loads notes or profile narratives.
    answers = {"$arrayToObject":{"$map":{"input":couple_research.object_entries("$about.answers"),
               "as":"a","in":{"k":"$$a.k","v":{"choice":"$$a.v.choice","question":"$$a.v.question","label":"$$a.v.label"}}}}}
    profile_fields = {"about.demographics."+key: 1 for key in ("age", "height_cm", *personal_demographics.OWN_OPTIONS)}
    projection = {"_id": 0, "user_id": 1, **profile_fields, "about.mbti": 1, "about.skipped": 1,
                  "about.skip_editions": 1, "about.answers": answers}
    cursor = db.users.find(query, {"_id": 0, "user_id": 1, "row_member_code": 1}).sort("row_member_code", 1)
    async for candidate in cursor:
        grant = candidate["row_member_code"]
        uid = candidate["user_id"]
        doc = await db.intimacy_answers.find_one(
            {"_id": uid, "$expr": {"$eq": ["$adult_confirmed", True]}},
            {"_id": 0, "values": 1, "conditions": 1, "wording": 1, "signatures": 1})
        # Earlier units may take time to stream. Reload the current selections and the same grant together.
        member = await db.users.find_one({"$and": [query, {"user_id": uid, "row_member_code": grant}]}, projection)
        if not member:
            continue
        code = member_code(grant)
        prefix = {"exported_at_utc":stamp,"unit_code":code,"member_code":code,"scope":"member"}
        for collection in (demographic_rows(member), everyday_rows(member.get("about") or {}, quiz, variables, previous),
                           intimacy_rows(doc, variables)):
            for entry in collection:
                yield {**prefix, **entry}
        tone_grant = {"$and": [query, {"row_member_code": grant}]}
        async for entry in research_row_tone.individual(db, tone_grant, member["user_id"], code, stamp):
            yield {**prefix, **entry}
        async for entry in your_type_research.member_rows(db, tone_grant, member["user_id"], code, stamp):
            yield {**prefix, **entry}


async def export_response(source, scope, stamp, code):
    first = await anext(source, None)
    if first is None and code is not None:
        raise HTTPException(404, "This research code has no eligible record.")
    async def chunks():
        out = io.StringIO(newline="")
        writer = csv.writer(out)
        writer.writerow(COLUMNS)
        row = first
        try:
            while row is not None:
                writer.writerow([trends_export.spreadsheet_cell(row.get(column, "")) for column in COLUMNS])
                if out.tell() >= 32768:
                    yield out.getvalue()
                    out.seek(0)
                    out.truncate()
                row = await anext(source, None)
            if out.tell():
                yield out.getvalue()
        finally:
            await source.aclose()
    filename = f'imladri-{scope}-{code or "all"}-{stamp[:10]}.csv'
    return StreamingResponse(chunks(), media_type="text/csv", headers={"Cache-Control":"private, no-store",
                             "X-Research-Empty": "1" if first is None else "0",
                             "Content-Disposition": f'attachment; filename="{filename}"'})


async def page(db, eligible, scope, after, limit, couple_codes):
    check_code(scope, after)
    if scope == "member":
        query = eligible_members(eligible, scope)
        if after:
            query = {"$and":[query, {"row_member_code":{"$gt":after[2:]}}]}
        result = await db.users.find(query, {"row_member_code":1,"_id":0}).sort("row_member_code",1).limit(limit+1).to_list(limit+1)
        codes = [member_code(row["row_member_code"]) for row in result]
    else:
        codes = []
        async for code in couple_codes(db, eligible):
            if (not after or code > after) and (len(codes) <= limit or code < codes[-1]):
                insort(codes, code)
                del codes[limit+1:]
    return {"items": codes[:limit], "next": codes[limit-1] if len(codes)>limit else None}
