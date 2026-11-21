"""Normal application update fixtures, including real Mongo differential checks."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import itertools
import os
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from bson import ObjectId
from pymongo import MongoClient

from backend import answer_history, membership_plans
from backend.document_queries import MISSING, UnsupportedDocumentOperation, bson_equal
from backend.document_updates import DocumentUpdateError, apply_update, upsert_document, validate_update


def test_credit_reservation_pipeline_uses_one_stage_snapshot_and_literal_receipt():
    receipt = {"pending": True, "result": "fixture", "signature": "$literal body"}
    update = [{"$set": {
        "receipts.key": {"$cond": [{"$lt": [{"$ifNull": ["$claimed", 0]}, 100]},
                                   {"$literal": receipt}, {"$literal": {**receipt, "pool": "topup"}}]},
        "claimed": {"$add": [{"$ifNull": ["$claimed", 0]}, {"$cond": [{"$lt": [{"$ifNull": ["$claimed", 0]}, 100]}, 1, 0]}]},
        "topup": {"$subtract": [{"$ifNull": ["$topup", 0]}, {"$cond": [{"$lt": [{"$ifNull": ["$claimed", 0]}, 100]}, 0, 1]}]},
    }}]
    source = {"_id": "member", "claimed": 99, "topup": 3}
    first = apply_update(source, update)
    assert first["claimed"] == 100 and first["topup"] == 3
    assert first["receipts"]["key"] == receipt
    second = apply_update(first, update)
    assert second["claimed"] == 100 and second["topup"] == 2
    assert second["receipts"]["key"]["pool"] == "topup"
    assert source == {"_id": "member", "claimed": 99, "topup": 3}


def test_actual_topup_grant_pipeline_never_regrants_refunded_spent_credits():
    class Collection:
        def __init__(self):
            self.row = {"_id": "member", "user_id": "member"}
        async def update_one(self, query, update):
            self.row = apply_update(self.row, update)
            return SimpleNamespace(matched_count=1)
    async def check(order):
        users = Collection()
        db = SimpleNamespace(users=users)
        for value in order:
            assert await membership_plans.apply_topup(db, "member", "one-purchase", value)
        assert users.row["topup_balance"] == 1
        users.row = apply_update(users.row, {"$inc": {"topup_balance": -1}})
        await membership_plans.apply_topup(db, "member", "one-purchase", 0)
        await membership_plans.apply_topup(db, "member", "one-purchase", 100)
        assert users.row["topup_balance"] == -1
        await membership_plans.apply_topup(db, "member", "new-purchase", 100)
        assert users.row["topup_balance"] == 99
    for order in itertools.permutations((100, 1, 12)):
        asyncio.run(check(order))


def test_history_batch_redaction_and_generation_are_copied_without_payload_loss():
    original = {"_id": "member", "user_id": "member"}
    events = [answer_history.event(source="users", source_id="member", owner_id="member",
        domain="everyday", question_id=key, after={"choice": value}) for key, value in (("one", 1), ("two", 2))]
    accepted = apply_update(original, answer_history.append({"$set": {"answer": 2}}, events, original))
    assert accepted[answer_history.REVISION] == 1 and len(accepted[answer_history.PENDING]) == 2
    erased = apply_update(accepted, answer_history.redact({"$unset": {"answer": ""}}, accepted, question_id="one"))
    assert erased[answer_history.GENERATION] == 1 and erased[answer_history.REVISION] == 2
    redaction = erased[answer_history.REDACTIONS][0]
    assert redaction["pending_ids"] == [events[0]["id"]]
    drained = apply_update(erased, {"$pull": {answer_history.PENDING: {"id": {"$in": [events[0]["id"]]}},
        answer_history.REDACTIONS: {"id": redaction["id"]}}})
    assert drained[answer_history.PENDING][0]["question_id"] == "two"
    assert not drained[answer_history.REDACTIONS] and "answer" not in drained
    assert len(accepted[answer_history.PENDING]) == 2 and original == {"_id": "member", "user_id": "member"}


def test_set_on_insert_tombstone_and_literal_fields_remain_immutable():
    tombstone = {"_id": "event", "id": "event", "purged": True}
    assert apply_update(tombstone, {"$setOnInsert": {"answer": "erased"}}) == tombstone
    created = apply_update({"_id": "event"}, {"$setOnInsert": {"answer": "$question"}}, inserting=True)
    assert created["answer"] == "$question"
    assert apply_update(created, {"$set": {"_id": "event"}}) == created
    with pytest.raises(DocumentUpdateError, match="immutable"):
        apply_update(created, {"$unset": {"_id": ""}})
    with pytest.raises(DocumentUpdateError, match="immutable"):
        apply_update(created, [{"$set": {"_id": "another"}}])


def test_clock_updates_require_database_time_and_use_same_time_for_all_fields():
    clock = datetime(2026, 11, 10, 12, 30, 15, 123000, tzinfo=timezone.utc)
    current = apply_update({}, {"$currentDate": {"checked_at": True, "second": {"$type": "date"}}}, now=clock)
    assert current == {"checked_at": clock, "second": clock}
    pipeline = apply_update({}, [{"$set": {"first": "$$NOW", "second": "$$NOW"}}], now=clock)
    assert pipeline == {"first": clock, "second": clock}
    with pytest.raises(DocumentUpdateError, match="database clock"):
        apply_update({}, {"$currentDate": {"checked_at": True}})
    with pytest.raises(DocumentUpdateError, match="database clock"):
        apply_update({}, [{"$set": {"checked_at": "$$NOW"}}])


def test_missing_reference_unsets_pipeline_target_and_next_stage_observes_prior_stage():
    result = apply_update({"a": 1, "obsolete": 3}, [{"$set": {"a": 2, "old_a": "$a", "obsolete": "$missing"}},
                                                    {"$set": {"new_a": "$a"}}])
    assert result == {"a": 2, "old_a": 1, "new_a": 2}
    assert result.get("obsolete", MISSING) is MISSING


def test_unsupported_operators_and_type_errors_never_mutate_input():
    original = {"_id": "member", "counter": None, "values": 3}
    for update in ({"$unknown": {"counter": 1}}, [{"$unknown": {"counter": 1}}]):
        with pytest.raises(UnsupportedDocumentOperation):
            apply_update(original, update)
    for update in ({"$inc": {"counter": 1}}, {"$inc": {"new": True}}, {"$push": {"values": 4}},
                   {"$set": {"x": 1}, "$unset": {"x.child": ""}}):
        with pytest.raises(DocumentUpdateError):
            apply_update(original, update)
    assert original == {"_id": "member", "counter": None, "values": 3}


def test_upsert_seed_uses_conjunctive_equalities_and_never_alternative_or_range_guesses():
    seed = upsert_document({"user_id": "member", "$and": [{"scope.env": {"$eq": "live"}}, {"tier": {"$in": ["paid"]}}],
        "$or": [{"closing": False}, {"closing": None}], "expires": {"$gt": 5}})
    assert seed == {"user_id": "member", "scope": {"env": "live"}}
    with pytest.raises(DocumentUpdateError):
        upsert_document({"a": 1, "$and": [{"a": 2}]})


def test_validation_rejects_unknown_unchosen_expression_or_array_predicate_without_rows():
    with pytest.raises(UnsupportedDocumentOperation):
        validate_update([{"$set": {"x": {"$cond": [True, 1, {"$not_implemented": 2}]}}}])
    with pytest.raises(UnsupportedDocumentOperation):
        validate_update({"$pull": {"history": {"id": {"$not_implemented": 2}}}})
    with pytest.raises(UnsupportedDocumentOperation):
        validate_update({"$setOnInsert": {"unchosen_literal": 1}, "$not_implemented": {}})
    validate_update({"$set": {"literal": {"$not_an_expression": "kept verbatim"}}})


@pytest.fixture(scope="module")
def mongo_collection():
    uri = os.getenv("IMLADRI_DOCUMENT_TEST_MONGO_URL") or os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Set a loopback test Mongo URI for differential document update checks")
    assert urlsplit(uri).hostname in {"127.0.0.1", "localhost", "::1"}
    client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    name = "imladri_document_updates_" + uuid4().hex
    try:
        client.admin.command("ping")
        yield client[name].normal_cases
    finally:
        client.drop_database(name)
        client.close()


NORMAL_CASES = [
    ({}, {"$set": {"a.b": 1}}),
    ({"a": {"keep": 2}}, {"$set": {"a.b": {"text": "$literal"}}}),
    ({"a": 3}, {"$unset": {"a.child": "", "missing": 1}}),
    ({"a": [1, {"old": 3}]}, {"$set": {"a.1.new": 4, "a.3": "last"}}),
    ({"a": [1, 2, 3]}, {"$unset": {"a.1": 1}}),
    ({"count": 4}, {"$inc": {"count": -1, "new": 2}}),
    ({"at": "later", "n": 4}, {"$min": {"at": "earlier", "n": 6, "new": 1}}),
    ({"n": None}, {"$max": {"n": 4}}),
    ({"v": [1, True]}, {"$addToSet": {"v": {"$each": [1.0, False, 2, 2]}}}),
    ({"v": [{"a": 1, "b": 2}]}, {"$addToSet": {"v": {"b": 2, "a": 1}}}),
    ({}, {"$addToSet": {"v": {"$each": []}}}),
    ({"v": [1]}, {"$push": {"v": [2, 3]}}),
    ({"v": [1, 2]}, {"$push": {"v": {"$each": [3, 4], "$slice": -3}}}),
    ({"v": [1, 4]}, {"$push": {"v": {"$each": [2, 3], "$position": -1}}}),
    ({"v": [3, 1]}, {"$push": {"v": {"$each": [2], "$sort": 1, "$slice": 2}}}),
    ({"v": [{"n": 3}, {"n": 1}]}, {"$push": {"v": {"$each": [{"n": 2}], "$sort": {"n": -1}}}}),
    ({"v": [1, 2, 3]}, {"$pull": {"v": {"$gte": 2}}}),
    ({"v": [{"id": "one", "value": 1}, {"id": "two", "value": 2}]}, {"$pull": {"v": {"id": {"$in": ["one"]}}}}),
    ({"candidates": [{"seller_id": "owner"}, {"listing_id": "one"}, {"seller_id": "other", "listing_id": "none"}]},
     {"$pull": {"candidates": {"$or": [{"seller_id": "owner"}, {"listing_id": {"$in": ["one"]}}]}}}),
    ({"v": [False, 0, True, 1]}, {"$pull": {"v": True}}),
    ({}, {"$pull": {"v": {"id": "missing"}}}),
    ({"keep": 1}, {"$setOnInsert": {"created": 2}, "$set": {"keep": 3}}),
    ({"a": 2, "b": 3}, [{"$set": {"a": "$b", "b": "$a"}}]),
    ({"a": 2, "b": 3}, [{"$set": {"total": {"$add": ["$a", "$b"]}}}, {"$unset": ["a", "b"]}]),
]


@pytest.mark.parametrize("initial,update", NORMAL_CASES)
def test_normal_updates_match_real_mongo(mongo_collection, initial, update):
    source = {"_id": ObjectId(), **deepcopy(initial)}
    mongo_collection.insert_one(deepcopy(source))
    mongo_collection.update_one({"_id": source["_id"]}, update)
    actual = mongo_collection.find_one({"_id": source["_id"]})
    result = apply_update(source, update)
    assert bson_equal(result, actual), (result, actual)
    assert source == {"_id": source["_id"], **initial}


def test_upsert_seed_and_insert_only_modifiers_match_real_mongo(mongo_collection):
    query = {"_id": ObjectId(), "owner": "member", "$and": [{"scope.env": {"$eq": "live"}}], "limit": {"$gt": 3}}
    update = {"$setOnInsert": {"created": "first"}, "$inc": {"counter": 1}}
    mongo_collection.update_one(query, update, upsert=True)
    actual = mongo_collection.find_one({"_id": query["_id"]})
    result = apply_update(upsert_document(query), update, inserting=True)
    # Mongo's equality-extraction map does not promise the top-level insertion
    # order of conjunctive upsert seeds. Values and BSON types remain exact.
    assert result.keys() == actual.keys()
    assert all(bson_equal(result[key], actual[key]) for key in result)
