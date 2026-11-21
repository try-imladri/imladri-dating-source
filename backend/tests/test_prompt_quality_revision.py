"""Quality guidance revisions preserve shipped schemas, editorial choices and CAS boundaries.

Frozen hashes below come from e615d647 Git blobs, not the replacement defaults.
These are offline composition/migration tests; model quality is evaluated separately.
"""
import ast
import asyncio
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import content, conversation_tone, couples_tone, prompt_library

SHIPPED_DEFAULT_SHA256 = {'couples_tone': '318855c44dcdd056bf22072131e143b742408551f04ad5ed2500eea270b4e16b', 'group_read': '159d01ed85271c9bde974194cef1c573bd4112d468e06c01cb2b6ef897ca4716', 'incoming_tone': 'ce41ab19a57498956539bf1d9b26f6541cdcaa131feaef72af5e010d7d9f6670', 'outcome_review': '234f868b906d93bd0515aca09d9c2a41987244a12d061f121892017bfd78ce64', 'practice_feedback': '662db6b9dc4dd118c11242888e8b4f4491b69c3a89a33f134385c668c3d2b23f', 'practice_options': '711c264dbd779f5d106fbc8004cddb2f658ed5ecad36ac1901fba7cabb29d9ea', 'practice_reply': 'df1ca13982425d4c2c3c2c5810142c6ebb050a2c7f98163c0151af018ac283ac', 'prepare': '94811f1597e1d2cb9a07e359b4f8ec57760e9799ca5872bbbf70f988a4ab5d43', 'profile_patterns': 'b78c45874a90cd1ab63f254e243d3a2b1af9904c8d42b06c021cc09a41ddf023', 'reply_style': 'e1511036f4a13478e275de1c77118c3a9c0721e79996e3df708f9347ce101a5e', 'report': '26472b8abcf6df95f22c003f825705c7c2cd3ef6a82f8f60f78aa0da6849c556', 'six_part': '0cacadea15d833fd53d53bcf43e12701b45660f658697686ef447247f04c5315', 'starters': '0b6abe9e8e64750d46d62e7b71c7736d5ddb36da824812ffe0d7d2ba69d40937'}
SHIPPED_LOGIC_SHA256 = {'prompt_library.py': '14fd7a4135909e86cd9df3d0504e97998d6c0a3bf7897d8797a008f39abd1d2a', 'content.py': 'f7eaa9b9ee0c7c03ecdb0ebddd849d991b1d3c2a9bc4a983917d2a7ac98fbbf7', 'conversation_tone.py': 'da671be1d4333ff752530452160ef7ac89a4de011b8d188b48ba33d4a61b75b3', 'couples_tone.py': '15161f43bd48faffd29aadf49928f28e7a26472c30dbbb11f5a908fc7fed04b2'}
SHIPPED_TONE_SCHEMA_SHA256 = '31d57bf9e34fcc2ef81bb38cb95f967035de597aed42db211c6f106154551dda'
SHIPPED_COACH_GUIDANCE_SHA256 = '5d9e3c62ade25564aa66b60e5afd8795eb67b4720d5680b00880076d3bbc9d91'
SHIPPED_DEFAULT_SHA256["archetype_ranking"] = 'b4bc2a5cb627b5fbaf2fe0239b2c0c1e1206b3e2d5bd4700a3e3961d537270c6'

FORMER = {**prompt_library.PREVIOUS_QUALITY_FEATURE_PROMPTS, **content.PREVIOUS_QUALITY_PROMPT_DEFAULTS}


