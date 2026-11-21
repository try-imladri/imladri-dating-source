"""Shadow migration invariants and real disposable Mongo/PostgreSQL parity."""
import asyncio
from datetime import datetime, timezone
import json
import os
import ipaddress
from urllib.parse import urlsplit
from uuid import uuid4

from bson import BSON, Int64, ObjectId
import pytest

from backend import postgres_migration as pg


def test_bson_identity_keeps_types_and_order_instead_of_stringifying_ids():
    oid = ObjectId()
    assert pg.document_key({"_id": oid}) != pg.document_key({"_id": str(oid)})
    assert pg.document_key({"_id": 3}) != pg.document_key({"_id": Int64(3)})
    assert pg.document_key({"_id": {"a": 1, "b": 2}}) != pg.document_key({"_id": {"b": 2, "a": 1}})
    assert pg.document_hash({"_id": 1, "x": 1}) != pg.document_hash({"_id": 1, "x": Int64(1)})


def test_jsonb_incompatible_strings_still_have_lossless_bson_and_text():
    doc = {"_id": "nul", "value": "a\x00b"}
    assert pg.jsonb_text(doc) is None
    assert BSON(pg.raw_document(doc)).decode() == doc
    assert "\\u0000" in pg.canonical_document(doc)
    assert pg.jsonb_text({"_id": "literal", "value": "a\\u0000b"}) is not None


def test_shadow_dsn_requires_verified_tls_except_explicit_loopback_fixture():
    options = pg.connection_options("postgresql://fixture:p%40ss@example.invalid/research?sslmode=require")
    assert options["password"] == "p@ss"
    assert options["ssl_context"].check_hostname
    assert options["ssl_context"].verify_mode.name == "CERT_REQUIRED"
    with pytest.raises(ValueError):
        pg.connection_options("postgresql://u:p@example.invalid/db?sslmode=disable", allow_local_plaintext=True)
    with pytest.raises(ValueError):
        pg.connection_options("postgresql://u:p@127.0.0.1/db?sslmode=disable")
    assert pg.connection_options("postgresql://u:p@127.0.0.1/db?sslmode=disable", allow_local_plaintext=True)["ssl_context"] is False
    with pytest.raises(ValueError):
        pg.connection_options("postgresql://u:p@localhost/db?sslmode=prefer")


def test_public_report_never_grants_cutover_or_exposes_arbitrary_payloads():
    report = pg.public_report({"status": "observed_parity_only", "cutover_eligible": True, "mongo_authoritative": False,
                              "dsn": "secret", "raw_answers": ["private"], "error": "private",
                              "collections": [{"name": "users", "source_count": 3, "raw_ids": ["person"]}],
                              "projections": {"participants": 3, "answers": "private"}})
    assert report["cutover_eligible"] is False and report["mongo_authoritative"] is True
    assert not any(s in json.dumps(report) for s in ("secret", "private", "person"))
    assert report["collections"] == [{"name": "users", "source_count": 3}]


def test_known_domains_cover_customer_couples_billing_admin_and_new_history():
    assert pg.domain_for_collection("users") == "individual"
    assert pg.domain_for_collection("couples_pairs") == "couples"
    assert pg.domain_for_collection("payment_transactions") == "billing"
    assert pg.domain_for_collection("settings") == "admin"
    assert pg.domain_for_collection("answer_history") == "research"
    assert pg.domain_for_collection("new_unreviewed_collection") == "unclassified"


