"""In-app reminders are derived from current grants. No partner text is queued."""
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import HTTPException

try:
    from .couples import DEFAULT_PREFERENCES, active_pair, is_adult
except ImportError:
    from couples import DEFAULT_PREFERENCES, active_pair, is_adult


async def inbox(db, member, current=None):
    config = {**DEFAULT_PREFERENCES, **(member.get("couples_preferences") or {})}
    if not is_adult(member) or not config["reminders"]:
        return []
    moment = (current or datetime.now(timezone.utc)).astimezone(ZoneInfo(config["timezone"]))
    clock = moment.strftime("%H:%M")
    start, end = config["quiet_start"], config["quiet_end"]
    if (start <= clock < end) or (start > end and (clock >= start or clock < end)):
        return []
    try:
        pair = await active_pair(db, member, write=True)
    except HTTPException as exc:
        if exc.status_code in (402, 403, 404):
            return []
        raise
    side = "a" if pair["inviter"] == member["user_id"] else "b"
    read = set(member.get("couples_reminders_seen") or [])
    items = []
    for thread_key, thread in (pair.get("journey") or {}).get("threads", {}).items():
        if not all(thread.get("shares", {}).get(k) for k in ("a", "b")):
            continue
        for agreement in thread.get("agreements", []):
            if agreement.get("status") != "active" or agreement.get("checkins", {}).get(side):
                continue
            due = date.fromisoformat(agreement["review_on"])
            if not due <= moment.date() <= due + timedelta(days=7):
                continue
            identity = f"{pair['_id']}:{agreement['id']}:{agreement['revision']}"
            key = sha256(identity.encode()).hexdigest()
            items.append({"id": key, "title": "A check-in is ready", "body": "Open Imladri when you have a moment.",
                          "link": "/app/couples/agreements?" + urlencode({"item": thread_key, "agreement": agreement["id"], "revision": agreement["revision"]}), "read": key in read,
                          "created_at": datetime.combine(due, time.min, moment.tzinfo).isoformat()})
    return sorted(items, key=lambda item: item["created_at"], reverse=True)
