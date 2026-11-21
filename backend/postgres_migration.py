"""Opt-in PostgreSQL shadow migration. No application datasource switch exists.

MongoDB is read-only apart from an explicitly requested metadata report publish.
Canonical Extended JSON is inspectable, while BSON bytes preserve types, field
order, binary values and JSONB-incompatible strings. Sensitive values never enter
reports. A successful scan is observed parity, not a global live-write snapshot.
"""
from collections import Counter
from contextlib import aclosing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import ipaddress
import json
import re
import ssl
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

from bson import BSON, json_util
from bson.codec_options import CodecOptions
from bson.raw_bson import RawBSONDocument

from . import quiz_answers


SCHEMA_VERSION = 1
REPORT_VERSION = 1
RESEARCH_VERSION = "all-answers-v1"
JOURNAL_SOURCES = ("users", "intimacy_answers", "research_responses", "couples_drafts", "couples_pairs", "people", "your_type_sessions")
# Retired study workflows stay removed. Preserve existing records only in the
# lossless administrative mirror; never infer questionnaire/research events.
HISTORICAL_RAW_ONLY_COLLECTIONS = frozenset({"field_study_links", "volunteer_studies"})
COLLECTION_DOMAINS = {
    **dict.fromkeys(("dating_enrollments", "dating_profiles", "dating_preferences", "dating_photos",
                     "dating_interests", "dating_matches", "dating_messages", "dating_blocks", "dating_shares",
                     "dating_feedback", "dating_reports", "dating_appeals", "dating_events", "dating_rate",
                     "dating_settings", "dating_moderation"), "dating"),
    **dict.fromkeys(sorted(HISTORICAL_RAW_ONLY_COLLECTIONS), "admin"),
    **dict.fromkeys(("users", "people", "memories", "exchanges", "corrections", "practice", "preps", "patterns",
                     "chat_groups", "group_chats", "group_starters", "notifications", "your_type_sessions", "your_type_profiles", "quick_advice"), "individual"),
    **dict.fromkeys(("couples_pairs", "couples_drafts", "couples_settings", "couples_prompt_versions"), "couples"),
    **dict.fromkeys(("payment_transactions", "membership_history", "closed_membership_revenue", "message_cost_units",
                     "ai_call_ledger", "ai_billing_days", "ai_daily_usage", "ai_request_usage", "ai_intro_daily_usage"), "billing"),
    **dict.fromkeys(("answer_history", "intimacy_answers", "research_responses", "research_catalogs",
                     "research_registrations", "questionnaire_scan_plans"), "research"),
    **dict.fromkeys(("announcements", "archetypes", "figures", "media", "settings", "catalog_wave_releases",
                     "events", "intimacy_question_copy", "prompt_candidates", "prompt_meter", "prompt_reviews",
                     "prompt_usage", "support_rate", "support_requests", "ops_state", "waitlist",
                     "independent_ballots", "rehearsal_ballots", "intro_controls", "intro_identities",
                     "intro_attempt_usage", "admin_answer_history_access"), "admin"),
    "database_migration_reports": "migration_control",
}
# Derived migration metadata is deliberately not recursively mirrored into itself.
EXCLUDED_COLLECTIONS = {"database_migration_reports": "Derived migration status, not an application record."}
SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}$")
FINGERPRINT_MODULUS = 1 << 256


def utc_now():
    return datetime.now(timezone.utc)


def validate_name(value):
    if not isinstance(value, str) or not SAFE_NAME.fullmatch(value):
        raise ValueError("Expected a bounded migration namespace or collection identifier.")
    return value


def domain_for_collection(collection):
    return COLLECTION_DOMAINS.get(collection, "unclassified")


def canonical_document(document):
    return json_util.dumps(document, json_options=json_util.CANONICAL_JSON_OPTIONS,
                           ensure_ascii=True, separators=(",", ":"))


def document_key(document):
    if "_id" not in document:
        raise ValueError("MongoDB source records must have an _id.")
    # Object-id order and int32/int64 are meaningful BSON identity information.
    return hashlib.sha256(BSON.encode({"_id": document["_id"]})).hexdigest()


def raw_document(document):
    return document.raw if isinstance(document, RawBSONDocument) else BSON.encode(document)


