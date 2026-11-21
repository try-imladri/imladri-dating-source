"""Archetype + figure catalog, default settings, and the AI page-drafting job (python content.py)."""
import asyncio
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pymongo import UpdateOne

try:
    from . import figure_career_review
    from .prompt_library import FEATURE_PROMPTS, PREVIOUS_FEATURE_PROMPTS, PREVIOUS_QUALITY_FEATURE_PROMPTS
except ImportError:
    import figure_career_review
    from prompt_library import FEATURE_PROMPTS, PREVIOUS_FEATURE_PROMPTS, PREVIOUS_QUALITY_FEATURE_PROMPTS

# number, slug, name, one_liner, texting tag, red-flag tag
ARCHETYPES = [
    (1, "love-bomber", "The Love Bomber", "too much, too fast, then gone", "over-texter", "love bombing"),
    (2, "almost-boyfriend", "The Almost Boyfriend", "everything but the label", "hot & cold", "avoidance"),
    (3, "breadcrumber", "The Breadcrumber", "just enough to keep you hungry", "slow replier", "breadcrumbing"),
    (4, "situationship-ceo", "The Situationship CEO", "manages you like a side project", "late-night only", "inconsistency"),
    (5, "future-faker", "The Future Faker", "plans a life he'll never show up for", "over-texter", "love bombing"),
    (6, "nice-guy", "The Nice Guy", "kindness with an invoice attached", "double texter", "ego"),
    (7, "ghost", "The Ghost", "here, then nowhere", "slow replier", "avoidance"),
    (8, "zombie", "The Zombie", "back from the dead with 'hey stranger'", "late-night only", "breadcrumbing"),
    (9, "avoidant-poet", "The Avoidant Poet", "deep feelings, zero follow-through", "voice-note guy", "avoidance"),
    (10, "golden-retriever", "The Golden Retriever", "loyal, loud, and a little lost", "double texter", "inconsistency"),
    (11, "negotiator", "The Negotiator", "turns every boundary into a debate", "dry texter", "gaslighting"),
    (12, "machine", "The Machine", "treats every conversation like a bid for control", "dry texter", "ego"),
    (13, "thermostat", "The Thermostat", "warm on Tuesday, ice by Thursday", "hot & cold", "inconsistency"),
    (14, "therapy-speak", "The Therapy-Speak Guy", "weaponizes words he learned online", "over-texter", "gaslighting"),
    (15, "jealous-guardian", "The Jealous Guardian", "calls control protection", "double texter", "jealousy"),
    (16, "mamas-boy", "The Mama's Boy", "already has a woman in charge", "voice-note guy", "avoidance"),
    (17, "player", "The Player", "the rotation is the relationship", "late-night only", "secrecy"),
    (18, "wounded-bird", "The Wounded Bird", "his past is your full-time job", "over-texter", "inconsistency"),
    (19, "slow-burn", "The Slow Burn", "takes his time, means every word", "slow replier", "rare"),
    (20, "ex-archaeologist", "The Ex Archaeologist", "still digging through his last relationship", "voice-note guy", "secrecy"),
    (21, "calendar-guy", "The Calendar Guy", "you're penciled in, never inked", "slow replier", "inconsistency"),
    (22, "charmer", "The Charmer", "makes everyone feel chosen", "meme sender", "secrecy"),
    (23, "fixer", "The Fixer", "solves you instead of hearing you", "dry texter", "ego"),
    (24, "commitment-tourist", "The Commitment Tourist", "visits seriousness, never moves in", "hot & cold", "avoidance"),
    (25, "peacock", "The Peacock", "dates the audience, not you", "meme sender", "ego"),
    (26, "bench-coach", "The Bench Coach", "keeps you on the bench, just in case", "slow replier", "breadcrumbing"),
    (27, "debate-club", "The Debate Club Intellectual", "argues with your feelings", "dry texter", "ego"),
    (28, "locked-vault", "The Locked Vault", "you get the brochure, never the tour", "dry texter", "avoidance"),
    (29, "knight", "The Knight", "needs you to need saving", "over-texter", "jealousy"),
    (30, "man-child", "The Man-Child", "you're dating his mom's replacement", "meme sender", "inconsistency"),
    (31, "phone-down", "The Phone-Down Guy", "screen always facing the table", "slow replier", "secrecy"),
    (32, "first-love-echo", "The First Love Echo", "sells you the past to buy your future", "voice-note guy", "breadcrumbing"),
    (33, "scorekeeper", "The Scorekeeper", "keeps a ledger of every favor", "double texter", "gaslighting"),
    (34, "adrenaline-guy", "The Adrenaline Guy", "bored the moment it's calm", "late-night only", "inconsistency"),
    (35, "fog-machine", "The Fog Machine", "makes you doubt what you saw", "dry texter", "gaslighting"),
    (36, "sweet-talker", "The Sweet Talker", "all words, no receipts", "over-texter", "love bombing"),
    (37, "workaholic", "The Workaholic", "married to the grind", "slow replier", "avoidance"),
    (38, "sad-boy", "The Sad Boy", "melancholy as a personality", "voice-note guy", "breadcrumbing"),
    (39, "alpha-cosplayer", "The Alpha Cosplayer", "dominance as a costume", "dry texter", "ego"),
    (40, "open-book", "The Open Book", "says it, means it, does it", "double texter", "rare"),
    (41, "boomerang", "The Boomerang", "always leaves, always returns", "hot & cold", "breadcrumbing"),
    (42, "watcher", "The Watcher", "views every story, answers no texts", "slow replier", "breadcrumbing"),
    (43, "overthinker", "The Overthinker", "analysis paralysis in a hoodie", "double texter", "avoidance"),
    (44, "provider", "The Provider", "pays for everything, owns the narrative", "dry texter", "ego"),
    (45, "someday-guy", "The Someday Guy", "it'll be different when...", "voice-note guy", "inconsistency"),
    (46, "grown-man", "The Grown Man", "clear, consistent, and calm", "double texter", "rare"),
    (47, "blend", "The Blend", "every real guy is a mix", "hot & cold", "inconsistency"),
]

