"""Duplicate evidence must describe exact delivered bytes, never padded crops."""
import hashlib
import json

from PIL import Image
import pytest

from scripts.audit_your_type_image_duplicates import audit


def fixture(tmp_path):
    public = tmp_path / "public"
    plate = public / "your-type/plates/example.png"
    plate.parent.mkdir(parents=True)
    image = Image.new("RGB", (64, 32), "gray")
    image.save(plate)
    raw = plate.read_bytes()
    url = "/your-type/plates/example.png"
    bank = {"sheets": [{"url": url, "width": 64, "height": 32, "bytes": len(raw),
                         "sha256": hashlib.sha256(raw).hexdigest()}],
            "assets": [{"id": str(i), "audience": "women", "url": url,
                        "rect": {"x": i * 32, "y": 0, "w": 32, "h": 32}} for i in range(2)]}
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(bank), encoding="utf8")
    return catalog, public, plate, bank


def test_duplicate_evidence_pins_delivered_sheets_and_exact_crop_pixels(tmp_path):
    catalog, public, plate, bank = fixture(tmp_path)
    result = audit(catalog, public)
    assert result["images"] == 2 and result["pair_comparisons"] == 1
    assert result["exact_pixel_duplicates"] == [["0", "1"]]
    assert result["delivered_image_pins"][bank["sheets"][0]["url"]]["sha256"] == hashlib.sha256(plate.read_bytes()).hexdigest()


def test_changed_image_is_rejected_before_reporting_stale_catalog_evidence(tmp_path):
    catalog, public, plate, _ = fixture(tmp_path)
    Image.new("RGB", (64, 32), "white").save(plate)
    with pytest.raises(AssertionError, match="differs from catalog pin"):
        audit(catalog, public)


@pytest.mark.parametrize("rect", [
    {"x": -1, "y": 0, "w": 32, "h": 32},
    {"x": 33, "y": 0, "w": 32, "h": 32},
    {"x": 0, "y": 0, "w": 0, "h": 32},
    {"x": False, "y": 0, "w": 32, "h": 32},
])
def test_invalid_crops_cannot_be_silently_padded_or_coerced(tmp_path, rect):
    catalog, public, _, bank = fixture(tmp_path)
    bank["assets"][0]["rect"] = rect
    catalog.write_text(json.dumps(bank), encoding="utf8")
    with pytest.raises(AssertionError):
        audit(catalog, public)


def test_plate_path_cannot_escape_verified_public_directory(tmp_path):
    catalog, public, _, bank = fixture(tmp_path)
    bank["assets"][0]["url"] = "/your-type/plates/../../outside.png"
    catalog.write_text(json.dumps(bank), encoding="utf8")
    with pytest.raises(AssertionError, match="escapes plate directory"):
        audit(catalog, public)