class Settings:
    """Exercise the actual migration's conditional writes, including an editor race."""
    def __init__(self, doc, race_key=None):
        self.doc = deepcopy(doc)
        self.race_key = race_key
        self.queries = []
        self.writes = 0

    async def update_one(self, query, update):
        self.queries.append(deepcopy(query))
        assert set(query) == {"key", *update["$set"]}
        assert set(update) == {"$set"} and len(update["$set"]) == 1
        field, replacement = next(iter(update["$set"].items()))
        assert field.startswith("prompts.")
        key = field.removeprefix("prompts.")
        if self.race_key == key and self.doc.get("prompts", {}).get(key) == query[field]:
            self.doc["prompts"][key] = "A concurrent administrator's new instruction."
            self.race_key = None
        matches = self.doc.get("key") == query["key"] and self.doc.get("prompts", {}).get(key) == query[field]
        if matches:
            self.doc["prompts"][key] = replacement
            self.writes += 1
        return SimpleNamespace(matched_count=int(matches))


def migrate(settings):
    asyncio.run(content.migrate_prompt_defaults(SimpleNamespace(settings=settings)))


def test_new_migrations_reference_exact_previously_shipped_bytes():
    assert len(FORMER) == 14 and set(FORMER) == set(SHIPPED_DEFAULT_SHA256)
    for key, former in FORMER.items():
        assert hashlib.sha256(former.encode()).hexdigest() == SHIPPED_DEFAULT_SHA256[key]
        assert former != content.DEFAULT_PROMPTS[key]


@pytest.mark.parametrize("key", sorted(FORMER))
def test_exact_shipped_default_migrates_idempotently_without_editorial_metadata_changes(key):
    before = {"key": "global", "prompts": {key: FORMER[key], "work": "Custom workplace guidance."},
              "prompts_revision": "editor-42", "prompts_published_at": "2026-11-01T00:00:00Z",
              "prompt_history": [{"revision": "editor-41"}], "unrelated_setting": False}
    settings = Settings(before)
    migrate(settings)
    expected = deepcopy(before)
    expected["prompts"][key] = content.DEFAULT_PROMPTS[key]
    assert settings.doc == expected and settings.writes == 1
    migrate(settings)
    assert settings.doc == expected and settings.writes == 1


@pytest.mark.parametrize("key", sorted(FORMER))
@pytest.mark.parametrize("editorial_change", ["append", "whitespace", "replacement"])
def test_near_defaults_and_custom_admin_text_never_match_migration(key, editorial_change):
    old = FORMER[key]
    edited = {"append": old + " Keep my custom instruction.", "whitespace": " " + old,
              "replacement": "Use this custom guidance instead."}[editorial_change]
    before = {"key": "global", "prompts": {key: edited}, "prompts_revision": "custom"}
    settings = Settings(before)
    migrate(settings)
    assert settings.doc == before and settings.writes == 0
    assert prompt_library.normalized(content.DEFAULT_PROMPTS, settings.doc["prompts"])[key] == edited.strip()


@pytest.mark.parametrize("key", sorted(FORMER))
def test_editor_change_at_conditional_write_wins_over_default_migration(key):
    settings = Settings({"key": "global", "prompts": {key: FORMER[key]}}, race_key=key)
    migrate(settings)
    assert settings.doc["prompts"][key] == "A concurrent administrator's new instruction."
    assert settings.writes == 0
    assert any(query.get("prompts." + key) == FORMER[key] for query in settings.queries)


def test_missing_default_fields_are_not_backfilled_and_already_current_values_are_not_republished():
    for prompts in ({}, dict(content.DEFAULT_PROMPTS)):
        before = {"key": "global", "prompts": prompts, "prompts_revision": "retained"}
        settings = Settings(before)
        migrate(settings)
        assert settings.doc == before and settings.writes == 0


