import asyncio
import os
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Request
from motor.motor_asyncio import AsyncIOMotorClient


def test_live_activity_sorts_offsets_and_native_dates_before_the_60_event_limit(monkeypatch):
    from backend import trends
    from backend.tests.test_free_plan_quota import run_case

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 2, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(trends, "datetime", Clock)
    async def check(db):
        member = {"user_id": "member", "env": "preview", "email": "fixture@example.invalid",
                  "created_at": datetime(2026, 1, 2, 6)}
        await db.users.insert_one(member)
        await db.exchanges.insert_many([{"user_id": "member", "created_at": "2026-01-02T00:45:00+13:00"}
                                        for _ in range(65)])
        await db.exchanges.insert_many([{"user_id": "member", "created_at": stamp} for stamp in (
            "2026-01-01T23:45:00-11:00", datetime(2026, 1, 2, 11), "2026-01-01T12:00:00Z",
            "2026-01-01T23:59:00-13:00", ["2026-01-02"], 123, "2026-01-02-invalid")])
        await db.events.insert_many([{"user_id": "member", "type": "couples_opened", "ts": stamp} for stamp in (
            "2026-01-01T04:00:00-08:00", datetime(2026, 1, 1, 12, 5),
            "2026-01-01T22:00:00-08:00", ["2026-01-02"])])
        view = await trends.live_activity(db, {"env": "preview"}, "chat", True)
        assert view["last24"] == {"signup": 1, "chat": 3, "group": 0, "practice": 0, "your_type": 0, "couples": 2}
        assert len(view["items"]) == 60
        assert [r["at"] for r in view["items"][:3]] == [
            "2026-01-02T11:00:00+00:00", "2026-01-02T10:45:00+00:00", "2026-01-01T12:00:00+00:00"]
        assert view["items"][3]["at"] == "2026-01-01T11:45:00+00:00"
        signup = await trends.live_activity(db, {"env": "preview"}, "signup")
        assert signup["items"][0]["at"] == "2026-01-02T06:00:00+00:00"
        hours = (await trends.live_activity(db, {"env": "preview"}, "couples"))["items"]
        assert [(row["at"], row["visits"]) for row in hours] == [
            ("2026-01-02T06:00:00+00:00", 1), ("2026-01-01T12:00:00+00:00", 1)]
    run_case(check)


def test_your_type_activity_counts_attempts_without_exposing_preferences_or_staff(monkeypatch):
    from backend import trends
    from backend.tests.test_free_plan_quota import run_case
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(trends, "datetime", Clock)
    async def check(db):
        await db.users.insert_many([{"user_id": "member", "env": "preview", "role": "member", "email": "member@fixture.invalid"},
            {"user_id": "staff", "env": "preview", "role": "admin", "email": "staff@fixture.invalid"}])
        await db.your_type_sessions.insert_many([{"user_id": uid, "created_at": "2026-01-02T11:00:00Z",
            "audience": "PRIVATE_PREFERENCE", "state": {"private_model": "PRIVATE_WEIGHTS"}} for uid in ("member", "staff")])
        result = await trends.live_activity(db, {"env": "preview", "role": "member"}, "your_type")
        assert result["last24"]["your_type"] == 1
        assert len(result["items"]) == 1 and result["items"][0]["type"] == "your_type"
        assert "PRIVATE" not in str(result) and "staff@" not in str(result)
    run_case(check)


@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Needs a disposable MongoDB.")
def test_live_couples_feed_groups_eligible_daily_actions_by_hour_without_identity(monkeypatch):
    from backend import trends
    from backend.tests.test_free_plan_quota import run_case

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 8, 3, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(trends, "datetime", Clock)

    async def check(db):
        eligible = {"env": "live", "role": "member", "closing": {"$ne": True}}
        for uid, fields in (("a", {}), ("b", {}), ("staff", {"role": "admin"}),
                            ("closing", {"closing": True}), ("other", {"env": "preview"})):
            await db.users.insert_one({"user_id": uid, "env": "live", "role": "member", "email": "private@" + uid, **fields})
        records = [
            ("a", "opened", "07T02:15"), ("a", "opened", "07T09:15"),  # A duplicate of a visit before the cutoff.
            ("b", "opened", "07T03:45"), ("b", "opened", "07T14:50"),
            ("a", "sample_completed", "07T03:39"), ("b", "sample_completed", "07T03:59"),
            ("a", "opened", "08T03:10"), ("a", "sample_completed", "08T03:40"),  # Future record.
            ("staff", "opened", "08T03:00"), ("closing", "opened", "08T03:00"),
            ("other", "opened", "08T03:00"), ("deleted", "opened", "08T03:00"),
        ]
        for uid, action, date_hour in records:
            await db.events.insert_one({"user_id": uid, "type": "couples_" + action,
                                        "ts": "2026-11-" + date_hour + ":00+00:00", "note": "private"})
        expected = [
            {"type": "couples", "at": "2026-11-08T03:00:00+00:00", "visits": 1, "samples": 0},
            {"type": "couples", "at": "2026-11-07T03:00:00+00:00", "visits": 1, "samples": 2},
        ]
        for kind, include in (("all", True), ("couples", False)):
            result = await trends.live_activity(db, eligible, kind, include)
            assert result["items"] == expected
            assert result["last24"]["couples"] == 4
        legacy = await trends.live_activity(db, eligible)
        assert legacy["items"] == [] and "couples" not in legacy["last24"]
        filtered = await trends.live_activity(db, eligible, "chat", True)
        assert filtered["items"] == [] and filtered["last24"]["couples"] == 4

    run_case(check)


