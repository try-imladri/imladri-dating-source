"""Quick Advice uses the existing allowance with owner/workspace/privacy fences."""
import asyncio
import ast
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException

from backend import accounts, couples_compatibility, free_plan, intro_access, message_costs, quick_advice, trends
from backend.postgres_migration import COLLECTION_DOMAINS
from backend.tests.test_admin_answer_history import run_case
from backend.tests.test_intro_access import configuration, grant, member as intro_member
from backend.tests.test_complimentary_access import grant as unlimited_grant


def person(uid="owner", **extra):
    stamp = int(time.time())
    return {"user_id": uid, "env": "preview", "role": "member", "tier": "paid",
            "stripe_subscription_id": "sub-" + uid, "subscription_period_start": stamp - 60,
            "subscription_period_end": stamp + 86400, "about": {"demographics": {"age": 30}},
            "couples_adult_at": "2026-01-01T00:00:00+00:00", **extra}


def output(**extra):
    return {"what_they_are_doing": "You described a changed plan.", "what_it_means": "They may be busy; ask rather than assume.",
            "best_move": "Ask one clarifying question.", "what_not_to_do": "Avoid assuming their motive.",
            "what_to_say": "Would another day work?", "confidence": "low", "unknowns": ["Their availability"], **extra}


async def harness(db, generate=None):
    calls = []
    async def current(x_user: str = Header(default="owner")):
        row = await db.users.find_one({"user_id": x_user})
        if not row:
            raise HTTPException(401, "No account")
        return await free_plan.initialize_usage(db, row)
    async def ai(*args):
        calls.append({"args": args, "message": message_costs.current_message.get(), "run": message_costs.current_run.get()})
        return await generate(*args) if generate else output()
    async def profile(member):
        return "Own profile for " + member["user_id"]
    for name, indexes in quick_advice.INDEXES.items():
        await db[name].create_indexes(indexes)
    app = FastAPI()
    app.include_router(quick_advice.make_router(lambda: db, current, ai, profile), prefix="/api")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture"), calls


def send_args(key="one", owner="owner", **body):
    return {"json": {"question": "They changed the plan. How can I ask about another day?", **body},
            "headers": {"Idempotency-Key": key, "X-User": owner}}


@pytest.mark.parametrize("plan,limit", [("plus_100", 100), ("plus_250", 250), ("plus_500", 500)])
def test_plan_meter_retry_history_outcome_and_delete_without_people(plan, limit):
    async def case(db):
        await db.users.insert_one(person(membership_plan_id=plan))
        api, calls = await harness(db)
        async with api:
            first = await api.post("/api/quick-advice", **send_args())
            assert first.status_code == 200, first.text
            again = await api.post("/api/quick-advice", **send_args())
            assert again.json() == first.json() and len(calls) == 1
            rid = first.json()["id"]
            assert calls[0]["message"] == (rid, "individual") and calls[0]["run"] == 1
            assert await db.people.count_documents({}) == 0
            usage = free_plan.reply_usage(await db.users.find_one({"user_id": "owner"}))
            assert usage["message_limit"] == limit and usage["messages_used"] == 1
            history = await api.get("/api/quick-advice")
            assert history.headers["cache-control"] == "private, no-store"
            assert history.json()["total"] == 1 and history.json()["items"][0]["id"] == rid
            changed_key = await api.post("/api/quick-advice", **send_args(question="A different situation"))
            assert changed_key.status_code == 409 and len(calls) == 1
            outcome = await api.patch(f"/api/quick-advice/{rid}/outcome", json={"text": "We found another day.", "helped": "helped"})
            assert outcome.status_code == 200
            exported = json.loads("".join([chunk async for chunk in accounts.export_json(db, person())]))
            assert exported["quick_advice"][0]["outcome"] == "We found another day."
            follow = await api.post("/api/quick-advice", **send_args("two", parent_id=rid, question="What should I consider next?"))
            assert follow.status_code == 200 and follow.json()["thread_id"] == rid
            prompt = json.loads(calls[-1]["args"][2])
            assert prompt["member_reported_outcome"] == "We found another day."
            assert (await api.delete(f"/api/quick-advice/{rid}")).status_code == 200
            assert (await api.get("/api/quick-advice")).json()["total"] == 0
            retry_deleted = await api.post("/api/quick-advice", **send_args())
            assert retry_deleted.status_code == 404 and len(calls) == 2
            assert not any([row.get("question") or row.get("response") async for row in db.quick_advice.find({})])
    run_case(case)


