"""Deep regeneration of every archetype + figure page (richer sections, 4 threads, 4 characters with actor photos).
Run: python regen.py  (idempotent: only pages without sections.v == 2)"""
import asyncio
import os

from ai import ai_json
from content import roster
from enrich import db, store, wiki_image

ITEM = '{"point": "short bold claim", "detail": "2 sentences explaining it with a concrete example"}'
ARCH = f"""{{"identity": "one punchy identity line",
"summary": ["4 rich paragraphs: who he is, why he does it, how it feels to date him, how it usually ends"],
"dating": ["an everyday date that brings this archetype to life", "a second moment that reveals how the reader could navigate the dynamic"],
"texting_patterns": {{"text": ["2 paragraphs on his texting rhythm, tells and what changes over time"]}},
"threads": [{{"title": "scenario name", "note": "one line: what this thread reveals", "bubbles": [{{"from": "him|you", "text": "..."}}] (6-8 realistic texts)}}] (exactly 4 different scenarios),
"characters": [{{"name": "character", "show": "movie/series title", "actor": "actor name or null if animated", "actor_wiki_title": "exact English Wikipedia title of the actor or null",
  "wiki_title": "exact English Wikipedia title of the character article or null", "why": "2 sentences on why he is this type"}}] (exactly 4 very well-known male characters, prefer live-action),
"interested": [{ITEM}] (6), "not_interested": [{ITEM}] (6), "red_flags": [{ITEM}] (6), "green_flags": [{ITEM}] (5),
"what_to_say": [{{"line": "exact words", "why": "why it works"}}] (5), "never_say": [{{"line": "exact words", "why": "why it backfires"}}] (5),
"manipulation": [{{"name": "pattern name", "how_it_works": "2 sentences", "defense": "how she protects herself"}}] (5),
"leverage": [{ITEM}] (5),
"closest_cousin": {{"number": <a different male roster number>, "text": "a paragraph comparing them", "tell": "the single tell that separates them"}}}}"""

FIG = f"""{{"identity": "one-line identity, archetype voice",
"matches": [{{"number": <int>, "why": "one line"}}] (closest first, 1-2),
"pattern": ["4 paragraphs on how he deals with people, from documented public behaviour"],
"dating": ["an imagined date with a fictional person who communicates this way", "a concrete moment of attraction or friction; no claims about the public figure's private life"],
"communicates": {{"text": ["2 paragraphs"], "examples": ["4 described or quoted documented public examples"]}},
"spectrum": {{"interested": "paragraph", "invested": "paragraph", "done": "paragraph"}},
"handling": {{"works": [{ITEM}] (5), "never": [{ITEM}] (5)}},
"power_moves": [{{"name": "...", "how_it_works": "2 sentences, documented", "defense": "..."}}] (5),
"leverage": [{ITEM}] (5),
"map": {{"closest": {{"number": <int>, "rationale": "paragraph"}}, "cousin": {{"number": <int>, "tell": "He reads like #X, but here's the tell..."}}, "secondary": [{{"number": <int>, "why": "..."}}] (1-2)}}}}"""

SYS = ("You write for Imladri, a sharp, warm, funny-but-wise dating insight brand for women. In-depth, specific, vivid. "
       "Confident about actions, honest about uncertainty on motives. No diagnoses or clinical labels.")
FIG_SYS = SYS + (" For public figures: ONLY documented public behaviour (interviews, public statements, recorded appearances, reported public actions). "
                 "No private-life speculation, no motive certainty - say 'publicly appears to' when soft. It's about 'someone like him'.")


async def photo_for(c):
    for t in (c.get("actor_wiki_title"), c.get("wiki_title")):
        got = await asyncio.to_thread(wiki_image, t)
        if got:
            return await store(got[0], "Image: Wikipedia", got[1])
    return None


async def regen_arch(a, sem):
    async with sem:
        try:
            s = await ai_json(SYS, f"Roster:\n{roster()}\n\nWrite the full in-depth page for #{a['number']} {a['name']} ('{a['one_liner']}'). Shape:\n{ARCH}")
            for c in s.get("characters", []):
                c["image_url"] = await photo_for(c)
            s["v"] = 2
            await db.archetypes.update_one({"slug": a["slug"]}, {"$set": {"sections": s, "identity": s.get("identity") or a["identity"]}})
            print("ok", a["slug"], flush=True)
        except Exception as e:
            print("fail", a["slug"], e, flush=True)


async def regen_fig(f, sem):
    async with sem:
        try:
            s = await ai_json(FIG_SYS, f"Roster:\n{roster()}\n\nWrite the in-depth figure page for {f['name']}. Shape:\n{FIG}")
            s["v"] = 2
            upd = {"sections": s, "identity": s.get("identity") or f["identity"], "matches": s.get("matches") or f["matches"]}
            if not f.get("photo_url"):
                got = await asyncio.to_thread(wiki_image, f["name"])
                if got:
                    upd.update(photo_url=await store(got[0], "Photo: Wikimedia Commons via Wikipedia", got[1]), photo_credit="Photo: Wikimedia Commons")
            await db.figures.update_one({"slug": f["slug"]}, {"$set": upd})
            print("ok", f["slug"], flush=True)
        except Exception as e:
            print("fail", f["slug"], e, flush=True)


async def main():
    sem = asyncio.Semaphore(8)
    archs = await db.archetypes.find({"sections.v": {"$ne": 2}}, {"_id": 0}).to_list(100)
    figs = await db.figures.find({"sections.v": {"$ne": 2}, "sources": {"$exists": False}, "sections_edited": {"$ne": True}}, {"_id": 0}).to_list(None)
    await asyncio.gather(*[regen_fig(f, sem) for f in figs], *[regen_arch(a, sem) for a in archs])
    used = set()
    async for a in db.archetypes.find({}, {"_id": 0, "sections.characters.image_url": 1}):
        used |= {c.get("image_url") for c in (a.get("sections") or {}).get("characters", [])}
    async for f in db.figures.find({}, {"_id": 0, "photo_url": 1}):
        used.add(f.get("photo_url"))
    ids = [u.rsplit("/", 1)[-1] for u in used if u]
    r = await db.media.delete_many({"id": {"$nin": ids}})
    print("done, removed unused media", r.deleted_count, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
