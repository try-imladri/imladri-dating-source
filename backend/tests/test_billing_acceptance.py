"""One real-account acceptance window, with fictional Stripe and owned local PG."""
import ast
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, StrictBool
import pytest
from typing import Literal

from backend import billing_acceptance as gate, free_plan, intro_access, intro_card as card, partner_offers, purchase_policy, stripe_membership
from backend.tests.test_intro_card import Provider, configuration, identity, member, setup, tenth
from backend.tests.test_research_notices import run_case

OWNER = "user_P1Owner0000001"
OTHER = "user_Outside000001"
SELECTED = {"id": "plus_100", "name": "Plus 100", "messages": 100, "price_cents": 1200,
            "currency": "usd", "offer": "standard", "pair_id": None}


@pytest.fixture(autouse=True)
def clean_acceptance_environment(monkeypatch):
    monkeypatch.delenv(gate.MODE_KEY, raising=False)
    monkeypatch.delenv(gate.MEMBER_KEY, raising=False)


@pytest.fixture
def acceptance(monkeypatch):
    monkeypatch.setenv(gate.MODE_KEY, gate.MODE)
    monkeypatch.setenv(gate.MEMBER_KEY, OWNER)


def body(uid=OWNER, plan="plus_100"):
    return card.Setup(expected_member=uid, plan_id=plan, expected_price_cents=1200, expected_messages=100,
        request_id=str(uuid4()), authorization_version=card.VERSION, authorize_after_messages=True,
        policy_accepted=True, authorization_text_sha256="a" * 64, quote_sha256="b" * 64,
        origin_url="https://fixture.invalid", challenge_token="fictional")


@pytest.mark.parametrize("mode", [None, "", "  ", "disabled", " disabled "])
@pytest.mark.parametrize("uid", [None, "", "  "])
def test_default_and_disabled_preserve_existing_normal_flow(monkeypatch, mode, uid):
    if mode is not None:
        monkeypatch.setenv(gate.MODE_KEY, mode)
    if uid is not None:
        monkeypatch.setenv(gate.MEMBER_KEY, uid)
    assert not gate.requested() and gate.intro_allowed(member(OTHER), plan_id="plus_500")
    gate.require_intro(member(OTHER), selected={"id": "plus_500"})
    gate.forbid_ordinary_purchase()


@pytest.mark.parametrize("mode,uid", [("wrong", OWNER), (gate.MODE, ""), (gate.MODE, "owner"),
    (gate.MODE, OWNER + " extra"), ("true", OWNER), (None, OWNER), ("", OWNER),
    ("  ", OWNER), ("disabled", OWNER), (" disabled ", " " + OWNER + " "), (gate.MODE, None), (gate.MODE, "  ")])
def test_malformed_active_configuration_fails_closed(monkeypatch, mode, uid):
    if mode is not None:monkeypatch.setenv(gate.MODE_KEY, mode)
    if uid is not None:monkeypatch.setenv(gate.MEMBER_KEY, uid)
    assert gate.requested() and not gate.intro_allowed(member(OWNER))
    with pytest.raises(HTTPException) as error:gate.require_intro(member(OWNER))
    assert error.value.status_code == 503
    with pytest.raises(HTTPException):gate.forbid_ordinary_purchase()
    result = asyncio.run(intro_access.public_status(Mock()))
    assert result["enabled"] is False and result["messages"] == result["messages_granted"] == 0
    db = Mock();provider = Mock()
    with pytest.raises(HTTPException):
        asyncio.run(card.setup(db, member(OWNER), identity(OWNER), body(), "https://fixture.invalid", provider=provider))
    with pytest.raises(HTTPException):
        asyncio.run(free_plan.reserve_intro_reply(db, member(OWNER), OWNER, "k", "p", {}, None, "exchange", "s", "sig", None))
    current = member(OWNER, intro_billing={"version": card.VERSION, "id": "fixture", "state": "billing_pending",
        "selected_plan": SELECTED, "conversion": {"stage": "due"}})
    with pytest.MonkeyPatch.context() as local:
        local.setattr(card, "read_member", AsyncMock(return_value=current))
        local.setattr(intro_access, "settings", AsyncMock(return_value={"paused": False}))
        asyncio.run(card._dispatch_due(db, OWNER, provider=provider))
    assert not db.mock_calls and not provider.mock_calls
    ns, trap = actual_server_namespace(OWNER)
    app = FastAPI();app.include_router(ns["api"])
    assert TestClient(app).post("/payments/checkout", json={"expected_member": OWNER,
        "origin_url": "https://fixture.invalid", "offer": "standard"}).status_code == 503
    assert not trap.mock_calls


