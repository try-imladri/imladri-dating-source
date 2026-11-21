"""Explicit Your Type research permission, structured exports and cohort summaries.

Private preferences remain usable with research off. No partner access, paid AI
calls, population estimates or demographic inference is performed here.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import re
import time

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import answer_history, couple_research, personal_demographics, questionnaire_report, quiz_answers, research, research_trends
from .report_dates import instant
from .quiz import TYPES


VERSION = "your-type-research-v2"
NOTICE_VERSION = "your-type-sensitive-research-v2"
DOMAIN = "your_type"
MAX_MEMBERS = 50000
# Leave one row for explicit truncation detection within the gateway's 100k cap.
MAX_EVENTS = 99999
MAX_SCAN_SECONDS = 20
REPORT_BATCH = 8
VARIABLES = {
    "audience": {"label": "Chosen image audience", "choices": ["women", "men", "both"]},
    "sexuality": {"label": "Self-reported sexuality at attempt start", "choices": ["straight", "bi", "gay", "lesbian"]},
    "viewer_gender": {"label": "Self-reported viewer gender at attempt start",
                      "choices": ["Woman", "Man", "Non-binary", "Another gender"]},
    "rated_audience": {"label": "Recorded portrait audience at attempt start", "choices": ["women", "men", "both"]},
    "progress": {"label": "Crowds completed", "choices": ["0", "1–4", "5–19", "20 or more"]},
    "signal": {"label": "Recorded labeled examples", "choices": ["0–11", "12–59", "60 or more"]},
    "age_band": {"label": "Age range", "choices": list(research.AGE_BANDS)},
    "height_band": {"label": "Height range", "choices": questionnaire_report.HEIGHT_BANDS},
    "p:mbti": {"label": "Self-reported personality type", "choices": list(TYPES)},
    **{"d:" + key: {"label": "Self-reported " + key, "choices": [item for item in values if item != "Prefer not to say"]}
       for key, values in personal_demographics.OWN_OPTIONS.items()},
}
PREFERENCE_VERSION = "your-type-preferences-v1"
PREFERENCE_KEYS = {"version", "sexuality", "viewer_gender", "rated_audience"}
RESEARCH_ACTIONS = frozenset("your_type." + kind for kind in (
    "session_started", "retake_started", "like", "pass", "next_crowd", "undo", "pause", "resume", "finish", "batch_committed"))
RESEARCH_REQUEST_FIELDS = frozenset(("kind", "face_id", "viewed_face_ids", "dwell_ms", "action_id", "expected_revision",
    "batch_id", "request_id", "audience", "adult_confirmed", "retake_of"))
STATED_PREFERENCES_EXCLUDED = "Private stated age and height preferences are not included in this research permission or its exports."


def preference_snapshot(value, audience=None):
    """Accepted attempt metadata only; never infer sexuality from rated faces.

    Legacy attempts have no snapshot. Current profile values cannot fill that
    gap, and malformed, future-edition or mismatched snapshots stay unknown.
    """
    if (not isinstance(value, dict) or set(value) != PREFERENCE_KEYS
            or value.get("version") != PREFERENCE_VERSION
            or not isinstance(value.get("sexuality"), str) or value["sexuality"] not in VARIABLES["sexuality"]["choices"]
            or value.get("viewer_gender") not in [*VARIABLES["viewer_gender"]["choices"], "Prefer not to say", None]
            or (value.get("viewer_gender") is not None and not isinstance(value["viewer_gender"], str))
            or not isinstance(value.get("rated_audience"), str) or value["rated_audience"] not in VARIABLES["rated_audience"]["choices"]
            or (audience is not None and value["rated_audience"] != audience)):
        return None
    return {key: value[key] for key in ("version", "sexuality", "viewer_gender", "rated_audience")}


def group_coverage(bank, group, audience):
    """Count only explicit, fully known positive annotations in this edition."""
    cached = bank.get("_research_audience_coverage", {}).get((tuple(group.get("indices", [])), audience))
    if cached is not None:
        return cached
    applicable = audience in group.get("applicable_audiences", ["men", "women"])
    assets = [asset for asset in bank.get("assets", []) if asset.get("audience") == audience]
    indices = group.get("indices", [])
    counts = {value: 0 for value in group.get("values", [])}
    covered = 0
    if applicable:
        for asset in assets:
            features, masks = asset.get("features", []), asset.get("feature_masks", [])
            if not indices or not all(index < len(features) and index < len(masks) and masks[index] is True for index in indices):
                continue
            positive = [value for index, value in zip(indices, group.get("values", [])) if features[index] > 0]
            if positive:
                covered += 1
                for value in positive:
                    counts[value] += 1
    return {"applicable": applicable, "coverage_available": covered > 0, "covered_portraits": covered,
        "total_portraits": len(assets), "variation_available": sum(value > 0 for value in counts.values()) >= 2,
        "value_counts": counts}


def feature_variables(bank, model_edition=None):
    from . import your_type_model as learner, your_type_model_v1 as legacy
    edition = model_edition or learner.EDITION
    independent = edition != legacy.EDITION
    variables = {}
    for group in bank.get("feature_groups", []):
        for audience in (("men", "women") if independent else (None,)):
            coverage = group_coverage(bank, group, audience) if audience else None
            if coverage and not coverage["applicable"]:
                continue
            for index, value in zip(group["indices"], group["values"]):
                label = (group.get("value_labels") or {}).get(value, value)
                prefix = "feature:" + (audience + ":" if audience else "")
                variables[prefix + str(index)] = {"label": ((audience.title() + " portrait choices · ") if audience else "")
                    + group.get("label", group["id"]) + " · " + label,
                    "choices": ["more_often_favored", "less_often_favored"], "feature_index": index,
                    "feature_group": group["id"], "portrait_audience": audience,
                    "asset_edition": bank.get("edition"), "asset_manifest_sha256": bank.get("sha256"), "model_edition": edition,
                    **({"annotation_coverage": coverage} if coverage else {}),
                    "meaning": "Authored visual cue association inside this exact model and asset edition, not an inferred personal attribute."}
    return variables


def variables_for(bank=None, quiz=None, model_edition=None):
    variables = dict(VARIABLES)
    if bank:
        variables.update(feature_variables(bank, model_edition))
    for qid, question in quiz_answers.questions_by_id(quiz or {}).items():
        variables["e:" + qid] = {"label": question["q"], "choices": [option["label"] for option in question["options"]],
            "question": question, "domain": "everyday"}
    return variables


def instrument_variables(banks, edition=None, quiz=None):
    """Do not attach one edition's coverage to a shared feature index."""
    variables = variables_for(None, quiz)
    for identity, schema in banks.items():
        if edition is not None and research.fingerprint(identity) != edition:
            continue
        for key, value in feature_variables(schema, identity[1]).items():
            if key in variables:
                # A feature ID is reused by independent-model editions, while
                # its image evidence and coverage remain instrument-specific.
                shared = variables[key]
                for field in ("asset_edition", "asset_manifest_sha256", "model_edition", "annotation_coverage"):
                    shared.pop(field, None)
                shared["requires_edition_selection"] = True
            else:
                variables[key] = value
    return variables


