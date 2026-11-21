"""Eligibility, payment proof and fixed-term subscription changes for a sponsored couple."""
import asyncio
from datetime import datetime, timedelta, timezone

import stripe
from fastapi import HTTPException
from pymongo import ReturnDocument

try:
    from . import membership_plans, stripe_membership, stripe_partner_prices, stripe_plan, billing_risk, billing_acceptance
    from .couples import incompatible, is_adult, member_info
    from .free_plan import complimentary_access, has_member_access, month_boundary, paid_cycle
except ImportError:
    import membership_plans, stripe_membership, stripe_partner_prices, stripe_plan, billing_risk, billing_acceptance
    from couples import incompatible, is_adult, member_info
    from free_plan import complimentary_access, has_member_access, month_boundary, paid_cycle


MONTHLY = "partner_monthly"
ANNUAL = "couple_annual"
LABELS = {**{key: f"${p['price_cents'] // 100}/month" for key, p in membership_plans.PLANS.items()},
          "standard": "$12/month", MONTHLY: "$10/month", ANNUAL: "$80 for one year"}


def utc_iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def price_for(offer):
    if offer in ("standard", "plus_100"):
        return stripe_plan.catalog_price()
    if offer in membership_plans.PLANS:
        return stripe_membership.catalog()[offer]
    return stripe_partner_prices.catalog_price(offer)


def first_paid_invoice(subscription):
    sub = stripe.Subscription.retrieve(subscription, expand=["latest_invoice"])
    latest = sub.get("latest_invoice")
    if isinstance(latest, str):
        latest = stripe.Invoice.retrieve(latest)
    if latest and latest.get("billing_reason") == "subscription_create":
        return latest
    # Checkout can be delivered after a renewal. Its $10 qualification belongs solely to the first invoice.
    return next((inv for inv in stripe.Invoice.list(subscription=subscription, limit=100).auto_paging_iter()
                 if inv.get("billing_reason") == "subscription_create" and inv.get("status") == "paid"), None)


def recover_period(member):
    offer = member["plan_offer"]
    sub_id = member["stripe_subscription_id"]
    sub = stripe.Subscription.retrieve(sub_id, expand=["latest_invoice"])
    items = (sub.get("items") or {}).get("data") or []
    canonical = price_for(offer)
    if (not canonical or sub.get("status") not in ("active", "past_due") or len(items) != 1 or items[0].get("quantity") != 1
            or stripe_plan.resource_id(items[0].get("price")) != canonical.id
            or (sub.get("metadata") or {}).get("user_id") != member["user_id"]):
        return None
    invoice = sub.get("latest_invoice")
    if isinstance(invoice, str):
        invoice = stripe.Invoice.retrieve(invoice)
    period = stripe_plan.paid_period(invoice or {}, sub_id, canonical.id)
    if not period or not fully_paid_offer(invoice, canonical.id, 8000 if offer == ANNUAL else 1000):
        return None
    if offer == ANNUAL:
        agreement = member.get("annual_agreement") or {}
        if (agreement.get("subscription") != sub_id or agreement.get("annual_price") != canonical.id
                or period != (agreement.get("starts"), agreement.get("ends"))
                or agreement.get("status") not in ("pending", "scheduled")):
            return None
    return canonical.id, period, stripe_plan.resource_id(sub.get("customer"))


def lookup(offer):
    if offer in membership_plans.PLANS:
        return membership_plans.PLANS[offer]["lookup_key"]
    return stripe_partner_prices.LOOKUP_KEYS.get(offer, stripe_plan.LOOKUP_KEY)


def fully_paid_offer(invoice, price_id, cents):
    """Invoice revenue excludes inclusive tax. Prove the quoted plan line and actual settlement separately."""
    total = invoice.get("total")
    if (invoice.get("status") != "paid" or not isinstance(invoice.get("currency"), str)
            or type(total) is not int or total <= 0
            or type(invoice.get("amount_paid")) is not int or invoice["amount_paid"] < total
            or any(item.get("amount") for item in invoice.get("total_discount_amounts") or [])):
        return False
    lines = invoice.get("lines") or {}
    rows = lines.get("data") or []
    if lines.get("has_more"):
        rows = stripe.Invoice.list_lines(invoice["id"], limit=100).auto_paging_iter()
    matching = []
    for line in rows:
        details = (line.get("parent") or {}).get("subscription_item_details") or {}
        priced = (line.get("pricing") or {}).get("price_details") or {}
        if (stripe_plan.resource_id(priced.get("price") or line.get("price")) == price_id
                and not (line.get("proration") or details.get("proration"))):
            matching.append(line)
    if len(matching) != 1:
        return False
    line = matching[0]
    if (line.get("quantity", 1) != 1 or line.get("amount", 0) <= 0
            or any(x.get("amount") for x in (line.get("pretax_credit_amounts") or line.get("discount_amounts") or []))):
        return False
    # A localized invoice still references the validated USD-denominated catalog price.
    return invoice.get("currency") != "usd" or (total >= cents and line.get("amount") == cents)


