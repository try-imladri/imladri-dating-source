import pytest

from backend import contact_boundary


@pytest.mark.parametrize("text", [
    "Please do not contact me again.", "Stop texting me.", "I don't want you to contact me again.",
    "Please leave me alone.", "Don't email me anymore.",
])
def test_direct_requests_survive_conflicting_model_advice(text):
    evidence, reopened = contact_boundary.turn_evidence([{"speaker": "them", "text": text}], "Should I send a last explanation?")
    assert evidence and not reopened
    read = contact_boundary.read("Avery", evidence)
    assert read["what_to_say"] is None and read["contact_boundary"] is True
    assert read["actions"] and all(action["say"] is None for action in read["actions"])
    assert "unsent" in read["best_move"]
    plan = contact_boundary.plan("Avery", evidence)
    assert plan["opening_line"] is None and plan["exit_line"] is None and plan["reactions"] == []


@pytest.mark.parametrize(("speaker", "line", "question", "quote"), [
    ("them", "I may be slow to reply this week.", "Should I wait?", "slow to reply"),
    ("them", "I need some space to think.", "Should I send something?", None),
    ("me", "Don't contact me again.", "I said I needed space.", "don't contact me again"),
    ("them", "Don't stop texting me.", "Should I stop?", None),
    ("them", "Don't call me about work; please email me instead.", "", "don't call me"),
    ("them", "Don't call me. Email me instead.", "", "don't call me"),
    ("them", "If they say 'don't contact me', respect it.", "", "don't contact me"),
    ("them", "I didn't say 'don't contact me'.", "", "don't contact me"),
    ("them", "Don't call me that nickname.", "", "don't call me"),
    ("them", "Good morning.", "What if he asked me not to contact him?", "asked me not to contact him"),
    ("me", "", "I told them not to contact me again.", "not to contact me"),
    ("me", "", "Alex told my sister not to contact him.", "not to contact him"),
    ("them", "Hello.", "I don't know what to do.", "Please do not contact me again"),
])
def test_ambiguous_reversed_qualified_and_ungrounded_sources_stay_open(speaker, line, question, quote):
    assert contact_boundary.turn_evidence([{"speaker": speaker, "text": line}], question, quote) == (None, False)


def test_current_request_is_distinct_from_later_explicit_invitation():
    messages = [{"speaker": "them", "text": "Please stop contacting me."},
                {"speaker": "them", "text": "You can text me again now."}]
    assert contact_boundary.turn_evidence(messages, "What should I say?") == (None, True)
    assert contact_boundary.turn_evidence(messages[::-1], "What should I say?")[0]
    assert not contact_boundary.ongoing("don't contact me tonight.")


def test_group_and_legacy_scope():
    question = "Avery said, 'Don't contact me again.' What if they expect a reply?"
    evidence, _ = contact_boundary.turn_evidence([], question)
    assert evidence
    assert contact_boundary.group_target(question, evidence, ["Avery", "Maya"]) == "Avery"
    assert contact_boundary.group_target(question, evidence, ["Maya", "Sam"]) is None
    separate_question = "Avery asked me not to contact them. How can I thank Maya for dinner?"
    assert contact_boundary.group_target(separate_question, contact_boundary.evidence_from(separate_question), ["Avery", "Maya"]) is None
    assert contact_boundary.saved_read({"question": question, "response": {"actions": [{"say": "old text"}]}})["response"]["what_to_say"] is None
    assert contact_boundary.saved_plan({"situation": question, "plan": {"opening_line":"old text"}})["plan"]["opening_line"] is None
    historical = {"question": "What can I say for their birthday?", "response": {"what_to_say": "Happy birthday!"}}
    assert contact_boundary.saved_read(historical) == historical

