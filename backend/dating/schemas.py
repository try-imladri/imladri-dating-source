"""Bounded, explicit member inputs. Legacy profile data is never an implicit input."""
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from . import catalog


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Enrollment(Body):
    version: Literal["dating-enrollment-v1"]
    accepted: Literal[True]
    adult_confirmed: Literal[True]


class Profile(Body):
    display_name: str = Field(min_length=1, max_length=60)
    date_of_birth: date
    gender: Literal["woman", "man", "nonbinary", "another"]
    city: str = Field(min_length=2, max_length=80)
    bio: str = Field(default="", max_length=1200)
    intent: Literal["long_term", "life_partner", "figuring_out"] = "long_term"
    relationship_structure: Literal["monogamous", "nonmonogamous", "open_to_discuss"] = "monogamous"
    expected_revision: int | None = Field(default=None, ge=0)

    @field_validator("display_name", "city", "bio")
    @classmethod
    def safe_text(cls, value):
        if any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ValueError("Remove unsupported control characters.")
        return value


class PreferenceAnswer(Body):
    self: str | None = Field(default=None, max_length=160)
    answer: str | None = Field(default=None, max_length=160)
    acceptable: list[str] = Field(default_factory=list, max_length=16)
    importance: Literal["preference", "essential", "open"] = "preference"


class Preferences(Body):
    age_min: int = Field(default=18, ge=18, le=100)
    age_max: int = Field(default=100, ge=18, le=100)
    genders: list[Literal["woman", "man", "nonbinary", "another"]] = Field(default_factory=list, max_length=4)
    cities: list[str] = Field(default_factory=list, max_length=8)
    intents: list[Literal["long_term", "life_partner", "figuring_out"]] = Field(default_factory=list, max_length=3)
    relationship_structures: list[Literal["monogamous", "nonmonogamous", "open_to_discuss"]] = Field(default_factory=list, max_length=3)
    personality: dict[str, PreferenceAnswer] = Field(default_factory=dict, max_length=16)
    intimacy: dict[str, PreferenceAnswer] = Field(default_factory=dict, max_length=16)
    personality_matching: StrictBool = False
    intimacy_matching: StrictBool = False
    expected_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_preferences(self):
        if self.age_min > self.age_max:
            raise ValueError("Choose an ordered adult age range.")
        for scope in ("personality", "intimacy"):
            answers = getattr(self, scope)
            for qid, value in answers.items():
                q = catalog.QUESTION_MAP[scope].get(qid)
                if not q:
                    raise ValueError("This preference question is unavailable.")
                selected = value.self if scope == "personality" else value.answer
                if selected is not None and selected not in q["options"]:
                    raise ValueError("Choose an available answer.")
                if any(a not in q["options"] for a in value.acceptable):
                    raise ValueError("Choose available partner answers.")
                if scope == "personality" and value.answer is not None or scope == "intimacy" and value.self is not None:
                    raise ValueError("Use the answer field for this question type.")
        self.cities = list(dict.fromkeys(c.strip() for c in self.cities if c.strip()))
        if any(len(c) > 80 for c in self.cities):
            raise ValueError("City names must be shorter than 80 characters.")
        return self


class ProfileState(Body):
    state: Literal["active", "paused", "draft"]


class Interest(Body):
    profile_id: str = Field(min_length=1, max_length=80)
    choice: Literal["like", "pass"]


class Message(Body):
    text: str = Field(min_length=1, max_length=3000)
    request_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class Block(Body):
    profile_id: str = Field(min_length=1, max_length=80)


class Report(Block):
    match_id: str | None = Field(default=None, max_length=80)
    category: Literal["profile", "photo", "messages", "safety", "other"] = "other"
    text: str = Field(min_length=1, max_length=2400)
    message_ids: list[str] = Field(default_factory=list, max_length=10)


class SharedAnswer(Body):
    scope: Literal["personality", "intimacy"]
    question_id: str = Field(min_length=1, max_length=80)
    answer: str = Field(min_length=1, max_length=160)


class Sharing(Body):
    answers: list[SharedAnswer] = Field(default_factory=list, max_length=32)
    expected_revision: int | None = Field(default=None, ge=0)


class Feedback(Body):
    conversation_happened: StrictBool = False
    date_happened: StrictBool = False
    want_another: Literal["yes", "no", "unsure", "not_yet"] = "not_yet"
    note: str = Field(default="", max_length=1500)


class Confirm(Body):
    confirmed: Literal[True]


class Review(Body):
    decision: Literal["approve", "reject", "suspend", "restore"]
    note: str = Field(default="", max_length=1200)
    expected_revision: int | None = Field(default=None, ge=0)


class ReportReview(Body):
    status: Literal["reviewing", "resolved", "dismissed"]
    note: str = Field(default="", max_length=1200)


class Appeal(Body):
    text: str = Field(min_length=1, max_length=2400)


class LaunchSettings(Body):
    city: str = Field(default="Chicago", min_length=2, max_length=80)
    discovery_enabled: StrictBool = True
    accepting_profiles: StrictBool = True
    review_note: str = Field(default="", max_length=600)
