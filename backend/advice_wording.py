"""Keep copyable replies grounded in the availability the member supplied."""
import re
import unicodedata
from typing import Literal, TypedDict


def grounded_member_observation(candidate, lines):
    """Return a literal member-authored observation, never the model's summary.

    The question to the app, generated replies, and the other speaker are not
    available as evidence. Broad behavioral inference belongs to the separate
    pattern scan and member-confirmation flow.
    """
    indices = candidate.get("source_indices") if isinstance(candidate, dict) else None
    if not isinstance(indices, list) or not 1 <= len(indices) <= 3 or not isinstance(lines, list):
        return None
    for index in indices:
        if type(index) is not int or not 0 <= index < len(lines):
            return None
        line = lines[index]
        if (not isinstance(line, dict) or line.get("speaker") != "me"
                or not isinstance(line.get("text"), str) or not line["text"].strip()):
            return None
    index = indices[0]
    quote = lines[index]["text"].strip()
    # Preserve a literal excerpt without turning a long message into a new
    # paraphrase, and stay below the stored observation's 300-character bound.
    if len(quote) > 220:
        quote = quote[:220].rsplit(" ", 1)[0] or quote[:220]
        quote += "…"
    return {"text": f'In this exchange you wrote: “{quote}”', "source_indices": [index],
            "evidence_kind": "literal_member_quote"}


class PauseInspection(TypedDict):
    version: Literal["pause-wording-v2"]
    state: Literal["explicit_pause", "ambiguous_pause", "return_offer"]
    language: Literal["en", "es", "ar", "fr", "de", "pt"]


_NUMBER = r"(?:[1-9]\d{0,2}|one|two|three|five|ten|fifteen|twenty|a few)"
_EXPLICIT_PAUSE = {
    "en": rf"(?:i (?:need|want|would like) (?:a (?:short |brief |little )?pause|a (?:short|brief) break)|i'd like a (?:short |brief )?pause|i(?:'d| would) like to pause|i (?:need|want) to pause|i (?:need|want) {_NUMBER} minutes? (?:before (?:we (?:talk|discuss|continue)|talking|discussing|continuing|i (?:talk|reply|respond))|to (?:think|cool down|calm down))(?: [^.!?;\n,]{{1,160}})?|(?:please )?give me (?:a moment|{_NUMBER} minutes?) to think|let's pause (?:this|the) conversation)\b",
    "es": r"(?:(?:necesito|quiero|quisiera) (?:una (?:breve |corta )?pausa|un (?:breve|corto) descanso)|(?:necesito|quiero) (?:hacer|tomar) una pausa|necesito (?:[1-9]\d{0,2}|cinco|diez|quince|unos) minutos para (?:pensar|calmarme))\b",
    "ar": r"(?:(?:انا )?(?:احتاج|اريد) (?:الى )?(?:استراحة(?: قصيرة)?|وقفة قصيرة)|(?:انا )?اود اخذ استراحة|(?:انا )?احتاج (?:[1-9]\d{0,2}|خمس|عشر|بضع) دقائق (?:للتفكير|لاهدا)|اريد ان اتوقف عن الحديث قليلا)(?=\s|[.،,؛;؟!?]|$)",
    "fr": r"(?:j'ai besoin (?:d'une (?:courte |petite )?pause|de faire une pause)|je (?:veux|voudrais|souhaite) (?:faire |prendre )?une (?:courte |petite )?pause|j'ai besoin de (?:[1-9]\d{0,2}|cinq|dix|quinze|quelques) minutes pour r[eé]fl[eé]chir)\b",
    "de": r"(?:ich (?:brauche|möchte|will) eine (?:kurze |kleine )?pause|ich möchte kurz pausieren|ich brauche (?:[1-9]\d{0,2}|fünf|zehn|fünfzehn|ein paar) minuten (?:zum nachdenken|um nachzudenken))\b",
    "pt": r"(?:(?:eu )?(?:preciso de|quero|gostaria de) uma (?:breve |curta )?pausa|(?:eu )?preciso (?:fazer|de fazer) uma pausa|(?:eu )?preciso de (?:[1-9]\d{0,2}|cinco|dez|quinze|alguns) minutos para (?:pensar|me acalmar))\b",
}
_PAUSE_REQUEST = {
    "en": r"(?:please )?(?:can|could) we (?:pause|take a (?:short |brief )?pause|take a short break)\b",
    "es": r"(?:podemos|podr[ií]amos) (?:hacer|tomar) una pausa\b",
    "ar": r"هل يمكننا (?:اخذ استراحة(?: قصيرة)?|التوقف عن الحديث قليلا)(?=\s|[.،,؛;؟!?]|$)",
    "fr": r"(?:peut-on|est-ce qu'on peut) faire une pause\b",
    "de": r"können wir (?:kurz pausieren|eine (?:kurze )?pause machen)\b",
    "pt": r"(?:podemos|poder[ií]amos) (?:fazer|dar) uma pausa\b",
}
_AMBIGUOUS_PAUSE = {
    "en": r"(?:i (?:need|want)|i think i need|i(?:'d| would) like) (?:a break|some space|space|some time|a moment)(?: (?:please|right now|for now))?[.!?]*",
    "es": r"(?:creo que )?(?:necesito|quiero|quisiera) (?:un descanso|espacio|un tiempo)(?: ahora)?[.!?]*",
    "ar": r"(?:انا )?(?:احتاج|اريد) (?:الى )?(?:مساحة|بعض الوقت|وقتا)(?: الان)?[.؟!?]*",
    "fr": r"(?:j'ai besoin (?:d'espace|de temps|d'un break|d'une coupure)|je crois que j'ai besoin d'espace)[.!?]*",
    "de": r"ich (?:brauche|möchte) (?:abstand|eine auszeit|etwas zeit)[.!?]*",
    "pt": r"(?:eu )?(?:preciso de|quero) (?:um tempo|espaço|espaco|um descanso)[.!?]*",
}
_TAIL = {
    "en": r"(?:now|right now|for now|for (?:a moment|a few minutes|[1-9]\d{0,2} minutes)|to (?:think|cool down|calm down)|before (?:we|i|talking|discussing)|because)\b",
    "es": r"(?:ahora|por (?:un momento|unos minutos)|para (?:pensar|calmarme)|antes de (?:hablar|seguir)|porque)\b",
    "ar": r"(?:الان|قليلا|للتفكير|لاهدا|قبل الحديث|لان)(?=\s|[.،,؛;؟!?]|$)",
    "fr": r"(?:maintenant|pour (?:r[eé]fl[eé]chir|me calmer|quelques minutes)|avant de parler|parce que)\b",
    "de": r"(?:jetzt|zum nachdenken|um (?:nachzudenken|mich zu beruhigen)|bevor wir|weil)\b",
    "pt": r"(?:agora|por (?:um momento|alguns minutos)|para (?:pensar|me acalmar)|antes de falar|porque)\b",
}
_NEGATED_PAUSE = re.compile(
    r"\b(?:i (?:do not|don't|never|no longer) (?:need|want)|i am not asking for) (?:a |any |that |this |the )?(?:(?:short|brief) )?(?:pause|break|space|one)\b|"
    r"\bi (?:do not|don't) (?:need|want) to pause\b|"
    r"\bno (?:necesito|quiero) (?:una (?:breve |corta )?pausa|un descanso|espacio|hacer una pausa)\b|"
    r"(?:لا (?:احتاج|اريد)|لست بحاجة الى) (?:استراحة|وقفة|مساحة)|"
    r"\b(?:je ne veux pas|je n'ai pas besoin) (?:de pause|d'une (?:courte )?pause|d'espace|faire une pause)\b|"
    r"\bich (?:brauche|möchte|will) (?:keine (?:kurze )?pause|keinen abstand|keine auszeit|nicht pausieren)\b|"
    r"\bn[aã]o (?:preciso de|quero) (?:uma (?:breve |curta )?pausa|um tempo|espaço|espaco|fazer uma pausa)\b|"
    r"\b(?:no la necesito|ya no necesito|je n'en ai (?:pas|plus) besoin|ich brauche (?:sie|das) nicht|n[aã]o preciso (?:disso|mais))\b|لم اعد احتاجها|"
    r"\b(?:actually no|forget that|never mind|on second thought,? no)\b", re.I)
