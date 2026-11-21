"""Staff membership status and current-month subscription revenue after discounts, excluding sales tax."""
import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
import stripe

if __package__:
    from . import couples, free_plan, membership_plans, report_dates
else:
    import couples, free_plan, membership_plans, report_dates


STATUSES = ("active", "past_due", "expired", "cancelled", "unsubscribed", "unverified", "staff", "closing", "complimentary", "intro", "intro_exhausted")
PAID_USD = {"kind": {"$in": ["purchase", "renewal", "upgrade"]}, "payment_status": "paid", "currency": "usd"}
RECEIPT_FIELDS = {field: 1 for field in (
    "kind", "session_id", "invoice_id", "created_at", "fulfilled_at", "subscription_revenue_cents", "amount", "env")}
ACTION_LIMIT = 50


def action_push(action, actor):
    item = {"action": action, "actor": actor, "at": datetime.now(timezone.utc).isoformat()}
    return {"admin_member_actions": {"$each": [item], "$slice": -ACTION_LIMIT}}


async def set_access_flag(db, query, field, value, action, actor):
    # The predicate and append share one atomic update. A repeated request is a successful no-op.
    changed = await db.users.update_one({**query, field: {"$ne": True} if value else True},
        {"$set": {field: value}, "$push": action_push(action, actor)})
    return bool(changed.matched_count or await db.users.find_one(
        {**query, field: True if value else {"$ne": True}}, {"_id": 1}))


def recent_actions(person):
    actions = list(person.get("admin_member_actions") or [])
    for field, action in (("reply_access_admin_change", "reply_access_changed"), ("couples_pilot_admin_change", "enrollment_changed")):
        legacy = person.get(field)
        if isinstance(legacy, dict) and legacy.get("at") and (legacy.get("actor") or legacy.get("by")):
            actions.append({"action": action, "at": legacy["at"], "actor": legacy.get("actor") or legacy["by"]})
    return sorted(actions, key=lambda row: row["at"], reverse=True)[:ACTION_LIMIT]


class ReplyAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paused: StrictBool


