"""Member exports and resumable account closure."""
import asyncio
import hashlib
import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import stripe
from bson import json_util
from fastapi import HTTPException
from pymongo import ReturnDocument
from standardwebhooks import Webhook, WebhookVerificationError

try:
    from . import archetype_copy
except ImportError:
    import archetype_copy


CONTENT = ("people", "memories", "exchanges", "preps", "patterns", "corrections", "practice", "group_chats",
           "chat_groups", "group_starters", "notifications", "events", "couples_drafts", "your_type_sessions", "your_type_profiles", "quick_advice")
ACCOUNT_COLLECTIONS = (*CONTENT, "payment_transactions", "ai_request_usage", "intimacy_answers", "research_responses")
PRIVATE_FIELDS = {"_id", "request_leases", "close_lock", "close_lock_until", "closing", "clerk_identity_gone", "admin_demotion",
                  "closure_billing_done", "checkout_attempt", "topup_checkout_attempt", "topup_grants", "topup_payment_tracking", "billing_risk", "plan_upgrade_authorizations",
                  "free_reply_requests", "message_cost_outbox", "intro_active_request", "intro_risk_flags", "intro_billing", "intro_ineligibility", "row_member_code", "row_couple_code", "row_couple_pair_id", "couples_blocked", "couples_reminders_seen",
                  "couples_pilot_admin_change", "reply_access_admin_change", "admin_member_actions", "content_clear_key", "content_clear_until", "content_revision"}
try:
    from . import history_lifecycle
    from .intimacy_answers import own_export as own_intimacy_export
    from .research import own_export as own_research_export
    from .game_plans import saved_plan
    from .membership_admin import retain_closed_revenue
    from .message_charge import flush_message_cost_outbox
    from .couples import clear_couples
    from .couples_journey import own_journey_export
    from .membership_plans import PLANS, TOPUP
    from .stripe_partner_prices import LOOKUP_KEYS as PARTNER_KEYS
except ImportError:
    import history_lifecycle
    from intimacy_answers import own_export as own_intimacy_export
    from research import own_export as own_research_export
    from game_plans import saved_plan
    from membership_admin import retain_closed_revenue
    from message_charge import flush_message_cost_outbox
    from couples import clear_couples
    from couples_journey import own_journey_export
    from membership_plans import PLANS, TOPUP
    from stripe_partner_prices import LOOKUP_KEYS as PARTNER_KEYS

log = logging.getLogger(__name__)
PRIVATE_FIELDS.update(history_lifecycle.PRIVATE_FIELDS)


def identity_key(uid):
    return hashlib.sha256(("imladri-personal:" + uid).encode()).hexdigest()


async def membership_history(db, uid):
    return await db.membership_history.find_one({"_id": identity_key(uid)})


async def rejoin(db, uid):
    if await db.users.find_one({"user_id": uid, "closing": True}, {"_id": 1}):
        raise HTTPException(409, "Account closure is still in progress.")
    await db.membership_history.update_one({"_id": identity_key(uid), "closed": True}, {"$set": {"closed": False}})



def dump_json(value):
    return json.dumps(archetype_copy.export_document(value), ensure_ascii=False, default=json_util.default, separators=(",", ":"))


async def export_collection(name, cursor, person_ids):
    yield ',' + dump_json(name) + ':['
    separator = ''
    async for doc in cursor.batch_size(100):
        doc = {key: value for key, value in doc.items() if key not in history_lifecycle.PRIVATE_FIELDS}
        if name == "people" and doc.get("id"):
            person_ids.add(doc["id"])
        if name in ("group_starters", "group_chats"):
            # Older releases could retain generated text after a profile was deleted.
            members = doc.get("person_ids") or (doc["key"].split(",") if doc.get("key") else [])
            if members and not person_ids.issuperset(members):
                continue
        yield separator + dump_json(saved_plan(doc) if name == "preps" else doc)
        separator = ','
    yield ']'


async def content_impact(db, uid):
    own = {"user_id": uid}
    collections = ("people", "exchanges", "memories", "preps", "practice", "couples_drafts", "your_type_sessions", "quick_advice")
    counts = await asyncio.gather(*(db[name].count_documents(
        {**own, "deleted": {"$ne": True}} if name in ("couples_drafts", "quick_advice") else own
    ) for name in collections))
    pair = await db.couples_pairs.find_one({"members": uid, "state": "active"}, {"_id": 1})
    return {"counts": dict(zip(collections, counts)), "shared_space_active": pair is not None}


