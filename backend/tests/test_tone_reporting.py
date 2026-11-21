"""Verify tone reporting and research using a disposable local MongoDB."""
import json
import os
from datetime import datetime, timezone

import httpx
import pytest

from backend import couples_analytics, personal_demographics, tone_research, trends
from backend.tests.test_free_plan_quota import run_case


pytestmark = pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires loopback MongoDB.")
ENROLLED = "2025-06-01T00:00:00+00:00"
READ = "2025-06-03T00:00:00+00:00"
DATES = {"$gte": "2025-06-01", "$lt": "2025-06-30"}
ELIGIBLE = {"env": "preview", "role": "member", "email": {"$ne": "fixture-excluded"}, "closing": {"$ne": True}}


def member(uid):
    return {"user_id": uid, "env": "preview", "role": "member", "email": uid + "@example.invalid",
            "couples_adult_at": ENROLLED, "tone_research_version": personal_demographics.TONE_CONSENT,
            "tone_research_at": ENROLLED, "questionnaire_research_version": personal_demographics.CONSENT_VERSION,
            "about": {"demographics": {"age": 30}}}


def read(uid, index):
    sent_style = "warm" if index % 2 == 0 else "direct"
    return {"id": uid, "user_id": uid, "created_at": READ,
            "tone": {"status": "estimated", "incoming": "reserved", "explanation": "PRIVATE-FIXTURE"},
            "tone_feedback": {"fit": "corrected" if index % 2 else "fits", "note": "PRIVATE-FIXTURE",
                              "style": "cool", "styles": [sent_style, "cool"], "copied_styles": [sent_style],
                              "sent": True, "sent_style": sent_style, "helped": index % 4 < 2}}


def shared_pair(pair_id, inviter, invitee, sessions):
    threads = {}
    for key, seats in sessions.items():
        version = max(row.get("source_version", 1) for row in seats.values())
        threads[key] = {"version": version, "shares": {"a": ["shared"], "b": ["shared"]}}
        for side, row in list(seats.items()):
            seats[side] = {**row, "mode":"shared", "env":"preview", "pair_id":pair_id, "source_version": row.get("source_version", version)}
    return {"_id":pair_id, "env":"preview", "state":"active", "members":[inviter,invitee],
            "inviter":inviter, "invitee":invitee, "tone_sessions":sessions, "journey":{"threads":threads}}


def test_activity_counts_each_read_and_off_reports_do_not_change_the_consent_choice():
    async def scenario(db):
        u = member("member")
        await db.users.insert_many([u, {**member("staff"), "role": "admin"}, {**member("qa"), "email": "fixture-excluded"}])
        await db.exchanges.insert_many([read("member", 0), {**read("member", 1), "id": "two"},
            {"id": "three", "created_at": READ, "user_id": "member", "tone": {"status": "insufficient"}},
            {**read("member", 0), "created_at": ENROLLED.replace("06", "03")}, read("staff", 0), read("qa", 1)])
        result = await trends.individual_tone_activity(db, ELIGIBLE, DATES)
        assert result["members"] == 1 and result["reads"] == 3 and result["estimated"] == 2 and result["insufficient"] == 1
        assert result["switched"] == result["copied"] == result["reported_sending"] == result["sent"] == 2
        assert result["corrected"] == 1 and result["reviewed"] == 2 and result["styles"]["cool"] == 2
        assert result["rated_helpfulness"] == 2
        assert "PRIVATE" not in json.dumps(result) and "member@" not in json.dumps(result)
    run_case(scenario)


def test_reports_require_prospective_consent_and_suppress_small_cells_and_overrepresentation():
    async def scenario(db):
        users, exchanges, pairs = [], [], []
        for i in range(60):
            uid, other = f"a{i}", f"b{i}"
            author = member(uid)
            author["about"]["demographics"]["age"] = 32 if i % 2 == 0 else 50
            users.extend([author, member(other)])
            r = read(uid, i)
            exchanges.append(r)
            pairs.append({**shared_pair(f"p{i}", uid, other, {"topic":{"a":r}}),
                          "joined_at": ENROLLED, "created_at": ENROLLED,
                          "stats": {"tone_usage": {"shared_last_at": READ, "private_last_at": READ}}})
        await db.users.insert_many(users)
        await db.exchanges.insert_many(exchanges)
        await db.couples_pairs.insert_many(pairs)
        await db.exchanges.insert_many([{**read("a0", 3), "id": "old-" + str(i), "created_at": ENROLLED} for i in range(33)])
        await db.exchanges.insert_one({**read("a0", 3), "id": "before", "created_at": "2024"})
        for unit in ("member", "couple"):
            r = await tone_research.report(db, "preview", ELIGIBLE, unit, "sent_style", "helped", "reserved")
            assert r["sample_size"] == 60 and [row["cells"] for row in r["rows"]] == [[15, 15], [15, 15]]
            assert "PRIVATE" not in json.dumps(r)
            r = await tone_research.report(db, "preview", ELIGIBLE, unit, "age_band", "helped")
            assert r["sample_size"] == 60 and len(r["rows"]) == 2
            assert not (await tone_research.report(db, "preview", ELIGIBLE, unit, "sent_style", "helped", "warm"))["available"]
        c = await couples_analytics.report(db, "preview", ELIGIBLE, 7, datetime(2025, 6, 5, tzinfo=timezone.utc))
        assert c["tone_usage"] == {"active_pairs": 60, "either": 60, "private": 60, "shared": 60}
        seat_ids = [f"b{i}" for i in range(12)]
        await db.users.update_many({"user_id": {"$in": seat_ids}}, {"$unset": {"tone_research_version": ""}})
        hidden = await tone_research.report(db, "preview", ELIGIBLE, "couple", "sent_style", "helped")
        assert not hidden["available"] and hidden["sample_size"] is None and hidden["rows"] == []
        await db.users.update_many({"user_id": {"$in": seat_ids}}, {"$set": {"tone_research_version": personal_demographics.TONE_CONSENT}})
        # Pairs are counted once even if both seats generated coaching. A withdrawal removes shared evidence.
        await db.couples_pairs.update_one({"_id": "p0"}, {"$set": {"tone_sessions.topic.b":
            {**read("b0", 0),"mode":"shared","env":"preview","pair_id":"p0","source_version":1}}})
        assert (await tone_research.report(db, "preview", ELIGIBLE, "couple", "sent_style", "helped"))["sample_size"] == 60
        await db.couples_pairs.update_many({"inviter": {"$in": [f"a{i}" for i in range(12)]}}, {"$unset": {"tone_sessions": ""}})
        assert not (await tone_research.report(db, "preview", ELIGIBLE, "couple", "sent_style", "helped"))["available"]
        # Renewed consent excludes older reads; fixed-size group suppressed even with many reads for one member.
        await db.users.update_many({"user_id": {"$in": [f"a{i}" for i in range(12)]}}, {"$set": {"tone_research_at": "2026"}})
        assert not (await tone_research.report(db, "preview", ELIGIBLE, "member", "sent_style", "helped"))["available"]
        await db.users.update_many({}, {"$unset": {"questionnaire_research_version": ""}})
        assert not (await tone_research.report(db, "preview", ELIGIBLE, "member", "age_band", "incoming"))["available"]
    run_case(scenario)


