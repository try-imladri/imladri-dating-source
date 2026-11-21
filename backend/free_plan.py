"""Reserve a reply atomically on its owning account and preserve retried results."""
import asyncio
import calendar
import hashlib
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

try:
    from . import ai_meter, intro_access, message_charge, message_costs, billing_risk, billing_acceptance
    from .message_charge import flush_message_cost_outbox
    from .membership_plans import BASE_PLAN, member_plan
except ImportError:
    import ai_meter, intro_access, message_charge, message_costs, billing_risk, billing_acceptance
    from message_charge import flush_message_cost_outbox
    from membership_plans import BASE_PLAN, member_plan

PLUS_MESSAGE_LIMIT = BASE_PLAN["messages"]
GENERATION_TIMEOUT = 480
LEASE_LENGTH = timedelta(minutes=10)
# A retry can cross a month boundary. Keep paid receipts for a full month after their cycle.
PAID_RETRY_WINDOW = timedelta(days=35)
log = logging.getLogger(__name__)
COMPLIMENTARY_VERSION = "complimentary-unlimited-v1"


def complimentary_access(member):
    grant = member.get("complimentary_entitlement")
    if (member.get("role") != "member" or member.get("closing")
            or not isinstance(grant, dict) or set(grant) != {"version", "id", "active"}
            or grant.get("version") != COMPLIMENTARY_VERSION or grant.get("active") is not True
            or not isinstance(grant.get("id"), str)):
        return False
    try:
        return str(uuid.UUID(grant["id"])) == grant["id"]
    except ValueError:
        return False


def has_member_access(member):
    """Product entitlement without granting any staff authorization."""
    return complimentary_access(member) or paid_cycle(member) is not None or intro_access.access(member)


def month_boundary(start, months):
    anchor = datetime.fromtimestamp(start, timezone.utc)
    year, month = divmod(anchor.year * 12 + anchor.month - 1 + months, 12)
    day = min(anchor.day, calendar.monthrange(year, month + 1)[1])
    return int(anchor.replace(year=year, month=month + 1, day=day).timestamp())


def quota_period(member, current=None):
    subscription = member.get("stripe_subscription_id")
    start = member.get("subscription_period_start")
    end = member.get("subscription_period_end")
    moment = time.time() if current is None else current
    if (member.get("tier") == "paid" and isinstance(subscription, str) and subscription.strip()
            and member.get("subscription_status") in (None, "active", "past_due")
            and type(start) is int and type(end) is int and 0 < start <= moment < end):
        if member.get("plan_offer") == "couple_annual":
            anchor = datetime.fromtimestamp(start, timezone.utc)
            today = datetime.fromtimestamp(moment, timezone.utc)
            months = (today.year - anchor.year) * 12 + today.month - anchor.month
            if month_boundary(start, months) > moment:
                months -= 1
            return month_boundary(start, months), min(end, month_boundary(start, months + 1))
        return start, end
    return None


def cycle_key(member, period):
    if period:
        return hashlib.sha256(f"{member['stripe_subscription_id']}/{period[0]}".encode()).hexdigest()
    return None


def paid_cycle(member):
    return cycle_key(member, quota_period(member))


def effective_message_limit(member):
    """Only the verified invoice's own period can reduce its plan allowance."""
    limit = member_plan(member)["messages"]
    period = quota_period(member)
    if not period:
        return limit
    records = member.get("subscription_allowance_adjustments") or {}
    if not isinstance(records, dict):
        return 0
    for record in records.values():
        if not isinstance(record, dict):
            return 0
        if (record.get("subscription_id") != member.get("stripe_subscription_id")
                or record.get("price_id") != member.get("stripe_price_id")):
            continue
        start, end, remaining, original = (record.get(k) for k in ("period_start", "period_end", "net_messages", "original_messages"))
        if (record.get("version") != "billing-adjustments-v1" or type(start) is not int
                or type(end) is not int or type(original) is not int or type(remaining) is not int or not 0 <= remaining <= original):
            return 0
        if start <= period[0] and period[1] <= end:
            available = record.get("available_messages", remaining)
            held = record.get("pending_dispute_messages", 0)
            if type(available) is not int or type(held) is not int or held < 0 or available != remaining - held or available < 0:
                return 0
            limit = min(limit, available)
    return limit


