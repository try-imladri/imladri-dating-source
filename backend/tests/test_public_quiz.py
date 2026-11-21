import asyncio
import copy
import os

import httpx

from backend.tests.test_free_plan_quota import run_case


def test_public_quiz_publication_conflict_and_visitor_config(monkeypatch):
    monkeypatch.setenv("MONGO_URL", os.getenv("IMLADRI_TEST_MONGO_URL", "mongodb://127.0.0.1:27017"))
    monkeypatch.setenv("DB_NAME", "public_quiz_unused")
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_YXV0aC5leGFtcGxlLnRlc3Qk")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_localonly")
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        await db.archetypes.insert_one({"slug": "first-result", "number": 1, "name": "First", "unlocked": True})
        quiz = {"questions": [{"q": "Choose", "a": ["One", "Two"]},
                              {"q": "Pattern", "a": ["One", "Two", "Three"]},
                              {"q": "Reply", "a": ["One", "Two", "Three"]}],
                "outcomes": [[["first-result"] * 3 for _ in range(3)] for _ in range(2)]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            path = "/api/admin/public-quiz"
            assert (await api.get(path)).status_code == 401
            monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
            data = (await api.get(path)).json()
            assert data["revision"] is None and data["quiz"] is None
            assert (await api.get("/api/public/config")).json()["public_quiz"] is None
            # Only one editor may publish against the same revision.
            body = {"quiz": quiz, "revision": None}
            results = await asyncio.gather(api.put(path, json=body), api.put(path, json=body))
            assert sorted(r.status_code for r in results) == [200, 409]
            assert (await api.get("/api/public/config")).json()["public_quiz"] == quiz
            revision = (await api.get(path)).json()["revision"]
            bad = copy.deepcopy(quiz)
            bad["questions"][0]["a"] = ["One", " one "]
            assert (await api.put(path, json={"quiz": bad, "revision": revision})).status_code == 422
            bad = copy.deepcopy(quiz)
            bad["outcomes"][1][2][2] = "missing-archetype"
            assert (await api.put(path, json={"quiz": bad, "revision": revision})).status_code == 422
            assert (await api.put(path, json={"quiz": quiz, "revision": {"$exists": True}})).status_code == 422
            restored = await api.put(path, json={"revision": revision, "restore_previous": True})
            assert restored.status_code == 200 and restored.json()["quiz"] is None
            assert (await api.get("/api/public/config")).json()["public_quiz"] is None
            assert (await api.put(path, json={"revision": revision, "restore_previous": True})).status_code == 409
            assert (await api.get(path)).json()["revision"] == restored.json()["revision"] != revision
    run_case(scenario)
