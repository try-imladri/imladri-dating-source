"""Card-first ten-message introduction; no calendar-triggered conversion.

The Managed Payments discount strategy remains off until an operator records a
positive provider proof. Importing this module never contacts Stripe. No card
number, CVC, fingerprint plaintext or research permission is stored here.
"""
import asyncio
import calendar
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import logging
import os
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
import stripe

if __package__:
    from . import intro_access, membership_plans, partner_offers, purchase_policy, stripe_plan, ops, billing_risk, billing_acceptance
else:
    import intro_access, membership_plans, partner_offers, purchase_policy, stripe_plan, ops, billing_risk, billing_acceptance

VERSION = "card-first-intro-v1"
STRATEGY = "managed-classic-discount-no-date-v1"
API_VERSION = "2026-02-25.clover"
LIMIT = 10
INELIGIBILITY_VERSION = "intro-ineligibility-v1"
STATES = {"checkout_pending", "active", "billing_pending", "requires_action", "payment_failed", "cancelled", "converted", "review_required"}
log = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def configured():
    # This is an operator admission gate, not evidence that this code was tested
    # against the provider. The referenced proof is reviewed before deployment.
    proof = os.getenv("INTRO_CARD_FIRST_PROVIDER_PROOF_SHA256", "")
    return (ops.billing_enabled() and os.getenv("INTRO_CARD_FIRST_VERIFIED_STRATEGY") == STRATEGY
            and len(proof) == 64 and all(c in "0123456789abcdef" for c in proof)
            and bool(os.getenv("INTRO_CARD_FIRST_COUPON_ID", ""))
            and stripe_plan.checkout_mode() == "managed" and bool(stripe.api_key))


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_member: str = Field(min_length=1, max_length=200)


class Setup(Input):
    plan_id: str = Field(max_length=40)
    expected_price_cents: StrictInt = Field(gt=0)
    expected_messages: StrictInt = Field(gt=0)
    request_id: str = Field(max_length=36)
    authorization_version: str = Field(max_length=50)
    authorization_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    quote_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    authorize_after_messages: StrictBool
    policy_accepted: StrictBool
    origin_url: str = Field(min_length=8, max_length=300)
    challenge_token: str | None = Field(default=None, max_length=2048)
    device_token: str | None = Field(default=None, max_length=64)

    @field_validator("request_id")
    @classmethod
    def canonical_uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("Use a canonical request UUID.")
        return value


class Confirm(Input):
    session_id: str = Field(min_length=4, max_length=200)


class Cancel(Input):
    request_id: str = Field(max_length=36)
    revision: StrictInt = Field(ge=0, lt=2**53)

    _uuid = field_validator("request_id")(Setup.canonical_uuid.__func__)


def billing(member):
    row = member.get("intro_billing")
    return row if (isinstance(row, dict) and row.get("version") == VERSION and isinstance(row.get("state"), str)
        and row["state"] in STATES) else None


def active(member):
    row = billing(member)
    return bool(row and authorization_valid(row) and row["state"] == "active" and row.get("card_verified") is True
                and row.get("authorization", {}).get("version") == VERSION
                and row.get("authorization", {}).get("authorize_after_messages") is True
                and row.get("session_id") and row.get("subscription_id") and row.get("customer_id")
                and row.get("grant_id") == (intro_access.entitlement(member) or {}).get("id"))


def status_fields(member):
    row = billing(member)
    legacy = bool(intro_access.entitlement(member) and not row)
    selected = deepcopy((row or {}).get("selected_plan"))
    state = row["state"] if row else "not_started" if configured() else "unavailable"
    if legacy:
        state = "not_started" if intro_access.totals(member)[1] < LIMIT and configured() else "unavailable"
    info = {"state": state, "revision": (row or {}).get("revision", 0),
            "agreement_id": (row or {}).get("id"), "can_cancel": can_cancel(row),
            "card_verified": bool(row and row.get("card_verified") is True), "legacy_intro": legacy,
            "cancellation_requested": bool(row and row.get("cancellation_requested"))}
    action = (row or {}).get("action_url")
    if action and trusted_action_url(action):
        info["action_url"] = action
    return {"authorization_version": VERSION, "requires_payment_method": True, "automatic_charge": True, "billing_required": required(member),
            "selected_plan": selected, "billing": info}


def trusted_action_url(value):
    try:
        p = urlsplit(value)
        host = (p.hostname or "").lower()
        return p.scheme == "https" and not p.username and not p.password and (host in ("stripe.com", "link.com")
            or host.endswith(".stripe.com") or host.endswith(".link.com"))
    except (TypeError, ValueError):
        return False


def can_cancel(row):
    return bool(row and (not row.get("cancellation_requested") or row.get("cancel_stage") == "needs_subscription_cancel")
        and row["state"] not in ("cancelled", "converted")
        and ((row.get("conversion") or {}).get("stage") in (None, "due"))
        and (row.get("session_id") or row.get("subscription_id")))


def agreement_text(plan, remaining):
    amount = f"${plan['price_cents'] / 100:g}"
    return (f"I authorize Imladri and Stripe Managed Payments to save my card and automatically start {plan['name']} "
            f"at {amount} USD/month plus applicable taxes after my remaining {remaining} introductory AI messages "
            f"are successfully completed ({LIMIT} total across Individual and Couples). I am charged $0 for this introduction; "
            "it never converts merely because time passes. I can cancel before conversion starts; cancellation ends remaining "
            "introductory AI access. The paid monthly allowance begins only after confirmed payment and renews monthly until canceled. "
            "I agree to the linked Terms and Billing & refunds policy, including applicable mandatory rights.")


