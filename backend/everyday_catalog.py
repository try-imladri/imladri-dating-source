"""Draft validation and compatibility rules for the Everyday questionnaire."""
import hashlib
import json

from fastapi import HTTPException

try:
    from . import quiz_answers
    from .quiz import default_quiz, valid_option_scores, with_quiz_scoring
except ImportError:
    import quiz_answers
    from quiz import default_quiz, valid_option_scores, with_quiz_scoring


def meaning(row):
    # Reordering choices or allowing an optional note does not change their meaning.
    return (row["q"], row["axis"], row.get("dim"), sorted(
        (option["label"], option.get("tag"), option.get("axis_score"), option.get("type_score"))
        for option in row["options"]))


def prepare(raw, previous, safe_id, *, rollback=False):
    defaults = default_quiz()
    if not isinstance(raw, dict) or any(not isinstance(raw.get(k), list) or len(raw[k]) > 200
                                         for k in quiz_answers.SETS):
        raise HTTPException(422, "Use at most 200 questions in each Everyday section.")
    if len(json.dumps({k: raw[k] for k in quiz_answers.SETS}, ensure_ascii=False).encode()) > 500_000:
        raise HTTPException(422, "This quiz exceeds the publication size limit.")
    all_ids = set()
    for section in quiz_answers.SETS:
        for row in raw[section]:
            if (not isinstance(row, dict) or not safe_id(row.get("id")) or len(row["id"]) > 160
                    or not isinstance(row.get("q"), str) or not row["q"].strip() or len(row["q"]) > 1600
                    or row.get("axis") not in defaults["axes"] or row.get("dim", "EI") not in ("EI", "SN", "TF", "JP")
                    or not isinstance(row.get("options"), list) or not 2 <= len(row["options"]) <= 32):
                raise HTTPException(422, "Each question needs an ID, wording, a supported axis and at least two answer choices.")
            for option in row["options"]:
                if (not valid_option_scores(option, row.get("dim")) or not isinstance(option["label"], str)
                        or not option["label"].strip() or not isinstance(option.get("tag", ""), str)):
                    raise HTTPException(422, "Give each choice a label and valid optional scores from 1 to 4. A type score requires a dimension.")
            if len({option["label"] for option in row["options"]}) != len(row["options"]) or row["id"] in all_ids:
                raise HTTPException(422, "Use distinct choices within a question and distinct question IDs across the quiz.")
            all_ids.add(row["id"])
    candidate = with_quiz_scoring({"version": defaults["version"], "axes": defaults["axes"],
                                   **{k: raw[k] for k in quiz_answers.SETS}})
    old_by_id = quiz_answers.questions_by_id(previous)
    for section in quiz_answers.SETS:
        rows = []
        for row in candidate[section]:
            original = old_by_id.get(row["id"])
            if not rollback and original and meaning(original) != meaning(row):
                digest = hashlib.sha256(json.dumps(meaning(row), ensure_ascii=False).encode()).hexdigest()[:20]
                row = {**row, "id": row["id"].split("--r-", 1)[0][:100] + "--r-" + digest}
            elif not rollback and not original and row["id"] in (previous.get("legacy_wording") or {}):
                raise HTTPException(422, "This question ID belongs to a retired version. Use a fresh ID or restore the previous publication.")
            rows.append(row)
        candidate[section] = rows
    ids = [row["id"] for section in quiz_answers.SETS for row in candidate[section]]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Two questions would publish with the same version ID.")
    candidate["legacy_wording"] = quiz_answers.remember_published_wording(previous, candidate)
    return candidate


def section_data(quiz):
    return {key: quiz[key] for key in quiz_answers.SETS}
