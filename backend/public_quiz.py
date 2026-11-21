"""Validate the public three-tap quiz and its archetype outcomes."""
from fastapi import HTTPException


def clean_text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise HTTPException(422, f"Enter text between 1 and {limit} characters.")
    return value.strip()


def validate(data, archetypes):
    if not isinstance(data, dict):
        raise HTTPException(422, "A quiz definition is required.")
    questions = data.get("questions")
    if not isinstance(questions, list) or len(questions) != 3:
        raise HTTPException(422, "The homepage quiz requires three questions.")
    cleaned = []
    for question, count in zip(questions, (2, 3, 3)):
        if not isinstance(question, dict) or not isinstance(question.get("a"), list) or len(question["a"]) != count:
            raise HTTPException(422, f"This question requires {count} answer choices.")
        choices = [clean_text(answer, 85) for answer in question["a"]]
        if len({s.casefold() for s in choices}) != count:
            raise HTTPException(422, "Use distinct choices within each question.")
        cleaned.append({"q": clean_text(question.get("q"), 175), "a": choices})
    groups = data.get("outcomes")
    if not isinstance(groups, list) or len(groups) != 2:
        raise HTTPException(422, "Choose a result for every combination.")
    for group in groups:
        if not isinstance(group, list) or len(group) != 3:
            raise HTTPException(422, "Choose a result for every combination.")
        for row in group:
            if not isinstance(row, list) or len(row) != 3 or any(not isinstance(slug, str) or slug not in archetypes for slug in row):
                raise HTTPException(422, "Each result must reference an existing archetype.")
    return {"questions": cleaned, "outcomes": groups}