class ComplimentaryAccess(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_member: str = Field(min_length=1, max_length=200)
    request_id: str
    enabled: StrictBool
    expected_entitlement_id: str | None = None
    reason: str = Field(min_length=1, max_length=300)

    @field_validator("request_id", "expected_entitlement_id")
    @classmethod
    def canonical_id(cls, value):
        if value is not None and str(UUID(value)) != value:
            raise ValueError("Use a canonical UUID.")
        return value


async def set_complimentary_access(db, query, uid, body, actor):
    if body.expected_member != uid:
        raise HTTPException(409, "The selected account changed. Reload before continuing.")
    scope = {**query, "user_id": uid, "role": "member", "closing": {"$ne": True}}
    fields = ("user_id", "role", "closing", "complimentary_entitlement", "stripe_subscription_id",
              "checkout_attempt", "topup_checkout_attempt")
    row = await db.users.find_one(scope, {key: 1 for key in fields})
    if row is None:
        raise HTTPException(404, "Open ordinary member account not found.")
    existing = row.get("complimentary_entitlement")
    if existing is not None and (not isinstance(existing, dict) or set(existing) != {"version", "id", "active"}
            or existing.get("version") != free_plan.COMPLIMENTARY_VERSION or type(existing.get("active")) is not bool
            or not isinstance(existing.get("id"), str)):
        raise HTTPException(409, "Review the existing complimentary entitlement before changing it.")
    if existing is not None:
        try:
            valid = str(UUID(existing["id"])) == existing["id"]
        except ValueError:
            valid = False
        if not valid:
            raise HTTPException(409, "Review the existing complimentary entitlement before changing it.")
    if body.enabled:
        if row.get("stripe_subscription_id") or row.get("checkout_attempt") or row.get("topup_checkout_attempt"):
            raise HTTPException(409, "Review existing subscription or checkout activity before granting complimentary access.")
        if existing and existing.get("id") == body.request_id:
            if existing["active"]:
                return {"user_id": uid, "complimentary": True, "entitlement": existing, "duplicate": True}
            raise HTTPException(409, "That grant was revoked. Use a new request to grant access again.")
        if existing and existing["active"]:
            raise HTTPException(409, "A different complimentary grant is already active.")
        desired = {"version": free_plan.COMPLIMENTARY_VERSION, "id": body.request_id, "active": True}
    else:
        if existing is None:
            return {"user_id": uid, "complimentary": False, "entitlement": None, "duplicate": True}
        if body.expected_entitlement_id != existing.get("id"):
            raise HTTPException(409, "The complimentary grant changed. Reload before revoking it.")
        if existing["active"] is False:
            return {"user_id": uid, "complimentary": False, "entitlement": existing, "duplicate": True}
        desired = {**existing, "active": False}
    guards = []
    for key in ("complimentary_entitlement", "stripe_subscription_id", "checkout_attempt", "topup_checkout_attempt"):
        guards.append({"$eq": [{"$type": "$" + key}, "missing"]} if key not in row
                      else {"$eq": ["$" + key, {"$literal": row[key]}]})
    action = "complimentary_unlimited_granted" if body.enabled else "complimentary_unlimited_revoked"
    record = {"action": action, "actor": actor, "at": datetime.now(timezone.utc).isoformat(),
              "entitlement_id": desired["id"], "request_id": body.request_id, "reason": body.reason}
    saved = await db.users.update_one({**scope, "$expr": {"$and": guards}},
        {"$set": {"complimentary_entitlement": desired}, "$push": {"admin_member_actions": {"$each": [record], "$slice": -ACTION_LIMIT}}})
    if not saved.matched_count:
        raise HTTPException(409, "Account access or billing changed. Reload before continuing.")
    return {"user_id": uid, "complimentary": body.enabled, "entitlement": desired, "duplicate": False}


def membership_status(member, usage, timestamp):
    if member.get("closing"):
        return "closing"
    if usage.get("plan") == "complimentary":
        return "complimentary"
    if usage["unlimited"]:
        return "staff"
    if usage.get("plan") == "intro":
        return "intro" if usage.get("messages_remaining", 0) > 0 else "intro_exhausted"
    status = member.get("subscription_status")
    if status in ("cancelled", "canceled"):
        return "cancelled"
    if status == "past_due":
        return "past_due"
    if usage["plan"] == "plus":
        return "active"
    if member.get("tier") == "paid" or member.get("stripe_subscription_id"):
        end = member.get("subscription_period_end")
        return "expired" if isinstance(end, int) and 0 < end <= timestamp else "unverified"
    return "unsubscribed"


def paid_receipt(row, env):
    # Old rows only have their saved transaction amount. New rows isolate the paid plan's ex-tax amount.
    checkout = row["kind"] in ("purchase", "topup")
    paid_at = (row.get("fulfilled_at") if checkout else None) or row.get("created_at")
    if not isinstance(paid_at, (datetime, str)):
        return None
    try:
        moment = datetime.fromisoformat(paid_at) if isinstance(paid_at, str) else paid_at
        month = moment.replace(tzinfo=moment.tzinfo or timezone.utc).astimezone(timezone.utc).date().replace(day=1)
    except (ValueError, OverflowError):
        return None
    cents = row.get("subscription_revenue_cents")
    if cents is None:
        try:
            cents = int((Decimal(str(row.get("amount"))) * 100).to_integral_value())
        except (InvalidOperation, ValueError, OverflowError):
            return None
    if type(cents) is not int or cents < 0:
        return None
    reference = row.get("session_id" if checkout else "invoice_id") or str(row["_id"])
    key = hashlib.sha256(f"{env}/{row['kind']}/{reference}".encode()).hexdigest()
    receipt = {"_id": key, "env": env, "month": month.isoformat(), "cents": cents}
    if row["kind"] == "topup":
        receipt["kind"] = "topup"
    return receipt


async def retain_closed_revenue(db, uid, env):
    if __package__:
        from . import billing_adjustments
    else:
        import billing_adjustments
    await billing_adjustments.flush_pending(db, env, uid)
    # Preserve anonymous financial totals before the normal account purge deletes payment history.
    # A retry after a partial closure writes the same keys. No owner, Stripe ID or contact details survive here.
    query = {**PAID_USD, "kind": {"$in": [*PAID_USD["kind"]["$in"], "topup"]}, "user_id": uid}
    async for row in db.payment_transactions.find(query, RECEIPT_FIELDS):
        receipt = paid_receipt(row, row.get("env") or env)
        if receipt:
            await db.closed_membership_revenue.update_one(
                {"_id": receipt["_id"]}, {"$setOnInsert": receipt}, upsert=True)
    async for event in db.events.find({"user_id": uid, "env": env, "type": "billing_financial_adjustment"},
                                     {"ts": 1, "data.kind": 1, "data.offset_cents": 1}):
        receipt = financial_receipt(event, env)
        if receipt:
            await db.closed_membership_revenue.update_one({"_id": receipt["_id"]}, {"$setOnInsert": receipt}, upsert=True)
    async for event in db.events.find({"user_id": uid, "env": env, "type": "billing_adjustment_review",
        "data.status": "pending", "data.reason": {"$ne": "pending_dispute_no_credit_reversal"}},
        {"ts": 1, "data.last_seen_at": 1}):
        stamp = (event.get("data") or {}).get("last_seen_at") or event.get("ts")
        try:
            at = datetime.fromisoformat(stamp) if isinstance(stamp, str) else stamp
            month = at.replace(tzinfo=at.tzinfo or timezone.utc).astimezone(timezone.utc).date().replace(day=1).isoformat()
        except (ValueError, TypeError, AttributeError, OverflowError):
            month = "unknown"
        # Retain uncertainty only: no account, charge, invoice, email, policy or
        # private response survives in this anonymous financial marker.
        marker = {"_id": billing_adjustments.anonymous_review_id(event["_id"], env),
                  "env": env, "month": month, "kind": "unresolved_adjustment"}
        await db.closed_membership_revenue.update_one({"_id": marker["_id"]}, {"$setOnInsert": marker}, upsert=True)


def financial_receipt(event, env):
    data = event.get("data") or {}
    cents, stamp = data.get("offset_cents"), event.get("ts")
    if type(cents) is not int or not isinstance(stamp, (str, datetime)):
        return None
    try:
        value = datetime.fromisoformat(stamp) if isinstance(stamp, str) else stamp
        month = value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).date().replace(day=1)
    except (ValueError, OverflowError):
        return None
    kind = "topup_adjustment" if data.get("kind") == "topup" else "subscription_adjustment"
    return {"_id": hashlib.sha256(f"{env}/adjustment/{event['_id']}".encode()).hexdigest(),
            "env": env, "month": month.isoformat(), "kind": kind, "cents": cents}


