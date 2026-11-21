"""Keep explicit contact boundaries intact across generated advice and its suggested actions."""
import re
from datetime import datetime, timedelta, timezone


INSTRUCTION = (
    "Check whether someone has explicitly asked the member to stop contact. Look at who said it, "
    "to whom, the channel, timing, and any later invitation. Silence, a late reply, a hypothetical, "
    "or the member's own fear is not such a request. A ban on one topic or channel can leave others open. A later invitation permits only the specific time, channel and subject offered. "
    "When outreach would disregard an active request, set boundary_evidence to an exact short excerpt of "
    "the supplied human words establishing it. Otherwise set it to null. Honor the request in every field: "
    "do not draft an acknowledgment, suggest waiting to try again, use a third party, or plan an encounter. "
    "Address threats or coercion by supporting safety and the member's choices; do not coach confrontation."
)

CONTACT = r"(?:contact|message|text|call|email|phone|write to|reach out to|talk to|speak to)"
ONGOING = r"(?:contacting|messaging|texting|calling|emailing|phoning|writing to|reaching out to|talking to|speaking to)"
# Deliberately narrow. Unparsed model excerpts cannot establish speaker or scope.
DIRECT = re.compile(
    rf"(?:^|[.!?;,]\s*)[\s\"']*(?:please[, ]+)?(?:(?:do not|don't|never) {CONTACT}(?: or {CONTACT})* me|"
    rf"stop {ONGOING} me|leave me alone|i (?:do not|don't) want you to {CONTACT} me|"
    r"i (?:do not|don't) want (?:any |further |more )?contact (?:from|with) you|"
    r"no (?:more |further )?contact(?: (?:from|with) you)?|"
    rf"i (?:want|need) (?:no (?:further )?contact|you to (?:leave me alone|stop {ONGOING} me)))"
    r"(?: (?:again|anymore|any more|at all|ever|please|for now|right now|tonight|today|this week))*\s*(?=$|[.!;,\n\"'])"
)
REPORTED = re.compile(
    rf"\b(?:asked|told) me (?:not to {CONTACT} (?:him|her|them)|to (?:stop {ONGOING} (?:him|her|them)|leave (?:him|her|them) alone))\b"
    rf"|\b(?:asked|told) (?:me|the member) to stop {ONGOING}(?: (?:him|her|them))?\s*(?=$|[.!;,])"
    r"|\b(?:asked for|requested|wants) no (?:further )?contact\b"
)
REPORTED_QUOTE = re.compile(r'\b(?:said|wrote|texted|told me|messaged me)(?:\s+to me)?\s*[:,]?\s*["\']\s*(.+)')
NOT_CURRENT = re.compile(
    r"\b(?:what if|hypothetically|suppose|imagined|pretend|for example|example of|used to|not sure|never (?:asked|said|told)|"
    r"did(?: not|n't) (?:ask|say|tell)|did .{0,32}(?:ask|say|tell)|if (?:they|he|she|you)|if .{1,20} (?:asked|told))\b"
    r"|\b(?:i (?:fear|worry|am afraid)|i'm afraid|i (?:want|wish|plan|intend) to (?:say|ask|tell)|"
    r"(?:an? )?(?:exercise|roleplay)|translate|quotation|quoting)\b"
)
ALTERNATIVE = re.compile(
    rf"\b(?:(?:but|instead|just|please|you can|you may) (?:you can |you may )?{CONTACT} me|"
    rf"{CONTACT} me (?:instead|during|after|before)|(?:email|text|call|message) (?:is|works) (?:fine|okay|ok|better))\b"
)
OTHER_RECIPIENT = re.compile(r"\b(?:told|asked|said to|texted|messaged) (?:my|her|his|their|our|someone|another|a friend)\b")
QUALIFIER = re.compile(r"\b(?:about|regarding|when|while|during|after|before|at work|at night|on (?:my|the|whatsapp|telegram|signal|instagram|facebook)|via|by (?:text|phone|email)|except|unless|only|that (?:nickname|name))\b")
INVITATION = re.compile(
    rf"^(?:you (?:can|may) {CONTACT} me (?:again(?: now)?|now)|(?:i changed my mind[, .]+)?(?:please )?"
    r"(?:text|call|message|contact) me (?:again(?: now)?|now)|i(?:'m| am) (?:ready|open) to (?:talk|reconnect)(?: now| again(?: now)?)?)\b"
)


