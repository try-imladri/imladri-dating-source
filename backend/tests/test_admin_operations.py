import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
from fastapi import Response

from backend import ai, ai_meter, free_plan, membership_admin, prompt_library
from backend.tests.test_free_plan_quota import run_case


def test_paid_sponsored_refills_and_actual_stripe_ledger_remain_separate(monkeypatch):
    at = datetime(2026, 11, 9, tzinfo=timezone.utc)
    when = int(at.timestamp())
    monkeypatch.setattr(membership_admin.stripe, "api_key", "sk_test_fixture")
    def entry(category, source, amount, fee, currency="usd"):
        return {"id":"txn_"+category, "reporting_category":category, "source":source, "amount":amount,
                "fee":fee, "net":amount-fee, "currency":currency}
    pages = [
        [entry("charge",{"payment_intent":"pi_sub"},1400,82), entry("refund",{"charge":"ch_refill"},-450,0)],
        [entry("stripe_fee",None,-45,0), entry("charge",{"id":"ch_refill"},1200,60),entry("charge",None,500,0,"cad")],
    ]
    def list_page(**kw):
        assert kw["expand"] == ["data.source"] and kw["created"]["lt"] == when
        index = int("starting_after" in kw)
        return SimpleNamespace(data=pages[index],has_more=not index)
    monkeypatch.setattr(membership_admin.stripe.BalanceTransaction, "list", list_page)
    async def go(db):
        paid = {"user_id":"paid","env":"preview","tier":"paid","stripe_subscription_id":"sub_real",
                "membership_plan_id":"plus_250","subscription_period_start":when-86400,"subscription_period_end":when+86400,
                "role":"member","couples_adult_at":"confirmed"}
        await db.users.insert_many([paid,{"user_id":"guest","env":"preview","role":"member","couples_adult_at":"confirmed"},
            {**paid,"user_id":"expired","subscription_period_end":when-60},{**paid,"user_id":"other-env","env":"live"}])
        await db.couples_pairs.insert_one({"env":"preview","state":"active","sponsor":"paid","members":["paid","guest"]})
        base = {"env":"preview","user_id":"paid","payment_status":"paid","currency":"usd","created_at":"2026-11-03T12:00:00+00:00"}
        await db.payment_transactions.insert_many([
            {**base,"kind":"purchase","session_id":"cs_sub","subscription_revenue_cents":1200,"balance_source_ids":["pi_sub"]},
            {**base,"kind":"topup","amount":12,"session_id":"cs_refill","stripe_charge_id":"ch_refill"},
            {**base,"kind":"topup","amount":12,"session_id":"cs_wait","payment_status":"pending"},
        ])
        result = await membership_admin.reconcile(db,"preview",{"env":"preview"},at)
        assert result["entitlements"] == {"paid":1,"sponsored_only":1,"by_plan":{"plus_250":1}}
        assert (result["subscriptions"],result["refills"]) == (1200,1200)
        balance = result["stripe"]
        assert balance["state"] == "complete" and balance["other_currency"] == 1
        assert balance["products"]["subscriptions"]["charge"]["fee"] == 82
        assert balance["products"]["refills"]["refund"]["amount"] == -450
        assert balance["products"]["refills"]["charge"]["net"] == 1140
        assert balance["totals_by_product"]["refills"] == {"rows":2,"amount":750,"fee":60,"net":690}
        assert balance["totals_by_product"]["subscriptions"]["net"] == 1318
        assert balance["totals_by_product"]["unlinked"]["net"] == -45
        assert balance["products"]["unlinked"]["stripe_fee"]["net"] == -45
        assert "pi_sub" not in json.dumps(result) and "ch_refill" not in json.dumps(result)
        await membership_admin.retain_closed_revenue(db,"paid","preview")
        await db.payment_transactions.delete_many({"user_id":"paid"})
        restored = await membership_admin.reconcile(db,"preview",{"env":"preview"},at)
        assert (restored["subscriptions"], restored["refills"]) == (1200,1200)
        assert not restored["stripe"]["products"]["subscriptions"]
        assert membership_admin.stripe_balance("live",at,at)["state"] == "unavailable"
    run_case(go)


