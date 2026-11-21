"""Private Your Type sessions: atomic actions, adaptive crowds and erasure.

The session CAS is authoritative. Immutable answer history and the same-document
pending journal retain each action; reporting/cache failures cannot lose a vote.
Each event includes its preceding state for exact linked undo, so session size
does not grow with lifetime crowd count. No AI call or message credit is used.
"""
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
import zlib
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid5, NAMESPACE_URL

from bson import BSON
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from pymongo import IndexModel, ReturnDocument
from pymongo.errors import DuplicateKeyError

try:
    from . import answer_context, answer_history, your_type_model as learner, your_type_legacy as legacy
    from .free_plan import has_personal_access, subscription_required
    from .research import age_band
    from .postgres_protocol import GatewayCommittedResultUnavailable
except ImportError:
    import answer_context, answer_history, your_type_model as learner, your_type_legacy as legacy
    from free_plan import has_personal_access, subscription_required
    from research import age_band
    from postgres_protocol import GatewayCommittedResultUnavailable

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "backend/seed/your_type_catalog.json"
V2_MANIFEST = ROOT / "backend/seed/your_type_catalog.v2.json"
V2_EDITION = "your-type-synthetic-adults-v2"
V2_MANIFEST_SHA256 = "2d0c1a68aa442845c5d6a5502a86ae57c6cae622ca2ebfc26c973195719dc6c8"
COLLECTION = "your_type_sessions"
ENGINE_VERSION = 3
RECOMMENDED_CROWDS = 20
REQUIRED_LIKES = learner.MIN_LIKES
CARDS = 12
RESERVE = 12
MAX_CHOICES = 64
MAX_SESSIONS = 100
RECENT_ATTEMPTS = 5
MAX_STATE_BYTES = 64 * 1024
MAX_PACKED_RAW_BYTES = 2 * MAX_STATE_BYTES + 4096
INDEXES = {COLLECTION: [IndexModel([("user_id", 1), ("created_at", -1), ("id", -1)], name="your_type_member_sessions"),
            IndexModel([("user_id", 1), ("slot", 1)], name="your_type_member_slot", unique=True,
                       partialFilterExpression={"slot": {"$exists": True}})],
           "your_type_profiles": [IndexModel("user_id", name="your_type_profile_owner", unique=True)]}


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Start(Input):
    expected_member: str = Field(min_length=1, max_length=200)
    request_id: str = Field(max_length=36)
    audience: Literal["women", "men", "both"]
    sexuality: Literal["straight", "bi", "gay", "lesbian"] | None = None
    viewer_gender: Literal["Woman", "Man", "Non-binary", "Another gender", "Prefer not to say"] | None = None
    use_saved_profile: StrictBool = False
    # Retained only for exact retries from older clients. Current eligibility
    # comes from the saved profile, so starting does not invent a new attestation.
    adult_confirmed: StrictBool | None = None
    retake_of: str | None = Field(default=None, max_length=36)

    @field_validator("request_id", "retake_of")
    @classmethod
    def valid_uuid(cls, value):
        if value is not None and str(UUID(value)) != value:
            raise ValueError("Use a canonical UUID.")
        return value


class Action(Input):
    action_id: str = Field(max_length=36)
    expected_revision: int = Field(ge=0, lt=2**53)
    kind: Literal["like", "pass", "next_crowd", "undo", "pause", "resume", "finish"]
    face_id: str | None = Field(default=None, max_length=80)
    viewed_face_ids: list[str] = Field(default_factory=list, max_length=CARDS)
    dwell_ms: dict[str, int] = Field(default_factory=dict, max_length=CARDS)

    @field_validator("action_id")
    @classmethod
    def valid_uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("Use a canonical UUID.")
        return value

    @field_validator("dwell_ms")
    @classmethod
    def dwell_bounds(cls, value):
        if any(not isinstance(k, str) or len(k) > 80 or type(v) is not int or not 0 <= v <= 3_600_000 for k, v in value.items()):
            raise ValueError("Invalid bounded dwell declaration.")
        return value


class BatchAction(Input):
    action_id: str = Field(max_length=36)
    kind: Literal["like", "pass", "next_crowd"]
    face_id: str | None = Field(default=None, max_length=80)
    viewed_face_ids: list[str] = Field(default_factory=list, max_length=CARDS)
    dwell_ms: dict[str, int] = Field(default_factory=dict, max_length=CARDS)

    @field_validator("action_id")
    @classmethod
    def valid_uuid(cls, value):
        return Action.valid_uuid(value)

    @field_validator("dwell_ms")
    @classmethod
    def valid_dwell(cls, value):
        return Action.dwell_bounds(value)


class Batch(Input):
    batch_id: str = Field(max_length=36)
    expected_revision: int = Field(ge=0, lt=2**53)
    actions: list[BatchAction] = Field(min_length=1, max_length=16)

    @field_validator("batch_id")
    @classmethod
    def valid_uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("Use a canonical UUID.")
        return value


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def start_request(body):
    """Retain the exact pre-preferences request hash for frozen legacy retries."""
    value = body.model_dump()
    if not body.use_saved_profile:
        value.pop("use_saved_profile")
    if body.adult_confirmed is None:
        value.pop("adult_confirmed")
    if body.sexuality is None and body.viewer_gender is None:
        value.pop("sexuality")
        value.pop("viewer_gender")
    return value


def preference_snapshot(body, member):
    """Only explicit self-report can label an attempt; portrait choices cannot."""
    if body.use_saved_profile:
        profile = ((member.get("about") or {}).get("demographics") or {})
        sexuality = profile.get("sexuality")
        if sexuality not in ("straight", "bi", "gay", "lesbian"):
            sexuality = None
        if body.sexuality != sexuality:
            raise HTTPException(409, "Your profile sexuality changed. Reload before starting Your Type.")
    if body.sexuality is None:
        if body.viewer_gender is not None or body.audience == "both":
            raise HTTPException(422, "Choose your sexuality and the image audience for this attempt.")
        return None
    saved = ((member.get("about") or {}).get("demographics") or {}).get("gender")
    if saved not in ("Woman", "Man", "Non-binary", "Another gender", "Prefer not to say"):
        saved = None
    if body.viewer_gender != saved:
        raise HTTPException(409, "Your profile gender changed. Reload before starting Your Type.")
    expected = "both" if body.sexuality == "bi" else "women" if body.sexuality == "lesbian" else None
    if body.sexuality in ("straight", "gay") and saved in ("Woman", "Man"):
        same = "women" if saved == "Woman" else "men"
        expected = same if body.sexuality == "gay" else "men" if same == "women" else "women"
    if expected is not None and body.audience != expected:
        raise HTTPException(422, "The image audience does not match your explicit preferences.")
    return {"version": "your-type-preferences-v1", "sexuality": body.sexuality,
            "viewer_gender": saved, "rated_audience": body.audience}


def audience_assets(bank, audience):
    return [asset for asset in bank["assets"] if audience == "both" or asset["audience"] == audience]


def consent(member):
    return {**answer_history.consent(member), **{key: member[key] for key in
        ("your_type_research_version", "your_type_research_revision", "your_type_research_at") if key in member}}


def unavailable():
    return HTTPException(503, "The original portrait bank is not ready. Please try again later.")