def document_hash(document):
    return hashlib.sha256(raw_document(document)).hexdigest()


def jsonb_compatible(value):
    if isinstance(value, str):
        return "\x00" not in value
    if isinstance(value, dict):
        return all(jsonb_compatible(k) and jsonb_compatible(v) for k, v in value.items())
    if isinstance(value, list):
        return all(jsonb_compatible(v) for v in value)
    return True


def jsonb_text(document):
    text = canonical_document(document)
    return text if jsonb_compatible(json.loads(text)) else None


def fingerprint_add(total, identity, digest):
    return (total + int(hashlib.sha256((identity + ":" + digest).encode()).hexdigest(), 16)) % FINGERPRINT_MODULUS


def fingerprint_finish(total, count):
    return hashlib.sha256(f"bson-multiset-v1:{count}:{total:064x}".encode()).hexdigest()


def safe_int(value):
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and -(2**63) <= value < 2**63 else None


def measurement(value, lower, upper):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    digits = str(value).strip()
    if digits.isascii() and digits.isdigit() and 1 <= len(digits) <= 3 and lower <= int(digits) <= upper:
        return int(digits)
    return None


def adult_eligible(value):
    # Same optional-age rule as questionnaire_report.adult_filter; enrollment
    # itself separately requires the member's adult confirmation and consent.
    if value in (None, ""):
        return True
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        return False
    digits = str(value)
    return digits.isascii() and digits.isdigit() and 1 <= len(digits) <= 3 and 18 <= int(digits) <= 120


def native_number(value):
    return isinstance(value, int) and not isinstance(value, bool)


def text(value):
    return value if isinstance(value, str) and "\x00" not in value else None


