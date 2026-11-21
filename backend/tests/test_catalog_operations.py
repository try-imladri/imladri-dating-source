import asyncio
import os
import hashlib
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from bson import ObjectId

from backend.tests.test_free_plan_quota import run_case


def complete_archetype(slug, number, cousin, wave=1, unlocked=True):
    from backend.content import seed_json
    sections = seed_json("male_archetypes.json")["love-bomber"]
    sections["closest_cousin"]["number"] = cousin
    return {"slug": slug, "number": number, "wave": wave, "unlocked": unlocked, "name": slug,
            "identity": sections["identity"], "one_liner": "An example", "texting_tags": [], "red_flag_tags": [], "sections": sections}


async def publish_article(api, path, patch):
    url = "/api/admin/editorial/articles/" + path
    current = (await api.get(url)).json()
    draft = await api.put(url + "/draft", json={"version": current["version"], "content": {**current["published"], **patch}})
    assert draft.is_success, draft.text
    return await api.post(url + "/publish", json={"version": draft.json()["version"]})


def test_reviewed_figure_lead_changes_together_and_preserves_editor_work(monkeypatch):
    from backend import content

    async def scenario(db):
        slug = "julia-gillard"
        old = [{"number": 156, "why": "Previous comparison"}]
        previous_map = {"closest": {"number": 156}, "basis": {"observation": "Previous observation"}}
        comm = {"examples": ["Example"], "example_sources": [{"example": "Example", "url": "https://example.org"}]}
        record = {"name": "Julia Gillard", "slug": slug, "matches": old, "published": False, "identity": "Staff bio",
                  "sections": {"matches": old, "map": previous_map, "communicates": comm, "dating": ["Staff vignette"]}}
        fingerprint = content.field_digest({"name": record["name"], "matches": old, "sections.matches": old,
            "sections.map": previous_map, "sections.communicates.examples": comm["examples"],
            "sections.communicates.example_sources": comm["example_sources"]})
        monkeypatch.setattr(content, "REVIEWED_LEAD_BASELINES", {slug: fingerprint})
        shipped = {f["slug"]: f for f in content.seed_json("figure_profiles/politics_global.json") if f["slug"] == slug}
        await db.figures.insert_one(record)
        for field, changed in (("sections_edited", True), ("lead_review_decision", {"result": "retain"}),
                               ("sections.communicates.examples", ["Revised"]), ("sections.map.basis.observation", "Staff edit")):
            await db.figures.update_one({"slug": slug}, {"$set": {field: changed}})
            before = await db.figures.find_one({"slug": slug})
            await content.apply_reviewed_leads(db, shipped)
            assert await db.figures.find_one({"slug": slug}) == before
            await db.figures.replace_one({"slug": slug}, record)
        await content.apply_reviewed_leads(db, shipped)
        after = await db.figures.find_one({"slug": slug})
        assert after["matches"] == after["sections"]["matches"] == shipped[slug]["sections"]["matches"]
        assert after["sections"]["map"] == shipped[slug]["sections"]["map"]
        assert after["sections"]["map"]["closest"]["number"] == 157
        assert after["published"] is False and after["identity"] == "Staff bio"
        assert after["sections"]["dating"] == ["Staff vignette"] and after["sections"]["communicates"] == comm
        await content.apply_reviewed_leads(db, shipped)
        assert after == await db.figures.find_one({"slug": slug})
    run_case(scenario)


def test_staff_cleared_catalog_content_survives_restart(monkeypatch):
    from backend import content, server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
        # Use the real startup loader and article seeds without importing unrelated media.
        seed_json = content.seed_json
        monkeypatch.setattr(content, "seed_json", lambda name: {} if name == "media_manifest.json" else seed_json(name))
        await content.seed_base(db)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            paths = ["archetypes/love-bomber", "archetypes/cool-girl", "figures/elon-musk"]
            for path in paths:
                assert (await api.get("/api/public/" + path)).json()["sections"]
                assert (await api.put("/api/admin/" + path, json={"sections": None, "identity": "Reviewed draft"})).status_code == 428
                # A pre-workflow installation may already contain a deliberately cleared document.
                group, slug = path.split("/")
                await db[group].update_one({"slug": slug}, {"$set": {"sections": None, "sections_edited": True, "identity": "Reviewed draft"}})
            await content.seed_base(db)
            for path in paths:
                article = (await api.get("/api/public/" + path)).json()
                assert article["sections"] is None and article["identity"] == "Reviewed draft"
                assert "sections_edited" not in article
    run_case(scenario)


