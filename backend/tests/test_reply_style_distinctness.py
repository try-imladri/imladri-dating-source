"""The production style check runs without importing server startup or a database."""
import ast
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend import conversation_tone


def check(drafts, member_words=()):
    path = Path(conversation_tone.__file__).with_name("server.py")
    tree = ast.parse(path.read_text(encoding="utf8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "checked_reply_styles")
    scope = {"conversation_tone": conversation_tone, "HTTPException": HTTPException}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), scope)
    return scope["checked_reply_styles"](drafts, member_words)


def sample():
    return {"warm": "Hey, does Friday still work?", "neutral": "Does Friday still work for you?",
            "direct": "Is Friday confirmed?", "playful": "Friday still on the calendar?", "cool": "Friday still works?"}


def test_distinct_usable_drafts_keep_the_exact_words_and_values():
    drafts = sample()
    assert check(drafts) == drafts


@pytest.mark.parametrize("replacement", ["Does Friday still work for you?", "  DOES   FRIDAY still work for you?  ", "Does Friday still work for you?\n"])
def test_one_repeated_style_is_rejected_even_when_other_three_differ(replacement):
    drafts = sample()
    drafts["cool"] = replacement
    with pytest.raises(HTTPException) as error:
        check(drafts)
    assert error.value.status_code == 502


@pytest.mark.parametrize("question", list(conversation_tone.TIME_QUESTIONS))
def test_known_time_clarification_retains_existing_five_fact_preserving_versions(question):
    assert check(dict.fromkeys(conversation_tone.STYLES, question)) == conversation_tone.TIME_QUESTIONS[question]


def test_arbitrary_identical_text_does_not_get_invented_replacements():
    with pytest.raises(HTTPException):
        check(dict.fromkeys(conversation_tone.STYLES, "I do not want to do that."))


@pytest.mark.parametrize("bad", [None, {}, {"neutral": "Hello"}, {**sample(), "other": "Extra"}, {**sample(), "warm": " "}, {**sample(), "warm": False}])
def test_incomplete_or_malformed_drafts_fail_closed(bad):
    with pytest.raises(HTTPException) as error:
        check(bad)
    assert error.value.status_code == 502


@pytest.mark.parametrize("style", conversation_tone.STYLES)
@pytest.mark.parametrize("extra", ["Name a time after dinner, and I'll be there.",
                                  "Family first, agreed — and my wallet's still closed this month."])
def test_observed_unsupported_commitment_or_belief_in_any_style_is_rejected_without_replacement(style, extra):
    drafts = sample()
    drafts[style] = extra
    before = dict(drafts)
    with pytest.raises(HTTPException) as error:
        check(drafts, ["Ask what time works after dinner.", "I cannot lend money this month."])
    assert error.value.status_code == 502 and error.value.reason == "reply_intent"
    assert drafts == before


@pytest.mark.parametrize("text,source", [
    ("I'll be there.", "I will be there."), ("I'll be there.", "I will attend."),
    ("I'll be there.", "Please say 'I will attend.'"),
    ("Family first, agreed.", "Family first, agreed."),
    ("I'll always be available.", "I will always be available."),
    ("I'll never change my mind.", "I will never change my mind."),
    ("I'll be there if I finish work.", "I will be there if I finish work."),
])
def test_direct_supplied_commitments_and_bounded_paraphrase_are_preserved(text, source):
    drafts = sample()
    drafts["warm"] = text
    assert check(drafts, [source]) == drafts


@pytest.mark.parametrize("source", [
    "Do not say I'll be there.", "Should I say I'll be there?", "They said I'll be there.",
    "If I go, I'll be there.", "'I'll be there.'", "I disagree with family first.",
])
def test_quotes_other_people_hypotheticals_and_negative_instructions_do_not_authorize_new_claims(source):
    assert conversation_tone.draft_intent_violation({"warm": "I'll be there."}, [source])
    assert conversation_tone.draft_intent_violation({"warm": "Family first, agreed."}, [source])


