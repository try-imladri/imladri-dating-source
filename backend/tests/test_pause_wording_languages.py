"""Bounded language/intent regression for the deterministic rehearsal guard."""
from copy import deepcopy
import pytest

from backend.advice_wording import current_pause_options, inspect_pause


def line(text, **fields):
    return [{"role": "me", "text": text, **fields}]


EXPLICIT = [
    ("en", "I need ten minutes before discussing this."),
    ("en", "I'd like a brief pause."),
    ("en", "I want to pause now."),
    ("en", "Please give me a moment to think."),
    ("es", "Necesito una pausa."),
    ("es", "Quisiera una breve pausa para pensar."),
    ("es", "Necesito diez minutos para calmarme."),
    ("ar", "أحتاج إلى استراحة قصيرة."),
    ("ar", "أَحْتَاجُ إِلَى اسْتِرَاحَةٍ قَصِيرَةٍ."),
    ("ar", "أحتاج ١٠ دقائق للتفكير."),
    ("ar", "أريد أن أتوقف عن الحديث قليلاً."),
    ("fr", "J'ai besoin d'une pause."),
    ("fr", "Je voudrais prendre une courte pause."),
    ("fr", "J’ai besoin de dix minutes pour réfléchir."),
    ("de", "Ich brauche eine kurze Pause."),
    ("de", "ICH MÖCHTE EINE PAUSE."),
    ("de", "Ich brauche zehn Minuten zum Nachdenken."),
    ("pt", "Eu preciso de uma pausa."),
    ("pt", "Gostaria de uma breve pausa para pensar."),
    ("pt", "Preciso de dez minutos para me acalmar."),
]


@pytest.mark.parametrize("language,text", EXPLICIT)
def test_confident_latest_member_pause_has_bounded_explicit_language_marker(language, text):
    assert inspect_pause(line(text)) == {"version": "pause-wording-v2", "state": "explicit_pause", "language": language}


@pytest.mark.parametrize("language,text", [
    ("en", "Could we pause for a moment?"),
    ("es", "¿Podemos hacer una pausa?"),
    ("ar", "هل يمكننا أخذ استراحة قصيرة؟"),
    ("fr", "Peut-on faire une pause ?"),
    ("de", "Können wir kurz pausieren?"),
    ("pt", "Podemos fazer uma pausa?"),
])
def test_clear_direct_question_is_a_pause_request_not_a_claim_of_partner_agreement(language, text):
    assert inspect_pause(line(text))["state"] == "explicit_pause"
    assert inspect_pause(line(text))["language"] == language
    assert len(current_pause_options(line(text))) == 3


AMBIGUOUS = [
    ("en", "I need a break."), ("en", "I need some space."), ("en", "I need a pause?"),
    ("es", "Necesito un descanso."), ("es", "Quiero un tiempo."),
    ("ar", "أحتاج بعض الوقت."), ("ar", "أريد مساحة."),
    ("fr", "J'ai besoin d'espace."), ("fr", "J'ai besoin de temps."),
    ("de", "Ich brauche Abstand."), ("de", "Ich möchte eine Auszeit."),
    ("pt", "Preciso de um tempo."), ("pt", "Quero um descanso."),
]


@pytest.mark.parametrize("language,text", AMBIGUOUS)
def test_ambiguous_request_gets_clarification_instead_of_an_invented_active_pause(language, text):
    assert inspect_pause(line(text)) == {"version": "pause-wording-v2", "state": "ambiguous_pause", "language": language}
    options = current_pause_options(line(text))
    assert [option["tone"] for option in options] == ["warm", "neutral", "difficult"]
    assert len({option["suggested"] for option in options}) == 3
    assert all(option["suggested"] not in {row["suggested"] for row in current_pause_options(line(dict((lang, phrase) for lang, phrase in EXPLICIT)[language]))} for option in options)


RETURNS = [
    ("en", "I need a pause. I'll call you tomorrow."),
    ("en", "I need ten minutes before we talk, then we can talk at six."),
    ("es", "Necesito una pausa. Te llamaré mañana."),
    ("es", "Necesito una pausa. Voy a escribirte en diez minutos."),
    ("ar", "أحتاج إلى استراحة. سأعود للحديث بعد عشر دقائق."),
    ("ar", "أحتاج إلى استراحة. سوف أتصل بك غدًا."),
    ("fr", "J'ai besoin d'une pause. Je te rappellerai demain."),
    ("fr", "J'ai besoin d'une pause. Je vais t'appeler dans dix minutes."),
    ("de", "Ich brauche eine Pause. Ich werde dich morgen anrufen."),
    ("de", "Ich brauche eine Pause. Ich melde mich in zehn Minuten."),
    ("pt", "Preciso de uma pausa. Vou te ligar amanhã."),
    ("pt", "Preciso de uma pausa. Eu volto a falar amanhã."),
    ("es", "Necesito una pausa. I'll call you tomorrow."),
    ("en", "I need a pause. Te escribiré después."),
]


