"""Individual HTTP history, owner boundaries and erasure on isolated storage.

All identities, answers and payment-period flags below are disposable fixtures.
No external identity, payment, mail or AI provider is called.
"""
import asyncio
import json
import os
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorClient

from backend import answer_context, answer_history, history_lifecycle, research


def run_case(monkeypatch, scenario):
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    dsn = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
    if not uri and not dsn:
        pytest.skip("Requires a disposable loopback PostgreSQL or MongoDB fixture.")
    assert urlsplit(dsn or uri).hostname in {"localhost", "127.0.0.1", "::1"}
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("IMLADRI_DATABASE_BACKEND", "mongodb")  # import-only lazy client, replaced below
    monkeypatch.setenv("MONGO_URL", uri or "mongodb://127.0.0.1:62973")
    monkeypatch.setenv("DB_NAME", "individual_history_http_fixture")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_fixture")
    from backend import server

    async def run():
        namespace = "individual_answer_history_" + uuid4().hex
        if dsn:
            from backend.postgres_store import PostgresDatabase
            db = PostgresDatabase(dsn, namespace, allow_local_plaintext=True)
            mongo = None
        else:
            mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
            db = mongo[namespace]
        monkeypatch.setattr(server, "db", db)

        async def identity(request: Request):
            uid = request.headers.get("x-history-member")
            if uid not in {"owner", "peer", "admin", "unpaid"}:
                raise HTTPException(401, "Fixture identity required")
            return {"user_id": uid}

        monkeypatch.setitem(server.app.dependency_overrides, server.require_user, identity)
        try:
            stamp = int(time.time())
            for uid in ("owner", "peer", "admin", "unpaid"):
                member = {"user_id": uid, "role": "admin" if uid == "admin" else "member", "env": server.ENV,
                          "about": {"answers": {}, "demographics": {"age": 30, "height_cm": 175, "gender": "Man",
                                   "sex": "Male", "ethnicity": "Asian", "education": "Bachelor's"}},
                          "couples_adult_at": "2026-01-01T00:00:00+00:00"}
                if uid != "unpaid":
                    member.update(tier="paid", stripe_subscription_id="sub_fixture_" + uid,
                                  subscription_period_start=stamp - 60, subscription_period_end=stamp + 86400)
                await db.users.insert_one(member)
            await server.ensure_indexes()
            quiz = (await server.get_settings())["quiz"]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost") as api:
                async def call(method, path, body=None, *, uid="owner", status=200, raw=False):
                    headers = {"x-history-member": uid} if uid is not None else {}
                    response = await api.request(method, "/api" + path, headers=headers, json=body)
                    assert response.status_code == status, (method, path, response.status_code, response.text)
                    return response if raw else response.json()
                await scenario(db, call, quiz, server)
        finally:
            if mongo is not None:
                await mongo.drop_database(db.name)
                mongo.close()
            else:
                def cleanup(cursor, _now):
                    for table in ("rpc_requests", "unique_keys", "indexes", "documents"):
                        cursor.execute("SELECT to_regclass(%s)", ("imladri_app." + table,))
                        if cursor.fetchone()[0] is not None:
                            cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (db.name,))
                await db._run(cleanup, write="__fixture_cleanup")
                await db.aclose()

    asyncio.run(run())


def selection(question, choice=0, note=""):
    return {"choice": choice, "note": note, "question": question["q"], "label": question["options"][choice]["label"]}


async def observations(db, *, uid="owner", domain=None):
    criteria = {"owner_id": uid, "purged": {"$ne": True}}
    if domain:
        criteria["domain"] = domain
    return await db.answer_history.find(criteria).sort([("at", 1), ("id", 1)]).to_list(None)


async def journal(db, uid, events, **fields):
    row = await db.users.find_one({"user_id": uid})
    update = {"$set": fields} if fields else {}
    answer_history.append(update, events, row)
    changed = await db.users.update_one(answer_history.guard({"user_id": uid}, row), update)
    assert changed.modified_count == 1


