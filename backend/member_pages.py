"""Stable, owner-scoped list pagination for the member workspace."""
import asyncio
import base64
import binascii
import json

from fastapi import HTTPException


async def page(collection, query, *, size, cursor="", field="created_at", order=-1, projection=None):
    window = dict(query)
    if cursor:
        try:
            payload = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True).decode())
            if not isinstance(payload, list) or len(payload) != 2:
                raise ValueError
            stamp, record_id = payload
            if not isinstance(record_id, str) or not record_id or not (stamp is None or isinstance(stamp, str)):
                raise ValueError
        except (ValueError, TypeError, UnicodeError, binascii.Error):
            raise HTTPException(422, "Invalid page. Reload this list to start again.") from None
        op = "$lt" if order == -1 else "$gt"
        later = [{field: stamp, "id": {op: record_id}}]
        if stamp is not None:
            later.append({field: {op: stamp}})
            if order == -1:
                later.append({field: None})
        elif order == 1:
            later.append({field: {"$ne": None}})
        window = {"$and": [query, {"$or": later}]}
    items, total = await asyncio.gather(
        collection.find(window, projection or {"_id": 0}).sort([(field, order), ("id", order)]).limit(size + 1).to_list(size + 1),
        collection.count_documents(query),
    )
    following = None
    if len(items) > size:
        del items[size:]
        last = items[-1]
        following = base64.urlsafe_b64encode(json.dumps([last.get(field), last["id"]], separators=(",", ":")).encode()).decode()
    return {"items": items, "total": total, "next_cursor": following}


SAVED_FIELDS = {"memories": ["kind", "text"], "exchanges": ["question", "conversation", "choice", "outcome"],
                "preps": ["situation"], "corrections": ["text", "note"], "practice": ["topic", "mode"]}


async def saved_page(db, query, section, size, cursor=""):
    if section not in SAVED_FIELDS:
        raise HTTPException(404, "Saved section unavailable.")
    projection = dict.fromkeys(["id", "created_at", *SAVED_FIELDS[section]], 1)
    projection["_id"] = 0
    result = await page(db[section], query, size=size, cursor=cursor, projection=projection)
    if section == "exchanges":
        for item in result["items"]:
            item.update(question=(item.get("question") or "")[:160], messages=len(item.pop("conversation", None) or []),
                        choice=(item.get("choice") or {}).get("label"), outcome=item.get("outcome"))
    return result