def test_observations_normalize_known_codes_without_inventing_values():
    event = {"domain": "intimacy", "question_id": "i:x:0", "signature": "edition", "after": {"choice": 0, "skipped": False}}
    assert pg.observation_rows(event) == [{"question_id": "i:x:0", "edition": "edition", "answer_code": 0, "skipped": False}]
    event["after"] = {"choice": None, "skipped": True}
    assert pg.observation_rows(event)[0]["skipped"] is True
    assert pg.observation_rows({"domain": "demographics", "after": {"age": 30}}) == []
    categorical = {"domain": "couples", "question_id": "prompt:one", "signature": "published-edition",
                   "question": {"choices": ["Listening", "A clear plan"]}, "after": {"choice": "Listening"}}
    assert pg.observation_rows(categorical)[0]["answer_code"] == 0
    categorical["after"]["choice"] = "Unknown option"
    assert pg.observation_rows(categorical)[0]["answer_code"] is None
    categorical["after"]["choice"] = "Listening"
    categorical["question"]["choices"] = ["Listening", "Listening"]
    assert pg.observation_rows(categorical)[0]["answer_code"] is None
    categorical["question"] = "Unstructured wording without published categories"
    assert pg.observation_rows(categorical)[0]["answer_code"] is None
    event = {"domain": "research", "after": {"values": {"e:a": 2, "i:b": None}, "signatures": {"e:a": "v1"}}}
    assert pg.observation_rows(event) == [{"question_id": "e:a", "edition": "v1", "answer_code": 2, "skipped": False},
                                          {"question_id": "i:b", "edition": None, "answer_code": None, "skipped": True}]


PG_DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
MONGO_URI = os.getenv("IMLADRI_TEST_MONGO_URL")
integration = pytest.mark.skipif(not (PG_DSN and MONGO_URI), reason="Requires isolated loopback MongoDB and PostgreSQL fixtures.")


def event(eid, uid="one", *, domain="intimacy", snapshot="demo", pair=None):
    return {"_id": eid, "id": eid, "source": "intimacy_answers", "source_id": uid, "owner_id": uid,
            "domain": domain, "question_id": "i:x:0", "after": {"choice": 0, "answer": "Interested", "conditions": [], "skipped": False},
            "question": "Fixture published wording", "signature": "fixture-edition", "demographics": {"snapshot_id": snapshot},
            "consent": {"questionnaire_research_version": "all-answers-v1", "env": "fixture", "role": "member", "closing": False},
            "revision": 1, "attempt_id": "attempt-one", "pair_id": pair, "actor_id": uid,
            "visibility": "private", "kind": "answer_changed", "at": datetime.now(timezone.utc).isoformat(), "baseline": False}


async def with_database(exercise):
    from motor.motor_asyncio import AsyncIOMotorClient
    from scripts.migrate_postgres import loopback_mongo
    assert loopback_mongo(MONGO_URI), "Tests must never read a production MongoDB."
    host = urlsplit(PG_DSN).hostname
    assert host == "localhost" or ipaddress.ip_address(host).is_loopback, "Tests require loopback PostgreSQL."
    pg.connection_options(PG_DSN, allow_local_plaintext=True)
    namespace = "fixture-" + uuid4().hex
    client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client["imladri_pg_test_" + uuid4().hex]
    target = pg.PostgresShadow.connect(PG_DSN, allow_local_plaintext=True)
    target.initialize()
    try:
        await exercise(db, target, namespace)
    finally:
        target.connection.rollback()
        for table in ("answer_observations", "demographic_snapshots", "history_events", "couples", "participants", "billing_records",
                      "history_tombstones", "checkpoints", "documents", "runs"):
            target.execute("DELETE FROM imladri_shadow." + table + " WHERE namespace=%s", (namespace,))
        target.connection.commit()
        target.close()
        await client.drop_database(db.name)
        client.close()


