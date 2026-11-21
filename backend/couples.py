"""Two-account invitations, membership, sponsorship and privacy settings."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets
import uuid
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from pymongo import IndexModel, ReturnDocument
from pymongo.errors import DuplicateKeyError

try:
    from . import answer_history, intro_access, partner_checkout, personal_demographics
    from .couples_analytics import is_adult, observe_offer, underage
    from .free_plan import has_member_access, reply_usage
except ImportError:
    import answer_history, intro_access, partner_checkout, personal_demographics
    from couples_analytics import is_adult, observe_offer, underage
    from free_plan import has_member_access, reply_usage


COUPLES_INDEXES = {"couples_pairs": [
    IndexModel([("env", 1), ("benefit_members", 1)], name="one_sponsored_pair_per_member", unique=True,
               partialFilterExpression={"benefit_members": {"$exists": True}}),
    IndexModel([("env", 1), ("members", 1)], name="couples_member_slot", unique=True,
               partialFilterExpression={"members": {"$exists": True}}),
    IndexModel([("state", 1), ("env", 1)], name="active_research_pairs", partialFilterExpression={"state": "active"}),
    IndexModel("token_hash", name="couples_invitation_token", unique=True, sparse=True),
    IndexModel([("env", 1), ("created_at", 1)], name="couples_cohort"),
    IndexModel("inviter", name="couples_inviter"), IndexModel("invitee", name="couples_invitee"),
]}
DEFAULT_PREFERENCES = {"analytics_opt_in": False, "review_return_opt_in": False, "reminders": False, "quiet_start": "21:00", "quiet_end": "08:00", "timezone": "UTC"}
PENDING = ["invited", "confirming"]
HIDDEN_WHEN_CLOSED = {key: "" for key in (
    "members", "token_hash", "declined_by", "journey", "tone_sessions", "tone_jobs", "stats.current_outcomes", "compatibility_sharing",
)}
MEMBER_FIELDS = {"_id": 0, "user_id": 1, "nickname": 1, "closing": 1, "env": 1,
                 "about.demographics.age": 1, "couples_adult_at": 1, "couples_blocked": 1,
                 "tier": 1, "subscription_status": 1, "stripe_subscription_id": 1, "subscription_period_start": 1, "subscription_period_end": 1,
                 "plan_offer": 1, "used_sponsored_seat": 1, "role": 1, "complimentary_entitlement": 1,
                 "intro_entitlement": 1, "intro_billing": 1, "intro_reply_totals": 1, "intro_converted_at": 1,
                 "intro_access_paused": 1, "replies_paused": 1, "billing_risk": 1,
                 "subscription_allowance_adjustments": 1, "topup_payment_tracking": 1, "topup_balance": 1,
                 "membership_plan_id": 1, "stripe_price_id": 1}


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdultConfirmation(Body):
    confirmed: Literal[True]


class ProfilePatch(Body):
    expected_member: str = Field(min_length=1, max_length=128)
    age: int | str | None = None
    height_cm: int | str | None = None
    gender: str | None = None
    sex: str | None = None
    ethnicity: str | None = None
    education: str | None = None


class InviteToken(Body):
    token: str = Field(min_length=32, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")


class PairTarget(Body):
    pair_id: str = Field(min_length=1, max_length=64)


class Leave(PairTarget):
    block: StrictBool = False


class Preferences(Body):
    analytics_opt_in: StrictBool | None = None
    review_return_opt_in: StrictBool | None = None
    reminders: StrictBool | None = None
    quiet_start: str | None = Field(None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    quiet_end: str | None = Field(None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    timezone: str | None = Field(None, min_length=1, max_length=80)

    @field_validator("timezone")
    @classmethod
    def known_zone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("Choose a valid time zone.") from None
        return value


def now():
    return datetime.now(timezone.utc).isoformat()


def require_adult(member):
    if not is_adult(member):
        raise HTTPException(403, "Couples participation requires adult confirmation.")


def block_key(uid):
    return sha256(("couples-block:" + uid).encode()).hexdigest()


def meeting_code(pair, uid):
    return sha256((pair["_id"] + ":" + uid).encode()).hexdigest()[:6].upper()


def display_name(member, pair):
    return (member.get("nickname") or "").strip() or "Member " + meeting_code(pair, member["user_id"])


def incompatible(first, second):
    return (block_key(first["user_id"]) in second.get("couples_blocked", []) or
            block_key(second["user_id"]) in first.get("couples_blocked", []))


def unavailable():
    return HTTPException(404, "This invitation is unavailable.")


async def close_expired(db, uid, env):
    await db.couples_pairs.update_many({"members": uid, "env": env, "state": {"$in": PENDING}, "expires_at": {"$lte": now()}},
        {"$set": {"state": "closed", "closed_at": now(), "close_reason": "expired"}, "$unset": HIDDEN_WHEN_CLOSED})


async def member_info(db, uid, env):
    return await db.users.find_one({"user_id": uid, "env": {"$in": [env, None]}}, MEMBER_FIELDS)


async def active_pair(db, member, write=False, *, allow_intro_read=False):
    require_adult(member)
    uid, env = member["user_id"], member["env"]
    pair = await db.couples_pairs.find_one({"env": env, "state": "active", "members": uid})
    if not pair:
        raise HTTPException(404, "Your account has no active pair.")
    other = await member_info(db, pair["invitee"] if uid == pair["inviter"] else pair["inviter"], env)
    if not is_adult(other) or incompatible(member, other):
        raise HTTPException(403, "This shared space is unavailable.")
    def funded_or_retained_intro(person):
        return (has_member_access(person) or (allow_intro_read and not write
                and intro_access.entitlement(person) and not intro_access.converted(person)))
    if pair.get("self_funded") and not (funded_or_retained_intro(member) and funded_or_retained_intro(other)):
        raise HTTPException(402, "Both members of this space need active Individual subscriptions.")
    if write and not has_member_access(member if pair["sponsor"] == uid else other):
        raise HTTPException(402, "An active Plus sponsor is needed for changes to this space.")
    return pair


async def sponsored_seat_used(db, env, *members):
    if any(member.get("used_sponsored_seat") for member in members):
        return True
    ids = [member["user_id"] for member in members]
    return await db.couples_pairs.find_one({"env": env, "benefit_members": {"$in": ids}}, {"_id": 1}) is not None


async def clear_couples(db, uid):
    # The pair document is also the authorization and CAS boundary for shared
    # content. Deleting it immediately invalidates writes from the other seat.
    scope = {"$or": [{"inviter": uid}, {"invitee": uid}]}
    sponsored = []
    async for used in db.couples_pairs.find({**scope, "benefit_members": {"$exists": True}}, {"benefit_members": 1, "env": 1}):
        sponsored.append((used["_id"], used["env"]))
        # Keep only an individual redemption flag when the original pairing is erased.
        await db.users.update_many({"user_id": {"$in": used["benefit_members"]}}, {"$set": {"used_sponsored_seat": True}})
    # Revoke both seats before draining history. A peer already holding an old
    # snapshot can no longer append to the source after this boundary.
    await db.couples_pairs.update_many(scope, {"$set": {"state": "closed"}, "$unset": HIDDEN_WHEN_CLOSED})
    async for pair in db.couples_pairs.find(scope, {"_id": 1}):
        await answer_history.purge(db, "couples_pairs", {"_id": pair["_id"]})
    await db.couples_pairs.delete_many(scope)
    for pair_id, env in sponsored:
        await partner_checkout.expire_pair(db, pair_id, env)
    await db.couples_pairs.update_many({"declined_by": block_key(uid)}, {"$pull": {"declined_by": block_key(uid)}})
    await db.users.update_one({"user_id": uid}, {"$unset": {
        "couples_adult_at": "", "couples_preferences": "", "couples_first_offer_at": "",
        "couples_first_draft_at": "", "couples_blocked": "", "couples_reminders_seen": "",
    }})


def couples_router(get_db, current_member):
    def no_store(response: Response):
        response.headers["Cache-Control"] = "private, no-store"

    router = APIRouter(prefix="/couples", dependencies=[Depends(no_store)])

    async def workspace(db, member):
        try:
            from . import workspace_access
        except ImportError:
            import workspace_access
        uid, env = member["user_id"], member["env"]
        await close_expired(db, uid, env)
        pair = await db.couples_pairs.find_one({"env": env, "members": uid}, {"journey": 0, "tone_sessions": 0, "tone_jobs": 0, "stats": 0, "token_hash": 0, "declined_by": 0})
        other = None
        if pair:
            other_id = pair.get("invitee") if uid == pair["inviter"] else pair["inviter"]
            if other_id:
                other = await member_info(db, other_id, env)
        has_plus = bool(has_member_access(member))
        adult = is_adult(member)
        invite_access = {}
        if not pair and has_plus:
            invite_access["sponsored_seat_used"] = await sponsored_seat_used(db, env, member)
            if adult:
                await observe_offer(db, member)
        return {"adult": adult, "underage": underage(member), "can_sponsor": has_plus, "invite_access": invite_access,
                "workspace_access": await workspace_access.snapshot(db, member),
                **({"usage": reply_usage(member)} if has_plus else {}),
                "state": pair["state"] if pair else "solo", "pair_id": pair["_id"] if pair else None,
                "side": ("inviter" if pair["inviter"] == uid else "invitee") if pair else None,
                "partner_name": display_name(other, pair) if other else None,
                "my_code": meeting_code(pair, uid) if pair else None,
                "partner_code": meeting_code(pair, other["user_id"]) if other else None,
                "sponsor_me": bool(pair and pair["sponsor"] == uid), "expires_at": pair.get("expires_at") if pair and pair["state"] in PENDING else None,
                "writable": bool(adult and is_adult(other) and pair["state"] == "active" and not incompatible(member, other)
                                 and has_member_access(member if pair["sponsor"] == uid else other)
                                 and (not pair.get("self_funded") or (has_member_access(member) and has_member_access(other)))),
                **{**DEFAULT_PREFERENCES, **(member.get("couples_preferences") or {})}}

    @router.get("")
    async def get_workspace(member=Depends(current_member)):
        return await workspace(get_db(), member)

    async def profile_access(member):
        require_adult(member)
        if not has_member_access(member):
            await active_pair(get_db(), member, write=True)

    @router.get("/profile")
    async def get_profile(member=Depends(current_member)):
        await profile_access(member)
        return {"user_id": member["user_id"], "demographics": (member.get("about") or {}).get("demographics") or {}}

    @router.patch("/profile")
    async def update_profile(body: ProfilePatch, member=Depends(current_member)):
        if body.expected_member != member["user_id"]:
            raise HTTPException(409, "Your signed-in account changed. Reload your questionnaire profile.")
        await profile_access(member)
        fields = body.model_dump(exclude_unset=True, exclude={"expected_member"})
        values = await personal_demographics.save_own(get_db(), member["user_id"], fields)
        return {"demographics": values}

    @router.post("/adult")
    async def confirm_adult(body: AdultConfirmation, member=Depends(current_member)):
        if underage(member):
            raise HTTPException(403, "Couples is for adults aged 18 or older.")
        db = get_db()
        previous = await db.users.find_one({"user_id": member["user_id"], "closing": {"$ne": True}})
        if previous is None or underage(previous):
            raise HTTPException(403, "Your account is not eligible for Couples.")
        if previous.get("couples_adult_at"):
            await answer_history.flush(db, "users", {"user_id": member["user_id"]})
            return await workspace(db, {**member, "couples_adult_at": previous["couples_adult_at"]})
        timestamp = now()
        saved_age = ((previous.get("about") or {}).get("demographics") or {}).get("age")
        update = {"$set": {"couples_adult_at": timestamp}}
        observation = answer_history.event(source="users", source_id=member["user_id"],
            owner_id=member["user_id"], domain="consent", question_id="couples.adult",
            question={"label": "Couples adult participation confirmation"}, signature="couples-adult-v1",
            after={"confirmed": True, "couples_adult_at": timestamp}, consent=answer_history.consent(previous),
            revision=int(previous.get(answer_history.REVISION, 0)) + 1, at=timestamp, kind="eligibility_confirmed")
        answer_history.append(update, observation, previous)
        result = await db.users.update_one(answer_history.guard(
            {"user_id": member["user_id"], "closing": {"$ne": True}, "about.demographics.age": saved_age}, previous), update)
        if not result.matched_count:
            latest = await db.users.find_one({"user_id": member["user_id"], "closing": {"$ne": True}})
            if latest and is_adult(latest):
                await answer_history.flush(db, "users", {"user_id": member["user_id"]})
                return await workspace(db, latest)
            raise HTTPException(403, "Your account is not eligible for Couples.")
        await answer_history.flush(db, "users", {"user_id": member["user_id"]})
        return await workspace(db, {**member, "couples_adult_at": timestamp})

    @router.patch("/preferences")
    async def preferences(body: Preferences, member=Depends(current_member)):
        updates = body.model_dump(exclude_none=True)
        db = get_db()
        if updates:
            saved = None
            for _attempt in range(5):
                query = {"user_id": member["user_id"], "closing": {"$ne": True}}
                previous = await db.users.find_one(query)
                if previous is None:
                    break
                if any(updates.get(field) is True for field in ("analytics_opt_in", "review_return_opt_in", "reminders")):
                    require_adult(previous)
                    query["couples_adult_at"] = previous["couples_adult_at"]
                old = previous.get("couples_preferences") or {}
                if (all(key in old and old[key] == value for key, value in updates.items())
                        and not (updates.get("review_return_opt_in") and not old.get("review_return_since"))):
                    saved = {"couples_preferences": old}
                    break
                fields = {"couples_preferences." + key: value for key, value in updates.items()}
                update = {"$set": fields}
                after = {**{key: old[key] for key in (*DEFAULT_PREFERENCES, "review_return_since") if key in old}, **updates}
                if "review_return_opt_in" in updates:
                    if updates["review_return_opt_in"]:
                        since = old.get("review_return_since") if old.get("review_return_opt_in") else None
                        after["review_return_since"] = since or now()
                        fields["couples_preferences.review_return_since"] = after["review_return_since"]
                    else:
                        after.pop("review_return_since", None)
                        update["$unset"] = {"couples_preferences.review_return_since": ""}
                observation = answer_history.event(source="users", source_id=member["user_id"],
                    owner_id=member["user_id"], domain="consent", question_id="couples.preferences",
                    question={"label": "Couples preferences", "fields": list(DEFAULT_PREFERENCES)},
                    signature="couples-preferences-v1", after=after, consent=answer_history.consent(previous),
                    revision=int(previous.get(answer_history.REVISION, 0)) + 1, kind="preferences_changed")
                answer_history.append(update, observation, previous)
                saved = await db.users.find_one_and_update(answer_history.guard(query, previous), update,
                    projection={"_id": 0, "couples_preferences": 1}, return_document=ReturnDocument.AFTER)
                if saved is not None:
                    break
            if saved is None:
                raise HTTPException(409, "Your eligibility changed. Refresh before saving your preferences.")
            await answer_history.flush(db, "users", {"user_id": member["user_id"]})
            member = {**member, "couples_preferences":saved["couples_preferences"]}
        return await workspace(db, member)

    @router.post("/invite")
    async def invite(member=Depends(current_member)):
        require_adult(member)
        db = get_db()
        uid, env = member["user_id"], member["env"]
        await close_expired(db, uid, env)
        if not has_member_access(member):
            raise HTTPException(402, "An active Plus plan is required to invite a partner.")
        await observe_offer(db, member)
        token = secrets.token_urlsafe(32)
        issued = datetime.now(timezone.utc)
        expiry = (issued + timedelta(days=7)).isoformat()
        details = {"issued_at": issued.isoformat(), "expires_at": expiry, "token_hash": sha256(token.encode()).hexdigest()}
        try:
            await db.couples_pairs.update_one({"env": env, "inviter": uid, "state": "invited", "members": uid},
                {"$set": details, "$unset": {"declined_by": ""}, "$setOnInsert": {"_id": uuid.uuid4().hex, "env": env, "inviter": uid,
                  "sponsor": uid, "members": [uid], "state": "invited", "created_at": issued.isoformat()}}, upsert=True)
        except DuplicateKeyError:
            raise HTTPException(409, "Finish or leave your current pairing first.") from None
        return {"token": token, "expires_at": expiry, "workspace": await workspace(db, member)}

    async def find_invite(db, member, token):
        require_adult(member)
        uid, env = member["user_id"], member["env"]
        pair = await db.couples_pairs.find_one({"env": env, "token_hash": sha256(token.encode()).hexdigest(),
                                               "declined_by": {"$ne": block_key(uid)},
                                               "state": {"$in": PENDING}, "expires_at": {"$gt": now()}})
        if not pair or pair["inviter"] == uid or pair.get("invitee") not in (None, uid):
            raise unavailable()
        inviter = await member_info(db, pair["inviter"], env)
        if not is_adult(inviter) or incompatible(member, inviter):
            raise unavailable()
        return pair, inviter

    async def invite_funding(db, member, inviter):
        if not has_member_access(inviter):
            return {"sponsored": False, "can_join": False}
        sponsored = not await sponsored_seat_used(db, member["env"], member, inviter)
        return {"sponsored": sponsored, "can_join": sponsored or bool(has_member_access(member))}

    @router.post("/invite/preview")
    async def preview(body: InviteToken, member=Depends(current_member)):
        db = get_db()
        pair, inviter = await find_invite(db, member, body.token)
        return {"inviter_name": display_name(inviter, pair), "inviter_code": meeting_code(pair, pair["inviter"]),
                "expires_at": pair["expires_at"], **await invite_funding(db, member, inviter)}

    @router.post("/invite/decline")
    async def decline(body: InviteToken, member=Depends(current_member)):
        require_adult(member)
        uid = member["user_id"]
        # An invite can be forwarded. A decline belongs to its recipient only.
        query = {"env": member["env"], "token_hash": sha256(body.token.encode()).hexdigest(),
                 "inviter": {"$ne": uid}, "expires_at": {"$gt": now()}}
        db = get_db()
        result = await db.couples_pairs.update_one({**query, "state": "invited"}, {"$addToSet": {"declined_by": block_key(uid)}})
        if not result.matched_count and await db.couples_pairs.find_one({**query, "state": "confirming", "invitee": uid}, {"_id": 1}):
            raise HTTPException(409, "This invitation was already accepted. Leave the pending pair to withdraw.")
        return {"ok": True}

    @router.post("/invite/accept")
    async def accept(body: InviteToken, member=Depends(current_member)):
        db = get_db()
        uid, env = member["user_id"], member["env"]
        await close_expired(db, uid, env)
        pair, inviter = await find_invite(db, member, body.token)
        if pair["state"] == "confirming":
            return await workspace(db, member)
        if not (await invite_funding(db, member, inviter))["can_join"]:
            raise HTTPException(409, "This invitation needs an active sponsor and an available sponsored seat or two active Plus memberships.")
        try:
            updated = await db.couples_pairs.update_one({"_id": pair["_id"], "state": "invited", "expires_at": {"$gt": now()},
                                                         "token_hash": pair["token_hash"], "declined_by": {"$ne": block_key(uid)}},
                {"$set": {"invitee": uid, "state": "confirming", "accepted_at": now()}, "$push": {"members": uid}})
        except DuplicateKeyError:
            raise HTTPException(409, "Finish or leave your current pairing first.") from None
        if not updated.matched_count:
            if not await db.couples_pairs.find_one({"_id": pair["_id"], "state": "confirming", "invitee": uid}):
                raise unavailable()
        return await workspace(db, member)

    @router.post("/confirm")
    async def confirm(body: PairTarget, member=Depends(current_member)):
        require_adult(member)
        db = get_db()
        uid, env = member["user_id"], member["env"]
        query = {"_id": body.pair_id, "env": env, "inviter": uid, "members": uid}
        pair = await db.couples_pairs.find_one({**query, "state": {"$in": ["confirming", "active"]}})
        if not pair:
            raise HTTPException(404, "No pairing is waiting for your confirmation.")
        if pair["state"] == "active":
            return await workspace(db, member)
        if not has_member_access(member):
            raise HTTPException(402, "An active Plus plan is required to confirm this pair.")
        partner = await member_info(db, pair["invitee"], env)
        if not is_adult(partner) or incompatible(member, partner):
            raise unavailable()
        activation = {**query, "state": "confirming", "expires_at": {"$gt": now()}}
        changes = {"state": "active", "joined_at": now()}
        cleanup = {"token_hash": "", "expires_at": "", "declined_by": ""}
        try:
            used = member.get("used_sponsored_seat") or partner.get("used_sponsored_seat")
            updated = None if used else await db.couples_pairs.update_one(activation, {"$unset": cleanup,
                "$set": {**changes, "benefit_members": [uid, partner["user_id"]]}})
        except DuplicateKeyError:
            updated = None
        if updated is None:
            if not has_member_access(partner):
                raise HTTPException(409, "The sponsored seat is limited to one confirmed couple per account.")
            updated = await db.couples_pairs.update_one(activation, {"$set": {**changes, "self_funded": True}, "$unset": cleanup})
        if not updated.matched_count and not await db.couples_pairs.find_one({**query, "state": "active"}):
            raise unavailable()
        return await workspace(db, member)

    @router.post("/sponsor")
    async def sponsor(body: PairTarget, member=Depends(current_member)):
        db = get_db()
        pair = await active_pair(db, member)
        if pair["_id"] != body.pair_id:
            raise HTTPException(404, "Shared space unavailable.")
        if not has_member_access(member):
            raise HTTPException(402, "An active Plus plan is required to sponsor this space.")
        await db.couples_pairs.update_one({"_id": pair["_id"], "state": "active", "members": member["user_id"]},
                                          {"$set": {"sponsor": member["user_id"]}})
        return await workspace(db, member)

    @router.post("/leave")
    async def leave(body: Leave, member=Depends(current_member)):
        db = get_db()
        uid = member["user_id"]
        query = {"_id": body.pair_id, "env": member["env"], "$or": [{"inviter": uid}, {"invitee": uid}]}
        pair = await db.couples_pairs.find_one(query, {"inviter": 1, "invitee": 1})
        if not pair:
            raise HTTPException(404, "Shared space unavailable.")
        other = pair.get("invitee") if pair["inviter"] == uid else pair["inviter"]
        if body.block and other:
            await db.users.update_one({"user_id": uid}, {"$addToSet": {"couples_blocked": block_key(other)}})
        await db.couples_pairs.update_one({**query, "members": uid}, {
            "$set": {"state": "closed", "closed_at": now(), "close_reason": "left"},
            "$unset": HIDDEN_WHEN_CLOSED})
        await answer_history.purge(db, "couples_pairs", {"_id": pair["_id"]})
        await db.couples_pairs.update_one({"_id": pair["_id"], "state": "closed"},
            {"$unset": {**HIDDEN_WHEN_CLOSED, **{field: "" for field in answer_history.FIELDS}}})
        await partner_checkout.expire_pair(db, pair["_id"], member["env"])
        return await workspace(db, member)

    return router
