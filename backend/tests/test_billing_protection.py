"""Actual PostgreSQL payment-specific holds, safe recovery and staff scopes."""
import asyncio
from copy import deepcopy
import hashlib
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
import httpx
import pytest
import stripe

from backend import billing_adjustments as billing, billing_risk as risk, free_plan
from backend.tests.test_billing_adjustments import fixture, exercise
from backend.tests.test_complimentary_access import Body, request


def payments(monkeypatch, charges, disputes):
    monkeypatch.setattr(stripe.Charge, "retrieve", lambda identifier: deepcopy(charges[identifier]))
    monkeypatch.setattr(stripe.Dispute, "list", lambda charge, **kw: {"has_more": False, "data": deepcopy(disputes.get(charge, []))})


async def reconcile(db, tx):
    return await billing.reconcile(db, tx, env="preview", charge_id=tx["stripe_charge_id"], payment_id=tx["stripe_payment_intent_id"])


def dispute(charge, status="under_review", amount=1200):
    return {"id": "dp_" + charge, "charge": charge, "currency": "usd", "amount": amount, "status": status}


def another(tx, charge):
    tx, charge = deepcopy(tx), deepcopy(charge)
    tx.update(_id="checkout:second", session_id="cs_second", stripe_payment_intent_id="pi_second", stripe_charge_id="ch_second")
    tx["topup_billing_basis"].update(session_id="cs_second", payment_intent_id="pi_second")
    charge.update(id="ch_second", payment_intent="pi_second")
    return tx, charge


async def reply(db, key, generate=None):
    async def delivered(rid):
        return {"id": rid}
    async def restore(doc):
        return {"id": doc["id"]}
    member = await db.users.find_one({"user_id": "member"})
    return await free_plan.run_metered(db, member, request(key), Body(), "exchange", "own", generate or delivered, restore)


def test_pending_refill_holds_only_its_unspent_pack_and_resolution_restores_exactly(monkeypatch):
    member, tx, charge = fixture()
    member["paid_reply_cycles"] = {free_plan.paid_cycle(member): {"claimed": 100, "used": 100}}
    second, charge2 = another(tx, charge)
    charges, disputes = {charge["id"]: charge, charge2["id"]: charge2}, {}
    payments(monkeypatch, charges, disputes)
    async def case(db):
        await db.users.insert_one(member)
        await db.payment_transactions.insert_many([tx, second])
        await reconcile(db, tx)
        for index in range(3):
            await reply(db, "before-dispute-" + str(index))
        await reconcile(db, second)
        disputes[charge["id"]] = [dispute(charge["id"])]
        await reconcile(db, tx); await reconcile(db, tx)
        held = await db.users.find_one({"user_id": "member"})
        first_key = hashlib.sha256(tx["session_id"].encode()).hexdigest()
        assert held["topup_balance"] == 100
        assert held["topup_payment_tracking"][first_key] == {"net_messages": 100, "claimed_messages": 3, "pending_messages": 100}
        assert free_plan.reply_usage(held)["held_message_count"] == 97
        await reply(db, "unrelated-funded-pack")
        current = await db.users.find_one({"user_id": "member"})
        receipt = next(r for r in current["free_reply_requests"].values() if r["result"] not in {v["result"] for v in held["free_reply_requests"].values()})
        assert receipt["topup_payment_key"] == hashlib.sha256(second["session_id"].encode()).hexdigest()
        disputes[charge["id"]][0]["status"] = "won"
        await reconcile(db, tx); await reconcile(db, tx)
        restored = await db.users.find_one({"user_id": "member"})
        assert restored["topup_balance"] == 196
        assert restored["billing_risk"]["pending_payment_disputes"] == 0
    exercise(case)


