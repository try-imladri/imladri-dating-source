"""Your Type consent/retake/export/privacy invariants on isolated PostgreSQL."""
import asyncio
from copy import deepcopy
import json
import os
import math
import time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException
import pytest

from backend import answer_history, history_lifecycle, postgres_copy_registry, your_type_research as yr
from backend.postgres_store import PostgresDatabase

DSN = os.environ.get("IMLADRI_TEST_POSTGRES_DSN")
BEFORE = "2026-01-01T00:00:00+00:00"
GRANT = "2026-02-01T00:00:00+00:00"
AFTER = "2026-03-01T00:00:00+00:00"


def exercise(callback):
    if not DSN:
        pytest.skip("Requires isolated loopback PostgreSQL.")
    assert urlsplit(DSN).hostname in {"127.0.0.1", "localhost", "::1"}
    async def run():
        db = PostgresDatabase(DSN, "fixture_your_type_research_" + uuid4().hex, allow_local_plaintext=True)
        try:
            await callback(db)
        finally:
            def cleanup(cursor, now):
                for name in ("rpc_requests", "unique_keys", "indexes", "documents"):
                    cursor.execute("SELECT to_regclass(%s)", ("imladri_app." + name,))
                    if cursor.fetchone()[0] is not None:
                        cursor.execute("DELETE FROM imladri_app." + name + " WHERE namespace=%s", (db.name,))
            await db._run(cleanup, write="__fixture_cleanup")
            await db.aclose()
    asyncio.run(run())


def member(uid="owner", **overrides):
    return {"_id": uid, "user_id": uid, "role": "member", "env": "preview", "about": {"demographics": {"age": 30}},
        "your_type_research_version": yr.VERSION, "your_type_research_at": GRANT, "your_type_research_revision": 1,
        "questionnaire_research_version": "all-answers-v1", "row_member_version": "row-member-v1",
        "row_member_code": "a" * 32, "row_member_at": GRANT, "row_member_env": "preview", **overrides}


def session(uid="owner", sid="attempt", **overrides):
    return {"_id": sid, "id": sid, "attempt_id": sid, "user_id": uid, "created_at": AFTER, "updated_at": AFTER, "status": "active", "revision": 0,
        "audience": "women", "asset_edition": "assets-v1", "model_edition": "model-v1",
        "state": {"completed_crowds": 3, "model": {"samples": 14}}, **overrides}


def observation(uid="owner", eid="event", **overrides):
    overrides.setdefault("kind", "your_type.like")
    event = answer_history.event(source="your_type_sessions", source_id="attempt" if uid == "owner" else uid, owner_id=uid, actor_id=uid,
        domain="your_type", question_id=eid, after={"request": {"kind": "like", "face_id": "synthetic-1"},
            "before_state": {"secret_model": "never export research"}, "after_state": {"weights": [1]},
            "effects": {"issued": [{"face_id": "synthetic-2", "prediction": .42}]},
            "asset_edition": "assets-v1", "model_edition": "model-v1"}, at=AFTER,
        consent={"your_type_research_version": yr.VERSION, "your_type_research_at": GRANT,
            "your_type_research_revision": 1}, **overrides)
    return {**event, "_id": event["id"], "generation": 0}


def test_registered_journal_and_repository_are_complete():
    assert "your_type_sessions" in history_lifecycle.SOURCES
    assert {"your_type_sessions", "your_type_profiles"} <= postgres_copy_registry.PERSONAL_COLLECTIONS


def test_transport_receipts_do_not_inflate_logical_actions_or_predictions():
    assert yr.action_counts({"kind": "your_type.batch_committed"}) == {"transport_batches": 1}
    counts = yr.action_counts({"kind": "your_type.next_crowd", "after": {"effects": {
        "issued": [{}, {}], "declared_views": ["a", "b", "c"], "skipped": ["d"],
        "choices": [{"choice": "like"}, {"choice": "pass"}, {"choice": "viewed_pass"}],
        "prospective": {"pairs": 2, "correct": 1, "ties": 0, "explicit_pairs": 1, "viewed_pairs": 1}}}})
    assert counts["accepted_action_events"] == 1 and counts["next_crowd"] == 1
    assert counts["issued_portraits"] == 2 and counts["declared_view_events"] == 3
    assert counts["choice_like"] == counts["choice_pass"] == counts["choice_viewed_pass"] == 1
    assert counts["prospective_pairs"] == 2


def test_no_population_baseline_and_small_n_suppresses_comparison():
    async def case(db):
        await db.users.insert_one(member())
        await db.your_type_sessions.insert_one(session())
        result = await yr.report(db, {"env": "preview"})
        assert result["denominators"] == {"consenting_adults": 1, "attempts": 1, "members_with_sessions": 1}
        assert result["sample_size"] is None and not result["available"]
        assert not result["rows"] and result["cramers_v"] is None
        assert result["population_baseline"]["available"] is False
        assert result["cost"] == {"ai_calls": 0, "message_credits": 0}
    exercise(case)


