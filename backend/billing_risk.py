"""Content-free billing review signals and explicit, scoped staff holds.

A refund request is never an abuse verdict. Provider-backed payment holds are
handled by billing_adjustments; this module never sends a financial request.
"""
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import IndexModel

VERSION = "billing-risk-v1"
SCOPES = ("new_ai", "new_intro", "new_purchase")
KINDS = ("payment_dispute", "identity_conflict", "reversed_after_usage", "unattributed_payment_dispute")
OUTCOMES = ("pending", "lost", "won", "conflict", "reversed", "restored")
INDEXES = {"users": [IndexModel([("env", 1), ("billing_risk.review_required", 1), ("user_id", 1)], name="billing_risk_reviews")]}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def profile(member):
    value = member.get("billing_risk")
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get("version") != VERSION or type(value.get("revision")) is not int
            or value["revision"] < 1 or not isinstance(value.get("id"), str)
            or not isinstance(value.get("signals"), dict) or len(value["signals"]) > 64
            or not isinstance(value.get("hold_scopes"), list) or any(s not in SCOPES for s in value["hold_scopes"])
            or type(value.get("review_required")) is not bool):
        raise HTTPException(503, "Billing review status needs reconciliation. Contact support.")
    return value


def require_action(member, scope):
    if scope not in SCOPES:
        raise ValueError("Unknown billing review scope")
    if member.get("role") == "admin":
        return
    row = profile(member)
    if row and scope in row["hold_scopes"]:
        raise HTTPException(403, "This new action is paused for billing review. Saved data and cancellation remain available.",
                            headers={"X-Imladri-Limit": "billing-review"})


def snapshot_guard(member):
    """Combine this expression with the caller's final single-user CAS."""
    return ({"$eq": ["$billing_risk", {"$literal": member["billing_risk"]}]}
            if "billing_risk" in member else {"$eq": [{"$type": "$billing_risk"}, "missing"]})


def guard_query(query, member, scope):
    require_action(member, scope)
    result = dict(query)
    expressions = ([result["$expr"]] if "$expr" in result else []) + [snapshot_guard(member)]
    result["$expr"] = {"$and": expressions}
    return result


def status(member):
    row = profile(member)
    pending = row.get("pending_payment_disputes", 0) if row else 0
    unattributed = counts(row)["attribution_reviews"] if row else 0
    overflow = bool(row and row.get("unresolved_payment_overflow"))
    return {"review_id": row["id"] if row else None, "revision": row["revision"] if row else 0,
            "hold_scopes": list(row["hold_scopes"]) if row else [],
            "requires_staff_review": bool(row and (row["review_required"] or pending or overflow)),
            "pending_payment_count": pending, "payment_holds_require_provider_resolution": pending > 0 or overflow,
            "pending_payment_disputes": pending, "temporary_payment_hold_count": max(0, pending - unattributed),
            "pending_count_is_lower_bound": overflow, "unresolved_payment_overflow": overflow,
            "payment_attribution_review_required": unattributed > 0,
            "actionable": bool(row and (row["review_required"] or row["hold_scopes"] or pending or overflow)),
            "type": "staff_review_hold" if row and row["hold_scopes"] else "payment_dispute_hold" if pending or overflow else "review" if row and row["review_required"] else "clear"}


def counts(row):
    signals = list(row["signals"].values())
    return {"unresolved_disputes": sum(s["kind"] == "payment_dispute" and s["outcome"] in ("pending", "lost") for s in signals),
            "identity_conflicts": sum(s["kind"] == "identity_conflict" for s in signals),
            "reversed_after_usage": sum(s["kind"] == "reversed_after_usage" and s["outcome"] == "reversed" for s in signals),
            "attribution_reviews": sum(s["kind"] == "unattributed_payment_dispute" and s["outcome"] == "pending" for s in signals)}


def signal_digest(signals):
    # A higher reconciliation watermark is not a new abuse allegation.
    return digest({key: {"kind": value["kind"], "outcome": value["outcome"]} for key, value in signals.items()})