async def monthly_offsets(db, env, start, *, refills=False):
    # These are adjustments recognized when current Stripe state was verified,
    # not a fabricated assertion about when the customer's bank settled funds.
    if __package__:
        from . import billing_adjustments
    else:
        import billing_adjustments
    await billing_adjustments.flush_pending(db, env)
    month = start.isoformat()
    end = (start + timedelta(days=32)).replace(day=1)
    amounts = {}
    async for event in db.events.find({"env": env, "type": "billing_financial_adjustment",
                                     "$or": [{"ts": dates} for dates in report_dates.calendar_ranges(start, end)]},
                                     {"ts": 1, "data.kind": 1, "data.offset_cents": 1}):
        receipt = financial_receipt(event, env)
        if receipt and receipt["month"] == month and (receipt["kind"] == "topup_adjustment") == refills:
            amounts[receipt["_id"]] = receipt["cents"]
    kind = "topup_adjustment" if refills else "subscription_adjustment"
    async for receipt in db.closed_membership_revenue.find({"env": env, "month": month, "kind": kind}, {"cents": 1}):
        amounts[receipt["_id"]] = receipt["cents"]
    return sum(amounts.values())


async def monthly_payments(db, env, in_env, start, end, *, refills=False, gross=False):
    month = start.isoformat()
    checkout_kind = "topup" if refills else "purchase"
    candidates = []
    for dates in report_dates.calendar_ranges(start, end):
        candidates.extend([{"kind": checkout_kind, "fulfilled_at": dates},
                           {"kind": checkout_kind, "fulfilled_at": None, "created_at": dates}])
        if not refills:
            candidates.append({"kind": {"$in": ["renewal", "upgrade"]}, "created_at": dates})
    query = {**PAID_USD, "$or": candidates}
    if refills:
        query["kind"] = "topup"
    # Legacy payments predate transaction-level environment tags. Resolve those from their owning account.
    current = db.payment_transactions.aggregate([
        {"$match": query},
        {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "user_id",
                     "pipeline": [{"$match": in_env}, {"$project": {"_id": 1}}], "as": "owner"}},
        {"$match": {"$or": [{"env": env}, {"env": None, "owner.0": {"$exists": True}}]}},
        {"$project": RECEIPT_FIELDS},
    ])
    # Read open accounts first: closure archives before purging. A concurrent close can appear on both
    # sides but the same receipt key is counted exactly once.
    amounts = {}
    async for row in current:
        receipt = paid_receipt(row, env)
        if receipt and receipt["month"] == month:
            amounts[receipt["_id"]] = receipt["cents"]
    archive = {"env": env, "month": month, "kind": "topup" if refills else {"$in": [None, "subscription"]}}
    async for receipt in db.closed_membership_revenue.find(archive, {"cents": 1}):
        amounts[receipt["_id"]] = receipt["cents"]
    total = sum(amounts.values())
    if gross:
        return total
    offsets = await monthly_offsets(db, env, start, refills=refills)
    if __package__:
        from . import billing_adjustments
    else:
        import billing_adjustments
    return None if await billing_adjustments.unresolved_reviews(db, env, start) else total - offsets