async def export_json(db, member):
    account = {key: value for key, value in member.items() if key not in PRIVATE_FIELDS}
    intro = member.get("intro_billing")
    if isinstance(intro, dict):
        account["intro_billing"] = {key: intro[key] for key in ("id", "version", "state", "selected_plan", "authorization",
            "created_at", "confirmed_at", "converted_at", "cancelled_at", "initial_invoice_id") if key in intro}
    yield '{"account":' + dump_json(account)
    yield ',"couples_shared_contributions":' + dump_json(await own_journey_export(db, member["user_id"]))
    from . import dating
    yield ',"dating":' + dump_json(await dating.own_export(db, member["user_id"]))
    person_ids = set()
    for name in ACCOUNT_COLLECTIONS:
        if name == "quick_advice":
            from .quick_advice import own_export
            yield ',"quick_advice":['
            separator = ''
            async for row in own_export(db, member):
                yield separator + dump_json(row)
                separator = ','
            yield ']'
            continue
        if name == "your_type_sessions":
            from .your_type import own_export
            yield ',"your_type_sessions":' + dump_json(await own_export(db, member["user_id"]))
            continue
        if name == "intimacy_answers":
            yield ',' + dump_json(name) + ':' + dump_json(await own_intimacy_export(db, member["user_id"]))
            continue
        if name == "research_responses":
            yield ',' + dump_json(name) + ':' + dump_json(await own_research_export(db, member["user_id"]))
            continue
        criteria = {"user_id": member["user_id"]}
        if name == "people":
            criteria["deleting"] = {"$ne": True}
        if name == "couples_drafts":
            criteria["deleted"] = {"$ne": True}
        projection = {"_id": 0, "tone_job": 0, "tone_coach.signature": 0} if name == "couples_drafts" else {"_id": 0}
        async for chunk in export_collection(name, db[name].find(criteria, projection), person_ids):
            yield chunk
    yield ',"answer_history":['
    separator = ''
    async for event in history_lifecycle.own_events(db, member["user_id"]):
        yield separator + dump_json(event)
        separator = ','
    yield ']'
    yield '}'


async def person_export_json(db, person):
    yield '{"person":' + dump_json({key: value for key, value in person.items()
                                   if key not in history_lifecycle.PRIVATE_FIELDS})
    owner = {"user_id": person["user_id"]}
    person_ids = set(await db.people.distinct("id", {**owner, "deleting": {"$ne": True}}))
    for name in ("memories", "exchanges", "corrections", "practice", "preps", "group_chats"):
        reference = "person_ids" if name == "group_chats" else "person_id"
        cursor = db[name].find({**owner, reference: person["id"]}, {"_id": 0})
        async for chunk in export_collection(name, cursor, person_ids):
            yield chunk
    yield '}'