_NO_CONTACT = re.compile(
    r"\b(?:no[ -]contact|no more (?:texts|messages|calls)|(?:do not|don't|stop) (?:contact|contacting|text|texting|message|messaging|call|calling)(?:ing)? me|leave me alone)\b|"
    r"\b(?:no me (?:contactes|escribas|llames)|no (?:quiero|deseo) (?:m[aá]s )?contacto)\b|"
    r"(?:لا (?:تتواصل معي|تراسلني|تتصل بي)|لا اريد (?:اي )?تواصل)|"
    r"\b(?:ne me contacte(?:z)? plus|ne m'[eé]cris plus|aucun contact)\b|"
    r"\b(?:kontaktiere mich nicht|schreib mir nicht|kein(?:en)? kontakt)\b|"
    r"\b(?:n[aã]o (?:me contacte|me contate|me escreva|me ligue)|sem contato)\b", re.I)
_NEGATED_BREAKUP = re.compile(r"\b(?:not a breakup|not breaking up|no es una ruptura|no una ruptura|pas une rupture|keine trennung|n[aã]o [eé] uma separa[cç][aã]o)\b|ليس انفصالا", re.I)
_BREAKUP = re.compile(
    r"\b(?:break up|breakup|(?:break|pause|space) (?:from|in) (?:(?:this|our|the) )?relationship|end (?:this|our|the) relationship)\b|"
    r"\b(?:de (?:esta|nuestra|la) relaci[oó]n|en nuestra relaci[oó]n|ruptura|terminar lo nuestro)\b|"
    r"(?:الانفصال|انهاء العلاقة|من العلاقة|في علاقتنا)|"
    r"\b(?:rupture|rompre|de notre relation|dans notre relation|trennung|schluss machen|von unserer beziehung|in unserer beziehung)\b|"
    r"\b(?:terminar (?:o|nosso) relacionamento|separa[cç][aã]o|do relacionamento|da rela[cç][aã]o)\b", re.I)