def eligible(member):
    if (member.get("role") != "member" or member.get("closing") or member.get("replies_paused")
            or member.get("intro_access_paused") or intro_access.converted(member) or member.get("complimentary_entitlement")
            or member.get("paid_reply_cycles") or member.get("topup_grants") or member.get("intro_active_request")
            or permanently_ineligible(member)):
        return False
    row = billing(member)
    if row:
        return row["state"] == "checkout_pending"
    legacy = intro_access.entitlement(member)
    if legacy:
        return intro_access.totals(member)[1] < LIMIT
    return intro_access.eligibility(member)


def required(member):
    """An unavailable card-first strategy must not expose immediate-charge checkout."""
    row = billing(member)
    if row:
        return row["state"] not in ("converted", "cancelled")
    # Temporary per-account holds prevent setup; they do not establish a
    # permanent exclusion that authorizes immediate full-price checkout.
    snapshot = {key: value for key, value in member.items()
                if key not in ("replies_paused", "intro_access_paused", "intro_active_request")}
    return eligible(snapshot)


def permanently_ineligible(member):
    value = member.get("intro_ineligibility")
    if (not isinstance(value, dict) or set(value) != {"version", "reason", "verified_at", "cohort_start"}
            or value.get("version") != INELIGIBILITY_VERSION or not isinstance(value.get("reason"), str)
            or value["reason"] not in intro_access.PERMANENT_DENIAL_REASONS):
        return False
    try:
        return all(datetime.fromisoformat(value[key]).tzinfo is not None for key in ("verified_at", "cohort_start"))
    except (TypeError, ValueError, KeyError):
        return False


async def retain_permanent_denial(db, current, reason):
    if reason not in intro_access.PERMANENT_DENIAL_REASONS or billing(current) or intro_access.access(current):
        raise HTTPException(409, "Your introductory status changed. Refresh Plans.")
    fields = ("tier", "stripe_subscription_id", "subscription_period_start", "subscription_period_end", "subscription_status",
        "intro_entitlement", "intro_reply_totals", "complimentary_entitlement", "paid_reply_cycles", "topup_grants", "intro_converted_at",
        "replies_paused", "intro_access_paused", "intro_ineligibility")
    query = {"user_id": current["user_id"], "role": "member", "closing": {"$ne": True},
        "intro_billing": {"$exists": False}, "intro_active_request": {"$exists": False},
        "checkout_attempt": {"$in": [None]}, "topup_checkout_attempt": {"$in": [None]},
        "$expr": {"$and": [{"$eq": ["$" + field, {"$literal": current[field]}]} if field in current
            else {"$eq": [{"$type": "$" + field}, "missing"]} for field in fields]}}
    receipt = {"version": INELIGIBILITY_VERSION, "reason": reason, "verified_at": now().isoformat(),
        "cohort_start": intro_access.eligible_since().isoformat()}
    saved = await db.users.update_one(query, {"$set": {"intro_ineligibility": receipt}})
    if not saved.matched_count:
        raise HTTPException(409, "Your account changed during eligibility verification. Refresh Plans.")


async def quote(db, member, plan_id):
    if plan_id not in membership_plans.PLANS:
        raise HTTPException(422, "Choose one of the three monthly plans.")
    plan = membership_plans.PLANS[plan_id]
    offer, pair_id, cents = ("standard" if plan_id == "plus_100" else plan_id), None, plan["price_cents"]
    if plan_id == "plus_100":
        pair, details = await partner_offers.details(db, member)
        if details["monthly_eligible"]:
            offer, pair_id, cents = partner_offers.MONTHLY, pair["_id"], 1000
    return {"id": plan_id, "name": plan["name"], "messages": plan["messages"], "price_cents": cents,
            "currency": "usd", "offer": offer, "pair_id": pair_id}


async def quotes(db, member):
    if not billing_acceptance.intro_allowed(member):
        return {}
    remaining = max(0, LIMIT - intro_access.totals(member)[1]) if intro_access.entitlement(member) else LIMIT
    result = {}
    for identifier in membership_plans.PLANS:
        if billing_acceptance.requested() and identifier != "plus_100":
            continue
        selected = await quote(db, member, identifier)
        if not billing_acceptance.intro_allowed(member, selected=selected):
            continue
        text = agreement_text(selected, remaining)
        item = {**selected, "remaining_intro_messages": remaining, "authorization_version": VERSION,
            "authorization_text": text, "authorization_text_sha256": hashlib.sha256(text.encode()).hexdigest()}
        result[identifier] = {**item, "quote_sha256": digest(item)}
    return result


class Provider:
    """Only supported Managed Checkout/existing-subscription operations."""
    def coupon(self, coupon_id):
        return stripe.Coupon.retrieve(coupon_id, expand=["applies_to"], stripe_version=API_VERSION)

    def price(self, offer):
        return partner_offers.price_for(offer)

    def create(self, parameters, key):
        # No standard-mode fallback and no promotion-code field, even false.
        return stripe.checkout.Session.create(**parameters, idempotency_key=key, stripe_version=API_VERSION)

    def session(self, session_id):
        return stripe.checkout.Session.retrieve(session_id, expand=["subscription"], stripe_version=API_VERSION)

    def subscription(self, sub_id):
        return stripe.Subscription.retrieve(sub_id, expand=["items.data.price", "discounts", "default_payment_method", "pending_setup_intent", "latest_invoice"], stripe_version=API_VERSION)

    def payment_method(self, identifier):
        return stripe.PaymentMethod.retrieve(identifier, stripe_version=API_VERSION)

    def setup_intent(self, identifier):
        return stripe.SetupIntent.retrieve(identifier, stripe_version=API_VERSION)

    def invoice(self, identifier):
        return stripe.Invoice.retrieve(identifier, expand=["payments"], stripe_version=API_VERSION)

    def convert(self, sub_id, operation):
        return stripe.Subscription.modify(sub_id, discounts="", billing_cycle_anchor="now", proration_behavior="none",
            payment_behavior="allow_incomplete", metadata={"imladri_intro_conversion": operation["id"]},
            idempotency_key=operation["key"], stripe_version=API_VERSION)

    def cancel(self, sub_id, key):
        return stripe.Subscription.cancel(sub_id, invoice_now=False, prorate=False, idempotency_key=key, stripe_version=API_VERSION)

    def expire(self, session_id, key):
        return stripe.checkout.Session.expire(session_id, idempotency_key=key, stripe_version=API_VERSION)


