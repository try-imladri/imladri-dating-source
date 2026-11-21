"""Content-free receipts for each hosted DeepSeek completion attempt."""
import asyncio
import hashlib
import hmac
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException
from pymongo import IndexModel
from pymongo.errors import DuplicateKeyError, PyMongoError

try:
    from . import ai_meter
except ImportError:
    import ai_meter


INDEXES = {"ai_call_ledger": [
    IndexModel([("environment", 1), ("key_id", 1), ("billing_at", 1)], name="provider_statement_window"),
    IndexModel([("environment", 1), ("message_id", 1)], name="message_attempts",
               partialFilterExpression={"message_id": {"$type": "string"}}),
    IndexModel([("environment", 1), ("preparation_id", 1)], name="preparation_attempts",
               partialFilterExpression={"preparation_id": {"$type": "string"}}),
]}
current_message = ContextVar("ai_cost_message", default=None)
current_run = ContextVar("ai_cost_message_run", default=None)
current_pool = ContextVar("ai_cost_credit_pool", default=None)
current_kind = ContextVar("ai_cost_message_kind", default=None)
_preparation = ContextVar("ai_cost_preparation", default=None)
_generation = ContextVar("ai_cost_generation", default=None)
_last_response_attempt = ContextVar("ai_last_response_attempt", default=None)
_COUPLES_REJECTIONS = frozenset(("tone_status", "tone_drafts", "tone_length", "tone_intent"))
_INDIVIDUAL_REJECTIONS = frozenset(("reply_tone_shape", "reply_style_shape", "reply_style_duplicate", "reply_intent"))
_REJECTION_CONTEXTS = {"couples_tone": ("couples", _COUPLES_REJECTIONS), "six_part": ("individual", _INDIVIDUAL_REJECTIONS)}
# Retain the exported receipt allowlist name for historical and current Admin details.
TONE_REJECTIONS = _COUPLES_REJECTIONS | _INDIVIDUAL_REJECTIONS
TONE_REJECTIONS = TONE_REJECTIONS | {"quick_advice_shape"}
log = logging.getLogger(__name__)


class TrackingUnavailable(HTTPException):
    def __init__(self):
        super().__init__(503, "AI usage tracking is temporarily unavailable.")


@contextmanager
def message_context(message_id, workspace, *, run_number=1, pool=None, kind=None):
    if workspace not in ("individual", "couples") or not message_id:
        raise ValueError("Expected a message and its workspace.")
    if run_number is not None and (type(run_number) is not int or run_number < 1):
        raise ValueError("Expected a positive message run.")
    if pool not in (None, "intro", "plan", "refill", "complimentary"):
        raise ValueError("Expected a known credit pool.")
    token = current_message.set((message_id, workspace))
    run_token = current_run.set(run_number)
    pool_token = current_pool.set(pool)
    kind_token = current_kind.set(kind)
    attempt_token = _last_response_attempt.set(None)
    try:
        yield
    finally:
        _last_response_attempt.reset(attempt_token)
        current_run.reset(run_token)
        current_pool.reset(pool_token)
        current_kind.reset(kind_token)
        current_message.reset(token)


@contextmanager
def preparation_context(preparation_id):
    token = _preparation.set(preparation_id)
    try:
        yield
    finally:
        _preparation.reset(token)


@contextmanager
def generation_context(environment, feature):
    # A validator may annotate only the generation that just produced its result.
    _last_response_attempt.set(None)
    token = _generation.set((environment, feature, str(uuid.uuid4())))
    try:
        yield
    finally:
        _generation.reset(token)


def key_id(credential=None):
    key = (os.getenv("DEEPSEEK_API_KEY", "") if credential is None else credential).strip()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24] if key else None


def checked_key(credential):
    fingerprint = key_id(credential)
    pinned = os.getenv("DEEPSEEK_PRODUCTION_KEY_ID", "").strip()
    generation = _generation.get()
    if pinned and (not fingerprint or not hmac.compare_digest(pinned, fingerprint)
                   or not generation or generation[0] != "live"):
        raise HTTPException(503, "The production AI connection requires verification.")
    dedicated = bool(pinned and os.getenv("DEEPSEEK_PRODUCTION_KEY_NAME", "").strip())
    return fingerprint, dedicated