_RETURN = re.compile(
    r"\b(?:i(?:'ll| will| can)|we(?:'ll| will| can)) (?:call|text|message|talk|return|resume|come back|be back|get back|pick this up)\b|\b(?:let's|can we) (?:talk|resume|pick this up)\b|"
    r"\b(?:te (?:llamar[eé]|escribir[eé])|volver[eé] a (?:hablar|escribir)|podemos (?:volver a )?(?:hablar|retomar)|puedo (?:hablar|retomar|llamarte|escribirte)|voy a (?:llamarte|escribirte|hablar)|hablar[eé] contigo)\b|"
    r"(?:سا(?:عود|تصل|راسلك|تحدث)|سوف (?:اعود|اتصل|اتحدث)|يمكن(?:ني|نا) (?:ان )?(?:نتحدث|اتحدث|نكمل))|"
    r"\b(?:je (?:te rappellerai|t'appellerai|t'[eé]crirai|reviendrai|peux (?:parler|reprendre)|vais (?:te rappeler|t'appeler|te parler|reprendre))|on peut (?:parler|reprendre)|on (?:se )?reparle|nous (?:pourrons|pouvons) parler)\b|"
    r"\b(?:ich (?:werde|kann) (?:dich |dir |mich )?(?:(?:morgen|später|heute) )?(?:anrufen|zurückrufen|wiederkommen|schreiben|reden|melden)|ich (?:rufe|melde mich|schreibe dir) (?:dich )?(?:sp[aä]ter|morgen|in (?:[1-9]\d{0,2}|zehn|fünf) minuten)|wir können (?:reden|sprechen|weitermachen))\b|"
    r"\b(?:(?:eu )?vou (?:te ligar|ligar|te escrever|escrever|voltar|retomar)|(?:eu )?posso (?:falar|conversar|retomar)|(?:eu )?volto a (?:falar|conversar)|podemos (?:conversar|retomar|falar)|te (?:ligarei|escreverei)|voltarei a (?:falar|conversar))\b", re.I)
_UNCERTAIN_RETURN = re.compile(
    r"\b(?:cannot|can't|don't know|do not know|not sure|no puedo|no s[eé]|je ne sais pas|je ne peux pas|nicht versprechen|weiß nicht|n[aã]o posso|n[aã]o sei)\b|(?:لا استطيع|لا اعرف)", re.I)
_NEGATED_OFFER_PREFIX = re.compile(r"(?:\b(?:not|no|n[aã]o|nicht|pas)|لا|لن)\s*$", re.I)
_READY_AGAIN = re.compile(
    r"(?:^|[.!?؛;\n])\s*(?:(?:actually|en realidad|en fait|na verdade)[, ]+)?(?:"
    r"i(?:'m| am) ready (?:now|to (?:talk|continue|resume))|ya estoy list[oa] para hablar|"
    r"je suis (?:maintenant )?pr[eê]t(?:e)? [aà] (?:parler|reprendre)|ich bin (?:jetzt )?bereit (?:zu reden|weiterzureden)|"
    r"j[aá] estou pront[oa] para (?:falar|conversar)|(?:انا )?مستعد(?:ة)? للحديث الان)", re.I)
_REPORTED_PAUSE = re.compile(
    r"[,;\n]\s*(?:[\w-]{1,30}\s+){1,3}(?:said|wrote)\b|"
    r"[,;\n]\s*(?:dijo|escribi[oó])\b|[,;\n]\s*(?:dit-il|dit-elle|a-t-il dit|a-t-elle dit)\b|"
    r"[,;\n]\s*(?:sagte|schrieb) (?:er|sie)\b|[,;\n]\s*(?:disse|escreveu) (?:ele|ela)\b|[,،\n]\s*قال(?:ت)?\b", re.I)
_NONCURRENT_PAUSE_TAIL = re.compile(
    r"[,،;؛:.!?؟\n]\s*(?:"
    r"(?:(?:is|was) what|that(?:'s| is| was) what) i (?:might|would|could|used to) say\b|"
    r"i (?:(?:might|would|could) say|said|wrote)\b|is that what i (?:should|could|might|would) say\b|"
    r"(?:es|era|ser[ií]a) lo que (?:yo )?(?:dir[ií]a|dije|podr[ií]a decir)\b|"
    r"(?:yo )?(?:dir[ií]a|dije|escrib[ií])\b|"
    r"(?:c'est|c'[eé]tait|ce serait) ce que je (?:dirais|disais|pourrais dire)\b|"
    r"(?:je dirais|j'ai dit|j'avais dit)\b|"
    r"(?:w[uü]rde ich sagen|h[aä]tte ich gesagt|sagte ich|schrieb ich)\b|"
    r"(?:[eé]|era|seria) o que (?:eu )?(?:diria|disse|poderia dizer)\b|"
    r"(?:eu )?(?:diria|disse|escrevi)\b|"
    r"(?:هذا|ذلك) ما (?:قد |كنت )?(?:اقوله|قلته)|(?:قد اقول|قلت ذلك)"
    r")", re.I)
_POLITE_PREFIX = re.compile(r"^(?:please|por favor|من فضلك|s'il te pla[iî]t|bitte)[,، ]+", re.I)
_COMPILED = {kind: {language: re.compile(r"^(?:" + pattern + r")", re.I) for language, pattern in patterns.items()}
             for kind, patterns in (("explicit", _EXPLICIT_PAUSE), ("request", _PAUSE_REQUEST), ("ambiguous", _AMBIGUOUS_PAUSE), ("tail", _TAIL))}