@integration
def test_real_shadow_cross_domain_roundtrip_idempotency_history_and_consent():
    async def exercise(db, target, namespace):
        await db.users.insert_many([{"_id": ObjectId(), "user_id": uid, "env": "fixture", "role": "member",
                                    "about": {"demographics": {"age": 30, "height_cm": 175}},
                                    "couples_adult_at": datetime.now(timezone.utc).isoformat(),
                                    "questionnaire_research_version": "all-answers-v1", "membership_plan_id": "plus_100", "topup_balance": 100}
                                   for uid in ("one", "two")])
        await db.couples_pairs.insert_one({"_id": "pair", "inviter": "one", "invitee": "two", "members": ["one", "two"], "state": "active", "env": "fixture"})
        await db.payment_transactions.insert_one({"_id": "payment", "user_id": "one", "kind": "purchase", "payment_status": "paid", "currency": "usd", "amount": 12, "subscription_revenue_cents": 1200})
        await db.settings.insert_one({"_id": "config", "key": "global", "prompts": {"private": "fixture only"}})
        demo = event("demo", domain="demographics")
        demo.update(source="users", question_id="demographics", after={"age": 30, "height_cm": 175, "gender": "fixture"})
        never_consented = event("never-consented")
        never_consented["consent"] = {}
        excluded = event("explicitly-excluded")
        excluded["research_eligible"] = False
        mislabeled_third_party = event("third-party")
        mislabeled_third_party["observation_subject"] = "third_party"
        await db.answer_history.insert_many([demo, event("answer"), event("couple-answer", pair="pair"),
                                            event("inferred", domain="person_intake"), event("private", domain="profile"),
                                            event("wording", domain="wording_feedback"), event("repeat", domain="assessment_repeat"),
                                            never_consented, excluded, mislabeled_third_party])
        first = await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert first["status"] == "observed_parity_only" and first["observed_parity"] and not first["cutover_eligible"]
        assert first["projections"]["participants"] == 2 and first["projections"]["couples"] == 1
        assert first["projections"]["billing_records"] == 1 and first["projections"]["history_events"] == 10
        assert target.execute("SELECT amount_cents FROM imladri_shadow.billing_records WHERE namespace=%s", (namespace,)).fetchone()[0] == 1200
        assert target.execute("SELECT count(*) FROM imladri_shadow.research_observations WHERE namespace=%s", (namespace,)).fetchone()[0] == 2
        repeat = await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert repeat["observed_parity"] and repeat["projections"] == first["projections"]
        # Exact BSON roundtrip, including ObjectId and int types, not JSON-only copying.
        original = await pg.source_collection(db, "users").find_one({"user_id": "one"})
        stored = target.execute("SELECT raw_bson FROM imladri_shadow.documents WHERE namespace=%s AND collection='users' AND id_key=%s",
                                (namespace, pg.document_key(original))).fetchone()[0]
        assert bytes(stored) == original.raw
        await db.users.update_one({"user_id": "one"}, {"$set": {"assessment_repeat.publish_group_results": True}})
        await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert target.execute("SELECT count(*) FROM imladri_shadow.research_observations WHERE namespace=%s", (namespace,)).fetchone()[0] == 3
        await db.users.update_one({"user_id": "one"}, {"$set": {"assessment_repeat.publish_group_results": False}})
        await db.users.update_one({"user_id": "two"}, {"$unset": {"questionnaire_research_version": ""}})
        await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert target.execute("SELECT count(*) FROM imladri_shadow.research_observations WHERE namespace=%s", (namespace,)).fetchone()[0] == 1
        await db.users.update_one({"user_id": "one"}, {"$unset": {"questionnaire_research_version": ""}})
        await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert target.execute("SELECT count(*) FROM imladri_shadow.research_observations WHERE namespace=%s", (namespace,)).fetchone()[0] == 0
        await db.users.update_one({"user_id": "one"}, {"$set": {"questionnaire_research_version": "all-answers-v1"}})
        await db.answer_history.replace_one({"_id": "demo"}, {"_id": "demo", "id": "demo", "purged": True})
        await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert target.execute("SELECT count(*) FROM imladri_shadow.demographic_snapshots WHERE namespace=%s", (namespace,)).fetchone()[0] == 0
        assert target.execute("SELECT count(*) FROM imladri_shadow.research_observations WHERE namespace=%s", (namespace,)).fetchone()[0] == 0
    asyncio.run(with_database(exercise))