def has_personal_access(member):
    return member.get("role") == "admin" or has_member_access(member)


def subscription_required():
    return HTTPException(402, "A current Imladri Plus subscription is required.",
                         headers={"X-Imladri-Limit": "subscription"})


def billing_tier(member):
    return "complimentary" if complimentary_access(member) else "paid" if paid_cycle(member) else "intro" if intro_access.access(member) else "unsubscribed"


def _reply_usage(member):
    if member.get("role") == "admin":
        return {"plan": "admin", "plan_id": None, "plan_name": None, "unlimited": True,
                "message_limit": None, "messages_used": 0, "monthly_messages_used": 0,
                "monthly_messages_remaining": None, "topup_remaining": 0,
                "messages_remaining": None, "period_start": None, "period_end": None, "period_verified": True}
    if complimentary_access(member):
        used = (member.get("complimentary_reply_totals") or {}).get("used", 0)
        used = used if type(used) is int and used >= 0 else 0
        return {"plan": "complimentary", "plan_id": "complimentary_unlimited", "plan_name": "Complimentary Unlimited",
                "unlimited": True, "message_limit": None, "messages_used": used, "monthly_messages_used": None,
                "monthly_messages_remaining": None, "topup_remaining": 0, "messages_remaining": None,
                "period_start": None, "period_end": None, "period_verified": True, "usage_window": "all_time"}
    period = quota_period(member)
    cycle = cycle_key(member, period)
    topup = max(0, member.get("topup_balance", 0))
    if cycle:
        counters = (member.get("paid_reply_cycles") or {}).get(cycle, {})
        plan = member_plan(member)
        message_limit = effective_message_limit(member)
        included = max(0, message_limit - counters.get("claimed", 0))
        used = counters.get("used", 0)
        adjustment = ({"allowance_adjusted": True, "original_message_limit": plan["messages"]}
                      if message_limit != plan["messages"] else {})
        return {"plan": "plus", "plan_id": plan["id"], "plan_name": plan["name"], **adjustment,
                "unlimited": False, "message_limit": message_limit, "messages_used": used,
                "monthly_messages_used": used - counters.get("topup_used", 0),
                "monthly_messages_remaining": included, "topup_remaining": topup, "messages_remaining": included + topup,
                "period_start": period[0], "period_end": period[1], "period_verified": True}
    if intro_access.entitlement(member) and not intro_access.converted(member):
        claimed, used = intro_access.totals(member)
        remaining = max(0, intro_access.LIMIT - claimed) if intro_access.access(member) else 0
        return {"plan": "intro", "plan_id": "intro_10", "plan_name": "10 introductory messages", "unlimited": False,
                "message_limit": intro_access.LIMIT, "messages_used": used, "monthly_messages_used": used,
                "monthly_messages_remaining": remaining, "topup_remaining": 0,
                "messages_remaining": remaining, "period_start": None, "period_end": None,
                "period_verified": False, "introductory": True}
    return {"plan": "unsubscribed", "plan_id": None, "plan_name": None, "unlimited": False,
            "message_limit": 0, "monthly_messages_used": 0, "monthly_messages_remaining": 0,
            "messages_used": 0, "messages_remaining": 0, "topup_remaining": topup,
            "period_start": None, "period_end": None, "period_verified": False}


