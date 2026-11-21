"""Account-side authority remains atomic across delayed financial workers."""
from datetime import datetime, timezone
import hashlib
import hmac

from fastapi import HTTPException
import httpx
from pymongo import ReturnDocument
import pytest
from starlette.requests import Request

from backend import billing_adjustments as billing, free_plan, membership_admin, postgres_gateway
from backend.postgres_remote import RemotePostgresDatabase
from backend.tests.test_billing_adjustments import exercise, fixture, provider, reconcile


@pytest.mark.parametrize("kind", ["purchase", "topup"])
@pytest.mark.parametrize("handoff_at", ["credit_dispatch", "expired_dispatch", "delayed_reservation"])
def test_financial_account_fence_blocks_stale_worker_at_sql_dispatch(monkeypatch, kind, handoff_at):
    member, tx, charge = fixture(kind=kind)
    charge["amount_refunded"] = 600
    provider(monkeypatch, charge, [])

    async def case(db):
        uid = member["user_id"]
        key = hashlib.sha256(tx["stripe_payment_intent_id"].encode()).hexdigest()
        fence = "billing_adjustment_fences." + key
        applied = "billing_adjustment_sequences." + key
        switched = []

        async def handoff():
            assert not switched
            if handoff_at == "expired_dispatch":
                # The separate payment-lease read already passed. Expiry must
                # still be checked by the actual account mutation's clock.
                await db.users.update_one({"user_id": uid}, {"$set": {
                    fence + ".expires": datetime(2000, 1, 1, tzinfo=timezone.utc)}})
            else:
                await db.payment_transactions.update_one({"_id": tx["_id"]}, {"$set": {
                    "billing_reconciliation.expires": "2000-01-01T00:00:00+00:00"}})
                leased = await db.payment_transactions.find_one_and_update(
                    {"_id": tx["_id"], "billing_reconciliation.expires": {"$lte": billing.current_time()}},
                    {"$set": {"billing_reconciliation": {"id": "new-worker",
                        "expires": "2099-01-01T00:00:00+00:00"}},
                     "$inc": {"billing_reconciliation_sequence": 1}}, return_document=ReturnDocument.AFTER)
                assert leased and leased["billing_reconciliation_sequence"] == 2
                reserved = await db.users.update_one({"user_id": uid, "$and": [
                    {"$or": [{applied: {"$exists": False}}, {applied: {"$lt": 2}}]},
                    {"$or": [{fence + ".sequence": {"$exists": False}}, {fence + ".sequence": {"$lt": 2}}]}]},
                    [{"$set": {fence: {"id": "new-worker", "sequence": 2,
                        "expires": {"$add": ["$$NOW", 120000]}}}}])
                assert reserved.matched_count == 1
                if handoff_at == "delayed_reservation":
                    # A newer provider read fails before publishing credit.
                    # Its reserved sequence must survive token cleanup.
                    await db.users.update_one({"user_id": uid, fence + ".id": "new-worker"},
                        {"$set": {fence + ".id": None,
                            fence + ".expires": datetime(2000, 1, 1, tzinfo=timezone.utc)}})
            switched.append(True)

        class Users:
            async def update_one(self, query, update, **kwargs):
                fields = update[0].get("$set", {}) if isinstance(update, list) else {}
                reservation, credit_write = fence in fields, applied in fields
                if not switched and ((handoff_at == "delayed_reservation" and reservation)
                        or (handoff_at != "delayed_reservation" and credit_write)):
                    await handoff()
                return await db.users.update_one(query, update, **kwargs)

            def __getattr__(self, name):
                return getattr(db.users, name)

        class Database:
            users = Users()

            def __getattr__(self, name):
                return getattr(db, name)

        await db.users.insert_one(member)
        await db.payment_transactions.insert_one(tx)
        with pytest.raises(HTTPException) as refused:
            await reconcile(Database(), tx)
        assert refused.value.status_code == 503 and switched
        current = await db.users.find_one({"user_id": uid})
        assert "subscription_allowance_adjustments" not in current
        assert "topup_grants" not in current and current["topup_balance"] == 0
        assert "billing_adjustment_sequences" not in current
        if handoff_at == "delayed_reservation":
            assert current["billing_adjustment_fences"][key]["sequence"] == 2
            assert current["billing_adjustment_fences"][key]["id"] is None
        assert not (await db.payment_transactions.find_one({"_id": tx["_id"]})).get("billing_adjustment")
        assert await db.events.count_documents({"type": "billing_financial_adjustment"}) == 0
        assert await db.closed_membership_revenue.count_documents({
            "kind": {"$in": ["topup_adjustment", "subscription_adjustment"]}}) == 0

    exercise(case)


