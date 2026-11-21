"""Own deterministic, regularized visual-preference learner; no provider calls.

Scores describe choices inside a synthetic asset edition, never personality,
compatibility, ethnicity or population popularity. Evaluation uses predictions
saved before feedback, not predictions fitted to the same observations.
"""
from copy import deepcopy
import hashlib
import math
import random

EDITION = "your-type-online-logistic-v1"
RIDGE = 4.0
WEAK_PASS_WEIGHT = 0.35
MIN_LIKES = 30


def initial(dimensions):
    return {"weights": [0.0] * (dimensions + 1), "precision": [RIDGE] * (dimensions + 1),
            "samples": 0, "likes": 0, "explicit_passes": 0, "viewed_passes": 0,
            "weighted_samples": 0.0, "feature_observations": [0] * dimensions,
            "feature_option_counts": [0] * dimensions}


def vector(asset, groups=None):
    values = asset["features"]
    masks = asset.get("feature_masks", [True] * len(values))
    result = [float(v) if m else 0.0 for v, m in zip(values, masks)]
    # Center only fully known categorical groups; unknowns carry no direction.
    for group in groups or []:
        indices = group.get("indices", [])
        if indices and all(masks[i] for i in indices):
            mean = sum(result[i] for i in indices) / len(indices)
            for i in indices:
                result[i] -= mean
    return [1.0, *result]


def sigmoid(value):
    value = max(-35.0, min(35.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def predict(model, asset, groups=None):
    x = vector(asset, groups)
    score = sigmoid(sum(a * b for a, b in zip(model["weights"], x)))
    uncertainty = math.sqrt(sum(a * a / p for a, p in zip(x, model["precision"])))
    return {"score": round(score, 8), "uncertainty": round(uncertainty, 8)}


def train(model, choices, assets, groups=None):
    updated = deepcopy(model)
    for choice in choices:
        x = vector(assets[choice["face_id"]], groups)
        positive = choice["choice"] == "like"
        weight = WEAK_PASS_WEIGHT if choice["choice"] == "viewed_pass" else 1.0
        prediction = sigmoid(sum(a * b for a, b in zip(updated["weights"], x)))
        step = 0.25 / math.sqrt(1 + updated["weighted_samples"] / 30)
        for i, value in enumerate(x):
            gradient = weight * ((1.0 if positive else 0.0) - prediction) * value
            # Shrink towards the independent zero-centered prior on each update.
            updated["weights"][i] = max(-8.0, min(8.0,
                updated["weights"][i] * (1 - step * 0.025) + step * gradient))
            updated["precision"][i] += weight * prediction * (1 - prediction) * value * value
        updated["samples"] += 1
        updated["likes" if positive else "viewed_passes" if choice["choice"] == "viewed_pass" else "explicit_passes"] += 1
        updated["weighted_samples"] += weight
        asset = assets[choice["face_id"]]
        for i, known in enumerate(asset.get("feature_masks", [True] * len(asset["features"]))):
            if known:
                updated["feature_observations"][i] += 1
                updated["feature_option_counts"][i] += int(asset["features"][i] > 0)
    return updated


def prospective(choices):
    positive = [c for c in choices if c["choice"] == "like"]
    negative = [c for c in choices if c["choice"] in ("pass", "viewed_pass")]
    result = {"correct": 0, "ties": 0, "pairs": 0, "explicit_pairs": 0, "viewed_pairs": 0}
    for a in positive:
        for b in negative:
            if a["face_id"] == b["face_id"]:
                continue
            result["pairs"] += 1
            result["explicit_pairs" if b["choice"] == "pass" else "viewed_pairs"] += 1
            difference = a["prediction"] - b["prediction"]
            result["ties" if abs(difference) < 1e-8 else "correct"] += int(abs(difference) < 1e-8 or difference > 0)
    return result


def group_evidence(model, group):
    """A constant/unknown cue cannot support a learned preference comparison."""
    size = len(model["weights"]) - 1
    observations = model.get("feature_observations", [0] * size)
    counts = model.get("feature_option_counts", [0] * size)
    indices = group.get("indices", [])
    supported = [i for i in indices if i < len(counts) and i < len(observations)
                 and counts[i] >= 3 and observations[i] - counts[i] >= 3]
    variation = len(supported) >= 2
    return {"variation_available": variation, "observed_options": sum(i < len(counts) and counts[i] > 0 for i in indices),
            "qualified_options": len(supported), "minimum_examples_per_option": 3,
            "minimum_known_comparisons_per_option": 3, "eligible_indices": supported if variation else []}


def sample(model, candidates, count, seed, groups=None, *, start_index=0, existing=None):
    """A bounded deterministic mix of exploitation, uncertainty and exploration."""
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest(), 16))
    remaining = list(candidates)
    rng.shuffle(remaining)
    chosen = []
    reasons = []
    for index in range(min(count, len(remaining))):
        reason = ("exploration", "uncertainty", "preference", "diversity")[(index + start_index) % 4]
        if reason == "diversity" and not (chosen or existing):
            reason = "exploration"
        if reason == "preference":
            asset = max(remaining, key=lambda a: predict(model, a, groups)["score"])
        elif reason == "uncertainty":
            asset = max(remaining, key=lambda a: predict(model, a, groups)["uncertainty"])
        elif reason == "diversity" and (chosen or existing):
            previous = [vector(a, groups) for a in [*(existing or []), *chosen]]
            asset = max(remaining, key=lambda a: min(sum((x-y)**2 for x, y in zip(vector(a, groups), p)) for p in previous))
        else:
            asset = remaining[0]
        remaining.remove(asset)
        chosen.append(asset)
        reasons.append(reason)
    return [{"face_id": a["id"], **predict(model, a, groups),
             "reason": reasons[i]}
            for i, a in enumerate(chosen)]


