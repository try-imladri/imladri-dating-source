import asyncio
import csv
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import StringIO
import os
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from backend import couple_research, couples_catalog, couples_reminders
from backend.couples_journey import PROMPTS


def test_joint_checkin_uses_both_utc_instants_when_reporting_or_starting_a_later_thread():
    from types import SimpleNamespace
    from backend import couples_analytics, couples_journey

    later = "2026-11-08T23:30:00-05:00"
    earlier = "2026-11-09T04:00:00+00:00"
    for first in (earlier, datetime(2026, 11, 9, 4)):
        stats = {"first_checkins": {"a": first, "b": later}, "first_agreement_thread_id": "old"}
        assert couples_analytics.first_joint_checkin(stats) == "2026-11-09T04:30:00+00:00"
        thread = {"id": "new", "shares": {"a": ["one"]}, "first_share_at": "2026-11-08T23:45:00-05:00"}
        action = SimpleNamespace(action="share", lines=["two"])
        couples_journey.apply_action(thread, "b", action, stats, "2026-11-09T05:00:00+00:00")
        assert stats["second_mutual_at"] == "2026-11-09T05:00:00+00:00"
    assert couples_analytics.first_joint_checkin({"first_checkins": {"a": earlier, "b": "invalid"}}) is None
    assert couples_analytics.first_joint_checkin({"first_both_checked_in_at": None,
                                                 "first_checkins": {"a": earlier, "b": later}}) is None