def test_http_answers_capture_wording_before_after_noop_retry_and_quick_skip(monkeypatch):
    async def scenario(db, call, quiz, server):
        question, skipped = quiz["short"][:2]
        qid = question["id"]
        body = {"expected_member": "owner", "answers": {qid: selection(question, note="First reflection")}}
        await call("PUT", "/me/about", body)
        rows = await observations(db, domain="everyday")
        assert len(rows) == 1
        first = rows[0]
        assert first["before"] is None and not first["before_exists"]
        assert first["after"]["choice"] == 0 and first["after"]["note"] == "First reflection"
        assert first["question"]["question"] == question
        assert first["signature"] == research.fingerprint(first["question"])
        assert first["demographics"]["snapshot_id"]
        assert set(first["demographics"]) == {"snapshot_id", "observed_at"}
        await call("PUT", "/me/about", body)
        assert len(await observations(db, domain="everyday")) == 1
        changed = {"answers": {qid: selection(question, 1, "Revised reflection")}}
        await call("PUT", "/me/about", changed)
        second = (await observations(db, domain="everyday"))[-1]
        assert second["before"] == first["after"] and second["before_exists"]
        assert second["before_observed_at"] and second["after"]["choice"] == 1
        assert second["attempt_id"] != first["attempt_id"]
        assert second["source_revision"] > first["source_revision"]
        await call("PUT", "/me/about", {**body, "expected_member": "peer"}, status=409)
        await call("PUT", "/me/about", {"answers": {qid: {**selection(question), "question": "Earlier published wording"}}}, status=409)
        assert len(await observations(db, domain="everyday")) == 2
        quick = {"qid": skipped["id"], "question": skipped["q"]}
        await call("POST", "/me/quick-answer", quick)
        await call("POST", "/me/quick-answer", quick)
        entries = await db.answer_history.find({"question_id": "e:" + skipped["id"]}).to_list(None)
        assert len(entries) == 1 and entries[0]["kind"] == "answer_skipped"
        assert entries[0]["after"]["state"] == "skipped" and entries[0]["after"]["edition"]["question"]
        await call("POST", "/me/quick-answer", {"qid": skipped["id"], **selection(skipped)})
        entries = await db.answer_history.find({"question_id": "e:" + skipped["id"]}).sort("at", 1).to_list(None)
        assert len(entries) == 2 and entries[-1]["before"]["state"] == "skipped"
        assert entries[-1]["after"]["state"] == "answered" and not entries[-1]["after"]["quick_skip"]
        await call("POST", "/me/quick-answer", {"qid": skipped["id"], "choice": 999}, status=422)
        await call("POST", "/me/quick-answer", {"qid": "not-published"}, status=404)
        assert await db.answer_history.count_documents({"domain": "everyday"}) == 4

    run_case(monkeypatch, scenario)


def test_all_108_answers_and_demographic_change_fit_one_atomic_source_journal(monkeypatch):
    async def scenario(db, call, quiz, server):
        questions = [question for section in ("short", "extended", "further") for question in quiz[section]]
        assert len(questions) == 108
        body = {"answers": {question["id"]: selection(question) for question in questions},
                "demographics_patch": {"age": 31, "height_cm": 181}}
        original = answer_history.flush

        async def unavailable(database, source, query, **kwargs):
            if source == "users":
                return {"archived": 0, "redacted": 0, "failed": True}
            return await original(database, source, query, **kwargs)

        monkeypatch.setattr(answer_history, "flush", unavailable)
        await call("PUT", "/me/about", body)
        row = await db.users.find_one({"user_id": "owner"})
        assert len(row["about"]["answers"]) == 108
        assert len(row[answer_history.PENDING]) == 110  # existing-state baseline + current demographics + answers
        assert len({event["source_revision"] for event in row[answer_history.PENDING]}) == 1
        assert await db.answer_history.count_documents({}) == 0
        monkeypatch.setattr(answer_history, "flush", original)
        await call("PUT", "/me/about", body)
        answers = await observations(db, domain="everyday")
        demos = await observations(db, domain="demographics")
        assert len(answers) == 108 and len(demos) == 2
        assert sorted((event["baseline"], event["after"]["age"]) for event in demos) == [(False, 31), (True, 30)]
        current_id = next(event["id"] for event in demos if event["after"]["age"] == 31)
        assert {answer["demographics"]["snapshot_id"] for answer in answers} == {current_id}
        assert len({answer["attempt_id"] for answer in answers}) == 1
        assert all(set(answer["demographics"]) == {"snapshot_id", "observed_at"} for answer in answers)
        row = await db.users.find_one({"user_id": "owner"})
        assert not row.get(answer_history.PENDING)

    run_case(monkeypatch, scenario)