# Female roster: stored numbers 101+ (shown as #01, #02 ...) so every archetype number stays unique
FEMALE_ARCHETYPES = [
    (101, "cool-girl", "The Cool Girl", "never asks for anything, then quietly leaves", "dry texter", "avoidance"),
    (102, "pick-me", "The Pick-Me", "competes for attention she already has", "over-texter", "ego"),
    (103, "whirlwind", "The Whirlwind", "a storm of attention that blows itself out", "over-texter", "love bombing"),
    (104, "ice-queen", "The Ice Queen", "warmth is earned and rarely paid out", "slow replier", "avoidance"),
    (105, "hopeless-romantic", "The Hopeless Romantic", "falls for the idea before the person", "over-texter", "love bombing"),
    (106, "orbit-keeper", "The Orbit Keeper", "just enough attention to keep you circling", "slow replier", "breadcrumbing"),
    (107, "vanishing-act", "The Vanishing Act", "gone mid-conversation, no explanation", "slow replier", "avoidance"),
    (108, "comeback-girl", "The Comeback Girl", "returns every time she gets bored", "late-night only", "breadcrumbing"),
    (109, "renovator", "The Renovator", "dates your potential, not you", "double texter", "ego"),
    (110, "rescuer", "The Rescuer", "falls for whoever needs saving", "voice-noter", "inconsistency"),
    (111, "social-butterfly", "The Social Butterfly", "everyone's favourite, nobody's person", "meme sender", "secrecy"),
    (112, "content-creator", "The Content Creator", "the relationship is the content", "meme sender", "ego"),
    (113, "career-first", "The Career-First Woman", "her calendar always wins", "slow replier", "avoidance"),
    (114, "mean-girl", "The Mean Girl", "affection with a side of judgment", "dry texter", "ego"),
    (115, "rewriter", "The Rewriter", "remembers it differently every time", "dry texter", "gaslighting"),
    (116, "phone-checker", "The Phone Checker", "calls it love, checks your phone", "double texter", "jealousy"),
    (117, "daddys-girl", "The Daddy's Girl", "already has a man in charge", "voice-noter", "avoidance"),
    (118, "heartbreaker", "The Heartbreaker", "the chase is the whole point", "late-night only", "secrecy"),
    (119, "wounded-darling", "The Wounded Darling", "her past becomes your full-time job", "over-texter", "inconsistency"),
    (120, "steady-flame", "The Steady Flame", "slow to open, sure once she does", "slow replier", "rare"),
    (121, "ex-collector", "The Ex Collector", "still close with every ex", "voice-noter", "secrecy"),
    (122, "wedding-planner", "The Wedding Planner", "has the board pinned before the second date", "double texter", "love bombing"),
    (123, "free-spirit", "The Free Spirit", "allergic to labels and Tuesdays", "hot & cold", "avoidance"),
    (124, "upgrade-seeker", "The Upgrade Seeker", "always eyeing the next level up", "dry texter", "secrecy"),
    (125, "princess", "The Princess", "expects to be chosen, never chooses", "slow replier", "ego"),
    (126, "backup-planner", "The Backup Planner", "keeps you warm as plan B", "slow replier", "breadcrumbing"),
    (127, "devils-advocate", "The Devil's Advocate", "argues to win, not to understand", "dry texter", "gaslighting"),
    (128, "closed-book", "The Closed Book", "you get the trailer, never the movie", "dry texter", "avoidance"),
    (129, "damsel", "The Damsel", "plays helpless to keep you close", "over-texter", "inconsistency"),
    (130, "mother-hen", "The Mother Hen", "mothers you instead of meeting you", "voice-noter", "jealousy"),
    (131, "second-chat", "The Second Chat", "always has another conversation open", "slow replier", "secrecy"),
    (132, "first-love-loyalist", "The First-Love Loyalist", "still measures you against him", "voice-noter", "breadcrumbing"),
    (133, "ledger", "The Ledger", "remembers every favour you owe", "double texter", "gaslighting"),
    (134, "thrill-seeker", "The Thrill Seeker", "bored the second it's stable", "late-night only", "inconsistency"),
    (135, "drama-queen", "The Drama Queen", "a new crisis every week", "over-texter", "inconsistency"),
    (136, "sweetheart", "The Sweetheart", "kind, warm, and actually means it", "double texter", "rare"),
    (137, "hustler", "The Hustler", "you're a meeting she keeps moving", "slow replier", "avoidance"),
    (138, "sad-girl", "The Sad Girl", "melancholy as an aesthetic", "voice-noter", "breadcrumbing"),
    (139, "boss", "The Boss", "runs the relationship like her company", "dry texter", "ego"),
    (140, "open-heart", "The Open Heart", "says it, means it, does it", "double texter", "rare"),
    (141, "boomerang-girl", "The Boomerang Girl", "always leaves, always comes back", "hot & cold", "breadcrumbing"),
    (142, "story-viewer", "The Story Viewer", "watches everything, replies to nothing", "slow replier", "breadcrumbing"),
    (143, "group-chat-analyst", "The Group-Chat Analyst", "screenshots every text for a committee vote", "double texter", "avoidance"),
    (144, "martyr", "The Martyr", "gives everything, then sends the bill", "voice-noter", "gaslighting"),
    (145, "someday-girl", "The Someday Girl", "waiting for you to become who she imagined", "voice-noter", "inconsistency"),
    (146, "grown-woman", "The Grown Woman", "clear, consistent, and calm", "double texter", "rare"),
    (147, "tester", "The Tester", "keeps checking if you'll stay", "hot & cold", "gaslighting"),
    (148, "healing-era", "The Healing Era", "on a break from love, mid-relationship", "slow replier", "avoidance"),
    (149, "cosmic-girl", "The Cosmic Girl", "reads your chart before your character", "meme sender", "ego"),
    (150, "blend-her", "The Blend", "every real woman is a mix", "hot & cold", "inconsistency"),
]
SEED_DIR = os.path.join(os.path.dirname(__file__), "seed")
FEMALE_FILE = os.path.join(SEED_DIR, "female_archetypes.json")