def test_failed_generation_during_dispute_never_releases_held_credit(monkeypatch):
    member, tx, charge = fixture()
    member["paid_reply_cycles"] = {free_plan.paid_cycle(member): {"claimed": 100, "used": 100}}
    disputes = {}; payments(monkeypatch, {charge["id"]: charge}, disputes)
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx); await reconcile(db, tx)
        async def fail_after_hold(rid):
            disputes[charge["id"]] = [dispute(charge["id"])]
            await reconcile(db, tx)
            raise HTTPException(502, "Fictional provider failure")
        with pytest.raises(HTTPException):
            await reply(db, "fails-while-pending", fail_after_hold)
        current = await db.users.find_one({"user_id": "member"})
        key = hashlib.sha256(tx["session_id"].encode()).hexdigest()
        assert current["topup_balance"] == 0 and current["topup_payment_tracking"][key]["claimed_messages"] == 0
        disputes[charge["id"]][0]["status"] = "won"
        await reconcile(db, tx)
        assert (await db.users.find_one({"user_id": "member"}))["topup_balance"] == 100
    exercise(case)


def test_subscription_pending_hold_is_period_specific_and_preserves_refills(monkeypatch):
    member, tx, charge = fixture(kind="purchase")
    member["topup_balance"] = 2
    disputes = {charge["id"]: [dispute(charge["id"], amount=600)]}
    payments(monkeypatch, {charge["id"]: charge}, disputes)
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        await reconcile(db, tx)
        current = await db.users.find_one({"user_id": "member"})
        usage = free_plan.reply_usage(current)
        assert usage["message_limit"] == 50 and usage["topup_remaining"] == 2
        assert usage["messages_remaining"] == 52
        assert usage["payment_dispute_hold"] and usage["held_message_count"] == 50
        adjustment = next(iter(current["subscription_allowance_adjustments"].values()))
        assert adjustment["net_messages"] == 100 and adjustment["pending_dispute_messages"] == 50
        assert not current["billing_risk"]["hold_scopes"]
        disputes[charge["id"]][0]["status"] = "won"; await reconcile(db, tx)
        assert free_plan.reply_usage(await db.users.find_one({"user_id": "member"}))["messages_remaining"] == 102
    exercise(case)


def test_pending_dispute_with_ambiguous_duplicate_invoice_basis_requires_review(monkeypatch):
    member, tx, charge = fixture(kind="purchase")
    allocation = tx["billing_basis"]
    member["subscription_allowance_adjustments"] = {"other": {"version": billing.VERSION,
        "subscription_id": allocation["subscription_id"], "price_id": allocation["price_id"],
        "period_start": allocation["period_start"], "period_end": allocation["period_end"],
        "net_messages": 100, "original_messages": 100}}
    payments(monkeypatch, {charge["id"]: charge}, {charge["id"]: [dispute(charge["id"])]})
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        with pytest.raises(HTTPException) as error: await reconcile(db, tx)
        assert error.value.status_code == 503
        current = await db.users.find_one({"user_id": "member"})
        assert current["subscription_allowance_adjustments"] == member["subscription_allowance_adjustments"]
        assert await db.closed_membership_revenue.count_documents({"kind": "unresolved_adjustment", "data.status": "pending"}) == 1
    exercise(case)


def test_unavailable_provider_retains_last_known_payment_hold(monkeypatch):
    member, tx, charge = fixture()
    payments(monkeypatch, {charge["id"]: charge}, {charge["id"]: [dispute(charge["id"])]})
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx); await reconcile(db, tx)
        before = await db.users.find_one({"user_id": "member"})
        def unavailable(*a, **kw): raise stripe.APIConnectionError("fictional read unavailable")
        monkeypatch.setattr(stripe.Charge, "retrieve", unavailable)
        with pytest.raises(HTTPException) as error: await reconcile(db, tx)
        assert error.value.status_code == 503
        after = await db.users.find_one({"user_id": "member"})
        assert after["topup_balance"] == before["topup_balance"] == 0
        assert after["topup_payment_tracking"] == before["topup_payment_tracking"]
    exercise(case)


def test_legacy_pooled_refill_requires_review_without_invented_attribution(monkeypatch):
    member, tx, charge = fixture()
    key = hashlib.sha256(tx["session_id"].encode()).hexdigest()
    member.update(topup_grants={key: 100}, topup_balance=60)
    payments(monkeypatch, {charge["id"]: charge}, {charge["id"]: [dispute(charge["id"])]})
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx); await reconcile(db, tx)
        current = await db.users.find_one({"user_id": "member"})
        assert current["topup_balance"] == 60 and not current.get("topup_payment_tracking")
        assert not free_plan.reply_usage(current)["payment_dispute_hold"]
        assert free_plan.reply_usage(current)["held_message_count"] == 0
        assert current["billing_risk"]["review_required"] and not current["billing_risk"]["hold_scopes"]
        assert (await db.payment_transactions.find_one({"_id": tx["_id"]}))["billing_adjustment"]["pending_refill_attribution_review"]
    exercise(case)