def coupon_ids(sub):
    values = sub.get("discounts") or []
    if sub.get("discount"):
        values = [*values, sub["discount"]]
    return {stripe_plan.resource_id(item.get("coupon") or (item.get("source") or {}).get("coupon"))
            for item in values if isinstance(item, dict)}


def owned_subscription(sub, row):
    metadata = sub.get("metadata") or {}
    items = (sub.get("items") or {}).get("data") or []
    return (sub.get("id") == row.get("subscription_id") and stripe_plan.resource_id(sub.get("customer")) == row.get("customer_id")
            and type(row.get("livemode")) is bool and sub.get("livemode") is row["livemode"]
            and metadata.get("user_id") == row["user_id"] and metadata.get("imladri_intro_agreement") == row["id"]
            and metadata.get("imladri_intro_version") == VERSION and len(items) == 1 and items[0].get("quantity") == 1
            and stripe_plan.resource_id(items[0].get("price")) == row["price_id"] and not sub.get("schedule"))


def same_subscription(sub, row):
    return owned_subscription(sub, row) and (sub.get("billing_mode") or {}).get("type") == "classic"


def cancelled(sub):
    return sub.get("status") in ("canceled", "cancelled", "incomplete_expired") or bool(sub.get("cancel_at_period_end") or sub.get("cancel_at"))


def authorization_valid(row):
    auth = row.get("authorization") or {}
    return (isinstance(auth, dict) and isinstance(auth.get("text"), str)
            and isinstance(row.get("selected_plan"), dict)
            and auth.get("version") == VERSION and auth.get("authorize_after_messages") is True
            and auth.get("policy_accepted") is True and auth.get("text_sha256") == hashlib.sha256(auth.get("text", "").encode()).hexdigest()
            and auth.get("selected_plan") == row.get("selected_plan") and row.get("strategy") == STRATEGY)


async def state_change(db, row, fields, *, states=None, extra_guard=None):
    query = {"user_id": row["user_id"], "role": "member", "closing": {"$ne": True},
             "intro_billing.id": row["id"], "intro_billing.revision": row["revision"]}
    if states is not None:
        query["intro_billing.state"] = {"$in": list(states)}
    if extra_guard:
        query.update(extra_guard)
    result = await db.users.find_one_and_update(query, {"$set": {"intro_billing." + k: v for k, v in fields.items()},
        "$inc": {"intro_billing.revision": 1}}, return_document=ReturnDocument.AFTER)
    if result is None:
        raise HTTPException(409, "Your introduction changed. Refresh its saved status before continuing.")
    return result


async def read_member(db, uid):
    member = await db.users.find_one({"user_id": uid, "closing": {"$ne": True}})
    if not member or member.get("role") != "member":
        raise HTTPException(403, "This account cannot start an introduction.")
    return member


def check_member(member, expected):
    if member["user_id"] != expected:
        raise HTTPException(409, "The signed-in account changed. Reopen Plans.")