def catalog():
    from . import your_type, your_type_model
    bank = your_type.catalog()
    counts = Counter(asset["audience"] for asset in bank["assets"])
    return {"name": "Your Type", "asset_edition": bank["edition"], "asset_manifest_sha256": bank["sha256"],
        "model_edition": your_type_model.EDITION, "assets": sum(counts.values()), "audiences": dict(counts),
        "cards_per_crowd": your_type.CARDS, "recommended_crowds": your_type.RECOMMENDED_CROWDS,
        "required_likes": your_type.REQUIRED_LIKES,
        "actions": ["start", "like", "pass", "next_crowd", "undo", "pause", "resume", "finish", "retake", "delete"],
        "recorded": ["Asset and model edition", "Audience selected explicitly", "Pre-choice predictions",
                     "Self-reported sexuality, viewer gender and portrait-audience snapshot at attempt start",
                     "Issued and viewed portraits", "Likes, explicit passes and weak viewed passes",
                     "Skipped portraits", "Dwell declarations", "Undo links and revisions",
                     "Prospective prediction pairs", "Demographic snapshot reference", "Consent version"],
        "rights": bank.get("rights") or bank.get("provenance") or {},
        "feature_groups": [{**deepcopy(group), "audience_coverage": {
            audience: group_coverage(bank, group, audience) for audience in ("men", "women")}}
            for group in bank.get("feature_groups", [])], "features": bank.get("feature_names", []),
        "catalog_schema_version": bank.get("schema_version"),
        "research_exclusions": [STATED_PREFERENCES_EXCLUDED],
        "association_scope": "Current independent models describe each portrait audience separately; earlier combined models retain their original edition and are never split retrospectively.",
        "cost": {"ai_calls": 0, "message_credits": 0},
        "meaning": "An adaptive description of choices within an original synthetic-adult portrait bank. It does not measure personality or compatibility.",
        "population_baseline": "No population preference baseline is shown without eligible real-user evidence."}


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_member: str = Field(min_length=1, max_length=200)
    version: str = Field(max_length=48)
    notice_version: str | None = Field(default=None, max_length=80)
    enabled: StrictBool
    adult_confirmed: StrictBool = False
    revision: int = Field(ge=0, lt=2**53)


