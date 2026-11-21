"""Map explicitly shared Couples answers to their published numeric choices."""
try:
    from . import couples_catalog, couples_journey, report_dates, research
except ImportError:
    import couples_catalog, couples_journey, report_dates, research


RESULTS = ["helped", "mixed", "didnt_help", "not_tried"]
RESULT_LABELS = ["Helped", "Mixed", "Didn’t help", "We didn’t try it"]


async def variables(db):
    releases = await db.couples_settings.find_one({"_id":couples_catalog.config_key()},
        {"previous":1,"research_published_ids":1}) or {}
    previous = {p["id"]:p for p in releases.get("previous", [])}
    witnessed = set(releases.get("research_published_ids", [])) | set(previous) | {p["id"] for p in couples_journey.PROMPTS}
    published, retained = await couples_journey.catalog(db, witnessed)
    current = {p["id"] for p in published}
    fields = {}
    for key,item in {**previous, **retained}.items():
        label = item["question"] if key in current else "Archived · " + item["question"]
        if item["choices"]:
            fields["c:prompt:" + key] = {"domain": "couples", "label": label, "choices": item["choices"],
                "signature": research.fingerprint(["prompt:" + key, item["question"], item["choices"]])}
        fields["c:checkin:prompt:" + key] = checkin_variable(label)
    for kind, spec in couples_journey.REFLECTIONS.items():
        fields["c:checkin:" + kind] = checkin_variable(spec["title"])
    fields["c:checkin:any"] = checkin_variable("all conversations")
    return fields


def checkin_variable(label):
    return {"domain": "couples", "label": "Latest Couples check-in · " + label, "choices": RESULT_LABELS}


def array_or_empty(field):
    return {"$cond": [{"$isArray": field}, field, []]}


def object_entries(field):
    return {"$objectToArray": {"$cond": [{"$eq": [{"$type": field}, "object"]}, field, {}]}}


def shared_prompt_code(uid, pair, side, key, variable):
    """Use only the actor's explicitly released choice from this exact edition."""
    if not isinstance(variable.get("signature"), str) or len(variable["signature"]) != 64:
        return {"$literal": None}
    def seat_value(path, default=None):
        return {"$switch": {"branches": [
            {"case": {"$eq": ["$$side", "a"]}, "then": path + ".a"},
            {"case": {"$eq": ["$$side", "b"]}, "then": path + ".b"}], "default": default}}
    index = "$$selected.index"
    bounded = {"$and": [{"$eq": [{"$type": index}, "int"]},
                         {"$gte": [index, 0]}, {"$lt": [index, len(variable["choices"])]}]}
    safe_index = {"$cond": [bounded, index, -1]}
    label = {"$arrayElemAt": [{"$literal": variable["choices"]}, safe_index]}
    owner = {"$switch": {"branches": [
        {"case": {"$eq": ["$$side", "a"]}, "then": "$$pair.inviter"},
        {"case": {"$eq": ["$$side", "b"]}, "then": "$$pair.invitee"}], "default": None}}
    valid = {"$and": [bounded, {"$in": ["$$side", ["a", "b"]]}, {"$eq": ["$$owner", owner]},
        {"$eq": ["$$thread.kind", "prompt"]}, {"$eq": ["$$thread.prompt_id", {"$literal": key}]},
        {"$eq": ["$$thread.title", {"$literal": variable["label"].removeprefix("Archived · ")}]},
        {"$eq": ["$$selected.edition", {"$literal": variable.get("signature")}]},
        {"$in": [label, array_or_empty("$$released")]}]}
    thread = {"$getField": {"field": {"$literal": key}, "input": {
        "$cond": [{"$eq": [{"$type": "$$pair.journey.threads"}, "object"]}, "$$pair.journey.threads", {}]}}}
    return {"$let": {"vars": {"pair": pair, "side": side, "owner": uid}, "in": {
        "$let": {"vars": {"thread": thread}, "in": {
            "$let": {"vars": {"selected": seat_value("$$thread.shared_choices"),
                               "released": seat_value("$$thread.shares", [])},
                     "in": {"$cond": [valid, index, None]}}}}}}}


def checkin_code(pair, side, kind):
    threads = {"$let": {"vars": {"pair": pair}, "in": object_entries("$$pair.journey.threads")}}
    if kind != "any":
        field, value = ("prompt_id", kind[7:]) if kind.startswith("prompt:") else ("kind", kind)
        threads = {"$filter": {"input": threads, "cond": {"$eq": ["$$this.v." + field, {"$literal": value}]}}}
    check = {"$switch": {"branches": [
        {"case": {"$eq": [side, "a"]}, "then": "$$agreement.checkins.a"},
        {"case": {"$eq": [side, "b"]}, "then": "$$agreement.checkins.b"}], "default": None}}
    # Each member or pair counts once in a comparison, even after many follow-ups.
    stamp = "$$c.updated_at"
    # Historical date-only entries represent midnight UTC. All newer writes include an offset.
    date_only = {"$regexMatch": {"input": {"$cond": [{"$eq": [{"$type": stamp}, "string"]}, stamp, ""]},
                                 "regex": r"^\d{4}-\d{2}-\d{2}$"}}
    time = {"$cond": [date_only, {"$multiply": [{"$toLong": report_dates.mongo_timestamp(stamp)}, 1000]},
                      report_dates.instant_micros(stamp)]}
    undated = {"$or": [{"$in": [{"$type": stamp}, ["missing", "null"]]}, {"$eq": [stamp, ""]}]}
    candidates = {"$reduce": {"input": threads, "initialValue": [], "in": {"$concatArrays": [
        "$$value", {"$map": {"input": array_or_empty("$$this.v.agreements"), "as": "agreement",
                            "in": {"$let": {"vars": {"c": check}, "in": {
                                "time": time, "undated": undated,
                                "code": {"$indexOfArray": [RESULTS, "$$c.result"]}}}}}}]}}}
    # Old check-ins have no timestamp. When multiple equally recent entries
    # disagree, there is no defensible latest value for that comparison.
    saved = {"$filter": {"input": candidates, "cond": {"$and": [
        {"$gte": ["$$this.code", 0]}, {"$or": ["$$this.undated", {"$ne": ["$$this.time", None]}]}]}}}
    latest = {"$filter": {"input": "$$saved", "cond": {"$eq": ["$$this.time", "$$last_time"]}}}
    codes = {"$setUnion": [[], {"$map": {"input": latest, "in": "$$this.code"}}]}
    unique = {"$cond": [{"$eq": [{"$size": "$$codes"}, 1]}, {"$arrayElemAt": ["$$codes", 0]}, -1]}
    last_code = {"$let": {"vars": {"codes": codes}, "in": unique}}
    return {"$let": {"vars": {"saved": saved}, "in": {"$let": {
        "vars": {"last_time": {"$max": "$$saved.time"}}, "in": last_code}}}}


def seat_stages(uid_expr, pair_expr, side_expr, axes, prefix):
    """Build numeric values for one respondent. Caller supplies eligible person and active pair."""
    checkins = {k: checkin_code(pair_expr, side_expr, k[len("c:checkin:"):]) for k in axes if k.startswith("c:checkin:")}
    prompts = {key: shared_prompt_code(uid_expr, pair_expr, side_expr, key[len("c:prompt:"):], variable)
               for key, variable in axes.items() if key.startswith("c:prompt:")}
    stages = [{"$set": {prefix: prompts}}]
    if checkins:
        stages.append({"$set": {prefix: {"$mergeObjects": ["$" + prefix, checkins]}}})
    return stages