def _pause_text(text):
    value = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    value = value.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ـ": ""}))
    return "".join(str(unicodedata.decimal(ch)) if unicodedata.category(ch) == "Nd" else ch for ch in value
                   if not (unicodedata.category(ch) == "Mn" and 0x0610 <= ord(ch) <= 0x06ED))


def _unquoted_pause_text(text):
    """Linear quote masking; apostrophes inside words are not quotation marks."""
    text = "\n".join("" if line.lstrip().startswith(">") else line for line in text.splitlines())
    pairs = {'"': '"', "'": "'", "“": "”", "‘": "’", "«": "»", "„": "“", "‚": "‘", "`": "`"}
    out, closing = [], None
    for index, char in enumerate(text):
        before = text[index - 1] if index else ""
        after = text[index + 1] if index + 1 < len(text) else ""
        apostrophe = char in ("'", "’") and before.isalnum() and after.isalnum()
        escaped = index > 0 and text[index - 1] == "\\"
        if closing:
            if char == closing and not apostrophe and not escaped:
                closing = None
            out.append("\n" if char == "\n" else " ")
        elif char in pairs and not apostrophe and not escaped:
            closing = pairs[char]
            out.append(" ")
        else:
            out.append(char)
    return None if closing else "".join(out)


def inspect_pause(messages) -> PauseInspection | None:
    """Recognize a small, explicit multilingual subset, never infer readiness.

    Only the latest member line supplies intent. Unsupported wording, explicit
    no-contact, negation, relationship-break wording and unbalanced quotations
    keep the ordinary flow; no language or motive is guessed from a profile.
    """
    if not isinstance(messages, list) or not messages or not isinstance(messages[-1], dict):
        return None
    last = messages[-1]
    role, speaker, text = last.get("role"), last.get("speaker"), last.get("text")
    if (role is not None and speaker is not None and role != speaker
            or (role if role is not None else speaker) != "me"
            or not isinstance(text, str) or not 0 < len(text) <= 4096 or len(text.splitlines()) > 12
            or any(unicodedata.category(ch) == "Cf" for ch in text)):
        return None
    # A quoted request for no contact is still a reason not to introduce replies.
    if _NO_CONTACT.search(_pause_text(text)):
        return None
    unquoted = _unquoted_pause_text(text)
    if unquoted is None:
        return None
    value = _pause_text(unquoted).strip().lstrip("¿¡").strip()
    value = _POLITE_PREFIX.sub("", value, count=1)
    if (not value or _NEGATED_PAUSE.search(value) or _READY_AGAIN.search(value) or _REPORTED_PAUSE.search(value)
            or _NONCURRENT_PAUSE_TAIL.search(value)
            or _BREAKUP.search(_NEGATED_BREAKUP.sub("", value))):
        return None
    found = None
    for kind in ("request", "explicit", "ambiguous"):
        for language, pattern in _COMPILED[kind].items():
            match = pattern.match(value)
            if not match:
                continue
            tail = value[match.end():].lstrip()
            if kind == "ambiguous":
                if tail:
                    continue
            elif tail and tail[0] not in ".!?؟,،;؛:\n" and not _COMPILED["tail"][language].match(tail):
                continue
            state = "ambiguous_pause" if kind == "ambiguous" else "explicit_pause"
            if kind == "explicit" and re.split(r"[.\n]", value, maxsplit=1)[0].rstrip().endswith(("?", "؟")):
                state = "ambiguous_pause"
            found = {"version": "pause-wording-v2", "state": state, "language": language}
            break
        if found:
            break
    if found:
        for offer in _RETURN.finditer(value):
            start = max((value.rfind(mark, 0, offer.start()) for mark in ".!?؟;؛,،\n"), default=-1) + 1
            if not _UNCERTAIN_RETURN.search(value[start:offer.start()]) and not _NEGATED_OFFER_PREFIX.search(value[start:offer.start()]):
                found["state"] = "return_offer"
                break
    return found