def action(member, *, revision=None, scopes=None, kind="hold", request_id=None):
    return risk.ReviewAction(expected_member=member["user_id"], revision=revision or member["billing_risk"]["revision"],
        request_id=request_id or str(uuid4()), action=kind, scopes=scopes if scopes is not None else ([] if kind == "dismiss" else ["new_ai"]),
        reason="manual_payment_review" if kind == "hold" else "resolved")


async def flagged(db):
    await db.users.insert_one({"user_id": "member", "role": "member", "env": "preview", "private_answer": "NOT STAFF BILLING DATA"})
    for index in range(3):
        await risk.observe(db, "member", "preview", "identity_conflict", "fictional-source-" + str(index), "conflict")
    return await db.users.find_one({"user_id": "member"})


def test_review_signals_are_idempotent_and_lawful_refunds_alone_never_hold():
    async def case(db):
        member = await flagged(db)
        revision = member["billing_risk"]["revision"]
        await risk.observe(db, "member", "preview", "identity_conflict", "fictional-source-2", "conflict")
        assert (await db.users.find_one({"user_id": "member"}))["billing_risk"]["revision"] == revision
        for index in range(4):
            await risk.observe(db, "member", "preview", "reversed_after_usage", "lawful-refund-" + str(index), "reversed")
        current = await db.users.find_one({"user_id": "member"})
        assert not current["billing_risk"]["hold_scopes"]
        await db.users.insert_one({"user_id": "refund-only", "role": "member", "env": "preview"})
        for index in range(4):
            await risk.observe(db, "refund-only", "preview", "reversed_after_usage", str(index), "reversed")
        lawful = await db.users.find_one({"user_id": "refund-only"})
        assert not lawful["billing_risk"]["review_required"] and not lawful["billing_risk"]["hold_scopes"]
    exercise(case)


def test_staff_scope_action_is_atomic_idempotent_and_dismiss_never_releases():
    async def case(db):
        member = await flagged(db); rid = member["billing_risk"]["id"]
        body = action(member, scopes=["new_intro"]); actor = {"user_id": "staff", "role": "admin"}
        first = await risk.review(db, {"env": "preview"}, rid, body, actor)
        assert first["hold_scopes"] == ["new_intro"] and not first["duplicate"]
        assert (await risk.review(db, {"env": "preview"}, rid, body, actor))["duplicate"]
        current = await db.users.find_one({"user_id": "member"})
        with pytest.raises(HTTPException): risk.require_action(current, "new_intro")
        risk.require_action(current, "new_ai"); risk.require_action(current, "new_purchase")
        dismissed = await risk.review(db, {"env": "preview"}, rid, action(current, kind="dismiss"), actor)
        assert dismissed["hold_scopes"] == ["new_intro"]
        current = await db.users.find_one({"user_id": "member"})
        released = await risk.review(db, {"env": "preview"}, rid, action(current, kind="release", scopes=["new_intro"]), actor)
        assert released["hold_scopes"] == []
        assert (await db.users.find_one({"user_id": "member"}))["private_answer"] == "NOT STAFF BILLING DATA"
    exercise(case)


