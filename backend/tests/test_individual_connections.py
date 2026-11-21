import asyncio
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection


@pytest.fixture
def isolated_member(monkeypatch):
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("A disposable loopback MongoDB is required.")
    assert urlsplit(uri).hostname in {"127.0.0.1", "localhost", "::1"}
    name = "individual_connections_" + uuid4().hex
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", name)
    from backend import server

    async def run(check):
        mongo = AsyncIOMotorClient(uri)
        db = mongo[name]
        monkeypatch.setattr(server, "db", db)
        member = {"user_id": "synthetic-member"}
        person = {**member, "id": "first", "nickname": "Synthetic", "rel_type": "friend", "created_at": server.now()}
        try:
            # Direct route calls still require the live owner row that
            # current_member supplies in authenticated application requests.
            await db.users.insert_one({**member, "role": "member", "env": server.ENV, "about": {}})
            await db.people.insert_one(dict(person))
            await check(server, db, member, person)
        finally:
            await mongo.drop_database(name)
            mongo.close()
    return lambda check: asyncio.run(run(check))


def test_deleted_plan_purges_automatic_context_and_retains_member_edits(isolated_member):
    async def check(server, db, member, person):
        owner = {**member, "person_id": person["id"]}
        linked = {**owner, "prep_id": "plan", "kind": "outcome"}
        await db.preps.insert_one({**owner, "id": "plan", "created_at": server.now(), "situation": "old situation"})
        await db.memories.insert_one({**linked, "id": "edited", "text": "my separate note", "edited_at": server.now(),
                                      "source": "Outcome of game plan: old situation", "created_at": server.now()})
        await server.prep_outcome("plan", server.TextIn(text="private outcome"), BackgroundTasks(), member)
        assert "private outcome" in await server.person_context(person)
        await db.people.update_one({"id": person["id"]}, {"$set": {"report": {"old": "private outcome"}, "report_run": "old"}})
        await db.notifications.insert_one({**member, "key": "checkin:plan"})
        await db.memories.insert_one({**linked, "id": "legacy", "text": "older update", "created_at": server.now()})
        await db.memories.insert_one({**linked, "user_id": "different-owner", "person_id": "different-person",
                                      "id": "other", "text": "unrelated", "created_at": server.now()})
        await server.delete_prep("plan", member)
        assert await db.preps.count_documents(owner) == 0
        assert await db.notifications.count_documents({**member, "key": "checkin:plan"}) == 0
        ctx = await server.person_context(person)
        assert "private outcome" not in ctx and "older update" not in ctx and "my separate note" in ctx
        edited = await db.memories.find_one({"id": "edited"})
        assert "prep_id" not in edited and "old situation" not in edited["source"]
        assert await db.memories.find_one({"id": "other"})
        saved = await db.people.find_one({"id": person["id"]})
        assert not saved["report"] and "report_run" not in saved
    isolated_member(check)


def test_legacy_orphan_repair_is_safe_and_repeatable(isolated_member):
    async def check(server, db, member, person):
        owner = {**member, "person_id": person["id"]}
        await db.preps.insert_one({**owner, "id": "existing"})
        base = {**owner, "kind": "outcome", "text": "member text"}
        await db.memories.insert_many([{**base, "id": "auto", "prep_id": "removed"},
            {**base, "id": "edited", "prep_id": "removed", "edited_at": server.now()},
            {**base, "id": "existing", "prep_id": "existing"}, {**base, "id": "unattributed"}])
        await server.game_plans.repair_deleted_outcomes(db)
        await server.game_plans.repair_deleted_outcomes(db)
        assert set(await db.memories.distinct("id")) == {"edited", "existing", "unattributed"}
        assert "prep_id" not in await db.memories.find_one({"id": "edited"})
    isolated_member(check)


