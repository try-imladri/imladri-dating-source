"""Result visibility follows the current questionnaire, independently of optional profile fields."""
from copy import deepcopy

import pytest

from backend.quiz import about_summary, default_quiz
from backend.quiz_answers import display_about, remember_published_wording


def completed_profile(quiz):
    return {"answers": {question["id"]: {"choice": 0, "question": question["q"],
                                      "label": question["options"][0]["label"]}
                        for section in ("short", "extended", "further") for question in quiz[section]}}


def test_all_108_current_answers_complete_without_demographics_description_or_intimacy():
    quiz = default_quiz()
    profile = completed_profile(quiz)
    assert len(profile["answers"]) == 108
    summary = about_summary(profile, quiz)
    assert summary["questionnaire_complete"] is True
    assert (summary["short_done"], summary["extended_done"], summary["further_done"]) == (12, 36, 60)
    assert summary["score"] < 100  # Optional free text is irrelevant to the result gate.


@pytest.mark.parametrize("section", ["short", "extended", "further"])
def test_one_missing_question_blocks_despite_complete_optional_fields_and_saved_type(section):
    quiz = default_quiz()
    profile = completed_profile(quiz)
    del profile["answers"][quiz[section][-1]["id"]]
    profile.update(mbti="INTJ", describe="A completed optional self description",
                   demographics={"age": 30, "gender": "Man", "sexuality": "bi"})
    assert about_summary(profile, quiz)["questionnaire_complete"] is False
    assert profile["mbti"] == "INTJ"


@pytest.mark.parametrize("change", ["new question", "new label", "invalid index", "skipped"])
def test_current_editions_and_valid_choices_are_required(change):
    quiz = default_quiz()
    original = deepcopy(quiz)
    profile = completed_profile(quiz)
    question = quiz["further"][-1]
    if change == "new question":
        question["q"] += " Revised"
    elif change == "new label":
        question["options"][0]["label"] += " Revised"
    elif change == "invalid index":
        profile["answers"][question["id"]] = {"choice": 999}
    else:
        del profile["answers"][question["id"]]
        profile["skipped"] = [question["id"]]
    quiz["legacy_wording"] = remember_published_wording(original, quiz)
    assert about_summary(profile, quiz)["questionnaire_complete"] is False
    visible = display_about(profile, quiz)
    assert about_summary(visible, quiz)["questionnaire_complete"] is False


def test_empty_quiz_cannot_unlock_saved_personality_results():
    assert about_summary({"mbti": "INTJ"}, {"short": [], "extended": [], "further": []})["questionnaire_complete"] is False
