"""Owned per-operation projection plans preserve Mongo BSON semantics."""
from copy import deepcopy
from datetime import datetime, timezone
import os
from urllib.parse import urlsplit
from uuid import uuid4

from bson import BSON, Decimal128, Int64
import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from backend import document_queries as q


def test_constant_membership_index_and_validation_are_once_per_operation(monkeypatch):
    calls = {"index": 0, "projection": 0}
    original_index, original_validate = q._membership_index, q.validate_projection
    def index(*args, **kwargs):
        calls["index"] += 1
        return original_index(*args, **kwargs)
    def validate(*args, **kwargs):
        calls["projection"] += 1
        return original_validate(*args, **kwargs)
    monkeypatch.setattr(q, "_membership_index", index)
    monkeypatch.setattr(q, "validate_projection", validate)
    projector = q.compile_projection({"_id": 0, "reviewed": {"$in": ["$slug", [str(i) for i in range(500)]]}})
    assert [projector({"slug": str(i)}) for i in range(1000)] == [{"reviewed": i < 500} for i in range(1000)]
    assert calls == {"index": 1, "projection": 1}
    again = q.compile_projection({"_id": 0, "reviewed": {"$in": ["$slug", ["new"]]}})
    assert again({"slug": "new"}) == {"reviewed": True}
    assert calls == {"index": 2, "projection": 2}


def test_projection_and_variables_owned_and_outputs_fresh():
    source = {"_id": 0, "included": {"$in": ["$n", [1]]}, "literal": {"$literal": {"x": [1]}}, "fixed": "$$config"}
    variables = {"config": {"owned": [1]}}
    plan = q.compile_projection(source, variables=variables)
    source["included"]["$in"][1].append(2)
    source["literal"]["$literal"]["x"].append(2)
    variables["config"]["owned"].append(2)
    first = plan({"n": 2})
    assert first == {"included": False, "literal": {"x": [1]}, "fixed": {"owned": [1]}}
    first["literal"]["x"].append(99); first["fixed"]["owned"].append(99)
    assert plan({"n": 2}) == {"included": False, "literal": {"x": [1]}, "fixed": {"owned": [1]}}
    assert q.compile_projection(source, variables=variables)({"n": 2})["included"] is True


def test_dynamic_membership_and_document_variables_never_carry_between_rows(monkeypatch):
    calls, original = [], q._membership_index
    def index(values, **kwargs):
        calls.append(deepcopy(values))
        return original(values, **kwargs)
    monkeypatch.setattr(q, "_membership_index", index)
    plan = q.compile_projection({"_id": 0, "root": "$$ROOT", "answer": {"$let": {"vars": {"allowed": "$allowed"},
        "in": {"$map": {"input": "$values", "as": "v", "in": {"$in": ["$$v", "$$allowed"]}}}}}})
    one = {"allowed": [1], "values": [1, 2]}
    two = {"allowed": [2], "values": [1, 2]}
    assert plan(one) == {"root": one, "answer": [True, False]}
    assert plan(two) == {"root": two, "answer": [False, True]}
    one["allowed"].append(2)
    assert plan(one)["answer"] == [True, True]
    assert calls == [[1], [2], [1, 2]]


IMPLICIT_CURRENT_ARRAYS = [
    {"$getField": "allowed"},
    {"$getField": {"field": "allowed"}},
    {"$getField": {"field": {"$literal": "allowed"}}},
    {"$concatArrays": [{"$getField": "allowed"}, []]},
    {"$ifNull": [{"$getField": {"field": "allowed"}}, []]},
    [{"$getField": "n"}],
    {"$map": {"input": [1], "in": {"$getField": "n"}}},
]


@pytest.mark.parametrize("array", IMPLICIT_CURRENT_ARRAYS)
def test_implicit_current_getField_is_dynamic_even_nested_and_compiled_query_agrees(array):
    rows = [{"n": 1, "allowed": [1]}, {"n": 2, "allowed": [2]}, {"n": 3, "allowed": [3]}]
    assert q._constant_expression(array) is False
    projection = {"_id": 0, "match": {"$in": ["$n", array]}}
    plan = q.compile_projection(projection)
    assert [plan(row) for row in rows] == [{"match": True}] * 3
    assert [q.project(row, projection) for row in rows] == [{"match": True}] * 3
    query = q.compile_query({"$expr": {"$in": ["$n", array]}})
    assert [query(row) for row in rows] == [True] * 3