def test_deleting_during_an_outcome_write_does_not_recreate_the_memory(isolated_member, monkeypatch):
    async def check(server, db, member, person):
        owner = {**member, "person_id": person["id"]}
        await db.preps.insert_one({**owner, "id": "plan", "created_at": server.now(), "situation": "private situation"})
        pending, resume = asyncio.Event(), asyncio.Event()
        collection_type = type(db.memories)
        insert = collection_type.insert_one

        async def delayed(collection, document, *args, **kwargs):
            if collection.name == "memories":
                pending.set()
                await resume.wait()
            return await insert(collection, document, *args, **kwargs)

        monkeypatch.setattr(collection_type, "insert_one", delayed)
        update = asyncio.create_task(server.prep_outcome("plan", server.TextIn(text="private outcome"), BackgroundTasks(), member))
        try:
            await asyncio.wait_for(pending.wait(), timeout=5)
            await server.delete_prep("plan", member)
        finally:
            resume.set()
        with pytest.raises(HTTPException) as changed:
            await update
        assert changed.value.status_code == 409
        assert not await db.memories.find_one(owner)
        assert "private outcome" not in await server.person_context(person)
    isolated_member(check)


def test_overview_counts_real_window_and_includes_group_and_practice_links(isolated_member):
    async def check(server, db, member, person):
        now = server.now()
        owner = {**member, "person_id": person["id"], "created_at": now}
        await db.people.insert_one({**person, "id": "second"})
        await db.exchanges.insert_many([{**owner, "id": str(i)} for i in range(501)])
        await db.practice.insert_one({**owner, "id": "session"})
        await db.group_chats.insert_one({**member, "person_ids": ["first", "second"], "created_at": now})
        old = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
        await db.practice.insert_one({**owner, "created_at": old})
        await db.practice.insert_one({**owner, "user_id": "other"})
        overview = await server.overview(member)
        assert sum(day["count"] for day in overview["activity"]) == 505
        # Make the two actionable items newer than the reads for deterministic ordering.
        await db.exchanges.delete_many(owner)
        items = (await server.overview(member))["feed"]
        assert any(item["tab"] == "group" and item["person_ids"] == ["first", "second"] for item in items)
        assert any(item["tab"] == "practice" and item["person_id"] == "first" for item in items)
    isolated_member(check)


def test_coaching_uses_most_recent_300_memories_in_chronological_order(isolated_member):
    async def check(server, db, member, person):
        start = datetime.now(timezone.utc)
        await db.memories.insert_many([{**member, "person_id": person["id"], "id": str(i),
            "kind": "event", "text": f"memory-{i:03d}", "created_at": (start + timedelta(seconds=i)).isoformat()}
            for i in range(301)])
        context = await server.person_context(person)
        assert "memory-000" not in context
        assert 0 < context.index("memory-001") < context.index("memory-300")
        await db.memories.update_one({"id": "0"}, {"$set": {"edited_at": (start + timedelta(days=1)).isoformat()}})
        context = await server.person_context(person)
        assert "memory-001" not in context
        assert context.index("memory-300") < context.index("memory-000")
    isolated_member(check)


