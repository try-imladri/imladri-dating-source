"""Bounded six-language direct incoming contact boundaries, not a general language parser."""
from datetime import datetime, timedelta, timezone

import pytest

from backend import contact_boundary as cb, couples_tone


CASES = {
    "en": ("Please do not contact me again.", "You can contact me again now.", "Don't contact me for now."),
    "es": ("Por favor, no me contactes.", "Puedes volver a contactarme ahora.", "No me contactes hoy."),
    "ar": ("من فضلك لا تتواصل معي.", "يمكنك التواصل معي الآن.", "لا تتواصل معي اليوم."),
    "fr": ("Ne me contacte plus, s'il te plaît.", "Tu peux me contacter maintenant.", "Ne me contacte pas pour le moment."),
    "de": ("Bitte kontaktiere mich nicht mehr.", "Du kannst mich jetzt kontaktieren.", "Kontaktiere mich nicht heute."),
    "pt": ("Por favor, não me contate.", "Você pode entrar em contato comigo agora.", "Não me contate por enquanto."),
}


@pytest.mark.parametrize("wording", [
    "No me escribas más.", "لا تراسلني مرة أخرى.", "Não entre mais em contato comigo.",
])
def test_no_contact_phrases_used_in_the_model_matrix_have_deterministic_protection(wording):
    lines = [{"speaker": "them", "text": wording}]
    evidence, reopened = cb.turn_evidence(lines, "Send one final charming reply.")
    assert evidence == cb.normalized(wording) and not reopened
    saved = cb.saved_read({"conversation": lines, "response": {"what_to_say": "An unwanted reply.",
                           "actions": [{"label": "Send", "say": "An acknowledgment."}]}})["response"]
    assert saved["contact_boundary"] and saved["what_to_say"] is None
    assert all(action.get("say") is None for action in saved["actions"])


@pytest.mark.parametrize("wording", [
    "No me escribas más", "لا تراسلني مرة أخرى", "Não entre mais em contato comigo",
])
def test_added_exact_matrix_phrases_do_not_authorize_a_boundary_from_the_wrong_source(wording):
    assert cb.turn_evidence([{"speaker": "me", "text": wording}], "", wording) == (None, False)
    assert cb.turn_evidence([{"speaker": "them", "text": 'What if they said "' + wording + '"?'}], "", wording) == (None, False)
    assert cb.turn_evidence([{"speaker": "them", "text": wording + " about work."}], "", wording) == (None, False)


@pytest.mark.parametrize("language", sorted(CASES))
def test_direct_incoming_boundary_wins_over_model_drafts_in_all_paths(language):
    request, _, _ = CASES[language]
    lines = [{"speaker": "them", "text": request}]
    evidence, reopened = cb.turn_evidence(lines, "Should I send a final explanation?", "ignore this invented quote")
    assert evidence == cb.normalized(request) and not reopened
    response = cb.saved_read({"conversation": lines, "response": {"what_to_say": "Send this anyway.",
                             "tone": {"drafts": {"warm": "An acknowledgment."}}}})["response"]
    assert response["contact_boundary"] and response["what_to_say"] is None and "tone" not in response
    assert all(item.get("say") is None for item in response["actions"])
    assert cb.plan("partner", evidence)["opening_line"] is None
    raw = {"status": "estimated", "drafts": {key: key + " outbound words" for key in ("warm", "neutral", "direct", "playful", "cool")}}
    for mode in ("private", "shared"):
        tone = couples_tone.coach_tone(raw, lines, "them", "Send a reply.", mode=mode, boundary=evidence)
        assert tone["drafts"] == {} and tone["status"] == "insufficient"


@pytest.mark.parametrize("language", sorted(CASES))
def test_clear_full_current_invitation_reopens_only_in_actual_temporal_order(language):
    request, invitation, _ = CASES[language]
    lines = [{"speaker": "them", "text": request}, {"speaker": "them", "text": invitation}]
    assert cb.turn_evidence(lines, "") == (None, True)
    assert cb.turn_evidence(lines[::-1], "")[0]
    assert cb.turn_evidence([lines[0], {"speaker": "me", "text": invitation}], "")[0]
    assert cb.turn_evidence([{ "speaker": "me", "text": request}], "", request) == (None, False)


@pytest.mark.parametrize("language", sorted(CASES))
def test_time_limited_request_keeps_scope_without_inferred_expiry_or_new_outreach(language):
    _, _, request = CASES[language]
    evidence, _ = cb.turn_evidence([{"speaker": "them", "text": request}], "")
    assert evidence and not cb.ongoing(evidence)
    stamp = datetime(2026, 11, 16, tzinfo=timezone.utc)
    assert cb.still_applies(evidence, stamp.isoformat(), stamp + timedelta(minutes=1))
    # "For now" and localized "today" lack a verified expiry/zone. A later timestamp is not permission.
    assert cb.still_applies(evidence, stamp.isoformat(), stamp + timedelta(days=100))
    assert "unclear" in cb.read("partner", evidence)["best_move"]
    assert cb.read("partner", evidence)["what_to_say"] is None