async def setup(db, member, clerk, body, origin, request=None, *, provider=None):
    check_member(member, body.expected_member)
    billing_acceptance.require_intro(member, plan_id=body.plan_id)
    if body.authorization_version != VERSION or not body.authorize_after_messages or not body.policy_accepted:
        raise HTTPException(422, "Review and explicitly authorize the card-first introduction and later monthly charge.")
    if not configured() or not intro_access.configured() or (await intro_access.settings(db))["paused"]:
        raise HTTPException(503, "The card-first introduction is awaiting verified payment-provider setup.")
    provider = provider or Provider()
    current = await read_member(db, member["user_id"])
    billing_risk.require_action(current, "new_intro")
    billing_risk.require_action(current, "new_purchase")
    selected = await quote(db, current, body.plan_id)
    billing_acceptance.require_intro(current, plan_id=body.plan_id, selected=selected)
    if (body.expected_price_cents, body.expected_messages) != (selected["price_cents"], selected["messages"]):
        raise HTTPException(409, "The offered price or allowance changed. Review and authorize the current quote.")
    displayed = (await quotes(db, current))[body.plan_id]
    if ({key: displayed[key] for key in selected} != selected
            or body.authorization_text_sha256 != displayed["authorization_text_sha256"] or body.quote_sha256 != displayed["quote_sha256"]):
        raise HTTPException(409, "The exact authorization changed. Review and accept the current wording.")
    signature = digest({"member": member["user_id"], "selected": selected, "origin": origin, "version": VERSION})
    existing = billing(current)
    if existing:
        if existing.get("request_id") != body.request_id or existing.get("request_signature") != signature:
            raise HTTPException(409, "An introduction already exists. Check its saved status before another checkout.")
        if existing.get("session_id"):
            session = await asyncio.to_thread(provider.session, existing["session_id"])
            if session.get("status") == "open" and trusted_action_url(session.get("url")):
                return {"url": session["url"], "session_id": session["id"], "state": existing["state"], "selected_plan": selected}
        raise HTTPException(409, "This setup has an unresolved outcome. Refresh its saved status; do not start another checkout.")
    if not eligible(current):
        raise HTTPException(403, "This account is not eligible for another introduction.")
    if callable(clerk):
        clerk = await asyncio.to_thread(clerk, member["user_id"])
        if inspect.isawaitable(clerk):
            clerk = await clerk
    try:
        prepared = await intro_access.prepare_claim(db, current, clerk,
            intro_access.Claim(expected_member=body.expected_member, challenge_token=body.challenge_token, device_token=body.device_token), request,
            allow_legacy=True)
    except intro_access.PermanentIntroIneligible as exclusion:
        await retain_permanent_denial(db, current, exclusion.reason)
        raise HTTPException(403, "This account is not eligible for another introduction. Refresh Plans to purchase a regular membership; no introductory messages were granted.") from None
    coupon_id = os.environ["INTRO_CARD_FIRST_COUPON_ID"]
    price, coupon = await asyncio.gather(asyncio.to_thread(provider.price, selected["offer"]), asyncio.to_thread(provider.coupon, coupon_id))
    if (not price or price.get("unit_amount") != selected["price_cents"] or price.get("currency") != "usd"
            or coupon.get("id") != coupon_id or coupon.get("valid") is not True or coupon.get("percent_off") != 100
            or coupon.get("duration") != "forever" or coupon.get("amount_off") is not None
            or coupon.get("livemode") is not price.get("livemode")
            or stripe_plan.resource_id(price.get("product")) not in ((coupon.get("applies_to") or {}).get("products") or [])):
        raise HTTPException(503, "The exact introductory coupon and selected plan require provider verification.")
    agreement_id = str(uuid4());accepted_at = now().isoformat()
    remaining = max(0, LIMIT - intro_access.totals(current)[1]) if intro_access.entitlement(current) else LIMIT
    text = displayed["authorization_text"]
    auth = {"version": VERSION, "text": text, "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "accepted_at": accepted_at, "selected_plan": selected, "remaining_intro_at_acceptance": remaining,
        "quote_sha256": displayed["quote_sha256"],
        "authorize_after_messages": True, "policy_accepted": True,
        "purchase_policy": purchase_policy.snapshot(accepted_at, selected["offer"])}
    metadata = {"user_id": member["user_id"], "lookup_key": partner_offers.lookup(selected["offer"]),
        "imladri_intro_agreement": agreement_id, "imladri_intro_version": VERSION}
    parameters = {"mode": "subscription", "line_items": [{"price": price["id"], "quantity": 1}],
        "discounts": [{"coupon": coupon_id}], "payment_method_collection": "always", "managed_payments": {"enabled": True},
        "customer_email": current.get("email") or None, "metadata": metadata,
        "subscription_data": {"metadata": metadata, "billing_mode": {"type": "classic"}},
        "success_url": origin + "/app/upgrade?intro_session_id={CHECKOUT_SESSION_ID}", "cancel_url": origin + "/app/upgrade?intro_cancelled=1"}
    row = {"id": agreement_id, "user_id": member["user_id"], "version": VERSION, "strategy": STRATEGY, "revision": 1,
        "state": "checkout_pending", "setup_stage": "prepared", "request_id": body.request_id, "request_signature": signature,
        "authorization": auth, "selected_plan": selected, "price_id": price["id"], "coupon_id": coupon_id,
        "provider_proof_sha256": os.environ["INTRO_CARD_FIRST_PROVIDER_PROOF_SHA256"], "livemode": price.get("livemode"), "prepared_grant": prepared["grant"],
        "risk_flags": prepared["flags"], "grant_id": prepared["grant"]["id"], "parameters": parameters,
        "parameters_sha256": digest(parameters), "created_at": accepted_at}
    expected = {"user_id": member["user_id"], "role": "member", "closing": {"$ne": True},
        "replies_paused": {"$ne": True}, "intro_access_paused": {"$ne": True}, "intro_billing": {"$exists": False},
        "intro_active_request": {"$exists": False}, "checkout_attempt": {"$in": [None]},
        "$expr": {"$and": [{"$eq": ["$" + k, {"$literal": current[k]}]} if k in current
            else {"$eq": [{"$type": "$" + k}, "missing"]} for k in ("tier", "stripe_subscription_id", "intro_entitlement", "intro_reply_totals", "complimentary_entitlement", "intro_ineligibility")]}}
    expected = billing_risk.guard_query(expected, current, "new_intro")
    reserved = await db.users.update_one(expected, {"$set": {"intro_billing": row}})
    if not reserved.matched_count:
        raise HTTPException(409, "Your billing or introductory access changed. Refresh before setup.")
    current = await read_member(db, member["user_id"])
    billing_risk.require_action(current, "new_intro")
    billing_acceptance.require_intro(current, selected=selected)
    current = await state_change(db, row, {"setup_stage": "dispatched"}, states=("checkout_pending",),
                                extra_guard=billing_risk.guard_query({}, current, "new_purchase"))
    row = billing(current)
    try:
        billing_acceptance.require_intro(current, selected=row.get("selected_plan"))
        session = await asyncio.to_thread(provider.create, parameters, "intro-setup:" + agreement_id)
        if not session.get("id", "").startswith("cs_") or not trusted_action_url(session.get("url")):
            raise ValueError("Unexpected hosted checkout result")
    except (stripe.StripeError, ValueError):
        await state_change(db, row, {"setup_stage": "unknown", "state": "review_required"})
        raise HTTPException(503, "Hosted setup has an unresolved outcome. Refresh saved status; do not open another setup.") from None
    current = await state_change(db, row, {"session_id": session["id"], "setup_stage": "created"}, states=("checkout_pending",))
    await db.payment_transactions.update_one({"_id": "checkout:" + session["id"]}, {"$setOnInsert": {
        "user_id": member["user_id"], "env": current.get("env"), "session_id": session["id"], "kind": "intro_setup",
        "status": "initiated", "payment_status": "pending", "amount": 0, "currency": "usd", "intro_agreement_id": agreement_id,
        "intro_authorization": auth, "plan_offer": selected["offer"], "stripe_price_id": price["id"],
        "lookup_key": metadata["lookup_key"], "pair_id": selected["pair_id"], "purchase_policy": auth["purchase_policy"],
        "created_at": accepted_at}}, upsert=True)
    return {"url": session["url"], "session_id": session["id"], "state": "checkout_pending", "selected_plan": selected}


