"""Integrity and coverage checks against the actual delivered asset edition."""
from collections import Counter
import hashlib
import json
from pathlib import Path

import pytest

from backend import your_type, your_type_model, your_type_research

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "backend/seed"


def read(name):
    return json.loads((SEED / name).read_text(encoding="utf8"))


def test_hashed_catalog_and_review_have_canonical_platform_independent_bytes():
    for name in ("your_type_catalog.v2.json", "your_type_catalog.v1.json", "your_type_review.v2.json"):
        raw = (SEED / name).read_bytes()
        assert b"\r" not in raw and raw.endswith(b"\n")


def test_actual_catalog_preserves_frozen_history_and_never_backfills_unknown_age():
    old = read("your_type_catalog.v1.json")
    bank = your_type.catalog_v2()
    assert hashlib.sha256((SEED / "your_type_catalog.v1.json").read_bytes()).hexdigest() == "333684b6905a182b6707770b1924609f901c31b7f8cb55dd9386d6b67243c616"
    assert bank["edition"] != old["edition"]
    assert bank["feature_names"][:45] == old["feature_names"]
    assert len(bank["feature_groups"]) == 19 and len(bank["feature_names"]) == 63
    assert bank["model_edition"] == your_type_model.EDITION
    current = bank["by_id"]
    for original in old["assets"]:
        asset = current[original["id"]]
        assert {k: v for k, v in asset.items() if k not in ("features", "feature_masks", "image_kind")} == {
            k: v for k, v in original.items() if k not in ("features", "feature_masks")}
        assert asset["features"][:45] == original["features"]
        assert asset["feature_masks"][:45] == original["feature_masks"]
        assert asset["features"][45:] == [0] * 18
        assert asset["feature_masks"][45:] == [False] * 18


def test_actual_applicable_groups_have_real_variation_without_unknown_defaults():
    bank = your_type.catalog_v2()
    assert Counter(a["audience"] for a in bank["assets"]) == {"men": 368, "women": 368}
    for group in bank["feature_groups"]:
        for audience in ("men", "women"):
            measured = your_type_research.group_coverage(bank, group, audience)
            assert measured == group["audience_coverage"][audience]
            if audience not in group["applicable_audiences"]:
                assert group["id"] == "facial_hair" and audience == "women"
                assert not measured["applicable"] and not measured["variation_available"]
                continue
            assert measured["variation_available"]
            assert all(count >= 3 for count in measured["value_counts"].values()), (group["id"], audience)
        for asset in bank["assets"]:
            indices = group["indices"]
            if asset["id"].startswith("yt-v2-") and asset["audience"] not in group["applicable_audiences"]:
                assert not any(asset["feature_masks"][i] for i in indices)
                continue
            known = [asset["feature_masks"][i] for i in indices]
            assert all(known) or not any(known)
            if all(known):
                value = asset["feature_values"][group["id"]]
                assert [asset["features"][i] for i in indices] == [int(v == value) for v in group["values"]]
            else:
                assert not any(asset["features"][i] for i in indices)


def test_new_original_provenance_rectangles_and_delivered_asset_hashes():
    bank = your_type.catalog_v2()
    review_path = SEED / "your_type_review.v2.json"
    review = read(review_path.name)
    assert len(review["plates"]) == 6
    review_sha = hashlib.sha256(review_path.read_bytes()).hexdigest()
    new = [asset for asset in bank["assets"] if asset["id"].startswith("yt-v2-")]
    assert len(new) == 96 and Counter(a["image_kind"] for a in new) == {"portrait": 64, "full_body": 32}
    sheets = {s["id"]: s for s in bank["sheets"]}
    for asset in new:
        p = asset["provenance"]
        sheet = sheets[p["plate_id"]]
        plate = review["plates"][p["plate_id"] + ".png"]
        assert p["review_record_sha256"] == review_sha
        assert p["original_png_sha256"] == plate["original_png_sha256"]
        assert asset["authored_age_band"] == plate["authored_age_bands"][p["source_cell"]]
        delivered = ROOT / "frontend/public" / asset["url"].lstrip("/")
        assert hashlib.sha256(delivered.read_bytes()).hexdigest() == p["delivery_jpeg_sha256"] == sheet["sha256"]
        assert delivered.stat().st_size == sheet["bytes"]
        rect, cell = asset["rect"], p["source_cell"]
        assert rect == {"x": cell % 4 * 256 + 3, "y": cell // 4 * 384 + 3, "w": 250, "h": 378}
        assert asset["adult_reviewed"] is True and asset["synthetic"] is True
        assert asset["sprite"]["rect"] == rect


@pytest.mark.parametrize("audience", ["men", "women", "both"])
def test_real_new_catalog_sampler_delivers_body_variety_without_repetition(audience):
    bank = your_type.catalog_v2()
    state = your_type.new_state(bank, audience)
    your_type.fill(state, bank, audience, "actual-736-catalog")
    issued = [card["face_id"] for card in state["cards"] + state["replacement_queue"]]
    assert len(set(issued)) == len(issued)
    for group in (("men", "women") if audience == "both" else (audience,)):
        cards = [bank["by_id"][i] for i in issued if bank["by_id"][i]["audience"] == group]
        assert len(cards) >= 12
        assert sum(a["image_kind"] == "full_body" for a in cards) >= len(cards) // 4
        assert all(a["audience"] == group for a in cards)