@lru_cache(maxsize=4)
def _load_catalog(path, file_stamp):
    source = Path(path)
    if source.stat().st_size > 4 * 1024 * 1024:
        raise unavailable()
    raw = source.read_bytes()
    value = json.loads(raw)
    features = value.get("feature_names")
    assets = value.get("assets")
    groups = value.get("feature_groups", [])
    if (value.get("schema_version") != 1 or not isinstance(value.get("edition"), str)
            or not isinstance(features, list) or not 1 <= len(features) <= 192
            or len(set(features)) != len(features) or any(not isinstance(f, str) for f in features)
            or not isinstance(assets, list) or not 24 <= len(assets) <= 1024):
        raise unavailable()
    ids = set()
    for item in assets:
        identity = item.get("id")
        vector = item.get("features")
        masks = item.get("feature_masks")
        if (not isinstance(identity, str) or not 1 <= len(identity) <= 80 or identity in ids
                or item.get("audience") not in ("men", "women")
                or item.get("adult_reviewed") is not True or item.get("active") is not True
                or not isinstance(item.get("url"), str) or not item["url"].startswith("/your-type/")
                or not isinstance(vector, list) or len(vector) != len(features)
                or any(type(v) not in (int, float) or not math.isfinite(v) or not -1 <= v <= 1 for v in vector)
                or not isinstance(masks, list) or len(masks) != len(features) or any(type(m) is not bool for m in masks)):
            raise unavailable()
        if item.get("image_kind", "portrait") not in ("portrait", "full_body"):
            raise unavailable()
        if "display_fit" in item and item["display_fit"] not in ("contain", "cover"):
            raise unavailable()
        ids.add(identity)
    used = set()
    for group in groups:
        indices = group.get("indices")
        if (not isinstance(group.get("id"), str) or not isinstance(indices, list) or not indices
                or any(type(i) is not int or not 0 <= i < len(features) or i in used for i in indices)
                or len(set(indices)) != len(indices) or len(group.get("values", [])) != len(indices)
                or any(not isinstance(v, str) for v in group.get("values", []))):
            raise unavailable()
        audiences = group.get("applicable_audiences", ["men", "women"])
        if (not isinstance(audiences, list) or not audiences or len(set(audiences)) != len(audiences)
                or any(audience not in ("men", "women") for audience in audiences)):
            raise unavailable()
        used.update(indices)
    return {**value, "sha256": hashlib.sha256(raw).hexdigest(), "by_id": {a["id"]: a for a in assets},
            "position": {a["id"]: i for i, a in enumerate(assets)}}


def catalog():
    try:
        return _load_catalog(str(MANIFEST), MANIFEST.stat().st_mtime_ns)
    except (OSError, ValueError, TypeError, KeyError):
        raise unavailable() from None


def catalog_v2():
    """The exact shipped v2 image bank remains usable by existing attempts."""
    try:
        bank = _load_catalog(str(V2_MANIFEST), V2_MANIFEST.stat().st_mtime_ns)
        if bank["edition"] != V2_EDITION or bank["sha256"] != V2_MANIFEST_SHA256:
            raise unavailable()
        return bank
    except (OSError, ValueError, TypeError, KeyError):
        raise unavailable() from None


def engine_version(bank):
    return 2 if bank["edition"] == V2_EDITION else ENGINE_VERSION


def public_face(item):
    result = {key: deepcopy(item[key]) for key in ("id", "audience", "url", "sprite", "rect", "width", "height", "sheet_width", "sheet_height", "authored_age_band", "presentation", "image_kind", "display_fit") if key in item}
    result.setdefault("image_kind", "portrait")
    if "display_fit" in result and result["display_fit"] not in ("contain", "cover"):
        raise unavailable()
    sprite = result.get("sprite")
    if sprite:
        rect = sprite.get("rect") or {}
        x, y = sprite.get("x", rect.get("x")), sprite.get("y", rect.get("y"))
        width, height = sprite.get("width", rect.get("w")), sprite.get("height", rect.get("h"))
        sw, sh = sprite.get("source_width", sprite.get("sheet_width")), sprite.get("source_height", sprite.get("sheet_height"))
        if (any(type(value) is not int for value in (x, y, width, height, sw, sh))
                or x < 0 or y < 0 or min(width, height, sw, sh) <= 0 or x + width > sw or y + height > sh):
            raise unavailable()
        sprite.update(x=x, y=y, width=width, height=height, source_width=sw, source_height=sh,
                      sheet_width=sw, sheet_height=sh, rect={"x": x, "y": y, "w": width, "h": height})
        sprite.setdefault("url", result["url"])
    return result


