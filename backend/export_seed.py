"""Export archetype/figure content and every picture they use into backend/seed/ so it ships with the code. Run: python export_seed.py"""
import asyncio
import json
import os
import re

from enrich import db
from content import FIGURES
from regen import photo_for

SEED = os.path.join(os.path.dirname(__file__), "seed")
MEDIA = os.path.join(SEED, "media")
EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif", "image/svg+xml": "svg"}


async def main():
    os.makedirs(MEDIA, exist_ok=True)
    async for a in db.archetypes.find({"sections.characters": {"$elemMatch": {"image_url": None}}}, {"_id": 0, "slug": 1, "sections.characters": 1}):
        chars = a["sections"]["characters"]
        for c in chars:
            if not c.get("image_url"):
                c["image_url"] = await photo_for(c)
        await db.archetypes.update_one({"slug": a["slug"]}, {"$set": {"sections.characters": chars}})
    male = {d["slug"]: d["sections"] async for d in db.archetypes.find({"gender": {"$ne": "female"}, "sections": {"$ne": None}}, {"_id": 0, "slug": 1, "sections": 1})}
    female = {d["slug"]: d["sections"] async for d in db.archetypes.find({"gender": "female", "sections": {"$ne": None}}, {"_id": 0, "slug": 1, "sections": 1})}
    figures = {d["slug"]: {k: d[k] for k in ("sections", "identity", "matches", "photo_url", "photo_credit", "sources") if k in d}
               async for d in db.figures.find({"sections": {"$ne": None}, "slug": {"$in": [f[0] for f in FIGURES]}}, {"_id": 0})}
    for name, data in (("male_archetypes.json", male), ("female_archetypes.json", female), ("figures.json", figures)):
        with open(os.path.join(SEED, name), "w") as fh:
            json.dump(data, fh, indent=1)
    ids = sorted(set(re.findall(r"/api/media/([0-9a-f-]{36})", json.dumps([male, female, figures]))))
    manifest, missing = {}, []
    for mid in ids:
        m = await db.media.find_one({"id": mid}, {"_id": 0})
        if not m:
            missing.append(mid)
            continue
        fn = f"{mid}.{EXT.get(m['content_type'], 'bin')}"
        with open(os.path.join(MEDIA, fn), "wb") as fh:
            fh.write(m["data"])
        manifest[mid] = {"file": fn, "content_type": m["content_type"], "credit": m.get("credit"), "source_page": m.get("source_page")}
    with open(os.path.join(SEED, "media_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    still = await db.archetypes.count_documents({"sections.characters": {"$elemMatch": {"image_url": None}}})
    print(json.dumps({"male": len(male), "female": len(female), "figures": len(figures), "images": len(manifest),
                      "missing_media": missing, "characters_without_photo": still}))


if __name__ == "__main__":
    asyncio.run(main())
