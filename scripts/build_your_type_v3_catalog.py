"""Build the reviewed close-up portrait edition, retaining older editions.

Generation and visual review happen separately. This builder only converts
original PNGs to WebP at unchanged dimensions and encodes verified annotations.
Unknown values stay masked; prompt instructions are never used as visual labels.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
EDITION = "your-type-synthetic-adults-v3"
PRIOR_SHA = "2d0c1a68aa442845c5d6a5502a86ae57c6cae622ca2ebfc26c973195719dc6c8"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def coverage(assets, group, audience):
    rows = [asset for asset in assets if asset["audience"] == audience]
    applicable = audience in group["applicable_audiences"]
    counts = Counter({value: 0 for value in group["values"]})
    known = 0
    if applicable:
        for asset in rows:
            if all(asset["feature_masks"][index] for index in group["indices"]):
                selected = [value for index, value in zip(group["indices"], group["values"])
                            if asset["features"][index] == 1]
                assert len(selected) == 1
                counts.update(selected)
                known += 1
    return {"applicable": applicable, "coverage_available": known > 0,
            "covered_portraits": known, "total_portraits": len(rows),
            "variation_available": sum(count > 0 for count in counts.values()) >= 2,
            "value_counts": dict(counts)}


def build(asset_dir, reviews):
    original = ROOT / "backend/seed/your_type_catalog.v2.json"
    assert sha(original) == PRIOR_SHA, "Frozen v2 bytes must remain unchanged"
    prior = json.loads(original.read_text(encoding="utf8"))
    bank = deepcopy(prior)
    bank["edition"] = EDITION
    bank["assets"], bank["sheets"] = [], []
    bank["selection_presentation"] = "close_up_faces"
    # Keep vector indices stable. Body measurements cannot be learned from faces.
    for group in bank["feature_groups"]:
        if group["id"] == "visible_build":
            group["basis"] = "Not represented in this face-only edition; no body outline is inferred from a face."
        group.pop("audience_coverage", None)
    plates = {}
    for path in reviews:
        reviewed = json.loads(path.read_text(encoding="utf8"))
        assert reviewed["version"] == "your-type-visible-review-v3"
        assert not set(plates) & set(reviewed["plates"])
        plates.update(reviewed["plates"])
    review = {"version": "your-type-visible-review-v3", "previous_manifest_sha256": PRIOR_SHA,
              "basis": "Machine visual review of original generated close-up faces. Unknown or ambiguous features remain unlabelled; no independent human validation is claimed.",
              "limits": ["Authored adult ages are creation metadata, not inferred actual age.",
                         "Visible skin shade is not ethnicity or race.",
                         "No sexuality, personality, height, weight, health or relationship compatibility is inferred from a face.",
                         "Image duplicate screening does not prove fictional biometric identity uniqueness."],
              "plates": plates}
    review_path = ROOT / "backend/seed/your_type_review.v3.json"
    review_bytes = (json.dumps(review, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf8")
    review_hash = hashlib.sha256(review_bytes).hexdigest()
    targets = ROOT / "frontend/public/your-type/plates"
    seen_ids = set()
    for name, plate in sorted(plates.items()):
        assert Path(name).name == name and name.endswith(".png")
        assert name.startswith("yt-v3-close-")
        source = asset_dir / name
        assert sha(source) == plate["original_png_sha256"], name
        audience = plate["audience"]
        assert audience in ("men", "women") and plate["image_kind"] == "portrait"
        columns, rows = plate["columns"], plate["rows"]
        assert (columns, rows) in ((4, 4), (2, 2))
        cells = plate["cells"]
        assert len({row["cell"] for row in cells}) == len(cells)
        assert len(plate["authored_age_bands"]) == columns * rows
        identity = source.stem
        target = targets / (identity + ".webp")
        with Image.open(source) as image:
            width, height = image.size
            assert width == height and width >= 1024
            image.convert("RGB").save(target, format="WEBP", quality=88, method=6)
        delivery_sha = sha(target)
        url = "/your-type/plates/" + target.name
        bank["sheets"].append({"id": identity, "url": url, "width": width, "height": height,
            "bytes": target.stat().st_size, "sha256": delivery_sha,
            "original_png_sha256": plate["original_png_sha256"],
            "format_conversion": "Pillow WebP quality88, method6, same dimensions; no raster crop, resize or content edits"})
        for row in sorted(cells, key=lambda row: row["cell"]):
            cell = row["cell"]
            assert type(cell) is int and 0 <= cell < columns * rows
            values = deepcopy(row["feature_values"])
            assert "authored_age_band" not in values and "visible_build" not in values
            known_groups = {group["id"] for group in bank["feature_groups"]}
            assert set(values) <= known_groups
            values["authored_age_band"] = plate["authored_age_bands"][cell]
            vector, masks = [0] * len(bank["feature_names"]), [False] * len(bank["feature_names"])
            for group in bank["feature_groups"]:
                value = values.get(group["id"])
                if value is None:
                    continue
                assert audience in group["applicable_audiences"], (identity, cell, group["id"])
                assert value in group["values"], (identity, cell, group["id"], value)
                for index, option in zip(group["indices"], group["values"]):
                    masks[index], vector[index] = True, int(option == value)
            x = cell % columns * width // columns + 3
            y = cell // columns * height // rows + 3
            right = (cell % columns + 1) * width // columns - 3
            bottom = (cell // columns + 1) * height // rows - 3
            rect = {"x": x, "y": y, "w": right - x, "h": bottom - y}
            asset_id = identity + "-" + str(cell).zfill(2)
            assert asset_id not in seen_ids
            seen_ids.add(asset_id)
            bank["assets"].append({"id": asset_id, "audience": audience, "image_kind": "portrait",
                "display_fit": "contain", "active": True, "synthetic": True, "adult_reviewed": True,
                "adult_review_basis": "Creation prompt specifies adults; visual output reviewed for adult appearance.",
                "authored_age_band": values["authored_age_band"],
                "age_basis": "Fictional adult age specified during creation, not estimated from appearance.",
                "presentation": "masculine" if audience == "men" else "feminine",
                "feature_values": values, "features": vector, "feature_masks": masks,
                "url": url, "width": rect["w"], "height": rect["h"], "sheet_width": width, "sheet_height": height,
                "rect": rect, "sprite": {"url": url, "x": x, "y": y, "width": rect["w"], "height": rect["h"],
                    "source_width": width, "source_height": height, "rect": rect},
                "provenance": {"plate_id": identity, "source_cell": cell,
                    "original_png_sha256": plate["original_png_sha256"], "delivery_image_sha256": delivery_sha,
                    "review_edition": review["version"], "review_record_sha256": review_hash}})
    assert Counter(asset["audience"] for asset in bank["assets"]) == {"men": 500, "women": 500}
    bank["feature_coverage"] = {}
    for group in bank["feature_groups"]:
        group["audience_coverage"] = {audience: coverage(bank["assets"], group, audience) for audience in ("men", "women")}
        counts = Counter({value: 0 for value in group["values"]})
        counts.update(asset["feature_values"].get(group["id"], "unknown") for asset in bank["assets"])
        bank["feature_coverage"][group["id"]] = dict(counts)
    bank["provenance"] = {"previous_manifest_sha256": PRIOR_SHA,
        "generation": "Original synthetic adult portraits created with built-in image_gen.imagegen; no third-party face-bank assets reused.",
        "annotation_basis": review["basis"], "review_record": "backend/seed/your_type_review.v3.json",
        "review_sha256": review_hash, "originals_retained": str(asset_dir).replace("\\", "/"),
        "historical_editions_retained": ["your-type-synthetic-adults-v1", "your-type-synthetic-adults-v2"]}
    bank["limitations"] = review["limits"] + [
        "Choices reflect these synthetic images, including lighting, pose and hair framing, and do not establish real-world compatibility.",
        "No validated population attractiveness baseline is published.",
        "Facial-hair variation is not modelled in the women's catalog.",
        "The face-only edition cannot measure clothed body outline; earlier editions retain their original evidence."]
    output = (json.dumps(bank, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf8")
    assert len(output) <= 4 * 1024 * 1024
    review_path.write_bytes(review_bytes)
    (ROOT / "backend/seed/your_type_catalog.json").write_bytes(output)
    return {"edition": EDITION, "manifest_sha256": hashlib.sha256(output).hexdigest(),
            "review_sha256": review_hash, "images": len(bank["assets"]), "sheets": len(bank["sheets"]),
            "counts": dict(Counter(asset["audience"] for asset in bank["assets"])),
            "delivery_bytes": sum(sheet["bytes"] for sheet in bank["sheets"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, nargs="+", required=True)
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.asset_dir, arguments.reviews), indent=2))
