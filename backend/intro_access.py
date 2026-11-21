"""One-time introductory access, minimal abuse records and bounded provider spend.

Identity keys are keyed digests, never addresses, IPs or questionnaire data. They
expire after one year; this limits retention and does not establish one human per
trial. The member's original grant remains immutable for the account lifetime.
"""
import asyncio
import hashlib
import hmac
import ipaddress
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from pymongo import IndexModel, ReturnDocument
from pymongo.errors import DuplicateKeyError

try:
    from . import ai_meter, billing_risk, billing_acceptance
except ImportError:
    import ai_meter, billing_risk, billing_acceptance


VERSION = "introductory-replies-v1"
LIMIT = 10
RETENTION_DAYS = 365
ACTION = "intro-access"
PERMANENT_DENIAL_REASONS = frozenset({"identity_precedes_offer", "blocked_email_domain", "previous_membership", "repeat_email_identity"})


class PermanentIntroIneligible(HTTPException):
    """Only server-verified permanent intro exclusions may open paid checkout."""
    def __init__(self, reason, detail):
        if reason not in PERMANENT_DENIAL_REASONS:
            raise ValueError("Unknown permanent introductory exclusion")
        self.reason = reason
        super().__init__(403, detail)
REVIEW_REASONS = {"repeat_identity": "Repeated introductory identity",
    "shared_device_grants": "Multiple introductions on a shared device",
    "shared_device_velocity": "Unusual introductory claim frequency"}
INDEXES = {
    "intro_identities": [IndexModel("expires_at", expireAfterSeconds=0, name="intro_identity_retention")],
    "intro_attempt_usage": [IndexModel("expires_at", expireAfterSeconds=0, name="intro_attempt_retention")],
    "ai_intro_daily_usage": [IndexModel("expires_at", expireAfterSeconds=0, name="intro_budget_retention")],
}


def now():
    return datetime.now(timezone.utc)


def configured():
    return (os.getenv("INTRO_ACCESS_ENABLED", "").lower() in ("1", "true")
            and len(os.getenv("INTRO_ABUSE_HMAC_SECRET", "").encode()) >= 32
            and bool(os.getenv("INTRO_TURNSTILE_SITE_KEY", "").strip())
            and bool(os.getenv("INTRO_TURNSTILE_SECRET_KEY", "").strip())
            and bool(allowed_hosts()) and eligible_since() is not None)


def eligible_since():
    try:
        value = datetime.fromisoformat(os.environ["INTRO_ELIGIBLE_SINCE"].replace("Z", "+00:00"))
        return value.astimezone(timezone.utc) if value.tzinfo else None
    except (KeyError, ValueError):
        return None


def allowed_hosts():
    return {x.strip().lower() for x in os.getenv("INTRO_ALLOWED_HOSTNAMES", "").split(",") if x.strip()}


def digest(kind, value):
    secret = os.getenv("INTRO_ABUSE_HMAC_SECRET", "")
    if len(secret.encode()) < 32:
        raise HTTPException(503, "Introductory access is temporarily unavailable.")
    return hmac.new(secret.encode(), (kind + ":" + value).encode(), hashlib.sha256).hexdigest()


def normalized_email(email):
    """Only collapse aliases where the provider defines mailbox equivalence."""
    if not isinstance(email, str) or len(email) > 320 or email.count("@") != 1:
        raise HTTPException(403, "A verified email address is required.")
    local, domain = email.strip().rsplit("@", 1)
    domain = domain.lower()
    if not local or not domain or any(char.isspace() for char in local + domain):
        raise HTTPException(403, "A verified email address is required.")
    if domain in ("gmail.com", "googlemail.com"):
        domain, local = "gmail.com", local.split("+", 1)[0].replace(".", "").lower()
    # Other providers may have case-sensitive or significant dot/plus mailboxes.
    return local + "@" + domain


