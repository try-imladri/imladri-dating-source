"""Fail-closed, one-member Plus100 acceptance window; never a payment authority.

Both keys absent/blank (or disabled without a member) preserve normal behavior.
An incomplete binding fails closed. An explicit acceptance mode
restricts new financial operations without changing existing data, cancellations,
paid access or provider reconciliation. The usual identity/card/CAS checks remain.
"""
import os
import re

from fastapi import HTTPException

MODE = "owner-plus100-v1"
MODE_KEY = "P1_BILLING_ACCEPTANCE_MODE"
MEMBER_KEY = "P1_BILLING_ACCEPTANCE_MEMBER_ID"
_UID = re.compile(r"user_[A-Za-z0-9]{8,128}\Z")
_UNSET = object()


def requested():
    return (os.getenv(MODE_KEY, "").strip() not in ("", "disabled")
            or bool(os.getenv(MEMBER_KEY, "").strip()))


def configured_member():
    mode = os.getenv(MODE_KEY, "").strip()
    uid = os.getenv(MEMBER_KEY, "").strip()
    if mode in ("", "disabled") and not uid:
        return None
    if mode != MODE or not _UID.fullmatch(uid):
        raise HTTPException(503, "Billing acceptance is awaiting its verified member configuration.")
    return uid


def _matches(member, uid, plan_id=None, selected=_UNSET):
    if (not isinstance(member, dict) or member.get("user_id") != uid
            or member.get("role") != "member" or member.get("closing")
            or member.get("complimentary_entitlement")):
        return False
    if plan_id is not None and plan_id != "plus_100":
        return False
    if selected is not _UNSET:
        return (isinstance(selected, dict) and selected.get("id") == "plus_100"
                and type(selected.get("messages")) is int and selected["messages"] == 100
                and type(selected.get("price_cents")) is int and selected["price_cents"] == 1200
                and selected.get("currency") == "usd" and selected.get("offer") == "standard"
                and selected.get("pair_id") is None)
    return True


def intro_allowed(member, *, plan_id=None, selected=_UNSET):
    """Safe availability predicate, including malformed active configuration."""
    try:
        uid = configured_member()
    except HTTPException:
        return False
    return uid is None or _matches(member, uid, plan_id, selected)


def require_intro(member, *, plan_id=None, selected=_UNSET):
    uid = configured_member()
    if uid is not None and not _matches(member, uid, plan_id, selected):
        raise HTTPException(403, "New billing is limited to the configured Plus 100 acceptance account.")


def forbid_ordinary_purchase():
    # Even the allowed member must use their one ordinary card-first agreement,
    # not an immediate checkout, upgrade, refill or future annual schedule.
    if configured_member() is not None:
        raise HTTPException(403, "New purchases are paused during the limited billing acceptance window.")