def payment_dispute_usage(member):
    if member.get("role") == "admin" or complimentary_access(member):
        return {"payment_dispute_hold": False, "held_message_count": 0}
    held = 0
    period = quota_period(member)
    if period:
        funded = member_plan(member)["messages"]
        available = funded
        for record in (member.get("subscription_allowance_adjustments") or {}).values():
            if (isinstance(record, dict) and record.get("subscription_id") == member.get("stripe_subscription_id")
                    and record.get("price_id") == member.get("stripe_price_id")
                    and type(record.get("period_start")) is int and type(record.get("period_end")) is int
                    and record["period_start"] <= period[0] and period[1] <= record["period_end"]
                    and type(record.get("net_messages")) is int and type(record.get("pending_dispute_messages", 0)) is int
                    and type(record.get("available_messages", record["net_messages"])) is int
                    and 0 <= record.get("pending_dispute_messages", 0) <= record["net_messages"]
                    and record.get("available_messages", record["net_messages"]) == record["net_messages"] - record.get("pending_dispute_messages", 0)):
                funded = min(funded, record["net_messages"])
                available = min(available, record.get("available_messages", record["net_messages"]))
        claimed = ((member.get("paid_reply_cycles") or {}).get(cycle_key(member, period)) or {}).get("claimed", 0)
        held += max(0, funded - claimed) - max(0, available - claimed)
    for record in (member.get("topup_payment_tracking") or {}).values():
        # Only actual per-payment attribution establishes an unused held pack.
        billing_risk.refill_available(record)
        held += min(record["pending_messages"], max(0, record["net_messages"] - record["claimed_messages"]))
    return {"payment_dispute_hold": held > 0, "held_message_count": held}


def reply_usage(member):
    return {**_reply_usage(member), **payment_dispute_usage(member)}


def reply_limit_error(member):
    if intro_access.entitlement(member) and not intro_access.converted(member):
        return HTTPException(402, "You've used your ten introductory messages. Choose a plan to continue.",
                             headers={"X-Imladri-Limit": "intro"})
    if not paid_cycle(member):
        return subscription_required()
    if payment_dispute_usage(member)["held_message_count"] and _reply_usage(member)["messages_remaining"] == 0:
        return HTTPException(402, "Some unused messages are temporarily held for payment review. Saved data and cancellation remain available; contact support.",
                             headers={"X-Imladri-Limit": "payment-review"})
    return HTTPException(402, "You've used your monthly replies and any available top-ups.", headers={"X-Imladri-Limit": "messages"})


def require_new_reply_access(member):
    billing_risk.require_action(member, "new_ai")
    if member.get("replies_paused"):
        raise HTTPException(403, "New replies are paused for this account. Contact support for help.")


def quick_person_id(reply_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"imladri/quickstart/{reply_id}"))


async def saved_reply(db, uid, receipt):
    kind, rid = receipt["kind"], receipt["result"]
    if kind in ("quick_advice", "couples_quick_advice"):
        # A sponsored member owns the text; the payer owns only its allowance receipt.
        return await db.quick_advice.find_one({"id": rid, "user_id": receipt["scope"]["owner_id"],
            "workspace": receipt["scope"]["workspace"], "pair_id": receipt["scope"]["pair_id"]}, {"_id": 0})
    if kind == "couples_tone":
        # Only establish whether generation committed. The actor's live source
        # authorization is required separately to restore their private content.
        source = receipt["scope"]
        collection = db.couples_drafts if source["slot"] == "tone_coach" else db.couples_pairs
        exists = await collection.find_one({"_id": source["document"], source["slot"] + ".id": rid}, {"_id": 1})
        return {"id": rid} if exists else None
    query = {"user_id": uid, "id": rid}
    if kind == "practice":
        return await db.practice.find_one({"user_id": uid, "id": receipt["scope"], "free_reply_ids": rid},
                                          {"_id": 0, "free_reply_ids": 0})
    collection = {"quickstart": "exchanges", "exchange": "exchanges", "group": "group_chats", "prep": "preps"}[kind]
    return await db[collection].find_one(query, {"_id": 0})


def counter_paths(cycle, pool=None):
    if pool == "intro":
        return "intro_reply_totals.claimed", "intro_reply_totals.used"
    if pool == "complimentary":
        return "complimentary_reply_totals.claimed", "complimentary_reply_totals.used"
    if cycle:
        return f"paid_reply_cycles.{cycle}.claimed", f"paid_reply_cycles.{cycle}.used"
    return "free_replies_claimed", "free_replies_used"


