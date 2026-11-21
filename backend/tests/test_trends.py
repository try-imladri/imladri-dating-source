import asyncio
import copy
import csv
from io import StringIO
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import Header
from motor.motor_asyncio import AsyncIOMotorClient

from backend import trends
from backend.quiz import default_quiz
from backend.trends_export import spreadsheet_cell


def test_csv_neutralizes_formula_like_text_and_preserves_numeric_counts():
    for text in ("=2+2", "  +2", "\t-2", "\r\n@SUM(A1)", "\ufeff  =2", " \ufeff=3", "\tplain"):
        assert spreadsheet_cell(text) == "'" + text
    assert spreadsheet_cell("A question, with commas?") == "A question, with commas?"
    assert spreadsheet_cell(-2) == -2


def test_small_groups_suppress_the_whole_answer_distribution():
    assert trends.disclosure(["A", "B"], [15, 1]) == {"available": False, "respondents": None, "options": []}
    assert trends.disclosure(["A", "B"], [0, 0])["available"] is False
    visible = trends.disclosure(["A", "B", "C"], [10, 10, 0])
    assert visible["respondents"] == 20
    assert [option["count"] for option in visible["options"]] == [10, 10, 0]


def test_saved_member_workflows_reach_trends_and_forget_deleted_content(monkeypatch):
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Set IMLADRI_TEST_MONGO_URL to a loopback MongoDB.")
    assert urlsplit(uri).hostname in {"localhost", "127.0.0.1", "::1"}
    name = "imladri_individual_trends_" + uuid4().hex
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", name)
    from backend import server

    async def run():
        mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        db = mongo[name]
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setattr(server, "regenerate_report", AsyncMock())
        def identity(x_member: str = Header()):
            return {"user_id": x_member}
        monkeypatch.setitem(server.app.dependency_overrides, server.require_user, identity)
        now = datetime.now(timezone.utc)
        stamp = now.isoformat()
        old = (now - timedelta(days=10)).isoformat()
        def owner(uid, **fields):
            return {"user_id": uid, "role": "member", "email": uid + "@invalid.test", "env": server.ENV,
                    "created_at": old, **fields}
        try:
            await server.ensure_indexes()
            await db.users.insert_many([owner("s", role="admin"), owner("m", tier="paid", stripe_subscription_id="sub_test",
                subscription_period_start=int(now.timestamp()) - 3600, subscription_period_end=int(now.timestamp()) + 3600),
                owner("outside", env="other"), owner("closing", closing=True)])
            await db.people.insert_many([{"id": pid, "user_id": "m", "nickname": pid, "rel_type": "friend",
                "created_at": old, "intake": {}} for pid in ("one", "two")])
            await db.people.update_one({"id": "two"}, {"$set": {"report": {"updated_at": stamp, "summary": "PRIVATE_REPORT"}}})
            await db.exchanges.insert_one({"id": "e", "user_id": "m", "person_id": "one", "created_at": old,
                                           "response": {"seen": [{"text": "PRIVATE_READ"}]}})
            await db.preps.insert_one({"id": "p", "user_id": "m", "person_id": "one", "created_at": old,
                                       "situation": "PRIVATE_PLAN"})
            await db.practice.insert_many([{"id": str(i), "user_id": "m", "created_at": date, "ended": finished}
                                           for i, (date, finished) in enumerate([(stamp, True), (stamp, False), (old, True)])])
            for uid in ("m", "s", "outside", "closing", "missing"):
                await db.patterns.insert_one({"id": uid, "user_id": uid, "created_at": stamp, "sources": ["e"], "text": "PRIVATE_PATTERN"})
                await db.events.insert_one({"user_id": uid, "type": "quiz_extended_complete", "ts": stamp})
            await db.patterns.insert_one({"id": "orphan", "user_id": "m", "created_at": stamp, "sources": [], "text": "PRIVATE_HIDDEN"})
            await db.events.insert_many([{"user_id": "m", "type": "quiz_short", "ts": t} for t in (old, stamp)] +
                                        [{"user_id": "m", "type": "quiz_further_complete", "ts": stamp} for _ in range(2)])
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
                async def call(method, path, body=None, actor="m"):
                    result = await api.request(method, "/api" + path, json=body, headers={"x-member": actor})
                    result.raise_for_status()
                    return result
                group = (await call("POST", "/chat-groups", {"name": "PRIVATE_GROUP", "person_ids": ["one", "two"]})).json()
                before = (await call("GET", "/admin/trends?days=7", actor="s")).json()
                assert before["totals"]["person_reports"] == 1
                await call("PATCH", "/people/two/intake", {"fields": {"her_goal": "PRIVATE_GOAL of sufficient length."}})
                await call("POST", "/exchanges/e/choice", {"label": "PRIVATE_CHOICE"})
                await call("POST", "/exchanges/e/correct", {"kind": "seen", "index": 0, "note": "PRIVATE_NOTE"})
                await call("POST", "/exchanges/e/memories", {"memories": [{"kind": "event", "text": "PRIVATE_MEMORY"}]})
                await call("POST", "/preps/p/outcome", {"text": "PRIVATE_FOLLOWUP"})
                await call("PATCH", "/preps/p", {"pinned": True})
                await db.people.update_one({"id": "two"}, {"$set": {"archived": True}})
                await db.patterns.update_one({"id": "m"}, {"$set": {"member_confirmed": True, "sources": ["practice:0"]}})
                await db.patterns.update_one({"id": "closing"}, {"$set": {"member_confirmed": True, "sources": ["practice:0"]}})
                response = await call("GET", "/admin/trends?days=7", actor="s")
                assert "PRIVATE_" not in response.text and "@invalid.test" not in response.text
                totals = response.json()["totals"]
                features = {item["key"]: item["count"] for item in response.json()["saved_features"]}
                assert features == {"people_archived": 1, "plans_pinned": 1, "patterns_confirmed": 1, "practice_patterns": 1}
                csv_result = await call("GET", "/admin/reports/workspace.csv?days=30", actor="s")
                assert {r["metric"]: int(r["value"]) for r in csv.DictReader(StringIO(csv_result.text))
                        if r["section"] == "Individual saved features"} == features
                # Staff counts current stored reports. A corrected source invalidates its previous report.
                assert totals["person_reports"] == 0
                for key in ("profile_updates", "reply_choices", "corrections", "approved_memories",
                            "patterns_saved", "practice_finished", "saved_groups", "plan_followups", "deeper_completed", "further_completed"):
                    assert totals[key] == 1, key
                assert totals["basics_completed"] == 0
                longer = (await call("GET", "/admin/trends?days=30", actor="s")).json()
                assert longer["totals"]["basics_completed"] == 1
                assert next(day for day in longer["daily"] if day["day"] == old[:10])["basics_completed"] == 1
                await call("DELETE", "/chat-groups/" + group["id"])
                await call("DELETE", "/exchanges/e/choice")
                await call("DELETE", "/exchanges/e/correct?kind=seen&index=0")
                await call("DELETE", "/patterns/m")
                await call("PATCH", "/preps/p", {"pinned": False})
                for memory in await db.memories.find({"user_id": "m"}, {"id": 1}).to_list(None):
                    for _ in range(2):
                        await call("PATCH", "/memories/" + memory["id"], {"text": "PRIVATE_EDIT"})
                    await call("DELETE", "/memories/" + memory["id"])
                await call("POST", "/practice/0/restart")
                # Withdrawing a coaching memory preserves the private plan outcome until its owner deletes it.
                plan = await db.preps.find_one({"id":"p"})
                assert plan["outcome"] == "PRIVATE_FOLLOWUP" and plan["outcome_use_for_coaching"] is False
                assert (await call("GET", "/admin/trends?days=7", actor="s")).json()["totals"]["plan_followups"] == 1
                await call("DELETE", "/preps/p/outcome?expected_revision=" + str(plan["outcome_revision"]))
                after = (await call("GET", "/admin/trends?days=7", actor="s")).json()["totals"]
                assert after["memory_edits"] == 4  # two real edits and two removals, despite replayed edit requests
                for key in ("saved_groups", "reply_choices", "corrections", "patterns_saved", "approved_memories",
                            "plan_followups", "practice_finished"):
                    assert after[key] == 0, key
        finally:
            await mongo.drop_database(name)
            mongo.close()
    asyncio.run(run())


