"""Keep a member's selected wording independent of the order of quiz options."""

SETS = ("short", "extended", "further")
GENDER_OPTIONS = ["Woman", "Man", "Non-binary", "Another gender", "Prefer not to say"]
HEIGHT_MIN_CM = 50
HEIGHT_MAX_CM = 300
DEMOGRAPHIC_OPTIONS = {
    "sex": ["Female", "Male", "Non-binary", "Prefer not to say"],
    "ethnicity": ["Asian", "Black", "Hispanic / Latino", "Middle Eastern / North African", "White", "Mixed", "Other", "Prefer not to say"],
    "education": ["High school", "Some college", "Bachelor's", "Master's", "Doctorate", "Trade / vocational", "Prefer not to say"],
}


def questions_by_id(quiz):
    return {q["id"]: q for section in SETS for q in quiz.get(section, [])}


def wording(question):
    return {"q": question["q"], "labels": [option["label"] for option in question["options"]]}


def remember_published_wording(previous, replacement):
    # An old member may still have only an integer. Keep its original meaning
    # once, before the first edit to that question, including if it is deleted.
    history = dict(previous.get("legacy_wording") or {})
    current = questions_by_id(replacement)
    for qid, old in questions_by_id(previous).items():
        if qid not in history and (qid not in current or wording(old) != wording(current[qid])):
            history[qid] = wording(old)
    return history


def _meaning(answer, quiz, qid, current):
    if not isinstance(answer, dict):
        return None
    if "question" in answer or "label" in answer:
        return (answer.get("question"), answer.get("label"))
    original = (quiz.get("legacy_wording") or {}).get(qid)
    if original is None and current:
        original = wording(current)
    index = answer.get("choice")
    if original and type(index) is int and 0 <= index < len(original["labels"]):
        return (original["q"], original["labels"][index])
    return None


def _current_index(meaning, question):
    if not meaning or question is None or meaning[0] != question["q"]:
        return None
    return next((i for i, option in enumerate(question["options"]) if option["label"] == meaning[1]), None)


def resolve_answer(about, quiz, question):
    answer = ((about or {}).get("answers") or {}).get(question["id"])
    return _current_index(_meaning(answer, quiz, question["id"], question), question)


def display_about(about, quiz):
    about = about or {}
    current = questions_by_id(quiz)
    active, earlier = {}, {}
    for qid, raw in (about.get("answers") or {}).items():
        question = current.get(qid)
        meaning = _meaning(raw, quiz, qid, question)
        if not meaning:
            # Preserve even an unresolvable record in the account export.
            earlier[qid] = raw
            continue
        snapshot = {**raw, "question": meaning[0], "label": meaning[1]}
        index = _current_index(meaning, question)
        if index is None:
            earlier[qid] = snapshot
        else:
            active[qid] = {**snapshot, "choice": index}
    return {**about, "answers": active, "previous_answers": earlier}


def selected_answer(quiz, question, index, prompt, label, note=""):
    if prompt is not None or label is not None:
        index = _current_index((prompt, label), question)
        if index is None:
            raise ValueError("This question changed. Refresh Personal to review its current answers.")
    elif question["id"] in (quiz.get("legacy_wording") or {}):
        # Old browser bundles submit only a position. After an edit we cannot
        # tell which published ordering that browser saw, even after a rollback.
        raise ValueError("This question changed. Refresh Personal before saving it.")
    if index is None or not 0 <= index < len(question["options"]):
        return None
    return {"choice": index, "question": question["q"], "label": question["options"][index]["label"],
            "note": note.strip()[:500]}