LIMITED = re.compile(r"\b(?:today|tonight|tomorrow|until|for now|for (?:a|the|this|one|two|three|four|five|six|seven|[0-9]+)|right now|this (?:week|month|weekend|evening)|at work|during)\b")

# Full incoming lines only: these catalogs do not parse arbitrary reported speech,
# dialects, quotations, topic/channel conditions or future-time invitations.
LOCAL_CONTACT = {
    "es": {
        "requests": ("no me contactes", "no vuelvas a contactarme", "no me escribas", "no me escribas más", "no me llames",
                     "déjame en paz", "no quiero que me contactes", "no quiero que me escribas",
                     "no quiero tener contacto contigo", "deja de escribirme", "deja de llamarme"),
        "polite": ("por favor",), "temporal": ("hoy", "por ahora", "de momento"),
        "invitations": ("puedes contactarme ahora", "puedes volver a contactarme ahora",
                        "puedes volver a ponerte en contacto conmigo", "ya puedes contactarme"),
    },
    "ar": {
        "requests": ("لا تتواصل معي", "لا تتصل بي", "لا تراسلني", "لا تراسلني مرة أخرى", "توقف عن مراسلتي",
                     "اتركني وشأني", "لا أريد أن تتواصل معي"),
        "polite": ("من فضلك", "أرجوك", "رجاء"),
        "temporal": ("اليوم", "الآن", "في الوقت الحالي", "حاليًا", "حاليا"),
        "invitations": ("يمكنك التواصل معي الآن", "يمكنك التواصل معي مجددا الآن"),
    },
    "fr": {
        "requests": ("ne me contacte pas", "ne me contacte plus", "ne me contactez plus",
                     "ne m'écris plus", "ne m'appelle plus", "arrête de m'écrire",
                     "laisse-moi tranquille", "je ne veux plus de contact avec toi"),
        "polite": ("s'il te plaît", "s'il vous plaît"),
        "temporal": ("aujourd'hui", "pour le moment", "pour l'instant"),
        "invitations": ("tu peux me contacter maintenant", "vous pouvez me contacter maintenant",
                        "tu peux reprendre contact avec moi maintenant"),
    },
    "de": {
        "requests": ("kontaktiere mich nicht", "kontaktiere mich nicht mehr", "schreib mir nicht",
                     "schreib mir nicht mehr", "ruf mich nicht an", "lass mich in ruhe"),
        "polite": ("bitte",), "temporal": ("heute", "vorerst", "im moment"),
        "temporal_requests": ("kontaktiere mich heute nicht", "kontaktiere mich vorerst nicht",
                              "schreib mir heute nicht", "ruf mich heute nicht an", "lass mich vorerst in ruhe"),
        "invitations": ("du kannst mich jetzt kontaktieren", "du darfst mich jetzt wieder kontaktieren"),
    },
    "pt": {
        "requests": ("não me contacte", "não me contate", "não me contactes", "não me escreva",
                     "não me escrevas", "não me ligue", "deixa-me em paz", "me deixe em paz",
                     "não quero que me contate", "não entre mais em contato comigo"),
        "polite": ("por favor",), "temporal": ("hoje", "por enquanto", "por agora"),
        "invitations": ("pode me contatar agora", "podes contactar-me agora",
                        "você pode entrar em contato comigo agora"),
    },
}
# Reject these contexts before considering a model quote or an embedded English
# command. They establish neither a current command nor the requesting speaker.
UNVERIFIED_CONTEXT = re.compile(
    r"\b(?:y si|qué pasaría si|si|hipotéticamente|imagina|supongamos|por ejemplo|temo que|me preocupa|"
    r"tengo miedo|me asusta|no dije|no he dicho|nunca dije|no quiero que|quiero decir|le dije|les dije|le pedí|dijo|pidió|ejercicio|traduce|traducción|"
    r"et si|hypothétiquement|imagine|supposons|par exemple|je crains|j'ai peur|je n'ai pas dit|je n'ai jamais dit|"
    r"je lui ai dit|je lui ai demandé|a dit|a demandé|exercice|traduis|traduction|"
    r"was wäre wenn|wenn|hypothetisch|zum beispiel|angenommen|stell dir vor|ich habe angst|"
    r"ich befürchte|ich habe nicht gesagt|ich habe nie gesagt|ich sagte|ich bat|sagte|übung|übersetze|"
    r"e se|se|talvez|hipoteticamente|suponha|por exemplo|tenho medo|receio que|não disse|nunca disse|eu disse|eu pedi|disse|exercício|traduza|tradução|"
    r"sobre|acerca de|en whatsapp|hasta|después de|solo|solamente|à propos|au sujet|sur whatsapp|par téléphone|uniquement|seulement|só|somente|"
    r"über|auf whatsapp|per email|nur|no whatsapp|pelo whatsapp|até|apenas|"
    r"مثلا|مثال|افترض|تخيل|لو|إذا|ربما|أخاف|اخاف|أخشى|لم أقل|لم يقل|قلت له|قلت لها|طلبت منه|طلبت منها|قال|تمرين|ترجم|ترجمة|عن العمل|عبر|على واتساب|حتى|فقط)\b"
)
INVITATION_SCOPE = re.compile(r"\b(?:about|regarding|only|except|unless|until|after|before|on|via|through|during|when|while|tomorrow)\b")


