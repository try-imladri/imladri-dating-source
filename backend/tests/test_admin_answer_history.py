"""Historical permissions, erasure fences and bounded staff presentation."""
import asyncio
import ast
import csv
import io
import json
import os
import time
from copy import deepcopy
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend import admin_answer_history as history, answer_context, answer_history, personal_demographics
from backend.tests.test_free_plan_quota import run_case as mock_case


ENV = "fixture"
STAFF = {"user_id": "staff", "role": "admin", "env": ENV}
STAMP = "2026-11-14T10:00:00+00:00"


def run_case(callback):
    """Use actual isolated PostgreSQL when supplied; never a provider database."""
    dsn = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
    if not dsn:
        return mock_case(callback)
    assert urlsplit(dsn).hostname in ("127.0.0.1", "localhost", "::1"), "Timeline tests require loopback PostgreSQL."
    from backend.postgres_store import PostgresDatabase
    async def exercise():
        namespace = "fixture_history_" + uuid4().hex
        db = PostgresDatabase(dsn, namespace, allow_local_plaintext=True)
        try:
            await callback(db)
        finally:
            def cleanup(cursor, _now):
                for table in ("unique_keys", "indexes", "documents"):
                    cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (namespace,))
            await db._run(cleanup, write="__cleanup")
            await db.aclose()
    asyncio.run(exercise())


@pytest.fixture(autouse=True)
def cursor_key(monkeypatch):
    monkeypatch.setenv("ANSWER_HISTORY_CURSOR_SECRET", "fixture-only-history-key-0123456789")


def member(uid="one", **changes):
    return {"user_id": uid, "role": "member", "env": ENV, "about": {"demographics": {"age": 30, "height_cm": 170}},
        "questionnaire_research_version": "all-answers-v1", "questionnaire_research_at": STAMP, "row_member_version": "row-member-v1", "row_member_code": "1" * 32,
        "row_member_env": ENV, "row_member_at": STAMP, "row_couple_version": "row-couple-v1", "row_couple_code": "2" * 32,
        "row_couple_env": ENV, "row_couple_at": STAMP, "row_couple_pair_id": "pair", "answer_history_staff_version": history.VERSION,
        "answer_history_staff_id": "3" * 32, "answer_history_staff_env": ENV, "answer_history_staff_at": STAMP,
        "answer_history_staff_revision": 1, **changes}


def observed(person, eid="event-one", **changes):
    result = answer_history.event(source="users", source_id=person["user_id"], owner_id=person["user_id"], domain="everyday",
        question_id="e:q1", after={"choice": 1, "label": "A clear plan"}, question={"label": "What helps you decide?"},
        signature="edition-one", consent=answer_history.consent(person), at="2026-11-14T11:00:00+00:00")
    return {**result, "id": eid, "generation": 0, **changes}


async def seed(db, people=None, events=None, pair=False):
    people = people or [member()]
    for person in people:
        row = deepcopy(person)
        row[answer_history.SOURCE] = {"source": "users", "source_id": row["user_id"]}
        await db.users.insert_one(row)
    if pair:
        await db.couples_pairs.insert_one({"_id": "pair", "env": ENV, "state": "active", "inviter": people[0]["user_id"],
            "invitee": people[1]["user_id"], "members": [p["user_id"] for p in people],
            answer_history.SOURCE: {"source": "couples_pairs", "source_id": "pair"}})
    for event in events if events is not None else [observed(people[0])]:
        await db.answer_history.insert_one({"_id": event["id"], **event})