def test_member_events_reconcile_across_entry_points_retries_and_legacy_skips(monkeypatch):
    from backend.tests.test_free_plan_quota import run_case
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Set IMLADRI_TEST_MONGO_URL to a loopback MongoDB.")
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", "imladri_fixture")
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        async def identity(request: Request):
            return await db.users.find_one({"user_id": request.headers["x-member"]}, {"_id": 0})
        monkeypatch.setitem(server.app.dependency_overrides, server.current_member, identity)
        monkeypatch.setattr(server, "regenerate_report", AsyncMock())
        monkeypatch.setattr(server, "ai_json", AsyncMock(side_effect=lambda *args, **kwargs: {
            "tone": {"status": "insufficient", "drafts": {}}}))
        monkeypatch.setattr(server, "parse_convo", AsyncMock(return_value=[{"speaker": "me", "text": "private"}]))
        await server.ensure_indexes()
        t = int(time.time())
        paid = {"tier": "paid", "stripe_subscription_id": "sub_fixture", "subscription_period_start": t-60,
                "subscription_period_end": t+3600}
        for uid in ("new", "old", "actual", "staff"):
            await db.users.insert_one({"user_id": uid, "role": "admin" if uid == "staff" else "member",
                                      "env": server.ENV, "email": uid + "@member.invalid", **paid})
        await db.users.update_one({"user_id": "old"}, {"$set": {"onboarded": True, "onboarding_step": 2}})
        await db.events.insert_many([{"user_id": "old", "type": "onboarding_step", "data": {"step": value}, "ts": server.now()}
                                     for value in (2, 5)])
        await server.advance("actual", 5)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://local.test") as http:
            async def call(uid, method, url, body=None, headers=None):
                result = await http.request(method, "/api" + url, headers={"x-member": uid, **(headers or {})}, json=body)
                result.raise_for_status()
                return result.json()
            mismatch = {"x-member": "new"}
            switched = {**mismatch, "X-Settings-Member": "old"}
            assert (await http.get("/api/me/export", headers=switched)).status_code == 409
            assert (await http.delete("/api/me", headers=switched)).status_code == 409
            monkeypatch.setitem(server.app.dependency_overrides, server.require_user, identity)
            assert (await http.delete("/api/me/account", headers=switched)).status_code == 409
            assert (await http.patch("/api/me/trends-consent", headers=mismatch, json={"expected_member":"old", "enabled":True})).status_code == 409
            await asyncio.gather(*(call("new", "POST", "/me/event", {"type": "onboarding_opened"}) for _ in range(2)))
            await call("new", "PATCH", "/me", {"onboarded": True})
            origins = await asyncio.gather(*(call("new", "PATCH", "/me", {"origin": value}) for value in ("types", "figures", "about")))
            assert len({response["origin"] for response in origins}) == 1
            assert await db.events.count_documents({"type": "signup", "user_id": "new"}) == 1
            session = {"type": "session", "attempt_id": str(uuid.uuid4())}
            await asyncio.gather(*(call(uid, "POST", "/me/event", session) for uid in ("new", "new", "actual")))
            first = {"X-Upload-Attempt": str(uuid.uuid4()), "Idempotency-Key": "first"}
            for _ in range(2):
                parsed = await call("new", "POST", "/quickstart/parse", {"nickname": "Alex", "conversation_text": "a line"}, first)
            quick = {"nickname": "Alex", "what_happened": "An actual question", "lines": parsed["lines"]}
            result = await call("new", "POST", "/quickstart", quick, first)
            assert (await call("new", "POST", "/quickstart", quick, first))["exchange"]["id"] == result["exchange"]["id"]
            pid = result["person"]["id"]
            wizard = {"X-Upload-Attempt": str(uuid.uuid4()), "Idempotency-Key": "second"}
            for _ in range(2):
                await call("new", "POST", "/me/event", {"type": "upload_started", "attempt_id": wizard["X-Upload-Attempt"]})
                await call("new", "POST", f"/people/{pid}/exchanges", {"conversation": parsed["lines"]}, wizard)
            await call("new", "POST", f"/people/{pid}/exchanges", {"question": "Only a question"}, {"Idempotency-Key": "third"})
            abandoned = {"X-Upload-Attempt": str(uuid.uuid4())}
            await call("new", "POST", "/quickstart/parse", {"nickname": "Alex", "conversation_text": "unused"}, abandoned)
            report = await call("staff", "GET", "/admin/metrics")
            assert report["onboarding_skipped"] == 2
            assert report["onboarding_steps"] == {"1": 3, "2": 2, "3": 1, "4": 1, "5": 1}
            assert report["sessions"] == 2 and report["returning_users"] == 0
            assert report["upload_completion"] == {"started": 3, "completed": 2, "rate": 67}
            activity = (await call("staff", "GET", "/admin/trends?days=7"))["totals"]
            assert (activity["uploads_started"], activity["uploads_completed"], activity["reads"], activity["sessions"]) == (3, 2, 3, 2)
            assert await db.events.count_documents({"type": "onboarding_step", "user_id": "new"}) == 1
    run_case(scenario)


