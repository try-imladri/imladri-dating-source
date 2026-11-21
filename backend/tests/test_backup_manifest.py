"""Verify backup manifests against disposable loopback collections and views."""
import os
from unittest import SkipTest
from urllib.parse import urlsplit
from uuid import uuid4

from pymongo import MongoClient

from scripts.backup_personal import manifest


def test_restored_data_indexes_and_view_definitions_match_independently():
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        raise SkipTest("A loopback MongoDB is required")
    assert urlsplit(uri).hostname in ("127.0.0.1", "localhost", "::1")
    with MongoClient(uri) as client:
        databases = [client["manifest_fixture_" + uuid4().hex] for _ in range(2)]
        source, restored = databases
        try:
            for db in databases:
                db.items.insert_many([{"_id": 1, "rank": 1}, {"_id": 2, "rank": 2}])
                db.items.create_index([("rank", 1), ("_id", -1)], name="rank_order")
                db.command("create", "ranked", viewOn="items", pipeline=[{"$sort": {"rank": 1, "_id": -1}}])
            before = manifest(source)
            assert before == manifest(restored)
            assert before["ranked"]["type"] == "view" and before["ranked"]["count"] == 0
            restored.command("collMod", "ranked", viewOn="items", pipeline=[{"$sort": {"_id": -1, "rank": 1}}])
            assert before["ranked"]["digest"] != manifest(restored)["ranked"]["digest"]
            restored.items.drop_index("rank_order")
            restored.items.create_index([("_id",-1), ("rank",1)], name="rank_order")
            modified = manifest(restored)
            assert before["items"]["indexes"] != modified["items"]["indexes"]
            assert before["items"]["digest"] == modified["items"]["digest"]
            restored.items.update_one({"_id": 2}, {"$set": {"rank": 3}})
            modified = manifest(restored)
            assert before["items"]["count"] == modified["items"]["count"]
            assert before["items"]["digest"] != modified["items"]["digest"]
        finally:
            for db in databases:
                client.drop_database(db.name)