def test_member_activity_and_opt_in_trends_round_trip(monkeypatch):
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Set IMLADRI_TEST_MONGO_URL to a loopback MongoDB.")
    assert urlsplit(uri).hostname in {"localhost", "127.0.0.1", "::1"}
    name = "imladri_trends_test_" + uuid4().hex
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", name)
    from backend import server

    async def run():
        mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        db = mongo[name]
        monkeypatch.setattr(server, "db", db)
        today = datetime.now(timezone.utc).date()
        stamp = lambda days: (today - timedelta(days=days)).isoformat() + "T00:01:00+00:00"
        consent = {"everyday_trends_opt_in": True}

        def account(uid, **extra):
            return {"user_id": uid, "role": "member", "email": uid + "@member.invalid", "env": server.ENV,
                    "created_at": stamp(90), **extra}

        def identity(x_member: str = Header()):
            return {"user_id": x_member}

        monkeypatch.setitem(server.app.dependency_overrides, server.require_user, identity)
        try:
            await server.ensure_indexes()
            quiz = default_quiz()
            await db.settings.insert_one({"key": "global", "quiz": quiz})
            users = [account(f"m{i}", **consent,
                             about={"answers": {"s1": {"choice": i % 2, "note": "PRIVATE_NOTE"}}}) for i in range(20)]
            await db.users.insert_many(users + [
                account("admin", role="admin"), account("outside", env="other"),
                account("tester", email="clerk_test@example.com"), account("closing", closing=True),
                account("nonconsenting", created_at=stamp(6), about={"answers": {"s1": {"choice": 2}}},
                        origin="PRIVATE_ORIGIN?email=PRIVATE_EMAIL")])
            for i, m in enumerate(users):
                await db.exchanges.insert_one({"id": f"e{i}", "user_id": m["user_id"], "created_at": stamp(9),
                                               "outcome_at": stamp(0), "outcome": "PRIVATE_REPORT",
                                               "outcome_note": "PRIVATE_NOTE",
                                               "outcome_result": "better" if i % 2 == 0 else "same"})
            # An earlier observation, a deleted one, and a free-text-only observation.
            await db.exchanges.insert_many([
                {"id": "older", "user_id": "m0", "created_at": stamp(6), "outcome_at": stamp(1), "outcome": "PRIVATE_REPORT", "outcome_result": "worse"},
                {"id": "deleted", "user_id": "m1", "created_at": stamp(7), "outcome_at": stamp(0), "outcome": None, "outcome_result": "worse"},
                {"id": "note", "user_id": "m2", "created_at": stamp(15), "outcome_at": stamp(0), "outcome": "PRIVATE_NOTE"}])
            for uid in ["admin", "outside", "tester", "closing", "missing"]:
                await db.exchanges.insert_one({"id": f"excluded-{uid}", "user_id": uid, "created_at": stamp(0), "outcome_at": stamp(0),
                                               "outcome": "PRIVATE_REPORT", "outcome_result": "worse"})
            await db.practice.insert_one({"user_id": "nonconsenting", "created_at": stamp(1), "topic": "PRIVATE_TOPIC"})
            for collection in ("people", "preps", "group_chats"):
                await db[collection].insert_one({"id": "created", "user_id": "m0", "created_at": stamp(1)})
            await db.events.insert_many([{"type": kind, "user_id": "m0", "ts": stamp(1)}
                                         for kind in ("session", "session", "memory_edit", "upload_started", "upload_completed")])
            await db.payment_transactions.insert_many([
                {"user_id": "m0", "kind": "purchase", "payment_status": "paid", "created_at": stamp(50), "fulfilled_at": stamp(1)},
                {"user_id": "m1", "kind": "purchase", "payment_status": "paid", "created_at": stamp(2)},
                {"user_id": "m2", "kind": "renewal", "payment_status": "paid", "created_at": stamp(1)},
                {"user_id": "m3", "kind": "purchase", "payment_status": "open", "created_at": stamp(1)},
                {"user_id": "m0", "kind": "upgrade", "payment_status": "paid", "created_at": stamp(3), "env": server.ENV},
                {"user_id": "m0", "kind": "upgrade", "payment_status": "paid", "created_at": stamp(3), "env": "other"},
                {"user_id": "m0", "kind": "upgrade", "payment_status": "pending", "created_at": stamp(3)},
                {"user_id": "m1", "kind": "topup", "payment_status": "paid", "created_at": stamp(8), "fulfilled_at": stamp(1),
                 "updated_at": stamp(0), "topup_credits": 50},
                {"user_id": "m2", "kind": "topup", "payment_status": "paid", "created_at": stamp(1), "fulfilled_at": stamp(0),
                 "topup_credits": 100},
                {"user_id": "tester", "kind": "topup", "payment_status": "paid", "created_at": stamp(1), "fulfilled_at": stamp(0),
                 "topup_credits": 0},
                {"user_id": "m3", "kind": "topup", "payment_status": "pending", "created_at": stamp(2)}])

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
                admin = {"x-member": "admin"}
                member = {"x-member": "nonconsenting"}
                assert (await api.get("/api/admin/trends", headers=member)).status_code == 403
                assert (await api.get("/api/admin/reports/workspace.csv", headers=member)).status_code == 403
                assert (await api.get("/api/admin/reports/workspace.csv?days=365", headers=admin)).status_code == 422
                assert (await api.get("/api/admin/trends?days=365", headers=admin)).status_code == 422
                assert (await api.get("/api/me", headers=member)).json()["everyday_trends_opt_in"] is False
                assert (await api.post("/api/me/couples-activity", headers=member, json={"action": "opened", "answer": "anything"})).status_code == 422
                assert (await api.post("/api/me/couples-activity", headers=member, json={"action": "unknown"})).status_code == 422
                assert (await api.patch("/api/me/trends-consent", headers=member, json={"enabled": "yes"})).status_code == 422
                opened = {"action": "opened"}
                requests = [api.post("/api/me/couples-activity", headers=member, json=opened) for _ in range(8)]
                assert all(r.status_code == 200 for r in await asyncio.gather(*requests))
                completed = {"action": "sample_completed"}
                assert (await api.post("/api/me/couples-activity", headers=member, json=completed)).status_code == 403
                await db.users.update_one({"user_id": "nonconsenting"}, {"$set": {"couples_adult_at": stamp(0)}})
                assert (await api.post("/api/me/couples-activity", headers=member, json=completed)).status_code == 402
                await db.users.update_one({"user_id": "nonconsenting"}, {"$set": {"tier": "paid", "stripe_subscription_id": "sub_fixture",
                    "subscription_period_start": int(datetime.now(timezone.utc).timestamp()) - 86400,
                    "subscription_period_end": int(datetime.now(timezone.utc).timestamp()) + 86400}})
                assert (await api.post("/api/me/couples-activity", headers=member, json=completed)).is_success
                assert await db.events.count_documents({"type": "couples_opened"}) == 1
                async def report(days=7):
                    response = await api.get(f"/api/admin/trends?days={days}", headers=admin)
                    assert response.is_success and response.headers["cache-control"] == "no-store"
                    assert all(secret not in response.text for secret in ["PRIVATE_", "m0@", "nonconsenting", "about.answers"])
                    return response.json()
                short = await report()
                assert len(short["daily"]) == 7 and short["daily"][0]["day"] == stamp(6)[:10]
                assert short["totals"] == {"your_type_attempts": 0, "signups": 1, "reads": 1, "outcomes": 22, "practice": 1,
                                           "people_added": 1, "group_chats": 1, "game_plans": 1, "checkouts_started": 2, "paid_purchases": 2,
                                           "paid_renewals": 1, "paid_upgrades": 1, "refill_checkouts": 2, "paid_refills": 2,
                                           "adjusted_refills": 1, "refill_refund_events": 0, "refill_lost_disputes": 0, "sessions": 2, "memory_edits": 1, "uploads_started": 1,
                                           "uploads_completed": 1, "couples_opened": 1, "couples_sample_completed": 1,
                                           "profile_updates": 0, "person_reports": 0, "reply_choices": 0, "corrections": 0,
                                           "approved_memories": 0, "patterns_saved": 0, "practice_finished": 0,
                                           "saved_groups": 0, "plan_followups": 0, "basics_completed": 0,
                                           "deeper_completed": 0, "further_completed": 0}
                assert {item["key"] for item in short["measures"]} == set(short["totals"])
                assert short["daily"][-2]["outcomes"] == 1
                assert short["daily"][-2]["paid_refills"] == short["daily"][-2]["adjusted_refills"] == 1
                assert short["daily"][-4]["paid_upgrades"] == 1 and short["daily"][-1]["adjusted_refills"] == 0
                # Operational attempt counts include immediate retakes and do
                # not require optional research permission. Deleted attempts
                # and excluded account/environment rows never contribute; no
                # choice or model payload leaves the aggregate response.
                await db.your_type_sessions.insert_many([
                    {"_id": "visual-first", "user_id": "m0", "slot": 0, "created_at": stamp(1),
                     "state": {"private_choices": "PRIVATE_VISUAL_MODEL"}},
                    {"_id": "visual-retake", "user_id": "m0", "slot": 1, "retake_of": "visual-first", "created_at": stamp(1)},
                    {"_id": "visual-nonconsenting", "user_id": "nonconsenting", "slot": 0, "created_at": stamp(1)},
                    {"_id": "visual-deleted", "user_id": "m1", "slot": 0, "created_at": stamp(1), "deleted": True},
                    *[{"_id": "visual-excluded-" + uid, "user_id": uid, "slot": 0, "created_at": stamp(1)}
                      for uid in ("admin", "outside", "tester", "closing", "missing")],
                ])
                visual = await report()
                assert visual["totals"]["your_type_attempts"] == visual["daily"][-2]["your_type_attempts"] == 3
                await db.your_type_sessions.update_one({"_id": "visual-retake"}, {"$set": {"deleted": True}})
                assert (await report())["totals"]["your_type_attempts"] == 2
                await db.payment_transactions.update_one({"kind": "topup", "user_id": "m1"}, {"$set": {"topup_credits": 0}})
                assert (await report())["totals"]["adjusted_refills"] == 1
                assert short["outcomes"]["respondents"] == 20
                assert [v["count"] for v in short["outcomes"]["options"]] == [10, 10, 0, 0]
                first = short["everyday"]["questions"][0]
                assert first["current"] and first["respondents"] == 20 and [v["count"] for v in first["options"]] == [10, 10, 0, 0]
                assert (await report(30))["totals"]["reads"] == 23
                # An option reorder keeps the original meaning of legacy indexed answers.
                revised = copy.deepcopy(quiz)
                revised["short"][0]["options"].reverse()
                revised["legacy_wording"] = {"s1": {"q": first["prompt"], "labels": [v["label"] for v in first["options"]]}}
                await db.settings.update_one({"key": "global"}, {"$set": {"quiz": revised}})
                assert (await report())["everyday"]["questions"][0] == first
                # Removing consent immediately suppresses the whole distribution when a group drops to nine.
                setting = await api.patch("/api/me/trends-consent", headers={"x-member": "m0"}, json={"enabled": False})
                assert setting.json() == {"enabled": False}
                private = await report()
                assert private["everyday"]["questions"][0]["options"] == [] and private["outcomes"]["respondents"] is None
                download = await api.get("/api/admin/reports/workspace.csv?days=7", headers=admin)
                assert download.status_code == 200 and download.headers["cache-control"] == "no-store"
                assert "attachment;" in download.headers["content-disposition"] and "text/csv" in download.headers["content-type"]
                assert all(text not in download.text for text in ("PRIVATE_", "m0@", "clerk_test", "nonconsenting"))
                rows = list(csv.DictReader(StringIO(download.text)))
                assert {"Entry pages", "People profiles", "Workspace overview"} <= {row["section"] for row in rows}
                entries = [row for row in rows if row["section"] == "Entry pages" and row["metric"] == "signups"]
                assert {row["category"]: int(row["value"]) for row in entries} == {"direct": 20, "other": 1}
                assert all(row["denominator"] == row["value"] for row in entries)
                overview = (await api.get("/api/admin/metrics", headers=admin)).json()
                def pair(metric):
                    row = next(row for row in rows if row["metric"] == metric)
                    return int(row["value"]), int(row["denominator"])
                assert pair("saved profile details") == (overview["profile_progress"]["with_details"], overview["users"])
                assert pair("first-week profile updates observed so far") == (overview["deepening_rate"]["deepened_within_7d"], overview["deepening_rate"]["first_session_users"])
                assert pair("profile deepened within 7 days") == (overview["deepening_window"]["deepened"], overview["deepening_window"]["complete"])
                rendered = {row["metric"]: int(row["value"]) for row in rows
                            if row["section"] == "Activity" and row["scope"] == "period"}
                assert rendered == private["totals"]
                assert all(row["definition"] for row in rows if row["section"] == "Activity")
                assert sum(int(row["value"]) for row in rows if row["scope"] == "UTC day" and row["metric"] == "reads") == rendered["reads"]
                assert all(not row["value"] and not row["denominator"] for row in rows if row["status"] == "suppressed")
                assert {"Individual quiz", "Account milestones", "Individual onboarding", "Couples", "Subscriptions"} <= {row["section"] for row in rows}
                # Republishing a different question under an approved ID cannot expand consent.
                await api.patch("/api/me/trends-consent", headers={"x-member": "m0"}, json={"enabled": True})
                revised["short"][0]["q"] = "A different question"
                await db.settings.update_one({"key": "global"}, {"$set": {"quiz": revised}})
                paused = (await report())["everyday"]["questions"][0]
                assert not paused["current"] and not paused["available"]
        finally:
            await mongo.drop_database(name)
            mongo.close()
    asyncio.run(run())