async def settle(db, uid, key, receipt, success):
    path = f"free_reply_requests.{key}"
    claimed, used = counter_paths(receipt.get("cycle"), receipt.get("pool"))
    query = {"user_id": uid, path + ".pending": True, path + ".result": receipt["result"]}
    tracked = "cost_run" in receipt
    if tracked:
        query[path + ".cost_run"] = receipt["cost_run"]
    event = message_charge.unit_event(receipt, "settled" if success else "refunded") if tracked else None
    if success:
        update = {"$set": {path + ".pending": False}, "$inc": {used: 1}}
        day = (event["settled_at"] if event else datetime.now(timezone.utc)).date().isoformat()
        pool = "intro" if receipt.get("pool") == "intro" else "complimentary" if receipt.get("pool") == "complimentary" else "refill" if receipt.get("pool") == "topup" else "plan"
        update["$inc"][f"reply_charge_days.{day}.{pool}"] = 1
        if receipt.get("pool") == "topup":
            update["$inc"][f"paid_reply_cycles.{receipt['cycle']}.topup_used"] = 1
    else:
        release = {"topup_balance": 1} if receipt.get("pool") == "topup" else {claimed: -1}
        update = {"$set": {path + ".pending": False, path + ".refunded": True}, "$inc": release} if tracked else {
            "$unset": {path: ""}, "$inc": release}
    if event:
        update.setdefault("$set", {})["message_cost_outbox." + receipt["result"]] = event
    if not success and receipt.get("pool") == "topup" and receipt.get("topup_payment_key"):
        # Releasing a failed generation must not expose credits that became
        # disputed while the provider was running. Restore only this payment's
        # currently available delta, using live net/hold/claimed values.
        tracking = "topup_payment_tracking." + receipt["topup_payment_key"]
        query[tracking + ".claimed_messages"] = {"$gt": 0}
        decreased = {"$subtract": ["$" + tracking + ".claimed_messages", 1]}
        fields = dict(update.get("$set", {}))
        for field, amount in update.get("$inc", {}).items():
            if field != "topup_balance":
                fields[field] = {"$add": [{"$ifNull": ["$" + field, 0]}, amount]}
        fields.update({tracking + ".claimed_messages": decreased,
            "topup_balance": {"$add": [{"$ifNull": ["$topup_balance", 0]}, {"$subtract": [
                billing_risk.refill_expression(tracking, claimed=decreased), billing_risk.refill_expression(tracking)]}]}})
        update = [{"$set": fields}, *([{"$unset": list(update["$unset"])}] if update.get("$unset") else [])]
    if receipt.get("pool") == "intro":
        query["intro_active_request.id"] = receipt["result"]
        update.setdefault("$unset", {})["intro_active_request"] = ""
    if success and receipt.get("pool") == "intro" and receipt.get("intro_used_before") == 9 and receipt.get("intro_agreement_id"):
        try:
            from . import intro_card
        except ImportError:
            import intro_card
        # Same mutation commits message delivery, used=10 and the durable
        # conversion outbox. Cancellation winning this CAS leaves no charge due.
        due_update = {**update, "$set": {**update.get("$set", {}), "intro_billing.state": "billing_pending",
            "intro_billing.conversion": intro_card.conversion_due(receipt)}}
        due_update["$inc"] = {**update.get("$inc", {}), "intro_billing.revision": 1}
        due = {**query, "intro_billing.id": receipt["intro_agreement_id"], "intro_billing.state": "active",
               "intro_reply_totals.used": 9, "intro_billing.cancellation_requested": {"$ne": True}}
        result = await db.users.update_one(due, due_update)
        if not result.matched_count:
            result = await db.users.update_one(query, update)
    else:
        result = await db.users.update_one(query, update)
    if event and result.matched_count:
        try:
            await message_charge.flush_event(db, uid, event)
        except PyMongoError:
            # The debit and journal are already durable. Admin can rebuild the reporting copy.
            log.warning("Message cost settlement is awaiting reconciliation")


