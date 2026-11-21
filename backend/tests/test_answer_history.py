"""Accepted-write durability and privacy races against an isolated MongoDB fixture."""
import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException

from backend import answer_history as history
from backend.tests.test_admin_answer_history import run_case


def observation(question="q1", *, actor="member", value=1):
    return history.event(source="users", source_id="member", owner_id="member", actor_id=actor,
        domain="everyday", question_id=question, after={"choice": value},
        question={"text": "A normal fixture question?"}, signature="edition-one",
        demographics={"snapshot_id": "opaque-demographic-reference"})


async def accept(db, row, value, *, field="answer"):
    update = history.append({"$set": {field: value["after"]}}, value, row)
    return await db.users.update_one(history.guard({"_id": "member"}, row), update)


class DatabaseProxy:
    def __init__(self, db, central):
        self.real = db
        self.answer_history = central

    def __getitem__(self, key):
        return self.real[key]


class CentralProxy:
    def __init__(self, collection, before_update):
        self.real = collection
        self.before_update = before_update

    def __getattr__(self, name):
        return getattr(self.real, name)

    async def update_one(self, *args, **kwargs):
        await self.before_update()
        return await self.real.update_one(*args, **kwargs)


def test_accepted_write_and_history_share_one_cas_and_retry_does_not_duplicate():
    async def go(db):
        await db.users.insert_one({"_id": "member", "user_id": "member"})
        original = await db.users.find_one({"_id": "member"})
        first = observation()
        assert (await accept(db, original, first)).matched_count == 1
        assert (await accept(db, original, observation(value=2))).matched_count == 0
        saved = await db.users.find_one({"_id": "member"})
        assert saved["answer"] == {"choice": 1}
        assert len(saved[history.PENDING]) == 1
        assert await db.answer_history.count_documents({}) == 0
        assert (await history.flush(db, "users", {"_id": "member"}))["archived"] == 1
        await history.flush(db, "users", {"_id": "member"})
        assert await db.answer_history.count_documents({}) == 1
        recorded = await db.answer_history.find_one({"_id": first["id"]})
        assert recorded["source_revision"] == 1 and recorded["generation"] == 0
    run_case(go)


def test_archive_outage_retains_atomic_journal_and_recovers_without_duplicates():
    async def go(db):
        await db.users.insert_one({"_id": "member"})
        await accept(db, await db.users.find_one({"_id": "member"}), observation())
        async def fail():
            raise ConnectionError("fixture archive outage")
        broken = DatabaseProxy(db, CentralProxy(db.answer_history, fail))
        result = await history.flush(broken, "users", {"_id": "member"})
        assert result["failed"] and result["error_type"] == "ConnectionError"
        assert len((await db.users.find_one({"_id": "member"}))[history.PENDING]) == 1
        assert not (await history.flush(db, "users", {"_id": "member"}))["failed"]
        assert await db.answer_history.count_documents({}) == 1
        assert not (await db.users.find_one({"_id": "member"}))[history.PENDING]
    run_case(go)


def test_late_archive_cannot_resurrect_erased_answer_after_tombstone():
    async def go(db):
        await db.users.insert_one({"_id": "member"})
        value = observation(value=7)
        await accept(db, await db.users.find_one({"_id": "member"}), value)
        waiting, resume = asyncio.Event(), asyncio.Event()
        async def pause():
            waiting.set()
            await resume.wait()
        delayed = DatabaseProxy(db, CentralProxy(db.answer_history, pause))
        task = asyncio.create_task(history.flush(delayed, "users", {"_id": "member"}))
        await waiting.wait()
        row = await db.users.find_one({"_id": "member"})
        erase = history.redact({"$unset": {"answer": ""}}, row, question_id="q1")
        assert (await db.users.update_one(history.guard({"_id": "member"}, row), erase)).matched_count == 1
        await history.flush(db, "users", {"_id": "member"}, strict=True)
        resume.set()
        await task
        tombstone = await db.answer_history.find_one({"_id": value["id"]})
        assert set(tombstone) == {"_id", "id", "purged", "purged_at"}
        assert tombstone["purged"] is True
        assert "answer" not in await db.users.find_one({"_id": "member"})
    run_case(go)