def preferences(member):
    return {"version": VERSION, "enabled": member.get("your_type_research_version") == VERSION,
            "revision": member.get("your_type_research_revision", 0),
            "requires_expanded_consent": member.get("your_type_research_version") == "your-type-research-v1",
            "demographic_comparisons_enabled": member.get("questionnaire_research_version") == questionnaire_report.CONSENT,
            "stated_preferences_included": False, "stated_preferences_notice": STATED_PREFERENCES_EXCLUDED}


def consent_update(row, body, *, adult_eligible=False, at=None):
    """Validate and build the existing Your Type research change, without I/O."""
    uid = row["user_id"]
    if body.expected_member != uid:
        raise HTTPException(409, "The signed-in account changed. Reopen Your Type research preferences.")
    if row.get("your_type_research_revision", 0) != body.revision:
        raise HTTPException(409, "Your research settings changed. Reload before continuing.")
    if body.version != VERSION and (body.enabled or body.version != "your-type-research-v1"):
        raise HTTPException(409, "Review the current Your Type research permission.")
    if body.enabled:
        if body.notice_version != NOTICE_VERSION:
            raise HTTPException(409, "Review the expanded Your Type research disclosure before opting in.")
        if not body.adult_confirmed or not adult_eligible:
            raise HTTPException(403, "Only adults can share Your Type research observations.")
    if (body.enabled and preferences(row)["enabled"]) or (not body.enabled and not row.get("your_type_research_version")):
        return None
    at = at or datetime.now(timezone.utc).isoformat()
    revision = body.revision + 1
    update = {"$set": {"your_type_research_revision": revision, "your_type_research_at": at}}
    if body.enabled:
        update["$set"]["your_type_research_version"] = VERSION
    else:
        update["$unset"] = {"your_type_research_version": ""}
    return update


async def consent(db, member, body):
    uid = member["user_id"]
    if body.expected_member != uid:
        raise HTTPException(409, "The signed-in account changed. Reopen Your Type research preferences.")
    row = await db.users.find_one({"user_id": uid, "closing": {"$ne": True}})
    if not row:
        raise HTTPException(409, "Your research settings changed. Reload before continuing.")
    adult = await db.users.find_one(questionnaire_report.adult_filter({"user_id": uid}), {"_id": 1}) if body.enabled else False
    update = consent_update(row, body, adult_eligible=bool(adult))
    if update is None:
        return preferences(row)
    at = update["$set"]["your_type_research_at"]
    revision = body.revision + 1
    event = answer_history.event(source="users", source_id=uid, owner_id=uid, actor_id=uid,
        domain="consent", question_id="your_type.research", after={"enabled": body.enabled, "version": VERSION,
            "requested_version": body.version, "notice_version": body.notice_version if body.enabled else None},
        question={"label": "Share Your Type choices, sexuality, viewer gender and visual features for optional research"},
        consent=answer_history.consent(row), revision=revision, at=at)
    answer_history.append(update, event, row)
    accepted = await db.users.update_one(answer_history.guard({"_id": row["_id"],
        "your_type_research_revision": row.get("your_type_research_revision", {"$exists": False})}, row), update)
    if not accepted.matched_count:
        raise HTTPException(409, "Your research settings changed. Reload before continuing.")
    await answer_history.flush(db, "users", {"_id": row["_id"]})
    return preferences(await db.users.find_one({"_id": row["_id"]}))


def cohort(base):
    return questionnaire_report.adult_filter({"$and": [base,
        questionnaire_report.exact_fields(your_type_research_version=VERSION)]})


def event_query(member, uid):
    return {"source": "your_type_sessions", "owner_id": uid, "actor_id": uid, "domain": DOMAIN,
        "kind": {"$in": sorted(RESEARCH_ACTIONS)}, "purged": {"$ne": True},
        "baseline": {"$ne": True}, "consent.your_type_research_version": VERSION,
        "consent.your_type_research_revision": member.get("your_type_research_revision"),
        "consent.your_type_research_at": member.get("your_type_research_at")}


def action_counts(event):
    if event.get("kind") not in RESEARCH_ACTIONS:
        return Counter()
    kind = event.get("kind", "").removeprefix("your_type.")
    if kind == "batch_committed":
        return Counter({"transport_batches": 1})
    counts = Counter({"accepted_action_events": 1})
    if kind in {"session_started", "retake_started", "like", "pass", "next_crowd", "undo", "pause", "resume", "finish"}:
        counts[kind] += 1
    after = event.get("after") or {}
    effects = after.get("effects") or {}
    initial = after.get("state") or {}
    counts["issued_portraits"] += len(effects.get("issued") or []) + len(initial.get("cards") or [])
    counts["declared_view_events"] += len(effects.get("declared_views") or [])
    counts["skipped_portraits"] += len(effects.get("skipped") or [])
    for choice in effects.get("choices") or []:
        value = choice.get("choice")
        if value in {"like", "pass", "viewed_pass"}:
            counts["choice_" + value] += 1
    for field in ("pairs", "correct", "ties", "explicit_pairs", "viewed_pairs"):
        value = (effects.get("prospective") or {}).get(field)
        if type(value) is int and value >= 0:
            counts["prospective_" + field] += value
    return counts


