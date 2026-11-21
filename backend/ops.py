"""Bound DeepSeek usage across workers and run authenticated operational checks."""
import asyncio
import hmac
import logging
import os
import time
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from weakref import WeakKeyDictionary

import httpx
import stripe
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError, PyMongoError

try:
    from . import ai_meter, intro_access, message_costs, stripe_membership, stripe_partner_prices, stripe_plan
    from .postgres_store import RepositoryUnavailable
except ImportError:
    import ai_meter, intro_access, message_costs, stripe_membership, stripe_partner_prices, stripe_plan
    from postgres_store import RepositoryUnavailable


request_budget_db = ContextVar("request_budget_db", default=None)
member_id = ContextVar("member_ai_budget_id", default=None)
log = logging.getLogger(__name__)
READINESS_TIMEOUT_SECONDS = 3
_READINESS_PROBES = WeakKeyDictionary()


class _ReadinessProbe:
    def __init__(self, db):
        async def ping():
            # Preserve command implementations that return an awaitable Future.
            return await db.command("ping")

        self.db = db
        self.task = asyncio.create_task(ping())
        self.failure_logged = False

    def log_failure(self, reason):
        if not self.failure_logged:
            # Never include connection details or the database exception.
            log.warning("Database readiness probe %s; service remains unavailable.", reason)
            self.failure_logged = True


def _readiness_probe(db):
    loop = asyncio.get_running_loop()
    probes = _READINESS_PROBES.setdefault(loop, {})
    key = id(db)
    probe = probes.get(key)
    if probe is not None and probe.db is db and not probe.task.done():
        return probe
    probe = _ReadinessProbe(db)
    probes[key] = probe

    def completed(task):
        # A timed-out or cancelled HTTP waiter never owns cancellation of the
        # database operation. Consume late failures and free its single slot.
        try:
            if task.exception() is not None:
                probe.log_failure("failed")
        except asyncio.CancelledError:
            pass
        if probes.get(key) is probe:
            del probes[key]
        if not probes and _READINESS_PROBES.get(loop) is probes:
            del _READINESS_PROBES[loop]

    probe.task.add_done_callback(completed)
    return probe


def billing_enabled():
    mode = os.getenv("PERSONAL_BILLING_MODE")
    if mode is None:
        # Existing sandboxes default to enabled. Live deployments require explicit opt-in.
        live_clerk = os.getenv("CLERK_PUBLISHABLE_KEY", "").startswith("pk_live_")
        live_stripe = os.getenv("STRIPE_SECRET_KEY", "").startswith(("sk_live_", "rk_live_"))
        return not (live_clerk or live_stripe)
    return mode.strip().lower() == "enabled"


class MemberContext:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        token = member_id.set(None)
        pool_token = message_costs.current_pool.set(None)
        try:
            await self.app(scope, receive, send)
        finally:
            member_id.reset(token)
            message_costs.current_pool.reset(pool_token)


async def reserve_member_call(db, uid):
    try:
        limit = int(os.getenv("AI_MEMBER_HOURLY_CALL_LIMIT", "60"))
    except ValueError:
        limit = 0
    now = datetime.now(timezone.utc)
    if limit <= 0:
        raise HTTPException(429, "AI requests are temporarily unavailable.")
    try:
        bucket = uid + ":" + now.strftime("%Y%m%d%H")
        await db.ai_request_usage.update_one({"_id": bucket, "calls": {"$lt": limit}},
            {"$inc": {"calls": 1}, "$setOnInsert": {"user_id": uid, "expires_at": now + timedelta(days=2)}}, upsert=True)
    except DuplicateKeyError:
        raise HTTPException(429, "You have reached the hourly AI request limit. Please try again next hour.") from None
    return bucket


def daily_token_limit():
    try:
        limit = int(os.environ.get("AI_DAILY_TOKEN_LIMIT", "1000000"))
    except ValueError:
        limit = 0
    return max(limit, 0)


def day_key():
    return datetime.now(timezone.utc).date().isoformat()


