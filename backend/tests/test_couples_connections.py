"""Check customer writes against the staff reports using a disposable local database."""
import csv
import os
from datetime import datetime, timezone
from io import StringIO

import httpx
import pytest
from fastapi import Request

from backend import questionnaire_report
from backend.tests.test_free_plan_quota import run_case


@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires a disposable local MongoDB.")
def test_confirmed_pair_activities_appear_in_reports_and_honor_withdrawal(monkeypatch):
    from backend import server

    async def check(db):
        monkeypatch.setattr(server, "db", db)
        async def identity(request: Request):
            return await db.users.find_one({"user_id": request.headers["x-member"]}, {"_id": 0})
        monkeypatch.setitem(server.app.dependency_overrides, server.current_member, identity)
        await server.ensure_indexes()
        now = datetime.now(timezone.utc)
        ts = int(now.timestamp())
        base = {"role": "member", "env": server.ENV}
        await db.users.insert_many([
            {**base, "user_id": "host", "tier": "paid", "stripe_subscription_id": "sub_local",
             "subscription_period_start": ts - 300, "subscription_period_end": ts + 86400},
            {**base, "user_id": "guest"}, {**base, "user_id": "staff", "role": "admin"},
        ])
        await db.couples_settings.insert_one({"_id": "pilot:" + server.ENV, "mode": "paused", "revision": 5})
        # The 50th research participant enters through the customer UI endpoints below.
        background = [{**base, "user_id": f"research-{i}", "questionnaire_research_version": questionnaire_report.CONSENT}
                      for i in range(49)]
        await db.users.insert_many(background)
        await db.couples_drafts.insert_many([
            {"user_id": u["user_id"], "key": "support-1", "choice": "Listening" if i < 30 else "Quiet company"}
            for i, u in enumerate(background)
        ])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as http:
            async def call(uid, method, path, body=None, status=200):
                response = await http.request(method, "/api" + path, json=body, headers={"x-member": uid})
                assert response.status_code == status, (path, response.text)
                return response.json()
            async def analytics():
                return await call("staff", "GET", "/admin/couples/analytics?days=7")
            async def frequencies():
                return await call("staff", "GET", "/admin/research/distribution?key=c:prompt:support-1")

            for uid in ("host", "guest"):
                await call(uid, "POST", "/couples/adult", {"confirmed": True})
            await call("guest", "POST", "/couples/invite", status=402)
            invite = await call("host", "POST", "/couples/invite")
            await call("guest", "POST", "/couples/invite/accept", {"token": invite["token"]})
            pair_id = invite["workspace"]["pair_id"]
            await call("host", "POST", "/couples/confirm", {"pair_id": pair_id})
            initial = await analytics()
            assert initial["membership"]["sponsored_pairs"] == 1
            assert initial["confirmed"] == initial["connections"]["invitations_accepted"] == 1
            await call("guest", "GET", "/admin/couples/analytics", status=403)
            for _ in range(2):
                await call("guest", "POST", "/me/couples-activity", {"action": "opened"})
                await call("guest", "POST", "/me/couples-activity", {"action": "sample_completed"})

            private = "PRIVATE TEXT ONLY THE AUTHOR CHOSE TO SAVE"
            for uid, choice in (("host", "Listening"), ("guest", "Quiet company")):
                await call(uid, "PUT", "/couples/drafts/support-1", {"version": 0, "choice": choice, "lines": [choice, private]})
            assert not (await frequencies())["available"]
            consent = {"version": questionnaire_report.CONSENT, "revision": 0, "enabled": True,
                       "adult_confirmed": True, "expected_member": "guest"}
            await call("guest", "PATCH", "/me/questionnaire-research", consent)
            measured = await frequencies()
            assert measured["available"] and measured["sample_size"] == 50 and private not in str(measured)
            await call("guest", "PATCH", "/me/questionnaire-research", {**consent, "revision": 1, "enabled": False})
            assert not (await frequencies())["available"]

            async def act(uid, version, action, **fields):
                return await call(uid, "POST", "/couples/threads/support-1",
                                  {"pair_id": pair_id, "version": version, "action": action, **fields})
            await act("host", 0, "share", kind="prompt", lines=["Listening"])
            await act("guest", 1, "share", kind="prompt", lines=["Quiet company"])
            proposal = await act("host", 2, "propose", text="Ask about our plans.", review_on=now.date().isoformat())
            agreement = proposal["threads"][0]["agreements"][0]["id"]
            await act("guest", 3, "accept", agreement_id=agreement)
            await act("host", 4, "checkin", agreement_id=agreement, result="helped", share=True)
            await act("guest", 5, "checkin", agreement_id=agreement, result="mixed", share=True)
            await act("guest", 6, "pin", agreement_id=agreement, text="Ask what would help.")
            await act("host", 7, "accept_pin", agreement_id=agreement)
            full = await analytics()
            assert full["milestones"] == {"first_mutual": 1, "first_agreement": 1, "both_checked_in": 1}
            assert full["shared_features"]["counts"]["learning"] == full["shared_features"]["counts"]["prompt"] == 1
            assert not full["outcomes"]["available"] and private not in str(full)
            csv_response = await http.get("/api/admin/reports/workspace.csv?days=7", headers={"x-member": "staff"})
            assert csv_response.is_success and private not in csv_response.text
            rows = list(csv.DictReader(StringIO(csv_response.text)))
            exported = {r["metric"]: int(r["value"]) for r in rows if r["section"] == "Couples membership"}
            assert exported == full["membership"]
            for event in ("couples_opened", "couples_sample_completed"):
                assert any(r["section"] == "Activity" and r["scope"] == "period"
                           and r["metric"] == event and r["value"] == "1" for r in rows)
            await act("host", 8, "withdraw_checkin", agreement_id=agreement)
            assert (await analytics())["shared_features"]["counts"]["learning"] == 0
            await call("guest", "POST", "/couples/leave", {"pair_id": pair_id})
            closed = await analytics()
            assert closed["connections"]["pairs_ended"] == 1
            assert closed["membership"]["sponsored_pairs"] == closed["shared_features"]["active_pairs"] == 0

    run_case(check)