async def confirm(db, member, session_id, *, provider=None):
    """Provider reads + one local CAS. A paid zero invoice is card setup only."""
    provider = provider or Provider()
    current = await read_member(db, member["user_id"])
    row = billing(current)
    if not row or session_id != row.get("session_id") or not authorization_valid(row):
        raise HTTPException(409, "This hosted setup does not belong to your current agreement.")
    if row["state"] != "checkout_pending":
        return current
    if not billing_acceptance.intro_allowed(current, selected=row.get("selected_plan")):
        return current
    try:
        billing_risk.require_action(current, "new_intro")
        billing_risk.require_action(current, "new_purchase")
    except HTTPException as hold:
        if hold.status_code == 403:
            return current  # Reads/cancellation remain usable; no grant occurs.
        raise
    if not configured() or not intro_access.configured() or (await intro_access.settings(db))["paused"]:
        return current
    session = await asyncio.to_thread(provider.session, session_id)
    metadata = session.get("metadata") or {}
    if (session.get("id") != session_id or session.get("mode") != "subscription" or session.get("livemode") is not row.get("livemode")
            or (session.get("managed_payments") or {}).get("enabled") is not True
            or session.get("payment_method_collection") != "always"
            or metadata.get("user_id") != current["user_id"] or metadata.get("imladri_intro_agreement") != row["id"]
            or metadata.get("imladri_intro_version") != VERSION):
        raise HTTPException(409, "Stripe has not verified this exact hosted agreement.")
    if session.get("status") == "expired":
        return await state_change(db, row, {"state": "cancelled", "cancelled_at": now().isoformat()})
    if session.get("status") != "complete":
        return current
    sub_id = stripe_plan.resource_id(session.get("subscription"))
    customer = stripe_plan.resource_id(session.get("customer"))
    if not sub_id or not customer or session.get("amount_total") != 0 or session.get("currency") != "usd":
        raise HTTPException(503, "Awaiting the verified zero-charge introduction.")
    sub = await asyncio.to_thread(provider.subscription, sub_id)
    bound = {**row, "subscription_id": sub_id, "customer_id": customer}
    if not owned_subscription(sub, bound):
        raise HTTPException(409, "The saved subscription does not match your authorized plan.")
    if not same_subscription(sub, bound):
        await state_change(db, row, {"state": "review_required", "review_reason": "billing_mode_changed",
            "subscription_id": sub_id, "customer_id": customer})
        raise HTTPException(409, "This subscription needs cancellation and a newly verified classic setup.")
    if cancelled(sub):
        return await state_change(db, row, {"state": "cancelled", "cancelled_at": now().isoformat(),
            "subscription_id": sub_id, "customer_id": customer})
    discounts = sub.get("discounts") or []
    if (sub.get("status") != "active" or sub.get("trial_end") is not None or coupon_ids(sub) != {row["coupon_id"]}
            or len(discounts) != 1 or discounts[0].get("end") is not None):
        raise HTTPException(503, "The indefinite introductory discount has not been verified.")
    invoice = sub.get("latest_invoice")
    if isinstance(invoice, str):
        invoice = await asyncio.to_thread(provider.invoice, invoice)
    if (not isinstance(invoice, dict) or invoice.get("status") != "paid" or invoice.get("total") != 0
            or invoice.get("amount_paid") != 0 or stripe_plan.invoice_subscription(invoice) != sub_id):
        raise HTTPException(503, "The initial zero-charge invoice has not been verified.")
    intent = sub.get("pending_setup_intent")
    if isinstance(intent, str):
        intent = await asyncio.to_thread(provider.setup_intent, intent)
    if intent and (intent.get("status") != "succeeded" or stripe_plan.resource_id(intent.get("customer")) != customer):
        raise HTTPException(503, "Your card setup still needs Stripe confirmation.")
    method = sub.get("default_payment_method")
    if isinstance(method, str):
        method = await asyncio.to_thread(provider.payment_method, method)
    card = (method or {}).get("card") or {}
    if (not isinstance(method, dict) or method.get("type") != "card"
            or stripe_plan.resource_id(method.get("customer")) != customer or not card.get("fingerprint")
            or any(v == "fail" for v in (card.get("checks") or {}).values())):
        raise HTTPException(403, "A verified saved card is required before introductory messages.")
    # A unique keyed digest, not card data, binds one introduction to this card.
    key = "card:" + intro_access.digest("card", str(bool(method.get("livemode"))) + ":" + card["fingerprint"])
    owner = intro_access.digest("user", current["user_id"])
    try:
        await db.intro_identities.update_one({"_id": key}, {"$setOnInsert": {"owner": owner,
            "grant_id": row["grant_id"], "issued_at": now(), "expires_at": now() + timedelta(days=intro_access.RETENTION_DAYS)}}, upsert=True)
    except DuplicateKeyError:
        pass
    identity = await db.intro_identities.find_one({"_id": key})
    if not identity or identity.get("owner") != owner or identity.get("grant_id") != row["grant_id"]:
        await state_change(db, row, {"state": "review_required", "review_reason": "card_already_used",
            "subscription_id": sub_id, "customer_id": customer})
        await billing_risk.observe(db, current["user_id"], current.get("env") or "preview", "identity_conflict",
                                   "card:" + row["id"], "conflict")
        raise HTTPException(403, "This card has already been used for an introduction. No messages were granted.")
    grant = row["prepared_grant"]
    query = {"user_id": current["user_id"], "role": "member", "closing": {"$ne": True},
        "intro_access_paused": {"$ne": True}, "replies_paused": {"$ne": True},
        "intro_billing.id": row["id"], "intro_billing.revision": row["revision"], "intro_billing.state": "checkout_pending",
        "$expr": {"$and": [{"$eq": ["$" + k, {"$literal": current[k]}]} if k in current
            else {"$eq": [{"$type": "$" + k}, "missing"]} for k in ("tier", "stripe_subscription_id", "intro_entitlement", "intro_reply_totals", "complimentary_entitlement")]}}
    query = billing_risk.guard_query(query, current, "new_intro")
    fields = {"state": "active", "card_verified": True, "card_identity": key, "confirmed_at": now().isoformat(),
        "subscription_id": sub_id, "customer_id": customer, "initial_invoice_id": invoice["id"], "setup_stage": "verified"}
    update = {"intro_billing." + k: v for k, v in fields.items()}
    update["intro_entitlement"] = grant
    if not intro_access.entitlement(current):
        update["intro_reply_totals"] = {"claimed": 0, "used": 0}
    saved = await db.users.find_one_and_update(query, {"$set": update, "$inc": {"intro_billing.revision": 1},
        "$addToSet": {"intro_risk_flags": {"$each": row.get("risk_flags", [])}}}, return_document=ReturnDocument.AFTER)
    if not saved:
        raise HTTPException(409, "Your introduction changed. Refresh status to reconcile card setup.")
    await db.payment_transactions.update_one({"_id": "checkout:" + session_id}, {"$set": {
        "status": "card_verified", "stripe_subscription_id": sub_id, "stripe_customer_id": customer,
        "initial_invoice_id": invoice["id"], "updated_at": now().isoformat()}})
    return saved