def test_member_consent_api_requires_adult_and_fresh_revision_and_withdraws(monkeypatch):
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        user = {**member("m"), "tone_research_version": None, "tone_research_at": None}
        await db.users.insert_one(user)
        async def current():
            return await db.users.find_one({"user_id": "m"})
        monkeypatch.setitem(server.app.dependency_overrides, server.current_member, current)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://local") as client:
            uri = "/api/me/tone-research"
            body = {"version": personal_demographics.TONE_CONSENT, "revision": 0, "enabled": True, "adult_confirmed": True,
                    "expected_member": "m"}
            assert (await client.patch(uri, json={**body, "adult_confirmed": False})).status_code == 403
            assert (await client.patch(uri, json={**body, "expected_member": "other"})).status_code == 409
            r = await client.patch(uri, json=body)
            assert r.status_code == 200 and r.json()["enabled"] and "no-store" in r.headers["Cache-Control"]
            assert (await client.patch(uri, json=body)).status_code == 409
            r = await client.patch(uri, json={**body, "revision": 1, "enabled": False})
            assert r.json()["enabled"] is False and r.json()["revision"] == 2
            assert "tone_research_at" not in await current()
            assert (await client.patch(uri, json={**body, "revision": 2})).is_success
            await personal_demographics.save_own(db, "m", {"age": 17})
            m = await current()
            assert not personal_demographics.preference(m, "tone_research")["enabled"]
            assert (await client.patch(uri, json={**body, "revision": 4})).status_code == 403
    run_case(scenario)


def test_only_current_mutual_seats_and_reads_at_or_after_the_grant_are_selected():
    async def scenario(db):
        grant = "2025-06-03T10:00:00.123456+02:00"
        for uid in ("a","b"):
            await db.users.insert_one({**member(uid),"tone_research_at":grant})
        current = {**read("a",1),"created_at":"2025-06-03T03:00:00.123456-05:00"}
        pair = shared_pair("pair","a","b",{"thread":{"a":current}})
        await db.couples_pairs.insert_one(pair)
        pipeline = [*tone_research.pairs_source("preview"),
                    *tone_research.lookup_member(ELIGIBLE,"other","partner"),
                    *tone_research.lookup_member(ELIGIBLE,"actor","author")]
        async def count():
            return await db.couples_pairs.aggregate(pipeline + [{"$count":"n"}]).to_list(1)
        assert await count() == [{"n":1}]
        for field, invalid in (
            ("tone_sessions.thread.a.created_at","2025-06-03T10:00:00.123455+02:00"),
            ("tone_sessions.thread.a.created_at","2025-06-03T10:00:00"),
            ("tone_sessions.thread.a.mode","private"), ("tone_sessions.thread.a.env","live"),
            ("tone_sessions.thread.a.user_id","b"), ("tone_sessions.thread.a.pair_id","other"),
            ("journey.threads.thread.shares.b",[]), ("journey.threads.thread.version",2),
            ("members",["a","stranger"]),
        ):
            await db.couples_pairs.update_one({"_id":"pair"},{"$set":{field:invalid}})
            assert await count() == [], field
            await db.couples_pairs.replace_one({"_id":"pair"},pair)
        await db.users.update_one({"user_id":"b"},{"$set":{"tone_research_at":datetime(2025,6,3,8,0,0,124000,tzinfo=timezone.utc)}})
        assert await count() == []
        await db.exchanges.insert_one({**current,"tone":{**current["tone"],"status":"insufficient"},
                                       "tone_feedback":{**current["tone_feedback"],"sent":False}})
        result = await db.exchanges.aggregate([{"$project":tone_research.source_projection("$")},
            {"$project":{"_id":0,**{k:tone_research.axis(k) for k in ("fit","incoming","sent_style","helped")}}}]).to_list(1)
        assert result == [{"fit":None,"incoming":None,"sent_style":None,"helped":None}]
    run_case(scenario)