def test_unpaid_paused_exhausted_and_invalid_input_never_dispatch():
    async def case(db):
        await db.users.insert_one(person(tier="unsubscribed", stripe_subscription_id=None))
        api, calls = await harness(db)
        async with api:
            assert (await api.post("/api/quick-advice", **send_args())).status_code == 402
            await db.users.replace_one({"user_id": "owner"}, person(replies_paused=True))
            assert (await api.post("/api/quick-advice", **send_args())).status_code == 403
            p = person()
            p["paid_reply_cycles"] = {free_plan.paid_cycle(p): {"claimed": 100, "used": 100}}
            await db.users.replace_one({"user_id": "owner"}, p)
            assert (await api.post("/api/quick-advice", **send_args())).status_code == 402
            assert (await api.post("/api/quick-advice", **send_args(question=" "))).status_code == 422
            assert (await api.post("/api/quick-advice", **send_args(question="x" * 6001))).status_code == 422
            assert not calls
    run_case(case)


def test_bad_provider_output_refunds_then_same_request_retries_once():
    async def case(db):
        await db.users.insert_one(person())
        count = 0
        async def generate(*_):
            nonlocal count
            count += 1
            return {} if count == 1 else output()
        api, calls = await harness(db, generate)
        async with api:
            assert (await api.post("/api/quick-advice", **send_args())).status_code == 502
            assert free_plan.reply_usage(await db.users.find_one({"user_id": "owner"}))["messages_used"] == 0
            second = await api.post("/api/quick-advice", **send_args())
            assert second.status_code == 200
            assert calls[0]["message"] == calls[1]["message"] and calls[1]["run"] == 2
            unit = await db.message_cost_units.find_one({"_id": second.json()["id"]})
            assert unit["kind"] == "quick_advice" and unit["status"] == "settled"
    run_case(case)


def test_concurrent_identical_send_has_one_generation_and_one_charge():
    async def case(db):
        await db.users.insert_one(person())
        entered, release = asyncio.Event(), asyncio.Event()
        async def generate(*_):
            entered.set()
            await release.wait()
            return output()
        api, calls = await harness(db, generate)
        async with api:
            first = asyncio.create_task(api.post("/api/quick-advice", **send_args()))
            await entered.wait()
            second = await api.post("/api/quick-advice", **send_args())
            assert second.status_code == 409
            release.set()
            assert (await first).status_code == 200
            assert len(calls) == 1
            assert free_plan.reply_usage(await db.users.find_one({"user_id": "owner"}))["messages_used"] == 1
    run_case(case)


def test_owner_cannot_read_modify_follow_or_delete_another_owner():
    async def case(db):
        await db.users.insert_many([person(), person("other")])
        api, calls = await harness(db)
        async with api:
            rid = (await api.post("/api/quick-advice", **send_args())).json()["id"]
            h = {"X-User": "other"}
            assert (await api.get("/api/quick-advice", headers=h)).json()["items"] == []
            assert (await api.patch(f"/api/quick-advice/{rid}/outcome", headers=h, json={"text": "intrusion"})).status_code == 404
            assert (await api.delete(f"/api/quick-advice/{rid}", headers=h)).status_code == 404
            assert (await api.post("/api/quick-advice", **send_args("other", "other", parent_id=rid))).status_code == 409
            assert len(calls) == 1
    run_case(case)


async def pair(db):
    await db.users.insert_many([person(), person("guest", tier="unsubscribed", stripe_subscription_id=None)])
    await db.couples_pairs.insert_one({"_id": "pair", "env": "preview", "state": "active", "members": ["owner", "guest"],
        "inviter": "owner", "invitee": "guest", "sponsor": "owner"})


