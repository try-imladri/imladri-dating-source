"""Real isolated repository tests; no Clerk, provider, production DB or Stripe calls."""
import asyncio
from datetime import date, datetime, timedelta, timezone
import os
import struct
from urllib.parse import urlsplit
from uuid import uuid4
import zlib

from fastapi import Depends, FastAPI, Header, HTTPException
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from backend.dating import INDEXES, make_router
from backend.dating import catalog, service as d
from backend.postgres_store import PostgresDatabase


def png(metadata=True):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
            + (chunk(b"tEXt", b"location\x00PRIVATE GPS") if metadata else b"")
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x99\x88\x77" * 64) * 64)) + chunk(b"IEND", b""))


def exercise(engine, callback):
    async def run():
        namespace = "dating_fixture_" + uuid4().hex
        client = None
        if engine == "mongo":
            uri = os.getenv("IMLADRI_TEST_DATING_MONGO", "mongodb://127.0.0.1:27339")
            assert urlsplit(uri).hostname in ("127.0.0.1", "localhost", "::1")
            client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2500)
            db = client[namespace]
        else:
            dsn = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
            if not dsn:
                pytest.skip("Set isolated loopback IMLADRI_TEST_POSTGRES_DSN")
            assert urlsplit(dsn).hostname in ("127.0.0.1", "localhost", "::1")
            db = PostgresDatabase(dsn, namespace, allow_local_plaintext=True)
        try:
            for collection, indexes in INDEXES.items():
                if indexes:
                    await db[collection].create_indexes(indexes)
            await db.users.insert_many([{"user_id": uid, "env": "test", "role": "admin" if uid == "staff" else "user"} for uid in ("a", "b", "c", "staff")])

            async def member(x_member: str = Header("a"), x_env: str = Header("test")):
                row = await db.users.find_one({"user_id": x_member})
                if not row:
                    raise HTTPException(401)
                return {**row, "env": x_env}

            async def staff(who=Depends(member)):
                if who["role"] != "admin":
                    raise HTTPException(403)
                return who

            app = FastAPI()
            app.include_router(make_router(lambda: db, member, staff), prefix="/api")
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
                await callback(db, api)
        finally:
            if client:
                await client.drop_database(namespace)
                client.close()
            else:
                def cleanup(cursor, timestamp):
                    for table in ("unique_keys", "indexes", "documents"):
                        cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (namespace,))
                await db._run(cleanup, write="__cleanup")
                await db.aclose()
    asyncio.run(run())


async def call(api, method, path, uid="a", expected=200, **kwargs):
    response = await api.request(method, "/api/dating" + path, headers={"x-member": uid, **kwargs.pop("headers", {})}, **kwargs)
    assert response.status_code == expected, response.text
    return response.json() if response.headers.get("content-type", "").startswith("application/json") else response.content


async def join(api, uid="a"):
    return await call(api, "POST", "/enrollment", uid, json={"version": catalog.ENROLLMENT_VERSION, "accepted": True, "adult_confirmed": True})


async def ready(api, uid="a", gender="woman", genders=None, city="Chicago", publish=True):
    await join(api, uid)
    row = await call(api, "PUT", "/profile", uid, json={"display_name": "Member " + uid, "date_of_birth": "1995-06-10", "gender": gender,
        "city": city, "bio": "I enjoy walks, books and thoughtful conversations.", "intent": "long_term", "relationship_structure": "monogamous"})
    pid = row["profile"]["id"]
    await call(api, "PUT", "/preferences", uid, json={"genders": genders or ["woman", "man", "nonbinary", "another"], "personality": {"communication": {"self": "Daily messages", "acceptable": ["Daily messages"]}}})
    photo = (await call(api, "POST", "/photos", uid, files={"file": ("photo.png", png(), "image/png")}))["photo"]
    await call(api, "POST", "/profile/submit", uid)
    if publish:
        await call(api, "POST", "/admin/photos/" + photo["id"] + "/review", "staff", json={"decision": "approve"})
        await call(api, "POST", "/admin/profiles/" + pid + "/review", "staff", json={"decision": "approve"})
    return pid, photo["id"]