def test_noncommittal_future_and_present_refusal_are_not_blanket_blocked():
    assert conversation_tone.draft_intent_violation({"warm": "We'll see."}) is None
    assert conversation_tone.draft_intent_violation({"warm": "I won't discuss it tonight."}) is None
    assert conversation_tone.draft_intent_violation({"warm": "Which day we'll shop is still open."}) is None
    assert conversation_tone.draft_intent_violation({"warm": "What does family first mean to you?"}) is None
    assert conversation_tone.draft_intent_violation({"warm": "Family first?"}) is None
    assert conversation_tone.draft_intent_violation({"warm": "I'll never change my mind."})
    assert conversation_tone.draft_intent_violation({"warm": "I'll always agree."})
    assert conversation_tone.draft_intent_violation({"warm": "I'll be there tomorrow."}, ["I will be there."])
    assert conversation_tone.draft_intent_violation({"warm": "I'll be there."}, ["I will be there if I finish work."])


@pytest.mark.parametrize("text", [
    "I'll know once I've inspected the remaining work. Which part is most urgent?",
    "I'll see what the estimate shows. Which part is most urgent?",
    "I'll pass on that in real life.", "I'll sit that one out in real life.",
])
def test_cognitive_forecasts_and_refusal_idioms_are_not_classified_as_action_promises(text):
    drafts = sample()
    drafts["playful"] = text
    assert check(drafts, ["I need to inspect the remaining work before estimating.",
                          "I do not want to try that in real life."]) == drafts


@pytest.mark.parametrize("text", ["I'll start there.", "I'll be there.", "I'll send it tomorrow.",
                                  "I'll always agree."])
def test_finite_action_commitments_and_explicit_permanence_still_require_own_authorization(text):
    assert conversation_tone.draft_intent_violation({"warm": text}, ["Ask what time works."]) == "unsupported_promise"


@pytest.mark.parametrize("text", [
    "I hear that family matters to you, and it does to me too. I still can't lend money this month.",
    "Family matters to you. It does to me too. My answer is still no.",
    "Family is important to me too, but I cannot lend money this month.",
    "Family's important to me too, and so is my budget. I cannot lend money this month.",
])
def test_observed_family_importance_assent_requires_the_members_own_positive_source(text):
    drafts = sample()
    drafts["warm"] = text
    with pytest.raises(HTTPException) as error:
        check(drafts, ["I cannot lend money this month."])
    assert error.value.status_code == 502 and error.value.reason == "reply_intent"
    assert check(drafts, ["Family matters to me."]) == drafts
    assert check(drafts, ["Please say 'Family is important to me.'"]) == drafts


@pytest.mark.parametrize("source", [
    "Family first.", "Do not say family matters to me.", "Should I say family matters to me?",
    "They said family matters to me.", "'Family matters to me.'", "Maybe family matters to me.",
])
def test_priority_other_speakers_quotes_and_uncertainty_do_not_authorize_family_importance(source):
    text = "I hear that family matters to you, and it does to me too. I cannot lend money."
    assert conversation_tone.draft_intent_violation({"warm": text}, [source]) == "unsupported_stance"
    assert conversation_tone.draft_intent_violation({"warm": "Family first, agreed."},
                                                 ["Family matters to me."]) == "unsupported_stance"


@pytest.mark.parametrize("mode", ["private", "shared"])
def test_family_assent_uses_requester_belief_not_the_partners_belief(mode):
    from backend import couples_tone
    drafts = sample()
    drafts["warm"] = "Family matters to you, and it does to me too. I cannot lend money."
    raw = {"status": "insufficient", "drafts": drafts}
    own = {"speaker": "me", "text": "I cannot lend money."}
    partner = {"speaker": "them", "text": "Family matters to me."}
    with pytest.raises(couples_tone.ToneResponseError) as error:
        couples_tone.coach_tone(raw, [own, partner], "me" if mode == "private" else "them",
                               "Keep my financial boundary.", mode=mode, boundary=False)
    assert error.value.reason == "tone_intent"
    own["text"] = "Family matters to me. I cannot lend money."
    assert couples_tone.coach_tone(raw, [own, partner], "me" if mode == "private" else "them",
                                 "Keep my financial boundary.", mode=mode, boundary=False)["drafts"] == drafts


