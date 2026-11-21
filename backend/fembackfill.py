"""Retry missing character photos on female archetype pages, then refresh female_archetypes.json. Run: python fembackfill.py"""
import asyncio
import json

from content import FEMALE_FILE
from enrich import db
from regen import photo_for


async def main():
    fixed = 0
    async for a in db.archetypes.find({"gender": "female", "sections.characters": {"$elemMatch": {"image_url": None}}}, {"_id": 0, "slug": 1, "sections.characters": 1}):
        chars = a["sections"]["characters"]
        for c in chars:
            if not c.get("image_url"):
                c["image_url"] = await photo_for(c)
                fixed += bool(c["image_url"])
                await asyncio.sleep(1)
        await db.archetypes.update_one({"slug": a["slug"]}, {"$set": {"sections.characters": chars}})
    out = {d["slug"]: d["sections"] async for d in db.archetypes.find({"gender": "female", "sections": {"$ne": None}}, {"_id": 0, "slug": 1, "sections": 1})}
    with open(FEMALE_FILE, "w") as fh:
        json.dump(out, fh, indent=1)
    missing = await db.archetypes.count_documents({"gender": "female", "sections.characters": {"$elemMatch": {"image_url": None}}})
    print("fixed", fixed, "pages still missing a photo", missing, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