def test_explicit_constant_getField_input_and_literal_data_remain_constant(monkeypatch):
    array = {"$getField": {"field": "allowed", "input": {"$literal": {"allowed": [1, 2]}}}}
    assert q._constant_expression(array) is True
    assert q._constant_expression({"$literal": {"$getField": "allowed"}}) is True
    calls, original = [], q._membership_index
    def index(values, **kwargs):
        calls.append(deepcopy(values)); return original(values, **kwargs)
    monkeypatch.setattr(q, "_membership_index", index)
    plan = q.compile_projection({"_id": 0, "match": {"$in": ["$n", array]}})
    assert [plan({"n": i, "allowed": [i]}) for i in (1, 2, 3)] == [{"match": True}, {"match": True}, {"match": False}]
    assert calls == [[1, 2]]


def test_clock_is_fixed_for_the_operation_and_new_operation_is_fresh():
    first, second = datetime(2026, 11, 11, tzinfo=timezone.utc), datetime(2026, 11, 12, tzinfo=timezone.utc)
    plan = q.compile_projection({"_id": 0, "at": "$$NOW"}, now=first)
    assert plan({}) == plan({"unrelated": True}) == {"at": first}
    assert q.compile_projection({"_id": 0, "at": "$$NOW"}, now=second)({}) == {"at": second}


def test_constant_index_cache_is_bounded_and_unused_branches_stay_lazy(monkeypatch):
    contexts, original = [], q._expression_membership
    def index(expression, array, context):
        result = original(expression, array, context)
        contexts.append(len(context))
        return result
    monkeypatch.setattr(q, "_expression_membership", index)
    projection = {"_id": 0, **{"k" + str(i): {"$in": ["$n", [i]]} for i in range(150)}}
    plan = q.compile_projection(projection)
    assert plan({"n": 7}) == plan({"n": 7})
    assert max(contexts) <= 128
    lazy = q.compile_projection({"_id": 0, "value": {"$cond": ["$choose", 1, {"$in": [1, {"$divide": [1, 0]}]}]}})
    assert lazy({"choose": True}) == {"value": 1}
    with pytest.raises(q.UnsupportedDocumentOperation): lazy({"choose": False})


@pytest.mark.parametrize("projection", [
    {"value": {"$cond": [True, 1, {"$unknown": 1}]}},
    {"value": {"$let": {"vars": {"x": 1}, "in": {"$unknown": "$$x"}}}},
    {"values": {"$elemMatch": {"$unknown": 1}}},
    {"bad": {"$in": [1]}}, {"included": 1, "excluded": 0},
])
def test_unknown_and_invalid_projection_branches_reject_before_any_row(projection):
    with pytest.raises(q.UnsupportedDocumentOperation): q.compile_projection(projection)


MONGO_URL = os.environ.get("IMLADRI_TEST_MONGO_URL")
integration = pytest.mark.skipif(not MONGO_URL, reason="Needs explicit disposable loopback Mongo.")


@pytest.fixture
def mongo():
    assert urlsplit(MONGO_URL).hostname in ("127.0.0.1", "localhost", "::1")
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    name = "imladri_projection_" + uuid4().hex
    try:
        yield client[name]
    finally:
        client.drop_database(name); client.close()


PROJECTIONS = [
    {"_id": 0, "n": 1, "nested.value": 1},
    {"secret": 0, "nested.hide": 0},
    {"_id": 0, "root": "$$ROOT", "current": "$$CURRENT.n"},
    {"_id": 0, "member": {"$in": ["$n", [1, Int64(2), Decimal128("3"), None, True, [1, 2]]]}},
    {"_id": 0, "nested": {"$let": {"vars": {"allowed": "$allowed"}, "in": {"$map": {
        "input": "$values", "as": "v", "in": {"value": "$$v", "present": {"$in": ["$$v", "$$allowed"]}}}}}}},
    {"_id": 0, "value": {"$cond": [{"$in": ["$n", [1, 2, 3]]}, {"examples": "$nested.value"}, "$$REMOVE"]}},
    {"_id": 0, "values": {"$elemMatch": {"$in": []}}},
    {"_id": 0, "items": {"$elemMatch": {"n": {"$gt": 1}}}},
    ["n", "nested.value"],
    {"_id": 1}, {"_id": 0}, {}, None,
]


@integration
@pytest.mark.parametrize("projection", PROJECTIONS)
def test_compiled_projection_matches_actual_mongo_on_multiple_rows(mongo, projection):
    rows = [
        {"_id": "one", "n": 1, "secret": "private", "nested": {"value": [1, 2], "hide": True},
         "values": [1, None, True, [1, 2]], "allowed": [1, [1, 2]], "items": [{"n": 1}, {"n": 2}]},
        {"_id": "two", "n": Int64(2), "nested": {"value": None}, "values": [1, None, True],
         "allowed": [None, True], "items": [{"n": 3}]},
        {"_id": "three", "n": Decimal128("3"), "nested": [{"value": "a"}, {"value": "b"}],
         "values": [], "allowed": [], "items": []},
        {"_id": "four", "n": True, "values": None, "allowed": [1], "items": []},
        {"_id": "five", "values": [], "allowed": [], "items": []},
    ]
    mongo.rows.insert_many(deepcopy(rows))
    expected = list(mongo.rows.find({}, projection))
    plan = q.compile_projection(projection)
    actual = [plan(row) for row in rows]
    assert [BSON.encode(value) for value in actual] == [BSON.encode(value) for value in expected]