def test_retakes_count_attempts_but_latest_member_only_and_pregrant_resume_is_excluded():
    async def case(db):
        await db.users.insert_one(member())
        await db.your_type_sessions.insert_many([session(sid="old", created_at=BEFORE), session(sid="new1"),
            session(sid="new2", updated_at="2026-04-01T00:00:00+00:00")])
        result = await yr.report(db, {"env": "preview"})
        assert result["denominators"]["attempts"] == 2
        assert result["denominators"]["members_with_sessions"] == 1
    exercise(case)


def test_demographics_require_both_permissions_and_no_inference():
    assert "age_band" not in yr.latest_values(member(questionnaire_research_version=None), session())
    assert yr.latest_values(member(), session())["age_band"] == "25-34"


@pytest.mark.parametrize("override", [{"your_type_research_version": None}, {"role": "admin"}, {"closing": True},
                                      {"about": {"demographics": {"age": 17}}}])
def test_nonconsenting_minor_admin_and_closing_are_excluded(override):
    async def case(db):
        await db.users.insert_one(member(**override))
        await db.your_type_sessions.insert_one(session())
        assert (await yr.report(db, {"env": "preview"}))["denominators"] == {}
    exercise(case)


def test_row_export_excludes_model_internal_peer_and_unconsented_events_and_stops_after_withdrawal():
    async def case(db):
        await db.users.insert_one(member())
        await db.your_type_sessions.insert_one(session())
        await db.answer_history.insert_many([observation(eid="allowed"), observation(uid="peer", eid="peer"),
            observation(eid="unconsented", baseline=True)])
        eligible = {"env": "preview", "row_member_code": "a" * 32}
        rows = [row async for row in yr.member_rows(db, eligible, "owner", "m_" + "a"*32, "2026-04-01T00:00:00+00:00")]
        assert len(rows) == 1
        value = json.dumps(rows)
        assert "never export research" not in value and "weights" not in value and "peer" not in value
        assert "prediction" in value and "synthetic-1" in value
        await db.users.update_one({"user_id": "owner"}, {"$unset": {"your_type_research_version": ""}})
        assert not [row async for row in yr.member_rows(db, eligible, "owner", "m_" + "a"*32, "2026-04-01T00:00:00+00:00")]
        assert await db.answer_history.count_documents({"purged": {"$ne": True}}) == 3  # private owner history retained
    exercise(case)


def test_consent_cas_journal_withdrawal_noop_and_minor_rejection():
    async def case(db):
        original = member(your_type_research_version=None, your_type_research_revision=0)
        await db.users.insert_one(original)
        body = yr.Consent(expected_member="owner", version=yr.VERSION, notice_version=yr.NOTICE_VERSION,
            enabled=True, adult_confirmed=True, revision=0)
        accepted = await yr.consent(db, original, body)
        assert accepted["enabled"] and accepted["revision"] == 1
        with pytest.raises(HTTPException) as conflict:
            await yr.consent(db, original, body)
        assert conflict.value.status_code == 409
        row = await db.users.find_one({"user_id": "owner"})
        await yr.consent(db, row, yr.Consent(expected_member="owner", version=yr.VERSION, enabled=False, revision=1))
        assert await db.answer_history.count_documents({"domain": "consent", "question_id": "your_type.research"}) == 2
        row = await db.users.find_one({"user_id": "owner"})
        await yr.consent(db, row, yr.Consent(expected_member="owner", version=yr.VERSION, enabled=False, revision=2))
        assert await db.answer_history.count_documents({"question_id": "your_type.research"}) == 2
        await db.users.update_one({"user_id": "owner"}, {"$set": {"about.demographics.age": 17}})
        with pytest.raises(HTTPException) as minor:
            await yr.consent(db, row, yr.Consent(expected_member="owner", version=yr.VERSION, notice_version=yr.NOTICE_VERSION,
                enabled=True, adult_confirmed=True, revision=2))
        assert minor.value.status_code == 403
    exercise(case)


def test_mixed_editions_never_pool_correlation():
    async def case(db):
        await db.users.insert_many([member("a"), member("b")])
        await db.your_type_sessions.insert_many([session("a", "a"), session("b", "b", model_edition="different")])
        report = await yr.report(db, {"env": "preview"})
        assert report["edition_cohorts"] == 2 and not report["edition_compatible"] and not report["available"]
        selected = await yr.report(db, {"env": "preview"}, edition=report["edition_choices"][0]["id"])
        assert selected["edition_compatible"]
    exercise(case)


def test_real_comparison_requires_fifty_distinct_people_and_large_cells():
    async def case(db):
        people, attempts = [], []
        for i in range(60):
            uid = "member_" + str(i)
            people.append(member(uid))
            attempts.append(session(uid, uid, audience="women" if i < 30 else "men",
                state={"completed_crowds": 1 if i % 2 else 10, "model": {"samples": 30}}))
        await db.users.insert_many(people)
        await db.your_type_sessions.insert_many(attempts)
        result = await yr.report(db, {"env": "preview"})
        assert result["available"] and result["sample_size"] == 60
        assert result["cramers_v"] == 0.0
        assert result["rows"] == [{"index": 0, "cells": [15, 15], "total": 30}, {"index": 1, "cells": [15, 15], "total": 30}]
        await db.your_type_sessions.update_one({"_id": "member_0"}, {"$set": {"state.completed_crowds": 0}})
        hidden = await yr.report(db, {"env": "preview"})
        assert not hidden["available"] and hidden["sample_size"] is None and not hidden["rows"]
    exercise(case)


