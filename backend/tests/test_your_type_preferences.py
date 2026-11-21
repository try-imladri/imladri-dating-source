"""Private stated ranges, source CAS, erasure and explicit history boundaries."""
import asyncio
from copy import deepcopy
import json
import time

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from backend import accounts, admin_answer_history, answer_history, history_lifecycle, your_type
from backend import your_type_preferences as prefs, your_type_research
from backend.tests.test_your_type_research import exercise, member


def person(uid="owner", **overrides):
    moment = int(time.time())
    return member(uid, tier="paid", stripe_subscription_id="fixture_subscription",
        subscription_period_start=moment-60, subscription_period_end=moment+3600,
        answer_history_staff_version=admin_answer_history.VERSION, answer_history_staff_id="b" * 32,
        answer_history_staff_env="preview", answer_history_staff_at="2026-01-01T00:00:00+00:00",
        answer_history_staff_revision=1, **overrides)


def ranges(**changes):
    return {"age_min": 28, "age_max": 44, "height_min_cm": 160, "height_max_cm": 190, **changes}


def request(**changes):
    return {"expected_member": "owner", "version": prefs.VERSION, "revision": 0,
        "by_audience": {"men": ranges(), "women": ranges(age_min=25, age_max=40)}, **changes}


@pytest.mark.parametrize("changes", [
    {"age_min": None}, {"age_min": 17}, {"age_max": 101}, {"age_min": True}, {"age_min": "28"},
    {"age_min": 28.0}, {"age_min": 45}, {"height_max_cm": None}, {"height_min_cm": 119},
    {"height_max_cm": 231}, {"height_min_cm": 200}, {"height_min_cm": [160]}, {"private_note": "unsupported"}])
def test_ranges_reject_unpaired_unordered_out_of_bounds_coerced_or_extra_fields(changes):
    data = request()
    data["by_audience"]["men"].update(changes)
    with pytest.raises(ValidationError):
        prefs.Change.model_validate(data)


@pytest.mark.parametrize("changes", [
    {"version": "future"}, {"revision": True}, {"revision": -1}, {"revision": "0"},
    {"by_audience": {"men": ranges()}},
    {"by_audience": {"men": ranges(), "women": ranges(), "both": ranges()}}, {"unrecognized": "private"}])
def test_write_contract_requires_exact_version_revision_and_both_audiences(changes):
    with pytest.raises(ValidationError):
        prefs.Change.model_validate(request(**changes))


def test_nullable_pairs_and_inclusive_bounds_are_accepted():
    data = request(by_audience={"men": ranges(age_min=18, age_max=100, height_min_cm=120, height_max_cm=230),
        "women": ranges(age_min=None, age_max=None, height_min_cm=None, height_max_cm=None)})
    assert prefs.Change.model_validate(data).by_audience.model_dump() == data["by_audience"]


def test_wrong_owner_is_rejected_before_database_access():
    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError("Ownership must be checked before accessing the database")
    async def case():
        with pytest.raises(HTTPException) as mismatch:
            await prefs.save(NoDatabase(), "peer", prefs.Change.model_validate(request()))
        assert mismatch.value.status_code == 409
    asyncio.run(case())


def test_save_persists_independent_private_ranges_and_an_owner_only_user_journal():
    async def case(db):
        initial = person()
        await db.users.insert_many([initial, person("peer")])
        default = await prefs.read(db, "owner")
        assert default["revision"] == 0 and default["by_audience"] == prefs.empty()["by_audience"]
        saved = await prefs.save(db, "owner", prefs.Change.model_validate(request()))
        assert saved["revision"] == 1 and saved["by_audience"] == request()["by_audience"]
        assert saved["private"] and not saved["research_included"] and not saved["automatic_matching"]
        row = await db.users.find_one({"user_id": "owner"})
        assert row[prefs.FIELD]["by_audience"] == request()["by_audience"]
        assert all(row[key] == initial[key] for key in initial if key.startswith(("your_type_research_", "row_member_", "answer_history_staff_")))
        events = [event async for event in history_lifecycle.own_events(db, "owner")]
        assert len(events) == 1 and events[0]["kind"] == prefs.QUESTION and events[0]["source"] == "users"
        assert events[0]["domain"] == "your_type" and events[0]["visibility"] == "private"
        assert events[0]["after"]["by_audience"] == request()["by_audience"] and not events[0]["before_exists"]
        assert (await prefs.read(db, "peer"))["revision"] == 0
        exported = json.loads("".join([chunk async for chunk in accounts.export_json(db, row)]))
        assert exported["account"][prefs.FIELD]["by_audience"] == request()["by_audience"]
        assert exported["answer_history"][0]["after"]["by_audience"] == request()["by_audience"]
    exercise(case)


