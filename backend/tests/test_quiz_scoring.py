from copy import deepcopy
from itertools import product

from backend.quiz import about_summary, default_quiz, personality, radar, valid_option_scores, with_quiz_scoring


def answers(**choices):
    return {"answers": {qid: {"choice": index} for qid, index in choices.items()}}


def test_typing_requires_direction_on_every_dimension_and_handles_all_ties():
    quiz = default_quiz()
    blank = personality({}, quiz)
    assert blank["code"] == blank["guess"] == ""
    assert all(d["letter"] == "" and d["first_pct"] is None for d in blank["dims"])
    tied = answers(s9=1, e21=2, s10=0, e22=3, s11=0, e23=3, s12=0, f4=3)
    outcome = personality(tied, quiz)
    assert outcome["code"] == outcome["guess"] == ""
    assert all((d["first_pct"], d["letter"], d["answered"]) == (50, "", 2) for d in outcome["dims"])
    assert personality(answers(s9=2), quiz)["guess"] == ""
    picked = personality({**tied, "mbti": "ISTP"}, quiz)
    assert picked["code"] == "ISTP" and picked["guess"] == "" and picked["self_set"]
    assert all(d["first_pct"] is None for d in picked["dims"])
    assert personality({"mbti": "ISTP"}, quiz)["answered"] == 0


def test_sixteen_types_remain_reachable_with_four_directional_answers():
    quiz = default_quiz()
    types = set()
    for indices in product((0, 3), repeat=4):
        p = personality(answers(**dict(zip(("s9", "s10", "s11", "s12"), indices))), quiz)
        assert p["answered"] == 4 and p["code"] == p["guess"]
        types.add(p["code"])
    assert len(types) == 16


def test_descriptive_choices_contribute_to_profile_but_not_unrelated_metrics():
    quiz = default_quiz()
    for index in range(4):
        about = answers(f49=index, f14=index, s9=index)
        assert all(axis["answered"] == 0 and axis["score"] is None for axis in radar(about, quiz))
        summary = about_summary(about, quiz)
        assert summary["further_done"] == 2 and summary["short_done"] == 1
        assert summary["personality"]["answered"] == 1
    for index in range(4):
        expect = round(index * 100 / 3)
        for qid in ("s2", "e10"):
            axes = {ax["axis"]: ax for ax in radar(answers(**{qid: index}), quiz)}
            assert axes["Reassurance"] == {"axis": "Reassurance", "score": expect, "answered": 1}


def test_quiz_upgrade_follows_meaning_and_keeps_custom_content_and_explicit_weights():
    original = default_quiz()
    old = {k: deepcopy(original[k]) for k in ("short", "extended", "further", "axes", "version")}
    for section in ("short", "extended", "further"):
        for row in old[section]:
            for i, option in enumerate(row["options"]):
                option.pop("axis_score", None)
                option.pop("type_score", None)
                option["score"] = 4 - i
    e10 = next(q for q in old["extended"] if q["id"] == "e10")
    e10["options"].reverse()
    s11 = next(q for q in old["short"] if q["id"] == "s11")
    s11["options"][0]["tag"] = "leads with her heart"
    s11["options"][3]["tag"] = "administrator's wording"
    for qid, old_tag in (("s3", "holds her ground"), ("s6", "says how she feels")):
        next(q for q in old["short"] if q["id"] == qid)["options"][0]["tag"] = old_tag
    edited = next(q for q in old["extended"] if q["id"] == "e31")
    edited["q"] = "What fruit would you choose?"
    old["extended"].append({"id": "custom", "q": "How frequently?", "axis": "Directness",
                            "options": [{"label": "Usually", "axis_score": 3}, {"label": "Skip this subject"}]})
    updated = with_quiz_scoring(old)
    weights = next(q for q in updated["extended"] if q["id"] == "e10")["options"]
    assert {o["label"]: o["axis_score"] for o in weights} == {
        "Barely": 1, "A normal amount": 2, "More than I'd admit": 3, "Constantly": 4}
    assert all("score" not in o and "axis_score" not in o for o in next(q for q in updated["extended"] if q["id"] == "e31")["options"])
    tags = next(q for q in updated["short"] if q["id"] == "s11")["options"]
    assert tags[0]["tag"] == "leads with the heart" and tags[3]["tag"] == "administrator's wording"
    for qid, new_tag in (("s3", "stands firm"), ("s6", "speaks openly about feelings")):
        assert next(q for q in updated["short"] if q["id"] == qid)["options"][0]["tag"] == new_tag
    assert updated["extended"][-1] == old["extended"][-1]
    assert with_quiz_scoring(updated) == updated
    assert not valid_option_scores({"label": "New choice", "type_score": 9}, "EI")
    assert not valid_option_scores({"label": "New choice", "type_score": 2}, None)
    assert valid_option_scores({"label": "New choice"}, None)
