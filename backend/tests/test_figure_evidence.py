import httpx
from backend import content, figure_evidence
from backend.tests.test_free_plan_quota import run_case


def test_updated_citation_reaches_reordered_card_without_overwriting_staff_edits(monkeypatch):
    old = "Former example"
    old_ref = {"example": old, "label": "Speech", "url": "https://example.org/transcript"}
    new = "Corrected example"
    new_ref = {**old_ref, "example": new}
    slugs = ("reordered", "edited")
    monkeypatch.setattr(content, "CAREER_CARD_REVISIONS",
                        {slug: (0, content.field_digest([old, old_ref])) for slug in slugs})
    async def scenario(db):
        for slug in slugs:
            ref = {**old_ref, "label": "Staff source"} if slug == "edited" else dict(old_ref)
            await db.figures.insert_one({"slug": slug, "sections": {"communicates": {
                "examples": ["Separate example", old], "example_sources": [{"example":"Separate example"}, ref],
            }}})
        shipped = {slug: {"sections":{"communicates":{"examples":[new],"example_sources":[new_ref]}}} for slug in slugs}
        for _ in range(2):
            await content.revise_career_cards(db.figures, shipped)
        revised = (await db.figures.find_one({"slug":"reordered"}))["sections"]["communicates"]
        assert revised["examples"] == ["Separate example", new] and revised["example_sources"][1] == new_ref
        preserved = (await db.figures.find_one({"slug":"edited"}))["sections"]["communicates"]
        assert preserved["examples"][1] == old and preserved["example_sources"][1]["label"] == "Staff source"
    run_case(scenario)


def test_picasso_correction_updates_the_shipped_card_and_keeps_custom_citations(monkeypatch):
    from backend import server

    row = next(p for p in content.seed_json("figure_profiles/arts_expansion.json") if p["slug"] == "pablo-picasso")
    shipped = content.attach_example_sources(row)
    comm = shipped["sections"]["communicates"]
    old = ("In Les Demoiselles d’Avignon, the five figures take up nearly the whole canvas and look outward. "
           "Shifting angles keep the viewer from settling into a single comfortable position in front of them.")
    old_ref = {**comm["example_sources"][0], "example": old}
    custom = {"label": "Staff annotation", "example": old, "url": "https://www.moma.org/audio/playlist/3/171"}
    assert old_ref["url"] == "https://www.moma.org/collection/works/79766"
    assert "Three look directly at the viewer" in comm["examples"][0]

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        key = {"slug": row["slug"]}
        await db.figures.insert_one({**row, "published": True, "matches": row["sections"]["matches"]})
        async def install(citations):
            await db.figures.update_one(key, {"$set": {
                "sections.communicates.examples": [comm["examples"][1], old],
                "sections.communicates.example_sources": citations,
                "editorial.draft": {"title":"Retain this draft"},
            }})
        await install([old_ref, custom, comm["example_sources"][1]])
        await content.revise_career_cards(db.figures, {row["slug"]: shipped})
        edited = await db.figures.find_one(key)
        assert edited["sections"]["communicates"]["examples"][1] == old
        assert edited["sections"]["communicates"]["example_sources"] == [old_ref, custom, comm["example_sources"][1]]
        await install([old_ref, comm["example_sources"][1]])
        for _ in range(2):
            await content.revise_career_cards(db.figures, {row["slug"]: shipped})
        assert (await db.figures.find_one(key))["editorial"]["draft"]["title"] == "Retain this draft"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://fixture") as api:
            reader = await api.get("/api/public/figures/pablo-picasso")
            reader.raise_for_status()
            article = reader.json()
            public = article["sections"]["communicates"]
            assert public["examples"] == [comm["examples"][1], comm["examples"][0]]
            assert public["example_sources"] == [comm["example_sources"][0], comm["example_sources"][1]]
            assert article["source_locators"] == [{
                "example": comm["examples"][0], "url": old_ref["url"],
                "locator": "Three of the five naked protagonists stare outward",
            }]
            assert article["evidence_review"]["lead_status"] == "changed"
            assert not article["evidence_review"]["career"] and "Retain this draft" not in reader.text
    run_case(scenario)


def test_reviewed_career_examples_have_two_source_linked_readings():
    batch = figure_evidence.review_batch()["profiles"]
    assert len(batch) == 155
    for row in batch.values():
        assert len(row["readers"]) == 2
        assert all(state in {"both", "one", "neither"} for state in row["versions"].values())
        for reader in row["readers"]:
            assert reader["reason"] and reader["label"] and reader["examples"]
            assert all(1 <= number <= 4 for number in reader["examples"])