def test_withdrawal_between_streamed_rows_stops_export_and_old_grant_never_returns():
    async def case(db):
        await db.users.insert_one(member())
        await db.your_type_sessions.insert_one(session())
        await db.answer_history.insert_many([observation(eid="a"), observation(eid="b")])
        stream = yr.member_rows(db, {"env": "preview"}, "owner", "m_" + "a"*32, "2026-04-01T00:00:00+00:00")
        assert (await anext(stream))["status"] == "recorded_action"
        await db.users.update_one({"user_id": "owner"}, {"$unset": {"your_type_research_version": ""}})
        assert await anext(stream, None) is None
        await db.users.update_one({"user_id": "owner"}, {"$set": {"your_type_research_version": yr.VERSION,
            "your_type_research_revision": 3, "your_type_research_at": "2026-03-10T00:00:00+00:00"}})
        assert not [r async for r in yr.member_rows(db, {"env": "preview"}, "owner", "m_"+"a"*32, "2026-04-01T00:00:00+00:00")]
    exercise(case)


@pytest.mark.parametrize("override", [{"row_member_code": None}, {"row_member_version": None},
    {"row_member_at": None}, {"row_member_code": ["a" * 32]}, {"row_member_env": "different"}])
def test_research_permission_does_not_grant_raw_rows_without_separate_scalar_grant(override):
    async def case(db):
        await db.users.insert_one(member(**override))
        await db.your_type_sessions.insert_one(session())
        await db.answer_history.insert_one(observation())
        assert not [row async for row in yr.member_rows(db, {"env": "preview"}, "owner", "m_" + "a" * 32, AFTER)]
    exercise(case)


def test_resuming_a_prepermission_attempt_does_not_expose_its_derived_predictions():
    async def case(db):
        await db.users.insert_one(member())
        await db.your_type_sessions.insert_one(session(created_at=BEFORE, updated_at=AFTER))
        await db.answer_history.insert_one(observation())
        assert not [row async for row in yr.member_rows(db, {"env": "preview"}, "owner", "m_" + "a"*32, AFTER)]
        report = await yr.report(db, {"env": "preview"})
        assert report["activity"] == {} and report["denominators"] == {"consenting_adults": 1}
    exercise(case)


def test_bounded_event_scan_withholds_incomplete_correlations_and_retired_edition_is_empty(monkeypatch):
    async def case(db):
        await db.users.insert_one(member())
        await db.your_type_sessions.insert_one(session())
        await db.answer_history.insert_many([observation(eid="a"), observation(eid="b")])
        monkeypatch.setattr(yr, "MAX_EVENTS", 1)
        result = await yr.report(db, {"env": "preview"})
        assert result["truncated"] and not result["available"] and result["sample_size"] is None
        monkeypatch.setattr(yr, "MAX_EVENTS", 99999)
        empty = await yr.report(db, {"env": "preview"}, edition="f" * 64)
        assert not empty["edition_available"] and not empty["available"]
    exercise(case)


def test_feature_and_questionnaire_correlations_are_edition_and_signal_qualified():
    from backend.your_type_model_v1 import EDITION
    bank = {"sha256": "fixed", "feature_groups": [{"id": "lighting", "label": "Lighting",
        "indices": [0, 1], "values": ["Soft", "Direct"]}]}
    snapshot = session(model_edition=EDITION, asset_manifest_sha256="fixed", state={"completed_crowds": 20,
        "model": {"likes": 30, "explicit_passes": 3, "weights": [0, .2, -.4], "feature_observations": [6, 6], "feature_option_counts": [3, 3]}})
    values = yr.latest_values(member(), snapshot, bank)
    assert values["feature:0"] == "more_often_favored" and values["feature:1"] == "less_often_favored"
    assert "feature:0" not in yr.latest_values(member(), {**snapshot, "asset_manifest_sha256": "wrong"}, bank)
    assert "feature:0" not in yr.latest_values(member(), {**snapshot, "model_edition": "old"}, bank)
    insufficient = deepcopy(snapshot)
    insufficient["state"]["model"]["feature_option_counts"][0] = 2
    assert "feature:0" not in yr.latest_values(member(), insufficient, bank)
    constant = deepcopy(snapshot)
    constant["state"]["model"]["feature_option_counts"] = [6, 0]
    assert not any(key.startswith("feature:") for key in yr.latest_values(member(), constant, bank))
    masked_no_negative = deepcopy(snapshot)
    masked_no_negative["state"]["model"]["feature_observations"][0] = 3
    assert "feature:0" not in yr.latest_values(member(), masked_no_negative, bank)
    member_record = member(about={"demographics": {"age": 30, "height_cm": 177, "gender": "Prefer not to say"}, "mbti": "INTJ"})
    demographics = yr.latest_values(member_record, session())
    assert demographics["height_band"] == "170–179 cm" and demographics["p:mbti"] == "INTJ"
    assert "d:gender" not in demographics