def conversion_due(receipt):
    identifier = str(uuid5(NAMESPACE_URL, "imladri-intro-conversion:" + receipt["intro_agreement_id"]))
    return {"id": identifier, "key": "intro-convert:" + identifier, "stage": "due", "due_at": now().isoformat(),
        "tenth_message_id": receipt["result"]}


def full_month_period(sub, period):
    """Invoice period must equal the actual item and a complete calendar month."""
    item = ((sub.get("items") or {}).get("data") or [{}])[0]
    start, end = period
    if (start, end) != (item.get("current_period_start"), item.get("current_period_end")):
        return False
    try:
        initial = datetime.fromtimestamp(start, timezone.utc)
        year, month = initial.year + (initial.month == 12), initial.month % 12 + 1
        expected = initial.replace(year=year, month=month, day=min(initial.day, calendar.monthrange(year, month)[1]))
        return int(expected.timestamp()) == end
    except (ValueError, OverflowError, TypeError, OSError):
        return False


async def dispatch_due(db, uid, *, provider=None):
    try:
        from . import accounts
    except ImportError:
        import accounts
    lease = await accounts.reserve_request(db, uid)
    if not lease:
        return
    try:
        return await _dispatch_due(db, uid, provider=provider)
    finally:
        await db.users.update_one({"user_id": uid}, {"$unset": {"request_leases." + lease: ""}})


async def _dispatch_due(db, uid, *, provider=None):
    """One durable dispatch. Unknown results are read-reconciled, never replayed."""
    if not configured() or not intro_access.configured() or (await intro_access.settings(db))["paused"]:
        return
    provider = provider or Provider()
    current = await read_member(db, uid);row = billing(current)
    if not billing_acceptance.intro_allowed(current, selected=(row or {}).get("selected_plan")):
        return
    operation = (row or {}).get("conversion") or {}
    if not row or row["state"] != "billing_pending" or operation.get("stage") != "due":
        return
    try:
        billing_risk.require_action(current, "new_purchase")
    except HTTPException as hold:
        if hold.status_code == 403:
            return
        raise
    if (not authorization_valid(row) or row.get("card_verified") is not True or intro_access.totals(current)[1] != LIMIT
            or current.get("intro_active_request") or current.get("intro_access_paused") or current.get("replies_paused")
            or current.get("complimentary_entitlement") or intro_access.converted(current)
            or current.get("paid_reply_cycles") or current.get("topup_grants")):
        return
    if await quote(db, current, row["selected_plan"]["id"]) != row["selected_plan"]:
        await state_change(db, row, {"state": "review_required", "review_reason": "authorized_quote_changed"})
        return
    sub = await asyncio.to_thread(provider.subscription, row["subscription_id"])
    if not same_subscription(sub, row):
        raise HTTPException(409, "The current subscription no longer matches the saved agreement.")
    if cancelled(sub):
        await state_change(db, row, {"state": "cancelled", "cancelled_at": now().isoformat()});return
    if sub.get("status") != "active" or coupon_ids(sub) != {row["coupon_id"]}:
        await state_change(db, row, {"state": "review_required", "review_reason": "intro_discount_changed"});return
    # Global sales control is checked again after provider reads, immediately
    # before claiming the sole financial dispatch.
    if not configured() or (await intro_access.settings(db))["paused"]:
        return
    if not billing_acceptance.intro_allowed(current, selected=row.get("selected_plan")):
        return
    dispatched_at = now().isoformat()
    op = {**operation, "stage": "dispatched", "dispatched_at": dispatched_at}
    # Cancellation and dispatch compete on the same user revision/state CAS.
    financial_fields = ("tier", "stripe_subscription_id", "subscription_period_start", "subscription_period_end",
        "subscription_status", "complimentary_entitlement", "intro_converted_at", "paid_reply_cycles", "topup_grants",
        "replies_paused", "intro_access_paused")
    boundary = {"$expr": {"$and": [{"$eq": ["$" + field, {"$literal": current[field]}]} if field in current
        else {"$eq": [{"$type": "$" + field}, "missing"]} for field in financial_fields]}}
    boundary = billing_risk.guard_query(boundary, current, "new_purchase")
    current = await state_change(db, row, {"conversion": op}, states=("billing_pending",), extra_guard=boundary)
    row = billing(current)
    try:
        updated = await asyncio.to_thread(provider.convert, row["subscription_id"], op)
        if not same_subscription(updated, row) or (updated.get("metadata") or {}).get("imladri_intro_conversion") != op["id"]:
            raise ValueError("Unexpected conversion response")
    except (stripe.StripeError, ValueError):
        await state_change(db, row, {"conversion": {**op, "stage": "unknown"}})
        return
    await state_change(db, row, {"conversion": {**op, "stage": "awaiting_invoice"}})


