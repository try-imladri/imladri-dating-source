import time

import httpx

from backend.tests.test_free_plan_quota import run_case


def test_account_demographics_consent_and_age_changes(monkeypatch):
    from backend import server

    async def check(db):
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_coverage")
        uid = "questionnaire_demographics_fixture"
        monkeypatch.setitem(server.app.dependency_overrides, server.require_user, lambda: {"user_id": uid})
        ts = int(time.time())
        await db.users.insert_one({"user_id": uid, "role": "member", "env": server.ENV, "tier": "paid",
            "stripe_subscription_id": "sub_fixture", "subscription_period_start": ts-300, "subscription_period_end": ts+8000,
            "about": {"demographics": {"sex": "Female"}}})
        endpoint = "/api/me/questionnaire-research"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as client:
            async def update(fields):
                return await client.put("/api/me/about", json={"demographics_patch": fields, "expected_member": uid})
            assert (await client.get("/api/me/intimacy-answers", headers={"X-Questionnaire-Owner":"different"})).status_code == 409
            pref = (await client.get("/api/me")).json()["questionnaire_research"]
            assert pref["enabled"] is False
            details = {"age": 28, "height_cm": 172, "gender": "Non-binary", "ethnicity": "Mixed"}
            assert (await update(details)).json()["about"]["demographics"] == {**details, "sex": "Female"}
            assert (await update({"height_cm": 23})).status_code == 422
            assert (await update({"gender":"Invalid selection"})).status_code == 422
            assert (await client.put("/api/me/about", json={"expected_member": "different", "describe":"wrong account"})).status_code == 409
            body = {"version": pref["version"], "revision": 0, "enabled": True, "adult_confirmed": False}
            assert (await client.patch(endpoint, json=body)).status_code == 403
            body["adult_confirmed"] = True
            assert (await client.patch(endpoint, json=body)).json()["enabled"] is True
            assert (await client.patch(endpoint, json=body)).status_code == 409
            assert (await update({"height_cm": None})).json()["about"]["demographics"].get("height_cm") is None
            assert (await update({"age": 17})).status_code == 200
            pref = (await client.get("/api/me")).json()["questionnaire_research"]
            assert pref["enabled"] is False and pref["revision"] == 2
            assert (await client.patch(endpoint, json={**body, "revision": 2})).status_code == 403
            assert (await update({"age": 29})).status_code == 200
            assert (await client.patch(endpoint, json={**body, "revision": 2})).json()["enabled"] is True
            await db.users.update_one({"user_id": uid}, {"$set": {"tier":"unsubscribed"}, "$unset":{"stripe_subscription_id": ""}})
            assert (await client.patch(endpoint, json={**body,"revision": 3,"enabled": False})).json()["enabled"] is False

    run_case(check)