def test_full_history_erasure_leaves_only_opaque_fences_and_never_peer_data():
    async def case(db):
        from backend import your_type
        own, peer = observation(), observation(uid="peer", eid="peer")
        row = session()
        answer_history.append(row_update := {"$set": row}, own, {})
        row.update(row_update["$set"])
        row[answer_history.PENDING] = row_update["$push"][answer_history.PENDING]["$each"]
        for key, value in row_update["$inc"].items(): row[key] = value
        await db.users.insert_many([member(), member("peer")])
        await db.your_type_sessions.insert_one(row)
        await db.answer_history.insert_one(peer)
        exported = await your_type.own_export(db, "owner")
        assert len(exported) == 1 and not any(key in exported[0] for key in answer_history.FIELDS)
        await your_type.clear_member(db, "owner")
        assert not await db.answer_history.find_one({"owner_id": "owner", "purged": {"$ne": True}})
        assert await db.answer_history.find_one({"owner_id": "peer", "purged": {"$ne": True}})
        assert await db.your_type_sessions.find_one({"_id": "attempt"}) == {"_id": "attempt", "deleted": True}
    exercise(case)


def test_application_http_admin_csv_owner_export_and_content_clear_are_connected(monkeypatch):
    import httpx
    from fastapi import Request
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("IMLADRI_DATABASE_BACKEND", "mongodb")  # import-only lazy fixture client, immediately replaced
    monkeypatch.setenv("IMLADRI_DATABASE_MAINTENANCE", "off")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:62973")
    monkeypatch.setenv("DB_NAME", "unused_import_only_fixture")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_fixture")
    from backend import server
    async def case(db):
        monkeypatch.setattr(server, "db", db)
        async def identity(request: Request):
            uid = request.headers.get("x-research-member")
            if uid not in {"owner", "peer", "admin"}:
                raise HTTPException(401, "Fixture identity required")
            return {"user_id": uid}
        monkeypatch.setitem(server.app.dependency_overrides, server.require_user, identity)
        await db.users.insert_many([member(env=server.ENV, row_member_env=server.ENV),
            member("peer", env=server.ENV, your_type_research_version=None), member("admin", env=server.ENV, role="admin")])
        await db.your_type_sessions.insert_many([session(), session("peer", "peer")])
        await db.answer_history.insert_many([observation(), observation(uid="peer", eid="peer")])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
            async def call(method, path, uid="owner", **kwargs):
                return await api.request(method, "/api" + path, headers={"x-research-member": uid}, **kwargs)
            assert (await call("GET", "/admin/research/your-type")).status_code == 403
            wrong_owner = {"expected_member": "peer", "version": yr.VERSION, "revision": 1, "adult_confirmed": True, "enabled": False}
            assert (await call("PATCH", "/your-type/research", json=wrong_owner)).status_code == 409
            assert (await db.users.find_one({"user_id": "owner"}))["your_type_research_version"] == yr.VERSION
            assert not await db.answer_history.find_one({"question_id": "your_type.research"})
            del wrong_owner["expected_member"]
            assert (await call("PATCH", "/your-type/research", json=wrong_owner)).status_code == 422
            overview = await call("GET", "/admin/research/your-type", "admin")
            assert overview.status_code == 200, overview.text
            assert overview.json()["denominators"]["members_with_sessions"] == 1
            details = await call("GET", "/users/owner/your-type", "admin")
            assert details.status_code == 200 and details.json()["attempts"] == 1
            assert "audience" not in details.json()["latest"]
            csv = await call("GET", "/users/owner/your-type.csv", "admin")
            assert csv.status_code == 200 and "synthetic-1" in csv.text and "secret_model" not in csv.text
            private = await call("GET", "/me/export")
            assert private.status_code == 200, private.text
            body = private.json()
            assert len(body["your_type_sessions"]) == 1 and body["your_type_sessions"][0]["user_id"] == "owner"
            assert all(event["owner_id"] == "owner" for event in body["answer_history"])
            assert (await call("DELETE", "/me")).status_code == 200
            assert not await db.your_type_sessions.find_one({"user_id": "owner"})
            assert await db.your_type_sessions.find_one({"user_id": "peer"})
            assert not await db.answer_history.find_one({"owner_id": "owner", "purged": {"$ne": True}})
            assert (await call("GET", "/users/owner/your-type.csv", "admin")).status_code == 404
    exercise(case)


def test_wrong_consent_owner_is_rejected_before_any_database_access():
    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError("Ownership must be checked before database access")
    async def run():
        with pytest.raises(HTTPException) as wrong:
            await yr.consent(NoDatabase(), {"user_id": "owner"}, yr.Consent(expected_member="peer",
                version=yr.VERSION, enabled=False, revision=0))
        assert wrong.value.status_code == 409
    asyncio.run(run())


