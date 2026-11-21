"""Tags every archetype with 3-5 red flags from a fixed vocabulary (one AI call). Run: python tags.py"""
import asyncio

from ai import ai_json
from content import ARCHETYPES
from enrich import db

FLAGS = ["love bombing", "breadcrumbing", "ghosting", "gaslighting", "jealousy", "control", "hot & cold", "future faking",
         "secrecy", "ego", "guilt-tripping", "scorekeeping", "no follow-through", "emotionally unavailable",
         "late-night only", "keeps exes close", "won't define it", "boundary pushing", "attention seeking", "keeps you on the bench"]


async def main():
    roster = "\n".join(f"#{n} {name} - {one}" for n, _, name, one, _, _ in ARCHETYPES)
    out = await ai_json("You classify dating archetypes.",
                        f"Vocabulary (use exactly these strings): {FLAGS}\n\nArchetypes:\n{roster}\n\n"
                        'For each archetype pick the 3-5 red flags that most define him (#46 The Grown Man and #40 The Open Book may have 1-2 mild ones). '
                        'Shape: {"tags": {"<number>": ["flag", ...]}}')
    for n, slug, *_ in ARCHETYPES:
        tags = [t for t in out["tags"].get(str(n), []) if t in FLAGS]
        if tags:
            await db.archetypes.update_one({"slug": slug}, {"$set": {"red_flag_tags": tags}})
    print("tagged", len(out["tags"]))


if __name__ == "__main__":
    asyncio.run(main())