async def pair(api):
    first, photo_a = await ready(api, "a")
    second, photo_b = await ready(api, "b", "man")
    results = await asyncio.gather(call(api, "POST", "/interests", "a", json={"profile_id": second, "choice": "like"}),
                                   call(api, "POST", "/interests", "b", json={"profile_id": first, "choice": "like"}))
    mid = next(result["match_id"] for result in results if result["matched"])
    return first, second, photo_a, photo_b, mid


@pytest.mark.parametrize("engine", ["mongo", "postgres"])
def test_explicit_enrollment_adult_gate_original_catalog_and_private_storage(engine):
    async def run(db, api):
        await db.people.insert_one({"user_id": "a", "id": "private-person", "answers": {"s1": "private prior answer"}})
        await db.intimacy_answers.insert_one({"user_id": "a", "answers": {"private": "secret"}})
        before = await call(api, "GET", "/bootstrap")
        assert before["enrollment"] is None and before["profile"] is None
        assert before["preferences"]["personality"] == {} and not before["preferences"]["intimacy_matching"]
        assert any(q["source"].get("id") == "s1" for q in before["catalog"]["personality"])
        assert any(q["source"]["catalog"] == "intimacy" for q in before["catalog"]["intimacy"])
        assert before["membership"]["checkout_enabled"] is False
        await call(api, "POST", "/enrollment", expected=422, json={"version": catalog.ENROLLMENT_VERSION, "accepted": False, "adult_confirmed": True})
        await join(api)
        today = date.today()
        too_young = today.replace(year=today.year-17).isoformat()
        await call(api, "PUT", "/profile", expected=422, json={"display_name": "A", "date_of_birth": too_young, "gender": "woman", "city": "Chicago"})
        await call(api, "PUT", "/preferences", expected=422, json={"genders": ["man"], "personality": {"invented": {"self": "private"}}})
        await call(api, "GET", "/admin/queue", expected=403)
        assert await db.dating_profiles.count_documents({}) == 0
        assert await db.people.count_documents({}) == 1
    exercise(engine, run)


@pytest.mark.parametrize("engine", ["mongo", "postgres"])
def test_review_and_reciprocal_eligibility_safe_dto_and_photos(engine):
    async def run(db, api):
        aid, photo_a = await ready(api, "a", genders=["man"])
        bid, photo_b = await ready(api, "b", "man", genders=["woman"])
        await ready(api, "c", "woman", genders=["man"])
        result = await call(api, "GET", "/discovery", "a")
        assert [row["id"] for row in result["items"]] == [bid]
        safe = result["items"][0]
        assert not {"date_of_birth", "user_id", "personality", "intimacy", "preferences"} & safe.keys()
        assert safe["verification"] == {"photo_reviewed": True, "identity_verified": False}
        assert "PRIVATE GPS" not in (await call(api, "GET", "/photos/" + photo_b + "/content", "a")).decode("latin1")
        await call(api, "GET", "/photos/" + photo_a + "/content", "c", expected=404)
        await call(api, "PUT", "/preferences", "b", json={"genders": ["man"]})
        assert not (await call(api, "GET", "/discovery", "a"))["items"]
        await call(api, "GET", "/profiles/" + bid, "a", expected=404)
        await call(api, "PUT", "/preferences", "b", json={"genders": ["woman"]})
        await call(api, "PUT", "/preferences", "a", json={"genders": ["man"], "personality_matching": True, "personality": {"communication": {"acceptable": ["Daily messages"], "importance": "essential"}}})
        assert not (await call(api, "GET", "/discovery", "a"))["items"]  # other has not granted ranking use
        await call(api, "PUT", "/preferences", "b", json={"genders": ["woman"], "personality_matching": True, "personality": {"communication": {"self": "Daily messages"}}})
        assert len((await call(api, "GET", "/discovery", "a"))["items"]) == 1
        await call(api, "POST", "/profile/state", "a", json={"state": "paused"})
        await call(api, "POST", "/admin/profiles/" + aid + "/review", "staff", json={"decision": "suspend"})
        await call(api, "POST", "/admin/profiles/" + aid + "/review", "staff", json={"decision": "restore"})
        assert (await call(api, "GET", "/profile", "a"))["profile"]["state"] == "paused"
    exercise(engine, run)