class ReleaseRequestLease:
    """Keep writes admitted before closure covered through response background work."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        try:
            await self.app(scope, receive, send)
        finally:
            lease = scope.get("state", {}).get("member_write_lease")
            if lease:
                collection, uid, key = lease
                await collection.update_one({"user_id": uid}, {"$unset": {"request_leases." + key: ""}})


_ANY_REVISION = object()


async def reserve_request(db, uid, revision=_ANY_REVISION):
    key = uuid.uuid4().hex
    timestamp = datetime.now(timezone.utc)
    expiry = timestamp + timedelta(minutes=20)
    eligible = {"user_id": uid, "closing": {"$ne": True},
                "$or": [{"content_clear_until": None}, {"content_clear_until": {"$lte": timestamp}}]}
    if revision is not _ANY_REVISION:
        eligible["content_revision"] = revision
    result = await db.users.update_one(eligible, {"$set": {"request_leases." + key: expiry}})
    return key if result.matched_count else None


async def admit_request(db, request, member):
    # These GET handlers can persist derived data or reconcile payments.
    writing_reads = {"type_compare", "overview", "person_starters", "list_notifications", "payment_status", "get_workspace", "own_status"}
    route = request.scope.get("route")
    if request.method == "GET" and getattr(route, "name", None) not in writing_reads:
        if member.get("closing") and request.url.path not in ("/api/me", "/api/me/export"):
            raise HTTPException(409, "Account closure is in progress.")
        return
    key = await reserve_request(db, member["user_id"], member.get("content_revision"))
    if not key:
        raise HTTPException(409, "Account data changed or is being cleared. Refresh and try again.")
    request.state.member_write_lease = (db.users, member["user_id"], key)


@asynccontextmanager
async def exclusive_content_clear(db, request, uid):
    """Exclude stale draft or pairing writes while a member clears their content."""
    timestamp = datetime.now(timezone.utc)
    guard = uuid.uuid4().hex
    query = {"user_id": uid, "closing": {"$ne": True},
             "$or": [{"content_clear_until": None}, {"content_clear_until": {"$lte": timestamp}}]}
    acquired = await db.users.find_one_and_update(query,
        {"$set": {"content_clear_key": guard, "content_clear_until": timestamp + timedelta(minutes=20)}},
        projection={"request_leases": 1}, return_document=ReturnDocument.AFTER)
    if not acquired:
        raise HTTPException(409, "Account content is currently being cleared. Please try again.")
    try:
        own = getattr(request.state, "member_write_lease", (None, None, None))[2]
        if any(key != own and expiry.replace(tzinfo=timezone.utc) > timestamp
               for key, expiry in (acquired.get("request_leases") or {}).items()):
            raise HTTPException(409, "A change is still being saved. Please try clearing content again.")
        await db.users.update_one({"user_id": uid, "content_clear_key": guard}, {"$inc": {"content_revision": 1}})
        yield
    finally:
        await db.users.update_one({"user_id": uid, "content_clear_key": guard},
                                  {"$unset": {"content_clear_key": "", "content_clear_until": ""}})


SUBSCRIPTION_KEYS = {"plus_monthly", *PARTNER_KEYS.values(), *(plan["lookup_key"] for plan in PLANS.values())}


def owns_personal_payment(obj, uid):
    metadata = obj.get("metadata") or {}
    return metadata.get("user_id") == uid and metadata.get("lookup_key") in SUBSCRIPTION_KEYS


def close_billing(subscriptions, sessions, uid):
    if not subscriptions and not sessions:
        return
    if not stripe.api_key:
        raise HTTPException(503, "Account closure is waiting for billing confirmation.")
    try:
        from_checkout = set()
        for sid in sessions:
            session = stripe.checkout.Session.retrieve(sid)
            metadata = session.get("metadata") or {}
            refill = (session.get("mode") == "payment" and metadata.get("user_id") == uid
                      and metadata.get("lookup_key") == TOPUP["lookup_key"])
            if not (refill or owns_personal_payment(session, uid)):
                continue
            if session.get("status") == "open":
                session = stripe.checkout.Session.expire(sid)
            if session.get("status") == "complete":
                if refill:
                    continue
                sub = session.get("subscription")
                if not isinstance(sub, str):
                    raise HTTPException(503, "Account closure is waiting for billing confirmation.")
                subscriptions.add(sub)
                from_checkout.add(sub)
            elif session.get("status") != "expired":
                raise HTTPException(503, "Account closure is waiting for billing confirmation.")
        for sub in subscriptions:
            current = stripe.Subscription.retrieve(sub)
            meta = current.get("metadata") or {}
            items = (current.get("items") or {}).get("data", [])
            if meta.get("user_id") and meta["user_id"] != uid:
                continue
            if not meta.get("user_id") or len(items) != 1 or current["items"].get("has_more"):
                # An ambiguous or mixed subscription must be resolved without canceling another product.
                raise HTTPException(503, "Account closure is waiting for billing confirmation.")
            price = items[0].get("price") or {}
            price_key = price.get("lookup_key") if isinstance(price, dict) or hasattr(price, "get") else None
            if (meta.get("lookup_key") and meta["lookup_key"] not in SUBSCRIPTION_KEYS) or (price_key and price_key not in SUBSCRIPTION_KEYS):
                continue
            # Original Plus subscriptions only tagged their Checkout session and Price.
            if not (owns_personal_payment(current, uid) or price_key in SUBSCRIPTION_KEYS or sub in from_checkout):
                raise HTTPException(503, "Account closure is waiting for billing confirmation.")
            if current.get("status") not in ("canceled", "incomplete_expired"):
                if current.get("schedule"):
                    schedule = current["schedule"]
                    schedule_id = schedule if isinstance(schedule, str) else schedule["id"]
                    result = stripe.SubscriptionSchedule.cancel(schedule_id, invoice_now=False, prorate=False)
                else:
                    result = stripe.Subscription.cancel(sub, invoice_now=False, prorate=False)
                if result.get("status") != "canceled":
                    raise HTTPException(503, "Account closure is waiting for billing confirmation.")
    except stripe.StripeError:
        raise HTTPException(503, "Account closure is waiting for billing confirmation.") from None


async def finish_closure(db, uid, *, identity_gone=False):
    now = datetime.now(timezone.utc)
    owner = {"user_id": uid}
    update = {"closing": True}
    if identity_gone:
        update["clerk_identity_gone"] = True
    exists = await db.users.update_one(owner, {"$set": update}, upsert=False)
    if not exists.matched_count and not exists.upserted_id:
        # A signed deletion can follow an earlier Personal-only closure.
        if identity_gone:
            await db.membership_history.delete_one({"_id": identity_key(uid)})
        return
    lock = uuid.uuid4().hex
    member = await db.users.find_one_and_update({**owner, "$or": [{"close_lock_until": None}, {"close_lock_until": {"$lte": now}}]},
        {"$set": {"close_lock": lock, "close_lock_until": now + timedelta(minutes=10)}}, return_document=ReturnDocument.AFTER)
    if not member:
        raise HTTPException(503, "Account closure is already in progress.", headers={"Retry-After": "10"})
    try:
        if any(expiry.replace(tzinfo=timezone.utc) > now for expiry in (member.get("request_leases") or {}).values()):
            raise HTTPException(503, "Account closure is waiting for an in-progress request.", headers={"Retry-After": "10"})
        if not member.get("closure_billing_done"):
            subs = {member["stripe_subscription_id"]} if member.get("stripe_subscription_id") else set()
            sessions, attempts = set(), set()
            intro = member.get("intro_billing") or {}
            if intro.get("subscription_id"):
                subs.add(intro["subscription_id"])
            if intro.get("session_id"):
                sessions.add(intro["session_id"])
            elif intro.get("setup_stage") in ("prepared", "dispatched", "unknown"):
                # Never erase ownership of an unresolved hosted creation.
                raise HTTPException(503, "Account closure is waiting for introductory setup reconciliation.")
            async for tx in db.payment_transactions.find(owner, {"kind": 1, "session_id": 1, "payment_status": 1, "stripe_subscription_id": 1, "checkout_attempt_id": 1}):
                if tx.get("stripe_subscription_id"):
                    subs.add(tx["stripe_subscription_id"])
                if tx.get("checkout_attempt_id"):
                    attempts.add(tx["checkout_attempt_id"])
                pending_purchase = tx.get("kind") in ("purchase", "intro_setup") and not tx.get("stripe_subscription_id")
                pending_refill = tx.get("kind") == "topup" and tx.get("payment_status") == "pending"
                if (pending_purchase or pending_refill) and tx.get("session_id") and tx.get("payment_status") not in ("expired", "failed"):
                    sessions.add(tx["session_id"])
            for field in ("checkout_attempt", "topup_checkout_attempt"):
                pending = member.get(field) or {}
                if pending.get("id") and pending["id"] not in attempts and pending.get("expires", "") > now.isoformat():
                    # A Checkout creation may have timed out before its response was journaled.
                    raise HTTPException(503, "Account closure is waiting for an outstanding checkout to expire.")
            await asyncio.to_thread(close_billing, subs, sessions, uid)
            await db.users.update_one({**owner, "close_lock": lock}, {"$set": {"closure_billing_done": True}})
        # A closed Personal membership must never be silently recreated by a shared Clerk session.
        await db.membership_history.update_one({"_id": identity_key(uid)},
            {"$set": {"closed": True}, "$unset": {"free_replies_used": ""}}, upsert=True)
        await clear_couples(db, uid)
        from . import dating
        await dating.clear_member(db, uid)
        from .your_type import clear_member
        await clear_member(db, uid)
        await history_lifecycle.purge_owner(db, uid)
        if await db.users.find_one({**owner, "used_sponsored_seat": True}, {"_id": 1}):
            await db.membership_history.update_one({"_id": identity_key(uid)}, {"$set": {"used_sponsored_seat": True}})
        env = member.get("env") or ("live" if os.getenv("CLERK_PUBLISHABLE_KEY", "").startswith("pk_live_") else "preview")
        await retain_closed_revenue(db, uid, env)
        await flush_message_cost_outbox(db, env, uid)
        for name in ACCOUNT_COLLECTIONS:
            await db[name].delete_many(owner)
        await db.users.delete_one({**owner, "close_lock": lock})
        if member.get("clerk_identity_gone"):
            await db.membership_history.delete_one({"_id": identity_key(uid)})
    finally:
        await db.users.update_one({**owner, "close_lock": lock}, {"$unset": {"close_lock": "", "close_lock_until": ""}})


async def retry_closures(db):
    while True:
        try:
            async for member in db.users.find({"closing": True}, {"user_id": 1}):
                try:
                    await finish_closure(db, member["user_id"])
                except HTTPException:
                    log.warning("Account closure pending; retry scheduled")
        except Exception:
            log.error("Account closure recovery could not complete its scan")
        await asyncio.sleep(60)


def deleted_identity(payload, headers):
    secret = os.environ.get("CLERK_WEBHOOK_SIGNING_SECRET")
    if not secret:
        raise HTTPException(503, "Clerk webhook has not been configured.")
    signature = {"webhook-" + name: headers.get("svix-" + name, "") for name in ("id", "timestamp", "signature")}
    try:
        event = Webhook(secret).verify(payload, signature)
    except (WebhookVerificationError, ValueError, TypeError):
        raise HTTPException(400, "Invalid Clerk webhook.") from None
    if not isinstance(event, dict):
        raise HTTPException(400, "Invalid Clerk webhook.")
    if event.get("type") != "user.deleted":
        return None
    uid = (event.get("data") or {}).get("id")
    if not isinstance(uid, str) or not re.fullmatch(r"user_[A-Za-z0-9]+", uid):
        raise HTTPException(400, "Invalid Clerk account identifier.")
    return uid