@pytest.mark.parametrize("language,text", RETURNS)
def test_actual_offered_return_timing_and_commitment_are_preserved_verbatim(language, text):
    messages = line(text)
    saved = deepcopy(messages)
    assert inspect_pause(messages) == {"version": "pause-wording-v2", "state": "return_offer", "language": language}
    options = current_pause_options(messages)
    assert [option["tone"] for option in options] == ["warm", "neutral", "difficult"]
    assert all(option["suggested"] == text for option in options)
    assert messages == saved


@pytest.mark.parametrize("text", [
    "I need a pause. I can't promise when I'll call.",
    "Necesito una pausa. No puedo hablar mañana.",
    "أحتاج إلى استراحة. لا أعرف متى سأتصل بك.",
    "J'ai besoin d'une pause. Je ne sais pas quand je t'appellerai.",
    "Ich brauche eine Pause. Ich kann nicht versprechen, wann ich anrufe.",
    "Preciso de uma pausa. Não posso falar amanhã.",
])
def test_uncertain_or_negated_return_is_not_misrepresented_as_an_offer(text):
    assert inspect_pause(line(text))["state"] == "explicit_pause"
    assert current_pause_options(line(text)) is not None


@pytest.mark.parametrize("language,text", EXPLICIT[:1] + [EXPLICIT[4], EXPLICIT[7], EXPLICIT[11], EXPLICIT[14], EXPLICIT[17]])
@pytest.mark.parametrize("kind", ["double", "single", "curly", "markdown", "reported", "unbalanced"])
def test_quoted_reported_and_incomplete_quotation_is_not_current_member_intent(language, text, kind):
    words = {"double": '"' + text + '"', "single": "'" + text + "'", "curly": "“" + text + "”",
             "markdown": "> " + text, "reported": "They wrote:\n" + text, "unbalanced": '"' + text}[kind]
    assert inspect_pause(line(words)) is None
    assert current_pause_options(line(words)) is None


@pytest.mark.parametrize("text", [
    'I need a pause. My draft said "I will call you tomorrow."',
    "I need a pause. My draft said 'I'll call you tomorrow.'",
    "I need a pause.\n> I'll call you tomorrow.",
    "J'ai besoin d'une pause. Le brouillon disait « Je te rappellerai demain. »",
    "Ich brauche eine Pause. Der Entwurf war: „Ich werde dich morgen anrufen.“",
])
def test_a_quoted_offer_is_not_a_new_member_commitment(text):
    assert inspect_pause(line(text))["state"] == "explicit_pause"


@pytest.mark.parametrize("text", [
    "I don't need a pause.", "I need a pause.\nActually, I don't need one.",
    "I need a pause.\nI'm ready to talk now.", "I need a pause. Never mind.",
    "No necesito una pausa.", "Necesito una pausa.\nEn realidad, no la necesito.",
    "Necesito una pausa.\nYa estoy lista para hablar.",
    "لا أحتاج استراحة.", "أحتاج إلى استراحة.\nلم أعد أحتاجها.",
    "أحتاج إلى استراحة.\nأنا مستعد للحديث الآن.",
    "Je ne veux pas de pause.", "J'ai besoin d'une pause.\nEn fait, je n'en ai plus besoin.",
    "J'ai besoin d'une pause.\nJe suis prête à parler.",
    "Ich brauche keine Pause.", "Ich brauche eine Pause.\nDoch ich brauche sie nicht.",
    "Ich brauche eine Pause.\nIch bin jetzt bereit zu reden.",
    "Não preciso de uma pausa.", "Preciso de uma pausa.\nNa verdade, não preciso disso.",
    "Preciso de uma pausa.\nJá estou pronta para conversar.",
])
def test_negation_retraction_or_explicit_readiness_does_not_become_a_current_pause(text):
    assert inspect_pause(line(text)) is None


@pytest.mark.parametrize("text", [
    "I need a pause. Don't text me.", "I need a pause. No more messages.",
    'I need a pause. They said "Don\'t contact me."',
    "Necesito una pausa. No me escribas.", "أحتاج إلى استراحة. لا تتواصل معي.",
    "J'ai besoin d'une pause. Ne me contacte plus.", "Ich brauche eine Pause. Kontaktiere mich nicht.",
    "Preciso de uma pausa. Não me ligue.",
])
def test_any_clear_no_contact_boundary_defers_to_existing_contact_rules(text):
    assert inspect_pause(line(text)) is None
    assert current_pause_options(line(text)) is None