def pack_states(before, after):
    raw = BSON.encode({"before": before, "after": after})
    if len(raw) > MAX_PACKED_RAW_BYTES:
        raise HTTPException(413, "This Your Type history state is too large to save safely.")
    return {"codec": "bson-zlib-v1", "raw_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "payload": zlib.compress(raw, level=6)}


def event_states(payload):
    """Bound decompression before allocating; reject truncation or trailing data."""
    if "states_packed" not in payload:
        before, after = payload.get("before_state"), payload.get("after_state")
        if not isinstance(before, dict) or not isinstance(after, dict) or len(BSON.encode({"before": before, "after": after})) > MAX_PACKED_RAW_BYTES:
            raise HTTPException(503, "Your Type history could not be verified. Please retry shortly.")
        return deepcopy(before), deepcopy(after)
    packed = payload["states_packed"]
    try:
        if (set(packed) != {"codec", "raw_bytes", "sha256", "payload"} or packed["codec"] != "bson-zlib-v1"
                or type(packed["raw_bytes"]) is not int or not 0 < packed["raw_bytes"] <= MAX_PACKED_RAW_BYTES
                or not isinstance(packed["payload"], bytes) or len(packed["payload"]) > MAX_PACKED_RAW_BYTES + 1024):
            raise ValueError()
        inflater = zlib.decompressobj()
        raw = inflater.decompress(packed["payload"], MAX_PACKED_RAW_BYTES + 1)
        if (len(raw) != packed["raw_bytes"] or len(raw) > MAX_PACKED_RAW_BYTES or not inflater.eof
                or inflater.unused_data or inflater.unconsumed_tail or hashlib.sha256(raw).hexdigest() != packed["sha256"]):
            raise ValueError()
        values = BSON(raw).decode()
        if (set(values) != {"before", "after"} or any(not isinstance(v, dict) for v in values.values())
                or BSON.encode(values) != raw):
            raise ValueError()
        return values["before"], values["after"]
    except Exception:
        raise HTTPException(503, "Your Type history could not be verified. Please retry shortly.") from None


def expand_event(value):
    """Owner-only history/export presentation; research never receives models."""
    result = deepcopy(value)
    payload = result.get("after") or {}
    if result.get("domain") == "your_type" and "states_packed" in payload:
        payload["before_state"], payload["after_state"] = event_states(payload)
        del payload["states_packed"]
    return result


async def eligible(db, uid, *, paid=True):
    member = await db.users.find_one({"user_id": uid, "closing": {"$ne": True}})
    if member:
        try:
            from . import workspace_access
        except ImportError:
            import workspace_access
        await workspace_access.require_individual(db, member)
    age = ((member or {}).get("about") or {}).get("demographics", {}).get("age")
    if member is None or age in (None, ""):
        raise HTTPException(403, "Your Type is available to members aged 18 and over. Confirm your age in your profile.")
    age_band(member)
    if paid and not has_personal_access(member):
        raise subscription_required()
    return member


async def account_owner(db, uid):
    """Permit management of own metadata after age or subscription is cleared."""
    member = await db.users.find_one({"user_id": uid, "closing": {"$ne": True}}, {"user_id": 1})
    if member is None:
        raise HTTPException(403, "This account is closed or changing. Reload before continuing.")
    return member


def conflict():
    return HTTPException(409, "This Your Type session changed. Refresh before trying again.")


def compatible(row, bank):
    if (row.get("engine_version") != engine_version(bank) or row.get("asset_edition") != bank["edition"]
            or row.get("asset_manifest_sha256") != bank["sha256"] or row.get("model_edition") != learner.EDITION):
        raise HTTPException(412, "This session uses an earlier portrait or model edition. Start a fresh session; your history is retained.")
    state = row.get("state") or {}
    dimensions = len(bank["feature_names"])
    audiences = set(selected_audiences(row.get("audience")))
    models = state.get("models_by_audience") or {}
    if (audiences - {"men", "women"} or set(models) != audiences
            or set(state.get("prospective_by_audience") or {}) != audiences
            or set(state.get("completed_crowds_by_audience") or {}) != audiences
            or "weights" in (state.get("model") or {})):
        raise HTTPException(412, "This session does not contain independent models for its recorded audiences.")
    for model in models.values():
        if any(not isinstance(model.get(key), list) or len(model[key]) != size
               for key, size in (("weights", dimensions + 1), ("precision", dimensions + 1),
                                 ("feature_observations", dimensions), ("feature_option_counts", dimensions))):
            raise HTTPException(412, "This session's model dimensions do not match its recorded catalog.")


def is_legacy(row):
    return row.get("model_edition") == learner.LEGACY_EDITION


def catalog_for_session(row):
    """Dispatch by recorded edition, never migrate weights or old predictions."""
    if is_legacy(row):
        if row.get("engine_version", 1) != 1:
            raise HTTPException(412, "The recorded Your Type engine does not match its model edition.")
        bank = legacy.catalog()
        legacy.compatible(row, bank)
        return bank
    bank = catalog_v2() if row.get("asset_edition") == V2_EDITION else catalog()
    compatible(row, bank)
    return bank


def legacy_response(value):
    return {**value, "engine_version": 1, "legacy_combined_profile": value.get("audience") == "both"}


def selected_audiences(audience):
    return ("men", "women") if audience == "both" else (audience,)


def aggregate_models(state):
    """Compatibility counters only: there is deliberately no combined vector."""
    fields = ("samples", "likes", "explicit_passes", "viewed_passes", "weighted_samples")
    state["model"] = {key: sum(model[key] for model in state["models_by_audience"].values()) for key in fields}
    state["prospective"] = {key: sum(value[key] for value in state["prospective_by_audience"].values())
                            for key in ("correct", "ties", "pairs", "explicit_pairs", "viewed_pairs")}


def audience_progress(state, audience):
    result = {}
    for group in selected_audiences(audience):
        model = state["models_by_audience"][group]
        pending = [choice for choice in state["choices"] if choice["audience"] == group]
        likes = model["likes"] + sum(choice["choice"] == "like" for choice in pending)
        negatives = model["explicit_passes"] + model["viewed_passes"]
        crowds = state["completed_crowds_by_audience"][group]
        result[group] = {"likes": likes, "committed_likes": model["likes"], "negative_examples": negatives,
            "completed_crowds": crowds, "minimum_crowds": RECOMMENDED_CROWDS,
            "minimum_likes": REQUIRED_LIKES, "minimum_negative_examples": 3,
            "results_available": crowds >= RECOMMENDED_CROWDS and model["likes"] >= REQUIRED_LIKES and negatives >= 3}
    return result


def issue_v2(state, bank, audience, seed, count):
    pool = audience_assets(bank, audience)
    used = set(state["seen"])
    occupied = {card["face_id"] for card in state["cards"] + state["replacement_queue"]}
    result = []
    for offset in range(count):
        choices = [a for a in pool if bank["position"][a["id"]] not in used and a["id"] not in occupied]
        if not choices:
            used = {bank["position"][identity] for identity in occupied}
            state["catalog_cycle"] += 1
            choices = [a for a in pool if a["id"] not in occupied]
        if not choices:
            break
        if audience == "both":
            # Keep the two presentation groups balanced where unseen candidates
            # allow it; preference/uncertainty sampling still chooses each face.
            counts = {group: sum(bank["by_id"][identity]["audience"] == group for identity in occupied)
                      for group in ("women", "men")}
            available = {asset["audience"] for asset in choices}
            group = min(available, key=lambda value: (counts[value], value))
            choices = [asset for asset in choices if asset["audience"] == group]
        group = choices[0]["audience"]
        # Give scarce body-cue assets actual exposure instead of waiting for
        # uniform sampling. The quota is independent within each audience.
        body_slot = state["issued_by_audience"][group] % 4 == 3
        desired_kind = "full_body" if body_slot else "portrait"
        coverage_choices = [asset for asset in choices if asset.get("image_kind", "portrait") == desired_kind]
        if coverage_choices:
            choices = coverage_choices
        selected = learner.sample(state["models_by_audience"][group], choices, 1, seed + ":" + str(offset), bank.get("feature_groups"),
            start_index=state.get("issued_count", 0) + offset,
            existing=[bank["by_id"][identity] for identity in sorted(occupied) if bank["by_id"][identity]["audience"] == group])[0]
        selected["audience"] = group
        selected["image_kind"] = bank["by_id"][selected["face_id"]].get("image_kind", "portrait")
        selected["coverage_sampling"] = desired_kind if coverage_choices else "available_catalog"
        state["issued_by_audience"][group] += 1
        selected["catalog_cycle"] = state["catalog_cycle"]
        used.add(bank["position"][selected["face_id"]])
        occupied.add(selected["face_id"])
        result.append(selected)
    state["seen"] = sorted(used)
    state["issued_count"] = state.get("issued_count", 0) + len(result)
    return result


def visible_shade(asset, groups):
    """Use reviewed visible color only, never an ethnicity inference."""
    group = next((item for item in groups if item["id"] == "skin_tone"), None)
    if not group:
        return None
    indices = group["indices"]
    if not all(asset["feature_masks"][i] for i in indices):
        return None
    positive = [value for i, value in zip(indices, group["values"]) if asset["features"][i] == 1]
    return positive[0] if len(positive) == 1 else None


def issue(state, bank, audience, seed, count):
    if engine_version(bank) == 2:
        return issue_v2(state, bank, audience, seed, count)
    pool = audience_assets(bank, audience)
    used = set(state["seen"])
    occupied = {card["face_id"] for card in state["cards"] + state["replacement_queue"]}
    groups = bank.get("feature_groups", [])
    recent = state.get("recent_exposure_counts", [])
    result = []
    for offset in range(count):
        choices = [a for a in pool if bank["position"][a["id"]] not in used and a["id"] not in occupied]
        if not choices:
            used = {bank["position"][identity] for identity in occupied}
            state["catalog_cycle"] += 1
            choices = [a for a in pool if a["id"] not in occupied]
        if not choices:
            break
        if audience == "both":
            counts = {group: sum(bank["by_id"][identity]["audience"] == group for identity in occupied)
                      for group in ("men", "women")}
            group = min({a["audience"] for a in choices}, key=lambda value: (counts[value], value))
            choices = [a for a in choices if a["audience"] == group]
        group = choices[0]["audience"]
        # Each audience receives every sampling phase. A global counter would
        # assign only even phases to men and odd phases to women in mixed crowds.
        phase = state["issued_by_audience"][group]
        if len(recent) == len(bank["assets"]):
            least_exposed = min(recent[bank["position"][a["id"]]] for a in choices)
            choices = [a for a in choices if recent[bank["position"][a["id"]]] == least_exposed]
        previous = [bank["by_id"][identity] for identity in sorted(occupied)
                    if bank["by_id"][identity]["audience"] == group]
        # Exploration remains broad even after strong preferences develop. Only
        # these two discovery phases are balanced; fitted scores and rankings
        # are never rewritten to impose a demographic distribution.
        if phase % 4 in (0, 3):
            available = {visible_shade(a, groups) for a in choices} - {None}
            if available:
                counts = {value: sum(visible_shade(a, groups) == value for a in previous) for value in available}
                lowest = min(counts.values())
                wanted = {value for value, n in counts.items() if n == lowest}
                choices = [a for a in choices if visible_shade(a, groups) in wanted]
        selected = learner.sample(state["models_by_audience"][group], choices, 1,
            seed + ":" + str(offset), groups, start_index=phase, existing=previous)[0]
        asset = bank["by_id"][selected["face_id"]]
        selected.update(audience=group, image_kind=asset.get("image_kind", "portrait"),
            coverage_sampling="visible_shade_exploration" if phase % 4 in (0, 3) else "adaptive_preference",
            catalog_cycle=state["catalog_cycle"])
        state["issued_by_audience"][group] += 1
        used.add(bank["position"][asset["id"]])
        occupied.add(asset["id"])
        result.append(selected)
    state["seen"] = sorted(used)
    state["issued_count"] = state.get("issued_count", 0) + len(result)
    return result


async def recent_exposure(db, uid, bank):
    """Bounded owner-only issuance history; no preferences/models are inherited.

    Reserved faces count conservatively as exposed. Deleted attempts and other
    catalog editions are excluded; a retained snapshot makes retries/undo exact.
    """
    rows = await db[COLLECTION].find({"user_id": uid, "deleted": {"$ne": True},
        "status": {"$nin": ["deleted", "clearing"]}, "asset_edition": bank["edition"],
        "asset_manifest_sha256": bank["sha256"]}, {"state.seen": 1}).sort(
            [("updated_at", -1), ("id", -1)]).limit(RECENT_ATTEMPTS).to_list(RECENT_ATTEMPTS)
    counts = [0] * len(bank["assets"])
    for row in rows:
        for position in set(row.get("state", {}).get("seen", [])):
            if type(position) is int and 0 <= position < len(counts):
                counts[position] += 1
    return counts, len(rows)


def new_state(bank, audience="both"):
    audiences = selected_audiences(audience)
    state = {"models_by_audience": {group: learner.initial(len(bank["feature_names"])) for group in audiences},
            "prospective_by_audience": {group: {"correct": 0, "ties": 0, "pairs": 0, "explicit_pairs": 0, "viewed_pairs": 0} for group in audiences},
            "completed_crowds_by_audience": {group: 0 for group in audiences}, "cards": [], "replacement_queue": [],
            "issued_by_audience": {group: 0 for group in audiences},
            "choices": [], "declared_views": {}, "seen": [], "catalog_cycle": 1, "issued_count": 0, "crowd": 1,
            "completed_crowds": 0, "undo_head": None, "picked_bits": "0", "passed_bits": "0",
            "prospective": {"correct": 0, "ties": 0, "pairs": 0, "explicit_pairs": 0, "viewed_pairs": 0}}
    aggregate_models(state)
    return state


def fill(state, bank, audience, seed, *, new_crowd=False):
    if new_crowd:
        state["cards"], state["replacement_queue"] = [], []
    state["cards"].extend(issue(state, bank, audience, seed + ":cards", CARDS - len(state["cards"])))
    state["replacement_queue"].extend(issue(state, bank, audience, seed + ":queue", RESERVE - len(state["replacement_queue"])))


def dto(row, bank, *, duplicate=False, accepted_revision=None):
    state = row["state"]
    def card(item):
        return {**public_face(bank["by_id"][item["face_id"]]), "face_id": item["face_id"], "sampling_reason": item["reason"]}
    return {"id": row["id"], "attempt_id": row["attempt_id"], "revision": row["revision"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "audience": row["audience"], "preference_snapshot": deepcopy(row.get("preference_snapshot")),
            "status": row["status"], "asset_edition": row["asset_edition"],
            "model_edition": row["model_edition"], "asset_manifest_sha256": row["asset_manifest_sha256"],
            "crowd": state["crowd"], "completed_crowds": state["completed_crowds"],
            "recommended_crowds": RECOMMENDED_CROWDS, "cards": [card(i) for i in state["cards"]],
            "next_milestone_crowds": 20 if state["completed_crowds"] < 20 else 50 if state["completed_crowds"] < 50 else ((state["completed_crowds"] // 50) + 1) * 50,
            "replacement_queue": [card(i) for i in state["replacement_queue"]],
            "likes": state["model"]["likes"] + sum(c["choice"] == "like" for c in state["choices"]),
            "committed_likes": state["model"]["likes"], "required_likes": REQUIRED_LIKES,
            "likes_remaining": max(0, REQUIRED_LIKES - state["model"]["likes"] - sum(c["choice"] == "like" for c in state["choices"])),
            "results_available": any(item["results_available"] for item in audience_progress(state, row["audience"]).values()),
            "audience_progress": audience_progress(state, row["audience"]), "engine_version": row["engine_version"],
            "undo_available": bool(state["undo_head"]), "current_crowd_choices": len(state["choices"]),
            "max_choices_per_crowd": MAX_CHOICES, "duplicate": duplicate,
            **({"accepted_revision": accepted_revision} if accepted_revision is not None else {})}


def action_event(row, member, body, before, after, effects, demographics):
    observation = answer_history.event(source=COLLECTION, source_id=row["id"], owner_id=row["user_id"],
        domain="your_type", question_id=body.action_id, after={"action_id": body.action_id,
        "request_hash": digest(body.model_dump()), "request": body.model_dump(), "states_packed": pack_states(before, after),
        "effects": effects, "before_status": row["status"],
        "preference_snapshot": deepcopy(row.get("preference_snapshot")),
        "asset_edition": row["asset_edition"], "asset_manifest_sha256": row["asset_manifest_sha256"],
        "model_edition": row["model_edition"]}, question={"kind": body.kind},
        signature=digest([row["asset_manifest_sha256"], row["model_edition"]]),
        demographics=demographics, consent=consent(member), revision=row["revision"] + 1,
        attempt_id=row["attempt_id"], kind="your_type." + body.kind)
    observation["id"] = event_id(row["id"], body.action_id)
    return observation


def event_id(sid, action_id):
    return hashlib.sha256(("your-type:" + sid + ":" + action_id).encode()).hexdigest()


async def lookup_event(db, row, identity):
    value = next((item for item in row.get(answer_history.PENDING, []) if item["id"] == identity), None)
    if value is None:
        value = await db.answer_history.find_one({"_id": identity, "source": COLLECTION,
            "source_id": row["id"], "owner_id": row["user_id"], "purged": {"$ne": True}})
    return value


async def owned(db, uid, sid):
    try:
        if str(UUID(sid)) != sid:
            raise ValueError()
    except ValueError:
        raise HTTPException(404, "Your Type session not found.") from None
    row = await db[COLLECTION].find_one({"_id": sid, "user_id": uid, "deleted": {"$ne": True}})
    if row is None:
        raise HTTPException(404, "Your Type session not found.")
    return row


async def start(db, uid, body):
    if body.expected_member != uid:
        raise HTTPException(409, "Your signed-in account changed. Reload before starting Your Type.")
    member = await eligible(db, uid)
    bank = catalog()
    if len(audience_assets(bank, body.audience)) < CARDS + RESERVE:
        raise unavailable()
    sid = str(uuid5(NAMESPACE_URL, "imladri/your-type/" + uid + "/" + body.request_id))
    prior = await db[COLLECTION].find_one({"_id": sid})
    request = start_request(body)
    request_hash = digest(request)
    if prior:
        if is_legacy(prior):
            catalog_for_session(prior)
            if body.use_saved_profile or body.adult_confirmed is None:
                raise conflict()
            # Frozen v1 request fingerprints must never acquire a newer
            # client's default fields when an accepted request is retried.
            legacy_body = legacy.Start.model_validate(body.model_dump(exclude={"use_saved_profile"}))
            return legacy_response(await legacy.start(db, uid, legacy_body))
        if prior.get("deleted"):
            raise HTTPException(410, "This session was removed. Start a new attempt.")
        if prior.get("start_request_hash") != request_hash:
            raise conflict()
        prior_bank = catalog_for_session(prior)
        return dto(prior, prior_bank, duplicate=True, accepted_revision=0)
    preferences = preference_snapshot(body, member)
    allocated = await db[COLLECTION].find({"user_id": uid, "deleted": {"$ne": True}}, {"slot": 1}).limit(MAX_SESSIONS + 1).to_list(MAX_SESSIONS + 1)
    available = set(range(MAX_SESSIONS)) - {r.get("slot") for r in allocated}
    if len(allocated) >= MAX_SESSIONS or not available:
        raise HTTPException(409, "Remove an older session before starting another. You can keep continuing an existing session.")
    if body.retake_of:
        await owned(db, uid, body.retake_of)
    state = new_state(bank, body.audience)
    if engine_version(bank) >= 3:
        state["recent_exposure_counts"], state["recent_attempts_considered"] = await recent_exposure(db, uid, bank)
    fill(state, bank, body.audience, sid)
    row = {"_id": sid, "id": sid, "user_id": uid, "attempt_id": sid, "engine_version": engine_version(bank),
           "asset_edition": bank["edition"], "asset_manifest_sha256": bank["sha256"], "model_edition": learner.EDITION,
           "audience": body.audience, "preference_snapshot": preferences,
           "slot": min(available), "start_request_hash": request_hash, "retake_of": body.retake_of,
           "created_at": stamp(), "updated_at": stamp(), "revision": 0, "status": "active", "state": state,
           "recent_receipts": [], "owner_content_revision": member.get("content_revision", 0)}
    context = await answer_context.for_member(db, uid)
    current_member = await eligible(db, uid)
    if preference_snapshot(body, current_member) != preferences:
        raise conflict()
    event = answer_history.event(source=COLLECTION, source_id=sid, owner_id=uid, domain="your_type",
        question_id="session_started", after={"request": request, "state": state, "preference_snapshot": preferences,
            "asset_edition": bank["edition"], "asset_manifest_sha256": bank["sha256"], "model_edition": learner.EDITION},
        demographics=context, consent=consent(member), revision=0, attempt_id=sid,
        kind="your_type.retake_started" if body.retake_of else "your_type.session_started")
    update = answer_history.append({"$set": row}, event, {})
    for key, value in update["$set"].items():
        row[key] = value
    row[answer_history.REVISION] = 1
    row[answer_history.PENDING] = update["$push"][answer_history.PENDING]["$each"]
    try:
        await db[COLLECTION].insert_one(row)
    except DuplicateKeyError:
        return await start(db, uid, body)
    await postcheck(db, uid, row)
    await answer_history.flush(db, COLLECTION, {"_id": sid})
    return dto(row, bank)


async def postcheck(db, uid, row):
    try:
        member = await eligible(db, uid, paid=False)
        if member.get("content_revision", 0) != row.get("owner_content_revision", 0):
            raise HTTPException(409, "Your content changed. Refresh before continuing.")
    except HTTPException:
        await erase(db, uid, row["id"])
        raise


async def reduce_action(db, row, bank, body):
    identity = event_id(row["id"], body.action_id)
    if row["status"] != "active" and body.kind not in ("resume", "undo"):
        raise HTTPException(409, "Resume this session before making another choice.")
    state = deepcopy(row["state"])
    before = deepcopy(state)
    status = row["status"]
    faces = {item["face_id"]: item for item in state["cards"]}
    if len(set(body.viewed_face_ids)) != len(body.viewed_face_ids) or not set(body.viewed_face_ids).issubset(faces) or not set(body.dwell_ms).issubset(body.viewed_face_ids):
        raise HTTPException(422, "Declare views only for the current crowd's visible cards.")
    if body.kind not in ("like", "pass") and body.face_id is not None:
        raise HTTPException(422, "This action does not select a face.")
    for face in body.viewed_face_ids:
        state["declared_views"][face] = max(state["declared_views"].get(face, 0), body.dwell_ms.get(face, 0))
    effects = {"declared_views": list(body.viewed_face_ids), "dwell_ms": deepcopy(body.dwell_ms),
               "issued": [], "choices": [], "skipped": [], "undone_event_id": None}
    if body.kind in ("like", "pass"):
        if body.face_id not in faces or body.face_id not in body.viewed_face_ids:
            raise HTTPException(422, "Choose a currently visible face you viewed.")
        if len(state["choices"]) >= MAX_CHOICES:
            raise HTTPException(409, "Continue to the next crowd before choosing more faces.")
        item = faces[body.face_id]
        choice = {"face_id": body.face_id, "audience": bank["by_id"][body.face_id]["audience"],
                  "choice": body.kind, "prediction": item["score"],
                  "weight": 1.0, "catalog_cycle": item["catalog_cycle"], "dwell_ms": body.dwell_ms.get(body.face_id, 0)}
        state["choices"].append(choice)
        bitfield = "picked_bits" if body.kind == "like" else "passed_bits"
        state[bitfield] = format(int(state[bitfield], 16) | (1 << bank["position"][body.face_id]), "x")
        effects["choices"].append(choice)
        state["declared_views"].pop(body.face_id, None)
        slot = next(i for i, c in enumerate(state["cards"]) if c["face_id"] == body.face_id)
        replacement = state["replacement_queue"].pop(0)
        state["cards"][slot] = replacement
        effects["issued"] = [replacement]
        fill(state, bank, row["audience"], identity)
    elif body.kind == "next_crowd":
        # This explicit control means: pass the remaining faces you viewed.
        for item in state["cards"]:
            face = item["face_id"]
            if face in state["declared_views"]:
                choice = {"face_id": face, "audience": bank["by_id"][face]["audience"],
                          "choice": "viewed_pass", "prediction": item["score"],
                          "weight": learner.WEAK_PASS_WEIGHT, "catalog_cycle": item["catalog_cycle"],
                          "dwell_ms": state["declared_views"][face]}
                state["choices"].append(choice)
                effects["choices"].append(choice)
            else:
                effects["skipped"].append(face)
        evaluations = {}
        for audience, model in state["models_by_audience"].items():
            choices = [choice for choice in state["choices"] if choice["audience"] == audience]
            state["models_by_audience"][audience] = learner.train(model, choices, bank["by_id"], bank.get("feature_groups"))
            evaluation = learner.prospective(choices)
            for key, value in evaluation.items():
                state["prospective_by_audience"][audience][key] += value
            state["completed_crowds_by_audience"][audience] += int(bool(choices))
            evaluations[audience] = evaluation
        aggregate_models(state)
        effects["prospective_by_audience"] = evaluations
        effects["prospective"] = {key: sum(value[key] for value in evaluations.values()) for key in state["prospective"]}
        effects["committed_choices"] = deepcopy(state["choices"])
        state["completed_crowds"] += 1
        state["crowd"] += 1
        state["choices"], state["declared_views"] = [], {}
        fill(state, bank, row["audience"], identity, new_crowd=True)
        effects["issued"] = deepcopy(state["cards"])
    elif body.kind == "undo":
        previous = await lookup_event(db, row, state["undo_head"]) if state["undo_head"] else None
        if not previous or previous.get("generation", 0) != row.get(answer_history.GENERATION, 0):
            raise HTTPException(409, "There is no retained action to undo.")
        effects["undone_event_id"] = previous["id"]
        status = previous["after"]["before_status"]
        state, _ = event_states(previous["after"])
    elif body.kind == "pause":
        status = "paused"
    elif body.kind == "resume":
        status = "active"
    elif body.kind == "finish":
        if state["completed_crowds"] < RECOMMENDED_CROWDS or state["model"]["likes"] < REQUIRED_LIKES:
            raise HTTPException(409, "Complete 20 crowds and commit at least 30 likes before opening your results. You can pause at any time.")
        status = "finished"
    if body.kind != "undo":
        state["undo_head"] = identity
    if len(BSON.encode(state)) > MAX_STATE_BYTES:
        raise HTTPException(409, "Continue to the next crowd before making more choices.")
    return before, state, status, effects


async def act(db, uid, sid, body):
    member = await eligible(db, uid)
    await answer_history.flush(db, COLLECTION, {"_id": sid, "user_id": uid})
    row = await owned(db, uid, sid)
    if is_legacy(row):
        catalog_for_session(row)
        return legacy_response(await legacy.act(db, uid, sid, body))
    bank = catalog_for_session(row)
    identity = event_id(sid, body.action_id)
    prior = next((r for r in row.get("recent_receipts", []) if r["id"] == body.action_id), None)
    if prior is None:
        archived = await lookup_event(db, row, identity)
        if archived:
            prior = {"body_sha256": archived["after"]["request_hash"], "revision": archived["revision"]}
    if prior:
        if prior["body_sha256"] != digest(body.model_dump()):
            raise conflict()
        return dto(row, bank, duplicate=True, accepted_revision=prior["revision"])
    if row["revision"] != body.expected_revision:
        raise conflict()
    before, state, status, effects = await reduce_action(db, row, bank, body)
    context = await answer_context.for_member(db, uid)
    # Recheck AFTER source generation/context reads; old writes are fenced by CAS.
    member = await eligible(db, uid)
    observation = action_event(row, member, body, before, state, effects, context)
    receipt = {"id": body.action_id, "body_sha256": digest(body.model_dump()), "revision": row["revision"] + 1}
    update = answer_history.append({"$set": {"state": state, "status": status, "updated_at": stamp(),
        "recent_receipts": [*row.get("recent_receipts", [])[-63:], receipt]}, "$inc": {"revision": 1}}, observation, row)
    try:
        updated = await db[COLLECTION].find_one_and_update(answer_history.guard({"_id": sid, "user_id": uid,
            "revision": body.expected_revision, "deleted": {"$ne": True}}, row), update, return_document=ReturnDocument.AFTER)
    except GatewayCommittedResultUnavailable:
        # The private RPC deliberately never caches document images. Reconcile
        # by the immutable action identity rather than replaying a new action.
        return await act(db, uid, sid, body)
    if updated is None:
        latest = await owned(db, uid, sid)
        if any(r["id"] == body.action_id for r in latest.get("recent_receipts", [])):
            return await act(db, uid, sid, body)
        raise conflict()
    await postcheck(db, uid, updated)
    await answer_history.flush(db, COLLECTION, {"_id": sid})
    return dto(updated, bank)


async def read(db, uid, sid):
    await eligible(db, uid)
    row = await owned(db, uid, sid)
    if is_legacy(row):
        catalog_for_session(row)
        return legacy_response(await legacy.read(db, uid, sid))
    bank = catalog_for_session(row)
    return dto(row, bank)


async def act_batch(db, uid, sid, body):
    """Validate an ordered burst entirely before ONE authoritative session CAS."""
    member = await eligible(db, uid)
    await answer_history.flush(db, COLLECTION, {"_id": sid, "user_id": uid})
    row = await owned(db, uid, sid)
    if is_legacy(row):
        catalog_for_session(row)
        return legacy_response(await legacy.act_batch(db, uid, sid, body))
    bank = catalog_for_session(row)
    batch_identity = event_id(sid, "batch:" + body.batch_id)
    commands = [Action(**item.model_dump(), expected_revision=body.expected_revision + i) for i, item in enumerate(body.actions)]
    if len({item.action_id for item in commands}) != len(commands):
        raise HTTPException(422, "Each action in a batch needs its own UUID.")
    if any(item.kind == "next_crowd" for item in commands[:-1]):
        raise HTTPException(422, "Next crowd can only be the last action in a batch.")
    identities = [event_id(sid, item.action_id) for item in commands]
    existing = {item["id"]: item for item in row.get(answer_history.PENDING, []) if item["id"] in [batch_identity, *identities]}
    async for item in db.answer_history.find({"_id": {"$in": [batch_identity, *identities]}, "owner_id": uid,
            "source": COLLECTION, "source_id": sid, "purged": {"$ne": True}}):
        existing[item["id"]] = item
    prior = existing.get(batch_identity)
    if prior:
        if prior["after"]["batch_hash"] != digest(body.model_dump()):
            raise conflict()
        return dto(row, bank, duplicate=True, accepted_revision=prior["revision"])
    if existing or row["revision"] != body.expected_revision:
        raise conflict()
    context = await answer_context.for_member(db, uid)
    member = await eligible(db, uid)
    working = deepcopy(row)
    observations, receipts = [], list(row.get("recent_receipts", []))
    for command in commands:
        before, state, status, effects = await reduce_action(db, working, bank, command)
        observation = action_event(working, member, command, before, state, effects, context)
        observation["after"]["batch_id"] = body.batch_id
        observations.append(observation)
        working.update(state=state, status=status, revision=working["revision"] + 1)
        receipts.append({"id": command.action_id, "body_sha256": digest(command.model_dump()), "revision": working["revision"]})
    marker = answer_history.event(source=COLLECTION, source_id=sid, owner_id=uid, domain="your_type",
        question_id="batch:" + body.batch_id, after={"batch_id": body.batch_id, "batch_hash": digest(body.model_dump()),
            "action_ids": [item.action_id for item in commands], "first_revision": row["revision"] + 1,
            "last_revision": working["revision"], "asset_edition": row["asset_edition"], "model_edition": row["model_edition"],
            "preference_snapshot": deepcopy(row.get("preference_snapshot"))},
        demographics=context, consent=consent(member), revision=working["revision"], attempt_id=row["attempt_id"], kind="your_type.batch_committed")
    marker["id"] = batch_identity
    observations.append(marker)
    # A final owner check follows reduction; no accepted partial batch exists.
    await eligible(db, uid)
    update = answer_history.append({"$set": {"state": working["state"], "status": working["status"],
        "updated_at": stamp(), "recent_receipts": receipts[-64:]}, "$inc": {"revision": len(commands)}}, observations, row)
    try:
        updated = await db[COLLECTION].find_one_and_update(answer_history.guard({"_id": sid, "user_id": uid,
            "revision": body.expected_revision, "deleted": {"$ne": True}}, row), update, return_document=ReturnDocument.AFTER)
    except GatewayCommittedResultUnavailable:
        return await act_batch(db, uid, sid, body)
    if updated is None:
        latest = await owned(db, uid, sid)
        duplicate = await lookup_event(db, latest, batch_identity)
        if duplicate and duplicate["after"]["batch_hash"] == digest(body.model_dump()):
            return dto(latest, bank, duplicate=True, accepted_revision=duplicate["revision"])
        raise conflict()
    await postcheck(db, uid, updated)
    await answer_history.flush(db, COLLECTION, {"_id": sid})
    return dto(updated, bank, accepted_revision=working["revision"])


async def results(db, uid, sid):
    await eligible(db, uid)
    row = await owned(db, uid, sid)
    bank = catalog_for_session(row)
    if is_legacy(row):
        result = legacy_response({**await legacy.results(db, uid, sid), "audience": row["audience"]})
        result["profiles_by_audience"] = {} if row["audience"] == "both" else {
            row["audience"]: {**deepcopy(result), "audience": row["audience"],
                "results_available": bool(result["rankings"]), "legacy_model": True,
                "minimum_likes": REQUIRED_LIKES, "minimum_negative_examples": 3}}
        result["profile_status"] = "legacy_combined" if row["audience"] == "both" else "legacy_single_audience"
        result["legacy_note"] = "Original catalog, predictions and model semantics retained. A new attempt uses independent audience profiles."
        return result
    if row["state"]["completed_crowds"] < RECOMMENDED_CROWDS or row["state"]["model"]["likes"] < REQUIRED_LIKES:
        raise HTTPException(409, "Complete 20 crowds and commit at least 30 likes to open these results.")
    state = row["state"]
    eligible_assets = audience_assets(bank, row["audience"])
    picked = [public_face(a) for a in eligible_assets if int(state["picked_bits"], 16) & (1 << bank["position"][a["id"]])]
    passed = [public_face(a) for a in eligible_assets if int(state["passed_bits"], 16) & (1 << bank["position"][a["id"]])]
    profiles = {}
    for audience in selected_audiences(row["audience"]):
        assets = audience_assets(bank, audience)
        result = learner.results(state["models_by_audience"][audience], assets,
            bank.get("feature_groups", []), state["prospective_by_audience"][audience],
            state["completed_crowds_by_audience"][audience], audience=audience)
        result["rankings"] = [{**item, **public_face(bank["by_id"][item["face_id"]])} for item in result["rankings"]]
        profiles[audience] = {**result, "audience": audience, "results_available": result["signal"] == "developing",
            "minimum_likes": REQUIRED_LIKES, "minimum_negative_examples": 3, "minimum_crowds": RECOMMENDED_CROWDS,
            "examples": result["rankings"][:12], "picked_photos": [item for item in picked if item["audience"] == audience],
            "explicitly_passed_photos": [item for item in passed if item["audience"] == audience]}
    available = any(profile["results_available"] for profile in profiles.values())
    evaluation = state["prospective"]
    result = {**state["model"], "completed_crowds": state["completed_crowds"],
        "rankings": [], "examples": [], "feature_preferences": [], "picked_photos": picked,
        "explicitly_passed_photos": passed, "signal": "developing" if available else "insufficient_signal",
        "results_available": available, "profiles_by_audience": profiles, "audience": row["audience"],
        "engine_version": row["engine_version"], "legacy_combined_profile": False,
        "confidence": {"kind": "independent_audience_models_not_validated_accuracy", "calibrated": False, "population_validated": False},
        "prospective_pairwise": {**evaluation,
            "accuracy": (evaluation["correct"] + .5 * evaluation["ties"]) / evaluation["pairs"] if evaluation["pairs"] else None,
            "independent_pairs": False, "cross_audience_pairs": False,
            "definition": "Sum of within-audience pre-feedback pairs only; pairs can share portraits."},
        "population": {"available": False, "reason": "No validated population baseline is published."}, "model_edition": learner.EDITION}
    if row["audience"] != "both":
        for key in ("rankings", "examples", "feature_preferences", "confidence", "population"):
            result[key] = deepcopy(profiles[row["audience"]][key])
    result["ranking_scope"] = {"audience": row["audience"], "eligible_originals": len(eligible_assets),
                               "personal_only": True, "population_popularity_available": False,
                               "cross_audience_scores_comparable": False, "profiles_separate_by_audience": True}
    return {**result, "session_id": sid, "source_revision": row["revision"], "asset_edition": bank["edition"],
            "preference_snapshot": deepcopy(row.get("preference_snapshot")),
            "asset_manifest_sha256": bank["sha256"], "generated_at": stamp(), "private": True,
            "interpretation": "Patterns in your choices among original synthetic adult portraits; not a personality or relationship compatibility score."}


async def profile(db, uid):
    """Derive from the latest authoritative session revision, never a stale cache."""
    await eligible(db, uid)
    rows = await db[COLLECTION].find({"user_id": uid, "deleted": {"$ne": True},
        "state.completed_crowds": {"$gte": RECOMMENDED_CROWDS}, "state.model.likes": {"$gte": REQUIRED_LIKES}}).sort([("updated_at", -1), ("id", -1)]).limit(1).to_list(1)
    if not rows:
        return {"available": False, "reason": "Complete 20 crowds and commit 30 likes in an attempt to build your private visual-preference profile."}
    result = await results(db, uid, rows[0]["id"])
    return {"available": result.get("results_available", bool(result.get("rankings"))), **result}


async def erase(db, uid, sid, revision=None):
    for _ in range(5):
        row = await db[COLLECTION].find_one({"_id": sid, "user_id": uid})
        if row is None:
            return
        if revision is not None and row.get("revision") != revision and not row.get("deleted"):
            raise conflict()
        if not row.get("deleted"):
            # Account/content erasure must also remove legacy or damaged rows.
            # Bind the actual old BSON value (including its absence), without
            # interpreting a malformed object as query operators or incrementing
            # a nonnumeric revision. Normal session writers remain fenced by
            # both this value and the answer-history generation/revision.
            old_revision = row.get("revision")
            query = {"_id": sid, "user_id": uid, "revision": {"$exists": "revision" in row}}
            if "revision" in row:
                query["$expr"] = {"$eq": ["$revision", {"$literal": old_revision}]}
            closed_revision = old_revision + 1 if isinstance(old_revision, int) and not isinstance(old_revision, bool) and 0 <= old_revision < 2**63-1 else 0
            change = answer_history.redact({"$set": {"deleted": True, "status": "deleted", "state": {},
                "recent_receipts": [], "revision": closed_revision}}, row, source=COLLECTION, source_id=sid)
            accepted = await db[COLLECTION].update_one(answer_history.guard(query, row), change)
            if not accepted.matched_count:
                continue
        await answer_history.flush(db, COLLECTION, {"_id": sid}, strict=True)
        await db.your_type_profiles.delete_many({"user_id": uid, "session_id": sid})
        # Retain only an opaque session retry fence, never choices or owner data.
        await db[COLLECTION].replace_one({"_id": sid, "deleted": True}, {"_id": sid, "deleted": True})
        return
    raise conflict()


async def clear_member(db, uid):
    """Caller holds the existing account/content-clear writer barrier."""
    from . import your_type_preferences
    await your_type_preferences.clear(db, uid)
    async for row in db[COLLECTION].find({"user_id": uid}, {"_id": 1}):
        await erase(db, uid, row["_id"])
    await db.your_type_profiles.delete_many({"user_id": uid})


async def own_export(db, uid):
    rows = []
    async for row in db[COLLECTION].find({"user_id": uid, "deleted": {"$ne": True}}).sort([("created_at", 1), ("id", 1)]):
        await answer_history.flush(db, COLLECTION, {"_id": row["_id"]}, strict=True)
        rows.append({key: deepcopy(value) for key, value in row.items() if key not in {
            "_id", "recent_receipts", "start_request_hash", "owner_content_revision", "slot", *answer_history.FIELDS}})
    return rows


def make_router(get_db, current_member, subscribed_member):
    router = APIRouter(prefix="/your-type", tags=["Your Type"])

    @router.get("")
    async def bootstrap(response: Response, member=Depends(subscribed_member)):
        response.headers["Cache-Control"] = "no-store"
        database = get_db()
        saved_member = await eligible(database, member["user_id"])
        gender = ((saved_member.get("about") or {}).get("demographics") or {}).get("gender")
        if gender not in ("Woman", "Man", "Non-binary", "Another gender", "Prefer not to say"):
            gender = None
        sexuality = ((saved_member.get("about") or {}).get("demographics") or {}).get("sexuality")
        if sexuality not in ("straight", "bi", "gay", "lesbian"):
            sexuality = None
        bank = catalog()
        sessions = await database[COLLECTION].find({"user_id": member["user_id"], "deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "audience": 1, "preference_snapshot": 1, "status": 1, "revision": 1, "updated_at": 1,
             "created_at": 1, "state.completed_crowds": 1, "asset_edition": 1, "model_edition": 1}
            ).sort([("created_at", -1), ("id", -1)]).limit(21).to_list(21)
        return {"edition": bank["edition"], "manifest_sha256": bank["sha256"], "model_edition": learner.EDITION,
                "audiences": [{"id": audience, "faces": len(audience_assets(bank, audience))} for audience in ("women", "men", "both")],
                "recommended_crowds": RECOMMENDED_CROWDS, "required_likes": REQUIRED_LIKES, "crowd_size": CARDS,
                "profile": {"adult_eligible": True, "gender": gender, "sexuality": sexuality},
                "sessions": sessions[:20], "history_next": sessions[19]["id"] if len(sessions) > 20 else None,
                "message_credits_per_action": 0, "retake_wait_days": 0, "private_by_default": True,
                "next_crowd_meaning": "Pass the remaining faces you viewed, then show the next crowd.",
                "research": "Optional Your Type research uses a separate permission. Demographic comparisons also require questionnaire research permission."}

    @router.post("/sessions")
    async def create(body: Start, response: Response, member=Depends(subscribed_member)):
        response.headers["Cache-Control"] = "no-store"
        return await start(get_db(), member["user_id"], body)

    @router.get("/history")
    async def history(response: Response, before: str | None = None, limit: int = Query(20, ge=1, le=50), member=Depends(current_member)):
        response.headers["Cache-Control"] = "no-store"
        database = get_db()
        try:
            from . import workspace_access
        except ImportError:
            import workspace_access
        await workspace_access.require_individual(database, member)
        await account_owner(database, member["user_id"])
        query = {"user_id": member["user_id"], "deleted": {"$ne": True}}
        if before:
            anchor = await owned(database, member["user_id"], before)
            query["$or"] = [{"created_at": {"$lt": anchor["created_at"]}}, {"created_at": anchor["created_at"], "id": {"$lt": before}}]
        rows = await database[COLLECTION].find(query, {"_id": 0, "id": 1, "audience": 1, "preference_snapshot": 1, "status": 1,
            "revision": 1, "created_at": 1, "updated_at": 1, "asset_edition": 1, "model_edition": 1,
            "state.completed_crowds": 1}).sort([("created_at", -1), ("id", -1)]).limit(limit + 1).to_list(limit + 1)
        return {"sessions": rows[:limit], "next": rows[limit-1]["id"] if len(rows) > limit else None}

    @router.get("/profile")
    async def my_profile(response: Response, member=Depends(subscribed_member)):
        response.headers["Cache-Control"] = "no-store"
        return await profile(get_db(), member["user_id"])

    @router.get("/sessions/{sid}")
    async def session(sid: str, response: Response, member=Depends(subscribed_member)):
        response.headers["Cache-Control"] = "no-store"
        return await read(get_db(), member["user_id"], sid)

    @router.get("/sessions/{sid}/results")
    async def report(sid: str, response: Response, member=Depends(subscribed_member)):
        response.headers["Cache-Control"] = "no-store"
        return await results(get_db(), member["user_id"], sid)

    @router.get("/sessions/{sid}/history")
    async def session_history(sid: str, response: Response, before: str | None = Query(None, max_length=64),
            limit: int = Query(50, ge=1, le=100), member=Depends(current_member)):
        response.headers["Cache-Control"] = "no-store"
        database = get_db()
        await eligible(database, member["user_id"], paid=False)
        await owned(database, member["user_id"], sid)
        await answer_history.flush(database, COLLECTION, {"_id": sid, "user_id": member["user_id"]}, strict=True)
        query = {"source": COLLECTION, "source_id": sid, "owner_id": member["user_id"],
                 "actor_id": member["user_id"], "purged": {"$ne": True}}
        if before:
            anchor = await database.answer_history.find_one({**query, "id": before}, {"revision": 1, "id": 1})
            if not anchor:
                raise conflict()
            query["$or"] = [{"revision": {"$lt": anchor["revision"]}}, {"revision": anchor["revision"], "id": {"$lt": before}}]
        records = await database.answer_history.find(query, {"_id": 0, "id": 1, "kind": 1, "revision": 1,
            "at": 1, "demographics": 1, "after.action_id": 1, "after.request": 1, "after.effects": 1,
            "after.batch_id": 1, "after.asset_edition": 1, "after.asset_manifest_sha256": 1,
            "after.model_edition": 1, "after.preference_snapshot": 1}).sort([("revision", -1), ("id", -1)]).limit(limit + 1).to_list(limit + 1)
        return {"items": records[:limit], "next": records[limit-1]["id"] if len(records) > limit else None}

    @router.post("/sessions/{sid}/actions")
    async def action(sid: str, body: Action, response: Response, member=Depends(subscribed_member)):
        response.headers["Cache-Control"] = "no-store"
        return await act(get_db(), member["user_id"], sid, body)

    @router.post("/sessions/{sid}/actions/batch")
    async def batch(sid: str, body: Batch, response: Response, member=Depends(subscribed_member)):
        response.headers["Cache-Control"] = "no-store"
        return await act_batch(get_db(), member["user_id"], sid, body)

    @router.delete("/sessions/{sid}")
    async def remove(sid: str, response: Response, expected_revision: int = Query(ge=0), member=Depends(current_member)):
        response.headers["Cache-Control"] = "no-store"
        await erase(get_db(), member["user_id"], sid, expected_revision)
        return {"removed": True}

    return router