def test_new_permission_is_default_off_and_requires_actual_adult_confirmation():
    ordinary = {"user_id": "new", "role": "member", "env": ENV, "about": {"demographics": {"age": 30}}}
    assert history.preferences(ordinary, ENV) == {"version": history.VERSION, "revision": 0, "enabled": False, "since": None}
    async def go(db):
        await db.users.insert_one(ordinary)
        body = {"expected_member": "new", "version": history.VERSION, "revision": 0, "enabled": True, "adult_confirmed": False}
        with pytest.raises(HTTPException) as error:
            await history.set_permission(db, ordinary, ENV, body)
        assert error.value.status_code == 403
        body["adult_confirmed"] = True
        enabled = await history.set_permission(db, ordinary, ENV, body)
        assert enabled["enabled"] and enabled["revision"] == 1 and enabled["since"]
        first = await db.users.find_one({"user_id": "new"})
        with pytest.raises(HTTPException) as stale:
            await history.set_permission(db, first, ENV, body)
        assert stale.value.status_code == 409
        await history.set_permission(db, first, ENV, {**body, "enabled": False, "revision": 1})
        disabled = await db.users.find_one({"user_id": "new"})
        assert not history.preferences(disabled, ENV)["enabled"]
        await history.set_permission(db, disabled, ENV, {**body, "revision": 2})
        third = await db.users.find_one({"user_id": "new"})
        assert first[history.PREFIX + "id"] != third[history.PREFIX + "id"]
    run_case(go)


@pytest.mark.parametrize("age", [None, "", 17, "17", 121, True, [], "adult"])
def test_permission_never_treats_missing_minor_or_malformed_age_as_adult(age):
    person = member(about={"demographics": {"age": age}})
    assert not history._history_granted(person, answer_history.consent(person), ENV)


def test_age_downgrade_removes_history_grant():
    changes, removals = {}, {}
    personal_demographics.protect_minor(changes, removals)
    assert {history.PREFIX + key for key in ("version", "id", "at", "env")} <= set(removals)


@pytest.mark.parametrize("scope", ["member", "couple"])
def test_private_couples_drafts_never_fetch_staff_payloads_even_with_every_grant(monkeypatch, scope):
    person = member()
    peer = member("two")
    pair = {"_id": "pair", "members": ["one", "two"], "inviter": "one", "invitee": "two"} if scope == "couple" else None
    people = {person["user_id"]: person, **({peer["user_id"]: peer} if pair else {})}
    event = observed(person, source="couples_drafts", source_id="one:support-1", pair_id="pair", domain="couples",
                     after={"choice": "Listening", "lines": ["SECRET_UNFINISHED_WORDS"]},
                     question_id="support-1", question={"label": "SECRET_DRAFT_QUESTION"})
    metadata = {key: value for key, value in event.items() if key in history.META_PROJECTION}
    source = {answer_history.SOURCE: {"source": "couples_drafts", "source_id": "one:support-1"}}
    monkeypatch.setattr(history, "_target", AsyncMock(return_value=(people, pair)))
    monkeypatch.setattr(history, "_audit", AsyncMock())

    class Cursor:
        def __init__(self, rows): self.rows = rows
        def sort(self, *_): return self
        def limit(self, *_): return self
        async def to_list(self, *_): return self.rows

    class History:
        def find(self, query, projection):
            assert projection != {"_id": 0}, "A staff request tried to fetch a private draft payload"
            return Cursor([metadata] if projection == history.META_PROJECTION else [{"id": event["id"]}])

    class Source:
        def find(self, *_): return Cursor([source])

    class Database:
        answer_history = History()
        def __getitem__(self, name):
            assert name == "couples_drafts"
            return Source()

    async def check():
        target = "pair" if pair else "one"
        result = await history.page(Database(), STAFF, ENV, scope, target)
        item, = result["items"]
        assert item["status"] == "withheld" and "Private Couples drafts" in item["withheld_reason"]
        assert set(item) == {"event_id", "observed_at_utc", "domain", "baseline", "status", "withheld_reason"}
        exported = await history.export_csv(Database(), STAFF, ENV, scope, target)
        row, = csv.DictReader(io.StringIO(exported.body.decode("utf-8-sig")))
        assert row["status"] == "withheld" and row["after"] == row["before"] == row["question"] == ""
        assert "SECRET_" not in str(result) and b"SECRET_" not in exported.body
        # Completed private observations keep their independent permission rules.
        completed = {**event, "source": "couples_pairs" if pair else "users"}
        assert history._reason(person, completed, ENV, scope, pair, people) is None

    asyncio.run(check())