@pytest.mark.parametrize("text", [
    "I need a break from this relationship.", "I need a pause in our relationship.",
    "Necesito una pausa de nuestra relación.", "أحتاج إلى استراحة من العلاقة.",
    "J'ai besoin d'une pause dans notre relation.", "Ich brauche eine Pause von unserer Beziehung.",
    "Preciso de uma pausa do relacionamento.",
])
def test_relationship_break_or_breakup_is_not_rewritten_as_a_brief_conversation_pause(text):
    assert inspect_pause(line(text)) is None


@pytest.mark.parametrize("text", [
    "I need a pause, not a breakup.", "Necesito una pausa, no una ruptura.",
    "J'ai besoin d'une pause, pas une rupture.", "Ich brauche eine Pause, keine Trennung.",
])
def test_explicitly_negated_breakup_does_not_erase_a_direct_pause_request(text):
    assert inspect_pause(line(text))["state"] == "explicit_pause"


@pytest.mark.parametrize("messages", [
    [], None, {}, ["I need a pause"], [{"role": "them", "text": "I need a pause."}],
    [{"role": "me", "speaker": "them", "text": "I need a pause."}],
    [{"role": "Me", "text": "I need a pause."}], [{"role": "me", "text": None}],
    [{"role": "me", "text": ""}], [{"role": "me", "text": True}],
    [{"role": "me", "text": "I need a pause."}, {"role": "them", "text": "Okay."}],
    [{"role": "me", "text": "I need a pause."}, {"role": "me", "text": "What time is dinner?"}],
    [{"role": "me", "text": "I need a pause." + "x" * 4096}],
    [{"role": "me", "text": "I need a pause.\n" * 13}],
    [{"role": "me", "text": "I need a pause.\u202e"}],
])
def test_invalid_oversized_old_or_wrong_speaker_input_is_not_inspected(messages):
    assert inspect_pause(messages) is None


def test_explicit_speaker_alias_is_supported_without_conflicting_actor_fields():
    assert inspect_pause([{"speaker": "me", "text": "Necesito una pausa."}])["language"] == "es"


@pytest.mark.parametrize("text", [
    "Ho bisogno di una pausa.", "Мне нужна пауза.", "休憩が必要です。", "Jeg trenger en pause.",
    "I need ten minutes to get there.", "I need a short break from work.",
    "I need a pause in this recording.", "Give me a break!", "I need zero minutes to think.",
    "I mochte eine Pause.", "Necesito una pausa para comprar leche.",
])
def test_unrecognized_language_or_non_conversation_context_keeps_ordinary_flow(text):
    assert inspect_pause(line(text)) is None


@pytest.mark.parametrize("language,example,token", [
    ("en", "I need a pause.", "pause"), ("es", "Necesito una pausa.", "pausa"),
    ("ar", "أحتاج إلى استراحة.", "الاستراحة"), ("fr", "J'ai besoin d'une pause.", "pause"),
    ("de", "Ich brauche eine Pause.", "Pause"), ("pt", "Preciso de uma pausa.", "pausa"),
])
def test_localized_branches_are_fresh_bounded_and_do_not_invent_elapsed_time_or_resumption(language, example, token):
    messages = line(example)
    first = current_pause_options(messages)
    assert inspect_pause(messages)["language"] == language
    assert [row["tone"] for row in first] == ["warm", "neutral", "difficult"]
    assert all(set(row) == {"reaction", "tone", "meaning", "outcome", "suggested"} for row in first)
    assert all(token.casefold() in row["suggested"].casefold() for row in first)
    assert all(isinstance(value, str) and 0 < len(value) <= 500 for row in first for value in row.values())
    suggested = " ".join(row["suggested"] for row in first).casefold()
    assert not any(term in suggested for term in ("tomorrow", "saturday", "mañana", "demain", "morgen", "amanhã", "غدا", "10", "I'll", "i will", "te llamaré", "je te rappellerai"))
    first[0]["suggested"] = "mutated"
    assert current_pause_options(messages)[0]["suggested"] != "mutated"


def test_input_messages_are_never_changed_by_normalization_or_generated_options():
    messages = line("أَحْتَاجُ إِلَى اسْتِرَاحَةٍ قَصِيرَةٍ.")
    before = deepcopy(messages)
    current_pause_options(messages)
    assert messages == before


