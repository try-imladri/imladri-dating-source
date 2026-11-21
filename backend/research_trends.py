"""Two-variable summaries of current, volunteered questionnaire contributions."""
from math import floor, sqrt

from datetime import datetime, timezone

from fastapi import HTTPException

try:
    from . import couple_research, intimacy_catalog, numeric_research, questionnaire_report, research
    from .trends import eligible_members
except ImportError:
    import couple_research, intimacy_catalog, numeric_research, questionnaire_report, research
    from trends import eligible_members


MIN_PAIRED = 50
MIN_CELL = 10
ROUND_TO = 5
AGE = "age_band"


def summarize(counts):
    # Suppress the whole comparison, including totals and association, if a small
    # cell could be recovered by subtraction. Round before deriving any statistics.
    hidden = {"available": False, "sample_size": None, "rows": [], "columns": [], "cramers_v": None}
    if sum(map(sum, counts)) < MIN_PAIRED or any(0 < n < MIN_CELL for row in counts for n in row):
        return hidden
    row_indices = [i for i, row in enumerate(counts) if any(row)]
    col_indices = [j for j in range(len(counts[0])) if any(row[j] for row in counts)]
    if min(len(row_indices), len(col_indices)) < 2:
        return hidden
    rows = []
    for i in row_indices:
        rounded = [floor(counts[i][j] / ROUND_TO + 0.5) * ROUND_TO for j in col_indices]
        rows.append({"index": i, "cells": rounded, "total": sum(rounded)})
    n = sum(row["total"] for row in rows)
    column_totals = [sum(cells) for cells in zip(*(row["cells"] for row in rows))]
    chi_square = 0.0
    for row in rows:
        for count, col_total in zip(row["cells"], column_totals):
            expected = row["total"] * col_total / n
            chi_square += (count - expected) ** 2 / expected
    v = sqrt(chi_square / (n * (min(len(row_indices), len(col_indices)) - 1)))
    return {"available": True, "sample_size": n, "rows": rows, "columns": col_indices, "cramers_v": round(v, 3)}


def category_index(key, condition):
    if key == AGE:
        return {"$indexOfArray": [list(research.AGE_BANDS), "$age_band"]}
    choice = {"$getField": {"field": {"$literal": key}, "input": "$values"}}
    if condition is None:
        return choice
    tags = {"$ifNull": [{"$getField": {"field": {"$literal": key}, "input": "$conditions"}}, []]}
    # A missing tag list on an “It depends” answer means no tags were selected.
    # Respondents choosing any other answer are outside this tag's denominator.
    return {"$cond": [
        {"$and": [{"$eq": [choice, research.CHOICES.index("It depends")]}, {"$isArray": tags}]},
        {"$cond": [{"$in": [condition, tags]}, 1, 0]}, None,
    ]}


def legacy_seat(eligible, axes, original, version):
    # An explicit ongoing-research decision supersedes the member's previous snapshot.
    allowed = {"$and": [questionnaire_report.adult_filter(eligible), {"questionnaire_research_managed": {"$ne": True},
                        "questionnaire_research_version": {"$ne": questionnaire_report.CONSENT}}]}
    values = {}
    for slot, (key, condition) in axes.items():
        if key == AGE:
            values[slot] = category_index(key, condition)
        elif key in original:
            matches = {"$or": [{"$eq": ["$catalog_version", version]},
                               {"$eq": [questionnaire_report.field(key, "$signatures"), original[key]["signature"]]}]}
            values[slot] = {"$cond": [matches, category_index(key, condition), None]}
    return [{"$match": {"env": eligible["env"], **questionnaire_report.exact_fields(submitted=True, consent_version=research.CONSENT_VERSION)}},
            *eligible_members(allowed, "user_id"), {"$project": {"_id": 0, "seat": values}}]


async def catalog(db, quiz):
    version, original = research.catalog(quiz, await intimacy_catalog.overrides(db))
    variables = {AGE: {"label": "Age range", "domain": "demographics", "choices": list(research.AGE_BANDS)},
                 **original, **questionnaire_report.additional_variables(), **await couple_research.variables(db)}
    return version, original, variables


def options(variables, key, condition):
    if key not in variables:
        raise HTTPException(422, "Choose variables from the current questionnaires.")
    if condition is None:
        return variables[key]["choices"]
    if variables[key]["domain"] != "intimacy" or condition not in research.CONDITIONS:
        raise HTTPException(422, "Choose a published condition for a Sex & Intimacy question.")
    return ["Tag not selected", "Tag selected"]


def comparison_fields(variables, axes):
    fields = {}
    for axis, (key, condition) in axes.items():
        choices = options(variables, key, condition)
        variable = variables[key]
        fields[axis] = {"key": key, "label": variable["label"], "choices": choices,
                        "signature": variable.get("signature"), "condition": condition}
    return fields


def comparable(x, y, unit):
    return unit != "member" or x[0] != y[0] or bool(x[1] and y[1] and x[1] != y[1])