def test_legacy_invoice_reference_backfill_is_scoped_and_reused(monkeypatch):
    calls = []
    def paid_page(*, invoice, status, limit):
        assert status == "paid" and invoice == "in_ours"
        calls.append(invoice)
        rows = [{"invoice": owner, "payment": {"payment_intent": ref}, "status": "paid"} for owner, ref in
                [("in_ours", "pi_ours"), ("in_other", "pi_other")]]
        return SimpleNamespace(auto_paging_iter=lambda: iter(rows))
    monkeypatch.setattr(membership_admin.stripe.InvoicePayment, "list", paid_page)
    async def check(db):
        await db.users.insert_many([{"user_id": "member", "env": "preview"}, {"user_id": "other", "env": "live"}])
        for owner, invoice in [("member", "in_ours"), ("other", "in_other")]:
            await db.payment_transactions.insert_one({"user_id": owner, "kind": "renewal", "payment_status": "paid",
                                                     "invoice_id": invoice})
        for _ in range(2):
            balance = {"entries": [("charge", {"pi_ours"}, {"amount": 2000, "fee": 88, "net": 1912}),
                                   ("refund", {"pi_other"}, {"amount": -600, "fee": 0, "net": -600})]}
            await membership_admin.attribute_balance(db, "preview", {"env": "preview"}, balance)
            assert balance["products"]["subscriptions"]["charge"]["net"] == 1912
            assert balance["products"]["unlinked"]["refund"]["net"] == -600
            assert balance["invoice_link_status"] == "complete" and "pi_ours" not in json.dumps(balance)
        assert calls == ["in_ours"]
    run_case(check)


def test_delayed_stripe_invoice_reference_is_retried(monkeypatch):
    calls = []
    def paid_page(*, invoice, status, limit):
        calls.append(invoice)
        rows = [] if len(calls) == 1 else [{"invoice": invoice, "payment": {"payment_intent": "pi_delayed"}, "status": "paid"}]
        return SimpleNamespace(auto_paging_iter=lambda: iter(rows))
    monkeypatch.setattr(membership_admin.stripe.InvoicePayment, "list", paid_page)

    async def check(db):
        receipts = db.payment_transactions
        await receipts.insert_one({"_id": "our", "env": "preview", "kind": "purchase", "payment_status": "paid",
                                   "invoice_id": "in_delayed"})
        async def backfill():
            return await membership_admin.refresh_invoice_links(db, "preview", {"env": "preview"})
        assert await backfill() == "partial" and await backfill() == "partial"
        assert calls == ["in_delayed"]
        await receipts.update_one({"_id": "our"}, {"$set": {"invoice_link_retry_at": "2000-01-01"}})
        assert await backfill() == "complete"
        assert (await receipts.find_one({"_id": "our"}))["balance_source_ids"] == ["pi_delayed"]
        assert calls == ["in_delayed", "in_delayed"]
    run_case(check)