@integration
@pytest.mark.parametrize("array", IMPLICIT_CURRENT_ARRAYS)
def test_implicit_current_getField_projection_and_query_match_actual_mongo(mongo, array):
    rows = [{"_id": "one", "n": 1, "allowed": [1]}, {"_id": "two", "n": 2, "allowed": [2]},
            {"_id": "three", "n": 3, "allowed": [3]}]
    mongo.rows.insert_many(deepcopy(rows))
    projection = {"_id": 0, "match": {"$in": ["$n", array]}}
    plan = q.compile_projection(projection)
    # The existing evaluator supports omitted-input object syntax as CURRENT.
    # Mongo 8 requires an explicit input for that object form (3041703), while
    # accepting shorthand. Bind this pre-existing syntax difference to the
    # explicit CURRENT equivalent, without changing the evaluator vocabulary.
    def explicit_current(value):
        if isinstance(value, dict):
            if set(value) == {"$literal"}: return deepcopy(value)
            result = {key: explicit_current(item) for key, item in value.items()}
            if set(result) == {"$getField"} and isinstance(result["$getField"], dict):
                result["$getField"].setdefault("input", "$$CURRENT")
            return result
        if isinstance(value, list): return [explicit_current(item) for item in value]
        return value
    def mongo_results(query, fields=None):
        try:
            return list(mongo.rows.find(query, fields))
        except OperationFailure as error:
            assert error.code == 3041703
            normalized_query, normalized_fields = explicit_current(query), explicit_current(fields)
            assert (normalized_query, normalized_fields) != (query, fields)
            return list(mongo.rows.find(normalized_query, normalized_fields))
    assert [BSON.encode(plan(row)) for row in rows] == [BSON.encode(row) for row in mongo_results({}, projection)]
    query = {"$expr": {"$in": ["$n", array]}}
    predicate = q.compile_query(query)
    assert [row["_id"] for row in rows if predicate(row)] == [row["_id"] for row in mongo_results(query)]


@pytest.mark.skipif(not os.environ.get("IMLADRI_TEST_POSTGRES_DSN"), reason="Needs explicit disposable loopback PostgreSQL.")
@pytest.mark.parametrize("use_rpc", [False, True])
def test_real_SQL_cursor_compiles_one_projection_for_all_rows_and_new_read_is_fresh(monkeypatch, use_rpc):
    import asyncio
    import hashlib
    from backend.postgres_store import PostgresDatabase
    dsn = os.environ["IMLADRI_TEST_POSTGRES_DSN"]
    assert urlsplit(dsn).hostname in ("127.0.0.1", "localhost", "::1")
    calls, original = [], q._membership_index
    def index(values, **kwargs):
        calls.append(deepcopy(values)); return original(values, **kwargs)
    monkeypatch.setattr(q, "_membership_index", index)
    async def exercise():
        db = PostgresDatabase(dsn, "fixture_projection_" + uuid4().hex, allow_local_plaintext=True,
                              public_catalog_cache_bytes=32 * 1024 * 1024)
        try:
            await db.figures.insert_many([{"_id": str(i), "published": True, "n": i} for i in range(100)])
            projection = {"_id": 0, "member": {"$in": ["$n", [1, 2, 3]]}, "at": "$$NOW"}
            async def read():
                if not use_rpc:
                    return await db.figures.find({"published": True}, projection).to_list(None)
                message = {"version": 1, "operation": "find", "collection": "figures", "arguments": {
                    "filter": {"published": True}, "projection": projection}}
                return await db.rpc(message, request_id=str(uuid4()), body_sha256=hashlib.sha256(BSON.encode(message)).hexdigest())
            first = await read()
            assert len(first) == 100 and sum(row["member"] for row in first) == 3
            assert len({row["at"] for row in first}) == 1
            assert calls == [[1, 2, 3]]
            projection["member"]["$in"][1].append(4)
            second = await read()
            assert len(second) == 100 and sum(row["member"] for row in second) == 4
            assert calls == [[1, 2, 3], [1, 2, 3, 4]]
            assert first[0]["at"] < second[0]["at"]
        finally:
            def cleanup(cursor, _now):
                for table in ("unique_keys", "indexes", "documents", "collections", "rpc_requests"):
                    cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (db.name,))
            await db._run(cleanup, write="__cleanup")
            await db.aclose()
    asyncio.run(exercise())
