from backend.advice_wording import respect_undecided_time


def test_unestimated_timeline_uses_a_clarifying_reply_and_keeps_member_offered_availability():
    ask = "I haven't estimated how long it will take. I can talk Friday."
    draft = "I'll finish by noon tomorrow."
    clarify = "Are the two comments blocking you?"
    answer = {"what_to_say": draft, "actions": [{"say": draft}, {"say": clarify}, {"say": "I can talk Friday."}]}
    respect_undecided_time(answer, ask)
    assert answer["what_to_say"] == clarify
    assert [action["say"] for action in answer["actions"]] == [None, clarify, "I can talk Friday."]


def test_unstated_weekdays_do_not_become_the_members_availability():
    from backend.advice_wording import respect_stated_days

    answer = {"what_to_say": "I'm around Tuesday or Thursday evening.", "actions": [
        {"say": "I'm free Tue or Thu."}, {"say": "What time after six works for you?"}]}
    respect_stated_days(answer, "How should I follow up?")
    assert answer["what_to_say"] == "What time after six works for you?"
    assert answer["actions"][0]["say"] is None

    answer = {"what_to_say": "I'm free Tuesday and Thursday."}
    respect_stated_days(answer, "Tuesday works after six.")
    assert answer["what_to_say"] == "What time would work for you?"
    for offered in ("Tuesday works after six.", "I can do Tue.", "I'm free Tuesday or Thursday."):
        answer = {"what_to_say": "I'm around Tuesday evening."}
        respect_stated_days(answer, offered)
        assert answer["what_to_say"] == "I'm around Tuesday evening."
    answer = {"what_to_say": "Could Tuesday work for you?"}
    respect_stated_days(answer, "")
    assert answer["what_to_say"] == "Could Tuesday work for you?"


def test_recipient_offering_a_shift_is_not_a_member_commitment():
    from backend.advice_wording import respect_stated_days

    for words in ("I can do Tuesday.", "I can cover the shift on Friday.", "I can make it Thursday.",
                  "I can meet at the library on Tuesday.", "Yes, happy to work the desk Friday."):
        answer = {"what_to_say": words, "actions": [{"say": "What time does the shift start?"}]}
        respect_stated_days(answer, "How can I confirm the shift time?")
        assert answer["what_to_say"] == "What time does the shift start?"
        answer["what_to_say"] = words
        respect_stated_days(answer, words)
        assert answer["what_to_say"] == words


def test_copyable_time_suggestion_needs_no_fill_in_blanks():
    from backend.advice_wording import respect_stated_days

    answer = {"what_to_say": "I can also do [the times you're free] if that helps.",
              "actions": [{"say": "What hours were you thinking?"}]}
    respect_stated_days(answer, "")
    assert answer["what_to_say"] == "What hours were you thinking?"


def test_question_about_shift_hours_does_not_accept_the_offer():
    from backend.advice_wording import clarify_invitation_time

    lines = [{"speaker": "them", "text": "Could we work the library desk on Tuesday?"}]
    question = "How can I confirm the shift time?"
    answer = {"what_to_say": "Count me in!", "actions": [{"say": "Tuesday works for me."}]}
    clarify_invitation_time(answer, lines, question)
    assert answer["what_to_say"] == "What time does the shift start and finish?"
    assert "decide" in answer["best_move"]
    assert [action["say"] for action in answer["actions"]] == [answer["what_to_say"]]
    for messages, ask in [(lines, "I already accepted. How should I decline?"),
                          ([*lines, {"speaker": "me", "text": "Tuesday works for me."}], question),
                          ([{"speaker": "them", "text": "Want to hang out?"}], question)]:
        answer = {"what_to_say": "Existing draft"}
        clarify_invitation_time(answer, messages, ask)
        assert answer["what_to_say"] == "Existing draft"