def test_attempts_retries_token_bounds_and_idempotent_paid_reply_charges(monkeypatch):
    from backend import server
    calls = []
    http_client = httpx.AsyncClient
    def provider(request):
        calls.append(request)
        result = "invalid" if len(calls) == 1 else '{"reply":"fixture"}'
        return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"content":result}}],
            "usage":{"prompt_cache_hit_tokens":10,"prompt_cache_miss_tokens":20,"prompt_tokens":30,"completion_tokens":5,"total_tokens":35}})
    monkeypatch.setenv("DEEPSEEK_API_KEY","fixture")
    monkeypatch.setattr(ai,"httpx",SimpleNamespace(AsyncClient=lambda **kw:http_client(transport=httpx.MockTransport(provider), **kw),
        Timeout=httpx.Timeout,RequestError=httpx.RequestError,TimeoutException=httpx.TimeoutException))
    async def go(db):
        monkeypatch.setattr(server,"db",db)
        result = await ai_meter.in_context("background",server.ai_json,"starters","Synthetic setup","Synthetic member text")
        assert result == {"reply":"fixture"}
        receipt = {"pending":True,"kind":"exchange","result":"r","cycle":"abcdefgh","pool":"topup"}
        await db.users.insert_one({"user_id":"owner","env":server.ENV,"free_reply_requests":{"one":receipt}})
        await free_plan.settle(db,"owner","one",receipt,True)
        await free_plan.settle(db,"owner","one",receipt,True)
        report = await prompt_library.usage(db,server.ENV,7)
        stats = report["activity"]["starters"]
        assert [stats[k] for k in ("background","attempts","retries","input_tokens","output_tokens","reported","priced")] == [1,2,1,60,10,2,2]
        assert stats["low_nusd"] == 2*(10*3+20*150+5*600)
        assert stats["high_nusd"] == stats["low_nusd"]*2
        assert stats["timed_attempts"] == stats["cache_reported"] == 2
        assert stats["provider_ms"] >= 0 and (stats["cache_hit_tokens"], stats["cache_miss_tokens"]) == (20,40)
        assert report["settled"] == {"plan":0,"refill":1}
        assert "Synthetic" not in json.dumps(report)
        exact_day = await prompt_library.usage(db, server.ENV, 1, report["end"])
        assert exact_day["start"] == exact_day["end"] and exact_day["activity"] == report["activity"]
        assert exact_day["work"]["background"]["generations"] == 1
        assert exact_day["work"]["background"]["attempts"] == exact_day["work"]["background"]["priced"] == 2
        assert exact_day["work"]["background"]["retries"] == 1
        assert exact_day["daily_budget"] == {"reservations": 2, "budget_tokens": 70}
        past = await prompt_library.usage(db, server.ENV, 1, "2010-01-01")
        assert past["activity"] == {} and past["settled"] == {"plan":0,"refill":0}
        assert past["daily_budget"] is None
        await db.ai_daily_usage.insert_one({"_id": "2010-01-01", "requests": 6, "tokens": 9406})
        legacy = await prompt_library.usage(db, server.ENV, 1, "2010-01-01")
        assert legacy["daily_budget"] == {"reservations": 6, "budget_tokens": 9406} and legacy["work"] == {}
        async def unavailable(*args):
            raise RuntimeError("Generation outcome counter unavailable")
        monkeypatch.setattr(prompt_library, "record", unavailable)
        assert await server.ai_json("starters", "Setup", "Synthetic text") == {"reply": "fixture"}
        degraded = await prompt_library.usage(db, server.ENV, 1, report["end"])
        assert degraded["work"]["included"]["attempts"] == 1 and degraded["daily_budget"]["reservations"] == 3
        assert degraded["counts"]["starters"]["succeeded"] == 1
    run_case(go)