@integration
def test_real_mixed_environment_is_preserved_raw_but_never_relabelled(monkeypatch):
    async def exercise(db, target, namespace):
        await db.users.insert_many([{"_id": "one", "user_id": "one", "role": "member", "env": "fixture", "about": {"demographics": {"age": 30}}},
                                    {"_id": "other", "user_id": "other", "role": "member", "env": "foreign", "about": {"demographics": {"age": 30}}}])
        await db.payment_transactions.insert_one({"_id": "foreign-payment", "user_id": "other", "env": "foreign", "kind": "purchase", "amount": 12, "currency": "usd"})
        await db.settings.insert_one({"_id": "bson-only", "text": "NUL\x00is valid BSON"})
        report = await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert report["observed_parity"] is True and report["status"] == "needs_reconciliation"
        assert report["projections"]["participants"] == 1 and report["projections"]["billing_records"] == 0
        assert report["projections"]["out_of_scope_records"] == 2 and report["jsonb_unavailable_records"] == 1
        assert target.stored_count(namespace, "users") == 2 and target.stored_count(namespace, "payment_transactions") == 1
        with pytest.raises(ValueError):
            await pg.reconcile(db, target, namespace=namespace, environment="foreign")
    asyncio.run(with_database(exercise))


@integration
def test_real_pending_tombstones_redactions_deletion_and_unknown_collections():
    async def exercise(db, target, namespace):
        member = {"_id": "member", "user_id": "one", "env": "fixture", "role": "member", "about": {"demographics": {"age": 30}}}
        await db.users.insert_one(member)
        pending = event("pending")
        await db.intimacy_answers.insert_one({"_id": "one", "user_id": "one", "answer_history_pending": [pending]})
        report = await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert report["pending_events"] == 1 and report["status"] == "needs_reconciliation"
        assert report["projections"]["history_events"] == 1
        # A central opaque erasure wins even when the same accepted outbox is still present.
        await db.answer_history.insert_one({"_id": "pending", "id": "pending", "purged": True, "purged_at": datetime.now(timezone.utc).isoformat()})
        report = await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert report["projections"].get("history_events", 0) == 0 and not report["observed_parity"]
        raw = target.execute("SELECT raw_bson FROM imladri_shadow.documents WHERE namespace=%s AND collection='intimacy_answers'", (namespace,)).fetchone()[0]
        assert BSON(bytes(raw)).decode()["answer_history_pending"] == []
        await db.intimacy_answers.update_one({"_id": "one"}, {"$set": {"answer_history_pending": []}})
        await db.answer_history.insert_one(event("private"))
        await db.users.update_one({"_id": "member"}, {"$set": {"answer_history_redactions": [{"id": "erasure", "through_generation": 1}]}})
        report = await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert report["unresolved_redactions"] == 1 and not report["observed_parity"]
        assert target.execute("SELECT count(*) FROM imladri_shadow.documents WHERE namespace=%s AND collection='answer_history' AND document->>'owner_id'='one'", (namespace,)).fetchone()[0] == 0
        # Removed collections/records are swept; unknown collections are mirrored but cannot pass coverage.
        await db.users.delete_many({})
        await db.intimacy_answers.drop()
        await db.new_future_domain.insert_one({"_id": "unknown", "value": 1})
        report = await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert report["projections"].get("participants", 0) == 0
        assert target.stored_count(namespace, "intimacy_answers") == 0
        assert report["unknown_collections"] == ["new_future_domain"] and report["status"] == "needs_reconciliation"
        await pg.publish_report(db, report)
        published = await db.database_migration_reports.find_one({})
        assert published["cutover_eligible"] is False
        assert not any(key in published for key in ("dsn", "password", "raw_bson", "raw_answers"))
    asyncio.run(with_database(exercise))