def test_competing_answer_changes_preserve_a_chain_then_explicit_removal_erases_payload(monkeypatch):
    async def scenario(db, call, quiz, server):
        question = quiz["short"][0]
        qid = question["id"]
        await call("PUT", "/me/about", {"answers": {qid: selection(question, note="Initial")},
                                      "describe": "PRIVATE DESCRIPTION", "mbti": "INTJ"})
        await asyncio.gather(*[call("PUT", "/me/about", {"answers": {qid: selection(question, choice, note)}})
            for choice, note in ((1, "Concurrent A"), (2, "Concurrent B"))])
        changed = [row for row in await observations(db, domain="everyday") if row["question_id"] == "e:" + qid]
        assert len(changed) == 3
        by_revision = sorted(changed, key=lambda row: row["source_revision"])
        assert by_revision[1]["before"] == by_revision[0]["after"]
        assert by_revision[2]["before"] == by_revision[1]["after"]
        assert {row["after"]["note"] for row in by_revision[1:]} == {"Concurrent A", "Concurrent B"}
        before = await observations(db)
        erased = [row for row in before if row["question_id"] in {"e:" + qid, "p:describe", "p:mbti"}]
        await call("PUT", "/me/about", {"answers": {qid: None}, "describe": "", "mbti": ""})
        for row in erased:
            tombstone = await db.answer_history.find_one({"_id": row["id"]})
            assert tombstone["purged"] and "after" not in tombstone
        markers = [row for row in await observations(db) if row["question_id"] in {"e:" + qid, "p:describe", "p:mbti"}]
        assert len(markers) == 3
        assert all(row["after"] == {"state": "removed"} and row["before"] is None and not row["before_exists"] for row in markers)
        payload = await call("GET", "/me/export", raw=True)
        assert "PRIVATE DESCRIPTION" not in payload.text
        assert "Concurrent A" not in payload.text and "Concurrent B" not in payload.text
        await call("PUT", "/me/about", {"answers": {qid: None}, "describe": "", "mbti": ""})
        # Removing again does not fabricate another completed answer or retry observation.
        assert all(row["kind"] == "answer_removed" for row in await observations(db, domain="everyday"))

    run_case(monkeypatch, scenario)


def test_demographic_withdrawal_tombstones_old_pii_but_keeps_answer_history_pointers(monkeypatch):
    async def scenario(db, call, quiz, server):
        question = quiz["short"][0]
        await call("PUT", "/me/about", {"answers": {question["id"]: selection(question)}})
        await call("PUT", "/me/about", {"demographics_patch": {"age": 31, "ethnicity": "White"}})
        await call("PUT", "/me/about", {"answers": {question["id"]: selection(question)}}, uid="peer")
        old = await observations(db, domain="demographics")
        retained = (await observations(db, domain="everyday"))[0]
        peer = await observations(db, uid="peer")
        await call("PUT", "/me/about", {"demographics_patch": {"ethnicity": "Prefer not to say", "height_cm": None}})
        for event in old:
            tombstone = await db.answer_history.find_one({"_id": event["id"]})
            assert tombstone["purged"] is True
            assert set(tombstone) == {"_id", "id", "purged", "purged_at"}
        current = await observations(db, domain="demographics")
        assert len(current) == 1 and current[0]["after"]["ethnicity"] == "Prefer not to say"
        assert "height_cm" not in current[0]["after"]
        answer = await db.answer_history.find_one({"_id": retained["id"]})
        assert answer["demographics"] == retained["demographics"] and not answer.get("purged")
        assert await observations(db, uid="peer") == peer
        await call("PUT", "/me/about", {"demographics_patch": {"age": None}})
        assert (await observations(db, domain="demographics"))[0]["after"].get("age") is None
        assert (await db.users.find_one({"user_id": "owner"}))["about"]["demographics"]["ethnicity"] == "Prefer not to say"

    run_case(monkeypatch, scenario)