def test_sponsored_couples_private_advice_meter_shared_context_and_withdrawal(monkeypatch):
    edition = ["shared-one"]
    async def context(db, member, quiz, pair_id):
        assert member["user_id"] in ("guest", "owner") and pair_id == "pair"
        return {"signature": edition[0], "comparisons": [{"scope": "personality", "topic": "Communication"}]}
    monkeypatch.setattr(couples_compatibility, "coach_context", context)
    async def case(db):
        await pair(db)
        api, calls = await harness(db)
        args = send_args("couple", "guest")
        async with api:
            assert (await api.post("/api/quick-advice", **args)).status_code == 403
            first = await api.post("/api/couples/quick-advice", **args)
            assert first.status_code == 200, first.text
            assert (await api.post("/api/couples/quick-advice", **args)).json() == first.json()
            rid = first.json()["id"]
            assert calls[0]["message"] == (rid, "couples") and len(calls) == 1
            prompt = json.loads(calls[0]["args"][2])
            assert prompt["requesting_member_profile"] == "Own profile for guest"
            assert prompt["mutually_shared_assessments"]["signature"] == "shared-one"
            assert free_plan.reply_usage(await db.users.find_one({"user_id": "owner"}))["messages_used"] == 1
            assert (await api.get("/api/couples/quick-advice")).json()["items"] == []
            edition[0] = "withdrawn"
            row = (await api.get("/api/couples/quick-advice", headers={"X-User": "guest"})).json()["items"][0]
            assert row["context_changed"] and "response" not in row
            detail = (await api.get(f"/api/couples/quick-advice/{rid}", headers={"X-User": "guest"})).json()
            assert detail["context_changed"] and "response" not in detail
            exports = [row async for row in quick_advice.own_export(db, await db.users.find_one({"user_id": "guest"}))]
            assert "response" not in exports[0]
            assert (await api.post("/api/couples/quick-advice", **send_args("next", "guest", parent_id=rid))).status_code == 409
    run_case(case)


@pytest.mark.parametrize("change", ["pair", "sharing", "profile", "closing"])
def test_inflight_changes_refund_and_prevent_saving(monkeypatch, change):
    edition = ["one"]
    async def shared(*_):
        return {"signature": edition[0], "comparisons": []}
    monkeypatch.setattr(couples_compatibility, "coach_context", shared)
    async def case(db):
        await pair(db)
        async def generate(*_):
            if change == "pair":
                await db.couples_pairs.update_one({"_id": "pair"}, {"$set": {"state": "closed"}})
            elif change == "sharing":
                edition[0] = "two"
            elif change == "profile":
                await db.users.update_one({"user_id": "guest"}, {"$set": {"about.describe": "Updated"}})
            else:
                await db.users.update_one({"user_id": "guest"}, {"$set": {"closing": True}})
            return output()
        api, calls = await harness(db, generate)
        async with api:
            response = await api.post("/api/couples/quick-advice", **send_args("change", "guest"))
            assert response.status_code in (404, 409)
            assert await db.quick_advice.count_documents({}) == 0
            assert free_plan.reply_usage(await db.users.find_one({"user_id": "owner"}))["messages_used"] == 0
    run_case(case)


def test_intro_uses_tenth_then_saved_retry_remains_readable(configuration):
    async def case(db):
        await db.users.insert_one(intro_member("owner", intro_entitlement=grant(), intro_reply_totals={"used": 9, "claimed": 9}))
        api, calls = await harness(db)
        async with api:
            first = await api.post("/api/quick-advice", **send_args())
            assert first.status_code == 200, first.text
            assert (await api.post("/api/quick-advice", **send_args())).json() == first.json()
            assert (await api.post("/api/quick-advice", **send_args("eleven"))).status_code == 402
            assert len(calls) == 1
            assert (await db.message_cost_units.find_one({"_id": first.json()["id"]}))["pool"] == "intro"
    run_case(case)