async def initialize_usage(db, member):
    uid = member["user_id"]
    query = {"user_id": uid}
    if member.get("message_cost_outbox"):
        try:
            await flush_message_cost_outbox(db, member.get("env") or deployment_env(), uid)
        except PyMongoError:
            log.warning("Message cost settlement is awaiting reconciliation")
    now = datetime.now(timezone.utc)
    prune_before = (now - PAID_RETRY_WINDOW).timestamp()
    prune = {}
    cutoff_day = (now - timedelta(days=92)).date().isoformat()
    for day in member.get("reply_charge_days") or {}:
        if day < cutoff_day:
            prune["reply_charge_days." + day] = ""
    originals = {}
    changed = False
    if (intro_access.entitlement(member) and intro_access.converted(member)
            and not member.get("intro_converted_at")):
        # Only authoritative paid-period fields (never an open checkout) freeze
        # the unused introduction. Later cancellation/refund cannot regrant it.
        guard = {"user_id": uid, "intro_entitlement.id": member["intro_entitlement"]["id"],
                 "intro_converted_at": {"$exists": False}}
        guard.update({field: member.get(field) for field in ("tier", "stripe_subscription_id",
            "subscription_period_start", "subscription_period_end", "subscription_status")})
        saved = await db.users.update_one(guard, {"$set": {"intro_converted_at": now.isoformat()}})
        changed = bool(saved.matched_count)
    for key, receipt in (member.get("free_reply_requests") or {}).items():
        if receipt["pending"] and receipt["expires"] < now.isoformat():
            persisted = await saved_reply(db, uid, receipt)
            if not persisted and receipt["kind"] == "quickstart":
                await db.people.delete_one({**query, "id": quick_person_id(receipt["result"])})
            await settle(db, uid, key, receipt, bool(persisted))
            changed = True
        if not receipt["pending"] and receipt.get("period_end", float("inf")) < prune_before:
            prune[f"free_reply_requests.{key}"] = ""
            originals[f"free_reply_requests.{key}.result"] = receipt["result"]
            if "cost_run" in receipt:
                originals[f"free_reply_requests.{key}.cost_run"] = receipt["cost_run"]
                originals[f"free_reply_requests.{key}.pending"] = False
    for cycle, bucket in (member.get("paid_reply_cycles") or {}).items():
        if bucket.get("end", float("inf")) < prune_before:
            prune[f"paid_reply_cycles.{cycle}"] = ""
            originals[f"paid_reply_cycles.{cycle}.end"] = bucket["end"]
    if prune:
        await db.users.update_one({**query, **originals}, {"$unset": prune})
    if changed or prune:
        return await db.users.find_one(query, {"_id": 0})
    return member


async def restore_previous(db, uid, previous, kind, scope, signature, restore):
    if (previous["kind"], previous["scope"], previous["signature"]) != (kind, scope, signature):
        raise HTTPException(409, "That request key belongs to a different message")
    if previous["pending"]:
        raise HTTPException(409, "This message is still being processed", headers={"Retry-After": "2"})
    result = await saved_reply(db, uid, previous)
    if result is None:
        raise HTTPException(410, "This saved reply has been deleted")
    return await restore(result)


def deployment_env():
    return "live" if os.getenv("CLERK_PUBLISHABLE_KEY", "").startswith("pk_live_") else "preview"