def test_rawya_revision_updates_only_original_text_and_citations_after_an_editor_reorders_cards():
    profile = next(p for p in content.seed_json("figure_profiles/politics_global.json") if p["slug"] == "rawya-ateya")
    profile = content.attach_example_sources(profile)
    original = ("As a member of parliament, Ateya pushed for two months of maternity leave at full pay. "
                "She made her request specific enough for legislators and working women to see exactly what entitlement she was seeking.")
    citation = {**profile["sources"][2], "example": original}
    old_heading = ("One of the first women elected to Egypt’s National Assembly, Rawya Ateya brought the length "
                   "and pay of maternity leave into parliamentary debate.")
    async def scenario(db):
        await db.figures.insert_one({"slug": "rawya-ateya", "identity": old_heading,
            "sources": [{"label": "Editor’s memoir label", "url": profile["sources"][-1]["url"]}],
            "sections": {"identity": old_heading, "communicates": {
                "examples": ["Editor’s example", original, original],
                "example_sources": [{}, citation, {**citation, "label": "Editor’s citation"}]}},
            "editorial": {"draft": {"note": "private desk copy"}}})
        for _ in range(2):
            await content.revise_rawya_profile(db.figures, profile)
        row = await db.figures.find_one({"slug": "rawya-ateya"})
        revised = profile["sections"]["communicates"]
        assert row["identity"] == row["sections"]["identity"] == profile["sections"]["identity"]
        assert row["sections"]["communicates"]["examples"] == ["Editor’s example", revised["examples"][2], original]
        assert row["sections"]["communicates"]["example_sources"][1:] == [
            revised["example_sources"][2], {**citation, "label": "Editor’s citation"}]
        assert len(row["sources"]) == 1 and row["sources"][0]["label"] == "Editor’s memoir label"
        assert row["editorial"]["draft"]["note"] == "private desk copy"
    run_case(scenario)


def test_editor_review_uses_current_evidence_and_keeps_staff_notes_private(monkeypatch):
    from backend import server
    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
        profiles = content.seed_json("figure_profiles/politics_europe.json")
        profile = next(p for p in profiles if p["slug"] in figure_evidence.review_batch()["profiles"])
        row = content.attach_example_sources(profile)
        await db.figures.insert_one({**row, "matches": row["sections"]["matches"], "published": True})
        slug = row["slug"]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as api:
            queue = (await api.get("/api/admin/editorial/figure-reviews")).json()
            assert len(queue) == 1 and queue[0]["status"] in {"both", "one", "neither"}
            public_url = "/api/public/figures/" + slug
            assert len((await api.get(public_url)).json()["evidence_review"]["readers"]) == 2
            path = "/api/admin/editorial/figure-reviews/" + slug
            body = {"version": queue[0]["version"], "result": "revise", "note": "internal editorial note"}
            r = await api.put(path, json=body)
            assert r.is_success and r.json()["result"] == "revise"
            assert (await api.put(path, json=body)).status_code == 409
            published = await api.get(public_url)
            assert published.json()["evidence_review"]["staff_result"] == "revise"
            assert published.json()["evidence_review"]["staff_reviewed_at"] == r.json()["at"]
            assert "internal editorial note" not in published.text and "lead_review_decision" not in published.json()
            listed = (await api.get("/api/public/figures")).json()[0]
            assert listed["lead_review_status"] == "revise" and "sections" not in listed and "lead_review_decision" not in listed
            await db.figures.update_one({"slug": slug}, {"$set": {"sections.communicates.examples.0": "New evidence"}})
            assert (await api.put(path, json={**body, "previous_id": r.json()["id"]})).status_code == 409
            updated_review = (await api.get(public_url)).json()["evidence_review"]
            assert updated_review["lead_status"] == "changed" and updated_review["readers"] == [] and updated_review["staff_result"] is None
            listed = (await api.get("/api/public/figures")).json()[0]
            assert listed["lead_review_status"] == "changed"
            assert (await api.get("/api/admin/editorial/figure-reviews")).json()[0]["status"] == "changed"
    run_case(scenario)


def test_new_and_previously_unassessed_articles_can_be_reviewed_without_inventing_readers(monkeypatch):
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
        slug = "new-figure"
        base = {"name": "New Figure", "matches": [{"number": 1}],
                "sections": {"map":{"closest":{"number":1}}, "communicates":{"examples":["Example"]}}}
        await db.figures.insert_many([{**base, "slug": slug, "published": True},
                                      {**base, "slug": "unpublished", "published": False}])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as api:
            queue_path = "/api/admin/editorial/figure-reviews"
            queue = (await api.get(queue_path)).json()
            assert [item["slug"] for item in queue] == [slug]
            assert queue[0]["status"] == "unassessed" and queue[0]["readers"] == []
            body = {"version":queue[0]["version"], "result":"retain", "note":"private editorial observation"}
            assert (await api.put(queue_path + "/unpublished", json=body)).status_code == 404
            assert (await api.put(queue_path + "/" + slug, json=body)).is_success
            assert (await api.put(queue_path + "/" + slug, json=body)).status_code == 409
            for status in ("retain", "changed"):
                listing = await api.get("/api/public/figures")
                article = await api.get("/api/public/figures/" + slug)
                assert listing.json()[0]["lead_review_status"] == status
                public_review = article.json()["evidence_review"]
                assert public_review["lead_status"] == status and public_review["readers"] == []
                assert "private editorial observation" not in listing.text + article.text
                if status == "retain":
                    await db.figures.update_one({"slug": slug}, {"$set": {"sections.communicates.examples": ["Edited"]}})
            refreshed = (await api.get(queue_path)).json()[0]
            assert refreshed["status"] == "changed" and refreshed["decision"] is None
            assert (await api.put(queue_path + "/" + slug, json=body)).status_code == 409
            assert (await api.put(queue_path + "/" + slug, json={**body, "version":refreshed["version"], "result":"revise"})).is_success
    run_case(scenario)
