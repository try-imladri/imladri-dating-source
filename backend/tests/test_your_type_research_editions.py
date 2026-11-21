"""Admin metadata must match the selected or sole observed instrument."""
import pytest

from backend import research, your_type as yt, your_type_research as yr, your_type_model
from backend.tests.test_your_type_research import exercise, member, session


def body_axis(bank):
    group = next(group for group in bank["feature_groups"] if group["id"] == "visible_build")
    return "feature:women:" + str(group["indices"][0])


def saved(bank, uid):
    return session(uid=uid, sid=uid, asset_edition=bank["edition"], asset_manifest_sha256=bank["sha256"],
                   model_edition=your_type_model.EDITION, engine_version=yt.engine_version(bank))


@pytest.mark.parametrize("cohort_edition", [2, 3])
def test_sole_current_or_archived_cohort_has_its_own_actual_body_coverage(cohort_edition):
    current, old = yt.catalog(), yt.catalog_v2()
    assert current["edition"] == "your-type-synthetic-adults-v3"
    bank = old if cohort_edition == 2 else current
    async def case(db):
        await db.users.insert_one(member())
        row = saved(bank, "owner")
        await db.your_type_sessions.insert_one(row)
        report = await yr.report(db, {"env": "preview"})
        values = {value["id"]: value for value in report["variables"]}
        body = values[body_axis(bank)]
        assert report["edition_cohorts"] == 1 and report["edition_compatible"]
        assert body["asset_edition"] == bank["edition"] and body["asset_manifest_sha256"] == bank["sha256"]
        assert body["annotation_coverage"]["total_portraits"] == (368 if cohort_edition == 2 else 500)
        assert body["annotation_coverage"]["coverage_available"] is (cohort_edition == 2)
        assert body["annotation_coverage"]["variation_available"] is (cohort_edition == 2)
        assert not body.get("requires_edition_selection")
    exercise(case)


def test_mixed_instruments_never_publish_last_merged_coverage_and_explicit_choice_is_exact():
    current, old = yt.catalog(), yt.catalog_v2()
    async def case(db):
        await db.users.insert_many([member("current"), member("old")])
        a, b = saved(current, "current"), saved(old, "old")
        await db.your_type_sessions.insert_many([a, b])
        mixed = await yr.report(db, {"env": "preview"})
        body = next(value for value in mixed["variables"] if value["id"] == body_axis(current))
        assert mixed["edition_cohorts"] == 2 and not mixed["edition_compatible"]
        assert body["requires_edition_selection"]
        assert not {"asset_edition", "asset_manifest_sha256", "annotation_coverage", "model_edition"}.intersection(body)
        for row, bank in ((a, current), (b, old)):
            exact = await yr.report(db, {"env": "preview"}, edition=research.fingerprint(yr.instrument(row)))
            body = next(value for value in exact["variables"] if value["id"] == body_axis(bank))
            assert exact["edition_available"] and exact["edition_compatible"]
            assert body["asset_manifest_sha256"] == bank["sha256"]
            assert body["annotation_coverage"]["coverage_available"] is (bank is old)
            assert not body.get("requires_edition_selection")
    exercise(case)


def test_unobserved_shared_feature_metadata_is_marked_ambiguous_without_borrowing_v2_counts():
    current = yt.catalog()
    variables = yr.instrument_variables(yr.instrument_banks(current))
    body = variables[body_axis(current)]
    assert body["requires_edition_selection"]
    assert "annotation_coverage" not in body and "asset_manifest_sha256" not in body