@pytest.mark.parametrize("mode", ["private", "shared"])
def test_couples_uses_requester_words_and_purpose_not_the_focus_partners_commitment(mode):
    from backend import couples_tone
    drafts = sample()
    drafts["playful"] = "I'll be there."
    raw = {"status": "insufficient", "drafts": drafts}
    lines = [{"speaker": "me", "text": "What time works?"}, {"speaker": "them", "text": "I will attend."}]
    with pytest.raises(couples_tone.ToneResponseError) as error:
        couples_tone.coach_tone(raw, lines, "me" if mode == "private" else "them", "Ask the time.", mode=mode, boundary=False)
    assert error.value.reason == "tone_intent"
    own = [{"speaker": "me", "text": "I will attend."}, {"speaker": "them", "text": "What time?"}]
    assert couples_tone.coach_tone(raw, own, "me" if mode == "private" else "them", "Keep my commitment.",
                                  mode=mode, boundary=False)["drafts"] == drafts


def test_warm_time_fallbacks_add_only_a_greeting_not_gratitude_or_promise():
    for question, expected in conversation_tone.TIME_QUESTIONS.items():
        drafts = check(dict.fromkeys(conversation_tone.STYLES, question))
        assert drafts == expected and drafts["warm"].startswith("Hi, ")
        assert "thank" not in drafts["warm"].lower() and "I'll" not in drafts["warm"]


@pytest.mark.parametrize("bad_kind,reason", [("duplicate", "reply_style_duplicate"), ("promise", "reply_intent")])
def test_rejected_style_set_keeps_the_paid_credit_and_a_saved_valid_set_uses_one(bad_kind, reason):
    import time
    from uuid import uuid4
    from pydantic import BaseModel
    from starlette.requests import Request
    from backend.free_plan import initialize_usage, reply_usage, run_metered
    from backend import message_costs
    from backend.tests.test_prompt_default_revisions import run_case

    class Input(BaseModel):
        question: str

    async def scenario(db):
        clock = int(time.time())
        user = {"user_id": "fixture-style-" + uuid4().hex, "role": "member", "tier": "paid",
                "stripe_subscription_id": "sub_fictional_style_qa", "subscription_period_start": clock - 60,
                "subscription_period_end": clock + 3600}
        await db.users.insert_one(dict(user))
        member = await initialize_usage(db, user)
        bad = sample()
        bad["cool"] = bad["neutral"] if bad_kind == "duplicate" else "I'll be there."

        async def invalid(reply_id):
            with message_costs.generation_context("preview", "six_part"):
                async with message_costs.provider_call(db, ("fictional-key-id", False), "deepseek-flash", False) as attempt:
                    attempt.status = "succeeded"
                try:
                    check(bad)
                except HTTPException as error:
                    await message_costs.mark_response_rejection(db, reply_id, error.reason)
                    raise
            pytest.fail("An invalid style set reached the save path")

        async def saved(reply_id):
            result = {"id": reply_id, "user_id": user["user_id"], "tone": {"drafts": check(sample())}}
            await db.exchanges.insert_one(dict(result))
            return result

        async def restore(row):
            return row

        def request():
            return Request({"type": "http", "headers": [(b"idempotency-key", str(uuid4()).encode())]})

        with pytest.raises(HTTPException) as error:
            await run_metered(db, member, request(), Input(question="Confirm Friday"), "exchange", "fixture-person", invalid, restore)
        assert error.value.status_code == 502
        current = await db.users.find_one({"user_id": user["user_id"]})
        assert reply_usage(current)["messages_used"] == 0
        assert reply_usage(current)["messages_remaining"] == 100
        assert await db.exchanges.count_documents({"user_id": user["user_id"]}) == 0
        call = await db.ai_call_ledger.find_one({"feature": "six_part"})
        assert call["application_rejection"] == reason and call["workspace"] == "individual"
        assert call["message_run"] == 1 and call["environment"] == "preview" and call["status"] == "succeeded"
        await run_metered(db, current, request(), Input(question="Confirm Friday"), "exchange", "fixture-person", saved, restore)
        current = await db.users.find_one({"user_id": user["user_id"]})
        assert reply_usage(current)["messages_used"] == 1
        assert await db.exchanges.count_documents({"user_id": user["user_id"]}) == 1

    run_case(scenario)


