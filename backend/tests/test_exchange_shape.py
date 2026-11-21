"""One outer response skeleton prevents ambiguous separate JSON examples."""
import ast
import json
from pathlib import Path

import pytest

from backend import conversation_tone


def test_tone_is_nested_in_one_complete_root_object_and_rules_stay_outside(monkeypatch):
    monkeypatch.setattr(conversation_tone, "TONE_SHAPE",
                        '{"drafts":{"neutral":"example","cool":"short"}}.\nOnly use supplied words.')
    shape = conversation_tone.exchange_shape('{"what_to_say":"example"}\nNever claim private motives.')
    skeleton, rules = shape.split("\nNever claim", 1)
    assert json.loads(skeleton) == {"what_to_say": "example", "tone": {
        "drafts": {"neutral": "example", "cool": "short"}}}
    assert "private motives" in rules and "Only use supplied words." in rules
    assert "close drafts, then tone, then the outer response object" in rules


def test_actual_runtime_examples_form_a_balanced_single_outer_skeleton():
    path = Path(conversation_tone.__file__).with_name("server.py")
    tree = ast.parse(path.read_text(encoding="utf8"))
    node = next(node for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(name, ast.Name) and name.id == "EX_SHAPE" for name in node.targets))
    shape = conversation_tone.exchange_shape(ast.literal_eval(node.value))
    skeleton = shape.split("\nNever claim", 1)[0]
    stack, quoted, escaped = [], False, False
    for char in skeleton:
        if quoted:
            if escaped: escaped = False
            elif char == "\\": escaped = True
            elif char == '"': quoted = False
            continue
        if char == '"': quoted = True
        elif char in "{[": stack.append(char)
        elif char in "}]":
            assert stack.pop() == ("{" if char == "}" else "[")
    assert not stack and not quoted
    assert skeleton.count('"tone":') == 1
    assert skeleton.rstrip().endswith("}}\n}")


@pytest.mark.parametrize("core", ["{}", "[]\nNever claim anything.", "{\nNever claim anything."])
def test_incomplete_schema_examples_fail_closed_without_json_repair(core):
    with pytest.raises(ValueError, match="Complete exchange and tone schema"):
        conversation_tone.exchange_shape(core)