def test_admin_counts_current_quiz_answers_from_both_entry_points(monkeypatch):
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Set IMLADRI_TEST_MONGO_URL to run against disposable local MongoDB.")
    assert urlsplit(uri).hostname in {"127.0.0.1", "localhost", "::1"}
    name = "imladri_quiz_metrics_" + uuid.uuid4().hex
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", name)
    from backend import server

    async def run():
        mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        db = mongo[name]
        monkeypatch.setattr(server, "db", db)

        async def fixture_member(request: Request):
            return await db.users.find_one({"user_id": request.headers["x-member"]}, {"_id": 0})

        monkeypatch.setitem(server.app.dependency_overrides, server.current_member, fixture_member)
        try:
            await db.users.insert_many([
                {"user_id": uid, "role": "admin" if uid == "staff" else "member", "onboarded": True,
                 "email": "a+test@example.com" if uid == "tester" else f"{uid}@qa.invalid"}
                for uid in ("staff", "full", "quick", "legacy", "tester")])
            t = int(time.time())
            for uid in ("quick", "full", "tester"):
                await db.users.update_one({"user_id": uid}, {"$set": {"tier": "paid", "stripe_subscription_id": "sub_" + uid,
                                         "subscription_period_start": t - 30, "subscription_period_end": t + 3600}})
            await db.settings.insert_one(server.default_settings())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://local.test") as client:
                async def call(uid, method, path, body=None):
                    result = await client.request(method, "/api" + path, headers={"x-member": uid}, json=body)
                    result.raise_for_status()
                    return result.json()

                async def metrics():
                    return (await call("staff", "GET", "/admin/metrics"))["quiz"]

                quiz = await call("full", "GET", "/quiz")
                basic, deeper, further = (quiz[key] for key in ("short", "extended", "further"))
                def choices(questions):
                    return {q["id"]: {"choice": 0, "note": "private test note"} for q in questions}

                await call("full", "PUT", "/me/about", {"answers": choices(basic + deeper + further)})
                await call("quick", "POST", "/me/quick-answer", {"qid": basic[0]["id"], "choice": None})
                await call("quick", "POST", "/me/quick-answer", {"qid": basic[0]["id"], "choice": 0})
                early = (await metrics())["sections"]["short"]
                assert (early["started"], early["halfway"], early["complete"]) == (2, 1, 1)
                for q in basic:
                    await call("quick", "POST", "/me/quick-answer", {"qid": q["id"], "choice": 0})
                await call("quick", "POST", "/me/quick-answer", {"qid": basic[0]["id"], "choice": 0})
                assert await db.events.count_documents({"user_id": "quick", "type": "quiz_short"}) == 1
                assert await db.events.count_documents({"user_id": "full", "type": "quiz_extended_complete"}) == 1

                # Historical answers have no wording or events. Ignore private notes, tester accounts,
                # out-of-range choices and a stale milestone left by an earlier app release.
                await db.users.update_one({"user_id": "legacy"}, {"$set": {
                    "about.answers": {**choices(basic[:6] + deeper[:18] + further[:30]), basic[6]["id"]: {"choice": 99}}}})
                await db.users.update_one({"user_id": "tester"}, {"$set": {"about.answers": choices(basic)}})
                await db.events.insert_one({"type": "quiz_short", "user_id": "legacy", "ts": server.now()})
                snapshot = await metrics()
                assert "private test note" not in str(snapshot)
                assert snapshot["users"] == 3
                assert snapshot["sections"] == {
                    "short": {"questions": 12, "halfway_at": 6, "started": 3, "halfway": 3, "complete": 2},
                    "extended": {"questions": 36, "halfway_at": 18, "started": 2, "halfway": 2, "complete": 1},
                    "further": {"questions": 60, "halfway_at": 30, "started": 2, "halfway": 2, "complete": 1}}
                assert (await client.get("/api/admin/metrics", headers={"x-member": "quick"})).status_code == 403

                await call("quick", "PUT", "/me/about", {"answers": {basic[0]["id"]: None}})
                assert (await metrics())["sections"]["short"]["complete"] == 1
                # New wording retires all previous responses to that question. Empty sections never
                # count as complete, and an odd-sized section rounds the halfway threshold up.
                quiz["short"] = basic[:3]
                quiz["short"][0]["q"] += " (revised)"
                quiz["extended"] = []
                saved = await call("staff", "PUT", "/admin/quiz/everyday/draft",
                                   {"quiz": quiz, "revision": None, "quiz_revision": None})
                preview = await call("staff", "POST", "/admin/quiz/everyday/preview", {"quiz": quiz, "choices": {}})
                assert preview["answered"] == 0
                await call("staff", "PUT", "/admin/settings", {"quiz": quiz, "quiz_revision": None,
                                                               "everyday_draft_revision": saved["revision"]})
                changed = (await metrics())["sections"]
                assert changed["short"] == {"questions": 3, "halfway_at": 2, "started": 3, "halfway": 3, "complete": 0}
                assert changed["extended"] == {"questions": 0, "halfway_at": 0, "started": 0, "halfway": 0, "complete": 0}
        finally:
            await mongo.drop_database(name)
            mongo.close()

    asyncio.run(run())


