import copy

from backend import content, figure_career_review as career, figure_evidence
from backend.tests.test_free_plan_quota import run_case


def test_entire_directory_has_current_review_and_concordant_citations(monkeypatch):
    async def scenario(db):
        # Seed the original six, then run the normal startup path for all later editions.
        originals = content.seed_json("figures.json")
        names = {slug: name for slug, name, _, _ in content.FIGURES}
        await db.figures.insert_many([{**content.attach_example_sources(row), "slug": slug, "name": names[slug], "published": True}
                                      for slug, row in originals.items()])
        # Upgrade an installation that has all 500 articles and the last ten-profile review.
        history = career.editions()
        with monkeypatch.context() as previous_release:
            previous_release.setattr(career, "editions", lambda: history[:-1])
            await content.seed_public_profiles(db, "test")
        others = {"slug": {"$ne": "berthe-morisot"}}
        before = await db.figures.find(others).sort("slug", 1).to_list(None)
        assert len(before) == 499
        await content.seed_public_profiles(db, "test")
        assert before == await db.figures.find(others).sort("slug", 1).to_list(None)
        expected = career.batch()["profiles"]
        assert len(expected) == 406
        queue = {row["slug"]: row for row in await figure_evidence.staff_queue(db)}
        assert len(queue) == 500
        assert all(item["status"] != "career_limited" for item in queue.values())
        directory = {row["slug"]: figure_evidence.assessment(row) async for row in
                     db.figures.find({"published": True}, figure_evidence.directory_projection())}
        async for doc in db.figures.find({"slug": {"$in": list(expected)}}):
            slug = doc["slug"]
            row = expected[slug]
            assert career.version(doc) == row["version"], slug
            rec = queue[slug]
            assert rec["status"] == directory[slug]["status"] == row["result"], slug
            assert rec["career"]["reviewer"] == "AI editorial review" and rec["decision"] is None
            assert doc["matches"] == doc["sections"]["matches"]
            mapping = doc["sections"]["map"]
            assert doc["matches"][0]["number"] == mapping["closest"]["number"] == row["lead_number"]
            assert mapping["cousin"]["number"] == row["alternative_number"] != row["lead_number"]
            comm = doc["sections"]["communicates"]
            assert [ref["example"] for ref in comm["example_sources"]] == comm["examples"]
            urls = {ref["url"] for ref in comm["example_sources"]}
            assert {ref["url"] for ref in mapping["basis"]["sources"]} <= urls
            assert urls <= {ref["url"] for ref in doc["sources"]}
            assert rec["career"].get("scope") == row.get("scope")
            assert len(rec["career"]["sources"]) > 0
        assert all(queue[s]["status"] == "both" and not queue[s]["career"] for s in queue.keys() - expected.keys())
    run_case(scenario)


def test_new_career_edition_protects_staff_decisions_and_source_edits(monkeypatch):
    async def scenario(db):
        doc = {"slug": "sample", "name": "Sample", "matches": [{"number": 1}], "sections": {
            "matches": [{"number": 1}], "map": {"closest": {"number": 1}}, "communicates": {
                "examples": ["A documented action."], "example_sources": []}}}
        expected = copy.deepcopy(doc)
        expected["sections"]["map"]["closest"]["number"] = 2
        monkeypatch.setattr(career, "editions", lambda: ({"edition": "sample", "profiles": {"sample": {
            "before": career.version(doc), "version": career.version(expected),
            "patch": {"sections.map.closest.number": 2}}}},))
        await db.figures.insert_one(copy.deepcopy(doc))
        await db.figures.update_one({}, {"$set": {"lead_review_decision": {"result": "retain"}}})
        await career.apply(db)
        assert (await db.figures.find_one({}))["sections"]["map"]["closest"]["number"] == 1
        await db.figures.update_one({}, {"$unset": {"lead_review_decision": ""}, "$set": {"sections.communicates.examples": []}})
        await career.apply(db)
        assert "career_review_applied" not in await db.figures.find_one({})
        await db.figures.update_one({}, {"$set": {"sections.communicates.examples": ["A documented action."]}})
        await career.apply(db)
        after = await db.figures.find_one({})
        assert career.version(after) == career.version(expected)
        await career.apply(db)
        assert await db.figures.find_one({}) == after
    run_case(scenario)


def test_followup_editions_only_revisit_tentative_profiles_with_new_evidence():
    history = career.editions()
    assert len(history[-2]["profiles"]) == 10
    assert set(history[-1]["profiles"]) == {"berthe-morisot"}
    assert all(row["scope"].strip() for edition in history[-2:] for row in edition["profiles"].values())
    for first, second in zip(history, history[1:]):
        cases = second["profiles"]
        assert set(cases) == {s for s, e in first["profiles"].items() if e["result"] == "career_limited"}
        for slug, new in cases.items():
            old = first["profiles"][slug]
            assert new["before"] == old["version"] and new["before"] != new["version"], slug
            assert bool(new.get("human_reason")) == (new["result"] == "career_limited"), slug
            assert new["lead_number"] != new["alternative_number"]
            assert all(citation["evidence"] and citation["label"] for citation in new["sources"])
            assert {r["url"] for r in new["sources"]} - {r["url"] for r in old["sources"]}, slug


def test_source_edits_survive_later_editions_and_clean_records_upgrade():
    history = career.editions()
    latest = history[-1]

    async def scenario(db):
        slug = "berthe-morisot"
        row = next(prof for prof in content.seed_json("figure_profiles/arts_expansion.json") if prof["slug"] == slug)
        doc = content.attach_example_sources(copy.deepcopy(row))
        doc.update(identity=doc["sections"]["identity"], matches=doc["sections"]["matches"], published=True)
        await db.figures.insert_one(doc)
        for edition in history[:-1]:
            await career.apply_edition(db, edition, [edition["edition"]])
        installed = await db.figures.find_one({"slug": slug})
        assert career.version(installed) == latest["profiles"][slug]["before"]
        for changes in ({"lead_review_decision": {"version": career.version(installed), "result": "retain"}},
                        {"sections.map.closest.rationale": "A revised staff reading"}):
            await db.figures.replace_one({"slug": slug}, installed)
            await db.figures.update_one({"slug": slug}, {"$set": changes})
            edited = await db.figures.find_one({"slug": slug})
            await career.apply(db)
            assert await db.figures.find_one({"slug": slug}) == edited
        await db.figures.replace_one({"slug": slug}, installed)
        await career.apply(db)
        assert (await db.figures.find_one({"slug": slug}))["career_review_applied"] == latest["edition"]
        saved = await db.figures.find_one({"slug": slug})
        assert career.version(saved) == latest["profiles"][slug]["version"]
        await career.apply(db)
        assert saved == await db.figures.find_one({"slug": slug})
    run_case(scenario)
