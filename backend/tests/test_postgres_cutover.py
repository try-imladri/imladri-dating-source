"""Frozen copy/rollback proofs use disposable loopback MongoDB and PostgreSQL."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlsplit
from uuid import uuid4

from bson import BSON, Binary, Decimal128, Int64, ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import IndexModel
import pytest

from backend.postgres_cutover import CopyLimits, FrozenCopyBlocked, copy_frozen
from backend.postgres_store import PostgresDatabase


DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
MONGO = os.getenv("IMLADRI_DOCUMENT_TEST_MONGO_URL") or os.getenv("IMLADRI_TEST_MONGO_URL")
integration = pytest.mark.skipif(not DSN or not MONGO, reason="Requires disposable loopback MongoDB and PostgreSQL.")


def frozen(source, target):
    identity = lambda db: {"backend": getattr(type(db), "backend", "mongodb"), "namespace": db.name}
    return {"source": identity(source), "target": identity(target), "source_writers_stopped": True,
        "target_writers_stopped": True, "source_background_writers_stopped": True,
        "target_background_writers_stopped": True, "evidence_reference": "local-fixture-explicit-freeze",
        "valid_until": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()}


@asynccontextmanager
async def repositories():
    assert urlsplit(DSN).hostname in {"127.0.0.1", "localhost", "::1"}
    assert urlsplit(MONGO).hostname in {"127.0.0.1", "localhost", "::1"}
    client = AsyncIOMotorClient(MONGO, serverSelectionTimeoutMS=3000)
    source = client["imladri_frozen_copy_" + uuid4().hex]
    target = PostgresDatabase(DSN, "fixture_frozen_copy_" + uuid4().hex, allow_local_plaintext=True)
    try:
        yield source, target
    finally:
        await client.drop_database(source.name)
        client.close()
        def cleanup(cursor, now):
            for table in ("unique_keys", "indexes", "documents", "collections", "rpc_requests"):
                cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (target.name,))
        await target._run(cleanup, write="__fixture_cleanup")
        await target.aclose()


async def seed(source):
    rows = {
        "users": [{"_id": ObjectId(), "user_id": "fixture-owner", "credits": Int64(3),
            "binary": Binary(b"normal fixture", subtype=128), "decimal": Decimal128("1.25"),
            "date": datetime(2026, 11, 10, 12, tzinfo=timezone.utc), "text": "preserve\x00exactly"}],
        "people": [{"_id": "person", "user_id": "fixture-owner", "id": "person"}],
        "couples_pairs": [{"_id": "pair", "env": "live", "members": ["fixture-owner", "fixture-peer"]}],
        "payment_transactions": [{"_id": "billing", "user_id": "fixture-owner", "amount": 12}],
        "answer_history": [{"_id": "history", "id": "history", "after": {"choice": "old answer"}}],
        "field_study_links": [{"_id": "retired-link", "historical_only": True}],
        "volunteer_studies": [{"_id": "retired-record", "historical_only": True}],
        "database_migration_reports": [{"_id": "control", "status": "fixture-only"}],
    }
    for name, records in rows.items():
        await source[name].insert_many(records)
    await source.create_collection("media")
    await source.users.create_indexes([IndexModel("user_id", unique=True, name="user_id")])
    await source.couples_pairs.create_indexes([IndexModel([("env", 1), ("members", 1)], name="members", unique=True,
        partialFilterExpression={"members": {"$exists": True}})])
    await source.prompt_candidates.create_indexes([IndexModel("expires_at", expireAfterSeconds=0, name="expiry")])
    return rows


@integration
def test_full_frozen_copy_preserves_all_domains_controls_retired_records_empty_collections_and_indexes():
    async def run():
        async with repositories() as (source, target):
            await seed(source)
            receipt = await copy_frozen(source, target, freeze=frozen(source, target))
            assert receipt["status"] == "completed_frozen_copy"
            assert receipt["source_before"] == receipt["target_after"]
            assert receipt["routing_changed"] is False and receipt["cutover_performed"] is False
            assert set(await source.list_collection_names()) == set(await target.list_collection_names())
            for name in await source.list_collection_names():
                for original in await source[name].find({}).to_list(None):
                    assert BSON.encode(await target[name].find_one({"_id": original["_id"]})) == BSON.encode(original)
            assert await target.media.count_documents({}) == 0
            assert (await target.prompt_candidates.index_information())["expiry"]["expireAfterSeconds"] == 0
            assert "old answer" not in str(receipt) and "fixture-owner" not in str(receipt)
            again = await copy_frozen(source, target, freeze=frozen(source, target), expected_target=receipt)
            assert again["target_after"] == receipt["target_after"]
    asyncio.run(run())


@integration
def test_reverse_after_postgres_writes_uses_current_frozen_state_and_reconciles_deletes_and_tombstones():
    async def run():
        async with repositories() as (source, target):
            await seed(source)
            forward = await copy_frozen(source, target, freeze=frozen(source, target))
            await target.users.update_one({"user_id": "fixture-owner"}, {"$inc": {"credits": -1}, "$set": {"new_answer": 3}})
            await target.people.delete_one({"_id": "person"})
            tombstone = {"_id": "history", "id": "history", "purged": True, "purged_at": "fixture"}
            await target.answer_history.replace_one({"_id": "history"}, tombstone)
            await target.exchanges.insert_one({"_id": "new-message", "id": "new-message", "user_id": "fixture-owner"})
            reversed_copy = await copy_frozen(target, source, freeze=frozen(target, source), expected_target=forward)
            assert reversed_copy["source_before"] == reversed_copy["target_after"]
            assert (await source.users.find_one({"user_id": "fixture-owner"}))["credits"] == 2
            assert await source.people.count_documents({}) == 0
            assert await source.answer_history.find_one({"_id": "history"}) == tombstone
            assert await source.exchanges.find_one({"_id": "new-message"}) is not None
    asyncio.run(run())


@integration
def test_prior_copy_receipt_never_overwrites_subsequent_postgres_app_writes():
    async def run():
        async with repositories() as (source, target):
            await seed(source)
            receipt = await copy_frozen(source, target, freeze=frozen(source, target))
            await target.users.update_one({"user_id": "fixture-owner"}, {"$set": {"after_launch": True}})
            with pytest.raises(FrozenCopyBlocked, match="no longer matches"):
                await copy_frozen(source, target, freeze=frozen(source, target), expected_target=receipt)
            assert (await target.users.find_one({"user_id": "fixture-owner"}))["after_launch"] is True
    asyncio.run(run())


@integration
@pytest.mark.parametrize("change", ["missing", "resurrected", "purged_with_payload", "aliased_id"])
def test_existing_irreversible_tombstone_cannot_be_deleted_or_resurrected(change):
    async def run():
        async with repositories() as (source, target):
            tombstone = {"_id": "erased", "id": "erased", "purged": True, "purged_at": "fixture"}
            await source.answer_history.insert_one(tombstone.copy())
            receipt = await copy_frozen(source, target, freeze=frozen(source, target))
            if change == "missing":
                await source.answer_history.delete_one({"_id": "erased"})
            elif change == "resurrected":
                await source.answer_history.replace_one({"_id": "erased"}, {"_id": "erased", "after": "old answer"})
            elif change == "purged_with_payload":
                await source.answer_history.replace_one({"_id": "erased"}, {**tombstone, "after": "old answer"})
            else:
                await source.answer_history.insert_one({"_id": "another-id", "id": "erased", "after": "old answer"})
            with pytest.raises(FrozenCopyBlocked, match="irreversible|opaque|identity"):
                await copy_frozen(source, target, freeze=frozen(source, target), expected_target=receipt)
            assert await target.answer_history.find_one({"_id": "erased"}) == tombstone
    asyncio.run(run())


@integration
def test_nonopaque_purged_source_record_is_rejected_even_on_first_copy():
    async def run():
        async with repositories() as (source, target):
            await source.answer_history.insert_one({"_id": "erased", "id": "erased", "purged": True,
                "owner_id": "fixture-owner", "demographics": {"old": "erased"}})
            with pytest.raises(FrozenCopyBlocked, match="non-opaque"):
                await copy_frozen(source, target, freeze=frozen(source, target))
            assert await target.answer_history.count_documents({}) == 0
    asyncio.run(run())


@integration
def test_explicit_source_and_background_freeze_is_required_before_any_copy():
    async def run():
        async with repositories() as (source, target):
            await source.users.insert_one({"_id": "one"})
            for key in ("source_writers_stopped", "target_writers_stopped", "source_background_writers_stopped",
                        "target_background_writers_stopped"):
                evidence = frozen(source, target)
                evidence[key] = False
                with pytest.raises(FrozenCopyBlocked, match="stopped"):
                    await copy_frozen(source, target, freeze=evidence)
            evidence = frozen(source, target)
            evidence["valid_until"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            with pytest.raises(FrozenCopyBlocked, match="expired"):
                await copy_frozen(source, target, freeze=evidence)
            assert await target.users.count_documents({}) == 0
    asyncio.run(run())


@integration
@pytest.mark.parametrize("change", ["update", "delete", "new_collection"])
def test_second_source_pass_detects_normal_concurrent_drift_before_any_target_writes(change):
    class Source:
        def __init__(self, real):
            self.real, self.name, self.calls = real, real.name, 0
        def __getitem__(self, name):
            return self.real[name]
        def get_collection(self, *args, **kwargs):
            return self.real.get_collection(*args, **kwargs)
        async def list_collection_names(self):
            self.calls += 1
            if self.calls == 2:
                if change == "update":
                    await self.real.users.update_one({"_id": "one"}, {"$set": {"answer": 2}})
                elif change == "delete":
                    await self.real.users.delete_one({"_id": "one"})
                else:
                    await self.real.settings.insert_one({"_id": "new"})
            return await self.real.list_collection_names()
    async def run():
        async with repositories() as (source, target):
            await source.users.insert_one({"_id": "one", "answer": 1})
            changing = Source(source)
            with pytest.raises(FrozenCopyBlocked, match="changed between"):
                await copy_frozen(changing, target, freeze=frozen(changing, target))
            assert await target.users.count_documents({}) == 0
    asyncio.run(run())


@integration
def test_unclassified_collection_and_record_byte_write_limits_fail_before_copy():
    async def run():
        async with repositories() as (source, target):
            await source.users.insert_many([{"_id": "one", "text": "normal fixture"}, {"_id": "two"}])
            for limits in (CopyLimits(max_records=1), CopyLimits(max_bytes=20), CopyLimits(max_document_bytes=20), CopyLimits(max_write_operations=1),
                           CopyLimits(max_total_scanned_bytes=100)):
                with pytest.raises(FrozenCopyBlocked, match="bound|limit"):
                    await copy_frozen(source, target, freeze=frozen(source, target), limits=limits)
                assert await target.users.count_documents({}) == 0
            await source.unclassified_fixture.insert_one({"_id": "never-ignore"})
            with pytest.raises(FrozenCopyBlocked, match="unclassified"):
                await copy_frozen(source, target, freeze=frozen(source, target))
            assert await target.users.count_documents({}) == 0
    asyncio.run(run())


@integration
def test_nondefault_collection_validator_is_not_silently_lost():
    async def run():
        async with repositories() as (source, target):
            await source.create_collection("users", validator={"$jsonSchema": {"bsonType": "object", "required": ["user_id"]}})
            await source.users.insert_one({"_id": "owner", "user_id": "fixture-owner"})
            with pytest.raises(FrozenCopyBlocked, match="Nondefault collection options"):
                await copy_frozen(source, target, freeze=frozen(source, target))
            assert await target.users.count_documents({}) == 0
    asyncio.run(run())


@integration
def test_partial_copy_failure_has_no_completion_receipt_and_is_not_automatically_replaced():
    class Target:
        backend = "postgresql"
        def __init__(self, real):
            self.real, self.name = real, real.name
        def __getattr__(self, name):
            return getattr(self.real, name)
        def __getitem__(self, name):
            collection = self.real[name]
            if name != "users":
                return collection
            class FailingCollection:
                def __getattr__(self, key):
                    return getattr(collection, key)
                async def replace_one(self, *args, **kwargs):
                    raise ConnectionError("fixture interruption")
            return FailingCollection()
    async def run():
        async with repositories() as (source, target):
            await source.settings.insert_one({"_id": "first"})
            await source.users.insert_one({"_id": "second"})
            interrupted = Target(target)
            with pytest.raises(FrozenCopyBlocked, match="may be partial"):
                await copy_frozen(source, interrupted, freeze=frozen(source, interrupted))
            assert await target.settings.count_documents({}) == 1
            with pytest.raises(FrozenCopyBlocked, match="nonempty target"):
                await copy_frozen(source, target, freeze=frozen(source, target))
    asyncio.run(run())