def test_dating_sections_cover_both_catalogs_and_respect_admin_edits(monkeypatch):
    from backend import content, server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
        seed = content.seed_json
        monkeypatch.setattr(content, "seed_json", lambda name: {} if name == "media_manifest.json" else seed(name))
        await content.seed_base(db)
        paragraphs = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            for group, count, articles in (("figures", 500, content.figure_dating_copy()),
                                          ("archetypes", 121, seed("archetype_dating.json"))):
                index = (await api.get("/api/public/" + group)).json()
                assert len(articles) == count and articles.keys() == {doc["slug"] for doc in index}
                live = await db[group].find({}, {"slug": 1, "sections.dating": 1}).to_list(None)
                for doc in live:
                    copy = doc["sections"]["dating"]
                    assert copy == articles[doc["slug"]] and len(copy) == 2
                    assert all(isinstance(p, str) and p.strip() for p in copy)
                    paragraphs.extend(copy)
            assert len(set(paragraphs)) == len(paragraphs) == 1242
            paths = ("figures/drake", "archetypes/love-bomber")
            for path in paths:
                public = (await api.get("/api/public/" + path)).json()
                assert "dating_seeded" not in public
                sections = public["sections"]
                assert len(sections["dating"]) == 2
                if path.startswith("figures/"):
                    del sections["dating"]
                else:
                    sections["dating"] = ["Custom staff copy."]
                saved = await publish_article(api, path, {"sections": sections})
                assert saved.is_success, saved.text
            await content.seed_base(db)
            assert "dating" not in (await api.get("/api/public/" + paths[0])).json()["sections"]
            assert (await api.get("/api/public/" + paths[1])).json()["sections"]["dating"] == ["Custom staff copy."]
    run_case(scenario)


def test_staff_visibility_applies_to_related_catalog_pages(monkeypatch):
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
        await db.archetypes.insert_many([complete_archetype("visible", 1, 2), complete_archetype("future", 2, 1, unlocked=False)])
        await db.archetypes.update_one({"slug": "future"}, {"$set": {"name": "Future type"}})
        await db.figures.insert_one({"slug": "example", "name": "Example", "published": True, "matches": [{"number": 2}]})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            for visible in (False, True, False):
                assert (await api.put("/api/admin/archetypes/future", json={"unlocked": visible})).is_success
                assert ((await api.get("/api/public/archetypes/future")).status_code == 200) == visible
                figure = (await api.get("/api/public/figures/example")).json()
                index = (await api.get("/api/public/figures")).json()
                cousin = (await api.get("/api/public/archetypes/visible")).json()["cousin_ref"]
                assert (index[0]["matches"][0]["name"] == "Future type") == visible
                assert figure["archetype_refs"]["2"]["unlocked"] == cousin["unlocked"] == visible
                assert ("name" in cousin) == ("slug" in figure["archetype_refs"]["2"]) == visible
            assert (await api.put("/api/admin/figures/example", json={"published": False})).is_success
            assert (await api.get("/api/public/figures")).json() == []
            assert (await api.get("/api/public/figures/example")).status_code == 404
    run_case(scenario)


