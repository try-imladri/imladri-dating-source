"""Independent regressions for safety controls found during integration review."""
import asyncio
from io import BytesIO
import struct

from fastapi import HTTPException
from PIL import Image
import pytest

from backend.dating import schemas, service
from backend.tests.test_dating import call, exercise, pair, png


class PausedFirstShare:
    """Pause after authorization/revision read, immediately before first insert."""
    def __init__(self, collection, arrived, release):
        self.collection, self.arrived, self.release = collection, arrived, release

    def __getattr__(self, name):
        return getattr(self.collection, name)

    async def insert_one(self, document, *args, **kwargs):
        if document.get("user_id") == "a" and document.get("answers"):
            self.arrived.set()
            await self.release.wait()
        return await self.collection.insert_one(document, *args, **kwargs)


class ShareRaceDatabase:
    def __init__(self, database, arrived, release):
        self.database = database
        self.dating_shares = PausedFirstShare(database.dating_shares, arrived, release)

    def __getattr__(self, name):
        return getattr(self.database, name)


@pytest.mark.parametrize("engine", ["mongo", "postgres"])
def test_revocation_fences_an_inflight_first_share(engine):
    async def run(db, api):
        _, _, _, _, match_id = await pair(api)
        answer = {"scope": "personality", "question_id": "communication", "answer": "Daily messages"}
        await call(api, "POST", f"/matches/{match_id}/sharing", "b", json={"answers": [answer]})
        arrived, release = asyncio.Event(), asyncio.Event()
        body = schemas.Sharing(answers=[answer], expected_revision=0)
        pending = asyncio.create_task(service.share_answers(ShareRaceDatabase(db, arrived, release), {"user_id": "a", "env": "test"}, match_id, body))
        try:
            await asyncio.wait_for(arrived.wait(), timeout=5)
            revoked = await call(api, "DELETE", f"/matches/{match_id}/sharing", "a")
            assert revoked["sharing"]["mine"] == []
        finally:
            release.set()
        with pytest.raises(HTTPException) as rejected:
            await pending
        assert rejected.value.status_code == 409
        partner_view = await call(api, "GET", f"/matches/{match_id}/sharing", "b")
        assert partner_view["sharing"]["partner"] == []
        assert not partner_view["sharing"]["partner_shared"]
        retained = await db.dating_shares.find_one({"env": "test", "match_id": match_id, "user_id": "a"})
        assert not (retained or {}).get("answers")
    exercise(engine, run)


@pytest.mark.parametrize("metadata_position", ["after_scan", "after_image"])
def test_jpeg_metadata_is_removed_after_the_first_scan_and_image_end(metadata_position):
    original = BytesIO()
    Image.new("RGB", (64, 64), (100, 150, 200)).save(original, format="JPEG")
    payload = b"PRIVATE_LOCATION_COMMENT"
    comment = b"\xff\xfe" + struct.pack(">H", len(payload) + 2) + payload
    jpeg = original.getvalue()
    candidate = jpeg[:-2] + comment + jpeg[-2:] if metadata_position == "after_scan" else jpeg + payload
    # A valid, decodable photograph must not be rejected merely to pass stripping.
    Image.open(BytesIO(candidate)).load()
    mime, clean = service.image_content(candidate)
    assert mime == "image/jpeg"
    assert payload not in clean
    decoded = Image.open(BytesIO(clean))
    decoded.load()
    assert decoded.size == (64, 64)
    assert not decoded.getexif()


@pytest.mark.parametrize("engine", ["mongo", "postgres"])
def test_photo_rejection_and_owner_removal_do_not_lift_a_suspension(engine):
    async def run(db, api):
        profile_a, profile_b, photo_a, photo_b, match_id = await pair(api)
        await call(api, "POST", f"/admin/profiles/{profile_a}/review", "staff", json={"decision": "suspend", "note": "Safety review required"})
        await call(api, "GET", f"/profiles/{profile_b}", "a", expected=404)
        await call(api, "GET", f"/photos/{photo_b}/content", "a", expected=404)
        await call(api, "POST", f"/admin/photos/{photo_a}/review", "staff", json={"decision": "reject", "note": "Replace this photo"})
        assert (await call(api, "GET", "/profile", "a"))["profile"]["state"] == "suspended"
        await call(api, "GET", f"/matches/{match_id}/messages", "a", expected=404)
        await call(api, "GET", f"/matches/{match_id}/messages", "b", expected=404)
        await call(api, "DELETE", f"/photos/{photo_a}", "a")
        assert (await call(api, "GET", "/profile", "a"))["profile"]["state"] == "suspended"
        await call(api, "POST", "/profile/submit", "a", expected=409)
        await call(api, "GET", f"/matches/{match_id}/messages", "b", expected=404)
    exercise(engine, run)


@pytest.mark.parametrize("engine", ["mongo", "postgres"])
def test_new_photo_is_not_published_to_existing_matches_until_profile_review(engine):
    async def run(db, api):
        profile_a, _, photo_a, _, _ = await pair(api)
        added = await call(api, "POST", "/photos", "a", files={"file": ("new-photo.png", png(), "image/png")})
        new_photo = added["photo"]["id"]
        await call(api, "POST", f"/admin/photos/{new_photo}/review", "staff", json={"decision": "approve"})
        existing_match_view = await call(api, "GET", f"/profiles/{profile_a}", "b")
        assert [photo["id"] for photo in existing_match_view["photos"]] == [photo_a]
        await call(api, "GET", f"/photos/{new_photo}/content", "b", expected=404)
        await call(api, "POST", "/profile/submit", "a")
        await call(api, "POST", f"/admin/profiles/{profile_a}/review", "staff", json={"decision": "approve"})
        published = await call(api, "GET", f"/profiles/{profile_a}", "b")
        assert {photo["id"] for photo in published["photos"]} == {photo_a, new_photo}
        await call(api, "GET", f"/photos/{new_photo}/content", "b")
        await call(api, "DELETE", f"/photos/{new_photo}", "a")
        await call(api, "GET", f"/photos/{new_photo}/content", "b", expected=404)
        after_deletion = await call(api, "GET", f"/profiles/{profile_a}", "b")
        assert [photo["id"] for photo in after_deletion["photos"]] == [photo_a]
    exercise(engine, run)