@pytest.mark.parametrize("kind", ["purchase", "topup"])
def test_refund_and_restoration_cross_signed_gateway_with_repository_date_fence(monkeypatch, kind):
    member, tx, charge = fixture(kind=kind)
    provider(monkeypatch, charge, [])
    secret, origin = "synthetic-billing-fence-signing-secret", "synthetic-billing-origin"
    monkeypatch.setenv("IMLADRI_POSTGRES_GATEWAY_SECRET", secret)

    async def case(native):
        statuses = []

        async def app(scope, receive, send):
            supplied = [value for key, value in scope["headers"] if key.lower() == b"x-imladri-origin"]
            assert len(supplied) == 1 and hmac.compare_digest(supplied[0], origin.encode())
            response = await postgres_gateway.endpoint(Request(scope, receive), native)
            statuses.append(response.status_code)
            await response(scope, receive, send)

        remote = RemotePostgresDatabase("https://imladri-fixture.onrender.com", secret, origin, native.name,
                                        transport=httpx.ASGITransport(app=app))
        try:
            await remote.users.insert_one(member)
            await remote.payment_transactions.insert_one(tx)
            charge["amount_refunded"] = 600
            await reconcile(remote, tx)
            changed = await remote.users.find_one({"user_id": member["user_id"]})
            if kind == "topup":
                assert changed["topup_balance"] == 50
            else:
                assert free_plan.reply_usage(changed)["message_limit"] == 50
            charge["amount_refunded"] = 0
            await reconcile(remote, tx)
            restored = await remote.users.find_one({"user_id": member["user_id"]})
            if kind == "topup":
                assert restored["topup_balance"] == 100
            else:
                assert free_plan.reply_usage(restored)["message_limit"] == 100
            key = hashlib.sha256(tx["stripe_payment_intent_id"].encode()).hexdigest()
            authority = restored["billing_adjustment_fences"][key]
            assert authority["sequence"] == 2 and authority["id"] is None
            assert isinstance(authority["expires"], datetime)
            assert restored["billing_adjustment_sequences"][key] == 2
            money = await remote.closed_membership_revenue.find({
                "kind": {"$in": ["topup_adjustment", "subscription_adjustment"]}}).to_list(10)
            assert len(money) == 2 and sum(row["cents"] for row in money) == 0
            assert all(set(row) == {"_id", "env", "month", "kind", "cents"} for row in money)
            assert await remote.events.count_documents({"type": "billing_financial_adjustment"}) == 0
            assert statuses and all(status == 200 for status in statuses)
        finally:
            await remote.aclose()

    exercise(case)