def test_staff_add_and_release_preserve_unselected_hold_scopes():
    async def case(db):
        member = await flagged(db); rid = member["billing_risk"]["id"]; actor = {"user_id": "staff", "role": "admin"}
        await risk.review(db, {"env": "preview"}, rid, action(member, scopes=["new_ai"]), actor)
        member = await db.users.find_one({"user_id": "member"})
        held = await risk.review(db, {"env": "preview"}, rid, action(member, scopes=["new_purchase"]), actor)
        assert held["hold_scopes"] == ["new_ai", "new_purchase"]
        member = await db.users.find_one({"user_id": "member"})
        released = await risk.review(db, {"env": "preview"}, rid, action(member, kind="release", scopes=["new_ai"]), actor)
        assert released["hold_scopes"] == ["new_purchase"]
        member = await db.users.find_one({"user_id": "member"})
        dismissed = await risk.review(db, {"env": "preview"}, rid, action(member, kind="dismiss"), actor)
        assert dismissed["hold_scopes"] == ["new_purchase"]
        member = await db.users.find_one({"user_id": "member"})
        risk.require_action(member, "new_ai")
        with pytest.raises(HTTPException): risk.require_action(member, "new_purchase")
        for kind, scopes in (("release", []), ("hold", []), ("dismiss", ["new_ai"])):
            with pytest.raises(HTTPException) as error:
                await risk.review(db, {"env": "preview"}, rid, action(member, kind=kind, scopes=scopes), actor)
            assert error.value.status_code == 422
    exercise(case)


def test_old_dispute_signal_cannot_replace_newer_resolution_or_reopen_review():
    async def case(db):
        await db.users.insert_one({"user_id": "member", "role": "member", "env": "preview"})
        await risk.observe(db, "member", "preview", "payment_dispute", "payment-1", "pending", sequence=2)
        await risk.observe(db, "member", "preview", "payment_dispute", "payment-1", "won", sequence=3)
        resolved = await db.users.find_one({"user_id": "member"})
        await risk.observe(db, "member", "preview", "payment_dispute", "payment-1", "pending", sequence=2)
        current = await db.users.find_one({"user_id": "member"})
        assert current["billing_risk"] == resolved["billing_risk"]
        for number in (2, 3):
            await risk.observe(db, "member", "preview", "payment_dispute", "payment-" + str(number), "pending", sequence=4)
        current = await db.users.find_one({"user_id": "member"})
        assert current["billing_risk"]["review_required"]
        await risk.review(db, {"env": "preview"}, current["billing_risk"]["id"], action(current, kind="dismiss"), {"user_id": "staff", "role": "admin"})
        await risk.observe(db, "member", "preview", "payment_dispute", "payment-2", "pending", sequence=5)
        assert not (await db.users.find_one({"user_id": "member"}))["billing_risk"]["review_required"]
    exercise(case)


def test_unresolved_payment_survives_64_identity_signals_and_staff_dismiss():
    async def case(db):
        await db.users.insert_one({"user_id": "member", "role": "member", "env": "preview"})
        await risk.observe(db, "member", "preview", "payment_dispute", "real-pending-payment", "pending", sequence=1)
        for index in range(64):
            await risk.observe(db, "member", "preview", "identity_conflict", "noise-" + str(index), "conflict")
        current = await db.users.find_one({"user_id": "member"})
        assert len(current["billing_risk"]["signals"]) == 64
        assert risk.status(current)["pending_payment_disputes"] == 1
        await risk.review(db, {"env": "preview"}, current["billing_risk"]["id"], action(current, kind="dismiss"), {"user_id": "staff", "role": "admin"})
        status = risk.status(await db.users.find_one({"user_id": "member"}))
        assert status["requires_staff_review"] and status["actionable"] and status["payment_holds_require_provider_resolution"]
        assert not status["pending_count_is_lower_bound"]
    exercise(case)


def test_unresolved_signal_overflow_stays_visible_without_inventing_resolution():
    async def case(db):
        await db.users.insert_one({"user_id": "member", "role": "member", "env": "preview"})
        for index in range(65):
            await risk.observe(db, "member", "preview", "payment_dispute", "payment-" + str(index), "pending", sequence=1)
        current = await db.users.find_one({"user_id": "member"})
        assert len(current["billing_risk"]["signals"]) == 64 and risk.status(current)["pending_count_is_lower_bound"]
        await risk.review(db, {"env": "preview"}, current["billing_risk"]["id"], action(current, kind="dismiss"), {"user_id": "staff", "role": "admin"})
        current = await db.users.find_one({"user_id": "member"})
        assert risk.status(current)["requires_staff_review"] and risk.status(current)["unresolved_payment_overflow"]
    exercise(case)