_PAUSE_ROWS = {
    "en": [
        ("Okay, take the pause you need.", "In this fictional option, your request for a pause is acknowledged.", "The conversation can stay paused without setting a return time.", "I still need that pause."),
        ("Okay.", "This brief fictional reply does not establish how the other person feels.", "You can leave the conversation paused without promising a return time.", "I still need the pause I asked for."),
        ("I want to keep talking now.", "This fictional response pushes against your request for a pause.", "You can restate the pause without promising further contact.", "I am taking a pause. I am not ready to continue."),
    ],
    "es": [
        ("De acuerdo, toma la pausa que necesitas.", "En esta opción ficticia, se reconoce tu petición de pausa.", "La conversación puede seguir en pausa sin fijar una hora para retomarla.", "Sigo necesitando esa pausa."),
        ("De acuerdo.", "Esta respuesta breve y ficticia no indica lo que siente la otra persona.", "Puedes mantener la pausa sin prometer cuándo volverás a hablar.", "Sigo necesitando la pausa que pedí."),
        ("Quiero seguir hablando ahora.", "Esta respuesta ficticia intenta continuar pese a tu petición de pausa.", "Puedes reiterar tu petición sin prometer otro contacto.", "Estoy tomando una pausa. No voy a continuar la conversación ahora."),
    ],
    "ar": [
        ("حسنًا، لا بأس بهذه الاستراحة.", "في هذا الاحتمال الخيالي، يتم تقبّل طلب الاستراحة.", "يمكن أن يبقى الحديث متوقفًا دون تحديد موعد لاستئنافه.", "ما زلت بحاجة إلى هذه الاستراحة."),
        ("حسنًا.", "هذا الرد الخيالي القصير لا يوضح شعور الطرف الآخر.", "يمكن الإبقاء على الاستراحة دون الوعد بموعد للعودة إلى الحديث.", "ما زلت بحاجة إلى الاستراحة التي طلبتها."),
        ("أريد أن نواصل الحديث الآن.", "في هذا الاحتمال الخيالي، يوجد ضغط لمواصلة الحديث رغم طلب الاستراحة.", "يمكن إعادة توضيح طلب الاستراحة دون الوعد بتواصل لاحق.", "أحتاج إلى هذه الاستراحة، ولا أرغب في مواصلة الحديث الآن."),
    ],
    "fr": [
        ("D'accord, prends la pause dont tu as besoin.", "Dans cette option fictive, ta demande de pause est reconnue.", "La conversation peut rester en pause sans fixer une heure de reprise.", "J'ai toujours besoin de cette pause."),
        ("D'accord.", "Cette courte réponse fictive ne permet pas de connaître les sentiments de l'autre personne.", "Tu peux maintenir la pause sans promettre de date de reprise.", "Je maintiens ma demande de pause."),
        ("Je veux continuer à parler maintenant.", "Cette réponse fictive insiste pour continuer malgré ta demande de pause.", "Tu peux répéter ta demande sans promettre un autre contact.", "J'ai besoin de cette pause et je ne reprends pas la conversation maintenant."),
    ],
    "de": [
        ("Okay, nimm dir die Pause, die du brauchst.", "In dieser fiktiven Möglichkeit wird deine Bitte um eine Pause anerkannt.", "Das Gespräch kann pausieren, ohne einen Zeitpunkt für die Fortsetzung festzulegen.", "Ich brauche diese Pause weiterhin."),
        ("Okay.", "Diese kurze fiktive Antwort belegt nicht, wie sich die andere Person fühlt.", "Du kannst bei der Pause bleiben, ohne eine Rückkehrzeit zu versprechen.", "Ich halte an meiner Bitte um eine Pause fest."),
        ("Ich möchte jetzt weiterreden.", "Diese fiktive Antwort drängt trotz deiner Bitte auf eine Fortsetzung.", "Du kannst die Pause bekräftigen, ohne weiteren Kontakt zu versprechen.", "Ich mache eine Pause und setze das Gespräch jetzt nicht fort."),
    ],
    "pt": [
        ("Tudo bem, faça a pausa de que precisa.", "Nesta opção fictícia, o seu pedido de pausa é reconhecido.", "A conversa pode continuar em pausa sem marcar uma hora para retomá-la.", "Ainda preciso desta pausa."),
        ("Tudo bem.", "Esta resposta breve e fictícia não revela o que a outra pessoa sente.", "Você pode manter a pausa sem prometer quando voltará a conversar.", "Ainda preciso da pausa que pedi."),
        ("Quero continuar a conversar agora.", "Esta resposta fictícia tenta continuar apesar do seu pedido de pausa.", "Você pode reafirmar a pausa sem prometer outro contato.", "Estou fazendo uma pausa. Não vou continuar a conversa agora."),
    ],
}
_CLARIFY_ROWS = {
    "en": [
        ("Can you clarify what you mean?", "This fictional branch asks for clarification rather than assuming a pause has started.", "You can clarify your meaning without deciding a return time.", "Let me clarify what I mean before choosing the next step."),
        ("I'm not sure what you mean.", "This fictional reply leaves the meaning open; it does not establish the other person's feelings.", "Explaining your request can come before any decision about continuing.", "I haven't specified exactly what I mean yet."),
        ("What are you asking for?", "This fictional branch asks for a clearer request, not proof of readiness or agreement.", "You can make your meaning clear without promising further contact.", "Let's clarify my meaning before assuming what happens next."),
    ],
    "es": [
        ("¿Puedes aclarar lo que quieres decir?", "Esta opción ficticia pide una aclaración sin dar por iniciada una pausa.", "Puedes aclarar tu intención sin decidir cuándo retomar la conversación.", "Quiero aclarar lo que quiero decir antes de elegir el siguiente paso."),
        ("No sé exactamente qué quieres decir.", "Esta respuesta ficticia deja el significado abierto y no demuestra lo que siente la otra persona.", "Puedes explicar tu petición antes de decidir si continuar.", "Todavía no he especificado exactamente lo que quiero decir."),
        ("¿Qué estás pidiendo?", "Esta opción ficticia pide una petición más clara, sin asumir disposición ni acuerdo.", "Puedes aclarar tu intención sin prometer otro contacto.", "Aclaremos lo que quiero decir antes de suponer qué pasará después."),
    ],
    "ar": [
        ("هل يمكن توضيح المقصود؟", "هذا الاحتمال الخيالي يطلب توضيحًا ولا يفترض أن الاستراحة بدأت.", "يمكن توضيح المقصود دون تحديد موعد للعودة إلى الحديث.", "أود توضيح ما أقصده قبل اختيار الخطوة التالية."),
        ("المقصود ليس واضحًا لي.", "هذا الرد الخيالي لا يحسم المقصود ولا يثبت شعور الطرف الآخر.", "يمكن شرح الطلب قبل اتخاذ قرار بشأن الاستمرار.", "لم أحدد بدقة ما أقصده بعد."),
        ("ما الطلب تحديدًا؟", "هذا الاحتمال الخيالي يطلب توضيح الطلب ولا يفترض الاستعداد أو الموافقة.", "يمكن توضيح المقصود دون الوعد بتواصل لاحق.", "لنوضح ما أقصده بدلًا من افتراض ما سيحدث بعد ذلك."),
    ],
    "fr": [
        ("Peux-tu préciser ce que tu veux dire ?", "Cette option fictive demande une précision sans supposer qu'une pause a commencé.", "Tu peux préciser ton intention sans fixer une heure de reprise.", "Je veux préciser ce que je veux dire avant de choisir la suite."),
        ("Je ne sais pas exactement ce que tu veux dire.", "Cette réponse fictive laisse le sens ouvert et ne prouve pas les sentiments de l'autre personne.", "Tu peux expliquer ta demande avant de décider de continuer.", "Je n'ai pas encore précisé exactement ce que je veux dire."),
        ("Que demandes-tu exactement ?", "Cette option fictive demande une précision sans supposer une disponibilité ou un accord.", "Tu peux préciser ton intention sans promettre un autre contact.", "Précisons ce que je veux dire avant de supposer la suite."),
    ],
    "de": [
        ("Kannst du genauer sagen, was du meinst?", "Diese fiktive Möglichkeit fragt nach, ohne anzunehmen, dass eine Pause begonnen hat.", "Du kannst deine Bedeutung klären, ohne eine Rückkehrzeit festzulegen.", "Ich möchte erst klären, was ich meine, bevor ich den nächsten Schritt wähle."),
        ("Ich weiß nicht genau, was du meinst.", "Diese fiktive Antwort lässt die Bedeutung offen und belegt keine Gefühle der anderen Person.", "Du kannst deinen Wunsch erklären, bevor du über eine Fortsetzung entscheidest.", "Ich habe noch nicht genau gesagt, was ich meine."),
        ("Worum bittest du genau?", "Diese fiktive Möglichkeit bittet um Klarheit und setzt keine Bereitschaft oder Zustimmung voraus.", "Du kannst deine Bedeutung klären, ohne weiteren Kontakt zu versprechen.", "Klären wir erst meine Bedeutung, statt die nächsten Schritte anzunehmen."),
    ],
    "pt": [
        ("Pode esclarecer o que quer dizer?", "Esta opção fictícia pede um esclarecimento sem presumir que uma pausa começou.", "Você pode esclarecer sua intenção sem definir uma hora para retomar a conversa.", "Quero esclarecer o que quero dizer antes de escolher o próximo passo."),
        ("Não sei exatamente o que quer dizer.", "Esta resposta fictícia deixa o significado em aberto e não comprova os sentimentos da outra pessoa.", "Você pode explicar o pedido antes de decidir se continua.", "Ainda não especifiquei exatamente o que quero dizer."),
        ("O que está pedindo exatamente?", "Esta opção fictícia pede clareza sem presumir disponibilidade ou acordo.", "Você pode esclarecer sua intenção sem prometer outro contato.", "Vamos esclarecer o que quero dizer antes de presumir o que acontece depois."),
    ],
}


