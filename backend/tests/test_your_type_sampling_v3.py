"""Actual domain actions against isolated PostgreSQL; synthetic catalog inputs."""
import ast
import asyncio
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend import your_type as yt, your_type_model as learner, your_type_research as yr
from backend import answer_history
from backend.tests.test_admin_answer_history import run_case
from backend.tests.test_your_type import member, action


def bank_data(size=300):
    groups = [{"id": "skin_tone", "label": "Visible skin shade", "values": ["light", "medium", "deep"],
               "indices": [0, 1, 2]}, {"id": "hair_length", "label": "Hair length", "values": ["short", "long"], "indices": [3, 4]}]
    return {"schema_version": 1, "edition": "fixture-sampling-v3", "feature_names": ["light", "medium", "deep", "short", "long"],
        "feature_groups": groups, "assets": [{"id": f"v3-{audience}-{i:03}", "audience": audience,
            "url": "/your-type/fixture.jpg", "image_kind": "portrait", "active": True, "adult_reviewed": True,
            "features": [int(i % 3 == j) for j in range(3)] + [i % 2, 1 - i % 2], "feature_masks": [True] * 5,
            "feature_values": {"skin_tone": ("light", "medium", "deep")[i % 3]}}
            for audience in ("men", "women") for i in range(size)]}


@pytest.fixture(autouse=True)
def synthetic_bank(tmp_path, monkeypatch):
    path = tmp_path / "catalog-v3.json"
    path.write_text(json.dumps(bank_data()), encoding="utf8")
    monkeypatch.setattr(yt, "MANIFEST", path)
    yt._load_catalog.cache_clear()


def ids(row):
    return {card["face_id"] for card in row["cards"] + row["replacement_queue"]}


@pytest.mark.parametrize("audience", ["men", "women", "both"])
def test_every_audience_gets_all_phases_and_initial_shade_variety(audience):
    bank = yt.catalog()
    for seed in ("first", "second", "third", "fourth"):
        state = yt.new_state(bank, audience)
        yt.fill(state, bank, audience, seed)
        assert len(ids(state)) == yt.CARDS + yt.RESERVE
        for group in yt.selected_audiences(audience):
            own = [c for c in state["cards"] + state["replacement_queue"] if c["audience"] == group]
            assert Counter(c["reason"] for c in own) == {reason: len(own) // 4 for reason in
                ("exploration", "uncertainty", "preference", "diversity")}
            visible = [bank["by_id"][c["face_id"]] for c in state["cards"] if c["audience"] == group]
            assert {yt.visible_shade(a, bank["feature_groups"]) for a in visible} == {"light", "medium", "deep"}


@pytest.mark.parametrize("audience", ["men", "women", "both"])
def test_strong_preference_keeps_later_discovery_varied_without_rewriting_scores(audience):
    bank = yt.catalog()
    state = yt.new_state(bank, audience)
    for model in state["models_by_audience"].values():
        model["weights"][1:4] = [-6, -6, 8]
    original_models = deepcopy(state["models_by_audience"])
    for crowd in range(5):
        yt.fill(state, bank, audience, f"strong-{crowd}", new_crowd=True)
        for group in yt.selected_audiences(audience):
            own = [c for c in state["cards"] if c["audience"] == group]
            shades = Counter(yt.visible_shade(bank["by_id"][c["face_id"]], bank["feature_groups"]) for c in own)
            assert set(shades) == {"light", "medium", "deep"}
            preferred = [c for c in own if c["reason"] == "preference"]
            assert all(yt.visible_shade(bank["by_id"][c["face_id"]], bank["feature_groups"]) == "deep" for c in preferred)
            assert all(c["score"] == learner.predict(original_models[group], bank["by_id"][c["face_id"]], bank["feature_groups"])["score"] for c in own)
    assert state["models_by_audience"] == original_models


def test_unknown_or_single_shade_and_exhaustion_are_valid_without_fabricating_labels():
    bank = deepcopy(yt.catalog())
    bank["assets"] = bank["assets"][:36]
    for i, asset in enumerate(bank["assets"]):
        asset["features"][:3] = [0, 0, 1]
        if i % 2:
            asset["feature_masks"][:3] = [False] * 3
    bank.update(by_id={a["id"]: a for a in bank["assets"]}, position={a["id"]: i for i, a in enumerate(bank["assets"])})
    state = yt.new_state(bank, "men")
    state["recent_exposure_counts"] = [1] * len(bank["assets"])
    for crowd in range(4):
        yt.fill(state, bank, "men", f"small-{crowd}", new_crowd=True)
        assert len(ids(state)) == 24
        assert all(yt.visible_shade(bank["by_id"][face], bank["feature_groups"]) in (None, "deep") for face in ids(state))
    assert state["catalog_cycle"] >= 2