def verified_identity(clerk, uid):
    if not isinstance(clerk, dict) or clerk.get("id") != uid or clerk.get("banned") or clerk.get("locked"):
        raise HTTPException(403, "This account is not eligible for introductory access.")
    primary = clerk.get("primary_email_address_id")
    for address in clerk.get("email_addresses") or []:
        if (isinstance(address, dict) and address.get("id") == primary
                and (address.get("verification") or {}).get("status") == "verified"):
            return normalized_email(address.get("email_address"))
    raise HTTPException(403, "Verify your primary email address before starting your ten messages.")


def entitlement(member):
    value = member.get("intro_entitlement")
    if (member.get("role") != "member" or member.get("closing") or not isinstance(value, dict)
            or set(value) != {"version", "id", "active", "granted_at"} or value.get("version") != VERSION
            or value.get("active") is not True or not isinstance(value.get("id"), str)):
        return None
    try:
        if str(uuid.UUID(value["id"])) != value["id"]:
            return None
        stamp = datetime.fromisoformat(value["granted_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    return value


def totals(member):
    source = member.get("intro_reply_totals", {})
    if not isinstance(source, dict):
        return LIMIT, LIMIT
    claimed, used = source.get("claimed", 0), source.get("used", 0)
    if any(type(n) is not int or not 0 <= n <= LIMIT for n in (claimed, used)) or used > claimed:
        return LIMIT, LIMIT
    return claimed, used


def access(member):
    # Retaining a completed grant does not create a permanent free feature tier.
    try:
        from . import intro_card
    except ImportError:
        import intro_card
    return bool(entitlement(member) and intro_card.active(member) and not converted(member)
                and totals(member)[1] < LIMIT and not member.get("intro_access_paused"))


def converted(member):
    """A checkout/customer/subscription ID alone never proves a paid conversion."""
    if member.get("intro_converted_at"):
        return True
    start, end = member.get("subscription_period_start"), member.get("subscription_period_end")
    return bool(member.get("stripe_subscription_id") and member.get("tier") == "paid"
                and member.get("subscription_status") not in ("incomplete", "incomplete_expired", "unpaid")
                and type(start) is int and type(end) is int and 0 < start < end)


def eligibility(member):
    if (member.get("role") != "member" or member.get("closing") or member.get("replies_paused")
            or member.get("intro_access_paused") or converted(member) or member.get("complimentary_entitlement")
            or member.get("paid_reply_cycles") or member.get("topup_grants") or member.get("intro_entitlement") is not None):
        return False
    try:
        created = datetime.fromisoformat(member["created_at"].replace("Z", "+00:00"))
        return bool(created.tzinfo and eligible_since() and created >= eligible_since())
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


async def settings(db):
    row = await db.intro_controls.find_one({"_id": "global"}) or {}
    paused = row.get("paused") is True
    return {"paused": paused, "grants_paused": paused, "generations_paused": paused}


async def status(db, member):
    try:
        from . import intro_card
    except ImportError:
        import intro_card
    enabled = configured() and intro_card.configured() and billing_acceptance.intro_allowed(member)
    controls = await settings(db)
    grant = entitlement(member)
    claimed, used = totals(member) if grant else (0, 0)
    has_billing = converted(member) or bool(member.get("complimentary_entitlement"))
    state = ("converted" if grant and has_billing else "exhausted" if grant and used >= LIMIT
             else "paused" if controls["paused"] or member.get("intro_access_paused") or member.get("replies_paused")
             else "active" if access(member) else "eligible" if intro_card.eligible(member) and enabled else "ineligible")
    if not enabled and not grant:
        state = "unavailable"
    return {"enabled": enabled, "state": state,
            "can_claim": bool(enabled and not controls["paused"] and intro_card.eligible(member)),
            "can_setup": bool(enabled and not controls["paused"] and intro_card.eligible(member)),
            "messages_granted": LIMIT, "messages_used": used,
            "messages_remaining": max(0, LIMIT - claimed) if access(member) and not has_billing else 0,
            "challenge_required": True, "turnstile_site_key": os.getenv("INTRO_TURNSTILE_SITE_KEY", "") if enabled else "",
            "challenge": {"required": True, "site_key": os.getenv("INTRO_TURNSTILE_SITE_KEY", "") if enabled else "", "action": ACTION},
            "retention_days": RETENTION_DAYS, "quotes": await intro_card.quotes(db, member), **intro_card.status_fields(member)}


async def public_status(db):
    try:
        from . import intro_card
    except ImportError:
        import intro_card
    enabled = (not billing_acceptance.requested() and configured() and intro_card.configured()
               and not (await settings(db))["paused"])
    return {"enabled": enabled, "messages": LIMIT if enabled else 0, "messages_granted": LIMIT if enabled else 0,
            "requires_payment_method": True, "automatic_charge": True, "authorization_version": intro_card.VERSION}


async def validate_challenge(token):
    if not isinstance(token, str) or not 1 <= len(token) <= 2048:
        raise HTTPException(403, "Complete the sign-up protection check and try again.")
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False) as client:
            response = await client.post("https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={"secret": os.environ["INTRO_TURNSTILE_SECRET_KEY"], "response": token})
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(503, "The sign-up protection check is temporarily unavailable.") from None
    if (not isinstance(result, dict) or result.get("success") is not True or result.get("action") != ACTION
            or not isinstance(result.get("hostname"), str) or result["hostname"].lower() not in allowed_hosts()):
        raise HTTPException(403, "Complete the sign-up protection check and try again.")


def network_signal(request):
    """Trust only the edge-overwritten IP on the authenticated gateway hop."""
    if request is None:
        return None
    secret = os.getenv("RENDER_ORIGIN_SECRET", "")
    origins = request.headers.getlist("x-imladri-origin")
    ips = request.headers.getlist("x-imladri-client-ip")
    if (not secret or len(origins) != 1 or len(ips) != 1
            or not hmac.compare_digest(origins[0].encode(), secret.encode())):
        return None
    try:
        address = ipaddress.ip_address(ips[0])
        network = ipaddress.ip_network(f"{address}/{24 if address.version == 4 else 64}", strict=False)
        return digest("network", str(network))
    except ValueError:
        return None


async def velocity(db, key, limit, *, period="hour", retention=2):
    stamp = now()
    bucket = key + ":" + stamp.strftime("%Y%m%d%H" if period == "hour" else "%Y%m%d")
    try:
        await db.intro_attempt_usage.update_one({"_id": bucket, "attempts": {"$lt": limit}},
            {"$inc": {"attempts": 1}, "$setOnInsert": {"expires_at": stamp + timedelta(days=retention)}}, upsert=True)
    except DuplicateKeyError:
        raise HTTPException(429, "Too many introductory-access attempts. Please try again later.",
                            headers={"Retry-After": "3600"}) from None


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_member: str = Field(min_length=1, max_length=200)
    challenge_token: str | None = Field(default=None, max_length=2048)
    device_token: str | None = Field(default=None, max_length=64)


class Controls(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    paused: StrictBool


async def prepare_claim(db, member, clerk, body, request=None, *, allow_legacy=False):
    billing_acceptance.require_intro(member)
    uid = member["user_id"]
    if body.expected_member != uid:
        raise HTTPException(409, "The signed-in account changed. Reload before starting.")
    current = await db.users.find_one({"user_id": uid})
    if not current:
        raise HTTPException(404, "Account not found.")
    billing_risk.require_action(current, "new_intro")
    legacy = entitlement(current)
    if legacy and (not allow_legacy or totals(current)[1] >= LIMIT or converted(current)):
        raise HTTPException(403, "This account cannot receive another introductory allowance.")
    if not configured() or (await settings(db))["paused"]:
        raise HTTPException(503, "Introductory access is temporarily unavailable.")
    if not eligibility(current) and not (allow_legacy and legacy and current.get("role") == "member"
            and not current.get("closing") and not current.get("replies_paused") and not current.get("intro_access_paused")
            and not converted(current) and not current.get("complimentary_entitlement")):
        raise HTTPException(403, "This account is not eligible for introductory access.")
    email = verified_identity(clerk, uid)
    blocked = {"mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "yopmail.com", "maildrop.cc"}
    blocked.update(x.strip().lower() for x in os.getenv("INTRO_BLOCKED_EMAIL_DOMAINS", "").split(",") if x.strip())
    if email.rsplit("@", 1)[1] in blocked:
        raise PermanentIntroIneligible("blocked_email_domain", "Use a permanent verified email address to start your introduction.")
    try:
        created = datetime.fromtimestamp(clerk["created_at"] / 1000, timezone.utc)
        if type(clerk["created_at"]) is not int:
            raise ValueError()
    except (ValueError, TypeError, KeyError, OverflowError):
        raise HTTPException(403, "This introduction is available to new verified accounts.") from None
    if created < eligible_since():
        raise PermanentIntroIneligible("identity_precedes_offer", "This introduction is available to new verified accounts.")
    # Closed/rejoined accounts and previously funded memberships never gain a new grant.
    identity = hashlib.sha256(("imladri-personal:" + uid).encode()).hexdigest()
    if await db.membership_history.find_one({"_id": identity}):
        raise PermanentIntroIneligible("previous_membership", "This account is not eligible for another introduction.")
    user_key = digest("user", uid)
    await velocity(db, "claim:" + user_key, 6)
    await validate_challenge(body.challenge_token)
    # Vendor replay detection can be eventually consistent. A local unique
    # claim also prevents one validated token racing across different users.
    challenge_key = "challenge:" + digest("challenge", body.challenge_token)
    try:
        await db.intro_attempt_usage.update_one({"_id": challenge_key}, {"$setOnInsert": {
            "owner": user_key, "expires_at": now() + timedelta(minutes=6)}}, upsert=True)
    except DuplicateKeyError:
        pass
    challenge = await db.intro_attempt_usage.find_one({"_id": challenge_key})
    if not challenge or challenge.get("owner") != user_key:
        raise HTTPException(403, "This protection check was already used. Complete a new check.") from None
    await velocity(db, "new-grants", 200)
    flags = []
    network = network_signal(request)
    if network is not None:
        await velocity(db, "network:" + network, 10)
    if body.device_token:
        try:
            device = str(uuid.UUID(body.device_token))
            if device != body.device_token:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, "Invalid introduction device token.") from None
        device_id = "device:" + digest("device", device)
        # A shared device is a review signal, never sufficient to reject a couple.
        row = await db.intro_attempt_usage.find_one({"_id": device_id + ":" + now().strftime("%Y%m%d")}) or {}
        if row.get("attempts", 0) >= 2:
            flags.append("shared_device_grants")
        try:
            await velocity(db, device_id, 10000, period="day")
        except HTTPException:
            flags.append("shared_device_velocity")
    stamp = now()
    key = digest("email", email)
    grant = {"version": VERSION, "id": str(uuid.uuid4()), "active": True, "granted_at": stamp.isoformat()}
    await db.intro_identities.update_one({"_id": key}, {"$setOnInsert": {"owner": user_key,
        "grant": grant, "issued_at": stamp, "expires_at": stamp + timedelta(days=RETENTION_DAYS)}}, upsert=True)
    reserved = await db.intro_identities.find_one({"_id": key})
    if (not isinstance(reserved, dict) or not isinstance(reserved.get("owner"), str)
            or len(reserved["owner"]) != 64 or any(char not in "0123456789abcdef" for char in reserved["owner"])
            or not entitlement({"role": "member", "intro_entitlement": reserved.get("grant")})):
        raise HTTPException(503, "The introductory identity reservation requires reconciliation.")
    if reserved["owner"] != user_key:
        await db.users.update_one({"user_id": uid}, {"$addToSet": {"intro_risk_flags": "repeat_identity"}})
        await billing_risk.observe(db, uid, current.get("env") or "preview", "identity_conflict",
                                   "email:" + digest("challenge", body.challenge_token), "conflict")
        raise PermanentIntroIneligible("repeat_email_identity", "An introduction has already been used for this verified email.")
    grant = reserved["grant"]
    if legacy and grant != legacy:
        raise HTTPException(409, "Your earlier introductory identity requires reconciliation; no new allowance was granted.")
    # Identity reservation is not a product entitlement. Only verified hosted
    # card setup plus a NEW explicit agreement may later install this grant.
    return {"grant": grant, "flags": flags, "identity": key, "owner": user_key}


async def claim(db, member, clerk, body, request=None):
    raise HTTPException(409, "Select a plan and complete verified card setup before introductory messages.")


def token_limit():
    try:
        return max(0, int(os.getenv("INTRO_DAILY_TOKEN_LIMIT", "250000")))
    except ValueError:
        return 0


def spend_limit():
    try:
        return max(0, int(os.getenv("INTRO_DAILY_BUDGET_CENTS", "100"))) * 10000000
    except ValueError:
        return 0


async def reserve_budget(db, amount, *, model):
    """Conservative spend ceiling includes failures, retries and background work."""
    if not configured() or (await settings(db))["paused"]:
        raise HTTPException(503, "Introductory replies are temporarily unavailable.")
    rates = ai_meter.OFF_PEAK_RATES.get(model.lower()) if isinstance(model, str) else None
    if rates is None:
        raise HTTPException(503, "Introductory AI pricing requires verification.")
    # Charge every possible token at the larger peak input/output rate. Keep
    # uncertain/failed dispatches reserved; this is a ceiling, not an invoice.
    ceiling = amount * 2 * max(rates)
    limit, money = token_limit(), spend_limit()
    if amount > limit or ceiling > money:
        raise HTTPException(503, "Today's introductory AI allowance is unavailable.")
    stamp = now()
    key = stamp.date().isoformat()
    try:
        await db.ai_intro_daily_usage.update_one({"_id": key}, {"$setOnInsert": {
            "tokens": 0, "spend_reserved_nusd": 0, "attempts": 0,
            "expires_at": stamp + timedelta(days=92)}}, upsert=True)
    except DuplicateKeyError:
        pass
    row = await db.ai_intro_daily_usage.update_one({"_id": key, "tokens": {"$lte": limit - amount},
        "spend_reserved_nusd": {"$lte": money - ceiling}},
        {"$inc": {"tokens": amount, "spend_reserved_nusd": ceiling, "attempts": 1}})
    if not row.modified_count:
        raise HTTPException(503, "Today's introductory AI allowance has been reached. Please try again tomorrow.")
    return key, amount, ceiling


async def release_undispatched(db, reservation):
    key, amount, ceiling = reservation
    await db.ai_intro_daily_usage.update_one({"_id": key, "tokens": {"$gte": amount},
        "spend_reserved_nusd": {"$gte": ceiling}, "attempts": {"$gt": 0}},
        {"$inc": {"tokens": -amount, "spend_reserved_nusd": -ceiling, "attempts": -1}})


async def summary(db, env):
    try:
        from . import intro_card
    except ImportError:
        import intro_card
    controls = await settings(db)
    query = {"env": {"$in": [env, None]}, "role": "member", "intro_entitlement.version": VERSION}
    counts = {"granted_accounts": 0, "active_accounts": 0, "exhausted_accounts": 0,
              "used_messages": 0, "converted_accounts": 0, "suspected_accounts": 0}
    async for row in db.users.find(query, {"user_id": 1, "role": 1, "closing": 1, "intro_entitlement": 1,
        "intro_billing": 1,
        "intro_reply_totals": 1, "intro_access_paused": 1, "intro_risk_flags": 1, "stripe_subscription_id": 1,
        "tier": 1, "subscription_period_start": 1, "subscription_period_end": 1, "subscription_status": 1, "intro_converted_at": 1}):
        if not entitlement(row):
            continue
        counts["granted_accounts"] += 1
        _, used = totals(row)
        counts["used_messages"] += used
        counts["exhausted_accounts"] += int(used >= LIMIT)
        counts["active_accounts"] += int(access(row))
        counts["converted_accounts"] += int(converted(row))
        counts["suspected_accounts"] += int(bool(row.get("intro_risk_flags")))
    counts["suspected_accounts"] = await db.users.count_documents({"env": {"$in": [env, None]},
        "role": "member", "intro_risk_flags.0": {"$exists": True}})
    flagged = await db.users.find({"env": {"$in": [env, None]}, "role": "member",
        "intro_risk_flags.0": {"$exists": True}}, {"_id": 0, "user_id": 1, "nickname": 1,
        "intro_risk_flags": 1}).sort("user_id", 1).limit(21).to_list(21)
    review_accounts = [{"user_id": row["user_id"], "nickname": row.get("nickname") or "",
        "reasons": [REVIEW_REASONS[flag] for flag in dict.fromkeys(
            flag for flag in (row.get("intro_risk_flags") or []) if isinstance(flag, str))
                    if flag in REVIEW_REASONS] or ["Introductory access needs review"]} for row in flagged[:20]
                       if isinstance(row.get("user_id"), str) and row["user_id"]]
    stamp = now()
    today = await db.ai_intro_daily_usage.find_one({"_id": stamp.date().isoformat()}) or {}
    costs = {"attempts": 0, "low_nusd": 0, "high_nusd": 0, "unknown": 0}
    async for call in db.ai_call_ledger.find({"environment": env, "credit_pool": "intro", "billing_at": {
        "$gte": stamp - timedelta(days=31)}}, {"low_nusd": 1, "high_nusd": 1}):
        costs["attempts"] += 1
        if all(type(call.get(field)) is int for field in ("low_nusd", "high_nusd")):
            costs["low_nusd"] += call["low_nusd"]
            costs["high_nusd"] += call["high_nusd"]
        else:
            costs["unknown"] += 1
    billing_states = {state: 0 for state in sorted(intro_card.STATES)}
    async for record in db.users.find({"env": {"$in": [env, None]}, "role": "member", "closing": {"$ne": True},
        "intro_billing.version": intro_card.VERSION}, {"intro_billing.state": 1}):
        state = (record.get("intro_billing") or {}).get("state")
        if isinstance(state, str) and state in billing_states:
            billing_states[state] += 1
    return {"enabled": configured() and intro_card.configured(), "settings": controls, "totals": counts,
        "card_first": {"authorization_version": intro_card.VERSION, "states": billing_states,
            "denominator": "retained_nonclosing_member_agreements", "zero_invoices_grant_paid_messages": False},
        "review_accounts": review_accounts, "review_accounts_more": len(flagged) > 20,
        "today": {"day": stamp.date().isoformat(), "tokens_reserved": today.get("tokens", 0),
            "token_limit": token_limit(), "spend_reserved_nusd": today.get("spend_reserved_nusd", 0),
            "spend_limit_nusd": spend_limit(), "attempts": today.get("attempts", 0)},
        "costs": {**costs, "window_days": 31}, "retention_days": RETENTION_DAYS,
        "denominator": "currently_retained_member_accounts"}


def make_router(get_db, current_member, admin_dep, clerk_fetch, refresh=None):
    router = APIRouter()

    @router.get("/public/intro-access")
    async def public_availability(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return await public_status(get_db())

    @router.get("/me/intro-access")
    async def own_status(response: Response, member=Depends(current_member)):
        response.headers["Cache-Control"] = "no-store"
        if refresh:
            try:
                member = await refresh(member)
            except HTTPException as exc:
                if exc.status_code not in (409, 503):
                    raise
        return await status(get_db(), member)

    @router.post("/me/intro-access/claim")
    async def own_claim(body: Claim, request: Request, response: Response, member=Depends(current_member)):
        response.headers["Cache-Control"] = "no-store"
        # The obsolete no-card route must never fetch identity or grant access.
        return await claim(get_db(), member, None, body, request)

    @router.get("/admin/intro-access/summary")
    async def admin_summary(response: Response, member=Depends(admin_dep)):
        response.headers["Cache-Control"] = "no-store"
        return await summary(get_db(), member["env"])

    @router.patch("/admin/intro-access/settings")
    async def admin_settings(body: Controls, response: Response, member=Depends(admin_dep)):
        response.headers["Cache-Control"] = "no-store"
        await get_db().intro_controls.update_one({"_id": "global"}, {"$set": {"paused": body.paused,
            "changed_at": now(), "actor": member["user_id"]}}, upsert=True)
        return await summary(get_db(), member["env"])

    return router