def test_dashboard_includes_members_and_people_beyond_old_query_limits(monkeypatch):
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Set IMLADRI_TEST_MONGO_URL to run against disposable local MongoDB.")
    assert urlsplit(uri).hostname in {"127.0.0.1", "localhost", "::1"}
    name = "imladri_admin_totals_" + uuid.uuid4().hex
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", name)
    from backend import server

    async def run():
        mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        db = mongo[name]
        monkeypatch.setattr(server, "db", db)
        first = "2026-03-01T00:00:00+00:00"
        second = "2026-03-02T00:00:00+00:00"
        late = "2026-03-20T00:00:00+00:00"
        try:
            await server.ensure_indexes()
            uids = [f"fixture-{i}" for i in range(10001)]
            await db.users.insert_many([{"user_id": uid, "tier": "paid" if uid == uids[0] else "free"} for uid in uids])
            t = int(time.time())
            await db.users.update_one({"user_id": uids[0]}, {"$set": {"stripe_subscription_id": "sub_fixt",
                                     "subscription_period_start": t - 30, "subscription_period_end": t + 3600}})
            await db.events.insert_many([
                {"type": "session", "user_id": uid, "ts": ts}
                for uid in uids for ts in (first, first if uid == uids[-1] else second)])
            await db.people.insert_many([{"id": f"friend-{i}", "rel_type": "friend", "user_id": uids[1]} for i in range(20001)])
            person = {"id": "p", "rel_type": "partner", "user_id": uids[0],
                      "intake": {"known_for": "  exactly8  ", "current_state": "brief", "her_goal": "   eight or more   ",
                                 "extra": "unknown fields do not contribute"}}
            await db.people.insert_one(person)
            await db.exchanges.insert_many([
                {"id": "initial", "person_id": "p", "user_id": uids[0], "created_at": first, "has_conversation": True},
                {"id": "followup", "person_id": "p", "user_id": uids[0], "created_at": second, "outcome": "logged"},
                {"id": "other", "person_id": "friend-0", "user_id": uids[1], "created_at": first},
                {"id": "too-late", "person_id": "friend-0", "user_id": uids[1], "created_at": late}])
            await db.events.insert_many([{"type": "deepen", "user_id": uids[i], "ts": ts}
                                         for i, ts in ((0, first), (0, second), (1, late))])
            await db.memories.insert_one({"id": "retained", "user_id": uids[0]})
            await db.events.insert_many([{"type": "memory_edit", "user_id": uids[i], "ts": second} for i in (0, 1, 1)])
            await db.events.insert_many([{"type": kind, "user_id": uids[0], "ts": ts}
                                         for kind in ("upload_started", "upload_completed", "memory_edit")
                                         for ts in ("bad timestamp", "2099-01-01T00:00:00Z")])
            await db.events.insert_many([{"type": ["upload_completed"], "user_id": uids[0], "ts": second},
                                         {"type": "memory_edit", "user_id": [uids[0]], "ts": second}])
            await db.memories.insert_one({"id": "bad-holder", "user_id": [uids[1], "outside-population"]})
            member_score = (await server.meter(person))["score"]
            metrics = await server.admin_metrics({"role": "admin"})
            assert metrics["upload_completion"] == {"started": 0, "completed": 0, "rate": 0}
            assert metrics["users"] == metrics["quiz"]["users"] == 10001
            assert metrics["sessions"] == 20002 and metrics["returning_users"] == 10001
            assert metrics["returning_days"] == 10000
            assert metrics["people_by_relationship"] == {"friend": 20001, "partner": 1}
            assert metrics["people_by_group"] == {"friends": 20001, "romantic": 1}
            assert metrics["avg_detail_by_relationship"] == {"friend": 0, "partner": member_score}
            assert metrics["avg_detail_by_tier"] == {"unsubscribed": 0, "paid": member_score}
            assert metrics["deepening_rate"] == {"first_session_users": 2, "deepened_within_7d": 1, "rate": 50}
            assert metrics["deepening_window"] == {"complete": 2, "deepened": 1, "open": 0}
            assert (metrics["memory_edits"], metrics["users_with_memories"], metrics["memory_edits_by_current_holders"], metrics["memory_edit_rate"]) == (3, 1, 1, 1)
            assert [step["value"] for step in metrics["activation"][:3]] == [10001, 2, 1]
            stamp = server.now()
            await db.users.update_one({"user_id": uids[-1]}, {"$set": {"email": "last@member.invalid", "created_at": first}})
            await db.users.insert_one({"user_id": "ignore", "email": "clerk_test@example.com"})
            await db.exchanges.insert_many([{"id": "activity-" + str(i), "user_id": uid, "created_at": stamp,
                "has_conversation": True, "question": "private fixture"} for i, uid in enumerate([uids[-1]] * 66 + ["ignore"] * 70)])
            live = await server.admin_activity({"role": "admin"})
            assert len(live["items"]) == 60 and live["last24"]["chat"] == 66
            assert all(row["user"] == "last@member.invalid" and row["detail"] == "Shared a conversation" for row in live["items"])
            assert "private fixture" not in str(live)
            await db.events.insert_one({"type": "deepen", "user_id": uids[-1], "ts": stamp})
            await db.users.update_one({"user_id": uids[1]}, {"$set": {"created_at": datetime(2026, 3, 1)}})
            await db.users.update_one({"user_id": uids[2]}, {"$set": {"created_at": "2026-02-28T20:30:00-04:00"}})
            await db.people.insert_one({"id": "before-read", "user_id": uids[2], "intake": {"known_for": "X"}})
            await db.events.insert_many([
                {"type": "session", "user_id": uids[3], "ts": datetime(2026, 3, 1)},
                {"type": "session", "user_id": uids[3], "ts": "invalid"},
                {"type": "deepen", "user_id": uids[1], "ts": "2026-03-08T02:00:01+02:00"},
            ])
            updated = await server.admin_metrics({"role": "admin"})
            assert updated["deepening_window"] == {"complete": 2, "deepened": 1, "open": 1}
            assert updated["deepening_rate"] == {"first_session_users": 3, "deepened_within_7d": 2, "rate": 67}
            assert updated["profile_progress"] == {"members": 10001, "started": 3, "with_details": 2}
            assert updated["sessions"] == 20003 and updated["signups_by_day"] == [{"day": "2026-03-01", "count": 3}]
            await db.users.update_many({"user_id": {"$in": uids[1:3]}}, {"$unset": {"created_at": ""}})
            signups = await server.admin_activity({"role": "admin"}, kind="signup")
            assert len(signups["items"]) > 0 and all(i["type"] == "signup" for i in signups["items"])
            assert signups["last24"] == live["last24"]
        finally:
            await mongo.drop_database(name)
            mongo.close()

    asyncio.run(run())