def test_real_start_owner_fence_is_retained_privately_but_never_exported_in_coded_research(monkeypatch):
    from backend import your_type
    from backend.tests.test_your_type import manifest
    raw = manifest()
    bank = {**raw, "sha256": "a" * 64, "by_id": {item["id"]: item for item in raw["assets"]},
            "position": {item["id"]: index for index, item in enumerate(raw["assets"])}}
    monkeypatch.setattr(your_type, "catalog", lambda: bank)
    async def case(db):
        stamp = int(time.time())
        await db.users.insert_one(member(tier="paid", stripe_subscription_id="fixture_sub",
            subscription_period_start=stamp-60, subscription_period_end=stamp+3600))
        body = your_type.Start(expected_member="owner", request_id=str(uuid4()), audience="women", adult_confirmed=True)
        accepted = await your_type.start(db, "owner", body)
        rows = [entry async for entry in yr.member_rows(db, {"env": "preview"}, "owner", "m_" + "a"*32,
            "2099-01-01T00:00:00+00:00")]
        assert len(rows) == 1
        public = json.loads(rows[0]["answer"])
        assert public["request"]["request_id"] == body.request_id and public["request"]["audience"] == "women"
        assert "expected_member" not in public["request"] and '"owner"' not in json.dumps(rows)
        private = [event async for event in history_lifecycle.own_events(db, "owner") if event.get("domain") == "your_type"]
        assert private[0]["after"]["request"]["expected_member"] == "owner"
        assert accepted["id"]
    exercise(case)


@pytest.mark.parametrize("simulated_request_latency", [0, .15])
def test_sixty_member_report_uses_bounded_signed_gateway_batches_and_rechecks_withdrawal(monkeypatch, capsys, record_property, simulated_request_latency):
    import httpx
    from bson import BSON
    from backend.postgres_remote import RemotePostgresDatabase
    from backend.tests.test_postgres_rpc import app_for, SECRET, ORIGIN, URL
    monkeypatch.setenv("IMLADRI_POSTGRES_GATEWAY_SECRET", SECRET)
    async def case(db):
        people, attempts, events = [], [], []
        for i in range(60):
            uid = "batch_member_" + str(i).zfill(2)
            people.append(member(uid))
            attempts.append(session(uid, uid, audience="women" if i < 30 else "men",
                state={"completed_crowds": 1 if i % 2 else 10, "model": {"samples": 30}}))
            events.append(observation(uid, kind="your_type.like"))
        await db.users.insert_many(people)
        await db.your_type_sessions.insert_many(attempts)
        await db.answer_history.insert_many(events)
        class CountTransport(httpx.AsyncBaseTransport):
            def __init__(self):
                self.inner = httpx.ASGITransport(app=app_for(db))
                self.operations = []
                self.withdrew = False
            async def handle_async_request(self, request):
                if simulated_request_latency:
                    await asyncio.sleep(simulated_request_latency)
                body = BSON(await request.aread()).decode()
                self.operations.append(body["operation"])
                # Withdrawal after source reads but BEFORE the final batch consent read.
                if body["operation"] == "find" and body.get("collection") == "users" and "about.demographics" in body.get("arguments", {}).get("projection", {}) and not self.withdrew:
                    self.withdrew = True
                    await db.users.update_one({"user_id": "batch_member_00"}, {"$unset": {"your_type_research_version": ""}})
                return await self.inner.handle_async_request(request)
        transport = CountTransport()
        remote = RemotePostgresDatabase(URL, SECRET, ORIGIN, db.name, transport=transport)
        started = time.monotonic()
        try:
            result = await yr.report(remote, {"env": "preview"})
            assert result["available"] and not result["truncated"]
            assert result["denominators"]["consenting_adults"] == 59
            assert result["denominators"]["members_with_sessions"] == 59
            assert result["activity"]["like"] == 59
            assert result["scan"]["member_batches"] == math.ceil(60 / yr.REPORT_BATCH)
            assert len(transport.operations) <= 4 * math.ceil(60 / yr.REPORT_BATCH) + 1
            assert set(transport.operations) <= {"find", "ping"}
            print(json.dumps({"fixture_members": 60, "current_consented_after_withdrawal": 59,
                "gateway_requests": len(transport.operations), "prior_n_plus_one_estimate_including_handshake": 182,
                "elapsed_seconds_local_asgi_and_postgres": round(time.monotonic() - started, 3),
                "injected_per_request_latency_ms": simulated_request_latency * 1000,
                "production_latency_measured": False}))
        finally:
            await remote.aclose()
    exercise(case)
    captured = capsys.readouterr().out
    measured = json.loads(captured.strip())
    assert measured["gateway_requests"] == 33
    for key, value in measured.items(): record_property(key, value)


def explicit_snapshot(**overrides):
    return {"version": yr.PREFERENCE_VERSION, "sexuality": "bi", "viewer_gender": "Man",
            "rated_audience": "both", **overrides}