def test_staff_draft_exclusion_preserves_owner_resumption_and_export():
    from backend import accounts, couples_journey

    async def check(db):
        person = member()
        stamp = int(time.time())
        person.update(tier="paid", stripe_subscription_id="sub_fixture",
                      subscription_period_start=stamp - 60, subscription_period_end=stamp + 3600,
                      couples_adult_at=STAMP)
        await seed(db, [person], [])
        secret = "OWNER_ONLY_UNFINISHED_WORDS"
        await couples_journey.save_draft(db, person, "support-1", couples_journey.DraftIn(
            expected_member="one", version=0, choice="Listening", lines=["Listening", secret]))
        source = await db.couples_drafts.find_one({"user_id": "one", "key": "support-1"})
        retained = await db.answer_history.find_one({"source": "couples_drafts", "owner_id": "one"})
        assert source["lines"] == ["Listening", secret] and retained["after"]["lines"] == source["lines"]
        result = await history.page(db, STAFF, ENV, "member", "one", domain="couples")
        assert result["items"][0]["status"] == "withheld" and secret not in str(result)
        exported = await history.export_csv(db, STAFF, ENV, "member", "one", domain="couples")
        assert secret.encode() not in exported.body
        resumed = await couples_journey.view(db, person)
        assert resumed["drafts"][0]["lines"] == ["Listening", secret]
        owner_export = json.loads("".join([part async for part in accounts.export_json(db, person)]))
        assert owner_export["couples_drafts"][0]["lines"] == ["Listening", secret]
        assert any(event.get("after", {}).get("lines") == ["Listening", secret]
                   for event in owner_export["answer_history"])
        assert await db.couples_drafts.find_one({"user_id": "one", "key": "support-1"}) == source
        assert await db.answer_history.find_one({"id": retained["id"]}) == retained

    run_case(check)


def test_existing_current_csv_permission_never_silently_authorizes_historical_values():
    async def go(db):
        person = member()
        old = observed(person, consent={"questionnaire_research_version": "all-answers-v1", "role": "member", "env": ENV},
            after={"answer": "Private sexual preference"}, domain="intimacy", question_id="private-specific-topic")
        await seed(db, [person], [old])
        response = await history.page(db, STAFF, ENV, "member", "one")
        item = response["items"][0]
        assert item["status"] == "withheld"
        assert set(item) == {"event_id", "observed_at_utc", "domain", "baseline", "status", "withheld_reason"}
        assert "Private sexual preference" not in str(response) and "private-specific-topic" not in str(response)
    run_case(go)


def test_available_answers_retain_wording_version_baseline_and_historical_demographics():
    async def go(db):
        person = member()
        demo = observed(person, "demo", domain="demographics", question_id="demographics", after={"age": 25, "height_cm": 160}, baseline=True)
        event = observed(person, demographics={"snapshot_id": "demo", "observed_at": STAMP})
        await seed(db, [person], [demo, event])
        response = await history.page(db, STAFF, ENV, "member", "one")
        answer = next(item for item in response["items"] if item["event_id"] == "event-one")
        assert answer["question"] == event["question"] and answer["edition"] == "edition-one"
        assert answer["demographics_at_observation"] == {"age": 25, "height_cm": 160}
        assert answer["demographics_at_observation"]["age"] != person["about"]["demographics"]["age"]
        assert next(item for item in response["items"] if item["event_id"] == "demo")["baseline"] is True
        audit = await db.admin_answer_history_access.find_one({})
        assert audit["actor_id"] == "staff" and audit["candidate_count"] == 2
        assert audit["phase"] == "before_final_revalidation"
        assert audit["expires_at"] is not None
        assert not any(key in str(audit) for key in ["A clear plan", "height_cm", "What helps"])
    run_case(go)


