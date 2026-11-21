"""In-depth (v2) pages for the female archetypes: richer sections, 4 texting threads, 4 screen characters with photos.
Run: python femregen.py  (only female pages without sections.v == 2; writes female_archetypes.json)"""
import asyncio
import json

from ai import ai_json
from content import roster, FEMALE_FILE, FEMALE_ARCHETYPES
from enrich import db
from regen import ITEM, photo_for

ARCH = f"""{{"identity": "one punchy identity line",
"summary": ["4 rich paragraphs: who she is, why she does it, how it feels to date her, how it usually ends"],
"dating": ["an everyday date that brings this archetype to life", "a second moment that reveals how the reader could navigate the dynamic"],
"texting_patterns": {{"text": ["2 paragraphs on her texting rhythm, tells and what changes over time"]}},
"threads": [{{"title": "scenario name", "note": "one line: what this thread reveals", "bubbles": [{{"from": "her|you", "text": "..."}}] (6-8 realistic texts)}}] (exactly 4 different scenarios),
"characters": [{{"name": "character", "show": "movie/series title", "actor": "actress name or null if animated", "actor_wiki_title": "exact English Wikipedia title of the actress or null",
  "wiki_title": "exact English Wikipedia title of the character article or null", "why": "2 sentences on why she is this type"}}] (exactly 4 very well-known FEMALE characters, prefer live-action),
"interested": [{ITEM}] (6), "not_interested": [{ITEM}] (6), "red_flags": [{ITEM}] (6), "green_flags": [{ITEM}] (5),
"what_to_say": [{{"line": "exact words", "why": "why it works"}}] (5), "never_say": [{{"line": "exact words", "why": "why it backfires"}}] (5),
"manipulation": [{{"name": "pattern name", "how_it_works": "2 sentences", "defense": "how you protect yourself"}}] (5),
"leverage": [{ITEM}] (5),
"closest_cousin": {{"number": <a different female roster number>, "text": "a paragraph comparing them", "tell": "the single tell that separates them"}}}}"""

SYS = ("You write for Imladri, a sharp, warm, funny-but-wise insight brand that helps people read who they're dating. In-depth, specific, vivid. "
       "This page is about a WOMAN archetype, written to the reader ('you') who is dating or talking to her - any gender. Use she/her. "
       "Confident about actions, honest about uncertainty on motives. No diagnoses, no clinical labels, never misogynistic or demeaning: describe behaviour, not worth.")


async def regen(a, sem):
    async with sem:
        for attempt in range(3):
            try:
                s = await ai_json(SYS, f"Female roster:\n{roster(True)}\n\nWrite the full in-depth page for #{a['number']} {a['name']} ('{a['one_liner']}'). Shape:\n{ARCH}")
                cc = s.get("closest_cousin") or {}
                if int(cc.get("number") or 0) not in {n for n, *_ in FEMALE_ARCHETYPES} or cc.get("number") == a["number"]:
                    raise ValueError("bad cousin")
                for c in s.get("characters", []):
                    c["image_url"] = await photo_for(c)
                s["v"] = 2
                await db.archetypes.update_one({"slug": a["slug"]}, {"$set": {"sections": s, "identity": s.get("identity") or a["identity"]}})
                print("ok", a["slug"], sum(1 for c in s.get("characters", []) if c.get("image_url")), flush=True)
                return
            except Exception as e:
                print("fail", a["slug"], attempt, e, flush=True)


async def main():
    sem = asyncio.Semaphore(8)
    archs = await db.archetypes.find({"gender": "female", "sections.v": {"$ne": 2}}, {"_id": 0}).to_list(100)
    await asyncio.gather(*[regen(a, sem) for a in archs])
    out = {d["slug"]: d["sections"] async for d in db.archetypes.find({"gender": "female", "sections": {"$ne": None}}, {"_id": 0, "slug": 1, "sections": 1})}
    with open(FEMALE_FILE, "w") as fh:
        json.dump(out, fh, indent=1)
    print("done", len(out), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