def test_expanded_figures_share_the_original_layout_and_respect_draft_edits(monkeypatch):
    from collections import Counter
    from pathlib import Path
    from backend import content, server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
        await db.figures.create_indexes(server.INDEXES["figures"])
        await content.seed_public_profiles(db, "first release")
        docs = await db.figures.find({}).to_list(None)
        assert Counter(f["gender"] for f in docs) == {"woman": 250, "man": 244}
        assert len({f["slug"] for f in docs}) == len({f["name"] for f in docs}) == 494
        identities = content.seed_json("figure_person_ids.json")
        assert set(identities) == {f["slug"] for f in docs} | set(content.seed_json("figures.json"))
        assert len(set(identities.values())) == 500
        politics = [p for region in ("americas", "europe", "global")
                    for p in content.seed_json(f"figure_profiles/politics_{region}.json")]
        assert Counter(p["gender"] for p in politics) == {"woman": 150, "man": 150}
        examples = [example for p in politics for example in p["sections"]["communicates"]["examples"]]
        assert len(set(examples)) == len(examples) == 1200
        maps = [p["sections"]["map"] for p in politics]
        for section, field in (("closest", "rationale"), ("cousin", "tell"), ("basis", "observation")):
            assert len({m[section][field].casefold().strip() for m in maps}) == len(politics)
        template = content.seed_json("figures.json")["travis-kelce"]["sections"]
        assets = Path(content.SEED_DIR).parents[1] / "frontend" / "public"
        for f in docs:
            s = f["sections"]
            assert set(s) == set(template) | {"dating"} and s["v"] == template["v"]
            assert all(len(s[k]) == len(template[k]) for k in ("pattern", "power_moves", "leverage"))
            for k in ("communicates", "handling"):
                assert {key: len(v) for key, v in s[k].items()} == {key: len(v) for key, v in template[k].items()}
            assert set(s["spectrum"]) == {"interested", "invested", "done"}
            roster = content.FEMALE_ARCHETYPES if f["gender"] == "woman" else content.ARCHETYPES
            allowed = {row[0] for row in roster}
            mapped = [s["map"]["closest"], s["map"]["cousin"], *s["map"]["secondary"]]
            assert {m["number"] for m in mapped} <= allowed
            assert f["matches"] == s["matches"] and 1 <= len(f["matches"]) <= 2
            assert f["matches"][0]["number"] == mapped[0]["number"]
            assert all(src["label"] and src["url"].startswith("https://") for src in f["sources"])
            examples, citations = s["communicates"]["examples"], s["communicates"]["example_sources"]
            assert len(set(examples)) == 4 and [ref["example"] for ref in citations] == examples
            assert all(ref["url"] in {src["url"] for src in f["sources"]} for ref in citations)
            basis = s["map"]["basis"]
            assert basis["style"] and basis["observation"] and basis["sources"]
            assert all(src["url"] in {ref["url"] for ref in citations} for src in basis["sources"])
            assert (assets / f["photo_url"].lstrip("/")).is_file()
            assert f["photo_attribution"]["source"].startswith("https://commons.wikimedia.org/")
        slug = "marie-curie"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            expected_slugs = {f["slug"] for f in docs}
            public_directory = (await api.get("/api/public/figures")).json()
            staff_directory = (await api.get("/api/admin/figures", params={"summary": True})).json()
            assert {p["slug"] for p in public_directory} == {p["slug"] for p in staff_directory} == expected_slugs
            assert (await api.get("/api/public/figures/" + slug)).json()["sections"]["map"]["closest"]
            expected = (await api.get("/api/admin/figures/" + slug)).json()
            mapping = expected["sections"]["map"]
            refs = {mapping["closest"]["number"], mapping["cousin"]["number"], *(m["number"] for m in mapping["secondary"])}
            await db.archetypes.insert_many([{"number": n} for n in refs])
            mismatch = [{"number": 107, "why": "A conflicting lead"}]
            assert (await publish_article(api, "figures/"+slug, {"matches": mismatch})).status_code == 422
            assert (await api.put("/api/admin/figures/" + slug, json={"published": False})).is_success
            assert (await publish_article(api, "figures/"+slug, {"identity": "Staff edit"})).is_success
            await content.seed_public_profiles(db, "restart")
            assert (await api.get("/api/public/figures/" + slug)).status_code == 404
            assert slug not in {f["slug"] for f in (await api.get("/api/public/figures")).json()}
            draft = next(f for f in (await api.get("/api/admin/figures")).json() if f["slug"] == slug)
            assert draft["published"] is False and draft["identity"] == "Staff edit"
            # An unfinished draft cannot replace the previously published catalog revision on release.
            assert (await publish_article(api, "figures/"+slug, {"matches": mismatch})).status_code == 422
            assert (await api.put("/api/admin/figures/" + slug, json={"published": True})).is_success
            assert (await api.get("/api/public/figures/" + slug)).json()["matches"] == expected["matches"]
            assert await db.notifications.count_documents({}) == 0
    run_case(scenario)


def test_new_archetypes_have_the_full_library_article_and_existing_admin_controls(monkeypatch):
    from backend import content, server
    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
        seed = content.seed_json
        monkeypatch.setattr(content, "seed_json", lambda name: {} if name == "media_manifest.json" else seed(name))
        await content.seed_base(db)
        media = seed("media_manifest.json")
        editions = seed("communication_roster.json")
        assert len(editions) == len({row["row"][0] for row in editions}) == len({row["row"][1] for row in editions}) == 24
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            public = (await api.get("/api/public/archetypes")).json()
            admin = (await api.get("/api/admin/archetypes")).json()
            assert len(admin) == len(public) == 121 and len({a["slug"] for a in public}) == 121
            for e in editions:
                n, slug = e["row"][:2]
                entry = (await api.get("/api/public/archetypes/" + slug)).json()
                a = entry["sections"]
                old = seed(e["gender"] + "_archetypes.json")["grown-" + ("woman" if e["gender"] == "female" else "man")]
                assert set(a) == set(old) | {"dating"} and all(len(a[k]) == len(old[k]) for k in old if isinstance(old[k], list))
                for char in a["characters"]:
                    assert char["actor"] and char["why"] and char["image_url"].split("/")[-1] in media
                assert all(6 <= len(t["bubbles"]) <= 8 for t in a["threads"])
                assert entry["number"] == n and entry["gender"] == e["gender"] and entry["cousin_ref"]["unlocked"]
                assert any(r["slug"] == slug and r["unlocked"] and r["wave"] == 7 for r in admin)
            slug = editions[0]["row"][1]
            assert (await api.put("/api/admin/archetypes/" + slug, json={"unlocked": False})).is_success
            await content.seed_base(db)
            assert (await api.get("/api/public/archetypes/" + slug)).status_code == 404
    run_case(scenario)


