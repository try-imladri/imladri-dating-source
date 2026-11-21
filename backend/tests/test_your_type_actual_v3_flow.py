"""Actual 1,000-image catalog through persisted actions on isolated PostgreSQL.

No customer data, provider API or production authentication is used. Unlike a
seeded-model test, every committed preference below comes from domain actions.
"""
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend import your_type as yt, your_type_research as research
from backend.tests.test_admin_answer_history import run_case
from backend.tests.test_your_type import action, member


def crowd_batch(row):
    """Two visible likes per audience, then pass the other viewed originals."""
    selected = [card["face_id"] for audience in yt.selected_audiences(row["audience"])
                for card in [c for c in row["cards"] if c["audience"] == audience][:2]]
    actions = [{"action_id": str(uuid4()), "kind": "like", "face_id": face,
                "viewed_face_ids": [face], "dwell_ms": {face: 500}} for face in selected]
    remaining = [card["face_id"] for card in row["cards"] if card["face_id"] not in selected]
    actions.append({"action_id": str(uuid4()), "kind": "next_crowd", "viewed_face_ids": remaining,
                    "dwell_ms": {face: 500 for face in remaining}})
    return yt.Batch(batch_id=str(uuid4()), expected_revision=row["revision"], actions=actions)


@pytest.mark.parametrize("audience", ["men", "women", "both"])
def test_actual_v3_saved_actions_results_profile_history_and_research_dispatch(audience):
    bank = yt.catalog()
    assert bank["edition"] == "your-type-synthetic-adults-v3"
    assert len(bank["assets"]) == 1000 and len(bank["feature_names"]) == 63
    body_group = next(group for group in bank["feature_groups"] if group["id"] == "visible_build")

    async def case(db):
        await member(db)
        row = await yt.start(db, "owner", yt.Start(expected_member="owner", request_id=str(uuid4()),
            audience=audience, sexuality="bi" if audience == "both" else None, adult_confirmed=True))
        assert row["engine_version"] == 3 and row["asset_manifest_sha256"] == bank["sha256"]
        assert len(row["cards"]) == 12 and len(row["replacement_queue"]) == 12
        assert len({card["face_id"] for card in row["cards"] + row["replacement_queue"]}) == 24
        assert all(not {"features", "feature_masks", "feature_values", "generation_prompt"}.intersection(card)
                   for card in row["cards"] + row["replacement_queue"])
        with pytest.raises(HTTPException) as early:
            await yt.results(db, "owner", row["id"])
        assert early.value.status_code == 409

        for crowd in range(20):
            batch = crowd_batch(row)
            row = await yt.act_batch(db, "owner", row["id"], batch)
            assert row["completed_crowds"] == crowd + 1
            if crowd == 0:
                # A transport retry must not double charge training or history.
                repeated = await yt.act_batch(db, "owner", row["id"], batch)
                assert repeated["duplicate"] and repeated["revision"] == row["revision"]
                committed = deepcopy((await db[yt.COLLECTION].find_one({"_id": row["id"]}))["state"])
                undone = await yt.act(db, "owner", row["id"], action(row, "undo"))
                assert undone["completed_crowds"] == 0 and undone["committed_likes"] == 0
                pending = await db[yt.COLLECTION].find_one({"_id": row["id"]})
                assert len(pending["state"]["choices"]) == 2 * len(yt.selected_audiences(audience))
                # Undo restores the pending choices. Re-commit the same views.
                views = batch.actions[-1].viewed_face_ids
                row = await yt.act(db, "owner", row["id"], action(undone, "next_crowd", views=views))
                restored = (await db[yt.COLLECTION].find_one({"_id": row["id"]}))["state"]
                assert restored["models_by_audience"] == committed["models_by_audience"]
                assert restored["completed_crowds_by_audience"] == committed["completed_crowds_by_audience"]
            if crowd == 2:
                before_pause = (await db[yt.COLLECTION].find_one({"_id": row["id"]}))["state"]
                paused = await yt.act(db, "owner", row["id"], action(row, "pause"))
                assert paused["status"] == "paused"
                reread = await yt.read(db, "owner", row["id"])
                assert reread["cards"] == paused["cards"]
                row = await yt.act(db, "owner", row["id"], action(paused, "resume"))
                after_resume = (await db[yt.COLLECTION].find_one({"_id": row["id"]}))["state"]
                assert row["status"] == "active" and row["cards"] == reread["cards"]
                assert {k: v for k, v in before_pause.items() if k != "undo_head"} == {
                    k: v for k, v in after_resume.items() if k != "undo_head"}

        row = await yt.act(db, "owner", row["id"], action(row, "finish"))
        result = await yt.results(db, "owner", row["id"])
        assert row["status"] == "finished" and result["engine_version"] == 3
        assert result["results_available"] and result["completed_crowds"] == 20
        assert len(result["rankings"]) == (0 if audience == "both" else 500)
        assert result["ranking_scope"]["eligible_originals"] == (1000 if audience == "both" else 500)
        assert result["ranking_scope"]["cross_audience_scores_comparable"] is False
        for name, profile in result["profiles_by_audience"].items():
            assert profile["results_available"] and len(profile["rankings"]) == 500
            assert len({item["face_id"] for item in profile["rankings"]}) == 500
            assert all(item["audience"] == name and item["image_kind"] == "portrait" for item in profile["rankings"])
            assert profile["likes"] == 40 and profile["completed_crowds"] == 20
            body = next(group for group in profile["feature_preferences"] if group["id"] == "visible_build")
            assert not body["coverage_available"] and not body["variation_available"]
            assert all(item["direction"] == "undetermined" for item in body["values"])
        own_profile = await yt.profile(db, "owner")
        assert own_profile["available"] and own_profile["session_id"] == row["id"]
        assert own_profile["source_revision"] == row["revision"]
        source = await db[yt.COLLECTION].find_one({"_id": row["id"]})
        for model in source["state"]["models_by_audience"].values():
            assert all(model["weights"][index + 1] == 0 and model["feature_observations"][index] == 0
                       for index in body_group["indices"])
        schemas = research.instrument_banks(bank)
        assert schemas[research.instrument(source)]["sha256"] == bank["sha256"]
        old = yt.catalog_v2()
        assert (old["edition"], source["model_edition"], old["sha256"]) in schemas
        assert len(yt.legacy.catalog()["assets"]) == 640 and len(old["assets"]) == 736
        values = research.latest_values({}, source, bank)
        assert all(any(key.startswith(f"feature:{name}:") for key in values)
                   for name in yt.selected_audiences(audience))
        assert not any(f"feature:{name}:{index}" in values for name in ("men", "women") for index in body_group["indices"])
        wrong_engine = research.latest_values({}, {**source, "engine_version": 2}, bank)
        assert not any(key.startswith("feature:") for key in wrong_engine)
        assert await db.answer_history.count_documents({"source_id": row["id"], "kind": "your_type.batch_committed"}) == 20
        assert await db.answer_history.count_documents({"source_id": row["id"], "kind": "your_type.like"}) == 40 * len(yt.selected_audiences(audience))
        assert await db.answer_history.count_documents({"source_id": row["id"], "kind": "your_type.undo"}) == 1
    run_case(case)
