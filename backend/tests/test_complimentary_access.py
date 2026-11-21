"""Complimentary access is an audited member entitlement, not a paid/staff role."""
import asyncio
from datetime import datetime, timezone
import os
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel
import pytest
from starlette.background import BackgroundTasks
from starlette.requests import Request

from backend import free_plan, membership_admin, message_charge, message_costs
from backend.postgres_store import PostgresDatabase

DSN = os.environ.get("IMLADRI_TEST_POSTGRES_DSN")


def exercise(callback):
    if not DSN:
        pytest.skip("Requires an isolated loopback PostgreSQL fixture.")
    assert urlsplit(DSN).hostname in {"127.0.0.1", "localhost", "::1"}
    async def run():
        db = PostgresDatabase(DSN, "fixture_complimentary_" + uuid4().hex, allow_local_plaintext=True)
        try:
            await callback(db)
        finally:
            def cleanup(cursor, now):
                for table in ("rpc_requests", "unique_keys", "indexes", "documents", "collections"):
                    cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (db.name,))
            await db._run(cleanup, write="__fixture_cleanup")
            await db.aclose()
    asyncio.run(run())


def grant(active=True):
    return {"version": free_plan.COMPLIMENTARY_VERSION, "id": str(uuid4()), "active": active}


def member(**changes):
    return {"user_id": "member", "role": "member", "tier": "free", "env": "preview", "topup_balance": 8,
            "complimentary_entitlement": grant(), **changes}


class Body(BaseModel):
    text: str = "Fictional sample"


def request(key="same-message"):
    return Request({"type": "http", "headers": [(b"idempotency-key", key.encode())]})


@pytest.mark.parametrize("change", [{"role": "admin"}, {"role": "coach"}, {"closing": True},
    {"complimentary_entitlement": {"version": free_plan.COMPLIMENTARY_VERSION, "id": "bad", "active": True}},
    {"complimentary_entitlement": {"version": free_plan.COMPLIMENTARY_VERSION, "id": str(uuid4()), "active": 1}},
    {"complimentary_entitlement": grant(False)}, {"complimentary_entitlement": [grant()]}])
def test_only_exact_active_ordinary_member_grant_is_recognized(change):
    assert not free_plan.complimentary_access(member(**change))


def test_usage_is_distinct_from_staff_paid_and_credits():
    row = member(complimentary_reply_totals={"used": 800}, paid_reply_cycles={"unrelated": {"used": 9}})
    usage = free_plan.reply_usage(row)
    assert free_plan.has_personal_access(row) and free_plan.has_member_access(row)
    assert free_plan.paid_cycle(row) is None and free_plan.billing_tier(row) == "complimentary"
    assert usage["plan"] == "complimentary" and usage["plan_id"] == "complimentary_unlimited" and usage["unlimited"] is True
    assert usage["messages_used"] == 800 and usage["usage_window"] == "all_time" and usage["messages_remaining"] is None
    assert usage["topup_remaining"] == 0 and usage["period_end"] is None
    assert membership_admin.membership_status(row, usage, 0) == "complimentary" and row["role"] == "member"


def test_atomic_grant_retry_revoke_and_old_grant_cannot_reactivate():
    async def case(db):
        original = {"user_id": "member", "role": "member", "env": "preview", "tier": "free",
                    "private_answer": "must remain unchanged", "questionnaire_research_version": None}
        await db.users.insert_one(original)
        body = membership_admin.ComplimentaryAccess(expected_member="member", request_id=str(uuid4()), enabled=True, reason="Owner authorized support")
        accepted = await membership_admin.set_complimentary_access(db, {"env": "preview"}, "member", body, "owner-authorized-support")
        assert accepted["complimentary"] and accepted["duplicate"] is False
        assert (await membership_admin.set_complimentary_access(db, {"env": "preview"}, "member", body, "owner-authorized-support"))["duplicate"]
        row = await db.users.find_one({"user_id": "member"})
        assert len(row["admin_member_actions"]) == 1 and row["admin_member_actions"][0]["entitlement_id"] == body.request_id
        assert row["role"] == "member" and row["tier"] == "free" and not row.get("stripe_subscription_id")
        assert row["private_answer"] == original["private_answer"] and row["questionnaire_research_version"] is None
        revoke = membership_admin.ComplimentaryAccess(expected_member="member", request_id=str(uuid4()), enabled=False,
            expected_entitlement_id=body.request_id, reason="Support revocation")
        assert (await membership_admin.set_complimentary_access(db, {"env": "preview"}, "member", revoke, "owner-authorized-support"))["complimentary"] is False
        assert (await membership_admin.set_complimentary_access(db, {"env": "preview"}, "member", revoke, "owner-authorized-support"))["duplicate"] is True
        assert len((await db.users.find_one({"user_id": "member"}))["admin_member_actions"]) == 2
        with pytest.raises(HTTPException) as denied:
            await membership_admin.set_complimentary_access(db, {"env": "preview"}, "member", body, "owner-authorized-support")
        assert denied.value.status_code == 409
    exercise(case)