def test_exact_binding_normalizes_surrounding_whitespace(monkeypatch):
    monkeypatch.setenv(gate.MODE_KEY, " " + gate.MODE + " ")
    monkeypatch.setenv(gate.MEMBER_KEY, " " + OWNER + " ")
    assert gate.requested() and gate.configured_member() == OWNER


@pytest.mark.parametrize("change", [{"user_id": OTHER}, {"role": "admin"}, {"closing": True},
    {"complimentary_entitlement": {"active": True}}])
def test_only_exact_ordinary_owner_member_may_start(acceptance, change):
    current = {**member(OWNER), **change}
    assert not gate.intro_allowed(current, selected=SELECTED)
    with pytest.raises(HTTPException) as error:gate.require_intro(current, selected=SELECTED)
    assert error.value.status_code == 403


@pytest.mark.parametrize("change", [{"id": "plus_250"}, {"price_cents": 1000}, {"price_cents": True},
    {"messages": 250}, {"currency": "eur"}, {"offer": "partner_monthly"}, {"pair_id": "another-pair"}])
def test_owner_quote_is_exact_plus100_standard_12(acceptance, change):
    assert gate.intro_allowed(member(OWNER), selected=SELECTED)
    assert not gate.intro_allowed(member(OWNER), selected={**SELECTED, **change})
    with pytest.raises(HTTPException):gate.require_intro(member(OWNER), selected={**SELECTED, **change})


def test_missing_selected_plan_is_not_a_conversion_permission(acceptance):
    assert not gate.intro_allowed(member(OWNER), selected=None)


@pytest.mark.parametrize("uid,plan", [(OTHER, "plus_100"), (OWNER, "plus_250"), (OWNER, "plus_500")])
def test_setup_denied_before_database_or_provider_access(acceptance, uid, plan):
    provider = Mock();db = Mock()
    with pytest.raises(HTTPException) as error:
        asyncio.run(card.setup(db, member(uid), identity(uid), body(uid, plan), "https://fixture.invalid", provider=provider))
    assert error.value.status_code == 403 and not provider.mock_calls and not db.mock_calls


def test_claim_and_new_intro_generation_cannot_bypass_owner(acceptance):
    db = Mock()
    with pytest.raises(HTTPException):
        asyncio.run(intro_access.prepare_claim(db, member(OTHER), identity(OTHER), intro_access.Claim(expected_member=OTHER)))
    with pytest.raises(HTTPException):
        asyncio.run(free_plan.reserve_intro_reply(db, member(OTHER), OTHER, "k", "p", {}, None, "exchange", "s", "sig", None))
    assert not db.mock_calls


def actual_server_namespace(uid):
    """Compile actual unchanged-shape route bodies, no server startup/credentials."""
    source = Path(__file__).resolve().parents[1] / "server.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    classes = {"BillingRequest", "CheckoutIn", "UpgradeIn", "RefillIn", "AnnualAcceptance"}
    functions = {"create_checkout", "upgrade_subscription", "purchase_refill", "accept_partner_year", "make_checkout_session"}
    nodes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in classes
             or isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in functions]
    async def current():return member(uid)
    trap = Mock(side_effect=AssertionError("Financial dependency must not be reached"))
    ns = {"BaseModel": BaseModel, "Field": Field, "StrictBool": StrictBool, "Literal": Literal,
          "Request": Request, "Depends": Depends, "HTTPException": HTTPException,
          "current_member": current, "api": APIRouter(), "purchase_policy": purchase_policy,
          "billing_acceptance": gate, "billing_risk": SimpleNamespace(require_action=trap), "stripe": trap}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), ns)
    return ns, trap


@pytest.mark.parametrize("uid", [OWNER, OTHER])
@pytest.mark.parametrize("path,offer", [("/payments/checkout", "standard"), ("/payments/upgrade", "plus_250"),
    ("/payments/topup", None), ("/payments/partner-annual", None)])
def test_actual_all_money_routes_deny_even_owner_ordinary_bypasses(acceptance, uid, path, offer):
    ns, trap = actual_server_namespace(uid)
    app = FastAPI();app.include_router(ns["api"])
    payload = {"expected_member": uid, "origin_url": "https://fixture.invalid"}
    if path == "/payments/partner-annual":payload["accept"] = True
    if offer:payload["offer"] = offer
    response = TestClient(app).post(path, json=payload)
    assert response.status_code == 403 and not trap.mock_calls