def decimal_value(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (ValueError, InvalidOperation):
        return None


def observation_rows(event):
    """Normalize only explicitly known formats, retaining everything in BSON."""
    after = event.get("after")
    if event.get("domain") == "demographics":
        return []
    if event.get("domain") == "research" and isinstance(after, dict) and isinstance(after.get("values"), dict):
        return [{"question_id": key, "edition": text((after.get("signatures") or {}).get(key)),
                 "answer_code": safe_int(value), "skipped": value is None}
                for key, value in after["values"].items() if text(key)]
    if not text(event.get("question_id")):
        return []
    code = after.get("choice") if isinstance(after, dict) else after
    if isinstance(code, str):
        question = event.get("question")
        published = question.get("choices") if isinstance(question, dict) else None
        if (isinstance(published, list) and all(isinstance(item, str) for item in published)
                and len(set(published)) == len(published) and code in published):
            # This is a category identifier within its frozen edition, not an
            # invented numerical scale or an input to numeric correlation.
            code = published.index(code)
    return [{"question_id": event["question_id"], "edition": text(event.get("signature")),
             "answer_code": safe_int(code),
             "skipped": ((after.get("skipped") is True or after.get("state") == "skipped" or after.get("quick_skip") is True)
                         if isinstance(after, dict) else after is None)}]


def connection_options(dsn, *, allow_local_plaintext=False):
    url = urlsplit(dsn)
    if url.scheme not in ("postgres", "postgresql") or not url.hostname or not url.username or not url.path.strip("/"):
        raise ValueError("Expected a PostgreSQL DSN with a database and user.")
    options = parse_qs(url.query)
    if set(options) - {"sslmode"} or any(len(v) != 1 for v in options.values()):
        raise ValueError("Unsupported PostgreSQL DSN options.")
    mode = options.get("sslmode", ["verify-full"])[0]
    try:
        local = ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        local = url.hostname == "localhost"
    if mode == "disable":
        if not (allow_local_plaintext and local):
            raise ValueError("Plaintext PostgreSQL is restricted to explicitly allowed loopback fixtures.")
        context = False
    elif mode in ("verify-full", "require"):
        # Never turn certificate/hostname verification off, including sslmode=require.
        context = ssl.create_default_context()
    else:
        raise ValueError("PostgreSQL requires verified TLS or an explicit loopback fixture.")
    return {"host": url.hostname, "port": url.port or 5432, "user": unquote(url.username),
            "password": unquote(url.password or ""), "database": unquote(url.path[1:]),
            "ssl_context": context, "timeout": 65, "application_name": "imladri-postgres-shadow"}


class PostgresShadow:
    """Synchronous DB-API adapter; use in a dedicated worker, not ASGI requests."""
    def __init__(self, connection):
        self.connection = connection
        self.cursor = connection.cursor()

    @classmethod
    def connect(cls, dsn, *, allow_local_plaintext=False):
        options = connection_options(dsn, allow_local_plaintext=allow_local_plaintext)
        import pg8000.dbapi
        return cls(pg8000.dbapi.connect(**options))

    def close(self):
        try:
            self.cursor.close()
        finally:
            self.connection.close()

    def execute(self, sql, args=()):
        self.cursor.execute(sql, args)
        return self.cursor

    def initialize(self):
        schema = Path(__file__).with_name("postgres_schema.sql").read_text(encoding="utf8")
        # The checked-in schema contains no procedural functions or quoted semicolons.
        clean = "\n".join(line.split("--", 1)[0] for line in schema.splitlines())
        try:
            for statement in clean.split(";"):
                if statement.strip():
                    self.execute(statement)
            # The server deadline must fire before the driver's socket deadline.
            self.execute("SET statement_timeout='55s'")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def begin(self, run_id, namespace, environment):
        self.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("imladri-shadow:" + namespace,))
        self.execute("SET LOCAL statement_timeout='55s'")
        prior = self.execute("SELECT DISTINCT environment FROM imladri_shadow.runs WHERE namespace=%s", (namespace,)).fetchall()
        if any(row[0] != environment for row in prior):
            self.connection.rollback()
            raise ValueError("A shadow namespace cannot silently change its environment.")
        self.execute("INSERT INTO imladri_shadow.runs(run_id,namespace,environment,status) VALUES(%s,%s,%s,'running')",
                     (run_id, namespace, environment))

    def existing_collections(self, namespace):
        return [row[0] for row in self.execute("SELECT DISTINCT collection FROM imladri_shadow.documents WHERE namespace=%s",
                                             (namespace,)).fetchall()]

    def tombstones(self, namespace):
        return {row[0] for row in self.execute("SELECT event_id FROM imladri_shadow.history_tombstones WHERE namespace=%s",
                                             (namespace,)).fetchall()}

    def mark_tombstone(self, namespace, event_id):
        self.execute("INSERT INTO imladri_shadow.history_tombstones VALUES(%s,%s) ON CONFLICT DO NOTHING",
                     (namespace, event_id))
        self.execute("DELETE FROM imladri_shadow.history_events WHERE namespace=%s AND event_id=%s", (namespace, event_id))
        # Old outbox snapshots cannot reintroduce a purged event through a raw mirror.
        self.execute("DELETE FROM imladri_shadow.documents WHERE namespace=%s AND collection<>'answer_history' "
                     "AND document->'answer_history_pending' @> %s::jsonb", (namespace, json.dumps([{"id": event_id}])))

    def put(self, namespace, collection, document, run_id, tombstones):
        source_raw = raw_document(document)
        source_hash = hashlib.sha256(source_raw).hexdigest()
        # Copy through BSON so a source RawBSONDocument cannot be mutated.
        stored = BSON(source_raw).decode()
        pending = stored.get("answer_history_pending") or []
        redactions = stored.get("answer_history_redactions") or []
        filtered = [e for e in pending if isinstance(e, dict) and e.get("id") not in tombstones]
        private_redaction = bool(redactions or len(filtered) != len(pending))
        if redactions:
            filtered = []
        if "answer_history_pending" in stored:
            stored["answer_history_pending"] = filtered
        if collection == "answer_history" and not stored.get("purged") and (stored.get("id") or stored.get("_id")) in tombstones:
            # The authoritative opaque tombstone takes precedence over stale payloads.
            self.execute("DELETE FROM imladri_shadow.documents WHERE namespace=%s AND collection=%s AND id_key=%s",
                         (namespace, collection, document_key(document)))
            return {"privacy_redacted": 1, "jsonb_only_unavailable": 0}
        stored_raw = BSON.encode(stored) if private_redaction else source_raw
        stored_doc = BSON(stored_raw).decode()
        query_json = jsonb_text(stored_doc)
        self.execute("INSERT INTO imladri_shadow.documents(namespace,collection,id_key,id_bson,raw_bson,canonical_json,document,"
                     "source_sha256,stored_sha256,privacy_redacted,seen_run) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s) "
                     "ON CONFLICT(namespace,collection,id_key) DO UPDATE SET id_bson=EXCLUDED.id_bson,raw_bson=EXCLUDED.raw_bson,"
                     "canonical_json=EXCLUDED.canonical_json,document=EXCLUDED.document,source_sha256=EXCLUDED.source_sha256,"
                     "stored_sha256=EXCLUDED.stored_sha256,privacy_redacted=EXCLUDED.privacy_redacted,seen_run=EXCLUDED.seen_run",
                     (namespace, collection, document_key(document), BSON.encode({"_id": document["_id"]}), stored_raw,
                      canonical_document(stored_doc), query_json, source_hash, hashlib.sha256(stored_raw).hexdigest(),
                      private_redaction, run_id))
        return {"privacy_redacted": int(private_redaction), "jsonb_only_unavailable": int(query_json is None)}

    def finish_collection(self, namespace, collection, run_id, count, fingerprint):
        self.execute("DELETE FROM imladri_shadow.documents WHERE namespace=%s AND collection=%s AND seen_run<>%s",
                     (namespace, collection, run_id))
        removed = max(0, self.cursor.rowcount)
        self.execute("INSERT INTO imladri_shadow.checkpoints VALUES(%s,%s,%s,%s,%s,'scanned') ON CONFLICT(namespace,collection) "
                     "DO UPDATE SET run_id=EXCLUDED.run_id,source_count=EXCLUDED.source_count,"
                     "source_fingerprint=EXCLUDED.source_fingerprint,status=EXCLUDED.status",
                     (namespace, collection, run_id, count, fingerprint))
        return removed

    def stored_hash(self, namespace, collection, identity):
        row = self.execute("SELECT stored_sha256 FROM imladri_shadow.documents WHERE namespace=%s AND collection=%s AND id_key=%s",
                           (namespace, collection, identity)).fetchone()
        return row[0] if row else None

    def stored_count(self, namespace, collection):
        return self.execute("SELECT count(*) FROM imladri_shadow.documents WHERE namespace=%s AND collection=%s",
                            (namespace, collection)).fetchone()[0]

    def rows(self, namespace, collection):
        # A real server cursor avoids DB-API's ordinary whole-result buffering.
        cursor = self.connection.cursor()
        name = "imladri_" + uuid4().hex
        try:
            cursor.execute("DECLARE " + name + " NO SCROLL CURSOR FOR SELECT id_key,raw_bson,privacy_redacted "
                           "FROM imladri_shadow.documents WHERE namespace=%s AND collection=%s ORDER BY id_key",
                           (namespace, collection))
            while True:
                cursor.execute("FETCH FORWARD 128 FROM " + name)
                batch = cursor.fetchall()
                if not batch:
                    break
                for key, raw, redacted in batch:
                    yield key, BSON(bytes(raw)).decode(), redacted
        finally:
            cursor.execute("CLOSE " + name)
            cursor.close()

    def quarantine_owners(self, namespace, owners):
        if not owners:
            return
        self.execute("DELETE FROM imladri_shadow.documents WHERE namespace=%s AND collection='answer_history' "
                     "AND document->>'owner_id'=ANY(%s)", (namespace, list(owners)))
        for collection in JOURNAL_SOURCES:
            for key, doc, _ in self.rows(namespace, collection):
                pending = doc.get("answer_history_pending") or []
                filtered = [e for e in pending if not isinstance(e, dict) or e.get("owner_id") not in owners]
                if len(filtered) == len(pending):
                    continue
                doc["answer_history_pending"] = filtered
                raw = BSON.encode(doc)
                self.execute("UPDATE imladri_shadow.documents SET raw_bson=%s,canonical_json=%s,document=%s::jsonb,"
                             "stored_sha256=%s,privacy_redacted=true WHERE namespace=%s AND collection=%s AND id_key=%s",
                             (raw, canonical_document(doc), jsonb_text(doc), hashlib.sha256(raw).hexdigest(), namespace, collection, key))

    def rebuild_projections(self, namespace, environment, blocked_owners):
        counts = Counter({name: 0 for name in ("projection_issues", "out_of_scope_records", "participants", "couples", "billing_records",
                                              "history_events", "demographic_snapshots", "answer_observations")})
        for table in ("answer_observations", "demographic_snapshots", "history_events", "couples", "participants", "billing_records"):
            self.execute("DELETE FROM imladri_shadow." + table + " WHERE namespace=%s", (namespace,))
        people, foreign_people = set(), set()
        legacy = {str(doc.get("user_id") or doc.get("_id")) for _, doc, redacted in self.rows(namespace, "research_responses")
                  if not redacted and doc.get("env") in (None, environment) and doc.get("submitted") is True and doc.get("consent_version") == "research-v1"}
        for key, doc, _ in self.rows(namespace, "users"):
            uid = text(doc.get("user_id"))
            if doc.get("env") not in (None, environment):
                counts["out_of_scope_records"] += 1
                if uid:
                    foreign_people.add(uid)
                continue
            if not uid:
                counts["projection_issues"] += 1
                continue
            demo = (doc.get("about") or {}).get("demographics") or {}
            self.execute("INSERT INTO imladri_shadow.participants(namespace,user_id,environment,role,closing,age,height_cm,research_version,"
                         "research_revision,legacy_research_submitted,membership_plan,subscription_status,topup_balance,source_key,assessment_publish,"
                         "adult_eligible,age_is_numeric,height_is_numeric,couples_adult_attested) "
                         "VALUES(" + ",".join(["%s"] * 19) + ")",
                         (namespace, uid, text(doc.get("env")) or environment, text(doc.get("role")), doc.get("closing") is True,
                          measurement(demo.get("age"), 13, 120), measurement(demo.get("height_cm"), quiz_answers.HEIGHT_MIN_CM, quiz_answers.HEIGHT_MAX_CM), text(doc.get("questionnaire_research_version")),
                          str(doc.get("questionnaire_research_revision", 0)), uid in legacy and doc.get("questionnaire_research_managed") is not True
                          and doc.get("questionnaire_research_version") != RESEARCH_VERSION,
                          text(doc.get("membership_plan_id") or doc.get("plan_offer")), text(doc.get("subscription_status")),
                          safe_int(doc.get("topup_balance")), key, (doc.get("assessment_repeat") or {}).get("publish_group_results") is True,
                          adult_eligible(demo.get("age")), native_number(demo.get("age")), native_number(demo.get("height_cm")),
                          isinstance(doc.get("couples_adult_at"), (str, datetime)) and doc.get("couples_adult_at") != ""))
            people.add(uid)
            counts["participants"] += 1
        for key, doc, _ in self.rows(namespace, "couples_pairs"):
            pair = text(str(doc.get("_id")))
            a, b = text(doc.get("inviter")), text(doc.get("invitee"))
            if doc.get("env") not in (None, environment) or a in foreign_people or b in foreign_people:
                counts["out_of_scope_records"] += 1
                continue
            if (not pair or (a and a not in people) or (b and b not in people) or (a and a == b)
                    or (doc.get("state") == "active" and (not a or not b or set(doc.get("members") or []) != {a, b}))):
                counts["projection_issues"] += 1
                continue
            self.execute("INSERT INTO imladri_shadow.couples VALUES(%s,%s,%s,%s,%s,%s,%s)",
                         (namespace, pair, text(doc.get("env")) or environment, text(doc.get("state")), a, b, key))
            counts["couples"] += 1
        for key, doc, _ in self.rows(namespace, "payment_transactions"):
            if doc.get("env") not in (None, environment) or doc.get("user_id") in foreign_people:
                counts["out_of_scope_records"] += 1
                continue
            amount = decimal_value(doc.get("amount"))
            if amount is not None and doc.get("currency") != "usd":
                counts["projection_issues"] += 1
            self.execute("INSERT INTO imladri_shadow.billing_records VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                         (namespace, key, text(doc.get("user_id")), text(doc.get("kind")), text(doc.get("payment_status")),
                          text(doc.get("currency")), (amount * 100 if amount is not None and doc.get("currency") == "usd" else None),
                          decimal_value(doc.get("subscription_revenue_cents"))))
            counts["billing_records"] += 1
        tombstones = self.tombstones(namespace)
        seen = {}
        # Central events win over pending copies. Purged opaque IDs always win.
        def events():
            for _, doc, _ in self.rows(namespace, "answer_history"):
                yield doc
            for source in JOURNAL_SOURCES:
                for _, doc, redacted in self.rows(namespace, source):
                    if not redacted:
                        yield from (doc.get("answer_history_pending") or [])
        for event in events():
            if not isinstance(event, dict):
                counts["projection_issues"] += 1
                continue
            eid = text(event.get("id") or event.get("_id"))
            if not eid or eid in tombstones or event.get("purged"):
                continue
            semantic = json_util.dumps({k: v for k, v in event.items() if k != "_id"},
                                       json_options=json_util.CANONICAL_JSON_OPTIONS, sort_keys=True)
            if eid in seen:
                if seen[eid] != semantic:
                    self.execute("DELETE FROM imladri_shadow.history_events WHERE namespace=%s AND event_id=%s", (namespace, eid))
                    seen[eid] = None
                    counts["projection_issues"] += 1
                continue
            seen[eid] = semantic
            owner = text(event.get("owner_id"))
            if owner in blocked_owners:
                continue
            payload = jsonb_text(event)
            if payload is None:
                counts["projection_issues"] += 1
                continue
            consent = event.get("consent") or {}
            snapshot = event.get("demographics") or {}
            if not isinstance(consent, dict) or not isinstance(snapshot, dict):
                counts["projection_issues"] += 1
                continue
            if consent.get("env") not in (None, environment) or owner in foreign_people:
                counts["out_of_scope_records"] += 1
                continue
            if owner not in people:
                counts["projection_issues"] += 1
                continue
            at = event.get("at")
            try:
                at = datetime.fromisoformat(at.replace("Z", "+00:00")) if isinstance(at, str) else at
                if not isinstance(at, datetime) or at.tzinfo is None:
                    raise ValueError()
            except (ValueError, TypeError):
                counts["projection_issues"] += 1
                continue
            self.execute("INSERT INTO imladri_shadow.history_events VALUES(" + ",".join(["%s"] * 19) + ")",
                         (namespace, eid, owner, text(event.get("pair_id")), text(event.get("domain")), text(event.get("question_id")),
                          text(event.get("signature")), at, str(event.get("revision", "")), text(event.get("attempt_id")),
                          event.get("baseline") is True, text(event.get("visibility")), text(consent.get("env")) or environment,
                          text(consent.get("role")), consent.get("closing") is True, text(consent.get("questionnaire_research_version")),
                          consent.get("consent_version") == "research-v1" and consent.get("submitted") is True,
                          text(snapshot.get("snapshot_id")), payload))
            # Explicit column count is validated against the schema in integration tests.
            counts["history_events"] += 1
            after = event.get("after")
            if event.get("domain") == "demographics" and isinstance(after, dict):
                self.execute("INSERT INTO imladri_shadow.demographic_snapshots(namespace,event_id,age,height_cm,gender,sex,ethnicity,education,adult_eligible) "
                             "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                             (namespace, eid, measurement(after.get("age"), 13, 120), measurement(after.get("height_cm"), quiz_answers.HEIGHT_MIN_CM, quiz_answers.HEIGHT_MAX_CM), text(after.get("gender")),
                              text(after.get("sex")), text(after.get("ethnicity")), text(after.get("education")), adult_eligible(after.get("age"))))
                counts["demographic_snapshots"] += 1
            for row in observation_rows(event):
                self.execute("INSERT INTO imladri_shadow.answer_observations VALUES(%s,%s,%s,%s,%s,%s)",
                             (namespace, eid, row["question_id"], row["edition"], row["answer_code"], row["skipped"]))
                counts["answer_observations"] += 1
        return dict(counts)

    def complete(self, run_id, report):
        for table in ("participants", "couples", "history_events", "answer_observations", "demographic_snapshots"):
            self.execute("ANALYZE imladri_shadow." + table)
        self.execute("UPDATE imladri_shadow.runs SET completed_at=now(),status=%s,report=%s::jsonb WHERE run_id=%s",
                     (report["status"], json.dumps(public_report(report)), run_id))
        self.connection.commit()