def test_preference_research_uses_only_immutable_explicit_attempt_snapshot():
    person = member(about={"demographics": {"age": 30, "gender": "Woman", "sexuality": "lesbian"}},
                    your_type_preferences=explicit_snapshot(sexuality="lesbian", viewer_gender="Woman", rated_audience="women"))
    legacy = yr.latest_values(person, session())
    assert not {"sexuality", "viewer_gender", "rated_audience"} & set(legacy)
    current = yr.latest_values(person, session(audience="both", preference_snapshot=explicit_snapshot()))
    assert {key: current[key] for key in ("sexuality", "viewer_gender", "rated_audience")} == {
        "sexuality": "bi", "viewer_gender": "Man", "rated_audience": "both"}
    assert "both" in yr.VARIABLES["audience"]["choices"]
    for gender in (None, "Prefer not to say"):
        values = yr.latest_values(person, session(audience="both", preference_snapshot=explicit_snapshot(viewer_gender=gender)))
        assert values["sexuality"] == "bi" and "viewer_gender" not in values


@pytest.mark.parametrize("bad", [None, {}, explicit_snapshot(version="future"), explicit_snapshot(sexuality="inferred"),
    explicit_snapshot(viewer_gender=["Man"]), explicit_snapshot(viewer_gender=True), explicit_snapshot(rated_audience=["both"]),
    {**explicit_snapshot(), "personality": "inferred"}, explicit_snapshot(rated_audience="women")])
def test_malformed_unrecorded_and_mismatched_preference_metadata_remains_unknown(bad):
    assert yr.preference_snapshot(bad, "both") is None


def test_explicit_preference_CSV_joins_only_matching_own_attempt_and_retains_legacy_unknown():
    import csv
    import io
    async def case(db):
        await db.users.insert_one(member(your_type_preferences=explicit_snapshot(sexuality="gay", rated_audience="men")))
        await db.your_type_sessions.insert_many([session(audience="both", preference_snapshot=explicit_snapshot()), session(sid="legacy")])
        recorded = observation(eid="recorded")
        recorded["attempt_id"] = "attempt"
        recorded["after"]["preference_snapshot"] = explicit_snapshot()
        recorded["after"]["request"].update(sexuality="unverified-request-label", viewer_gender="unverified-gender")
        legacy = observation(eid="legacy-event"); legacy.update(source_id="legacy", attempt_id="legacy")
        mismatch = observation(eid="mismatch")
        mismatch["attempt_id"] = "attempt"
        mismatch["after"]["preference_snapshot"] = explicit_snapshot(sexuality="gay", rated_audience="men")
        await db.answer_history.insert_many([recorded, legacy, mismatch, observation("peer", "peer-event")])
        response = await yr.member_csv(db, {"env": "preview"}, "owner", "2026-04-01T00:00:00+00:00")
        chunks = [chunk async for chunk in response.body_iterator]
        text = "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)
        rows = list(csv.DictReader(io.StringIO(text)))
        assert len(rows) == 3
        payloads = {row["variable_id"]: json.loads(row["answer"]) for row in rows}
        assert payloads["recorded"]["preference_snapshot"] == explicit_snapshot()
        assert "preference_snapshot" not in payloads["legacy-event"] and "preference_snapshot" not in payloads["mismatch"]
        provenance = {row["variable_id"]: json.loads(row["conditions"]) for row in rows}
        assert provenance["recorded"]["preference_snapshot_status"] == "self_reported_at_attempt_start"
        assert provenance["recorded"]["attempt_code"] == provenance["mismatch"]["attempt_code"]
        assert provenance["legacy-event"]["preference_snapshot_status"] == "unknown"
        assert '"owner"' not in text and "peer-event" not in text and "unverified-request-label" not in text
        assert "unverified-gender" not in text and "weights" not in text
        await db.users.update_one({"user_id": "owner"}, {"$unset": {"row_member_version": ""}})
        with pytest.raises(HTTPException) as denied:
            await yr.member_csv(db, {"env": "preview"}, "owner", "2026-04-01T00:00:00+00:00")
        assert denied.value.status_code == 404
    exercise(case)


def test_preference_admin_detail_requires_current_both_grants_and_new_attempt():
    async def case(db):
        await db.users.insert_one(member())
        await db.your_type_sessions.insert_one(session(audience="both", preference_snapshot=explicit_snapshot()))
        report = await yr.member_admin(db, "owner")
        assert report["latest"]["preference_snapshot"] == explicit_snapshot()
        assert report["latest"]["attempt_code"] and "id" not in report["latest"]
        assert report["preference_snapshot_requires_both_current_grants"] is True
        await db.users.update_one({"user_id": "owner"}, {"$unset": {"your_type_research_version": ""}})
        assert "preference_snapshot" not in (await yr.member_admin(db, "owner"))["latest"]
        await db.users.update_one({"user_id": "owner"}, {"$set": {"your_type_research_version": yr.VERSION}, "$unset": {"row_member_version": ""}})
        assert "preference_snapshot" not in (await yr.member_admin(db, "owner"))["latest"]
        await db.users.update_one({"user_id": "owner"}, {"$set": {"row_member_version": "row-member-v1", "row_member_at": "2026-03-15T00:00:00+00:00"}})
        assert "preference_snapshot" not in (await yr.member_admin(db, "owner"))["latest"]
        await db.users.update_one({"user_id": "owner"}, {"$set": {"row_member_at": GRANT, "role": "admin"}})
        assert "preference_snapshot" not in (await yr.member_admin(db, "owner"))["latest"]
    exercise(case)


