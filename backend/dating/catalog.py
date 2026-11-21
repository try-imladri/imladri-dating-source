"""Curated original Imladri wording plus new dating basics, never old answers."""
from ..quiz import SHORT
from ..intimacy_catalog import questions as intimacy_questions
VERSION = "dating-preferences-v1"
ENROLLMENT_VERSION = "dating-enrollment-v1"
NOTICE = (
    "Join Imladri Dating as an adult. Only a new dating profile you preview, submit and publish "
    "can appear to eligible signed-in dating members after review. Existing Personal, People, "
    "Couples, research, screenshots, memories and Your Type answers stay private and are not "
    "copied into dating. Optional preference use and sharing with one match are separate choices. "
    "You can pause discovery or leave dating and manage your data without a subscription."
)
GENDERS = ["woman", "man", "nonbinary", "another"]
INTENTS = ["long_term", "life_partner", "figuring_out"]
STRUCTURES = ["monogamous", "nonmonogamous", "open_to_discuss"]
PERSONALITY = [
    {"id": "communication", "label": "How do you prefer to stay in touch?", "options": ["Daily messages", "A few thoughtful check-ins", "Calls and time together", "Flexible"]},
    {"id": "plans", "label": "How do you like to make plans?", "options": ["In advance", "A mix of planning and spontaneity", "Spontaneously"]},
    {"id": "repair", "label": "What helps during a disagreement?", "options": ["Talk it through calmly", "A pause with a time to return", "Time to write my thoughts"]},
    {"id": "social", "label": "What does a good social weekend look like?", "options": ["Mostly quiet time", "A balance", "Often out with people"]},
    {"id": "family_plans", "label": "What are your hopes about children?", "options": ["Want children", "Do not want children", "Already a parent; open to discussing", "Not sure"]},
    {"id": "pace", "label": "What dating pace works for you?", "options": ["Slow and steady", "Regular dates to build connection", "Open to discussing"]},
]
INTIMACY = [
    {"id": "closeness", "label": "What helps you build closeness?", "options": ["Emotional trust first", "Time together and affection", "Open conversation", "Still exploring"]},
    {"id": "boundaries", "label": "How do you want to discuss boundaries?", "options": ["Directly and early", "Gradually as trust grows", "Ask and check in each time"]},
    {"id": "affection", "label": "How do you prefer to express affection?", "options": ["Words", "Time together", "Physical affection with consent", "A mix"]},
]
for question in PERSONALITY + INTIMACY:
    question["source"] = {"catalog": "dating-core", "id": question["id"], "version": VERSION}

# Reuse actual published product assets with traceable IDs. Sensitive historical
# answers remain in their original purpose; members answer these questions anew.
for question in SHORT:
    if question["id"] in {"s1", "s4", "s9", "s12"}:
        PERSONALITY.append({"id": "quiz:" + question["id"], "label": question["q"],
                            "options": [option["label"] for option in question["options"]],
                            "source": {"catalog": "everyday-quiz", "id": question["id"], "version": 3}})
for section, topic in [("conversations", "Choosing a time to talk"), ("conversations", "Starting with a note"),
                       ("affection", "Being held"), ("affection", "Companionable silence")]:
    question, _ = intimacy_questions()[(section, topic)]
    INTIMACY.append({"id": "intimacy:" + section + ":" + topic.lower().replace(" ", "-"), "label": question,
                     "options": ["Interested", "Curious", "It depends", "Not for me", "Off limits"],
                     "source": {"catalog": "intimacy", "section": section, "topic": topic, "version": "seed-v1"}})
QUESTION_MAP = {"personality": {q["id"]: q for q in PERSONALITY}, "intimacy": {q["id"]: q for q in INTIMACY}}


def present():
    return {"version": VERSION, "personality": PERSONALITY, "intimacy": INTIMACY,
            "genders": GENDERS, "intents": INTENTS, "structures": STRUCTURES,
            "reuse_notice": "Selected questions come from your existing Imladri personality and intimacy catalogs. Answer afresh for Dating; existing answers are never copied automatically.",
            "notice": "These are self-reported preferences, not a diagnosis or prediction of chemistry. A shared preference is not consent."}