@pytest.mark.parametrize("engine", ["mongo", "postgres"])
def test_real_member_chat_concurrent_match_idempotency_cursors_block_and_report(engine):
    async def run(db, api):
        aid, bid, photo_a, photo_b, mid = await pair(api)
        assert await db.dating_matches.count_documents({}) == 1
        duplicate = await call(api, "POST", "/interests", "a", json={"profile_id": bid, "choice": "like"})
        assert duplicate["match_id"] == mid
        await join(api, "c")
        await call(api, "GET", "/matches/" + mid + "/messages", "c", expected=404)
        first = await call(api, "POST", "/matches/" + mid + "/messages", "a", json={"text": "Hello there", "request_id": "fixture-request-1"})
        repeat = await call(api, "POST", "/matches/" + mid + "/messages", "a", json={"text": "Hello there", "request_id": "fixture-request-1"})
        assert first == repeat
        await call(api, "POST", "/matches/" + mid + "/messages", "a", expected=409, json={"text": "changed", "request_id": "fixture-request-1"})
        await call(api, "POST", "/matches/" + mid + "/messages", "b", json={"text": "Hi", "request_id": "fixture-request-2"})
        matches = await call(api, "GET", "/matches", "b")
        assert matches["items"][0]["unread_count"] == 1
        page = await call(api, "GET", "/matches/" + mid + "/messages?limit=1", "a")
        assert page["items"][0]["text"] == "Hi" and page["next_cursor"]
        older = await call(api, "GET", "/matches/" + mid + "/messages?cursor=" + page["next_cursor"], "a")
        assert older["items"][0]["text"] == "Hello there"
        await call(api, "POST", "/matches/" + mid + "/read", "b")
        assert (await call(api, "GET", "/matches", "b"))["items"][0]["unread_count"] == 0
        await call(api, "POST", "/blocks", "a", json={"profile_id": bid})
        assert not (await call(api, "GET", "/matches", "b"))["items"]
        await call(api, "GET", "/photos/" + photo_a + "/content", "b", expected=404)
        await call(api, "POST", "/matches/" + mid + "/messages", "b", expected=404, json={"text": "Blocked", "request_id": "fixture-request-3"})
        report = await call(api, "POST", "/reports", "b", json={"profile_id": aid, "match_id": mid, "category": "messages", "text": "Review this incident", "message_ids": [first["message"]["id"]]})
        await call(api, "POST", "/admin/reports/" + report["id"] + "/review", "staff", json={"status": "resolved", "note": "Reviewed evidence"})
        await call(api, "DELETE", "/blocks/" + bid, "a")
        assert not (await call(api, "GET", "/matches", "a"))["items"]
        await call(api, "GET", "/matches/" + mid + "/messages", "a", expected=403, headers={"x-env": "other"})
    exercise(engine, run)