def test_group_keeps_uninvolved_friendship_available_without_relaying():
    response = contact_boundary.group_read("Mo", "mo asked for no contact.", ["Jay", "Mo"])
    assert "Jay" in response["best_move"] and "friendship" in response["best_move"]
    assert "Mo" in response["what_not_to_do"] and "updates" in response["what_not_to_do"]
    assert response["what_to_say"] is None and response["actions"][0]["say"] is None
    question = "Mo asked me to leave them alone. Could I get Jay to reach out for me about Mo?"
    assert contact_boundary.saved_group({"question":question}, ["Jay","Mo"])["response"]["contact_boundary"]
    via_jay = "Mo told me to leave them alone. Could Jay pass my explanation along?"
    assert contact_boundary.saved_group({"question":via_jay}, ["Jay","Mo"])["response"]["contact_boundary"]


def test_uncertain_timing_does_not_claim_a_deadline_and_explicit_duration_uses_original_time():
    from datetime import datetime, timedelta, timezone

    stamp = datetime(2026, 10, 1, 1, tzinfo=timezone.utc)
    assert contact_boundary.still_applies("don't contact me tonight.", stamp.isoformat(), stamp + timedelta(minutes=3))
    assert not contact_boundary.still_applies("don't contact me tonight.", stamp.isoformat(), stamp + timedelta(days=4))
    assert contact_boundary.still_applies("he asked me not to contact him for two hours.", stamp.isoformat(), stamp + timedelta(minutes=119))
    assert not contact_boundary.still_applies("he asked me not to contact him for two hours.", stamp.isoformat(), stamp + timedelta(hours=2))
    assert "unclear" in contact_boundary.read("Ray", "she asked me not to contact her until things settle.")["best_move"]


def test_legacy_and_explicit_boundaries_are_scoped_and_survive_unrelated_exchanges(monkeypatch):
    import asyncio
    import os
    from datetime import datetime, timedelta, timezone
    from urllib.parse import urlsplit
    from uuid import uuid4

    from motor.motor_asyncio import AsyncIOMotorClient
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("An isolated loopback database is required")
    assert urlsplit(uri).hostname in {"127.0.0.1", "localhost", "::1"}
    name = "contact_boundary_test_" + uuid4().hex
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("DB_NAME", name)
    from backend import server

    async def scenario():
        client = AsyncIOMotorClient(uri)
        db = client[name]
        monkeypatch.setattr(server, "db", db)
        now = datetime.now(timezone.utc)
        person = {"id": "A", "user_id": "U"}
        async def add(person_id, when, **data):
            await db.exchanges.insert_one({"person_id": person_id, "user_id": "U", "created_at": when.isoformat(), **data})
        try:
            await add("A", now - timedelta(days=3), conversation=[{"speaker": "them", "text": "Don't contact me again."}])
            for i in range(18):
                await add("A", now - timedelta(minutes=21-i), question="I wrote down what I learned.", contact_update=None)
            assert await server.recent_contact_boundary(person)
            assert await server.recent_contact_boundary({"id":"B", "user_id":"U"}) is None
            await add("A", now - timedelta(minutes=2), contact_update="reopened")
            assert await server.recent_contact_boundary(person) is None
            await add("A", now - timedelta(minutes=1), conversation=[{"speaker":"them","text":"Don't contact me tonight."}])
            assert "tonight" in await server.recent_contact_boundary(person)
        finally:
            await client.drop_database(name)
            client.close()
    asyncio.run(scenario())

def test_specific_later_permission_and_broad_request_apply_to_the_requested_channel():
    lines = [{"speaker": "them", "text": "Please don’t contact me again."},
             {"speaker": "them", "text": "I’ve had time to think. You can text me again now. Could you send the book title?"}]
    assert contact_boundary.turn_evidence(lines, "") == (None, True)
    question, original = contact_boundary.reopened_question(lines)
    assert question == "Could you send the book title?" and original
    read = contact_boundary.limited_read("Ray", question)
    assert "book title" in read["best_move"] and not read["contact_boundary"] and read["what_to_say"] is None
    broad = [{"speaker": "them", "text": "I do not want any contact from you. Don’t call or message me."}]
    assert contact_boundary.turn_evidence(broad, "Is a handwritten card okay?")[0]
    assert contact_boundary.evidence_from("Don’t call or message me.", incoming=True)