async def run_metered(db, member, request, body, kind, scope, generate, restore, *, key_namespace="", preparation_id=None):
    if member.get("role") == "admin":
        return await generate(str(uuid.uuid4()))
    uid = member["user_id"]
    supplied_key = request.headers.get("Idempotency-Key")
    if supplied_key is not None and not 1 <= len(supplied_key.strip()) <= 200:
        raise HTTPException(422, "Invalid request key")
    key = hashlib.sha256((key_namespace + (supplied_key or str(uuid.uuid4()))).encode()).hexdigest()
    signature = hashlib.sha256((uid + key + body.model_dump_json()).encode()).hexdigest()
    path = f"free_reply_requests.{key}"
    current = await db.users.find_one({"user_id": uid}, {"_id": 0})
    if current is None:
        raise HTTPException(404, "Account not found")
    period = quota_period(current)
    cycle = cycle_key(current, period)
    complimentary = complimentary_access(current)
    intro = bool(intro_access.access(current) and not cycle and not complimentary
                 and not intro_access.converted(current))
    if (complimentary != complimentary_access(member) or (complimentary and
            current["complimentary_entitlement"] != member.get("complimentary_entitlement"))):
        raise HTTPException(409, "Your access changed. Refresh before requesting another reply.")
    previous = (current.get("free_reply_requests") or {}).get(key)
    if (previous and not previous.get("refunded") and previous.get("pool") == "intro"
            and current.get("role") == "member" and not current.get("closing")
            and previous.get("entitlement_id") == (intro_access.entitlement(current) or {}).get("id")):
        # Exhaustion/cancellation removes new generations, not saved replies.
        return await restore_previous(db, uid, previous, kind, scope, signature, restore)
    if not cycle and not complimentary and not intro:
        raise reply_limit_error(current)
    if previous and not previous.get("refunded"):
        return await restore_previous(db, uid, previous, kind, scope, signature, restore)
    require_new_reply_access(current)
    if not complimentary and cycle != paid_cycle(member):
        raise HTTPException(409, "Your plan changed. Retry this reply.")

    claimed, _ = counter_paths(cycle, "complimentary" if complimentary else "intro" if intro else None)
    retry = previous and (previous["kind"], previous["scope"], previous["signature"]) == (kind, scope, signature)
    if (complimentary or intro) and previous and not retry:
        raise HTTPException(409, "That request key belongs to a different message")
    rid = previous["result"] if retry else str(uuid.uuid4())
    receipt = {"kind": kind, "scope": scope, "signature": signature, "result": rid, "pending": True,
               "cost_env": current.get("env") or deployment_env(),
               "cost_started_at": previous["cost_started_at"] if retry else datetime.now(timezone.utc),
               "cost_run": previous["cost_run"] + 1 if retry else 1,
               "cost_key_id": previous["cost_key_id"] if retry else message_costs.key_id(),
               "expires": (datetime.now(timezone.utc) + LEASE_LENGTH).isoformat()}
    if retry:
        preparation_id = previous.get("cost_upload") or preparation_id
    if preparation_id:
        receipt["cost_upload"] = preparation_id
    if intro:
        reservation, restored = await reserve_intro_reply(db, current, uid, key, path, receipt, previous,
                                                         kind, scope, signature, restore)
        if reservation is None:
            return restored
    elif complimentary:
        receipt.update(cycle=None, period_end=int(time.time()), pool="complimentary",
                       entitlement_id=current["complimentary_entitlement"]["id"])
        claim = {"user_id": uid, "role": "member", "closing": {"$ne": True}, "replies_paused": {"$ne": True},
            "$expr": {"$and": [billing_risk.snapshot_guard(current),
                {"$eq": ["$complimentary_entitlement", {"$literal": current["complimentary_entitlement"]}]}]}}
        if previous:
            claim.update({path + ".result": previous["result"], path + ".cost_run": previous["cost_run"], path + ".refunded": True})
        else:
            claim[path] = {"$exists": False}
        reservation = await db.users.find_one_and_update(claim,
            {"$set": {path: receipt}, "$inc": {claimed: 1}}, projection={path: 1, "_id": 0}, return_document=ReturnDocument.AFTER)
        if not reservation:
            row = await db.users.find_one({"user_id": uid}, {"_id": 0})
            if row:
                previous = (row.get("free_reply_requests") or {}).get(key)
                if previous and not previous.get("refunded"):
                    return await restore_previous(db, uid, previous, kind, scope, signature, restore)
                require_new_reply_access(row)
            raise HTTPException(409, "Your complimentary access changed. Refresh before requesting another reply.")
    else:
        reservation, restored = await reserve_paid_reply(db, current, uid, key, path, receipt, previous, cycle, period,
                                                         kind, scope, signature, restore)
        if reservation is None:
            return restored
    receipt = reservation["free_reply_requests"][key]
    try:
        event = message_charge.unit_event(receipt, "pending")
        await message_charge.persist(db, event)
        with message_costs.message_context(rid, event["workspace"], run_number=receipt["cost_run"], pool=event["pool"], kind=kind):
            output = await asyncio.wait_for(ai_meter.in_context("metered", generate, rid), timeout=GENERATION_TIMEOUT)
    except (Exception, asyncio.CancelledError) as exc:
        result = await saved_reply(db, uid, receipt)
        await settle(db, uid, key, receipt, result is not None)
        if result is not None:
            return await restore(result)
        if isinstance(exc, TimeoutError):
            raise HTTPException(504, "The reply timed out. You can try again without losing a reply.") from exc
        raise
    await settle(db, uid, key, receipt, True)
    return output


