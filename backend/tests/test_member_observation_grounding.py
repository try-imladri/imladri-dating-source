"""Automatic exchange observations cannot promote suggested behavior to facts."""
import pytest
from copy import deepcopy

from backend.advice_wording import grounded_member_observation


LINES = [{"speaker": "me", "text": "Could we confirm Friday?"},
         {"speaker": "them", "text": "Yes, Friday works."}]


def test_model_summary_is_discarded_even_when_it_cites_a_real_member_line():
    result = grounded_member_observation({"text": "You followed up and agreed to a meeting time.", "source_indices": [0]}, LINES)
    assert result == {"text": "In this exchange you wrote: “Could we confirm Friday?”",
                      "source_indices": [0], "evidence_kind": "literal_member_quote"}
    assert "followed up" not in result["text"]


@pytest.mark.parametrize("indices", [[], [1], [True], [-1], [2], [0, 1], [0.0], ["0"], None])
def test_invalid_or_other_speaker_evidence_cannot_create_an_observation(indices):
    assert grounded_member_observation({"text": "An uncited claim", "source_indices": indices}, LINES) is None


def test_app_question_or_provider_draft_is_never_a_source_without_member_lines():
    for candidate in ({"text": "You sent the suggested reply."},
                      {"text": "You asked what time works.", "source_indices": [0]},
                      {"source_quotes": ["I will message you Friday."]}):
        assert grounded_member_observation(candidate, []) is None


def test_fabricated_provider_quote_and_extra_fields_are_never_saved():
    result = grounded_member_observation({"source_indices": [0], "source_quotes": ["I already called them."],
                                         "secret": "Do not retain arbitrary provider fields."}, LINES)
    assert "already called" not in str(result) and "secret" not in result
    assert result["text"].endswith("Could we confirm Friday?”")


def test_long_literal_excerpt_is_bounded_without_provider_paraphrasing():
    line = "I need some time to think. " * 30
    result = grounded_member_observation({"source_indices": [0]}, [{"speaker": "me", "text": line}])
    assert len(result["text"]) < 300
    assert result["text"].endswith("…”")
    quoted = result["text"].split("“", 1)[1][:-2]
    assert line.startswith(quoted)


@pytest.mark.parametrize("text", ["I need ten minutes before discussing this.",
                                  "I need 10 minutes before we discuss dinner.",
                                  "I want a short pause.", "I need a brief break."])
def test_current_pause_cannot_become_a_new_date_elapsed_time_or_return_promise(text):
    from backend.advice_wording import current_pause_options
    options = current_pause_options([{"role": "me", "text": text}])
    assert [o["tone"] for o in options] == ["warm", "neutral", "difficult"]
    replies = " ".join(o["suggested"] for o in options).lower()
    assert "saturday" not in replies and "ready now" not in replies
    assert "i'll" not in replies and "i will" not in replies and "be back" not in replies
    assert all("pause" in o["suggested"] for o in options)


@pytest.mark.parametrize("messages", [[], [{"role": "them", "text": "I need ten minutes before we discuss dinner."}],
    [{"role": "me", "text": "I need ten minutes to get there."}],
    [{"role": "me", "text": "I don't need a pause."}],
    [{"role": "me", "text": "They said 'I need a pause'."}],
    [{"role": "me", "text": "I need a break from this relationship."}],
    [{"role": "me", "text": "I need a pause."}, {"role": "me", "text": "I'm ready now."}]])
def test_pause_guard_does_not_invent_a_pause_or_erase_an_explicit_return_offer(messages):
    from backend.advice_wording import current_pause_options
    assert current_pause_options(messages) is None


def test_current_pause_guard_keeps_an_explicit_return_offer_word_for_word():
    from backend.advice_wording import current_pause_options
    words = "I need ten minutes before discussing this, then I'll call you."
    options = current_pause_options([{"role": "me", "text": words}])
    assert [option["tone"] for option in options] == ["warm", "neutral", "difficult"]
    assert all(option["suggested"] == words for option in options)


