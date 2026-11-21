"""Current, explicitly shared fixed-choice data for mutually consenting active pairs."""
from bson import BSON

try:
    from . import couples, couples_analytics, couples_catalog, couples_journey, couple_research, personal_demographics, questionnaire_report, research, research_row_tone, research_rows
except ImportError:
    import couples, couples_analytics, couples_catalog, couples_journey, couple_research, personal_demographics, questionnaire_report, research, research_row_tone, research_rows


SEATS = {"a": "inviter", "b": "invitee"}


def shared_at(path):
    # A member's words never leave MongoDB. Only whether that seat shared any lines is projected.
    return {"$gt": [{"$size": couple_research.array_or_empty(path)}, 0]}


def joint_threads():
    thread_list = couple_research.object_entries("$journey.threads")
    mutual = {"$filter": {"input": thread_list, "as": "thread", "cond": {"$and": [
        shared_at("$$thread.v.shares.a"), shared_at("$$thread.v.shares.b"),
    ]}}}
    accepted = {"$filter": {"input": couple_research.array_or_empty("$$thread.v.agreements"), "as": "ag",
                            "cond": {"$and": [{"$in": ["$$ag.status", ["active", "retired"]]},
                                {"$setEquals": [couple_research.array_or_empty("$$ag.accepted"), ["a", "b"]]}]}}}
    # Check-in outcomes, like the member-facing reveal, are available only when both sides shared.
    jointly_shared = {"$and": [{"$eq": [f"$$ag.checkins.{side}.shared", True]} for side in SEATS]}
    checks = {side: {"result": f"$$ag.checkins.{side}.result", "at": f"$$ag.checkins.{side}.updated_at"}
              for side in SEATS}
    agreements = {"$map": {"input": accepted, "as": "ag", "in": {
        "id": "$$ag.id", "revision": "$$ag.revision", "accepted_at": "$$ag.accepted_at",
        "checks": {"$cond": [jointly_shared, checks, None]},
    }}}
    return {"$map": {"input": mutual, "as": "thread", "in": {
        "id": "$$thread.k", "kind": "$$thread.v.kind", "prompt_id": "$$thread.v.prompt_id",
        "card": "$$thread.v.card", "mutual_at": "$$thread.v.mutual_at",
        "words_version": {"$ifNull": ["$$thread.v.words_version", "$$thread.v.version"]},
        "choices": {side: f"$$thread.v.shared_choices.{side}" for side in SEATS},
        "agreements": agreements,
    }}}


def candidate_pairs(db, eligible, content, pair_id=None):
    env = eligible["env"]
    if isinstance(env, dict):
        env = next(value for value in env["$in"] if value is not None)
    match = {"env": env, "state": "active", "$expr": {"$and": [
        {"$eq": [{"$type": "$inviter"}, "string"]}, {"$eq": [{"$type": "$invitee"}, "string"]},
        {"$ne": ["$inviter", "$invitee"]},
        {"$setEquals": [couple_research.array_or_empty("$members"), ["$inviter", "$invitee"]]},
    ]}}
    if pair_id is not None:
        match["_id"] = pair_id
    fields = {"_id": 0, "user_id": 1, "row_couple_code": 1, "couples_blocked": 1}
    if content:
        fields.update({"row_member_code": 1, "row_member_version": 1, "row_member_env": 1,
                       "row_member_revision": 1, "row_couple_at": 1,
                       "row_couple_version": 1, "row_couple_env": 1, "row_couple_revision": 1,
                       "tone_research_version": 1, "tone_research_at": 1, "tone_research_revision": 1})
        for key in ("age", "height_cm", *personal_demographics.OWN_OPTIONS):
            fields["about.demographics." + key] = 1
    pipeline = [{"$match": match}]
    for side, role in SEATS.items():
        pipeline.extend([{"$lookup": {"from": "users", "localField": role, "foreignField": "user_id",
            "let": {"pair": "$_id"}, "pipeline": [
                {"$match": research_rows.eligible_members(eligible, "couple")},
                {"$match": questionnaire_report.couples_adult_attestation()},
                {"$match": {"$expr": {"$eq": ["$row_couple_pair_id", "$$pair"]}}},
                {"$project": fields}], "as": side}}, {"$unwind": "$" + side}])
    project = {"a": 1, "b": 1, "env": 1}
    if content:
        project.update({"joined_at": 1, "journey_version": 1, "threads": joint_threads(),
                        "tone_reads": research_row_tone.shared_projection()})
    return db.couples_pairs.aggregate([*pipeline, {"$sort": {"_id": 1}}, {"$project": project}])


def unit_id(pair):
    a, b = pair["a"], pair["b"]
    if not couples.incompatible(a, b):
        return research_rows.pair_code(pair["_id"], a["row_couple_code"], b["row_couple_code"])


async def iter_codes(db, eligible):
    async for candidate in candidate_pairs(db, eligible, False):
        code = unit_id(candidate)
        if code:
            yield code


def day(value):
    at = couples_analytics.timestamp(value)
    return at.date().isoformat() if at else ""


