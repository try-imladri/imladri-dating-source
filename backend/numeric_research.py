"""Aggregate exact age and height while enforcing the banded comparison's disclosure rules."""
try:
    from . import questionnaire_report as questions, quiz_answers
except ImportError:
    import questionnaire_report as questions
    import quiz_answers


MEASUREMENTS = {
    questions.AGE: ("age", 18, 120),
    questions.HEIGHT: ("height_cm", quiz_answers.HEIGHT_MIN_CM, quiz_answers.HEIGHT_MAX_CM),
}


def exact_values(member, axes):
    values = {}
    for slot, (key, _) in axes.items():
        field, minimum, maximum = MEASUREMENTS[key]
        raw = member + ".about.demographics." + field
        valid = {"$and": [{"$in": [{"$type": raw}, ["int", "long"]]},
                          {"$gte": [raw, minimum]}, {"$lte": [raw, maximum]}]}
        values["exact_" + slot] = {"$cond": [valid, raw, None]}
    return values


def paired_observations(unit):
    def observation(left, right):
        return {"x": left + ".exact_x", "y": right + ".exact_y", "band_x": left + ".x", "band_y": right + ".y"}
    if unit == "member":
        orientations = [observation("$seat", "$seat")]
    elif unit == "couple_inviter":
        orientations = [observation("$a", "$b")]
    elif unit == "couple_invitee":
        orientations = [observation("$b", "$a")]
    else:
        orientations = [observation("$a", "$b"), observation("$b", "$a")]

    return [{"$project": {"_id": 0, "obs": {"$filter": {"input": orientations, "as": "o",
            "cond": {"$and": [{"$ne": ["$$o.x", None]}, {"$ne": ["$$o.y", None]}]}}}}},
            {"$match": {"obs.0": {"$exists": True}}},
            {"$set": {"w": {"$divide": [1, {"$size": "$obs"}]}}},
            {"$unwind": "$obs"}]


async def correlation(db, eligible, quiz, variables, axes, unit, minimum, minimum_cell, round_to):
    if not all(key in MEASUREMENTS and condition is None for key, condition in axes.values()):
        return None
    if unit == "member":
        source = db.users
        pipeline = questions.member_seat(eligible, quiz, variables, axes, measurements=exact_values)
    else:
        source = db.couples_pairs
        pipeline = questions.couple_seats(eligible, quiz, variables, axes, measurements=exact_values)
    pipeline.extend(paired_observations(unit))

    def weighted(*factors):
        return {"$sum": {"$multiply": ["$w", *factors]}}

    # One member or couple has total weight one. Check the occupied band cells
    # again using only records that supplied the exact measurements.
    pipeline.extend([
        {"$group": {"_id": {"x": "$obs.band_x", "y": "$obs.band_y"}, "n": {"$sum": "$w"},
                    "sx": weighted("$obs.x"), "sy": weighted("$obs.y"),
                    "sxx": weighted("$obs.x", "$obs.x"), "syy": weighted("$obs.y", "$obs.y"),
                    "sxy": weighted("$obs.x", "$obs.y")}},
        {"$group": {"_id": None, "smallest": {"$min": "$n"}, "rows": {"$addToSet": "$_id.x"},
                    "columns": {"$addToSet": "$_id.y"},
                    **{key: {"$sum": "$" + key} for key in ("n", "sx", "sy", "sxx", "syy", "sxy")}}},
        {"$match": {"n": {"$gte": minimum}, "smallest": {"$gte": minimum_cell},
                    "rows.1": {"$exists": True}, "columns.1": {"$exists": True}}},
        {"$set": {"vx": {"$subtract": [{"$multiply": ["$n", "$sxx"]}, {"$multiply": ["$sx", "$sx"]}]},
                  "vy": {"$subtract": [{"$multiply": ["$n", "$syy"]}, {"$multiply": ["$sy", "$sy"]}]},
                  "cov": {"$subtract": [{"$multiply": ["$n", "$sxy"]}, {"$multiply": ["$sx", "$sy"]}]}}},
        {"$match": {"vx": {"$gt": 0}, "vy": {"$gt": 0}}},
        {"$set": {"r": {"$divide": ["$cov", {"$sqrt": {"$multiply": ["$vx", "$vy"]}}]}}},
        {"$project": {"_id": 0, "r": {"$round": [{"$max": [-1, {"$min": [1, "$r"]}]}, 3]},
                      "sample_size": {"$toLong": {"$multiply": [round_to,
                          {"$floor": {"$add": [{"$divide": ["$n", round_to]}, 0.5]}}]}}}},
    ])
    result = await source.aggregate(pipeline).to_list(1)
    return result[0] if result else None