def test_all_25_editable_keys_keep_their_member_composition_and_preview_routes():
    contexts = {"crush", "early_dating", "established", "fading", "family", "friend", "work"}
    features = {"incoming_tone", "reply_style", "couples_tone", "group_read", "starters", "outcome_review",
                "practice_options", "practice_reply", "practice_feedback", "practice_insights", "profile_patterns",
                "archetype_ranking", "archetype_line", "transcribe_image", "transcribe_text", "six_part", "report", "prepare"}
    assert set(content.DEFAULT_PROMPTS) == contexts | features
    assert prompt_library.composition_checks(content.DEFAULT_PROMPTS) == 34
    editorial = {key: "EDITORIAL_" + key for key in content.DEFAULT_PROMPTS}
    for context in sorted(contexts):
        for feature in ("six_part", "report", "prepare"):
            keys = ((context, feature, "incoming_tone", "reply_style") if feature == "six_part" else (context, feature))
            assert prompt_library.compose(editorial, feature, context) == "\n\n".join(editorial[key] for key in keys)
    for feature, keys in (("couples_tone", ("couples_tone", "incoming_tone", "reply_style")),
                          ("group_read", ("six_part", "group_read"))):
        assert prompt_library.compose(editorial, feature) == "\n\n".join(editorial[key] for key in keys)
    for key in sorted(content.DEFAULT_PROMPTS):
        selected_context = "couples_tone" if key in ("incoming_tone", "reply_style") else "six_part"
        result = prompt_library.preview(content.DEFAULT_PROMPTS, editorial, key, "PENDING_EDIT", context=selected_context)
        assert result["guidance"].count("PENDING_EDIT") == 1 and key in result["keys"]
        assert "PENDING_EDIT" not in prompt_library.compose(editorial, "couples_tone")
    assert all(0 < len(value) <= prompt_library.MAX_LENGTH for value in content.DEFAULT_PROMPTS.values())


@pytest.mark.parametrize("name", sorted(SHIPPED_LOGIC_SHA256))
def test_guidance_revision_does_not_change_task_logic_validators_or_api_classes(name):
    tree = ast.parse(Path(content.__file__).with_name(name).read_text(encoding="utf8"))
    # Later explicitly reviewed runtime guards add these pure helpers. Preserve
    # the e615 definitions around them; guard semantics are tested separately.
    added = {"_intent_text", "_claim_text", "_authorized_claims", "draft_intent_violation"} if name == "conversation_tone.py" else set()
    defs = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name != "migrate_prompt_defaults" and node.name not in added]
    if name == "conversation_tone.py":
        assert {node.name for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in added} == added
    if name == "couples_tone.py":
        coach = next(node for node in defs if node.name == "coach_tone")
        inserted = [node for node in coach.body if (isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "member_words" for target in node.targets))
                    or (isinstance(node, ast.If) and isinstance(node.test, ast.Call)
                        and isinstance(node.test.func, ast.Attribute) and node.test.func.attr == "draft_intent_violation")]
        assert len(inserted) == 2
        coach.body = [node for node in coach.body if node not in inserted]
    actual = ast.dump(ast.Module(body=defs, type_ignores=[]), include_attributes=False).encode()
    assert hashlib.sha256(actual).hexdigest() == SHIPPED_LOGIC_SHA256[name]


def test_response_schema_and_existing_couples_safety_guidance_are_unchanged():
    schema = conversation_tone.TONE_SHAPE.split("\nOnly use", 1)[0]
    assert hashlib.sha256(schema.encode()).hexdigest() == SHIPPED_TONE_SCHEMA_SHA256
    prior = couples_tone.COACH_INSTRUCTIONS.split("\nKeep the explanation proportional to the evidence:", 1)[0]
    voice = ('The focus speaker selects only whose incoming tone to estimate. In shared mode every draft is the\n'
             'requesting member\'s "me" voice responding to "them", never the partner speaking as the member.\n'
             'In private mode rewrite only the selected "me" line; do not swap either person\'s perspective.\n')
    assert prior.count(voice) == 1
    prior = prior.replace(voice, "", 1)
    assert hashlib.sha256(prior.encode()).hexdigest() == SHIPPED_COACH_GUIDANCE_SHA256
    # The production assembler still produces the original one-root schema; no JSON repair or schema shrink.
    core = '{"what_to_say":"example"}\nNever claim unsupported motives.'
    assembled = conversation_tone.exchange_shape(core)
    assert assembled.count('"tone":') == 1 and assembled.startswith('{"what_to_say":"example",')
    assert '"baseline_evidence"' in assembled and '"evidence"' in assembled