@pytest.mark.parametrize("engine", ["mongo", "postgres"])
def test_selected_bilateral_sharing_revocation_private_feedback_export_and_leave(engine):
    async def run(db, api):
        aid, bid, photo_a, photo_b, mid = await pair(api)
        answer = {"scope": "personality", "question_id": "communication", "answer": "Daily messages"}
        shared = await call(api, "POST", "/matches/" + mid + "/sharing", "a", json={"answers": [answer]})
        assert shared["sharing"]["mine"] and not shared["sharing"]["partner"]
        hidden = await call(api, "GET", "/matches/" + mid + "/sharing", "b")
        assert hidden["sharing"]["partner"] == []
        shared = await call(api, "POST", "/matches/" + mid + "/sharing", "b", json={"answers": [answer]})
        assert shared["sharing"]["partner"][0]["answer"] == "Daily messages"
        await call(api, "DELETE", "/matches/" + mid + "/sharing", "a")
        assert (await call(api, "GET", "/matches/" + mid + "/sharing", "b"))["sharing"]["partner"] == []
        await call(api, "POST", "/matches/" + mid + "/sharing", "a", expected=422, json={"answers": [{**answer, "answer": "Flexible"}]})
        await call(api, "POST", "/matches/" + mid + "/feedback", "a", json={"conversation_happened": True, "date_happened": False, "want_another": "unsure", "note": "PRIVATE FEEDBACK"})
        export_b = await call(api, "GET", "/export", "b")
        assert "PRIVATE FEEDBACK" not in str(export_b)
        await call(api, "POST", "/blocks", "b", json={"profile_id": aid})
        await db.people.insert_one({"user_id": "a", "id": "legacy-private"})
        await call(api, "DELETE", "/enrollment", "a", json={"confirmed": True})
        assert (await call(api, "GET", "/bootstrap", "a"))["enrollment"] is None
        for name in ("dating_profiles", "dating_preferences", "dating_photos", "dating_feedback", "dating_shares"):
            assert await db[name].count_documents({"user_id": "a"}) == 0
        assert await db.people.count_documents({"user_id": "a"}) == 1
        assert await db.dating_blocks.count_documents({"user_id": "b", "target_user_id": "a"}) == 1
    exercise(engine, run)


def test_exact_adult_birthday_and_image_metadata_validation():
    assert d.age_on("2008-11-19", date(2026, 11, 19)) == 18
    assert d.age_on("2008-11-20", date(2026, 11, 19)) == 17
    mime, data = d.image_content(png())
    assert mime == "image/png" and b"PRIVATE GPS" not in data
    with pytest.raises(HTTPException):
        d.image_content(b"<svg onload=alert(1)>")
    with pytest.raises(HTTPException):
        d.image_content(png()[:len(png()) // 2])


@pytest.mark.parametrize("engine", ["mongo", "postgres"])
def test_staff_publication_appeal_city_controls_and_bounded_incident_retention(engine):
    async def run(db, api):
        profile, photo = await ready(api, "a", publish=False)
        await call(api, "POST", "/admin/profiles/" + profile + "/review", "staff", expected=409, json={"decision": "approve"})
        await call(api, "POST", "/admin/profiles/" + profile + "/review", "a", expected=403, json={"decision": "approve"})
        await call(api, "POST", "/admin/profiles/" + profile + "/review", "staff", json={"decision": "reject", "note": "Please update your profile"})
        appeal = await call(api, "POST", "/appeals", "a", json={"text": "Please review my profile again"})
        queue = await call(api, "GET", "/admin/queue", "staff")
        assert queue["settings"]["city"] == "Chicago" and queue["appeals"][0]["id"] == appeal["id"]
        await call(api, "POST", "/admin/appeals/" + appeal["id"] + "/review", "staff", json={"status": "resolved", "note": "You can resubmit"})
        await call(api, "POST", "/profile/submit", "a")
        await call(api, "POST", "/admin/photos/" + photo + "/review", "staff", json={"decision": "approve"})
        await call(api, "POST", "/admin/profiles/" + profile + "/review", "staff", json={"decision": "approve"})
        await call(api, "PUT", "/admin/settings", "staff", json={"city": "Chicago", "discovery_enabled": False, "accepting_profiles": False, "review_note": "Waiting for local availability"})
        assert (await call(api, "GET", "/discovery", "a"))["items"] == []
        await call(api, "POST", "/enrollment", "b", expected=409, json={"version": catalog.ENROLLMENT_VERSION, "accepted": True, "adult_confirmed": True})
        await db.dating_reports.insert_one({"env": "test", "id": "expired-incident", "status": "open", "expires_at": datetime.now(timezone.utc)-timedelta(days=1)})
        await call(api, "GET", "/admin/queue", "staff")
        assert await db.dating_reports.find_one({"id": "expired-incident"}) is None
        assert all(row.get("expires_at") for row in await db.dating_moderation.find({}).to_list(50))
        await call(api, "GET", "/export", "a")
        await call(api, "DELETE", "/enrollment", "a", json={"confirmed": True})
    exercise(engine, run)