@pytest.mark.parametrize("field", ["answer_history_staff_id", "questionnaire_research_version", "questionnaire_research_at", "row_member_code", "row_member_env"])
def test_revocation_or_regrant_never_reauthorizes_previous_observation(field):
    async def go(db):
        original = member()
        changed = member(**{field: "different"})
        await seed(db, [changed], [observed(original)])
        assert (await history.page(db, STAFF, ENV, "member", "one"))["items"][0]["status"] == "withheld"
    run_case(go)


def test_previous_value_without_matching_consented_observation_is_not_exposed():
    async def go(db):
        person = member()
        event = observed(person, before_exists=True, before={"earlier": "unconsented earlier value"})
        await seed(db, [person], [event])
        result = (await history.page(db, STAFF, ENV, "member", "one"))["items"][0]
        assert result["after"] == event["after"] and result["before"] is None and result["before_withheld"]
        assert "unconsented earlier value" not in str(result)
    run_case(go)


def test_previous_value_with_matching_retained_grant_can_be_compared():
    async def go(db):
        person = member()
        old = observed(person, "old", at="2026-11-14T10:59:00+00:00", after={"choice": 0})
        event = observed(person, before_exists=True, before=old["after"])
        await seed(db, [person], [old, event])
        result = (await history.page(db, STAFF, ENV, "member", "one"))["items"][0]
        assert result["before"] == {"choice": 0}
    run_case(go)


def test_forward_permission_does_not_relabel_an_earlier_event_as_consented():
    async def go(db):
        person = member()
        event = observed(person, at="2026-11-14T09:59:59+00:00")
        await seed(db, [person], [event])
        assert (await history.page(db, STAFF, ENV, "member", "one"))["items"][0]["status"] == "withheld"
    run_case(go)


def test_erased_demographic_snapshot_is_not_replaced_by_current_profile():
    async def go(db):
        person = member()
        event = observed(person, demographics={"snapshot_id": "demo"})
        await seed(db, [person], [event])
        await db.answer_history.insert_one({"_id": "demo", "id": "demo", "purged": True})
        row = (await history.page(db, STAFF, ENV, "member", "one"))["items"][0]
        assert row["demographics_at_observation"] is None
        assert "height_cm" not in str(row)
    run_case(go)


def test_permission_change_during_demographic_snapshot_cas_retries_without_overwriting_new_grant():
    async def go(db):
        person = member()
        await seed(db, [person], [])
        changed = False
        class Users:
            def __getattr__(self, name):
                return getattr(db.users, name)
            async def update_one(self, query, update, **kwargs):
                nonlocal changed
                if not changed and "about.demographics" in query:
                    changed = True
                    await db.users.update_one({"user_id": "one"}, {"$set": {"answer_history_staff_id": "4" * 32}})
                return await db.users.update_one(query, update, **kwargs)
        class Database:
            users = Users()
            answer_history = db.answer_history
            def __getitem__(self, name):
                return self.users if name == "users" else db[name]
        pointer = await answer_context.for_member(Database(), "one")
        saved = await db.users.find_one({"user_id": "one"})
        snapshot = await db.answer_history.find_one({"id": pointer["snapshot_id"]})
        assert changed and saved["answer_history_staff_id"] == "4" * 32
        assert snapshot["consent"]["answer_history_staff_id"] == "4" * 32
        assert await db.answer_history.count_documents({}) == 1
    run_case(go)