def test_concurrent_same_revision_accepts_exactly_one_user_mutation():
    async def case(db):
        await db.users.insert_one(person())
        results = await asyncio.gather(*(prefs.save(db, "owner", prefs.Change.model_validate(request()))
            for _ in range(2)), return_exceptions=True)
        assert sum(isinstance(result, dict) for result in results) == 1
        rejected = [result for result in results if isinstance(result, HTTPException)]
        assert len(rejected) == 1 and rejected[0].status_code == 409
        assert (await prefs.read(db, "owner"))["revision"] == 1
        assert await db.answer_history.count_documents({"owner_id": "owner", "purged": {"$ne": True}}) == 1
    exercise(case)


def test_archive_failure_keeps_accepted_private_observation_on_its_user_source(monkeypatch):
    async def case(db):
        await db.users.insert_one(person())
        original = answer_history.flush
        async def unavailable(*args, **kwargs):
            return {"archived": 0, "redacted": 0, "failed": True}
        monkeypatch.setattr(answer_history, "flush", unavailable)
        assert (await prefs.save(db, "owner", prefs.Change.model_validate(request())))["revision"] == 1
        row = await db.users.find_one({"user_id": "owner"})
        assert len(row[answer_history.PENDING]) == 1
        assert row[answer_history.PENDING][0]["after"]["by_audience"] == request()["by_audience"]
        assert not await db.answer_history.find_one({"owner_id": "owner"})
        monkeypatch.setattr(answer_history, "flush", original)
        await history_lifecycle.prepare_export(db, "owner")
        events = [event async for event in history_lifecycle.own_events(db, "owner")]
        assert len(events) == 1 and events[0]["after"]["by_audience"] == request()["by_audience"]
    exercise(case)


def test_generation_change_between_read_and_CAS_rejects_without_preferences_or_event(monkeypatch):
    async def case(db):
        await db.users.insert_one(person())
        collection_type = type(db.users)
        original = collection_type.find_one_and_update
        changed = False
        async def intercept(collection, query, update, **kwargs):
            nonlocal changed
            if prefs.FIELD in update.get("$set", {}) and not changed:
                changed = True
                await db.users.update_one({"user_id": "owner"}, {"$inc": {answer_history.GENERATION: 1}})
            return await original(collection, query, update, **kwargs)
        monkeypatch.setattr(collection_type, "find_one_and_update", intercept)
        with pytest.raises(HTTPException) as stale:
            await prefs.save(db, "owner", prefs.Change.model_validate(request()))
        assert stale.value.status_code == 409
        assert prefs.FIELD not in await db.users.find_one({"user_id": "owner"})
        assert not await db.answer_history.find_one({"owner_id": "owner"})
    exercise(case)


@pytest.mark.parametrize("overrides,status", [({"tier": "unsubscribed", "stripe_subscription_id": None}, 402),
    ({"about": {"demographics": {"age": 17}}}, 403)])
def test_write_requires_paid_adult_eligibility_but_read_remains_available(overrides, status):
    async def case(db):
        initial = person()
        initial.update(overrides)
        await db.users.insert_one(initial)
        assert (await prefs.read(db, "owner"))["revision"] == 0
        with pytest.raises(HTTPException) as denied:
            await prefs.save(db, "owner", prefs.Change.model_validate(request()))
        assert denied.value.status_code == status
    exercise(case)


def test_confirmed_couples_workspace_cannot_write_individual_preferences():
    async def case(db):
        await db.users.insert_one(person())
        await db.couples_pairs.insert_one({"_id": "pair", "env": "preview", "state": "active",
            "members": ["owner", "peer"], "inviter": "owner", "invitee": "peer", "sponsor": "owner"})
        with pytest.raises(HTTPException) as denied:
            await prefs.save(db, "owner", prefs.Change.model_validate(request()))
        assert denied.value.status_code == 403
        assert not await db.answer_history.find_one({"owner_id": "owner"})
    exercise(case)


def test_existing_staff_and_research_grants_never_authorize_private_stated_values(monkeypatch):
    monkeypatch.setenv("ANSWER_HISTORY_CURSOR_SECRET", "fixture-only-history-key-0123456789")
    async def case(db):
        await db.users.insert_one(person())
        await prefs.save(db, "owner", prefs.Change.model_validate(request()))
        event = await db.answer_history.find_one({"owner_id": "owner", "kind": prefs.QUESTION})
        timeline = await admin_answer_history.page(db, {"user_id": "staff", "role": "admin"}, "preview", "member", "owner")
        assert timeline["items"] == []
        direct = await admin_answer_history._present_batch(db, [event], "preview", "member", "owner")
        assert len(direct) == 1 and direct[0]["status"] == "withheld"
        assert "after" not in direct[0] and "before" not in direct[0]
        csv = await admin_answer_history.export_csv(db, {"user_id": "staff", "role": "admin"}, "preview", "member", "owner")
        assert b"age_min" not in csv.body and b"height_min_cm" not in csv.body
        assert not [entry async for entry in your_type_research.member_rows(db, {"env": "preview"}, "owner", "m_" + "a" * 32,
            "2099-01-01T00:00:00+00:00")]
        assert (await your_type_research.report(db, {"env": "preview"}))["activity"] == {}
    exercise(case)