def instrument(thread, variables):
    key = thread.get("prompt_id")
    if isinstance(key, str) and "c:prompt:" + key in variables:
        row = variables["c:prompt:" + key]
        return "prompt:" + key, row["label"].removeprefix("Archived · "), row["choices"]
    card_id = thread.get("card")
    card = couples_catalog.CARDS.get(card_id) if isinstance(card_id, str) else None
    if card:
        return "card:" + card["id"], card["question"], []
    kind = thread.get("kind")
    reflection = couples_journey.REFLECTIONS.get(kind) if isinstance(kind, str) else None
    if reflection:
        return "reflection:" + thread["kind"], reflection["title"], []
    if isinstance(key, str) and "c:checkin:prompt:" + key in variables:
        row = variables["c:checkin:prompt:" + key]
        return "prompt:" + key, row["label"].removeprefix("Latest Couples check-in · ").removeprefix("Archived · "), []
    return None


def thread_rows(thread, variables, code, seat_codes):
    spec = instrument(thread, variables)
    if not spec:
        return
    measure, title, options = spec
    edition = research.fingerprint([measure, title, options])
    base = {"domain": "couples", "section": measure, "edition": edition, "seat": "joint",
            "observation_code": research.observation_code(code, "thread", thread["id"])}
    yield {**base, "variable_id": "c:shared:" + measure, "question": title,
           "status": "observed", "answer": "Both shared", "observed_on_utc": day(thread.get("mutual_at"))}
    for side, role in SEATS.items():
        release = (thread.get("choices") or {}).get(side) or {}
        choice = release.get("index") if isinstance(release, dict) else None
        if type(choice) is int and 0 <= choice < len(options) and release.get("edition") == edition:
            yield {**base, "variable_id": "c:" + measure, "seat": role, "member_code": seat_codes[side],
                   "question": title, "status": "answered", "answer": options[choice], "observed_on_utc": day(release.get("at"))}
    for agreement in thread["agreements"]:
        if not agreement.get("id"):
            continue
        obs = research.observation_code(code, "agreement", thread["id"], agreement["id"], agreement.get("revision"))
        agreement_row = {**base, "observation_code": obs}
        yield {**agreement_row, "variable_id": "c:agreement:" + measure, "question": title,
               "status": "observed", "answer": "Both accepted", "observed_on_utc": day(agreement.get("accepted_at"))}
        checks = agreement.get("checks") or {}
        if all((checks.get(s) or {}).get("result") in couple_research.RESULTS for s in SEATS):
            for side, role in SEATS.items():
                check = checks[side]
                yield {**agreement_row, "variable_id": "c:checkin:" + measure, "seat": role,
                       "member_code": seat_codes[side], "edition": research.fingerprint([edition, couple_research.RESULTS]),
                       "question": "Shared check-in · " + title,
                       "status": "answered", "answer": check["result"], "observed_on_utc": day(check.get("at"))}


async def iter_rows(db, eligible, stamp, unit_code=None):
    variables = await couple_research.variables(db)
    # A source cursor or the streaming consumer can yield while a member withdraws
    # permission or shared content. Revalidate the same structured snapshot after
    # those awaits and before every emitted row, never mixing revisions in a unit.
    async for candidate in candidate_pairs(db, eligible, False):
        code = unit_id(candidate)
        if not code or (unit_code is not None and unit_code != code):
            continue
        async for pair in candidate_pairs(db, eligible, True, candidate["_id"]):
            if unit_id(pair) != code:
                continue
            snapshot = BSON.encode(pair)
            for row in unit_rows(pair, variables, code, stamp):
                if not await current_unit_matches(db, eligible, pair["_id"], code, snapshot):
                    break
                # No await may separate this authorization check from the yield.
                yield row
        if unit_code is not None:
            return


async def current_unit_matches(db, eligible, pair_id, code, snapshot):
    # This one current aggregation joins both live grants/adult/member state and
    # the active pair. Its projection has no private wording. Exact typed BSON
    # comparison includes grant revisions, demographics, shared choices/check-ins,
    # journey revision and independently mutable tone feedback.
    async for current in candidate_pairs(db, eligible, True, pair_id):
        return unit_id(current) == code and BSON.encode(current) == snapshot
    return False


def unit_rows(pair, variables, code, stamp):
    common = {"exported_at_utc": stamp, "scope": "couple", "unit_code": code}
    yield {**common, "seat": "joint", "domain": "participation", "section": "couples",
           "variable_id": "c:active", "edition": "1", "question": "Pair status",
           "status": "observed", "answer": "Active pair", "observed_on_utc": day(pair.get("joined_at"))}
    linked = all(research_rows.grant_active(pair[s], "member") and pair[s].get("row_member_env") == pair["env"] for s in SEATS)
    seat_codes = {s: research_rows.member_code(pair[s]["row_member_code"]) if linked else "" for s in SEATS}
    for side, role in SEATS.items():
        for field in research_rows.demographic_rows(pair[side]):
            if field["domain"] == "demographics":
                yield {**common, "seat": role, "member_code": seat_codes[side], **field}
    for thread in pair["threads"]:
        for field in thread_rows(thread, variables, code, seat_codes):
            yield {**common, **field}
    for field in research_row_tone.shared(pair, code, seat_codes, stamp):
        yield {**common, **field}