async def reconcile(db, member, *, provider=None, fulfill=None):
    """GET-safe: provider reads and local confirmations only, never dispatches money."""
    provider = provider or Provider();current = await read_member(db, member["user_id"]);row = billing(current)
    if not row or not row.get("session_id") or row["state"] in ("cancelled", "converted"):
        return current
    if row["state"] == "checkout_pending":
        return await confirm(db, current, row["session_id"], provider=provider)
    if not row.get("subscription_id"):
        return current
    sub = await asyncio.to_thread(provider.subscription, row["subscription_id"])
    if not same_subscription(sub, row):
        return await state_change(db, row, {"state": "review_required", "review_reason": "subscription_changed"})
    if cancelled(sub):
        return await state_change(db, row, {"state": "cancelled", "cancelled_at": now().isoformat()})
    operation = row.get("conversion") or {}
    if row["state"] not in ("billing_pending", "requires_action", "payment_failed") or operation.get("stage") not in ("dispatched", "unknown", "awaiting_invoice"):
        return current
    # Initial $0 invoices, active status and a successful update are not payment.
    if (sub.get("metadata") or {}).get("imladri_intro_conversion") != operation.get("id") or coupon_ids(sub):
        return current
    invoice = sub.get("latest_invoice")
    if isinstance(invoice, str):
        invoice = await asyncio.to_thread(provider.invoice, invoice)
    invoice = invoice or {}
    details = (invoice.get("parent") or {}).get("subscription_details") or invoice.get("subscription_details") or {}
    metadata = details.get("metadata") or {}
    if (invoice.get("id") == row.get("initial_invoice_id") or invoice.get("billing_reason") != "subscription_update"
            or metadata.get("imladri_intro_conversion") != operation.get("id")
            or stripe_plan.invoice_subscription(invoice) != row["subscription_id"]):
        return current
    created = invoice.get("created")
    sent = int(datetime.fromisoformat(operation["dispatched_at"]).timestamp())
    if type(created) is not int or created < sent:
        return current
    if invoice.get("status") != "paid":
        action = invoice.get("hosted_invoice_url")
        fields = {"state": "requires_action" if trusted_action_url(action) else "payment_failed"}
        if trusted_action_url(action):
            fields["action_url"] = action
        return await state_change(db, row, fields)
    charge = await asyncio.to_thread(stripe_plan.paid_plan_charge, invoice, row["subscription_id"], row["price_id"])
    # Require the exact whole monthly plan and actual settlement, never a
    # partial/prorated invoice or credit-balance-only zero charge.
    if (not charge or charge[1] != row["selected_plan"]["price_cents"] or charge[0][0] < sent or not full_month_period(sub, charge[0])
            or type(invoice.get("amount_paid")) is not int or invoice["amount_paid"] < invoice.get("total", 0)
            or invoice.get("currency") != "usd" or not fulfill):
        return current
    tx = await db.payment_transactions.find_one({"_id": "checkout:" + row["session_id"], "intro_agreement_id": row["id"]})
    if not tx:
        raise HTTPException(503, "The introductory agreement transaction needs reconciliation.")
    tx = {**tx, "kind": "purchase", "amount": row["selected_plan"]["price_cents"] / 100,
        "intro_conversion_id": operation["id"], "intro_conversion_verified_invoice": invoice["id"]}
    await db.payment_transactions.update_one({"_id": tx["_id"], "intro_agreement_id": row["id"]}, {"$set": {
        "kind": "purchase", "amount": tx["amount"], "intro_conversion_id": operation["id"],
        "intro_conversion_verified_invoice": invoice["id"]}})
    await fulfill(tx, {"id": row["session_id"], "subscription": row["subscription_id"], "customer": row["customer_id"], "invoice": invoice})
    delivered = await db.payment_transactions.find_one({"_id": tx["_id"], "payment_status": "paid", "credit_delivery_status": "fulfilled"})
    if not delivered:
        return current
    return await state_change(db, row, {"state": "converted", "conversion": {**operation, "stage": "paid", "invoice_id": invoice["id"]},
        "converted_at": now().isoformat(), "action_url": None})


