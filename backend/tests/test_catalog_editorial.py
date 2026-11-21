import asyncio
from copy import deepcopy

import httpx

from backend import catalog_editorial, content, server
from backend.tests.test_free_plan_quota import run_case


def test_every_seed_and_publishing_lifecycle(monkeypatch):
    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        seed = content.seed_json
        monkeypatch.setattr(content, "seed_json", lambda n: {} if n == "media_manifest.json" else seed(n))
        await content.seed_base(db)
        for group, count in (("archetypes", 121), ("figures", 500)):
            records = await db[group].find({}, {"_id": 0}).to_list(None)
            assert len(records) == count
            for row in records:
                await catalog_editorial.validate(
                    db, group, catalog_editorial.content(group, row), row.get("number")
                )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://localhost"
        ) as api:
            for group, slug in (("archetypes", "love-bomber"), ("figures", "julia-gillard")):
                endpoint = f"/api/admin/editorial/articles/{group}/{slug}"
                public = f"/api/public/{group}/{slug}"
                monkeypatch.delitem(server.app.dependency_overrides, server.admin_dep, raising=False)
                monkeypatch.delitem(server.app.dependency_overrides, server.current_member, raising=False)
                assert (await api.get(endpoint)).status_code == 401
                monkeypatch.setitem(
                    server.app.dependency_overrides, server.current_member, lambda: {"role": "member"}
                )
                assert (await api.get(endpoint)).status_code == 403
                monkeypatch.setitem(
                    server.app.dependency_overrides,
                    server.admin_dep,
                    lambda: {"role": "admin", "user_id": "test-editor-only"},
                )
                previous = (await api.get(public)).json()
                start = (await api.get(endpoint)).json()
                assert start["draft"] is None and start["live_revision"] == 0
                if group == "figures":
                    article = deepcopy(start["published"])
                    communication = article["sections"]["communicates"]
                    extra = {**communication["example_sources"][0], "url": "https://archive.example/transcript"}
                    communication["example_sources"].append(extra)
                    assert content.attach_example_sources(article) == article
                    body = {"version": start["version"], "content": article}
                    assert (await api.post(endpoint + "/preview", json=body)).status_code == 200
                    communication["example_sources"].append(extra)
                    assert (await api.post(endpoint + "/preview", json=body)).status_code == 422
                    communication["example_sources"].pop()
                    communication["examples"].append(communication["examples"][0])
                    assert (await api.post(endpoint + "/preview", json=body)).status_code == 422
                values = [{**start["published"], "name": name} for name in ("Private draft A", "Private draft B")]
                race = await asyncio.gather(
                    *(
                        api.put(endpoint + "/draft", json={"version": start["version"], "content": candidate})
                        for candidate in values
                    )
                )
                assert sorted(r.status_code for r in race) == [200, 409]
                row = next(r.json() for r in race if r.is_success)
                name = row["draft"]["name"]
                assert (await api.get(public)).json() == previous
                assert name not in (await api.get("/api/public/" + group)).text
                preview = await api.post(
                    endpoint + "/preview", json={"version": row["version"], "content": row["draft"]}
                )
                assert preview.is_success and preview.json()["name"] == name
                assert "editorial" not in preview.json() and "lead_review_decision" not in preview.json()
                assert (
                    await api.post(endpoint + "/publish", json={"version": start["version"]})
                ).status_code == 409
                release = (await api.post(endpoint + "/publish", json={"version": row["version"]})).json()
                live = (await api.get(public)).json()
                assert live["name"] == name and "editorial" not in live and "test-editor-only" not in str(live)
                assert release["live_revision"] == 1 and release["history"][0]["changes"] == ["name"]
                rev = release["history"][0]["revision"]
                restored = (
                    await api.post(endpoint + "/restore", json={"revision": rev, "version": release["version"]})
                ).json()
                assert restored["draft"] == start["published"] and (await api.get(public)).json()["name"] == name
                assert (await api.post(endpoint + "/publish", json={"version": restored["version"]})).is_success
                assert (await api.get(public)).json() == previous
                current = (await api.get(endpoint)).json()
                broken = deepcopy(current["published"])
                if group == "figures":
                    broken["sections"]["communicates"]["example_sources"][0]["url"] = "javascript:noop()"
                else:
                    broken["sections"]["threads"][0]["bubbles"][0]["text"] = {"malformed": True}
                edited = (
                    await api.put(endpoint + "/draft", json={"version": current["version"], "content": broken})
                ).json()
                assert (
                    await api.post(endpoint + "/preview", json={"version": edited["version"], "content": broken})
                ).status_code == 422
                assert (
                    await api.post(endpoint + "/publish", json={"version": edited["version"]})
                ).status_code == 422
                assert (await api.get(public)).json() == previous
                assert (
                    await api.put(f"/api/admin/{group}/{slug}", json={"name": "Legacy overwrite"})
                ).status_code == 428

    run_case(scenario)


def test_invalid_legacy_article_blocks_wave_and_individual_release(monkeypatch):
    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
        await db.archetypes.insert_many(
            [
                {"slug": "incomplete", "number": 1, "wave": 1, "unlocked": False, "sections": {"summary": 3}},
                {"slug": "empty", "number": 2, "wave": 1, "unlocked": False},
            ]
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://localhost"
        ) as api:
            for method, path in (("POST", "/api/admin/waves/1"), ("PUT", "/api/admin/archetypes/incomplete")):
                assert (await api.request(method, path, json={"unlocked": True})).status_code == 422
            assert await db.archetypes.count_documents({"unlocked": True}) == 0

    run_case(scenario)