def localized_request(text):
    """Recognize only a complete cataloged direct request; retain any time condition."""
    value = normalized(text).rstrip(".!。")
    if any(mark in value for mark in ("?", "؟", "¿", '"', "«", "»")):
        return None
    for language, rules in LOCAL_CONTACT.items():
        if value in rules.get("temporal_requests", ()) or any(
                value == polite + " " + request for polite in rules["polite"]
                for request in rules.get("temporal_requests", ())):
            return language, True
        for request in rules["requests"]:
            for temporal in ("", *rules["temporal"]):
                cores = {request + (" " + temporal if temporal else "")}
                if temporal:
                    cores.add(temporal + ", " + request)
                    cores.add(temporal + "، " + request)
                forms = set(cores)
                for core in cores:
                    for polite in rules["polite"]:
                        forms.update((polite + " " + core, polite + ", " + core, polite + "، " + core,
                                      core + ", " + polite, core + " " + polite))
                if value in forms:
                    return language, bool(temporal)
    return None


def localized_invitation(text):
    value = normalized(text).rstrip(".!。")
    return any(value in rules["invitations"] for rules in LOCAL_CONTACT.values())


def ongoing(evidence):
    local = localized_request(evidence)
    if local:
        return not local[1]
    return not LIMITED.search(evidence)


def still_applies(evidence, observed_at, now=None):
    local = localized_request(evidence)
    if local and local[1]:
        # "Today" and "for now" are scoped, not perpetual. These languages do not
        # supply a verified time zone or an elapsed-time parser; never invent expiry.
        return True
    if ongoing(evidence) or not observed_at:
        return True
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        elapsed = (now or datetime.now(timezone.utc)) - observed
    except (AttributeError, ValueError, TypeError):
        return True
    duration = re.search(r"\bfor (\d+|one|two|three|four|five|six|seven|a) (hours?|days?|weeks?)\b", evidence)
    if duration:
        numbers = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
        n, unit = duration.groups()
        count = int(n) if n.isdigit() else numbers[n]
        return elapsed.total_seconds() < count * {"hour": 3600, "day": 86400, "week": 604800}[unit.rstrip("s")]
    # Calendar names do not supply the member's time zone. Only drop these when the window has clearly passed.
    for phrase, days in ((r"\b(today|tonight|this evening)\b", 2), (r"\btomorrow\b", 3),
                         (r"\bthis (week|weekend)\b", 9), (r"\bthis month\b", 33)):
        if re.search(phrase, evidence):
            return elapsed < timedelta(days=days)
    return True