def test_owner_history_auth_pagination_and_export_exclude_peer_and_journal_fields(monkeypatch):
    async def scenario(db, call, quiz, server):
        for uid in ("owner", "peer"):
            for question in quiz["short"][:3]:
                await call("PUT", "/me/about", {"answers": {question["id"]: selection(question, note=uid + " private")}}, uid=uid)
        peer_history = await observations(db, uid="peer")
        forged = answer_history.event(source="users", source_id="owner", owner_id="owner", actor_id="peer",
            domain="profile", question_id="fixture-peer-actor", after={"value": "PEER ACTOR SECRET"})
        await db.answer_history.insert_one({"_id": forged["id"], **forged})
        await db.people.insert_many([{"id": "own-contact", "user_id": "owner", "nickname": "Own contact",
            answer_history.SOURCE: {"source": "people", "source_id": "own-contact"}, answer_history.REVISION: 1,
            answer_history.GENERATION: 0, answer_context.SNAPSHOT: {"hidden": "INTERNAL SNAPSHOT"}},
            {"id": "peer-contact", "user_id": "peer", "nickname": "PEER CONTACT SECRET"}])
        await call("GET", "/me/answer-history", uid=None, status=401)
        await call("GET", "/admin/database-migration", status=403)
        await call("POST", "/admin/database-migration/repair", {"limit": 50}, status=403)
        await call("PUT", "/me/about", {"describe": "Unpaid"}, uid="unpaid", status=402)
        assert (await call("GET", "/me/answer-history", uid="unpaid"))["items"] == []
        ids, cursor = [], ""
        while True:
            page = await call("GET", "/me/answer-history?limit=2&user_id=peer" + ("&before=" + cursor if cursor else ""), raw=True)
            assert page.headers["cache-control"] == "private, no-store"
            data = page.json()
            assert all(row["owner_id"] == row["actor_id"] == "owner" for row in data["items"])
            assert all(not {"_id", "source_id", "generation"}.intersection(row) for row in data["items"])
            ids.extend(row["id"] for row in data["items"])
            cursor = data["next_cursor"]
            if not cursor:
                break
        expected = [row["id"] for row in await observations(db) if row["actor_id"] == "owner"]
        assert set(ids) == set(expected) and len(ids) == len(set(ids))
        await call("GET", "/me/answer-history?before=" + peer_history[0]["id"], status=409)
        await call("GET", "/me/answer-history?limit=0", status=422)
        await call("GET", "/me/answer-history?limit=201", status=422)
        exported = await call("GET", "/me/export", raw=True)
        text = exported.text
        assert exported.headers["cache-control"] == "no-store"
        assert all(secret not in text for secret in ("PEER ACTOR SECRET", "PEER CONTACT SECRET", "peer private", "INTERNAL SNAPSHOT"))
        payload = exported.json()

        def clean(value):
            if isinstance(value, dict):
                assert not set(history_lifecycle.PRIVATE_FIELDS).intersection(value)
                for child in value.values():
                    clean(child)
            elif isinstance(value, list):
                for child in value:
                    clean(child)

        clean(payload)
        assert {row["id"] for row in payload["answer_history"]} == set(ids)
        assert await observations(db, uid="peer") == peer_history

    run_case(monkeypatch, scenario)


