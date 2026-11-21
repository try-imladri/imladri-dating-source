"""Expire an unused invitation checkout when the couple or its sponsor is no longer eligible."""

import asyncio
from datetime import datetime, timezone

import stripe

if __package__:
    from . import stripe_partner_prices
else:
    import stripe_partner_prices


MONTHLY = "partner_monthly"
PENDING = {"kind": "purchase", "plan_offer": MONTHLY, "payment_status": "pending"}


def expire_at_stripe(row):
    session = stripe.checkout.Session.retrieve(row["session_id"])
    metadata = session.get("metadata") or {}
    if (metadata.get("user_id") != row["user_id"] or metadata.get("lookup_key") != stripe_partner_prices.LOOKUP_KEYS[MONTHLY]
            or session.get("mode") != "subscription" or session.get("id") != row["session_id"]):
        return None
    if session.get("status") == "open":
        try:
            session = stripe.checkout.Session.expire(session["id"])
        except stripe.InvalidRequestError:
            # A payment, expiry webhook or a concurrent close may have won.
            session = stripe.checkout.Session.retrieve(row["session_id"])
    return session.get("status")


async def expire_row(db, row):
    if not stripe.api_key:
        return False
    try:
        status = await asyncio.to_thread(expire_at_stripe, row)
    except stripe.StripeError:
        return False
    # A complete session belongs to the payer, including asynchronous payments already submitted.
    if status == "complete":
        return True
    if status != "expired":
        return False
    reference = {"_id": row["_id"], "user_id": row["user_id"], "payment_status": {"$in": ["pending", "expired"]}}
    await db.payment_transactions.update_one(reference, {"$set": {
        "status": "expired", "payment_status": "expired", "updated_at": datetime.now(timezone.utc).isoformat(),
    }})
    if row.get("checkout_attempt_id"):
        await db.users.update_one({"user_id": row["user_id"], "checkout_attempt.id": row["checkout_attempt_id"]},
                                  {"$unset": {"checkout_attempt": ""}})
    return True


async def expire_pair(db, pair_id, env):
    rows = db.payment_transactions.find({**PENDING, "pair_id": pair_id, "env": env})
    async for row in rows:
        await expire_row(db, row)


async def eligible(db, row, member_info, pair_context):
    member = await member_info(db, row["user_id"], row["env"])
    if not member or member.get("closing"):
        return False
    # member_info also accepts legacy accounts with no stored environment. The pair itself
    # is checked against the environment recorded on this purchase.
    pair = await pair_context(db, {**member, "env": row["env"]})
    return bool(pair and pair["_id"] == row["pair_id"] and pair["invitee"] == row["user_id"])


async def reconcile(db, env, member_info, pair_context):
    # Rotate through pending discounted sessions. A crash during pair deletion or an unavailable
    # provider leaves the pending purchase durable for the next operations check.
    query = {**PENDING, "env": env}
    rows = await db.payment_transactions.find(query).sort("partner_checked_at", 1).limit(100).to_list(100)
    healthy = True
    for row in rows:
        if not await eligible(db, row, member_info, pair_context):
            if not await expire_row(db, row):
                healthy = False
        await db.payment_transactions.update_one({"_id": row["_id"], "payment_status": "pending"},
            {"$set": {"partner_checked_at": datetime.now(timezone.utc).isoformat()}})
    return healthy
