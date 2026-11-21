import asyncio
import time
import httpx

from backend.intimacy_catalog import questions
from backend.quiz import TYPES, default_quiz, personality
from backend.tests.test_free_plan_quota import run_case


def test_question_publication_reaches_members_without_collecting_answers(monkeypatch):
    from backend import server
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_intimacy_question_fixture")

    async def exercise(db):
        monkeypatch.setattr(server, "db", db)
        app = server.app
        endpoint = "/api/admin/quiz/intimacy"
        (section, topic), (question, phrase) = next(iter(questions().items()))
        assert len(questions()) == 176
        body = {"section": section, "topic": topic, "question": "Updated private question?",
                "phrase": phrase, "revision": 0}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            assert (await client.get(endpoint)).status_code == 401
            assert (await client.get("/api/admin/quiz/results")).status_code == 401
            viewer = {"user_id":"catalog-viewer", "env":server.ENV, "role":"member", "tier":"paid",
                "stripe_subscription_id":"sub_catalog_fixture", "subscription_status":"active",
                "subscription_period_start":int(time.time())-300, "subscription_period_end":int(time.time())+3600}
            monkeypatch.setitem(app.dependency_overrides, server.current_member, lambda: dict(viewer))
            assert (await client.get(endpoint)).status_code == 403
            assert (await client.get("/api/admin/quiz/results")).status_code == 403
            assert (await client.put(endpoint, json=body)).status_code == 403
            assert (await client.get("/api/quiz")).json()["intimacy_copy"] == []
            monkeypatch.setitem(app.dependency_overrides, server.admin_dep, lambda: {"role": "admin"})
            assert (await client.get(endpoint)).json() == {"overrides": [], "drafts": []}
            draft_url = endpoint + "/draft"
            draft = {**body, "draft_revision": 0}
            saves = await asyncio.gather(*(client.put(draft_url, json=draft) for _ in range(2)))
            assert sorted(r.status_code for r in saves) == [200, 409]
            assert (await client.get(endpoint)).json() == {"overrides": [], "drafts": [{**draft, "draft_revision": 1}]}
            assert (await client.get("/api/quiz")).json()["intimacy_copy"] == []
            assert (await client.put(draft_url, json={**draft, "draft_revision": 1})).json()["draft_revision"] == 2
            types = (await client.get("/api/admin/quiz/results")).json()["types"]
            assert {result["code"] for result in types} == set(TYPES)
            for result in types:
                member_result = personality({"mbti": result["code"]}, default_quiz())
                assert result == {k: member_result[k] for k in ("code", "name", "line")}
            assert (await client.put(endpoint, json={**body, "topic": "unknown"})).status_code == 422
            assert (await client.put(endpoint, json={**body, "answers": ["private"]})).status_code == 422
            responses = await asyncio.gather(*(client.put(endpoint, json=body) for _ in range(2)))
            assert sorted(r.status_code for r in responses) == [200, 409]
            expected = {"section": section, "topic": topic, "question": body["question"], "phrase": None}
            assert (await client.get("/api/quiz")).json()["intimacy_copy"] == [expected]
            assert (await client.get(endpoint)).json()["overrides"] == [{**expected, "revision": 1, "previous": {"question": question, "phrase": phrase}}]
            assert (await client.get(endpoint)).json()["drafts"] == []
            assert (await client.put(draft_url, json={**draft, "draft_revision": 2})).status_code == 409
            restored = await client.put(endpoint, json={**body, "question": question, "revision": 1})
            assert restored.status_code == 200 and restored.json()["revision"] == 2
            assert (await client.get("/api/quiz")).json()["intimacy_copy"] == []
            assert (await client.put(endpoint, json=body)).status_code == 409
            assert await db.intimacy_question_copy.count_documents({}) == 1

    run_case(exercise)