async def pair_context(db, member):
    if not is_adult(member) or not member.get("env"):
        return None
    uid = member["user_id"]
    pair = await db.couples_pairs.find_one({"env": member["env"], "state": "active", "members": uid, "benefit_members": uid},
        {"inviter": 1, "invitee": 1, "sponsor": 1, "annual_opens_at": 1, "annual_expires_at": 1, "qualifying_subscription": 1})
    if not pair:
        return None
    other_id = pair["inviter"] if uid == pair["invitee"] else pair["invitee"]
    other = await member_info(db, other_id, member["env"])
    if not is_adult(other) or incompatible(member, other):
        return None
    if not has_member_access(member if pair["sponsor"] == uid else other):
        return None
    return pair


async def details(db, member):
    uid = member["user_id"]
    pair = await pair_context(db, member)
    complimentary = complimentary_access(member)
    existing = member.get("tier") == "paid" and member.get("stripe_subscription_id")
    agreement = member.get("annual_agreement") or {}
    cutoff = agreement.get("starts") if agreement.get("status") == "cancelled" else agreement.get("ends")
    if (agreement.get("subscription") != member.get("stripe_subscription_id") or not existing
            or (cutoff is not None and cutoff <= datetime.now(timezone.utc).timestamp())):
        agreement = {}
    expires = pair and pair.get("annual_expires_at")
    moment = datetime.now(timezone.utc).isoformat()
    annual = bool(not complimentary and pair and expires and moment < expires and paid_cycle(member) and member.get("subscription_status") in ("active", None)
                  and member.get("plan_offer") != ANNUAL and not agreement)
    starts = agreement.get("starts") or (member.get("subscription_period_end") if annual else None)
    ends = agreement.get("ends") or (month_boundary(starts, 12) if starts else None)
    return pair, {"monthly_eligible": bool(not complimentary and pair and pair["invitee"] == uid and not existing and not pair.get("qualifying_subscription")),
        "monthly_available": False, "annual_eligible": annual, "annual_available": False, "annual_expires_at": expires,
        "annual_accepted": agreement.get("status") == "scheduled", "annual_pending": agreement.get("status") == "pending",
        "annual_cancelled": agreement.get("status") == "cancelled",
        "annual_cancel_available": bool(agreement.get("status") == "scheduled" and utc_iso(agreement["starts"]) > moment),
        "annual_starts_at": utc_iso(starts) if starts else None, "annual_ends_at": utc_iso(ends) if ends else None,
        "monthly_price_cents": 1000, "annual_price_cents": 8000, "annual_renews": False}


async def inbox_notice(db, member):
    if complimentary_access(member) or not paid_cycle(member):
        return None
    pair, offer = await details(db, member)
    if not offer["annual_eligible"]:
        return None
    return await db.notifications.find_one(
        {"user_id": member["user_id"], "type": "billing", "key": "partner-year:" + pair["_id"]},
        {"_id": 0, "id": 1, "type": 1, "title": 1, "body": 1, "link": 1, "created_at": 1, "read": 1},
    )


async def unlock(db, tx, sub_id, paid_at, notify):
    # Receipt timestamps are authoritative even when Stripe delivers a webhook late.
    end = (datetime.fromisoformat(paid_at) + timedelta(hours=24)).isoformat()
    query = {"_id": tx["pair_id"], "env": tx["env"], "state": "active", "invitee": tx["user_id"], "benefit_members": tx["user_id"]}
    await db.couples_pairs.update_one({**query, "qualifying_subscription": None},
        {"$set": {"qualifying_subscription": sub_id, "annual_opens_at": paid_at, "annual_expires_at": end}})
    pair = await db.couples_pairs.find_one({**query, "qualifying_subscription": sub_id}, {"members": 1, "annual_expires_at": 1})
    if not pair or pair["annual_expires_at"] <= datetime.now(timezone.utc).isoformat():
        return
    for uid in pair["members"]:
        recipient = await member_info(db, uid, tx["env"])
        if recipient and complimentary_access(recipient):
            continue
        await notify(uid, "billing", "Your one-year membership offer is ready",
            "You have 24 hours from the partner payment to choose one year for $80. Open Billing to see your deadline and terms.",
            "/app/settings/upgrade", "partner-year:" + tx["pair_id"])


