import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from pymongo.errors import AutoReconnect, DuplicateKeyError

from backend import ai, ai_meter, message_costs, ops
from backend.tests.test_free_plan_quota import run_case


def provider(monkeypatch, dispatch):
    original = httpx.AsyncClient
    monkeypatch.setenv("DEEPSEEK_API_KEY", "credential-test-only")
    monkeypatch.setenv("DEEPSEEK_PRODUCTION_KEY_ID", message_costs.key_id())
    monkeypatch.setenv("DEEPSEEK_PRODUCTION_KEY_NAME", "production-test")
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(dispatch), **kw))


def completion(content, usage):
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": usage, "created": int(datetime.now(timezone.utc).timestamp()), "model": "deepseek-flash"}


def test_dispatches_follow_context_including_json_retries_and_preparation(monkeypatch):
    calls = []
    usage = {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13,
             "prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 6}
    def dispatch(request):
        calls.append(request)
        return httpx.Response(200, json=completion("invalid" if len(calls) == 1 else "{}", usage))
    provider(monkeypatch, dispatch)
    async def go(db):
        token = ops.request_budget_db.set(db)
        try:
            with message_costs.message_context("unit-a", "couples", run_number=2), message_costs.generation_context("live", "couples_tone"):
                assert await ai_meter.in_context("metered", ai.ai_json, "private prompt", "private chat") == {}
            with message_costs.message_context("unit-a", "couples"), message_costs.generation_context("live", "report"):
                await ai_meter.in_context("background", ai.ai_json, "private prompt", "private chat")
            with message_costs.preparation_context("upload-b"), message_costs.generation_context("live", "transcribe_text"):
                await ai.ai_json("private prompt", "private chat")
            with message_costs.generation_context("live", "starters"):
                await ai.ai_json("private prompt", "private chat")
        finally:
            ops.request_budget_db.reset(token)
        rows = await db.ai_call_ledger.find({}).to_list(10)
        assert len(calls) == len(rows) == 5
        assert len({row["_id"] for row in rows}) == 5
        assert all(row["status"] == "succeeded" and row["input_tokens"] == 10 and row["total_tokens"] == 13
                   and row["key_id"] == message_costs.key_id() and row["dedicated_key"]
                   and row["billing_at"] == row["provider_created_at"] and row["billing_hour_certain"] for row in rows)
        metered = [row for row in rows if row["mode"] == "metered"]
        assert len({row["generation_id"] for row in metered}) == 1
        assert sorted(row["retry"] for row in metered) == [False, True]
        assert all(row["message_run"] == 2 for row in metered)
        assert all(row["message_id"] == "unit-a" and row["workspace"] == "couples" for row in rows if row["feature"] in ("couples_tone", "report"))
        background = next(row for row in rows if row["feature"] == "report")
        assert background["mode"] == "background" and not background["retry"] and background["message_run"] == 1
        prep = next(row for row in rows if row["preparation_id"] == "upload-b")
        assert prep["feature"] == "transcribe_text" and prep["message_id"] is None
        standalone = next(row for row in rows if row["feature"] == "starters")
        assert standalone["message_id"] is None and standalone["message_run"] is None and standalone["preparation_id"] is None
        encoded = json.dumps(rows, default=str)
        assert all(secret not in encoded for secret in ["private prompt", "private chat", "credential-test-only"])
    run_case(go)


def test_application_rejection_is_scoped_to_the_final_provider_attempt_of_one_message_run(monkeypatch):
    replies = iter(["{}", "bad json", "{}", "{}"])
    provider(monkeypatch, lambda req: httpx.Response(200, json=completion(next(replies), {"total_tokens": 7})))

    async def go(db):
        token = ops.request_budget_db.set(db)
        async def generate():
            with message_costs.generation_context("live", "couples_tone"):
                return await ai.ai_json("private", "private")
        async def validate():
            assert await generate() == {}
            await message_costs.mark_response_rejection(db, "other-message", "tone_drafts")
            await message_costs.mark_response_rejection(db, "same-message", "do not store this input")
            await message_costs.mark_response_rejection(db, "same-message", "tone_drafts")
        try:
            with message_costs.message_context("same-message", "couples", run_number=1):
                await ai_meter.in_context("metered", generate)
            with message_costs.message_context("same-message", "couples", run_number=2):
                await ai_meter.in_context("metered", validate)
            with message_costs.message_context("same-message", "couples", run_number=3):
                await ai_meter.in_context("background", generate)
                await ai_meter.in_context("metered", message_costs.mark_response_rejection, db, "same-message", "tone_status")
            await ai_meter.in_context("metered", message_costs.mark_response_rejection, db, "same-message", "tone_length")
        finally:
            ops.request_budget_db.reset(token)
        calls = await db.ai_call_ledger.find({}).to_list(10)
        assert len(calls) == 4
        assert all(row["status"] == "succeeded" and row["http_status"] == 200 for row in calls)
        assert [(r["message_run"], r["retry"], r["application_rejection"]) for r in calls if "application_rejection" in r] == [
            (2, True, "tone_drafts")]
    run_case(go)


def test_failed_transport_unreported_usage_and_http_errors_remain_distinguishable(monkeypatch):
    responses = iter([
        httpx.ReadTimeout("private error"), httpx.ConnectError("private error"),
        httpx.Response(429, json={"error": {"message": "private error"}}),
        httpx.Response(200, json={"model": "deepseek-flash", "usage": {"total_tokens": 0}}),
        httpx.Response(200, json=completion("{}", {"total_tokens": True})),
    ])
    def dispatch(request):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response
    provider(monkeypatch, dispatch)
    async def go(db):
        token = ops.request_budget_db.set(db)
        try:
            with message_costs.message_context("paid-a", "individual"), message_costs.generation_context("live", "six_part"):
                for status in (504, 502, 503, 502):
                    with pytest.raises(HTTPException) as exc:
                        await ai.ai_text("private prompt", "private chat")
                    assert exc.value.status_code == status
                assert await ai.ai_text("private prompt", "private chat") == "{}"
        finally:
            ops.request_budget_db.reset(token)
        rows = {r["status"]:r for r in await db.ai_call_ledger.find({}).to_list(10)}
        assert set(rows) == {"timeout", "transport_error", "http_error", "invalid_response", "succeeded"}
        assert all("low_nusd" not in row for row in rows.values())
        assert rows["http_error"]["http_status"] == 429
        assert "total_tokens" not in rows["succeeded"] and rows["invalid_response"]["total_tokens"] == 0
        assert "private error" not in json.dumps(rows, default=str)
    run_case(go)


def test_dedicated_pin_requires_matching_key_and_live_hosted_context(monkeypatch):
    provider(monkeypatch, lambda request: pytest.fail("unexpected inference"))
    async def go():
        with pytest.raises(HTTPException):
            await ai.ai_text("fixture", "fixture")
        for env in ("preview", "live"):
            if env == "live":
                monkeypatch.setenv("DEEPSEEK_API_KEY", "different-test-only")
            with message_costs.generation_context(env, "starters"), pytest.raises(HTTPException):
                await ai.ai_text("fixture", "fixture")
    asyncio.run(go())


def test_confirmed_undispatched_call_restores_hourly_and_daily_capacity(monkeypatch):
    provider(monkeypatch, lambda request: pytest.fail("unregistered provider call"))
    monkeypatch.setattr(message_costs, "insert_attempt", AsyncMock(side_effect=message_costs.TrackingUnavailable()))
    async def go(db):
        budget = ops.request_budget_db.set(db)
        member = ops.member_id.set("test-member")
        try:
            with message_costs.generation_context("live", "six_part"), pytest.raises(HTTPException) as exc:
                await ai.ai_text("a", "b")
            assert exc.value.status_code == 503
        finally:
            ops.member_id.reset(member)
            ops.request_budget_db.reset(budget)
        daily = await db.ai_daily_usage.find_one({})
        assert daily["tokens"] == daily["requests"] == 0
        assert (await db.ai_request_usage.find_one({}))["calls"] == 0
    run_case(go)


def test_cancelled_call_keeps_an_unknown_cost_receipt(monkeypatch):
    started = None
    async def delayed(request):
        started.set()
        await asyncio.Future()
    provider(monkeypatch, delayed)
    async def go(db):
        nonlocal started
        started = asyncio.Event()
        budget = ops.request_budget_db.set(db)
        try:
            with message_costs.generation_context("live", "six_part"), message_costs.message_context("unit", "individual"):
                work = asyncio.create_task(ai.ai_text("a", "b"))
            await asyncio.wait_for(started.wait(), timeout=5)
            pending = await db.ai_call_ledger.find_one({"message_id": "unit"})
            assert pending["status"] == "pending" and "low_nusd" not in pending
            work.cancel()
            with pytest.raises(asyncio.CancelledError):
                await work
            receipt = await db.ai_call_ledger.find_one({"_id": pending["_id"]})
            assert receipt["status"] == "cancelled" and "total_tokens" not in receipt
        finally:
            ops.request_budget_db.reset(budget)
    run_case(go)


def test_interrupted_ledger_writes_preserve_attempt_identity(monkeypatch):
    provider(monkeypatch, lambda request: httpx.Response(200, json=completion("{}", {"total_tokens": 17})))
    async def go():
        coll = SimpleNamespace(insert_one=AsyncMock(side_effect=[AutoReconnect(), DuplicateKeyError("ambiguous acknowledgement")]),
                               update_one=AsyncMock(side_effect=[AutoReconnect(), AutoReconnect()]))
        db = SimpleNamespace(ai_call_ledger=coll)
        token = ops.request_budget_db.set(db)
        monkeypatch.setattr(ops, "reserve_tokens", AsyncMock(return_value=("today", 9128, None)))
        monkeypatch.setattr(ops, "settle_tokens", AsyncMock())
        monkeypatch.setattr(ops, "release_undispatched", AsyncMock())
        try:
            with message_costs.generation_context("live", "six_part"):
                assert await ai.ai_text("a", "b") == "{}"
            inserts = [call.args[0] for call in coll.insert_one.await_args_list]
            assert inserts[0] == inserts[1] and inserts[0]["status"] == "pending"
            assert all(call.args[0] == {"_id": inserts[0]["_id"], "status":"pending"} for call in coll.update_one.await_args_list)
            coll.insert_one = AsyncMock(side_effect=AutoReconnect())
            with message_costs.generation_context("live", "six_part"), pytest.raises(HTTPException) as exc:
                await ai.ai_text("a", "b")
            assert exc.value.status_code == 503 and ops.settle_tokens.await_count == 1
            ops.release_undispatched.assert_awaited_once_with(db, ("today", 9128, None))
        finally:
            ops.request_budget_db.reset(token)
    asyncio.run(go())


def test_price_bounds_use_provider_billing_time_and_uncertain_boundaries(monkeypatch):
    class Clock(datetime):
        moment = None
        @classmethod
        def now(cls, tz=None):
            return cls.moment
    monkeypatch.setattr(message_costs, "datetime", Clock)
    usage = {"prompt_tokens": 10, "completion_tokens": 1, "prompt_cache_hit_tokens": 9, "prompt_cache_miss_tokens": 1}
    for hour, high in [(4, 777), (9, 1554)]:
        at = datetime(2026, 11, 9, hour, 20, tzinfo=timezone.utc)
        Clock.moment = at
        result = message_costs.ProviderResult()
        result.envelope = {"created": int(at.timestamp()), "model": "deepseek-flash", "usage": usage}
        fields = result.fields(at)
        assert fields["billing_hour_certain"] and (fields["low_nusd"], fields["high_nusd"]) == (777, high)
    start = datetime(2026, 11, 9, 3, 59, tzinfo=timezone.utc)
    Clock.moment = datetime(2026, 11, 9, 4, 1, tzinfo=timezone.utc)
    result.envelope["created"] = 0
    fields = result.fields(start)
    assert not fields["billing_hour_certain"] and "provider_created_at" not in fields
    assert fields["billing_at"] == start and fields["high_nusd"] == 1554