async def monthly_summary_amounts(db, env, in_env, start, end):
    """Read both payment kinds together, retaining current-before-archive deduplication."""
    if __package__:
        from . import billing_adjustments
    else:
        import billing_adjustments
    # The standalone monthly helpers keep their flush contract. This combined
    # report drains the same pending financial records once before reading them.
    await billing_adjustments.flush_pending(db, env)
    month = start.isoformat()
    candidates = []
    for dates in report_dates.calendar_ranges(start, end):
        candidates.extend([
            {"kind": {"$in": ["purchase", "topup"]}, "fulfilled_at": dates},
            {"kind": {"$in": ["purchase", "topup"]}, "fulfilled_at": None, "created_at": dates},
            {"kind": {"$in": ["renewal", "upgrade"]}, "created_at": dates},
        ])
    query = {**PAID_USD, "kind": {"$in": [*PAID_USD["kind"]["$in"], "topup"]}, "$or": candidates}
    amounts = {kind: {} for kind in ("subscription", "topup", "subscription_adjustment", "topup_adjustment")}

    async def payments():
        current = db.payment_transactions.aggregate([
            {"$match": query},
            {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "user_id",
                         "pipeline": [{"$match": in_env}, {"$project": {"_id": 1}}], "as": "owner"}},
            {"$match": {"$or": [{"env": env}, {"env": None, "owner.0": {"$exists": True}}]}},
            {"$project": RECEIPT_FIELDS},
        ])
        async for row in current:
            receipt = paid_receipt(row, env)
            if receipt and receipt["month"] == month:
                amounts[receipt.get("kind", "subscription")][receipt["_id"]] = receipt["cents"]

    async def adjustments():
        async for event in db.events.find({"env": env, "type": "billing_financial_adjustment",
                "$or": [{"ts": dates} for dates in report_dates.calendar_ranges(start, end)]},
                {"ts": 1, "data.kind": 1, "data.offset_cents": 1}):
            receipt = financial_receipt(event, env)
            if receipt and receipt["month"] == month:
                amounts[receipt["kind"]][receipt["_id"]] = receipt["cents"]

    await asyncio.gather(payments(), adjustments())
    # Closure archives before purging live records. Reading all live receipts
    # first and then the archive preserves that order and counts each key once.
    async for receipt in db.closed_membership_revenue.find(
            {"env": env, "month": month, "kind": {"$in": [None, *amounts]}}, {"kind": 1, "cents": 1}):
        amounts[receipt.get("kind") or "subscription"][receipt["_id"]] = receipt["cents"]
    return {kind: sum(rows.values()) for kind, rows in amounts.items()}