def test_old_success_cannot_resolve_newer_crashed_financial_journal(monkeypatch):
    member, tx, charge = fixture(kind="purchase")
    charge["amount_refunded"] = 600
    provider(monkeypatch, charge, [])

    async def case(db):
        await db.users.insert_one(member)
        await db.payment_transactions.insert_one(tx)
        tripped = []

        class FailingPayments:
            def __getattr__(self, name):
                return getattr(db.payment_transactions, name)

            async def update_one(self, query, update, **kwargs):
                if "billing_adjustment" in update.get("$set", {}):
                    raise RuntimeError("Synthetic successor summary write failure")
                return await db.payment_transactions.update_one(query, update, **kwargs)

        class NewWorker:
            users = db.users
            events = db.events
            closed_membership_revenue = db.closed_membership_revenue
            payment_transactions = FailingPayments()

        review_id = billing.anonymous_review_id("stripe:review:" + billing.digest({"transaction": str(tx["_id"])}), "preview")

        class Reviews:
            def __getattr__(self, name):
                return getattr(db.closed_membership_revenue, name)

            async def update_one(self, query, update, **kwargs):
                if update.get("$set", {}).get("data.status") == "resolved" and not tripped:
                    # A finished its partial-refund summary, but has not yet
                    # resolved its review. B acquires the expired lease and
                    # changes credit for a newer full refund, then crashes
                    # before its own financial summary/outbox is durable.
                    tripped.append(True)
                    await db.payment_transactions.update_one({"_id": tx["_id"]}, {"$set": {
                        "billing_reconciliation.expires": "2000-01-01T00:00:00+00:00"}})
                    charge["amount_refunded"] = 1200
                    with pytest.raises(RuntimeError):
                        await reconcile(NewWorker(), tx)
                    pending = await db.closed_membership_revenue.find_one({"_id": review_id})
                    assert pending["data"]["status"] == "pending"
                return await db.closed_membership_revenue.update_one(query, update, **kwargs)

        class OldWorker:
            users = db.users
            payment_transactions = db.payment_transactions
            events = db.events
            closed_membership_revenue = Reviews()

        try:
            await reconcile(OldWorker(), tx)
        except HTTPException as stale:
            assert stale.status_code == 503
        assert tripped
        current = await db.users.find_one({"user_id": member["user_id"]})
        assert next(iter(current["subscription_allowance_adjustments"].values()))["net_messages"] == 0
        pending = await db.closed_membership_revenue.find_one({"_id": review_id})
        assert pending["data"]["status"] == "pending"
        summary = await membership_admin.summary(db, "preview", {"env": "preview"}, datetime.now(timezone.utc))
        assert summary["net_revenue_verified"] is False
        assert summary["monthly_net_revenue_cents"] is None

    exercise(case)


@pytest.mark.parametrize("late_at", ["after_retention_before_purge", "after_owner_purge"])
def test_already_started_review_survives_closure_without_identity_resurrection(late_at):
    member, tx, _ = fixture(kind="purchase")
    tx.update(billing_reconciliation_sequence=1, billing_reconciliation={
        "id": "already-started-worker", "expires": "2099-01-01T00:00:00+00:00"})

    async def case(db):
        uid = member["user_id"]
        await db.users.insert_one(member)
        await db.payment_transactions.insert_one(tx)
        # An existing writer obtained context before closure marked the owner.
        # Exercise retention/purge interleavings without any Stripe cancellation
        # or account-identity call; full closure has a separate integration test.
        await db.users.update_one({"user_id": uid}, {"$set": {"closing": True}})
        await membership_admin.retain_closed_revenue(db, uid, "preview")

        async def late_review():
            await billing.review_required(db, tx, "provider_payment_verification_unavailable", env="preview",
                charge_id="ch_fixture", payment_id="pi_fixture", sequence=1, token="already-started-worker")

        if late_at == "after_retention_before_purge":
            await late_review()
        await db.payment_transactions.delete_many({"user_id": uid})
        await db.events.delete_many({"user_id": uid})
        await db.users.delete_one({"user_id": uid})
        if late_at == "after_owner_purge":
            await late_review()
        assert await db.users.count_documents({}) == await db.payment_transactions.count_documents({}) == 0
        assert await db.events.count_documents({"user_id": uid}) == 0
        for row in await db.closed_membership_revenue.find({}).to_list(10):
            assert set(row) <= {"_id", "env", "month", "kind", "cents", "data", "pending_months"}
            if "data" in row:
                assert set(row["data"]) <= {"reason", "status", "reconciliation_sequence", "reconciliation_token"}
            assert not any(value in str(row) for value in (uid, "ch_fixture", "pi_fixture", str(tx["_id"])))
        result = await membership_admin.summary(db, "preview", {"env": "preview"}, datetime.now(timezone.utc))
        assert result["monthly_gross_revenue_cents"] == 1200
        assert result["net_revenue_verified"] is False and result["monthly_net_revenue_cents"] is None

    exercise(case)
