"""Fulfill paid one-time message packs and their cumulative refunds."""
import asyncio

import stripe
from fastapi import HTTPException

if __package__:
    from . import billing_adjustments, membership_plans, stripe_plan
else:
    import billing_adjustments, membership_plans, stripe_plan


def verified_pack(tx, session):
    spec = membership_plans.TOPUP
    meta = session.get("metadata") or {}
    if (session.get("id") != tx["session_id"] or session.get("mode") != "payment" or session.get("payment_status") != "paid"
            or meta.get("user_id") != tx["user_id"] or meta.get("lookup_key") != spec["lookup_key"]
            or meta.get("topup_attempt") != tx["checkout_attempt_id"] or session.get("currency") != "usd"
            or session.get("amount_subtotal") != spec["price_cents"] or not session.get("amount_total")
            or stripe_plan.resource_id(session.get("customer")) != tx["stripe_customer_id"]):
        raise ValueError("Unexpected refill checkout.")
    lines = stripe.checkout.Session.list_line_items(session["id"], limit=2)
    if len(lines.data) != 1 or lines.data[0].get("quantity") != 1 or (
            stripe_plan.resource_id(lines.data[0].get("price")) != tx["stripe_price_id"]):
        raise ValueError("Unexpected refill price.")
    payment = stripe.PaymentIntent.retrieve(stripe_plan.resource_id(session.get("payment_intent")), expand=["latest_charge"])
    charge = payment.get("latest_charge") or {}
    if (not hasattr(charge, "get") or not charge.get("paid") or charge.get("currency") != "usd"
            or charge.get("amount") != session["amount_total"] or payment.get("amount_received") != charge["amount"]):
        raise ValueError("Awaiting the confirmed refill charge.")
    return payment["id"], charge


def net_credits(charge):
    amount, refunded = charge.get("amount"), charge.get("amount_refunded", 0)
    if type(amount) is not int or amount <= 0 or type(refunded) is not int or not 0 <= refunded <= amount:
        raise ValueError("Unexpected refund amount.")
    return membership_plans.TOPUP["messages"] * (amount - refunded) // amount


async def settle(db, tx, session, current_time, *, env="preview"):
    try:
        payment_id, charge = await asyncio.to_thread(verified_pack, tx, session)
        credits = net_credits(charge)
    except (ValueError, stripe.StripeError):
        raise HTTPException(503, "Awaiting the verified payment details for this refill.") from None
    await billing_adjustments.reconcile(db, tx, env=env, charge_id=charge["id"], payment_id=payment_id,
                                       source_event="verified_refill_fulfillment")
    await db.payment_transactions.update_one({"_id": tx["_id"]} if "_id" in tx else {"session_id": tx["session_id"]}, {
        "$set": {"status": "completed", "payment_status": "paid", "stripe_payment_intent_id": payment_id,
                 "stripe_charge_id": charge["id"], "updated_at": current_time,
                 "payment_verified": True, "credit_delivery_status": "fulfilled"},
        "$min": {"fulfilled_at": current_time}})
    await db.users.update_one({"user_id": tx["user_id"], "topup_checkout_attempt.id": tx["checkout_attempt_id"]},
                              {"$unset": {"topup_checkout_attempt": ""}})


async def reverse(db, obj, *, lost=False, env="preview"):
    # Compatibility entry point; the current Charge and full Dispute inventory,
    # rather than an old webhook body, decide the verified net grant.
    value = await billing_adjustments.handle_event(db, obj, env=env,
        source_event="charge.dispute.closed" if lost else "charge.refunded")
    return value if value.get("status") != "unlinked_payment_not_applied" else None
