"""Build the expanded, versioned synthetic-adult catalog from reviewed sources.

Only format conversion is performed: original PNGs remain intact. Existing v1
annotations are preserved verbatim in the first 45 dimensions; new dimensions
are unknown for those assets. Run with --asset-dir and --review pointing at the
retained generation outputs and the versioned review record.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
EDITION = "your-type-synthetic-adults-v2"
MODEL = "your-type-audience-logistic-v2"
EXTRA_GROUPS = [
    ("authored_age_band", "Fictional adult age range", ["18_24", "25_34", "35_44", "45_54", "55_64", "65_plus"],
     ["18–24", "25–34", "35–44", "45–54", "55–64", "65+"],
     "Fictional age range specified when creating the asset, not an apparent-age estimate or a real person's age."),
    ("nose_width", "Visible nose width", ["narrow", "balanced", "broad"], ["Narrow", "Balanced", "Broad"], None),
    ("nose_tip", "Visible nose tip", ["rounded", "defined"], ["Rounded", "Defined"], None),
    ("cheek_definition", "Visible cheek definition", ["soft", "moderate", "prominent"], ["Soft", "Moderate", "Prominent"], None),
    ("visible_build", "Clothed body outline", ["slender", "medium", "athletic", "fuller"],
     ["Slender outline", "Medium outline", "More defined outline", "Fuller outline"],
     "Coarse clothed appearance in full-body assets only. Not weight, health, strength, fitness or measured body composition."),
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def coverage(assets, group, audience):
    rows = [a for a in assets if a["audience"] == audience]
    applicable = audience in group["applicable_audiences"]
    counts = Counter({value: 0 for value in group["values"]})
    known = 0
    if applicable:
        for asset in rows:
            if all(asset["feature_masks"][i] for i in group["indices"]):
                selected = [v for i, v in zip(group["indices"], group["values"]) if asset["features"][i] == 1]
                if len(selected) != 1:
                    raise ValueError("Known one-hot group must select exactly one value")
                counts.update(selected)
                known += 1
    return {"applicable": applicable, "coverage_available": known > 0,
            "covered_portraits": known, "total_portraits": len(rows),
            "variation_available": sum(n > 0 for n in counts.values()) >= 2,
            "value_counts": dict(counts)}


def build(asset_dir, review_path):
    original = ROOT / "backend/seed/your_type_catalog.v1.json"
    bank = json.loads(original.read_text(encoding="utf8"))
    review = json.loads(review_path.read_text(encoding="utf8"))
    assert review["version"] == "your-type-visible-review-v2"
    assert sha(original) == review["legacy_manifest_sha256"]
    original_dimensions = len(bank["feature_names"])
    assert original_dimensions == 45
    bank["edition"], bank["model_edition"] = EDITION, MODEL
    for group in bank["feature_groups"]:
        group["applicable_audiences"] = ["men"] if group["id"] == "facial_hair" else ["men", "women"]
    for identity, label, values, labels, basis in EXTRA_GROUPS:
        start = len(bank["feature_names"])
        bank["feature_names"].extend(identity + ":" + value for value in values)
        bank["feature_groups"].append({"id": identity, "label": label, "values": values,
            "value_labels": dict(zip(values, labels)), "indices": list(range(start, start + len(values))),
            "encoding": "one_hot", "unknown": "all_feature_masks_false_for_group",
            "basis": basis or "Coarse visible-output review. Ambiguous and occluded values remain unknown.",
            "applicable_audiences": ["men", "women"]})
    added = len(bank["feature_names"]) - original_dimensions
    for asset in bank["assets"]:
        asset["features"] += [0] * added
        asset["feature_masks"] += [False] * added
        asset["image_kind"] = "portrait"
    plates_dir = ROOT / "frontend/public/your-type/plates"
    plates_dir.mkdir(parents=True, exist_ok=True)
    for name, plate in review["plates"].items():
        path = asset_dir / name
        assert sha(path) == plate["original_png_sha256"], name
        assert len(plate["cells"]) == 16 and {r["cell"] for r in plate["cells"]} == set(range(16))
        identity = path.stem
        target = plates_dir / (identity + ".jpg")
        with Image.open(path) as image:
            assert image.size == (1024, 1536)
            image.convert("RGB").save(target, format="JPEG", quality=90, subsampling=0, optimize=True)
        delivery_sha = sha(target)
        url = "/your-type/plates/" + target.name
        bank["sheets"].append({"id": identity, "url": url, "width": 1024, "height": 1536,
            "bytes": target.stat().st_size, "sha256": delivery_sha,
            "original_png_sha256": plate["original_png_sha256"],
            "format_conversion": "Pillow JPEG quality90, subsampling0, same dimensions; no raster crop, resize or content edits"})
        for row in sorted(plate["cells"], key=lambda r: r["cell"]):
            cell = row["cell"]
            values = deepcopy(row["feature_values"])
            assert "authored_age_band" not in values
            values["authored_age_band"] = plate["authored_age_bands"][cell]
            vector, masks = [0] * len(bank["feature_names"]), [False] * len(bank["feature_names"])
            for group in bank["feature_groups"]:
                value = values.get(group["id"])
                if value is None or plate["audience"] not in group["applicable_audiences"]:
                    continue
                assert value in group["values"], (name, cell, group["id"], value)
                for index, option in zip(group["indices"], group["values"]):
                    masks[index], vector[index] = True, int(option == value)
            rect = {"x": (cell % 4) * 256 + 3, "y": (cell // 4) * 384 + 3, "w": 250, "h": 378}
            bank["assets"].append({"id": identity + "-" + str(cell).zfill(2), "audience": plate["audience"],
                "image_kind": plate["image_kind"], "display_fit": "contain", "active": True, "synthetic": True,
                "adult_reviewed": True,
                "adult_review_basis": "Creation prompt specifies adults; direct visual review confirms adult appearance and ordinary clothing.",
                "authored_age_band": values["authored_age_band"],
                "age_basis": "Fictional age range specified in creation prompt, not estimated from appearance.",
                "presentation": "masculine" if plate["audience"] == "men" else "feminine",
                "feature_values": values, "features": vector, "feature_masks": masks,
                "url": url, "width": rect["w"], "height": rect["h"], "sheet_width": 1024, "sheet_height": 1536,
                "rect": rect, "sprite": {"url": url, "x": rect["x"], "y": rect["y"], "width": rect["w"],
                    "height": rect["h"], "source_width": 1024, "source_height": 1536, "rect": rect},
                "provenance": {"plate_id": identity, "source_cell": cell,
                    "original_png_sha256": plate["original_png_sha256"], "delivery_jpeg_sha256": delivery_sha,
                    "review_edition": review["version"], "review_record_sha256": sha(review_path)}})
    bank["feature_coverage"] = {}
    for group in bank["feature_groups"]:
        group["audience_coverage"] = {a: coverage(bank["assets"], group, a) for a in ("men", "women")}
        counts = Counter({v: 0 for v in group["values"]})
        counts.update(asset["feature_values"].get(group["id"], "unknown") for asset in bank["assets"])
        bank["feature_coverage"][group["id"]] = dict(counts)
    bank["provenance"] = {**bank["provenance"], "legacy_manifest_sha256": sha(original),
        "legacy_asset_count": 640, "added_asset_count": 96,
        "annotation_basis": "Coarse machine visual review, with separate second machine review for head plates; no independent human or biometric validation.",
        "v2_review_record": "backend/seed/your_type_review.v2.json", "v2_review_sha256": sha(review_path),
        "new_exact_prompts_location": "E:/codex/imladri-your-type-complete-20261118/evidence/*-generation-provenance.json",
        "new_originals_retained": str(asset_dir).replace("\\", "/"),
        "adult_prompt_band": "Existing640:30–60; new96:adult21–24,25–64,65–75. Catalogbins are fictional creation labels."}
    bank["limitations"] += [
        "New coarse labels are machine judgments and may disagree with other reviewers; no scientific accuracy or human validation is claimed.",
        "Visible clothed outline is not measured weight, fitness, health or body composition; clothing, pose and styling can confound choices.",
        "Fictional age ranges were authored during creation, not inferred from faces. Existing 30–60 assets remain unknown for finer age bins.",
        "Actual height cannot be read from these images. Private stated age/height ranges are explicit preferences, not image measurements or research enrollment.",
        "Head and full-body presentation can itself affect preference. Models describe this catalog and do not establish real-world compatibility.",
        "Facial-hair preference is not modeled for the women's catalog, which has no authored facial-hair variation.",
    ]
    target = ROOT / "backend/seed/your_type_catalog.json"
    target.write_text(json.dumps(bank, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf8", newline="\n")
    return {"edition": EDITION, "model_edition": MODEL, "assets": len(bank["assets"]),
        "feature_groups": len(bank["feature_groups"]), "features": len(bank["feature_names"]),
        "audiences": dict(Counter(a["audience"] for a in bank["assets"])), "manifest_sha256": sha(target),
        "review_sha256": sha(review_path), "legacy_manifest_sha256": sha(original),
        "new_delivery_bytes": sum(s["bytes"] for s in bank["sheets"] if s["id"].startswith("yt-v2-"))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument("--review", type=Path, default=ROOT / "backend/seed/your_type_review.v2.json")
    args = parser.parse_args()
    print(json.dumps(build(args.asset_dir, args.review), indent=2))
