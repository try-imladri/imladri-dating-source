"""Adds 4 texting threads + 4 screen-character look-alikes (with Wikipedia images) to every archetype,
and real photos to figures. Images use the application media repository. Run: python enrich.py"""
import asyncio
import io
import os
import urllib.parse
import uuid

import requests
from dotenv import load_dotenv
from backend.database import open_database
from PIL import Image

from ai import ai_json
from content import roster

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
client, db = open_database()
UA = {"User-Agent": "imladri-content/1.0 (+https://imladri.com/support)"}

SHAPE = """{"characters": [{"name": "character name", "show": "movie or series title", "wiki_title": "exact English Wikipedia article title for the character (or null)",
"show_wiki_title": "exact English Wikipedia article title of the movie/series", "why": "one punchy line on why he's this type"}] (exactly 4 very well-known male characters from popular movies/series),
"threads": [{"title": "short scenario name", "bubbles": [{"from": "him|you", "text": "..."}] (5-7 realistic texts)}] (exactly 4 different scenarios)}"""


def wiki_image(title: str | None):
    if not title:
        return None
    try:
        r = requests.get("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_")), headers=UA, timeout=15)
        if r.status_code != 200:
            return None
        j = r.json()
        src = (j.get("thumbnail") or {}).get("source") or (j.get("originalimage") or {}).get("source")
        if not src:
            return None
        img = requests.get(src, headers=UA, timeout=20)
        im = Image.open(io.BytesIO(img.content)).convert("RGB")
        im.thumbnail((640, 640))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return buf.getvalue(), j.get("content_urls", {}).get("desktop", {}).get("page", "")
    except Exception as e:
        print("img fail", title, e, flush=True)
        return None


async def store(data: bytes, credit: str, page: str) -> str:
    mid = str(uuid.uuid4())
    await db.media.insert_one({"id": mid, "data": data, "content_type": "image/jpeg", "credit": credit, "source_page": page})
    return f"/api/media/{mid}"


async def enrich_arch(a, sem):
    async with sem:
        try:
            out = await ai_json("You write for Imladri, a sharp, warm dating-insight brand for women.",
                                f"Roster:\n{roster()}\n\nFor #{a['number']} {a['name']} ('{a['one_liner']}'). Shape:\n{SHAPE}")
            chars = []
            for c in out.get("characters", [])[:4]:
                got = await asyncio.to_thread(wiki_image, c.get("wiki_title")) or await asyncio.to_thread(wiki_image, c.get("show_wiki_title"))
                c["image_url"] = await store(got[0], "Image: Wikipedia", got[1]) if got else None
                chars.append(c)
            await db.archetypes.update_one({"slug": a["slug"]}, {"$set": {"sections.threads": out.get("threads", [])[:4], "sections.characters": chars}})
            print("ok", a["slug"], sum(1 for c in chars if c["image_url"]), flush=True)
        except Exception as e:
            print("fail", a["slug"], e, flush=True)


async def main():
    sem = asyncio.Semaphore(6)
    archs = await db.archetypes.find({"sections": {"$ne": None}, "sections.threads": {"$exists": False}}, {"_id": 0}).to_list(100)
    figs = await db.figures.find({"photo_url": {"$exists": False}, "sources": {"$exists": False}}, {"_id": 0}).to_list(100)
    for f in figs:
        got = await asyncio.to_thread(wiki_image, f["name"])
        if got:
            await db.figures.update_one({"slug": f["slug"]}, {"$set": {"photo_url": await store(got[0], "Photo: Wikimedia Commons via Wikipedia", got[1]), "photo_credit": "Photo: Wikimedia Commons"}})
            print("photo", f["slug"], flush=True)
    await asyncio.gather(*[enrich_arch(a, sem) for a in archs])
    print("done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