@pytest.mark.parametrize("language,text", [
    ("en", "Please, I need a pause."), ("es", "Por favor, necesito una pausa."),
    ("ar", "من فضلك، أحتاج إلى استراحة."), ("fr", "S'il te plaît, j'ai besoin d'une pause."),
    ("de", "Bitte, ich brauche eine Pause."), ("pt", "Por favor, preciso de uma pausa."),
])
def test_a_courtesy_prefix_does_not_change_direct_current_intent(language, text):
    assert inspect_pause(line(text))["state"] == "explicit_pause"
    assert inspect_pause(line(text))["language"] == language


@pytest.mark.parametrize("text", [
    "I need a pause, Jordan said.", "I need a pause.\nMy partner wrote that.",
    "Necesito una pausa, dijo ella.", "أحتاج إلى استراحة، قالت سلمى.",
    "J'ai besoin d'une pause, dit-elle.", "Ich brauche eine Pause, sagte er.",
    "Preciso de uma pausa, disse ela.",
])
def test_postposed_reporting_is_not_the_members_current_request(text):
    assert inspect_pause(line(text)) is None


@pytest.mark.parametrize("text", [
    "I need a pause.\nI don't need a short pause.",
    "I need a pause.\nI don't want to pause.",
    "Necesito una pausa.\nNo quiero hacer una pausa.",
    "J'ai besoin d'une pause.\nJe n'ai pas besoin d'une courte pause.",
    "Ich brauche eine Pause.\nIch brauche keine kurze Pause.",
    "Preciso de uma pausa.\nNão quero uma breve pausa.",
])
def test_explicit_later_negation_is_not_erased_by_an_earlier_pause_sentence(text):
    assert inspect_pause(line(text)) is None


@pytest.mark.parametrize("text", [
    "I need a pause, is what I might say if we ever fight.",
    "Necesito una pausa, es lo que diría si discutimos.",
    "J'ai besoin d'une pause, c'est ce que je dirais si nous nous disputions.",
    "Ich brauche eine Pause, würde ich sagen, wenn wir streiten.",
    "Preciso de uma pausa, é o que eu diria se discutíssemos.",
    "أحتاج إلى استراحة، هذا ما قد أقوله إذا تشاجرنا.",
    "I need a pause. That is what I would say in that situation.",
    "I need a pause; I said that in yesterday's exercise.",
    "I need a pause, is that what I should say?",
    "Necesito una pausa; dije eso durante el ejercicio.",
    "J'ai besoin d'une pause. Je dirais cela dans cet exercice.",
    "Ich brauche eine Pause; sagte ich gestern.",
    "Preciso de uma pausa. Eu diria isso naquele exercício.",
    "أحتاج إلى استراحة. هذا ما كنت أقوله في التمرين.",
])
def test_hypothetical_or_reported_tail_does_not_turn_a_sample_phrase_into_current_intent(text):
    assert inspect_pause(line(text)) is None
    assert current_pause_options(line(text)) is None


@pytest.mark.parametrize("text", [
    "I need a pause, because I need to think.",
    "Necesito una pausa, porque necesito pensar.",
    "J'ai besoin d'une pause, parce que je dois réfléchir.",
    "Ich brauche eine Pause, weil ich nachdenken muss.",
    "Preciso de uma pausa, porque preciso pensar.",
    "أحتاج إلى استراحة، لأنني بحاجة إلى التفكير.",
])
def test_a_direct_pause_with_an_actual_current_reason_still_has_current_intent(text):
    assert inspect_pause(line(text))["state"] == "explicit_pause"


@pytest.mark.parametrize("language,text,gratitude", [
    ("en", "I need a pause.", "thanks"),
    ("es", "Necesito una pausa.", "gracias"),
    ("fr", "J'ai besoin d'une pause.", "merci"),
    ("de", "Ich brauche eine Pause.", "danke"),
    ("pt", "Preciso de uma pausa.", "agradeço"),
    ("ar", "أحتاج إلى استراحة.", "شكر"),
])
def test_generated_pause_draft_preserves_request_without_inventing_gratitude(language, text, gratitude):
    assert inspect_pause(line(text))["language"] == language
    options = current_pause_options(line(text))
    assert len({option["suggested"] for option in options}) == 3
    assert all(gratitude not in option["suggested"].casefold() for option in options)