def test_temporary_hold_error_is_distinct_from_spent_or_refunded_messages(monkeypatch):
    member, tx, charge = fixture(kind="purchase")
    payments(monkeypatch, {charge["id"]: charge}, {charge["id"]: [dispute(charge["id"])]})
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx); await reconcile(db, tx)
        current = await db.users.find_one({"user_id": "member"})
        assert free_plan.reply_usage(current)["held_message_count"] == 100
        error = free_plan.reply_limit_error(current)
        assert error.headers["X-Imladri-Limit"] == "payment-review" and "temporarily held" in error.detail
        current["paid_reply_cycles"] = {free_plan.paid_cycle(current): {"claimed": 100, "used": 100}}
        assert not free_plan.reply_usage(current)["payment_dispute_hold"]
        assert free_plan.reply_limit_error(current).headers["X-Imladri-Limit"] == "messages"
        current["paid_reply_cycles"] = {}; current["subscription_period_start"] = current["subscription_period_end"]
        current["subscription_period_end"] += 30 * 86400
        monkeypatch.setattr(free_plan.time, "time", lambda: current["subscription_period_start"] + 1)
        assert free_plan.reply_usage(current)["held_message_count"] == 0
    exercise(case)


@pytest.mark.parametrize("change", ["stale", "wrong_owner", "wrong_environment", "non_admin", "closing"])
def test_review_action_rejects_stale_or_foreign_authority(change):
    async def case(db):
        member = await flagged(db); body = action(member); actor = {"user_id": "staff", "role": "admin"}; env = {"env": "preview"}
        if change == "stale": await risk.observe(db, "member", "preview", "identity_conflict", "new-source", "conflict")
        if change == "wrong_owner": body = body.model_copy(update={"expected_member": "other"})
        if change == "wrong_environment": env = {"env": "live"}
        if change == "non_admin": actor["role"] = "member"
        if change == "closing": await db.users.update_one({"user_id": "member"}, {"$set": {"closing": True}})
        with pytest.raises(HTTPException): await risk.review(db, env, member["billing_risk"]["id"], body, actor)
        assert not (await db.users.find_one({"user_id": "member"}))["billing_risk"]["hold_scopes"]
    exercise(case)


def test_hold_winning_before_reservation_stops_new_AI_without_spending():
    async def case(db):
        original = await flagged(db)
        member, _, _ = fixture(); member["billing_risk"] = original["billing_risk"]
        await db.users.update_one({"user_id": "member"}, {"$set": member})
        await risk.review(db, {"env": "preview"}, member["billing_risk"]["id"], action(member), {"user_id": "staff", "role": "admin"})
        receipt = {"result": str(uuid4()), "kind": "exchange", "scope": "own", "signature": "synthetic"}
        with pytest.raises(HTTPException) as error:
            await free_plan.reserve_paid_reply(db, member, "member", "key", "free_reply_requests.key", receipt, None,
                free_plan.paid_cycle(member), free_plan.quota_period(member), "exchange", "own", "synthetic", None)
        assert error.value.status_code == 403
        current = await db.users.find_one({"user_id": "member"})
        assert not current.get("free_reply_requests") and not current.get("paid_reply_cycles")
    exercise(case)


def test_admin_routes_are_content_free_and_scope_release_cannot_release_payment_hold(monkeypatch):
    member, tx, charge = fixture(kind="purchase")
    payments(monkeypatch, {charge["id"]: charge}, {charge["id"]: [dispute(charge["id"])]})
    async def case(db):
        await db.users.insert_one({**member, "private_answer": "NEVER INCLUDE"}); await db.payment_transactions.insert_one(tx); await reconcile(db, tx)
        actor = {"user_id": "staff", "role": "admin"}
        async def admin(): return actor
        app = FastAPI(); app.include_router(risk.make_router(lambda: db, admin, {"env": "preview"}))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://fixture.invalid") as client:
            result = await client.get("/admin/billing-risk/reviews")
            assert result.status_code == 200 and len(result.json()["items"]) == 1
            queued = result.json()["items"][0]
            assert queued["type"] == "payment_dispute_hold" and queued["pending_payment_disputes"] == queued["temporary_payment_hold_count"] == 1
            assert queued["requires_staff_review"] and queued["actionable"] and queued["hold_scopes"] == []
            assert "NEVER INCLUDE" not in result.text and "pi_fixture" not in result.text
            current = await db.users.find_one({"user_id": "member"}); rid = current["billing_risk"]["id"]
            response = await client.patch("/admin/billing-risk/reviews/" + rid, json=action(current, kind="release").model_dump())
            assert response.status_code == 200
            current = await db.users.find_one({"user_id": "member"})
            assert free_plan.effective_message_limit(current) == 0
            assert risk.status(current)["payment_holds_require_provider_resolution"]
            assert risk.status(current)["requires_staff_review"] and risk.status(current)["actionable"]
            assert len((await client.get("/admin/billing-risk/reviews")).json()["items"]) == 1
    exercise(case)


