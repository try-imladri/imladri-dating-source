"""Current Couples sponsorship and streamed research withdrawal on isolated PG."""
from datetime import datetime, timezone
import json

import pytest

from backend import couples, couples_analytics, research_couple_export as export, research_rows
from backend.couples_journey import Action, DraftIn, PROMPTS, change_thread, save_draft
from backend.tests.test_complimentary_couples import exercise, grant, member, pair, paid


STAMP = "2026-01-01T00:00:00+00:00"
PRIVATE = "AUDIT_PRIVATE_FREE_TEXT_NEVER_EXPORTED"
ELIGIBLE = {"env": {"$in": ["preview", None]}, "closing": {"$ne": True}, "role": {"$ne": "admin"}}


@pytest.mark.parametrize("access,expected", [
    ("complimentary", 1), ("paid", 1), ("revoked", 0),
    ("wrong_role", 0), ("nonboolean_active", 0),
])
def test_operational_sponsor_count_uses_valid_access_without_inventing_paid_conversions(access, expected):
    async def case(db):
        owner = member("comp-owner", complimentary_entitlement=grant())
        if access == "paid":
            owner = member("comp-owner", **paid())
        elif access == "revoked":
            owner["complimentary_entitlement"]["active"] = False
        elif access == "wrong_role":
            owner["role"] = "coach"
        elif access == "nonboolean_active":
            owner["complimentary_entitlement"]["active"] = 1
        await pair(db, owner=owner, benefit_members=["comp-owner", "comp-guest"])
        data = await couples_analytics.report(db, "preview", ELIGIBLE, 7)
        assert data["shared_features"]["active_pairs"] == 1
        assert data["membership"]["sponsored_pairs"] == expected
        assert data["membership"]["partner_upgrades"] == data["membership"]["annual_payments"] == 0
        assert PRIVATE not in json.dumps(data)
    exercise(case)


async def released_pair(db):
    first, second, record = await pair(db)
    for person, code in ((first, "1"), (second, "2")):
        await db.users.update_one({"user_id": person["user_id"]}, {"$set": {
            "row_couple_code": code * 32, "row_couple_version": research_rows.VERSIONS["couple"],
            "row_couple_env": "preview", "row_couple_pair_id": record["_id"], "row_couple_at": STAMP,
            "row_couple_revision": 1, "row_member_revision": 1,
            "row_member_code": ("3" if code == "1" else "4") * 32,
            "row_member_version": research_rows.VERSIONS["member"], "row_member_env": "preview",
        }})
    prompt = next(p for p in PROMPTS if p["id"] == "support-1")
    for version, person in enumerate((first, second)):
        chosen = prompt["choices"][version]
        await save_draft(db, person, prompt["id"], DraftIn(version=0, lines=[chosen, PRIVATE],
                         choice=chosen, expected_member=person["user_id"]))
        await change_thread(db, person, prompt["id"], Action(pair_id=record["_id"], version=version,
                            action="share", kind="prompt", lines=[chosen]))
    return first, second, record


def test_unchanged_stream_keeps_all_structured_rows_and_never_releases_private_text():
    async def case(db):
        await released_pair(db)
        rows = [r async for r in export.iter_rows(db, ELIGIBLE, STAMP)]
        assert len([r for r in rows if r["variable_id"] == "c:prompt:support-1"]) == 2
        assert rows[0]["variable_id"] == "c:active"
        assert {r["member_code"] for r in rows if r["domain"] == "demographics"} == {"m_" + "3"*32, "m_" + "4"*32}
        assert all(secret not in json.dumps(rows) for secret in (PRIVATE, "comp-owner", "comp-guest", "comp-pair"))
    exercise(case)