def results(model, assets, groups, evaluation, completed_crowds):
    ranked = sorted(({"face_id": a["id"], **predict(model, a, groups)} for a in assets),
                    key=lambda a: (-a["score"], a["face_id"]))
    pairs = evaluation["pairs"]
    signal = model["likes"] >= MIN_LIKES and model["explicit_passes"] + model["viewed_passes"] >= 3
    accuracy = ((evaluation["correct"] + 0.5 * evaluation["ties"]) / pairs) if pairs else None
    traits = []
    total_importance = sum(abs(model["weights"][i + 1]) for group in groups
                           for i in group_evidence(model, group)["eligible_indices"]) or 1.0
    for group in groups:
        evidence = group_evidence(model, group)
        supported = set(evidence["eligible_indices"])
        values, labels = group.get("values", []), group.get("value_labels", {})
        entries = [{"feature": index, "value": values[i] if i < len(values) else str(index),
                    "label": labels.get(values[i], str(values[i]).replace("_", " ")) if i < len(values) else str(index),
                    "direction": "undetermined" if index not in supported else "more_often_favored" if model["weights"][index + 1] > 0.03 else "less_often_favored" if model["weights"][index + 1] < -0.03 else "undetermined",
                    "association_strength": round(math.tanh(model["weights"][index + 1]), 3) if index in supported else None,
                    "observed_examples": model.get("feature_observations", [0] * (len(model["weights"]) - 1))[index],
                    "option_examples": model.get("feature_option_counts", [0] * (len(model["weights"]) - 1))[index],
                    "sufficient_option_evidence": index in supported,
                    "standard_error": round(1 / math.sqrt(model["precision"][index + 1]), 6) if index in supported else None}
                   for i, index in enumerate(group.get("indices", []))]
        entries.sort(key=lambda item: (item["association_strength"] is None, -(item["association_strength"] or 0), item["feature"]))
        traits.append({"id": group["id"], "label": group.get("label", group["id"]), "values": entries,
            "importance": round(sum(abs(model["weights"][i + 1]) for i in supported) / total_importance, 4),
            **{key: value for key, value in evidence.items() if key != "eligible_indices"},
            "meaning": "Within-model association with your choices, not a causal effect or validated attraction probability."})
    traits.sort(key=lambda item: (-item["importance"], item["id"]))
    return {"rankings": ranked if signal else [], "feature_preferences": traits if signal else [],
            "signal": "developing" if signal else "insufficient_signal", "samples": model["samples"],
            "likes": model["likes"], "explicit_passes": model["explicit_passes"],
            "viewed_passes": model["viewed_passes"], "completed_crowds": completed_crowds,
            "confidence": {"kind": "regularized_model_uncertainty_not_validated_accuracy",
                           "standard_error_kind": "diagonal_curvature_proxy_not_calibrated",
                           "calibrated": False, "population_validated": False},
            "prospective_pairwise": {**evaluation, "accuracy": accuracy,
                "independent_pairs": False, "definition": "Pre-feedback selected versus explicitly or weakly passed scores; pairs share faces."},
            "population": {"available": False, "reason": "No validated population baseline is published.",
                           "minimum_consented_adults": 50, "minimum_distinct_viewers_per_face": 10,
                           "requires_same_asset_and_model_edition": True},
            "model_edition": EDITION}