def test_direct_financial_helpers_also_refuse_bypasses(acceptance):
    ns, trap = actual_server_namespace(OWNER)
    with pytest.raises(HTTPException):ns["make_checkout_session"]({}, "managed", "fictional")
    with pytest.raises(HTTPException):stripe_membership.upgrade_session(member(OWNER), "plus_250", "https://fixture.invalid")
    with pytest.raises(HTTPException):asyncio.run(partner_offers.accept(Mock(), member(OWNER)))
    with pytest.raises(HTTPException):asyncio.run(partner_offers.admit_schedule_write(Mock(), OWNER, {}, "schedule_create"))
    assert not trap.mock_calls


def test_public_intro_stays_held_without_reading_database(acceptance):
    result = asyncio.run(intro_access.public_status(Mock()))
    assert result["enabled"] is False and result["messages"] == result["messages_granted"] == 0
    assert result["requires_payment_method"] is True and result["automatic_charge"] is True


def test_only_owner_gets_only_plus100_quote(acceptance, monkeypatch):
    monkeypatch.setattr(card, "quote", AsyncMock(return_value=deepcopy(SELECTED)))
    assert asyncio.run(card.quotes(Mock(), member(OTHER))) == {}
    quotes = asyncio.run(card.quotes(Mock(), member(OWNER)))
    assert set(quotes) == {"plus_100"} and quotes["plus_100"]["price_cents"] == 1200


def test_nonowner_background_conversion_never_reads_provider(acceptance, monkeypatch):
    current = member(OTHER, intro_billing={"version": card.VERSION, "id": "fixture", "state": "billing_pending",
        "selected_plan": SELECTED, "conversion": {"stage": "due"}})
    monkeypatch.setattr(card, "read_member", AsyncMock(return_value=current))
    monkeypatch.setattr(intro_access, "settings", AsyncMock(return_value={"paused": False}))
    provider = Mock()
    asyncio.run(card._dispatch_due(Mock(), OTHER, provider=provider))
    assert not provider.mock_calls


def test_owner_one_setup_tenth_settlement_and_single_unknown_conversion_on_actual_PG(acceptance):
    async def case(db):
        provider = Provider();current, _ = await setup(db, provider, uid=OWNER)
        assert card.active(current) and current["intro_reply_totals"]["used"] == 0
        current = await tenth(db, current);provider.fail_convert = True
        await card.dispatch_due(db, OWNER, provider=provider)
        await card.dispatch_due(db, OWNER, provider=provider)
        saved = await db.users.find_one({"user_id": OWNER})
        assert saved["intro_reply_totals"]["used"] == 10
        assert saved["intro_billing"]["conversion"]["stage"] == "unknown"
        assert len([x for x in provider.calls if x[0] == "convert"]) == 1
        assert saved["tier"] == "unsubscribed"
    run_case(case)


def test_other_existing_intro_remains_cancelable_without_new_grant(acceptance, monkeypatch):
    async def case(db):
        # Existing legitimate setup predates the acceptance window.
        monkeypatch.setenv(gate.MODE_KEY, "disabled")
        monkeypatch.delenv(gate.MEMBER_KEY)
        provider = Provider();current, _ = await setup(db, provider, uid=OTHER)
        original_grant = deepcopy(current["intro_entitlement"])
        monkeypatch.setenv(gate.MODE_KEY, gate.MODE)
        monkeypatch.setenv(gate.MEMBER_KEY, OWNER)
        cancelled = await card.cancel(db, current, card.Cancel(expected_member=OTHER, request_id=str(uuid4()),
            revision=current["intro_billing"]["revision"]), provider=provider)
        assert cancelled["intro_billing"]["state"] == "cancelled"
        assert cancelled["intro_entitlement"] == original_grant
        assert len([x for x in provider.calls if x[0] == "cancel"]) == 1
        assert not any(x[0] == "convert" for x in provider.calls)
    run_case(case)


def test_mode_change_after_provider_read_blocks_owner_first_charge(acceptance, monkeypatch):
    async def case(db):
        provider = Provider();current, _ = await setup(db, provider, uid=OWNER);await tenth(db, current)
        original = provider.subscription
        def changed(sub):
            result = original(sub);monkeypatch.setenv(gate.MEMBER_KEY, OTHER);return result
        provider.subscription = changed
        await card.dispatch_due(db, OWNER, provider=provider)
        saved = await db.users.find_one({"user_id": OWNER})
        assert saved["intro_billing"]["conversion"]["stage"] == "due"
        assert not any(x[0] == "convert" for x in provider.calls)
    run_case(case)