async def reserve_intro_reply(db, current, uid, key, path, receipt, previous, kind, scope, signature, restore):
    billing_acceptance.require_intro(current, selected=(current.get("intro_billing") or {}).get("selected_plan"))
    if not intro_access.configured() or (await intro_access.settings(db))["paused"]:
        raise HTTPException(503, "Introductory replies are temporarily unavailable.")
    if current.get("intro_access_paused"):
        raise HTTPException(403, "Introductory access is paused for this account. Contact support for help.")
    if intro_access.totals(current)[1] >= intro_access.LIMIT:
        raise reply_limit_error(current)
    grant = intro_access.entitlement(current)
    if not intro_access.access(current):
        raise reply_limit_error(current)
    row = current["intro_billing"]
    receipt.update(cycle=None, pool="intro", period_end=int(time.time()), entitlement_id=grant["id"],
                   intro_agreement_id=row["id"], intro_used_before=intro_access.totals(current)[1])
    claim = {"user_id": uid, "role": "member", "closing": {"$ne": True}, "replies_paused": {"$ne": True},
        "intro_access_paused": {"$ne": True}, "intro_active_request": {"$exists": False},
        "intro_billing.id": row["id"], "intro_billing.revision": row["revision"], "intro_billing.state": "active",
        "intro_billing.cancellation_requested": {"$ne": True},
        "$expr": {"$and": [billing_risk.snapshot_guard(current), {"$eq": ["$intro_entitlement", {"$literal": grant}]}, *[
            {"$eq": ["$" + field, {"$literal": current[field]}]} if field in current
            else {"$eq": [{"$type": "$" + field}, "missing"]}
            for field in ("tier", "stripe_subscription_id", "subscription_period_start", "subscription_period_end",
                          "subscription_status", "intro_converted_at")]]},
        "$or": [{"intro_reply_totals.claimed": {"$exists": False}}, {"intro_reply_totals.claimed": {"$lt": intro_access.LIMIT}}]}
    if previous:
        claim.update({path + ".result": previous["result"], path + ".cost_run": previous["cost_run"], path + ".refunded": True})
    else:
        claim[path] = {"$exists": False}
    reservation = await db.users.find_one_and_update(claim, {"$set": {path: receipt,
        "intro_active_request": {"id": receipt["result"], "expires": receipt["expires"]}},
        "$inc": {"intro_reply_totals.claimed": 1}}, projection={path: 1, "_id": 0}, return_document=ReturnDocument.AFTER)
    if reservation is not None:
        return reservation, None
    row = await db.users.find_one({"user_id": uid}, {"_id": 0})
    if row:
        old = (row.get("free_reply_requests") or {}).get(key)
        if old and not old.get("refunded"):
            return None, await restore_previous(db, uid, old, kind, scope, signature, restore)
        require_new_reply_access(row)
        if row.get("intro_access_paused"):
            raise HTTPException(403, "Introductory access is paused for this account. Contact support for help.")
        if row.get("intro_active_request"):
            raise HTTPException(409, "Your introductory reply is still being processed. Please wait before sending another.",
                                headers={"Retry-After": "2"})
        if not intro_access.entitlement(row) or intro_access.converted(row):
            raise HTTPException(409, "Your access changed. Refresh before requesting another reply.")
    raise reply_limit_error(current)


