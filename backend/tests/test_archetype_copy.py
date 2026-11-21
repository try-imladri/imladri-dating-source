"""Grounded derived captions, genuine PostgreSQL persistence and owner reads."""
from copy import deepcopy
import json

import httpx
import pytest

from backend import archetype_copy as copy
from backend.tests.test_prompt_default_revisions import run_case


ROSTER = [{"number": 19, "name": "Slow Burn", "slug": "slow-burn", "one_liner": "Takes time and follows through."},
          {"number": 46, "name": "Grown Man", "slug": "grown-man", "one_liner": "Clear, consistent, calm."}]
RAW = "Your stated calm, direct style may loosely fit this comparison."


@pytest.fixture
def api(monkeypatch):
    from backend import database
    monkeypatch.setattr(database, "open_database", lambda: (None, None))
    from backend import server
    return server


def assert_history(caption, raw):
    assert caption["historical_unverified_rationales"][-1] == {
        "status": "historical_unverified_AI_text", "note": copy.HISTORY_NOTE, "line": raw}


def test_generated_ranking_keeps_order_scores_catalog_and_unverified_raw(api):
    raw = {"items": [{"number": 19, "score": 45, "line": "You prefer taking your time."},
                     {"number": 46, "score": 70, "line": RAW}]}
    before = deepcopy(raw)
    ranked = api.normalize_ranking(raw, ROSTER)
    assert [(r["number"], r["score"]) for r in ranked] == [(46, 70), (19, 45)]
    assert ranked[0]["line"] == copy.catalog_line(ROSTER[1])
    assert "Your stated" not in ranked[0]["line"]
    assert_history(ranked[0], RAW)
    assert raw == before


def test_legacy_projection_is_pure_preserves_answers_and_history():
    original = {"about": {"answers": {"q": {"choice": 1, "note": "private answer"}}, "describe": "Own words",
        "type_compare": {"sig": "same", "items": [{"number": 46, "score": 70, "line": RAW}]}}}
    before = deepcopy(original)
    shown = copy.account(original)
    assert shown["about"]["answers"] == original["about"]["answers"]
    assert shown["about"]["type_compare"]["items"][0]["line"] == copy.NEUTRAL
    assert_history(shown["about"]["type_compare"]["items"][0], RAW)
    assert original == before
    assert copy.account(shown) == shown


def test_catalog_refresh_preserves_verified_catalog_provenance():
    old = copy.new_caption(ROSTER[0], raw=RAW)
    current = {**ROSTER[0], "one_liner": "A revised catalog description."}
    shown = copy.ranking_cache({"items": [{"number": 19, "score": 4, **old}]}, [current])["items"][0]
    assert shown["line"] == copy.catalog_line(current)
    assert shown["historical_catalog_rationales"] == [{"status": "historical_catalog_caption",
        "line_policy": copy.POLICY, "catalog_basis": copy.basis(ROSTER[0]), "line": old["line"]}]
    assert_history(shown, RAW)


@pytest.mark.parametrize("match", [{}, {"line": "", "failed": True}, {"line": "", "failed": False},
                                    {"stale": True}, {"line": RAW, "failed": True}])
def test_failed_empty_or_stale_never_becomes_a_completed_caption(match):
    shown = copy.person_match(match)
    if match.get("failed") is True:
        assert shown["failed"] is True and shown["line"] == ""
    else:
        assert shown == match


def test_policy_marker_cannot_authorize_arbitrary_active_wording():
    match = {"code": "ISFP", "tag": 19, **copy.new_caption(ROSTER[0]), "line": RAW}
    shown = copy.person_match(match)
    assert shown["line"] == copy.catalog_line(ROSTER[0], 200)
    assert_history(shown, RAW)


def test_export_serializer_projects_account_and_person_without_mutating_answers():
    from backend import accounts
    originals = [{"about": {"answers": {"q": {"note": "unaltered"}},
        "type_compare_female": {"items": [{"number": 46, "score": 50, "line": RAW}]}}},
        {"id": "p", "type_match": {"line": RAW, "failed": False}}]
    for original in originals:
        before = deepcopy(original)
        exported = json.loads(accounts.dump_json(original))
        match = exported.get("type_match") or exported["about"]["type_compare_female"]["items"][0]
        assert match["line"] == copy.NEUTRAL
        assert_history(match, RAW)
        assert original == before
    assert json.loads(accounts.dump_json({"ordinary": "unchanged"})) == {"ordinary": "unchanged"}


