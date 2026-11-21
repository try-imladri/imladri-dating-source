"""Verify the actual delivered close-up edition and its historical boundaries."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from PIL import Image

from backend import your_type, your_type_research

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "backend/seed"


def test_closeup_catalog_has_exactly_500_images_per_audience_and_no_reused_asset_ids():
    bank = your_type.catalog()
    assert bank["edition"] == "your-type-synthetic-adults-v3"
    assert Counter(asset["audience"] for asset in bank["assets"]) == {"men": 500, "women": 500}
    assert len(bank["by_id"]) == 1000
    assert {asset["image_kind"] for asset in bank["assets"]} == {"portrait"}
    assert {asset["display_fit"] for asset in bank["assets"]} == {"contain"}
    archived = json.loads((SEED / "your_type_catalog.v2.json").read_bytes())
    assert not set(bank["by_id"]) & {asset["id"] for asset in archived["assets"]}
    assert bank["feature_names"] == archived["feature_names"]
    assert len(bank["feature_names"]) == 63 and len(bank["feature_groups"]) == 19


def test_old_catalog_bytes_and_source_assets_remain_available():
    for name, expected in (
        ("your_type_catalog.v1.json", "333684b6905a182b6707770b1924609f901c31b7f8cb55dd9386d6b67243c616"),
        ("your_type_catalog.v2.json", "2d0c1a68aa442845c5d6a5502a86ae57c6cae622ca2ebfc26c973195719dc6c8"),
    ):
        raw = (SEED / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected
        old = json.loads(raw)
        for sheet in old["sheets"]:
            delivered = ROOT / "frontend/public" / sheet["url"].lstrip("/")
            assert hashlib.sha256(delivered.read_bytes()).hexdigest() == sheet["sha256"]


def test_closeup_rectangles_hashes_and_review_join_every_image():
    bank = your_type.catalog()
    review_raw = (SEED / "your_type_review.v3.json").read_bytes()
    review = json.loads(review_raw)
    review_sha = hashlib.sha256(review_raw).hexdigest()
    assert b"\r" not in review_raw and review_raw.endswith(b"\n")
    assert review["version"] == "your-type-visible-review-v3"
    sheets = {sheet["id"]: sheet for sheet in bank["sheets"]}
    assert len(sheets) == len(bank["sheets"])
    for sheet in sheets.values():
        path = ROOT / "frontend/public" / sheet["url"].lstrip("/")
        assert sha(path) == sheet["sha256"] and path.stat().st_size == sheet["bytes"]
        with Image.open(path) as image:
            assert image.size == (sheet["width"], sheet["height"])
            assert image.width == image.height and image.width >= 1024
    cells = set()
    for asset in bank["assets"]:
        provenance = asset["provenance"]
        plate = review["plates"][provenance["plate_id"] + ".png"]
        cell = provenance["source_cell"]
        assert (provenance["plate_id"], cell) not in cells
        cells.add((provenance["plate_id"], cell))
        assert provenance["review_record_sha256"] == review_sha
        assert provenance["original_png_sha256"] == plate["original_png_sha256"]
        assert provenance["delivery_image_sha256"] == sheets[provenance["plate_id"]]["sha256"]
        assert asset["authored_age_band"] == plate["authored_age_bands"][cell]
        reviewed_cell = next(item for item in plate["cells"] if item["cell"] == cell)
        assert asset["feature_values"] == {**reviewed_cell["feature_values"],
                                            "authored_age_band": plate["authored_age_bands"][cell]}
        rect = asset["rect"]
        assert abs(rect["w"] - rect["h"]) <= 1 and min(rect["w"], rect["h"]) >= 250
        normalized = your_type.public_face(asset)["sprite"]
        assert normalized["sheet_width"] == asset["sheet_width"]
        assert normalized["sheet_height"] == asset["sheet_height"]
        assert rect["x"] >= 0 and rect["y"] >= 0
        assert rect["x"] + rect["w"] <= asset["sheet_width"]
        assert rect["y"] + rect["h"] <= asset["sheet_height"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_known_label_is_consistent_and_unsupported_body_cues_are_masked():
    bank = your_type.catalog()
    for group in bank["feature_groups"]:
        for audience in ("men", "women"):
            assert your_type_research.group_coverage(bank, group, audience) == group["audience_coverage"][audience]
        for asset in bank["assets"]:
            values = [asset["features"][index] for index in group["indices"]]
            masks = [asset["feature_masks"][index] for index in group["indices"]]
            assert all(masks) or not any(masks)
            if all(masks):
                assert asset["audience"] in group["applicable_audiences"]
                value = asset["feature_values"][group["id"]]
                assert values == [int(option == value) for option in group["values"]]
            else:
                assert not any(values)
            if group["id"] == "visible_build":
                assert not any(masks) and "visible_build" not in asset["feature_values"]
            if group["id"] == "facial_hair" and asset["audience"] == "women":
                assert not any(masks)


def test_both_audiences_have_substantial_visible_shade_variation():
    bank = your_type.catalog()
    for audience in ("men", "women"):
        counts = Counter(asset["feature_values"].get("skin_tone", "unknown")
                         for asset in bank["assets"] if asset["audience"] == audience)
        assert all(counts[shade] >= 75 for shade in ("light", "medium", "deep")), counts
        assert counts["unknown"] <= 50
