"""MongoDB projections used by ongoing questionnaire comparisons."""

try:
    from . import couple_research, personal_demographics, quiz_answers, research
    from .quiz import TYPES
except ImportError:
    import couple_research, personal_demographics, quiz_answers, research
    from quiz import TYPES


CONSENT = "all-answers-v1"
AGE = "age_band"
HEIGHT = "height_band"
HEIGHT_BANDS = ["Under 150 cm", "150–159 cm", "160–169 cm", "170–179 cm", "180–189 cm", "190–199 cm", "200 cm or more"]
UNITS = ("member", "couple", "couple_inviter", "couple_invitee")


def additional_variables():
    return {
        HEIGHT: {"domain": "demographics", "label": "Height range", "choices": HEIGHT_BANDS},
        "d:gender": {"domain": "demographics", "label": "Gender", "choices": [
            value for value in quiz_answers.GENDER_OPTIONS if value != "Prefer not to say"]},
        "d:sexuality": {"domain": "demographics", "label": "Sexuality", "choices": list(personal_demographics.SEXUALITY_OPTIONS)},
        "p:mbti": {"domain": "personal", "label": "Self-reported personality type", "choices": list(TYPES)},
    }


def literal(value):
    return {"$literal": value}


def field(key, obj):
    return {"$getField": {"field": literal(key), "input": obj}}


def bounded(value, size):
    return {"$and": [{"$eq": [{"$type": value}, "int"]}, {"$gte": [value, 0]}, {"$lt": [value, size]}]}


def couples_adult_attestation():
    # Legacy confirmations may be BSON dates. Match a scalar so arrays containing dates do not qualify.
    return {"couples_adult_at": {"$ne": ""},
            "$expr": {"$in": [{"$type": "$couples_adult_at"}, ["string", "date"]]}}


def exact_fields(**values):
    # Keep indexed equality predicates, but reject arrays which MongoDB also matches by element.
    return {**values, "$expr": {"$and": [{"$eq": ["$" + key, value]} for key, value in values.items()]}}


def ongoing_members(eligible):
    return {"$and": [adult_filter(eligible), exact_fields(questionnaire_research_version=CONSENT)]}


def adult_filter(eligible):
    # Birthdays are optional. New enrollments and legacy submissions explicitly attest to adulthood.
    age_text = {"$convert": {"input": "$about.demographics.age", "to": "string", "onError": "invalid", "onNull": ""}}
    age = {"$convert": {"input": age_text, "to": "int", "onError": -1, "onNull": -1}}
    field = "$about.demographics.age"
    unspecified = {"$or": [{"$in": [{"$type": field}, ["missing", "null"]]}, {"$eq": [field, ""]}]}
    valid = {"$and": [{"$in": [{"$type": field}, ["int", "long", "string"]]},
                      {"$regexMatch": {"input": age_text, "regex": "^[0-9]{1,3}$"}},
                      {"$gte": [age, 18]}, {"$lte": [age, 120]}]}
    return {"$and": [eligible, {"role": "member", "closing": {"$ne": True}},
                     {"$expr": {"$and": [{"$eq": ["$role", "member"]}, {"$or": [unspecified, valid]}]}}]}


def band(value, minimum, maximum, cuts):
    # Validate integer-valued numbers or digit strings before converting historical values.
    text = {"$convert": {"input": value, "to": "string", "onError": "", "onNull": ""}}
    number = {"$convert": {"input": text, "to": "int", "onError": -1, "onNull": -1}}
    return {"$let": {"vars": {"v": number}, "in": {"$cond": [
        {"$and": [{"$regexMatch": {"input": text, "regex": "^[0-9]+$"}},
                  {"$gte": ["$$v", minimum]}, {"$lte": ["$$v", maximum]}]},
        {"$size": {"$filter": {"input": cuts, "as": "cut", "cond": {"$gte": ["$$v", "$$cut"]}}}}, None]}}}


