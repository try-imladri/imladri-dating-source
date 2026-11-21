"""Flag exact or perceptually similar catalog crops for visual review.

This compares pixels, not biometric identities. It never claims to establish
that two fictional portraits depict the same or different person.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


def signature(image):
    gray = np.asarray(image.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=float)
    x = np.arange(32)
    basis = np.cos(np.pi * (2 * x[np.newaxis, :] + 1) * x[:, np.newaxis] / 64)
    frequency = (basis @ gray @ basis.T)[:8, :8].flatten()[1:]
    phash = sum(int(value > np.median(frequency)) << index for index, value in enumerate(frequency))
    small = np.asarray(image.convert("L").resize((9, 8), Image.Resampling.LANCZOS))
    dhash = sum(int(value) << index for index, value in enumerate((small[:, 1:] > small[:, :-1]).flatten()))
    thumb = np.asarray(image.convert("RGB").resize((32, 32), Image.Resampling.LANCZOS), dtype=float)
    return phash, dhash, thumb


def audit(catalog, public_dir, nearest_limit=60):
    raw = catalog.read_bytes()
    bank = json.loads(raw)
    public_dir = public_dir.resolve()
    plate_dir = (public_dir / "your-type/plates").resolve()
    sheets = {sheet["url"]: sheet for sheet in bank["sheets"]}
    assert len(sheets) == len(bank["sheets"]), "Duplicate sheet URL"
    loaded = {}
    image_pins = {}
    entries = []
    for asset in bank["assets"]:
        assert asset["url"].startswith("/your-type/plates/")
        source = (public_dir / asset["url"].lstrip("/")).resolve()
        assert source.is_relative_to(plate_dir), "Portrait path escapes plate directory"
        sheet = sheets[asset["url"]]
        if source not in loaded:
            image_raw = source.read_bytes()
            digest = hashlib.sha256(image_raw).hexdigest()
            assert digest == sheet["sha256"], "Delivered sheet differs from catalog pin"
            assert len(image_raw) == sheet["bytes"], "Delivered sheet byte count differs"
            with Image.open(source) as image:
                assert image.size == (sheet["width"], sheet["height"]), "Delivered sheet dimensions differ"
                loaded[source] = image.convert("RGB")
            image_pins[asset["url"]] = {"sha256": digest, "bytes": len(image_raw),
                                      "width": sheet["width"], "height": sheet["height"]}
        if "delivery_image_sha256" in asset.get("provenance", {}):
            assert asset["provenance"]["delivery_image_sha256"] == sheet["sha256"], "Asset provenance differs from sheet"
        rect = asset["rect"]
        assert all(type(rect[key]) is int for key in ("x", "y", "w", "h")), "Crop coordinates must be integers"
        assert rect["x"] >= 0 and rect["y"] >= 0 and rect["w"] > 0 and rect["h"] > 0
        assert rect["x"] + rect["w"] <= loaded[source].width and rect["y"] + rect["h"] <= loaded[source].height, "Crop leaves delivered image"
        crop = loaded[source].crop((rect["x"], rect["y"], rect["x"] + rect["w"], rect["y"] + rect["h"]))
        digest = hashlib.sha256(f"RGB:{crop.width}:{crop.height}:".encode() + crop.tobytes()).hexdigest()
        phash, dhash, thumb = signature(crop)
        entries.append((asset["id"], asset["audience"], digest, phash, dhash, thumb))
    exact, candidates, nearest = [], [], []
    comparisons = 0
    for offset, a in enumerate(entries):
        matches = []
        for b in entries[offset + 1:]:
            comparisons += 1
            p, d = (a[3] ^ b[3]).bit_count(), (a[4] ^ b[4]).bit_count()
            if a[2] == b[2]:
                exact.append([a[0], b[0]])
            # Small grayscale/hash distance is only a review trigger, not identity.
            if p <= 12 or d <= 12:
                rmse = float(np.sqrt(np.mean((a[5] - b[5]) ** 2)))
                row = {"left": a[0], "right": b[0], "phash_distance": p, "dhash_distance": d,
                       "rgb_rmse": round(rmse, 3), "same_audience": a[1] == b[1]}
                matches.append(row)
                if (p <= 6 and d <= 12) or (p <= 12 and d <= 6) or rmse < 10:
                    candidates.append(row)
        if matches:
            nearest.append(min(matches, key=lambda row: (row["phash_distance"] + row["dhash_distance"], row["rgb_rmse"])))
    nearest.sort(key=lambda row: (row["phash_distance"] + row["dhash_distance"], row["rgb_rmse"]))
    return {"catalog_sha256": hashlib.sha256(raw).hexdigest(), "images": len(entries),
            "delivered_image_pins": image_pins,
            "pair_comparisons": comparisons, "exact_pixel_duplicates": exact,
            "perceptual_review_candidates": candidates, "nearest_for_visual_review": nearest[:nearest_limit],
            "method": "Verified delivered sheet bytes/dimensions and bounded catalog crops; dimension-bound RGB SHA256, 63-bit DCT pHash, 64-bit dHash and 32px RGB RMSE.",
            "limitation": "Pixel similarity is not biometric identification or proof of unique fictional identities. Human-like visual template repetition still requires visual review.",
            "status": "exact_duplicates_found" if exact else "visual_review_required"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--public-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.catalog, args.public_dir)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf8")
    print(json.dumps({key: result[key] for key in ("images", "pair_comparisons", "status")}))