def test_preference_rows_stop_when_attempt_enters_erasure():
    async def case(db):
        await db.users.insert_one(member())
        await db.your_type_sessions.insert_one(session(audience="both", preference_snapshot=explicit_snapshot()))
        events = [observation(eid=name) for name in ("a", "b")]
        for event in events:
            event["attempt_id"] = "attempt"
            event["after"]["preference_snapshot"] = explicit_snapshot()
        await db.answer_history.insert_many(events)
        rows = yr.member_rows(db, {"env": "preview"}, "owner", "m_" + "a" * 32, "2026-04-01T00:00:00+00:00")
        assert "preference_snapshot" in json.loads((await anext(rows))["answer"])
        await db.your_type_sessions.update_one({"_id": "attempt"}, {"$set": {"status": "clearing"}})
        assert await anext(rows, None) is None
    exercise(case)


def test_sensitive_preference_correlations_keep_minimum_cells_missing_exclusion_and_default_off():
    async def case(db):
        people, attempts = [], []
        for i in range(60):
            uid = "preference_member_" + str(i).zfill(2)
            people.append(member(uid))
            snapshot = explicit_snapshot(sexuality="straight" if i < 30 else "gay", rated_audience="women" if i < 30 else "men")
            attempts.append(session(uid, uid, audience=snapshot["rated_audience"], preference_snapshot=snapshot,
                state={"completed_crowds": 1 if i % 2 else 10, "model": {"samples": 30}}))
        await db.users.insert_many(people); await db.your_type_sessions.insert_many(attempts)
        report = await yr.report(db, {"env": "preview"}, "sexuality", "progress")
        assert report["available"] and report["sample_size"] == 60 and report["minimum_paired"] == 50 and report["minimum_cell"] == 10
        await db.your_type_sessions.update_many({"user_id": {"$in": [p["user_id"] for p in people[:31]]}}, {"$unset": {"preference_snapshot": ""}})
        hidden = await yr.report(db, {"env": "preview"}, "sexuality", "progress")
        assert not hidden["available"] and hidden["sample_size"] is None and hidden["rows"] == []
        await db.users.update_many({}, {"$unset": {"your_type_research_version": ""}})
        off = await yr.report(db, {"env": "preview"}, "sexuality", "progress")
        assert off["denominators"] == {} and not off["available"]
    exercise(case)


def test_expanded_v2_permission_never_upgrades_old_positive_grants_or_relabels_private_history():
    async def case(db):
        original = member(your_type_research_version="your-type-research-v1")
        await db.users.insert_one(original)
        await db.your_type_sessions.insert_one(session(audience="both", preference_snapshot=explicit_snapshot()))
        old = observation(eid="legacy-private")
        old["attempt_id"] = "attempt"
        old["consent"]["your_type_research_version"] = "your-type-research-v1"
        old["after"]["preference_snapshot"] = explicit_snapshot()
        await db.answer_history.insert_one(old)
        current = yr.preferences(original)
        assert current["version"] == "your-type-research-v2" and current["enabled"] is False
        assert current["requires_expanded_consent"] is True
        assert (await yr.report(db, {"env": "preview"}, "sexuality", "progress"))["denominators"] == {}
        assert "preference_snapshot" not in (await yr.member_admin(db, "owner"))["latest"]
        with pytest.raises(HTTPException) as denied:
            await yr.member_csv(db, {"env": "preview"}, "owner", "2099-01-01T00:00:00+00:00")
        assert denied.value.status_code == 404
        assert (await db.users.find_one({"user_id": "owner"}))["your_type_research_version"] == "your-type-research-v1"
        assert await db.answer_history.find_one({"id": old["id"]}) == old
        enabled = await yr.consent(db, original, yr.Consent(expected_member="owner", version=yr.VERSION,
            notice_version=yr.NOTICE_VERSION, enabled=True, adult_confirmed=True, revision=original["your_type_research_revision"]))
        assert enabled["enabled"] is True and enabled["requires_expanded_consent"] is False
        # New consent does not make a pre-consent attempt or its old events eligible.
        report = await yr.report(db, {"env": "preview"}, "sexuality", "progress")
        assert report["denominators"] == {"consenting_adults": 1}
        assert not [row async for row in yr.member_rows(db, {"env": "preview"}, "owner", "m_" + "a" * 32,
            "2099-01-01T00:00:00+00:00")]
        assert await db.answer_history.find_one({"id": old["id"]}) == old
        assert (await db.your_type_sessions.find_one({"_id": "attempt"}))["preference_snapshot"] == explicit_snapshot()
    exercise(case)


