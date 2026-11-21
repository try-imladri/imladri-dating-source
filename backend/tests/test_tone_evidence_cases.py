import pytest

from backend.conversation_tone import normalize


@pytest.mark.parametrize("current,citations,past,history_refs,expected", [
    (["Okay", "Sounds good to me"], [0, 1], [], [], "insufficient"),
    (["Sure thing!", "Thanks."], [0, 1], [], [], "insufficient"),
    (["👍👍", "…"], [0, 1], [], [], "insufficient"),
    (["I miss you", "I MISS YOU!"], [0, 1], [], [], "insufficient"),
    (["Okay", "I wish we could spend the weekend together."], [0], [], [], "insufficient"),
    (["Fine", "I felt let down yesterday.", "I'm glad we talked."], [0], [], [], "insufficient"),
    (["Okay"], [0], ["Can you call after your shift?"], [0], "insufficient"),
    (["Okay"], [0], ["Can you call after your shift?", "CAN YOU CALL AFTER YOUR SHIFT!"], [0, 1], "insufficient"),
    (["I would enjoy an evening with you."], [9], [], [], "insufficient"),
    (["I would enjoy an evening with you."], [0], [], [], "limited"),
    (["Please slow down", "Let me finish"], [0, 1], [], [], "limited"),
    (["Come celebrate with me.", "I can't wait to see you.", "I'll save you a seat."], [1], [], [], "limited"),
    (["Come celebrate with me.", "I can't wait to see you.", "I'll save you a seat."], [0, 1, 2], [], [], "moderate"),
    (["Okay"], [0], ["I want to talk this through tomorrow.", "I still care about our plans."], [0, 1], "limited"),
])
def test_conservative_estimate_uses_only_distinct_cited_words(current, citations, past, history_refs, expected):
    source = [{"speaker": "them", "text": text} for text in current]
    prior = [{"speaker": "them", "text": text, "exchange_id": "prior", "index": i} for i, text in enumerate(past)]
    raw = {"incoming": "warm", "status": "estimated", "confidence": "moderate", "evidence": citations,
           "baseline_evidence": history_refs, "explanation": "An interpretation of the quoted lines."}
    result = normalize(raw, source, previous=prior)
    assert (result["confidence"] if result["status"] == "estimated" else result["status"]) == expected
    if expected == "insufficient":
        assert result["incoming"] is None and result["evidence"] == []