async def summary(db, env, in_env, current):
    start = current.astimezone(timezone.utc).date().replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    total, amounts = await asyncio.gather(db.users.count_documents(in_env), monthly_summary_amounts(db, env, in_env, start, end))
    received, offsets = amounts["subscription"], amounts["subscription_adjustment"]
    refill_gross, refill_offsets = amounts["topup"], amounts["topup_adjustment"]
    if __package__:
        from . import billing_adjustments
    else:
        import billing_adjustments
    unresolved = await billing_adjustments.unresolved_reviews(db, env, start)
    return {"total_users": total, "monthly_revenue_cents": None if unresolved else received - offsets,
            "monthly_gross_revenue_cents": received, "monthly_refund_dispute_adjustments_cents": offsets,
            "monthly_net_revenue_cents": None if unresolved else received - offsets, "currency": "usd",
            "monthly_refill_gross_revenue_cents": refill_gross, "monthly_refill_adjustments_cents": refill_offsets,
            "monthly_refill_net_revenue_cents": None if unresolved else refill_gross - refill_offsets,
            "net_revenue_verified": unresolved == 0, "pending_financial_reviews": unresolved,
            "month_start": start.isoformat(), "month_end_exclusive": end.isoformat(), "environment": env}


async def entitlements(db, env, in_env, current):
    fields = dict.fromkeys(("user_id", "tier", "subscription_status", "stripe_subscription_id",
        "subscription_period_start", "subscription_period_end", "plan_offer", "membership_plan_id"), 1)
    eligible = {**in_env, "role": {"$ne":"admin"}, "closing": {"$ne":True}}
    paid = set()
    plans = {}
    async for row in db.users.find({**eligible, "tier":"paid"}, fields):
        if not row.get("user_id") or not free_plan.quota_period(row, current.timestamp()):
            continue
        paid.add(row["user_id"])
        key = row.get("plan_offer") if row.get("plan_offer") in ("couple_annual", "partner_monthly") else membership_plans.member_plan(row)["id"]
        plans[key] = plans.get(key, 0) + 1
    sponsored = set()
    seat_fields = dict.fromkeys(("user_id","couples_adult_at","about.demographics.age","couples_blocked"),1)
    async for pair in db.couples_pairs.aggregate([
        {"$match":{"env":env, "state":"active", "self_funded":{"$ne":True}}},
        {"$lookup":{"from":"users", "localField":"members", "foreignField":"user_id", "as":"accounts",
                    "pipeline":[{"$match":eligible}, {"$project":seat_fields}]}},
        {"$project":{"members":1,"sponsor":1,"accounts":1}},
    ]):
        seats = pair.get("members") or []
        sponsor = pair.get("sponsor")
        if len(seats) != 2 or sponsor not in seats or sponsor not in paid:
            continue
        guest = next((uid for uid in seats if uid != sponsor), None)
        people = {r["user_id"]:r for r in pair["accounts"]}
        partner, owner = people.get(guest), people.get(sponsor)
        if guest not in paid and couples.is_adult(partner) and couples.is_adult(owner) and not couples.incompatible(partner,owner):
            sponsored.add(guest)
    return {"paid": len(paid), "sponsored_only": len(sponsored), "by_plan": plans}