def test_figure_example_upgrade_preserves_staff_content_and_visibility(monkeypatch):
    import json
    from backend import content, server
    original = ["Earlier career example " + str(i) for i in range(4)]
    old_hash = hashlib.sha256(json.dumps(original, separators=(",", ":")).encode()).hexdigest()
    seed = content.seed_json
    monkeypatch.setattr(content, "seed_json", lambda name: (
        dict.fromkeys(["drake", "serena-williams", "marie-curie"], old_hash)
        if name == "figure_legacy_examples.json" else seed(name)))

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
        for slug, examples in (("drake", original), ("serena-williams", original), ("marie-curie", ["Editor wrote this"])):
            await db.figures.insert_one({"slug": slug, "name": slug, "identity": "Staff introduction", "published": False,
                "sections_edited": True, "sources": [{"url": "https://example.org/staff", "label": "Staff reference"}],
                "sections": {"pattern": ["Staff edit"], "communicates": {"examples": examples}}})
        await content.seed_public_profiles(db, "upgrade")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            summaries = (await api.get("/api/admin/figures?summary=true")).json()
            assert all({"slug", "name", "published"} <= set(row) <= {"slug", "name", "published", "lead_type"} for row in summaries)
            for slug in ("drake", "serena-williams", "marie-curie"):
                doc = (await api.get("/api/admin/figures/" + slug)).json()
                assert doc["sections"]["pattern"] == ["Staff edit"] and doc["identity"] == "Staff introduction"
                assert doc["published"] is False and (await api.get("/api/public/figures/" + slug)).status_code == 404
                assert "https://example.org/staff" in {ref["url"] for ref in doc["sources"]}
                comm = doc["sections"]["communicates"]
                if slug == "marie-curie":
                    assert comm == {"examples": ["Editor wrote this"]}
                else:
                    assert len(comm["example_sources"]) == 4
                    assert [ref["example"] for ref in comm["example_sources"]] == comm["examples"] != original
            before = await db.figures.find_one({"slug": "drake"})
            await content.seed_public_profiles(db, "restart")
            assert await db.figures.find_one({"slug": "drake"}) == before
            assert await db.notifications.count_documents({}) == 0
    run_case(scenario)


def test_public_signup_reaches_staff_waitlist_with_stable_private_pagination(monkeypatch):
    monkeypatch.setenv("MONGO_URL", os.getenv("IMLADRI_TEST_MONGO_URL", "mongodb://127.0.0.1:27017"))
    monkeypatch.setenv("DB_NAME", "imladri_unused")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_catalog_fixture")
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        await db.settings.insert_one({"key": "global", "waitlist_mode": True})
        await db.waitlist.create_indexes(server.INDEXES["waitlist"])
        # A legacy import and a server-side upsert can generate ObjectIds on different hosts.
        # Give the older fixture an earlier timestamp, independent of the test's wall-clock second.
        legacy_id = ObjectId.from_datetime(datetime.now(timezone.utc) - timedelta(days=1))
        await db.waitlist.insert_many([{"_id": legacy_id, "email": "legacy@example.com", "origin": "https://private.example/item?email=hidden@example.com", "ts": server.now()},
                                       {"email": "another@example.com", "env": "other"}])
        await db.users.insert_one({"user_id": "legacy", "origin": "hidden@example.com"})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            path = "/api/admin/waitlist"
            assert (await api.get(path)).status_code == 401
            monkeypatch.setitem(server.app.dependency_overrides, server.current_member, lambda: {"role": "member"})
            assert (await api.get(path)).status_code == 403
            for n in range(3):
                assert (await api.post("/api/public/waitlist", json={"email": f"Join{n}@Example.com", "origin": "demo"})).is_success
            monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
            assert (await api.post("/api/public/waitlist", json={"email": "another@example.com"})).status_code == 503
            assert (await api.get("/api/users")).json()["users"][0]["origin"] == "other"
            first = await api.get(path, params={"limit": 2})
            assert first.headers["Cache-Control"] == "no-store"
            entries = first.json()["entries"]
            assert [row["email"] for row in entries] == ["join2@example.com", "join1@example.com"]
            assert all(set(row) == {"email", "origin", "ts"} for row in entries)
            await db.waitlist.delete_one({"email": entries[-1]["email"]})
            second = (await api.get(path, params={"after": first.json()["next"], "limit": 2})).json()
            assert [row["email"] for row in second["entries"]] == ["join0@example.com", "legacy@example.com"]
            assert second["entries"][-1]["origin"] == "other"
            assert second["next"] is None
            assert (await api.get(path, params={"after": "invalid"})).status_code == 422
    run_case(scenario)