def test_couples_cohorts_permissions_content_and_reminders(monkeypatch):
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("A disposable loopback MongoDB is required.")
    assert urlsplit(uri).hostname in {"localhost", "127.0.0.1", "::1"}
    name = "couples_dashboard_" + uuid4().hex
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", name)
    from backend import server

    async def run():
        mongo = AsyncIOMotorClient(uri)
        db = mongo[name]
        monkeypatch.setattr(server, "db", db)
        now = datetime.now(timezone.utc)
        earlier = lambda days: (now - timedelta(days=days)).isoformat()
        from fastapi import Request
        async def member(request: Request):
            return await db.users.find_one({"user_id": request.headers.get("x-member")}, {"_id": 0})
        server.app.dependency_overrides[server.current_member] = member
        try:
            await db.users.insert_one({"user_id": "staff", "env": server.ENV, "role": "admin"})
            for i in range(10):
                for seat in ("a", "b"):
                    uid = seat + str(i)
                    await db.users.insert_one({"user_id": uid, "email": uid + "@sample.invalid", "env": server.ENV,
                        "role": "member", "couples_adult_at": earlier(70), "couples_first_draft_at": earlier(55),
                        "couples_preferences": {"analytics_opt_in": True}})
                await db.couples_pairs.insert_one({"_id": "pair" + str(i), "inviter": "a"+str(i), "invitee": "b"+str(i),
                    "env": server.ENV, "state": "active", "members": ["a"+str(i), "b"+str(i)], "sponsor": "a"+str(i),
                    "created_at": earlier(56), "joined_at": earlier(55), "stats": {
                        "first_share_at": earlier(54), "first_mutual_at": earlier(53),
                        "first_agreement_at": earlier(52), "first_review_date": earlier(47)[:10],
                        "first_checkins": {"a": earlier(47), "b": earlier(46)},
                        "second_mutual_at": earlier(30), "current_outcomes": {"a":"helped","b":"helped"}},
                    "journey": {"private_fixture_text": "NEVER_IN_REPORT"}})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
                async def call(path, method="GET", body=None, uid="staff", status=200):
                    resp = await client.request(method, "/api/admin/couples/" + path, json=body, headers={"x-member": uid})
                    assert resp.status_code == status, resp.text
                    return resp.json()
                await call("analytics", uid="a0", status=403)
                await call("pilot", status=404)
                await call("pilot", "PUT", {"mode":"paused","revision":0}, status=404)
                await call("enrollment", "PUT", {"account":"a0","enrolled":False}, status=404)
                for tag, state, expiry in (("pending", "invited", -2), ("accepted", "confirming", -3), ("expired", "invited", 1)):
                    owner = "inviter-" + tag
                    await db.users.insert_one({"user_id": owner, "env": server.ENV, "role": "member"})
                    partner = {}
                    if state == "confirming":
                        await db.users.insert_one({"user_id": "accepted-partner", "env": server.ENV, "role": "member"})
                        partner = {"invitee": "accepted-partner"}
                    await db.couples_pairs.insert_one({"_id": tag, "inviter": owner, "env": server.ENV, "state": state,
                        "created_at": earlier(120), "issued_at": earlier(4), "expires_at": earlier(expiry), **partner})
                # Existing pairs can have several threads of the same kind, one-sided
                # shares and unsaved learning proposals. Snapshot counts are per pair.
                two = {"a": ["NEVER_IN_REPORT"], "b": ["NEVER_IN_REPORT"]}
                await db.couples_pairs.update_one({"_id": "pair0"}, {"$set": {"journey.threads": {
                    "one": {"kind": "moment", "shares": two}, "two": {"kind": "moment", "shares": two},
                    "three": {"kind": "positive", "shares": {"a": ["NEVER_IN_REPORT"]}},
                    "four": {"kind": "prompt", "shares": two, "agreements": [{"pin": {"accepted": ["a"]}}]},
                }}})
                data = await call("analytics?days=90")
                assert data["shared_features"] == {"active_pairs": 10, "counts": {"prompt": 1, "moment": 1, "positive": 0, "learning": 0}}
                assert (await call("analytics?days=7"))["shared_features"] == data["shared_features"]
                assert data["awaiting_accept"] == data["awaiting_confirm"] == 1
                signup = await client.patch("/api/me", json={"origin": "/couples/invite#NEVER_IN_REPORT"}, headers={"x-member": "b0"})
                assert signup.is_success and signup.json()["origin"] == "couples/invite"
                await db.users.update_many({"user_id": {"$in": ["a0", "b0"]}}, {"$set": {
                    "couples_preferences.review_return_opt_in": True, "couples_preferences.review_return_since": earlier(60),
                }})
                tracked_card = next(iter(couples_catalog.CARDS))
                await db.couples_pairs.update_one({"_id": "pair0"}, {"$set": {
                    "stats.first_due_tracking": True, "stats.first_review_date": now.date().isoformat(),
                    "stats.first_agreement_thread_id": "card-" + tracked_card + "-fixture",
                    "stats.first_due_visits": {"a": now.isoformat(), "b": (now+timedelta(days=1)).isoformat()},
                }})
                download = await client.get("/api/admin/reports/workspace.csv?days=7", headers={"x-member": "staff"})
                assert download.status_code == 200 and "NEVER_IN_REPORT" not in download.text
                current_report = await call("analytics?days=7")
                all_csv = list(csv.DictReader(StringIO(download.text)))
                for scope in ("observed so far", "mature cohort"):
                    rows = {r["metric"]: r for r in all_csv if r["section"] == "Couples" and r["scope"] == scope}
                    assert set(rows) == set(current_report["observed_so_far"])
                    for key, row in rows.items():
                        counts = current_report["observed_so_far"][key] if scope == "observed so far" else current_report[key]
                        assert (int(row["value"]), int(row["denominator"])) == (counts["count"], counts["eligible"])
                        assert ("windows still open" in row["definition"]) == (scope == "observed so far")
                for section_name, category in (("Couples scheduled check-ins", ""),
                                               ("Couples card scheduled check-ins", couples_catalog.CARDS[tracked_card]["name"])):
                    observed = {r["metric"]: r for r in all_csv if r["section"] == section_name
                                and r["scope"] == "observed so far" and r["category"] == category}
                    assert observed["returned"]["value"] == observed["returned"]["denominator"] == "1"
                    assert observed["both_returned"]["value"] == "0"
                    assert "open windows" in observed["returned"]["definition"]
                await db.couples_pairs.update_one({"_id": "pair0"}, {"$set": {"stats.first_review_date": earlier(47)[:10]}})
                card_rows = [row for row in all_csv if row["section"] == "Couples cards"]
                assert len(card_rows) == 24 and all(row["definition"] for row in card_rows)
                assert {row["category"] for row in card_rows} == {row["name"] for row in data["card_funnel"]}
                tone_rows = [row for row in csv.DictReader(StringIO(download.text)) if row["section"] == "Couples tone"]
                assert {row["metric"] for row in tone_rows} == {"private", "shared", "either"}
                assert all(row["denominator"] == "10" for row in tone_rows)
                assert any(row["metric"] == "corrected" and row["denominator"] == "0"
                           for row in csv.DictReader(StringIO(download.text)) if row["section"] == "Individual tone")
                origins = {row["category"]: row["value"] for row in csv.DictReader(StringIO(download.text))
                           if row["section"] == "Entry pages" and row["metric"] == "signups"}
                assert origins["couples/invite"] == "1"
                feature_rows = [row for row in csv.DictReader(StringIO(download.text)) if row["metric"] == "shared_features"]
                assert {row["category"]: int(row["value"]) for row in feature_rows} == data["shared_features"]["counts"]
                assert all(row["scope"] == "current snapshot" and row["denominator"] == "10" for row in feature_rows)
                connection_rows = {row["metric"]: int(row["value"]) for row in csv.DictReader(StringIO(download.text))
                                   if row["section"] == "Couples" and row["metric"] in data["connections"] and row["scope"] == "period"}
                assert connection_rows == data["connections"] == {
                    "invitations_accepted": 0, "invitations_expired": 1, "invitations_canceled": 0, "pairs_ended": 0}
                # The member form clears adult confirmation when participation ceases.
                await db.users.update_one({"user_id": "b0"}, {"$unset": {"couples_adult_at": ""}})
                hidden = (await call("analytics?days=7"))["shared_features"]
                assert hidden["active_pairs"] == 9 and not any(hidden["counts"].values())
                assert not (await call("analytics?days=90"))["outcomes"]["available"]
                await db.users.update_one({"user_id": "b0"}, {"$set": {"couples_adult_at": earlier(70)}})
                await db.users.update_one({"user_id": "b0"}, {"$set": {"about.demographics.age": "17"}})
                flagged = await call("analytics?days=7")
                assert not flagged["outcomes"]["available"] and flagged["shared_features"]["active_pairs"] == 9
                await db.users.update_one({"user_id": "b0"}, {"$unset": {"about.demographics.age": ""}})
                for key in ["pairing", "adoption", "completion", "return", "mutual"]:
                    assert data[key] == {"count":10,"eligible":10}
                # A withdrawal before the partner shares means there was no joint reveal.
                await db.couples_pairs.update_one({"_id": "pair0"}, {"$set": {"stats.first_both_checked_in_at": None}})
                actual = await call("analytics?days=90")
                assert actual["completion"] == {"count": 9, "eligible": 10}
                assert actual["milestones"]["both_checked_in"] == actual["return"]["eligible"] == 9
                await db.couples_pairs.update_one({"_id": "pair0"}, {"$unset": {"stats.first_both_checked_in_at": ""}})
                assert "enrolled" not in data and data["reviews"]["both"] == 10
                assert data["outcomes"]["available"] and data["outcomes"]["respondents"] == 10
                assert "NEVER_IN_REPORT" not in str(data) and "@sample.invalid" not in str(data)
                await db.users.update_one({"user_id":"a0"}, {"$set":{"couples_preferences.analytics_opt_in":False}})
                assert (await call("analytics?days=90"))["outcomes"]["respondents"] is None
                assert (await call("analytics?days=7"))["completion"] == {"count":0,"eligible":0}
                original = await call("catalog")
                questions = deepcopy(original["draft"])
                questions[0]["question"] += " Think about this week."
                saved = await call("catalog","PUT",{"revision":0,"action":"save","prompts":questions})
                assert saved["published"] == PROMPTS
                published = await call("catalog","PUT",{"revision":saved["revision"],"action":"publish"})
                new_key = published["published"][0]["id"]
                assert new_key != PROMPTS[0]["id"]
                old_var = "c:prompt:" + PROMPTS[0]["id"]
                new_var = "c:prompt:" + new_key
                fields = await couple_research.variables(db)
                assert fields[old_var]["label"] == "Archived · " + PROMPTS[0]["question"]
                assert fields[old_var]["choices"] == PROMPTS[0]["choices"]
                assert fields[new_var]["label"] == questions[0]["question"]
                _, retained = await couples_catalog.read_catalog(db, PROMPTS, [PROMPTS[0]["id"]])
                assert retained[PROMPTS[0]["id"]]["question"] == PROMPTS[0]["question"]
                reverted = await call("catalog","PUT",{"revision":published["revision"],"action":"rollback"})
                assert reverted["published"] == PROMPTS
                fields = await couple_research.variables(db)
                assert fields[new_var]["label"] == "Archived · " + questions[0]["question"]
                assert fields[old_var]["label"] == PROMPTS[0]["question"]
                _, retained = await couples_catalog.read_catalog(db, PROMPTS, [new_key])
                assert retained[new_key]["retired"] and retained[new_key]["question"] == questions[0]["question"]
                await call("catalog","PUT",{"revision":0,"action":"rollback"}, status=409)
            pair = await db.couples_pairs.find_one({"_id":"pair0"})
            private = {"shares":{"a":["one"], "b":["two"]}, "agreements":[{"id":"safe","status":"active",
                       "revision":1,"review_on":now.date().isoformat(),"checkins":{},"text":"NEVER_IN_REMINDER"}]}
            await db.couples_pairs.update_one({"_id":pair["_id"]},{"$set":{"sponsor":"a0","journey.threads.one":private}})
            await db.users.update_one({"user_id":"a0"},{"$set":{"tier":"paid","stripe_subscription_id":"sub_fixture",
                "subscription_period_start":int(now.timestamp())-80,"subscription_period_end":int(now.timestamp())+90000}})
            b = await db.users.find_one({"user_id":"b0"})
            assert not await couples_reminders.inbox(db, b, now)
            b["couples_preferences"].update(reminders=True, quiet_start="00:00", quiet_end="00:00", timezone="UTC")
            items = await couples_reminders.inbox(db,b,now)
            assert len(items) == 1 and "NEVER_IN_REMINDER" not in str(items)
            assert items[0]["link"] == "/app/couples/agreements?item=one&agreement=safe&revision=1"
            b["couples_reminders_seen"] = [items[0]["id"]]
            assert (await couples_reminders.inbox(db,b,now))[0]["read"]
            b["couples_preferences"].update(quiet_start="21:00",quiet_end="08:00")
            assert not await couples_reminders.inbox(db,b,now.replace(hour=23, minute=0))
            await db.couples_pairs.update_one({"_id":"pair0"},{"$set":{"state":"closed"}})
            assert not await couples_reminders.inbox(db,b,now)
        finally:
            server.app.dependency_overrides.pop(server.current_member, None)
            await mongo.drop_database(name)
            mongo.close()
    asyncio.run(run())