def test_stale_rejected_erasure_does_not_remove_accepted_history():
    async def go(db):
        await db.users.insert_one({"_id": "member"})
        await accept(db, await db.users.find_one({"_id": "member"}), observation(value=1))
        await history.flush(db, "users", {"_id": "member"})
        stale = await db.users.find_one({"_id": "member"})
        await accept(db, stale, observation(value=2))
        erased = history.redact({"$unset": {"answer": ""}}, stale, question_id="q1")
        assert (await db.users.update_one(history.guard({"_id": "member"}, stale), erased)).matched_count == 0
        await history.flush(db, "users", {"_id": "member"})
        assert await db.answer_history.count_documents({"purged": {"$ne": True}}) == 2
        assert (await db.users.find_one({"_id": "member"}))["answer"] == {"choice": 2}
    run_case(go)


def test_generation_scoped_redaction_preserves_new_answer_and_other_question():
    async def go(db):
        await db.users.insert_one({"_id": "member"})
        old = observation("q1", value=1)
        await accept(db, await db.users.find_one({"_id": "member"}), old)
        await history.flush(db, "users", {"_id": "member"})
        other = observation("q2", value=3)
        await accept(db, await db.users.find_one({"_id": "member"}), other)
        row = await db.users.find_one({"_id": "member"})
        erased = history.redact({"$unset": {"answer": ""}}, row, question_id=["q1"])
        await db.users.update_one(history.guard({"_id": "member"}, row), erased)
        new = observation("q1", value=9)
        await accept(db, await db.users.find_one({"_id": "member"}), new)
        await history.flush(db, "users", {"_id": "member"}, strict=True)
        assert (await db.answer_history.find_one({"_id": old["id"]}))["purged"]
        assert (await db.answer_history.find_one({"_id": new["id"]}))["after"] == {"choice": 9}
        assert (await db.answer_history.find_one({"_id": other["id"]}))["after"] == {"choice": 3}
    run_case(go)


def test_failed_redaction_remains_durable_and_retry_finishes(monkeypatch):
    async def go(db):
        await db.users.insert_one({"_id": "member"})
        first = observation("q1", value=1)
        await accept(db, await db.users.find_one({"_id": "member"}), first)
        await history.flush(db, "users", {"_id": "member"})
        second = observation("q1", value=2)
        await accept(db, await db.users.find_one({"_id": "member"}), second)
        row = await db.users.find_one({"_id": "member"})
        await db.users.update_one(history.guard({"_id": "member"}, row),
                                  history.redact({"$unset": {"answer": ""}}, row))
        real = history._tombstone
        async def failed(*args, **kwargs):
            raise ConnectionError("fixture redaction outage")
        monkeypatch.setattr(history, "_tombstone", failed)
        with pytest.raises(HTTPException) as unavailable:
            await history.flush(db, "users", {"_id": "member"}, strict=True)
        assert unavailable.value.status_code == 503
        assert (await db.users.find_one({"_id": "member"}))[history.REDACTIONS]
        monkeypatch.setattr(history, "_tombstone", real)
        await history.flush(db, "users", {"_id": "member"}, strict=True)
        assert await db.answer_history.count_documents({"purged": True}) == 2
        assert not (await db.users.find_one({"_id": "member"}))[history.REDACTIONS]
        assert not (await db.users.find_one({"_id": "member"}))[history.PENDING]
    run_case(go)


