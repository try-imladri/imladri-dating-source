"""Verified financial state, allowance reversals and content-free Admin metadata."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import time

from fastapi import HTTPException
import pytest
import stripe

from backend import billing_adjustments as billing, free_plan, membership_admin, membership_plans
from backend.tests.test_complimentary_access import Body, exercise, request


def fixture(*, kind="topup", messages=100, gross=1200, total=None, paid=None, days=30):
    at = int(time.time()) - 100
    member = {"user_id": "member", "role": "member", "env": "preview", "tier": "paid",
        "stripe_subscription_id": "sub_fixture", "stripe_price_id": "price_fixture", "membership_plan_id": "plus_100",
        "subscription_status": "active", "subscription_period_start": at, "subscription_period_end": at + days * 86400,
        "topup_balance": 0}
    invoice = {"id": "in_fixture", "amount_paid": gross if paid is None else paid,
               "total": total or gross, "currency": "usd"}
    tx = {"_id": "checkout:fixture", "kind": kind, "user_id": "member", "session_id": "cs_fixture",
        "invoice_id": invoice["id"], "stripe_payment_intent_id": "pi_fixture", "stripe_charge_id": "ch_fixture",
        "amount": gross / 100, "currency": "usd", "env": "preview", "status": "completed", "payment_status": "paid",
        "created_at": datetime.now(timezone.utc).isoformat(), "fulfilled_at": datetime.now(timezone.utc).isoformat()}
    if kind != "topup":
        tx["billing_basis"] = billing.basis(invoice, "sub_fixture", "price_fixture", (at, at + days * 86400), messages, gross)
        tx["subscription_revenue_cents"] = gross
    else:
        tx.update(status="initiated", payment_status="pending")
        tx.pop("fulfilled_at")
        tx["topup_billing_basis"] = {"version": billing.VERSION, "session_id": "cs_fixture",
            "payment_intent_id": "pi_fixture", "gross_cents": gross, "currency": "usd", "messages": 100}
    state = {"id": "ch_fixture", "object": "charge", "paid": True, "currency": "usd", "amount": gross,
             "amount_refunded": 0, "payment_intent": "pi_fixture", "captured": True, "amount_captured": gross}
    return member, tx, state


def provider(monkeypatch, charge, disputes):
    monkeypatch.setattr(stripe.PaymentIntent, "retrieve", lambda *a, **kw:
                        {"id": "pi_fixture", "latest_charge": "ch_fixture"})
    monkeypatch.setattr(stripe.Charge, "retrieve", lambda *a, **kw: deepcopy(charge))
    monkeypatch.setattr(stripe.Dispute, "list", lambda **kw: {"data": deepcopy(disputes), "has_more": False})


async def reconcile(db, tx, **kwargs):
    return await billing.reconcile(db, tx, env="preview", charge_id="ch_fixture", payment_id="pi_fixture", **kwargs)


@pytest.mark.parametrize("status,expected", [("needs_response", 90), ("under_review", 90), ("warning_needs_response", 90),
                                            ("lost", 90), ("won", 100)])
def test_disputed_proportion_pending_hold_and_final_loss_are_distinct(monkeypatch, status, expected):
    member, tx, charge = fixture(); disputes = [{"id": "dp_one", "charge": "ch_fixture", "currency": "usd", "amount": 120, "status": status}]
    provider(monkeypatch, charge, disputes)
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        await reconcile(db, tx); await reconcile(db, tx)
        assert (await db.users.find_one({"user_id": "member"}))["topup_balance"] == expected
        count = await db.closed_membership_revenue.count_documents({"kind": {"$in": ["topup_adjustment", "subscription_adjustment"]}})
        assert count == (1 if status == "lost" else 0)
        if status in {"needs_response", "under_review", "warning_needs_response"}:
            assert await db.closed_membership_revenue.count_documents({"kind": "unresolved_adjustment", "data.status": "pending"}) == 1
    exercise(case)


def test_spent_refund_and_failed_refund_or_won_recovery_preserve_exact_consumption(monkeypatch):
    member, tx, charge = fixture(); disputes = []; provider(monkeypatch, charge, disputes)
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        await reconcile(db, tx)
        await db.users.update_one({"user_id": "member"}, {"$inc": {"topup_balance": -100}})
        charge["amount_refunded"] = 600; await reconcile(db, tx)
        assert (await db.users.find_one({"user_id": "member"}))["topup_balance"] == -50
        # Failed/cancelled refund is reconciled from today's Charge, not a stale event.
        charge["amount_refunded"] = 0; await reconcile(db, tx)
        assert (await db.users.find_one({"user_id": "member"}))["topup_balance"] == 0
        disputes.append({"id": "dp_one", "charge": "ch_fixture", "currency": "usd", "amount": 120, "status": "lost"})
        await reconcile(db, tx)
        assert (await db.users.find_one({"user_id": "member"}))["topup_balance"] == -10
        disputes[0]["status"] = "won"; await reconcile(db, tx)
        assert (await db.users.find_one({"user_id": "member"}))["topup_balance"] == 0
        assert sum(e["cents"] for e in await db.closed_membership_revenue.find({"kind": "topup_adjustment"}).to_list(10)) == 0
    exercise(case)


def test_subscription_refund_changes_only_its_period_and_keeps_saved_content_and_refills(monkeypatch):
    member, tx, charge = fixture(kind="purchase"); provider(monkeypatch, charge, [])
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        await db.exchanges.insert_one({"id": "saved", "user_id": "member", "response": "synthetic private content"})
        charge["amount_refunded"] = 600; await reconcile(db, tx)
        row = await db.users.find_one({"user_id": "member"}); assert free_plan.reply_usage(row)["message_limit"] == 50
        charge["amount_refunded"] = 1200; await reconcile(db, tx)
        await db.users.update_one({"user_id": "member"}, {"$set": {"topup_balance": 2}})
        row = await db.users.find_one({"user_id": "member"})
        assert free_plan.reply_usage(row)["message_limit"] == 0 and free_plan.reply_usage(row)["messages_remaining"] == 2
        async def generate(rid):
            await db.exchanges.insert_one({"user_id": "member", "id": rid}); return {"id": rid}
        async def restore(doc): return {"id": doc["id"]}
        await free_plan.run_metered(db, row, request("refill-after-plan-refund"), Body(), "exchange", "own", generate, restore)
        assert (await db.users.find_one({"user_id": "member"}))["topup_balance"] == 1
        assert await db.exchanges.find_one({"id": "saved"})
        await db.users.update_one({"user_id": "member"}, {"$set": {"subscription_period_start": member["subscription_period_end"],
            "subscription_period_end": member["subscription_period_end"] + 2592000}})
        # Query the explicit following month without manufacturing a current timestamp.
        future = await db.users.find_one({"user_id": "member"})
        monkeypatch.setattr(free_plan.time, "time", lambda: member["subscription_period_end"] + 100)
        assert free_plan.reply_usage(future)["message_limit"] == 100
    exercise(case)


def test_annual_and_part_credit_funding_are_not_reversed_as_an_unrelated_cycle(monkeypatch):
    member, tx, charge = fixture(kind="renewal", gross=600, total=1200, days=365)
    member["plan_offer"] = "couple_annual"; provider(monkeypatch, charge, [])
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        charge["amount_refunded"] = 600; await reconcile(db, tx)
        row = await db.users.find_one({"user_id": "member"})
        assert free_plan.reply_usage(row)["message_limit"] == 50
        monkeypatch.setattr(free_plan.time, "time", lambda: member["subscription_period_start"] + 40 * 86400)
        assert free_plan.reply_usage(row)["message_limit"] == 50
    exercise(case)


def test_fully_refunded_unused_cycle_with_zero_refills_cannot_generate_or_create_debt(monkeypatch):
    member, tx, charge = fixture(kind="purchase"); charge["amount_refunded"] = 1200; provider(monkeypatch, charge, [])
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        await reconcile(db, tx)
        row = await db.users.find_one({"user_id": "member"})
        async def never(rid): pytest.fail("A zero-funded period generated a message")
        with pytest.raises(HTTPException) as error:
            await free_plan.run_metered(db, row, request("zero-funded"), Body(), "exchange", "own", never, never)
        assert error.value.status_code == 402
        final = await db.users.find_one({"user_id": "member"})
        assert final["topup_balance"] == 0 and "free_reply_requests" not in final
    exercise(case)


@pytest.mark.parametrize("change", [{"captured": False, "amount_captured": 0}, {"amount_captured": 600}, {"amount_captured": True}])
def test_uncaptured_or_partially_captured_cash_never_funds_a_full_pack(monkeypatch, change):
    member, tx, charge = fixture(); charge.update(change); provider(monkeypatch, charge, [])
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        with pytest.raises(HTTPException) as error: await reconcile(db, tx)
        assert error.value.status_code == 503
        assert (await db.users.find_one({"user_id": "member"}))["topup_balance"] == 0
        assert await db.closed_membership_revenue.count_documents({"kind": "unresolved_adjustment", "data.status": "pending"}) == 1
    exercise(case)


@pytest.mark.parametrize("mismatch", ["transaction_currency", "account_environment"])
def test_local_currency_or_account_environment_mismatch_cannot_receive_credits(monkeypatch, mismatch):
    member, tx, charge = fixture(); provider(monkeypatch, charge, [])
    if mismatch == "transaction_currency": tx["currency"] = "eur"
    else: member["env"] = "live"
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        with pytest.raises(HTTPException) as error: await reconcile(db, tx)
        assert error.value.status_code == 503
        assert (await db.users.find_one({"user_id": "member"}))["topup_balance"] == 0
    exercise(case)


def test_fulfilled_legacy_refill_without_grant_map_is_reviewed_not_regranted(monkeypatch):
    member, tx, charge = fixture(); member["topup_balance"] = 100
    tx.update(payment_status="paid", status="completed", fulfilled_at=datetime.now(timezone.utc).isoformat())
    charge["amount_refunded"] = 600; provider(monkeypatch, charge, [])
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        with pytest.raises(HTTPException) as error: await reconcile(db, tx)
        assert error.value.status_code == 503
        assert (await db.users.find_one({"user_id": "member"}))["topup_balance"] == 100
        assert await db.closed_membership_revenue.count_documents({"kind": "unresolved_adjustment", "data.status": "pending"}) == 1
    exercise(case)


@pytest.mark.parametrize("mode", ["missing_basis", "partial_overlap", "paged_disputes"])
def test_ambiguous_cases_are_visible_admin_review_without_any_credit_mutation(monkeypatch, mode):
    member, tx, charge = fixture(kind="purchase"); provider(monkeypatch, charge, [])
    if mode == "missing_basis": tx.pop("billing_basis")
    if mode == "partial_overlap":
        charge["amount_refunded"] = 100
        monkeypatch.setattr(stripe.Dispute, "list", lambda **kw: {"data": [{"id": "dp", "charge": "ch_fixture",
            "currency": "usd", "amount": 100, "status": "lost"}], "has_more": False})
    if mode == "paged_disputes": monkeypatch.setattr(stripe.Dispute, "list", lambda **kw: {"data": [], "has_more": True})
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        with pytest.raises(HTTPException) as error: await reconcile(db, tx)
        assert error.value.status_code == 503
        row = await db.users.find_one({"user_id": "member"})
        assert "subscription_allowance_adjustments" not in row and row["topup_balance"] == 0
        assert await db.closed_membership_revenue.count_documents({"kind": "unresolved_adjustment", "data.status": "pending"}) == 1
        admin = await billing.member_records(db, "member", "preview", {"env": "preview"})
        assert admin["items"][0]["adjustment_status"] == "review_required" and len(admin["pending_reviews"]) == 1
    exercise(case)


def test_fully_credit_funded_invoice_does_not_invent_a_charge_or_false_503(monkeypatch):
    member, tx, _ = fixture(kind="renewal")
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        monkeypatch.setattr(stripe.InvoicePayment, "list", lambda **kw: pytest.fail("No cash charge exists"))
        result = await billing.prepare_subscription(db, tx, {"id": "in_zero", "amount_paid": 0, "total": 1200}, {},
            "sub_fixture", "price_fixture", (100, 200), 100, 0, env="preview")
        assert result["status"] == "fully_credited_invoice_no_cash_charge"
        assert "subscription_allowance_adjustments" not in await db.users.find_one({"user_id": "member"})
    exercise(case)


@pytest.mark.parametrize("stage", ["after_account", "after_event", "before_outbox_clear"])
def test_crash_boundaries_leave_one_effective_grant_and_one_financial_change(monkeypatch, stage):
    member, tx, charge = fixture(); charge["amount_refunded"] = 600; provider(monkeypatch, charge, [])
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        payment, receipts = db.payment_transactions, db.closed_membership_revenue
        tripped = [False]
        class Payments:
            def __getattr__(self, name): return getattr(payment, name)
            async def update_one(self, query, update, **kw):
                if stage == "after_account" and "billing_adjustment" in update.get("$set", {}) and not tripped[0]:
                    tripped[0] = True; raise RuntimeError("synthetic before summary")
                if stage == "before_outbox_clear" and "billing_adjustment_outbox" in update.get("$unset", {}) and not tripped[0]:
                    tripped[0] = True; raise RuntimeError("synthetic before clear")
                return await payment.update_one(query, update, **kw)
        class Receipts:
            def __getattr__(self, name): return getattr(receipts, name)
            async def update_one(self, query, update, **kw):
                result = await receipts.update_one(query, update, **kw)
                if stage == "after_event" and update.get("$setOnInsert", {}).get("kind") in ("topup_adjustment", "subscription_adjustment") and not tripped[0]:
                    tripped[0] = True; raise RuntimeError("synthetic financial publication ACK lost")
                return result
        class Database:
            users = db.users; payment_transactions = Payments(); events = db.events; closed_membership_revenue = Receipts()
        with pytest.raises(RuntimeError): await reconcile(Database(), tx)
        interim = await membership_admin.summary(db, "preview", {"env": "preview"}, datetime.now(timezone.utc))
        assert interim["net_revenue_verified"] is False and interim["monthly_net_revenue_cents"] is None
        assert (await billing.global_reviews(db, "preview"))["items"]
        await reconcile(db, tx)
        assert (await db.users.find_one({"user_id": "member"}))["topup_balance"] == 50
        items = await db.closed_membership_revenue.find({"kind": "topup_adjustment"}).to_list(10)
        assert len(items) == 1 and items[0]["cents"] == 600
    exercise(case)


def test_net_revenue_and_anonymous_retention_preserve_gross_and_offset_once(monkeypatch):
    member, tx, charge = fixture(kind="purchase"); provider(monkeypatch, charge, [])
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        charge["amount_refunded"] = 1200; await reconcile(db, tx)
        now = datetime.now(timezone.utc)
        summary = await membership_admin.summary(db, "preview", {"env": "preview"}, now)
        assert (summary["monthly_gross_revenue_cents"], summary["monthly_refund_dispute_adjustments_cents"], summary["monthly_net_revenue_cents"]) == (1200, 1200, 0)
        await membership_admin.retain_closed_revenue(db, "member", "preview")
        await db.payment_transactions.delete_many({"user_id": "member"}); await db.events.delete_many({"user_id": "member"})
        again = await membership_admin.summary(db, "preview", {"env": "preview"}, now)
        assert again["monthly_net_revenue_cents"] == 0 and again["monthly_gross_revenue_cents"] == 1200
        for row in await db.closed_membership_revenue.find({}).to_list(10):
            assert set(row) <= {"_id", "env", "month", "kind", "cents", "data", "pending_months"} and "member" not in str(row)
    exercise(case)


@pytest.mark.parametrize("owned", [True, False])
def test_postclosure_owned_adjustments_are_globally_visible_and_foreign_products_do_not_taint_net(monkeypatch, owned):
    async def case(db):
        await db.closed_membership_revenue.insert_one({"_id": "anonymous", "env": "preview",
            "month": datetime.now(timezone.utc).date().replace(day=1).isoformat(), "kind": "topup", "cents": 1200})
        obj = {"id": "ch_closed", "object": "charge", "payment_intent": "pi_closed", "amount": 1200,
            "amount_refunded": 1200, "metadata": {"lookup_key": membership_plans.TOPUP["lookup_key"] if owned else "other_app",
                                                   "user_id": "deleted-account", "topup_attempt": "retired"}}
        result = await billing.handle_event(db, obj, env="preview", source_event="charge.refunded")
        assert result["status"] == "unlinked_payment_not_applied"
        reviews = await billing.global_reviews(db, "preview")
        assert len(reviews["items"]) == int(owned)
        assert await db.users.count_documents({}) == await db.payment_transactions.count_documents({}) == 0
        summary = await membership_admin.summary(db, "preview", {"env": "preview"}, datetime.now(timezone.utc))
        assert summary["monthly_refill_gross_revenue_cents"] == 1200
        assert summary["net_revenue_verified"] is not owned
        assert summary["monthly_net_revenue_cents"] is None if owned else summary["monthly_net_revenue_cents"] == 0
    exercise(case)


def test_negative_refill_balance_blocks_new_payable_checkout_and_recovery_restores_availability(monkeypatch):
    from backend import server, purchase_policy
    from backend.tests.test_membership_checkout import item
    from starlette.requests import Request
    from types import SimpleNamespace
    monkeypatch.setattr(stripe, "api_key", "sk_test_fixture")
    monkeypatch.setenv("PERSONAL_BILLING_MODE", "enabled"); monkeypatch.setenv("STRIPE_CHECKOUT_MODE", "managed")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://billing.invalid")
    monkeypatch.setattr(server.stripe_membership, "catalog", lambda: {k: item(k) for k in server.stripe_membership.PRODUCTS})
    async def settings(): return {"upgrade": {}, "people_limit": None}
    async def offers(db, member): return None, {"monthly_eligible": False, "annual_eligible": False, "annual_pending": False}
    monkeypatch.setattr(server, "get_settings", settings); monkeypatch.setattr(server.partner_offers, "details", offers)
    created = []
    monkeypatch.setattr(stripe.checkout.Session, "create", lambda **kw:
        (created.append(kw) or SimpleNamespace(id="cs_new", url="https://checkout.invalid/new")))
    async def case(db):
        monkeypatch.setattr(server, "db", db)
        member, _, _ = fixture(); member.update(topup_balance=-5, stripe_customer_id="cus_fixture")
        await db.users.insert_one(member)
        body = server.RefillIn(origin_url="https://billing.invalid", expected_member="member",
            purchase_policy_accepted=True, purchase_policy_version=purchase_policy.VERSION)
        req = Request({"type": "http", "scheme": "https", "server": ("billing.invalid", 443), "path": "/", "headers": []})
        info = await server.upgrade_info(member)
        assert info["topup_available"] is False and "refund" in info["topup_blocked_reason"]
        with pytest.raises(HTTPException) as error: await server.purchase_refill(body, req, member)
        assert error.value.status_code == 409 and not created
        await db.users.update_one({"user_id": "member"}, {"$set": {"topup_balance": 0}})
        current = await db.users.find_one({"user_id": "member"})
        assert (await server.upgrade_info(current))["topup_available"] is True
        assert (await server.purchase_refill(body, req, current))["session_id"] == "cs_new" and len(created) == 1
    exercise(case)


def test_admin_billing_records_and_global_review_routes_enforce_rbac_and_metadata_only(monkeypatch):
    import httpx
    from backend import server, purchase_policy
    async def case(db):
        monkeypatch.setattr(server, "db", db)
        member, tx, _ = fixture(kind="purchase"); member["env"] = server.ENV; tx["env"] = server.ENV
        notice = purchase_policy.snapshot(datetime.now(timezone.utc).isoformat(), "plus_100")
        tx.update(private_text="SYNTHETIC_PRIVATE_MARKER", purchase_policy={**notice,
            "private_answer": "SYNTHETIC_PRIVATE_MARKER"})
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        monkeypatch.setitem(server.app.dependency_overrides, server.current_member, lambda: {"user_id": "member", "role": "member"})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
            paths = ["/api/users/member/billing-records", "/api/admin/membership/adjustment-reviews"]
            for path in paths: assert (await client.get(path)).status_code == 403
            monkeypatch.setitem(server.app.dependency_overrides, server.current_member, lambda: {"user_id": "admin", "role": "admin"})
            response = await client.get(paths[0]); assert response.status_code == 200
            assert "no-store" in response.headers["cache-control"] and "SYNTHETIC_PRIVATE_MARKER" not in response.text
            assert response.json()["items"][0]["purchase_policy"] == notice
            assert (await client.get("/api/users/other/billing-records")).status_code == 404
            assert (await client.get(paths[0] + "?limit=51")).status_code == 422
    exercise(case)


@pytest.mark.parametrize("cash,refunded,expected", [(1200, 600, 50), (1200, 1200, 0), (0, 0, 100)])
def test_checkout_refund_before_first_fulfillment_and_fully_credit_funded_invoice(monkeypatch, cash, refunded, expected):
    from backend import server, purchase_policy
    from backend.tests.test_plus_billing import invoice
    member, tx, charge = fixture(kind="purchase"); provider(monkeypatch, charge, [])
    charge["amount_refunded"] = refunded
    tx.update(status="initiated", payment_status="pending", plan_offer="standard", lookup_key=server.PLUS_KEY,
              stripe_price_id="price_fixture", purchase_policy=purchase_policy.snapshot(datetime.now(timezone.utc).isoformat(), "plus_100"))
    tx.pop("billing_basis"); tx.pop("fulfilled_at")
    member.update(tier="free"); member.pop("subscription_period_start"); member.pop("subscription_period_end")
    receipt = invoice(int(time.time()) - 10); receipt["amount_paid"] = cash
    session = {"id": "cs_fixture", "subscription": "sub_fixture", "customer": "cus_fixture",
               "payment_intent": "pi_fixture" if cash else None, "invoice": receipt}
    async def case(db):
        monkeypatch.setattr(server, "db", db)
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        if cash == 0:
            monkeypatch.setattr(stripe.Charge, "retrieve", lambda *a, **kw: pytest.fail("Fully credited invoice has no cash charge"))
        await server.mark_paid(tx, session)
        row = await db.users.find_one({"user_id": "member"})
        assert free_plan.reply_usage(row)["message_limit"] == expected
        payment = await db.payment_transactions.find_one({"_id": tx["_id"]})
        assert payment["payment_status"] == "paid" and payment["purchase_policy"] == tx["purchase_policy"]
    exercise(case)


@pytest.mark.parametrize("annual", [False, True])
def test_fulfilled_upgrade_or_annual_keeps_only_its_matching_policy_authorization(monkeypatch, annual):
    from backend import server, purchase_policy
    from types import SimpleNamespace
    at = int(time.time()) - 10; end = at + (365 if annual else 30) * 86400
    price_id = "price_annual" if annual else "price_upgrade"
    offer = "couple_annual" if annual else "plus_500"
    amount = 8000 if annual else 2250
    policy = purchase_policy.snapshot(datetime.now(timezone.utc).isoformat(), offer)
    member, _, _ = fixture(); member.update(stripe_price_id="price_old", plan_offer="partner_monthly" if annual else "standard")
    if annual:
        member["annual_agreement"] = {"subscription": "sub_fixture", "status": "pending", "annual_price": price_id,
            "starts": at, "ends": end, "purchase_policy": policy}
    else:
        member["plan_upgrade_authorizations"] = {offer: {"subscription": "sub_fixture", "price_id": price_id,
            "source_price": "price_old", "purchase_policy": policy}}
    receipt = {"id": "in_policy", "status": "paid", "currency": "usd", "billing_reason": "subscription_update",
        "amount_paid": amount, "total": amount, "total_excluding_tax": amount,
        "parent": {"subscription_details": {"subscription": "sub_fixture"}},
        "payments": {"data": [{"invoice": "in_policy", "status": "paid", "amount_paid": amount,
            "payment": {"type": "payment_intent", "payment_intent": "pi_fixture"}}], "has_more": False},
        "lines": {"data": [{"type": "subscription", "price": price_id, "amount": amount,
                              "period": {"start": at, "end": end}}]}}
    _, _, charge = fixture(gross=amount); provider(monkeypatch, charge, [])
    monkeypatch.setattr(server.partner_offers, "price_for", lambda key: SimpleNamespace(id=price_id))
    async def case(db):
        monkeypatch.setattr(server, "db", db)
        await db.users.insert_one(member)
        await server.renew_subscription(receipt)
        row = await db.payment_transactions.find_one({"invoice_id": "in_policy"})
        assert row["payment_status"] == "paid" and row["purchase_policy"] == policy
        monkeypatch.setattr(stripe.Charge, "retrieve", lambda *a, **kw: pytest.fail("Duplicate verified invoice should need no fresh cash read"))
        await server.renew_subscription(receipt)
        assert await db.payment_transactions.count_documents({"invoice_id": "in_policy"}) == 1
    exercise(case)


def test_local_presentment_details_never_change_the_verified_usd_accounting_basis(monkeypatch):
    member, tx, charge = fixture(); charge["presentment_details"] = {"presentment_currency": "eur", "presentment_amount": 1090}
    provider(monkeypatch, charge, [])
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        await reconcile(db, tx)
        assert (await db.users.find_one({"user_id": "member"}))["topup_balance"] == 100
        assert (await db.payment_transactions.find_one({"_id": tx["_id"]}))["billing_adjustment"]["state"]["currency"] == "usd"
    exercise(case)


def test_admin_taxed_original_cash_and_pending_net_are_never_guessed_from_list_price(monkeypatch):
    member, tx, charge = fixture(kind="purchase", gross=1320)
    tx.update(amount=12, subscription_revenue_cents=1200)
    tx["billing_basis"]["revenue_cents"] = 1200
    charge["amount_refunded"] = 660; provider(monkeypatch, charge, [])
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        await reconcile(db, tx)
        row = (await billing.member_records(db, "member", "preview", {"env": "preview"}))["items"][0]
        assert (row["original_amount_cents"], row["original_revenue_cents"], row["refund_dispute_offset_cents"], row["net_revenue_cents"]) == (1320, 1200, 600, 600)
        await billing.review_required(db, tx, "unverified_new_state", env="preview")
        pending = (await billing.member_records(db, "member", "preview", {"env": "preview"}))["items"][0]
        assert pending["adjustment_status"] == "review_required" and pending["net_revenue_cents"] is None
        legacy = {"_id": "legacy", "user_id": "member", "env": "preview", "kind": "purchase", "amount": 12, "currency": "usd"}
        await db.payment_transactions.insert_one(legacy)
        rows = (await billing.member_records(db, "member", "preview", {"env": "preview"}))["items"]
        assert next(r for r in rows if r["id"] == "legacy")["original_amount_cents"] is None
    exercise(case)


@pytest.mark.parametrize("race", [False, True])
def test_already_paid_new_pack_with_prior_refund_debt_needs_review_not_silent_partial_delivery(monkeypatch, race):
    from backend import server
    member, tx, charge = fixture(); member["topup_balance"] = 0 if race else -5
    provider(monkeypatch, charge, [])
    async def case(db):
        monkeypatch.setattr(server, "db", db)
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        users = db.users; injected = [False]
        class Users:
            def __getattr__(self, name): return getattr(users, name)
            async def update_one(self, query, update, **kwargs):
                if race and isinstance(update, list) and "topup_balance" in update[0].get("$set", {}) and not injected[0]:
                    injected[0] = True
                    await users.update_one({"user_id": "member"}, {"$set": {"topup_balance": -5}})
                return await users.update_one(query, update, **kwargs)
        class Database:
            users = Users(); payment_transactions = db.payment_transactions; events = db.events
            closed_membership_revenue = db.closed_membership_revenue
        with pytest.raises(HTTPException) as error: await reconcile(Database(), tx)
        assert error.value.status_code == 503
        owner = await db.users.find_one({"user_id": "member"}); assert owner["topup_balance"] == -5
        assert not owner.get("topup_grants")
        payment = await db.payment_transactions.find_one({"_id": tx["_id"]})
        assert payment["payment_status"] == "paid" and payment["payment_verified"] is True
        assert payment["credit_delivery_status"] == "review_required" and "fulfilled_at" not in payment
        status = await server.payment_status("cs_fixture", owner)
        assert status["review_required"] is True and status["payment_verified"] is True and status["payment_status"] == "paid"
        assert (await billing.global_reviews(db, "preview"))["items"]
    exercise(case)


def test_closed_account_webhook_keeps_owned_unattributed_review_without_resurrection(monkeypatch):
    from backend import server
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_synthetic_audit_only")
    obj = {"id": "ch_closed", "object": "charge", "payment_intent": "pi_closed", "amount": 1200,
        "amount_refunded": 1200, "metadata": {"user_id": "deleted", "topup_attempt": "retired",
                                             "lookup_key": membership_plans.TOPUP["lookup_key"]}}
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda *args:
        {"type": "charge.refunded", "created": int(time.time()), "data": {"object": obj}})
    async def case(db):
        monkeypatch.setattr(server, "db", db)
        result = await server.stripe_webhook(SimpleNamespace(headers={}, body=AsyncMock(return_value=b"{}")))
        assert result["review_required"] is True
        assert await db.users.count_documents({}) == 0
        assert len((await billing.global_reviews(db, server.ENV))["items"]) == 1
    exercise(case)


def test_closure_preserves_only_anonymous_unresolved_financial_state_not_false_verified_net(monkeypatch):
    from backend import accounts
    member, tx, charge = fixture(kind="purchase"); tx.pop("billing_basis")
    member["closure_billing_done"] = True  # This test covers retention, not provider cancellation.
    charge["amount_refunded"] = 600; provider(monkeypatch, charge, [])
    async def case(db):
        await db.users.insert_one(member); await db.payment_transactions.insert_one(tx)
        with pytest.raises(HTTPException): await reconcile(db, tx)
        assert (await membership_admin.summary(db, "preview", {"env": "preview"}, datetime.now(timezone.utc)))["net_revenue_verified"] is False
        await membership_admin.retain_closed_revenue(db, "member", "preview")
        await accounts.finish_closure(db, "member")
        assert await db.users.count_documents({}) == await db.payment_transactions.count_documents({}) == 0
        after = await membership_admin.summary(db, "preview", {"env": "preview"}, datetime.now(timezone.utc))
        assert after["monthly_gross_revenue_cents"] == 1200 and after["monthly_net_revenue_cents"] is None
        reviews = await billing.global_reviews(db, "preview")
        assert len(reviews["items"]) == 1 and reviews["items"][0]["user_id"] is None
        assert "allowance basis" in reviews["items"][0]["reason"]
        for row in await db.closed_membership_revenue.find({}).to_list(10):
            assert set(row) <= {"_id", "env", "month", "kind", "cents", "data", "pending_months"}
            assert "member" not in str(row) and "pi_fixture" not in str(row) and "ch_fixture" not in str(row)
    exercise(case)