def test_plan_only_consent_revision_and_removal_across_a_retry(isolated_member, monkeypatch):
    async def check(server, db, member, person):
        owner = {**member, "person_id": person["id"]}
        await db.preps.insert_one({**owner, "id": "plan", "created_at": server.now(), "situation": "Need notice"})
        private = server.PlanOutcomeIn(text="Rescheduled for Friday", use_for_coaching=False, expected_revision=0)
        bg = BackgroundTasks()
        saved = await server.prep_outcome("plan", private, bg, member)
        assert saved["outcome_revision"] == 1 and not bg.tasks
        assert await server.prep_outcome("plan", private, bg, member) == saved
        assert "Rescheduled for Friday" not in await server.person_context(person)
        share = private.model_copy(update={"use_for_coaching": True, "expected_revision": 1})
        await server.prep_outcome("plan", share, BackgroundTasks(), member)
        source = await db.memories.find_one(owner)
        assert source["text"] in await server.person_context(person)
        with pytest.raises(HTTPException) as conflict:
            await server.prep_outcome("plan", private, BackgroundTasks(), member)
        assert conflict.value.status_code == 409
        await db.memories.insert_many([{**owner, "id":"child", "kind":"interpretation", "text":"automatic", "derived_from":[source["id"]]},
                                       {**owner, "id":"grandchild", "kind":"assessment", "text":"automatic", "derived_from":["child"]},
                                       {**owner, "id":"my-edit", "kind":"event", "text":"my own note", "edited_at":server.now(), "derived_from":["child"]}])
        original = server.game_plans.clear_outcome_memories
        async def interrupted(*args):
            raise RuntimeError("temporary service interruption")
        monkeypatch.setattr(server.game_plans, "clear_outcome_memories", interrupted)
        unshare = private.model_copy(update={"expected_revision":2})
        with pytest.raises(RuntimeError):
            await server.prep_outcome("plan", unshare, BackgroundTasks(), member)
        monkeypatch.setattr(server.game_plans, "clear_outcome_memories", original)
        assert (await server.prep_outcome("plan", unshare, BackgroundTasks(), member))["outcome_revision"] == 3
        assert await db.memories.distinct("id", owner) == ["my-edit"]
        assert not (await db.memories.find_one({"id":"my-edit"}))["derived_from"]
        await server.prep_outcome("plan", share.model_copy(update={"expected_revision":3}), BackgroundTasks(), member)
        restored = await db.memories.find_one({**owner, "kind":"outcome"})
        await server.delete_memory(restored["id"], BackgroundTasks(), member)
        still_private = await db.preps.find_one(owner)
        assert still_private["outcome"] == private.text and not still_private["outcome_use_for_coaching"]
        await server.remove_plan_outcome("plan", still_private["outcome_revision"], member)
        assert not (await db.preps.find_one(owner))["outcome"]
    isolated_member(check)