async def comparisons(db, eligible, quiz, catalog_state, axes, unit):
    if unit not in questionnaire_report.UNITS:
        raise HTTPException(422, "Choose a member or partner comparison.")
    version, original, variables = catalog_state
    sizes = {slot: len(options(variables, *axis)) for slot, axis in axes.items()}
    if unit == "member":
        source = db.users
        pipeline = questionnaire_report.member_seat(eligible, quiz, variables, axes)
        legacy_keys = {AGE, *original}
        if axes["x"][0] in legacy_keys and any(key in legacy_keys for slot, (key, _) in axes.items() if slot != "x"):
            pipeline.append({"$unionWith": {"coll": "research_responses",
                                            "pipeline": legacy_seat(eligible, axes, original, version)}})
    else:
        source = db.couples_pairs
        pipeline = questionnaire_report.couple_seats(eligible, quiz, variables, axes)
    pipeline.extend(questionnaire_report.observations(axes, sizes, unit))
    counts = {slot: [[0] * size for _ in range(sizes["x"])] for slot, size in sizes.items() if slot != "x"}
    async for row in source.aggregate(pipeline):
        cell = row["_id"]
        counts[cell["slot"]][cell["x"]][cell["y"]] = row["n"]
    return {slot: summarize(table) for slot, table in counts.items()}


async def report(db, eligible, quiz, x=None, y=None, x_condition=None, y_condition=None, unit="member"):
    state = await catalog(db, quiz)
    variables = state[2]
    if x is None and y is None:
        x, y = AGE, next(key for key, v in variables.items() if v["domain"] == "intimacy")
    axes = {"x": (x, x_condition), "y": (y, y_condition)}
    if not comparable(axes["x"], axes["y"], unit):
        raise HTTPException(422, "Choose two different variables or two distinct conditions on the same question.")
    summaries = await comparisons(db, eligible, quiz, state, axes, unit)
    numeric = None
    if summaries["y"]["available"]:
        numeric = await numeric_research.correlation(db, eligible, quiz, variables, axes, unit,
                                                     MIN_PAIRED, MIN_CELL, ROUND_TO)
    return {"unit": unit, "observed_at": datetime.now(timezone.utc).isoformat(),
            "instrument": research.fingerprint(comparison_fields(variables, axes)), "pearson": numeric, "variables": [{"id": key, "label": v["label"], "domain": v["domain"]}
                                        for key, v in variables.items()],
            "x": x, "y": y, "x_condition": x_condition, "y_condition": y_condition, "conditions": list(research.CONDITIONS),
            "x_choices": options(variables, x, x_condition), "y_choices": options(variables, y, y_condition),
            "minimum": MIN_PAIRED, "minimum_cell": MIN_CELL, "round_to": ROUND_TO, **summaries["y"]}


async def distribution(db, eligible, quiz, key, condition=None):
    version, original, variables = await catalog(db, quiz)
    choices = options(variables, key, condition)
    axes = {"x": (key, condition)}
    pipeline = questionnaire_report.member_seat(eligible, quiz, variables, axes)
    if key == AGE or key in original:
        pipeline.append({"$unionWith": {"coll": "research_responses",
                                        "pipeline": legacy_seat(eligible, axes, original, version)}})
    pipeline.extend([
        {"$match": {"$expr": questionnaire_report.bounded("$seat.x", len(choices))}},
        {"$group": {"_id": "$seat.x", "n": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ])
    counts = await db.users.aggregate(pipeline).to_list(len(choices))
    available = (len(counts) >= 2 and sum(row["n"] for row in counts) >= MIN_PAIRED
                 and all(row["n"] >= MIN_CELL for row in counts))
    rows = [{"label": choices[row["_id"]], "count": floor(row["n"] / ROUND_TO + 0.5) * ROUND_TO}
            for row in counts] if available else []
    return {"key": key, "condition": condition, "available": available, "rows": rows,
            "sample_size": sum(row["count"] for row in rows) if available else None,
            "minimum": MIN_PAIRED, "minimum_cell": MIN_CELL, "round_to": ROUND_TO}


# One group query covers a bounded page, including each conditional tag on a selected question.
DISCOVERY_PAGE = 14


async def discover(db, eligible, quiz, x, unit="member", x_condition=None, cursor=0, catalog_version=None):
    state = await catalog(db, quiz)
    variables = state[2]
    keys = list(variables)
    options(variables, x, x_condition)
    if unit not in questionnaire_report.UNITS or cursor < 0 or cursor >= len(keys) or cursor % DISCOVERY_PAGE:
        raise HTTPException(422, "Choose a valid comparison and search page.")
    revision = research.fingerprint([state[0], [[k, v.get("signature") or v] for k, v in variables.items()]])
    if (cursor or catalog_version is not None) and catalog_version != revision:
        raise HTTPException(409, "The published questionnaires changed. Start a fresh search.")
    focus = (x, x_condition)
    axes = {"x": focus}
    for key in keys[cursor:cursor + DISCOVERY_PAGE]:
        conditions = (None, *research.CONDITIONS) if variables[key]["domain"] == "intimacy" else (None,)
        for condition in conditions:
            if comparable(focus, (key, condition), unit):
                axes["v" + str(len(axes))] = (key, condition)
    summaries = await comparisons(db, eligible, quiz, state, axes, unit) if len(axes) > 1 else {}
    matches = [{"y": axes[slot][0], "y_condition": axes[slot][1], "sample_size": group["sample_size"],
                "cramers_v": group["cramers_v"]} for slot, group in summaries.items() if group["available"]]
    matches.sort(key=lambda row: (-row["cramers_v"], -row["sample_size"], row["y"], row["y_condition"] or ""))
    scanned = min(cursor + DISCOVERY_PAGE, len(keys))
    return {"x": x, "x_condition": x_condition, "unit": unit, "catalog_version": revision,
            "checked": scanned, "total": len(keys), "next_cursor": scanned if scanned < len(keys) else None,
            "matches": matches}