@pytest.mark.parametrize("feature,workspace,reason", [("six_part", "individual", "reply_intent"),
    ("couples_tone", "couples", "tone_intent"), ("couples_tone", "couples", "tone_drafts")])
def test_rejection_receipt_is_exact_message_run_environment_feature_and_workspace_bound(feature, workspace, reason):
    import json
    from uuid import uuid4
    from backend import ai_meter, message_costs
    from backend.tests.test_prompt_default_revisions import run_case

    async def scenario(db):
        mid = str(uuid4())
        async def validate():
            with message_costs.generation_context("preview", feature):
                async with message_costs.provider_call(db, ("fictional-key-id", False), "deepseek-flash", False) as attempt:
                    attempt.status = "succeeded"
                await message_costs.mark_response_rejection(db, "another-message", reason)
                await message_costs.mark_response_rejection(db, mid, "private words must never be recorded")
                wrong_reason = "tone_intent" if workspace == "individual" else "reply_intent"
                await message_costs.mark_response_rejection(db, mid, wrong_reason)
                with message_costs.message_context(mid, "couples" if workspace == "individual" else "individual", run_number=2):
                    await message_costs.mark_response_rejection(db, mid, reason)
                with message_costs.message_context(mid, workspace, run_number=3):
                    await message_costs.mark_response_rejection(db, mid, reason)
                assert not await db.ai_call_ledger.count_documents({"application_rejection": {"$exists": True}})
                await message_costs.mark_response_rejection(db, mid, reason)
                await message_costs.mark_response_rejection(db, mid, reason)
        with message_costs.message_context(mid, workspace, run_number=2):
            await ai_meter.in_context("metered", validate)
        rows = await db.ai_call_ledger.find({}).to_list(10)
        assert len(rows) == 1 and rows[0]["application_rejection"] == reason
        assert (rows[0]["environment"], rows[0]["feature"], rows[0]["workspace"], rows[0]["message_run"], rows[0]["message_id"]) == (
            "preview", feature, workspace, 2, mid)
        assert "private words" not in json.dumps(rows, default=str)
        assert rows[0]["status"] == "succeeded"
        from datetime import datetime, timezone
        import httpx
        from fastapi import FastAPI, Request
        from backend.message_cost_admin import make_router
        await db.message_cost_units.insert_one({"_id": mid, "env": "preview", "workspace": workspace,
            "kind": "exchange", "pool": "plan", "status": "failed", "started_at": datetime.now(timezone.utc)})
        async def admin(request: Request):
            if request.headers.get("Authorization") != "Bearer fixture-admin":
                raise HTTPException(403)
        app = FastAPI()
        app.include_router(make_router(lambda: db, admin, "preview"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            path = "/admin/prompts/message-costs/receipts/" + mid
            assert (await client.get(path)).status_code == 403
            response = await client.get(path, headers={"Authorization": "Bearer fixture-admin"})
            assert response.status_code == 200
            assert response.json()["calls"][0]["application_rejection"] == reason
            assert "fictional-key-id" not in response.text and "private words" not in response.text
    run_case(scenario)
