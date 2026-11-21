import asyncio
import json
import os
from uuid import uuid4

from fastapi import HTTPException
import pytest

from backend import answer_history, database_migration_admin as admin


URI = os.getenv("IMLADRI_TEST_MONGO_URL")
integration = pytest.mark.skipif(not URI, reason="Requires disposable loopback MongoDB.")


async def database(exercise):
    from motor.motor_asyncio import AsyncIOMotorClient
    from scripts.migrate_postgres import loopback_mongo
    assert loopback_mongo(URI)
    client = AsyncIOMotorClient(URI, serverSelectionTimeoutMS=5000)
    db = client["imladri_migration_admin_" + uuid4().hex]
    try:
        await exercise(db)
    finally:
        await client.drop_database(db.name)
        client.close()


@integration
def test_admin_status_is_count_only_scoped_and_never_claims_a_postgres_connection(monkeypatch):
    async def exercise(db):
        await db.users.insert_many([{"user_id": "one", "env": "fixture"}, {"user_id": "foreign", "env": "other"}])
        await db.answer_history.insert_many([
            {"_id": "one", "domain": "intimacy", "baseline": True, "consent": {"env": "fixture"}, "after": {"private": "answer-secret"}},
            {"_id": "two", "domain": "intimacy", "consent": {"env": "fixture"}},
            {"_id": "foreign", "domain": "intimacy", "consent": {"env": "other"}},
            {"_id": "erased", "purged": True},
        ])
        await db.intimacy_answers.insert_many([
            {"_id": "one", "user_id": "one", "answer_history_pending": [{"after": "answer-secret"}]},
            {"_id": "foreign", "user_id": "foreign", "answer_history_pending": [{}, {}]},
        ])
        await db.database_migration_reports.insert_one({"_id": "report", "report_version": 1, "environment": "fixture",
            "namespace": "fixture", "status": "observed_parity_only", "completed_at": "2026-11-10T23:00:00+00:00",
            "cutover_eligible": True, "mongo_authoritative": False, "dsn": "dsn-secret", "raw_answers": ["answer-secret"],
            "domains": {"individual": {"source_records": 2, "target_records": 2, "parity": True}}})
        monkeypatch.delenv("IMLADRI_POSTGRES_SHADOW_DSN", raising=False)
        result = await admin.status(db, "fixture")
        postgres_primary = getattr(type(db), "backend", None) == "postgresql"
        assert result["postgres"]["configured"] is postgres_primary and result["postgres"]["connected"] is postgres_primary
        assert result["mode"] == ("postgres_authoritative" if postgres_primary else "mongo_authoritative")
        assert result["latest_report"]["cutover_eligible"] is False and result["latest_report"]["mongo_authoritative"] is True
        assert result["history"]["pending_events"] == 1
        assert result["history"]["domains"] == [{"domain": "intimacy", "baseline": 1, "new_observations": 1, "total": 2}]
        assert result["history"]["database_tombstones"] == 1
        encoded = json.dumps(result)
        assert not any(secret in encoded for secret in ("dsn-secret", "answer-secret", "raw_answers", '"user_id"'))
        monkeypatch.setenv("IMLADRI_POSTGRES_SHADOW_DSN", "postgresql://hidden-password@invalid/db")
        result = await admin.status(db, "fixture")
        assert result["postgres"]["configured"] is True and result["postgres"]["connected"] is postgres_primary
        assert "hidden-password" not in json.dumps(result)
    asyncio.run(database(exercise))


@integration
def test_bounded_repair_archives_real_journals_without_paid_or_postgres_operations():
    async def exercise(db):
        for uid in ("one", "two"):
            await db.users.insert_one({"user_id": uid, "env": "fixture"})
            value = answer_history.event(source="intimacy_answers", source_id=uid, owner_id=uid, domain="intimacy",
                                         question_id="i:x:0", after={"choice": 0})
            await db.intimacy_answers.insert_one({"_id": uid, "user_id": uid, answer_history.SOURCE: {
                "source": "intimacy_answers", "source_id": uid}, answer_history.PENDING: [value]})
        first = await admin.repair_history(db, "fixture", limit=1)
        assert first["attempted"] == 1 and first["archived"] == 1 and first["failed"] == 0
        assert await db.answer_history.count_documents({"purged": {"$ne": True}}) == 1
        second = await admin.repair_history(db, "fixture", limit=1)
        assert second["attempted"] == 1 and second["archived"] == 1
        assert (await admin.status(db, "fixture"))["history"]["pending_events"] == 0
        with pytest.raises(HTTPException) as error:
            await admin.repair_history(db, "fixture", limit=101)
        assert error.value.status_code == 422
    asyncio.run(database(exercise))


@integration
def test_repair_stops_on_failure_and_never_returns_raw_database_errors(monkeypatch):
    async def exercise(db):
        await db.users.insert_one({"user_id": "one", "env": "fixture", answer_history.PENDING: [{"private": "secret"}]})
        calls = []
        async def failure(*args, **kwargs):
            calls.append(1)
            return {"failed": True, "error_type": "fixture-secret-error"}
        monkeypatch.setattr(answer_history, "flush", failure)
        with pytest.raises(HTTPException) as error:
            await admin.repair_history(db, "fixture")
        assert error.value.status_code == 503 and len(calls) == 1
        assert "fixture-secret-error" not in json.dumps(error.value.detail)
        assert (await admin.status(db, "fixture"))["latest_repair"]["failed"] == 1
    asyncio.run(database(exercise))