async def reserve_paid_reply(db, current, uid, key, path, receipt, previous, cycle, period, kind, scope, signature, restore):
    """Existing paid allowance reservation; complimentary access uses no pool debit."""
    claimed, _ = counter_paths(cycle)
    plan = member_plan(current)
    limit = effective_message_limit(current)
    allowance = ([{claimed: {"$exists": False}}, {claimed: {"$lt": limit}}] if limit > 0 else [])
    claim = {"user_id": uid, "replies_paused": {"$ne": True},
             "$or": [*allowance, {"topup_balance": {"$gt": 0}}]}
    if previous:
        claim.update({path + ".result": previous["result"], path + ".cost_run": previous["cost_run"], path + ".refunded": True})
    else:
        claim[path] = {"$exists": False}
    claim.update({field: current.get(field) for field in ("tier", "stripe_subscription_id", "subscription_period_start",
                 "subscription_period_end", "subscription_status", "membership_plan_id", "plan_offer")})
    adjustment_guard = ({"$eq": ["$subscription_allowance_adjustments", {"$literal": current["subscription_allowance_adjustments"]}]}
        if "subscription_allowance_adjustments" in current else {"$eq": [{"$type": "$subscription_allowance_adjustments"}, "missing"]})
    tracking_guard = ({"$eq": ["$topup_payment_tracking", {"$literal": current["topup_payment_tracking"]}]}
        if "topup_payment_tracking" in current else {"$eq": [{"$type": "$topup_payment_tracking"}, "missing"]})
    claim["$expr"] = {"$and": [adjustment_guard, tracking_guard, billing_risk.snapshot_guard(current)]}
    period_end = period[1]
    receipt.update(cycle=cycle, period_end=period_end)
    monthly_claims = {"$ifNull": ["$" + claimed, 0]}
    use_monthly = {"$lt": [monthly_claims, limit]}
    balance = {"$ifNull": ["$topup_balance", 0]}
    records = current.get("topup_payment_tracking") or {}
    if not isinstance(records, dict):
        raise HTTPException(503, "Refill attribution needs reconciliation.")
    payment_key = None
    for candidate, record in sorted(records.items()):
        if len(candidate) != 64 or any(c not in "0123456789abcdef" for c in candidate):
            raise HTTPException(503, "Refill attribution needs reconciliation.")
        if billing_risk.refill_available(record) > 0 and payment_key is None:
            payment_key = candidate
    refill_receipt = {**receipt, "pool": "topup"}
    tracking_fields = {}
    if payment_key:
        refill_receipt["topup_payment_key"] = payment_key
        field = "topup_payment_tracking." + payment_key + ".claimed_messages"
        tracking_fields[field] = {"$add": ["$" + field, {"$cond": [use_monthly, 0, 1]}]}
    # Choose the pool and reserve it on one document. A concurrent last-credit
    # request, refill, refund or renewal must observe the same decision.
    reservation = await db.users.find_one_and_update(claim, [{"$set": {
        path: {"$cond": [use_monthly, {"$literal": receipt}, {"$literal": refill_receipt}]},
        f"paid_reply_cycles.{cycle}.end": period_end,
        claimed: {"$add": [monthly_claims, {"$cond": [use_monthly, 1, 0]}]},
        "topup_balance": {"$subtract": [balance, {"$cond": [use_monthly, 0, 1]}]},
        **tracking_fields,
    }}], projection={path: 1, "_id": 0}, return_document=ReturnDocument.AFTER)
    if not reservation:
        row = await db.users.find_one({"user_id": uid}, {"_id": 0})
        if row:
            previous = (row.get("free_reply_requests") or {}).get(key)
            if previous and not previous.get("refunded"):
                return None, await restore_previous(db, uid, previous, kind, scope, signature, restore)
            require_new_reply_access(row)
            if row.get("tier") != current.get("tier") or paid_cycle(row) != cycle or member_plan(row) != plan:
                raise HTTPException(409, "Your billing period changed. Retry this reply.")
            if any(row.get(field) != current.get(field) for field in ("subscription_allowance_adjustments", "topup_payment_tracking", "billing_risk")):
                raise HTTPException(409, "Your payment availability changed. Refresh before a new reply.")
        raise reply_limit_error(current)
    return reservation, None
