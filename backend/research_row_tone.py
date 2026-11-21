"""Prospective, explicitly reported feedback. No free-form or model-generated content."""
try:
    from .report_dates import instant
    from . import couple_research, personal_demographics, research
except ImportError:
    from report_dates import instant
    import couple_research, personal_demographics, research


MEASURES = {
    "fit": ("Did the member report that the tone read fit?", ("fits", "corrected")),
    "sent": ("Did the member report sending the reply?", ("Yes", "No")),
    "helped": ("Did the member report that the reply they sent helped?", ("Yes", "No")),
}


def permission_since(member, scope):
    if member.get("tone_research_version") != personal_demographics.TONE_CONSENT:
        return None
    granted = instant(member.get(f"row_{scope}_at"))
    tone = instant(member.get("tone_research_at"))
    return max(granted, tone) if granted and tone else None


def feedback_rows(read, code, earliest, latest):
    at = instant(read.get("created_at"))
    if not earliest or not latest or not at or not earliest <= at <= latest:
        return
    if not isinstance(read.get("id"), str) or not read["id"] or read.get("status") not in ("estimated", "insufficient"):
        return
    observation = research.observation_code(code, "tone-read", read["id"])
    for key, (question, choices) in MEASURES.items():
        raw = read.get(key)
        if key == "fit":
            if read["status"] != "estimated" or raw not in choices:
                continue
            answer = raw
        else:
            if not isinstance(raw, bool) or (key == "helped" and read.get("sent") is not True):
                continue
            answer = "Yes" if raw else "No"
        yield {"domain": "tone_feedback", "section": "conversation", "variable_id": "tone:" + key,
               "observation_code": observation, "observed_on_utc": at.date().isoformat(),
               "edition": research.fingerprint(["self-report-v1", key, question, choices]),
               "question": question, "status": "answered", "answer": answer}


def projection(prefix):
    return {"id": prefix + "id", "created_at": prefix + "created_at", "status": prefix + "tone.status",
            **{key: prefix + "tone_feedback." + key for key in MEASURES}}


async def individual(db, eligible_grant, uid, code, stamp):
    member = await db.users.find_one({"$and": [eligible_grant, {"user_id": uid,
            "tone_research_version": personal_demographics.TONE_CONSENT}]},
        {"_id": 0, "tone_research_version": 1, "tone_research_at": 1, "row_member_at": 1, "row_member_env": 1})
    earliest, end = permission_since(member or {}, "member"), instant(stamp)
    if not earliest or not end or earliest > end:
        return
    cursor = db.exchanges.aggregate([
        {"$match": {"user_id": uid, "env": {"$in": [member["row_member_env"], None]}, "tone.status": {"$in": ["estimated", "insufficient"]},
                    "$or": [{"tone_feedback.fit": {"$in": ["fits", "corrected"]}},
                            {"tone_feedback.sent": {"$type": "bool"}}]}},
        {"$sort": {"created_at": 1, "_id": 1}}, {"$project": {"_id": 0, **projection("$")}},
    ], allowDiskUse=True)
    async for read in cursor:
        for row in feedback_rows(read, code, earliest, end):
            yield row


def shared_projection():
    sessions = couple_research.object_entries("$tone_sessions")
    seats = {}
    for side in ("a", "b"):
        prefix = "$$item.v." + side + "."
        seats[side] = {"$cond": [{"$eq": [prefix + "mode", "shared"]},
            {**projection(prefix), "actor": prefix + "user_id", "pair": prefix + "pair_id",
             "env": prefix + "env", "revision": prefix + "source_version"}, None]}
    allowed = {"$and": [{"$eq": ["$" + side + ".tone_research_version", personal_demographics.TONE_CONSENT]}
                        for side in ("a", "b")]}
    return {"$cond": [allowed, {"$map": {"input": sessions, "as": "item",
                                         "in": {"key": "$$item.k", **seats}}}, []]}


def shared(pair, code, seat_codes, stamp):
    a, b = (permission_since(pair[s], "couple") for s in ("a", "b"))
    end = instant(stamp)
    if not a or not b or not end:
        return
    revisions = {t["id"]: t.get("words_version") for t in pair["threads"]}
    for session in pair.get("tone_reads", []):
        revision = revisions.get(session["key"])
        if type(revision) is not int or revision < 1:
            continue
        for side, role in (("a", "inviter"), ("b", "invitee")):
            read = session.get(side)
            if (not read or read.get("actor") != pair[side]["user_id"] or read.get("env") != pair["env"]
                    or read.get("pair") != pair["_id"] or type(read.get("revision")) is not int
                    or read["revision"] != revision):
                continue
            for row in feedback_rows(read, code, max(a, b), end):
                yield {**row, "seat": role, "member_code": seat_codes[side]}