def stripe_balance(env, start, end):
    key = stripe.api_key or ""
    if not key.startswith(("sk_live_", "rk_live_") if env == "live" else ("sk_test_", "rk_test_")):
        return {"state": "unavailable"}
    totals = {}
    entries = []
    other_currency = 0
    request = {"created": {"gte": int(start.timestamp()), "lt": int(end.timestamp())}, "limit": 100, "expand": ["data.source"]}
    try:
        for _ in range(10):
            page = stripe.BalanceTransaction.list(**request)
            for row in page.data:
                if row["currency"] != "usd":
                    other_currency += 1
                    continue
                category = row.get("reporting_category") or row["type"]
                bucket = totals.setdefault(category, {"rows": 0, "amount": 0, "fee": 0, "net": 0})
                bucket["rows"] += 1
                for field in ("amount", "fee", "net"):
                    bucket[field] += row[field]
                source = row.get("source")
                references = set()
                if hasattr(source, "get"):
                    for field in ("id", "charge", "payment_intent", "invoice"):
                        ref = source.get(field)
                        if hasattr(ref, "get"):
                            ref = ref.get("id")
                        if isinstance(ref, str):
                            references.add(ref)
                elif isinstance(source, str):
                    references.add(source)
                entries.append((category, references, {field: row[field] for field in ("amount","fee","net")}))
            if not page.has_more:
                return {"state": "complete", "categories": totals, "other_currency": other_currency, "entries": entries}
            request["starting_after"] = page.data[-1]["id"]
    except stripe.StripeError:
        return {"state": "unavailable"}
    return {"state": "over_limit"}


def invoice_refs(invoice, session=None):
    refs = set()
    def add(item):
        ref = item.get("id") if hasattr(item, "get") else item
        if isinstance(ref, str) and ref.startswith(("in_", "pi_", "ch_", "py_")):
            refs.add(ref)
    if hasattr(invoice, "get"):
        for field in ("id", "payment_intent", "charge"):
            add(invoice.get(field))
        for row in (invoice.get("payments") or {}).get("data", []):
            payment = row.get("payment") or {}
            if row.get("status") == "paid":
                add(payment.get("payment_intent"))
                add(payment.get("charge"))
    if session:
        add(session.get("payment_intent"))
        add(session.get("invoice"))
    return sorted(refs)


async def attribute_balance(db, env, in_env, balance):
    entries = balance.pop("entries", [])
    refs = sorted({ref for _, references, _ in entries for ref in references})
    if any(ref.startswith(("pi_", "ch_", "py_")) for ref in refs):
        balance["invoice_link_status"] = await refresh_invoice_links(db, env, in_env)
    fields = ("stripe_charge_id", "stripe_payment_intent_id", "invoice_id", "balance_source_ids")
    known = {}
    if refs:
        source = db.payment_transactions.aggregate([
            {"$match": {"kind": {"$in": ["purchase","renewal","upgrade","topup"]}, "payment_status":"paid",
                        "$or":[{field: {"$in": refs}} for field in fields]}},
            {"$lookup":{"from":"users","localField":"user_id","foreignField":"user_id","as":"owner",
                        "pipeline":[{"$match":in_env},{"$project":{"_id":1}}]}},
            {"$match":{"$or":[{"env":env},{"env":None,"owner.0":{"$exists":True}}]}},
            {"$project":{**dict.fromkeys(fields,1),"kind":1}},
        ])
        for_match = set(refs)
        async for row in source:
            product = "refills" if row["kind"] == "topup" else "subscriptions"
            for field in fields:
                for ref in (row.get(field) or []) if field == "balance_source_ids" else [row.get(field)]:
                    if ref in for_match:
                        known.setdefault(ref,set()).add(product)
    products = {"subscriptions": {}, "refills": {}, "unlinked": {}}
    for category, references, amounts in entries:
        matches = {product for ref in references for product in known.get(ref, ())}
        product = matches.pop() if len(matches) == 1 else "unlinked"
        bucket = products[product].setdefault(category, {"rows":0,"amount":0,"fee":0,"net":0})
        bucket["rows"] += 1
        for field, value in amounts.items():
            bucket[field] += value
    balance["products"] = products
    balance["totals_by_product"] = {
        product: {field: sum(item[field] for item in categories.values()) for field in ("rows", "amount", "fee", "net")}
        for product, categories in products.items()
    }