async def reserve_tokens(db, system, prompt, image, max_output, *, model="deepseek-flash"):
    # UTF-8 bytes conservatively bound text tokens. DeepSeek Flash documents at most
    # 1024 tokens per inline image. Include chat framing and the entire possible reply.
    amount = len(system.encode()) + len(prompt.encode()) + 128 + (1024 if image else 0) + max_output
    limit = daily_token_limit()
    if amount > limit:
        raise HTTPException(503, "The daily AI allowance is currently unavailable.")
    uid = member_id.get()
    hourly_bucket = await reserve_member_call(db, uid) if uid else None
    key = day_key()
    coll = db.ai_daily_usage
    try:
        await coll.update_one({"_id": key}, {"$setOnInsert": {"tokens": 0,
            "expires_at": datetime.now(timezone.utc) + timedelta(days=32)}}, upsert=True)
    except DuplicateKeyError:
        pass
    result = await coll.update_one({"_id": key, "tokens": {"$lte": limit - amount}},
                                   {"$inc": {"tokens": amount, "requests": 1}})
    if not result.modified_count:
        if hourly_bucket:
            await db.ai_request_usage.update_one({"_id": hourly_bucket, "calls": {"$gt": 0}}, {"$inc": {"calls": -1}})
        raise HTTPException(503, "The daily AI allowance has been reached. Please try again tomorrow.")
    reservation = (key, amount, hourly_bucket)
    if message_costs.current_pool.get() == "intro":
        try:
            intro = await intro_access.reserve_budget(db, amount, model=model)
        except Exception:
            await release_undispatched(db, reservation)
            raise
        return (*reservation, intro)
    return reservation


async def release_undispatched(db, reservation):
    # Called only when the pre-dispatch ledger write failed and no HTTP call
    # was made. A transport error has a different, unknown billing outcome.
    key, reserved, hourly_bucket = reservation[:3]
    await db.ai_daily_usage.update_one({"_id": key, "tokens": {"$gte": reserved}, "requests": {"$gt": 0}},
                                       {"$inc": {"tokens": -reserved, "requests": -1}})
    if hourly_bucket:
        await db.ai_request_usage.update_one({"_id": hourly_bucket, "calls": {"$gt": 0}}, {"$inc": {"calls": -1}})
    if len(reservation) > 3 and reservation[3] is not None:
        await intro_access.release_undispatched(db, reservation[3])


async def settle_tokens(db, reservation, usage):
    # Keep unconfirmed reservations conservative. Save measured counts on the
    # original UTC day even if a reply or a JSON retry finishes after midnight.
    if not isinstance(usage, dict):
        return
    counts = ai_meter.token_counts(usage)
    if counts is not None:
        inputs, outputs = counts
        actual = inputs + outputs
        measured = {"usage_reports": 1, "input_tokens": inputs, "output_tokens": outputs}
    else:
        actual = ai_meter.total_only(usage)
        if actual is None:
            return
        measured = {"total_only_reports": 1, "total_only_tokens": actual}
    key, reserved = reservation[:2]
    await db.ai_daily_usage.update_one({"_id": key}, {"$inc": {"tokens": actual - reserved, **measured}})


async def readiness(db):
    probe = _readiness_probe(db)
    try:
        # PostgreSQL deliberately finishes an admitted transaction after caller
        # cancellation. wait_for would therefore exceed its advertised timeout.
        # Keep one actual ping per database/loop and bound only the HTTP wait;
        # a pending probe is never a cached success or another queued DB call.
        done, _ = await asyncio.wait((probe.task,), timeout=READINESS_TIMEOUT_SECONDS)
        if not done:
            probe.log_failure("timed out")
            raise HTTPException(503, "Service starting or temporarily unavailable.")
        probe.task.result()
    except (TimeoutError, PyMongoError, RepositoryUnavailable):
        probe.log_failure("failed")
        raise HTTPException(503, "Service starting or temporarily unavailable.") from None


def authorize(header):
    secret = os.environ.get("OPS_CHECK_SECRET", "")
    if not secret or not hmac.compare_digest(header.encode(), ("Bearer " + secret).encode()):
        raise HTTPException(401, "Unauthorized.")


async def check_ai():
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return False
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get("https://api.deepseek.com/user/balance", headers={"Authorization": f"Bearer {key}"})
            return response.status_code == 200 and response.json().get("is_available") is True
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


def check_stripe():
    if not (stripe.api_key and os.getenv("STRIPE_WEBHOOK_SECRET") and os.getenv("STRIPE_PORTAL_CONFIGURATION")):
        return False
    try:
        stripe_plan.checkout_mode()
        prices = stripe_membership.catalog()
        return all(prices.values()) and all((prices[key].get("metadata") or {}).get("upgrade_portal") for key in ("plus_250", "plus_500"))
    except (stripe.StripeError, ValueError):
        return False