def seed_json(name: str) -> dict:
    try:
        with open(os.path.join(SEED_DIR, name), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def female_sections() -> dict:
    return seed_json("female_archetypes.json")


# Different editions use the existing numbering convention and the same article format.
COMMUNICATION_ROSTER = seed_json("communication_roster.json")
COMMUNICATION_NUMBERS = {entry["row"][0] for entry in COMMUNICATION_ROSTER}
for entry in COMMUNICATION_ROSTER:
    row = tuple(entry["row"])
    (FEMALE_ARCHETYPES if entry["gender"] == "female" else ARCHETYPES).append(row)


def attach_example_sources(profile):
    """Bind each citation to its text; moving a card in Admin cannot misattribute it."""
    comm = profile["sections"]["communicates"]
    if "example_sources" in comm and any("example" not in ref for ref in comm["example_sources"]):
        comm["example_sources"] = [
            {**source, "example": source.get("example", example)}
            for example, source in zip(comm["examples"], comm["example_sources"], strict=True)
        ]
    return profile


def field_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# Each fingerprint covers the formerly shipped card text and its complete citation.
CAREER_CARD_REVISIONS = {
    "pablo-picasso": (0, "06f81e6b413482aaab96c80913d13aa31737af30f5ca9fced8ea6ebcfac48a16"),
    "dolly-parton": (1, "d3865d19b003ab19f61d5826176a78c417f8ba4dbb783c1881f6205a5e7b94bb"),
    "sukarno": (1, "e09d34b9086a246f3176ad3cc2a9c6d1d9e5435e3932151ce995b99130894898"),
    "dan-carter": (2, "1c50b7a96e4d079a6f05f79135b129f0305b0c9a44c756de26e1d596e48dedb4"),
    "therese-casgrain": (3, "a70aa9dae0cb389e5daeb494511839ccfdf384738165abd86a2540e2cb1d0160"),
    "matilde-hidalgo": (2, "4e2a1326ae82c2de889dfb0c2aff9fc104cef720b0ddf370fdd1f1de27556f5a"),
    "mary-lou-mcdonald": (3, "dcf28881732fe5e51c7fe2465c8e7d88179cb7a69e0037030a1e32e0604a0052"),
    "kersti-kaljulaid": (3, "05ee07717408763383681fc1dcd6418456f1a39585611187a0c95f1ff7c3bf68"),
}


async def revise_career_cards(collection, shipped):
    field = "sections.communicates."
    projection = {"slug": 1, field + "examples": 1, field + "example_sources": 1}
    async for row in collection.find({"slug": {"$in": list(CAREER_CARD_REVISIONS)}}, projection):
        pos, fingerprint = CAREER_CARD_REVISIONS[row["slug"]]
        comm = (row.get("sections") or {}).get("communicates") or {}
        refs = comm.get("example_sources") or []
        for index, text in enumerate(comm.get("examples") or []):
            related = [(i, ref) for i, ref in enumerate(refs) if isinstance(ref, dict) and ref.get("example") == text]
            # Multiple citations or a changed source indicate a staff edit to this example.
            if len(related) != 1:
                continue
            ref_index, ref = related[0]
            if field_digest([text, ref]) != fingerprint:
                continue
            target = shipped[row["slug"]]["sections"]["communicates"]
            text_key = field + f"examples.{index}"
            ref_key = field + f"example_sources.{ref_index}"
            await collection.update_one(
                {"_id": row["_id"], text_key: text, field + "example_sources": refs},
                {"$set": {text_key: target["examples"][pos], ref_key: target["example_sources"][pos]}},
            )
            break


    # Correct the same historical detail in the article only while its shipped wording survives.
    row = await collection.find_one({"slug": "sukarno"}, {"sections.pattern": 1})
    if row and "sukarno" in shipped:
        for index, text in enumerate((row.get("sections") or {}).get("pattern") or []):
            if field_digest(text) == "1b10ea0fb6c0307ae19091d7ffa03c12b41e7003110973b1d1d6d69fe7ef864e":
                key = f"sections.pattern.{index}"
                await collection.update_one(
                    {"_id": row["_id"], key: text},
                    {"$set": {key: shipped["sukarno"]["sections"]["pattern"][3]}},
                )
                break


async def revise_rawya_profile(collection, profile):
    """Correct the published article wherever the original wording and source still match."""
    row = await collection.find_one({"slug": "rawya-ateya"})
    if not row:
        return
    sections = profile["sections"]
    target = {**profile, "identity": sections["identity"], "matches": sections["matches"]}
    baseline = seed_json("figure_rawya_revision.json")
    writes = []

    def at(document, path):
        for key in path.split("."):
            document = document.get(key) if isinstance(document, dict) else None
        return document

    def replace(original, revised):
        writes.append(UpdateOne({"_id": row["_id"], **original}, {"$set": revised}))

    # The directory and article copies of a headline or lead must agree.
    for group in baseline["groups"]:
        paths = group["paths"]
        original = {path: at(row, path) for path in paths}
        if field_digest(list(original.values())) == group["digest"]:
            replace(original, {path: at(target, path) for path in paths})

    for path, fingerprints in baseline["items"].items():
        current = at(row, path)
        if isinstance(current, list):
            for index, item in enumerate(current):
                pos = fingerprints.get(field_digest(item))
                if pos is not None:
                    key = f"{path}.{index}"
                    replace({key: item}, {key: at(target, path)[pos]})

    comm = at(row, "sections.communicates") or {}
    revised = sections["communicates"]
    if isinstance(comm, dict):
        for index, pair in enumerate(zip(comm.get("examples") or [], comm.get("example_sources") or [])):
            pos = baseline["cards"].get(field_digest(pair))
            if pos is not None:
                text_key = f"sections.communicates.examples.{index}"
                ref_key = f"sections.communicates.example_sources.{index}"
                replace({text_key: pair[0], ref_key: pair[1]},
                        {text_key: revised["examples"][pos], ref_key: revised["example_sources"][pos]})

    if writes:
        # Preserve custom source labels and ordering, including during parallel startup.
        memoir = profile["sections"]["map"]["basis"]["sources"][0]
        writes.append(UpdateOne({"_id": row["_id"], "sources.url": {"$ne": memoir["url"]}},
                                {"$addToSet": {"sources": memoir}}))
        await collection.bulk_write(writes, ordered=False)


# The starting article and its four example bindings for the seven reviewed leads.
# A fresh install already receives the new maps. On an existing install, change
# only the map that was reviewed, including when a parallel editor saves first.
REVIEWED_LEAD_BASELINES = {
    "alicia-moreau-de-justo": "7235fc00b4a8e2798fe68e470fe29b6f4e1ad076586f0d6f5dc742f9cf9b57f9",
    "annalena-baerbock": "6b3b4f30506252a8c18288903b687d6d577904c7b37a20f0bfd51137d4bc09c5",
    "julia-gillard": "8aa18de75a40377cbcdaf8a04b14c083ee95314646281d392648fd129dc3b93f",
    "leni-robredo": "57af180756b361c6f55598959425791e156c0e3da2357cf591d9bb4c8d39be8a",
    "mo-mowlam": "0f69e771da86d6173873864354f14694c1282ad5e62a1b26323db5c6cc7b45a7",
    "rachel-carson": "bbf1442b5fc3bacd98d1a2abebaffb5013fca915badad0e25f0316d3b2d3df8c",
    "toni-morrison": "6a81c8f8a31743bb6108b43c4ab2955e097e58a26b3b61c272de0c5111ad2cbe",
}


async def apply_reviewed_leads(db, shipped):
    guard = {"sections_edited": {"$ne": True}, "lead_review_decision": {"$exists": False}}
    query = {"slug": {"$in": list(REVIEWED_LEAD_BASELINES)}, **guard}
    projection = {"slug": 1, "name": 1, "matches": 1, "sections.matches": 1, "sections.map": 1,
                  "sections.communicates.examples": 1, "sections.communicates.example_sources": 1}
    async for row in db.figures.find(query, projection):
        slug = row["slug"]
        sections = row.get("sections") or {}
        comm = sections.get("communicates") or {}
        snapshot = {"name": row.get("name"), "matches": row.get("matches"), "sections.matches": sections.get("matches"),
                    "sections.map": sections.get("map"), "sections.communicates.examples": comm.get("examples"),
                    "sections.communicates.example_sources": comm.get("example_sources")}
        if field_digest(snapshot) != REVIEWED_LEAD_BASELINES[slug]:
            continue
        revised = shipped[slug]["sections"]
        await db.figures.update_one({"_id": row["_id"], **guard, **snapshot}, {"$set": {
            "matches": revised["matches"], "sections.matches": revised["matches"], "sections.map": revised["map"],
        }})


async def migrate_figure_reviews(db, shipped):
    """Replace only the prior shipped copy, and keep the figure's three match representations together."""
    baseline = seed_json("figure_review_baseline.json")
    candidates = {"slug": {"$in": list(baseline)}, "sections.map": {"$type": "object"},
                  "sections.map.basis": {"$exists": False}}
    group = {"matches", "sections.matches", "sections.map"}
    writes = []
    def at(record, path):
        for key in path.split("."):
            record = record.get(key) if isinstance(record, dict) else None
        return record
    async for row in db.figures.find(candidates):
        slug = row["slug"]
        prior = baseline[slug]
        clean = {field for field, fingerprint in prior.items() if field_digest(at(row, field)) == fingerprint}
        if not group.issubset(clean):
            continue  # Never combine an editor's matches with a different rationale.
        guard = {field: at(row, field) for field in clean}
        update = {field: at(shipped[slug], field) for field in clean}
        writes.append(UpdateOne({"_id": row["_id"], **guard}, {"$set": update}))
    if writes:
        await db.figures.bulk_write(writes, ordered=False)


def figure_dating_copy():
    folder = os.path.join(SEED_DIR, "figure_dating")
    articles = {}
    for filename in sorted(name for name in os.listdir(folder) if name.endswith(".json")):
        batch = seed_json(os.path.join("figure_dating", filename))
        if articles.keys() & batch.keys():
            raise ValueError(f"Duplicate dating section in {filename}")
        articles.update(batch)
    return articles


async def add_dating_sections(collection, copy):
    # Migrate this field only once. An editor can subsequently change or remove it.
    guard = {"sections": {"$type": "object"}, "sections.dating": {"$exists": False},
             "dating_seeded": {"$ne": True}}
    writes = [UpdateOne({"_id": row["_id"], **guard},
                        {"$set": {"sections.dating": copy[row["slug"]], "dating_seeded": True}})
              async for row in collection.find({**guard, "slug": {"$in": list(copy)}}, {"slug": 1})]
    if writes:
        await collection.bulk_write(writes, ordered=False)


async def seed_public_profiles(db, created_at):
    portraits = seed_json("figure_portraits.json")
    existing = set(await db.figures.distinct("slug"))
    shipped = {slug: attach_example_sources(f) for slug, f in seed_json("figures.json").items()}
    writes = []
    folder = os.path.join(SEED_DIR, "figure_profiles")
    for filename in sorted(name for name in os.listdir(folder) if name.endswith(".json")):
        path = os.path.join(folder, filename)
        with open(path, encoding="utf-8") as source:
            profiles = json.load(source)
        for profile in profiles:
            slug = profile["slug"]
            if slug in shipped:
                raise ValueError(f"Duplicate public figure: {slug}")
            attach_example_sources(profile)
            shipped[slug] = {**profile, "matches": profile["sections"]["matches"], "identity": profile["sections"]["identity"]}
            if slug in existing:
                continue
            sections = profile["sections"]
            doc = {key: profile[key] for key in ("slug", "name", "gender", "sources")}
            doc.update(identity=sections["identity"], matches=sections["matches"], sections=sections,
                       published=True, created_at=created_at)
            if photo := portraits.get(slug):
                doc.update(photo_url="/img/figures/" + photo["file"], photo_attribution=photo,
                           photo_credit=photo["artist"] + " · " + photo["license"])
            writes.append(UpdateOne({"slug": slug}, {"$setOnInsert": doc}, upsert=True))

    # Update the old 78 only when the four cards still match the previously shipped text.
    # An independently edited heading, photo, draft status or section survives the update.
    previous = seed_json("figure_legacy_examples.json")
    field = "sections.communicates."
    pending = {"slug": {"$in": list(previous)}, field + "examples": {"$type": "array"},
               field + "example_sources": {"$exists": False}}
    async for row in db.figures.find(pending, {"slug": 1, field + "examples": 1, "sources": 1}):
        slug = row["slug"]
        old = row["sections"]["communicates"]["examples"]
        digest = hashlib.sha256(json.dumps(old, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        if digest != previous[slug]:
            continue
        comm = shipped[slug]["sections"]["communicates"]
        refs = {ref["url"]: ref for ref in (*shipped[slug]["sources"], *(row.get("sources") or []))}
        writes.append(UpdateOne({"_id": row["_id"], field + "examples": old, field + "example_sources": {"$exists": False},
                                 "sources": row.get("sources")},
                                {"$set": {field + key: comm[key] for key in ("examples", "example_sources")} |
                                 {"sources": list(refs.values())}}))
    if writes:
        await db.figures.bulk_write(writes, ordered=False)
    await migrate_figure_reviews(db, shipped)
    await apply_reviewed_leads(db, shipped)
    await revise_career_cards(db.figures, shipped)
    await revise_rawya_profile(db.figures, shipped["rawya-ateya"])
    await add_dating_sections(db.figures, figure_dating_copy())
    # The earlier sentence made Debierne sound like a co-recipient of Curie's 1911 Nobel.
    curie = shipped["marie-curie"]["sections"]["communicates"]["text"][1]
    corrected = ("Curie continued her research after Pierre died in 1906 and obtained radium metal with "
                 "André-Louis Debierne in 1910. She was the sole recipient of the 1911 chemistry prize for her work on radium and polonium.")
    former = ("After sharing the 1903 physics prize and continuing the research after Pierre died in 1906, she and "
              "André-Louis Debierne obtained radium metal in 1910 and received the 1911 chemistry prize for work on radium and polonium.")
    if corrected in curie:
        await db.figures.update_one({"slug": "marie-curie", field + "text.1": curie.replace(corrected, former)},
                                    {"$set": {field + "text.1": curie}})
    await figure_career_review.apply(db)


FIGURES = [
    ("elon-musk", "Elon Musk", "the man who treats every conversation like a bid for control", 12),
    ("drake", "Drake", "the man who turns every relationship into a lyric about himself", 25),
    ("john-mayer", "John Mayer", "the man who confesses beautifully and commits to nothing", 9),
    ("leonardo-dicaprio", "Leonardo DiCaprio", "the man whose dating life has a famously strict age limit", 34),
    ("pete-davidson", "Pete Davidson", "the man who makes intensity look effortless and charming", 18),
    ("travis-kelce", "Travis Kelce", "the man who goes all-in loudly and in public", 10),
]


def wave_of(n: int) -> int:
    if n in COMMUNICATION_NUMBERS:
        return 7
    n = n - 100 if n > 100 else n
    return 1 if n <= 8 else 2 if n <= 16 else 3 if n <= 23 else 4 if n <= 31 else 5 if n <= 39 else 6


PREVIOUS_SIX_PART = """Always answer in the six-part format:
1) What they're actually doing (observable actions only)
2) What it likely means (with honest confidence: low / medium / high)
3) What changed since last time (explicitly revise earlier reads when evidence shifts them)
4) Your best move
5) What to say - word for word
6) What not to do
Be confident about actions, honest about uncertainty on motives. Never diagnose. Everyday language."""

SIX_PART = PREVIOUS_SIX_PART + """\nUse the exact JSON fields requested by the task. Keep the response proportional: for a simple short exchange, aim for one short sentence per section and one copyable reply. Expand only for material uncertainty, safety, conflicting evidence or an explicit request for detail. Give each required field a distinct purpose; do not restate the same observation in several sections. Preserve citations, necessary qualifications and boundaries rather than shortening them away. Choose one primary next action. Keep the member's single stated purpose, emotional stance and commitments in every draft and action. Do not add an extra question or unsupported sentiment, apology, plan, promise, invitation or deadline. A question about a date does not prove an imposed deadline. A present refusal does not authorize a permanent commitment. A proposed next step is not something the member has already done: member_pattern and event memories describe only their actual supplied words or actions, never suggested advice."""

PREVIOUS_PREPARE_PROMPT = "Help the member rehearse an upcoming conversation. Give an opening in their voice, distinct things the other person might say or do, and a concrete next step for each. Include what to avoid, words for leaving, and what is unknown. Leave out probability estimates and conclusions about private motives."

SIX_PART += " For a hypothetical or advice-only question that does not request an outbound reply, set what_to_say to JSON null and tone.drafts to an empty object. Explain the next step in the advice fields; never put 'Ask them...' instructions in a copyable message or return five blank strings. Otherwise preserve the member's exact question or refusal in all five complete drafts."

DEFAULT_PROMPTS = {
    **FEATURE_PROMPTS,
    "crush": "Context: a crush - nothing established yet. Optimise for clarity and self-respect; keep investment proportional to the other person’s.",
    "early_dating": "Context: early dating. Read consistency between words and actions; advise clear, low-pressure invitations.",
    "established": "Context: an established relationship. Focus on patterns over incidents, repair, and direct requests.",
    "fading": "Context: a fading connection. Help the member see what is being offered and act with dignity.",
    "family": "Context: a family member (e.g. mom, sister). Long shared history and loyalty; advise boundaries that keep the relationship intact where the member wants to preserve it. Never romantic framing.",
    "friend": "Context: a friend or other important person. Mutuality, honesty and repair. Never romantic framing.",
    "work": "Context: a work relationship (coworker or boss). Professional stakes and power differences; advise tactful, documentable, career-safe moves. Never romantic framing.",
    "six_part": SIX_PART,
    "report": "Build a cautious, source-backed read from what the member has shared: everyday-language traits (Big Five style), communication style, attachment style, triggers, needs, conflict style, what they respond to and what shuts them down. Every substantive point needs a confidence level and its sources. Keep required sections concise and avoid repeating an observation under several traits. If evidence is sparse, state the shared limitation once and keep each unsupported section's empty note brief; do not fill space with guesses or repeated lists of questions. Weigh reported events far above interpretations. A short acknowledgment cannot establish personality, attachment, compatibility or motive. No clinical labels or disorders; a cited impression is not a validated psychological measurement.",
    "prepare": PREVIOUS_PREPARE_PROMPT + " Keep the plan proportional to the situation: a concise opening, one primary next action per distinct branch, and a brief exit if needed. Do not repeat the same advice across branches. Use only the member's supplied emotional stance, availability and commitments; add no affection, apology or promise to sound warmer. Needing to inspect work does not authorize a promise to inspect today, report this afternoon, or meet a requested deadline. A question about timing is not proof of an imposed deadline. Keep unconfirmed timing out of every opening, branch and exit. Do not turn a present boundary into a permanent rule. Use names or neutral pronouns when identity or pronouns were not supplied.",
}

DEFAULT_PROMPTS["prepare"] += (
    " Final check every opening, branch and exit against the supplied words. A crush plan does not "
    "authorize 'I've enjoyed talking with you' or 'I'm glad we talked' unless the member expressed that. "
    "Give the grounded invitation or question instead. Do not add 'I'll follow up', 'I'll start there' "
    "or another promise to make a next step feel complete; leave it as an optional action in the advice. "
    "Keep an explicit offered commitment, but do not expand its condition, timing or emotional stance."
)

# Existing installations store the default prompts alongside administrator edits.
# Migrate only an exact match for a previously shipped default.
LEGACY_MEMBER_PROMPTS = {
    "crush": "Context: a crush - nothing established yet. Optimise for clarity and self-respect; keep investment proportional to his.",
    "fading": "Context: a fading connection. Help her see what is actually being offered and protect her dignity whichever way it goes.",
    "family": "Context: a family member (e.g. mom, sister). Long shared history and loyalty; advise boundaries that keep the relationship intact where she wants it intact. Never romantic framing.",
    "report": "Build an informed psychological read of this person from what she has shared: everyday-language traits (Big Five style), communication style, attachment style, triggers, needs, conflict style, what they respond to and what shuts them down. Every point needs a confidence level and its sources. Where there is not enough evidence say so plainly instead of guessing. Weigh reported events (things they did or said) far above her interpretations. No clinical labels or disorders.",
    "prepare": "Give a game plan for an upcoming encounter: an opening line, the likely reactions with probabilities that sum to roughly 100 and what each reaction tells her, how to handle each, what to avoid, and a graceful way out if it goes badly. Spell out the uncertainty.",
}


PREVIOUS_PROMPT_DEFAULTS = {
    "prepare": "Give a game plan for an upcoming encounter: an opening line, the likely reactions with probabilities that sum to roughly 100 and what each reaction might indicate, how to handle each, what to avoid, and a graceful way out if it goes badly. Spell out the uncertainty.",
    "practice_insights": "Describe recurring communication choices in the member's own terms.",
    "profile_patterns": "Describe the member's observable patterns in everyday language.",
}

# Exact defaults shipped before the 2026-11-16 quality revision; never match editorial variants.
PREVIOUS_QUALITY_PROMPT_DEFAULTS = {
    "six_part": "Always answer in the six-part format:\n1) What they're actually doing (observable actions only)\n2) What it likely means (with honest confidence: low / medium / high)\n3) What changed since last time (explicitly revise earlier reads when evidence shifts them)\n4) Your best move\n5) What to say - word for word\n6) What not to do\nBe confident about actions, honest about uncertainty on motives. Never diagnose. Everyday language.\nUse the exact JSON fields requested by the task. Keep the member's single stated purpose in every copyable draft and action. Do not add another question, new plan, invitation or commitment. A proposed next step is not something the member has already done: member_pattern and event memories must describe only their actual supplied words or actions, never your suggested advice.",
    "report": "Build an informed psychological read of this person from what the member has shared: everyday-language traits (Big Five style), communication style, attachment style, triggers, needs, conflict style, what they respond to and what shuts them down. Every point needs a confidence level and its sources. Where there is not enough evidence say so plainly instead of guessing. Weigh reported events (things they did or said) far above the member’s interpretations. No clinical labels or disorders.",
    "prepare": "Help the member rehearse an upcoming conversation. Give an opening in their voice, distinct things the other person might say or do, and a concrete next step for each. Include what to avoid, words for leaving, and what is unknown. Leave out probability estimates and conclusions about private motives. Use only the availability and commitments the member explicitly offered. Needing to inspect work does not authorize a promise to inspect today, report this afternoon, or meet a requested deadline. Keep unconfirmed timing out of every opening, branch and exit line. Use names or neutral pronouns when identity or pronouns were not supplied."
}

DEFAULT_UPGRADE = {
    "headline": "Plans",
    "pitch": "A personal workspace for understanding yourself, practicing conversations, and following up on what worked.",
    "paid_features": ["Unlimited active people", "Full memory history"],
    "price_label": "$12/month",
}

RETIRED_OFFER = re.compile(r"\b(?:free|trial|lifetime|unlimited (?:AI )?(?:messages?|replies)|(?:1|one) active (?:person|profile)|(?:ten|10) (?:AI )?replies)\b|\$(?!12(?:\.00)?(?:[^0-9.]|$))", re.I)


def paid_plan_copy(stored):
    """Preserve editorial copy while retiring every configurable price and former free offer."""
    stored = stored if isinstance(stored, dict) else {}
    copy = dict(DEFAULT_UPGRADE)
    for key in ("headline", "pitch"):
        value = stored.get(key)
        if key == "headline" and value == "Imladri Plus":
            continue
        if isinstance(value, str) and value.strip() and not RETIRED_OFFER.search(value):
            copy[key] = value.strip()
    features = stored.get("paid_features")
    if isinstance(features, list):
        extra = [value.strip() for value in features if isinstance(value, str) and value.strip()
                 and not RETIRED_OFFER.search(value) and value.strip() not in {"Priority responses", "100 AI replies per billing month"}]
        copy["paid_features"] = list(dict.fromkeys([*DEFAULT_UPGRADE["paid_features"], *extra]))
    return copy


def default_settings():
    return {"key": "global", "people_limit": None, "plan_version": 3, "waitlist_mode": False, "prompts": DEFAULT_PROMPTS,
            "upgrade": DEFAULT_UPGRADE, "tiktok_url": ""}


async def seed_base(db):
    now = datetime.now(timezone.utc).isoformat()
    inserts, sections = [], []
    male = seed_json("male_archetypes.json")
    female = female_sections()
    for gender, rows, articles in (("male", ARCHETYPES, male), ("female", FEMALE_ARCHETYPES, female)):
        for n, slug, name, one, txt, red in rows:
            entry = {"slug": slug, "number": n, "name": name, "one_liner": one, "gender": gender,
                     "wave": wave_of(n), "unlocked": True, "texting_tags": [txt],
                     "red_flag_tags": [red], "created_at": now}
            if gender == "male":
                entry.update(identity=f"{name}: {one}", sections=None)
            inserts.append(UpdateOne({"slug": slug}, {"$setOnInsert": entry}, upsert=True))
            article = articles.get(slug)
            if article:
                sections.append(UpdateOne({"slug": slug, "sections": None, "sections_edited": {"$ne": True}},
                    {"$set": {"sections": article, "identity": article.get("identity") or
                              (slug if gender == "male" else f"{name}: {one}")}}))
            elif gender == "female":
                sections.append(UpdateOne({"slug": slug, "sections": {"$exists": False}},
                    {"$set": {"sections": None, "identity": f"{name}: {one}"}}))
    await db.archetypes.bulk_write(inserts, ordered=False)
    await db.archetypes.update_many({"gender": {"$exists": False}}, {"$set": {"gender": "male"}})
    await db.archetypes.bulk_write(sections, ordered=False)
    await add_dating_sections(db.archetypes, seed_json("archetype_dating.json"))

    inserts = [UpdateOne({"slug": slug}, {"$setOnInsert": {
        "slug": slug, "name": name, "identity": f"{name}: {identity}", "published": True,
        "matches": [{"number": match, "why": ""}], "sections": None, "created_at": now}}, upsert=True)
        for slug, name, identity, match in FIGURES]
    await db.figures.bulk_write(inserts, ordered=False)
    sections = []
    for slug, figure in seed_json("figures.json").items():
        attach_example_sources(figure)
        sections.append(UpdateOne({"slug": slug, "sections": None, "sections_edited": {"$ne": True}},
                                  {"$set": {k: v for k, v in figure.items() if v is not None}}))
    await db.figures.bulk_write(sections, ordered=False)
    await seed_public_profiles(db, now)

    manifest = seed_json("media_manifest.json")
    existing = set(await db.media.distinct("id", {"id": {"$in": list(manifest)}}))
    media_writes = []
    batch_bytes = 0
    for mid in manifest.keys() - existing:
        media = manifest[mid]
        path = os.path.join(SEED_DIR, "media", media["file"])
        if os.path.exists(path):
            with open(path, "rb") as fh:
                data = fh.read()
            # A stable key lets two services seed an empty database concurrently.
            media_writes.append(UpdateOne({"_id": "seed:" + mid}, {"$setOnInsert": {
                "id": mid, "data": data, "content_type": media["content_type"],
                "credit": media["credit"], "source_page": media["source_page"]}}, upsert=True))
            batch_bytes += len(data)
            if batch_bytes >= 4 * 1024 * 1024:
                await db.media.bulk_write(media_writes, ordered=False)
                media_writes.clear()
                batch_bytes = 0
    if media_writes:
        await db.media.bulk_write(media_writes, ordered=False)
    await db.settings.update_one({"key": "global"}, {"$setOnInsert": default_settings()}, upsert=True)
    await migrate_prompt_defaults(db)
    await migrate_paid_plan(db)


async def migrate_prompt_defaults(db):
    for version in (LEGACY_MEMBER_PROMPTS, PREVIOUS_PROMPT_DEFAULTS, PREVIOUS_FEATURE_PROMPTS,
                    PREVIOUS_QUALITY_FEATURE_PROMPTS, PREVIOUS_QUALITY_PROMPT_DEFAULTS,
                    {"six_part": PREVIOUS_SIX_PART, "prepare": PREVIOUS_PREPARE_PROMPT}):
        for key, old_prompt in version.items():
            field = f"prompts.{key}"
            await db.settings.update_one({"key": "global", field: old_prompt}, {"$set": {field: DEFAULT_PROMPTS[key]}})


async def migrate_paid_plan(db):
    legacy = {"key": "global", "plan_version": {"$ne": 3}}
    while old := await db.settings.find_one(legacy, {"upgrade": 1}):
        update = {"$set": {"upgrade": paid_plan_copy(old.get("upgrade")), "people_limit": None, "plan_version": 3},
                  "$unset": {"free_plan_version": ""}}
        if (await db.settings.update_one({**legacy, "upgrade": old.get("upgrade")}, update)).matched_count:
            return


ARCH_SHAPE = """{"identity": "one punchy identity line", "summary": "2 short paragraphs",
"dating": ["one concrete date with this type, from the reader's perspective", "a follow-up moment that shows the attraction or friction"],
"texting_patterns": {"text": "1 paragraph", "bubbles": [{"from": "him|you", "text": "..."}] (6-8 bubbles, realistic texts)},
"interested": ["4 signs he's actually interested"], "not_interested": ["4 signs he's not"],
"red_flags": ["4"], "green_flags": ["3"], "what_to_say": ["3 word-for-word lines"], "never_say": ["3 lines"],
"manipulation": ["4 patterns, framed defensively"], "leverage": ["3 points where she holds power"],
"closest_cousin": {"number": <a different male roster number>, "text": "how to tell them apart - the tell"}}"""

FIG_SHAPE = """{"identity": "one-line identity, same voice as archetypes",
"matches": [{"number": <int>, "why": "one line"}] (closest first, 1-2 items),
"pattern": ["paragraph 1", "paragraph 2"],
"communicates": {"text": "1 paragraph", "examples": ["2-3 described or quoted documented public examples"]},
"dating": ["an imagined dating scene with a fictional person who communicates this way", "how the reader could navigate that dynamic; no private claims about the public figure"],
"spectrum": {"interested": "how he treats allies/people he's courting", "invested": "how he treats people he's committed to", "done": "how he treats people he's publicly discarded"},
"handling": {"works": ["3"], "never": ["3"]},
"power_moves": ["3-4 control patterns visible in public conduct, framed defensively"],
"leverage": ["3"],
"map": {"closest": {"number": <int>, "rationale": "..."}, "cousin": {"number": <int>, "tell": "He reads like #X, but here's the tell..."}, "secondary": [{"number": <int>, "why": "..."}]}}"""


ARCH_SHAPE_F = """{"identity": "one punchy identity line", "summary": "2 short paragraphs",
"dating": ["one concrete date with this type, from the reader's perspective", "a follow-up moment that shows the attraction or friction"],
"texting_patterns": {"text": "1 paragraph", "bubbles": [{"from": "her|you", "text": "..."}] (6-8 bubbles, realistic texts)},
"interested": ["4 signs she's actually interested"], "not_interested": ["4 signs she's not"],
"red_flags": ["4"], "green_flags": ["3"], "what_to_say": ["3 word-for-word lines"], "never_say": ["3 lines"],
"manipulation": ["4 patterns, framed defensively"], "leverage": ["3 points where you hold power"],
"closest_cousin": {"number": <a different female roster number>, "text": "how to tell them apart - the tell"}}"""


def roster(female: bool = False):
    return "\n".join(f"#{n} {name} - {one}" for n, _, name, one, _, _ in (FEMALE_ARCHETYPES if female else ARCHETYPES))


async def draft_female(db, doc, sem, out: dict):
    from ai import ai_json
    async with sem:
        system = ("You write for Imladri, a sharp, warm, funny-but-wise insight brand that helps people read who they're dating. "
                  "This page is about a WOMAN archetype, written to the reader ('you') who is dating or talking to her - any gender. "
                  "Voice: confident about actions, honest about uncertainty on motives, never clinical, no diagnoses, never misogynistic or demeaning; "
                  "describe behaviour, not worth. Use she/her for the archetype.")
        prompt = (f"The female roster:\n{roster(True)}\n\nWrite the page for #{doc['number']} {doc['name']} ('{doc['one_liner']}'). "
                  f"Texting tag: {doc['texting_tags'][0]}. Red-flag tag: {doc['red_flag_tags'][0]}.\nShape:\n{ARCH_SHAPE_F}")
        for attempt in range(3):
            try:
                s = await ai_json(system, prompt)
                await db.archetypes.update_one({"slug": doc["slug"]}, {"$set": {"sections": s, "identity": s.get("identity") or doc["identity"]}})
                out[doc["slug"]] = s
                print("ok", doc["slug"], flush=True)
                return
            except Exception as e:
                print("fail", doc["slug"], attempt, e, flush=True)


async def draft_archetype(db, doc, sem):
    from ai import ai_json
    async with sem:
        system = ("You write for Imladri, a sharp, warm, funny-but-wise dating insight brand for women. "
                  "Voice: confident about actions, honest about uncertainty on motives, never clinical, no diagnoses.")
        prompt = (f"The 47 roster:\n{roster()}\n\nWrite the page for #{doc['number']} {doc['name']} "
                  f"('{doc['one_liner']}'). Texting tag: {doc['texting_tags'][0]}. Red-flag tag: {doc['red_flag_tags'][0]}.\nShape:\n{ARCH_SHAPE}")
        try:
            s = await ai_json(system, prompt)
            await db.archetypes.update_one({"slug": doc["slug"]}, {"$set": {"sections": s, "identity": s.get("identity") or doc["identity"]}})
            print("ok", doc["slug"], flush=True)
        except Exception as e:
            print("fail", doc["slug"], e, flush=True)


async def draft_figure(db, doc, sem):
    from ai import ai_json
    async with sem:
        system = ("You write persona analyses for Imladri. ONLY documented public behaviour: interviews, public statements, "
                  "recorded appearances, reported public actions, his own published words. No speculation about private life, "
                  "no clinical language, no motive certainty - use 'publicly appears to' when soft. It's about 'someone like him'. "
                  "Same voice as the archetype pages.")
        prompt = (f"The catalog:\n{roster(doc.get('gender') in ('female', 'woman'))}\n\nWrite a draft for {doc['name']}. "
                  "Compare the defining interpersonal behavior, not the name of an archetype. Career length, genre, awards, "
                  "job titles and fictional roles are not evidence of relationship patterns. Do not force a match. "
                  f"An editor will check the career evidence before publication.\nShape:\n{FIG_SHAPE}")
        try:
            s = await ai_json(system, prompt)
            await db.figures.update_one({"slug": doc["slug"]}, {"$set": {
                "sections": s, "identity": s.get("identity") or doc["identity"], "matches": s.get("matches") or [], "published": False}})
            print("ok", doc["slug"], flush=True)
        except Exception as e:
            print("fail", doc["slug"], e, flush=True)


async def main():
    from dotenv import load_dotenv
    from backend.database import open_database
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    client, db = open_database()
    await seed_base(db)
    sem = asyncio.Semaphore(6)
    archs = await db.archetypes.find({"sections": None}, {"_id": 0}).to_list(100)
    figs = await db.figures.find({"sections": None, "sources": {"$exists": False},
                                  "sections_edited": {"$ne": True}}, {"_id": 0}).to_list(100)
    archs = [a for a in archs if a.get("gender") != "female"]
    fem = await db.archetypes.find({"gender": "female", "sections": None}, {"_id": 0}).to_list(100)
    out = {d["slug"]: d["sections"] async for d in db.archetypes.find({"gender": "female", "sections": {"$ne": None}}, {"_id": 0, "slug": 1, "sections": 1})}
    await asyncio.gather(*[draft_archetype(db, a, sem) for a in archs], *[draft_figure(db, f, sem) for f in figs],
                         *[draft_female(db, a, sem, out) for a in fem])
    with open(FEMALE_FILE, "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps({"done": True, "female": len(out)}))


if __name__ == "__main__":
    asyncio.run(main())