def test_retake_prefers_unseen_faces_is_owner_scoped_and_does_not_inherit_model():
    async def case(db):
        await member(db)
        await member(db, "other")
        bank = yt.catalog()
        await db[yt.COLLECTION].insert_one({"_id": "other-history", "user_id": "other", "asset_edition": bank["edition"],
            "asset_manifest_sha256": bank["sha256"], "updated_at": "9999", "state": {"seen": list(range(600))}})
        attempts = []
        used = set()
        for i in range(4):
            body = yt.Start(expected_member="owner", request_id=str(uuid4()), audience="women", adult_confirmed=True,
                retake_of=attempts[-1]["id"] if attempts else None)
            row = await yt.start(db, "owner", body)
            assert not (used & ids(row))
            used |= ids(row)
            attempts.append(row)
            saved = await db[yt.COLLECTION].find_one({"_id": row["id"]})
            assert saved["state"]["recent_attempts_considered"] == i
            assert saved["state"]["models_by_audience"]["women"] == learner.initial(5)
            duplicate = await yt.start(db, "owner", body)
            assert duplicate["duplicate"] and ids(duplicate) == ids(row)
        snapshot = deepcopy(saved["state"])
        face = attempts[-1]["cards"][0]["face_id"]
        choice = await yt.act(db, "owner", saved["id"], action(attempts[-1], "like", face=face, views=[face]))
        undone = await yt.act(db, "owner", saved["id"], action(choice, "undo"))
        restored = await db[yt.COLLECTION].find_one({"_id": saved["id"]})
        assert restored["state"] == snapshot
        with pytest.raises(HTTPException) as stale:
            face = choice["cards"][0]["face_id"]
            await yt.act(db, "owner", saved["id"], action(choice, "like", face=face, views=[face]))
        assert stale.value.status_code == 409
    run_case(case)


def test_recent_exposure_query_is_bounded_excludes_deleted_and_other_manifest():
    async def case(db):
        bank = yt.catalog()
        for i in range(8):
            await db[yt.COLLECTION].insert_one({"_id": str(i), "id": str(i), "user_id": "owner", "updated_at": str(i),
                "asset_edition": bank["edition"], "asset_manifest_sha256": bank["sha256"], "state": {"seen": [i]}})
        for suffix, extra in (("deleted", {"deleted": True}), ("clearing", {"status": "clearing"}),
                ("different-manifest", {"asset_manifest_sha256": "other"}), ("other-owner", {"user_id": "other"})):
            await db[yt.COLLECTION].insert_one({"_id": suffix, "id": suffix, "user_id": "owner", "updated_at": "9999",
                "asset_edition": bank["edition"], "asset_manifest_sha256": bank["sha256"], "state": {"seen": list(range(600))}, **extra})
        counts, considered = await yt.recent_exposure(db, "owner", bank)
        assert considered == 5 and sum(counts) == 5 and counts[3:8] == [1] * 5
    run_case(case)


def test_v2_sampler_matches_frozen_source_byte_for_byte_semantics():
    frozen = next(n for n in ast.parse(Path(yt.__file__).read_text()).body if isinstance(n, ast.FunctionDef) and n.name == "issue_v2")
    frozen.name = "issue"
    # Exact normalized function from deployed d6f70ab; no Git CLI needed in CI.
    assert hashlib.sha256(ast.dump(frozen, include_attributes=False).encode()).hexdigest() == "e3165b55806085b8eb001ae1ddb0a4753f36f9904f3ee114683beda3a572f536"
    bank = yt.catalog_v2()
    state = yt.new_state(bank, "both")
    a, b = deepcopy(state), deepcopy(state)
    assert yt.issue(a, bank, "both", "frozen", 24) == yt.issue_v2(b, bank, "both", "frozen", 24)
    assert a == b