def test_consent_withdrawn_during_awaited_source_delivery_emits_nothing(monkeypatch):
    async def case(db):
        _, second, _ = await released_pair(db)
        original = export.candidate_pairs
        changed = []
        def query(store, eligible, content, pair_id=None):
            async def cursor():
                async for row in original(store, eligible, content, pair_id):
                    if content and not changed:
                        changed.append(True)
                        await db.users.update_one({"user_id": second["user_id"]},
                            {"$unset": {"row_couple_version": "", "row_couple_code": ""}})
                    yield row
            return cursor()
        monkeypatch.setattr(export, "candidate_pairs", query)
        assert [r async for r in export.iter_rows(db, ELIGIBLE, STAMP)] == []
        assert changed == [True]
    exercise(case)


@pytest.mark.parametrize("change", ["withdraw", "grant_revision", "grant_code", "member_link",
    "block", "minor", "close", "source_revision", "shared_choice", "demographics", "tone_grant"])
def test_every_stream_emit_rechecks_current_grants_pair_and_typed_source_snapshot(change):
    async def case(db):
        first, second, record = await released_pair(db)
        stream = export.iter_rows(db, ELIGIBLE, STAMP)
        assert (await anext(stream))["variable_id"] == "c:active"
        if change == "withdraw":
            await db.users.update_one({"user_id": second["user_id"]}, {"$unset": {"row_couple_version": ""}})
        elif change == "grant_revision":
            await db.users.update_one({"user_id": second["user_id"]}, {"$inc": {"row_couple_revision": 1}})
        elif change == "grant_code":
            await db.users.update_one({"user_id": second["user_id"]}, {"$set": {"row_couple_code": "5" * 32}})
        elif change == "member_link":
            await db.users.update_one({"user_id": second["user_id"]}, {"$unset": {"row_member_version": ""}})
        elif change == "block":
            await db.users.update_one({"user_id": second["user_id"]}, {"$set": {"couples_blocked": [couples.block_key(first["user_id"])]}})
        elif change == "minor":
            await db.users.update_one({"user_id": second["user_id"]}, {"$set": {"about.demographics.age": 17}})
        elif change == "close":
            await db.couples_pairs.update_one({"_id": record["_id"]}, {"$set": {"state": "closed"}})
        elif change == "source_revision":
            await db.couples_pairs.update_one({"_id": record["_id"]}, {"$inc": {"journey_version": 1}})
        elif change == "shared_choice":
            await db.couples_pairs.update_one({"_id": record["_id"]}, {"$set": {"journey.threads.support-1.shared_choices.b.index": 0}})
        elif change == "demographics":
            await db.users.update_one({"user_id": second["user_id"]}, {"$set": {"about.demographics.age": 44}})
        else:
            await db.users.update_one({"user_id": second["user_id"]}, {"$inc": {"tone_research_revision": 1}})
        assert [r async for r in stream] == []
    exercise(case)


def test_pair_replacement_during_stream_does_not_release_another_pairs_rows():
    async def case(db):
        first, second, record = await released_pair(db)
        old_code = research_rows.pair_code(record["_id"], "1"*32, "2"*32)
        stream = export.iter_rows(db, ELIGIBLE, STAMP, old_code)
        assert (await anext(stream))["unit_code"] == old_code
        await db.couples_pairs.update_one({"_id": record["_id"]}, {"$set": {"state": "closed"}, "$unset": {"members": ""}})
        await db.couples_pairs.insert_one({"_id": "replacement-pair", "env": "preview", "state": "active",
            "members": [first["user_id"], second["user_id"]], "inviter": first["user_id"], "invitee": second["user_id"],
            "sponsor": first["user_id"], "joined_at": datetime.now(timezone.utc).isoformat()})
        await db.users.update_many({"user_id": {"$in": [first["user_id"], second["user_id"]]}},
            {"$set": {"row_couple_pair_id": "replacement-pair"}})
        assert [r async for r in stream] == []
        fresh = [r async for r in export.iter_rows(db, ELIGIBLE, STAMP)]
        assert fresh and all(r["unit_code"] != old_code for r in fresh)
    exercise(case)
