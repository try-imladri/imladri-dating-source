"""Live/period parity during an actual interrupted Your Type erasure."""
import json
from uuid import uuid4

from fastapi import HTTPException
import pytest

from backend import answer_history, trends, your_type as yt
from backend.quiz import default_quiz
from backend.tests.test_admin_answer_history import run_case
from backend.tests.test_your_type import action, manifest, member


def test_interrupted_your_type_erasure_is_excluded_from_live_and_period_reports(monkeypatch, tmp_path):
    bank = tmp_path / "catalog.v1.json"
    bank.write_text(json.dumps(manifest()), encoding="utf8")
    monkeypatch.setattr(yt, "MANIFEST", bank)
    yt._load_catalog.cache_clear()

    async def scenario(db):
        for uid in ("owner", "survivor"):
            await member(db, uid, role="member")
            await db.users.update_one({"user_id": uid}, {"$set": {"env": "preview",
                "email": uid + "@synthetic.invalid", "about.demographics.gender": "Man"}})
        async def start(uid):
            return await yt.start(db, uid, yt.Start(expected_member=uid, request_id=str(uuid4()),
                audience="women", sexuality="straight", viewer_gender="Man", adult_confirmed=True))
        removed, survivor = await start("owner"), await start("survivor")
        face = removed["cards"][0]["face_id"]
        removed = await yt.act(db, "owner", removed["id"], action(removed, "like", face=face, views=[face]))
        eligible = {"env": "preview", "role": "member", "closing": {"$ne": True}}
        assert (await trends.live_activity(db, eligible, "your_type"))["last24"]["your_type"] == 2
        original = answer_history._tombstone
        async def outage(*args, **kwargs):
            raise ConnectionError("Synthetic redaction storage outage")
        monkeypatch.setattr(answer_history, "_tombstone", outage)
        with pytest.raises(HTTPException) as interrupted:
            await yt.erase(db, "owner", removed["id"], removed["revision"])
        assert interrupted.value.status_code == 503
        pending = await db[yt.COLLECTION].find_one({"_id": removed["id"]})
        assert pending["deleted"] is True and pending["state"] == {} and pending[answer_history.REDACTIONS]
        with pytest.raises(HTTPException) as hidden:
            await yt.read(db, "owner", removed["id"])
        assert hidden.value.status_code == 404
        for selected in ("your_type", "all"):
            feed = await trends.live_activity(db, eligible, selected)
            assert feed["last24"]["your_type"] == 1
            assert [row["user"] for row in feed["items"] if row["type"] == "your_type"] == ["survivor@synthetic.invalid"]
            assert "owner@synthetic.invalid" not in json.dumps(feed)
            assert all(field not in json.dumps(feed) for field in ("straight", "viewer_gender", "preference_snapshot", "fixture-women"))
        assert (await trends.report(db, eligible, default_quiz(), 7))["totals"]["your_type_attempts"] == 1
        monkeypatch.setattr(answer_history, "_tombstone", original)
        await yt.erase(db, "owner", removed["id"], removed["revision"])
        assert await db[yt.COLLECTION].find_one({"_id": removed["id"]}) == {"_id": removed["id"], "deleted": True}
        feed = await trends.live_activity(db, eligible, "your_type")
        assert feed["last24"]["your_type"] == 1 and len(feed["items"]) == 1
        assert (await db[yt.COLLECTION].find_one({"_id": survivor["id"]}))["user_id"] == "survivor"
    run_case(scenario)