def test_v2_retry_resume_results_and_research_keep_exact_catalog_after_current_changes(monkeypatch):
    async def case(db):
        await member(db)
        current = yt.MANIFEST
        monkeypatch.setattr(yt, "MANIFEST", yt.V2_MANIFEST)
        body = yt.Start(expected_member="owner", request_id=str(uuid4()), audience="women", adult_confirmed=True)
        row = await yt.start(db, "owner", body)
        assert row["engine_version"] == 2
        monkeypatch.setattr(yt, "MANIFEST", current)
        retry = await yt.start(db, "owner", body)
        assert retry["duplicate"] and ids(retry) == ids(row)
        paused = await yt.act(db, "owner", row["id"], action(row, "pause"))
        resumed = await yt.act(db, "owner", row["id"], action(paused, "resume"))
        assert resumed["engine_version"] == 2 and resumed["asset_manifest_sha256"] == yt.V2_MANIFEST_SHA256
        saved = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        bank = yt.catalog_for_session(saved)
        assert len(bank["assets"]) == 736
        models = saved["state"]["models_by_audience"]
        models["women"].update(likes=30, explicit_passes=3, samples=33)
        await db[yt.COLLECTION].update_one({"_id": row["id"]}, {"$set": {
            "state.models_by_audience": models, "state.completed_crowds_by_audience.women": 20,
            "state.completed_crowds": 20, "state.model.likes": 30}})
        report = await yt.results(db, "owner", row["id"])
        assert len(report["rankings"]) == 368 and report["engine_version"] == 2
        assert report["asset_manifest_sha256"] == yt.V2_MANIFEST_SHA256
        schemas = yr.instrument_banks(yt.catalog())
        assert schemas[yr.instrument(saved)]["sha256"] == yt.V2_MANIFEST_SHA256
        assert not yr.latest_values({}, {**saved, "engine_version": 3}, bank).get("feature:women:0")
        with pytest.raises(HTTPException) as wrong_engine:
            yt.catalog_for_session({**saved, "engine_version": 3})
        assert wrong_engine.value.status_code == 412
    run_case(case)


@pytest.mark.parametrize("audience", ["women", "both"])
def test_thousand_portrait_state_with_recent_history_fits_every_action_and_history_bound(audience):
    from bson import BSON
    async def case():
        bank = bank_data(500)
        bank["feature_names"] += [f"unknown-{i}" for i in range(58)]
        for asset in bank["assets"]:
            asset["features"] += [0] * 58
            asset["feature_masks"] += [False] * 58
        bank.update(by_id={a["id"]: a for a in bank["assets"]}, position={a["id"]: i for i, a in enumerate(bank["assets"])}, sha256="f" * 64)
        state = yt.new_state(bank, audience)
        state["recent_exposure_counts"] = [yt.RECENT_ATTEMPTS] * 1000
        state["recent_attempts_considered"] = yt.RECENT_ATTEMPTS
        yt.fill(state, bank, audience, "thousand-state")
        state["seen"] = list(range(1000))
        row = {"id": str(uuid4()), "user_id": "owner", "audience": audience, "attempt_id": "bounded-fixture",
               "revision": 0, "status": "active", "state": state, "model_edition": learner.EDITION,
               "asset_edition": bank["edition"], "asset_manifest_sha256": bank["sha256"]}
        for _ in range(yt.MAX_CHOICES):
            face = row["state"]["cards"][0]["face_id"]
            command = action(row, "like", face=face, views=[face])
            before, after, status, effects = await yt.reduce_action(None, row, bank, command)
            assert len(BSON.encode(after)) <= yt.MAX_STATE_BYTES
            event = yt.action_event(row, {}, command, before, after, effects, {})
            assert len(BSON.encode(event)) <= answer_history.MAX_EVENT_BYTES
            restored_before, restored_after = yt.event_states(event["after"])
            assert restored_before == before and restored_after == after
            row.update(state=after, status=status, revision=row["revision"] + 1)
        command = action(row, "next_crowd", views=[c["face_id"] for c in row["state"]["cards"]])
        before, after, status, effects = await yt.reduce_action(None, row, bank, command)
        assert len(BSON.encode(after)) <= yt.MAX_STATE_BYTES
        assert len(BSON.encode(yt.action_event(row, {}, command, before, after, effects, {}))) <= answer_history.MAX_EVENT_BYTES
        assert after["model"]["samples"] == yt.MAX_CHOICES + yt.CARDS
    asyncio.run(case())
