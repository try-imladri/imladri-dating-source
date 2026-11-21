"""Dating domain operations with purpose-separated records and safe public views.

No calls to an LLM, no imports of legacy People/assessment records, and no
automatic enrollment. Mongo-compatible primitives also support the project's
PostgreSQL document adapter. Every read/write is scoped to an environment.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import base64
import json
import uuid

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from pymongo import IndexModel, ReturnDocument
from pymongo.errors import DuplicateKeyError

from . import catalog

MAX_PHOTO_BYTES = 2 * 1024 * 1024
RANKING_VERSION = "dating-stated-reciprocal-v1"
INDEXES = {
    "dating_enrollments": [IndexModel([("env", 1), ("user_id", 1)], unique=True, name="dating_enrollment_owner")],
    "dating_profiles": [IndexModel([("env", 1), ("user_id", 1)], unique=True, name="dating_profile_owner"), IndexModel([("env", 1), ("id", 1)], unique=True, name="dating_profile_id"), IndexModel([("env", 1), ("state", 1), ("city_key", 1)], name="dating_discovery")],
    "dating_preferences": [IndexModel([("env", 1), ("user_id", 1)], unique=True, name="dating_preference_owner")],
    "dating_photos": [IndexModel([("env", 1), ("id", 1)], unique=True, name="dating_photo_id"), IndexModel([("env", 1), ("user_id", 1)], name="dating_photo_owner")],
    "dating_interests": [IndexModel([("env", 1), ("user_id", 1), ("target_user_id", 1)], unique=True, name="dating_interest_pair")],
    "dating_matches": [IndexModel([("env", 1), ("id", 1)], unique=True, name="dating_match_id"), IndexModel([("env", 1), ("members", 1), ("state", 1)], name="dating_member_matches")],
    "dating_messages": [IndexModel([("env", 1), ("match_id", 1), ("id", 1)], unique=True, name="dating_message_id"), IndexModel([("env", 1), ("match_id", 1), ("created_at", 1), ("id", 1)], name="dating_message_page")],
    "dating_blocks": [IndexModel([("env", 1), ("user_id", 1), ("target_user_id", 1)], unique=True, name="dating_block_pair")],
    "dating_shares": [IndexModel([("env", 1), ("match_id", 1), ("user_id", 1)], unique=True, name="dating_share_member")],
    "dating_feedback": [IndexModel([("env", 1), ("match_id", 1), ("user_id", 1)], unique=True, name="dating_feedback_member")],
    "dating_reports": [IndexModel([("env", 1), ("id", 1)], unique=True, name="dating_report_id"), IndexModel([("env", 1), ("status", 1), ("created_at", -1)], name="dating_report_queue"), IndexModel("expires_at", expireAfterSeconds=0, name="dating_incident_expiry")],
    "dating_appeals": [IndexModel([("env", 1), ("user_id", 1), ("created_at", -1)], name="dating_appeal_queue")],
    "dating_events": [IndexModel([("env", 1), ("user_id", 1), ("type", 1), ("created_at", -1)], name="dating_event_owner")],
    "dating_rate": [IndexModel("expires_at", expireAfterSeconds=0, name="dating_rate_expiry")],
    "dating_settings": [],
    "dating_moderation": [IndexModel([("env", 1), ("created_at", -1)], name="dating_moderation_audit"), IndexModel("expires_at", expireAfterSeconds=0, name="dating_audit_expiry")],
}


def now():
    return datetime.now(timezone.utc).isoformat()


def new_id():
    return str(uuid.uuid4())


def env_of(member):
    return member.get("env") or "live"


def key(*parts):
    return sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def age_on(dob, today=None):
    try:
        birth = date.fromisoformat(str(dob))
    except (TypeError, ValueError):
        return None
    today = today or datetime.now(timezone.utc).date()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def require_open(member):
    if member.get("closing"):
        raise HTTPException(409, "Your account is closing. Dating changes are unavailable.")


def defaults():
    return {"age_min": 18, "age_max": 100, "genders": [], "cities": [], "intents": [],
            "relationship_structures": [], "personality": {}, "intimacy": {},
            "personality_matching": False, "intimacy_matching": False, "revision": 0}


def clean_record(doc):
    return {k: deepcopy(v) for k, v in (doc or {}).items() if k not in ("_id", "env", "user_id")}


async def event(db, member, kind, **values):
    # Only operational identifiers/booleans belong here; never questionnaire text.
    await db.dating_events.insert_one({"id": new_id(), "env": env_of(member), "user_id": member["user_id"], "type": kind, "created_at": now(), **values})


async def audit(db, member, **values):
    await db.dating_moderation.insert_one({"id": new_id(), "env": env_of(member), "staff_id": member["user_id"],
        "created_at": now(), "expires_at": datetime.now(timezone.utc) + timedelta(days=180), **values})


async def launch(db, env):
    stored = await db.dating_settings.find_one({"_id": "launch:" + env}) or {}
    return {"city": stored.get("city", "Chicago"), "mode": "founding_beta", "discovery_enabled": stored.get("discovery_enabled", True),
            "accepting_profiles": stored.get("accepting_profiles", True), "review_note": stored.get("review_note", ""),
            "checkout_enabled": False, "notice": "Founding beta. New profiles are reviewed. Identity and age are not established by a profile photo review."}


async def enrollment(db, member, required=True):
    row = await db.dating_enrollments.find_one({"env": env_of(member), "user_id": member["user_id"], "state": "active"})
    if required and not row:
        raise HTTPException(403, "Join Dating explicitly before using this feature.")
    return row


async def prefs(db, uid, env):
    stored = await db.dating_preferences.find_one({"env": env, "user_id": uid}) or {}
    return {**defaults(), **{k: deepcopy(v) for k, v in stored.items() if k in defaults()}}


async def own_profile(db, member):
    return await db.dating_profiles.find_one({"env": env_of(member), "user_id": member["user_id"]})


async def photos_for(db, profile, owner=False):
    if not profile:
        return []
    rows = await db.dating_photos.find({"env": profile["env"], "user_id": profile["user_id"]}, {"data": 0}).to_list(20)
    lookup = {r["id"]: r for r in rows}
    result = []
    for pid in profile.get("photo_ids", []):
        if not owner and pid not in profile.get("published", {}).get("photo_ids", []):
            continue
        row = lookup.get(pid)
        if row and (owner or row["state"] == "approved"):
            photo = {"id": pid, "url": "/api/dating/photos/" + pid + "/content", "state": row["state"]}
            if owner:
                photo["review_note"] = row.get("review_note", "")
            result.append(photo)
    return result


async def present_owner(db, profile):
    if not profile:
        return None
    safe = clean_record(profile)
    safe.pop("published", None)
    safe.pop("city_key", None)
    safe["age"] = age_on(profile.get("date_of_birth"))
    safe["photos"] = await photos_for(db, profile, owner=True)
    safe["cover_photo_id"] = profile.get("photo_ids", [None])[0] if profile.get("photo_ids") else None
    return safe


async def bootstrap(db, member):
    require_open(member)
    env, uid = env_of(member), member["user_id"]
    enrolled = await enrollment(db, member, required=False)
    profile = await own_profile(db, member)
    config = await launch(db, env)
    return {"member": {k: member.get(k, "") for k in ("user_id", "nickname", "role")},
            "enrollment": clean_record(enrolled) if enrolled else None, "profile": await present_owner(db, profile),
            "preferences": await prefs(db, uid, env), "catalog": catalog.present(), "launch": config,
            "membership": {"plan": "founding_beta", "price_cents": 1900, "currency": "USD", "checkout_enabled": False,
                           "message": "Founding beta is free. $19/month is a future pricing test, not an active subscription."},
            "counts": {"likes": len((await incoming_likes(db, member))["items"]) if enrolled else 0,
                       "matches": len((await matches(db, member))["items"]) if enrolled else 0},
            "consent": {"version": catalog.ENROLLMENT_VERSION, "notice": catalog.NOTICE}}


async def enroll(db, member, body):
    require_open(member)
    env, uid = env_of(member), member["user_id"]
    settings = await launch(db, env)
    if not settings["accepting_profiles"]:
        raise HTTPException(409, "Founding enrollment is paused. Existing data controls remain available.")
    scope = {"env": env, "user_id": uid}
    await db.dating_enrollments.update_one(scope, {"$setOnInsert": {**scope, "id": new_id(), "created_at": now()},
        "$set": {"version": body.version, "notice_sha": key(catalog.NOTICE), "adult_confirmed": True, "state": "active", "accepted_at": now()}}, upsert=True)
    return {"enrollment": clean_record(await enrollment(db, member))}


async def save_profile(db, member, body):
    require_open(member)
    await enrollment(db, member)
    age = age_on(body.date_of_birth)
    if age is None or not 18 <= age <= 100:
        raise HTTPException(422, "Dating is for adults aged 18 or older. Choose a valid date of birth.")
    env, uid, ts = env_of(member), member["user_id"], now()
    old = await own_profile(db, member)
    values = body.model_dump(exclude={"expected_revision"})
    values["date_of_birth"] = body.date_of_birth.isoformat()
    values.update({"city_key": body.city.strip().casefold(), "updated_at": ts, "state": "draft", "review_status": "pending", "review_note": ""})
    if old:
        if old.get("state") == "suspended":
            raise HTTPException(403, "This dating profile is suspended. Use an appeal to request review.")
        revision = old.get("revision", 0)
        if body.expected_revision is not None and body.expected_revision != revision:
            raise HTTPException(409, "Your profile changed. Reload before saving.")
        result = await db.dating_profiles.update_one({"_id": old["_id"], "revision": revision}, {"$set": values, "$inc": {"revision": 1}})
        if not result.modified_count:
            raise HTTPException(409, "Your profile changed. Reload before saving.")
    else:
        if body.expected_revision not in (None, 0):
            raise HTTPException(409, "Reload your profile before saving.")
        try:
            await db.dating_profiles.insert_one({"id": new_id(), "env": env, "user_id": uid, "created_at": ts,
                "photo_ids": [], "revision": 1, **values})
        except DuplicateKeyError:
            raise HTTPException(409, "Your profile was created in another tab. Reload it.") from None
    await event(db, member, "profile_saved")
    return {"profile": await present_owner(db, await own_profile(db, member))}


async def save_preferences(db, member, body):
    require_open(member)
    await enrollment(db, member)
    env, uid = env_of(member), member["user_id"]
    old = await prefs(db, uid, env)
    if body.expected_revision is not None and body.expected_revision != old["revision"]:
        raise HTTPException(409, "Your preferences changed. Reload before saving.")
    values = body.model_dump(exclude={"expected_revision"})
    values["revision"] = old["revision"] + 1
    values["updated_at"] = now()
    values["catalog_version"] = catalog.VERSION
    values["matching_permission_at"] = now()
    for scope in ("personality", "intimacy"):
        values[scope] = {qid: {k: v for k, v in row.items() if v is not None} for qid, row in values[scope].items()}
    query = {"env": env, "user_id": uid}
    existing = await db.dating_preferences.find_one(query)
    if existing:
        result = await db.dating_preferences.update_one({**query, "revision": old["revision"]}, {"$set": values})
        if not result.modified_count:
            raise HTTPException(409, "Your preferences changed. Reload before saving.")
    else:
        try:
            await db.dating_preferences.insert_one({**query, **values})
        except DuplicateKeyError:
            raise HTTPException(409, "Your preferences changed. Reload before saving.") from None
    # Published shared snapshots must not survive a changed/revoked answer.
    await db.dating_shares.delete_many({"env": env, "user_id": uid})
    return {"preferences": await prefs(db, uid, env)}


def image_content(data):
    """Decode pixels, orient, then encode a fresh container with no metadata."""
    from io import BytesIO
    from PIL import Image, ImageOps, UnidentifiedImageError
    if not data or len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(422, "Choose a JPEG or PNG photo smaller than 2 MB.")
    try:
        with Image.open(BytesIO(data)) as original:
            if original.format not in ("JPEG", "PNG") or getattr(original, "n_frames", 1) != 1:
                raise HTTPException(422, "Choose a still JPEG or PNG photo.")
            width, height = original.size
            if not 64 <= width <= 8000 or not 64 <= height <= 8000 or width * height > 32_000_000:
                raise HTTPException(422, "Choose a clear photo between 64 and 8000 pixels on each side, up to 32 megapixels.")
            original.load()
            is_png = original.format == "PNG"
            original.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
            ImageOps.exif_transpose(original, in_place=True)
            oriented = original
            mode = "RGBA" if is_png and ("A" in oriented.getbands() or "transparency" in oriented.info) else "RGB"
            pixels = oriented.convert(mode)
            # A new image carries no EXIF, GPS, ICC, text comments, trailing data,
            # or a second JPEG scan container supplied by an uploader.
            fresh = Image.new(mode, pixels.size)
            fresh.paste(pixels)
            output = BytesIO()
            fresh.save(output, format="PNG" if is_png else "JPEG", **({"compress_level": 6} if is_png else {"quality": 88}))
            clean = output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise HTTPException(422, "This photo could not be decoded. Choose a clear JPEG or PNG image.") from None
    if len(clean) > MAX_PHOTO_BYTES:
        raise HTTPException(422, "This photo is too large after processing. Resize it and upload again.")
    return ("image/png" if is_png else "image/jpeg"), clean


async def add_photo(db, member, data):
    require_open(member)
    await enrollment(db, member)
    profile = await own_profile(db, member)
    if not profile:
        raise HTTPException(409, "Save your dating profile before adding photos.")
    if profile["state"] == "suspended":
        raise HTTPException(403, "This profile is suspended.")
    if len(profile.get("photo_ids", [])) >= 6:
        raise HTTPException(409, "A profile can have up to six photos.")
    mime, clean = await run_in_threadpool(image_content, data)
    pid, ts = new_id(), now()
    row = {"id": pid, "env": env_of(member), "user_id": member["user_id"], "profile_id": profile["id"],
           "mime": mime, "data": base64.b64encode(clean).decode(), "state": "pending", "review_note": "", "created_at": ts}
    await db.dating_photos.insert_one(row)
    result = await db.dating_profiles.update_one({"_id": profile["_id"], "revision": profile["revision"]},
        {"$addToSet": {"photo_ids": pid}, "$set": {"state": "draft", "review_status": "pending", "updated_at": ts}, "$inc": {"revision": 1}})
    if not result.modified_count:
        await db.dating_photos.delete_one({"env": row["env"], "id": pid})
        raise HTTPException(409, "Your profile changed. Reload before uploading again.")
    return {"photo": {"id": pid, "url": "/api/dating/photos/" + pid + "/content", "state": "pending"}}


async def delete_photo(db, member, pid):
    # Account data controls remain available even after enrollment withdrawal.
    profile = await own_profile(db, member)
    if not profile or pid not in profile.get("photo_ids", []):
        raise HTTPException(404, "Photo unavailable.")
    values = {"updated_at": now()}
    if profile["state"] not in ("suspended", "rejected", "closed"):
        values.update({"state": "draft", "review_status": "pending"})
    result = await db.dating_profiles.update_one({"_id": profile["_id"], "revision": profile["revision"]},
        {"$pull": {"photo_ids": pid}, "$set": values, "$inc": {"revision": 1}})
    if not result.modified_count:
        raise HTTPException(409, "Your profile changed. Reload before removing the photo.")
    await db.dating_photos.delete_one({"env": env_of(member), "user_id": member["user_id"], "id": pid})
    return {"ok": True}


async def submit_profile(db, member):
    require_open(member)
    await enrollment(db, member)
    profile = await own_profile(db, member)
    if not profile or profile["state"] == "suspended":
        raise HTTPException(409, "Save an eligible profile before submitting.")
    preferences = await prefs(db, member["user_id"], env_of(member))
    if not preferences["genders"]:
        raise HTTPException(422, "Choose who you want to meet in your preferences.")
    if not profile.get("photo_ids") or len(profile.get("bio", "")) < 20:
        raise HTTPException(422, "Add at least one photo and a bio of at least 20 characters.")
    if not 18 <= (age_on(profile.get("date_of_birth")) or 0) <= 100:
        raise HTTPException(422, "An adult date of birth is required.")
    result = await db.dating_profiles.update_one({"_id": profile["_id"], "revision": profile["revision"]},
        {"$set": {"state": "pending", "review_status": "pending", "submitted_at": now(), "updated_at": now()}, "$inc": {"revision": 1}})
    if not result.modified_count:
        raise HTTPException(409, "Your profile changed. Reload before submitting.")
    await event(db, member, "profile_submitted")
    return {"profile": await present_owner(db, await own_profile(db, member))}


async def profile_state(db, member, state):
    require_open(member)
    await enrollment(db, member)
    profile = await own_profile(db, member)
    if not profile:
        raise HTTPException(404, "Dating profile unavailable.")
    if profile["state"] == "suspended":
        raise HTTPException(403, "This profile is suspended. Use an appeal to request review.")
    if state == "active" and profile.get("review_status") != "approved":
        raise HTTPException(409, "Your profile needs review before it can appear in discovery.")
    result = await db.dating_profiles.update_one({"_id": profile["_id"], "revision": profile["revision"]},
        {"$set": {"state": state, "updated_at": now()}, "$inc": {"revision": 1}})
    if not result.modified_count:
        raise HTTPException(409, "Your profile changed. Reload before changing visibility.")
    return {"profile": await present_owner(db, await own_profile(db, member))}


async def blocked(db, env, uid, other):
    return bool(await db.dating_blocks.find_one({"env": env, "$or": [{"user_id": uid, "target_user_id": other}, {"user_id": other, "target_user_id": uid}]}, {"_id": 1}))


def direction_allows(profile, preferences, other, other_preferences):
    age = age_on(other.get("date_of_birth"))
    if age is None or not preferences["age_min"] <= age <= preferences["age_max"] or age < 18:
        return False
    if not preferences["genders"] or other.get("gender") not in preferences["genders"]:
        return False
    cities = [c.casefold() for c in preferences["cities"]] or [profile.get("city_key", "")]
    if other.get("city_key") not in cities:
        return False
    if preferences["intents"] and other.get("intent") not in preferences["intents"]:
        return False
    if preferences["relationship_structures"] and other.get("relationship_structure") not in preferences["relationship_structures"]:
        return False
    for scope in ("personality", "intimacy"):
        if not preferences.get(scope + "_matching"):
            continue
        for qid, value in preferences[scope].items():
            if value.get("importance") != "essential" or not value.get("acceptable"):
                continue
            offered = other_preferences[scope].get(qid, {}) if other_preferences.get(scope + "_matching") else {}
            answer = offered.get("self" if scope == "personality" else "answer")
            if answer not in value["acceptable"]:
                return False
    return True


def eligible(first, fp, second, sp):
    return direction_allows(first, fp, second, sp) and direction_allows(second, sp, first, fp)


def preference_score(first, second):
    score = 0
    for scope in ("personality", "intimacy"):
        if not first.get(scope + "_matching") or not second.get(scope + "_matching"):
            continue
        field = "self" if scope == "personality" else "answer"
        for own, other in ((first, second), (second, first)):
            for qid, row in own[scope].items():
                if row.get("importance") == "open":
                    continue
                answer = other[scope].get(qid, {}).get(field)
                if answer is not None and answer in row.get("acceptable", []):
                    score += 1
    return score


async def enrolled_profile(db, env, uid, active=True):
    query = {"env": env, "user_id": uid}
    if active:
        query.update({"state": "active", "review_status": "approved"})
    row = await db.dating_profiles.find_one(query)
    if not row:
        return None
    if not await db.dating_enrollments.find_one({"env": env, "user_id": uid, "state": "active"}, {"_id": 1}):
        return None
    account = await db.users.find_one({"user_id": uid}, {"closing": 1})
    if not account or account.get("closing"):
        return None
    return row


async def safe_profile(db, profile, viewer=None):
    # Only reviewed snapshots may be shown to another member. No raw preferences.
    public = profile.get("published") or {}
    value = {k: public.get(k, "") for k in ("display_name", "gender", "city", "bio", "intent", "relationship_structure")}
    value.update({"id": profile["id"], "age": age_on(public.get("date_of_birth")), "photos": await photos_for(db, profile),
                  "personality_shared": {}, "verification": {"photo_reviewed": profile.get("review_status") == "approved", "identity_verified": False},
                  "reasons": [], "conversation_starter": "What does a good weekend together look like?"})
    value["cover_photo_id"] = value["photos"][0]["id"] if value["photos"] else None
    if viewer:
        if viewer.get("city_key") == profile.get("city_key"):
            value["reasons"].append("You are looking to meet in the same city.")
        if viewer.get("intent") == public.get("intent"):
            value["reasons"].append("Your public relationship intentions align.")
        if viewer.get("relationship_structure") == public.get("relationship_structure"):
            value["reasons"].append("Your stated relationship structure aligns.")
    return value


async def discovery(db, member, limit=24):
    require_open(member)
    await enrollment(db, member)
    env, uid = env_of(member), member["user_id"]
    mine = await enrolled_profile(db, env, uid)
    settings = await launch(db, env)
    status = "profile_required" if not mine else "waiting"
    empty = {"items": [], "availability": {"eligible_count": 0, "status": status, "message": "Complete your profile and review before discovery opens." if not mine else "We are building your local founding community. No relevant introductions are available yet."}, "ranking_version": RANKING_VERSION}
    if not mine or not settings["discovery_enabled"] or mine["city_key"] != settings["city"].casefold():
        return empty
    own_preferences = await prefs(db, uid, env)
    candidates = await db.dating_profiles.find({"env": env, "state": "active", "review_status": "approved", "user_id": {"$ne": uid}}).limit(500).to_list(500)
    reactions = await db.dating_interests.find({"env": env, "user_id": uid}, {"target_user_id": 1}).to_list(5000)
    dismissed = {r["target_user_id"] for r in reactions}
    matches = await db.dating_matches.find({"env": env, "members": uid}, {"members": 1}).to_list(5000)
    dismissed.update(other for match in matches for other in match["members"] if other != uid)
    ranked = []
    for candidate in candidates:
        other = candidate["user_id"]
        if other in dismissed or await blocked(db, env, uid, other):
            continue
        if not await enrolled_profile(db, env, other):
            continue
        other_preferences = await prefs(db, other, env)
        if eligible(mine, own_preferences, candidate, other_preferences):
            ranked.append((preference_score(own_preferences, other_preferences), candidate))
    ranked.sort(key=lambda item: (-item[0], item[1]["id"]))
    count = len(ranked)
    status = "ready" if count >= 5 else "limited" if count else "waiting"
    items = [await safe_profile(db, row, mine) for _, row in ranked[:limit]]
    return {"items": items, "availability": {"eligible_count": count, "status": status,
             "message": "A small set of relevant founding members." if count else empty["availability"]["message"], "scan_limit": 500}, "ranking_version": RANKING_VERSION}


async def accessible_profile(db, member, pid, allow_matched=True):
    env, uid = env_of(member), member["user_id"]
    candidate = await db.dating_profiles.find_one({"env": env, "id": pid})
    if not candidate or candidate["user_id"] == uid:
        raise HTTPException(404, "Profile unavailable.")
    other = candidate["user_id"]
    if await blocked(db, env, uid, other):
        raise HTTPException(404, "Profile unavailable.")
    viewer = await enrolled_profile(db, env, uid, active=False)
    if not viewer or viewer.get("state") in ("closed", "suspended", "rejected"):
        raise HTTPException(404, "Profile unavailable.")
    if allow_matched:
        match = await db.dating_matches.find_one({"env": env, "members": uid, "state": "active", "profile_ids": pid})
        if match and other in match["members"] and await enrolled_profile(db, env, other, active=False) and candidate["state"] not in ("closed", "suspended", "rejected"):
            return candidate
    mine = await enrolled_profile(db, env, uid)
    config = await launch(db, env)
    if not mine or not config["discovery_enabled"] or mine["city_key"] != config["city"].casefold() or not await enrolled_profile(db, env, other):
        raise HTTPException(404, "Profile unavailable.")
    if not eligible(mine, await prefs(db, uid, env), candidate, await prefs(db, other, env)):
        raise HTTPException(404, "Profile unavailable.")
    return candidate


async def interest(db, member, body):
    require_open(member)
    await enrollment(db, member)
    env, uid = env_of(member), member["user_id"]
    other = await accessible_profile(db, member, body.profile_id, allow_matched=False)
    mine = await own_profile(db, member)
    query = {"env": env, "user_id": uid, "target_user_id": other["user_id"]}
    await db.dating_interests.update_one(query, {"$set": {"profile_id": other["id"], "choice": body.choice, "updated_at": now()}, "$setOnInsert": {"created_at": now()}}, upsert=True)
    reverse = await db.dating_interests.find_one({"env": env, "user_id": other["user_id"], "target_user_id": uid, "profile_id": mine["id"], "choice": "like"})
    if body.choice != "like" or not reverse:
        return {"matched": False, "match_id": None}
    # Deterministic pair id + unique index makes concurrent reciprocal likes safe.
    mid = key("dating-match-v1", env, *sorted((mine["id"], other["id"])))
    row = {"id": mid, "env": env, "members": sorted((uid, other["user_id"])), "profile_ids": sorted((mine["id"], other["id"])), "state": "active", "created_at": now(), "updated_at": now(), "read_at": {}}
    try:
        await db.dating_matches.update_one({"env": env, "id": mid}, {"$setOnInsert": row}, upsert=True)
    except DuplicateKeyError:
        pass
    matched = await db.dating_matches.find_one({"env": env, "id": mid})
    # A block racing with creation always wins on subsequent authorization.
    if matched["state"] != "active" or await blocked(db, env, uid, other["user_id"]):
        return {"matched": False, "match_id": None}
    return {"matched": True, "match_id": mid}


async def active_match(db, member, mid):
    require_open(member)
    await enrollment(db, member)
    env, uid = env_of(member), member["user_id"]
    mine = await enrolled_profile(db, env, uid, active=False)
    if not mine or mine.get("state") in ("closed", "suspended", "rejected"):
        raise HTTPException(404, "Conversation unavailable.")
    match = await db.dating_matches.find_one({"env": env, "id": mid, "members": uid, "state": "active"})
    if not match:
        raise HTTPException(404, "Conversation unavailable.")
    other = next((u for u in match["members"] if u != uid), None)
    profile = await enrolled_profile(db, env, other, active=False) if other else None
    if not profile or profile["state"] in ("closed", "suspended", "rejected") or await blocked(db, env, uid, other):
        raise HTTPException(404, "Conversation unavailable.")
    return match, profile


async def matches(db, member):
    await enrollment(db, member)
    env, uid = env_of(member), member["user_id"]
    rows = await db.dating_matches.find({"env": env, "members": uid, "state": "active"}).sort("updated_at", -1).limit(100).to_list(100)
    mine, result = await own_profile(db, member), []
    for row in rows:
        try:
            _, other = await active_match(db, member, row["id"])
        except HTTPException:
            continue
        last = await db.dating_messages.find_one({"env": env, "match_id": row["id"], "deleted": {"$ne": True}}, sort=[("created_at", -1), ("id", -1)], projection={"_id": 0, "env": 0, "request_id": 0})
        read = row.get("read_at", {}).get(key(uid), "")
        unread = await db.dating_messages.count_documents({"env": env, "match_id": row["id"], "sender_id": {"$ne": uid}, "created_at": {"$gt": read}, "deleted": {"$ne": True}})
        result.append({"id": row["id"], "state": row["state"], "profile": await safe_profile(db, other, mine), "last_message": last, "unread_count": unread, "updated_at": row["updated_at"]})
    return {"items": result}


async def incoming_likes(db, member):
    await enrollment(db, member)
    env, uid, output = env_of(member), member["user_id"], []
    rows = await db.dating_interests.find({"env": env, "target_user_id": uid, "choice": "like"}).sort("updated_at", -1).limit(150).to_list(150)
    mine = await own_profile(db, member)
    for row in rows:
        own = await db.dating_interests.find_one({"env": env, "user_id": uid, "target_user_id": row["user_id"]})
        if own:
            continue
        other = await enrolled_profile(db, env, row["user_id"])
        if not other:
            continue
        try:
            other = await accessible_profile(db, member, other["id"], allow_matched=False)
        except HTTPException:
            continue
        output.append(await safe_profile(db, other, mine))
    return {"items": output}


async def message_page(db, member, mid, cursor=None, limit=40):
    await active_match(db, member, mid)
    query = {"env": env_of(member), "match_id": mid, "deleted": {"$ne": True}}
    if cursor:
        boundary = await db.dating_messages.find_one({**query, "id": cursor})
        if not boundary:
            raise HTTPException(422, "This conversation cursor is unavailable.")
        query["$or"] = [{"created_at": {"$lt": boundary["created_at"]}}, {"created_at": boundary["created_at"], "id": {"$lt": boundary["id"]}}]
    rows = await db.dating_messages.find(query, {"_id": 0, "env": 0, "request_id": 0}).sort([("created_at", -1), ("id", -1)]).limit(limit+1).to_list(limit+1)
    more = len(rows) > limit
    rows = rows[:limit]
    return {"items": list(reversed(rows)), "next_cursor": rows[-1]["id"] if rows and more else None}


async def send_message(db, member, mid, body):
    require_open(member)
    await active_match(db, member, mid)
    env, uid = env_of(member), member["user_id"]
    message_id = key("message", env, mid, uid, body.request_id)
    existing = await db.dating_messages.find_one({"env": env, "match_id": mid, "id": message_id})
    if existing:
        if existing.get("text") != body.text:
            raise HTTPException(409, "This request already sent a different message.")
        return {"message": {k: existing[k] for k in ("id", "sender_id", "text", "created_at")}}
    row = {"id": message_id, "env": env, "match_id": mid, "sender_id": uid, "text": body.text, "created_at": now(), "request_id": body.request_id}
    try:
        await db.dating_messages.insert_one(row)
    except DuplicateKeyError:
        existing = await db.dating_messages.find_one({"env": env, "match_id": mid, "id": message_id})
        if existing.get("text") != body.text:
            raise HTTPException(409, "This request already sent a different message.") from None
        row = existing
    # Recheck after insert: a simultaneous unmatch/block must not deliver a message.
    try:
        await active_match(db, member, mid)
    except HTTPException:
        await db.dating_messages.delete_one({"env": env, "match_id": mid, "id": message_id})
        raise
    await db.dating_matches.update_one({"env": env, "id": mid, "state": "active"}, {"$set": {"updated_at": row["created_at"]}})
    return {"message": {k: row[k] for k in ("id", "sender_id", "text", "created_at")}}


async def mark_read(db, member, mid):
    await active_match(db, member, mid)
    await db.dating_matches.update_one({"env": env_of(member), "id": mid, "members": member["user_id"], "state": "active"}, {"$set": {"read_at." + key(member["user_id"]): now()}})
    return {"ok": True}


async def unmatch(db, member, mid):
    # Idempotent account control; no paid or active-profile admission.
    scope = {"env": env_of(member), "id": mid, "members": member["user_id"]}
    if not await db.dating_matches.find_one(scope):
        raise HTTPException(404, "Conversation unavailable.")
    await db.dating_matches.update_one(scope, {"$set": {"state": "unmatched", "updated_at": now()}})
    await db.dating_shares.delete_many({"env": env_of(member), "match_id": mid})
    return {"ok": True}


async def target_for_control(db, member, pid):
    row = await db.dating_profiles.find_one({"env": env_of(member), "id": pid})
    if not row or row["user_id"] == member["user_id"]:
        raise HTTPException(404, "Member unavailable.")
    # Controls remain possible after block/unmatch if any authorized exposure existed.
    env, uid, other = env_of(member), member["user_id"], row["user_id"]
    seen = await db.dating_interests.find_one({"env": env, "$or": [{"user_id": uid, "target_user_id": other}, {"user_id": other, "target_user_id": uid}]})
    match = await db.dating_matches.find_one({"env": env, "members": uid, "profile_ids": pid})
    if seen or match:
        return row
    return await accessible_profile(db, member, pid)


async def block_member(db, member, pid):
    target = await target_for_control(db, member, pid)
    env, uid, other = env_of(member), member["user_id"], target["user_id"]
    await db.dating_blocks.update_one({"env": env, "user_id": uid, "target_user_id": other},
        {"$setOnInsert": {"profile_id": pid, "created_at": now()}}, upsert=True)
    rows = await db.dating_matches.find({"env": env, "members": uid, "profile_ids": pid}, {"id": 1}).to_list(100)
    for row in rows:
        await db.dating_matches.update_one({"env": env, "id": row["id"]}, {"$set": {"state": "blocked", "updated_at": now()}})
        await db.dating_shares.delete_many({"env": env, "match_id": row["id"]})
    return {"ok": True}


async def block_list(db, member):
    rows = await db.dating_blocks.find({"env": env_of(member), "user_id": member["user_id"]}).limit(500).to_list(500)
    output = []
    for row in rows:
        target = await db.dating_profiles.find_one({"env": env_of(member), "id": row.get("profile_id")}, {"published.display_name": 1})
        output.append({"profile_id": row.get("profile_id"), "display_name": (target or {}).get("published", {}).get("display_name", "Unavailable member"), "created_at": row["created_at"]})
    return {"items": output}


async def unblock(db, member, pid):
    await db.dating_blocks.delete_one({"env": env_of(member), "user_id": member["user_id"], "profile_id": pid})
    # Matches closed by a block are never reopened automatically.
    return {"ok": True}


async def report_member(db, member, body):
    target = await target_for_control(db, member, body.profile_id)
    env, uid = env_of(member), member["user_id"]
    evidence = []
    if body.match_id:
        match = await db.dating_matches.find_one({"env": env, "id": body.match_id, "members": uid, "profile_ids": body.profile_id})
        if not match:
            raise HTTPException(404, "Conversation unavailable.")
        for message_id in list(dict.fromkeys(body.message_ids)):
            item = await db.dating_messages.find_one({"env": env, "match_id": body.match_id, "id": message_id}, {"_id": 0, "text": 1, "sender_id": 1, "created_at": 1})
            if not item:
                raise HTTPException(422, "One selected message is unavailable.")
            evidence.append(item)
    elif body.message_ids:
        raise HTTPException(422, "Choose a conversation for message evidence.")
    row = {"id": new_id(), "env": env, "user_id": uid, "target_user_id": target["user_id"], "profile_id": target["id"],
           "category": body.category, "text": body.text, "match_id": body.match_id, "evidence": evidence, "status": "open", "created_at": now(),
           "expires_at": datetime.now(timezone.utc) + timedelta(days=90)}
    await db.dating_reports.insert_one(row)
    return {"id": row["id"], "status": row["status"]}


async def share_answers(db, member, mid, body):
    require_open(member)
    await active_match(db, member, mid)
    env, uid = env_of(member), member["user_id"]
    current_preferences = await prefs(db, uid, env)
    answers, seen = [], set()
    for answer in body.answers:
        identity = (answer.scope, answer.question_id)
        q = catalog.QUESTION_MAP[answer.scope].get(answer.question_id)
        actual = current_preferences[answer.scope].get(answer.question_id, {}).get("self" if answer.scope == "personality" else "answer")
        if not q or actual is None or answer.answer != actual or identity in seen:
            raise HTTPException(422, "Select your current saved answers once each before sharing.")
        seen.add(identity)
        answers.append({**answer.model_dump(), "label": q["label"]})
    query = {"env": env, "match_id": mid, "user_id": uid}
    old = await db.dating_shares.find_one(query) or {}
    revision = old.get("revision", 0)
    if body.expected_revision is not None and body.expected_revision != revision:
        raise HTTPException(409, "Your shared selection changed. Reload before sharing.")
    value = {"answers": answers, "revision": revision+1, "preferences_revision": current_preferences["revision"], "updated_at": now()}
    if old:
        changed = await db.dating_shares.update_one({**query, "revision": revision}, {"$set": value})
        if not changed.modified_count:
            raise HTTPException(409, "Your shared selection changed. Reload before sharing.")
    else:
        try:
            await db.dating_shares.insert_one({**query, **value})
        except DuplicateKeyError:
            raise HTTPException(409, "Your shared selection changed. Reload before sharing.") from None
    # A concurrent preference change/revocation cannot resurrect a prior selection.
    if (await prefs(db, uid, env))["revision"] != current_preferences["revision"]:
        await db.dating_shares.delete_many(query)
        raise HTTPException(409, "Your answers changed. Review the selection again.")
    try:
        await active_match(db, member, mid)
    except HTTPException:
        await db.dating_shares.delete_many(query)
        raise
    return await shared_answers(db, member, mid)


async def shared_answers(db, member, mid):
    match, _ = await active_match(db, member, mid)
    env, uid = env_of(member), member["user_id"]
    rows = await db.dating_shares.find({"env": env, "match_id": mid}).limit(2).to_list(2)
    valid = {}
    for row in rows:
        if row["user_id"] in match["members"] and row.get("preferences_revision") == (await prefs(db, row["user_id"], env))["revision"]:
            valid[row["user_id"]] = row
    mine = valid.get(uid, {})
    other = next((valid[u] for u in match["members"] if u != uid and u in valid), {})
    mutual = bool(mine.get("answers") and other.get("answers"))
    return {"sharing": {"revision": mine.get("revision", 0), "mine": mine.get("answers", []), "partner": other.get("answers", []) if mutual else [], "partner_shared": bool(other.get("answers"))}}


async def revoke_share(db, member, mid):
    await active_match(db, member, mid)
    # A durable revision fence makes revocation win over any in-flight share
    # that read the previous revision, including a not-yet-created first share.
    query = {"env": env_of(member), "match_id": mid, "user_id": member["user_id"]}
    values = {"answers": [], "preferences_revision": (await prefs(db, member["user_id"], env_of(member)))["revision"], "updated_at": now()}
    try:
        await db.dating_shares.update_one(query, {"$set": values, "$inc": {"revision": 1}}, upsert=True)
    except DuplicateKeyError:
        await db.dating_shares.update_one(query, {"$set": values, "$inc": {"revision": 1}})
    return await shared_answers(db, member, mid)


async def feedback(db, member, mid, body):
    require_open(member)
    await active_match(db, member, mid)
    if body.date_happened and not body.conversation_happened:
        raise HTTPException(422, "Confirm a conversation happened before recording a date.")
    query = {"env": env_of(member), "match_id": mid, "user_id": member["user_id"]}
    values = {**body.model_dump(), "updated_at": now()}
    await db.dating_feedback.update_one(query, {"$set": values, "$setOnInsert": {"created_at": now()}}, upsert=True)
    return {"feedback": values}


async def own_export(db, uid, env=None):
    scope = {"user_id": uid, **({"env": env} if env else {})}
    result = {"notice": "Your own dating data. Other members' private answers, notes and feedback are excluded."}
    for name in ("dating_enrollments", "dating_profiles", "dating_preferences", "dating_photos", "dating_interests", "dating_blocks", "dating_shares", "dating_feedback", "dating_appeals", "dating_events"):
        rows = await db[name].find(scope).to_list(10000)
        result[name] = [{k: deepcopy(v) for k, v in row.items() if k not in ("_id", "published", "notice_sha")} for row in rows]
    matches_query = {"members": uid, **({"env": env} if env else {})}
    matches_rows = await db.dating_matches.find(matches_query).to_list(10000)
    result["dating_matches"] = [{k: v for k, v in r.items() if k not in ("_id", "read_at")} for r in matches_rows]
    match_ids = [r["id"] for r in matches_rows]
    messages_scope = {"match_id": {"$in": match_ids}, **({"env": env} if env else {})}
    result["dating_messages"] = [{k: v for k, v in r.items() if k not in ("_id", "request_id")} for r in await db.dating_messages.find(messages_scope).to_list(100000)]
    result["dating_reports"] = [{k: r.get(k) for k in ("id", "category", "text", "created_at", "status")} for r in await db.dating_reports.find(scope).to_list(1000)]
    return result


async def clear_member(db, uid, env=None):
    """Close discovery first, then erase own data; partner safety blocks remain."""
    scope = {"user_id": uid, **({"env": env} if env else {})}
    pair_scope = {"members": uid, **({"env": env} if env else {})}
    await db.dating_enrollments.update_many(scope, {"$set": {"state": "closed", "closed_at": now()}})
    await db.dating_profiles.update_many(scope, {"$set": {"state": "closed", "published": {}}})
    await db.dating_matches.update_many(pair_scope, {"$set": {"state": "closed", "updated_at": now()}})
    closed_matches = await db.dating_matches.find(pair_scope, {"id": 1, "env": 1}).to_list(10000)
    for match in closed_matches:
        await db.dating_shares.delete_many({"env": match["env"], "match_id": match["id"]})
    await db.dating_messages.update_many({"sender_id": uid, **({"env": env} if env else {})}, {"$set": {"text": "", "deleted": True}})
    for name in ("dating_enrollments", "dating_profiles", "dating_preferences", "dating_photos", "dating_interests", "dating_blocks", "dating_shares", "dating_feedback", "dating_events", "dating_appeals"):
        await db[name].delete_many(scope)
    # Reports retain only the member-submitted incident evidence for restricted
    # safety review. Remove free-form report text written by a closing reporter.
    await db.dating_reports.update_many(scope, {"$set": {"text": "", "reporter_closed": True}})


async def rate(db, member, action, maximum, seconds=3600):
    env, uid = env_of(member), member["user_id"]
    ts = datetime.now(timezone.utc)
    bucket = int(ts.timestamp()) // seconds
    rid = key("dating-rate", env, uid, action, bucket)
    try:
        result = await db.dating_rate.update_one({"_id": rid, "count": {"$lt": maximum}}, {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": ts + timedelta(seconds=seconds*2)}}, upsert=True)
    except DuplicateKeyError:
        result = await db.dating_rate.update_one({"_id": rid, "count": {"$lt": maximum}}, {"$inc": {"count": 1}})
        if not result.modified_count:
            raise HTTPException(429, "Please wait before trying this dating action again.") from None


async def admin_queue(db, member):
    env = env_of(member)
    await purge_expired(db, env)
    profiles = await db.dating_profiles.find({"env": env, "state": {"$in": ["pending", "suspended", "rejected"]}}).sort("updated_at", -1).limit(100).to_list(100)
    reports = await db.dating_reports.find({"env": env, "status": {"$in": ["open", "reviewing"]}}, {"_id": 0}).sort("created_at", -1).limit(100).to_list(100)
    appeals = await db.dating_appeals.find({"env": env, "status": "open"}, {"_id": 0}).sort("created_at", -1).limit(100).to_list(100)
    for row in reports:
        row["reporter_id"] = row.get("user_id")
    settings = await launch(db, env)
    return {"profiles": [await present_owner(db, p) for p in profiles], "reports": reports, "appeals": appeals, "settings": settings, "launch": settings}


async def review_profile(db, member, pid, body):
    env = env_of(member)
    profile = await db.dating_profiles.find_one({"env": env, "id": pid})
    if not profile:
        raise HTTPException(404, "Dating profile unavailable.")
    revision = profile["revision"]
    if body.expected_revision is not None and body.expected_revision != revision:
        raise HTTPException(409, "This profile changed. Reload before reviewing.")
    values = {"review_note": body.note, "reviewed_at": now(), "updated_at": now()}
    if body.decision in ("approve", "restore"):
        if body.decision == "approve" and profile["state"] != "pending":
            raise HTTPException(409, "A member must submit this profile before staff can publish it.")
        if body.decision == "restore" and profile["state"] != "suspended":
            raise HTTPException(409, "Restore is available for suspended profiles.")
        enrolled = await db.dating_enrollments.find_one({"env": env, "user_id": profile["user_id"], "state": "active"})
        photos = await photos_for(db, profile, owner=True)
        if not enrolled or not 18 <= (age_on(profile.get("date_of_birth")) or 0) <= 100 or not photos or any(p["state"] != "approved" for p in photos):
            raise HTTPException(409, "An enrolled adult profile and reviewed approved photos are required.")
        settings = await launch(db, env)
        if profile["city_key"] != settings["city"].casefold():
            raise HTTPException(409, "This city is on the waiting list. Update launch availability before approval.")
        if not (await prefs(db, profile["user_id"], env))["genders"]:
            raise HTTPException(409, "Reciprocal partner preferences must be completed first.")
        restored = profile.get("state_before_suspension", "paused")
        visible_state = "active" if body.decision == "approve" or restored == "active" else "paused"
        values.update({"state": visible_state, "review_status": "approved", "published": {k: profile[k] for k in ("display_name", "date_of_birth", "gender", "city", "bio", "intent", "relationship_structure", "photo_ids")}})
    else:
        values.update({"state": "suspended" if body.decision == "suspend" else "rejected", "review_status": "rejected"})
        if body.decision == "suspend" and profile["state"] != "suspended":
            values["state_before_suspension"] = profile["state"]
    changed = await db.dating_profiles.update_one({"_id": profile["_id"], "revision": revision}, {"$set": values, "$inc": {"revision": 1}})
    if not changed.modified_count:
        raise HTTPException(409, "This profile changed. Reload before reviewing.")
    await audit(db, member, profile_id=pid, decision=body.decision, note=body.note)
    return {"profile": await present_owner(db, await db.dating_profiles.find_one({"env": env, "id": pid}))}


async def review_photo(db, member, pid, body):
    env = env_of(member)
    photo = await db.dating_photos.find_one({"env": env, "id": pid}, {"data": 0})
    if not photo:
        raise HTTPException(404, "Photo unavailable.")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(422, "Choose approve or reject for a photo.")
    await db.dating_photos.update_one({"env": env, "id": pid}, {"$set": {"state": "approved" if body.decision == "approve" else "rejected", "review_note": body.note, "reviewed_at": now()}})
    if body.decision == "reject":
        await db.dating_profiles.update_one({"env": env, "user_id": photo["user_id"], "state": {"$nin": ["suspended", "rejected", "closed"]}}, {"$set": {"state": "pending", "review_status": "pending"}, "$inc": {"revision": 1}})
    await audit(db, member, photo_id=pid, decision=body.decision, note=body.note)
    return {"ok": True}


async def metrics(db, member):
    scope = {"env": env_of(member)}
    return {"enrolled": await db.dating_enrollments.count_documents({**scope, "state": "active"}),
            "active_profiles": await db.dating_profiles.count_documents({**scope, "state": "active", "review_status": "approved"}),
            "pending_profiles": await db.dating_profiles.count_documents({**scope, "state": "pending"}),
            "active_matches": await db.dating_matches.count_documents({**scope, "state": "active"}),
            "messages": await db.dating_messages.count_documents({**scope, "deleted": {"$ne": True}}),
            "open_reports": await db.dating_reports.count_documents({**scope, "status": {"$in": ["open", "reviewing"]}}),
            "notice": "Operational counts, not claims of dates, relationship success or paid demand. Private feedback is not included."}


async def appeal(db, member, body):
    require_open(member)
    await enrollment(db, member)
    profile = await own_profile(db, member)
    if not profile or profile.get("state") not in ("suspended", "rejected"):
        raise HTTPException(409, "Appeals are available for rejected or suspended profiles.")
    await rate(db, member, "appeal", 3, 86400)
    row = {"id": new_id(), "env": env_of(member), "user_id": member["user_id"], "profile_id": profile["id"],
           "text": body.text, "status": "open", "created_at": now()}
    await db.dating_appeals.insert_one(row)
    return {"id": row["id"], "status": "open"}


async def save_settings(db, member, body):
    env = env_of(member)
    await db.dating_settings.update_one({"_id": "launch:" + env}, {"$set": {**body.model_dump(), "env": env, "updated_at": now()}}, upsert=True)
    await audit(db, member, decision="launch_settings", settings=body.model_dump())
    return {"settings": await launch(db, env)}


async def review_case(db, member, collection, case_id, body):
    if collection not in ("dating_reports", "dating_appeals"):
        raise ValueError("Unsupported safety case collection")
    env = env_of(member)
    row = await db[collection].find_one({"env": env, "id": case_id})
    if not row:
        raise HTTPException(404, "Safety case unavailable.")
    await db[collection].update_one({"env": env, "id": case_id}, {"$set": {"status": body.status, "review_note": body.note, "reviewed_at": now(), "staff_id": member["user_id"]}})
    await audit(db, member, collection=collection, case_id=case_id, decision=body.status, note=body.note)
    return {"ok": True, "id": case_id, "status": body.status}


async def purge_expired(db, env):
    """Repository-independent expiry; Mongo TTL is an additional backstop."""
    timestamp = datetime.now(timezone.utc)
    await db.dating_reports.delete_many({"env": env, "expires_at": {"$lte": timestamp}})
    await db.dating_moderation.delete_many({"env": env, "expires_at": {"$lte": timestamp}})
