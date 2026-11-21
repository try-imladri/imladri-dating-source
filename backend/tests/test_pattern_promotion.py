import asyncio
import os
import uuid
from urllib.parse import urlsplit

import pytest
from fastapi import HTTPException


def test_real_evidence_and_confirmation_reach_future_advice(monkeypatch):
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Set IMLADRI_TEST_MONGO_URL to a loopback MongoDB.")
    assert urlsplit(uri).hostname in {"localhost", "127.0.0.1", "::1"}
    from motor.motor_asyncio import AsyncIOMotorClient
    from backend import server

    async def run():
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        db = client["imladri_pattern_evidence_" + uuid.uuid4().hex]
        monkeypatch.setattr(server, "db", db)
        member, outsider = {"user_id": "one"}, {"user_id": "two"}
        text = "You asked for a little thinking time."
        confirm = lambda wording, value: server.PatternConfirmation(text=wording, confirmed=value)

        async def records():
            return {p["id"]: p for p in await server.list_patterns(member)}

        async def advice():
            return await server.member_pattern_evidence("one", 30)

        async def record(eid, conversation, uid="one"):
            await db.exchanges.insert_one({"id": eid, "user_id": uid, "person_id": "a",
                                           "conversation": [{"speaker": speaker, "text": line} for speaker, line in conversation]})

        try:
            await db.patterns.create_indexes(server.INDEXES["patterns"])
            await db.practice.insert_many([{"id": x, "user_id": "one", "person_id": "a"} for x in ("r1","r2")])
            await db.people.insert_one({"id": "a", "user_id": "one", "nickname": "Example"})
            await record("one", [("me", "Let me think about that."), ("them", "Okay.")])
            await record("copy", [("me", "LET ME THINK ABOUT THAT!")])
            await record("expanded", [("me", "Let me think about that"), ("me", "Let's check in tomorrow.")])
            await record("reported", [])
            await record("their words", [("them", "I need to think.")])
            await record("foreign", [("me", "This belongs to someone else")], "two")
            rehearsal, _ = await server.pattern_evidence.save(db,"one","You tried a short opening.",["practice:r1","practice:r2"],server.now())
            await db.patterns.insert_one({"id": "legacy", "user_id": "one", "text": text,
                "sources": ["one","reported","copy","expanded","practice:r1","their words","foreign","one","missing"], "edited": False})
            rows = await records()
            assert rows[rehearsal["id"]]["evidence_status"] == "tentative"
            assert rows[rehearsal["id"]]["practice_count"] == 2
            assert rows["legacy"]["evidence_status"] == "tentative" and rows["legacy"]["example_count"] == 1
            assert "foreign" not in rows["legacy"]["sources"]
            assert await advice() == []

            await record("different", [("me", "I'll get back to you when I've had a break.")])
            await db.patterns.update_one({"id":"legacy"}, {"$addToSet":{"sources":"different"}})
            assert (await records())["legacy"]["evidence_status"] == "supported"
            assert len(await advice()) == 1 and "2 distinct saved conversations" in (await advice())[0]
            await server.drop_pattern_sources("one",["different"])
            assert (await records())["legacy"]["evidence_status"] == "tentative" and await advice() == []
            with pytest.raises(HTTPException) as unauthorized:
                await server.confirm_pattern("legacy",confirm(text,True),outsider)
            assert unauthorized.value.status_code == 404
            with pytest.raises(HTTPException) as outdated:
                await server.confirm_pattern("legacy",confirm("old wording",True),member)
            assert outdated.value.status_code == 409
            await server.confirm_pattern("legacy",confirm(text,True),member)
            assert (await records())["legacy"]["evidence_status"] == "confirmed" and "member confirmed" in (await advice())[0]
            await server.confirm_pattern("legacy",confirm(text,False),member)
            assert await advice() == []
            await server.confirm_pattern("legacy",confirm(text,True),member)
            await server.drop_pattern_sources("one",(await records())["legacy"]["sources"])
            assert "member confirmed" in (await advice())[0]
            await db.practice.update_one({"id": "r1"}, {"$set": {"profile_saved": [{"id": "legacy", "text": text}]}})
            await server.edit_pattern("legacy",server.TextIn(text="You often prefer to write first."),member)
            assert (await records())["legacy"]["evidence_status"] == "tentative" and await advice() == []
            assert (await db.practice.find_one({"id": "r1"}))["profile_saved"] == []
            await db.practice.update_one({"id": "r1"}, {"$set": {"profile_saved": [{"id": "legacy", "text": text}]}})
            await server.delete_pattern("legacy",member)
            assert "legacy" not in await records()
            assert (await db.practice.find_one({"id": "r1"}))["profile_saved"] == []

            # Parallel retries of equivalent wording add one record and one source.
            concurrent = await asyncio.gather(*[server.pattern_evidence.save(db,"one","A second idea.",["one","one"],server.now()) for _ in range(4)])
            assert len({row["id"] for row,_ in concurrent}) == 1 and sum(new for _,new in concurrent) == 1
            assert concurrent[0][0]["sources"] == ["one"]
            await db.patterns.insert_one({"id":"edited-old","user_id":"one","text":"Earlier updated wording",
                                          "edited":True,"sources":["one","different"]})
            assert (await records())["edited-old"]["evidence_status"] == "tentative"
            assert await advice() == []
        finally:
            await client.drop_database(db.name)
            client.close()

    asyncio.run(run())