_RETURN_ROWS = {
    "en": [
        ("Okay, I hear your request.", "In this fictional branch, the other person acknowledges the member's words; this is not evidence of agreement.", "The return offer belongs to the member. It does not establish the other person's plans or that the pause has elapsed."),
        ("Okay.", "This brief fictional acknowledgment does not establish the other person's feelings or availability.", "The member's exact wording, including any condition, remains unchanged; no shared return plan is assumed."),
        ("Can we continue now?", "This fictional request presses to continue; it does not show the real person's wishes or readiness.", "The member can retain their exact pause and offer without adding agreement, a new promise or a different kind of contact."),
    ],
    "es": [
        ("De acuerdo, entiendo tu petición.", "En esta opción ficticia, la otra persona reconoce las palabras de quien pide la pausa; esto no demuestra un acuerdo.", "La propuesta de retomar el contacto pertenece a quien pide la pausa. No confirma los planes de la otra persona ni que la pausa haya terminado."),
        ("De acuerdo.", "Este breve reconocimiento ficticio no demuestra los sentimientos ni la disponibilidad de la otra persona.", "Se conservan las palabras exactas de quien pide la pausa, incluida cualquier condición, sin suponer un plan acordado."),
        ("¿Podemos continuar ahora?", "Esta petición ficticia presiona para continuar; no demuestra lo que la persona real quiere ni si está preparada.", "Se pueden mantener la pausa y la propuesta exactas sin añadir un acuerdo, otra promesa ni una forma diferente de contacto."),
    ],
    "ar": [
        ("حسنًا، فهمت طلبك.", "في هذا الاحتمال الخيالي، يتم الإقرار بكلمات صاحب طلب الاستراحة؛ وهذا ليس دليلًا على الموافقة.", "عرض العودة للتواصل يخص صاحب طلب الاستراحة. ولا يثبت خطط الطرف الآخر أو أن الاستراحة قد انتهت."),
        ("حسنًا.", "هذا الإقرار الخيالي القصير لا يثبت مشاعر الطرف الآخر أو تفرغه.", "تبقى الكلمات الأصلية لصاحب الطلب، بما فيها أي شرط، دون افتراض خطة متفق عليها."),
        ("هل يمكننا المتابعة الآن؟", "هذا الطلب الخيالي يضغط للاستمرار؛ ولا يثبت رغبة الشخص الحقيقي أو استعداده.", "يمكن الحفاظ على الاستراحة والعرض بالكلمات الأصلية دون إضافة موافقة أو وعد جديد أو وسيلة تواصل مختلفة."),
    ],
    "fr": [
        ("D'accord, j'entends ta demande.", "Dans cette option fictive, l'autre personne reconnaît les mots de la personne qui demande la pause ; cela ne prouve pas un accord.", "L'offre de reprendre contact appartient à la personne qui demande la pause. Elle ne confirme ni les projets de l'autre personne ni la fin de la pause."),
        ("D'accord.", "Ce bref accusé de réception fictif ne prouve ni les sentiments ni la disponibilité de l'autre personne.", "Les mots exacts de la personne qui demande la pause, y compris toute condition, restent inchangés, sans supposer un plan commun."),
        ("Peut-on continuer maintenant ?", "Cette demande fictive insiste pour continuer ; elle ne prouve ni les souhaits ni la disponibilité de la personne réelle.", "La pause et l'offre peuvent garder leurs mots exacts, sans ajouter un accord, une nouvelle promesse ou un autre moyen de contact."),
    ],
    "de": [
        ("Okay, ich habe deine Bitte verstanden.", "In dieser fiktiven Möglichkeit werden die Worte der Person anerkannt, die um die Pause bittet; das belegt keine Zustimmung.", "Das Angebot für späteren Kontakt stammt von der Person, die um die Pause bittet. Es belegt weder Pläne der anderen Person noch das Ende der Pause."),
        ("Okay.", "Diese kurze fiktive Bestätigung belegt weder Gefühle noch Verfügbarkeit der anderen Person.", "Der genaue Wortlaut samt Bedingungen bleibt erhalten; ein gemeinsamer Plan wird nicht angenommen."),
        ("Können wir jetzt weitermachen?", "Diese fiktive Bitte drängt auf eine Fortsetzung; sie belegt keine Wünsche oder Bereitschaft der wirklichen Person.", "Die genaue Pause und das Angebot können bestehen bleiben, ohne Zustimmung, ein neues Versprechen oder eine andere Kontaktart hinzuzufügen."),
    ],
    "pt": [
        ("Tudo bem, entendo o seu pedido.", "Nesta opção fictícia, a outra pessoa reconhece as palavras de quem pede a pausa; isso não comprova um acordo.", "A oferta de retomar o contato pertence a quem pede a pausa. Não confirma os planos da outra pessoa nem que a pausa terminou."),
        ("Tudo bem.", "Este breve reconhecimento fictício não comprova os sentimentos nem a disponibilidade da outra pessoa.", "As palavras exatas de quem pede a pausa, incluindo qualquer condição, permanecem sem presumir um plano combinado."),
        ("Podemos continuar agora?", "Este pedido fictício pressiona para continuar; não demonstra os desejos nem a disposição da pessoa real.", "A pausa e a oferta podem manter as palavras originais, sem acrescentar um acordo, outra promessa ou uma forma diferente de contato."),
    ],
}


