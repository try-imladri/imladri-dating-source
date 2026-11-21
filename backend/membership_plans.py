"""Personal's public catalog and the durable balance from one-time message purchases."""
import hashlib
import json
from pathlib import Path


_PUBLIC = json.loads((Path(__file__).parent / "seed" / "public_plans.json").read_text(encoding="utf-8"))
PLANS = {plan["id"]: {**plan, "lookup_key": f"imladri_{plan['id']}_monthly_usd{plan['price_cents'] // 100}"}
         for plan in _PUBLIC}
TOPUP = {"id": "topup_100", "name": "100 extra messages", "messages": 100, "price_cents": 1200,
         "lookup_key": "imladri_topup_100_usd12"}
BASE_PLAN = PLANS["plus_100"]


def public_plans():
    fields = ("id", "name", "messages", "price_cents")
    return [{key: plan[key] for key in fields} for plan in PLANS.values()]


def member_plan(member):
    offer = member.get("plan_offer")
    if offer in ("partner_monthly", "couple_annual"):
        return BASE_PLAN
    return PLANS.get(member.get("membership_plan_id")) or PLANS.get(offer) or BASE_PLAN


async def apply_topup(db, uid, purchase_id, credits):
    """Apply the net grant after verified payment/refunds, once per Checkout session.

    Refund notifications and Checkout reconciliation can arrive concurrently or in
    reverse order. A verified grant can only decrease for a given paid purchase.
    """
    if not purchase_id or not isinstance(purchase_id, str) or type(credits) is not int or not 0 <= credits <= TOPUP["messages"]:
        raise ValueError("Expected a purchase identifier and a valid net credit count.")
    key = hashlib.sha256(purchase_id.encode()).hexdigest()
    field = "topup_grants." + key
    previous = "$" + field
    net = {"$min": [credits, {"$ifNull": [previous, credits]}]}
    delta = {"$subtract": [net, {"$ifNull": [previous, 0]}]}
    # A refund after credits have already been spent can leave a negative balance.
    # Preserve it internally to avoid regranting spent credits on the next purchase.
    result = await db.users.update_one({"user_id": uid, "closing": {"$ne": True}}, [{"$set": {
        field: net, "topup_balance": {"$add": [{"$ifNull": ["$topup_balance", 0]}, delta]},
    }}])
    return bool(result.matched_count)