def test_admin_status_is_count_only_and_bounded_recovery_archives_accepted_event(monkeypatch):
    async def scenario(db, call, quiz, server):
        event = answer_history.event(source="users", source_id="owner", owner_id="owner", domain="everyday",
            question_id="e:fixture", after={"note": "CONFIDENTIAL ANSWER"}, consent={"env": server.ENV})
        await journal(db, "owner", event)
        from backend import postgres_migration
        await db.database_migration_reports.insert_one({"environment": server.ENV,
            "report_version": postgres_migration.REPORT_VERSION, "status": "observed_parity_only", "completed_at": answer_history.now(),
            "dsn": "CONFIDENTIAL CREDENTIAL", "raw_answers": ["CONFIDENTIAL ANSWER"], "owner_id": "CONFIDENTIAL OWNER",
            "domains": {"individual": {"source_records": 4, "target_records": 4, "parity": True}},
            "cutover_eligible": True})
        monkeypatch.setenv("IMLADRI_POSTGRES_SHADOW_DSN", "postgresql://fixture:CONFIDENTIAL@unreachable.invalid/db")
        status = await call("GET", "/admin/database-migration", uid="admin", raw=True)
        state = status.json()
        assert "no-store" in status.headers["cache-control"]
        postgres_primary = getattr(type(db), "backend", None) == "postgresql"
        assert state["mode"] == ("postgres_authoritative" if postgres_primary else "mongo_authoritative")
        assert state["postgres"] == {"configured": True, "connected": postgres_primary,
            "connection_verification": "current_repository_queries" if postgres_primary else "not_checked_in_request",
            "cutover_eligible": False}
        assert state["history"]["pending_events"] == 1
        assert set(state["domains"]) == {"individual", "couples", "billing", "admin", "research"}
        assert state["domains"]["individual"]["parity"] is True
        assert "CONFIDENTIAL" not in status.text
        for invalid in ({"limit": 0}, {"limit": 101}, {"limit": True}, {"limit": "50"}, {"limit": 50, "dsn": "forbidden"}):
            await call("POST", "/admin/database-migration/repair", invalid, uid="admin", status=422)
        repaired = await call("POST", "/admin/database-migration/repair", {"limit": 50}, uid="admin")
        assert repaired["attempted"] == 1 and repaired["archived"] == 1 and repaired["failed"] == 0
        assert "CONFIDENTIAL" not in json.dumps(repaired)
        assert (await db.answer_history.find_one({"_id": event["id"]}))["after"]["note"] == "CONFIDENTIAL ANSWER"
        fresh = await call("GET", "/admin/database-migration", uid="admin")
        assert fresh["history"]["pending_events"] == 0 and fresh["latest_repair"]["archived"] == 1
        counts = next(row for row in fresh["history"]["domains"] if row["domain"] == "everyday")
        assert counts == {"domain": "everyday", "baseline": 0, "new_observations": 1, "total": 1}

    run_case(monkeypatch, scenario)


def test_minor_update_erases_repeat_and_wording_scopes_without_losing_ordinary_answers(monkeypatch):
    async def scenario(db, call, quiz, server):
        question = quiz["short"][0]
        await call("PUT", "/me/about", {"answers": {question["id"]: selection(question)}})
        ordinary = (await observations(db, domain="everyday"))[0]
        scoped = [answer_history.event(source="users", source_id="owner", owner_id="owner", domain=domain,
                  question_id=qid, after={"value": "WITHDRAWN ADULT FEEDBACK"}) for domain, qid in (
                  ("assessment_repeat", "assessment-repeat:fixture:attempt"), ("wording_feedback", "wording:fixture:rating"))]
        await journal(db, "owner", scoped, assessment_repeat={"fixture": True}, intimacy_wording_review={"fixture": True},
                      questionnaire_research_version="all-answers-v1", tone_research_version="conversation-tone-v1")
        await call("PUT", "/me/about", {"demographics_patch": {"age": 17}})
        for event in scoped:
            stored = await db.answer_history.find_one({"_id": event["id"]})
            assert stored["purged"] and "after" not in stored
        row = await db.users.find_one({"user_id": "owner"})
        assert all(key not in row for key in ("assessment_repeat", "intimacy_wording_review", "questionnaire_research_version",
                                              "tone_research_version", "couples_adult_at"))
        assert row["couples_preferences"]["analytics_opt_in"] is False
        assert not (await db.answer_history.find_one({"_id": ordinary["id"]})).get("purged")
        assert "WITHDRAWN ADULT FEEDBACK" not in json.dumps(await call("GET", "/me/export"))

    run_case(monkeypatch, scenario)