def source_collection(db, name):
    # Real Mongo reads retain exact source BSON bytes; fakes can implement get_collection.
    return db.get_collection(name, codec_options=CodecOptions(document_class=RawBSONDocument))


async def source_rows(db, name):
    cursor = source_collection(db, name).find({}).sort("_id", 1).batch_size(128).max_time_ms(55000)
    try:
        async for document in cursor:
            yield document
    finally:
        await cursor.close()


async def reconcile(source_db, destination, *, namespace, environment):
    """Complete idempotent shadow reconciliation; never grants production cutover."""
    validate_name(namespace)
    validate_name(environment)
    run_id = str(uuid4())
    actual = {name for name in await source_db.list_collection_names() if not name.startswith("system.")}
    for name in actual:
        validate_name(name)
    excluded = sorted(actual & EXCLUDED_COLLECTIONS.keys())
    actual -= EXCLUDED_COLLECTIONS.keys()
    report = {"report_version": REPORT_VERSION, "run_id": run_id, "namespace": namespace, "environment": environment,
              "started_at": utc_now().isoformat(), "status": "running", "mongo_authoritative": True,
              "cutover_eligible": False, "consistent_capture": False, "collections": [], "domains": {},
              "unknown_collections": sorted(actual - COLLECTION_DOMAINS.keys()), "excluded_control_collections": excluded,
              "source_write_operations": 0, "capture_mode": "two_pass_read_only_reconciliation",
              "remaining_gates": ["A validated consistent snapshot/change-stream handoff and ongoing capture.",
                                  "Customer/admin/billing parity and role/withdrawal/deletion tests on the target.",
                                  "An explicitly authorized production cutover; no switch exists in this tool."]}
    counts = Counter()
    blocked_owners = set()
    try:
        destination.begin(run_id, namespace, environment)
        all_names = sorted((set(COLLECTION_DOMAINS) | actual | set(destination.existing_collections(namespace))) - EXCLUDED_COLLECTIONS.keys())
        tombstones = destination.tombstones(namespace)
        if "answer_history" in actual:
            async with aclosing(source_rows(source_db, "answer_history")) as source_documents:
                async for event in source_documents:
                    if event.get("purged"):
                        eid = text(event.get("id") or event.get("_id"))
                        if eid:
                            tombstones.add(eid)
                            destination.mark_tombstone(namespace, eid)
        for collection in all_names:
            source_count, total = 0, 0
            local = Counter()
            if collection in actual:
                async with aclosing(source_rows(source_db, collection)) as source_documents:
                    async for doc in source_documents:
                        source_count += 1
                        total = fingerprint_add(total, document_key(doc), document_hash(doc))
                        redactions = doc.get("answer_history_redactions") or []
                        if redactions:
                            local["unresolved_redactions"] += len(redactions)
                            owners = [doc.get("user_id"), doc.get("inviter"), doc.get("invitee"), *list(doc.get("members") or [])]
                            owners.extend(e.get("owner_id") for e in (doc.get("answer_history_pending") or []) if isinstance(e, dict))
                            blocked_owners.update(owner for owner in owners if text(owner))
                        local["pending_events"] += len(doc.get("answer_history_pending") or [])
                        local.update(destination.put(namespace, collection, doc, run_id, tombstones))
            first = fingerprint_finish(total, source_count)
            removed = destination.finish_collection(namespace, collection, run_id, source_count, first)
            counts.update(local)
            report["collections"].append({"name": collection, "domain": domain_for_collection(collection), "source_count": source_count,
                                          "first_fingerprint": first, "removed_stale_records": removed, **dict(local)})
        destination.quarantine_owners(namespace, blocked_owners)
        # Re-read all source bytes and compare every ID/hash; a count-only test is insufficient.
        for item in report["collections"]:
            count, total, mismatches = 0, 0, 0
            if item["name"] in actual:
                async with aclosing(source_rows(source_db, item["name"])) as source_documents:
                    async for doc in source_documents:
                        count += 1
                        key, digest = document_key(doc), document_hash(doc)
                        total = fingerprint_add(total, key, digest)
                        mismatches += int(destination.stored_hash(namespace, item["name"], key) != digest)
            second = fingerprint_finish(total, count)
            target_count = destination.stored_count(namespace, item["name"])
            item.update(source_stable_during_scan=second == item["first_fingerprint"], target_count=target_count,
                        mismatched_records=mismatches, parity=second == item["first_fingerprint"] and count == target_count and mismatches == 0)
        names_stable = actual == ({n for n in await source_db.list_collection_names() if not n.startswith("system.")} - EXCLUDED_COLLECTIONS.keys())
        report["collection_inventory_stable"] = names_stable
        projection = destination.rebuild_projections(namespace, environment, blocked_owners)
        report["projections"] = projection
        for domain in sorted(set(COLLECTION_DOMAINS.values()) | {"unclassified"}):
            rows = [row for row in report["collections"] if row["domain"] == domain]
            report["domains"][domain] = {"collections": len(rows), "source_records": sum(r["source_count"] for r in rows),
                                         "target_records": sum(r["target_count"] for r in rows), "parity": all(r["parity"] for r in rows)}
        report["observed_parity"] = names_stable and all(item["parity"] for item in report["collections"])
        report["pending_events"] = counts["pending_events"]
        report["unresolved_redactions"] = counts["unresolved_redactions"]
        report["privacy_redacted_records"] = counts["privacy_redacted"]
        report["jsonb_unavailable_records"] = counts["jsonb_only_unavailable"]
        ready = report["observed_parity"] and not (report["unknown_collections"] or report["pending_events"]
                or report["unresolved_redactions"] or report["jsonb_unavailable_records"] or projection.get("projection_issues")
                or projection.get("out_of_scope_records"))
        report["status"] = "observed_parity_only" if ready else "needs_reconciliation"
        report["completed_at"] = utc_now().isoformat()
        destination.complete(run_id, report)
        return public_report(report)
    except BaseException:
        # Task cancellation must release the transaction and its advisory lock.
        destination.connection.rollback()
        raise