def test_multiple_scoped_redactions_share_one_privacy_generation():
    original = {history.GENERATION: 4, history.REVISION: 9,
                history.SOURCE: {"source": "users", "source_id": "member"},
                history.PENDING: [{**observation("thread:one", actor="a"), "generation": 4}]}
    update = history.redact({}, original, question_prefix="thread:", actor_id="a")
    history.redact(update, original, question_id="q1")
    assert update["$inc"] == {history.GENERATION: 1, history.REVISION: 1}
    assert len(update["$push"][history.REDACTIONS]["$each"]) == 2
    assert len(update["$push"][history.REDACTIONS]["$each"][0]["pending_ids"]) == 1


def test_legitimate_full_everyday_batch_fits_and_capacity_fails_before_mutation():
    batch = [observation("q" + str(i)) for i in range(109)]
    update = history.append({}, batch, {})
    assert len(update["$push"][history.PENDING]["$each"]) == 109
    original = {history.PENDING: deepcopy(batch * 2)}
    with pytest.raises(HTTPException) as unavailable:
        history.append({}, batch, original)
    assert unavailable.value.status_code == 503
    assert len(original[history.PENDING]) == 218


def test_history_preservation_does_not_fabricate_old_attempt_timestamps():
    value = history.event(source="users", source_id="member", owner_id="member", domain="everyday",
        question_id="q1", after={"choice": 2}, before={"choice": 1}, before_exists=True)
    assert value["before_observed_at"] is None
    assert value["baseline"] is False
    assert history.preserve({"answer": 3, history.GENERATION: 7}) == {history.GENERATION: 7}


def test_bulk_archive_has_bounded_parallelism_and_one_source_acknowledgment():
    async def go(db):
        await db.users.insert_one({"_id": "member"})
        row = await db.users.find_one({"_id": "member"})
        events = [observation("q" + str(i)) for i in range(109)]
        await db.users.update_one(history.guard({"_id": "member"}, row), history.append({}, events, row))
        live, maximum, source_pulls = 0, 0, 0
        async def observe_parallelism():
            nonlocal live, maximum
            live += 1
            maximum = max(maximum, live)
            await asyncio.sleep(.003)
            live -= 1
        class Source:
            def __getattr__(self, name):
                return getattr(db.users, name)
            async def update_one(self, query, update, **kwargs):
                nonlocal source_pulls
                source_pulls += int(history.PENDING in update.get("$pull", {}))
                return await db.users.update_one(query, update, **kwargs)
        source = Source()
        class BatchDatabase(DatabaseProxy):
            def __getitem__(self, name):
                return source if name == "users" else self.real[name]
        wrapped = BatchDatabase(db, CentralProxy(db.answer_history, observe_parallelism))
        result = await history.flush(wrapped, "users", {"_id": "member"})
        assert result["archived"] == 109 and result["failed"] is False
        assert 1 < maximum <= history.ARCHIVE_CONCURRENCY == 4
        assert source_pulls == 1
        assert await db.answer_history.count_documents({}) == 109
        assert not (await db.users.find_one({"_id": "member"}))[history.PENDING]
    run_case(go)


def test_partial_archive_failure_acknowledges_only_persisted_ids():
    async def go(db):
        await db.users.insert_one({"_id": "member"})
        row = await db.users.find_one({"_id": "member"})
        events = [observation("q" + str(i)) for i in range(5)]
        await db.users.update_one(history.guard({"_id": "member"}, row), history.append({}, events, row))
        calls = 0
        async def fail_one():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ConnectionError("one fixture archive failed")
        wrapped = DatabaseProxy(db, CentralProxy(db.answer_history, fail_one))
        result = await history.flush(wrapped, "users", {"_id": "member"})
        assert result["failed"] and result["archived"] == 4
        pending = (await db.users.find_one({"_id": "member"}))[history.PENDING]
        assert len(pending) == 1 and await db.answer_history.find_one({"_id": pending[0]["id"]}) is None
        await history.flush(db, "users", {"_id": "member"})
        assert await db.answer_history.count_documents({}) == 5
        assert not (await db.users.find_one({"_id": "member"}))[history.PENDING]
    run_case(go)
