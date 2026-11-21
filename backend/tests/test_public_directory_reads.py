"""Directory transfer reductions retain current publication and editorial state."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend import document_queries, figure_evidence
from backend.public_directory_reads import archetype_directory_projection, figure_directory_inputs
from backend.tests.test_admin_answer_history import run_case


@pytest.mark.parametrize("sections", [None, {}, [], "", False, 0, 0.0, {"identity": "Reviewed"}, ["legacy"], "legacy"])
def test_card_ready_matches_previous_python_semantics_without_sending_article(sections):
    original = {"number": 1, "slug": "example", "wave": 1, "unlocked": True,
                "name": "Example", "one_liner": "Example", "texting_tags": [], "red_flag_tags": [],
                "sections": sections, "editorial": {"private_draft": "Never sent"}}
    projected = document_queries.project(original, archetype_directory_projection())
    assert projected["ready"] is bool(sections)
    assert "sections" not in projected and "editorial" not in projected
    assert set(projected) == set(original) - {"sections", "editorial"} | {"ready"}


def test_missing_sections_are_not_ready():
    assert document_queries.project({"slug": "unfinished"}, archetype_directory_projection()) == {
        "slug": "unfinished", "ready": False}


def test_figure_inputs_start_both_reads_before_waiting_and_never_cache():
    async def scenario():
        arrived, generation, calls = set(), [1], []
        both = asyncio.Event()
        class Cursor:
            def __init__(self, collection):
                self.collection = collection
            async def to_list(self, limit):
                assert limit is None
                arrived.add(self.collection)
                if len(arrived) == 2:
                    both.set()
                await asyncio.wait_for(both.wait(), .5)
                return ([{"slug": "figure", "revision": generation[0]}] if self.collection == "figures"
                        else [{"number": 4, "name": "Fresh " + str(generation[0]), "gender": "female"}])
        class Collection:
            def __init__(self, name):
                self.name = name
            def find(self, query, projection):
                calls.append((self.name, query, projection))
                return Cursor(self.name)
        db = SimpleNamespace(figures=Collection("figures"), archetypes=Collection("archetypes"))
        first = await figure_directory_inputs(db)
        generation[0] = 2
        second = await figure_directory_inputs(db)
        assert first == ([{"slug": "figure", "revision": 1}], {4: {"number": 4, "name": "Fresh 1", "gender": "female"}})
        assert second[0][0]["revision"] == 2 and second[1][4]["name"] == "Fresh 2"
        assert len(calls) == 4
        for name, query, projection in calls:
            assert query == ({"published": True} if name == "figures" else {"unlocked": True})
            assert projection == (figure_evidence.directory_projection() if name == "figures"
                                  else {"_id": 0, "number": 1, "name": 1, "gender": 1})
    asyncio.run(scenario())


@pytest.mark.parametrize("failed", ["figures", "archetypes"])
def test_figure_lookup_failure_never_returns_a_partial_directory(failed):
    class Collection:
        def __init__(self, name): self.name = name
        def find(self, *_): return self
        async def to_list(self, _):
            if self.name == failed: raise RuntimeError("Read unavailable")
            return []
    async def scenario():
        with pytest.raises(RuntimeError, match="Read unavailable"):
            await figure_directory_inputs(SimpleNamespace(figures=Collection("figures"), archetypes=Collection("archetypes")))
    asyncio.run(scenario())


def test_current_directory_routes_preserve_hidden_cards_and_follow_publication_edits(monkeypatch):
    from backend import server
    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        records = [
            {"number": 1, "slug": "visible", "wave": 1, "unlocked": True, "name": "Visible", "one_liner": "One",
             "texting_tags": ["clear"], "red_flag_tags": [], "sections": {"identity": "long article"}},
            {"number": 2, "slug": "locked", "wave": 2, "unlocked": False, "name": "Unpublished", "gender": "female",
             "one_liner": "Hidden copy", "texting_tags": [], "red_flag_tags": [], "sections": {"identity": "private"}},
            {"number": 3, "slug": "empty", "wave": 1, "unlocked": True, "name": "Empty", "one_liner": "Empty",
             "gender": "female", "texting_tags": [], "red_flag_tags": [], "sections": {}},
        ]
        await db.archetypes.insert_many(deepcopy(records))
        def previous(rows):
            out = []
            for row in rows:
                card = {key: row[key] for key in ("number", "slug", "wave", "unlocked")}
                card["gender"] = row.get("gender", "male")
                if row["unlocked"]:
                    card.update({key: row[key] for key in ("name", "one_liner", "texting_tags", "red_flag_tags")})
                    card["ready"] = bool(row.get("sections"))
                out.append(card)
            return out
        assert await server.list_archetypes() == previous(records)
        figures = [
            {"slug": "fixture-published", "name": "Published", "published": True, "identity": "Career",
             "matches": [{"number": 1, "why": "Example"}, {"number": 2, "why": "Hidden type"}]},
            {"slug": "fixture-hidden", "name": "Hidden", "published": False, "matches": [{"number": 1}]},
        ]
        await db.figures.insert_many(figures)
        first = await server.list_figures()
        assert [row["slug"] for row in first] == ["fixture-published"]
        assert first[0]["matches"][0]["name"] == "Visible"
        assert first[0]["matches"][1]["name"] is None
        await db.archetypes.update_one({"number": 1}, {"$set": {"name": "Renamed", "sections": {}}})
        assert (await server.list_figures())[0]["matches"][0]["name"] == "Renamed"
        assert (await server.list_archetypes())[0]["ready"] is False
        await db.archetypes.update_one({"number": 1}, {"$set": {"unlocked": False}})
        assert (await server.list_figures())[0]["matches"][0]["name"] is None
        await db.figures.update_one({"slug": "fixture-published"}, {"$set": {"published": False}})
        assert await server.list_figures() == []
        await db.archetypes.delete_one({"number": 1})
        assert [row["number"] for row in await server.list_archetypes()] == [2, 3]
    run_case(scenario)


def test_figure_input_projection_keeps_exact_evidence_for_fresh_review(monkeypatch):
    from backend import content
    async def scenario(db):
        row = content.seed_json("figures.json")["elon-musk"]
        row = content.attach_example_sources(row)
        row.update(slug="elon-musk", published=True)
        await db.figures.insert_one(deepcopy(row))
        baseline = await db.figures.find({"published": True}, figure_evidence.directory_projection()).to_list(None)
        current, _ = await figure_directory_inputs(db)
        assert current == baseline
        assert figure_evidence.assessment(current[0]) == figure_evidence.assessment(row)
        await db.figures.update_one({"slug": "elon-musk"}, {"$set": {"sections.communicates.examples": ["Changed source text"]}})
        changed, _ = await figure_directory_inputs(db)
        assert changed[0]["sections"]["communicates"]["examples"] == ["Changed source text"]
        assert figure_evidence.assessment(changed[0])["status"] == "changed"
    run_case(scenario)
