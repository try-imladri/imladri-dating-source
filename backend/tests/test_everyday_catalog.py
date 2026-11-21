import asyncio
from copy import deepcopy

import httpx

from backend import quiz_answers
from backend.quiz import default_quiz
from backend.tests.test_free_plan_quota import run_case


def test_quiz_draft_score_preview_release_restore_and_historical_answers(monkeypatch):
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        staff = {"user_id":"reviewer", "role":"admin"}
        monkeypatch.setitem(server.app.dependency_overrides, server.admin_dep, lambda: staff)
        await db.settings.insert_one(server.default_settings())
        q = default_quiz()
        draft = {key: deepcopy(q[key]) for key in quiz_answers.SETS}
        original = draft["short"][0]
        qid = original["id"]
        option = original["options"][0]
        old_score = option.get("axis_score")
        option["axis_score"] = 1 if old_score != 1 else 4
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://fixture") as api:
            url = "/api/admin/quiz/everyday/draft"
            raw = {"quiz_revision":None, "revision":None, "quiz":draft}
            assert (await api.put(url, json={**raw, "revision": []})).status_code == 422
            assert (await api.put("/api/admin/settings", json={"quiz": draft, "quiz_revision": None, "everyday_draft_revision": []})).status_code == 422
            one, two = await asyncio.gather(api.put(url,json=raw), api.put(url,json=raw))
            assert sorted([one.status_code,two.status_code]) == [200,409]
            saved = (await api.get(url)).json()
            assert saved["draft"]["sets"] == draft and saved["revision"]
            assert "everyday_draft" not in (await server.get_settings())
            assert (await server.get_settings())["quiz"]["short"][0]["options"][0].get("axis_score") == old_score
            preview = (await api.post("/api/admin/quiz/everyday/preview",json={"quiz":draft,"choices":{qid:0}})).json()
            axis = next(a for a in preview["axes"] if a["axis"] == original["axis"])
            assert preview["answered"] == axis["answered"] == 1 and axis["score"] == (0 if option["axis_score"] == 1 else 100)
            assert (await api.put("/api/admin/settings",json={"quiz":draft,"quiz_revision":None,"everyday_draft_revision":None})).status_code == 409
            publish = await api.put("/api/admin/settings",json={"quiz":draft,"quiz_revision":None,"everyday_draft_revision":saved["revision"]})
            assert publish.status_code == 200, publish.text
            live = publish.json()
            assert live["quiz"]["short"][0]["id"] != qid and live["quiz_can_restore"]
            member = {"answers":{qid:{"choice":0,"note":"an old answer"}}}
            viewed = quiz_answers.display_about(member,live["quiz"])
            assert not viewed["answers"] and viewed["previous_answers"][qid]["label"] == option["label"]
            assert (await api.get(url)).json()["draft"] is None
            assert (await api.put("/api/admin/settings",json={"restore_previous_quiz":True,"quiz_revision":None})).status_code == 409
            restored = (await api.put("/api/admin/settings",json={"restore_previous_quiz":True,"quiz_revision":live["quiz_revision"],
                        "everyday_draft_revision":live["everyday_draft_revision"]})).json()
            assert restored["quiz_revision"] != live["quiz_revision"]
            assert restored["quiz"]["short"][0]["id"] == qid
            assert restored["quiz"]["short"][0]["options"][0].get("axis_score") == old_score
            assert quiz_answers.display_about(member,restored["quiz"])["answers"][qid]["label"] == option["label"]
            assert (await api.get(url)).json()["published_revision"] == restored["quiz_revision"]
    run_case(scenario)
