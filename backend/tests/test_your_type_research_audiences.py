"""Audience, immutable-schema and private-preference research boundaries."""
from copy import deepcopy
import json

from backend import research, your_type, your_type_legacy, your_type_model, your_type_model_v1
from backend import your_type_research as yr
from backend.tests.test_your_type_research import AFTER, exercise, member, observation, session


def bank_fixture():
    return {"edition": "fixture-adults-v2", "sha256": "current-manifest", "schema_version": 2,
        "feature_names": ["soft", "direct", "clean", "beard"],
        "feature_groups": [
            {"id": "lighting", "label": "Lighting", "indices": [0, 1], "values": ["soft", "direct"],
             "applicable_audiences": ["men", "women"]},
            {"id": "facial_hair", "label": "Facial hair", "indices": [2, 3], "values": ["clean", "beard"],
             "applicable_audiences": ["men"]}],
        "assets": [{"id": audience + str(i), "audience": audience,
            "features": [i, 1-i, i if audience == "men" else 0, 1-i if audience == "men" else 0],
            "feature_masks": [True, True, audience == "men", audience == "men"]}
            for audience in ("men", "women") for i in range(2)]}


def learned(direction=1):
    return {"likes": 30, "explicit_passes": 3, "viewed_passes": 0, "samples": 33,
        "weights": [0, .2 * direction, -.4 * direction, .3 * direction, -.2 * direction],
        "feature_observations": [6] * 4, "feature_option_counts": [3] * 4}


def independent_snapshot(bank):
    return session(audience="both", asset_edition=bank["edition"], asset_manifest_sha256=bank["sha256"],
        model_edition=your_type_model.EDITION, engine_version=3,
        state={"completed_crowds": 40, "completed_crowds_by_audience": {"men": 20, "women": 20},
            "model": {"samples": 66}, "models_by_audience": {"men": learned(), "women": learned(-1)}})


def feature_values(values):
    return {key: value for key, value in values.items() if key.startswith("feature:")}


def test_independent_associations_preserve_opposite_audience_directions_without_aggregate_fallback():
    bank = bank_fixture()
    row = independent_snapshot(bank)
    values = feature_values(yr.latest_values(member(), row, bank))
    assert values["feature:men:0"] == "more_often_favored"
    assert values["feature:women:0"] == "less_often_favored"
    assert values["feature:men:1"] == "less_often_favored"
    assert values["feature:women:1"] == "more_often_favored"
    assert "feature:women:2" not in values and "feature:0" not in values
    assert row["state"]["models_by_audience"]["women"]["weights"] == learned(-1)["weights"]
    missing = deepcopy(row)
    missing["state"]["model"] = learned()
    missing["state"].pop("models_by_audience")
    assert not feature_values(yr.latest_values(member(), missing, bank))
    assert not feature_values(yr.latest_values(member(), {**row, "engine_version": 1}, bank))


def test_each_audience_needs_its_own_completed_crowds_and_signal():
    bank = bank_fixture()
    row = independent_snapshot(bank)
    row["state"]["completed_crowds_by_audience"]["women"] = 19
    values = feature_values(yr.latest_values(member(), row, bank))
    assert values and all(key.startswith("feature:men:") for key in values)
    row["state"]["completed_crowds_by_audience"]["women"] = 20
    row["state"]["models_by_audience"]["women"]["likes"] = 29
    assert not any(key.startswith("feature:women:") for key in yr.latest_values(member(), row, bank))
    row["state"]["models_by_audience"]["women"] = learned(-1)
    row["state"]["models_by_audience"]["women"]["feature_option_counts"] = [6] * 4
    assert not any(key.startswith("feature:women:") for key in yr.latest_values(member(), row, bank))


def test_admin_coverage_counts_known_positive_annotations_and_exposes_inapplicability(monkeypatch):
    bank = bank_fixture()
    bank["assets"].extend([
        {"id": "unknown", "audience": "men", "features": [1, 0, 0, 0], "feature_masks": [False] * 4},
        {"id": "all-zero", "audience": "men", "features": [0] * 4, "feature_masks": [True] * 4}])
    original = deepcopy(bank)
    monkeypatch.setattr(your_type, "catalog", lambda: bank)
    catalog = yr.catalog()
    lighting = catalog["feature_groups"][0]["audience_coverage"]["men"]
    assert lighting == {"applicable": True, "coverage_available": True, "covered_portraits": 2,
        "total_portraits": 4, "variation_available": True, "value_counts": {"soft": 1, "direct": 1}}
    beard = catalog["feature_groups"][1]["audience_coverage"]["women"]
    assert not beard["applicable"] and not beard["coverage_available"] and beard["covered_portraits"] == 0
    assert beard["total_portraits"] == 2 and not any(beard["value_counts"].values())
    assert bank == original
    assert "Private stated age and height" in catalog["research_exclusions"][0]


def test_unknown_edition_annotations_cannot_be_treated_as_comparisons():
    bank = bank_fixture()
    for asset in bank["assets"]:
        if asset["audience"] == "women":
            asset["feature_masks"] = [False] * 4
    values = feature_values(yr.latest_values(member(), independent_snapshot(bank), bank))
    assert values and not any(key.startswith("feature:women:") for key in values)
    variables = yr.feature_variables(bank)
    assert not variables["feature:women:0"]["annotation_coverage"]["coverage_available"]
    assert "feature:women:2" not in variables