def test_erasure_tombstones_and_unflushed_source_fences_are_never_presented(monkeypatch):
    async def go(db):
        person = member()
        await seed(db, [person], [observed(person), observed(person, "purged")])
        await db.answer_history.replace_one({"id": "purged"}, {"id": "purged", "purged": True})
        await db.users.update_one({"user_id": "one"}, {"$set": {answer_history.REDACTIONS: [{"question_id": "e:q1", "through_generation": 0}]}})
        monkeypatch.setattr(history.history_lifecycle, "prepare_export", AsyncMock())
        result = await history.page(db, STAFF, ENV, "member", "one")
        assert result["items"] == []
    run_case(go)


def test_deleted_source_cannot_leave_visible_orphaned_history():
    async def go(db):
        person = member()
        event = observed(person, source="intimacy_answers", source_id="missing")
        await seed(db, [person], [event])
        assert (await history.page(db, STAFF, ENV, "member", "one"))["items"] == []
    run_case(go)


def test_withdrawal_while_access_audit_is_written_redacts_already_assembled_payload(monkeypatch):
    async def go(db):
        await seed(db)
        async def revoke(*_args, **_kwargs):
            await db.users.update_one({"user_id": "one"}, {"$unset": {"answer_history_staff_version": ""}})
        monkeypatch.setattr(history, "_audit", revoke)
        result = (await history.page(db, STAFF, ENV, "member", "one"))["items"][0]
        assert result["status"] == "withheld" and "after" not in result
    run_case(go)


def test_cursor_is_signed_and_bound_to_member_filters_environment_and_scope():
    bound = history._binding(ENV, "member", "one", {"domain": "intimacy"})
    cursor = history._cursor([STAMP, "event"], bound, STAMP)
    assert history._position(cursor, bound) == ([STAMP, "event"], STAMP)
    for bad, binding in [(cursor[:-2] + "ab", bound), (cursor, history._binding(ENV, "member", "two", {})),
                          (cursor, history._binding("other", "member", "one", {"domain": "intimacy"}))]:
        with pytest.raises(HTTPException) as error:
            history._position(bad, binding)
        assert error.value.status_code == 409


def test_paginated_observations_are_not_duplicated_and_filters_are_applied():
    async def go(db):
        person = member()
        events = [observed(person, f"e-{i}") for i in range(5)]
        await seed(db, [person], events)
        first = await history.page(db, STAFF, ENV, "member", "one", limit=2)
        second = await history.page(db, STAFF, ENV, "member", "one", limit=2, before=first["next"])
        third = await history.page(db, STAFF, ENV, "member", "one", limit=2, before=second["next"])
        ids = [r["event_id"] for page in (first, second, third) for r in page["items"]]
        assert ids == ["e-4", "e-3", "e-2", "e-1", "e-0"] and third["next"] is None
        with pytest.raises(HTTPException):
            await history.page(db, STAFF, ENV, "member", "one", before=first["next"], domain="intimacy")
        assert (await history.page(db, STAFF, ENV, "member", "one", from_date="2026-11-15"))["items"] == []
        assert (await history.page(db, STAFF, ENV, "member", "one", question_id="not-this-question"))["items"] == []
    run_case(go)


@pytest.mark.parametrize("domain", ["person_intake", "assessment_repeat", "wording_feedback"])
def test_named_history_areas_find_retained_producer_observations(domain):
    async def go(db):
        person = member()
        if domain == "person_intake":
            from backend.person_answer_history import observation
            wanted = observation("other-person", person, "context", None, "Owner supplied observation",
                {"context": "How do you know this person?"}, None, "2026-11-14T11:00:00+00:00", created=True)
            wanted.update(id="wanted", generation=0)
        else:
            wanted = observed(person, "wanted", domain=domain, after={"choice": "Saved producer response"})
        await seed(db, [person], [wanted, observed(person, "unrelated")])
        if domain == "person_intake":
            await db.people.insert_one({"id": "other-person", "user_id": person["user_id"],
                answer_history.SOURCE: {"source": "people", "source_id": "other-person"}})
        result = await history.page(db, STAFF, ENV, "member", "one", domain=domain)
        assert [item["event_id"] for item in result["items"]] == ["wanted"]
        assert result["items"][0]["status"] == "available"
        await db.users.update_one({"user_id": "one"}, {"$unset": {"answer_history_staff_version": ""}})
        restricted = await history.page(db, STAFF, ENV, "member", "one", domain=domain)
        assert restricted["items"][0]["status"] == "withheld"
        assert "after" not in restricted["items"][0]
    run_case(go)