def everyday(quiz, key, question, member):
    qid = key[2:]
    stored = field(qid, member + ".about.answers")
    original = (quiz.get("legacy_wording") or {}).get(qid, {"q": question["label"], "labels": question["choices"]})
    legacy = {"$cond": [
        {"$and": [literal(original["q"] == question["label"]), bounded("$$a.choice", len(original["labels"]))]},
        {"$arrayElemAt": [literal(original["labels"]), "$$a.choice"]}, None]}
    meaning = {"$cond": [
        {"$or": [{"$ne": [{"$type": "$$a.question"}, "missing"]}, {"$ne": [{"$type": "$$a.label"}, "missing"]}]},
        {"$cond": [{"$eq": ["$$a.question", literal(question["label"])]}, "$$a.label", None]}, legacy]}
    return {"$let": {"vars": {"a": stored}, "in": {"$indexOfArray": [literal(question["choices"]), meaning]}}}


def intimacy_index(key, condition, signature, doc):
    choice = field(key, doc + ".values")
    if condition:
        tags = {"$ifNull": [field(key, doc + ".conditions"), []]}
        choice = {"$cond": [{"$and": [{"$eq": [choice, research.CHOICES.index("It depends")]}, {"$isArray": tags}]},
                            {"$cond": [{"$in": [literal(condition), tags]}, 1, 0]}, None]}
    return {"$cond": [{"$eq": [field(key, doc + ".signatures"), literal(signature)]}, choice, None]}


def value_for(key, condition, variable, quiz, member, intimacy, couples):
    if key == AGE:
        return band(member + ".about.demographics.age", 18, 120, [25, 35, 45, 55, 65])
    if key == HEIGHT:
        return band(member + ".about.demographics.height_cm", quiz_answers.HEIGHT_MIN_CM, quiz_answers.HEIGHT_MAX_CM,
                    [150, 160, 170, 180, 190, 200])
    if variable["domain"] == "couples":
        return field(key, couples)
    if variable["domain"] == "intimacy":
        return intimacy_index(key, condition, variable["signature"], intimacy)
    if variable["domain"] == "everyday":
        return everyday(quiz, key, variable, member)
    source = member + (".about.mbti" if key == "p:mbti" else ".about.demographics." + key[2:])
    return {"$indexOfArray": [literal(variable["choices"]), source]}


def seat_values(axes, variables, quiz, member, pair, side, name, measurements=None):
    keys = [key for key, _ in axes.values()]
    uid = member + ".user_id"
    stages = []
    intimate = [key for key in keys if variables[key]["domain"] == "intimacy"]
    if intimate:
        # Only load the selected question codes. Personal free text is never projected into the report.
        selected = {f"{part}.{key}": 1 for part in ("values", "conditions", "signatures") for key in intimate}
        stages += [{"$lookup": {"from": "intimacy_answers", "localField": uid[1:], "foreignField": "_id",
                                 "pipeline": [{"$project": {"_id": 0, **selected}}], "as": "intimacy"}},
                   {"$set": {"intimacy": {"$first": "$intimacy"}}}]
    couple_keys = {key: variables[key] for key in keys if variables[key]["domain"] == "couples"}
    if couple_keys:
        stages.extend(couple_research.seat_stages(uid, pair, side, couple_keys, "couple_values"))
    values = {axis: value_for(key, condition, variables[key], quiz, member, "$intimacy", "$couple_values")
              for axis, (key, condition) in axes.items()}
    if measurements:
        values.update(measurements(member, axes))
    return [*stages, {"$set": {name: values}}]


def member_projection(axes, variables):
    projection = {"_id": 0, "user_id": 1}
    for key, _ in axes.values():
        if key in (AGE, HEIGHT):
            projection["about.demographics." + ("age" if key == AGE else "height_cm")] = 1
        elif key == "p:mbti":
            projection["about.mbti"] = 1
        elif variables[key]["domain"] == "demographics":
            projection["about.demographics." + key[2:]] = 1
        elif variables[key]["domain"] == "everyday":
            # Exclude answer notes and the account's other answers.
            for part in ("choice", "question", "label"):
                projection["about.answers." + key[2:] + "." + part] = 1
    return projection


