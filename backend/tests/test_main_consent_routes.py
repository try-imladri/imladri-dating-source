"""Main consent connects account, stored choices and content-free Admin counts."""
import httpx

from backend.tests.test_main_consent import decision, member
from backend.tests.test_research_notices import run_case


def test_main_account_route_and_admin_counts_use_the_same_saved_permissions(monkeypatch):
    from backend import server

    async def case(db):
        own = member()
        await db.users.insert_one(own)
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setattr(server, "ENV", "preview")
        monkeypatch.setattr(server, "IN_ENV", {"env": "preview"})
        actor = own

        async def current():
            return await db.users.find_one({"user_id": actor["user_id"]}) if actor["role"] == "member" else actor

        server.app.dependency_overrides[server.current_member] = current
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as client:
                initial = await client.get("/api/me/main-consent")
                assert initial.status_code == 200 and initial.headers["cache-control"] == "private, no-store"
                assert not initial.json()["welcome_complete"]
                assert (await client.get("/api/admin/main-consent")).status_code == 403
                body = decision(initial.json(), action="agree_all")
                accepted = await client.patch("/api/me/main-consent", json=body.model_dump())
                assert accepted.status_code == 200 and accepted.json()["welcome_complete"]
                account = await client.get("/api/me")
                assert account.status_code == 200
                assert account.json()["main_consent"] == accepted.json()
                assert account.json()["questionnaire_research"]["enabled"]
                assert account.json()["answer_history_permission"]["enabled"]
                actor = {"user_id": "staff", "role": "admin", "env": "preview"}
                report = await client.get("/api/admin/main-consent")
                assert report.status_code == 200 and report.headers["cache-control"] == "private, no-store"
                assert report.json()["members"] == report.json()["reviewed"] == 1
                assert report.json()["scopes"]["staff_history"]["enabled"] == 1
                assert '"owner"' not in report.text and '"about"' not in report.text
                assert (await client.get("/api/me/main-consent")).status_code == 403
                assert (await client.patch("/api/me/main-consent", json=body.model_dump())).status_code == 403
        finally:
            server.app.dependency_overrides.clear()

    run_case(case)


def test_wrong_owner_and_malformed_choices_never_partially_enable_permissions(monkeypatch):
    from backend import server

    async def case(db):
        own = member()
        await db.users.insert_one(own)
        monkeypatch.setattr(server, "db", db)

        async def current():
            return await db.users.find_one({"user_id": "owner"})

        server.app.dependency_overrides[server.current_member] = current
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as client:
                state = (await client.get("/api/me/main-consent")).json()
                body = decision(state, action="agree_all").model_dump()
                body["expected_member"] = "someone-else"
                assert (await client.patch("/api/me/main-consent", json=body)).status_code == 403
                body["expected_member"] = "owner"
                body["choices"]["tone"]["enabled"] = "yes"
                assert (await client.patch("/api/me/main-consent", json=body)).status_code == 422
                after = (await client.get("/api/me/main-consent")).json()
                assert after == state and not any(row["permission"]["enabled"] for row in after["scopes"].values())
        finally:
            server.app.dependency_overrides.clear()

    run_case(case)