@pytest.mark.parametrize("route,extra", [("checkout", {"offer": "plus_100", "origin_url": "https://fixture.invalid"}),
    ("topup", {"origin_url": "https://fixture.invalid"}), ("upgrade", {"offer": "plus_250", "origin_url": "https://fixture.invalid"}),
    ("partner-annual", {"accept": True})])
def test_real_server_purchase_routes_respect_staff_hold_before_provider(monkeypatch, route, extra):
    from backend import server, purchase_policy
    async def case(db):
        current = await flagged(db)
        await risk.review(db, {"env": "preview"}, current["billing_risk"]["id"], action(current, scopes=["new_purchase"]),
                          {"user_id": "staff", "role": "admin"})
        monkeypatch.setattr(server, "db", db); monkeypatch.setattr(server, "ENV", "preview")
        async def identity(): return {"user_id": "member"}
        monkeypatch.setitem(server.app.dependency_overrides, server.require_user, identity)
        def no_provider(*args, **kwargs): pytest.fail("A held new purchase reached a financial provider")
        monkeypatch.setattr(server, "make_checkout_session", no_provider)
        monkeypatch.setattr(server.stripe_membership, "upgrade_session", no_provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="https://fixture.invalid") as client:
            response = await client.post("/api/payments/" + route, json={"expected_member": "member", "purchase_policy_accepted": True,
                "purchase_policy_version": purchase_policy.VERSION, **extra})
            assert response.status_code == 403 and response.headers["x-imladri-limit"] == "billing-review", response.text
        assert not await db.payment_transactions.count_documents({})
    exercise(case)


@pytest.mark.parametrize("hold_at", ["before_admission", "during_subscription_read"])
def test_annual_schedule_write_admission_rechecks_current_staff_scope(monkeypatch, hold_at):
    from backend import partner_offers
    async def case(db):
        original = await flagged(db)
        agreement = {"subscription": "sub_annual_fixture", "status": "pending", "pair": "pair", "schedule": None}
        await db.users.update_one({"user_id": "member"}, {"$set": {"stripe_subscription_id": agreement["subscription"],
                                                                          "annual_agreement": agreement}})
        actor = {"user_id": "staff", "role": "admin"}
        async def apply_hold():
            member = await db.users.find_one({"user_id": "member"})
            await risk.review(db, {"env": "preview"}, original["billing_risk"]["id"], action(member, scopes=["new_purchase"]), actor)
        if hold_at == "before_admission": await apply_hold()
        # The existing provider read is harmless. A hold arriving during it
        # must win before the first schedule-changing request.
        calls = []
        if hold_at == "during_subscription_read":
            async def read(*args):
                await apply_hold()
                return {"id": agreement["subscription"], "schedule": None}
            original_thread = partner_offers.asyncio.to_thread
            async def thread(function, *args, **kwargs):
                if function is partner_offers.current_subscription: return await read()
                return await original_thread(function, *args, **kwargs)
            monkeypatch.setattr(partner_offers.asyncio, "to_thread", thread)
        else:
            monkeypatch.setattr(partner_offers, "current_subscription", lambda *a: {"id": agreement["subscription"], "schedule": None})
        monkeypatch.setattr(stripe.SubscriptionSchedule, "create", lambda **kw: calls.append(kw))
        with pytest.raises(HTTPException) as error:
            await partner_offers.schedule_year(db, await db.users.find_one({"user_id": "member"}), agreement)
        assert error.value.status_code == 403 and calls == []
    exercise(case)