@pytest.mark.parametrize("language,text,state", [
    ("en", "I need space.", "ambiguous_pause"),
    ("ar", "أحتاج إلى مساحة.", "ambiguous_pause"),
    ("fr", "J’ai besoin d’une pause. Je t’écrirai demain.", "return_offer"),
])
def test_actual_evaluation_target_wording_preserves_ambiguity_or_explicit_return(language, text, state):
    assert inspect_pause(line(text)) == {"version": "pause-wording-v2", "state": state, "language": language}
    options = current_pause_options(line(text))
    assert options is not None
    if state == "return_offer":
        assert all(option["suggested"] == text for option in options)


@pytest.mark.parametrize("text", [
    '"I need space."', "I don't need space.", "They said I need space.",
    '"أحتاج إلى مساحة."', "لا أحتاج إلى مساحة.", "قالت أحتاج إلى مساحة.",
])
def test_new_space_wording_does_not_override_quoted_negated_or_other_speaker_provenance(text):
    assert inspect_pause(line(text)) is None
    assert inspect_pause([{"role": "them", "text": text}]) is None


@pytest.mark.parametrize("text", [
    'J’ai besoin d’une pause. Mon brouillon disait « Je t’écrirai demain. »',
    'J’ai besoin d’une pause. Je ne sais pas quand je t’écrirai.',
])
def test_french_written_return_is_not_invented_from_a_quote_or_uncertainty(text):
    assert inspect_pause(line(text)) == {"version": "pause-wording-v2", "state": "explicit_pause", "language": "fr"}


@pytest.mark.parametrize("language,text", [
    ("en", "  I need a pause. I'll text you tomorrow if I am ready.\n"),
    ("es", "Necesito una pausa. Te escribiré mañana si puedo."),
    ("ar", "أحتاج إلى استراحة. سأراسلك غدًا إذا كنت مستعدًا."),
    ("fr", "J’ai besoin d’une pause. Je t’écrirai demain si je me sens prêt."),
    ("de", "Ich brauche eine Pause. Ich melde mich morgen, wenn ich bereit bin."),
    ("pt", "Preciso de uma pausa. Vou te escrever amanhã se eu puder."),
    ("en", "I need a pause. I'll call only if you want to talk."),
    ("fr", "J’ai besoin d’une pause. Je t’écrirai demain; je ne propose pas un appel."),
])
def test_return_offer_suggestions_preserve_exact_actor_channel_condition_and_whitespace(language, text):
    messages = line(text)
    original = deepcopy(messages)
    assert inspect_pause(messages) == {"version": "pause-wording-v2", "state": "return_offer", "language": language}
    options = current_pause_options(messages)
    assert [option["tone"] for option in options] == ["warm", "neutral", "difficult"]
    assert len({option["reaction"] for option in options}) == 3
    assert all(set(option) == {"reaction", "tone", "meaning", "outcome", "suggested"} for option in options)
    assert all(option["suggested"] == text for option in options)
    assert messages == original
    # Repeated suggestions intentionally preserve one actual offer rather than
    # changing its promise/channel to create three artificial variations.
    options[0]["suggested"] = "changed"
    assert all(option["suggested"] == text for option in current_pause_options(messages))


@pytest.mark.parametrize("language,text", [RETURNS[0], RETURNS[2], RETURNS[4], RETURNS[6], RETURNS[8], RETURNS[10]])
def test_return_offer_branch_never_gets_authority_from_another_speaker_or_a_quoted_draft(language, text):
    assert current_pause_options([{"role": "them", "text": text}]) is None
    assert current_pause_options(line('"' + text + '"')) is None


@pytest.mark.parametrize("text", [
    "I need a pause. Don't text me. I'll call you tomorrow.",
    "Necesito una pausa. No me escribas. Te llamaré mañana.",
    "أحتاج إلى استراحة. لا تتواصل معي. سأتصل بك غدًا.",
    "J'ai besoin d'une pause. Ne me contacte plus. Je t'écrirai demain.",
    "Ich brauche eine Pause. Kontaktiere mich nicht. Ich melde mich morgen.",
    "Preciso de uma pausa. Não me ligue. Vou te escrever amanhã.",
])
def test_recognized_later_offer_does_not_bypass_the_existing_no_contact_guard(text):
    assert inspect_pause(line(text)) is None
    assert current_pause_options(line(text)) is None


def test_french_written_offer_does_not_turn_into_discussion_or_partner_agreement():
    text = "J’ai besoin d’une pause. Je t’écrirai demain."
    options = current_pause_options(line(text))
    assert all(option["suggested"] == text for option in options)
    other_words = " ".join(option[key] for option in options for key in ("reaction", "meaning", "outcome")).casefold()
    assert "demain" not in other_words and "comme prévu" not in other_words and "on en reparle" not in other_words
    assert all("ficti" in option["meaning"].casefold() for option in options)