def test_bulk_release_honors_switch_and_recovers_partial_bell_delivery(monkeypatch):
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        await db.notifications.create_indexes(server.INDEXES["notifications"])
        await db.archetypes.insert_many([complete_archetype(f"entry-{i}", i+1, (i+1)%6+1, unlocked=False) for i in range(6)])
        await db.archetypes.insert_one({"wave": 2, "slug": "already-open", "unlocked": True})
        await db.users.insert_many([{"user_id": uid, "env": env} for uid, env in (("a", server.ENV), ("b", server.ENV), ("other", "other"))])
        monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            publish = {"unlocked": True, "notify": True}
            path = "/api/admin/waves/1"
            assert (await api.post("/api/admin/waves/2", json=publish)).json() == {"updated": 0}
            assert (await api.post(path, json={"unlocked": True})).json() == {"updated": 6}
            assert (await api.post(path, json=publish)).json() == {"updated": 0}
            assert await db.notifications.count_documents({}) == 0
            assert (await api.post(path, json={"unlocked": False, "notify": True})).json() == {"updated": 6}
            real_notify = server.notify_all

            async def interrupted(kind, title, body, link, key):
                await server.notify("a", kind, title, body, link, key)
                raise RuntimeError("test interruption")
            monkeypatch.setattr(server, "notify_all", interrupted)
            with pytest.raises(RuntimeError, match="test interruption"):
                await api.post(path, json=publish)
            monkeypatch.setattr(server, "notify_all", real_notify)
            entered, proceed = asyncio.Event(), asyncio.Event()
            async def paused(*args):
                entered.set()
                await proceed.wait()
                await real_notify(*args)
            monkeypatch.setattr(server, "notify_all", paused)
            work = asyncio.create_task(api.post(path, json=publish))
            await asyncio.wait_for(entered.wait(), timeout=3)
            try:
                assert (await api.post(path, json={"unlocked": False})).status_code == 409
                assert (await api.post(path, json=publish)).status_code == 409
            finally:
                proceed.set()
            assert (await work).is_success
            assert (await api.post(path, json=publish)).is_success
            assert await db.notifications.count_documents({"link": "/types", "type": "new_page"}) == 2
            assert await db.notifications.count_documents({"user_id": "other"}) == 0
            assert (await api.post(path, json={"unlocked": False})).is_success
            assert (await api.post(path, json=publish)).is_success
            assert await db.notifications.count_documents({}) == 4
    run_case(scenario)


def test_registration_invite_only_recognizes_an_open_current_sponsored_invitation(monkeypatch):
    from backend import server
    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        token = "test-invitation-" + "a" * 32
        now = int(time.time())
        await db.users.insert_one({"user_id": "sponsor", "env": server.ENV, "couples_adult_at": server.now(),
            "tier": "paid", "stripe_subscription_id": "sub_fixture", "subscription_status": "active",
            "subscription_period_start": now - 60, "subscription_period_end": now + 600})
        await db.couples_pairs.insert_one({"_id": "pair", "state": "invited", "env": server.ENV,
            "inviter": "sponsor", "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            async def check(value):
                return await api.post("/api/public/registration-invite", json={"token": value})
            valid = await check(token)
            assert valid.json() == {"eligible": True} and valid.headers["cache-control"] == "no-store"
            assert (await check("bogus" * 9)).json() == {"eligible": False}
            await db.users.update_one({"user_id": "sponsor"}, {"$set": {"closing": True}})
            assert (await check(token)).json() == {"eligible": False}
            await db.users.update_one({"user_id": "sponsor"}, {"$unset": {"closing": ""}})
            for values in ({"env": "other"}, {"env": server.ENV, "state": "confirming"},
                           {"state": "invited", "expires_at": "2000-01-01"}):
                await db.couples_pairs.update_one({"_id": "pair"}, {"$set": values})
                assert (await check(token)).json() == {"eligible": False}
    run_case(scenario)
