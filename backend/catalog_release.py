"""Bulk publication and retryable in-app announcements for archetype waves."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, StrictBool
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

try:
    from . import catalog_editorial
except ImportError:
    import catalog_editorial


class WavePublication(BaseModel):
    unlocked: StrictBool
    notify: StrictBool = False


async def publish_wave(db, wave, change, notify_all):
    # One writer owns the pages and the fan-out together. The expiry recovers an abandoned
    # process; the shorter operation deadline cancels a slow live writer before recovery.
    now = datetime.now(timezone.utc)
    owner = uuid4().hex
    releases = db.catalog_wave_releases
    try:
        state = await releases.find_one_and_update(
            {"_id": wave, "$or": [{"owner": None}, {"lease_until": {"$lte": now}}]},
            {"$set": {"owner": owner, "lease_until": now + timedelta(minutes=12)}},
            upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        raise HTTPException(409, "A change to this wave is in progress. Try again after it finishes.") from None
    owned = {"_id": wave, "owner": owner}
    try:
        async with asyncio.timeout(600):
            pending = state.get("pending")
            selector = {"wave": wave, "unlocked": {"$ne": change.unlocked}}
            if not pending or pending["unlocked"] != change.unlocked:
                if not await db.archetypes.find_one(selector, {"_id": 1}):
                    await releases.update_one(owned, {"$unset": {"pending": ""}})
                    return {"updated": 0}
                pending = {"unlocked": change.unlocked, "notify": change.unlocked and change.notify,
                           "key": f"wave:{wave}:{uuid4().hex}"}
                await releases.update_one(owned, {"$set": {"pending": pending}})
            if change.unlocked:
                async for page in db.archetypes.find(selector, {"editorial": 0}):
                    await catalog_editorial.validate(db, "archetypes", catalog_editorial.content("archetypes", page), page.get("number"))
            count = await db.archetypes.update_many(selector, {"$set": {"unlocked": change.unlocked}})
            if pending["notify"]:
                # Retrying after partial delivery reuses the same in-app notification key.
                await notify_all("new_page", f"Archetype wave {wave} is live", "Explore the newly released archetypes.",
                                 "/types", pending["key"])
            await releases.update_one(owned, {"$unset": {"pending": ""}})
            return {"updated": count.modified_count}
    except TimeoutError:
        raise HTTPException(503, "Publication is taking longer than expected. Retry to finish the change.") from None
    finally:
        await releases.update_one(owned, {"$unset": {"owner": "", "lease_until": ""}})
