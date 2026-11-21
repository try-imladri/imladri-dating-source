"""Differential checks for the application's actual document vocabulary."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import os
from uuid import uuid4

from bson import Int64, ObjectId
import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from backend import document_queries as q
from backend import report_dates, questionnaire_report


def test_missing_numeric_and_embedded_document_identity():
    assert deepcopy(q.MISSING) is q.MISSING
    assert q.bson_equal(1, 1.0) and q.bson_equal(Int64(1), 1)
    assert not q.bson_equal(True, 1)
    assert not q.bson_equal({"a": 1, "b": 2}, {"b": 2, "a": 1})
    assert q.matches({}, {"a": None}) and not q.matches({}, {"a": {"$ne": None}})
    assert q.path_values({"a": [{"b": 1}, {"b": 2}]}, "a.b") == [1, 2]
    assert q.get_path({"a": [{"b": 1}, {"b": 2}]}, "a.b") == [1, 2]
    assert q.bson_hash_key(1) == q.bson_hash_key(1.0) == q.bson_hash_key(Int64(1))
    assert q.bson_hash_key(True) != q.bson_hash_key(1)
    assert q.bson_hash_key({"a": 1, "b": 2}) != q.bson_hash_key({"b": 2, "a": 1})


def test_group_and_foreign_lookup_are_linear_at_ten_thousand_members(monkeypatch):
    size = 10001
    people = [{"_id": i, "user_id": f"member-{i}", "n": 1} for i in range(size)]
    calls, original = [0], q.bson_equal
    def counted(a, b):
        calls[0] += 1
        return original(a, b)
    monkeypatch.setattr(q, "bson_equal", counted)
    grouped = q.aggregate_documents(people, [{"$group": {"_id": "$user_id", "n": {"$sum": "$n"}}}])
    assert len(grouped) == size and all(row["n"] == 1 for row in grouped)
    joined = q.aggregate_documents(people, [{"$lookup": {"from": "people", "localField": "user_id",
        "foreignField": "user_id", "as": "peer"}}], lookup=lambda _: people)
    assert all(len(row["peer"]) == 1 and row["peer"][0]["user_id"] == row["user_id"] for row in joined)
    assert calls[0] < size*10


def test_compiled_membership_is_linear_at_twenty_thousand_events(monkeypatch):
    size = 10001
    members = [f"member-{index}" for index in range(size)]
    rows = [{"user_id": members[index % size]} for index in range(size*2)]
    calls, original = [0], q.bson_equal
    visits, original_constant = [0], q._constant_expression
    def counted(a, b):
        calls[0] += 1
        assert calls[0] < size*20
        return original(a, b)
    def visited(value):
        visits[0] += 1
        assert visits[0] < size*10
        return original_constant(value)
    monkeypatch.setattr(q, "bson_equal", counted)
    monkeypatch.setattr(q, "_constant_expression", visited)
    for query, variables in [
        ({"user_id": {"$in": members}}, None),
        ({"$expr": {"$in": ["$user_id", members]}}, None),
        ({"$expr": {"$in": ["$user_id", "$$allowed"]}}, {"allowed": members}),
    ]:
        predicate = q.compile_query(query, variables=variables)
        assert sum(predicate(row) for row in rows) == size*2
    assert calls[0] < size*20


def test_compiled_query_is_immutable_and_does_not_share_identity_caches():
    query = {"value": {"$in": ["first"]}}
    first = q.compile_query(query)
    query["value"]["$in"][:] = ["second"]
    second = q.compile_query(query)
    assert first({"value": "first"}) and not first({"value": "second"})
    assert second({"value": "second"}) and not second({"value": "first"})


@pytest.mark.parametrize("action", [
    lambda: q.matches({}, {"$or": [{}, {"a": {"$unknown": 2}}]}),
    lambda: q.evaluate({"$cond": [True, 1, {"$unknown": 2}]}, {}),
    lambda: q.aggregate_documents([], [{"$project": {"x": {"$unknown": 2}}}]),
    lambda: q.aggregate_documents([], [{"$lookup": {"from": "x", "as": "x", "pipeline": [{"$unknown": {}}]}}]),
    lambda: q.aggregate_documents([], [{"$group": {"_id": None, "x": {"$unknown": "$v"}}}]),
    lambda: q.aggregate_documents([], [{"$unknown": {}}]),
])
def test_unknown_operations_fail_even_without_rows_or_in_hidden_branches(action):
    with pytest.raises(q.UnsupportedDocumentOperation): action()


MONGO_URL = os.environ.get("IMLADRI_TEST_MONGO_URL")
integration = pytest.mark.skipif(not MONGO_URL, reason="Disposable loopback Mongo required")


@integration
@pytest.mark.parametrize("expression", [
    {"$isNumber": "$missing"}, {"$isNumber": None}, {"$isNumber": True},
    {"$isNumber": Int64(4)}, {"$isNumber": 1.25}, {"$isNumber": "1.25"},
    {"$pow": [2, 10]}, {"$pow": [2, 32]}, {"$pow": [Int64(2), 3]},
    {"$pow": [2, 63]}, {"$pow": [5, -2]}, {"$pow": [-5, 0.5]},
    {"$pow": ["$missing", 2]}, {"$pow": [0, 0]},
    {"$sin": 0.5}, {"$cos": -2}, {"$asin": 1}, {"$asin": -1},
    {"$degreesToRadians": 180}, {"$degreesToRadians": -720.5},
    {"$sin": None}, {"$cos": "$missing"},
])
def test_legacy_catalog_math_matches_mongo(mongo, expression):
    import math
    mongo.rows.insert_one({"_id": "fixture"})
    expected = list(mongo.rows.aggregate([{"$project": {"value": expression}}]))[0]["value"]
    actual = q.evaluate(expression, {})
    if isinstance(expected, float) and math.isnan(expected): assert math.isnan(actual)
    elif isinstance(expected, float): assert actual == pytest.approx(expected, rel=1e-14, abs=1e-14)
    else: assert q.bson_equal(actual, expected)
    assert q.bson_type(actual) == q.bson_type(expected)


@integration
@pytest.mark.parametrize("expression", [
    {"$pow": [0, -1]}, {"$pow": [True, 2]}, {"$sin": "one"},
    {"$sin": float("inf")}, {"$asin": 1.1},
])
def test_legacy_catalog_invalid_math_rejects_like_mongo(mongo, expression):
    mongo.rows.insert_one({"_id": "fixture"})
    with pytest.raises(OperationFailure): list(mongo.rows.aggregate([{"$project": {"value": expression}}]))
    with pytest.raises(q.UnsupportedDocumentOperation): q.evaluate(expression, {})


@integration
def test_legacy_catalog_haversine_boundaries_match_mongo(mongo):
    # Frozen Market distance expression: ordinary doubles plus invalid/missing
    # location values and antipodes. No native geo index or location provider.
    import math
    lat, lng = 41.8781, -87.6298
    delta_lat = {"$divide": [{"$degreesToRadians": {"$subtract": ["$lat", lat]}}, 2]}
    delta_lng = {"$divide": [{"$degreesToRadians": {"$subtract": ["$lng", lng]}}, 2]}
    haversine = {"$add": [{"$pow": [{"$sin": delta_lat}, 2]}, {"$multiply": [
        math.cos(math.radians(lat)), {"$cos": {"$degreesToRadians": "$lat"}}, {"$pow": [{"$sin": delta_lng}, 2]}]}]}
    expression = {"$cond": [{"$and": [{"$isNumber": "$lat"}, {"$isNumber": "$lng"}]},
        {"$multiply": [7917.6, {"$asin": {"$sqrt": {"$min": [1, {"$max": [0, haversine]}]}}}]}, None]}
    rows = [{"_id": index, **location} for index, location in enumerate([
        {"lat": lat, "lng": lng}, {"lat": 41.88, "lng": -87.63},
        {"lat": -lat, "lng": lng + 180}, {"lat": 0, "lng": 0},
        {"lat": None, "lng": lng}, {"lat": "41.0", "lng": lng}, {"lng": lng},
    ])]
    mongo.rows.insert_many(rows)
    pipeline = [{"$project": {"value": expression}}, {"$sort": {"_id": 1}}]
    expected = list(mongo.rows.aggregate(pipeline))
    actual = q.aggregate_documents(rows, pipeline)
    for left, right in zip(actual, expected):
        assert left["_id"] == right["_id"]
        if right["value"] is None: assert left["value"] is None
        else: assert left["value"] == pytest.approx(right["value"], rel=1e-14, abs=1e-10)


@pytest.fixture
def mongo():
    if not MONGO_URL: pytest.skip("Disposable loopback Mongo required")
    from scripts.migrate_postgres import loopback_mongo
    assert loopback_mongo(MONGO_URL)
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    database = client["imladri_document_queries_" + uuid4().hex]
    try: yield database
    finally: client.drop_database(database.name); client.close()


DOCUMENTS = [
    {"_id": 0}, {"_id": 1, "a": None}, {"_id": 2, "a": []},
    {"_id": 3, "a": [None, 1]}, {"_id": 4, "a": 1}, {"_id": 5, "a": True},
    {"_id": 6, "a": "Alpha"}, {"_id": 7, "a": {"x": 1}}, {"_id": 8, "a": [{"x": 2}, {"x": 5}]},
    {"_id": 9, "a": [[1, 2], [3]]}, {"_id": 10, "a": Int64(2)},
    {"_id": 11, "a": {"x": 1, "y": 2}}, {"_id": 12, "a": {"y": 2, "x": 1}},
    {"_id": 13, "a": [False, "beta", 5]}, {"_id": 14, "a": datetime(2026, 11, 10, tzinfo=timezone.utc)},
]
QUERIES = [
    {}, {"a": None}, {"a": 1}, {"a": True}, {"a": []}, {"a": [1, 2]}, {"a": {"x": 1}},
    {"a": {"x": 1, "y": 2}}, {"a": {"$ne": None}}, {"a": {"$ne": 1}}, {"a": {"$in": [None, 2]}},
    {"a": {"$nin": [None]}}, {"a": {"$exists": False}}, {"a": {"$exists": True}}, {"a": {"$type": "array"}},
    {"a": {"$type": "number"}}, {"a": {"$type": "object"}}, {"a": {"$gt": {}}}, {"a": {"$gte": None}},
    {"a": {"$gt": 1}}, {"a": {"$lt": 2}}, {"a": {"$not": {"$gte": 2}}}, {"a": {"$size": 2}},
    {"a": {"$regex": "^a", "$options": "i"}}, {"a.x": {"$gt": 2}}, {"a.0": {"$exists": True}},
    {"a": {"$elemMatch": {"x": {"$gte": 2, "$lt": 3}}}}, {"a": {"$elemMatch": {"$gt": 0, "$lt": 2}}},
    {"$expr": {"$eq": ["$a", None]}}, {"$expr": {"$eq": [{"$type": "$a"}, "array"]}},
    {"$and": [{"a": {"$exists": True}}, {"a": {"$ne": None}}]}, {"$nor": [{"a": 1}, {"a": True}]},
]


@integration
@pytest.mark.parametrize("query", QUERIES)
def test_actual_query_semantics_match_mongo(mongo, query):
    mongo.rows.insert_many(deepcopy(DOCUMENTS))
    source = list(mongo.rows.find({}))
    expected = sorted(doc["_id"] for doc in mongo.rows.find(query))
    actual = sorted(doc["_id"] for doc in source if q.matches(doc, query))
    assert actual == expected


EXPRESSIONS = [
    "$missing", "$a", {"$literal": "$a"}, {"$ifNull": ["$missing", "$a", 4]},
    {"$cond": [{"$eq": ["$a", 2]}, "yes", "no"]},
    {"$switch": {"branches": [{"case": {"$gte": ["$a", 1]}, "then": 4}], "default": None}},
    {"$let": {"vars": {"n": "$a"}, "in": {"$add": ["$$n", 3]}}},
    {"$map": {"input": "$arr", "as": "n", "in": {"$ifNull": ["$$n", 0]}}},
    {"$filter": {"input": "$arr", "cond": {"$ne": ["$$this", None]}}},
    {"$reduce": {"input": "$arr", "initialValue": 0, "in": {"$add": ["$$value", {"$ifNull": ["$$this", 0]}]}}},
    {"$type": "$a"}, {"$type": "$missing"}, {"$isArray": "$arr"}, {"$and": [True, [], 1]},
    {"$not": [False]}, {"$in": ["$a", [1, 2, 3]]},
    {"$setEquals": [[1, 2], [2, 1, 1]]}, {"$setIsSubset": [[1], [1, 2]]},
    {"$setUnion": [[1, 2], [2, 3]]}, {"$anyElementTrue": [[False, []]]},
    {"$arrayElemAt": ["$arr", -1]}, {"$arrayElemAt": ["$arr", 99]},
    {"$indexOfArray": ["$arr", 2]}, {"$size": "$arr"}, {"$concatArrays": ["$arr", [8]]},
    {"$objectToArray": "$obj"}, {"$arrayToObject": {"$literal": [{"k": "x", "v": 1}, {"k": "x", "v": 2}]}},
    {"$getField": {"field": {"$literal": "a.b"}, "input": "$obj"}},
    {"$mergeObjects": [{"a": 1}, None, {"b": 2, "a": 3}]},
    {"$concat": ["prefix", "$text"]}, {"$trim": {"input": "$text"}}, {"$strLenCP": "$text"},
    {"$substrBytes": ["abcdef", 1, 3]}, {"$regexMatch": {"input": "$text", "regex": "^[ ]*a", "options": "i"}},
    {"$regexFind": {"input": "$text", "regex": "(a)(l)?", "options": "i"}},
    {"$convert": {"input": "$a", "to": "string", "onNull": "", "onError": "bad"}},
    {"$convert": {"input": "12.4", "to": "int", "onError": -1}},
    {"$toInt": "42"}, {"$toLong": "$when"}, {"$toString": "$when"}, {"$toBool": {"$literal": []}},
    {"$add": [1, "$a", 2]}, {"$subtract": [6, "$a"]}, {"$multiply": [2, "$a"]},
    {"$divide": [3, "$a"]}, {"$floor": 3.5}, {"$sqrt": 4}, {"$round": [2.125, 2]},
    {"$sum": "$arr"}, {"$avg": "$arr"}, {"$min": "$arr"}, {"$max": [1, "$a"]},
    {"$dateToString": {"date": "$when", "format": "%Y-%m-%dT%H:%M:%S.%LZ", "timezone": "UTC"}},
    report_dates.instant_micros("$stamp"),
    {"$toInt": ["$a"]}, {"$type": [[1, 2]]}, {"$isArray": [["$a"]]},
]


@integration
@pytest.mark.parametrize("expression", EXPRESSIONS)
def test_actual_expression_semantics_match_mongo(mongo, expression):
    mongo.rows.insert_one({"_id": "row", "a": 2, "arr": [1, 2, None], "obj": {"a.b": 7, "b": 3},
                          "text": " Alpha ", "when": datetime(2026, 11, 10, 12, 34, 56, 123000, tzinfo=timezone.utc),
                          "stamp": "2026-11-10T04:05:06.123456+01:00"})
    source = mongo.rows.find_one({})
    expected = list(mongo.rows.aggregate([{"$project": {"_id": 0, "out": expression}}]))[0]
    value = q.evaluate(expression, source)
    actual = {} if value is q.MISSING else {"out": value}
    assert q.bson_equal(actual, expected)


@integration
@pytest.mark.parametrize("projection", [
    {"_id": 0, "a": 1}, {"a.x": 1}, {"_id": 0}, {"a.x": 0}, {"_id": 1},
    {"_id": 0, "alias": "$a", "missing": "$unknown"}, {"_id": 0, "empty": {"$literal": {}}},
])
def test_projection_matches_mongo(mongo, projection):
    mongo.rows.insert_many(deepcopy(DOCUMENTS))
    rows = list(mongo.rows.find({}).sort("_id", 1))
    expected = list(mongo.rows.aggregate([{"$sort": {"_id": 1}}, {"$project": projection}]))
    assert [q.project(doc, projection) for doc in rows] == expected


@integration
def test_find_elem_match_projection_returns_only_first_matching_element(mongo):
    documents = [
        {"_id": 1, "prompt_history": [{"revision": 1}, {"revision": 2, "prompt": "first"}, {"revision": 2, "prompt": "later"}], "private": "excluded"},
        {"_id": 2, "prompt_history": [{"revision": 1}]},
        {"_id": 3, "prompt_history": []}, {"_id": 4},
    ]
    mongo.rows.insert_many(documents)
    projection = {"prompt_history": {"$elemMatch": {"revision": 2}}}
    assert [q.project(doc, projection) for doc in documents] == list(mongo.rows.find({}, projection).sort("_id", 1))
    with pytest.raises(q.UnsupportedDocumentOperation):
        q.aggregate_documents([], [{"$project": projection}])


@integration
@pytest.mark.parametrize("pattern,options", [
    (r"\d", ""), (r"\w", ""), (r"\s", ""), (r"\w", "i"),
    ("i", "i"), ("İ", "i"), ("ı", "i"), ("[a-z]", "i"), ("[^i]", "i"), ("é", "i"),
])
def test_actual_pcre_character_classes_and_unicode_folding(mongo, pattern, options):
    texts = ["i", "I", "İ", "ı", "ſ", "s", "K", "k", "é", "É", "٣", "3", "a b", "a\u00a0b"]
    docs = [{"_id": index, "value": value} for index, value in enumerate(texts)]
    mongo.rows.insert_many(docs)
    query = {"value": {"$regex": pattern, "$options": options}}
    assert [doc["_id"] for doc in docs if q.matches(doc, query)] == [doc["_id"] for doc in mongo.rows.find(query)]


@integration
@pytest.mark.parametrize("sort", [[("a", 1), ("_id", 1)], [("a", -1), ("_id", 1)], [("a.x", 1), ("_id", -1)]])
def test_multikey_bson_sort_matches_mongo(mongo, sort):
    mongo.rows.insert_many(deepcopy(DOCUMENTS))
    rows = list(mongo.rows.find({}))
    assert [d["_id"] for d in q.sort_documents(rows, sort)] == [d["_id"] for d in mongo.rows.find({}).sort(sort)]


@integration
def test_actual_lookup_union_group_and_unwind_semantics(mongo):
    source = [{"_id": "a", "members": ["one", "two"], "weight": 2},
              {"_id": "b", "members": [], "weight": None}, {"_id": "c", "weight": 3}]
    users = [{"_id": "one", "user_id": "one", "env": "live", "score": 1}, {"_id": "two", "user_id": "two", "env": "other", "score": 2},
             {"_id": "missing", "env": "live", "score": 3}]
    mongo.rows.insert_many(source); mongo.users.insert_many(users)
    pipeline = [
        {"$lookup": {"from": "users", "localField": "members", "foreignField": "user_id", "let": {"w": "$weight"},
                     "pipeline": [{"$match": {"env": "live", "$expr": {"$gte": ["$score", {"$ifNull": ["$$w", 0]}]}}},
                                  {"$project": {"_id": 0, "value": "$score", "outer": "$$w"}}], "as": "seats"}},
        {"$unwind": {"path": "$seats", "preserveNullAndEmptyArrays": True}},
        {"$group": {"_id": None, "n": {"$sum": 1}, "weight": {"$avg": "$weight"}, "first": {"$first": "$missing"},
                    "ids": {"$push": "$_id"}, "values": {"$addToSet": "$seats.value"}, "min": {"$min": "$weight"}}},
    ]
    data = {"users": list(mongo.users.find({})), "rows": list(mongo.rows.find({}))}
    assert q.aggregate_documents(data["rows"], pipeline, lookup=lambda name: data[name]) == list(mongo.rows.aggregate(pipeline))
    union = [{"$project": {"_id": 0, "v": "$weight"}}, {"$unionWith": {"coll": "users", "pipeline": [{"$project": {"_id": 0, "v": "$score"}}]}}]
    assert q.aggregate_documents(data["rows"], union, lookup=lambda name: data[name]) == list(mongo.rows.aggregate(union))


@integration
def test_correlated_lookup_index_preserves_missing_null_and_scalar_equality(mongo):
    mongo.rows.insert_many([{"_id": 1, "owner": "one"}, {"_id": 2, "owner": None}, {"_id": 3}])
    mongo.drafts.insert_many([{"_id": 1, "user_id": "one"}, {"_id": 2, "user_id": None}, {"_id": 3},
                              {"_id": 4, "user_id": ["one"]}])
    pipeline = [{"$lookup": {"from": "drafts", "let": {"owner": "$owner"}, "pipeline": [
        {"$match": {"$expr": {"$eq": ["$user_id", "$$owner"]}}}, {"$sort": {"_id": 1}}
    ], "as": "drafts"}}, {"$sort": {"_id": 1}}]
    foreign = list(mongo.drafts.find({}))
    expected = list(mongo.rows.aggregate(pipeline))
    assert q.aggregate_documents(list(mongo.rows.find({})), pipeline, lookup=lambda _: foreign) == expected


@integration
def test_couples_checkin_and_activity_expressions_match_actual_mongo(mongo):
    from backend import couple_research, couples_analytics
    checks = [
        [{"result": "helped", "updated_at": "2026-11-10T12:00:00.123456Z"}],
        [{"result": "helped", "updated_at": "2026-11-10T12:00:00Z"},
         {"result": "mixed", "updated_at": "2026-11-10T13:00:00+01:00"}],
        [{"result": "mixed"}, {"result": "helped", "updated_at": "2026-11-10T12:00:00Z"}],
        [{"result": "mixed", "updated_at": "invalid"}, {"result": "not_tried", "updated_at": "2026-11-10"}],
        [{"result": "didnt_help", "updated_at": datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)}],
    ]
    for index, entries in enumerate(checks):
        mongo.rows.insert_one({"_id": index, "state": "active", "journey": {"threads": {
            "one": {"kind": "prompt", "prompt_id": "care", "shares": {"a": [1], "b": [2]}, "agreements": [
                {"checkins": {"a": {**item, "shared": True}, "b": {"result": "mixed", "shared": True}},
                 "pin": {"accepted": ["a", "b"]}} for item in entries]},
            "private": {"kind": "moment", "shares": {"a": [1], "b": []}, "agreements": []},
        }}})
    projection = {"_id": 1, "code": couple_research.checkin_code("$$ROOT", {"$literal": "a"}, "any"),
                  "activities": couples_analytics.shared_activities_projection()}
    expected = list(mongo.rows.aggregate([{"$project": projection}, {"$sort": {"_id": 1}}]))
    actual = q.aggregate_documents(list(mongo.rows.find({})), [{"$project": projection}, {"$sort": {"_id": 1}}])
    assert [row["code"] for row in actual] == [row["code"] for row in expected]
    assert all(set(a["activities"]) == set(b["activities"]) for a, b in zip(actual, expected))


@integration
def test_current_numeric_and_categorical_research_outputs_match_all_four_units(mongo):
    from motor.motor_asyncio import AsyncIOMotorClient
    from backend import numeric_research, research_trends
    from backend.quiz import default_quiz

    class Cursor:
        def __init__(self, rows): self.rows = rows
        async def to_list(self, length): return self.rows[:length] if length is not None else self.rows
        def __aiter__(self):
            async def iterate():
                for row in self.rows: yield row
            return iterate()

    class Collection:
        def __init__(self, data, name): self.data, self.name = data, name
        def aggregate(self, pipeline, **kwargs):
            return Cursor(q.aggregate_documents(self.data.get(self.name, []), pipeline,
                          lookup=lambda name: self.data.get(name, [])))

    class MemoryDB:
        def __init__(self, data): self.data = data
        def __getattr__(self, name): return Collection(self.data, name)

    async def exercise():
        client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
        try:
            db = client[mongo.name]
            quiz = default_quiz()
            state = await research_trends.catalog(db, quiz)
            variables = state[2]
            x, y = [key for key, variable in variables.items() if variable["domain"] == "intimacy"][:2]
            signatures = {key: variables[key]["signature"] for key in (x, y)}
            stamp = datetime.now(timezone.utc).isoformat()
            members, pairs, intimacy = [], [], []
            for i in range(120):
                group = i//30
                age, height = (22, 32, 42, 52)[group], (155, 175, 155, 175)[group]
                x_code, y_code = divmod(group, 2)
                uids = [f"person-{i}-a", f"person-{i}-b"]
                pairs.append({"_id": f"pair-{i}", "inviter": uids[0], "invitee": uids[1], "members": uids,
                              "state": "active", "env": "fixture"})
                for side, uid in enumerate(uids):
                    profile = {"age": age+side, "height_cm": height+side}
                    if i%30 == 0 and side == 0: profile.pop("age")
                    if i%10 == 0 and side == 1: profile.pop("height_cm")
                    member = {"_id": uid, "user_id": uid, "role": "member", "env": "fixture",
                              "about": {"demographics": profile}, "couples_adult_at": stamp,
                              "questionnaire_research_version": "all-answers-v1"}
                    if i%20 == 5 and side == 1: member.pop("questionnaire_research_version")
                    members.append(member)
                    intimacy.append({"_id": uid, "user_id": uid,
                        "values": {x: x_code, y: None if i%20 == 4 else y_code},
                        "signatures": {x: "old-edition" if i%20 == 3 else signatures[x], y: signatures[y]}})
            await db.users.insert_many(members)
            await db.couples_pairs.insert_many(pairs)
            await db.intimacy_answers.insert_many(intimacy)
            memory = MemoryDB({name: list(mongo[name].find({})) for name in mongo.list_collection_names()})
            axes = {"x": (questionnaire_report.AGE, None), "y": (questionnaire_report.HEIGHT, None)}
            categories = {"x": (x, None), "y": (y, None)}
            for unit in ("member", "couple", "couple_inviter", "couple_invitee"):
                expected = await numeric_research.correlation(db, {"env": "fixture"}, quiz, variables, axes, unit, 50, 10, 5)
                actual = await numeric_research.correlation(memory, {"env": "fixture"}, quiz, variables, axes, unit, 50, 10, 5)
                assert actual == expected, (unit, actual, expected)
                expected = await research_trends.comparisons(db, {"env": "fixture"}, quiz, state, categories, unit)
                actual = await research_trends.comparisons(memory, {"env": "fixture"}, quiz, state, categories, unit)
                assert actual == expected, (unit, actual, expected)
        finally: client.close()
    asyncio.run(exercise())