@integration
def test_real_source_drift_is_detected_and_failed_scans_roll_back(monkeypatch):
    async def exercise(db, target, namespace):
        await db.users.insert_one({"_id": "one", "user_id": "one", "role": "member", "env": "fixture", "about": {"demographics": {"age": 30}}})
        original = pg.source_rows
        calls = Counter()
        async def changing(source, name):
            calls[name] += 1
            async for row in original(source, name):
                yield row
            if name == "users" and calls[name] == 1:
                await source.users.update_one({"_id": "one"}, {"$set": {"about.demographics.age": 31}})
        monkeypatch.setattr(pg, "source_rows", changing)
        report = await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert not report["observed_parity"] and report["cutover_eligible"] is False
        monkeypatch.setattr(pg, "source_rows", original)
        report = await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert report["observed_parity"]
        prior = target.execute("SELECT stored_sha256 FROM imladri_shadow.documents WHERE namespace=%s AND collection='users'", (namespace,)).fetchone()[0]
        await db.users.update_one({"_id": "one"}, {"$set": {"about.demographics.age": 32}})
        async def failure(source, name):
            async for row in original(source, name):
                yield row
            if name == "users":
                raise RuntimeError("Fixture source scan failure")
        monkeypatch.setattr(pg, "source_rows", failure)
        with pytest.raises(RuntimeError):
            await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert target.execute("SELECT stored_sha256 FROM imladri_shadow.documents WHERE namespace=%s AND collection='users'", (namespace,)).fetchone()[0] == prior
    from collections import Counter
    asyncio.run(with_database(exercise))


@integration
def test_real_retired_study_collections_keep_exact_raw_bytes_without_research_projection():
    async def exercise(db, target, namespace):
        await db.users.insert_one({"_id": "one", "user_id": "one", "env": "fixture", "role": "member",
                                   "questionnaire_research_version": "all-answers-v1", "about": {"demographics": {"age": 30}}})
        demographic = event("demo", domain="demographics")
        demographic.update(source="users", question_id="demographics", after={"age": 30, "height_cm": 175})
        await db.answer_history.insert_one(demographic)
        for name in sorted(pg.HISTORICAL_RAW_ONLY_COLLECTIONS):
            # Even an answer-shaped historical record must never become an
            # eligible event merely because its legacy collection is mirrored.
            historical = event("retired-" + name)
            historical.update(opaque_bson={"object_id": ObjectId(), "counter": Int64(42), "bytes": b"\x00\xff"})
            await db[name].insert_one(historical)
        report = await pg.reconcile(db, target, namespace=namespace, environment="fixture")
        assert report["status"] == "observed_parity_only" and report["unknown_collections"] == []
        assert report["domains"]["admin"]["source_records"] == report["domains"]["admin"]["target_records"] == 2
        assert report["projections"]["history_events"] == report["projections"]["demographic_snapshots"] == 1
        assert report["projections"]["answer_observations"] == 0
        assert target.execute("SELECT count(*) FROM imladri_shadow.research_observations WHERE namespace=%s", (namespace,)).fetchone()[0] == 0
        for name in pg.HISTORICAL_RAW_ONLY_COLLECTIONS:
            original = await pg.source_collection(db, name).find_one({})
            raw = target.execute("SELECT raw_bson FROM imladri_shadow.documents WHERE namespace=%s AND collection=%s AND id_key=%s",
                                 (namespace, name, pg.document_key(original))).fetchone()[0]
            assert bytes(raw) == original.raw
            assert await db[name].count_documents({}) == 1
        # No runtime module outside the migration registry names these retired
        # collections; mirroring them does not recreate an API/UI workflow.
        import ast
        from pathlib import Path
        consumers = []
        for path in Path(pg.__file__).parent.glob("*.py"):
            if path.name == "postgres_migration.py": continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in pg.HISTORICAL_RAW_ONLY_COLLECTIONS:
                    consumers.append((path.name, node.lineno))
        assert consumers == []
    asyncio.run(with_database(exercise))