def instrument(session):
    return (session.get("asset_edition", "unrecorded"), session.get("model_edition", "unrecorded"),
            session.get("asset_manifest_sha256", "unrecorded"))


def instrument_banks(current=None):
    """Local immutable schemas only; no saved session is upgraded or relabeled."""
    from . import your_type_model as learner, your_type_model_v1 as legacy
    result = {}
    if current:
        result[(current["edition"], learner.EDITION, current["sha256"])] = current
    from . import your_type, your_type_legacy
    archived_v2 = your_type.catalog_v2()
    result[(archived_v2["edition"], learner.EDITION, archived_v2["sha256"])] = archived_v2
    try:
        archived = your_type_legacy.catalog()
    except HTTPException:
        archived = None
    if archived:
        result[(archived["edition"], legacy.EDITION, archived["sha256"])] = archived
    # The immutable annotation scan is shared by all members in this request;
    # never rescan the full portrait bank for each participant's saved model.
    return {identity: {**schema, "_research_audience_coverage": {
        (tuple(group["indices"]), audience): group_coverage(schema, group, audience)
        for group in schema.get("feature_groups", []) for audience in ("men", "women")}}
        for identity, schema in result.items()}


def association_values(model, bank, prefix, audience=None):
    from .your_type_model_v1 import MIN_LIKES, group_evidence
    if (not isinstance(model, dict) or type(model.get("likes")) is not int or model["likes"] < MIN_LIKES
            or any(type(model.get(key, 0)) is not int for key in ("explicit_passes", "viewed_passes"))
            or model.get("explicit_passes", 0) + model.get("viewed_passes", 0) < 3):
        return {}
    weights = model.get("weights") or []
    observed = model.get("feature_observations") or []
    options = model.get("feature_option_counts") or []
    if not all(isinstance(value, list) for value in (weights, observed, options)):
        return {}
    result = {}
    for group in bank.get("feature_groups", []):
        if audience:
            coverage = group_coverage(bank, group, audience)
            if not coverage["applicable"] or ("assets" in bank and not coverage["variation_available"]):
                continue
        # Validate counts before handing them to the original model's evidence rule.
        indices = group.get("indices", [])
        if not all(type(index) is int and 0 <= index < min(len(observed), len(options), len(weights) - 1)
                   and type(observed[index]) is int and type(options[index]) is int
                   and 0 <= options[index] <= observed[index] for index in indices):
            continue
        for index in group_evidence(model, group)["eligible_indices"]:
            weight = weights[index + 1]
            if type(weight) in (int, float) and math.isfinite(weight) and abs(weight) > .03:
                result[prefix + str(index)] = "more_often_favored" if weight > 0 else "less_often_favored"
    return result


def latest_values(member, session, bank=None, quiz=None):
    from . import your_type_model as learner, your_type_model_v1 as legacy
    state = session.get("state") or {}
    count = state.get("completed_crowds", 0)
    samples = (state.get("model") or {}).get("samples", 0)
    if isinstance(samples, list):
        samples = len(samples)
    values = {"audience": session.get("audience")}
    snapshot = preference_snapshot(session.get("preference_snapshot"), session.get("audience"))
    if snapshot:
        for key in ("sexuality", "viewer_gender", "rated_audience"):
            if snapshot[key] in VARIABLES[key]["choices"]:
                values[key] = snapshot[key]
    if type(count) is int and count >= 0:
        values["progress"] = "0" if count == 0 else "1–4" if count < 5 else "5–19" if count < 20 else "20 or more"
    if type(samples) is int and samples >= 0:
        values["signal"] = "0–11" if samples < 12 else "12–59" if samples < 60 else "60 or more"
    if member.get("questionnaire_research_version") == questionnaire_report.CONSENT:
        try:
            values["age_band"] = research.age_band(member)
        except HTTPException:
            pass
        demo = (member.get("about") or {}).get("demographics") or {}
        if (member.get("about") or {}).get("mbti") in TYPES:
            values["p:mbti"] = member["about"]["mbti"]
        height = personal_demographics.number(demo.get("height_cm"), quiz_answers.HEIGHT_MIN_CM, quiz_answers.HEIGHT_MAX_CM)
        if height is not None:
            values["height_band"] = questionnaire_report.HEIGHT_BANDS[sum(height >= edge for edge in (150, 160, 170, 180, 190, 200))]
        for key, options in personal_demographics.OWN_OPTIONS.items():
            if demo.get(key) in options and demo[key] != "Prefer not to say":
                values["d:" + key] = demo[key]
        for qid, question in quiz_answers.questions_by_id(quiz or {}).items():
            selected = quiz_answers.resolve_answer(member.get("about") or {}, quiz, question)
            if selected is not None:
                values["e:" + qid] = question["options"][selected]["label"]
    # A saved weight index is meaningful only within its exact instrument. The
    # new aggregate counter object is never a fallback for an audience model.
    if (bank and session.get("asset_manifest_sha256") == bank.get("sha256")
            and (bank.get("edition") is None or session.get("asset_edition") == bank["edition"])
            and type(count) is int and count >= 20):
        if session.get("model_edition") == legacy.EDITION:
            values.update(association_values(state.get("model"), bank, "feature:"))
        elif session.get("model_edition") == learner.EDITION and session.get("engine_version") == (
                2 if bank.get("edition") == "your-type-synthetic-adults-v2" else 3):
            models = state.get("models_by_audience") or {}
            crowds = state.get("completed_crowds_by_audience") or {}
            if isinstance(models, dict) and isinstance(crowds, dict):
                selected = ("men", "women") if session.get("audience") == "both" else (session.get("audience"),)
                for audience in selected:
                    if audience in ("men", "women") and type(crowds.get(audience)) is int and crowds[audience] >= 20:
                        values.update(association_values(models.get(audience), bank, "feature:" + audience + ":", audience))
    return values