def test_midnight_provider_retry_reports_usage_on_each_reservation_day(monkeypatch):
    from backend import ops, server
    days = iter(["2010-10-01", "2010-10-02", "2010-10-02"])
    monkeypatch.setattr(ops, "day_key", lambda: next(days))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture")
    responses = iter([
        ("new-unknown-model", "invalid", {"prompt_tokens": 4, "completion_tokens": 5}),
        ("deepseek-v4-pro", "{}", {"total_tokens": 18, "prompt_tokens": 12, "completion_tokens": 6}),
        ("deepseek-flash", "{}", {"total_tokens": 0, "prompt_tokens": 2, "completion_tokens": 3}),
    ])
    client_type = httpx.AsyncClient
    def reply(request):
        model, content, usage = next(responses)
        return httpx.Response(200, json={"model": model, "usage": usage,
            "choices": [{"finish_reason": "stop", "message": {"content": content}}]})
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **settings: client_type(transport=httpx.MockTransport(reply), **settings))
    async def go(db):
        monkeypatch.setattr(server, "db", db)
        for _ in range(2):
            assert await server.ai_json("starters", "Fixture", "Test") == {}
        first, second = [await prompt_library.usage(db, server.ENV, 1, day) for day in ("2010-10-01", "2010-10-02")]
        assert first["reserved_day_usage"] == {"usage_reports": 1, "input_tokens": 4, "output_tokens": 5}
        assert first["daily_budget"] == {"reservations": 1, "budget_tokens": 9}
        assert second["reserved_day_usage"] == {"usage_reports": 1, "input_tokens": 12, "output_tokens": 6}
        assert second["daily_budget"]["reservations"] == 2 and second["daily_budget"]["budget_tokens"] > 8192
        completed = (await prompt_library.usage(db, server.ENV, 1))["activity"]["starters"]
        assert [completed[k] for k in ("attempts", "reported", "priced")] == [3, 2, 1]
        assert completed["low_nusd"] == 12 * 22 + 6 * 1980
    run_case(go)


def test_staff_candidate_cases_conflict_rollback_and_preexisting_versions(monkeypatch):
    from backend import couples_tone, prompt_candidate, server
    result_ok = True
    couples_ok = False
    calls = []
    async def provider(system, source, image=None):
        calls.append((system,source))
        if "stop messaging" in source:
            return {"answer":"I’ll give them space." if result_ok else 'For private use only. Warm: "I hear you, and I’ll leave you be."', "would_send":False,"reply_options":[]}
        if source.startswith("{"):
            data = json.loads(source)
            assert couples_tone.COACH_INSTRUCTIONS in system and data["mode"] == "shared"
            assert data["focus_speaker"] == "them" and data["purpose"] == couples_tone.DEFAULT_GOAL
            assert data["source_lines"] == [{"index": i, **line} for i, line in enumerate(prompt_candidate.PAIR_LINES)]
            if couples_ok:
                return {"status": "estimated", "incoming": "neutral", "evidence": [1],
                        "explanation": "Your partner may have understood the decision as open.",
                        "drafts": {"warm": "What part still felt open to you?", "neutral": "What felt undecided?",
                                   "direct": "What had we yet to decide?", "playful": "Which piece was still up in the air?",
                                   "cool": "Which part was open?"}}
        return {"answer":"Confirm the day.","reply_options":["Is Friday still good for you?"], "would_send":True}
    async def go(db):
        nonlocal result_ok, couples_ok
        monkeypatch.setattr(server,"db",db)
        monkeypatch.setattr(server,"provider_json",provider)
        async def staff():
            return {"user_id":"staff","role":"admin"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url="http://local") as api:
            path = "/api/admin/prompts"
            assert (await api.get(path+"/versions")).status_code == 401
            monkeypatch.setitem(server.app.dependency_overrides,server.admin_dep,staff)
            patch = {"prompts":{"reply_style":"Preserve the speaker’s requested question. CASE_EDIT"},"prompts_revision":None}
            result_ok = False
            failed = (await api.post(path+"/evaluate",json=patch)).json()
            assert not failed["eligible"] and len(failed["cases"]) == len(calls) == 4
            assert all(not c["passed"] for c in failed["cases"] if "stop messaging" in c["input"])
            invalid_shape = next(c for c in failed["cases"] if c["input"] == prompt_candidate.PAIR)
            assert invalid_shape["passed"] is False and not invalid_shape["reply_options"]
            assert (await api.put("/api/admin/settings",json={**patch,"reviewed_candidate":failed["id"]})).status_code == 409
            result_ok = couples_ok = True
            observed = (await api.post(path+"/evaluate",json=patch)).json()
            assert observed["eligible"] and len(observed["cases"]) == 4 and len(calls) == 8
            assert {c["feature"] for c in observed["cases"]} == {"six_part","couples_tone"}
            shared = next(c for c in observed["cases"] if c["input"] == prompt_candidate.PAIR)
            assert shared["would_send"] and "decision as open" in shared["answer"]
            assert shared["reply_options"][0] == "Warm: What part still felt open to you?" and len(shared["reply_options"]) == 5
            assert all("CASE_EDIT" in system for system,_ in calls)
            old_calls = len(calls)
            assert (await api.post(path+"/evaluate",json=patch)).json() == observed and len(calls) == old_calls
            changed = {**patch,"prompts":{"reply_style":"Changed after review."},"reviewed_candidate":observed["id"]}
            assert (await api.put("/api/admin/settings",json=changed)).status_code == 409
            evidence = {"_id": observed["id"]}
            await db.prompt_candidates.update_one(evidence, {"$set": {"report.protocol": "communication-candidate-v2"}})
            assert (await api.put("/api/admin/settings",json={**patch,"reviewed_candidate":observed["id"]})).status_code == 409
            await db.prompt_candidates.update_one(evidence, {"$set": {"report.protocol": prompt_candidate.PROTOCOL}})
            release = (await api.put("/api/admin/settings",json={**patch,"reviewed_candidate":observed["id"]})).json()
            rev = release["prompts_revision"]
            versions = (await api.get(path+"/versions")).json()["versions"]
            assert versions[0]["candidate"]["cases"] == 4 and versions[1]["revision"] == "initial"
            assert (await api.post(path+"/rollback",json={"current":None,"target":"initial"})).status_code == 409
            restored = (await api.post(path+"/rollback",json={"current":rev,"target":"initial"})).json()
            assert restored["prompts"]["reply_style"] == server.DEFAULT_PROMPTS["reply_style"]
            assert restored["prompts_revision"] != rev
            assert (await api.post(path+"/review",json={"revision":rev,"answers":dict.fromkeys(prompt_library.EVAL_CASES,"pass")})).status_code == 409
            assert len(calls) == old_calls
    run_case(go)