@pytest.mark.parametrize("wording", [
    "Por ahora, no me escribas.", "اليوم، لا تتواصل معي.", "Pour le moment, ne me contacte pas.",
    "Bitte kontaktiere mich heute nicht.", "Por enquanto, não me contate.",
])
def test_cataloged_temporal_word_order_does_not_become_an_unqualified_limit(wording):
    evidence, _ = cb.turn_evidence([{"speaker": "them", "text": wording}], "")
    assert evidence and not cb.ongoing(evidence)


@pytest.mark.parametrize(("text", "quote"), [
    ("Don't contact me again?", "don't contact me again"),
    ("What if they said 'don't contact me'?", "don't contact me"),
    ("I want to say 'don't contact me'.", "don't contact me"),
    ("Don't stop contacting me.", "contacting me"),
    ("Don't contact me about work.", "don't contact me"),
    ("Don't contact me on WhatsApp.", "don't contact me"),
    ("Don't contact me after work.", "don't contact me"),
    ("¿No me contactes?", "no me contactes"),
    ("Si te dijera 'no me escribas', sería un ejemplo.", "no me escribas"),
    ("Me asusta que diga 'don't contact me'.", "don't contact me"),
    ("Le dije que no me contactes.", "no me contactes"),
    ("No dije 'no me contactes'.", "no me contactes"),
    ("No dejes de escribirme.", "escribirme"),
    ("No me contactes sobre el trabajo.", "no me contactes"),
    ("No me escribas en WhatsApp.", "no me escribas"),
    ("No me contactes hasta mañana.", "no me contactes"),
    ("هل تقصد لا تتواصل معي؟", "لا تتواصل معي"),
    ("لو قلت لا تتواصل معي فهذا مثال.", "لا تتواصل معي"),
    ("أخشى أن يقول 'don't contact me'.", "don't contact me"),
    ("قلت له لا تتواصل معي.", "لا تتواصل معي"),
    ("لم أقل لا تتواصل معي.", "لا تتواصل معي"),
    ("لا تتوقف عن مراسلتي.", "مراسلتي"),
    ("لا تتواصل معي عن العمل.", "لا تتواصل معي"),
    ("لا تراسلني على واتساب.", "لا تراسلني"),
    ("لا تتواصل معي حتى الغد.", "لا تتواصل معي"),
    ("Ne me contacte plus ?", "ne me contacte plus"),
    ("Et si quelqu'un disait 'ne me contacte plus' ?", "ne me contacte plus"),
    ("J'ai peur qu'il dise 'don't contact me'.", "don't contact me"),
    ("Je lui ai dit 'ne me contacte plus'.", "ne me contacte plus"),
    ("Je n'ai pas dit 'ne me contacte plus'.", "ne me contacte plus"),
    ("N'arrête pas de m'écrire.", "m'écrire"),
    ("Ne me contacte pas au sujet du travail.", "ne me contacte pas"),
    ("Ne m'écris plus sur WhatsApp.", "ne m'écris plus"),
    ("Ne me contacte pas jusqu'à demain.", "ne me contacte pas"),
    ("Kontaktiere mich nicht?", "kontaktiere mich nicht"),
    ("Was wäre wenn jemand sagt 'kontaktiere mich nicht'?", "kontaktiere mich nicht"),
    ("Ich habe Angst vor 'don't contact me'.", "don't contact me"),
    ("Ich sagte ihm 'kontaktiere mich nicht'.", "kontaktiere mich nicht"),
    ("Ich habe nicht gesagt 'kontaktiere mich nicht'.", "kontaktiere mich nicht"),
    ("Hör nicht auf mir zu schreiben.", "mir zu schreiben"),
    ("Kontaktiere mich nicht über die Arbeit.", "kontaktiere mich nicht"),
    ("Schreib mir nicht auf WhatsApp.", "schreib mir nicht"),
    ("Kontaktiere mich nicht bis morgen.", "kontaktiere mich nicht"),
    ("Não me contate?", "não me contate"),
    ("E se alguém dissesse 'não me contate'?", "não me contate"),
    ("Tenho medo de ouvir 'don't contact me'.", "don't contact me"),
    ("Eu disse 'não me contate'.", "não me contate"),
    ("Não disse 'não me contate'.", "não me contate"),
    ("Não pare de me escrever.", "me escrever"),
    ("Não me contate sobre o trabalho.", "não me contate"),
    ("Não me escreva no WhatsApp.", "não me escreva"),
    ("Não me contate até amanhã.", "não me contate"),
    ("Traduce: Don't contact me.", "don't contact me"),
    ("Exercice: Don't contact me.", "don't contact me"),
    ("Übung: Don't contact me.", "don't contact me"),
    ("Traduza: Don't contact me.", "don't contact me"),
    ("تمرين: Don't contact me.", "don't contact me"),
])
def test_questions_hypotheticals_fears_reversed_and_qualified_sources_do_not_create_global_boundary(text, quote):
    assert cb.turn_evidence([{"speaker": "them", "text": text}], "", quote) == (None, False)
    assert cb.turn_evidence([], text, quote) == (None, False)