def test_frozen_legacy_schema_is_never_split_or_reinterpreted_with_current_catalog(monkeypatch):
    current = bank_fixture()
    old = {"edition": "fixture-adults-v1", "sha256": "archived-manifest", "feature_names": ["old-a", "old-b"],
        "feature_groups": [{"id": "old_group", "label": "Old group", "indices": [0, 1], "values": ["old-a", "old-b"]}]}
    monkeypatch.setattr(your_type_legacy, "catalog", lambda: old)
    schemas = yr.instrument_banks(current)
    row = session(audience="both", asset_edition=old["edition"], asset_manifest_sha256=old["sha256"],
        model_edition=your_type_model_v1.EDITION,
        state={"completed_crowds": 20, "model": {**learned(), "weights": [0, .2, -.4],
            "feature_observations": [6, 6], "feature_option_counts": [3, 3]}})
    values = feature_values(yr.latest_values(member(), row, schemas[yr.instrument(row)]))
    assert values == {"feature:0": "more_often_favored", "feature:1": "less_often_favored"}
    assert not feature_values(yr.latest_values(member(), row, current))
    assert not feature_values(yr.latest_values(member(), {**row, "asset_manifest_sha256": "unrecorded"}, old))
    assert schemas.get((old["edition"], your_type_model_v1.EDITION, current["sha256"])) is None
    variables = yr.feature_variables(old, your_type_model_v1.EDITION)
    assert set(variables) == {"feature:0", "feature:1"}
    assert variables["feature:0"]["label"] == "Old group · old-a"
    assert variables["feature:0"]["asset_manifest_sha256"] == old["sha256"]


def test_private_stated_preferences_and_unknown_history_actions_are_outside_existing_grants():
    query = yr.event_query(member(), "owner")
    assert query["source"] == "your_type_sessions"
    assert "your_type.like" in query["kind"]["$in"]
    for kind in ("your_type.stated_preferences", "your_type.future_private_field", "answer_changed"):
        assert kind not in query["kind"]["$in"] and not yr.action_counts({"kind": kind})
    values = yr.latest_values(member(your_type_stated_preferences={"age_min": 28, "height_min_cm": 170}), session())
    assert not any("stated" in key or "height_min" in key for key in values)
    assert yr.preferences(member())["stated_preferences_included"] is False


def test_research_export_and_activity_exclude_private_preferences_under_existing_grants():
    async def case(db):
        await db.users.insert_one(member(your_type_stated_preferences={"age_min": 28, "height_min_cm": 170}))
        await db.your_type_sessions.insert_one(session())
        choice = observation(eid="choice")
        choice["after"]["request"].update(stated_preferences={"age_min": 28}, height_min_cm=170,
            sexuality="unverified", arbitrary_private="do not export")
        private = observation(eid="private", kind="your_type.stated_preferences")
        private["source"] = "users"
        private["after"] = {"age_min": 28, "height_min_cm": 170}
        unknown = observation(eid="unknown", kind="your_type.future_private_field")
        await db.answer_history.insert_many([choice, private, unknown])
        rows = [row async for row in yr.member_rows(db, {"env": "preview"}, "owner", "m_" + "a" * 32, AFTER)]
        assert [row["variable_id"] for row in rows] == ["choice"]
        request = json.loads(rows[0]["answer"])["request"]
        assert request == {"kind": "like", "face_id": "synthetic-1"}
        assert not any(word in json.dumps(rows) for word in ("height_min", "age_min", "arbitrary_private", "unverified"))
        report = await yr.report(db, {"env": "preview"})
        assert report["activity"]["like"] == 1 and report["activity"]["accepted_action_events"] == 1
    exercise(case)


def test_report_reads_per_audience_models_and_keeps_manifest_cohorts_separate(monkeypatch):
    bank = bank_fixture()
    monkeypatch.setattr(your_type, "catalog", lambda: bank)
    async def case(db):
        await db.users.insert_many([member("first"), member("second")])
        row = independent_snapshot(bank)
        row.update(_id="first", user_id="first")
        other = deepcopy(row)
        other.update(_id="second", user_id="second", asset_manifest_sha256="unknown-sha")
        await db.your_type_sessions.insert_many([row, other])
        mixed = await yr.report(db, {"env": "preview"}, "feature:men:0", "feature:women:0")
        assert mixed["edition_cohorts"] == 2 and not mixed["edition_compatible"]
        selected = await yr.report(db, {"env": "preview"}, "feature:men:0", "feature:women:0",
            edition=research.fingerprint(yr.instrument(row)))
        assert selected["edition_compatible"] and selected["edition_available"]
        assert selected["denominators"]["members_with_sessions"] == 2
        # This small cohort is correctly suppressed; inspect the actual values
        # passed to aggregation to verify the source projection, not mock rows.
        captured = []
        original = yr.latest_values
        def capture(person, source, schema=None, quiz=None):
            values = original(person, source, schema, quiz)
            captured.append((source["asset_manifest_sha256"], feature_values(values)))
            return values
        monkeypatch.setattr(yr, "latest_values", capture)
        await yr.report(db, {"env": "preview"})
        values_by_sha = dict(captured)
        assert values_by_sha[bank["sha256"]]["feature:men:0"] == "more_often_favored"
        assert values_by_sha[bank["sha256"]]["feature:women:0"] == "less_often_favored"
        assert values_by_sha["unknown-sha"] == {}
    exercise(case)
