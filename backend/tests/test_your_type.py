"""Synthetic-only Your Type domain tests, including actual PostgreSQL CAS races."""
import asyncio
from copy import deepcopy
import json
import time
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest

from backend import answer_history, your_type as yt, your_type_model as model
from backend.tests.test_admin_answer_history import run_case


def manifest():
    return {"schema_version": 1, "edition": "fixture-original-adults-v1",
        "feature_names": ["short", "long", "smile", "neutral"],
        "feature_groups": [{"id": "hair", "label": "Hair", "values": ["Short", "Long"], "indices": [0, 1]},
                           {"id": "expression", "label": "Expression", "values": ["Smile", "Neutral"], "indices": [2, 3]}],
        "assets": [{"id": f"fixture-{audience}-{i:03}", "audience": audience,
            "url": "/your-type/plates/fixture.png", "features": [i % 2, 1-i % 2, (i//2) % 2, 1-(i//2) % 2],
            "feature_masks": [True] * 4, "adult_reviewed": True, "active": True,
            "authored_age_band": "28-39"} for audience in ("men", "women") for i in range(40)]}


@pytest.fixture(autouse=True)
def original_bank(tmp_path, monkeypatch):
    path = tmp_path / "catalog.v1.json"
    path.write_text(json.dumps(manifest()), encoding="utf8")
    monkeypatch.setattr(yt, "MANIFEST", path)
    yt._load_catalog.cache_clear()


async def member(db, uid="owner", *, role="admin", age="30"):
    await db[yt.COLLECTION].create_indexes(yt.INDEXES[yt.COLLECTION])
    await db.users.insert_one({"user_id": uid, "role": role, "about": {"demographics": {"age": age}},
        "tier": "paid", "stripe_subscription_id": "fixture_subscription", "subscription_period_start": int(time.time()) - 100,
        "subscription_period_end": int(time.time()) + 3600})


async def start(db, uid="owner", request=None):
    return await yt.start(db, uid, yt.Start(expected_member=uid, request_id=request or str(uuid4()), audience="women", adult_confirmed=True))


def action(row, kind, *, face=None, views=None, action_id=None):
    return yt.Action(action_id=action_id or str(uuid4()), expected_revision=row["revision"], kind=kind,
        face_id=face, viewed_face_ids=views or [], dwell_ms={i: 500 for i in views or []})


def test_learner_updates_are_regularized_masked_and_deterministic():
    bank = manifest()
    assets = {a["id"]: a for a in bank["assets"]}
    original = model.initial(4)
    choices = [{"face_id": "fixture-women-001", "choice": "like", "prediction": .5},
               {"face_id": "fixture-women-000", "choice": "pass", "prediction": .5}]
    trained = model.train(original, choices * 20, assets, bank["feature_groups"])
    assert original == model.initial(4)
    assert trained == model.train(original, choices * 20, assets, bank["feature_groups"])
    assert model.predict(trained, assets["fixture-women-001"], bank["feature_groups"])["score"] > model.predict(trained, assets["fixture-women-000"], bank["feature_groups"])["score"]
    assert all(abs(w) <= 8 for w in trained["weights"])
    unknown = {"features": [1, 1, 1, 1], "feature_masks": [False]*4}
    assert model.vector(unknown, bank["feature_groups"]) == [1, 0, 0, 0, 0]


def test_prospective_metric_uses_frozen_scores_and_never_invents_unlabeled_pairs():
    choices = [{"face_id": "a", "choice": "like", "prediction": .2},
               {"face_id": "b", "choice": "pass", "prediction": .8},
               {"face_id": "c", "choice": "viewed_pass", "prediction": .2}]
    result = model.prospective(choices)
    assert result == {"pairs": 2, "correct": 0, "ties": 1, "explicit_pairs": 1, "viewed_pairs": 1}
    assert model.prospective(choices[:1])["pairs"] == 0
    assert model.prospective([choices[0], {**choices[1], "face_id": "a"}])["pairs"] == 0


def test_model_sampler_includes_exploration_and_adaptation():
    bank = manifest()
    candidates = bank["assets"][:20]
    a = model.sample(model.initial(4), candidates, 12, "same", bank["feature_groups"])
    assert a == model.sample(model.initial(4), candidates, 12, "same", bank["feature_groups"])
    assert len({i["face_id"] for i in a}) == 12
    assert {i["reason"] for i in a} == {"exploration", "uncertainty", "preference", "diversity"}


def test_single_slot_sampler_uses_existing_crowd_for_actual_diversity():
    known = {"id": "known", "features": [1, 0], "feature_masks": [True, True]}
    similar = {"id": "similar", "features": [1, 0], "feature_masks": [True, True]}
    different = {"id": "different", "features": [0, 1], "feature_masks": [True, True]}
    chosen = model.sample(model.initial(2), [similar, different], 1, "seed", start_index=3, existing=[known])
    assert chosen[0]["face_id"] == "different" and chosen[0]["reason"] == "diversity"


def test_original_manifest_is_versioned_and_rejects_unreviewed_or_unknown_vectors(tmp_path, monkeypatch):
    bank = yt.catalog()
    assert bank["sha256"] and len(bank["by_id"]) == 80
    raw = manifest()
    raw["assets"][0]["adult_reviewed"] = False
    path = tmp_path / "unreviewed.json"
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(yt, "MANIFEST", path)
    with pytest.raises(HTTPException) as exc:
        yt.catalog()
    assert exc.value.status_code == 503


def test_session_create_idempotency_ownership_subscription_adult_and_zero_credits():
    async def go(db):
        await member(db)
        await member(db, "partner")
        request = str(uuid4())
        a = await start(db, request=request)
        b = await start(db, request=request)
        assert a["id"] == b["id"] and b["duplicate"]
        assert len(a["cards"]) == len(a["replacement_queue"]) == 12
        assert len({i["face_id"] for i in a["cards"] + a["replacement_queue"]}) == 24
        with pytest.raises(HTTPException) as exc:
            await yt.read(db, "partner", a["id"])
        assert exc.value.status_code == 404
        await db.users.update_one({"user_id": "owner"}, {"$set": {"role": "member", "tier": "unsubscribed"}})
        with pytest.raises(HTTPException) as exc:
            await start(db)
        assert exc.value.status_code == 402
        await db.users.update_one({"user_id": "owner"}, {"$set": {"role": "admin", "about.demographics.age": "17"}})
        with pytest.raises(HTTPException) as exc:
            await start(db)
        assert exc.value.status_code == 403
        assert not (await db.users.find_one({"user_id": "owner"})).get("paid_reply_cycles")
        assert await db.ai_request_usage.count_documents({}) == 0
    run_case(go)


def test_like_replaces_the_same_slot_with_preissued_queue_and_retry_is_exactly_once():
    async def go(db):
        await member(db)
        row = await start(db)
        face = row["cards"][3]["face_id"]
        command = action(row, "like", face=face, views=[face])
        result = await yt.act(db, "owner", row["id"], command)
        assert result["cards"][3]["face_id"] == row["replacement_queue"][0]["face_id"]
        assert result["revision"] == 1
        retry = await yt.act(db, "owner", row["id"], command)
        assert retry["duplicate"] and retry["accepted_revision"] == 1
        source = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        assert len(source["state"]["choices"]) == 1
        assert await db.answer_history.count_documents({"domain": "your_type", "kind": "your_type.like"}) == 1
        modified = command.model_copy(update={"kind": "pass"})
        with pytest.raises(HTTPException) as exc:
            await yt.act(db, "owner", row["id"], modified)
        assert exc.value.status_code == 409
    run_case(go)


def test_concurrent_stale_actions_one_wins_and_duplicate_race_one_observation():
    async def go(db):
        await member(db)
        row = await start(db)
        face = row["cards"][0]["face_id"]
        commands = [action(row, "like", face=face, views=[face]) for _ in range(2)]
        outcomes = await asyncio.gather(*(yt.act(db, "owner", row["id"], c) for c in commands), return_exceptions=True)
        assert sum(isinstance(o, dict) for o in outcomes) == 1
        assert sum(isinstance(o, HTTPException) and o.status_code == 409 for o in outcomes) == 1
        row = await yt.read(db, "owner", row["id"])
        face = row["cards"][0]["face_id"]
        command = action(row, "pass", face=face, views=[face])
        outcomes = await asyncio.gather(*(yt.act(db, "owner", row["id"], command) for _ in range(2)))
        assert all(o["revision"] == 2 for o in outcomes)
        assert await db.answer_history.count_documents({"id": yt.event_id(row["id"], command.action_id)}) == 1
    run_case(go)


def test_explicit_next_only_weak_labels_declared_visible_views_and_undo_restores_crowd_one():
    async def go(db):
        await member(db)
        row = await start(db)
        before = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        face = row["cards"][0]["face_id"]
        row = await yt.act(db, "owner", row["id"], action(row, "next_crowd", views=[face]))
        saved = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        assert row["crowd"] == 2 and row["completed_crowds"] == 1
        assert saved["state"]["model"]["samples"] == 1 and saved["state"]["model"]["viewed_passes"] == 1
        event = await db.answer_history.find_one({"kind": "your_type.next_crowd"})
        assert len(event["after"]["effects"]["skipped"]) == 11
        assert event["after"]["effects"]["choices"][0]["weight"] == .35
        restored = await yt.act(db, "owner", row["id"], action(row, "undo"))
        current = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        assert restored["crowd"] == 1 and restored["completed_crowds"] == 0
        assert current["state"] == before["state"]
        assert await db.answer_history.count_documents({"domain": "your_type"}) == 3
    run_case(go)


def test_unseen_reserved_faces_cannot_be_labeled_and_pause_finish_do_not_make_negatives():
    async def go(db):
        await member(db)
        row = await start(db)
        unseen = row["replacement_queue"][0]["face_id"]
        with pytest.raises(HTTPException) as exc:
            await yt.act(db, "owner", row["id"], action(row, "like", face=unseen, views=[unseen]))
        assert exc.value.status_code == 422
        row = await yt.act(db, "owner", row["id"], action(row, "pause", views=[row["cards"][0]["face_id"]]))
        saved = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        assert saved["state"]["model"]["samples"] == 0 and not saved["state"]["choices"]
        with pytest.raises(HTTPException):
            await yt.act(db, "owner", row["id"], action(row, "next_crowd"))
        row = await yt.act(db, "owner", row["id"], action(row, "resume"))
        with pytest.raises(HTTPException):
            await yt.act(db, "owner", row["id"], action(row, "finish"))
        with pytest.raises(HTTPException):
            await yt.results(db, "owner", row["id"])
    run_case(go)


def test_twenty_empty_crowds_report_insufficient_signal_and_retake_immediate():
    async def go(db):
        await member(db)
        row = await start(db)
        for _ in range(20):
            row = await yt.act(db, "owner", row["id"], action(row, "next_crowd"))
        assert not row["results_available"] and row["likes_remaining"] == 30
        with pytest.raises(HTTPException) as exc:
            await yt.results(db, "owner", row["id"])
        assert exc.value.status_code == 409
        state = (await db[yt.COLLECTION].find_one({"_id": row["id"]}))["state"]
        report = model.results(state["models_by_audience"]["women"], yt.audience_assets(yt.catalog(), "women"),
                               yt.catalog()["feature_groups"], state["prospective_by_audience"]["women"], 20)
        assert report["signal"] == "insufficient_signal" and report["rankings"] == [] and report["prospective_pairwise"]["accuracy"] is None
        fresh = await yt.start(db, "owner", yt.Start(expected_member="owner", request_id=str(uuid4()), audience="women", adult_confirmed=True, retake_of=row["id"]))
        assert fresh["id"] != row["id"] and fresh["completed_crowds"] == 0
        assert (await yt.read(db, "owner", row["id"]))["completed_crowds"] == 20
    run_case(go)


def test_journal_outage_retains_accepted_actions_and_undo_works_without_projection(monkeypatch):
    async def go(db):
        await member(db)
        row = await start(db)
        real = answer_history.flush
        async def outage(*args, **kwargs):
            return {"failed": True, "archived": 0, "redacted": 0}
        monkeypatch.setattr(answer_history, "flush", outage)
        face = row["cards"][0]["face_id"]
        row = await yt.act(db, "owner", row["id"], action(row, "like", face=face, views=[face]))
        assert (await db[yt.COLLECTION].find_one({"_id": row["id"]}))[answer_history.PENDING]
        row = await yt.act(db, "owner", row["id"], action(row, "undo"))
        assert row["cards"][0]["face_id"] == face
        monkeypatch.setattr(answer_history, "flush", real)
        await real(db, yt.COLLECTION, {"_id": row["id"]}, strict=True)
        assert await db.answer_history.count_documents({"domain": "your_type"}) == 3
    run_case(go)


def test_deletion_erases_models_and_historical_payloads_and_retains_opaque_retry_fence():
    async def go(db):
        await member(db)
        request = str(uuid4())
        row = await start(db, request=request)
        face = row["cards"][0]["face_id"]
        row = await yt.act(db, "owner", row["id"], action(row, "like", face=face, views=[face]))
        await db.your_type_profiles.insert_one({"user_id": "owner", "session_id": row["id"], "secret": "derived"})
        await yt.erase(db, "owner", row["id"], row["revision"])
        assert await db[yt.COLLECTION].find_one({"_id": row["id"]}) == {"_id": row["id"], "deleted": True}
        assert await db.your_type_profiles.count_documents({}) == 0
        events = await db.answer_history.find({"purged": True}).to_list(100)
        assert len(events) == 2 and all(set(e) == {"_id", "id", "purged", "purged_at"} for e in events)
        with pytest.raises(HTTPException) as exc:
            await start(db, request=request)
        assert exc.value.status_code == 410
    run_case(go)


def test_rejected_stale_delete_preserves_valid_history_and_export_strips_internal_metadata():
    async def go(db):
        await member(db)
        row = await start(db)
        face = row["cards"][0]["face_id"]
        row = await yt.act(db, "owner", row["id"], action(row, "like", face=face, views=[face]))
        with pytest.raises(HTTPException) as exc:
            await yt.erase(db, "owner", row["id"], 0)
        assert exc.value.status_code == 409
        assert await db.answer_history.count_documents({"domain": "your_type"}) == 2
        exported = await yt.own_export(db, "owner")
        assert len(exported) == 1 and not set(answer_history.FIELDS).intersection(exported[0])
        assert exported[0]["state"]["choices"][0]["face_id"] == face
    run_case(go)


def test_edition_change_fails_closed_and_no_results_leak_before_twenty():
    async def go(db):
        await member(db)
        row = await start(db)
        await db[yt.COLLECTION].update_one({"_id": row["id"]}, {"$set": {"asset_manifest_sha256": "earlier"}})
        with pytest.raises(HTTPException) as exc:
            await yt.read(db, "owner", row["id"])
        assert exc.value.status_code == 412
    run_case(go)


def test_twenty_trained_crowds_rank_all_eligible_faces_and_profile_tracks_revision():
    async def go(db):
        await member(db)
        row = await start(db)
        assert not (await yt.profile(db, "owner"))["available"]
        for i in range(20):
            chosen, passed = row["cards"][0]["face_id"], row["cards"][1]["face_id"]
            row = await yt.act(db, "owner", row["id"], action(row, "like", face=chosen, views=[chosen]))
            chosen = row["cards"][2]["face_id"]
            row = await yt.act(db, "owner", row["id"], action(row, "like", face=chosen, views=[chosen]))
            row = await yt.act(db, "owner", row["id"], action(row, "next_crowd", views=[passed]))
        report = await yt.results(db, "owner", row["id"])
        assert report["signal"] == "developing" and len(report["rankings"]) == 40
        assert all(item["url"].startswith("/your-type/") for item in report["rankings"])
        assert report["picked_photos"] and len(report["feature_preferences"]) == 2
        assert "weights" not in json.dumps(report) and "coefficient" not in json.dumps(report)
        assert report["prospective_pairwise"]["pairs"] > 0
        profile = await yt.profile(db, "owner")
        assert profile["source_revision"] == row["revision"] and profile["available"]
        row = await yt.act(db, "owner", row["id"], action(row, "next_crowd"))
        assert (await yt.profile(db, "owner"))["source_revision"] == row["revision"]
    run_case(go)


def test_unique_session_slots_bound_concurrent_new_attempts(monkeypatch):
    monkeypatch.setattr(yt, "MAX_SESSIONS", 2)
    async def go(db):
        await member(db)
        outcomes = await asyncio.gather(*(start(db) for _ in range(5)), return_exceptions=True)
        assert sum(isinstance(result, dict) for result in outcomes) == 2
        assert all(isinstance(result, dict) or isinstance(result, HTTPException) and result.status_code == 409 for result in outcomes)
        assert await db[yt.COLLECTION].count_documents({"user_id": "owner"}) == 2
        first = next(result for result in outcomes if isinstance(result, dict))
        await yt.erase(db, "owner", first["id"], first["revision"])
        assert (await start(db))["revision"] == 0
    run_case(go)


def test_age_correction_during_context_read_cannot_accept_or_recreate_answers(monkeypatch):
    async def go(db):
        await member(db)
        row = await start(db)
        original = yt.answer_context.for_member
        async def corrected(database, uid):
            context = await original(database, uid)
            await database.users.update_one({"user_id": uid}, {"$set": {"about.demographics.age": "17"}})
            await yt.clear_member(database, uid)
            return context
        monkeypatch.setattr(yt.answer_context, "for_member", corrected)
        face = row["cards"][0]["face_id"]
        with pytest.raises(HTTPException) as exc:
            await yt.act(db, "owner", row["id"], action(row, "like", face=face, views=[face]))
        assert exc.value.status_code == 403
        assert await db.answer_history.count_documents({"kind": "your_type.like"}) == 0
        assert await db[yt.COLLECTION].find_one({"_id": row["id"]}) == {"_id": row["id"], "deleted": True}
    run_case(go)


def test_deletion_outage_closes_writer_and_recovers_without_revealing_private_state(monkeypatch):
    async def go(db):
        await member(db)
        row = await start(db)
        original = answer_history._tombstone
        async def outage(*args, **kwargs):
            raise ConnectionError("normal local storage outage")
        monkeypatch.setattr(answer_history, "_tombstone", outage)
        with pytest.raises(HTTPException) as exc:
            await yt.erase(db, "owner", row["id"], 0)
        assert exc.value.status_code == 503
        private = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        assert private["deleted"] and private["state"] == {} and private[answer_history.REDACTIONS]
        with pytest.raises(HTTPException) as exc:
            await yt.read(db, "owner", row["id"])
        assert exc.value.status_code == 404
        monkeypatch.setattr(answer_history, "_tombstone", original)
        await yt.erase(db, "owner", row["id"], 0)
        assert await db[yt.COLLECTION].find_one({"_id": row["id"]}) == {"_id": row["id"], "deleted": True}
    run_case(go)


def test_session_document_stays_bounded_and_old_uuid_retry_survives_receipt_window():
    async def go(db):
        from bson import BSON
        await member(db)
        row = await start(db)
        first = action(row, "next_crowd")
        row = await yt.act(db, "owner", row["id"], first)
        for _ in range(66):
            row = await yt.act(db, "owner", row["id"], action(row, "next_crowd"))
        source = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        assert len(source["recent_receipts"]) == 64
        assert len(BSON.encode(source)) < 32 * 1024
        assert (await yt.act(db, "owner", row["id"], first))["accepted_revision"] == 1
        assert (await yt.read(db, "owner", row["id"]))["revision"] == 67
    run_case(go)


def test_real_router_uses_member_ownership_and_no_store_for_mutations():
    async def go(db):
        import httpx
        from fastapi import FastAPI
        await member(db)
        async def signed_in():
            return {"user_id": "owner"}
        app = FastAPI()
        app.include_router(yt.make_router(lambda: db, signed_in, signed_in), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            response = await client.get("/api/your-type")
            assert response.status_code == 200 and response.json()["message_credits_per_action"] == 0
            response = await client.post("/api/your-type/sessions", json={"expected_member": "owner", "request_id": str(uuid4()), "audience": "women", "adult_confirmed": True})
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            row = response.json()
            face = row["cards"][0]["face_id"]
            command = action(row, "like", face=face, views=[face])
            response = await client.post(f'/api/your-type/sessions/{row["id"]}/actions', json=command.model_dump())
            assert response.status_code == 200 and response.json()["revision"] == 1
            assert (await client.get(f'/api/your-type/sessions/{row["id"]}/results')).status_code == 409
            assert (await client.get("/api/your-type/history")).json()["sessions"][0]["id"] == row["id"]
            history = (await client.get(f'/api/your-type/sessions/{row["id"]}/history?limit=1')).json()
            assert len(history["items"]) == 1 and history["next"]
            assert "before_state" not in json.dumps(history) and "weights" not in json.dumps(history)
            assert (await client.get(f'/api/your-type/sessions/{row["id"]}/history?before={history["next"]}')).json()["items"]
            assert (await client.delete(f'/api/your-type/sessions/{row["id"]}?expected_revision=1')).json() == {"removed": True}
    run_case(go)


def test_saved_adult_age_starts_without_repeated_attestation_and_records_exact_request():
    async def go(db):
        import httpx
        from fastapi import FastAPI
        await member(db, age=18)
        await db.users.update_one({"user_id": "owner"}, {"$set": {"about.demographics.gender": "Man"}})
        async def signed_in():
            return {"user_id": "owner"}
        app = FastAPI()
        app.include_router(yt.make_router(lambda: db, signed_in, signed_in), prefix="/api")
        payload = {"expected_member": "owner", "request_id": str(uuid4()), "audience": "women",
                   "sexuality": "straight", "viewer_gender": "Man"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            boot = await client.get("/api/your-type")
            assert boot.status_code == 200 and boot.headers["cache-control"] == "no-store"
            assert boot.json()["profile"] == {"adult_eligible": True, "gender": "Man", "sexuality": None}
            assert "age" not in boot.json()["profile"]
            created = await client.post("/api/your-type/sessions", json=payload)
            assert created.status_code == 200
            retried = await client.post("/api/your-type/sessions", json=payload)
            assert retried.status_code == 200 and retried.json()["duplicate"]
            assert retried.json()["id"] == created.json()["id"]
            # A stale screen cannot use its prior successful bootstrap to bypass
            # the current saved age, even when it sends the old checkbox field.
            await db.users.update_one({"user_id": "owner"}, {"$set": {"about.demographics.age": 17}})
            changed = {**payload, "request_id": str(uuid4()), "adult_confirmed": True}
            assert (await client.post("/api/your-type/sessions", json=changed)).status_code == 403
        event = await db.answer_history.find_one({"source_id": created.json()["id"], "kind": "your_type.session_started"})
        assert "adult_confirmed" not in event["after"]["request"]
        assert await db[yt.COLLECTION].count_documents({"user_id": "owner"}) == 1
        assert await db.ai_request_usage.count_documents({}) == 0
    run_case(go)


@pytest.mark.parametrize("age", [None, "", 17, "17", True, "unknown", 121])
def test_saved_profile_age_is_required_even_without_the_old_checkbox(age):
    async def go(db):
        await member(db, age=age)
        body = yt.Start(expected_member="owner", request_id=str(uuid4()), audience="women")
        with pytest.raises(HTTPException) as exc:
            await yt.start(db, "owner", body)
        assert exc.value.status_code == 403
        assert await db[yt.COLLECTION].count_documents({}) == 0
        assert await db.answer_history.count_documents({}) == 0
    run_case(go)


def test_bootstrap_includes_owned_history_page_and_cursor_without_private_choices():
    async def go(db):
        import httpx
        from fastapi import FastAPI
        await member(db)
        for i in range(21):
            sid = str(UUID(int=i + 1))
            await db[yt.COLLECTION].insert_one({"_id": sid, "id": sid, "user_id": "owner", "audience": "women",
                "status": "active", "revision": i, "created_at": "2026-11-18T10:00:00+00:00",
                "updated_at": "2026-11-18T10:00:00+00:00", "asset_edition": "fixture", "model_edition": "fixture",
                "state": {"completed_crowds": i, "choices": [{"private": True}], "models_by_audience": {"secret": True}}})
        await db[yt.COLLECTION].insert_one({"_id": str(uuid4()), "id": str(uuid4()), "user_id": "another",
            "created_at": "2026-11-19T10:00:00+00:00", "state": {"choices": ["private"]}})
        async def signed_in():
            return {"user_id": "owner"}
        app = FastAPI()
        app.include_router(yt.make_router(lambda: db, signed_in, signed_in), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            bootstrap = (await client.get("/api/your-type")).json()
            history = (await client.get("/api/your-type/history")).json()
            assert bootstrap["sessions"] == history["sessions"]
            assert bootstrap["history_next"] == history["next"]
            assert len(bootstrap["sessions"]) == 20
            remaining = (await client.get("/api/your-type/history", params={"before": bootstrap["history_next"]})).json()
            assert len(remaining["sessions"]) == 1 and remaining["next"] is None
            encoded = json.dumps(bootstrap)
            assert '"choices"' not in encoded and '"models_by_audience"' not in encoded
            assert all(set(row["state"]) == {"completed_crowds"} for row in bootstrap["sessions"])
    run_case(go)


def make_batch(row, *, include_next=True):
    # Optimistic UI knows the initial ordered reserve before any request.
    first = row["cards"][0]["face_id"]
    replacement = row["replacement_queue"][0]["face_id"]
    actions = [{"action_id": str(uuid4()), "kind": "like", "face_id": first, "viewed_face_ids": [first]},
               {"action_id": str(uuid4()), "kind": "like", "face_id": replacement, "viewed_face_ids": [replacement]}]
    if include_next:
        face = row["cards"][1]["face_id"]
        actions.append({"action_id": str(uuid4()), "kind": "next_crowd", "viewed_face_ids": [face]})
    return yt.Batch(batch_id=str(uuid4()), expected_revision=row["revision"], actions=actions)


def test_batch_single_cas_retains_each_action_revision_and_exact_undo_commit(monkeypatch):
    async def go(db):
        await member(db)
        row = await start(db)
        command = make_batch(row)
        result = await yt.act_batch(db, "owner", row["id"], command)
        assert result["revision"] == 3 and result["crowd"] == 2 and result["committed_likes"] == 2
        events = await db.answer_history.find({"domain": "your_type", "kind": {"$in": ["your_type.like", "your_type.next_crowd"]}}).sort("revision", 1).to_list(20)
        assert [e["revision"] for e in events] == [1, 2, 3]
        assert len({e["source_revision"] for e in events}) == 1
        assert [e["after"]["request"]["expected_revision"] for e in events] == [0, 1, 2]
        assert all(e["after"]["batch_id"] == command.batch_id for e in events)
        undone = await yt.act(db, "owner", row["id"], action(result, "undo"))
        assert undone["crowd"] == 1 and undone["completed_crowds"] == 0 and undone["likes"] == 2 and undone["committed_likes"] == 0
        source = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        assert source["state"]["model"]["samples"] == 0
    run_case(go)


def test_batch_ack_loss_duplicate_and_body_conflict_never_repeats_votes():
    async def go(db):
        await member(db)
        row = await start(db)
        command = make_batch(row)
        await yt.act_batch(db, "owner", row["id"], command)  # Simulate lost HTTP acknowledgment.
        duplicate = await yt.act_batch(db, "owner", row["id"], command)
        assert duplicate["duplicate"] and duplicate["accepted_revision"] == 3
        assert await db.answer_history.count_documents({"kind": "your_type.like"}) == 2
        changed = command.model_copy(update={"actions": command.actions[:-1]})
        with pytest.raises(HTTPException) as exc:
            await yt.act_batch(db, "owner", row["id"], changed)
        assert exc.value.status_code == 409
        single = yt.Action(**command.actions[0].model_dump(), expected_revision=0)
        assert (await yt.act(db, "owner", row["id"], single))["accepted_revision"] == 1
    run_case(go)


def test_batch_invalid_later_action_has_no_partial_mutation_and_stale_race_one_wins():
    async def go(db):
        await member(db)
        row = await start(db)
        good = make_batch(row)
        invalid = deepcopy(good)
        invalid.actions[1].face_id = row["cards"][0]["face_id"]
        invalid.actions[1].viewed_face_ids = [invalid.actions[1].face_id]
        with pytest.raises(HTTPException) as exc:
            await yt.act_batch(db, "owner", row["id"], invalid)
        assert exc.value.status_code == 422
        assert (await yt.read(db, "owner", row["id"]))["revision"] == 0
        assert await db.answer_history.count_documents({"domain": "your_type"}) == 1
        other = make_batch(row)
        outcomes = await asyncio.gather(yt.act_batch(db, "owner", row["id"], good), yt.act_batch(db, "owner", row["id"], other), return_exceptions=True)
        assert sum(isinstance(o, dict) for o in outcomes) == 1
        assert sum(isinstance(o, HTTPException) and o.status_code == 409 for o in outcomes) == 1
        assert (await yt.read(db, "owner", row["id"]))["revision"] == 3
    run_case(go)


def test_concurrent_identical_batch_and_pending_archive_outage_are_exactly_once(monkeypatch):
    async def go(db):
        await member(db)
        row = await start(db)
        original = answer_history.flush
        async def outage(*args, **kwargs):
            return {"failed": True, "archived": 0, "redacted": 0}
        monkeypatch.setattr(answer_history, "flush", outage)
        command = make_batch(row)
        outcomes = await asyncio.gather(*(yt.act_batch(db, "owner", row["id"], command) for _ in range(2)))
        assert all(o["revision"] == 3 for o in outcomes)
        source = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        assert len(source[answer_history.PENDING]) == 4  # Three actions and one batch receipt.
        monkeypatch.setattr(answer_history, "flush", original)
        await original(db, yt.COLLECTION, {"_id": row["id"]}, strict=True)
        assert await db.answer_history.count_documents({"domain": "your_type"}) == 5
    run_case(go)


def test_private_rpc_committed_but_uncached_result_reconciles_batch_without_new_actions(monkeypatch):
    async def go(db):
        from backend.postgres_protocol import GatewayCommittedResultUnavailable
        await member(db)
        row = await start(db)
        cls = type(db[yt.COLLECTION])
        original = cls.find_one_and_update
        raised = []
        async def accepted_then_ack_lost(self, *args, **kwargs):
            value = await original(self, *args, **kwargs)
            if self.name == yt.COLLECTION and not raised:
                raised.append(True)
                raise GatewayCommittedResultUnavailable("Private result was deliberately not cached.")
            return value
        monkeypatch.setattr(cls, "find_one_and_update", accepted_then_ack_lost)
        result = await yt.act_batch(db, "owner", row["id"], make_batch(row))
        assert result["revision"] == 3 and result["duplicate"]
        assert await db.answer_history.count_documents({"kind": "your_type.like"}) == 2
    run_case(go)


@pytest.mark.parametrize("dimensions,audience", [(45, "women"), (192, "both")])
def test_full_640_face_state_and_each_history_observation_remain_bounded(dimensions, audience):
    async def go():
        from bson import BSON
        features = [f"f-{i}" for i in range(dimensions)]
        groups = [{"id": f"g{i}", "values": ["a", "b", "c"], "indices": [3*i, 3*i+1, 3*i+2]} for i in range(dimensions // 3)]
        assets = [{"id": f"yt-{group}-{i:04}", "audience": group, "features": [int(j % 3 == i % 3) for j in range(dimensions)],
                   "feature_masks": [True]*dimensions, "url": "/your-type/fixture.jpg"} for group in ("women", "men") for i in range(320)]
        bank = {"feature_names": features, "feature_groups": groups, "assets": assets,
                "by_id": {a["id"]: a for a in assets}, "position": {a["id"]: i for i, a in enumerate(assets)}, "edition": "fullfixture", "sha256": "a"*64}
        sid = str(uuid4())
        state = yt.new_state(bank, audience)
        yt.fill(state, bank, audience, sid)
        state["seen"] = list(range(640))
        row = {"id": sid, "user_id": "owner", "audience": audience, "attempt_id": sid, "revision": 0,
               "status": "active", "state": state, "model_edition": model.EDITION, "asset_edition": bank["edition"], "asset_manifest_sha256": bank["sha256"]}
        for i in range(64):
            face = row["state"]["cards"][0]["face_id"]
            command = action({"revision": row["revision"]}, "like", face=face, views=[face])
            before, after, status, effects = await yt.reduce_action(None, row, bank, command)
            event = yt.action_event(row, {}, command, before, after, effects, {})
            assert len(BSON.encode(event)) <= answer_history.MAX_EVENT_BYTES
            assert len(BSON.encode(after)) <= yt.MAX_STATE_BYTES
            row.update(state=after, revision=row["revision"]+1, status=status)
        command = action({"revision": row["revision"]}, "next_crowd", views=[c["face_id"] for c in row["state"]["cards"]])
        before, after, status, effects = await yt.reduce_action(None, row, bank, command)
        assert len(BSON.encode(yt.action_event(row, {}, command, before, after, effects, {}))) <= answer_history.MAX_EVENT_BYTES
        assert after["model"]["samples"] == 76
    asyncio.run(go())


def test_actual_paid_member_taps_preserve_the_existing_message_credit_meter():
    async def go(db):
        from backend.free_plan import paid_cycle
        await member(db, role="member")
        user = await db.users.find_one({"user_id": "owner"})
        key = paid_cycle(user)
        await db.users.update_one({"user_id": "owner"}, {"$set": {"paid_reply_cycles." + key: {"used": 7, "claimed": 7}, "topup_balance": 9}})
        before = await db.users.find_one({"user_id": "owner"})
        row = await start(db)
        await yt.act_batch(db, "owner", row["id"], make_batch(row))
        after = await db.users.find_one({"user_id": "owner"})
        assert after["paid_reply_cycles"] == before["paid_reply_cycles"] and after["topup_balance"] == 9
        assert not after.get("free_reply_requests")
        assert await db.ai_request_usage.count_documents({}) == 0
    run_case(go)


def test_packed_history_is_lossless_bounded_and_owner_presentation_expands_it():
    from bson import BSON, Int64
    before = {"weights": [0.1, -0.0, -0.25], "version": Int64(4), "nested": {"x": None}}
    after = {**before, "weights": [0.2, 0.0, -0.1]}
    packed = yt.pack_states(before, after)
    restored_before, restored_after = yt.event_states({"states_packed": packed})
    assert BSON.encode(restored_before) == BSON.encode(before) and BSON.encode(restored_after) == BSON.encode(after)
    event = {"domain": "your_type", "after": {"states_packed": packed, "effects": {"issued": []}}}
    expanded = yt.expand_event(event)
    assert expanded["after"]["before_state"] == before and expanded["after"]["after_state"] == after
    assert "states_packed" not in expanded["after"] and "states_packed" in event["after"]
    json.dumps(expanded)
    assert yt.event_states({"before_state": before, "after_state": after}) == (before, after)
    truncated = {**packed, "payload": packed["payload"][:-1]}
    with pytest.raises(HTTPException) as exc:
        yt.event_states({"states_packed": truncated})
    assert exc.value.status_code == 503
    with pytest.raises(HTTPException) as exc:
        yt.pack_states({"oversized": "x" * yt.MAX_PACKED_RAW_BYTES}, {})
    assert exc.value.status_code == 413


def test_packed_model_snapshots_reduce_full_feature_history_without_pruning_any_state():
    from bson import BSON
    before = {"model": model.initial(45), "seen": list(range(640)), "choices": [
        {"face_id": f"yt-w-{i:04}", "choice": "like", "prediction": .5, "catalog_cycle": 1} for i in range(64)]}
    after = deepcopy(before)
    after["model"]["weights"][1] = .173
    raw = BSON.encode({"before": before, "after": after})
    packed = yt.pack_states(before, after)
    assert len(BSON.encode(packed)) < len(raw) * .3
    a, b = yt.event_states({"states_packed": packed})
    assert BSON.encode(a) == BSON.encode(before) and BSON.encode(b) == BSON.encode(after)


def test_public_sprite_normalization_handles_both_valid_alias_shapes_without_eager_defaults():
    base = {"id": "fixture", "url": "/your-type/fixture.jpg", "features": [1, 0], "private_prompt": "not public"}
    direct = {**base, "sprite": {"x": 3, "y": 3, "width": 186, "height": 250, "source_width": 1536, "source_height": 1024}}
    alias = {**base, "sprite": {"rect": {"x": 3, "y": 3, "w": 186, "h": 250}, "sheet_width": 1536, "sheet_height": 1024}}
    assert yt.public_face(direct) == yt.public_face(alias)
    assert "features" not in yt.public_face(alias) and "private_prompt" not in yt.public_face(alias)


def test_constant_all_women_facial_hair_is_not_presented_as_a_preference():
    assets = {f"woman-{i}": {"id": f"woman-{i}", "features": [1, 0, 0, i % 2, 1-i % 2], "feature_masks": [True]*5} for i in range(80)}
    groups = [{"id": "facial_hair", "values": ["none", "stubble", "beard"], "indices": [0, 1, 2]},
              {"id": "smile", "values": ["smiling", "neutral"], "indices": [3, 4]}]
    choices = [{"face_id": f"woman-{i}", "choice": "like" if i % 2 else "pass", "prediction": .5} for i in range(80)]
    trained = model.train(model.initial(5), choices, assets, groups)
    # A strong incidental constant coefficient must not become a trait claim.
    trained["weights"][1] = 5.0
    report = model.results(trained, list(assets.values()), groups, model.prospective(choices), 20)
    facial = next(g for g in report["feature_preferences"] if g["id"] == "facial_hair")
    varied = next(g for g in report["feature_preferences"] if g["id"] == "smile")
    assert facial["variation_available"] is False and facial["observed_options"] == 1 and facial["importance"] == 0
    assert all(v["direction"] == "undetermined" and v["association_strength"] is None and v["standard_error"] is None for v in facial["values"])
    assert varied["variation_available"] and varied["qualified_options"] == 2 and varied["importance"] == 1


def test_partial_feature_masks_require_positive_and_known_negative_comparisons():
    trained = model.initial(3)
    trained.update(feature_option_counts=[6, 6, 0], feature_observations=[6, 12, 12])
    group = {"id": "fixture", "indices": [0, 1, 2]}
    evidence = model.group_evidence(trained, group)
    assert evidence["observed_options"] == 2 and evidence["qualified_options"] == 1
    assert not evidence["variation_available"] and evidence["eligible_indices"] == []


def test_actual_bank_display_labels_preserve_coarse_annotation_and_private_feature_boundary():
    path = yt.ROOT / 'backend/seed/your_type_catalog.json'
    bank = yt._load_catalog(str(path), path.stat().st_mtime_ns)
    audience = [a for a in bank['assets'] if a['audience'] == 'women'][:60]
    choices = [{'face_id': a['id'], 'choice': 'like' if i % 2 else 'pass', 'prediction': .5} for i, a in enumerate(audience)]
    trained = model.train(model.initial(len(bank['feature_names'])), choices, bank['by_id'], bank['feature_groups'])
    result = model.results(trained, audience, bank['feature_groups'], model.prospective(choices), 20)
    texture = next(g for g in result['feature_preferences'] if g['id'] == 'hair_texture')
    assert next(v for v in texture['values'] if v['value'] == 'braided')['label'] == 'Braids or loc styles'
    for group in result['feature_preferences']:
        original = next(g for g in bank['feature_groups'] if g['id'] == group['id'])
        assert all(v['label'] == original['value_labels'][v['value']] for v in group['values'])
    public = json.loads((yt.ROOT/'frontend/public/your-type/catalog.v1.json').read_text(encoding='utf8'))
    assert 'feature_names' not in public
    assert all(not {'features', 'feature_masks', 'generation_prompt'}.intersection(a) for a in public['assets'])


def test_start_and_retake_reject_switched_member_before_any_feature_or_context_write():
    async def go(db):
        import httpx
        from fastapi import FastAPI
        await member(db, "first")
        await member(db, "second")
        owned = await start(db, "first")
        before_users = await db.users.find({}).sort("user_id", 1).to_list(10)
        before_sessions = await db[yt.COLLECTION].find({}).to_list(10)
        before_events = await db.answer_history.find({}).sort("id", 1).to_list(100)
        body = yt.Start(expected_member="first", request_id=str(uuid4()), audience="women", adult_confirmed=True)
        with pytest.raises(HTTPException) as exc:
            await yt.start(db, "second", body)
        assert exc.value.status_code == 409
        async def switched():
            return {"user_id": "second"}
        app = FastAPI();app.include_router(yt.make_router(lambda: db, switched, switched), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            for retake in (None, owned["id"]):
                payload = {**body.model_dump(), "retake_of": retake}
                assert (await client.post("/api/your-type/sessions", json=payload)).status_code == 409
            missing = body.model_dump();del missing['expected_member']
            assert (await client.post("/api/your-type/sessions", json=missing)).status_code == 422
        assert await db.users.find({}).sort("user_id", 1).to_list(10) == before_users
        assert await db[yt.COLLECTION].find({}).to_list(10) == before_sessions
        assert await db.answer_history.find({}).sort("id", 1).to_list(100) == before_events
    run_case(go)


def test_cleared_age_expired_owner_can_list_metadata_then_delete_without_portraits_or_models():
    async def go(db):
        import httpx
        from fastapi import FastAPI
        await member(db, role="member")
        row = await start(db)
        await db.users.update_one({"user_id": "owner"}, {"$unset": {"about.demographics.age": ""},
            "$set": {"subscription_period_end": int(time.time())-1, "tier": "unsubscribed"}})
        async def owner():
            return {"user_id": "owner"}
        app = FastAPI();app.include_router(yt.make_router(lambda: db, owner, owner), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            listed = await client.get('/api/your-type/history')
            assert listed.status_code == 200 and listed.json()['sessions'][0]['id'] == row['id']
            item = listed.json()['sessions'][0]
            assert set(item.get('state', {})) == {'completed_crowds'}
            assert not {'cards', 'replacement_queue', 'weights', 'choices'}.intersection(item)
            assert (await client.get('/api/your-type/sessions/'+row['id'])).status_code == 403
            assert (await client.get('/api/your-type/sessions/'+row['id']+'/results')).status_code == 403
            assert (await client.get('/api/your-type/sessions/'+row['id']+'/history')).status_code == 403
            body = {'expected_member': 'owner', 'request_id': str(uuid4()), 'audience': 'women', 'adult_confirmed': True}
            assert (await client.post('/api/your-type/sessions', json=body)).status_code == 403
            assert (await client.delete('/api/your-type/sessions/'+row['id']+'?expected_revision=0')).json() == {'removed': True}
            assert (await client.get('/api/your-type/history')).json()['sessions'] == []
    run_case(go)


def test_full_member_erasure_handles_missing_and_malformed_revisions_without_touching_peer():
    async def go(db):
        from bson import BSON, ObjectId
        await member(db)
        await member(db, "peer")
        missing = object()
        own_rows = []
        events = []
        for index, value in enumerate((missing, None, "damaged", {"$ne": 0}, [1, 2], True)):
            row = {"_id": ObjectId(), "user_id": "owner", "private_payload": "erase me", "state": {"choices": ["private"]}}
            if value is not missing:
                row["revision"] = value
            await db[yt.COLLECTION].insert_one(deepcopy(row))
            event = answer_history.event(source=yt.COLLECTION, source_id=row["_id"], owner_id="owner",
                domain="your_type", question_id="session", after={"private_payload": "erase me"})
            await db[yt.COLLECTION].update_one({"_id": row["_id"]}, answer_history.append({}, event, row))
            if index % 2:
                await answer_history.flush(db, yt.COLLECTION, {"_id": row["_id"]}, strict=True)
            own_rows.append(row)
            events.append(event["id"])
        peer = {"_id": ObjectId(), "user_id": "peer", "revision": {"$ne": 0}, "state": {"choices": ["keep me"]}}
        await db[yt.COLLECTION].insert_one(deepcopy(peer))
        peer_event = answer_history.event(source=yt.COLLECTION, source_id=peer["_id"], owner_id="peer",
            domain="your_type", question_id="session", after={"private_payload": "keep me"})
        await db[yt.COLLECTION].update_one({"_id": peer["_id"]}, answer_history.append({}, peer_event, peer))
        await answer_history.flush(db, yt.COLLECTION, {"_id": peer["_id"]}, strict=True)
        before_peer = BSON.encode(await db[yt.COLLECTION].find_one({"_id": peer["_id"]}))
        before_peer_event = BSON.encode(await db.answer_history.find_one({"_id": peer_event["id"]}))
        await db.your_type_profiles.insert_many([{"user_id": "owner", "private_payload": "erase"}, {"user_id": "peer", "private_payload": "keep"}])
        await yt.clear_member(db, "owner")
        for row in own_rows:
            assert await db[yt.COLLECTION].find_one({"_id": row["_id"]}) == {"_id": row["_id"], "deleted": True}
        for identity in events:
            erased = await db.answer_history.find_one({"_id": identity})
            assert erased["purged"] is True and set(erased) == {"_id", "id", "purged", "purged_at"}
        assert await db.your_type_profiles.count_documents({"user_id": "owner"}) == 0
        assert await db.your_type_profiles.count_documents({"user_id": "peer"}) == 1
        assert BSON.encode(await db[yt.COLLECTION].find_one({"_id": peer["_id"]})) == before_peer
        assert BSON.encode(await db.answer_history.find_one({"_id": peer_event["id"]})) == before_peer_event
    run_case(go)


@pytest.mark.parametrize("sexuality,gender,audience", [
    ("straight", "Man", "women"), ("straight", "Woman", "men"),
    ("gay", "Man", "men"), ("gay", "Woman", "women"),
    ("lesbian", "Woman", "women"), ("bi", "Man", "both"), ("bi", "Woman", "both"),
    ("straight", "Non-binary", "both"), ("gay", "Another gender", "women"),
    ("straight", None, "men"), ("bi", None, "both"),
])
def test_explicit_preferences_use_saved_gender_and_preserve_chosen_fallback(sexuality, gender, audience):
    body = yt.Start(expected_member="owner", request_id=str(uuid4()), audience=audience,
                    sexuality=sexuality, viewer_gender=gender, adult_confirmed=True)
    person = {"about": {"demographics": {"gender": gender}}}
    assert yt.preference_snapshot(body, person) == {"version": "your-type-preferences-v1",
        "sexuality": sexuality, "viewer_gender": gender, "rated_audience": audience}


@pytest.mark.parametrize("sexuality,gender,audience,status", [
    ("straight", "Man", "men", 422), ("gay", "Woman", "men", 422),
    ("bi", "Man", "women", 422), ("lesbian", "Woman", "both", 422),
    (None, None, "both", 422), (None, "Man", "women", 422),
])
def test_contradictory_or_unlabeled_mixed_preferences_are_rejected(sexuality, gender, audience, status):
    body = yt.Start(expected_member="owner", request_id=str(uuid4()), audience=audience,
                    sexuality=sexuality, viewer_gender=gender, adult_confirmed=True)
    with pytest.raises(HTTPException) as exc:
        yt.preference_snapshot(body, {"about": {"demographics": {"gender": gender}}})
    assert exc.value.status_code == status


def test_preference_gender_cannot_be_asserted_in_place_of_saved_own_profile():
    body = yt.Start(expected_member="owner", request_id=str(uuid4()), audience="women",
                    sexuality="straight", viewer_gender="Man", adult_confirmed=True)
    with pytest.raises(HTTPException) as exc:
        yt.preference_snapshot(body, {"about": {"demographics": {"gender": "Woman"}}})
    assert exc.value.status_code == 409
    with pytest.raises(ValueError):
        yt.Start(expected_member="owner", request_id=str(uuid4()), audience="both",
                 sexuality="inferred", adult_confirmed=True)


def test_legacy_start_hash_and_unknown_orientation_are_exactly_preserved():
    original = {"expected_member": "owner", "request_id": str(uuid4()), "audience": "women",
                "adult_confirmed": True, "retake_of": None}
    body = yt.Start(**original)
    assert yt.start_request(body) == original
    assert yt.digest(yt.start_request(body)) == yt.digest(original)
    assert yt.preference_snapshot(body, {"about": {"demographics": {"gender": "Woman"}}}) is None


def test_both_crowds_balance_real_candidates_without_repeats_and_retain_sampling_reasons():
    bank = yt.catalog()
    state, repeated = yt.new_state(bank), yt.new_state(bank)
    yt.fill(state, bank, "both", "mixed-seed")
    yt.fill(repeated, bank, "both", "mixed-seed")
    assert state == repeated
    for key in ("cards", "replacement_queue"):
        genders = [bank["by_id"][card["face_id"]]["audience"] for card in state[key]]
        assert genders.count("men") == genders.count("women") == 6
        assert {card["reason"] for card in state[key]} == {"exploration", "uncertainty", "preference", "diversity"}
    assert len({card["face_id"] for card in state["cards"] + state["replacement_queue"]}) == 24
    for _ in range(4):
        yt.fill(state, bank, "both", "next-mixed", new_crowd=True)
        assert len({card["face_id"] for card in state["cards"] + state["replacement_queue"]}) == 24
    assert state["catalog_cycle"] >= 2


def test_actual_original_bank_mixed_audience_includes_all_640_and_balances_first_grid():
    path = yt.ROOT / "backend/seed/your_type_catalog.v1.json"
    bank = yt._load_catalog(str(path), path.stat().st_mtime_ns)
    assert len(yt.audience_assets(bank, "both")) == 640
    state = yt.new_state(bank)
    yt.fill(state, bank, "both", "actual-mixed-bank")
    assert sum(bank["by_id"][card["face_id"]]["audience"] == "men" for card in state["cards"]) == 6
    assert sum(bank["by_id"][card["face_id"]]["audience"] == "women" for card in state["cards"]) == 6


def test_mixed_attempt_snapshot_roundtrips_history_actions_results_and_legacy_retry():
    async def go(db):
        await member(db)
        await db.users.update_one({"user_id": "owner"}, {"$set": {"about.demographics.gender": "Woman"}})
        legacy = await start(db)
        legacy_event = await db.answer_history.find_one({"source_id": legacy["id"], "kind": "your_type.session_started"})
        assert legacy["preference_snapshot"] is None
        # A real earlier-version session did not have the newly added field.
        await db[yt.COLLECTION].update_one({"_id": legacy["id"]}, {"$unset": {"preference_snapshot": ""}})
        legacy_request = legacy_event["after"]["request"]
        assert "sexuality" not in legacy_request and "viewer_gender" not in legacy_request
        legacy_retry = await yt.start(db, "owner", yt.Start(**legacy_request))
        assert legacy_retry["duplicate"] and legacy_retry["id"] == legacy["id"] and legacy_retry["preference_snapshot"] is None
        body = yt.Start(expected_member="owner", request_id=str(uuid4()), audience="both", sexuality="bi",
                        viewer_gender="Woman", adult_confirmed=True)
        row = await yt.start(db, "owner", body)
        expected = {"version": "your-type-preferences-v1", "sexuality": "bi", "viewer_gender": "Woman", "rated_audience": "both"}
        assert row["preference_snapshot"] == expected and row["audience"] == "both"
        face = row["cards"][0]["id"]
        liked = await yt.act(db, "owner", row["id"], action(row, "like", face=face, views=[face]))
        assert liked["preference_snapshot"] == expected
        await db.users.update_one({"user_id": "owner"}, {"$set": {"about.demographics.gender": "Man"}})
        # Lost-ack replay retains the original snapshot even after profile edits.
        duplicate = await yt.start(db, "owner", body)
        assert duplicate["duplicate"] and duplicate["preference_snapshot"] == expected
        events = await db.answer_history.find({"source_id": row["id"], "owner_id": "owner"}).to_list(20)
        assert len(events) >= 2 and all(event["after"]["preference_snapshot"] == expected for event in events)
        source = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        for audience in ("men", "women"):
            assets = yt.audience_assets(yt.catalog(), audience)
            choices = [{"face_id": asset["id"], "audience": audience, "choice": "like" if i < 30 else "pass", "prediction": .5}
                       for i, asset in enumerate(assets)]
            source["state"]["models_by_audience"][audience] = model.train(source["state"]["models_by_audience"][audience], choices, yt.catalog()["by_id"], yt.catalog()["feature_groups"])
            source["state"]["completed_crowds_by_audience"][audience] = 20
        yt.aggregate_models(source["state"])
        source["state"]["completed_crowds"] = 20
        await db[yt.COLLECTION].update_one({"_id": row["id"]}, {"$set": {"state": source["state"]}})
        report = await yt.results(db, "owner", row["id"])
        assert report["rankings"] == [] and report["feature_preferences"] == []
        assert report["ranking_scope"]["eligible_originals"] == 80
        assert all(len(profile["rankings"]) == 40 for profile in report["profiles_by_audience"].values())
        assert report["preference_snapshot"] == expected
        assert (await yt.profile(db, "owner"))["preference_snapshot"] == expected
        assert await db.ai_request_usage.count_documents({}) == 0
    run_case(go)


def test_start_profile_gender_drift_and_wrong_owner_cannot_write_new_preference_attempt(monkeypatch):
    async def go(db):
        await member(db)
        await db.users.update_one({"user_id": "owner"}, {"$set": {"about.demographics.gender": "Woman"}})
        body = yt.Start(expected_member="peer", request_id=str(uuid4()), audience="both", sexuality="bi",
                        viewer_gender="Woman", adult_confirmed=True)
        with pytest.raises(HTTPException) as exc:
            await yt.start(db, "owner", body)
        assert exc.value.status_code == 409 and await db[yt.COLLECTION].count_documents({}) == 0
        body.expected_member = "owner"
        original = yt.answer_context.for_member
        async def changed(database, uid):
            await database.users.update_one({"user_id": uid}, {"$set": {"about.demographics.gender": "Man"}})
            return await original(database, uid)
        monkeypatch.setattr(yt.answer_context, "for_member", changed)
        with pytest.raises(HTTPException) as exc:
            await yt.start(db, "owner", body)
        assert exc.value.status_code == 409
        assert await db[yt.COLLECTION].count_documents({}) == 0
        assert await db.answer_history.count_documents({"domain": "your_type"}) == 0
    run_case(go)


def test_both_audience_training_and_frozen_prediction_pairs_are_independent():
    async def go():
        bank = yt.catalog()
        state = yt.new_state(bank, "both")
        yt.fill(state, bank, "both", "independence")
        choices = []
        for audience, positive_index in (("men", 1), ("women", 0)):
            for index in range(2):
                choices.append({"face_id": f"fixture-{audience}-{index:03}", "audience": audience,
                    "choice": "like" if index == positive_index else "pass",
                    "prediction": .8 if index == positive_index else .2, "dwell_ms": 123, "weight": 1.0})
        state["choices"] = deepcopy(choices)
        row = {"id": str(uuid4()), "user_id": "owner", "audience": "both", "state": state,
               "status": "active", "revision": 0}
        _, after, _, effects = await yt.reduce_action(None, row, bank, action(row, "next_crowd"))
        for audience in ("men", "women"):
            own = [choice for choice in choices if choice["audience"] == audience]
            expected = model.train(model.initial(4), own, bank["by_id"], bank["feature_groups"])
            assert after["models_by_audience"][audience] == expected
            assert after["prospective_by_audience"][audience]["pairs"] == 1
            assert effects["prospective_by_audience"][audience]["correct"] == 1
        assert after["prospective"]["pairs"] == 2  # Never the four cross-audience pair combinations.
        assert after["model"]["likes"] == 2 and "weights" not in after["model"]
        assert effects["committed_choices"] == choices  # Preserve pre-feedback scores, not refitted predictions.
        men, women = (after["models_by_audience"][audience] for audience in ("men", "women"))
        a, b = bank["by_id"]["fixture-men-001"], bank["by_id"]["fixture-men-000"]
        assert model.predict(men, a)["score"] > model.predict(men, b)["score"]
        assert model.predict(women, a)["score"] < model.predict(women, b)["score"]
        isolated = [{**choice, "choice": "like" if choice["audience"] == "men" else "pass"} for choice in choices]
        state["choices"] = isolated
        _, after, _, _ = await yt.reduce_action(None, row, bank, action(row, "next_crowd"))
        assert after["prospective"]["pairs"] == 0  # Male likes plus female passes are not evidence pairs.
        with pytest.raises(ValueError):
            model.train(model.initial(4), choices, bank["by_id"], bank["feature_groups"])
        with pytest.raises(ValueError):
            model.prospective(choices)
    asyncio.run(go())


def test_full_body_exploration_is_balanced_per_audience_without_repeating_assets():
    bank = deepcopy(yt.catalog())
    bank["edition"] = yt.V2_EDITION
    for asset in bank["assets"]:
        asset["image_kind"] = "full_body" if int(asset["id"].rsplit("-", 1)[1]) < 8 else "portrait"
    bank["assets"][0]["display_fit"] = "contain"
    state = yt.new_state(bank, "both")
    yt.fill(state, bank, "both", "body-coverage")
    issued = state["cards"] + state["replacement_queue"]
    assert len({card["face_id"] for card in issued}) == 24
    for audience in ("men", "women"):
        own = [card for card in issued if card["audience"] == audience]
        assert len(own) == 12 and sum(card["image_kind"] == "full_body" for card in own) == 3
        assert state["issued_by_audience"][audience] == 12
    assert yt.public_face(bank["assets"][0])["image_kind"] == "full_body"
    assert yt.public_face(bank["assets"][0])["display_fit"] == "contain"
    with pytest.raises(HTTPException):
        yt.public_face({**bank["assets"][0], "display_fit": "stretch"})


def test_constant_and_inapplicable_catalog_groups_never_claim_preferences():
    trained = model.initial(4)
    trained.update(weights=[0, 3, -3, 3, -3], likes=30, explicit_passes=10, samples=40,
                   feature_observations=[40]*4, feature_option_counts=[20]*4)
    assets = [{"id": "constant", "audience": "women", "features": [1, 0, 1, 0], "feature_masks": [True]*4},
              {"id": "unknown", "audience": "women", "features": [0, 1, 0, 1], "feature_masks": [False]*4}]
    groups = [{"id": "constant", "indices": [0, 1], "values": ["a", "b"]},
              {"id": "men_only", "indices": [2, 3], "values": ["a", "b"], "applicable_audiences": ["men"]}]
    assert model.vector(assets[0], groups)[3:] == [0, 0]
    result = model.results(trained, assets, groups, {"correct": 0, "ties": 0, "pairs": 0, "explicit_pairs": 0, "viewed_pairs": 0}, 20)
    constant, excluded = result["feature_preferences"]
    assert constant["covered_portraits"] == 1 and constant["total_portraits"] == 2
    assert not constant["catalog_variation_available"] and not excluded["applicable"]
    for group in (constant, excluded):
        assert not group["variation_available"] and group["importance"] == 0
        assert all(value["direction"] == "undetermined" and value["association_strength"] is None for value in group["values"])


def test_per_audience_results_do_not_borrow_likes_crowds_or_negatives():
    async def go(db):
        await member(db)
        body = yt.Start(expected_member="owner", request_id=str(uuid4()), audience="both", sexuality="bi", adult_confirmed=True)
        row = await yt.start(db, "owner", body)
        source = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        bank = yt.catalog()
        for audience, likes in (("men", 30), ("women", 29)):
            choices = [{"face_id": asset["id"], "audience": audience, "choice": "like" if index < likes else "pass", "prediction": .5}
                       for index, asset in enumerate(yt.audience_assets(bank, audience))]
            source["state"]["models_by_audience"][audience] = model.train(model.initial(4), choices, bank["by_id"], bank["feature_groups"])
            source["state"]["completed_crowds_by_audience"][audience] = 20
        source["state"]["completed_crowds"] = 20
        yt.aggregate_models(source["state"])
        await db[yt.COLLECTION].update_one({"_id": row["id"]}, {"$set": {"state": source["state"]}})
        report = await yt.results(db, "owner", row["id"])
        assert report["rankings"] == report["feature_preferences"] == []
        assert report["likes"] == 59 and report["results_available"]
        assert report["profiles_by_audience"]["men"]["results_available"]
        assert not report["profiles_by_audience"]["women"]["results_available"]
        assert len(report["profiles_by_audience"]["men"]["rankings"]) == 40
        assert report["profiles_by_audience"]["women"]["rankings"] == []
        assert not (await yt.read(db, "owner", row["id"]))["audience_progress"]["women"]["results_available"]
        source["state"]["completed_crowds_by_audience"]["men"] = 19
        await db[yt.COLLECTION].update_one({"_id": row["id"]}, {"$set": {"state": source["state"]}})
        assert not (await yt.results(db, "owner", row["id"]))["profiles_by_audience"]["men"]["results_available"]
    run_case(go)


@pytest.mark.parametrize("audience", ["women", "both"])
def test_archived_v1_sessions_resume_batch_undo_and_results_without_reinterpreting_predictions(tmp_path, monkeypatch, audience):
    archived = tmp_path / "immutable-v1.json"
    archived.write_text(json.dumps(manifest()), encoding="utf8")
    monkeypatch.setattr(yt.legacy, "MANIFEST", archived)
    yt.legacy._load_catalog.cache_clear()
    async def go(db):
        await member(db)
        body = yt.Start(expected_member="owner", request_id=str(uuid4()), audience=audience,
                        sexuality="bi" if audience == "both" else None, adult_confirmed=True)
        legacy_body = yt.legacy.Start.model_validate(body.model_dump(exclude={"use_saved_profile"}))
        row = await yt.legacy.start(db, "owner", legacy_body)
        bank = yt.legacy.catalog()
        before = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        assert before["model_edition"] == model.LEGACY_EDITION and "models_by_audience" not in before["state"]
        assert (await yt.start(db, "owner", body))["duplicate"]
        with pytest.raises(HTTPException) as changed_request:
            await yt.start(db, "owner", body.model_copy(update={"use_saved_profile": True}))
        assert changed_request.value.status_code == 409
        read = await yt.read(db, "owner", row["id"])
        assert read["cards"] == row["cards"] and read["engine_version"] == 1
        paused = await yt.act(db, "owner", row["id"], action(read, "pause"))
        resumed = await yt.act(db, "owner", row["id"], action(paused, "resume"))
        batch = make_batch(resumed)
        responses = await asyncio.gather(*(yt.act_batch(db, "owner", row["id"], batch) for _ in range(2)))
        assert sum(response["duplicate"] for response in responses) == 1
        accepted = responses[0]
        undone = await yt.act(db, "owner", row["id"], action(accepted, "undo"))
        assert undone["completed_crowds"] == 0 and undone["committed_likes"] == 0
        source = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        choices = [{"face_id": asset["id"], "choice": "like" if index < 30 else "pass", "prediction": .5}
                   for index, asset in enumerate(yt.legacy.audience_assets(bank, audience))]
        source["state"]["model"] = yt.legacy.learner.train(source["state"]["model"], choices, bank["by_id"], bank["feature_groups"])
        source["state"]["completed_crowds"] = 20
        await db[yt.COLLECTION].update_one({"_id": row["id"]}, {"$set": {"state": source["state"]}})
        report = await yt.results(db, "owner", row["id"])
        assert report["model_edition"] == model.LEGACY_EDITION and report["engine_version"] == 1
        assert report["legacy_combined_profile"] == (audience == "both")
        assert set(report["profiles_by_audience"]) == (set() if audience == "both" else {"women"})
        assert source["asset_manifest_sha256"] == bank["sha256"]
        altered = {**source, "asset_manifest_sha256": "0"*64}
        with pytest.raises(HTTPException) as rejected:
            yt.catalog_for_session(altered)
        assert rejected.value.status_code == 412
        assert (await db[yt.COLLECTION].find_one({"_id": row["id"]}))["model_edition"] == model.LEGACY_EDITION
    run_case(go)


def test_recorded_engine_dimensions_cannot_be_silently_migrated():
    bank = yt.catalog()
    row = {"engine_version": yt.engine_version(bank), "model_edition": model.EDITION, "asset_edition": bank["edition"],
           "asset_manifest_sha256": bank["sha256"], "audience": "women", "state": yt.new_state(bank, "women")}
    yt.compatible(row, bank)
    row["state"]["models_by_audience"]["women"]["weights"].append(0.0)
    with pytest.raises(HTTPException) as rejected:
        yt.compatible(row, bank)
    assert rejected.value.status_code == 412


def test_both_audience_duplicate_batch_is_once_and_undo_restores_independent_models():
    async def go(db):
        await member(db)
        row = await yt.start(db, "owner", yt.Start(expected_member="owner", request_id=str(uuid4()),
                            audience="both", sexuality="bi", adult_confirmed=True))
        batch = make_batch(row)
        responses = await asyncio.gather(*(yt.act_batch(db, "owner", row["id"], batch) for _ in range(2)))
        assert sum(response["duplicate"] for response in responses) == 1
        assert all(response["revision"] == 3 for response in responses)
        source = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        assert source["state"]["model"]["likes"] == 2 and source["state"]["model"]["viewed_passes"] == 1
        assert source["state"]["prospective"]["pairs"] == 0
        own_models = source["state"]["models_by_audience"]
        assert sorted(value["likes"] for value in own_models.values()) == [0, 2]
        assert sorted(value["viewed_passes"] for value in own_models.values()) == [0, 1]
        assert await db.answer_history.count_documents({"kind": "your_type.like"}) == 2
        undone = await yt.act(db, "owner", row["id"], action(responses[0], "undo"))
        assert undone["crowd"] == 1 and undone["committed_likes"] == 0
        restored = (await db[yt.COLLECTION].find_one({"_id": row["id"]}))["state"]
        assert all(value == model.initial(4) for value in restored["models_by_audience"].values())
        assert all(value["pairs"] == 0 for value in restored["prospective_by_audience"].values())
    run_case(go)


def test_actual_v2_catalog_single_and_both_profiles_use_current_coverage_and_original_v1_bank(monkeypatch):
    monkeypatch.setattr(yt, "MANIFEST", yt.V2_MANIFEST)
    bank = yt.catalog()
    assert len(bank["assets"]) == 736 and len(bank["feature_names"]) == 63
    assert len(yt.legacy.catalog()["assets"]) == 640 and len(yt.legacy.catalog()["feature_names"]) == 45
    async def go(db):
        await member(db)
        for audience in ("women", "both"):
            row = await yt.start(db, "owner", yt.Start(expected_member="owner", request_id=str(uuid4()),
                audience=audience, sexuality="bi" if audience == "both" else None, adult_confirmed=True))
            source = await db[yt.COLLECTION].find_one({"_id": row["id"]})
            for group in yt.selected_audiences(audience):
                choices = [{"face_id": asset["id"], "audience": group,
                            "choice": "like" if index % 3 == 0 else "pass", "prediction": .5}
                           for index, asset in enumerate(yt.audience_assets(bank, group))]
                source["state"]["models_by_audience"][group] = model.train(model.initial(63), choices, bank["by_id"], bank["feature_groups"])
                source["state"]["completed_crowds_by_audience"][group] = 20
            source["state"]["completed_crowds"] = 20
            yt.aggregate_models(source["state"])
            await db[yt.COLLECTION].update_one({"_id": row["id"]}, {"$set": {"state": source["state"]}})
            report = await yt.results(db, "owner", row["id"])
            assert set(report["profiles_by_audience"]) == set(yt.selected_audiences(audience))
            assert len(report["rankings"]) == (368 if audience == "women" else 0)
            for group, profile in report["profiles_by_audience"].items():
                assert profile["results_available"] and len(profile["rankings"]) == 368
                assert all(item["audience"] == group for item in profile["rankings"])
                assert sum(item["image_kind"] == "full_body" for item in profile["rankings"]) == 16
                assert len(profile["feature_preferences"]) == 19
                if group == "women":
                    facial = next(item for item in profile["feature_preferences"] if item["id"] == "facial_hair")
                    assert not facial["applicable"] and all(item["direction"] == "undetermined" for item in facial["values"])
    run_case(go)


def test_display_fit_is_optional_public_metadata_and_rejects_unknown_modes(tmp_path, monkeypatch):
    raw = manifest()
    raw["assets"][0]["display_fit"] = "contain"
    path = tmp_path / "display-fit.json"
    path.write_text(json.dumps(raw), encoding="utf8")
    monkeypatch.setattr(yt, "MANIFEST", path)
    bank = yt.catalog()
    assert yt.public_face(bank["assets"][0])["display_fit"] == "contain"
    assert "display_fit" not in yt.public_face(bank["assets"][1])
    raw["assets"][0]["display_fit"] = "stretch"
    bad = tmp_path / "invalid-display-fit.json"
    bad.write_text(json.dumps(raw), encoding="utf8")
    monkeypatch.setattr(yt, "MANIFEST", bad)
    with pytest.raises(HTTPException) as rejected:
        yt.catalog()
    assert rejected.value.status_code == 503


def test_profile_sexuality_saves_with_history_and_drives_new_attempts_without_relabeling_old_ones():
    from backend import personal_demographics, answer_context
    async def go(db):
        import httpx
        from fastapi import FastAPI
        await member(db, age=30)
        await personal_demographics.save_own(db, "owner", {"gender": "Man", "sexuality": "straight"})
        saved = await db.users.find_one({"user_id": "owner"})
        assert answer_context.values(saved)["sexuality"] == "straight"
        observations = await db.answer_history.find({"owner_id": "owner", "domain": "demographics"}).to_list(20)
        assert any(row.get("after", {}).get("sexuality") == "straight" for row in observations)
        async def signed_in():
            return {"user_id": "owner"}
        app = FastAPI()
        app.include_router(yt.make_router(lambda: db, signed_in, signed_in), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            boot = (await client.get("/api/your-type")).json()
            assert boot["profile"] == {"adult_eligible": True, "gender": "Man", "sexuality": "straight"}
            payload = {"request_id": str(uuid4()), "expected_member": "owner", "audience": "women",
                       "sexuality": "straight", "viewer_gender": "Man", "use_saved_profile": True}
            response = await client.post("/api/your-type/sessions", json=payload)
            assert response.status_code == 200
            first = response.json()
            await personal_demographics.save_own(db, "owner", {"sexuality": "bi"})
            stale = await client.post("/api/your-type/sessions", json={**payload, "request_id": str(uuid4())})
            assert stale.status_code == 409
            current = await client.post("/api/your-type/sessions", json={**payload, "request_id": str(uuid4()),
                "sexuality": "bi", "audience": "both"})
            assert current.status_code == 200 and current.json()["preference_snapshot"]["sexuality"] == "bi"
            old = (await client.get("/api/your-type/sessions/" + first["id"])).json()
            assert old["preference_snapshot"]["sexuality"] == "straight"
            await personal_demographics.save_own(db, "owner", {"sexuality": None})
            assert "sexuality" not in (await db.users.find_one({"user_id": "owner"}))["about"]["demographics"]
            assert (await client.get("/api/your-type")).json()["profile"]["sexuality"] is None
            assert await db.ai_request_usage.count_documents({}) == 0
    run_case(go)


@pytest.mark.parametrize("value", ["inferred", "Straight", True, 1, ["bi"]])
def test_profile_sexuality_rejects_invalid_and_inferred_values(value):
    from backend import personal_demographics
    with pytest.raises(HTTPException) as invalid:
        personal_demographics.demographics_patch({"sexuality": value})
    assert invalid.value.status_code == 422


def test_saved_profile_start_flag_preserves_legacy_request_fingerprints():
    body = yt.Start(expected_member="owner", request_id=str(uuid4()), audience="women", adult_confirmed=True)
    assert "use_saved_profile" not in yt.start_request(body)
    current = yt.Start(expected_member="owner", request_id=str(uuid4()), audience="women", use_saved_profile=True)
    assert yt.start_request(current)["use_saved_profile"] is True