@pytest.mark.parametrize("invitation", [
    "You can contact me again now?", "You can contact me again now about the budget.",
    "You can contact me again now on WhatsApp only.",
    "¿Puedes contactarme ahora?", "Puedes contactarme ahora sobre el presupuesto.",
    "Puedes contactarme después de las cinco.", "Puedes escribirme ahora por WhatsApp.",
    "هل يمكنك التواصل معي الآن؟", "يمكنك التواصل معي الآن عن العمل.", "يمكنك التواصل معي غدا.",
    "Tu peux me contacter maintenant ?", "Tu peux me contacter maintenant au sujet du budget.",
    "Tu peux me contacter demain.", "Tu peux m'écrire sur WhatsApp maintenant.",
    "Du kannst mich jetzt kontaktieren?", "Du kannst mich jetzt kontaktieren über das Budget.",
    "Du kannst mich morgen kontaktieren.", "Du kannst mir jetzt auf WhatsApp schreiben.",
    "Pode me contatar agora?", "Pode me contatar agora sobre o orçamento.",
    "Pode me contatar amanhã.", "Pode me escrever agora no WhatsApp.",
])
def test_partial_future_or_questioned_invitation_does_not_erase_earlier_boundary(invitation):
    assert not cb.invitation(invitation)
    lines = [{"speaker": "them", "text": "Please do not contact me again."},
             {"speaker": "them", "text": invitation}]
    assert cb.turn_evidence(lines, "")[0]


@pytest.mark.parametrize("text", [
    "No me escribas.", "لا تراسلني.", "Ne m’écris plus.", "Schreib mir nicht mehr.", "Não me escreva.",
])
def test_direct_contact_verb_does_not_claim_permanence_or_another_channel(text):
    evidence, _ = cb.turn_evidence([{"speaker": "them", "text": text}], "")
    assert evidence and cb.read("partner", evidence)["boundary_evidence"] == cb.normalized(text)
    assert "time and channels" in cb.read("partner", evidence)["best_move"]
    assert "forever" not in str(cb.read("partner", evidence)).lower()


def test_catalog_does_not_claim_general_reported_or_mixed_language_coverage():
    for text in ("Mi pareja me pidió que no la contactara.", "Mon partenaire m'a demandé de ne plus le contacter.",
                 "Mein Partner bat mich, ihn nicht zu kontaktieren.", "طلب مني شريكي ألا أتواصل معه.",
                 "Meu parceiro pediu que eu não entrasse em contato."):
        assert cb.evidence_from(text) is None
    assert cb.turn_evidence([{"speaker": "them", "text": "لا تراسلني."}], "", "no me escribas")[0] == "لا تراسلني."


@pytest.mark.parametrize("text", [
    "Je n'ai jamais dit don't contact me", "No he dicho don't contact me",
    "Ich habe nie gesagt don't contact me", "Talvez eu dissesse don't contact me",
    "ربما أقول don't contact me", "I'm afraid they will say, 'Don't contact me.'",
    "I want to say, 'Don't contact me.'", "Translate this, 'Don't contact me.'",
])
def test_peer_negated_hypothetical_excerpts_cannot_supply_missing_speaker_or_scope(text):
    assert cb.turn_evidence([{"speaker": "them", "text": text}], "", "don't contact me") == (None, False)
    assert cb.turn_evidence([], text, "don't contact me") == (None, False)
    assert cb.turn_evidence([], "I want no further contact", "I want no further contact") == (None, False)


@pytest.mark.parametrize("qualifier", [
    "Seulement pour le travail.", "Só pelo email.", "Solo por trabajo.", "Nur wegen der Arbeit.", "فقط عن العمل.",
])
def test_followup_foreign_statement_cannot_silently_qualify_a_general_english_invitation(qualifier):
    text = "You can contact me now. " + qualifier
    assert not cb.invitation(text)
    assert cb.turn_evidence([{"speaker": "them", "text": "Do not contact me."},
                             {"speaker": "them", "text": text}], "")[0]


@pytest.mark.parametrize("qualifier", [
    "Seulement pour le travail", "Só pelo email", "Solo por trabajo", "Nur wegen der Arbeit", "فقط عن العمل",
])
def test_known_foreign_scope_is_not_lost_before_invitation_or_when_asked_as_a_question(qualifier):
    assert not cb.invitation(qualifier + ". You can contact me now.")
    assert not cb.invitation("You can contact me now. " + qualifier + "?")


@pytest.mark.parametrize("wording", ["No more contact.", "No further contact from you."])
def test_literal_english_no_contact_request_does_not_require_a_model_excerpt(wording):
    assert cb.turn_evidence([{"speaker": "them", "text": wording}], "")[0]
