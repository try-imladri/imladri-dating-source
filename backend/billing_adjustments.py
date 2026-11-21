"""Verified, content-free payment adjustments; never issue a Stripe mutation.

Reconciliation serializes provider reads for one payment. The account update is
sequence fenced; a durable transaction outbox makes the financial report copy
retryable. Ambiguous historical payment/allowance links require review.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
from uuid import uuid4

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
import stripe

if __package__:
    from . import membership_plans, stripe_plan, stripe_membership, stripe_partner_prices, billing_risk
else:
    import membership_plans, stripe_plan, stripe_membership, stripe_partner_prices, billing_risk

VERSION = "billing-adjustments-v1"
KINDS = ("purchase", "renewal", "upgrade", "topup")
DISPUTE_STATES = {"needs_response", "under_review", "lost", "won", "warning_needs_response",
                  "warning_under_review", "warning_closed", "prevented"}
WITHDRAWN = {"lost"}
PENDING_DISPUTES = {"needs_response", "under_review", "warning_needs_response", "warning_under_review"}
PERSONAL_KEYS = set(stripe_membership.ALL_KEYS) | set(stripe_partner_prices.LOOKUP_KEYS.values()) | {"plus_monthly"}
EVENTS = ("charge.refunded", "refund.updated", "refund.failed", "charge.refund.updated", "charge.dispute.created",
          "charge.dispute.updated", "charge.dispute.closed", "charge.dispute.funds_reinstated")


def blocked(reason):
    raise HTTPException(503, "Payment adjustment requires reconciliation: " + reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def current_time():
    return datetime.now(timezone.utc).isoformat()


def anonymous_review_id(event_id, env):
    return hashlib.sha256(f"{env}/unresolved-financial/{event_id}".encode()).hexdigest()


def basis(invoice, sub_id, price_id, period, messages, revenue_cents):
    """Retain only verified allowance and accounting inputs, never content."""
    paid = invoice.get("amount_paid")
    if not (isinstance(invoice.get("id"), str) and type(paid) is int and paid > 0
            and type(messages) is int and messages > 0 and type(revenue_cents) is int
            and 0 <= revenue_cents <= paid):
        return None
    return {"version": VERSION, "invoice_id": invoice["id"], "subscription_id": sub_id,
            "price_id": price_id, "period_start": period[0], "period_end": period[1],
            "messages": messages, "gross_cents": paid, "invoice_total_cents": invoice.get("total"), "revenue_cents": revenue_cents,
            "currency": invoice.get("currency")}


def validate_basis(value):
    return (isinstance(value, dict) and set(value) == {"version", "invoice_id", "subscription_id", "price_id",
            "period_start", "period_end", "messages", "gross_cents", "invoice_total_cents", "revenue_cents", "currency"}
            and value["version"] == VERSION and value["currency"] == "usd"
            and all(isinstance(value[k], str) and value[k] for k in ("invoice_id", "subscription_id", "price_id"))
            and all(type(value[k]) is int for k in ("period_start", "period_end", "messages", "gross_cents", "invoice_total_cents", "revenue_cents"))
            and 0 < value["period_start"] < value["period_end"] and value["messages"] > 0
            and 0 < value["gross_cents"] <= value["invoice_total_cents"]
            and 0 <= value["revenue_cents"] <= value["gross_cents"])


def invoice_payment(invoice, session=None):
    direct = stripe_plan.resource_id((session or {}).get("payment_intent"))
    if direct: return direct
    page = invoice.get("payments")
    if page is None or page.get("has_more"):
        page = stripe.InvoicePayment.list(invoice=invoice["id"], status="paid", limit=2)
    rows = [p for p in page.get("data", []) if p.get("status") == "paid"]
    if page.get("has_more") or len(rows) != 1: blocked("split or missing invoice payment link")
    payment = rows[0].get("payment") or {}
    if (payment.get("type") != "payment_intent" or not payment.get("payment_intent")
            or stripe_plan.resource_id(rows[0].get("invoice")) != invoice["id"]
            or rows[0].get("amount_paid") != invoice.get("amount_paid")):
        blocked("non-cash or split invoice payment needs review")
    return stripe_plan.resource_id(payment["payment_intent"])


async def review_required(db, tx, reason, *, env, charge_id=None, payment_id=None, sequence=None, token=None):
    key = digest({"transaction": str(tx.get("_id"))})
    month = datetime.now(timezone.utc).date().replace(day=1).isoformat()
    if sequence is None:
        current = await db.payment_transactions.find_one({"_id": tx.get("_id")}, {"billing_reconciliation_sequence": 1})
        sequence = (current or tx).get("billing_reconciliation_sequence", 0) + 1
    marker_id = anonymous_review_id("stripe:review:" + key, env)
    match = {"_id": marker_id, "$or": [
        {"data.reconciliation_sequence": {"$exists": False}},
        {"data.reconciliation_sequence": {"$lt": sequence}},
        {"data.reconciliation_sequence": sequence, "data.reconciliation_token": {"$in": [None, token]}}]}
    try:
        # This is authoritative and anonymous from the first write. Account
        # erasure cannot race a late owner/Stripe-identified event insertion.
        await db.closed_membership_revenue.update_one(match, {"$setOnInsert": {
            "env": env, "kind": "unresolved_adjustment"}, "$set": {
                "month": month, "data.reason": reason, "data.status": "pending",
                "data.reconciliation_sequence": sequence, "data.reconciliation_token": token},
                "$addToSet": {"pending_months": month}}, upsert=True)
    except DuplicateKeyError:
        # A newer journal owner already has the deterministic ID. A stale
        # writer cannot replace its pending or resolved marker.
        return False
    return True


async def prepare_subscription(db, tx, invoice, session, sub_id, price_id, period, messages, revenue_cents, *, env):
    if "_id" not in tx:
        tx = await db.payment_transactions.find_one({"session_id": tx.get("session_id"), "user_id": tx["user_id"]})
        if tx is None: blocked("transaction registration unavailable")
    # A fully credited invoice is an existing legitimate paid-period path. It
    # has no captured cash payment to refund, and must not invent a charge.
    if type(invoice.get("amount_paid")) is int and invoice["amount_paid"] == 0 and revenue_cents == 0:
        await db.payment_transactions.update_one({"_id": tx["_id"]}, {"$set": {
            "billing_basis_status": "fully_credited_invoice_no_cash_charge"}})
        return {"status": "fully_credited_invoice_no_cash_charge"}
    allocation = basis(invoice, sub_id, price_id, period, messages, revenue_cents)
    if not validate_basis(allocation):
        await review_required(db, tx, "exact_subscription_allowance_basis_missing", env=env)
        blocked("exact subscription allowance basis missing")
    try:
        payment_id = await asyncio.wait_for(asyncio.to_thread(invoice_payment, invoice, session), 30)
    except (stripe.StripeError, TimeoutError, HTTPException):
        await review_required(db, tx, "subscription_payment_link_unavailable", env=env)
        blocked("subscription payment link unavailable")
    if tx.get("currency") not in (None, "usd"):
        await review_required(db, tx, "local_payment_currency_mismatch", env=env, payment_id=payment_id)
        blocked("local payment currency mismatch")
    fields = {"billing_basis": allocation, "stripe_invoice_id": invoice["id"], "stripe_payment_intent_id": payment_id}
    if tx.get("currency") is None:
        fields.update(currency=invoice["currency"], billing_basis_recovered_from_verified_invoice=True)
    await db.payment_transactions.update_one({"_id": tx["_id"]}, {"$set": fields})
    return await reconcile(db, {**tx, "billing_basis": allocation, "stripe_payment_intent_id": payment_id},
                           env=env, payment_id=payment_id, source_event="verified_invoice_fulfillment")


def fetch_state(charge_id, payment_id=None):
    if not charge_id:
        if not payment_id: blocked("missing payment link")
        payment = stripe.PaymentIntent.retrieve(payment_id)
        charge_id = stripe_plan.resource_id(payment.get("latest_charge"))
    if not charge_id: blocked("missing captured charge")
    charge = stripe.Charge.retrieve(charge_id)
    actual_pi = stripe_plan.resource_id(charge.get("payment_intent"))
    gross, refunded = charge.get("amount"), charge.get("amount_refunded")
    if (charge.get("id") != charge_id or charge.get("paid") is not True or charge.get("captured") is not True
            or type(charge.get("amount_captured")) is not int or charge["amount_captured"] != gross or charge.get("currency") != "usd"
            or not actual_pi or (payment_id and actual_pi != payment_id)
            or type(gross) is not int or gross <= 0 or type(refunded) is not int or not 0 <= refunded <= gross):
        blocked("unverified charge")
    page = stripe.Dispute.list(charge=charge_id, limit=100)
    if page.get("has_more"): blocked("dispute inventory exceeds bound")
    disputes = []
    for item in page.get("data", []):
        if (item.get("status") not in DISPUTE_STATES or stripe_plan.resource_id(item.get("charge")) != charge_id
                or item.get("currency") != "usd" or not isinstance(item.get("id"), str)
                or type(item.get("amount")) is not int or not 0 <= item["amount"] <= gross):
            blocked("unverified dispute")
        disputes.append({k: item[k] for k in ("id", "amount", "status")})
    if len({d["id"] for d in disputes}) != len(disputes): blocked("duplicate dispute identifiers")
    disputed = sum(d["amount"] for d in disputes if d["status"] in WITHDRAWN)
    if disputed > gross: blocked("overlapping dispute amounts")
    # A partial refund and partial dispute may cover the same portion. Never
    # invent an allocation when the current objects do not establish one.
    if refunded and disputed and refunded < gross and disputed < gross:
        blocked("overlapping partial refund and dispute need review")
    loss = min(gross, refunded + disputed)
    state = {"version": VERSION, "charge_id": charge_id, "payment_intent_id": actual_pi,
             "gross_cents": gross, "refunded_cents": refunded, "disputed_cents": disputed,
             "loss_cents": loss, "currency": "usd", "disputes": sorted(disputes, key=lambda d: d["id"])}
    return state


async def flush_outbox(db, tx):
    event = tx.get("billing_adjustment_outbox")
    if not event: return
    at = datetime.fromisoformat(event["ts"])
    receipt = {"_id": hashlib.sha256(f"{event['env']}/adjustment/{event['_id']}".encode()).hexdigest(),
        "env": event["env"], "month": at.astimezone(timezone.utc).date().replace(day=1).isoformat(),
        "kind": "topup_adjustment" if event["data"]["kind"] == "topup" else "subscription_adjustment",
        "cents": event["data"]["offset_cents"]}
    # Financial publication is also anonymous from its first write, including
    # when a derived-outbox flush races account closure.
    await db.closed_membership_revenue.update_one({"_id": receipt["_id"]}, {"$setOnInsert": receipt}, upsert=True)
    await db.payment_transactions.update_one({"_id": tx["_id"], "billing_adjustment_outbox._id": event["_id"]},
                                              {"$unset": {"billing_adjustment_outbox": ""}})


async def flush_pending(db, env, uid=None):
    query = {"env": {"$in": [None, env]}, "billing_adjustment_outbox": {"$exists": True}}
    if uid is not None: query["user_id"] = uid
    async for tx in db.payment_transactions.find(query, {"billing_adjustment_outbox": 1}):
        await flush_outbox(db, tx)


async def event_owner(db, obj):
    payment = stripe_plan.resource_id(obj.get("payment_intent"))
    charge = obj.get("id") if obj.get("object") == "charge" else stripe_plan.resource_id(obj.get("charge"))
    clauses = []
    if payment: clauses.append({"stripe_payment_intent_id": payment})
    if charge: clauses.append({"stripe_charge_id": charge})
    refs = [x for x in (payment, charge) if x]
    if refs: clauses.append({"balance_source_ids": {"$in": refs}})
    if not clauses: return None
    rows = await db.payment_transactions.find({"kind": {"$in": list(KINDS)}, "$or": clauses}, {"user_id": 1}).limit(2).to_list(2)
    return rows[0].get("user_id") if len(rows) == 1 else None


async def reconcile(db, tx, *, env, charge_id=None, payment_id=None, source_event=None):
    """Reconcile a precisely linked local payment from current Stripe reads."""
    if "_id" not in tx:
        tx = await db.payment_transactions.find_one({"session_id": tx.get("session_id"), "user_id": tx["user_id"], "kind": tx["kind"]})
        if tx is None: blocked("transaction registration unavailable")
    if tx.get("env") not in (None, env): blocked("payment environment mismatch")
    token = str(uuid4()); stamp = current_time()
    deadline = time.monotonic() + 2
    while True:
        leased = await db.payment_transactions.find_one_and_update(
            {"_id": tx["_id"], "kind": {"$in": list(KINDS)}, "$or": [{"billing_reconciliation": None},
                {"billing_reconciliation.expires": {"$lte": stamp}}]},
            {"$set": {"billing_reconciliation": {"id": token, "expires":
                 (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()}},
             "$inc": {"billing_reconciliation_sequence": 1}}, return_document=ReturnDocument.AFTER)
        if leased is not None or time.monotonic() >= deadline:
            break
        await asyncio.sleep(.05)
    if leased is None: blocked("another reconciliation is running")
    sequence = leased["billing_reconciliation_sequence"]
    payment_key = hashlib.sha256((payment_id or leased.get("stripe_payment_intent_id") or str(leased["_id"])).encode()).hexdigest()
    fence = "billing_adjustment_fences." + payment_key
    applied = "billing_adjustment_sequences." + payment_key
    try:
        # The allowance mutation and its authority live on the same account
        # document. A new payment-lease owner reserves this account fence before
        # any provider await; delayed workers require exact token equality.
        reserved = await db.users.update_one({"user_id": leased["user_id"], "env": {"$in": [None, env]},
            "closing": {"$ne": True}, "$and": [
                {"$or": [{applied: {"$exists": False}}, {applied: {"$lt": sequence}}]},
                {"$or": [{fence + ".sequence": {"$exists": False}}, {fence + ".sequence": {"$lt": sequence}}]}]},
            [{"$set": {fence: {"id": token, "sequence": sequence, "expires": {"$add": ["$$NOW", 120000]}}}}])
        if not reserved.matched_count: blocked("account reconciliation authority unavailable")
        await flush_outbox(db, leased)
        try:
            state = await asyncio.wait_for(asyncio.to_thread(fetch_state, charge_id, payment_id), 30)
        except (stripe.StripeError, TimeoutError):
            blocked("provider state unavailable")
        if leased.get("currency") != "usd": blocked("local payment currency mismatch")
        if leased["kind"] == "topup":
            topup_basis = leased.get("topup_billing_basis")
            expected = {"version": VERSION, "session_id": leased.get("session_id"),
                "payment_intent_id": state["payment_intent_id"], "gross_cents": state["gross_cents"],
                "currency": "usd", "messages": membership_plans.TOPUP["messages"]}
            if topup_basis is None:
                if __package__:
                    from . import stripe_refills
                else:
                    import stripe_refills
                session = await asyncio.wait_for(asyncio.to_thread(stripe.checkout.Session.retrieve, leased["session_id"]), 30)
                verified_pi, verified_charge = await asyncio.wait_for(asyncio.to_thread(stripe_refills.verified_pack, leased, session), 30)
                if verified_pi != state["payment_intent_id"] or verified_charge.get("id") != state["charge_id"]:
                    blocked("refill charge link mismatch")
                await db.payment_transactions.update_one({"_id": leased["_id"], "billing_reconciliation.id": token},
                                                         {"$set": {"topup_billing_basis": expected}})
            elif topup_basis != expected or digest(topup_basis) != digest(expected):
                blocked("refill billing basis changed")
        # Durable pending journal precedes the account-side change. If the
        # process stops between that change and the reporting outbox, Admin
        # cannot mistakenly label original gross as verified net revenue.
        marked = await review_required(db, leased, "financial_reconciliation_in_progress", env=env,
            charge_id=state["charge_id"], payment_id=state["payment_intent_id"], sequence=sequence, token=token)
        if not marked: blocked("newer financial journal owner requires readback")
        if not await db.payment_transactions.find_one({"_id": leased["_id"], "billing_reconciliation.id": token,
                "billing_reconciliation_sequence": sequence, "billing_reconciliation.expires": {"$gt": current_time()}}, {"_id": 1}):
            blocked("payment lease expired before account mutation")
        if hashlib.sha256(state["payment_intent_id"].encode()).hexdigest() != payment_key:
            blocked("payment identity missing before account fence")
        await apply_state(db, leased, state, sequence, token, env, source_event)
        return {"user_id": leased["user_id"], "kind": leased["kind"], "payment_intent_id": state["payment_intent_id"],
                "state_sha256": digest(state), "loss_cents": state["loss_cents"], "refunded_cents": state["refunded_cents"]}
    except (stripe.StripeError, TimeoutError, ValueError):
        await review_required(db, leased, "provider_payment_verification_unavailable", env=env,
                              charge_id=charge_id, payment_id=payment_id, sequence=sequence, token=token)
        blocked("provider payment verification unavailable")
    except HTTPException as exc:
        reason = str(exc.detail).removeprefix("Payment adjustment requires reconciliation: ")
        await review_required(db, leased, reason, env=env, charge_id=charge_id, payment_id=payment_id,
                              sequence=sequence, token=token)
        raise
    finally:
        # Keep the highest reserved sequence even after a failed provider read;
        # a delayed lower owner must never reacquire an erased fence.
        await db.users.update_one({"user_id": leased["user_id"], fence + ".id": token,
            fence + ".sequence": sequence}, {"$set": {fence + ".id": None,
                fence + ".expires": datetime.now(timezone.utc)}})
        await db.payment_transactions.update_one({"_id": tx["_id"], "billing_reconciliation.id": token},
                                                 {"$unset": {"billing_reconciliation": ""}})


async def apply_state(db, tx, state, sequence, token, env, source_event):
    if tx.get("env") not in (None, env): blocked("payment environment mismatch")
    uid, gross, loss = tx["user_id"], state["gross_cents"], state["loss_cents"]
    payment_id = state["payment_intent_id"]
    known = tx.get("stripe_payment_intent_id")
    if known and known != payment_id: blocked("payment identity changed")
    key = hashlib.sha256(payment_id.encode()).hexdigest()
    seq_field = "billing_adjustment_sequences." + key
    changes = {seq_field: sequence}
    new_refill = False
    pending_cents = min(gross - loss, sum(d["amount"] for d in state["disputes"] if d["status"] in PENDING_DISPUTES))
    legacy_pending_refill = False
    claimed_for_risk = 0
    if tx["kind"] == "topup":
        if not isinstance(tx.get("session_id"), str): blocked("refill session link missing")
        grant = "topup_grants." + hashlib.sha256(tx["session_id"].encode()).hexdigest()
        tracking = "topup_payment_tracking." + grant.split(".", 1)[1]
        prior = await db.users.find_one({"user_id": uid, "env": {"$in": [None, env]}}, {grant: 1, "topup_balance": 1, tracking: 1})
        old_grants = (prior or {}).get("topup_grants") or {}
        if not isinstance(old_grants, dict): blocked("legacy refill grant shape needs review")
        new_refill = grant.split(".", 1)[1] not in old_grants
        if (new_refill and not tx.get("billing_adjustment") and tx.get("credit_delivery_status") != "review_required"
                and (tx.get("payment_status") == "paid" or tx.get("fulfilled_at"))):
            blocked("fulfilled legacy refill has no attributable grant balance")
        if new_refill and type((prior or {}).get("topup_balance")) is int and prior["topup_balance"] < 0:
            await paid_refill_review(db, tx, state, sequence, token)
            blocked("paid refill needs review because a previous refund adjusted already-used credits")
        net = membership_plans.TOPUP["messages"] * (gross - loss) // gross
        pending_messages = min(net, (membership_plans.TOPUP["messages"] * pending_cents + gross - 1) // gross)
        tracking_records = (prior or {}).get("topup_payment_tracking") or {}
        if not isinstance(tracking_records, dict):
            blocked("refill attribution shape requires review")
        tracked = tracking_records.get(grant.split(".", 1)[1])
        if new_refill and tracked is not None:
            blocked("refill grant and attribution disagree")
        if new_refill or tracked is not None:
            if tracked is not None:
                billing_risk.refill_available(tracked)
                claimed_for_risk = tracked["claimed_messages"]
            # Provider reads and AI reservations can overlap. Read the CURRENT
            # claimed amount in the same document mutation; never overwrite it
            # with the earlier Python snapshot or debit another funded pack.
            changes.update({grant: net, tracking + ".net_messages": net,
                tracking + ".pending_messages": pending_messages,
                tracking + ".claimed_messages": {"$ifNull": ["$" + tracking + ".claimed_messages", 0]},
                "topup_balance": {"$add": [{"$ifNull": ["$topup_balance", 0]}, {"$subtract": [
                    billing_risk.refill_expression(tracking, net=net, pending=pending_messages),
                    billing_risk.refill_expression(tracking)]}]}})
        else:
            # Historical pooled credits contain no per-pack consumption link.
            # Do not invent which pack was spent or freeze unrelated credits.
            # Preserve confirmed loss accounting and surface exact attribution
            # review while a pending dispute remains unresolved.
            legacy_pending_refill = pending_cents > 0
            changes.update({grant: net, "topup_balance": {"$add": [{"$ifNull": ["$topup_balance", 0]},
                {"$subtract": [net, {"$ifNull": ["$" + grant, 0]}]}]}})
        revenue = membership_plans.TOPUP["price_cents"]
    else:
        allocation = tx.get("billing_basis")
        if not validate_basis(allocation) or allocation["gross_cents"] != gross:
            blocked("legacy or split subscription payment needs exact allowance basis")
        net = allocation["messages"] * (allocation["invoice_total_cents"] - loss) // allocation["invoice_total_cents"]
        held = min(net, (allocation["messages"] * pending_cents + allocation["invoice_total_cents"] - 1) // allocation["invoice_total_cents"])
        field = "subscription_allowance_adjustments." + key
        changes[field] = {"$literal": {"version": VERSION, "invoice_id": allocation["invoice_id"],
            "subscription_id": allocation["subscription_id"], "price_id": allocation["price_id"],
            "period_start": allocation["period_start"], "period_end": allocation["period_end"],
            "original_messages": allocation["messages"], "net_messages": net,
            "pending_dispute_messages": held, "available_messages": net - held}}
        revenue = allocation["revenue_cents"]
    # The sequence is shared with the payment lease, so a late older worker
    # cannot overwrite a newer restored/reversed allowance.
    query = {"user_id": uid, "env": {"$in": [None, env]}, "closing": {"$ne": True}, "$or": [
        {seq_field: {"$exists": False}}, {seq_field: {"$lt": sequence}}]}
    fence = "billing_adjustment_fences." + key
    query.update({fence + ".id": token, fence + ".sequence": sequence})
    if new_refill:
        query["$and"] = [{"$or": [{"topup_balance": {"$exists": False}}, {"topup_balance": {"$gte": 0}}]}]
    expires = {"$gt": ["$" + fence + ".expires", "$$NOW"]}
    query["$expr"] = expires
    if tx["kind"] != "topup":
        owner = await db.users.find_one({"user_id": uid, "env": {"$in": [None, env]}}, {"subscription_allowance_adjustments": 1})
        records = (owner or {}).get("subscription_allowance_adjustments") or {}
        if not isinstance(records, dict): blocked("allowance adjustment shape requires review")
        for other_key, other in records.items():
            if (other_key != key and isinstance(other, dict)
                    and other.get("subscription_id") == allocation["subscription_id"]
                    and other.get("price_id") == allocation["price_id"]
                    and other.get("period_start") == allocation["period_start"]
                    and other.get("period_end") == allocation["period_end"]
                    and (loss > 0 or held > 0 or other.get("pending_dispute_messages", 0) > 0
                         or other.get("net_messages") != other.get("original_messages"))):
                blocked("multiple paid invoices for the same allowance need review")
        snapshot = ({"$eq": ["$subscription_allowance_adjustments", {"$literal": owner["subscription_allowance_adjustments"]}]}
            if owner and "subscription_allowance_adjustments" in owner else
            {"$eq": [{"$type": "$subscription_allowance_adjustments"}, "missing"]})
        query["$expr"] = {"$and": [snapshot, expires]}
    # Owner/grant reads above can await. Recheck the exact lease again at the
    # final account mutation boundary; a stale worker must not publish credit.
    if not await db.payment_transactions.find_one({"_id": tx["_id"], "billing_reconciliation.id": token,
            "billing_reconciliation_sequence": sequence, "billing_reconciliation.expires": {"$gt": current_time()}}, {"_id": 1}):
        blocked("payment lease changed before account mutation")
    saved = await db.users.update_one(query, [{"$set": changes}])
    if not saved.matched_count:
        existing = await db.users.find_one({"user_id": uid, "env": {"$in": [None, env]}, "closing": {"$ne": True}}, {seq_field: 1})
        if not existing: blocked("account is unavailable")
        if new_refill:
            actual = await db.users.find_one({"user_id": uid}, {"topup_balance": 1})
            if type((actual or {}).get("topup_balance")) is int and actual["topup_balance"] < 0:
                await paid_refill_review(db, tx, state, sequence, token)
                blocked("paid refill needs review because a previous refund adjusted already-used credits")
        blocked("newer account reconciliation requires readback")
    offset = revenue - revenue * (gross - loss) // gross
    previous = tx.get("billing_adjustment") or {}
    delta = offset - previous.get("revenue_offset_cents", 0)
    observed = current_time()
    summary = {"version": VERSION, "state_sha256": digest(state), "state": state, "net_credits": net,
               "pending_dispute_cents": pending_cents, "pending_refill_attribution_review": legacy_pending_refill,
               "revenue_offset_cents": offset, "observed_at": observed}
    update = {"billing_adjustment": summary, "stripe_charge_id": state["charge_id"],
              "stripe_payment_intent_id": payment_id}
    if tx["kind"] == "topup": update["topup_credits"] = net
    if delta:
        update["billing_adjustment_outbox"] = {"_id": "stripe:financial:" + key + ":" + str(sequence),
            "type": "billing_financial_adjustment", "user_id": uid, "env": env, "ts": observed,
            "data": {"version": VERSION, "kind": tx["kind"], "offset_cents": delta, "currency": "usd",
                     "state_sha256": digest(state), "source_event": source_event,
                     "recognition_basis": "current verified provider state at reconciliation"}}
    recorded = await db.payment_transactions.update_one({"_id": tx["_id"], "billing_reconciliation.id": token,
        "billing_reconciliation_sequence": sequence}, {"$set": update})
    if not recorded.matched_count: blocked("payment lease changed; readback required")
    await flush_outbox(db, {**tx, **update})
    review_key = digest({"transaction": str(tx.get("_id"))})
    if pending_cents:
        await review_required(db, tx, "pending_dispute_legacy_attribution_review" if legacy_pending_refill else "pending_dispute_credit_hold", env=env,
                              charge_id=state["charge_id"], payment_id=payment_id, sequence=sequence, token=token)
    else:
        await db.closed_membership_revenue.update_one({"_id": anonymous_review_id("stripe:review:" + review_key, env),
            "data.reconciliation_sequence": sequence, "data.reconciliation_token": token},
            {"$set": {"data.status": "resolved"}, "$unset": {"pending_months": ""}})
    if state["disputes"]:
        outcome = "pending" if pending_cents else "lost" if any(d["status"] == "lost" for d in state["disputes"]) else "won"
        await billing_risk.observe(db, uid, env, "payment_dispute", payment_id, outcome, sequence=sequence)
        if tx["kind"] == "topup":
            await billing_risk.observe(db, uid, env, "unattributed_payment_dispute", payment_id,
                                       "pending" if legacy_pending_refill else "won", sequence=sequence)
    if claimed_for_risk and loss:
        await billing_risk.observe(db, uid, env, "reversed_after_usage", payment_id, "reversed", sequence=sequence)


async def paid_refill_review(db, tx, state, sequence, token):
    await db.payment_transactions.update_one({"_id": tx["_id"], "billing_reconciliation.id": token,
        "billing_reconciliation_sequence": sequence}, {"$set": {"payment_status": "paid", "payment_verified": True,
            "status": "billing_review", "credit_delivery_status": "review_required",
            "credit_delivery_reason": "previous_refund_adjusted_used_credits", "payment_verified_at": current_time(),
            "stripe_charge_id": state["charge_id"], "stripe_payment_intent_id": state["payment_intent_id"]}})


async def resolve_transaction(db, obj):
    """Resolve exact payment references; never guess a customer from content."""
    charge_id = obj.get("id") if obj.get("object") == "charge" else stripe_plan.resource_id(obj.get("charge"))
    payment_id = stripe_plan.resource_id(obj.get("payment_intent"))
    refs = [value for value in (charge_id, payment_id) if value]
    clauses = [{"stripe_charge_id": charge_id}] if charge_id else []
    if payment_id: clauses.append({"stripe_payment_intent_id": payment_id})
    if refs: clauses.append({"balance_source_ids": {"$in": refs}})
    rows = await db.payment_transactions.find({"kind": {"$in": list(KINDS)}, "$or": clauses}).limit(2).to_list(2) if clauses else []
    if len(rows) > 1: blocked("ambiguous payment transaction links")
    if rows: return rows[0], charge_id, payment_id
    # A signed early refill event can precede local fulfillment. Bind it to the
    # recorded checkout and verify its PaymentIntent before applying anything.
    meta = obj.get("metadata") or {}
    if meta.get("lookup_key") == membership_plans.TOPUP["lookup_key"] and meta.get("topup_attempt"):
        tx = await db.payment_transactions.find_one({"kind": "topup", "user_id": meta.get("user_id"),
                                                    "checkout_attempt_id": meta["topup_attempt"]})
        if tx:
            session = await asyncio.to_thread(stripe.checkout.Session.retrieve, tx["session_id"])
            if payment_id and stripe_plan.resource_id(session.get("payment_intent")) == payment_id:
                if __package__:
                    from . import stripe_refills
                else:
                    import stripe_refills
                await asyncio.to_thread(stripe_refills.verified_pack, tx, session)
                return tx, charge_id, payment_id
            blocked("early refill payment is not yet linked")
        blocked("early refill transaction is not yet registered")
    # Foreign/legacy payments are explicit. Do not infer a subscription period
    # or owner from a charge amount, current plan, email, or unverified metadata.
    return None, charge_id, payment_id


async def handle_event(db, obj, *, env, source_event):
    try:
        tx, charge_id, payment_id = await resolve_transaction(db, obj)
    except HTTPException:
        if (obj.get("metadata") or {}).get("lookup_key") not in PERSONAL_KEYS:
            raise
        charge_id = obj.get("id") if obj.get("object") == "charge" else stripe_plan.resource_id(obj.get("charge"))
        payment_id = stripe_plan.resource_id(obj.get("payment_intent"))
        placeholder = {"_id": "unlinked:" + digest({"charge": charge_id, "payment": payment_id}), "user_id": None}
        await review_required(db, placeholder, "unlinked_personal_payment_no_allocation", env=env,
                              charge_id=charge_id, payment_id=payment_id)
        owner = await db.users.find_one({"user_id": (obj.get("metadata") or {}).get("user_id"),
            "env": {"$in": [None, env]}, "closing": {"$ne": True}}, {"_id": 1})
        if owner: raise
        return {"status": "unlinked_payment_not_applied", "review_required": True, "source_event": source_event}
    if tx is None:
        owned = await personal_ownership(db, obj, env)
        if owned:
            placeholder = {"_id": "unlinked:" + digest({"charge": charge_id, "payment": payment_id}), "user_id": None}
            await review_required(db, placeholder, "unlinked_personal_payment_no_allocation", env=env,
                                  charge_id=charge_id, payment_id=payment_id)
            return {"status": "unlinked_payment_not_applied", "review_required": True, "source_event": source_event}
        return {"status": "unlinked_payment_not_applied", "source_event": source_event}
    return await reconcile(db, tx, env=env, charge_id=charge_id, payment_id=payment_id, source_event=source_event)


async def personal_ownership(db, obj, env):
    """Positive app ownership only; an unrelated Stripe charge cannot taint net."""
    lookup = (obj.get("metadata") or {}).get("lookup_key")
    if lookup is not None:
        return lookup in PERSONAL_KEYS
    payment_id = stripe_plan.resource_id(obj.get("payment_intent"))
    charge_id = obj.get("id") if obj.get("object") == "charge" else stripe_plan.resource_id(obj.get("charge"))
    def inspect():
        charge = stripe.Charge.retrieve(charge_id) if charge_id else {}
        key = (charge.get("metadata") or {}).get("lookup_key")
        if key is not None: return key in PERSONAL_KEYS, []
        pi = payment_id or stripe_plan.resource_id(charge.get("payment_intent"))
        if not pi: return False, []
        page = stripe.InvoicePayment.list(payment={"type": "payment_intent", "payment_intent": pi}, status="paid", limit=2)
        if page.get("has_more"): return False, []
        invoices = sorted({stripe_plan.resource_id(p.get("invoice")) for p in page.get("data", [])
            if p.get("status") == "paid" and stripe_plan.resource_id((p.get("payment") or {}).get("payment_intent")) == pi
            and stripe_plan.resource_id(p.get("invoice"))})
        for invoice_id in invoices:
            if not invoice_id: continue
            invoice = stripe.Invoice.retrieve(invoice_id)
            details = (invoice.get("parent") or {}).get("subscription_details") or invoice.get("subscription_details") or {}
            if (details.get("metadata") or {}).get("lookup_key") in PERSONAL_KEYS:
                return True, invoices
        return False, invoices
    try:
        owned, invoices = await asyncio.wait_for(asyncio.to_thread(inspect), 30)
    except (stripe.StripeError, TimeoutError):
        blocked("payment ownership lookup unavailable")
    if owned: return True
    # Existing anonymous closure receipts can establish ownership without
    # recovering a deleted account or retaining private answers/policy data.
    identifiers = [hashlib.sha256(f"{env}/{kind}/{invoice}".encode()).hexdigest()
                   for invoice in invoices if invoice for kind in ("renewal", "upgrade")]
    return bool(identifiers and await db.closed_membership_revenue.find_one({"env": env, "_id": {"$in": identifiers}}, {"_id": 1}))


async def unresolved_reviews(db, env, start=None):
    query = {"env": env, "kind": "unresolved_adjustment", "data.status": {"$ne": "resolved"},
             "data.reason": {"$ne": "pending_dispute_no_credit_reversal"}}
    if start is not None:
        query["$or"] = [{"pending_months": {"$in": [start.isoformat(), "unknown"]}},
            {"pending_months": None, "month": {"$in": [start.isoformat(), "unknown"]}}]
    return await db.closed_membership_revenue.count_documents(query)


async def global_reviews(db, env, limit=50):
    rows = await db.closed_membership_revenue.find({"env": env, "kind": "unresolved_adjustment", "data.status": {"$ne": "resolved"}},
        {"month": 1, "data.reason": 1}).sort([("month", -1), ("_id", -1)]).limit(limit + 1).to_list(limit + 1)
    return {"items": [{"id": str(row["_id"]), "user_id": None,
        "reason": row.get("data", {}).get("reason") or "anonymous_closed_account_adjustment_unresolved",
        "charge_id": None, "payment_intent_id": None, "opened_at": None, "accounting_month": row["month"]}
        for row in rows[:limit]], "truncated": len(rows) > limit}


async def member_records(db, uid, env, in_env, limit=25):
    if not await db.users.find_one({**in_env, "user_id": uid}, {"_id": 1}):
        raise HTTPException(404, "Member account not found.")
    fields = {k: 1 for k in ("kind", "status", "payment_status", "created_at", "fulfilled_at", "currency", "amount",
        "subscription_revenue_cents", "billing_adjustment", "purchase_policy", "billing_basis_status", "billing_basis", "topup_billing_basis",
        "credit_delivery_status", "payment_verified")}
    rows = await db.payment_transactions.find({"user_id": uid, "env": {"$in": [None, env]}}, fields).sort(
        [("created_at", -1), ("_id", -1)]).limit(limit + 1).to_list(limit + 1)
    marker_ids = {anonymous_review_id("stripe:review:" + digest({"transaction": str(row["_id"])}), env): str(row["_id"])
                  for row in rows[:limit]}
    reviews = await db.closed_membership_revenue.find({"env": env, "_id": {"$in": list(marker_ids)},
        "kind": "unresolved_adjustment", "data.status": {"$ne": "resolved"}}, {"data.reason": 1, "month": 1}).limit(50).to_list(50)
    pending = {marker_ids[r["_id"]] for r in reviews}
    items = []
    for row in rows[:limit]:
        from decimal import Decimal, InvalidOperation
        try:
            gross = int(Decimal(str(row.get("amount", 0))) * 100)
        except (ValueError, TypeError, InvalidOperation, OverflowError):
            gross = None
        adjustment = row.get("billing_adjustment") or {}
        captured = (adjustment.get("state") or {}).get("gross_cents")
        allocation = row.get("billing_basis")
        topup_basis = row.get("topup_billing_basis")
        if type(captured) is int and captured > 0:
            gross = captured
        elif validate_basis(allocation):
            gross = allocation["gross_cents"]
        elif isinstance(topup_basis, dict) and topup_basis.get("version") == VERSION and type(topup_basis.get("gross_cents")) is int:
            gross = topup_basis["gross_cents"]
        elif row.get("billing_basis_status") == "fully_credited_invoice_no_cash_charge":
            gross = 0
        else:
            gross = None
        original = row.get("subscription_revenue_cents")
        if row.get("kind") == "topup" and isinstance(topup_basis, dict) and topup_basis.get("version") == VERSION:
            original = membership_plans.TOPUP["price_cents"]
        original = original if type(original) is int and original >= 0 else None
        offset = adjustment.get("revenue_offset_cents")
        policy = row.get("purchase_policy")
        policy = ({key: policy[key] for key in ("version", "sha256", "text", "accepted_at", "offer", "source") if key in policy}
                  if isinstance(policy, dict) else None)
        recorded = (isinstance(policy, dict) and set(policy) == {"version", "sha256", "text", "accepted_at", "offer", "source"}
            and all(isinstance(policy[k], str) and policy[k] for k in policy)
            and hashlib.sha256(policy["text"].encode()).hexdigest() == policy["sha256"]
            and policy["source"] == "authenticated-plans-form")
        if not recorded: policy = None
        pending_review = str(row["_id"]) in pending
        items.append({"id": str(row["_id"]), **{k: row.get(k) for k in ("kind", "status", "payment_status", "created_at", "fulfilled_at", "currency")},
            "original_amount_cents": gross, "original_revenue_cents": original,
            "refund_dispute_offset_cents": offset, "net_revenue_cents": original - offset if not pending_review and type(original) is int and type(offset) is int else None,
            "net_credits": adjustment.get("net_credits"), "adjustment_observed_at": adjustment.get("observed_at"),
            "credit_delivery_status": row.get("credit_delivery_status"), "payment_verified": row.get("payment_verified") is True,
            "adjustment_status": "review_required" if pending_review else "verified" if adjustment else "not_recorded",
            "purchase_policy": policy, "policy_recorded": recorded})
    return {"user_id": uid, "items": items,
            "pending_reviews": [{"id": str(r["_id"]), "reason": r.get("data", {}).get("reason"),
                                 "opened_at": None, "accounting_month": r["month"]} for r in reviews],
            "truncated": len(rows) > limit}