def member_seat(eligible, quiz, variables, axes, measurements=None):
    stages = [{"$match": ongoing_members(eligible)},
              {"$project": member_projection(axes, variables)}, {"$set": {"person": "$$ROOT"}}]
    if any(key.startswith(("c:checkin:", "c:prompt:")) for key, _ in axes.values()):
        stages.extend([
            {"$lookup": {"from": "couples_pairs", "localField": "person.user_id", "foreignField": "members",
                         "pipeline": [{"$match": {"state": "active", "env": eligible["env"], "$expr": {"$and": [
                             {"$ne": ["$inviter", "$invitee"]},
                             {"$setEquals": [["$inviter", "$invitee"], {"$cond": [{"$isArray": "$members"}, "$members", []]}]}]}}},
                                      {"$project": {"_id": 1, "inviter": 1, "invitee": 1, "journey.threads": 1}}],
                         "as": "pair"}},
            {"$set": {"pair": {"$first": "$pair"}}},
        ])
    side = {"$switch": {"branches": [
        {"case": {"$eq": ["$person.user_id", "$pair.inviter"]}, "then": "a"},
        {"case": {"$eq": ["$person.user_id", "$pair.invitee"]}, "then": "b"}], "default": None}}
    return [*stages, *seat_values(axes, variables, quiz, "$person", "$pair", side, "seat", measurements)]


def couple_seats(eligible, quiz, variables, axes, measurements=None):
    # An active pair owns exactly one inviter and one invitee. Re-evaluate both accounts' eligibility on every load.
    pair_projection = {"inviter": 1, "invitee": 1}
    if any(key.startswith(("c:checkin:", "c:prompt:")) for key, _ in axes.values()):
        pair_projection["journey.threads"] = 1
    pipeline = [{"$match": {"state": "active", "env": eligible["env"], "$expr": {"$and": [
        {"$ne": ["$inviter", "$invitee"]}, {"$setEquals": [["$inviter", "$invitee"], {"$cond": [{"$isArray": "$members"}, "$members", []]}]}]}}},
        {"$project": pair_projection}, {"$set": {"pair": "$$ROOT"}}]
    valid = {"$and": [ongoing_members(eligible), couples_adult_attestation()]}
    for side, seat in (("a", "inviter"), ("b", "invitee")):
        pipeline.extend([
            {"$lookup": {"from": "users", "localField": seat, "foreignField": "user_id",
                         "pipeline": [{"$match": valid}, {"$project": member_projection(axes, variables)}], "as": "person"}},
            {"$unwind": "$person"},
            *seat_values(axes, variables, quiz, "$person", "$pair", literal(side), side, measurements)])
    return pipeline


def partner_observations(unit, y_slot, x_size, y_size):
    orientations = []
    if unit != "couple_invitee":
        orientations.append({"x": "$a.x", "y": "$b." + y_slot})
    if unit != "couple_inviter":
        orientations.append({"x": "$b.x", "y": "$a." + y_slot})
    # Distribute one vote per couple equally among orientations with both answers present.
    valid = {"$filter": {"input": orientations, "as": "o", "cond": {"$and": [
        bounded("$$o.x", x_size), bounded("$$o.y", y_size)]}}}
    return {"$let": {"vars": {"valid": valid}, "in": {"$map": {"input": "$$valid", "as": "o", "in": {
        "x": "$$o.x", "y": "$$o.y", "w": {"$divide": [1, {"$size": "$$valid"}]}}}}}}


def observations(axes, sizes, unit):
    # Each candidate table sees only valid observations. Internal slot names are independent of user-edited IDs.
    tables = []
    for slot in axes:
        if slot == "x":
            continue
        if unit == "member":
            valid = {"$and": [bounded("$seat.x", sizes["x"]), bounded("$seat." + slot, sizes[slot])]}
            rows = {"$cond": [valid, [{"x": "$seat.x", "y": "$seat." + slot, "w": 1}], []]}
        else:
            rows = partner_observations(unit, slot, sizes["x"], sizes[slot])
        tables.append({"slot": slot, "obs": rows})
    return [{"$project": {"_id": 0, "tables": tables}}, {"$unwind": "$tables"}, {"$unwind": "$tables.obs"},
            {"$group": {"_id": {"slot": "$tables.slot", "x": "$tables.obs.x", "y": "$tables.obs.y"},
                        "n": {"$sum": "$tables.obs.w"}}}]