def test_CSV_rechecks_permission_after_awaited_attempt_validation_before_yield():
    async def case(db):
        await db.users.insert_one(member())
        await db.your_type_sessions.insert_one(session(audience="both", preference_snapshot=explicit_snapshot()))
        event = observation(); event["attempt_id"] = "attempt"; event["after"]["preference_snapshot"] = explicit_snapshot()
        await db.answer_history.insert_one(event)
        source = db.your_type_sessions
        class WithdrawingSource:
            called = False
            def __getattr__(self, name): return getattr(source, name)
            async def find_one(self, *args, **kwargs):
                result = await source.find_one(*args, **kwargs)
                self.called = True
                await db.users.update_one({"user_id": "owner"}, {"$unset": {"your_type_research_version": ""}})
                return result
        wrapped = WithdrawingSource()
        class Database:
            def __getattr__(self, name): return wrapped if name == "your_type_sessions" else getattr(db, name)
        rows = [row async for row in yr.member_rows(Database(), {"env": "preview"}, "owner", "m_" + "a" * 32,
            "2026-04-01T00:00:00+00:00")]
        assert wrapped.called and rows == []
        assert await db.answer_history.count_documents({"domain": "your_type"}) == 1
    exercise(case)


def test_admin_count_await_cannot_leak_preference_after_consent_withdrawal():
    async def case(db):
        await db.users.insert_one(member())
        await db.your_type_sessions.insert_one(session(audience="both", preference_snapshot=explicit_snapshot()))
        source = db.your_type_sessions
        class WithdrawingCount:
            called = False
            def __getattr__(self, name): return getattr(source, name)
            async def count_documents(self, *args, **kwargs):
                count = await source.count_documents(*args, **kwargs)
                self.called = True
                await db.users.update_one({"user_id": "owner"}, {"$unset": {"your_type_research_version": ""}})
                return count
        wrapped = WithdrawingCount()
        class Database:
            def __getattr__(self, name): return wrapped if name == "your_type_sessions" else getattr(db, name)
        result = await yr.member_admin(Database(), "owner")
        assert wrapped.called and result["attempts"] == 1 and result["research"]["enabled"] is False
        assert "preference_snapshot" not in result["latest"] and "attempt_code" not in result["latest"]
    exercise(case)


def test_old_UI_cannot_enable_server_v2_without_displayed_expanded_notice_marker():
    async def case(db):
        original = member(your_type_research_version=None, your_type_research_revision=0)
        await db.users.insert_one(original)
        for marker in (None, "your-type-image-only-research-v1", "wrong-v2"):
            # This is exactly the old UI's dynamic-version enable request when
            # marker=None: receiving server versionv2 is not scope acceptance.
            body = yr.Consent(expected_member="owner", version=yr.VERSION, enabled=True,
                adult_confirmed=True, revision=0, notice_version=marker)
            with pytest.raises(HTTPException) as denied:
                await yr.consent(db, original, body)
            assert denied.value.status_code == 409
        assert (await db.users.find_one({"user_id": "owner"}))["your_type_research_revision"] == 0
        assert await db.answer_history.count_documents({"question_id": "your_type.research"}) == 0
        accepted = await yr.consent(db, original, yr.Consent(expected_member="owner", version=yr.VERSION,
            enabled=True, adult_confirmed=True, revision=0, notice_version=yr.NOTICE_VERSION))
        assert accepted["enabled"] is True and accepted["revision"] == 1
        event = await db.answer_history.find_one({"question_id": "your_type.research"})
        assert event["after"]["notice_version"] == yr.NOTICE_VERSION
        assert event["after"]["requested_version"] == yr.VERSION
    exercise(case)


def test_legacy_opt_out_remains_possible_without_notice_or_scope_upgrade():
    async def case(db):
        old = member(your_type_research_version="your-type-research-v1")
        await db.users.insert_one(old)
        result = await yr.consent(db, old, yr.Consent(expected_member="owner", version="your-type-research-v1",
            enabled=False, revision=1))
        assert result["enabled"] is False and result["requires_expanded_consent"] is False
        current = await db.users.find_one({"user_id": "owner"})
        assert "your_type_research_version" not in current
        event = await db.answer_history.find_one({"question_id": "your_type.research"})
        assert event["after"]["enabled"] is False and event["after"]["requested_version"] == "your-type-research-v1"
        assert event["after"]["notice_version"] is None
        # A current-grant owner can also withdraw using a cached older client;
        # the revision CAS still protects concurrent edits.
        await db.users.update_one({"user_id": "owner"}, {"$set": {"your_type_research_version": yr.VERSION}})
        current = await db.users.find_one({"user_id": "owner"})
        off = await yr.consent(db, current, yr.Consent(expected_member="owner", version="your-type-research-v1",
            enabled=False, revision=current["your_type_research_revision"]))
        assert off["enabled"] is False and "your_type_research_version" not in await db.users.find_one({"user_id": "owner"})
    exercise(case)