async def report(db, eligible, x="audience", y="progress", edition=None, quiz=None):
    from . import your_type
    try:
        bank = your_type.catalog()
    except HTTPException:
        bank = None
    banks = instrument_banks(bank)
    variables = instrument_variables(banks, edition, quiz)
    if x not in variables or y not in variables or x == y:
        raise HTTPException(422, "Choose two different Your Type research variables.")
    if edition is not None and (not isinstance(edition, str) or not re.fullmatch(r"[0-9a-f]{64}", edition)):
        raise HTTPException(422, "Choose a recorded asset and model edition.")
    counts = [[0] * len(variables[y]["choices"]) for _ in variables[x]["choices"]]
    totals = Counter()
    editions = Counter()
    actions = Counter()
    scanned_events = 0
    scanned_members = 0
    query_batches = 0
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    truncated = False
    async def read(collection, query, projection, maximum, order=None):
        cursor = collection.find(query, projection)
        if order:
            cursor.sort(order)
        cursor.limit(maximum)
        try:
            return await cursor.to_list(maximum)
        finally:
            await cursor.close()

    # Page cohorts, source sessions and archive events in small bounded batches.
    # A final batch user read rechecks every exact grant before any contribution.
    # No per-person gateway RPC is needed, and profile notes are not selected.
    after_uid = None
    while not truncated:
        page_query = cohort(eligible)
        if after_uid is not None:
            page_query = {"$and": [page_query, {"user_id": {"$gt": after_uid}}]}
        members = await read(db.users, page_query, {"user_id": 1, "your_type_research_at": 1,
            "your_type_research_revision": 1}, REPORT_BATCH, [("user_id", 1)])
        if not members:
            break
        query_batches += 1
        scanned_members += len(members)
        if scanned_members > MAX_MEMBERS or time.monotonic() > deadline:
            truncated = True
            break
        after_uid = members[-1]["user_id"]
        by_uid = {row["user_id"]: row for row in members}
        source_sessions = await read(db.your_type_sessions,
            {"user_id": {"$in": list(by_uid)}, "deleted": {"$ne": True}, "status": {"$nin": ["deleted", "clearing"]}},
            {"user_id": 1, "created_at": 1, "updated_at": 1, "audience": 1, "preference_snapshot": 1, "asset_edition": 1,
             "asset_manifest_sha256": 1, "model_edition": 1, "engine_version": 1, "state.model": 1,
             "state.models_by_audience": 1, "state.completed_crowds_by_audience": 1, "state.completed_crowds": 1},
            REPORT_BATCH * your_type.MAX_SESSIONS + 1, [("updated_at", -1), ("_id", -1)])
        if len(source_sessions) > REPORT_BATCH * your_type.MAX_SESSIONS:
            truncated = True
            break
        latest, attempt_counts = {}, Counter()
        eligible_sessions = {uid: [] for uid in by_uid}
        for session in source_sessions:
            uid = session["user_id"]
            since = instant(by_uid[uid].get("your_type_research_at"))
            started = instant(session.get("created_at"))
            if since and started and started >= since:
                attempt_counts[uid] += 1
                latest.setdefault(uid, session)
                eligible_sessions[uid].append(str(session["_id"]))
        event_scopes = [{**event_query(row, row["user_id"]), "source_id": {"$in": eligible_sessions[row["user_id"]]}}
                        for row in members if eligible_sessions[row["user_id"]]]
        source_events = await read(db.answer_history,
            {"$or": event_scopes},
            {"owner_id": 1, "kind": 1, "at": 1, "after.effects": 1, "after.state.cards": 1},
            MAX_EVENTS - scanned_events + 1) if event_scopes else []
        scanned_events += len(source_events)
        if scanned_events > MAX_EVENTS or time.monotonic() > deadline:
            truncated = True
            break
        own_actions = {uid: Counter() for uid in by_uid}
        for event in source_events:
            uid = event["owner_id"]
            observed, since = instant(event.get("at")), instant(by_uid[uid].get("your_type_research_at"))
            if observed and since and observed >= since:
                own_actions[uid].update(action_counts(event))
        refreshed = await read(db.users, {"$and": [cohort(eligible), {"user_id": {"$in": list(by_uid)}}]},
            {"user_id": 1, "your_type_research_at": 1, "your_type_research_revision": 1,
             "questionnaire_research_version": 1, "about.demographics": 1, "about.mbti": 1,
             "about.answers": {"$arrayToObject": {"$map": {"input": couple_research.object_entries("$about.answers"),
                "as": "a", "in": {"k": "$$a.k", "v": {"choice": "$$a.v.choice", "question": "$$a.v.question", "label": "$$a.v.label"}}}}}}, REPORT_BATCH)
        for member in refreshed:
            uid = member["user_id"]
            if any(member.get(key) != by_uid[uid].get(key) for key in ("your_type_research_at", "your_type_research_revision")):
                continue
            totals["consenting_adults"] += 1
            actions.update(own_actions[uid])
            if uid not in latest:
                continue
            totals["attempts"] += attempt_counts[uid]
            totals["members_with_sessions"] += 1
            edition_pair = instrument(latest[uid])
            values = latest_values(member, latest[uid], banks.get(edition_pair), quiz)
            editions[edition_pair] += 1
            if edition is not None and research.fingerprint(edition_pair) != edition:
                continue
            if values.get(x) in variables[x]["choices"] and values.get(y) in variables[y]["choices"]:
                counts[variables[x]["choices"].index(values[x])][variables[y]["choices"].index(values[y])] += 1
                totals["paired_members"] += 1
        if len(members) < REPORT_BATCH:
            break
    edition_available = edition is None or edition in {research.fingerprint(pair) for pair in editions}
    compatible = edition is not None or len(editions) <= 1
    if edition is None and len(editions) == 1:
        observed_instrument = next(iter(editions))
        if observed_instrument in banks:
            exact = instrument_variables(banks, research.fingerprint(observed_instrument), quiz)
            # An axis selected before the cohort was known can be unavailable
            # in its schema. Preserve its label without borrowing other evidence.
            for axis in (x, y):
                if axis not in exact:
                    exact[axis] = {key: value for key, value in variables[axis].items() if key not in {
                        "asset_edition", "asset_manifest_sha256", "model_edition", "annotation_coverage",
                        "requires_edition_selection"}}
                    exact[axis]["not_recorded_in_selected_instrument"] = True
            variables = exact
    result = research_trends.summarize(counts) if not truncated and compatible else {"available": False, "sample_size": None,
                "rows": [], "columns": [], "cramers_v": None}
    return {"version": VERSION, "unit": "member", "x": x, "y": y,
        "variables": [{"id": key, "domain": value.get("domain", DOMAIN), **{k: v for k, v in value.items() if k != "question"}} for key, value in variables.items()],
        "x_label": variables[x]["label"], "y_label": variables[y]["label"],
        "x_choices": variables[x]["choices"], "y_choices": variables[y]["choices"], **result,
        "denominators": {key: value for key, value in totals.items() if key != "paired_members"}, "truncated": truncated,
        "scan": {"member_batches": query_batches, "member_batch_size": REPORT_BATCH,
                 "members_scanned": scanned_members, "archived_events_scanned": scanned_events},
        "activity": dict(actions), "activity_meaning": "Accepted historical actions after current research permission; undo remains a separate event. Issued portraits and declared views are distinct, and prospective pairs share faces.",
        "minimum_paired": research_trends.MIN_PAIRED, "minimum_cell": research_trends.MIN_CELL,
        "minimum": research_trends.MIN_PAIRED, "round_to": research_trends.ROUND_TO,
        "retakes": "Attempts remain separate; only the latest attempt started after the current permission per adult enters correlations. Retake immediately to share a new attempt.",
        "population_baseline": {"available": False, "reason": "No validated crowd baseline is published by this report."},
        "cost": {"ai_calls": 0, "message_credits": 0},
        "edition_cohorts": len(editions), "edition": edition,
        "edition_choices": [{"id": research.fingerprint(pair), "asset_edition": pair[0], "model_edition": pair[1],
                             "asset_manifest_sha256": pair[2]} for pair in sorted(editions)],
        "edition_compatible": compatible,
        "edition_available": edition_available,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "instrument": edition or (research.fingerprint(next(iter(editions))) if len(editions) == 1 else "choose-edition"),
        "demographics": "Profile values and everyday answers use current volunteered records only with current questionnaire consent; appearance never infers demographics. Historical event exports retain a pseudonymous demographic-version reference.",
        "preference_metadata": "Sexuality, viewer gender and rated audience use the explicit snapshot saved when that attempt began, under current Your Type research permission. Legacy or unverified snapshots are unknown; current profile edits never relabel older attempts. Prefer not to say and missing values are excluded from comparisons.",
        "visual_associations": "Audience-qualified variables use only that audience's independent saved model. Earlier combined models remain bound to their frozen schema and are not split retrospectively.",
        "stated_preferences_included": False, "stated_preferences_notice": STATED_PREFERENCES_EXCLUDED}