class ProviderResult:
    def __init__(self):
        self.status = "invalid_response"
        self.envelope = {}
        self.http_status = None

    def fields(self, started_at):
        finished_at = datetime.now(timezone.utc)
        data = {"status": self.status, "finished_at": finished_at}
        if self.http_status is not None:
            data["http_status"] = self.http_status
        model = self.envelope.get("model")
        if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,100}", model):
            data["model"] = model
        created = self.envelope.get("created")
        # The signed provider response supplies UTC epoch seconds. Ignore invalid
        # clocks for attribution, including implausibly old dates from fixtures.
        if type(created) is int and started_at.timestamp() - 300 <= created <= finished_at.timestamp() + 300:
            data["provider_created_at"] = datetime.fromtimestamp(created, timezone.utc)
        stamp = data.get("provider_created_at", started_at)
        data["billing_at"] = stamp
        hour = lambda at: at.replace(minute=0, second=0, microsecond=0)
        data["billing_hour_certain"] = "provider_created_at" in data or (
            hour(started_at - timedelta(minutes=5)) == hour(finished_at + timedelta(minutes=5)))
        measured = ai_meter.usage_fields(self.envelope.get("usage"), data.get("model"))
        # With no verified Chinese holiday calendar a weekday peak window can
        # have either rate. Nights and weekends are always off-peak.
        if ("high_nusd" in measured and data["billing_hour_certain"]
                and (stamp.weekday() >= 5 or stamp.hour not in (1, 2, 3, 6, 7, 8, 9))):
            measured["high_nusd"] //= 2
        data.update(measured)
        provider_id = self.envelope.get("id")
        if isinstance(provider_id, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", provider_id):
            data["provider_ref"] = hashlib.sha256(provider_id.encode("utf-8")).hexdigest()
        return data


async def insert_attempt(collection, row):
    # Reuse the dispatch ID when an acknowledgement is lost. Never send a
    # completion unless its ledger entry has been acknowledged by storage.
    for attempt in range(2):
        try:
            await collection.insert_one(row)
            return
        except DuplicateKeyError:
            if attempt:
                return
            raise
        except PyMongoError:
            if attempt:
                raise TrackingUnavailable() from None


async def finish_attempt(collection, call_id, data):
    for attempt in range(2):
        try:
            await collection.update_one({"_id": call_id, "status": "pending"}, {"$set": data})
            return
        except PyMongoError:
            if attempt:
                # Pending rows force incomplete reconciliation without charging
                # a second generation or discarding a member's completed reply.
                log.warning("DeepSeek call receipt is awaiting reconciliation")


async def mark_response_rejection(db, message_id, reason):
    target = _last_response_attempt.get()
    context = _REJECTION_CONTEXTS.get(target.get("feature")) if target else None
    if (target and target.get("feature") == "six_part" and reason == "quick_advice_shape"
            and current_kind.get() in ("quick_advice", "couples_quick_advice")):
        current = current_message.get()
        if current and current[1] in ("individual", "couples"):
            context = (current[1], {"quick_advice_shape"})
    if (context is None or reason not in context[1] or current_message.get() != (message_id, context[0])
            or target["message_id"] != message_id or target["message_run"] != current_run.get()
            or ai_meter.execution.get() != "metered"):
        return
    _last_response_attempt.set(None)
    try:
        await db.ai_call_ledger.update_one({**target, "application_rejection": {"$exists": False}},
                                           {"$set": {"application_rejection": reason}})
    except PyMongoError:
        log.warning("Could not record the response validation outcome")


@asynccontextmanager
async def provider_call(db, identity, requested_model, retry):
    result = ProviderResult()
    generation = _generation.get()
    if generation is None:
        yield result
        return
    if db is None:
        raise TrackingUnavailable()
    env, feature, generation_id = generation
    message_id, workspace = current_message.get() or (None, None)
    fingerprint, dedicated = identity
    started_at = datetime.now(timezone.utc)
    call_id = str(uuid.uuid4())
    collection = db.ai_call_ledger
    await insert_attempt(collection, {
        "_id": call_id, "environment": env, "key_id": fingerprint, "dedicated_key": dedicated,
        "message_id": message_id, "message_run": current_run.get(), "workspace": workspace,
        "credit_pool": current_pool.get(),
        "preparation_id": _preparation.get(),
        "generation_id": generation_id, "feature": feature, "mode": ai_meter.execution.get(), "retry": retry,
        "started_at": started_at, "billing_at": started_at, "status": "pending", "requested_model": requested_model,
    })
    try:
        yield result
    except httpx.TimeoutException:
        result.status = "timeout"
        raise
    except httpx.RequestError:
        result.status = "transport_error"
        raise
    except asyncio.CancelledError:
        result.status = "cancelled"
        raise
    finally:
        await finish_attempt(collection, call_id, result.fields(started_at))
        context = _REJECTION_CONTEXTS.get(feature)
        if feature == "six_part" and workspace == "couples" and current_kind.get() == "couples_quick_advice":
            context = ("couples", {"quick_advice_shape"})
        if (context and workspace == context[0] and current_run.get() is not None
                and ai_meter.execution.get() == "metered" and result.status == "succeeded"):
            _last_response_attempt.set({"_id": call_id, "generation_id": generation_id, "environment": env,
                                      "message_id": message_id, "message_run": current_run.get(),
                                      "workspace": workspace, "feature": feature, "mode": "metered", "status": "succeeded"})