def current_pause_options(messages):
    """Localized fictional options for a recognized pause or unclear request.

    Recognized return offers keep the latest member's exact words in every
    copyable suggestion. Fictional reactions do not establish agreement,
    readiness, elapsed time or a different promise. Unsupported language,
    negative/breakup/no-contact statements and another speaker keep normal flow.
    """
    inspected = inspect_pause(messages)
    if inspected is None:
        return None
    if inspected["state"] == "return_offer":
        words = messages[-1]["text"]
        return [{"reaction": row[0], "tone": tone, "meaning": row[1], "outcome": row[2], "suggested": words}
                for tone, row in zip(("warm", "neutral", "difficult"), _RETURN_ROWS[inspected["language"]])]
    rows = (_CLARIFY_ROWS if inspected["state"] == "ambiguous_pause" else _PAUSE_ROWS)[inspected["language"]]
    return [{"reaction": row[0], "tone": tone, "meaning": row[1], "outcome": row[2], "suggested": row[3]}
            for tone, row in zip(("warm", "neutral", "difficult"), rows)]


UNDECIDED = re.compile(
    r"\b(?:i|we) (?:have not|haven't|haven’t|had not|hadn't) (?:yet )?(?:estimated|worked out|decided|confirmed)\b"
    r".{0,55}\b(?:time|timing|date|deadline|how long|when|availability)\b"
    r"|\b(?:i|we) (?:do not know|don't know|don’t know|am unsure|are unsure|can't promise|cannot promise)\s+"
    r"(?:yet\s+)?(?:when|how long|(?:a |the |my |our )?(?:time|timing|date|deadline|availability))\b", re.I)