def refill_available(record):
    if (not isinstance(record, dict) or set(record) != {"net_messages", "claimed_messages", "pending_messages"}
            or any(type(value) is not int or not 0 <= value <= 100 for value in record.values())):
        raise HTTPException(503, "Refill attribution needs reconciliation.")
    remaining = record["net_messages"] - record["claimed_messages"]
    return remaining - min(record["pending_messages"], max(0, remaining))


def refill_expression(prefix, *, net=None, pending=None, claimed=None):
    net = net if net is not None else {"$ifNull": ["$" + prefix + ".net_messages", 0]}
    pending = pending if pending is not None else {"$ifNull": ["$" + prefix + ".pending_messages", 0]}
    claimed = claimed if claimed is not None else {"$ifNull": ["$" + prefix + ".claimed_messages", 0]}
    remaining = {"$subtract": [net, claimed]}
    return {"$subtract": [remaining, {"$min": [pending, {"$max": [0, remaining]}]}]}


async def observe(db, uid, env, kind, source_id, outcome, *, sequence=0):
    if kind not in KINDS or outcome not in OUTCOMES or not isinstance(source_id, str) or not 1 <= len(source_id) <= 512:
        raise ValueError("Invalid content-free billing signal")
    if type(sequence) is not int or sequence < 0:
        raise ValueError("Invalid billing signal sequence")
    key = digest([env, uid, kind, source_id])
    for _ in range(4):
        member = await db.users.find_one({"user_id": uid, "role": "member", "env": {"$in": [None, env]}, "closing": {"$ne": True}})
        if not member:
            return None
        old = profile(member)
        previous = (old or {}).get("signals", {}).get(key)
        if previous:
            previous_sequence = previous.get("sequence", 0)
            if previous_sequence > sequence or (previous_sequence == sequence and previous["kind"] == kind and previous["outcome"] == outcome):
                return status(member)
            if previous_sequence == sequence and previous["outcome"] != outcome and sequence:
                raise HTTPException(503, "Billing signal requires current-state reconciliation.")
        stamp = now()
        row = {**(old or {}), "version": VERSION, "id": digest(["billing-review", env, uid]),
               "revision": (old or {}).get("revision", 0) + 1, "updated_at": stamp,
               "hold_scopes": list((old or {}).get("hold_scopes", [])),
               "signals": {**(old or {}).get("signals", {}), key: {"kind": kind, "outcome": outcome,
                   "sequence": sequence, "at": previous["at"] if previous and previous["outcome"] == outcome else stamp}}}
        def unresolved(signal):
            return signal["kind"] in ("payment_dispute", "unattributed_payment_dispute") and signal["outcome"] == "pending"
        # Identity noise, staff acknowledgement and resolved outcomes cannot
        # evict the last-known provider-backed unresolved payment evidence.
        row["unresolved_payment_overflow"] = bool((old or {}).get("unresolved_payment_overflow")
            or sum(unresolved(signal) for signal in row["signals"].values()) > 64)
        row["signals"] = dict(sorted(row["signals"].items(), key=lambda item: (unresolved(item[1]), item[1]["at"], item[0]))[-64:])
        observed = counts(row)
        row["pending_payment_disputes"] = sum(s["kind"] == "payment_dispute" and s["outcome"] == "pending" for s in row["signals"].values())
        signature = signal_digest(row["signals"])
        suspicious = (row["unresolved_payment_overflow"] or observed["attribution_reviews"] > 0 or observed["unresolved_disputes"] >= 2 or observed["identity_conflicts"] >= 3
                      or (observed["reversed_after_usage"] >= 3 and observed["unresolved_disputes"] >= 1))
        # Flags request staff attention. Only a named staff decision can add a
        # scope. Lawful refunds by themselves never restrict a member.
        row["review_required"] = bool(suspicious and signature != row.get("reviewed_signals_sha256"))
        saved = await db.users.update_one({"user_id": uid, "role": "member", "closing": {"$ne": True},
            "env": {"$in": [None, env]}, "$expr": snapshot_guard(member)}, {"$set": {"billing_risk": row}})
        if saved.matched_count:
            return status({"billing_risk": row})
    raise HTTPException(503, "Billing review changed concurrently. Refresh its saved status.")


class ReviewAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_member: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1)
    request_id: str
    action: Literal["hold", "release", "dismiss"]
    scopes: list[Literal["new_ai", "new_intro", "new_purchase"]] = Field(default_factory=list, max_length=3)
    reason: Literal["suspected_identity_abuse", "repeated_disputes", "manual_payment_review", "resolved"]

    @field_validator("request_id")
    @classmethod
    def uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("A canonical request UUID is required")
        return value


def staff_row(member):
    row = profile(member)
    return {"member_id": member["user_id"], **status(member), "counts": counts(row),
            "updated_at": row["updated_at"], "recent_actions": row.get("actions", [])[-25:]}


async def review(db, in_env, review_id, body, actor):
    if (not isinstance(actor, dict) or actor.get("role") != "admin" or not actor.get("user_id")):
        raise HTTPException(403, "Staff review permission is required.")
    if (len(body.scopes) != len(set(body.scopes))
            or (body.action in ("hold", "release")) != bool(body.scopes)):
        raise HTTPException(422, "Select explicit unique scopes for hold or release; dismiss takes no scopes.")
    member = await db.users.find_one({**in_env, "user_id": body.expected_member, "role": "member",
                                    "closing": {"$ne": True}, "billing_risk.id": review_id})
    if not member:
        raise HTTPException(404, "Billing review not found.")
    row = profile(member)
    signature = digest(body.model_dump())
    for prior in row.get("actions", []):
        if prior["request_id"] == body.request_id:
            if prior["request_sha256"] != signature:
                raise HTTPException(409, "That request was used for a different review action.")
            return {**staff_row(member), "duplicate": True}
    if row["revision"] != body.revision:
        raise HTTPException(409, "Billing review changed. Refresh before acting.")
    # Dismiss acknowledges flags, but must never silently remove a hold.
    selected, existing = set(body.scopes), set(row["hold_scopes"])
    scopes = sorted(existing | selected) if body.action == "hold" else sorted(existing - selected) if body.action == "release" else row["hold_scopes"]
    action = {"request_id": body.request_id, "request_sha256": signature, "action": body.action,
              "scopes": scopes, "reason": body.reason, "actor": actor["user_id"], "at": now()}
    changed = {**row, "revision": row["revision"] + 1, "hold_scopes": scopes,
               "review_required": False, "reviewed_signals_sha256": signal_digest(row["signals"]),
               "updated_at": action["at"], "actions": [*row.get("actions", []), action][-25:]}
    saved = await db.users.update_one({**in_env, "user_id": body.expected_member, "role": "member", "closing": {"$ne": True},
                                      "$expr": snapshot_guard(member)}, {"$set": {"billing_risk": changed}})
    if not saved.matched_count:
        raise HTTPException(409, "Billing review changed. Refresh its saved status; do not replay an uncertain action.")
    return {**staff_row({**member, "billing_risk": changed}), "duplicate": False}


def make_router(get_db, admin_dep, in_env):
    router = APIRouter()

    @router.get("/admin/billing-risk/reviews")
    async def reviews(response: Response, after: str | None = Query(None, max_length=200),
                      limit: int = Query(25, ge=1, le=50), actor=Depends(admin_dep)):
        response.headers["Cache-Control"] = "private, no-store"
        query = {**in_env, "role": "member", "closing": {"$ne": True}, "$or": [
            {"billing_risk.review_required": True}, {"billing_risk.pending_payment_disputes": {"$gt": 0}},
            {"billing_risk.unresolved_payment_overflow": True},
            {"billing_risk.hold_scopes.0": {"$exists": True}}]}
        if after is not None:
            query["user_id"] = {"$gt": after}
        rows = await get_db().users.find(query, {"user_id": 1, "billing_risk": 1}).sort("user_id", 1).limit(limit + 1).to_list(limit + 1)
        return {"items": [staff_row(row) for row in rows[:limit]], "next": rows[limit - 1]["user_id"] if len(rows) > limit else None}

    @router.patch("/admin/billing-risk/reviews/{review_id}")
    async def decide(review_id: str, body: ReviewAction, response: Response, actor=Depends(admin_dep)):
        response.headers["Cache-Control"] = "private, no-store"
        return await review(get_db(), in_env, review_id, body, actor)

    return router