async def member_admin(db, uid):
    from . import research_rows
    query = {"user_id": uid, "deleted": {"$ne": True}, "status": {"$nin": ["deleted", "clearing"]}}
    attempts_count = await db.your_type_sessions.count_documents(query)
    member = await db.users.find_one({"user_id": uid}, {"user_id": 1, "your_type_research_version": 1,
        "your_type_research_revision": 1, "questionnaire_research_version": 1, "env": 1, "row_member_env": 1})
    if not member:
        raise HTTPException(404, "Member not found.")
    latest = await db.your_type_sessions.find(query, {"_id": 0, "state.completed_crowds": 1, "id": 1,
        "asset_edition": 1, "model_edition": 1, "updated_at": 1, "created_at": 1, "audience": 1,
        "preference_snapshot": 1}).sort("updated_at", -1).limit(1).to_list(1)
    env = member.get("env") or member.get("row_member_env")
    grant_query = cohort(research_rows.eligible_members({"user_id": uid, "env": {"$in": [env, None]}}, "member")) if env in ("live", "preview") else None
    grant_fields = ("your_type_research_at", "your_type_research_revision", "row_member_at", "row_member_version", "row_member_code", "row_member_env")
    grant = await db.users.find_one(grant_query, {key: 1 for key in grant_fields}) if grant_query else None
    if latest:
        row = latest[0]
        snapshot = preference_snapshot(row.pop("preference_snapshot", None), row.pop("audience", None))
        sid = row.pop("id", None)
        started = instant(row.get("created_at"))
        permission = instant((grant or {}).get("your_type_research_at"))
        row_permission = instant((grant or {}).get("row_member_at"))
        allowed = bool(grant and permission and row_permission and started and started >= max(permission, row_permission))
        if allowed and snapshot:
            unchanged = await db.users.find_one({"$and": [grant_query,
                questionnaire_report.exact_fields(**{key: grant.get(key) for key in grant_fields})]}, {"_id": 1})
            if unchanged:
                row["preference_snapshot"] = snapshot
                row["attempt_code"] = research.observation_code("m_" + grant["row_member_code"], "attempt", sid) if sid else None
    return {"attempts": attempts_count, "research": preferences(member),
        "latest": latest[0] if latest else None, "private_choices_included": False,
        "ai_calls": 0, "message_credits": 0,
        "row_export_requires_separate_grant": True, "preference_snapshot_requires_both_current_grants": True}


