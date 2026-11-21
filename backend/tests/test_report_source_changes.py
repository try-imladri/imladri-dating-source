import asyncio

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock

from backend import report_sources
from backend.tests.test_individual_connections import isolated_member


def test_claims_keep_only_their_own_current_sources():
    source = {"kind": "exchange", "id": "e1"}
    report = {"sections": [{"read": "Unanchored", "source_ids": ["missing"]}],
              "traits": [{"trait": "Warmth", "source_ids": [None]}],
              "compatibility": {"summary": "Observed", "summary_source_ids": ["S1"], "works": [
                  {"text": "Takes turns", "source_ids": ["S1", "S1"]}, {"text": "Uncertain", "source_ids": ["invented"]}],
                  "friction": ["Older style"]}}
    report_sources.apply(report, {"change_source_ids": ["S1"]}, {"S1": source})
    assert report["sections"][0]["read"] is None and not report["traits"]
    c = report["compatibility"]
    assert c["works"] == ["Takes turns"] and c["friction"] == []
    assert c["claim_evidence"] == {"summary": [source], "tip": [], "works": [[source]], "friction": []}
    assert report["change_evidence"] == [source]


def test_edit_during_report_generation_revokes_old_claims(isolated_member, monkeypatch):
    async def check(server, db, member, person):
        pid = person["id"]
        await db.users.insert_one(dict(member))
        await db.memories.insert_one({**member, "id": "memory", "person_id": pid, "text": "First account", "kind":"event"})
        await db.people.update_one({"id":pid}, {"$set":{"report":{"sections":[{"read":"Old claim"}]}, "report_status":"ready"}})
        monkeypatch.setattr(server,"has_reply_allowance",AsyncMock(return_value=True))
        entered, finish = asyncio.Event(), asyncio.Event()
        async def respond(*args, **kwargs):
            entered.set()
            await finish.wait()
            return {"sections": []}
        monkeypatch.setattr(server, "ai_json", respond)
        task = asyncio.create_task(server.regenerate_report(pid, ["Manual refresh"]))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            await server.edit_memory("memory", server.TextIn(text="Corrected account"),member)
            row = await db.people.find_one({"id":pid})
            assert row["report"] is None and row["report_status"] == "source_changed"
        finally:
            finish.set()
            await task
        assert (await db.people.find_one({"id":pid}))["report"] is None
        await db.corrections.insert_one({**member, "id":"correction", "person_id":pid, "exchange_id":"ex", "key":"seen:0"})
        await db.people.update_one({"id":pid}, {"$set":{"report":{"sections":[{"read":"Old correction"}]}, "report_status":"ready"}})
        await server.delete_correction("correction", member)
        assert (await db.people.find_one({"id":pid}))["report"] is None
    isolated_member(check)


def test_report_excerpt_and_correction_show_the_reviewed_words(isolated_member):
    async def check(server, db, member, person):
        own = {**member, "person_id": person["id"]}
        await db.exchanges.insert_one({**own, "id":"ex", "created_at":server.now(),
            "question":"What happened?", "conversation":[{"speaker":"them","text":"Ok."}],
            "response":{"what_it_means":"Earlier generated guess"}})
        await db.corrections.insert_one({**own, "id":"c", "text":"An inaccurate reading",
                                         "note":"We arranged a time.", "created_at":server.now()})
        ctx = await server.person_context(person, source_map={}, cite_sources=True)
        assert "Other person: Ok.\n" in ctx and "Earlier generated guess" not in ctx
        source = await server.read_person_evidence(person["id"], "correction", "c", member)
        assert "Flagged interpretation: An inaccurate reading" in source["text"]
        assert "Member correction: We arranged a time." in source["text"]
    isolated_member(check)


def test_rehearsal_reset_and_late_turn_do_not_overwrite_each_other(isolated_member, monkeypatch):
    async def check(server, db, member, person):
        await db.practice.insert_one({**member,"person_id":person["id"],"id":"session","mode":"live",
                                      "topic":"Lunch","messages":[],"options":[],"created_at":server.now()})
        monkeypatch.setattr(server,"practice_ctx",AsyncMock(return_value=(person,"Only a rehearsal")))
        entered, finish = asyncio.Event(), asyncio.Event()
        async def reply(*args, **kwargs):
            entered.set()
            await finish.wait()
            return {"reply":"Saturday?"}
        monkeypatch.setattr(server,"ai_json",reply)
        turn = server.PracticeTurnIn(text="Lunch?", expected_revision=0)
        pending = asyncio.create_task(server.practice_turn("session",turn,member,"first"))
        try:
            await asyncio.wait_for(entered.wait(),timeout=4)
            await server.practice_restart("session",member)
        finally:
            finish.set()
        with pytest.raises(HTTPException) as conflict:
            await pending
        assert conflict.value.status_code == 409
        with pytest.raises(HTTPException) as stale:
            await server.practice_turn("session",turn,member,"second")
        assert stale.value.status_code == 409
        saved = await db.practice.find_one({"id":"session"})
        assert saved["messages"] == [] and saved["turn_revision"] == 1 and not saved.get("free_reply_ids")
    isolated_member(check)