@pytest.mark.parametrize("block", [{"stripe_subscription_id": "sub_existing"}, {"checkout_attempt": {"id": "pending"}},
    {"topup_checkout_attempt": {"id": "pending"}}, {"role": "admin"}, {"closing": True}])
def test_grant_never_silently_changes_existing_billing_staff_or_closed_accounts(block):
    async def case(db):
        row = {"user_id": "member", "role": "member", "env": "preview", **block}
        await db.users.insert_one(row)
        body = membership_admin.ComplimentaryAccess(expected_member="member", request_id=str(uuid4()), enabled=True, reason="Owner authorized")
        with pytest.raises(HTTPException):
            await membership_admin.set_complimentary_access(db, {"env": "preview"}, "member", body, "owner-authorized-support")
        after = await db.users.find_one({"user_id": "member"})
        assert "complimentary_entitlement" not in after and "admin_member_actions" not in after
        assert all(after[key] == value for key, value in row.items())
    exercise(case)


@pytest.mark.parametrize("kind", ["exchange", "couples_tone"])
def test_unlimited_messages_keep_real_cost_retry_background_and_idempotency_without_debit(kind):
    async def case(db):
        row = member(); await db.users.insert_one(row)
        background = BackgroundTasks(); generated = []
        scope = {"document": "own-draft", "slot": "tone_coach"} if kind == "couples_tone" else "own-person"
        async def provider_attempt(retry=False):
            with message_costs.generation_context("preview", kind):
                async with message_costs.provider_call(db, (message_costs.key_id(), False), "deepseek-flash", retry) as result:
                    result.status = "succeeded"
                    result.envelope = {"model": "deepseek-flash", "created": int(datetime.now(timezone.utc).timestamp()),
                        "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9,
                                  "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 7}}
        async def generate(rid):
            generated.append(rid)
            await provider_attempt(False); await provider_attempt(True)
            if kind == "couples_tone":
                await db.couples_drafts.insert_one({"_id": "own-draft", "tone_coach": {"id": rid}})
            else:
                await db.exchanges.insert_one({"user_id": row["user_id"], "id": rid, "answer": "Fictional"})
            await message_charge.enqueue(db, background, provider_attempt)
            return {"id": rid}
        async def restore(doc): return {"id": doc["id"]}
        result = await free_plan.run_metered(db, row, request(), Body(), kind, scope, generate, restore)
        retry = await free_plan.run_metered(db, await db.users.find_one({"user_id": "member"}), request(), Body(), kind, scope, generate, restore)
        assert retry == result and len(generated) == 1
        await background()
        after = await db.users.find_one({"user_id": "member"})
        assert after["complimentary_reply_totals"] == {"claimed": 1, "used": 1}
        assert after["topup_balance"] == 8 and "paid_reply_cycles" not in after and "stripe_subscription_id" not in after
        unit = await db.message_cost_units.find_one({"_id": result["id"]})
        assert unit["status"] == "settled" and unit["pool"] == "complimentary" and unit["background_pending"] == 0
        assert unit["workspace"] == ("couples" if kind == "couples_tone" else "individual")
        calls = await db.ai_call_ledger.find({"message_id": result["id"]}).to_list(10)
        assert len(calls) == 3 and sum(bool(c["retry"]) for c in calls) == 1
        assert sum(c["mode"] == "background" for c in calls) == 1 and all(c["message_run"] == 1 for c in calls)
        assert all(c["workspace"] == unit["workspace"] and c["input_tokens"] == 7 for c in calls)
    exercise(case)


def test_unlimited_is_not_a_large_fake_cap_and_pause_or_revocation_prevent_generation():
    async def case(db):
        row = member(complimentary_reply_totals={"claimed": 1000000, "used": 1000000})
        await db.users.insert_one(row)
        async def generate(rid):
            await db.exchanges.insert_one({"user_id": "member", "id": rid}); return {"id": rid}
        async def restore(doc): return {"id": doc["id"]}
        await free_plan.run_metered(db, row, request("over-a-million"), Body(), "exchange", "own", generate, restore)
        assert (await db.users.find_one({"user_id": "member"}))["complimentary_reply_totals"]["used"] == 1000001
        await db.users.update_one({"user_id": "member"}, {"$set": {"replies_paused": True}})
        async def never(rid): pytest.fail("paused/revoked account generated")
        with pytest.raises(HTTPException) as paused:
            await free_plan.run_metered(db, row, request("paused"), Body(), "exchange", "own", never, restore)
        assert paused.value.status_code == 403
        await db.users.update_one({"user_id": "member"}, {"$set": {"replies_paused": False, "complimentary_entitlement.active": False}})
        with pytest.raises(HTTPException) as revoked:
            await free_plan.run_metered(db, row, request("revoked"), Body(), "exchange", "own", never, restore)
        assert revoked.value.status_code == 409
    exercise(case)


def test_failed_generation_refunds_no_credits_and_retry_reuses_message_with_new_cost_run():
    async def case(db):
        row = member(); await db.users.insert_one(row)
        attempts = []
        async def fail(rid): attempts.append(rid); raise RuntimeError("synthetic failure")
        async def restore(doc): return {"id": doc["id"]}
        with pytest.raises(RuntimeError):
            await free_plan.run_metered(db, row, request(), Body(), "exchange", "own", fail, restore)
        failed = await db.users.find_one({"user_id": "member"})
        assert failed["complimentary_reply_totals"]["claimed"] == 0 and failed["topup_balance"] == 8
        assert (await db.message_cost_units.find_one({"_id": attempts[0]}))["status"] == "refunded"
        async def success(rid):
            attempts.append(rid); await db.exchanges.insert_one({"user_id": "member", "id": rid}); return {"id": rid}
        output = await free_plan.run_metered(db, failed, request(), Body(), "exchange", "own", success, restore)
        assert attempts == [output["id"], output["id"]]
        final = await db.users.find_one({"user_id": "member"})
        assert final["complimentary_reply_totals"] == {"claimed": 1, "used": 1} and final["topup_balance"] == 8
        assert (await db.message_cost_units.find_one({"_id": output["id"]}))["sequence"] == 5
    exercise(case)


def test_same_key_concurrency_reserves_one_generation_and_one_cost_unit():
    async def case(db):
        row = member(); await db.users.insert_one(row)
        entered, release = asyncio.Event(), asyncio.Event(); generated = []
        async def generate(rid):
            generated.append(rid); entered.set(); await release.wait()
            await db.exchanges.insert_one({"user_id": "member", "id": rid}); return {"id": rid}
        async def restore(doc): return {"id": doc["id"]}
        first = asyncio.create_task(free_plan.run_metered(db, row, request(), Body(), "exchange", "own", generate, restore))
        await entered.wait()
        with pytest.raises(HTTPException) as pending:
            await free_plan.run_metered(db, row, request(), Body(), "exchange", "own", generate, restore)
        assert pending.value.status_code == 409
        release.set(); result = await first
        assert await free_plan.run_metered(db, row, request(), Body(), "exchange", "own", generate, restore) == result
        assert len(generated) == 1 and await db.message_cost_units.count_documents({}) == 1
        after = await db.users.find_one({"user_id": "member"})
        assert after["complimentary_reply_totals"] == {"claimed": 1, "used": 1} and after["topup_balance"] == 8
    exercise(case)


def test_grant_reads_only_access_fields_and_checkout_race_prevents_grant_and_audit():
    async def case(db):
        await db.users.insert_one({"user_id": "member", "role": "member", "env": "preview", "private_answers": "not selected"})
        users = db.users
        class GuardedUsers:
            projected = False
            async def find_one(self, query, projection):
                assert "private_answers" not in projection and set(projection) <= {
                    "user_id", "role", "closing", "complimentary_entitlement", "stripe_subscription_id", "checkout_attempt", "topup_checkout_attempt"}
                self.projected = True
                return await users.find_one(query, projection)
            async def update_one(self, query, update):
                await users.update_one({"user_id": "member"}, {"$set": {"checkout_attempt": {"id": "racing-checkout"}}})
                return await users.update_one(query, update)
        guarded = GuardedUsers()
        class Database:
            users = guarded
        body = membership_admin.ComplimentaryAccess(expected_member="member", request_id=str(uuid4()), enabled=True, reason="Owner authorized")
        with pytest.raises(HTTPException) as changed:
            await membership_admin.set_complimentary_access(Database(), {"env": "preview"}, "member", body, "owner-authorized-support")
        assert changed.value.status_code == 409 and guarded.projected
        row = await users.find_one({"user_id": "member"})
        assert "complimentary_entitlement" not in row and "admin_member_actions" not in row
    exercise(case)