def verify_schedule(schedule, agreement):
    phases = schedule.get("phases") or []
    return bool(schedule.get("end_behavior") == "cancel" and len(phases) == 2
        and phases[0].get("end_date") == agreement["starts"] and len(phases[0].get("items") or []) == 1
        and stripe_plan.resource_id(phases[0]["items"][0].get("price")) == agreement["monthly_price"]
        and phases[0]["items"][0].get("quantity") == 1
        and phases[-1].get("start_date") == agreement["starts"] and phases[-1].get("end_date") == agreement["ends"]
        and phases[-1].get("billing_cycle_anchor") == "phase_start" and phases[-1].get("proration_behavior") == "none"
        and len(phases[-1].get("items") or []) == 1
        and stripe_plan.resource_id(phases[-1]["items"][0].get("price")) == agreement["annual_price"]
        and phases[-1]["items"][0].get("quantity") == 1)


def current_subscription(agreement, member):
    sub = stripe.Subscription.retrieve(agreement["subscription"])
    items = (sub.get("items") or {}).get("data") or []
    if (sub.get("id") != agreement["subscription"] or sub.get("status") != "active" or sub.get("cancel_at_period_end")
            or sub.get("cancel_at") or sub.get("pause_collection") or sub.get("pending_update")
            or sub.get("collection_method") != "charge_automatically" or sub.get("discounts")
            or stripe_plan.resource_id(sub.get("customer")) != member.get("stripe_customer_id")
            or (sub.get("metadata") or {}).get("user_id") not in (None, member["user_id"])
            or len(items) != 1 or items[0].get("quantity") != 1
            or stripe_plan.resource_id(items[0].get("price")) != agreement["monthly_price"]
            or (items[0].get("current_period_end") or sub.get("current_period_end")) != agreement["starts"]):
        raise HTTPException(409, "Your current subscription changed. Review Billing before choosing this offer.")
    return sub


async def admit_schedule_write(db, uid, agreement, stage):
    billing_acceptance.forbid_ordinary_purchase()
    current = await db.users.find_one({"user_id": uid, "closing": {"$ne": True},
        "stripe_subscription_id": agreement["subscription"], "annual_agreement.subscription": agreement["subscription"],
        "annual_agreement.status": {"$in": ["pending", "scheduled"]}})
    if not current:
        raise HTTPException(409, "Your membership changed. Review Billing before scheduling.")
    query = billing_risk.guard_query({"user_id": uid, "closing": {"$ne": True},
        "stripe_subscription_id": agreement["subscription"], "annual_agreement.subscription": agreement["subscription"],
        "annual_agreement.status": {"$in": ["pending", "scheduled"]}}, current, "new_purchase")
    saved = await db.users.update_one(query, {"$set": {"annual_agreement.last_write_admission": {
        "stage": stage, "at": datetime.now(timezone.utc).isoformat()}}})
    if not saved.matched_count:
        raise HTTPException(409, "Billing review changed before scheduling. Refresh its saved status.")