def test_total_only_provider_usage_and_model_versions_in_one_day(monkeypatch):
    from backend import ops, server
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture")
    monkeypatch.setattr(ops, "day_key", lambda: "2010-11-03")
    responses = iter([
        ("DeepSeek-V4.1-Flash-1017", {"prompt_tokens": 10, "completion_tokens": 2}),
        ("DeepSeek-V4-Pro-0813", {"prompt_tokens": 3, "completion_tokens": 1}),
        ("unknown", {"total_tokens": 17}), ("unknown", {"total_tokens": 0}),
        ("unknown", {"total_tokens": True}), ("unknown", {"total_tokens": 20, "completion_tokens": 5}),
    ])
    def reply(request):
        model, usage = next(responses)
        return httpx.Response(200, json={"model": model, "usage": usage,
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})
    client = httpx.AsyncClient
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **opts: client(transport=httpx.MockTransport(reply), **opts))

    async def go(db):
        monkeypatch.setattr(server, "db", db)
        for _ in range(6):
            assert await server.ai_json("starters", "Fixture", "Fixture") == {}
        reserved = await prompt_library.usage(db, server.ENV, 1, "2010-11-03")
        assert reserved["daily_budget"]["reservations"] == 6
        assert reserved["reserved_day_usage"] == dict(usage_reports=2, input_tokens=13, output_tokens=3,
                                                       total_only_reports=2, total_only_tokens=17)
        completed = (await prompt_library.usage(db, server.ENV, 1))["activity"]["starters"]
        assert [completed[k] for k in ("attempts", "reported", "total_only_reports", "priced")] == [6, 2, 2, 2]
        assert completed["input_tokens"] + completed["output_tokens"] + completed["total_only_tokens"] == 33
        assert completed["low_nusd"] == 10 * 3 + 2 * 600 + 3 * 22 + 1980

    run_case(go)