@pytest.mark.parametrize("with_stated_preferences", [False, True])
def test_content_clear_tombstones_fence_an_archive_already_in_flight(monkeypatch, with_stated_preferences):
    async def scenario(db, call, quiz, server):
        question = quiz["short"][0]
        if with_stated_preferences:
            ranges = {"age_min": 25, "age_max": 45, "height_min_cm": 150, "height_max_cm": 200}
            await call("PUT", "/your-type/preferences", {"version": "your-type-stated-preferences-v1",
                "revision": 0, "expected_member": "owner", "by_audience": {"men": ranges, "women": ranges}})
        await call("PUT", "/me/about", {"answers": {question["id"]: selection(question, note="PEER REMAINS")}}, uid="peer")
        peer = await observations(db, uid="peer")
        original_flush = answer_history.flush

        async def unavailable(database, source, query, **kwargs):
            if source == "users" and query.get("user_id") == "owner":
                return {"archived": 0, "redacted": 0, "failed": True}
            return await original_flush(database, source, query, **kwargs)

        monkeypatch.setattr(answer_history, "flush", unavailable)
        await call("PUT", "/me/about", {"answers": {question["id"]: selection(question, note="ERASE ME COMPLETELY")}})
        owner = await db.users.find_one({"user_id": "owner"})
        pending_ids = [event["id"] for event in owner[answer_history.PENDING]]
        assert len(pending_ids) == 2
        monkeypatch.setattr(answer_history, "flush", original_flush)
        collection_type = type(db.answer_history)
        original_update = collection_type.update_one
        reached, release = asyncio.Event(), asyncio.Event()

        async def delayed(collection, query, update, *args, **kwargs):
            value = update.get("$setOnInsert", {})
            if collection.name == "answer_history" and value.get("owner_id") == "owner":
                reached.set()
                await release.wait()
            return await original_update(collection, query, update, *args, **kwargs)

        monkeypatch.setattr(collection_type, "update_one", delayed)
        late = asyncio.create_task(answer_history.flush(db, "users", {"user_id": "owner"}))
        try:
            await asyncio.wait_for(reached.wait(), 5)
            await asyncio.wait_for(call("DELETE", "/me"), 10)
            for eid in pending_ids:
                row = await db.answer_history.find_one({"_id": eid})
                assert row["purged"] and set(row) == {"_id", "id", "purged", "purged_at"}
        finally:
            release.set()
            await asyncio.wait_for(late, 5)
        assert not await observations(db)
        assert (await db.users.find_one({"user_id": "owner"}))["about"] == {}
        assert (await call("GET", "/me/answer-history"))["items"] == []
        exported = await call("GET", "/me/export", raw=True)
        assert "ERASE ME COMPLETELY" not in exported.text and "PEER REMAINS" not in exported.text
        assert await observations(db, uid="peer") == peer
        for eid in pending_ids:
            assert (await db.answer_history.find_one({"_id": eid}))["purged"] is True

    run_case(monkeypatch, scenario)