def test_clear_erases_values_and_history_and_fences_stale_revision_even_when_minor_or_closing():
    async def case(db):
        await db.users.insert_one(person())
        await prefs.save(db, "owner", prefs.Change.model_validate(request()))
        await db.users.update_one({"user_id": "owner"}, {"$set": {"closing": True, "tier": "unsubscribed",
            "about.demographics.age": 17}})
        await your_type.clear_member(db, "owner")
        row = await db.users.find_one({"user_id": "owner"})
        assert row[prefs.FIELD]["revision"] == 2
        assert row[prefs.FIELD]["by_audience"] == prefs.empty()["by_audience"]
        assert not await db.answer_history.find_one({"owner_id": "owner", "purged": {"$ne": True}})
        await db.users.update_one({"user_id": "owner"}, {"$set": {"closing": False, "about.demographics.age": 30,
            "tier": "paid"}})
        with pytest.raises(HTTPException) as stale:
            await prefs.save(db, "owner", prefs.Change.model_validate(request(revision=1)))
        assert stale.value.status_code == 409
        assert row[prefs.FIELD]["by_audience"] == prefs.empty()["by_audience"]
    exercise(case)


def test_account_closure_erases_private_field_and_journal_without_provider_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Fixture closure must not contact a provider")
    monkeypatch.setattr(accounts, "close_billing", forbidden)
    async def case(db):
        await db.users.insert_one(person(closure_billing_done=True))
        await prefs.save(db, "owner", prefs.Change.model_validate(request()))
        await accounts.finish_closure(db, "owner", identity_gone=True)
        assert not await db.users.find_one({"user_id": "owner"})
        assert not await db.answer_history.find_one({"owner_id": "owner", "purged": {"$ne": True}})
    exercise(case)


@pytest.mark.parametrize("erasure", ["content", "minor"])
def test_http_owner_routes_validate_conflicts_export_and_content_erasure(monkeypatch, erasure):
    import httpx
    from fastapi import Request
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("IMLADRI_DATABASE_BACKEND", "mongodb")
    monkeypatch.setenv("IMLADRI_DATABASE_MAINTENANCE", "off")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:62973")
    monkeypatch.setenv("DB_NAME", "unused_import_only_fixture")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_fixture")
    from backend import server
    async def case(db):
        monkeypatch.setattr(server, "db", db)
        async def identity(incoming: Request):
            uid = incoming.headers.get("x-preference-member")
            if uid not in ("owner", "peer"):
                raise HTTPException(401, "Fixture identity required")
            return {"user_id": uid}
        monkeypatch.setitem(server.app.dependency_overrides, server.require_user, identity)
        await db.users.insert_many([person(env=server.ENV), person("peer", env=server.ENV)])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            async def call(method, path, uid="owner", **kwargs):
                return await api.request(method, "/api" + path, headers={"x-preference-member": uid}, **kwargs)
            loaded = await call("GET", "/your-type/preferences")
            assert loaded.status_code == 200 and loaded.json()["revision"] == 0
            assert loaded.headers["cache-control"] == "private, no-store"
            saved = await call("PUT", "/your-type/preferences", json=request())
            assert saved.status_code == 200 and saved.json()["revision"] == 1
            assert saved.json()["by_audience"] == request()["by_audience"]
            assert (await call("PUT", "/your-type/preferences", json=request())).status_code == 409
            assert (await call("PUT", "/your-type/preferences", "peer", json=request())).status_code == 409
            invalid = request(revision=1)
            invalid["by_audience"]["women"]["age_min"] = True
            assert (await call("PUT", "/your-type/preferences", json=invalid)).status_code == 422
            exported = await call("GET", "/me/export")
            assert exported.status_code == 200
            assert exported.json()["account"][prefs.FIELD]["by_audience"] == request()["by_audience"]
            if erasure == "content":
                assert (await call("DELETE", "/me")).status_code == 200
            else:
                assert (await call("PUT", "/me/about", json={"expected_member": "owner",
                    "demographics_patch": {"age": 17}})).status_code == 200
            cleared = await call("GET", "/your-type/preferences")
            assert cleared.status_code == 200 and cleared.json()["revision"] == 2
            assert cleared.json()["by_audience"] == prefs.empty()["by_audience"]
            assert not await db.answer_history.find_one({"owner_id": "owner", "domain": "your_type", "purged": {"$ne": True}})
            if erasure == "content":
                assert not await db.answer_history.find_one({"owner_id": "owner", "purged": {"$ne": True}})
    exercise(case)
