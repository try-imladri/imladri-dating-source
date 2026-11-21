import asyncio
import time

import httpx

from backend import answer_history, intimacy_catalog, research
from backend.tests.test_free_plan_quota import run_case


def test_private_intimacy_progress_resume_scope_versions_and_erasure(monkeypatch):
    from backend import server
    uid = "saved_intimacy_fixture"
    other = "another_member_fixture"
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_intimacy")
    identity = {"user_id": uid}

    async def check(db):
        monkeypatch.setattr(server, "db", db)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as client:
            path = "/api/me/intimacy-answers"
            assert (await client.get(path)).status_code == 401
            monkeypatch.setitem(server.app.dependency_overrides, server.require_user, lambda: identity)
            for who in (uid, other):
                await db.users.insert_one({"user_id": who, "role": "member", "tier": "paid", "env": server.ENV,
                    "stripe_subscription_id": "sub_fixture_"+who, "subscription_period_start": int(time.time())-300,
                    "subscription_period_end": int(time.time())+300, "about": {"demographics": {"age": 30}}})
            first, second = list(intimacy_catalog.questions().items())[:2]
            section, topic = first[0]
            def edit(item, index, selection, revision):
                (sec, top), (question, phrase) = item
                return {"id": f"i:{sec}:{index}", "topic": top, "question": question, "phrase": phrase,
                        "adult_confirmed": True, "selection": selection, "revision": revision}
            body = edit(first, 0, {"choice": "It depends", "conditions": ["In private"]}, 0)
            second_id = edit(second, 1, None, 0)["id"]
            assert (await client.get(path)).json() == {"answers": {}, "revision": 0, "previous": 0}
            for change in ({"adult_confirmed": False}, {"selection": {"choice": "Off limits", "conditions": ["In private"]}},
                           {"selection": {"choice": "unknown"}}):
                assert (await client.patch(path, json={**body, **change})).status_code == 422
            assert (await client.patch(path, json={**body, "question": "stale"})).status_code == 412
            racing = await asyncio.gather(*(client.patch(path, json=body) for _ in range(2)))
            assert sorted(r.status_code for r in racing) == [200, 409]
            assert (await client.patch(path, json=edit(second, 1, None, 1))).json() == {"revision": 2}
            state = await client.get(path)
            assert state.headers["cache-control"] == "private, no-store"
            assert state.json() == {"revision": 2, "previous": 0, "answers": {body["id"]: body["selection"], second_id: None}}
            row = await db.intimacy_answers.find_one({"_id": uid})
            variables = research.catalog({"short": [], "extended": [], "further": []}, [])[1]
            assert row["signatures"][body["id"]] == variables[body["id"]]["signature"]
            assert row["values"][body["id"]] == research.CHOICES.index("It depends")
            assert await db.research_responses.count_documents({}) == 0
            identity["user_id"] = other
            assert (await client.get(path)).json()["answers"] == {}
            assert (await client.get("/api/me/export")).json()["intimacy_answers"] == []
            identity["user_id"] = uid
            await intimacy_catalog.publish(db, intimacy_catalog.QuestionEdit(section=section, topic=topic,
                question="New wording?", phrase=body["phrase"], revision=0))
            updated = (await client.get(path)).json()
            assert updated["previous"] == 1 and updated["answers"] == {second_id: None}
            exported = (await client.get("/api/me/export")).json()["intimacy_answers"]
            assert next(q for q in exported if q["id"] == body["id"]) == {"id": body["id"], "topic": topic,
                "question": body["question"], "answer": "It depends", "skipped": False, "conditions": ["In private"]}
            assert updated["previous_answers"] == [next(q for q in exported if q["id"] == body["id"])]
            assert next(q for q in exported if q["id"] == second_id)["skipped"] is True
            assert (await client.patch(path, json={**body, "revision": 2})).status_code == 412
            revised = {**body, "question": "New wording?", "selection": {"choice": "Off limits"}, "revision": 2}
            assert (await client.patch(path, json=revised)).json() == {"revision": 3}
            await db.users.update_one({"user_id": uid}, {"$set": {"tier": "unsubscribed"}})
            assert (await client.patch(path, json={**revised, "revision": 3})).status_code == 402
            params = {"revision": 3, "item": second_id}
            assert (await client.delete(path, params={**params, "item":"wrong.key"})).status_code == 422
            assert (await client.delete(path, params=params, headers={"X-Questionnaire-Owner": other})).status_code == 409
            assert (await client.delete(path, params=params)).json() == {"revision": 4}
            assert (await client.delete(path, params=params)).status_code == 409
            stored = await db.intimacy_answers.find_one({"_id":uid})
            for field in ("values","conditions","signatures","wording"):
                assert set(stored[field]) == {body["id"]}
            assert [item["id"] for item in (await client.get("/api/me/export")).json()["intimacy_answers"]] == [body["id"]]
            assert (await client.delete(path, params={"revision": 4})).json() == {"revision": 5}
            assert (await client.get("/api/me/export")).json()["intimacy_answers"] == []
            await db.users.update_one({"user_id": uid}, {"$set": {"tier": "paid"}})
            assert (await client.patch(path, json={**revised, "revision": 3})).status_code == 409
            assert (await client.patch(path, json={**revised, "revision": 5})).status_code == 200
            assert (await client.put("/api/me/about", json={"demographics_patch": {"age": 17}})).status_code == 200
            assert (await client.get(path)).status_code == 403
            assert (await client.patch(path, json={**revised, "revision": 5})).status_code == 403
            erased = await db.intimacy_answers.find_one({"_id": uid})
            assert set(erased) - set(answer_history.FIELDS) == {"_id", "user_id", "revision"}
            assert not erased.get(answer_history.PENDING) and not erased.get(answer_history.REDACTIONS)
            assert await db.answer_history.count_documents({"source": "intimacy_answers", "source_id": uid}) == 0
            assert (await client.delete("/api/me")).status_code == 200
            assert (await client.patch(path, json={**revised, "revision": 5})).status_code == 409
            rev = (await client.get(path)).json()["revision"]
            assert (await client.patch(path, json={**revised, "revision": rev})).status_code == 200
            await db.users.update_one({"user_id": uid}, {"$unset": {"stripe_subscription_id": ""}})
            monkeypatch.setattr(server.accounts, "close_billing", lambda *args: None)
            assert (await client.delete("/api/me/account")).status_code == 200
            assert await db.intimacy_answers.find_one({"_id": uid}) is None
    run_case(check)