COMMITMENT = re.compile(
    r"\b(?:i['’]ll|i (?:will|can)|we['’]ll|we (?:will|can)|i['’]m (?:available|free)|i am (?:available|free))\b"
    r"[^.!?\n]{0,100}?\b(?P<time>today|tomorrow|tonight|this afternoon|this evening|next week|by (?:noon|lunch|close|end)|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}(?::\d\d)?\s*(?:am|pm))\b", re.I)


def respect_undecided_time(answer, member_words):
    if answer.get("contact_boundary") or not UNDECIDED.search(member_words):
        return
    stated = {m["time"].lower() for m in COMMITMENT.finditer(member_words)}

    def unsupported(words):
        return any(m["time"].lower() not in stated for m in COMMITMENT.finditer(str(words or "")))

    for action in answer.get("actions") or []:
        if isinstance(action, dict) and unsupported(action.get("say")):
            action["say"] = None
    if unsupported(answer.get("what_to_say")):
        alternatives = (a["say"] for a in answer.get("actions") or [] if isinstance(a, dict) and isinstance(a.get("say"), str) and a["say"].strip())
        answer["what_to_say"] = next(alternatives, "I need to check the timing before I commit to a date.")


WEEKDAY = r"(?:mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday|s)?|thu(?:rs?(?:day)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"
DAY = re.compile(rf"\b{WEEKDAY}\b", re.I)
DAY_LIST = rf"{WEEKDAY}\b(?:\s*(?:,\s*(?:or |and )?|/|or |and )\s*{WEEKDAY}\b)*"
AVAILABILITY = re.compile(
    rf"\b(?:(?:i['’]m|i am|we['’]re|we are) (?:free|available|around|up for) |"
    rf"(?:(?:i|we) can |(?:happy|able) to )(?:do|meet|make|work|come|cover|attend)\b"
    rf"(?:(?!\b(?:if|whether|but)\b)[^.!?;,\n]){{0,48}}? )"
    rf"(?:(?:on|either|both) )?(?P<days>{DAY_LIST})", re.I)
DAY_WORKS = re.compile(rf"\b(?P<days>{DAY_LIST}) (?:works for (?:me|us)|is (?:fine|good) for (?:me|us))\b", re.I)
OFFERED = re.compile(rf"\b(?:how about|i prefer) (?P<days>{DAY_LIST})"
                     rf"|\b(?P<subject>{DAY_LIST}) works\b", re.I)
TIME_PLACEHOLDER = re.compile(r"\[[^]\n]{0,60}\b(?:days?|times?|dates?|availability|hours?)\b[^]\n]{0,40}\]", re.I)


def respect_stated_days(answer, member_words):
    if answer.get("contact_boundary"):
        return
    days = set()
    for match in [*AVAILABILITY.finditer(member_words), *DAY_WORKS.finditer(member_words), *OFFERED.finditer(member_words)]:
        offered = next(group for group in match.groups() if group)
        days.update(day[0][:3].lower() for day in DAY.finditer(offered))

    def grounded(words):
        words = str(words or "")
        if TIME_PLACEHOLDER.search(words):
            return False
        for match in [*AVAILABILITY.finditer(words), *DAY_WORKS.finditer(words)]:
            if any(day[0][:3].lower() not in days for day in DAY.finditer(match["days"])):
                return False
        return True

    for action in answer.get("actions") or []:
        if isinstance(action, dict) and not grounded(action.get("say")):
            action["say"] = None
    if not grounded(answer.get("what_to_say")):
        alternatives = (a["say"] for a in answer.get("actions") or []
                        if isinstance(a, dict) and isinstance(a.get("say"), str) and a["say"].strip())
        answer["what_to_say"] = next(alternatives, "What time would work for you?")


ASKS_HOURS = re.compile(
    r"(?:(?:how (?:can|should|do) i|help me) )?(?:confirm|clarify|find out|ask (?:about|for)) "
    r"(?:the |their )?(?:(?:shift|meeting|start|end|exact) )?(?:times?|hours?)[?! .]*", re.I)


def clarify_invitation_time(answer, lines, question):
    """Asking for the time of an invitation leaves acceptance to the member."""
    if answer.get("contact_boundary") or not ASKS_HOURS.fullmatch((question or "").strip()):
        return
    if not lines or any(line["speaker"] == "me" for line in lines):
        return
    invitation = lines[-1]["text"]
    if not DAY.search(invitation) or not re.search(r"\b(?:can|could|want to|would|free to|available to)\b", invitation, re.I):
        return
    words = ("What time does the shift start and finish?" if "shift" in question.lower()
             else "What time did you have in mind?")
    answer["best_move"] = "Ask for the timing first. Once you know it, decide whether you can make it."
    answer["what_to_say"] = words
    answer["actions"] = [{"label": "Ask for the time", "detail": "Check the timing before you decide.", "say": words}]