def test_people_filter_alias_includes_legacy_and_canonical_rows_with_one_cursor_binding():
    async def go(db):
        person = member()
        events = [observed(person, "canonical", domain="person_intake"),
                  observed(person, "legacy", domain="people", at="2026-11-14T11:01:00+00:00"),
                  observed(person, "unrelated")]
        await seed(db, [person], events)
        first = await history.page(db, STAFF, ENV, "member", "one", domain="people", limit=1)
        assert [item["event_id"] for item in first["items"]] == ["legacy"] and first["next"]
        second = await history.page(db, STAFF, ENV, "member", "one", domain="person_intake", limit=1, before=first["next"])
        assert [item["event_id"] for item in second["items"]] == ["canonical"]
        assert second["next"] is None
        exported = await history.export_csv(db, STAFF, ENV, "member", "one", domain="people")
        rows = list(csv.DictReader(io.StringIO(exported.body.decode("utf-8-sig"))))
        assert {row["event_id"] for row in rows} == {"canonical", "legacy"}
    run_case(go)


def test_explicit_answer_history_producer_domains_have_named_staff_filters():
    emitted = set()
    for path in Path(history.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "answer_history"
                    and node.func.attr == "event"):
                continue
            domain = next((keyword.value for keyword in node.keywords if keyword.arg == "domain"), None)
            if isinstance(domain, ast.Constant):
                emitted.add(domain.value)
            elif isinstance(domain, ast.Name):
                # Individual history emits a short literal table of profile fields.
                for loop in ast.walk(tree):
                    if not (isinstance(loop, ast.For) and isinstance(loop.target, ast.Tuple)
                            and isinstance(loop.iter, (ast.Tuple, ast.List))):
                        continue
                    columns = [value.id if isinstance(value, ast.Name) else None for value in loop.target.elts]
                    if domain.id not in columns:
                        continue
                    column = columns.index(domain.id)
                    for row in loop.iter.elts:
                        if (isinstance(row, (ast.Tuple, ast.List)) and len(row.elts) > column
                                and isinstance(row.elts[column], ast.Constant)):
                            emitted.add(row.elts[column].value)
    assert emitted and emitted <= history.DOMAINS


def test_couples_timeline_never_includes_visual_preferences_or_other_pair_events():
    async def go(db):
        people = [member("one"), member("two")]
        events = [observed(people[0], "ours", source="couples_pairs", source_id="pair", pair_id="pair", domain="couples"),
            observed(people[0], "visual", source="couples_pairs", source_id="pair", pair_id="pair", domain="your_type"),
            observed(people[0], "other", source="couples_pairs", source_id="pair", pair_id="elsewhere", domain="couples")]
        await seed(db, people, events, pair=True)
        response = await history.page(db, STAFF, ENV, "couple", "pair")
        assert [r["event_id"] for r in response["items"]] == ["ours"]
        assert response["items"][0]["visibility"] == "private" and response["items"][0]["seat"] == "inviter"
        await db.users.update_one({"user_id": "two"}, {"$unset": {"answer_history_staff_id": ""}})
        assert (await history.page(db, STAFF, ENV, "couple", "pair"))["items"][0]["status"] == "withheld"
    run_case(go)


