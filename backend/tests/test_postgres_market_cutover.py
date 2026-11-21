"""Fixed Market conversion scope; actual local native PostgreSQL and MongoDB."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlsplit
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from backend.postgres_copy_registry import MARKET_COLLECTIONS, PERSONAL_COLLECTIONS, application_collections
from backend.postgres_cutover import FrozenCopyBlocked, copy_frozen
from backend.postgres_store import PostgresDatabase


DSN = os.environ.get("IMLADRI_TEST_POSTGRES_DSN")
MONGO = os.environ.get("IMLADRI_TEST_MONGO_URL")
integration = pytest.mark.skipif(not DSN or not MONGO, reason="Disposable loopback Mongo/PostgreSQL required")


def test_copy_scopes_are_fixed_and_personal_does_not_include_market():
    assert application_collections("personal") is PERSONAL_COLLECTIONS
    assert application_collections("market") is MARKET_COLLECTIONS
    assert "listings" not in PERSONAL_COLLECTIONS
    assert {"listings", "jobs", "users", "worker_heartbeats", "database_migration_reports"} <= MARKET_COLLECTIONS
    assert len(MARKET_COLLECTIONS) == 30
    with pytest.raises(ValueError): application_collections(["users", "arbitrary"])
    with pytest.raises(ValueError): application_collections("arbitrary")


def fence(source, target):
    identity = lambda db: {"backend": getattr(type(db), "backend", "mongodb"), "namespace": db.name}
    return {"source": identity(source), "target": identity(target), "source_writers_stopped": True,
        "target_writers_stopped": True, "source_background_writers_stopped": True,
        "target_background_writers_stopped": True, "evidence_reference": "fixture-market-explicit-fence",
        "valid_until": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}


@asynccontextmanager
async def stores():
    assert urlsplit(DSN).hostname == urlsplit(MONGO).hostname == "127.0.0.1"
    client = AsyncIOMotorClient(MONGO, serverSelectionTimeoutMS=3000)
    source = client["imladri_market_copy_" + uuid4().hex]
    target = PostgresDatabase(DSN, "fixture_market_copy_" + uuid4().hex, allow_local_plaintext=True)
    try:
        yield source, target
    finally:
        await client.drop_database(source.name); client.close()
        def cleanup(cursor, now):
            cursor.execute("DELETE FROM imladri_app.collections WHERE namespace=%s", (target.name,))
        await target._run(cleanup, write="__fixture_cleanup")
        await target.aclose()


@integration
def test_explicit_market_copies_all_raw_data_indexes_and_reverses_current_state():
    async def run():
        async with stores() as (source, target):
            await source.users.insert_one({"_id": "member", "user_id": "fixture-user"})
            await source.listings.insert_one({"_id": "listing", "seller_id": "fixture-user", "lat": 41.0, "lng": -87.0})
            await source.jobs.insert_one({"_id": "job", "status": "running", "lease_token": "fixture-token"})
            await source.jobs.create_index([("status", 1), ("lease_until", 1)], name="job_claim")
            await source.database_migration_reports.insert_one({"_id": "barrier", "kind": "fixture_metadata"})
            with pytest.raises(FrozenCopyBlocked, match="unclassified"):
                await copy_frozen(source, target, freeze=fence(source, target))
            assert await target.users.count_documents({}) == 0
            receipt = await copy_frozen(source, target, freeze=fence(source, target), application="market")
            assert receipt["application"] == "market" and receipt["exact_bson_and_identity_parity"]
            assert receipt["source_before"] == receipt["target_after"]
            assert (await target.jobs.find_one({"_id": "job"}))["lease_token"] == "fixture-token"
            await target.jobs.update_one({"_id": "job"}, {"$set": {"status": "completed"}, "$unset": {"lease_token": ""}})
            await target.listings.delete_one({"_id": "listing"})
            reverse = await copy_frozen(target, source, freeze=fence(target, source), application="market", expected_target=receipt)
            assert reverse["application"] == "market" and reverse["exact_bson_and_identity_parity"]
            assert (await source.jobs.find_one({"_id": "job"}))["status"] == "completed"
            assert await source.listings.count_documents({}) == 0
    asyncio.run(run())


@integration
@pytest.mark.parametrize("change", ["unknown_collection", "nondefault_options", "unknown_application"])
def test_market_unknown_scope_and_unrepresented_semantics_block_before_writes(change):
    async def run():
        async with stores() as (source, target):
            if change == "nondefault_options":
                await source.create_collection("users", validator={"$jsonSchema": {"bsonType": "object"}})
            await source.users.insert_one({"_id": "fixture"})
            if change == "unknown_collection": await source.unreviewed_collection.insert_one({"_id": "fixture"})
            expected = "options" if change == "nondefault_options" else "scope" if change == "unknown_application" else "unclassified"
            with pytest.raises(FrozenCopyBlocked, match=expected):
                await copy_frozen(source, target, freeze=fence(source, target),
                                  application="unreviewed" if change == "unknown_application" else "market")
            assert await target.users.count_documents({}) == 0
    asyncio.run(run())