def public_report(report):
    """Allowlisted report, never a DSN, person ID, raw answer or database error."""
    scalar = ("report_version", "run_id", "namespace", "environment", "started_at", "completed_at", "status",
              "mongo_authoritative", "cutover_eligible", "consistent_capture", "source_write_operations", "capture_mode",
              "observed_parity", "collection_inventory_stable", "pending_events", "unresolved_redactions",
              "privacy_redacted_records", "jsonb_unavailable_records")
    result = {key: report[key] for key in scalar if key in report}
    result["cutover_eligible"] = False
    result["mongo_authoritative"] = True
    for key in ("unknown_collections", "excluded_control_collections", "remaining_gates"):
        result[key] = list(report.get(key, []))
    result["collections"] = [{key: row[key] for key in ("name", "domain", "source_count", "target_count", "first_fingerprint",
        "removed_stale_records", "pending_events", "unresolved_redactions", "privacy_redacted", "jsonb_only_unavailable",
        "source_stable_during_scan", "mismatched_records", "parity") if key in row} for row in report.get("collections", [])]
    result["domains"] = {name: {k: row[k] for k in ("collections", "source_records", "target_records", "parity") if k in row}
                         for name, row in report.get("domains", {}).items()}
    result["projections"] = {k: v for k, v in report.get("projections", {}).items()
                             if k in ("participants", "couples", "billing_records", "history_events", "demographic_snapshots",
                                      "answer_observations", "projection_issues", "out_of_scope_records")}
    return result


async def publish_report(source_db, report):
    """Explicit control-plane metadata write, never part of source data copying."""
    safe = public_report(report)
    if safe.get("status") not in ("observed_parity_only", "needs_reconciliation") or not safe.get("completed_at"):
        raise ValueError("Only a completed reconciliation report can be published.")
    identity = "postgres-shadow:" + validate_name(safe["namespace"]) + ":" + validate_name(safe["environment"])
    await source_db.database_migration_reports.replace_one({"_id": identity}, {"_id": identity, **safe}, upsert=True)
    return {"published": True, "application_data_mutated": False, "control_collection": "database_migration_reports"}