def test_your_type_timeline_never_exports_packed_or_latent_models():
    async def go(db):
        person = member(your_type_research_version="your-type-research-v2", your_type_research_at=STAMP)
        event = observed(person, domain="your_type", after={"states_packed": {"payload": b"binary"}, "model": {"weights": [999]},
            "request": {"kind": "like", "face_id": "portrait-one"}, "asset_edition": "bank-one", "model_edition": "model-one"})
        await seed(db, [person], [event])
        row = (await history.page(db, STAFF, ENV, "member", "one"))["items"][0]
        assert row["status"] == "available" and row["after"]["request"]["face_id"] == "portrait-one"
        assert "states_packed" not in str(row) and "weights" not in str(row)
        await db.users.update_one({"user_id": "one"}, {"$unset": {"your_type_research_version": ""}})
        assert (await history.page(db, STAFF, ENV, "member", "one"))["items"][0]["status"] == "withheld"
    run_case(go)


def test_csv_is_bounded_formula_safe_and_does_not_invent_empty_answers():
    async def go(db):
        person = member()
        event = observed(person, kind="=HYPERLINK(\"https://fixture.invalid\")", question_id="@SUM(1,2)")
        await seed(db, [person], [event, observed(person, "other")])
        response = await history.export_csv(db, STAFF, ENV, "member", "one", limit=1)
        assert response.headers["cache-control"] == "private, no-store" and response.headers["x-history-has-more"] == "1"
        rows = list(csv.DictReader(io.StringIO(response.body.decode("utf-8-sig"))))
        assert len(rows) == 1 and set(rows[0]) == set(history.COLUMNS)
        # Request the page carrying the malicious-looking authored fields.
        response = await history.export_csv(db, STAFF, ENV, "member", "one", question_id="@SUM(1,2)")
        row = list(csv.DictReader(io.StringIO(response.body.decode("utf-8-sig"))))[0]
        assert row["question_id"].startswith("'@") and row["kind"].startswith("'=")
        assert row["before"] == "" and row["status"] == "available"
    run_case(go)


def test_pair_picker_uses_existing_pairs_without_answers_or_email_addresses():
    async def go(db):
        await seed(db, [member("one", nickname="A"), member("two", nickname="B")], [], pair=True)
        result = await history.targets(db, STAFF, ENV)
        assert result == {"items": [{"pair_id": "pair", "state": "active", "label": "A · B"}], "next": None}
        assert (await history.targets(db, STAFF, ENV, q="not-a-pair"))["items"] == []
    run_case(go)


def test_role_and_bounds_reject_before_reading_private_records():
    db = SimpleNamespace()
    for person in [member(), {**STAFF, "closing": True}]:
        with pytest.raises(HTTPException) as error:
            asyncio.run(history.page(db, person, ENV, "member", "one"))
        assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        asyncio.run(history.page(db, STAFF, ENV, "member", "one", limit=101))
    assert error.value.status_code == 422
    for args in [("made-up", None, None, None), (None, None, "2026-11-31", None), (None, None, "2026-11-15", "2026-11-14")]:
        with pytest.raises(HTTPException):
            history._filters(*args)


class CountedDatabase:
    def __init__(self, db):
        self.real, self.calls, self.user_projections = db, Counter(), []
    def __getattr__(self, name):
        return self[name]
    def __getitem__(self, name):
        collection, calls, projections = self.real[name], self.calls, self.user_projections
        class Collection:
            def __getattr__(self, action):
                original = getattr(collection, action)
                def call(*args, **kwargs):
                    calls[action] += 1
                    if name == "users" and action in ("find", "find_one") and len(args) > 1:
                        projections.append(args[1])
                    return original(*args, **kwargs)
                return call
        return Collection()