async def schedule_year(db, member, agreement):
    uid = member["user_id"]
    sub = await asyncio.to_thread(current_subscription, agreement, member)
    key = "imladri-fixed-year:" + agreement["pair"] + ":" + agreement["subscription"]
    schedule_id = agreement.get("schedule")
    attached = stripe_plan.resource_id(sub.get("schedule"))
    if schedule_id:
        if attached != schedule_id:
            raise HTTPException(409, "Your subscription schedule changed. Contact support to review this offer.")
        schedule = await asyncio.to_thread(stripe.SubscriptionSchedule.retrieve, schedule_id)
    else:
        # from_subscription cannot include phases. Replaying this exact call recovers a lost first response.
        await admit_schedule_write(db, uid, agreement, "schedule_create")
        schedule = await asyncio.to_thread(stripe.SubscriptionSchedule.create, from_subscription=sub["id"], idempotency_key=key + ":create")
        schedule_id = schedule["id"]
        await db.users.update_one({"user_id": uid, "annual_agreement.subscription": sub["id"], "annual_agreement.status": "pending"},
            {"$set": {"annual_agreement.schedule": schedule_id}})
    if not verify_schedule(schedule, agreement):
        phases = schedule.get("phases") or []
        if (schedule.get("status") != "active" or stripe_plan.resource_id(schedule.get("subscription")) != sub["id"]
                or schedule.get("end_behavior") == "cancel" or len(phases) != 1
                or not phases[0].get("start_date") or phases[0].get("discounts")):
            raise HTTPException(409, "Your existing subscription schedule requires review.")
        current_phase = phases[0]
        # These optional phase overrides are writable. Schedule defaults retain the subscription's tax
        # and payment configuration when a phase has no overrides of its own.
        inherited = {k: current_phase[k] for k in ("collection_method", "default_payment_method",
            "default_tax_rates", "description", "metadata") if current_phase.get(k)}
        if current_phase.get("automatic_tax") and current_phase["automatic_tax"].get("enabled"):
            inherited["automatic_tax"] = {"enabled": True}
        phase1 = {**inherited, "start_date": current_phase["start_date"], "end_date": agreement["starts"],
                  "items": [{"price": agreement["monthly_price"], "quantity": 1}], "proration_behavior": "none"}
        phase2 = {**inherited, "start_date": agreement["starts"], "end_date": agreement["ends"], "billing_cycle_anchor": "phase_start",
                  "items": [{"price": agreement["annual_price"], "quantity": 1}], "proration_behavior": "none"}
        await admit_schedule_write(db, uid, agreement, "schedule_phases")
        schedule = await asyncio.to_thread(stripe.SubscriptionSchedule.modify, schedule_id, phases=[phase1, phase2],
            end_behavior="cancel", proration_behavior="none", idempotency_key=key + ":phases")
    if not verify_schedule(schedule, agreement):
        raise HTTPException(503, "Stripe has not confirmed the one-year terms yet. Retry to check your agreement.")
    updated = await db.users.update_one({"user_id": uid, "stripe_subscription_id": sub["id"], "annual_agreement.subscription": sub["id"],
        "annual_agreement.status": {"$in": ["pending", "scheduled"]}},
        {"$set": {"annual_agreement.status": "scheduled", "annual_agreement.schedule": schedule_id}})
    if not updated.matched_count:
        raise HTTPException(409, "Your membership changed. Refresh Billing to check the current schedule.")


async def accept(db, member, *, expected_starts_at=None, purchase_acknowledgment=None):
    billing_acceptance.forbid_ordinary_purchase()
    billing_risk.require_action(member, "new_purchase")
    if complimentary_access(member):
        raise HTTPException(409, "Your complimentary unlimited access does not require a paid partner offer.")
    pair, offer = await details(db, member)
    agreement = member.get("annual_agreement") or {}
    if agreement.get("subscription") != member.get("stripe_subscription_id"):
        agreement = {}
    if not agreement:
        if not offer["annual_eligible"]:
            raise HTTPException(409, "This one-year offer is unavailable or has expired.")
        if expected_starts_at is not None:
            try:
                reviewed = datetime.fromisoformat(expected_starts_at)
            except ValueError:
                reviewed = None
            if not reviewed or not reviewed.tzinfo or reviewed.timestamp() != member.get("subscription_period_end"):
                raise HTTPException(409, "Your charge date changed. Refresh Billing to review the updated terms.")
        price = await asyncio.to_thread(price_for, ANNUAL)
        if not price or not member.get("stripe_price_id") or not member.get("stripe_customer_id"):
            raise HTTPException(503, "The one-year offer is temporarily unavailable.")
        moment = datetime.now(timezone.utc).isoformat()
        if moment >= pair["annual_expires_at"]:
            raise HTTPException(409, "This offer has expired.")
        start = member["subscription_period_end"]
        proposal = {"status": "pending", "pair": pair["_id"], "accepted_at": moment, "subscription": member["stripe_subscription_id"],
                    "starts": start, "ends": month_boundary(start, 12), "annual_price": price.id, "monthly_price": member["stripe_price_id"]}
        if purchase_acknowledgment:
            proposal["purchase_policy"] = purchase_acknowledgment
        sub = await asyncio.to_thread(current_subscription, proposal, member)
        if sub.get("schedule"):
            # A concurrently accepted request may already have attached our own schedule.
            existing = await db.users.find_one({"user_id": member["user_id"], "annual_agreement.subscription": proposal["subscription"]},
                                               {"annual_agreement": 1})
            if not existing:
                raise HTTPException(409, "There is already a change scheduled on this subscription.")
        # The pair is the CAS boundary shared with leave/clear and the offer expiry check.
        side = "invitee" if member["user_id"] == pair["invitee"] else "inviter"
        authorized = await db.couples_pairs.find_one_and_update({"_id": pair["_id"], "state": "active",
            "benefit_members": member["user_id"], "annual_expires_at": {"$gt": datetime.now(timezone.utc).isoformat()},
            "agreement_" + side: None}, {"$set": {"agreement_" + side: proposal}}, return_document=ReturnDocument.AFTER)
        if not authorized:
            authorized = await db.couples_pairs.find_one({"_id": pair["_id"], "state": "active", "agreement_" + side + ".subscription": proposal["subscription"]})
        if not authorized:
            raise HTTPException(409, "This one-year offer is no longer available.")
        proposal = authorized["agreement_" + side]
        owner_query = billing_risk.guard_query({"user_id": member["user_id"], "closing": {"$ne": True},
            "stripe_subscription_id": proposal["subscription"], "subscription_period_end": start, "annual_agreement": None}, member, "new_purchase")
        owner = await db.users.find_one_and_update(owner_query,
            {"$set": {"annual_agreement": proposal}}, return_document=ReturnDocument.AFTER)
        if not owner:
            owner = await db.users.find_one({"user_id": member["user_id"], "closing": {"$ne": True}, "annual_agreement.subscription": proposal["subscription"]})
        if not owner:
            raise HTTPException(409, "Your subscription changed. Refresh Billing.")
        agreement = owner["annual_agreement"]
    if agreement["status"] == "cancelled":
        raise HTTPException(409, "This agreement was cancelled.")
    if agreement["status"] != "scheduled":
        await schedule_year(db, member, agreement)
    return {"accepted": True, "starts_at": utc_iso(agreement["starts"]), "ends_at": utc_iso(agreement["ends"]), "renews": False}


