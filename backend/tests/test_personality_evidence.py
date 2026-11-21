"""Contracts for source labels and complete, displayable archetype rankings."""
import pytest
from fastapi import HTTPException


@pytest.fixture
def api(monkeypatch):
    from backend import database
    monkeypatch.setattr(database, "open_database", lambda: (None, None))
    from backend import server
    return server


def test_pattern_sources_stay_distinguishable(api):
    classify = api.pattern_evidence
    data = {"text": "I ask for a pause.", "evidence_status": "tentative", "example_count": 0, "practice_count": 1}
    assert "Tentative observation (1 practice rehearsal)" in classify.description(data)
    data.update(evidence_status="confirmed", edited=True, example_count=1)
    result = classify.description(data)
    assert all(label in result for label in ("Confirmed observation", "member confirmed", "1 distinct saved conversation", "1 practice rehearsal", "wording edited"))


ROSTER = [{"number": n, "name": f"Name {n}", "slug": f"name-{n}"} for n in (1, 2)]
FIRST = {"number": 1, "score": 65, "line": " An example. "}
SECOND = {"number": 2, "score": 80, "line": "Another example."}


@pytest.mark.parametrize("items", [
    [], [FIRST], [FIRST, FIRST], [FIRST, {**SECOND, "number": 3}],
    [FIRST, {**SECOND, "score": 200}], [FIRST, {**SECOND, "score": True}],
    [FIRST, {**SECOND, "line": " "}], [FIRST, {**SECOND, "number": False}],
])
def test_incomplete_or_malformed_rankings_are_never_returned_or_reused(api, items):
    assert not api.ranking_complete(items, ROSTER)
    with pytest.raises(HTTPException) as error:
        api.normalize_ranking({"items": items}, ROSTER)
    assert error.value.status_code == 502


def test_rankings_use_the_current_catalog_and_sort_by_score(api):
    ranked = api.normalize_ranking({"items": [{**FIRST, "slug": "invented"}, SECOND]}, ROSTER)
    assert [item["number"] for item in ranked] == [2, 1]
    assert ranked[1] == {**ROSTER[0], "score": 65, **api.archetype_copy.new_caption(ROSTER[0], raw=FIRST["line"])}
    assert api.ranking_complete(ranked, ROSTER)