def test_contact_boundary_is_respected_and_no_provider_called():
    async def case(db):
        await db.users.insert_one(person())
        api, calls = await harness(db)
        async with api:
            row = await api.post("/api/quick-advice", **send_args(question="They told me to stop contacting them. What should I send?"))
            assert row.status_code == 200 and row.json()["response"]["contact_boundary"]
            assert row.json()["response"]["what_to_say"] is None and not calls
    run_case(case)


def test_unlimited_uses_existing_complimentary_receipts_and_admin_activity_has_no_text():
    async def case(db):
        await db.users.insert_one(person(tier="free", stripe_subscription_id=None, complimentary_entitlement=unlimited_grant()))
        api, _ = await harness(db)
        async with api:
            first = await api.post("/api/quick-advice", **send_args(question="Secret situation text"))
            assert first.status_code == 200
            unit = await db.message_cost_units.find_one({"_id": first.json()["id"]})
            assert unit["pool"] == "complimentary" and unit["workspace"] == "individual"
            live = await trends.live_activity(db, {"user_id": "owner"}, selected="quick_advice")
            assert live["last24"]["quick_advice"] == 1
            assert live["items"][0]["detail"] == "Requested Individual Quick Advice"
            assert "Secret situation text" not in json.dumps(live)
            await api.delete("/api/quick-advice/" + first.json()["id"])
            assert (await trends.live_activity(db, {"user_id": "owner"}, selected="quick_advice"))["last24"]["quick_advice"] == 0
    run_case(case)


@pytest.mark.parametrize("reading", ["history", "retry"])
def test_withdrawal_after_saved_row_read_never_returns_generated_content(monkeypatch, reading):
    edition = ["shared-one"]
    async def shared(*_):
        return {"signature": edition[0], "comparisons": [{"scope": "personality"}]}
    monkeypatch.setattr(couples_compatibility, "coach_context", shared)
    async def case(db):
        await pair(db)
        api, calls = await harness(db)
        args = send_args("race", "guest")
        async with api:
            first = await api.post("/api/couples/quick-advice", **args)
            assert first.status_code == 200
            if reading == "history":
                original = quick_advice.member_pages.page
                async def withdraw_page(*a, **kw):
                    result = await original(*a, **kw)
                    edition[0] = "withdrawn"
                    return result
                monkeypatch.setattr(quick_advice.member_pages, "page", withdraw_page)
                result = (await api.get("/api/couples/quick-advice", headers={"X-User": "guest"})).json()["items"][0]
            else:
                original = type(db.quick_advice).find_one
                seen = 0
                async def withdraw_read(collection, query, *a, **kw):
                    nonlocal seen
                    value = await original(collection, query, *a, **kw)
                    if collection.name == "quick_advice" and query.get("id") == first.json()["id"]:
                        seen += 1
                        if seen == 2:  # saved_reply then restore's actual stored-row read
                            edition[0] = "withdrawn"
                    return value
                monkeypatch.setattr(type(db.quick_advice), "find_one", withdraw_read)
                retry = await api.post("/api/couples/quick-advice", **args)
                assert retry.status_code == 200, retry.text
                result = retry.json()
            assert result["context_changed"] and "response" not in result and "based_on" not in result
            assert len(calls) == 1
            assert free_plan.reply_usage(await db.users.find_one({"user_id": "owner"}))["messages_used"] == 1
    run_case(case)