def test_actual_postgres_ranking_persist_cache_and_owner_about_routes(api, monkeypatch):
    async def case(db):
        monkeypatch.setattr(api, "db", db)
        uid = "grounding-owner"
        member = {"user_id": uid, "role": "member", "about": {"mbti": "ISFP", "describe": "I value planning.",
            "updated_at": "fixture-1", "answers": {}}}
        await db.users.insert_one(deepcopy(member))
        await db.archetypes.insert_many([{**row, "gender": "male", "unlocked": True} for row in ROSTER])
        calls = []

        async def owner():
            return await db.users.find_one({"user_id": uid})

        async def eligible(_member, **kwargs):
            return None

        async def payer(_db, own):
            return own

        async def generated(*args, **kwargs):
            calls.append((args, kwargs))
            return {"items": [{"number": r["number"], "score": 60 + i, "line": RAW} for i, r in enumerate(ROSTER)]}

        monkeypatch.setattr(api, "ai_json", generated)
        monkeypatch.setattr(api, "require_generation_access", eligible)
        monkeypatch.setattr(api.workspace_access, "assessment_payer", payer)
        monkeypatch.setitem(api.app.dependency_overrides, api.assessment_member, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://fixture.test") as client:
            first = await client.get("/api/me/type-compare")
            assert first.status_code == 200
            stored = (await owner())["about"]["type_compare"]
            assert stored["items"][0]["number"] == 46 and stored["items"][0]["score"] == 61
            assert stored["items"][0]["line"] == copy.catalog_line(ROSTER[1])
            assert_history(stored["items"][0], RAW)
            assert len(calls) == 1
            # Historical cache reuse has no generation or cache-repair write.
            await db.users.update_one({"user_id": uid}, {"$set": {"about.type_compare.items.0.line": RAW}})
            legacy = await owner()
            second = await client.get("/api/me/type-compare")
            assert second.status_code == 200 and second.json()["items"][0]["line"] == copy.catalog_line(ROSTER[1])
            assert len(calls) == 1 and await owner() == legacy
            about = await client.get("/api/me/about")
            assert about.status_code == 200
            assert about.json()["about"]["type_compare"]["items"][0]["line"] == copy.catalog_line(ROSTER[1])
            saved = await client.put("/api/me/about", json={"mbti": "ISFP", "expected_member": uid})
            assert saved.status_code == 200
            assert saved.json()["about"]["type_compare"]["items"][0]["line"] == copy.catalog_line(ROSTER[1])
            assert (await owner())["about"]["type_compare"]["items"][0]["line"] == RAW
            wrong = await client.put("/api/me/about", json={"mbti": "INTJ", "expected_member": "different-owner"})
            assert wrong.status_code == 409 and (await owner())["about"]["mbti"] == "ISFP"
            assert (await owner())["about"]["answers"] == {}
            # A changed source during generation cannot create a stale ranking.
            await db.users.update_one({"user_id": uid}, {"$unset": {"about.type_compare": ""}})
            async def changed_source(*args, **kwargs):
                await db.users.update_one({"user_id": uid}, {"$set": {"about.describe": "A different current preference."}})
                return {"items": [{"number": r["number"], "score": 70, "line": RAW} for r in ROSTER]}
            monkeypatch.setattr(api, "ai_json", changed_source)
            changed = await client.get("/api/me/type-compare")
            assert changed.status_code == 409
            assert "type_compare" not in (await owner())["about"]
    run_case(case)


def test_actual_postgres_background_and_person_read_export_preserve_state(api, monkeypatch):
    async def case(db):
        from backend import accounts
        monkeypatch.setattr(api, "db", db)
        uid, pid = "grounding-owner", "grounding-person"
        member = {"user_id": uid, "about": {"mbti": "ISFP", "describe": "I value planning."}}
        await db.users.insert_one(member)
        await db.people.insert_one({"id": pid, "user_id": uid, "nickname": "Fictional Alex", "rel_type": "partner", "archetype_tag": 19})
        await db.archetypes.insert_one({**ROSTER[0], "unlocked": True})
        calls = []

        async def allowance(_):
            return True

        async def generated(*args, **kwargs):
            calls.append(args)
            return {"line": RAW}

        monkeypatch.setattr(api, "has_reply_allowance", allowance)
        monkeypatch.setattr(api, "ai_json", generated)
        await api.type_match_task(pid, "ISFP", 19)
        person = await db.people.find_one({"id": pid})
        expected_sig = api.context_signature(api.saved_profile_text(*await api.saved_profile(member, (await api.get_settings())["quiz"])))
        assert person["type_match"]["profile_sig"] == expected_sig
        assert person["type_match"]["code"] == "ISFP" and person["type_match"]["tag"] == 19
        assert person["type_match"]["started"] and person["type_match"]["failed"] is False
        assert person["type_match"]["line"] == copy.catalog_line(ROSTER[0], 200)
        assert_history(person["type_match"], RAW)
        before = deepcopy(person)
        await db.people.update_one({"id": pid}, {"$set": {"type_match.line": RAW}, "$unset": {"type_match.line_policy": "", "type_match.catalog_basis": ""}})
        legacy = await db.people.find_one({"id": pid})
        shown = await api.person_out(legacy, expected_sig)
        assert shown["type_match"]["line"] == copy.NEUTRAL and len(calls) == 1
        exported = json.loads("".join([part async for part in accounts.person_export_json(db, legacy)]))
        assert exported["person"]["type_match"]["line"] == copy.NEUTRAL
        assert await db.people.find_one({"id": pid}) == legacy
        assert (await api.person_out(legacy, "new-profile"))["type_match"] == {"stale": True}
        assert (await api.get_person(pid, member))["type_match"]["line"] == copy.NEUTRAL
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as other_owner:
            await api.get_person(pid, {"user_id": "different-owner"})
        assert other_owner.value.status_code == 404
        # A later owned result wins against an older in-flight generation.
        future = {**before["type_match"], "started": "9999-12-31T00:00:00+00:00", "tag": 19}
        async def late(*args, **kwargs):
            await db.people.update_one({"id": pid}, {"$set": {"type_match": future}})
            return {"line": "An older generated caption."}
        monkeypatch.setattr(api, "ai_json", late)
        await api.type_match_task(pid, "ISFP", 19)
        assert (await db.people.find_one({"id": pid}))["type_match"] == future
        # Empty generation stays failed, not a successful catalog comparison.
        await db.people.update_one({"id": pid}, {"$unset": {"type_match": ""}})
        async def empty(*args, **kwargs):
            return {"line": ""}
        monkeypatch.setattr(api, "ai_json", empty)
        await api.type_match_task(pid, "ISFP", 19)
        failed = (await db.people.find_one({"id": pid}))["type_match"]
        assert failed["failed"] is True and failed["line"] == "" and "catalog_basis" not in failed
    run_case(case)