def normalized(text):
    return " ".join(str(text or "").lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').split())


def evidence_from(text, *, incoming=False):
    """Return the human source only for a direct command or a reported request aimed at the member."""
    value = normalized(text)
    if incoming and localized_request(value):
        return value[:320]
    if UNVERIFIED_CONTEXT.search(value):
        return None
    if not value or ALTERNATIVE.search(value):
        return None
    for clause in re.split(r"(?<=[.!?])['\x22]?\s+", value):
        if NOT_CURRENT.search(clause) or OTHER_RECIPIENT.search(clause) or QUALIFIER.search(clause):
            continue
        if incoming:
            match = DIRECT.search(clause)
        else:
            match = REPORTED.search(clause)
            if match and (clause.startswith(("i ", "we ")) or re.search(r"\b(?:if|whether|might|could|would|may|unsure)\b", clause[:match.start()]) or clause.endswith("?")):
                match = None
            if not match and not clause.startswith(("i ", "we ")):
                quoted = REPORTED_QUOTE.search(clause)
                match = DIRECT.search(quoted.group(1)) if quoted else None
        if match:
            return clause.strip().strip(chr(34)).strip(chr(39))[:320]
    return None


def invitation(text):
    value = normalized(text)
    if localized_invitation(value):
        return True
    if UNVERIFIED_CONTEXT.search(value):
        return False
    clauses = re.split(r"(?<=[.!?])['\x22]?\s+", value)
    if INVITATION_SCOPE.search(value):
        return False
    for index, clause in enumerate(clauses):
        # Require a complete current invitation, not a prefix followed by unknown
        # conditions in another language. The existing English follow-up-question
        # path retains only the explicitly requested question as its reply scope.
        if INVITATION.fullmatch(clause.rstrip(".!\"'")):
            return all(tail.endswith("?") for tail in clauses[index + 1:])
    return False


def reopened_question(lines):
    """Use the partner's own question to keep a newly permitted reply within its stated scope."""
    for index in range(len(lines) - 1, -1, -1):
        item = lines[index]
        if item.get("speaker") == "them" and invitation(item.get("text")):
            questions = re.findall(r"(?:^|[.!?])\s*([^.!?]+\?)", str(item.get("text") or ""))
            if questions:
                earlier_evidence, _ = turn_evidence(lines[:index], "")
                return questions[-1][:240].strip(), earlier_evidence
            break
    return None, None


def limited_read(who, question):
    return {
        "limited_reply": True, "contact_boundary": False, "no_new_evidence": False,
        "what_they_are_doing": f"{who} invited a reply and asked: {question}",
        "what_it_means": "There is permission to answer this question in the channel they offered.",
        "confidence": "high", "best_move": f"If you want to reply, answer only their question: {question} Keep the answer brief and factual.",
        "what_to_say": None, "what_not_to_do": "Leave any earlier disagreement or wider conversation for them to raise.",
        "actions": [], "seen": [], "explanations": [], "unknowns": [], "proposed_memories": [],
    }


def turn_evidence(lines, question, model_quote=None):
    """A later explicit invitation supersedes an earlier request in the supplied conversation."""
    for line in reversed(lines):
        if line.get("speaker") != "them":
            continue
        value = normalized(line.get("text"))
        if invitation(value):
            return None, True
        direct = evidence_from(value, incoming=True)
        if direct:
            return direct, False
    member_account = normalized(question)
    reopening = REPORTED_QUOTE.search(member_account)
    if reopening and not member_account.startswith(("i ", "we ")) and not NOT_CURRENT.search(member_account) and invitation(reopening.group(1)):
        return None, True
    reported = evidence_from(question)
    if reported:
        return reported, False
    # An excerpt cannot prove speaker, addressee, negation or scope. Keep the model
    # argument for call compatibility, but require the human request itself to pass
    # the direct/reported parser above; unparsed model classifications stay unverified.
    return None, False


def group_target(question, evidence, names):
    """Resolve a reported boundary to this group; an outsider's request isn't a group-wide one."""
    if not evidence:
        return None
    value = normalized(question)
    candidates = {normalized(name): name for name in names}
    speakers = re.findall(r"\b([\w'-]+) (?:said|texted|wrote|told|asked|requested|wants|messaged)\b", normalized(evidence))
    speaker = speakers[-1] if speakers else None
    if speaker in candidates:
        return candidates[speaker] if relevant_to_group(value, candidates[speaker], names) else None
    if speaker and speaker not in ("he", "she", "they", "partner", "ex"):
        return None
    return "the person who made the request"


def relevant_to_group(question, who, names):
    value = normalized(question)
    # In a two-part question the member can report Alex's boundary and separately ask about Jamie.
    questions = re.findall(r"(?:^|[.!?])\s*([^.!?]+)\?", value)
    subject = questions[-1] if questions else value
    mentioned = [name for name in names if re.search(r"(?<!\w)" + re.escape(normalized(name)) + r"(?!\w)", subject)]
    if not mentioned or who in mentioned:
        return True
    # Naming only an intermediary does not make a proposed relay independent of the original request.
    return bool(re.search(r"\b(pass .{0,50}(?:along|on|message|explanation)|forward|relay|get (?:an? )?updates?|"
                          r"(?:get|ask) .{1,45} to (?:reach out|check on|tell|contact|message)|on my behalf)\b", subject))


def read(who, evidence):
    next_step = f"Respect the time and channels {who} specified. If you want to explain yourself, write it down and keep it unsent."
    if not ongoing(evidence):
        next_step = f"Respect the time or condition {who} gave. If the window is still open or its end is unclear, keep your explanation in an unsent note."
    return {
        "contact_boundary": True, "boundary_evidence": evidence,
        "no_new_evidence": False, "what_they_are_doing": f"You described a request from {who} to stop contact.",
        "what_it_means": "Their request tells you what to do now. It does not tell you why they made it.",
        "confidence": "high", "best_move": next_step,
        "what_to_say": None, "what_not_to_do": "Skip the final acknowledgment. Avoid reaching out through a friend or another account.",
        "actions": [{"label": "Write privately", "detail": "Keep your thoughts in a private note for yourself.", "say": None}], "seen": [], "explanations": [], "unknowns": [], "proposed_memories": [],
    }


def group_read(who, evidence, names):
    response = read(who, evidence)
    others = [name for name in names if name != who]
    if who in names and len(others) == 1:
        friend = others[0]
        response["best_move"] = (f"If you talk with {friend}, keep the conversation about your own friendship. "
                                 f"Leave {who} out of it. Give them the time and space they requested.")
        response["what_not_to_do"] = f"Don't ask {friend} to pass on a message or give you updates about {who}."
        response["actions"] = [{"label": "Keep it separate", "detail": f"Think of something you and {friend} enjoy that is independent of {who}.", "say": None}]
    return response


def plan(who, evidence):
    return {
        "contact_boundary": True, "boundary_evidence": evidence, "opening_line": None, "exit_line": None,
        "reactions": [], "avoid": ["Skip a final message or an uninvited visit.", "Avoid asking someone else to relay a message."],
        "uncertainty": f"Give {who} the space they requested, within the time and channels they specified. You can write down what you wish you could say and keep it for yourself."
    }


def saved_plan(document):
    """Apply the same contact choice when a member reopens a plan saved by an earlier version."""
    if (document.get("plan") or {}).get("contact_boundary") is True:
        return document
    evidence = evidence_from(document.get("situation"))
    if evidence:
        return {**document, "plan": plan("the other person", evidence)}
    return document


def saved_read(document, who="the other person"):
    if (document.get("response") or {}).get("contact_boundary") is True:
        return document
    evidence, _ = turn_evidence(document.get("conversation") or [], document.get("question"))
    if evidence:
        return {**document, "response": read(who, evidence), "proposed_memories": []}
    return document


def saved_group(document, names):
    if (document.get("response") or {}).get("contact_boundary") is True:
        return document
    question = document.get("question")
    evidence = evidence_from(question)
    who = group_target(question, evidence, names)
    if who:
        return {**document, "response": group_read(who, evidence, names)}
    return document