@pytest.mark.parametrize("authorized", [False, True], ids=["metadata_only", "answers_with_context"])
def test_fifty_row_page_uses_bounded_batches_not_per_answer_rpcs(authorized):
    async def go(db):
        person = member()
        snapshot = observed(person, "demo", domain="demographics", question_id="demographics", after={"age": 30},
            at="2026-11-14T10:10:00+00:00", baseline=True)
        events = [snapshot]
        for n in range(50):
            events.append(observed(person, "old-" + str(n), question_id="e:q" + str(n), after={"choice": 0}, at="2026-11-14T10:30:00+00:00"))
            entry = observed(person, "new-" + str(n), question_id="e:q" + str(n), before_exists=True, before={"choice": 0},
                after={"choice": 1}, demographics={"snapshot_id": "demo"})
            if not authorized:
                entry["consent"] = {"env": ENV, "role": "member"}
            events.append(entry)
        await seed(db, [person], events)
        counted = CountedDatabase(db)
        result = await history.page(counted, STAFF, ENV, "member", "one", limit=50)
        assert len(result["items"]) == 50 and result["next"]
        assert sum(counted.calls.values()) <= 20, counted.calls
        assert all("about.answers" not in projection and "about" not in projection for projection in counted.user_projections)
        if authorized:
            assert all(row["before"] == {"choice": 0} and row["demographics_at_observation"] == {"age": 30} for row in result["items"])
        else:
            assert all(row["status"] == "withheld" and "after" not in row for row in result["items"])
    run_case(go)


@pytest.mark.parametrize("change", ["withdraw_permission", "purge_event", "queue_demographic_erasure"])
def test_mid_batch_revocation_or_erasure_is_rechecked_after_payload_prefetch(change):
    async def go(db):
        person = member()
        snapshot = observed(person, "demo", domain="demographics", question_id="demographics", after={"age": 25})
        event = observed(person, demographics={"snapshot_id": "demo"})
        await seed(db, [person], [snapshot, event])
        intercepted = False
        class Cursor:
            def __init__(self, real): self.real = real
            def limit(self, count): self.real = self.real.limit(count); return self
            async def to_list(self, count):
                nonlocal intercepted
                rows = await self.real.to_list(count)
                if not intercepted:
                    intercepted = True
                    if change == "withdraw_permission":
                        await db.users.update_one({"user_id": "one"}, {"$unset": {"answer_history_staff_version": ""}})
                    elif change == "purge_event":
                        await db.answer_history.replace_one({"id": "event-one"}, {"id": "event-one", "purged": True})
                    else:
                        await db.users.update_one({"user_id": "one"}, {"$set": {answer_history.REDACTIONS: [{"question_id": "demographics", "through_generation": 0}]}})
                return rows
        class History:
            def __getattr__(self, name): return getattr(db.answer_history, name)
            def find(self, query, projection=None):
                cursor = db.answer_history.find(query, projection)
                return Cursor(cursor) if projection == {"_id": 0} and "id" in query else cursor
        class Database:
            answer_history = History()
            def __getattr__(self, name): return getattr(db, name)
            def __getitem__(self, name): return self.answer_history if name == "answer_history" else db[name]
        result = await history.page(Database(), STAFF, ENV, "member", "one")
        assert intercepted
        if change == "withdraw_permission":
            assert all(row["status"] == "withheld" and "after" not in row for row in result["items"])
        elif change == "purge_event":
            assert "event-one" not in [row["event_id"] for row in result["items"]]
        else:
            row = next(row for row in result["items"] if row["event_id"] == "event-one")
            assert row["demographics_at_observation"] is None
            assert "demo" not in [item["event_id"] for item in result["items"]]
    run_case(go)


def test_indexed_owner_predicates_do_not_scan_unrelated_accounts_history():
    async def go(db):
        person = member()
        await seed(db, [person], [observed(person)])
        foreign = member("another-account")
        await db.answer_history.insert_many([{"_id": "foreign-" + str(n), **observed(foreign, "foreign-" + str(n))} for n in range(150)])
        # On actual PostgreSQL an unqualified history scan would fail this
        # repository limit. The SQL owner candidates exclude the150 other rows.
        db.max_scan_records = 80
        result = await history.page(db, STAFF, ENV, "member", "one")
        assert [row["event_id"] for row in result["items"]] == ["event-one"]
    run_case(go)