@pytest.mark.parametrize("citation,expected_count", [([0], 1), ([1], 0), (None, 0)])
def test_actual_exchange_save_keeps_only_grounded_pattern_and_never_returns_raw_claim(monkeypatch, citation, expected_count):
    from backend.tests.test_prompt_default_revisions import run_case
    from backend import database
    from fastapi import BackgroundTasks
    from backend.content import DEFAULT_PROMPTS
    from backend.quiz import default_quiz

    async def case(db):
        monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
        monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_prompt_fixture")
        monkeypatch.setattr(database, "open_database", lambda: (db, db))
        from backend import server
        monkeypatch.setattr(server, "db", db)
        async def settings(): return {"prompts": DEFAULT_PROMPTS, "quiz": default_quiz()}
        async def context(*_args, **_kwargs): return "Fictional adult fixture", []
        async def no_boundary(*_args, **_kwargs): return None
        async def no_background(*_args, **_kwargs): return None
        async def provider(*_args, **_kwargs):
            return {"what_it_means": "Jordan confirmed Friday.", "what_to_say": "Thanks for confirming.",
                    "actions": [], "seen": [], "explanations": [], "unknowns": [], "proposed_memories": [],
                    "member_pattern": {"text": "You already sent a follow-up and agreed to Saturday.",
                                       "source_indices": citation},
                    "tone": {"status": "insufficient", "drafts": {
                        "warm": "Thanks for confirming!", "neutral": "Thanks for confirming.",
                        "direct": "Friday confirmed.", "playful": "Friday it is!", "cool": "Confirmed."}}}
        monkeypatch.setattr(server, "get_settings", settings)
        monkeypatch.setattr(server, "full_context", context)
        monkeypatch.setattr(server, "recent_contact_boundary", no_boundary)
        monkeypatch.setattr(server, "ai_json", provider)
        monkeypatch.setattr(server, "advance", no_background)
        monkeypatch.setattr(server.message_charge, "enqueue", no_background)
        member = {"user_id": "fictional-owner", "env": server.ENV, "role": "admin"}
        person = {"id": "fictional-person", "user_id": member["user_id"], "nickname": "Jordan", "stage": "early", "rel_type": "partner"}
        await db.people.insert_one(deepcopy(person))
        result = await server.run_exchange(person, member, deepcopy(LINES), "Can I send a follow-up Saturday?",
                                           "early", "", BackgroundTasks(), "fictional-reply")
        saved = await db.exchanges.find_one({"id": "fictional-reply"})
        patterns = await db.patterns.find({"user_id": member["user_id"]}).to_list(10)
        assert len(patterns) == expected_count
        assert "already sent" not in str(result) and "already sent" not in str(saved)
        assert "Saturday" not in str(patterns)
        assert "member_pattern" not in result["response"]
        assert await db.memories.count_documents({}) == 0
        if patterns:
            assert patterns[0]["text"] == "In this exchange you wrote: “Could we confirm Friday?”"
            assert patterns[0]["sources"] == ["fictional-reply"]
    run_case(case)


def test_actual_rehearsal_options_replace_invented_current_pause_facts_without_extra_generation(monkeypatch):
    from backend.tests.test_prompt_default_revisions import run_case
    from backend import database

    async def case(db):
        monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
        monkeypatch.setattr(database, "open_database", lambda: (db, db))
        from backend import server
        calls = []
        async def context(*_args): return {"nickname": "Jordan"}, "Fictional adult rehearsal"
        async def provider(*args, **_kwargs):
            calls.append(args[0])
            return {"options": [{"reaction": "Are you ready for Saturday?", "tone": "warm",
                                  "suggested": "I am ready now. I'll be back in ten minutes."}]}
        monkeypatch.setattr(server, "practice_ctx", context)
        monkeypatch.setattr(server, "ai_json", provider)
        output = await server.likely_options({"messages": [{"role": "me", "text": "I need ten minutes before discussing this."}]}, {})
        assert calls == ["practice_options"]
        assert len(output) == 3
        assert "Saturday" not in str(output) and "ready now" not in str(output)
        assert all("I'll" not in row["suggested"] for row in output)
        assert all("fictional" in row["meaning"].lower() for row in output)
    run_case(case)