async def cancel_before_charge(db, member):
    agreement = member.get("annual_agreement") or {}
    if (agreement.get("subscription") != member.get("stripe_subscription_id")
            or agreement.get("status") not in ("scheduled", "cancelled")):
        raise HTTPException(409, "There is no scheduled year on this account.")
    end = agreement["starts"]
    if agreement["status"] == "cancelled":
        return {"cancelled": True, "ends_at": utc_iso(end)}
    if end <= datetime.now(timezone.utc).timestamp():
        raise HTTPException(409, "The scheduled year has already started. Check your membership in Billing.")
    sid = agreement["schedule"]
    schedule = await asyncio.to_thread(stripe.SubscriptionSchedule.retrieve, sid)
    if schedule.get("status") != "active" or stripe_plan.resource_id(schedule.get("subscription")) != agreement["subscription"]:
        raise HTTPException(409, "The subscription schedule changed. Contact support to check its status.")
    phases = schedule.get("phases") or []
    if len(phases) == 2 and verify_schedule(schedule, agreement):
        phase = phases[0]
        keep = {k: phase[k] for k in ("start_date", "end_date", "proration_behavior", "collection_method",
            "default_tax_rates", "description", "metadata") if phase.get(k)}
        if phase.get("default_payment_method"):
            keep["default_payment_method"] = stripe_plan.resource_id(phase["default_payment_method"])
        if (phase.get("automatic_tax") or {}).get("enabled"):
            keep["automatic_tax"] = {"enabled": True}
        keep["items"] = [{"price": agreement["monthly_price"], "quantity": 1}]
        schedule = await asyncio.to_thread(stripe.SubscriptionSchedule.modify, sid, phases=[keep], end_behavior="cancel",
            proration_behavior="none", idempotency_key="imladri-cancel-partner-year:" + sid)
        phases = schedule.get("phases") or []
    if (len(phases) != 1 or phases[0].get("end_date") != end or schedule.get("end_behavior") != "cancel"
            or len(phases[0].get("items") or []) != 1
            or stripe_plan.resource_id(phases[0]["items"][0].get("price")) != agreement["monthly_price"]):
        raise HTTPException(503, "Stripe has not confirmed cancellation yet. Retry to check the schedule.")
    await db.users.update_one({"user_id": member["user_id"], "stripe_subscription_id": agreement["subscription"],
        "annual_agreement.schedule": sid}, {"$set": {"annual_agreement.status": "cancelled"}})
    return {"cancelled": True, "ends_at": utc_iso(end)}