async def member_csv(db, eligible, uid, stamp):
    from . import research_rows
    granted = research_rows.eligible_members(eligible, "member")
    candidate = await db.users.find_one({"$and": [cohort(granted), {"user_id": uid}]}, {"row_member_code": 1})
    if not candidate:
        raise HTTPException(404, "No currently consented Your Type record export is available.")
    code = research_rows.member_code(candidate["row_member_code"])
    exact_grant = {"$and": [granted, {"row_member_code": candidate["row_member_code"]}]}
    async def rows():
        async for row in member_rows(db, exact_grant, uid, code, stamp):
            yield {"exported_at_utc": stamp, "scope": "member", "unit_code": code, "member_code": code, **row}
    return await research_rows.export_response(rows(), "member", stamp, code)


async def member_rows(db, eligible_grant, uid, code, stamp):
    from .research_rows import grant_active
    query = {"$and": [cohort(eligible_grant), {"user_id": uid}]}
    member = await db.users.find_one(query, {"your_type_research_at": 1, "your_type_research_revision": 1,
        "row_member_at": 1, "row_member_version": 1, "row_member_code": 1, "row_member_env": 1})
    if (not member or not grant_active(member, "member") or member.get("row_member_env") not in ("live", "preview")
            or code != "m_" + member["row_member_code"]):
        return
    permission, row_permission = instant(member.get("your_type_research_at")), instant(member.get("row_member_at"))
    if not permission or not row_permission:
        return
    since = max(permission, row_permission)
    query = {"$and": [query, questionnaire_report.exact_fields(**{key: member[key]
        for key in ("row_member_version", "row_member_code", "row_member_env")})]}
    end = instant(stamp)
    if not member or not since or not end:
        return
    from .your_type import MAX_SESSIONS
    sessions = await db.your_type_sessions.find({"user_id": uid, "deleted": {"$ne": True}, "status": {"$nin": ["deleted", "clearing"]}},
        {"created_at": 1, "audience": 1, "preference_snapshot": 1, "attempt_id": 1}).limit(MAX_SESSIONS + 1).to_list(MAX_SESSIONS + 1)
    if len(sessions) > MAX_SESSIONS:
        raise HTTPException(503, "This research export exceeded its attempt safety limit.")
    allowed = [str(row["_id"]) for row in sessions if instant(row.get("created_at")) and instant(row["created_at"]) >= since]
    snapshots = {str(row["_id"]): preference_snapshot(row.get("preference_snapshot"), row.get("audience"))
                 for row in sessions if str(row["_id"]) in allowed}
    attempts = {str(row["_id"]): row.get("attempt_id") for row in sessions if str(row["_id"]) in allowed}
    if not allowed:
        return
    cursor = db.answer_history.find({**event_query(member, uid), "source_id": {"$in": allowed}}).sort([("at", 1), ("id", 1)])
    async for event in cursor:
        at = instant(event.get("at"))
        if not at or not since <= at <= end:
            continue
        if not await db.users.find_one({"$and": [query, {"your_type_research_at": member["your_type_research_at"],
                "row_member_at": member.get("row_member_at")} ]}, {"_id": 1}):
            return
        source = await db.your_type_sessions.find_one({"user_id": uid, "_id": event.get("source_id"),
            "deleted": {"$ne": True}, "status": {"$nin": ["deleted", "clearing"]}},
            {"audience": 1, "preference_snapshot": 1, "attempt_id": 1})
        if source is None:
            continue
        payload = event.get("after") or {}
        # Structured own action data only. Model internals and before/after state
        # stay in the owner's full export, never in this row research dataset.
        selected = {key: payload[key] for key in ("action_id", "request", "batch_id", "action_ids", "first_revision", "last_revision",
            "asset_edition", "asset_manifest_sha256", "model_edition", "effects") if key in payload}
        if isinstance(selected.get("request"), dict):
            selected["request"] = {key: value for key, value in selected["request"].items()
                                   if key in RESEARCH_REQUEST_FIELDS}
        snapshot = preference_snapshot(payload.get("preference_snapshot"))
        if (snapshot and snapshot == snapshots.get(str(event.get("source_id")))
                and snapshot == preference_snapshot(source.get("preference_snapshot"), source.get("audience"))
                and event.get("attempt_id") is not None
                and event.get("attempt_id") == attempts.get(str(event.get("source_id"))) == source.get("attempt_id")):
            selected["preference_snapshot"] = snapshot
        else:
            snapshot = None
        if isinstance(payload.get("state"), dict):
            selected["initial_issued"] = payload["state"].get("cards", [])
        context = event.get("demographics") or {}
        provenance = {"revision": event.get("revision"), "attempt_code": research.observation_code(code, "attempt", event.get("attempt_id")),
            "demographic_context_code": research.observation_code(code, "demographics", context.get("snapshot_id")) if context.get("snapshot_id") else None,
            "demographic_observed_at": context.get("observed_at"), "kind": event.get("kind"),
            "preference_snapshot_version": snapshot["version"] if snapshot else None,
            "preference_snapshot_status": "self_reported_at_attempt_start" if snapshot else "unknown"}
        # Source validation performs I/O. Recheck permission after that await,
        # immediately before yielding; no subsequent database await may expose
        # a row after a withdrawal observed during source verification.
        if not await db.users.find_one({"$and": [query, {"your_type_research_at": member["your_type_research_at"],
                "row_member_at": member.get("row_member_at")} ]}, {"_id": 1}):
            return
        yield {"domain": DOMAIN, "section": "visual_preferences", "variable_id": event.get("question_id", ""),
            "observation_code": research.observation_code(code, DOMAIN, event["id"]),
            "observed_on_utc": at.isoformat(), "edition": research.fingerprint(instrument(payload)),
            "question": "Batch transport receipt" if event.get("kind") == "your_type.batch_committed" else "Recorded Your Type action",
            "status": "transport_receipt" if event.get("kind") == "your_type.batch_committed" else "recorded_action",
            "answer": json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "conditions": json.dumps(provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":"))}