def test_history_paging_is_bounded_and_owner_scoped():
    async def case(db):
        await db.users.insert_one(person())
        for i in range(25):
            await db.quick_advice.insert_one({"id": str(uuid4()), "thread_id": str(i), "user_id": "owner", "env": "preview",
                "workspace": "individual", "pair_id": None, "created_at": f"2026-11-18T12:{i:02}:00Z", "question": str(i)})
        api, _ = await harness(db)
        async with api:
            first = (await api.get("/api/quick-advice")).json()
            assert len(first["items"]) == 20 and first["total"] == 25
            second = (await api.get("/api/quick-advice", params={"cursor": first["next_cursor"]})).json()
            assert len(second["items"]) == 5 and second["next_cursor"] is None
            assert not ({x["id"] for x in first["items"]} & {x["id"] for x in second["items"]})
            detail = await api.get("/api/quick-advice/" + second["items"][0]["id"])
            assert detail.status_code == 200 and detail.json()["question"] == second["items"][0]["question"]
            await db.users.insert_one(person("other"))
            assert (await api.get("/api/quick-advice/" + detail.json()["id"], headers={"X-User": "other"})).status_code == 404
            await api.delete("/api/quick-advice/" + detail.json()["id"])
            assert (await api.get("/api/quick-advice/" + detail.json()["id"])).status_code == 404
    run_case(case)


def test_couples_invalid_provider_response_cost_is_recorded_and_allowance_refunded(monkeypatch):
    async def shared(*_):
        return {"signature": "one", "comparisons": []}
    monkeypatch.setattr(couples_compatibility, "coach_context", shared)
    async def case(db):
        await pair(db)
        async def generate(feature, *_):
            with message_costs.generation_context("preview", feature):
                async with message_costs.provider_call(db, ("fixture-key", False), "deepseek-flash", 0) as receipt:
                    receipt.status = "succeeded"
                    receipt.envelope = {"model": "deepseek-flash", "usage": {"prompt_tokens": 50, "completion_tokens": 10,
                        "total_tokens": 60, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 50}}
            return {"best_move": "Incomplete fixture output"}
        api, _ = await harness(db, generate)
        async with api:
            failed = await api.post("/api/couples/quick-advice", **send_args("cost", "guest"))
            assert failed.status_code == 502
            ledger = await db.ai_call_ledger.find_one({})
            assert ledger["workspace"] == "couples" and ledger["application_rejection"] == "quick_advice_shape"
            assert ledger["total_tokens"] == 60 and ledger["low_nusd"] > 0
            unit = await db.message_cost_units.find_one({"_id": ledger["message_id"]})
            assert unit["status"] == "refunded" and unit["kind"] == "couples_quick_advice"
            assert free_plan.reply_usage(await db.users.find_one({"user_id": "owner"}))["messages_used"] == 0
            assert await db.quick_advice.count_documents({}) == 0
    run_case(case)


def test_final_grant_snapshot_changed_after_context_is_withheld(monkeypatch):
    withdraw = [False]
    async def shared(db, *_):
        if withdraw[0]:
            await db.couples_pairs.update_one({"_id": "pair"}, {"$set": {"compatibility_sharing": {"a": {"revision": 2}}}})
        return {"signature": "same-old-signature", "comparisons": []}
    monkeypatch.setattr(couples_compatibility, "coach_context", shared)
    async def case(db):
        await pair(db)
        api, _ = await harness(db)
        async with api:
            first = await api.post("/api/couples/quick-advice", **send_args("final-race", "guest"))
            assert first.status_code == 200
            withdraw[0] = True
            history = await api.get("/api/couples/quick-advice", headers={"X-User": "guest"})
            assert history.status_code == 200
            assert history.json()["items"][0]["context_changed"]
            assert "response" not in history.json()["items"][0]
    run_case(case)


def test_collection_lifecycle_and_admin_are_declared_content_free():
    assert "quick_advice" in accounts.CONTENT and "quick_advice" in accounts.ACCOUNT_COLLECTIONS
    assert COLLECTION_DOMAINS["quick_advice"] == "individual"
    records = {entry[0]: entry for entry in trends.SAVED_RECORDS}
    assert records["quick_advice"][3]["workspace"] == "individual"
    assert records["couples_quick_advice"][3]["workspace"] == "couples"
    assert trends.live_source_filter("quick_advice") == {"deleted": {"$ne": True}}
    source = ast.parse(Path("backend/server.py").read_text(encoding="utf-8"))
    overview = next(n for n in source.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "overview")
    initial = next(n for n in overview.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Await))
    assert len(initial.value.value.args) == 7
    assert "quick-advice" in ast.unparse(overview)