def test_real_http_individual_and_couples_roundtrip_to_postgres_without_cutover(monkeypatch):
    dsn = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Requires the optional disposable loopback PostgreSQL fixture and driver.")
    assert urlsplit(dsn).hostname in {"localhost", "127.0.0.1", "::1"}
    pytest.importorskip("pg8000.dbapi")

    async def scenario(db, call, quiz, server):
        from bson import BSON
        from backend import postgres_migration as pg

        if getattr(type(db), "backend", None) == "postgresql":
            pytest.skip("This legacy shadow-copy test requires a MongoDB source; PG application parity is tested separately.")

        namespace = "http-fixture-" + uuid4().hex
        target = pg.PostgresShadow.connect(dsn, allow_local_plaintext=True)
        target.initialize()
        try:
            question = quiz["short"][0]
            for uid, choice in (("owner", 0), ("peer", 1)):
                await call("PATCH", "/me/questionnaire-research", {"version": "all-answers-v1", "enabled": True,
                    "adult_confirmed": True, "revision": 0, "expected_member": uid}, uid=uid)
                await call("PUT", "/me/about", {"answers": {question["id"]: selection(question, choice)}}, uid=uid)
            await db.couples_pairs.insert_one({"_id": "pair", "env": server.ENV, "state": "active",
                "inviter": "owner", "invitee": "peer", "members": ["owner", "peer"], "sponsor": "owner"})
            for uid in ("owner", "peer"):
                await call("PUT", "/couples/drafts/support-1", {"version": 0, "choice": "Listening",
                    "lines": ["Listening", uid + " private reflection"]}, uid=uid)
            # This is a synthetic retained source record, never a Stripe/API payment.
            await db.payment_transactions.insert_one({"_id": "billing-fixture", "user_id": "owner", "env": server.ENV,
                "kind": "purchase", "payment_status": "paid", "currency": "usd", "amount": 12,
                "subscription_revenue_cents": 1200})
            await db.settings.insert_one({"key": "fixture-admin", "value": "local only"})
            before = await db.users.find_one({"user_id": "owner"})
            report = await pg.reconcile(db, target, namespace=namespace, environment=server.ENV)
            assert report["observed_parity"] is True and report["source_write_operations"] == 0
            assert report["cutover_eligible"] is False and report["mongo_authoritative"] is True
            assert all(report["domains"][key]["parity"] for key in ("individual", "couples", "billing", "admin", "research"))
            assert await db.users.find_one({"user_id": "owner"}) == before
            typed = target.execute("SELECT h.owner_id,o.answer_code,d.age,d.height_cm,h.signature FROM imladri_shadow.history_events h "
                "JOIN imladri_shadow.answer_observations o USING(namespace,event_id) "
                "JOIN imladri_shadow.demographic_snapshots d ON d.namespace=h.namespace AND d.event_id=h.demographic_snapshot_id "
                "WHERE h.namespace=%s AND h.domain='everyday' ORDER BY h.owner_id", (namespace,)).fetchall()
            assert len(typed) == 2
            assert [(row[0], row[1], row[2], row[3]) for row in typed] == [("owner", 0, 30, 175), ("peer", 1, 30, 175)]
            assert all(row[4] for row in typed)
            assert target.execute("SELECT count(*) FROM imladri_shadow.research_observations WHERE namespace=%s AND domain='everyday'",
                                  (namespace,)).fetchone()[0] == 2
            raw = target.execute("SELECT raw_bson FROM imladri_shadow.documents WHERE namespace=%s AND collection='users' AND id_key=%s",
                                 (namespace, pg.document_key(before))).fetchone()[0]
            assert BSON(bytes(raw)).decode() == before
            assert target.execute("SELECT amount_cents FROM imladri_shadow.billing_records WHERE namespace=%s", (namespace,)).fetchone()[0] == 1200
            couples = target.execute("SELECT owner_id,pair_id,payload->'after'->>'choice',payload->'after'->>'pair_side',o.answer_code,payload->'question'->'choices' "
                                    "FROM imladri_shadow.history_events JOIN imladri_shadow.answer_observations o USING(namespace,event_id) "
                                    "WHERE namespace=%s AND domain='couples' ORDER BY owner_id",
                                    (namespace,)).fetchall()
            assert [tuple(row[:4]) for row in couples] == [("owner", "pair", "Listening", "a"), ("peer", "pair", "Listening", "b")]
            assert all(row[4] == row[5].index("Listening") for row in couples)
            await db.database_migration_reports.insert_one(report)
            state = await call("GET", "/admin/database-migration", uid="admin")
            assert state["latest_report"]["observed_parity"] is True
            assert state["postgres"]["connected"] is False and state["postgres"]["cutover_eligible"] is False
            await call("PATCH", "/me/questionnaire-research", {"version": "all-answers-v1", "enabled": False,
                "revision": 1, "expected_member": "owner"})
            await pg.reconcile(db, target, namespace=namespace, environment=server.ENV)
            rows = target.execute("SELECT DISTINCT owner_id FROM imladri_shadow.research_observations WHERE namespace=%s", (namespace,)).fetchall()
            assert [row[0] for row in rows] == ["peer"]
            await call("DELETE", "/me")
            await pg.reconcile(db, target, namespace=namespace, environment=server.ENV)
            assert target.execute("SELECT count(*) FROM imladri_shadow.history_events WHERE namespace=%s AND owner_id='owner'",
                                  (namespace,)).fetchone()[0] == 0
            assert target.execute("SELECT count(*) FROM imladri_shadow.history_tombstones WHERE namespace=%s", (namespace,)).fetchone()[0] > 0
            assert await db.payment_transactions.count_documents({"_id": "billing-fixture"}) == 1
            assert (await call("GET", "/me/answer-history"))["items"] == []
        finally:
            target.connection.rollback()
            for table in ("answer_observations", "demographic_snapshots", "history_events", "couples", "participants", "billing_records",
                          "history_tombstones", "checkpoints", "documents", "runs"):
                target.execute("DELETE FROM imladri_shadow." + table + " WHERE namespace=%s", (namespace,))
            target.connection.commit()
            target.close()

    run_case(monkeypatch, scenario)