def test_nonowner_pending_confirmation_grants_nothing_and_calls_no_provider(acceptance, monkeypatch):
    async def case(db):
        monkeypatch.setenv(gate.MODE_KEY, "disabled")
        monkeypatch.delenv(gate.MEMBER_KEY)
        provider = Provider();current, _ = await setup(db, provider, uid=OTHER, complete=False)
        monkeypatch.setenv(gate.MODE_KEY, gate.MODE);monkeypatch.setenv(gate.MEMBER_KEY, OWNER);provider.calls.clear()
        saved = await card.confirm(db, current, provider.sid, provider=provider)
        assert saved["intro_billing"]["state"] == "checkout_pending" and not saved.get("intro_entitlement")
        assert provider.calls == []
    run_case(case)


def test_nonstandard_owner_quote_is_not_published(acceptance, monkeypatch):
    monkeypatch.setattr(card, "quote", AsyncMock(return_value={**SELECTED, "offer": "partner_monthly", "price_cents": 1000}))
    assert asyncio.run(card.quotes(Mock(), member(OWNER))) == {}


def test_member_mode_change_during_setup_provider_reads_prevents_create(acceptance, monkeypatch):
    async def case(db):
        provider = Provider();original = provider.coupon
        def coupon(identifier):
            result = original(identifier);monkeypatch.setenv(gate.MEMBER_KEY, OTHER);return result
        provider.coupon = coupon
        with pytest.raises(HTTPException) as error:await setup(db, provider, uid=OWNER)
        assert error.value.status_code == 403 and not any(x[0] == "create" for x in provider.calls)
        saved = await db.users.find_one({"user_id": OWNER})
        assert not saved.get("intro_entitlement")
    run_case(case)


def test_owner_verified_full_12_invoice_delivers_100_once_in_acceptance_mode(acceptance, monkeypatch):
    from backend.tests import test_intro_history_routes as routes
    from backend.tests.test_intro_card import invoice as exact_invoice
    import time
    async def case(db):
        server, _ = routes.configure(monkeypatch, db)
        monkeypatch.setattr(server, "ENV", "fixture")
        monkeypatch.setattr(server, "IN_ENV", {"env": {"$in": ["fixture", None]}})
        provider = routes.install_card_provider(monkeypatch, server)
        current, _ = await setup(db, provider, uid=OWNER)
        current = await tenth(db, current)
        await card.dispatch_due(db, OWNER, provider=provider)
        current = await db.users.find_one({"user_id": OWNER})
        assert current["intro_billing"]["conversion"]["stage"] == "awaiting_invoice"
        paid = routes.invoice("plus_100", int(time.time()), "subscription_update", "in_owner_paid_" + uuid4().hex)
        paid.update({key: value for key, value in exact_invoice(current["intro_billing"]).items() if key != "id"})
        provider.sub["latest_invoice"] = paid
        period = paid["lines"]["data"][0]["period"]
        provider.sub["items"]["data"][0].update(current_period_start=period["start"], current_period_end=period["end"])
        current = await card.refresh(db, current, provider=provider, fulfill=server.mark_paid)
        assert current["intro_billing"]["state"] == "converted" and current["tier"] == "paid"
        assert free_plan.reply_usage(current)["messages_remaining"] == 100
        assert current["intro_reply_totals"] == {"claimed": 10, "used": 10}
        tx = await db.payment_transactions.find_one({"session_id": provider.sid})
        assert tx["payment_status"] == "paid" and tx["credit_delivery_status"] == "fulfilled"
        assert tx["subscription_revenue_cents"] == 1200
        await card.refresh(db, current, provider=provider, fulfill=server.mark_paid)
        assert len([x for x in provider.calls if x[0] == "convert"]) == 1
        assert await db.payment_transactions.count_documents({"session_id": provider.sid, "payment_status": "paid"}) == 1
        assert free_plan.reply_usage(await db.users.find_one({"user_id": OWNER}))["messages_remaining"] == 100
    run_case(case)


def test_acceptance_mode_does_not_enable_global_billing(acceptance, monkeypatch):
    monkeypatch.setenv("PERSONAL_BILLING_MODE", "disabled")
    assert not card.configured()
