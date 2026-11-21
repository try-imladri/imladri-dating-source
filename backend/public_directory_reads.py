"""Small, current database reads for anonymous directories (no result cache)."""
import asyncio

from . import figure_evidence


def archetype_directory_projection():
    # Render executes this projection before the gateway sends rows to the
    # Cloudflare API. Article sections and editorial metadata are unnecessary
    # for cards. Preserve Python bool(sections), including legacy empty values;
    # Mongo's direct $toBool would incorrectly treat an empty object as ready.
    return {
        "_id": 0, "number": 1, "slug": 1, "wave": 1, "unlocked": 1,
        "gender": 1, "name": 1, "one_liner": 1, "texting_tags": 1,
        "red_flag_tags": 1,
        "ready": {"$not": [{"$in": [
            {"$ifNull": ["$sections", None]}, [None, False, 0, "", [], {}]
        ]}]},
    }


async def figure_directory_inputs(db):
    # These independent reads previously paid two serial gateway round trips.
    # Keep the original visibility predicates and fresh reads on every call.
    figures, archetypes = await asyncio.gather(
        db.figures.find({"published": True}, figure_evidence.directory_projection()).to_list(None),
        db.archetypes.find({"unlocked": True}, {"_id": 0, "number": 1, "name": 1, "gender": 1}).to_list(None),
    )
    return figures, {row["number"]: row for row in archetypes}