def recent_invoices(since, until):
    events = stripe.Event.list(type="invoice.paid", created={"gte": since, "lte": until}, limit=100)
    return [event["data"]["object"] for event in events.auto_paging_iter()]


def paid_monthly_upgrade(invoice, subscription):
    prices = stripe_membership.catalog()
    return any(prices[key] and stripe_plan.paid_period(invoice, subscription, prices[key]["id"])
               for key in ("plus_250", "plus_500"))


async def invoices_recorded(db):
    # Re-scan the overlap after an interrupted check. Give normal Stripe delivery 15 minutes.
    now = int(time.time())
    until = now - 900
    last = await db.ops_state.find_one({"_id": "stripe_invoice_cursor"}) or {}
    since = max(now - 29 * 86400, last.get("until", now - 7 * 86400) - 3600)
    try:
        invoices = await asyncio.to_thread(recent_invoices, since, until)
    except stripe.StripeError:
        return False
    for invoice in invoices:
        reason = invoice.get("billing_reason")
        # A zero-total invoice cannot establish a paid period in paid_plan_charge.
        if type(invoice.get("total")) is int and invoice["total"] <= 0:
            continue
        if reason not in ("subscription_create", "subscription_cycle", "subscription_update"):
            continue
        sub = stripe_plan.invoice_subscription(invoice)
        if not sub:
            continue
        details = (invoice.get("parent") or {}).get("subscription_details") or invoice.get("subscription_details") or {}
        meta = details.get("metadata") or {}
        fields = {"user_id": 1, "annual_agreement": 1}
        owner = await db.users.find_one({"stripe_subscription_id": sub}, fields)
        if not owner:
            # Replaced subscriptions may still have a late paid renewal. Look up the
            # original purchase, just as the renewal webhook does, while the member exists.
            purchase = await db.payment_transactions.find_one(
                {"stripe_subscription_id": sub, "kind": "purchase", "payment_status": "paid"}, {"user_id": 1})
            if purchase:
                owner = await db.users.find_one({"user_id": purchase["user_id"]}, fields)
        if not owner and meta.get("lookup_key") in (*stripe_membership.ALL_KEYS, "plus_monthly", *stripe_partner_prices.LOOKUP_KEYS.values()) and meta.get("user_id"):
            owner = await db.users.find_one({"user_id": meta["user_id"]}, fields)
        if not owner:
            continue
        query = {"user_id": owner["user_id"], "stripe_subscription_id": sub, "payment_status": "paid"}
        if reason == "subscription_update":
            agreement = owner.get("annual_agreement") or {}
            annual = agreement.get("subscription") == sub and await asyncio.to_thread(
                stripe_plan.paid_period, invoice, sub, agreement.get("annual_price"))
            if not annual:
                try:
                    if not await asyncio.to_thread(paid_monthly_upgrade, invoice, sub):
                        continue
                except (stripe.StripeError, ValueError):
                    return False
        if reason in ("subscription_cycle", "subscription_update"):
            query["invoice_id"] = invoice["id"]
        else:
            query["kind"] = "purchase"
        if not await db.payment_transactions.find_one(query, {"_id": 1}):
            return False
    await db.ops_state.update_one({"_id": "stripe_invoice_cursor"}, {"$max": {"until": until}}, upsert=True)
    return True


async def check_operations(db):
    await readiness(db)
    ai_ok = await check_ai()
    usage = await db.ai_daily_usage.find_one({"_id": day_key()}) or {}
    limit = daily_token_limit()
    usage_ok = limit > 0 and usage.get("tokens", 0) < limit * 0.9
    checks = {"deepseek": ai_ok, "ai_budget": usage_ok}
    if billing_enabled():
        checks["stripe"] = await asyncio.to_thread(check_stripe)
        if checks["stripe"]:
            checks["stripe_invoices"] = await invoices_recorded(db)
    return checks


CHECK_ALERTS = {"partner_checkouts": "An unused partner checkout could not be reconciled with Stripe.",
                "stripe_invoices": "Stripe has a paid Personal invoice without a matching local payment, or its event feed is unavailable.",
                "deepseek": "DeepSeek could not confirm an available balance.",
                "stripe": "The Stripe billing configuration or plan catalog needs attention.",
                "ai_budget": "The daily AI token allowance has reached its warning threshold."}