def test_open_cohorts_count_each_sponsor_or_pair_once_through_the_report_instant():
    from backend import couples_analytics

    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("A disposable loopback MongoDB is required.")
    assert urlsplit(uri).hostname in {"localhost", "127.0.0.1", "::1"}
    as_of = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)

    async def run():
        client = AsyncIOMotorClient(uri)
        db = client["couples_open_cohorts_" + uuid4().hex]
        try:
            def at(days):
                return (as_of + timedelta(days=days)).isoformat()

            cases = {
                "today": [-0.10, -0.09, -0.08, -0.07, -0.06, 0],
                "in_progress": [-0.05, -0.01, 1, 2, 3, 4],
                "on_deadline": [-61, -54, -47, -40, -40, -10],
                "late": [-68, -60, -52, -44, -43, -12],
            }
            for label, timeline in cases.items():
                inviter, invitee = label + "-sponsor", label + "-guest"
                for uid in (inviter, invitee):
                    await db.users.insert_one({"user_id": uid})
                invited, joined, mutual, agreed, checked, another = map(at, timeline)
                await db.couples_pairs.insert_one({
                    "env": "test", "state": "active", "inviter": inviter, "invitee": invitee,
                    "created_at": invited, "joined_at": joined, "stats": {
                        "first_mutual_at": mutual, "first_agreement_at": agreed,
                        "first_review_date": "not a date", "first_checkins": {"a": {"legacy": True}},
                        "first_both_checked_in_at": checked, "second_mutual_at": another,
                    },
                })
            await db.couples_pairs.insert_one({"env": "test", "state": "closed", "inviter": "today-sponsor",
                                                "created_at": at(-0.12)})
            report = await couples_analytics.report(db, "test", {}, 90, current=as_of)
            seven = await couples_analytics.report(db, "test", {}, 7, current=as_of)
            for metric in ("pairing", "mutual", "adoption", "completion", "return"):
                starts_after_sharing = metric in {"adoption", "return"}
                # The pair in progress has joined already; its later events fall after the report instant.
                assert report["observed_so_far"][metric] == {
                    "count": 3 if metric == "pairing" else 2, "eligible": 3 if starts_after_sharing else 4,
                }
                assert report[metric] == {"count": 1, "eligible": 2}
                assert seven["observed_so_far"][metric] == {
                    "count": 2 if metric == "pairing" else 1, "eligible": 1 if starts_after_sharing else 2,
                }
                assert seven[metric] == {"count": 0, "eligible": 0}
        finally:
            await client.drop_database(db.name)
            client.close()

    asyncio.run(run())