async def refresh_invoice_links(db, env, in_env):
    # Recent Stripe versions omit payments on webhook invoices. Backfill app-owned invoices only.
    current = datetime.now(timezone.utc)
    unpaid_links = {"kind": {"$in": ["purchase", "renewal", "upgrade"]}, "payment_status": "paid",
        "invoice_links_checked": {"$ne": True}, "stripe_payment_intent_id": None, "stripe_charge_id": None,
        "balance_source_ids": {"$not": {"$regex": "^(pi_|ch_|py_)"}},
        "$or": [{"invoice_id": {"$regex": "^in_"}}, {"balance_source_ids": {"$regex": "^in_"}}]}
    rows = await db.payment_transactions.aggregate([
        {"$match": unpaid_links},
        {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "user_id", "as": "owner",
                     "pipeline": [{"$match": in_env}, {"$project": {"_id": 1}}]}},
        {"$match": {"$or": [{"env": env}, {"env": None, "owner.0": {"$exists": True}}]}},
        {"$sort": {"invoice_link_retry_at": 1, "created_at": -1, "_id": 1}}, {"$limit": 13},
        {"$project": {"invoice_id": 1, "balance_source_ids": 1, "invoice_link_retry_at": 1}}
    ]).to_list(13)
    def paid_links(invoice):
        payments = stripe.InvoicePayment.list(invoice=invoice, status="paid", limit=100)
        return invoice_refs({"payments": {"data": [p for p in payments.auto_paging_iter() if p.get("invoice") == invoice]}})

    async def refresh(row):
        if (row.get("invoice_link_retry_at") or "") > current.isoformat():
            return False
        invoice = next(ref for ref in [row.get("invoice_id"), *(row.get("balance_source_ids") or [])]
                       if isinstance(ref, str) and ref.startswith("in_"))
        try:
            links = await asyncio.to_thread(paid_links, invoice)
        except stripe.StripeError:
            links = []
        if not links:
            # An invoice webhook can precede the provider's payment reference becoming available.
            await db.payment_transactions.update_one({"_id": row["_id"], "invoice_links_checked": {"$ne": True}},
                {"$set": {"invoice_link_retry_at": (current + timedelta(hours=1)).isoformat()}})
            return False
        await db.payment_transactions.update_one({"_id": row["_id"]}, {
            "$addToSet": {"balance_source_ids": {"$each": links}}, "$set": {"invoice_links_checked": True},
            "$unset": {"invoice_link_retry_at": ""}})
        return True
    results = await asyncio.gather(*(refresh(row) for row in rows[:12]))
    return "complete" if len(rows) <= 12 and all(results) else "partial"


async def reconcile(db, env, in_env, current, year_month=None):
    current = current.astimezone(timezone.utc)
    start = datetime.strptime(year_month, "%Y-%m").replace(tzinfo=timezone.utc) if year_month else current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    snapshot, subscriptions, refills, balance = await asyncio.gather(entitlements(db, env, in_env, current),
        monthly_payments(db, env, in_env, start.date(), end.date()), monthly_payments(db, env, in_env, start.date(), end.date(), refills=True),
        asyncio.to_thread(stripe_balance, env, start, min(end, current)))
    if balance["state"] == "complete":
        await attribute_balance(db, env, in_env, balance)
    sub_gross, refill_gross = await asyncio.gather(
        monthly_payments(db, env, in_env, start.date(), end.date(), gross=True),
        monthly_payments(db, env, in_env, start.date(), end.date(), refills=True, gross=True))
    return {"as_of": current.isoformat(), "start": start.date().isoformat(), "end_exclusive": end.date().isoformat(),
            "environment": env, "entitlements": snapshot, "subscriptions": subscriptions, "refills": refills, "stripe": balance,
            "net_revenue_verified": subscriptions is not None and refills is not None,
            "revenue_breakdown": {"subscriptions": {"gross_cents": sub_gross, "net_cents": subscriptions},
                                  "refills": {"gross_cents": refill_gross, "net_cents": refills}},
            "adjustment_recognition": "UTC date current provider state was reconciled; original paid amounts retained"}