def test_memory_provenance_priority_paging_and_owner_isolation(isolated_member):
    async def check(server, db, member, person):
        pid = person["id"]
        owner = {**member, "person_id": pid}
        await db.memories.insert_many([{**owner, "id":f"m{i:04}", "created_at":f"2026-09-01T{i:04}", "kind":"event", "text":f"source {i}"} for i in range(1005)])
        await db.memories.insert_one({**owner, "user_id":"other", "id":"foreign", "kind":"event", "text":"excluded"})
        await server.prioritize_memory("m0000", server.MemoryPriority(prioritized=True), member)
        sources = {}
        ctx = await server.person_context(person, source_map=sources, cite_sources=True)
        ids = [v["id"] for v in sources.values()]
        assert len(ids) == 300 and "m0000" in ids and "m0001" not in ids and "foreign" not in ids and "[S1]" in ctx
        await db.exchanges.insert_one({**owner,"id":"ex","memory_context_ids":ids})
        await db.exchanges.insert_one({**owner,"id":"old"})
        assert not (await server.exchange_memories("old",member))["recorded"]
        await db.memories.delete_one({**owner,"id":"m0000"})
        exposed = await server.exchange_memories("ex", member)
        assert exposed["removed"] == 1 and len(exposed["items"]) == 299
        assert (await server.read_person_evidence(pid,"memory",ids[0] if ids[0] != "m0000" else ids[1],member))["text"]
        with pytest.raises(HTTPException) as missing:
            await server.read_person_evidence(pid,"memory","foreign",member)
        assert missing.value.status_code == 404
        seen, cursor = set(), ""
        while True:
            page = await server.list_memories(pid,member,100,cursor)
            assert page["total"] == 1004
            seen.update(item["id"] for item in page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert len(seen) == 1004 and "foreign" not in seen
    isolated_member(check)


def test_two_devices_edit_profile_and_compare_rehearsal_privately(isolated_member):
    async def check(server, db, member, person):
        pid = person["id"]
        first = server.PersonPatch(nickname="New name", expected_profile_revision=0)
        updated = await server.patch_person(pid,first,member)
        assert updated["profile_revision"] == 1
        assert (await server.patch_person(pid,first,member))["profile_revision"] == 1
        with pytest.raises(HTTPException) as stale:
            await server.patch_person(pid,first.model_copy(update={"nickname":"Other device"}),member)
        assert stale.value.status_code == 409
        keys = list(server.FIELD_LABELS)[:2]
        for key, value in zip(keys, ["First note","Second note"]):
            await server.patch_intake(pid,server.IntakeIn(fields={key:value}, previous={key:""}),BackgroundTasks(),member)
        with pytest.raises(HTTPException) as conflict:
            await server.patch_intake(pid,server.IntakeIn(fields={keys[0]:"Stale"}, previous={keys[0]:""}),BackgroundTasks(),member)
        assert conflict.value.status_code == 409
        assert (await db.people.find_one({"id":pid}))["intake"] == dict(zip(keys,["First note","Second note"]))
        history = await db.answer_history.find({"owner_id": member["user_id"], "domain": "person_intake"}).to_list(None)
        assert len(history) == 2
        assert {row["after"]["text"] for row in history} == {"First note", "Second note"}
        assert all(row["research_eligible"] is False and row["observation_subject"] == "third_party" for row in history)
        assert all(set(row["demographics"]) == {"snapshot_id", "observed_at"} for row in history)
        snapshots = await db.answer_history.find({"owner_id": member["user_id"], "domain": "demographics"}).to_list(None)
        assert len(snapshots) == 1 and snapshots[0]["after"] == {} and snapshots[0]["baseline"] is True
        rehearsal = {**member, "person_id":pid, "id":"rehearsal", "ended":True, "messages":[], "rounds":[{"picked":1,"options":[{"reaction":"Friday?"},{"reaction":"Saturday?"}]}]}
        await db.practice.insert_one(rehearsal)
        body = server.PracticeReflection(result="different", note="A private weekend observation", round_index=0, expected_revision=0)
        result = await server.save_practice_reflection("rehearsal",body,member)
        assert result["reflection"]["branch"] == {"round":1,"reaction":"Saturday?"}
        assert (await server.save_practice_reflection("rehearsal",body,member))["reflection_revision"] == 1
        assert body.note not in await server.person_context(person)
        for actor, expected in [(member,409),({"user_id":"outsider"},404)]:
            with pytest.raises(HTTPException) as failure:
                await server.save_practice_reflection("rehearsal",body.model_copy(update={"result":"similar"}),actor)
            assert failure.value.status_code == expected
        await db.practice.update_one({"id":"rehearsal"},{"$set":{"rounds.0.picked":0,"turn_revision":1}})
        with pytest.raises(HTTPException) as replayed:
            await server.save_practice_reflection("rehearsal",body.model_copy(update={"expected_turn_revision":0,"expected_revision":1}),member)
        assert replayed.value.status_code == 409
        amended = server.PracticeReflection(result="similar",note="Updated what happened",keep_saved_branch=True,expected_revision=1)
        kept = await server.save_practice_reflection("rehearsal",amended,member)
        assert kept["reflection"]["branch"] == {"round":1,"reaction":"Saturday?"}
        fresh = await server.save_practice_reflection("rehearsal",body.model_copy(update={"expected_turn_revision":1,"expected_revision":2}),member)
        assert fresh["reflection"]["branch"] == {"round":1,"reaction":"Friday?"}
        removed = await server.save_practice_reflection("rehearsal",server.PracticeReflection(expected_revision=3),member)
        assert removed["reflection"] is None and removed["reflection_revision"] == 4
    isolated_member(check)


def test_intake_history_rejects_a_missing_or_closing_owner_without_mutating_the_person(isolated_member):
    async def check(server, db, member, person):
        key = next(iter(server.FIELD_LABELS))
        body = server.IntakeIn(fields={key: "Must not be accepted"}, previous={key: ""})
        await db.users.update_one(member, {"$set": {"closing": True}})
        for missing in (False, True):
            if missing:
                await db.users.delete_one(member)
            with pytest.raises(HTTPException) as unavailable:
                await server.patch_intake(person["id"], body, BackgroundTasks(), member)
            assert unavailable.value.status_code == 409
            saved = await db.people.find_one({"id": person["id"]})
            assert not saved.get("intake")
            assert await db.answer_history.count_documents({}) == 0

    isolated_member(check)


def test_rehearsal_respects_real_contact_changes_across_saved_scenarios(isolated_member, monkeypatch):
    async def check(server, db, member, person):
        from unittest.mock import AsyncMock
        provider = AsyncMock()
        monkeypatch.setattr(server, "ai_json", provider)
        pid = person["id"]
        owner = {**member, "person_id":pid}
        turn = {"id":"session", **owner, "mode":"likely", "topic":"How shall we meet?", "created_at":"2026-01-01",
                "messages":[{"role":"them","text":"You can text me again."}], "ended":True,
                "rounds":[{"picked":0,"msg_index":0,"options":[{"reaction":"Meet me.","suggested":"Keep writing."}]}],
                "options":[{"reaction":"Meet me.","suggested":"Keep writing."}], "feedback":{"cleaned":"Keep writing."}}
        await db.practice.insert_one(turn)
        allowed = await server.get_practice("session",member)
        assert allowed["options"] and "contact_pause" not in allowed
        mid = await server.practice_say("session",server.TextIn(text="They asked me to stop messaging."),None,member)
        assert mid["contact_pause"] and not any(mid[k] for k in ["rounds","messages","options","feedback"])
        for response in [await server.practice_pick("session",server.PickIn(index=0),member),
                         await server.practice_branch("session",server.BranchIn(round=0,index=0),member),
                         await server.get_practice("session",member),
                         (await server.list_practice(pid,member,0,""))[0]]:
            assert not response["options"] and response["contact_pause"] and "Keep writing." not in str(response)
        provider.assert_not_awaited()
        assert not (await db.practice.find_one({"id":"session"})).get("free_reply_ids")
        await db.exchanges.insert_one({**owner,"id":"new","created_at":"9999-01-01","contact_update":"requested","response":{"boundary_evidence":"Don't contact me."},
             "conversation":[{"speaker":"them","text":"Don't contact me."},{"speaker":"them","text":"You can text me again. Which day is the return due?"}]})
        scoped = await server.get_practice("session",member)
        assert "Which day is the return due?" in scoped["contact_pause"] and not scoped["rounds"]
        await db.exchanges.update_one({"id":"new"},{"$set":{"contact_update":"reopened","conversation":[{"speaker":"them","text":"You can text me again."}]}})
        reopened = await server.get_practice("session",member)
        assert "contact_pause" not in reopened and reopened["rounds"]
        await db.exchanges.delete_many(owner)
        await db.people.update_one({"id":pid},{"$set":{"background":"Rin explicitly asked the member to stop messaging. There has been no invitation to resume."}})
        await db.practice.insert_one({**turn,"_id":"another","id":"fresh","topic":"Making plans","messages":[]})
        assert (await server.get_practice("fresh",member))["contact_pause"]
        await db.people.update_one({"id":pid},{"$set":{"background":"I never asked the member to stop messaging."}})
        assert "contact_pause" not in await server.get_practice("fresh",member)
    isolated_member(check)


def test_unlinked_legacy_rehearsal_reads_restarts_without_generation(isolated_member, monkeypatch):
    async def check(server, db, member, person):
        from unittest.mock import AsyncMock
        provider = AsyncMock()
        monkeypatch.setattr(server, "ai_json", provider)
        await db.practice.insert_one({**member, "id":"legacy-unlinked", "ended": True, "created_at":server.now()})
        assert (await server.get_practice("legacy-unlinked",member))["ended"]
        assert (await server.list_practice("",member,0,""))[0]["id"] == "legacy-unlinked"
        assert not (await server.practice_restart("legacy-unlinked",member))["ended"]
        for action in [server.practice_say("legacy-unlinked",server.TextIn(text="hello"),None,member),
                       server.practice_end("legacy-unlinked",member),server.practice_to_profile("legacy-unlinked",member)]:
            with pytest.raises(HTTPException) as failure:
                await action
            assert failure.value.status_code == 409
        provider.assert_not_awaited()
    isolated_member(check)