async def cancel(db, member, body, *, provider=None):
    check_member(member, body.expected_member);provider = provider or Provider()
    current = await read_member(db, member["user_id"]);row = billing(current)
    if row and row.get("cancel_request_id") == body.request_id:
        # GET is the only reconciliation after an unknown provider outcome.
        return await reconcile_cancel(db, current, provider=provider)
    if not row or body.revision != row["revision"] or not status_fields(current)["billing"]["can_cancel"]:
        raise HTTPException(409, "The introduction changed. Refresh before cancelling.")
    current = await state_change(db, row, {"state": "review_required", "cancellation_requested": True,
        "cancel_request_id": body.request_id, "cancel_stage": "dispatched"})
    row = billing(current)
    try:
        if row.get("subscription_id"):
            await asyncio.to_thread(provider.cancel, row["subscription_id"], "intro-cancel:" + row["id"])
        elif row.get("session_id"):
            await asyncio.to_thread(provider.expire, row["session_id"], "intro-expire:" + row["id"])
        else:
            return current
    except stripe.StripeError:
        return current
    return await reconcile_cancel(db, current, provider=provider)


async def reconcile_cancel(db, member, *, provider=None):
    provider = provider or Provider();row = billing(member)
    if not row or not row.get("cancellation_requested") or row["state"] == "cancelled":
        return member
    if row.get("subscription_id"):
        sub = await asyncio.to_thread(provider.subscription, row["subscription_id"])
        complete = owned_subscription(sub, row) and cancelled(sub)
    else:
        session = await asyncio.to_thread(provider.session, row["session_id"])
        complete = session.get("id") == row["session_id"] and session.get("status") == "expired"
        if session.get("status") == "complete" and (session.get("metadata") or {}).get("imladri_intro_agreement") == row["id"]:
            sub_id, customer = stripe_plan.resource_id(session.get("subscription")), stripe_plan.resource_id(session.get("customer"))
            sub = await asyncio.to_thread(provider.subscription, sub_id)
            if owned_subscription(sub, {**row, "subscription_id": sub_id, "customer_id": customer}):
                # The expired-session operation is never replayed. A newly
                # explicit cancellation can now target the bound subscription.
                return await state_change(db, row, {"subscription_id": sub_id, "customer_id": customer,
                    "cancel_stage": "needs_subscription_cancel"})
    if not complete:
        return member
    return await state_change(db, row, {"state": "cancelled", "cancel_stage": "confirmed", "cancelled_at": now().isoformat()})


async def refresh(db, member, *, provider=None, fulfill=None):
    row = billing(member)
    if row and row.get("cancellation_requested"):
        return await reconcile_cancel(db, member, provider=provider)
    return await reconcile(db, member, provider=provider, fulfill=fulfill)


async def handle_event(db, event_type, obj, *, fulfill):
    sub = stripe_plan.invoice_subscription(obj) if event_type.startswith("invoice.") else obj.get("id") if event_type.startswith("customer.subscription.") else stripe_plan.resource_id(obj.get("subscription"))
    session = obj.get("id") if event_type.startswith("checkout.session.") else None
    alternatives = ([{"intro_billing.subscription_id": sub}] if sub else []) + ([{"intro_billing.session_id": session}] if session else [])
    query = {"$or": alternatives} if alternatives else None
    if not query:
        return False
    member = await db.users.find_one(query)
    row = billing(member or {})
    if not row or row["state"] == "converted":
        return False
    await refresh(db, member, fulfill=fulfill)
    return True


async def recover(db, fulfill):
    cursor = None
    base = {"role": "member", "closing": {"$ne": True}, "user_id": {"$type": "string"},
        "intro_billing.state": {"$in": ["billing_pending", "requires_action", "payment_failed"]}}
    while True:
        try:
            query = base if cursor is None else {**base, "user_id": {"$type": "string", "$gt": cursor}}
            rows = await db.users.find(query).sort("user_id", 1).limit(25).to_list(25)
            if not rows and cursor is not None:
                cursor = None
                rows = await db.users.find(base).sort("user_id", 1).limit(25).to_list(25)
            for member in rows:
                # Failed/SCA accounts stay eligible for read reconciliation.
                # Advance before handling so they cannot block later members.
                cursor = member["user_id"]
                try:
                    await dispatch_due(db, member["user_id"])
                    await refresh(db, await read_member(db, member["user_id"]), fulfill=fulfill)
                except Exception:
                    # Durable original operation remains due/unknown; later reads
                    # can recover it without a fresh financial operation.
                    log.warning("Introductory billing is awaiting safe reconciliation")
        except Exception:
            log.warning("Introductory billing recovery scan did not complete")
        await asyncio.sleep(30)


def make_router(get_db, current_member, clerk_fetch, origin_fn, fulfill):
    router = APIRouter()

    @router.post("/me/intro-access/setup")
    async def start(body: Setup, request: Request, member=Depends(current_member)):
        return await setup(get_db(), member, clerk_fetch, body, origin_fn(body.origin_url, request), request)

    @router.post("/me/intro-access/confirm")
    async def finish(body: Confirm, member=Depends(current_member)):
        check_member(member, body.expected_member)
        saved = await confirm(get_db(), member, body.session_id)
        return await intro_access.status(get_db(), saved)

    @router.post("/me/intro-access/cancel")
    async def stop(body: Cancel, member=Depends(current_member)):
        saved = await cancel(get_db(), member, body)
        return await intro_access.status(get_db(), saved)

    return router
