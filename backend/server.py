import asyncio
import hashlib
import json
import logging
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, List, Literal, Optional
from urllib.parse import quote, urlsplit

import requests
import stripe
from bson import ObjectId
from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field, StrictBool
from pymongo import ASCENDING, DESCENDING, IndexModel, ReturnDocument
from pymongo.errors import DuplicateKeyError
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

try:
    from backend import game_plans, intimacy_catalog, membership_plans, partner_checkout, partner_offers, trends_export
    from backend.free_plan import billing_tier, complimentary_access, has_member_access, has_personal_access, initialize_usage, quick_person_id, reply_limit_error, reply_usage, run_metered, subscription_required
    from backend import accounts, billing_adjustments, couples_analytics, couples_catalog, couples_reminders, ops, stripe_membership, stripe_plan, stripe_refills, quiz_answers, trends
    from backend.support import SUPPORT_INDEXES, support_router
    from backend.couples import COUPLES_INDEXES, InviteToken, active_pair, couples_router, clear_couples, is_adult, member_info, require_adult
    from backend.couples_journey import JOURNEY_INDEXES, PROMPTS as COUPLES_STARTERS, journey_router
    from backend.clerk_auth import clerk_router, require_user
    from backend.ai import ai_json as provider_json
    from backend.content import seed_base, default_settings, DEFAULT_PROMPTS, paid_plan_copy
    from backend.quiz import default_quiz, about_summary, radar, TYPES, valid_option_scores, with_quiz_scoring
except ModuleNotFoundError:
    import game_plans, intimacy_catalog, membership_plans, partner_checkout, partner_offers, trends_export
    from free_plan import billing_tier, complimentary_access, has_member_access, has_personal_access, initialize_usage, quick_person_id, reply_limit_error, reply_usage, run_metered, subscription_required
    from quiz import default_quiz, about_summary, radar, TYPES, valid_option_scores, with_quiz_scoring
    import accounts, billing_adjustments, couples_analytics, couples_catalog, couples_reminders, ops, stripe_membership, stripe_plan, stripe_refills, quiz_answers, trends
    from support import SUPPORT_INDEXES, support_router
    from couples import COUPLES_INDEXES, InviteToken, active_pair, couples_router, clear_couples, is_adult, member_info, require_adult
    from couples_journey import JOURNEY_INDEXES, PROMPTS as COUPLES_STARTERS, journey_router
    from clerk_auth import clerk_router, require_user
    from ai import ai_json as provider_json
    from content import seed_base, default_settings, DEFAULT_PROMPTS, paid_plan_copy

from backend.database import open_database
from backend import quick_advice, report_dates, account_read_context, member_overview, dating
from backend.database_maintenance import DatabaseMaintenance, paused as database_paused, startup_allowed
from backend.postgres_store import RepositoryUnavailable
from starlette.responses import JSONResponse
client, db = open_database()
log = logging.getLogger(__name__)

try:
    from . import answer_context, answer_history, individual_history, history_lifecycle, database_migration_admin, admin_answer_history, intro_access
    from . import your_type, your_type_research, your_type_preferences, purchase_policy, research_notices, intro_card, workspace_access, couples_compatibility, billing_risk, main_consent, billing_acceptance
except ImportError:
    import answer_context, answer_history, individual_history, history_lifecycle, database_migration_admin, admin_answer_history, intro_access
    import purchase_policy
    import your_type, your_type_research, your_type_preferences, research_notices, intro_card, workspace_access, couples_compatibility, billing_risk, main_consent, billing_acceptance

try:
    from . import report_sources, human_review, rehearsal_review, wording_feedback, ai_meter, assessment_evidence, catalog_editorial, catalog_release, figure_evidence, intimacy_answers, membership_admin, research, research_trends, tone_research
except ImportError:
    import report_sources, human_review, rehearsal_review, wording_feedback, ai_meter, assessment_evidence, catalog_editorial, catalog_release, figure_evidence, intimacy_answers, membership_admin, research, research_trends, tone_research

try:
    from . import advice_wording, announcements, archetype_copy, contact_boundary, conversation_tone, deepseek_connection, message_charge, message_cost_admin, message_costs, pattern_evidence, prompt_candidate, prompt_library, public_quiz
except ImportError:
    import advice_wording, announcements, archetype_copy, contact_boundary, conversation_tone, deepseek_connection, message_charge, message_cost_admin, message_costs, pattern_evidence, prompt_candidate, prompt_library, public_quiz


async def ai_json(feature, system, prompt, image_b64=None, *, settings=None, tone=None):
    if settings is None:
        stored = await db.settings.find_one({"key": "global"}, {"prompts": 1}) or {}
        prompts = prompt_library.normalized(DEFAULT_PROMPTS, stored.get("prompts"))
    else:
        prompts = settings["prompts"]
    system = prompt_library.compose(prompts, feature, tone) + "\n\n" + system
    if feature in ("six_part", "group_read", "prepare", "couples_tone"):
        system += "\n\n" + contact_boundary.INSTRUCTION
    token = ops.request_budget_db.set(db)
    measures, observation = ai_meter.start()
    status = "failed"
    try:
        with message_costs.generation_context(ENV, feature):
            result = await provider_json(system, prompt, image_b64)
        status = "succeeded"
        return result
    finally:
        ops.request_budget_db.reset(token)
        ai_meter.metrics.reset(observation)
        if ai_meter.execution.get() != "evaluation":
            try:
                await prompt_library.record(db, ENV, feature, status)
            except Exception:
                log.warning("Could not record generation outcome for %s", feature)
        try:
            await prompt_library.record_attempts(db, ENV, feature, ai_meter.execution.get(), measures)
        except Exception:
            log.warning("Could not record provider usage for %s", feature)


INDEXES: dict[str, list[IndexModel]] = {
    "answer_history": [IndexModel([("source", ASCENDING), ("source_id", ASCENDING), ("generation", ASCENDING)], name="history_source_generation"),
                       IndexModel([("owner_id", ASCENDING), ("at", DESCENDING)], name="history_owner_time"),
                       IndexModel([("pair_id", ASCENDING), ("at", DESCENDING)], name="history_pair_time")],
    **SUPPORT_INDEXES,
    **COUPLES_INDEXES,
    **JOURNEY_INDEXES,
    **message_costs.INDEXES,
    **message_charge.INDEXES,
    **intro_access.INDEXES,
    "admin_answer_history_access": [IndexModel("expires_at", expireAfterSeconds=0, name="history_staff_access_retention")],
    "questionnaire_scan_plans": [IndexModel("expires_at", expireAfterSeconds=0, name="expire_questionnaire_scan_plans")],
    "prompt_candidates": [IndexModel("expires_at", expireAfterSeconds=0, name="expire_prompt_candidates")],
    "research_responses": [IndexModel("user_id", name="research_owner"),
                           IndexModel([("env", ASCENDING), ("consent_version", ASCENDING)], name="research_cohort",
                                      partialFilterExpression={"submitted": True})],
    "ai_request_usage": [IndexModel("expires_at", expireAfterSeconds=0, name="member_usage_expiry")],
    "ai_daily_usage": [IndexModel("expires_at", expireAfterSeconds=0, name="daily_budget_expiry")],
    "archetypes": [IndexModel([("slug", ASCENDING)], name="slug", unique=True), IndexModel([("number", ASCENDING)], name="number")],
    "figures": [IndexModel([("slug", ASCENDING)], name="slug", unique=True)],
    "users": [IndexModel([("user_id", ASCENDING)], name="user_id", unique=True),
              IndexModel("created_at", name="trend_signups"),
              IndexModel([("row_member_env", ASCENDING), ("row_member_code", ASCENDING)], name="research_member_codes",
                         unique=True, partialFilterExpression={"row_member_code": {"$type":"string"}}),
              IndexModel([("questionnaire_research_version", ASCENDING), ("env", ASCENDING)],
                         name="ongoing_research", partialFilterExpression={"questionnaire_research_version": "all-answers-v1"}),
              IndexModel([("assessment_repeat.instrument", ASCENDING), ("env", ASCENDING)],
                         name="published_repeat", partialFilterExpression={"assessment_repeat.publish_group_results": True}),
              IndexModel("everyday_trends_opt_in", name="trend_consent", partialFilterExpression={"everyday_trends_opt_in": True})],
    "people": [IndexModel([("user_id", ASCENDING), ("last_activity", DESCENDING)], name="user_activity"),
               IndexModel("created_at", name="trend_people"),
               IndexModel("report.updated_at", name="trend_profile_reports", sparse=True),
               IndexModel([("id", ASCENDING)], name="id", unique=True),
               IndexModel([("user_id", ASCENDING), ("free_slot", ASCENDING)], name="free_active_slot", unique=True,
                          partialFilterExpression={"free_slot": {"$exists": True}})],
    "memories": [IndexModel([("person_id", ASCENDING), ("created_at", DESCENDING)], name="person_created"), IndexModel([("id", ASCENDING)], name="id", unique=True),
                 IndexModel("created_at", name="trend_saved_memories")],
    "corrections": [IndexModel("created_at", name="trend_corrections")],
    "exchanges": [IndexModel([("person_id", ASCENDING), ("created_at", ASCENDING)], name="person_created"), IndexModel([("id", ASCENDING)], name="id", unique=True),
                  IndexModel([("person_id", ASCENDING), ("created_at", ASCENDING), ("id", ASCENDING)], name="person_history_page"),
                  IndexModel([("user_id", ASCENDING), ("tone_sources.id", ASCENDING)], name="tone_source_cleanup",
                             partialFilterExpression={"tone_sources.id": {"$exists": True}}),
                  IndexModel([("person_id", ASCENDING), ("tone_thread_id", ASCENDING), ("created_at", DESCENDING)],
                             name="tone_thread_history", partialFilterExpression={"tone_thread_id": {"$exists": True}}),
                  IndexModel("choice.at", name="trend_reply_choices", sparse=True),
                  IndexModel([("tone.status", ASCENDING), ("created_at", ASCENDING), ("user_id", ASCENDING)],
                             name="tone_report_window", partialFilterExpression={"tone.status": {"$exists": True}}),
                  IndexModel("created_at", name="trend_reads"), IndexModel([("outcome_at", DESCENDING), ("_id", DESCENDING)], name="trend_outcomes",
                  partialFilterExpression={"outcome_at": {"$type": "string"}})],
    "practice": [IndexModel("created_at", name="trend_practice")],
    "preps": [IndexModel([("person_id", ASCENDING), ("created_at", DESCENDING)], name="person_created"),
              IndexModel("created_at", name="trend_plans"), IndexModel("outcome_at", name="trend_plan_followups", sparse=True)],
    "patterns": [IndexModel([("user_id", ASCENDING)], name="user_id"),
                 IndexModel("created_at", name="trend_observations"),
                 IndexModel([("user_id", 1), ("generated_text_key", 1)], name="member_observation_wording", unique=True,
                            partialFilterExpression={"generated_text_key": {"$type": "string"}})],
    "chat_groups": [IndexModel("created_at", name="trend_named_groups")],
    "group_chats": [IndexModel([("user_id", ASCENDING), ("key", ASCENDING), ("created_at", ASCENDING)], name="user_key"),
                    IndexModel([("user_id", ASCENDING), ("key", ASCENDING), ("created_at", ASCENDING), ("id", ASCENDING)], name="group_history_page"),
                    IndexModel("created_at", name="trend_group_chats")],
    "payment_transactions": [IndexModel([("kind", ASCENDING), ("payment_status", ASCENDING), ("created_at", ASCENDING)], name="trend_transactions"),
                             IndexModel("balance_source_ids", sparse=True, name="billing_balance_sources"),
                             IndexModel([("kind", ASCENDING), ("payment_status", ASCENDING), ("fulfilled_at", ASCENDING)], name="trend_fulfilments"),
                             IndexModel([("env", ASCENDING), ("partner_checked_at", ASCENDING)], name="pending_partner_checkouts",
                                        partialFilterExpression=partner_checkout.PENDING)],
    "closed_membership_revenue": [IndexModel([("env", ASCENDING), ("month", ASCENDING)], name="monthly_closed_payments")],
    "events": [IndexModel([("type", ASCENDING), ("user_id", ASCENDING)], name="type_user"),
               IndexModel([("type", ASCENDING), ("ts", ASCENDING)], name="trend_events")],
    "waitlist": [IndexModel([("email", ASCENDING)], name="email", unique=True)],
    "notifications": [IndexModel([("user_id", ASCENDING), ("key", ASCENDING)], name="user_key", unique=True),
                      IndexModel("key", name="announcement_receipts", partialFilterExpression={"type": "announcement"}),
                      IndexModel([("user_id", 1), ("created_at", -1), ("id", -1)], name="inbox_page")],
    "announcements": [IndexModel([("created_at", DESCENDING), ("id", DESCENDING)], name="announcement_history")],
}


INDEXES.update(your_type.INDEXES)
INDEXES.update(quick_advice.INDEXES)
INDEXES.update(dating.INDEXES)
for _collection, _models in billing_risk.INDEXES.items():
    INDEXES.setdefault(_collection, []).extend(_models)

for _collection, _field, _person_scoped in (
    ("people", "last_activity", False), ("chat_groups", "created_at", False),
    *((name, "created_at", True) for name in ("memories", "corrections", "practice", "preps"))
):
    _prefix = [("user_id", ASCENDING), ("person_id", ASCENDING)] if _person_scoped else [("user_id", ASCENDING)]
    INDEXES[_collection].append(IndexModel([*_prefix, (_field, DESCENDING), ("id", DESCENDING)], name="member_page"))

for _source in history_lifecycle.SOURCES:
    INDEXES.setdefault(_source, []).extend([
        IndexModel(answer_history.SOURCE + ".source_id", name="answer_history_source_id", sparse=True),
        IndexModel(answer_history.REVISION, name="answer_history_pending", partialFilterExpression={answer_history.PENDING + ".0": {"$exists": True}}),
        IndexModel(answer_history.REVISION, name="answer_history_redactions", partialFilterExpression={answer_history.REDACTIONS + ".0": {"$exists": True}}),
    ])


async def ensure_indexes() -> None:
    parallel = asyncio.Semaphore(4)
    async def ensure_collection(collection, models):
        if not models:
            return
        async with parallel:
            try:
                await db[collection].create_indexes(models)
                return
            except Exception:
                # Check individually to retain each index's existing startup failure policy.
                for model in models:
                    try:
                        await db[collection].create_indexes([model])
                    except Exception as exc:
                        name = model.document["name"]
                        log.error("ensure_indexes(%s.%s): %s", collection, name, exc)
                        if model.document.get("unique") and (name in ("free_active_slot", "member_observation_wording", "your_type_member_slot") or collection.startswith(("couples_", "dating_"))):
                            raise
    await asyncio.gather(*(ensure_collection(collection, models) for collection, models in INDEXES.items()))


app = FastAPI()


@app.middleware("http")
async def private_dating_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/api/dating" or request.url.path.startswith("/api/dating/"):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.exception_handler(RepositoryUnavailable)
async def repository_unavailable(request: Request, exc):
    return JSONResponse({"detail": "The database is temporarily unavailable. Refresh before retrying."},
                        status_code=503, headers={"Retry-After": "5", "Cache-Control": "no-store"})
app.add_middleware(accounts.ReleaseRequestLease)
app.add_middleware(ops.MemberContext)
api = APIRouter(prefix="/api")

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


# ---------------- roles (Clerk playbook) ----------------
ROLES = ["admin", "member"]
DEFAULT_ROLE = "member"
RETIRED_ROLES: dict = {}
RANK = {r: len(ROLES) - i for i, r in enumerate(ROLES)}
ENV = "live" if os.environ.get("CLERK_PUBLISHABLE_KEY", "").startswith("pk_live_") else "preview"
IN_ENV = {"env": {"$in": [ENV, None]}}


def effective_role(role) -> str:
    role = RETIRED_ROLES.get(role, role)
    return role if role in ROLES else DEFAULT_ROLE


def _clerk_user(user_id: str) -> dict:
    try:
        response = requests.get(f"https://api.clerk.com/v1/users/{user_id}", timeout=10,
                                headers={"Authorization": f"Bearer {os.environ['CLERK_SECRET_KEY']}", "User-Agent": "imladri-app"})
        if response.status_code == 404:
            raise HTTPException(401, "This account has been closed.")
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        raise HTTPException(503, "Your sign-in provider is temporarily unavailable. Please try again.") from None
    addresses = data.get("email_addresses") if isinstance(data, dict) else None
    if not isinstance(addresses, list) or any(
        not isinstance(address, dict) or not isinstance(address.get("email_address"), str)
        or not isinstance(address.get("verification") or {}, dict)
        for address in addresses
    ):
        raise HTTPException(503, "Your account details could not be loaded. Please try again.")
    return data


def _primary_email(data):
    addresses = data.get("email_addresses") or []
    primary = data.get("primary_email_address_id")
    if primary:
        return next((item.get("email_address", "") for item in addresses if item.get("id") == primary), "")
    return addresses[0].get("email_address", "") if addresses else ""


def _is_admin_email(data: dict) -> bool:
    admins = {e.strip().lower() for e in os.environ.get("ADMIN_EMAIL", "").split(",") if e.strip()}
    return any(e["email_address"].lower() in admins and (e.get("verification") or {}).get("status") == "verified"
               for e in data["email_addresses"])


async def current_member(request: Request, user: dict = Depends(require_user)) -> dict:
    uid = user["user_id"]
    ops.member_id.set(uid)
    member = await db.users.find_one({"user_id": uid}, {"_id": 0})
    if member is None:
        history = await accounts.membership_history(db, uid)
        if history and history["closed"]:
            raise HTTPException(410, "Your Imladri Personal account is closed.")
        member = await db.users.find_one_and_update({"user_id": uid}, {"$setOnInsert": {"user_id": uid}},
            upsert=True, projection={"_id": 0}, return_document=ReturnDocument.AFTER)
    if not member.get("role") and not member.get("closing"):
        try:
            data = await asyncio.to_thread(_clerk_user, uid)
        except HTTPException as exc:
            if exc.status_code == 401:
                await db.users.delete_one({"user_id": uid, "role": None, "closing": {"$ne": True}})
            raise
        role = "admin" if _is_admin_email(data) else DEFAULT_ROLE
        email = _primary_email(data)
        defaults = {"email": email, "nickname": data.get("first_name") or "", "avatar": "", "origin": "", "tier": "unsubscribed",
                    "onboarding_step": 1, "onboarded": False, "goal_style": "", "redaction_defaults": {"mask_names": True, "words": []},
                    "created_at": now()}
        # Preserve an existing member’s data if role initialization is incomplete.
        if len(member) > 1:
            defaults = {}
        else:
            history = await accounts.membership_history(db, uid)
            if history:
                if history["closed"]:
                    await db.users.delete_one({"user_id": uid, "role": None, "closing": {"$ne": True}})
                    raise HTTPException(410, "Your Imladri Personal account is closed.")
                if history.get("used_sponsored_seat"):
                    defaults["used_sponsored_seat"] = True
        await db.users.update_one({"user_id": uid, "role": {"$in": [None, ""]}, "closing": {"$ne": True}},
                                  {"$set": {"role": role, **defaults}})
        member = await db.users.find_one({"user_id": uid}, {"_id": 0})
        if member is None:
            raise HTTPException(401, "This account has been closed.")
    await accounts.admit_request(db, request, member)
    if member.get("closing"):
        return member
    if member.get("env") != ENV:
        await db.users.update_one({"user_id": uid}, {"$set": {"env": ENV}})
    if (member.get("tier") == "paid" and member.get("stripe_subscription_id")
            and not (member.get("subscription_period_start") and member.get("subscription_period_end"))):
        member = await hydrate_subscription_period(member)
    member = await initialize_usage(db, member)
    message_costs.current_pool.set("intro" if billing_tier(member) == "intro" else None)
    return {**member, "env": ENV, "role": effective_role(member["role"])}


async def subscribed_member(member: dict = Depends(current_member)) -> dict:
    await workspace_access.require_individual(db, member)
    if not has_personal_access(member):
        raise subscription_required()
    return member


async def assessment_member(member: dict = Depends(current_member)) -> dict:
    return await workspace_access.assessment_member(db, member)


async def reply_member(member: dict = Depends(current_member)) -> dict:
    # Let the meter restore a saved tenth reply. Only metered AI routes use
    # this dependency; all other new writes retain the subscription gate.
    await workspace_access.require_individual(db, member)
    if (has_personal_access(member) or
            intro_access.entitlement(member) and not intro_access.converted(member)):
        return member
    raise subscription_required()


def require_role(*roles: str):
    async def check(member: dict = Depends(current_member)) -> dict:
        if member["role"] not in roles:
            raise HTTPException(status_code=403, detail="Not allowed")
        return member
    return check


async def get_settings() -> dict:
    s = await db.settings.find_one({"key": "global"}, {"_id": 0, "prompt_history": 0, "everyday_draft": 0, "everyday_previous": 0, "public_quiz_previous": 0}) or default_settings()
    s["prompts"] = prompt_library.normalized(DEFAULT_PROMPTS, s.get("prompts"))
    s["upgrade"] = paid_plan_copy(s.get("upgrade"))
    s.pop("free_plan_version", None)
    s["people_limit"] = None
    quiz = s.get("quiz") or {}
    s["quiz"] = with_quiz_scoring(quiz) if quiz.get("version") == default_quiz()["version"] else default_quiz()
    return s


async def has_reply_allowance(member: dict) -> bool:
    current = await db.users.find_one({"user_id": member["user_id"]}, {"_id": 0})
    if not current:
        return False
    billing_risk.require_action(current, "new_ai")
    usage = reply_usage(current)
    return usage["unlimited"] or usage["messages_remaining"] > 0


async def plan_usage(member: dict, *, active_people: int | None = None) -> dict:
    active = active_people if active_people is not None else await db.people.count_documents({"user_id": member["user_id"], "archived": {"$ne": True}})
    return {**reply_usage(member), "active_people": active,
            "people_limit": None if has_personal_access(member) else 0}


async def metered_reply(member, request, body, kind, scope, generate, upload_attempt=None, starter_batch=None):
    async def generate_in_individual(reply_id):
        await workspace_access.require_individual(db, member)
        result = await generate(reply_id)
        await workspace_access.require_individual(db, member)
        return result

    async def restore(result):
        if kind == "quickstart":
            person = await own_person(result["person_id"], member)
            return {"person": await person_out(person), "exchange": await saved_exchange_with_tone(result, person)}
        if kind == "exchange":
            person = await own_person(result["person_id"], member)
            return await saved_exchange_with_tone(result, person)
        if kind == "group":
            names = await group_contact_names(result["person_ids"], member)
            return contact_boundary.saved_group(result, names)
        if kind == "practice":
            return await own_session(scope, member)
        if kind == "prep":
            return game_plans.saved_plan(result)
        return result
    preparation = (message_charge.upload_id(member["user_id"], upload_attempt)
                   or message_charge.starter_id(member["user_id"], scope, starter_batch))
    return await run_metered(db, member, request, body, kind, scope, generate_in_individual, restore, preparation_id=preparation)


async def track(user_id: str, type_: str, data: dict | None = None, *, attempt: uuid.UUID | None = None):
    event = {"user_id": user_id, "type": type_, "data": data or {}, "ts": now()}
    if attempt is None:
        await db.events.insert_one(event)
    else:
        await db.events.update_one({"_id": f"{type_}:{user_id}:{attempt}"}, {"$setOnInsert": event}, upsert=True)


async def completed_upload(uid, attempt, exchange):
    if attempt and exchange.get("has_conversation"):
        # A lost start or success response can be retried with the same upload ID.
        await track(uid, "upload_started", attempt=attempt)
        await track(uid, "upload_completed", attempt=attempt)


QUIET = {"report": ("A read was updated", "Open Imladri to see what changed."), "checkin": ("A check-in is waiting", "Tap to log how it went.")}


async def notify(user_id: str, type_: str, title: str, body: str, link: str, key: str | None = None):
    doc = {"id": new_id(), "user_id": user_id, "type": type_, "title": title, "body": body, "link": link,
           "read": False, "created_at": now(), "key": key or new_id()}
    await db.notifications.update_one({"user_id": user_id, "key": doc["key"]}, {"$setOnInsert": doc}, upsert=True)


async def notify_all(type_: str, title: str, body: str, link: str, key: str, *, created_through: str | None = None):
    recipients = announcements.recipients(IN_ENV, created_through)
    async for u in db.users.find(recipients, {"_id": 0, "user_id": 1}):
        uid = u["user_id"]
        lease = await accounts.reserve_request(db, uid)
        if lease:
            try:
                await notify(uid, type_, title, body, link, key)
            finally:
                await db.users.update_one({"user_id": uid}, {"$unset": {"request_leases." + lease: ""}})
        elif created_through and await db.users.find_one({"user_id": uid, "closing": {"$ne": True}}, {"_id": 1}):
            raise HTTPException(503, "An inbox is being updated. Resume delivery from announcement history.")


# ---------------- intake schema + meter ----------------
INTAKE = [
    {"key": "basics", "title": "Basics", "fields": [
        {"key": "known_for", "label": "How long have you known them?", "hint": "e.g. 4 months - we met at Sam's birthday"},
        {"key": "current_state", "label": "Where things stand right now", "hint": "e.g. we text daily but he hasn't asked me out again"},
        {"key": "her_goal", "label": "What you want with them", "hint": "e.g. find out if he's serious, or stop fighting about money"}]},
    {"key": "who", "title": "Who they are", "fields": [
        {"key": "personality", "label": "Their personality, as you see it", "hint": "e.g. funny in groups, quiet one-on-one, proud"},
        {"key": "talk_text", "label": "How they talk and text", "hint": "e.g. short replies, lots of voice notes, never asks questions"},
        {"key": "conflict", "label": "How they handle conflict", "hint": "Describe the last time you disagreed - what did they say, word for word?"},
        {"key": "pressure", "label": "Stress, criticism and being told no", "hint": "e.g. goes silent for a day, then acts like nothing happened"},
        {"key": "cares_about", "label": "What they care about", "hint": "e.g. his career, his little brother, being respected"},
        {"key": "sets_off", "label": "What sets them off", "hint": "e.g. feeling compared, being late, money talk"},
        {"key": "affection", "label": "How they show affection or approval", "hint": "e.g. does favours instead of saying things"}]},
    {"key": "history", "title": "History", "fields": [
        {"key": "key_moments", "label": "Key moments between you", "hint": "e.g. the trip in June, the night he met my friends"},
        {"key": "recurring", "label": "Recurring fights or patterns", "hint": "e.g. every few weeks he pulls away after we get close"},
        {"key": "worked_failed", "label": "What has worked and failed before", "hint": "e.g. direct asks work; hints never do"}]},
    {"key": "her_side", "title": "Your side", "fields": [
        {"key": "her_reaction", "label": "How you usually react", "hint": "e.g. I over-explain, then apologise"},
        {"key": "want_change", "label": "What you want to change", "hint": "e.g. stop chasing, say what I need once"},
        {"key": "at_stake", "label": "What's at stake for you", "hint": "e.g. my job, family holidays, my self-respect"}]},
]
FIELD_LABELS = {f["key"]: f["label"] for s in INTAKE for f in s["fields"]}
VALUE_ORDER = ["conflict", "talk_text", "recurring", "her_goal", "sets_off", "pressure", "current_state", "worked_failed",
               "affection", "personality", "cares_about", "key_moments", "her_reaction", "want_change", "at_stake", "known_for"]
REL_GROUPS = {
    "romantic": ["crush", "talking", "situationship", "partner", "husband", "fwb", "long_distance", "ex"],
    "family": ["mom", "dad", "sister", "brother", "grandparent", "aunt", "uncle", "cousin", "other_family"],
    "friends": ["best_friend", "friend", "roommate"],
    "work": ["boss", "coworker", "client"],
    "other": ["other"],
}
GROUP_OF = {t: g for g, ts in REL_GROUPS.items() for t in ts}
ROMANTIC = set(REL_GROUPS["romantic"])
ROMANTIC_STAGES = ("crush", "early", "established", "fading")
OTHER_STAGES = ("good", "strained", "distant", "repairing")


MIN_INTAKE_LENGTH = 8


def detail_score(filled, conversations, outcomes):
    return round(filled / len(FIELD_LABELS) * 60 + min(conversations, 5) / 5 * 25 + min(outcomes, 3) / 3 * 15)


def person_meter(person: dict, convs: int, outs: int) -> dict:
    intake = person.get("intake") or {}
    filled = [k for k in FIELD_LABELS if isinstance(intake.get(k), str) and len(intake[k].strip()) >= MIN_INTAKE_LENGTH]
    score = detail_score(len(filled), convs, outs)
    missing = []
    if convs < 2:
        missing.append("Add a real conversation")
    missing += [FIELD_LABELS[k] for k in VALUE_ORDER if k not in filled][:3]
    if outs == 0 and convs > 0:
        missing.append("Log what actually happened after advice")
    return {"score": score, "fields_filled": len(filled), "fields_total": len(FIELD_LABELS),
            "conversations": convs, "outcomes": outs, "missing": missing[:4]}


async def meter(person: dict) -> dict:
    convs = await db.exchanges.count_documents({"person_id": person["id"], "has_conversation": True})
    outs = await db.exchanges.count_documents({"person_id": person["id"], "outcome": {"$nin": [None, ""]}})
    return person_meter(person, convs, outs)


async def listed_people_meters(people: list[dict]) -> dict:
    # The page is capped at200. Keep the exact existing two count predicates,
    # grouping their results once instead of two gateway round trips per card.
    if not people:
        return {}
    ids = [person["id"] for person in people]
    async def counts(criteria):
        rows = await db.exchanges.aggregate([{"$match": {"person_id": {"$in": ids}, **criteria}},
            {"$group": {"_id": "$person_id", "n": {"$sum": 1}}}]).to_list(None)
        return {row["_id"]: row["n"] for row in rows}
    convs, outs = await asyncio.gather(counts({"has_conversation": True}), counts({"outcome": {"$nin": [None, ""]}}))
    return {person["id"]: person_meter(person, convs.get(person["id"], 0), outs.get(person["id"], 0)) for person in people}


def prompt_key(p: dict) -> str:
    if p["rel_type"] in ROMANTIC:
        return {"crush": "crush", "early": "early_dating", "established": "established", "fading": "fading"}.get(p.get("stage"), "early_dating")
    return {"family": "family", "work": "work"}.get(GROUP_OF.get(p["rel_type"], "other"), "friend")


def arch_no(n: int) -> str:
    return f"{n - 100 if n > 100 else n:02d}"


def arch_ref(n: int) -> str:
    return f"{'female' if n > 100 else 'male'} archetype #{arch_no(n)}"


async def own_person(pid: str, member: dict) -> dict:
    p = await db.people.find_one({"id": pid, "user_id": member["user_id"], "deleting": {"$ne": True}}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    return p


async def person_context(p: dict, limit_ex: int = 4, *, source_map=None, cite_sources=False) -> str:
    def source(kind, key):
        if source_map is None or not key:
            return ""
        label = f"S{len(source_map) + 1}"
        source_map[label] = {"kind": kind, "id": key}
        return f"[{label}] " if cite_sources else ""
    intake = p.get("intake") or {}
    mems = await db.memories.aggregate([
        {"$match": {"person_id": p["id"], "user_id": p["user_id"]}},
        {"$project": {"id": 1, "text": 1, "kind": 1, "priority": {"$eq": ["$prioritized", True]}, "last_changed": {"$max": ["$created_at", "$edited_at"]}}},
        {"$sort": {"priority": -1, "last_changed": -1, "id": -1}}, {"$limit": 300},
    ]).to_list(300)
    exs = await db.exchanges.find({"person_id": p["id"], "user_id": p["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(limit_ex)
    rel = p.get("rel_label") or p["rel_type"]
    lines = [f"PERSON: {p['nickname']} | relationship: {rel} ({GROUP_OF.get(p['rel_type'], 'other')}) | stage: {p.get('stage')} | member's goal: {p.get('goal') or 'not set'}",
             f"{source('profile', 'background') if p.get('background') else ''}Background: {p.get('background') or '-'}", "PROFILE DETAILS (from the member):"]
    lines += [f"- {source('profile', k)}{FIELD_LABELS[k]}: {v}" for k, v in intake.items() if k in FIELD_LABELS and v]
    if demo_line(p.get("demographics")):
        lines.append(f"- About them (member’s estimate): {demo_line(p.get('demographics'))}")
    lines.append("APPROVED MEMORIES (kind: text):")
    lines += [f"- {source('memory', m['id'])}[{m['kind']}] {m['text']} (id {m['id'][:8]})" for m in reversed(mems)]
    lines.append("RECENT ADVICE (newest first):")
    for e in exs:
        r = e.get("response") or {}
        ch = (e.get("choice") or {}).get("label") or "not chosen"
        ref = source("exchange", e["id"])
        if cite_sources:
            actual = report_sources.reviewed_messages(e)
            lines.append(f"- {ref}{e['created_at'][:10]} Confirmed messages:\n{actual or 'No transcript supplied'}\n"
                         f"Member's question: {str(e.get('question') or '')[:250]}\n"
                         f"Member's reported outcome: {str(e.get('outcome') or 'not recorded')[:250]}")
            continue
        lines.append(f"- {ref}{e['created_at'][:10]}: read='{r.get('what_it_means', '')}' move='{r.get('best_move', '')}' member chose='{ch}' "
                     f"result='{e.get('outcome_result') or '-'}' outcome='{e.get('outcome') or 'not logged'}'")
    cors = await db.corrections.find({"person_id": p["id"], "user_id": p["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(20)
    if cors:
        lines.append("MEMBER CORRECTIONS (adjust the interpretation to account for these corrections):")
        lines += [f"- {source('correction', c['id'])}'{c['text']}'" + (f" (member note: {c['note']})" if c.get("note") else "") for c in cors]
    return "\n".join(lines)


MEMBER_ADVICE_RULE = ("Address the member as 'you'. Use stated identities and pronouns for the people involved. "
                      "When those are unstated, use their name or neutral pronouns rather than guessing. "
                      "Quiz scenarios and archetypes do not establish anyone's identity. Tailor advice to the "
                      "member's stated preferences. Use everyday language and preserve the source of each observation. "
                      "Keep suggested words brief and natural. Use only availability, commitments and exceptions the "
                      "member actually offered. If the other person says when to reach them, that does not tell you "
                      "when the member is free. Keep each copyable draft and action within the member's single "
                      "stated purpose. Ask about a time only when arranging or clarifying a time is part of that "
                      "purpose; do not add a time question to a request just to confirm a day or acknowledge an answer. "
                      "Do not invent an inspection deadline, follow-up promise, availability, affection or another offer. "
                      "Write a complete message they can send as written, without "
                      "fill-in blanks such as '[day]'. When someone specifies how or when to "
                      "reach them, say when to use the suggested words and honor that limit in every action.")


def context_signature(*parts) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


async def saved_profile(member: dict, quiz: dict) -> tuple[dict, list[str]]:
    return about_summary(member.get("about"), quiz), await member_pattern_evidence(member["user_id"], 30)


def saved_profile_text(summary: dict, patterns: list[str]) -> str:
    lines = []
    personality = summary["personality"]
    if personality["code"]:
        source = "member-selected" if personality["self_set"] else "suggested by the quiz"
        lines.append(f"Personality type ({source}): {personality['code']} - {personality['name']}. {personality['line']}")
    if summary["tags"]:
        lines.append("Snapshot: " + ", ".join(summary["tags"]))
    lines.extend(summary["lines"])
    if summary["describe"]:
        lines.append("Self-description: " + summary["describe"])
    details = demo_line(summary["demographics"])
    if details:
        lines.append("Member details: " + details)
    lines.extend(patterns)
    return "\n".join(lines) or "No saved profile details."


async def full_context(p: dict, user: dict, limit_ex: int = 4, *, profile=None, source_map=None, cite_sources=False) -> tuple[str, list[str]]:
    ctx = await person_context(p, limit_ex, source_map=source_map, cite_sources=cite_sources)
    summ, pats = profile or await saved_profile(user, (await get_settings())["quiz"])
    pers = summ["personality"]
    arch = ""
    if p["rel_type"] in ROMANTIC and p.get("archetype_tag"):
        a = await db.archetypes.find_one({"number": p["archetype_tag"], "unlocked": True}, {"_id": 0, "name": 1, "one_liner": 1, "sections": 1, "gender": 1})
        if a:
            arch = (f"\nARCHETYPE THE MEMBER SELECTED (their interpretation): {a.get('gender', 'male')} archetype #{arch_no(p['archetype_tag'])} {a['name']} - {a['one_liner']}. "
                    f"Page notes: {json.dumps(a.get('sections') or {})[:2500]}")
    intake = [FIELD_LABELS[k].lower().rstrip("?") for k, v in (p.get("intake") or {}).items() if k in FIELD_LABELS and v and not (k == "her_goal" and p.get("goal"))]
    convs = await db.exchanges.count_documents({"person_id": p["id"], "has_conversation": True})
    advice = await db.exchanges.count_documents({"person_id": p["id"]})
    mems = await db.memories.count_documents({"person_id": p["id"], "kind": {"$ne": "outcome"}})
    outs = await db.memories.count_documents({"person_id": p["id"], "kind": "outcome"})
    bo = [f"profile: {p.get('rel_label') or p['rel_type'].replace('_', ' ')}, {p.get('stage')}"]
    if intake:
        bo.append("profile: " + ", ".join(intake[:2]) + (f" +{len(intake) - 2}" if len(intake) > 2 else ""))
    if p.get("goal"):
        bo.append("your goal")
    if demo_line(p.get("demographics")):
        bo.append("their details")
    for n, label in ((convs, "conversation"), (mems, "memory"), (outs, "outcome"), (advice, "earlier advice")):
        if n:
            bo.append(f"{n} {label}" + ("s" if n > 1 and label in ("conversation", "outcome") else "") + (" items" if n > 1 and label == "memory" else ""))
    if summ["lines"]:
        bo.append("your quiz" + (": " + ", ".join(summ["tags"][:2]) if summ["tags"] else ""))
    if pers["code"]:
        bo.append(f"your type: {pers['code']}")
    if summ["describe"]:
        bo.append("your self-description")
    if pats:
        bo.append(f"your patterns ({len(pats)})")
    if arch:
        bo.append(f"{arch_ref(p['archetype_tag'])} (your tag)")
    return f"{ctx}\n\nMEMBER'S OWN PROFILE:\n" + saved_profile_text(summ, pats) + arch, bo


def strip(doc: dict | None):
    if doc:
        doc.pop("_id", None)
    return doc


# ---------------- report ----------------
REPORT_SECTIONS = [("personality", "Likely personality traits"), ("communication", "Communication style"),
                   ("attachment", "Attachment style"), ("triggers", "Emotional triggers"), ("needs", "Needs and motivations"),
                   ("conflict", "Conflict style"), ("responds_to", "What they respond to"), ("shuts_down", "What shuts them down"),
                   ("approach", "How to approach them for your goal")]


async def require_generation_access(member: dict, *, assessment=False):
    if not assessment:
        await workspace_access.require_individual(db, member)
    if not await has_reply_allowance(member):
        raise reply_limit_error(member)


async def invalidate_person_report(pid: str, uid: str):
    # Editing source material also cancels any in-flight generation based on the old version.
    await db.people.update_one({"id": pid, "user_id": uid,
                                "$or": [{"report": {"$ne": None}}, {"working_read": {"$ne": None}}, {"report_run": {"$exists": True}}]},
                               {"$set": {"report": None, "working_read": None, "report_status": "source_changed"},
                                "$unset": {"report_run": ""}})


async def regenerate_report(pid: str, reasons: list[str], *, source_removed: bool = False):
    p = await db.people.find_one({"id": pid}, {"_id": 0})
    if not p:
        return
    user = await db.users.find_one({"user_id": p["user_id"]}, {"_id": 0})
    if not user:
        return
    if not await has_reply_allowance(user):
        if source_removed:
            await db.people.update_one({"id": pid}, {"$set": {"report": None, "report_status": "empty", "working_read": None}, "$unset": {"report_run": ""}})
        return
    run = new_id()
    state = {"report_status": "updating", "report_run": run}
    if source_removed:
        state.update(report=None, working_read=None)
    p = await db.people.find_one_and_update({"id": pid, "user_id": user["user_id"]}, {"$set": state},
                                           return_document=ReturnDocument.AFTER)
    if not p:
        return
    active_run = {"id": pid, "report_run": run}
    try:
        s = await get_settings()
        profile = await saved_profile(user, s["quiz"])
        profile_sig = context_signature(saved_profile_text(*profile))
        evidence = {}
        ctx, bo = await full_context(p, user, 8, profile=profile, source_map=evidence, cite_sources=True)
        previous = p.get("report") or {}
        prev = json.dumps(previous)[:4000] if not source_removed and previous.get("profile_sig") == profile_sig else "{}"
        shape = ('{"sections": [{"key": "<one of: ' + ", ".join(k for k, _ in REPORT_SECTIONS) + '>", "read": "2-3 sentences or null if not enough evidence", '
                 '"confidence": "low|medium|high|null", "source_ids": ["S1"], '
                 '"empty_note": "if read is null: what is missing and what to add"}], '
                 '"change_note": "what changed vs the previous report and why, explicit (\'previously read as X; now Y because...\'). Distinguish the member’s interpretation from observed events.", '
                 '"interpretation_note": "optional: \'Noted - though this is your read, not yet something they have shown\' style note or null", '
                 '"traits": [{"trait": "<one of: Warmth, Consistency, Openness, Directness, Emotional availability, Effort>", "score": 0-100, "confidence": "low|medium|high"}] '
                 '(ONLY traits with real evidence from what they did or said - omit the rest, never guess), '
                 '"compatibility": {"summary": "2 sentences comparing the member’s stated style with the other person’s", "works": ["where your styles fit"], '
                 '"friction": ["where your styles clash"], "tip": "one concrete action the member can take given both styles"} '
                 '(set compatibility to null if the member profile is empty)}')
        prompt = (f"{ctx}\n\nPREVIOUS REPORT: {prev}\nWHY UPDATING NOW: {'; '.join(reasons)}\n\n"
                  f"Return all {len(REPORT_SECTIONS)} sections. Shape:\n{shape}")
        prompt += (" For every section with a read and every trait or compatibility claim, include a source_ids array "
                   "of the bracketed S identifiers supporting that claim. Use only identifiers in the supplied evidence. "
                   "Return change_source_ids and interpretation_source_ids for any change or interpretation note. "
                   "For compatibility use summary_source_ids and tip_source_ids, and write each works or friction "
                   "entry as an object with text and source_ids. If a section has no supporting observation, use read: null. "
                   "Quote messages exactly. A short acknowledgment alone cannot establish mood, intent or a personality trait.")
        out = await ai_json("report", MEMBER_ADVICE_RULE, prompt, settings=s, tone=prompt_key(p))
        titles = dict(REPORT_SECTIONS)
        supplied = {x["key"]: x for x in (out.get("sections") or []) if isinstance(x, dict) and x.get("key") in titles}
        secs = [{**supplied.get(key, {"read": None, "empty_note": "Add a relevant example from a real conversation."}),
                 "key": key, "title": title} for key, title in REPORT_SECTIONS]
        report = {"sections": secs, "change_note": out.get("change_note"), "interpretation_note": out.get("interpretation_note"),
                  "reasons": reasons, "updated_at": now(), "based_on": bo, "profile_sig": profile_sig,
                  "traits": [t for t in out.get("traits") or [] if isinstance(t, dict) and t.get("confidence") in ("low", "medium", "high")][:6],
                  "compatibility": out.get("compatibility") if isinstance(out.get("compatibility"), dict) else None}
        report_sources.apply(report, out, evidence)
        result = await db.people.update_one(active_run, {"$set": {"report": report, "report_status": "ready"}, "$unset": {"report_run": ""}})
        if not result.matched_count:
            return
        await advance(p["user_id"], 5)
        if p.get("report") and reasons != ["Manual refresh"]:
            await notify(p["user_id"], "report", *QUIET["report"], f"/app/person/{pid}?tab=report")
    except Exception as exc:
        log.error("report %s: %s", pid, exc)
        await db.people.update_one(active_run, {"$set": {"report_status": "error"}, "$unset": {"report_run": ""}})


# ---------------- models ----------------
async def advance(user_id: str, step: int, extra: dict | None = None):
    fields = dict(extra or {})
    if step >= 5:
        fields["onboarded"] = True
    change = {"$max": {"onboarding_step": step}}
    if fields:
        change["$set"] = fields
    previous = await db.users.find_one_and_update({"user_id": user_id, "onboarded": {"$ne": True}}, change,
                                                  projection={"_id": 0, "onboarding_step": 1})
    if previous is not None and step > (previous.get("onboarding_step") or 1):
        await track(user_id, "onboarding_step", {"step": step})


class MeIn(BaseModel):
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    goal_style: Optional[str] = None
    redaction_defaults: Optional[dict] = None
    origin: Optional[str] = None
    onboarding_step: int | None = Field(default=None, ge=1, le=5)
    onboarded: Optional[bool] = None


class EventIn(BaseModel):
    type: str
    data: dict = {}
    attempt_id: uuid.UUID | None = None


class PersonIn(BaseModel):
    nickname: str = Field(min_length=1, max_length=40)
    rel_type: str
    rel_label: str = Field(default="", max_length=40)
    stage: str
    background: str = Field(min_length=1, max_length=500)
    goal: str = ""


class PersonPatch(BaseModel):
    expected_profile_revision: int | None = Field(None, ge=0)
    demographics: Optional[dict] = None
    nickname: Optional[str] = Field(default=None, min_length=1, max_length=40)
    stage: Optional[str] = None
    goal: Optional[str] = None
    background: Optional[str] = Field(default=None, min_length=1, max_length=500)
    archetype_tag: Optional[int] = None
    archived: Optional[bool] = None
    avatar: Optional[str] = Field(default=None, max_length=300_000)
    clear_archetype: bool = False


class IntakeIn(BaseModel):
    fields: dict
    previous: dict[str, str] | None = None


try:
    from . import everyday_catalog, member_pages, personal_demographics, research_scan, research_registration, research_rows, research_couple_export
except ImportError:
    import everyday_catalog, member_pages, personal_demographics, research_scan, research_registration, research_rows, research_couple_export


DEMO_OPTS = quiz_answers.DEMOGRAPHIC_OPTIONS
clean_demo = personal_demographics.clean_demo


def demo_line(d: dict | None) -> str:
    d = {k: v for k, v in (d or {}).items() if k in ("age", *DEMO_OPTS) and v and v != "Prefer not to say"}
    return ", ".join(f"{k}: {v}" for k, v in d.items())


class DemoIn(BaseModel):
    age: Optional[int] = None
    sex: Optional[str] = None
    ethnicity: Optional[str] = None
    education: Optional[str] = None


class Line(BaseModel):
    speaker: Literal["me", "them"]
    text: str


class ExchangeIn(BaseModel):
    conversation: List[Line] = []
    question: str = ""
    context_stage: str = ""
    context_goal: str = ""
    continue_exchange_id: str | None = Field(None, max_length=128)


class MemoryIn(BaseModel):
    kind: str
    text: str


class ApproveIn(BaseModel):
    memories: List[MemoryIn]


class TextIn(BaseModel):
    text: str = Field(min_length=1)


class PracticeTurnIn(TextIn):
    expected_revision: int | None = Field(default=None, ge=0)


class GroupAskIn(BaseModel):
    person_ids: List[str] = Field(min_length=2, max_length=6)
    question: str = Field(min_length=1, max_length=4000)


class OutcomeIn(BaseModel):
    result: Optional[str] = None
    text: str = Field(default="", max_length=4000)


class ChoiceIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    custom: bool = False


class CorrectIn(BaseModel):
    kind: str = Field(pattern="^(seen|explanations)$")
    index: int
    note: str = Field(default="", max_length=300)


class QuickParseIn(BaseModel):
    nickname: str = Field(min_length=1, max_length=40)
    conversation_text: str = Field(default="", max_length=8000)
    image_base64: str = ""


class QuickIn(BaseModel):
    nickname: str = Field(min_length=1, max_length=40)
    what_happened: str = Field(min_length=1, max_length=1000)
    rel_type: str = "talking"
    stage: str = ""
    lines: Optional[List[Line]] = None
    conversation_text: str = Field(default="", max_length=8000)
    image_base64: str = ""
    gender: str = Field(default="", pattern="^(|male|female)$")


class QuickAnswerIn(BaseModel):
    qid: str
    choice: Optional[int] = None
    question: Optional[str] = None
    label: Optional[str] = None


class PracticeIn(BaseModel):
    person_id: str
    topic: str = Field(min_length=1, max_length=500)
    mode: str


class PickIn(BaseModel):
    index: int


class BranchIn(BaseModel):
    round: int
    index: int


class GroupIdsIn(BaseModel):
    person_ids: List[str] = Field(min_length=2, max_length=6)


class ChatGroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    person_ids: List[str] = Field(min_length=2, max_length=6)


class OcrIn(BaseModel):
    image_base64: str
    nickname: str = Field(default="", max_length=80)


class PrepIn(BaseModel):
    situation: str = Field(min_length=3)


class WaitlistIn(BaseModel):
    email: EmailStr
    origin: str = ""


class RoleIn(BaseModel):
    role: str


class AnswerIn(BaseModel):
    choice: int
    note: str = ""
    question: Optional[str] = None
    label: Optional[str] = None


class AboutIn(BaseModel):
    expected_member: Optional[str] = Field(default=None, max_length=256)
    answers: dict[str, Optional[AnswerIn]] = {}
    expected_earlier_answer: Optional[dict] = None
    describe: Optional[str] = None
    mbti: Optional[str] = None
    demographics: Optional[dict] = None
    demographics_patch: Optional[dict] = None


# ---------------- public ----------------
@api.get("/ready")
async def ready(response: Response):
    response.headers["Cache-Control"] = "no-store"
    if database_paused():
        return {"ready": True, "maintenance": True}
    await ops.readiness(db)
    return {"ready": True}


async def notify_ops_admins(name, message):
    key = f"ops:{name}:{ops.day_key()}"
    async for member in db.users.find({**IN_ENV, "role": "admin", "closing": {"$ne": True}}, {"user_id": 1}):
        uid = member["user_id"]
        lease = await accounts.reserve_request(db, uid)
        if lease:
            try:
                await notify(uid, "operations", "Service check needs attention", message, "/admin?view=operations", key)
            finally:
                await db.users.update_one({"user_id": uid}, {"$unset": {"request_leases." + lease: ""}})


@api.post("/ops/check")
async def check_operations(request: Request, response: Response):
    ops.authorize(request.headers.get("Authorization", ""))
    response.headers["Cache-Control"] = "no-store"
    checks = await ops.check_operations(db)
    checks["partner_checkouts"] = await partner_checkout.reconcile(db, ENV, member_info, partner_offers.pair_context)
    await db.ops_state.update_one({"_id": f"service_checks:{ENV}"},
        {"$set": {"checks": checks}, "$currentDate": {"checked_at": True}}, upsert=True)
    if not all(checks.values()):
        response.status_code = 503
        for name, ok in checks.items():
            if not ok:
                await notify_ops_admins(name, ops.CHECK_ALERTS[name])
    return {"checks": checks}


@api.get("/")
async def root():
    return {"ok": True}


@api.get("/media/{mid}")
async def get_media(mid: str):
    m = await db.media.find_one({"id": mid}, {"_id": 0})
    if not m:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(content=m["data"], media_type=m["content_type"], headers={"Cache-Control": "public, max-age=604800"})


@api.get("/public/plans")
async def public_monthly_plans():
    return {"plans": membership_plans.public_plans(), "intro_access": await intro_access.public_status(db)}


@api.get("/public/config")
async def public_config():
    s = await get_settings()
    return {"waitlist_mode": s["waitlist_mode"], "tiktok_url": s.get("tiktok_url"), "people_limit": s["people_limit"],
            "public_quiz": s.get("public_quiz"), "plans": membership_plans.public_plans()}


@api.get("/public/catalog-paths")
async def public_catalog_paths():
    paths = []
    for collection, visible, section in [(db.archetypes, "unlocked", "types"), (db.figures, "published", "figures")]:
        async for row in collection.find({visible: True}, {"slug": 1, "_id": 0}).sort("slug", 1):
            paths.append(f"/{section}/{row['slug']}")
    return paths


@api.get("/public/archetypes")
async def list_archetypes():
    from .public_directory_reads import archetype_directory_projection
    docs = await db.archetypes.find({}, archetype_directory_projection()).sort("number", 1).to_list(None)
    out = []
    for d in docs:
        base = {k: d[k] for k in ("number", "slug", "wave", "unlocked")}
        base["gender"] = d.get("gender", "male")
        if d["unlocked"]:
            base.update(name=d["name"], one_liner=d["one_liner"], texting_tags=d["texting_tags"],
                        red_flag_tags=d["red_flag_tags"], ready=d["ready"])
        out.append(base)
    return out


@api.get("/public/archetypes/{slug}")
async def get_archetype(slug: str):
    d = await db.archetypes.find_one({"slug": slug, "unlocked": True}, {"_id": 0, "sections_edited": 0, "dating_seeded": 0, "editorial": 0})
    if not d:
        raise HTTPException(status_code=404, detail="Not found")
    return await archetype_reader(d)


async def archetype_reader(d):
    cousin = (d.get("sections") or {}).get("closest_cousin") or {}
    if cousin.get("number"):
        c = await db.archetypes.find_one({"number": cousin["number"]}, {"_id": 0, "slug": 1, "name": 1, "unlocked": 1})
        d["cousin_ref"] = c if not c or c.get("unlocked") else {"unlocked": False}
    if d.get("number"):
        figure = await db.figures.find_one({"published": True, "matches.number": d["number"]}, {"_id": 1})
        d["has_public_figures"] = figure is not None
    return d


ORIGINAL_FIGURE_CREDITS = json.loads((ROOT_DIR / "seed" / "figure_original_credits.json").read_text(encoding="utf-8"))
FIGURE_LOCATORS = json.loads((ROOT_DIR / "seed" / "figure_source_locators.json").read_text(encoding="utf-8"))
FIGURE_SEARCH = json.loads((ROOT_DIR / "seed" / "figure_search.json").read_text(encoding="utf-8"))


@api.get("/public/figures")
async def list_figures():
    from .public_directory_reads import figure_directory_inputs
    docs, types = await figure_directory_inputs(db)
    for d in docs:
        reviewed = figure_evidence.assessment(d)
        if reviewed:
            d["lead_review_status"] = figure_evidence.public_lead_status(reviewed)
            scope = (reviewed.get("career") or {}).get("scope")
            if scope:
                d["lead_review_scope"] = scope
        d.pop("sections", None)
        d.pop("lead_review_decision", None)
        d["matches"] = [{**m, "name": types.get(m.get("number"), {}).get("name"),
                        "edition": types.get(m.get("number"), {}).get("gender")} for m in d.get("matches", [])]
        search = FIGURE_SEARCH.get(d["slug"], {})
        d["name_variants"] = search.get("names", [])
        d["field"] = search.get("field", "")
    return docs


@api.get("/public/figures/{slug}")
async def get_figure(slug: str):
    d = await db.figures.find_one({"slug": slug, "published": True}, {"_id": 0, "sections_edited": 0, "dating_seeded": 0, "editorial": 0})
    if not d:
        raise HTTPException(status_code=404, detail="Not found")
    return await figure_reader(d)


async def figure_reader(d):
    if not d.get("photo_attribution") and d.get("photo_url") in ORIGINAL_FIGURE_CREDITS:
        d["photo_attribution"] = ORIGINAL_FIGURE_CREDITS[d["photo_url"]]
    candidates = FIGURE_LOCATORS["profiles"].get(d.get("slug"), [])
    if candidates:
        communication = (d.get("sections") or {}).get("communicates") or {}
        citations = communication.get("example_sources") or []
        current = {(ref.get("example"), ref.get("url")) for ref in citations}
        d["source_locators"] = [ref for ref in candidates if (ref["example"], ref["url"]) in current]
        d["source_locators_checked_on"] = FIGURE_LOCATORS["checked_on"]
    reviewed = figure_evidence.assessment(d)
    d.pop("lead_review_decision", None)
    d.pop("career_review_applied", None)
    if reviewed:
        d["evidence_review"] = {"status": reviewed["status"], "lead_status": figure_evidence.public_lead_status(reviewed),
                                "readers": reviewed["readers"], "career": reviewed["career"],
                                "staff_result": (reviewed["decision"] or {}).get("result"),
                                "staff_reviewed_at": (reviewed["decision"] or {}).get("at")}
    nums = {m.get("number") for m in d.get("matches", [])}
    mp = (d.get("sections") or {}).get("map") or {}
    nums |= {(mp.get("closest") or {}).get("number"), (mp.get("cousin") or {}).get("number")}
    nums |= {x.get("number") for x in mp.get("secondary", [])}
    refs = await db.archetypes.find({"number": {"$in": [n for n in nums if n]}}, {"_id": 0, "number": 1, "slug": 1, "name": 1, "unlocked": 1}).to_list(20)
    d["archetype_refs"] = {str(r["number"]): r if r.get("unlocked") else {"number": r["number"], "unlocked": False} for r in refs}
    return d


@api.post("/public/registration-invite")
async def registration_invite(body: InviteToken, response: Response):
    response.headers["Cache-Control"] = "no-store"
    record = await db.couples_pairs.find_one(
        {"env": ENV, "token_hash": hashlib.sha256(body.token.encode()).hexdigest(),
         "state": "invited", "invitee": None, "expires_at": {"$gt": now()}}, {"_id": 0, "inviter": 1})
    inviter = await member_info(db, record["inviter"], ENV) if record else None
    return {"eligible": bool(is_adult(inviter) and has_member_access(inviter))}


@api.post("/public/waitlist")
async def join_waitlist(body: WaitlistIn):
    settings = await db.settings.find_one({"key": "global"}, {"waitlist_mode": 1})
    if not (settings and settings.get("waitlist_mode")):
        raise HTTPException(409, "Registration is open. Continue to sign up.")
    email = body.email.lower()
    origin = await trends.capture_entry_page(db, body.origin)
    # Live and pilot use separate databases. If a foreign row is ever present, keep it isolated.
    scope = {**IN_ENV, "email": email}
    try:
        await db.waitlist.update_one(scope, {"$setOnInsert": {"email": email, "origin": origin, "ts": now(), "env": ENV}}, upsert=True)
    except DuplicateKeyError:
        if not await db.waitlist.find_one(scope, {"_id": 1}):
            raise HTTPException(503, "Unable to save your signup right now.") from None
    return {"ok": True}


# ---------------- me ----------------
async def me_out(m: dict, *, database=None) -> dict:
    source = database if database is not None else db
    account = {k: m.get(k) for k in ("user_id", "email", "nickname", "avatar", "goal_style", "origin", "tier", "role",
                                     "onboarding_step", "onboarded", "redaction_defaults", "guide_person_id", "created_at", "closing")}
    account["content_revision"] = m.get("content_revision", 0)
    account["tier"] = billing_tier(m)
    account["personal_access"] = has_personal_access(m)
    account["billing_review"] = billing_risk.status(m)
    account["complimentary_access"] = complimentary_access(m)
    account["membership_plan_id"] = membership_plans.member_plan(m)["id"] if account["tier"] == "paid" else None
    account["questionnaire_research"] = personal_demographics.preference(m)
    account["research_notices"] = research_notices.snapshot(m)
    account["answer_history_permission"] = admin_answer_history.preferences(m, ENV)
    async def consent_snapshot():
        return await main_consent.snapshot(source, m, ENV) if m.get("role") == "member" and not m.get("closing") else None
    account["workspace_access"], account["main_consent"], account["research_exports"], account["intro_access"] = await asyncio.gather(
        workspace_access.snapshot(source, m), consent_snapshot(), research_rows.preferences(source, m, ENV), intro_access.status(source, m))
    account["tone_research"] = personal_demographics.preference(m, "tone_research")
    account["everyday_trends_opt_in"] = m.get("everyday_trends_opt_in") is True
    return account


@api.get("/me")
async def get_me(member: dict = Depends(current_member)):
    async def people_counts():
        rows = await db.people.aggregate([{"$match": {"user_id": member["user_id"]}}, {"$facet": {
            "total": [{"$count": "n"}],
            "active": [{"$match": {"archived": {"$ne": True}}}, {"$count": "n"}]}}]).to_list(1)
        counts = rows[0]
        return {key: counts[key][0]["n"] if counts[key] else 0 for key in ("total", "active")}
    context, counts = await asyncio.gather(account_read_context.AccountReadContext.read(db, member), people_counts())
    account = await me_out(member, database=context)
    return {**account, "people_count": counts["total"], "usage": await plan_usage(member, active_people=counts["active"])}


@api.patch("/me")
async def patch_me(body: MeIn, member: dict = Depends(current_member)):
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    if ("onboarding_step" in upd or "onboarded" in upd) and not has_personal_access(member):
        raise subscription_required()
    if "avatar" in upd and len(upd["avatar"]) > 300_000:
        raise HTTPException(status_code=413, detail="Avatar too large")
    if "origin" in upd:
        origin = upd.pop("origin")
        if not member.get("origin"):
            origin = await trends.capture_entry_page(db, origin)
            claimed = await db.users.update_one({"user_id": member["user_id"], "origin": {"$in": [None, ""]}},
                                                {"$set": {"origin": origin}})
            if claimed.modified_count:
                await track(member["user_id"], "signup", {"origin": origin})
            else:
                saved = await db.users.find_one({"user_id": member["user_id"]}, {"origin": 1})
                origin = (saved or {}).get("origin")
            member = {**member, "origin": origin}
    if "onboarding_step" in upd:
        await track(member["user_id"], "onboarding_step", {"step": upd["onboarding_step"]})
    if upd.get("onboarded") and not member.get("onboarded") and (upd.get("onboarding_step") or member.get("onboarding_step") or 1) < 5:
        upd["onboarding_skipped"] = True
    if upd:
        await db.users.update_one({"user_id": member["user_id"]}, {"$set": upd})
    return await me_out({**member, **upd})


ALLOWED_EVENTS = {"session", "upload_started", "upload_completed", "onboarding_opened"}


@api.get("/me/research-notices")
async def my_research_notices(response: Response, member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return research_notices.snapshot(member)


@api.get("/me/main-consent")
async def my_main_consent(response: Response, member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    if member.get("role") != "member" or member.get("closing"):
        raise HTTPException(403, "These data choices belong to an active member account.")
    return await main_consent.snapshot(db, member, ENV)


@api.patch("/me/main-consent")
async def save_main_consent(body: main_consent.Decision, response: Response,
                            member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await main_consent.decide(db, member, ENV, body)


@api.post("/me/main-consent/partner-revocation")
async def revoke_main_partner_sharing(body: main_consent.PartnerRevocation, response: Response,
                                       member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await main_consent.revoke_partner(db, member, ENV, body)


@api.patch("/me/research-notices/{scope}")
async def review_research_notice(scope: research_notices.Scope, body: research_notices.Decision,
                                 response: Response, member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await research_notices.decide(db, member, ENV, scope, body)


@api.patch("/me/research-notices")
async def review_all_research_notices(body: research_notices.BulkDecision, response: Response,
                                     member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await research_notices.decide_all(db, member, ENV, body)


@api.patch("/me/tone-research")
async def set_tone_research(body: personal_demographics.ToneParticipation, response: Response,
                            member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await personal_demographics.participate(db, member, body)


@api.patch("/me/research-exports/{scope}")
async def set_row_export_consent(scope: research_rows.Scope, body: research_rows.ExportConsent, response: Response,
                                 member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await research_rows.consent(db, member, ENV, scope, body)


@api.patch("/me/questionnaire-research")
async def set_questionnaire_research(body: personal_demographics.Participation, response: Response,
                                     member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await personal_demographics.participate(db, member, body)


@api.patch("/me/trends-consent")
async def update_trends_consent(body: trends.Consent, member: dict = Depends(current_member)):
    if body.expected_member is not None and body.expected_member != member["user_id"]:
        raise HTTPException(409, "Your account changed. Refresh your settings before saving.")
    await db.users.update_one({"user_id": member["user_id"]}, {"$set": {"everyday_trends_opt_in": body.enabled}})
    return {"enabled": body.enabled}


@api.get("/me/assessment-repeat")
async def personal_assessment_repeat(response: Response, member: dict = Depends(assessment_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return assessment_evidence.member_view(member, (await get_settings())["quiz"], datetime.now(timezone.utc))


@api.post("/me/assessment-repeat")
async def submit_assessment_repeat(body: assessment_evidence.RepeatSubmission, response: Response,
                                   member: dict = Depends(assessment_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await assessment_evidence.save_repeat(db, member, (await get_settings())["quiz"], body, datetime.now(timezone.utc))


@api.delete("/me/assessment-repeat")
async def delete_assessment_repeat(body: assessment_evidence.RepeatRemoval, member: dict = Depends(current_member)):
    return await assessment_evidence.remove_repeat(db, member, body)


@api.post("/me/couples-activity")
async def couples_activity(body: trends.CouplesActivity, member: dict = Depends(current_member)):
    if body.action == "sample_completed":
        require_adult(member)
    if body.action == "sample_completed" and not has_personal_access(member):
        # Opening the switch is a visit; completing an exercise requires a covered workspace.
        try:
            await active_pair(db, member, write=True)
        except HTTPException as exc:
            if exc.status_code == 404:
                raise subscription_required() from None
            raise
    await trends.record_couples_activity(db, member["user_id"], body.action, now())
    return {"ok": True}


@api.post("/me/event")
async def post_event(body: EventIn, member: dict = Depends(current_member)):
    if body.type not in ALLOWED_EVENTS:
        raise HTTPException(status_code=422, detail="Unknown event")
    if body.type != "session" and not has_personal_access(member):
        raise subscription_required()
    if body.type == "onboarding_opened":
        await db.events.update_one({"_id": f"onboarding_opened:{member['user_id']}"}, {"$setOnInsert": {
            "user_id": member["user_id"], "type": "onboarding_step", "data": {"step": 1}, "ts": now()}}, upsert=True)
    else:
        await track(member["user_id"], body.type, attempt=body.attempt_id)
    return {"ok": True}


SettingsOwner = Annotated[str | None, Header(alias="X-Settings-Member", max_length=200)]


def confirm_settings_owner(member, expected):
    if expected is not None and expected != member["user_id"]:
        raise HTTPException(409, "Your account changed. Reopen Settings before continuing.")


@api.get("/me/export")
async def export_me(member: dict = Depends(current_member), expected_member: SettingsOwner = None):
    confirm_settings_owner(member, expected_member)
    await history_lifecycle.prepare_export(db, member["user_id"])
    if member.get("about"):
        quiz = (await get_settings())["quiz"]
        member = {**member, "about": quiz_answers.display_about(member["about"], quiz)}
    return StreamingResponse(accounts.export_json(db, member), media_type="application/json", headers={
        "Content-Disposition": 'attachment; filename="imladri-export.json"', "Cache-Control": "no-store"})


@api.get("/me/answer-history")
async def own_answer_history(response: Response, member: dict = Depends(current_member),
                             limit: Annotated[int, Query(ge=1, le=200)] = 50,
                             before: Annotated[str, Query(max_length=64)] = ""):
    response.headers["Cache-Control"] = "private, no-store"
    await history_lifecycle.prepare_export(db, member["user_id"])
    query = {"owner_id": member["user_id"], "actor_id": member["user_id"], "purged": {"$ne": True}}
    if before:
        # Opaque event IDs form a stable total order with their observation timestamp.
        anchor = await db.answer_history.find_one({**query, "id": before}, {"at": 1, "id": 1})
        if anchor is None:
            raise HTTPException(409, "This history page changed. Reload your answer history.")
        query["$or"] = [{"at": {"$lt": anchor["at"]}}, {"at": anchor["at"], "id": {"$lt": anchor["id"]}}]
    rows = await db.answer_history.find(query, {"_id": 0, "generation": 0, "source_id": 0}).sort([
        ("at", DESCENDING), ("id", DESCENDING)]).limit(limit + 1).to_list(limit + 1)
    rows = [your_type.expand_event(row) for row in rows]
    return {"items": rows[:limit], "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None}


class HistoryRepair(BaseModel):
    model_config = {"extra": "forbid"}
    limit: int = Field(default=50, ge=1, le=100, strict=True)


@api.get("/me/content-impact")
async def preview_content_clear(response: Response, member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await accounts.content_impact(db, member["user_id"])


@api.delete("/me")
async def delete_me(request: Request, member: dict = Depends(current_member), expected_member: SettingsOwner = None):
    confirm_settings_owner(member, expected_member)
    q = {"user_id": member["user_id"]}
    async with accounts.exclusive_content_clear(db, request, member["user_id"]):
        await clear_couples(db, member["user_id"])
        await your_type.clear_member(db, member["user_id"])
        await history_lifecycle.purge_owner(db, member["user_id"])
        for c in accounts.CONTENT:
            await db[c].delete_many(q)
        await intimacy_answers.clear(db, member["user_id"])
        await research.clear(db, member["user_id"], include_empty=True)
        await db.users.update_one(q, {"$set": {"nickname": "", "avatar": "", "goal_style": "", "onboarding_step": 1, "onboarded": False, "about": {},
                                               "redaction_defaults": {"mask_names": True, "words": []}},
                                      "$inc": {**personal_demographics.ROW_REVISIONS, "questionnaire_research_revision": 1, "tone_research_revision": 1, "intimacy_wording_revision": 1, "your_type_research_revision": 1},
                                      "$unset": {**personal_demographics.ROW_CLEAR_FIELDS, "guide_person_id": "", "onboarding_skipped": "", "questionnaire_research_at": "",
                                                 "questionnaire_research_version": "", "tone_research_at": "", "tone_research_version": "",
                                                 "assessment_repeat": "", "everyday_trends_opt_in": "", "intimacy_wording_review": "", "your_type_research_at": "", "your_type_research_version": "", "research_notice_receipts": ""}})
    return {"ok": True}


@api.post("/me/rejoin")
async def rejoin_personal(user: dict = Depends(require_user)):
    await accounts.rejoin(db, user["user_id"])
    return {"ok": True}


@api.delete("/me/account")
async def close_account(user: dict = Depends(require_user), expected_member: SettingsOwner = None):
    confirm_settings_owner(user, expected_member)
    await accounts.finish_closure(db, user["user_id"])
    return {"ok": True}


@api.post("/clerk/webhook")
async def clerk_account_webhook(request: Request):
    uid = accounts.deleted_identity(await request.body(), request.headers)
    if uid:
        await accounts.finish_closure(db, uid, identity_gone=True)
    return {"ok": True}


@api.get("/quiz")
async def get_quiz(_: dict = Depends(assessment_member)):
    return await assessment_quiz()


async def assessment_quiz():
    # Questionnaire reads do not need private system prompts or billing copy.
    # Facet retains a row for an empty settings collection; lookup uses the
    # existing read-only repository snapshot and published-copy predicates.
    rows = await db.settings.aggregate([{"$match": {"key": "global"}},
        {"$facet": {"stored": [{"$project": {"_id": 0, "quiz": 1}}]}},
        {"$lookup": {"from": "intimacy_question_copy", "pipeline": [
            {"$match": {"changed": True}}, {"$project": {"_id": 0, "section": 1, "topic": 1, "question": 1, "phrase": 1}}],
            "as": "intimacy_copy"}}]).to_list(1)
    stored = rows[0]["stored"][0] if rows[0]["stored"] else {}
    quiz = stored.get("quiz") or {}
    quiz = with_quiz_scoring(quiz) if quiz.get("version") == default_quiz()["version"] else default_quiz()
    edits = rows[0]["intimacy_copy"]
    version, variables = research.catalog(quiz, edits)
    return {**{key: value for key, value in quiz.items() if key != "legacy_wording"},
            "intimacy_copy": edits, "research_catalog_version": version,
            "research_intimacy_signature": research.intimacy_signature(variables)}


def check_questionnaire_owner(member: dict = Depends(current_member), x_questionnaire_owner: str | None = Header(None)):
    if x_questionnaire_owner is not None and x_questionnaire_owner != member["user_id"]:
        raise HTTPException(409, "The signed-in account changed. Reopen your questionnaire.")


@api.get("/me/intimacy-answers", dependencies=[Depends(check_questionnaire_owner)])
async def get_intimacy_answers(response: Response, member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await intimacy_answers.read(db, member["user_id"])


@api.patch("/me/intimacy-answers", dependencies=[Depends(check_questionnaire_owner)])
async def save_intimacy_answer(body: intimacy_answers.AnswerEdit, response: Response,
                               member: dict = Depends(assessment_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await intimacy_answers.save(db, member["user_id"], body)


@api.delete("/me/intimacy-answers", dependencies=[Depends(check_questionnaire_owner)])
async def remove_intimacy_answers(revision: Annotated[int, Query(ge=0, lt=2**53)], response: Response,
                                  item: Annotated[str | None, Query(pattern=r"^i:[a-z0-9-]+:[0-9]+$", max_length=150)] = None,
                                  member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    if item:
        return await intimacy_answers.remove_one(db, member["user_id"], item, revision)
    return await intimacy_answers.clear(db, member["user_id"], revision)


@api.get("/me/intimacy-wording")
async def my_wording_feedback(response: Response, member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return wording_feedback.view(member)


@api.put("/me/intimacy-wording")
async def rate_intimacy_wording(body: wording_feedback.Ratings, member: dict = Depends(assessment_member)):
    return await wording_feedback.update(db, member, body, False, now())


@api.delete("/me/intimacy-wording")
async def withdraw_wording_feedback(body: wording_feedback.Revision, member: dict = Depends(current_member)):
    return await wording_feedback.update(db, member, body, True, now())


@api.get("/me/research")
async def get_research(response: Response, member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await research.status(db, member["user_id"])


@api.put("/me/research")
async def put_research(body: research.Submission, response: Response, member: dict = Depends(assessment_member)):
    response.headers["Cache-Control"] = "private, no-store"
    quiz = (await get_settings())["quiz"]
    return await research.submit(db, member["user_id"], ENV, body, quiz, await intimacy_catalog.overrides(db))


@api.delete("/me/research")
async def delete_research(revision: Annotated[int, Query(ge=0, lt=2**53)], response: Response,
                          member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await research.withdraw(db, member["user_id"], revision)


@api.get("/me/about")
async def get_about(member: dict = Depends(assessment_member)):
    stored = await db.settings.find_one({"key": "global"}, {"_id": 0, "quiz": 1}) or {}
    quiz = stored.get("quiz") or {}
    quiz = with_quiz_scoring(quiz) if quiz.get("version") == default_quiz()["version"] else default_quiz()
    about = member.get("about") or {}
    shown = archetype_copy.account({"about": quiz_answers.display_about(about, quiz)})["about"]
    return {"about": shown, "summary": about_summary(about, quiz), "radar": radar(about, quiz)}


@api.get("/me/assessments", dependencies=[Depends(check_questionnaire_owner)])
async def get_assessments(response: Response, member: dict = Depends(assessment_member), include_intimacy: bool = False):
    response.headers["Cache-Control"] = "private, no-store"
    intimate = {}
    if include_intimacy:
        async def current_profile():
            current = await db.users.find_one({"user_id": member["user_id"], "closing": {"$ne": True}}, {"_id": 0})
            if current is None:
                raise HTTPException(403, "These activities are available to adults with an active account.")
            current = {**current, "env": ENV, "role": effective_role(current.get("role"))}
            await workspace_access.assessment_member(db, current)
            return current
        def adult_profile(current):
            try:
                return research.age_band(current) is not None
            except HTTPException as error:
                if error.status_code != 403:
                    raise
                return False
        async def saved_answers():
            current = await current_profile()
            if not adult_profile(current):
                # An unknown/minor profile must never fetch raw answer rows.
                return current, None
            row = await db.intimacy_answers.find_one({"_id": current["user_id"]}) or {}
            # Recheck current profile/access after the read, so a concurrent
            # age/access withdrawal cannot expose the earlier adult snapshot.
            current = await current_profile()
            return current, row if adult_profile(current) else None
        quiz, (member, row) = await asyncio.gather(assessment_quiz(), saved_answers())
        intimate = {"intimacy_eligible": row is not None, "intimacy_answers":
            intimacy_answers.display(row, intimacy_answers.catalog_variables(quiz["intimacy_copy"])) if row is not None else None}
    else:
        quiz = await assessment_quiz()
    about = member.get("about") or {}
    shown = archetype_copy.account({"about": quiz_answers.display_about(about, quiz)})["about"]
    return {"owner_id": member["user_id"], "quiz": quiz, "about": shown,
            "summary": about_summary(about, quiz), "radar": radar(about, quiz), **intimate}


def safe_quiz_id(qid):
    return isinstance(qid, str) and bool(qid.strip()) and "." not in qid and "\x00" not in qid and not qid.startswith("$") and qid not in ("__proto__", "constructor", "prototype")


def submitted_answer(quiz, question, body):
    try:
        return quiz_answers.selected_answer(quiz, question, body.choice, body.question, body.label, getattr(body, "note", ""))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


async def initialize_legacy_about(member):
    # Dotted writes create missing parents, but MongoDB cannot walk an explicit
    # legacy null. Only replace the null if another tab has not initialized it.
    about = member.get("about", {})
    paths = {"about": {}} if about is None else {
        f"about.{key}": empty for key, empty in (("answers", {}), ("demographics", {}), ("skipped", []))
        if key in about and about[key] is None}
    for path, empty in paths.items():
        await db.users.update_one({"user_id": member["user_id"], path: {"$type": "null"}}, {"$set": {path: empty}})


def quiz_answer_counts(about, quiz):
    return {section: sum(quiz_answers.resolve_answer(about, quiz, q) is not None for q in quiz[section])
            for section in quiz_answers.SETS}


async def record_quiz_milestones(uid, quiz, counts):
    # Preserve the first-achievement meaning of the three historical event names.
    events = []
    for section, answered in counts.items():
        size = len(quiz[section])
        if size and answered >= (size + 1) // 2:
            events.append("quiz_short_half" if section == "short" else f"quiz_{section}")
        if size and answered == size:
            events.append("quiz_short" if section == "short" else f"quiz_{section}_complete")
    if events:
        recorded = await db.events.distinct("type", {"user_id": uid, "type": {"$in": events}})
        for event in set(events) - set(recorded):
            await db.events.update_one({"_id": f"{event}:{uid}"}, {"$setOnInsert": {
                "user_id": uid, "type": event, "data": {}, "ts": now()}}, upsert=True)


@api.put("/me/about")
async def put_about(body: AboutIn, member: dict = Depends(assessment_member)):
    if body.expected_member is not None and body.expected_member != member["user_id"]:
        raise HTTPException(409, "The signed-in account changed. Reopen Personal.")
    s = await get_settings()
    qs = quiz_answers.questions_by_id(s["quiz"])
    owner_query = {"user_id": member["user_id"]}
    if body.expected_earlier_answer is not None:
        key = next(iter(body.answers), None)
        if not key or not safe_quiz_id(key) or body.answers != {key: None}:
            raise HTTPException(422, "Select one earlier answer to remove.")
        path = f"about.answers.{key}"
        entry = await db.users.find_one(owner_query, {path: 1}) or {}
        saved_about = entry.get("about") or {}
        earlier = quiz_answers.display_about(saved_about, s["quiz"])["previous_answers"]
        if key not in earlier or body.expected_earlier_answer != {key: earlier[key]}:
            raise HTTPException(409, "This answer changed. Reload Personal to review it.")
        # Another tab may answer the republished question between this read and the removal.
        owner_query[path] = {"$eq": saved_about["answers"][key], "$exists": True}
    changes, removals = {"about.updated_at": now()}, {}
    if body.mbti is not None and (body.mbti == "" or body.mbti.upper() in TYPES):
        changes["about.mbti"] = body.mbti.upper()
    for qid, a in body.answers.items():
        if not safe_quiz_id(qid):
            continue
        if a is None:
            removals[f"about.answers.{qid}"] = ""
        elif qid in qs:
            selected = submitted_answer(s["quiz"], qs[qid], a)
            if selected:
                changes[f"about.answers.{qid}"] = selected
        elif a.question is not None:
            raise HTTPException(409, "This question was removed. Refresh Personal before saving it.")
    if body.describe is not None:
        changes["about.describe"] = body.describe.strip()[:2000]
    if body.demographics is not None:
        changes["about.demographics"] = clean_demo(body.demographics, own=True)
    elif body.demographics_patch:
        added, deleted = personal_demographics.demographics_patch(body.demographics_patch)
        changes.update(added)
        removals.update(deleted)
    saved_age = changes.get("about.demographics.age", changes.get("about.demographics", {}).get("age"))
    if saved_age is not None and saved_age < 18:
        personal_demographics.protect_minor(changes, removals)
    update = {"$set": changes}
    if saved_age is not None and saved_age < 18:
        update["$inc"] = {**personal_demographics.ROW_REVISIONS, "questionnaire_research_revision": 1, "tone_research_revision": 1, "intimacy_wording_revision": 1, "your_type_research_revision": 1}
    # A manually saved or removed answer supersedes a previous Quick Question skip.
    replaced_skips = [qid for qid in body.answers if f"about.answers.{qid}" in changes or f"about.answers.{qid}" in removals]
    if replaced_skips:
        update["$pull"] = {"about.skipped": {"$in": replaced_skips}}
        removals.update({f"about.skip_editions.{qid}": "" for qid in replaced_skips})
    if removals:
        update["$unset"] = removals
    await initialize_legacy_about(member)
    stored = await individual_history.save(db, member["user_id"], owner_query, update, s["quiz"])
    if stored is None:
        raise HTTPException(409, "This answer changed. Reload Personal to review it.")
    about = stored["about"]
    if saved_age is not None and saved_age < 18:
        await intimacy_answers.clear(db, member["user_id"])
        await research.clear(db, member["user_id"])
        await your_type.clear_member(db, member["user_id"])
    summ = about_summary(about, s["quiz"])
    if summ["short_done"] or summ["describe"]:
        await advance(member["user_id"], 2)
    await record_quiz_milestones(member["user_id"], s["quiz"], {k: summ[f"{k}_done"] for k in quiz_answers.SETS})
    shown = archetype_copy.account({"about": quiz_answers.display_about(about, s["quiz"])})["about"]
    return {"about": shown, "summary": summ, "radar": radar(about, s["quiz"])}


async def member_pattern_evidence(uid: str, limit: int) -> list[str]:
    eligible = []
    cursor = db.patterns.find({"user_id": uid}, {"_id": 0}).sort("id", 1)
    while len(eligible) < limit:
        batch = await cursor.to_list(50)
        if not batch:
            break
        assessed = await pattern_evidence.assess(db, uid, batch)
        eligible.extend(pattern_evidence.description(row) for row in assessed if row["evidence_status"] != "tentative")
    return eligible[:limit]


def ranking_complete(items: list | None, roster: list) -> bool:
    if not roster or not isinstance(items, list) or len(items) != len(roster):
        return False
    numbers = set()
    for item in items:
        if not isinstance(item, dict) or type(item.get("number")) is not int or type(item.get("score")) is not int:
            return False
        if not 0 <= item["score"] <= 100 or not isinstance(item.get("line"), str) or not item["line"].strip():
            return False
        numbers.add(item["number"])
    return numbers == {row["number"] for row in roster}


def normalize_ranking(output: dict, roster: list) -> list[dict]:
    items = output.get("items") if isinstance(output, dict) else None
    if not ranking_complete(items, roster):
        raise HTTPException(502, "The full archetype comparison could not be generated. Please try again.")
    by_number = {row["number"]: row for row in roster}
    ranked = []
    for item in items:
        row = by_number[item["number"]]
        ranked.append({"number": row["number"], "name": row["name"], "slug": row["slug"],
                       "score": item["score"], **archetype_copy.new_caption(row, raw=item["line"])})
    return sorted(ranked, key=lambda item: (-item["score"], item["number"]))


async def comparison_context(member, settings, gender):
    summary, patterns = await saved_profile(member, settings["quiz"])
    profile = saved_profile_text(summary, patterns)
    roster = await db.archetypes.find({"gender": gender, "unlocked": True},
        {"_id": 0, "number": 1, "name": 1, "slug": 1, "one_liner": 1}).sort("number", 1).to_list(None)
    signature = context_signature(profile, gender, roster, settings["prompts"]["archetype_ranking"])
    return summary["personality"]["code"], profile, roster, signature


@api.get("/me/type-compare")
async def type_compare(gender: str = "male", member: dict = Depends(assessment_member)):
    gender = "female" if gender == "female" else "male"
    s = await get_settings()
    code, profile_text, arch, sig = await comparison_context(member, s, gender)
    if not code or not arch:
        return {"code": code, "gender": gender, "items": []}
    key = "type_compare" if gender == "male" else "type_compare_female"
    roster = "\n".join(f"#{a['number']} {a['name']}: {a['one_liner']}" for a in arch)

    async def with_people(result):
        tagged = {}
        query = {"user_id": member["user_id"], "archived": {"$ne": True}, "deleting": {"$ne": True},
                 "rel_type": {"$in": list(ROMANTIC)}, "archetype_tag": {"$in": [row["number"] for row in arch]}}
        async for person in db.people.find(query, {"id": 1, "nickname": 1, "archetype_tag": 1}):
            tagged.setdefault(person["archetype_tag"], []).append({"id": person["id"], "nickname": person["nickname"]})
        return {**result, "gender": gender, "items": [{**i, "people": tagged.get(i["number"], [])} for i in result["items"]]}

    cached = (member.get("about") or {}).get(key) or {}
    if cached.get("sig") == sig and ranking_complete(cached.get("items"), arch):
        return await with_people(archetype_copy.ranking_cache(cached, arch))
    # A sponsored partner may complete assessments without acquiring access to
    # Individual conversations. Comparison is an existing assessment feature.
    payer = await workspace_access.assessment_payer(db, member)
    await require_generation_access(payer, assessment=True)
    started = now()
    out = await ai_json("archetype_ranking", f"You are Imladri. Using the member's saved profile, score how well their stated preferences could fit EACH {gender} dating archetype "
                        "(0-100). Give one short line (max 14 words) addressing the member as 'you'. Treat scores as estimates. Use only stated identities and preserve the source of each pattern. "
                        "Use everyday words. Return JSON {\"items\": [{\"number\": n, \"score\": integer 0-100, \"line\": \"...\"}]} covering every archetype exactly once.",
                        f"Member's saved profile:\n{profile_text}\nArchetypes:\n{roster}", settings=s)
    items = normalize_ranking(out, arch)
    res = {"code": code, "sig": sig, "items": items, "started": started}
    changed = HTTPException(409, "Your profile or the archetype directory changed. Try the comparison again.")
    current = await db.users.find_one({"user_id": member["user_id"], "closing": {"$ne": True}})
    if not current or (await comparison_context(current, await get_settings(), gender))[3] != sig:
        raise changed
    update = await db.users.update_one({"_id": current["_id"], "closing": {"$ne": True},
        "about.updated_at": (current.get("about") or {}).get("updated_at"),
        "$or": [{f"about.{key}.started": {"$exists": False}}, {f"about.{key}.started": {"$lte": started}}]},
        {"$set": {f"about.{key}": res}})
    if not update.matched_count:
        raise changed
    return await with_people(res)


@api.get("/overview")
async def overview(member: dict = Depends(subscribed_member)):
    uid = member["user_id"]
    today = datetime.now(timezone.utc).date()
    days = {(today - timedelta(days=i)).isoformat(): 0 for i in range(29, -1, -1)}
    window = {"$gte": min(days), "$lt": (today + timedelta(days=1)).isoformat()}
    s, records = await asyncio.gather(get_settings(), member_overview.read(db, uid, window))
    people, exs, preps, sessions, group_chats, quick_reads = (records[key] for key in
        ("people", "exchanges", "preps", "practice", "group_chats", "quick_advice"))
    names = {p["id"]: p["nickname"] for p in people}
    groups = {g: {"count": 0, "people": []} for g in REL_GROUPS}
    for p in people:
        if p.get("archived"):
            continue
        g = groups[GROUP_OF.get(p["rel_type"], "other")]
        g["count"] += 1
        if len(g["people"]) < 6:
            g["people"].append({"id": p["id"], "nickname": p["nickname"]})
    feed = []

    def append_feed(item, ts):
        instant = report_dates.instant(ts)
        if instant is not None:
            # Preserve existing valid ISO references. Historical BSON dates
            # become explicit UTC strings; malformed dates never enter sorting.
            feed.append({**item, "ts": ts if isinstance(ts, str) else instant.isoformat()})

    def add(type_, ts, pid, text, tab):
        if pid in names:
            append_feed({"type": type_, "person_id": pid, "nickname": names[pid], "text": text, "tab": tab}, ts)

    for p in people:
        add("person", p.get("created_at"), p["id"], f"You added {p['nickname']}", "profile")
        add("report", (p.get("report") or {}).get("updated_at"), p["id"], f"The read on {p['nickname']} was updated", "report")
    for e in exs:
        add("conversation" if e.get("has_conversation") else "question", e["created_at"], e["person_id"],
            f"{'Conversation shared' if e.get('has_conversation') else 'You asked about'} {names.get(e['person_id'], '')}", "chat")
        add("outcome", e.get("outcome_at") if e.get("outcome") else None, e["person_id"], f"You logged what happened with {names.get(e['person_id'], '')}", "chat")
    for x in preps:
        add("prep", x["created_at"], x["person_id"], f"Game plan for {names.get(x['person_id'], '')}: {x['situation'][:60]}", "prepare")
        add("outcome", x.get("outcome_at"), x["person_id"], f"You followed up on a game plan for {names.get(x['person_id'], '')}", "prepare")
    for session in sessions:
        pid = session["person_id"]
        add("practice", session.get("created_at"), pid, f"You started practice with {names.get(pid, '')}", "practice")
    for chat in group_chats:
        ids = chat.get("person_ids") or []
        if len(ids) >= 2 and all(pid in names for pid in ids):
            append_feed({"type": "group", "person_ids": ids,
                         "text": f"You chatted about {len(ids)} people together", "tab": "group"}, chat.get("created_at"))
    for row in quick_reads:
        append_feed({"type": "quick_advice", "text": "You asked for Quick Advice",
                     "tab": "quick-advice", "advice_id": row["id"]}, row.get("created_at"))
    feed.sort(key=lambda f: report_dates.instant(f["ts"]), reverse=True)
    # Count the whole UTC window independently of the recent-activity display limits.
    for result in records["counts"]:
        for row in result:
            days[row["_id"]["day"]] += row["n"]
    pending_rows, review_rows = records["pending"], records["review"]
    checkins = [{"exchange_id": e["id"], "person_id": e["person_id"], "nickname": names[e["person_id"]],
                 "ts": e["created_at"], "choice": e["choice"]["label"]} for e in pending_rows]
    unreviewed = [{"exchange_id": e["id"], "person_id": e["person_id"], "nickname": names[e["person_id"]],
                   "count": len(e["proposed_memories"])} for e in review_rows]
    return {"groups": groups, "total": sum(g["count"] for g in groups.values()), "feed": feed[:20],
            "activity": [{"day": d, "count": c} for d, c in days.items()], "checkins": checkins, "unreviewed": unreviewed,
            "checkin_count": records["pending_count"], "review_count": records["review_count"],
            "unread": records["unread"], "about": about_summary(member.get("about"), s["quiz"]), "axes": s["quiz"]["axes"],
            "radar": radar(member.get("about"), s["quiz"])}


@api.get("/intake-schema")
async def intake_schema(_: dict = Depends(subscribed_member)):
    return INTAKE


# ---------------- people ----------------
async def person_out(p: dict, profile_sig: str | None = None, *, detail_meter: dict | None = None) -> dict:
    p = {key: value for key, value in p.items() if key not in history_lifecycle.PRIVATE_FIELDS}
    p = dict(p)
    if p.get("report") or p.get("type_match"):
        if profile_sig is None:
            member = await db.users.find_one({"user_id": p["user_id"]}, {"_id": 0, "user_id": 1, "about": 1})
            if member:
                profile = await saved_profile(member, (await get_settings())["quiz"])
                profile_sig = context_signature(saved_profile_text(*profile))
        if p.get("report") and (not profile_sig or p["report"].get("profile_sig") != profile_sig):
            p["report_stale"] = True
            if p.get("report_status") == "ready":
                p["report_status"] = "stale"
        match = p.get("type_match")
        if match and (match.get("tag") != p.get("archetype_tag") or not profile_sig or match.get("profile_sig") != profile_sig):
            p["type_match"] = {"stale": True}
    p.pop("report_run", None)
    p = archetype_copy.person(p)
    p["meter"] = detail_meter if detail_meter is not None else await meter(p)
    return p


@api.get("/people/lookup")
async def lookup_people(q: Annotated[str, Query(min_length=1, max_length=80)], member: dict = Depends(subscribed_member),
                        page_size: Annotated[int, Query(ge=1, le=50)] = 20, cursor: Annotated[str, Query(max_length=1024)] = ""):
    name = q.strip()
    if not name:
        raise HTTPException(422, "Enter a nickname.")
    criteria = {"user_id": member["user_id"], "deleting": {"$ne": True}, "nickname": {"$regex": re.escape(name), "$options": "i"}}
    return await member_pages.page(db.people, criteria, size=page_size, cursor=cursor, field="nickname", order=1,
                                  projection={"_id": 0, "id": 1, "nickname": 1, "archived": 1, "rel_type": 1})


@api.get("/people")
async def list_people(bg: BackgroundTasks, member: dict = Depends(subscribed_member),
                      page_size: Annotated[int, Query(ge=0, le=200)] = 0, cursor: Annotated[str, Query(max_length=1024)] = ""):
    people_scope = {"user_id": member["user_id"], "deleting": {"$ne": True}}
    counts = {group: 0 for group in REL_GROUPS}
    async for count in db.people.aggregate([{"$match": {**people_scope, "archived": {"$ne": True}}},
                                            {"$group": {"_id": "$rel_type", "total": {"$sum": 1}}}]):
        counts[GROUP_OF.get(count["_id"], "other")] += count["total"]
    counts["all"] = sum(counts.values())
    listing = await member_pages.page(db.people, people_scope, field="last_activity",
                                      size=page_size or 200, cursor=cursor)
    docs = listing.pop("items")
    s, detail_meters = await asyncio.gather(get_settings(), listed_people_meters(docs))
    profile = await saved_profile(member, s["quiz"])
    code = profile[0]["personality"]["code"]
    profile_sig = context_signature(saved_profile_text(*profile))
    for p in docs:
        p["can_message"] = True
        tm = p.get("type_match") or {}
        if not cursor and code and p["rel_type"] in ROMANTIC and p.get("archetype_tag") and (tm.get("code"), tm.get("tag")) != (code, p["archetype_tag"]) and p["id"] not in TM_RUNNING:
            TM_RUNNING.add(p["id"])
            bg.add_task(ai_meter.in_context, "background", type_match_task, p["id"], code, p["archetype_tag"])
        if tm and (tm.get("code"), tm.get("tag")) != (code, p.get("archetype_tag")):
            p["type_match"] = None
    return {**listing, "active_counts": counts, "people": [await person_out(p, profile_sig, detail_meter=detail_meters[p["id"]]) for p in docs], "limit": s["people_limit"], "tier": billing_tier(member), "my_type": code,
            "usage": await plan_usage(member)}


TM_RUNNING: set = set()


async def type_match_task(pid: str, code: str, tag: int):
    result = None
    started = now()
    try:
        person = await db.people.find_one({"id": pid}, {"_id": 0})
        if not person or person.get("archetype_tag") != tag or person["rel_type"] not in ROMANTIC:
            return
        member = await db.users.find_one({"user_id": person["user_id"]}, {"_id": 0})
        if not member or not await has_reply_allowance(member):
            return
        profile = await saved_profile(member, (await get_settings())["quiz"])
        if profile[0]["personality"]["code"] != code:
            return
        text = saved_profile_text(*profile)
        result = {"code": code, "tag": tag, "profile_sig": context_signature(text), "started": started, "line": "", "failed": True}
        a = await db.archetypes.find_one({"number": tag, "unlocked": True}, {"_id": 0, "name": 1, "one_liner": 1})
        if not a:
            return
        out = await ai_json("archetype_line", "You are Imladri. Write ONE short, punchy line (max 18 words) addressing the member as 'you'. "
                            "Use their stated preferences and type to reflect on the dating archetype they selected. Treat the comparison as tentative. "
                            "Everyday words, no clinical labels. Return JSON {\"line\": \"...\"}",
                            f"Member's saved profile:\n{text}\nSelected archetype: #{arch_no(tag)} {a['name']} - {a['one_liner']}")
        line = out.get("line")
        if isinstance(line, str) and line.strip():
            result.update(**archetype_copy.new_caption(a, 200, raw=line), failed=False)
    except Exception as e:
        log.warning("type match failed %s", e)
    finally:
        TM_RUNNING.discard(pid)
    if result:
        await db.people.update_one({"id": pid, "archetype_tag": tag, "$or": [
            {"type_match.started": {"$exists": False}}, {"type_match.started": {"$lte": started}}]}, {"$set": {"type_match": result}})


@api.post("/people/{pid}/type-match/refresh")
async def refresh_type_match(pid: str, member: dict = Depends(subscribed_member)):
    person = await own_person(pid, member)
    await require_generation_access(member)
    code = about_summary(member.get("about"), (await get_settings())["quiz"])["personality"]["code"]
    if not code or person["rel_type"] not in ROMANTIC or not person.get("archetype_tag"):
        raise HTTPException(422, "Choose a personality type and a dating archetype first.")
    await type_match_task(pid, code, person["archetype_tag"])
    return await person_out(await own_person(pid, member))


@api.patch("/people/{pid}/demographics")
async def patch_demographics(pid: str, body: DemoIn, member: dict = Depends(subscribed_member)):
    p = await own_person(pid, member)
    d = clean_demo(body.model_dump())
    await db.people.update_one({"id": pid}, {"$set": {"demographics": d, "last_activity": now()}})
    return await person_out({**p, "demographics": d})


async def activate_person(member, person=None, pid=None, fields=None):
    try:
        from . import person_answer_history
    except ImportError:
        import person_answer_history
    if not has_personal_access(member):
        raise subscription_required()
    if person is not None:
        prepared = await person_answer_history.prepare_insert(db, person, member, FIELD_LABELS)
        await db.people.insert_one(prepared)
        await answer_history.flush(db, "people", {"id": person["id"], "user_id": member["user_id"]})
    else:
        owner = {"user_id": member["user_id"], "id": pid, "archived": True}
        update = {"$set": fields, "$unset": {"free_slot": ""}}
        if "intake.her_goal" in fields:
            await person_answer_history.save(db, pid, member, owner,
                {"intake.her_goal": fields["intake.her_goal"]}, FIELD_LABELS, now(), extra_update=update)
        else:
            await db.people.update_one(owner, update)


@api.post("/people")
async def create_person(body: PersonIn, member: dict = Depends(subscribed_member)):
    if body.rel_type not in GROUP_OF:
        raise HTTPException(status_code=422, detail="Unknown relationship type")
    if body.stage not in (ROMANTIC_STAGES if body.rel_type in ROMANTIC else OTHER_STAGES):
        raise HTTPException(status_code=422, detail="Unknown stage for this relationship type")
    p = {"id": new_id(), "user_id": member["user_id"], **body.model_dump(), "intake": {"her_goal": body.goal} if body.goal else {},
         "archetype_tag": None, "archived": False, "report": None, "report_status": "empty",
         "created_at": now(), "last_activity": now()}
    await activate_person(member, person=p)
    await track(member["user_id"], "person_added", {"rel_type": body.rel_type})
    await advance(member["user_id"], 3, None if member.get("guide_person_id") else {"guide_person_id": p["id"]})
    return await person_out(strip(p))


@api.get("/people/{pid}")
async def get_person(pid: str, member: dict = Depends(subscribed_member)):
    p = await own_person(pid, member)
    p["can_message"] = True
    return await person_out(p)


@api.get("/people/{pid}/saved")
async def person_saved(pid: str, member: dict = Depends(subscribed_member), summary: bool = False):
    p = await own_person(pid, member)
    q = {"person_id": pid, "user_id": member["user_id"]}
    details = [{"key": "background", "label": "Background", "value": p.get("background")}] if p.get("background") else []
    details += [{"key": k, "label": FIELD_LABELS[k], "value": v} for k, v in (p.get("intake") or {}).items() if k in FIELD_LABELS and v]
    if summary:
        return {"details": details, "report": {"exists": True} if p.get("report") else None,
                "group_chats": await db.group_chats.count_documents({"user_id": member["user_id"], "person_ids": pid})}
    exs = await db.exchanges.find(q, {"_id": 0, "id": 1, "question": 1, "conversation": 1, "choice": 1, "outcome": 1, "created_at": 1}).sort("created_at", -1).to_list(500)
    return {
        "person": {"nickname": p["nickname"], "goal": p.get("goal") or "", "has_photo": bool(p.get("avatar")), "archetype_tag": p.get("archetype_tag")},
        "details": details,
        "report": {"exists": True} if p.get("report") else None,
        "memories": await db.memories.find(q, {"_id": 0, "id": 1, "kind": 1, "text": 1, "created_at": 1}).sort("created_at", -1).to_list(1000),
        "exchanges": [{"id": e["id"], "created_at": e["created_at"], "question": (e.get("question") or "")[:160], "messages": len(e.get("conversation") or []),
                       "choice": (e.get("choice") or {}).get("label"), "outcome": e.get("outcome")} for e in exs],
        "preps": await db.preps.find(q, {"_id": 0, "id": 1, "situation": 1, "created_at": 1}).sort("created_at", -1).to_list(500),
        "corrections": await db.corrections.find(q, {"_id": 0, "id": 1, "text": 1, "note": 1, "created_at": 1}).sort("created_at", -1).to_list(500),
        "practice": await db.practice.find(q, {"_id": 0, "id": 1, "topic": 1, "mode": 1, "created_at": 1}).sort("created_at", -1).to_list(500),
        "group_chats": await db.group_chats.count_documents({"user_id": member["user_id"], "person_ids": pid}),
    }


@api.get("/people/{pid}/saved/{section}")
async def list_saved_section(pid: str, section: str, member: dict = Depends(subscribed_member),
                             page_size: Annotated[int, Query(ge=1, le=100)] = 30, cursor: Annotated[str, Query(max_length=1024)] = ""):
    await own_person(pid, member)
    return await member_pages.saved_page(db, {"user_id": member["user_id"], "person_id": pid}, section, page_size, cursor)


@api.get("/people/{pid}/export")
async def person_export(pid: str, member: dict = Depends(subscribed_member)):
    await own_person(pid, member)
    await history_lifecycle.prepare_export(db, member["user_id"])
    p = await own_person(pid, member)
    return StreamingResponse(accounts.person_export_json(db, p), media_type="application/json",
                             headers={"Cache-Control": "no-store"})


@api.delete("/people/{pid}/report")
async def delete_report(pid: str, member: dict = Depends(subscribed_member)):
    await own_person(pid, member)
    await db.people.update_one({"id": pid}, {"$set": {"report": None, "report_status": "empty", "working_read": None}, "$unset": {"report_run": ""}})
    return {"ok": True}


@api.delete("/corrections/{cid}")
async def delete_correction(cid: str, member: dict = Depends(subscribed_member)):
    c = await db.corrections.find_one({"id": cid, "user_id": member["user_id"]}, {"_id": 0})
    if not c:
        raise HTTPException(status_code=404, detail="Not found")
    await db.corrections.delete_one({"id": cid, "user_id": member["user_id"]})
    await invalidate_person_report(c["person_id"], member["user_id"])
    await db.exchanges.update_one({"id": c["exchange_id"], "user_id": member["user_id"]}, {"$pull": {"corrected": c["key"]}})
    return {"ok": True}


@api.delete("/preps/{xid}")
async def delete_prep(xid: str, member: dict = Depends(subscribed_member)):
    query = {"id": xid, "user_id": member["user_id"]}
    prep = await db.preps.find_one_and_update(query, {"$set": {"deleting": True}})
    if not prep:
        raise HTTPException(status_code=404, detail="Not found")
    owner = {"user_id": member["user_id"], "person_id": prep["person_id"]}
    await game_plans.clear_deleted_plan(db, owner, xid, had_outcome=bool(prep.get("outcome")))
    await db.preps.delete_one({**query, "deleting": True})
    return {"ok": True}


@api.patch("/people/{pid}")
async def patch_person(pid: str, body: PersonPatch, member: dict = Depends(subscribed_member)):
    try:
        from . import person_answer_history
    except ImportError:
        import person_answer_history
    p = await own_person(pid, member)
    upd = {k: v for k, v in body.model_dump(exclude={"clear_archetype", "expected_profile_revision"}).items() if v is not None}
    for k in ("nickname", "background"):
        if k in upd and not upd[k].strip():
            raise HTTPException(status_code=422, detail=f"{k.title()} can't be empty")
    if "demographics" in upd:
        values = upd["demographics"]
        cleaned = clean_demo(values)
        if any(value not in (None, "") and key not in cleaned for key, value in values.items()):
            raise HTTPException(422, "Check the age and demographic selections before saving.")
        upd["demographics"] = cleaned
    if body.clear_archetype:
        upd["archetype_tag"] = None
    if upd.get("avatar") and not upd["avatar"].startswith(("data:image/png", "data:image/jpeg", "data:image/webp")):
        raise HTTPException(status_code=422, detail="Photo must be an image")
    if "archetype_tag" in upd and upd["archetype_tag"] is not None and p["rel_type"] not in ROMANTIC:
        raise HTTPException(status_code=422, detail="Archetype tags are for romantic people only")
    if (upd.get("archetype_tag") is not None and upd["archetype_tag"] != p.get("archetype_tag")
            and not await db.archetypes.find_one({"number": upd["archetype_tag"], "unlocked": True}, {"_id": 1})):
        raise HTTPException(status_code=422, detail="Unknown archetype")
    if "stage" in upd and upd["stage"] not in (ROMANTIC_STAGES if p["rel_type"] in ROMANTIC else OTHER_STAGES):
        raise HTTPException(status_code=422, detail="Unknown stage for this relationship type")
    if "goal" in upd:
        upd["intake.her_goal"] = upd["goal"]
    upd["last_activity"] = now()
    if body.expected_profile_revision is not None or (body.archived is None and any(
            key in upd for key in ("nickname", "stage", "background", "demographics", "goal", "archetype_tag"))):
        if body.archived is not None:
            raise HTTPException(422, "Save the profile and change its archive status separately.")
        compare = {"id": pid, "user_id": member["user_id"], "deleting": {"$ne":True}}
        if body.expected_profile_revision is not None:
            compare["profile_revision"] = ({"$in":[0,None]} if body.expected_profile_revision == 0 else body.expected_profile_revision)
        if "intake.her_goal" in upd:
            changed = await person_answer_history.save(db, pid, member, compare,
                {"intake.her_goal": upd["intake.her_goal"]}, FIELD_LABELS, now(),
                extra_update={"$set": upd, "$inc": {"profile_revision": 1}}, conflict_returns_none=True)
            matched = changed is not None
        else:
            saved = await db.people.update_one(compare, {"$set":upd, "$inc":{"profile_revision":1}})
            matched = bool(saved.matched_count)
        if matched and any(key in upd and upd[key] != p.get(key) for key in ("nickname", "background", "stage", "goal", "demographics", "archetype_tag")):
            await invalidate_person_report(pid, member["user_id"])
        if not matched:
            current = await own_person(pid, member)
            matches = all(value == current.get(key) for key, value in upd.items() if key not in {"last_activity","intake.her_goal"})
            if not matches:
                raise HTTPException(409, "Another edit was saved. Review this profile before applying your changes.")
    elif body.archived is False:
        if p.get("archived"):
            await activate_person(member, pid=pid, fields=upd)
        else:
            if "intake.her_goal" in upd:
                await person_answer_history.save(db, pid, member, {"id": pid, "archived": {"$ne": True}},
                    {"intake.her_goal": upd["intake.her_goal"]}, FIELD_LABELS, now(), extra_update={"$set": upd})
            else:
                await db.people.update_one({"id": pid, "archived": {"$ne": True}}, {"$set": upd})
    else:
        op = {"$set": upd}
        if body.archived is True:
            op["$unset"] = {"free_slot": ""}
        if "intake.her_goal" in upd:
            await person_answer_history.save(db, pid, member, {"id": pid},
                {"intake.her_goal": upd["intake.her_goal"]}, FIELD_LABELS, now(), extra_update=op)
        else:
            await db.people.update_one({"id": pid}, op)
    return await person_out(await own_person(pid, member))


@api.delete("/people/{pid}")
async def delete_person(pid: str, member: dict = Depends(subscribed_member)):
    owner = {"user_id": member["user_id"]}
    # Hide the person from new writes first. A retry can finish an interrupted cascade.
    marked = await db.people.update_one({**owner, "id": pid}, {"$set": {"deleting": True}})
    if not marked.matched_count:
        raise HTTPException(404, "Not found")
    await answer_history.purge(db, "people", {**owner, "id": pid}, strict=True)
    exs = [e["id"] async for e in db.exchanges.find({"person_id": pid}, {"_id": 0, "id": 1})]
    await drop_pattern_sources(member["user_id"], exs)
    await db.notifications.delete_many({**owner, "link": {"$regex": rf"^/app/person/{re.escape(pid)}(?:[/?#]|$)"}})
    await db.group_chats.delete_many({"user_id": member["user_id"], "person_ids": pid})
    # Older starter caches stored their profile IDs only in the comma-separated key.
    await db.group_starters.delete_many({**owner, "key": {"$regex": rf"(?:^|,){re.escape(pid)}(?:,|$)"}})
    await db.chat_groups.update_many({"user_id": member["user_id"]}, {"$pull": {"person_ids": pid}})
    await db.corrections.delete_many({"person_id": pid})
    prs = [f"practice:{x['id']}" async for x in db.practice.find({"person_id": pid}, {"id": 1})]
    await drop_pattern_sources(member["user_id"], prs)
    await db.practice.delete_many({"person_id": pid})
    await db.chat_groups.delete_many({"user_id": member["user_id"], "person_ids.1": {"$exists": False}})
    for c in ("memories", "exchanges", "preps"):
        await db[c].delete_many({"person_id": pid})
    await db.people.delete_one({**owner, "id": pid})
    return {"ok": True}


@api.patch("/people/{pid}/intake")
async def patch_intake(pid: str, body: IntakeIn, bg: BackgroundTasks, member: dict = Depends(subscribed_member)):
    try:
        from . import person_answer_history
    except ImportError:
        import person_answer_history
    p = await own_person(pid, member)
    intake = dict(p.get("intake") or {})
    substantial, updates = [], {}
    conditions = []
    owner = {"id": pid, "user_id": member["user_id"]}
    for key, value in (body.previous or {}).items():
        if key in FIELD_LABELS and key in body.fields:
            original = value.strip()[:3000]
            acceptable = [original] if original else [None, ""]
            if isinstance(body.fields[key], str):
                acceptable.append(body.fields[key].strip()[:3000])
            conditions.append({f"intake.{key}": {"$in": acceptable}})
    for k, v in body.fields.items():
        if k not in FIELD_LABELS or not isinstance(v, str):
            continue
        old, v = (intake.get(k) or "").strip(), v.strip()[:3000]
        if (not old and len(v) >= 20) or abs(len(v) - len(old)) >= 80:
            substantial.append(FIELD_LABELS[k])
        updates[f"intake.{k}"] = v
    if updates:
        query = {**owner, **({"$and": conditions} if conditions else {})}
        updated = await person_answer_history.save(db, pid, member, query, updates, FIELD_LABELS, now())
        if updated is None:
            raise HTTPException(409, "These profile details changed. Review the saved answers before saving again.")
    if updates:
        await invalidate_person_report(pid, member["user_id"])
    refresh = False
    if substantial:
        await track(member["user_id"], "deepen", {"person_id": pid, "fields": substantial})
        refresh = bool(p.get("report") or await db.exchanges.count_documents({"person_id": pid})) and await has_reply_allowance(member)
        if refresh:
            bg.add_task(ai_meter.in_context, "background", regenerate_report, pid, [f"Substantial profile addition: {', '.join(substantial)}"])
    return {**(await person_out(await own_person(pid, member))), "report_triggered": refresh}


@api.post("/people/{pid}/report/refresh")
async def refresh_report(pid: str, member: dict = Depends(subscribed_member)):
    await own_person(pid, member)
    await require_generation_access(member)
    await regenerate_report(pid, ["Manual refresh"])
    return await person_out(await own_person(pid, member))


# ---------------- exchanges (chat) ----------------
async def conversation_page(collection, query, before_time, before_id, limit):
    if bool(before_time) != bool(before_id):
        raise HTTPException(422, "Include the conversation time and ID to load earlier messages.")
    if before_time:
        query["$or"] = [{"created_at": {"$lt": before_time}},
                        {"created_at": before_time, "id": {"$lt": before_id}}]
    rows = await collection.find(query, {"_id": 0}).sort([("created_at", -1), ("id", -1)]).limit(limit).to_list(limit)
    return list(reversed(rows))


@api.get("/people/{pid}/exchanges/{eid}")
async def focused_exchange(pid: str, eid: str, member: dict = Depends(subscribed_member)):
    person = await own_person(pid, member)
    row = await db.exchanges.find_one({"user_id": member["user_id"], "person_id": pid, "id": eid}, {"_id": 0})
    if not row:
        raise HTTPException(404, "This conversation is no longer available.")
    boundary = await recent_contact_boundary(person) if (row.get("tone") or {}).get("drafts") else None
    return render_saved_exchange(row, person, boundary)


@api.get("/people/{pid}/exchanges")
async def list_exchanges(pid: str, member: dict = Depends(subscribed_member), limit: int = Query(500, ge=1, le=500),
                         before_time: str = Query("", max_length=64), before_id: str = Query("", max_length=256)):
    person = await own_person(pid, member)
    exchanges = await conversation_page(db.exchanges, {"person_id": pid}, before_time, before_id, limit)
    current_boundary = await recent_contact_boundary(person) if any(row.get("tone", {}).get("drafts") for row in exchanges) else None
    return [render_saved_exchange(row, person, current_boundary) for row in exchanges]


EX_SHAPE = """{"no_new_evidence": true/false (true only if there is no conversation and nothing new in the member's question),
"what_they_are_doing": "...", "what_it_means": "...", "confidence": "low|medium|high", "what_changed": "...",
"best_move": "...", "what_to_say": "exact words the member could send", "what_not_to_do": "...",
"revision": {"previous": "previous read", "now": "new read", "because": "the evidence that shifted it"} or null,
"proposed_memories": [{"kind": "event|interpretation|assessment", "text": "short, factual for events"}] (2-5 items, events = what they did/said),
"member_pattern": {"source_indices": [current NEW CONVERSATION indices whose speaker is MEMBER]} or null
(only actual supplied MEMBER lines; never the question to this app, a proposed reply, a future action or the
other person's line. Do not add a behavioral summary: the application retains only a literal member quotation),
"deepen_prompt": "one optional question to make the profile fuller, or null",
"seen": [{"text": "an observation taken directly from the messages (or from the member’s account if there are no messages)", "msgs": [message numbers it comes from]}] (2-4),
"explanations": [{"text": "one possible reading", "likelihood": "more likely|possible|less likely", "msgs": [message numbers], "tells_apart": "what would confirm or rule this out"}] (2-4, ALWAYS more than one),
"unknowns": ["missing context that would change the read"] (1-3),
"actions": [{"label": "2-4 words, e.g. Wait a day", "detail": "one sentence", "say": "exact words to send, or null"}] (2-3)}
Never claim to know their real intentions; offer several explanations and say what would tell them apart."""


async def recent_contact_boundary(person, *, include_invitation=False):
    """Use recent human evidence separately from generated advice when considering future contact."""
    query = {"person_id": person["id"], "user_id": person["user_id"],
             "$or": [{"contact_update": {"$in": ["requested", "reopened"]}}, {"contact_update": {"$exists": False}}]}
    rows = db.exchanges.find(query, {"conversation": 1, "question": 1, "response.boundary_evidence": 1,
                                    "contact_update": 1, "created_at": 1, "_id": 0})
    async for row in rows.sort("created_at", -1):
        state = row.get("contact_update")
        if include_invitation:
            question, prior = contact_boundary.reopened_question(row.get("conversation") or [])
            if question and (prior or state in {"requested", "reopened"}):
                return {"invited_question": question, "reopened_at": row.get("created_at")}
        if state == "reopened":
            return {"reopened_at": row.get("created_at")} if include_invitation else None
        evidence = (row.get("response") or {}).get("boundary_evidence") if state == "requested" else None
        if not evidence:
            evidence, reopened = contact_boundary.turn_evidence(row.get("conversation") or [], row.get("question"))
            if reopened:
                return {"reopened_at": row.get("created_at")} if include_invitation else None
        if evidence:
            return evidence if contact_boundary.still_applies(evidence, row.get("created_at")) else None
    evidence = contact_boundary.evidence_from(person.get("background"))
    return evidence if evidence and contact_boundary.still_applies(evidence, person.get("created_at")) else None


def render_saved_exchange(row, person, current_boundary):
    saved = contact_boundary.saved_read(row, person["nickname"])
    if saved.get("tone") and (current_boundary or saved["response"].get("contact_boundary")):
        return {**saved, "tone_contact_paused": True, "tone": {**saved["tone"], "drafts": {}}}
    return saved


async def saved_exchange_with_tone(row, person):
    boundary = await recent_contact_boundary(person) if row.get("tone", {}).get("drafts") else None
    return render_saved_exchange(row, person, boundary)


def checked_reply_styles(drafts, member_words=()):
    """Keep all five usable styles distinct after the factual wording guards."""
    def rejected(reason, detail):
        error = HTTPException(502, detail)
        error.reason = reason
        return error
    if (not isinstance(drafts, dict) or set(drafts) != set(conversation_tone.STYLES)
            or any(not isinstance(text, str) or not text.strip() for text in drafts.values())):
        raise rejected("reply_style_shape", "The complete set of reply styles could not be prepared. Please retry.")
    distinct = {" ".join(text.casefold().split()) for text in drafts.values()}
    if len(distinct) == 1:
        only = next(iter(drafts.values()))
        if only in conversation_tone.TIME_QUESTIONS:
            return conversation_tone.TIME_QUESTIONS[only].copy()
    if len(distinct) != len(conversation_tone.STYLES):
        raise rejected("reply_style_duplicate", "The reply styles did not produce different wording. Please retry.")
    if conversation_tone.draft_intent_violation(drafts, member_words):
        raise rejected("reply_intent", "The reply styles added a commitment or belief you did not state. Please retry.")
    return drafts


async def run_exchange(p: dict, member: dict, lines: list, question: str, stage: str, goal: str, bg: BackgroundTasks,
                       reply_id: str, continue_exchange_id: str | None = None) -> dict:
    s = await get_settings()
    pid = p["id"]
    context_sources = {}
    ctx, bo = await full_context(p, member, source_map=context_sources)
    thread, earlier, notes, versions = await conversation_tone.continuation(db.exchanges, p, continue_exchange_id, lines)
    baseline = [{"index": i, "speaker": line["speaker"], "text": line["text"]} for i, line in enumerate(earlier)]
    convo = "\n".join(f"[{i}] {'MEMBER' if l['speaker'] == 'me' else p['nickname'].upper()}: {l['text']}" for i, l in enumerate(lines))
    prompt = (f"{ctx}\n\n"
              f"NEW CONVERSATION (numbered; stage: {stage or p.get('stage')}; goal of exchange: {goal or '-'}):\n{convo or '(none)'}\n\n"
              f"MEMBER QUESTION: {question or '(none - read the conversation)'}\n\nShape:\n{conversation_tone.exchange_shape(EX_SHAPE)}\n"
              "Target writer: them. Cite current messages in evidence. Where the current slice is short, you may "
              "describe the thread overall only with cited earlier target lines. Clearly distinguish that from the latest message. "
              "Only compare earlier behavior when the following baseline supports it, citing its indices. "
              "The member's submitted goal or question takes precedence over generic advice. Match every draft to that purpose. "
              "Prior advice and profile background are not primary evidence for tone.\n"
              f"EARLIER CONFIRMED THREAD MESSAGES: {json.dumps(baseline, ensure_ascii=False)}\n"
              f"MEMBER'S EARLIER TONE CORRECTIONS: {json.dumps(notes, ensure_ascii=False)}")
    out = await ai_json("six_part", f"You are Imladri, an advisor helping the member understand relationships in their life.\n{MEMBER_ADVICE_RULE}", prompt, settings=s, tone=prompt_key(p))
    requested_tone = out.pop("tone", None)
    out.pop("contact_boundary", None)
    quote = out.pop("boundary_evidence", None)
    evidence, reopened = contact_boundary.turn_evidence(lines, question, quote)
    contact_update = "requested" if evidence else "reopened" if reopened else None
    if not evidence and not reopened:
        evidence = await recent_contact_boundary(p)
    if evidence:
        out = contact_boundary.read(p["nickname"], evidence)
    elif reopened:
        allowed_question, earlier_request = contact_boundary.reopened_question(lines)
        if allowed_question:
            out = contact_boundary.limited_read(p["nickname"], allowed_question)
            # A topic-specific invitation grants this reply. Preserve any broader restriction for future turns.
            contact_update = "requested" if earlier_request else None
            if earlier_request:
                out["boundary_evidence"] = earlier_request
    hp = advice_wording.grounded_member_observation(out.pop("member_pattern", None) or out.pop("her_pattern", None), lines)
    n = len(lines)
    for k in ("seen", "explanations"):
        out[k] = [{**x, "msgs": [m for m in (x.get("msgs") or []) if isinstance(m, int) and 0 <= m < n]} for x in (out.get(k) or []) if isinstance(x, dict) and x.get("text")][:4]
    out["unknowns"] = [str(x) for x in (out.get("unknowns") or []) if x][:3]
    out["actions"] = [x for x in (out.get("actions") or []) if isinstance(x, dict) and x.get("label")][:3]
    advice_wording.respect_undecided_time(out, question)
    member_lines = [line["text"] for line in lines if line["speaker"] == "me"]
    advice_wording.respect_stated_days(out, "\n".join([question or "", *member_lines]))
    advice_wording.clarify_invitation_time(out, lines, question)
    member_words = [question or "", goal or "", *member_lines]
    copyable = {"reply": out.get("what_to_say"), **{str(index): action.get("say") for index, action in enumerate(out["actions"])}}
    if conversation_tone.draft_intent_violation(copyable, member_words):
        await message_costs.mark_response_rejection(db, reply_id, "reply_intent")
        error = HTTPException(502, "The suggested reply added a commitment or belief you did not state. Please retry.")
        error.reason = "reply_intent"
        raise error
    tone = conversation_tone.normalize(requested_tone, lines, intent=goal, previous=earlier)
    if out.get("contact_boundary") or out.get("limited_reply"):
        # The deterministic boundary response also governs every optional rewrite.
        tone["drafts"] = {}
    else:
        if not isinstance(requested_tone, dict) or not isinstance(requested_tone.get("drafts"), dict):
            await message_costs.mark_response_rejection(db, reply_id, "reply_tone_shape")
            error = HTTPException(502, "The conversation tone could not be prepared. Please retry.")
            error.reason = "reply_tone_shape"
            raise error
        if (out.get("what_to_say") or tone["drafts"]) and set(tone["drafts"]) != set(conversation_tone.STYLES):
            await message_costs.mark_response_rejection(db, reply_id, "reply_style_shape")
            error = HTTPException(502, "The complete set of reply styles could not be prepared. Please retry.")
            error.reason = "reply_style_shape"
            raise error
        checked = {}
        for style, draft in tone["drafts"].items():
            candidate = {"what_to_say": draft}
            advice_wording.respect_undecided_time(candidate, question)
            advice_wording.respect_stated_days(candidate, "\n".join([question or "", *member_lines]))
            advice_wording.clarify_invitation_time(candidate, lines, question)
            checked[style] = candidate["what_to_say"]
        try:
            tone["drafts"] = checked_reply_styles(checked, member_words) if checked else {}
        except HTTPException as error:
            await message_costs.mark_response_rejection(db, reply_id, error.reason)
            raise
    ex = {"id": reply_id, "user_id": member["user_id"], "person_id": pid, "conversation": lines, "contact_update": contact_update,
          "has_conversation": bool(lines), "question": question, "context": {"stage": stage, "goal": goal},
          "response": out, "proposed_memories": out.pop("proposed_memories", []), "memories_reviewed": False,
          "outcome": None, "choice": None, "corrected": [], "based_on": bo, "created_at": now(),
          "memory_context_ids": [r["id"] for r in context_sources.values() if r["kind"] == "memory"],
          "tone": tone, "tone_thread_id": thread or reply_id,
          "tone_sources": [{"id": source, "revision": rev} for source, rev in versions.items()]}
    if out.get("contact_boundary") or out.get("limited_reply"):
        ex.pop("tone")
    if not await db.people.find_one({"id": pid}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="This person was deleted")
    await db.exchanges.insert_one(ex)
    if not await conversation_tone.sources_current(db.exchanges, member["user_id"], versions):
        await db.exchanges.update_one({"id": reply_id, "user_id": member["user_id"]}, conversation_tone.INVALIDATE)
        ex.pop("tone", None)
        ex.pop("tone_sources", None)
        ex["tone_invalidated"] = True
    await db.people.update_one({"id": pid}, {"$set": {"last_activity": now(), "working_read": out.get("what_it_means")}})
    if isinstance(hp, dict) and isinstance(hp.get("text"), str):
        await pattern_evidence.save(db, member["user_id"], hp["text"], [ex["id"]], now())
    if lines:
        await message_charge.enqueue(db, bg, regenerate_report, pid, ["New conversation shared"])
    await advance(member["user_id"], 4)
    return strip(ex)


@api.post("/people/{pid}/exchanges")
async def create_exchange(pid: str, body: ExchangeIn, request: Request, bg: BackgroundTasks, member: dict = Depends(reply_member),
                          upload_attempt: Annotated[uuid.UUID | None, Header(alias="X-Upload-Attempt")] = None,
                          starter_batch: Annotated[uuid.UUID | None, Header(alias="X-Starter-Batch")] = None):
    p = await own_person(pid, member)
    if not body.conversation and not body.question.strip():
        raise HTTPException(status_code=422, detail="Add a conversation or a question")
    async def generate(reply_id):
        return await run_exchange(p, member, [l.model_dump() for l in body.conversation], body.question, body.context_stage,
                                  body.context_goal, bg, reply_id, body.continue_exchange_id)
    result = await metered_reply(member, request, body, "exchange", pid, generate, upload_attempt, starter_batch)
    await completed_upload(member["user_id"], upload_attempt, result)
    return result


@api.delete("/exchanges/{eid}")
async def delete_exchange(eid: str, bg: BackgroundTasks, member: dict = Depends(subscribed_member)):
    ex = await db.exchanges.find_one({"id": eid, "user_id": member["user_id"]}, {"_id": 0})
    if not ex:
        raise HTTPException(status_code=404, detail="Not found")
    await db.exchanges.delete_one({"id": eid})
    await conversation_tone.invalidate_dependents(db.exchanges, member["user_id"], eid)
    await db.memories.delete_many({"exchange_id": eid})
    await db.corrections.delete_many({"exchange_id": eid})
    await drop_pattern_sources(member["user_id"], [eid])
    await invalidate_person_report(ex["person_id"], member["user_id"])
    with await message_charge.saved_context(db, eid, ENV):
        await message_charge.enqueue(db, bg, regenerate_report, ex["person_id"], ["A conversation and its memories were deleted"], source_removed=True)
    return {"ok": True}


@api.post("/exchanges/{eid}/memories")
async def approve_memories(eid: str, body: ApproveIn, member: dict = Depends(subscribed_member)):
    ex = await db.exchanges.find_one({"id": eid, "user_id": member["user_id"]}, {"_id": 0})
    if not ex:
        raise HTTPException(status_code=404, detail="Not found")
    events = [m for m in body.memories if m.kind == "event" and m.text.strip()]
    others = [m for m in body.memories if m.kind in ("interpretation", "assessment") and m.text.strip()]
    ev_ids = []
    for m in events:
        doc = {"id": new_id(), "user_id": member["user_id"], "person_id": ex["person_id"], "kind": "event", "text": m.text.strip()[:500],
               "exchange_id": eid, "source": f"Conversation {ex['created_at'][:10]}", "derived_from": [], "created_at": now()}
        await db.memories.insert_one(doc)
        ev_ids.append(doc["id"])
    for m in others:
        await db.memories.insert_one({"id": new_id(), "user_id": member["user_id"], "person_id": ex["person_id"], "kind": m.kind,
                                      "text": m.text.strip()[:500], "exchange_id": eid, "source": f"Derived from conversation {ex['created_at'][:10]}",
                                      "derived_from": ev_ids, "created_at": now()})
    await db.exchanges.update_one({"id": eid}, {"$set": {"memories_reviewed": True}})
    return {"saved": len(events) + len(others)}


async def own_exchange(eid: str, member: dict) -> dict:
    ex = await db.exchanges.find_one({"id": eid, "user_id": member["user_id"]}, {"_id": 0})
    if not ex:
        raise HTTPException(status_code=404, detail="Not found")
    return ex


RESULT_LABEL = trends.RESULT_LABEL


@api.post("/exchanges/{eid}/tone-feedback")
async def exchange_tone_feedback(eid: str, body: conversation_tone.Feedback, member: dict = Depends(subscribed_member)):
    if body.action in ("select", "copy"):
        ex = await own_exchange(eid, member)
        person = await own_person(ex["person_id"], member)
        if await recent_contact_boundary(person):
            raise HTTPException(409, "Respect their current request for space. Reply wording is unavailable.")
    return await conversation_tone.save_feedback(db.exchanges, member["user_id"], eid, body)


@api.get("/exchanges/{eid}/tone-evidence")
async def tone_evidence(eid: str, member: dict = Depends(subscribed_member)):
    ex = await own_exchange(eid, member)
    refs = (ex.get("tone") or {}).get("baseline_evidence") or []
    query = {"user_id": member["user_id"], "person_id": ex["person_id"], "id": {"$in": [r["exchange_id"] for r in refs]}}
    sources = {row["id"]: row["conversation"] async for row in db.exchanges.find(query, {"id": 1, "conversation": 1})}
    return [sources[ref["exchange_id"]][ref["index"]]["text"] for ref in refs
            if 0 <= ref["index"] < len(sources.get(ref["exchange_id"], []))]


@api.post("/exchanges/{eid}/outcome")
async def log_outcome(eid: str, body: OutcomeIn, bg: BackgroundTasks, member: dict = Depends(subscribed_member)):
    ex = await own_exchange(eid, member)
    text = body.text.strip()[:2000]
    if body.result and body.result not in RESULT_LABEL:
        raise HTTPException(status_code=422, detail="Unknown result")
    if not body.result and not text:
        raise HTTPException(status_code=422, detail="Pick how it went or add a note")
    summary = " - ".join(x for x in (RESULT_LABEL.get(body.result or ""), text) if x)
    p = await db.people.find_one({"id": ex["person_id"]}, {"_id": 0})
    r = ex.get("response") or {}
    upd = {}
    try:
        await require_generation_access(member)
        with await message_charge.saved_context(db, eid, ENV):
            upd = await ai_json("outcome_review", "You are Imladri. The member logged what happened after an earlier read. Decide if this new evidence changes the earlier read. "
                            "Never claim to know their real intentions. Everyday words. "
                            'Return JSON {"changed": true/false, "earlier": "the earlier read in one line", "now": "the updated read in one line", "note": "1-2 sentences addressing the member"}',
                            f"PERSON: {p['nickname'] if p else 'them'}\nEARLIER READ: {r.get('what_it_means', '')}\nEXPLANATIONS: {json.dumps(r.get('explanations') or [])[:1500]}\n"
                            f"WHAT THE MEMBER CHOSE TO DO: {(ex.get('choice') or {}).get('label') or 'not recorded'}\nWHAT HAPPENED: {summary}")
    except HTTPException:
        upd = {}
    read_update = {"changed": bool(upd.get("changed")), "earlier": str(upd.get("earlier") or "")[:300], "now": str(upd.get("now") or "")[:300],
                   "note": str(upd.get("note") or "")[:400]} if upd else None
    await db.exchanges.update_one({"id": eid}, {"$set": {"outcome": summary, "outcome_result": body.result or None, "outcome_note": text,
                                                       "outcome_at": now(), "read_update": read_update}})
    await db.memories.delete_many({"exchange_id": eid, "kind": "outcome"})
    await db.memories.insert_one({"id": new_id(), "user_id": member["user_id"], "person_id": ex["person_id"], "kind": "outcome",
                                  "text": summary[:500], "exchange_id": eid, "source": f"Outcome of advice {ex['created_at'][:10]}",
                                  "derived_from": [], "created_at": now()})
    pset = {"last_activity": now()}
    if read_update and read_update["changed"] and read_update["now"]:
        pset["working_read"] = read_update["now"]
    await db.people.update_one({"id": ex["person_id"]}, {"$set": pset})
    await db.notifications.delete_many({"user_id": member["user_id"], "key": f"checkin:{eid}"})
    with await message_charge.saved_context(db, eid, ENV):
        await message_charge.enqueue(db, bg, regenerate_report, ex["person_id"], ["Outcome logged: what actually happened" + (f" (earlier: {read_update['earlier']}; now: {read_update['now']})" if read_update and read_update["changed"] else "")])
    saved = strip(await db.exchanges.find_one({"id": eid, "user_id": member["user_id"]}))
    return await saved_exchange_with_tone(saved, p) if p else saved


@api.delete("/exchanges/{eid}/outcome")
async def delete_outcome(eid: str, bg: BackgroundTasks, member: dict = Depends(subscribed_member)):
    ex = await own_exchange(eid, member)
    await db.exchanges.update_one({"id": eid}, {"$set": {"outcome": None, "outcome_result": None, "outcome_note": "", "outcome_at": None, "read_update": None}})
    await db.memories.delete_many({"exchange_id": eid, "kind": "outcome"})
    with await message_charge.saved_context(db, eid, ENV):
        await message_charge.enqueue(db, bg, regenerate_report, ex["person_id"], ["An outcome was removed"], source_removed=True)
    return {"ok": True}


@api.post("/exchanges/{eid}/choice")
async def set_choice(eid: str, body: ChoiceIn, member: dict = Depends(subscribed_member)):
    await own_exchange(eid, member)
    choice = {"label": body.label.strip()[:200], "custom": body.custom, "at": now()}
    await db.exchanges.update_one({"id": eid}, {"$set": {"choice": choice}})
    return {"choice": choice}


@api.delete("/exchanges/{eid}/choice")
async def clear_choice(eid: str, member: dict = Depends(subscribed_member)):
    await own_exchange(eid, member)
    await db.exchanges.update_one({"id": eid}, {"$set": {"choice": None}})
    return {"ok": True}


@api.post("/exchanges/{eid}/correct")
async def correct_point(eid: str, body: CorrectIn, member: dict = Depends(subscribed_member)):
    ex = await own_exchange(eid, member)
    items = (ex.get("response") or {}).get(body.kind) or []
    if not 0 <= body.index < len(items):
        raise HTTPException(status_code=404, detail="Point not found")
    key = f"{body.kind}:{body.index}"
    await db.corrections.delete_many({"exchange_id": eid, "key": key})
    await db.corrections.insert_one({"id": new_id(), "user_id": member["user_id"], "person_id": ex["person_id"], "exchange_id": eid, "key": key,
                                     "text": items[body.index]["text"][:400], "note": body.note.strip()[:300], "created_at": now()})
    await db.exchanges.update_one({"id": eid}, {"$addToSet": {"corrected": key}})
    await invalidate_person_report(ex["person_id"], member["user_id"])
    return {"corrected": key}


@api.delete("/exchanges/{eid}/correct")
async def undo_correct(eid: str, kind: str, index: int, member: dict = Depends(subscribed_member)):
    ex = await own_exchange(eid, member)
    key = f"{kind}:{index}"
    removed = await db.corrections.delete_many({"user_id": member["user_id"], "exchange_id": eid, "key": key})
    await db.exchanges.update_one({"id": eid, "user_id": member["user_id"]}, {"$pull": {"corrected": key}})
    if removed.deleted_count:
        await invalidate_person_report(ex["person_id"], member["user_id"])
    return {"ok": True}


# ---------------- fast first answer ----------------
async def transcribe_screenshot(image_b64: str, nickname: str) -> list:
    image = image_b64.split(",", 1)[-1]
    if len(image) > 8_000_000:
        raise HTTPException(413, "Image too large")
    instruction = ("Transcribe visible message bubbles in order in their original language. Never guess obscured text. "
                   "Use [redacted] where words are covered. Exclude timestamps, headers and interface text. "
                   "In a direct conversation, side and visible sender labels can establish me (device owner) or them. "
                   "In a group, report the visible sender label for each incoming message; another participant is unknown. "
                   "A bubble color by itself does not identify its sender. Set sure to false when ambiguous.")
    shape = ('Return thread_kind as direct, group or unclear. For each line include text, speaker (me, them or unknown), '
             'sure (true or false), and sender_label (only a visible message sender name, otherwise empty). '
             'The member’s nickname for the intended other participant is ' + json.dumps(nickname, ensure_ascii=False))
    result = await ai_json("transcribe_image", instruction, shape, image)
    lines = result.get("lines")
    if not isinstance(lines, list):
        return []
    reviewed = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        row = dict(line)
        if row.get("speaker") == "them" and result.get("thread_kind") != "direct":
            label = row.get("sender_label")
            if not nickname or not isinstance(label, str) or label.strip().casefold() != nickname.strip().casefold():
                row["sure"] = False
        reviewed.append(row)
    return transcript_lines(reviewed)


async def parse_convo(nickname: str, text: str, image_b64: str) -> list:
    lines = []
    if image_b64:
        return await transcribe_screenshot(image_b64, nickname)
    elif text.strip():
        out = await ai_json("transcribe_text", "Split a pasted chat into messages. 'me' is the member who pasted it; 'them' is " + nickname + ". "
                            "Keep the exact words. Use name labels like 'Me:' or '" + nickname + ":' when present. When a line has no clear sender, use unknown and set sure to false.",
                            f'Pasted chat:\n{text[:6000]}\n\nShape: {{"lines": [{{"speaker": "me|them", "text": "...", "sure": true}}]}}')
        lines = out.get("lines") or []
    return transcript_lines(lines)


def transcript_lines(lines):
    reviewed = []
    for line in lines or []:
        if not isinstance(line, dict) or not isinstance(line.get("text"), str) or not line["text"].strip():
            continue
        speaker = line.get("speaker")
        sure = speaker in ("me", "them") and line.get("sure") is True
        reviewed.append({"speaker": speaker if sure else "unknown", "sure": sure, "text": line["text"][:2000]})
        if len(reviewed) == 80:
            break
    return reviewed


@api.post("/quickstart/parse")
async def quickstart_parse(body: QuickParseIn, member: dict = Depends(subscribed_member),
                           upload_attempt: Annotated[uuid.UUID | None, Header(alias="X-Upload-Attempt")] = None):
    await require_generation_access(member)
    if upload_attempt and (body.conversation_text.strip() or body.image_base64):
        await track(member["user_id"], "upload_started", attempt=upload_attempt)
    with message_costs.preparation_context(message_charge.upload_id(member["user_id"], upload_attempt)):
        return {"lines": await parse_convo(body.nickname, body.conversation_text, body.image_base64)}


@api.post("/quickstart")
async def quickstart(body: QuickIn, request: Request, bg: BackgroundTasks, member: dict = Depends(reply_member),
                     upload_attempt: Annotated[uuid.UUID | None, Header(alias="X-Upload-Attempt")] = None):
    if body.rel_type not in GROUP_OF:
        raise HTTPException(status_code=422, detail="Pick who they are to you")
    if body.stage and body.stage not in (ROMANTIC_STAGES if body.rel_type in ROMANTIC else OTHER_STAGES):
        raise HTTPException(status_code=422, detail="Unknown stage for this relationship")
    async def generate(reply_id):
        stage = body.stage or ("early" if body.rel_type in ROMANTIC else "good")
        p = {"id": quick_person_id(reply_id), "user_id": member["user_id"], "nickname": body.nickname.strip(), "rel_type": body.rel_type,
             "rel_label": "", "stage": stage, "background": body.what_happened.strip()[:500], "goal": "", "intake": {},
             "archetype_tag": None, "archived": False, "report": None, "report_status": "empty", "gender": body.gender, "created_at": now(), "last_activity": now()}
        await activate_person(member, person=p)
        try:
            if body.lines is not None:
                lines = [{"speaker": l.speaker, "text": l.text[:2000]} for l in body.lines if l.text.strip()][:80]
            else:
                parsed = await parse_convo(body.nickname, body.conversation_text, body.image_base64)
                if any(not line["sure"] for line in parsed):
                    raise HTTPException(422, "Review the conversation and assign each unknown speaker first.")
                lines = [{"speaker": l["speaker"], "text": l["text"]} for l in parsed]
            ex = await run_exchange(p, member, lines, body.what_happened.strip(), "", "", bg, reply_id)
        except (Exception, asyncio.CancelledError):
            if not await db.exchanges.find_one({"user_id": member["user_id"], "id": reply_id}, {"_id": 1}):
                await db.people.delete_one({"user_id": member["user_id"], "id": p["id"]})
            raise
        await track(member["user_id"], "person_added", {"rel_type": body.rel_type, "via": "quickstart", "speakers_confirmed": body.lines is not None})
        await advance(member["user_id"], 3, {"guide_person_id": p["id"]})
        if not lines:
            await message_charge.enqueue(db, bg, regenerate_report, p["id"], ["First situation shared"])
        return {"person": await person_out(p), "exchange": ex}
    result = await metered_reply(member, request, body, "quickstart", "", generate, upload_attempt)
    await completed_upload(member["user_id"], upload_attempt, result["exchange"])
    return result


@api.get("/me/next-question")
async def next_question(member: dict = Depends(subscribed_member)):
    s = await get_settings()
    about = member.get("about") or {}
    skipped = set(about.get("skipped") or [])
    for set_ in ("short", "extended", "further"):
        for qq in s["quiz"][set_]:
            if quiz_answers.resolve_answer(about, s["quiz"], qq) is None and qq["id"] not in skipped:
                return {"question": {"id": qq["id"], "q": qq["q"], "options": [o["label"] for o in qq["options"]]}, "set": set_}
    return {"question": None}


@api.post("/me/quick-answer")
async def quick_answer(body: QuickAnswerIn, member: dict = Depends(subscribed_member)):
    s = await get_settings()
    qs = quiz_answers.questions_by_id(s["quiz"])
    if body.qid not in qs or not safe_quiz_id(body.qid):
        raise HTTPException(status_code=404, detail="Unknown question")
    await initialize_legacy_about(member)
    if body.choice is None:
        if body.question is not None and body.question != qs[body.qid]["q"]:
            raise HTTPException(409, "This question changed. Refresh Personal before skipping it.")
        section = next(name for name in quiz_answers.SETS if any(q["id"] == body.qid for q in s["quiz"][name]))
        variable = research.everyday_variable(section, qs[body.qid])
        await individual_history.save(db, member["user_id"], {"user_id": member["user_id"]}, {
            "$addToSet": {"about.skipped": body.qid},
            "$set": {f"about.skip_editions.{body.qid}": {"edition": research.fingerprint([variable]),
                                                         "question": variable["label"], "section": section}}}, s["quiz"])
    else:
        selected = submitted_answer(s["quiz"], qs[body.qid], body)
        if selected is None:
            raise HTTPException(status_code=422, detail="Unknown option")
        stored = await individual_history.save(db, member["user_id"], {"user_id": member["user_id"]}, {
            "$set": {f"about.answers.{body.qid}": selected, "about.updated_at": now()}, "$pull": {"about.skipped": body.qid},
            "$unset": {f"about.skip_editions.{body.qid}": ""}}, s["quiz"])
        counts = quiz_answer_counts(stored["about"], s["quiz"])
        if counts["short"]:
            await advance(member["user_id"], 2)
        await record_quiz_milestones(member["user_id"], s["quiz"], counts)
        return await next_question(stored)
    return await next_question(await db.users.find_one({"user_id": member["user_id"]}, {"about": 1}))


# ---------------- practice ----------------
async def drop_pattern_sources(uid: str, sources: list):
    if sources:
        await db.patterns.update_many({"user_id": uid}, {"$pull": {"sources": {"$in": sources}}})
        await db.patterns.delete_many({"user_id": uid, "sources": {"$size": 0}, "edited": {"$ne": True}, "member_confirmed": {"$ne": True}})


async def practice_contact_limit(pid, member):
    if not pid:
        return None
    owner = {"id": pid, "user_id": member["user_id"]}
    person = await db.people.find_one(owner, {"_id": 0, "id": 1, "user_id": 1, "background": 1, "created_at": 1})
    return await recent_contact_boundary(person, include_invitation=True) if person else None


def practice_view(row, real_limit):
    # A fictional character's words cannot grant real permission to resume contact.
    local_evidence = row.get("practice_boundary") or contact_boundary.evidence_from(row.get("topic"))
    if isinstance(real_limit, dict):
        local_stamp = row.get("practice_boundary_at") or row.get("created_at") or ""
        if real_limit.get("reopened_at") and real_limit["reopened_at"] > local_stamp:
            local_evidence = None
        if not real_limit.get("invited_question"):
            real_limit = None
    limit = local_evidence or real_limit
    if not limit:
        return row
    question = limit.get("invited_question") if isinstance(limit, dict) else None
    notice = ("They invited a reply to this question: " + question + " Keep any reply to the question and channel they offered."
              if question else "They asked for space. Leave the next contact to them. You can write a private reflection for yourself.")
    # Historical branches were simulations. A new real limit also governs their replay and suggested next lines.
    return {**row, "contact_pause": notice, "messages": [], "options": [], "rounds": [], "feedback": None,
            "reflection": {**row["reflection"], "branch": None} if row.get("reflection") else None, "profile_saved": []}


async def own_session(sid: str, member: dict) -> dict:
    x = await db.practice.find_one({"id": sid, "user_id": member["user_id"]}, {"_id": 0})
    if not x:
        raise HTTPException(status_code=404, detail="Not found")
    return practice_view(x, await practice_contact_limit(x.get("person_id"), member))


def require_practice_contact(session):
    if not session.get("person_id"):
        raise HTTPException(409, "This earlier rehearsal has no linked person. Start a new rehearsal to generate replies.")


async def practice_ctx(x: dict, member: dict) -> tuple[dict, str]:
    require_practice_contact(x)
    p = await own_person(x["person_id"], member)
    ctx, _ = await full_context(p, member, limit_ex=3)
    convo = "\n".join(f"{'MEMBER' if m['role'] == 'me' else p['nickname'].upper()}: {m['text']}" for m in x["messages"])
    return p, f"{ctx}\n\nWHAT THE MEMBER WANTS TO BRING UP: {x['topic']}\n\nPRACTICE SO FAR:\n{convo or '(nothing yet)'}"


async def likely_options(x: dict, member: dict) -> list:
    p, ctx = await practice_ctx(x, member)
    out = await ai_json("practice_options", f"This is a PRACTICE rehearsal, not real advice. Write plausible fictional reactions from {p['nickname']} to the member's last line, based only on what is known about them. "
                        "Give exactly 3 different rehearsal scenarios: 1 warm, 2 neutral or deflecting, 3 difficult. These are practice scenarios, NOT predictions: never claim how likely each is. For each: the reply as they might text it, what it could signal, "
                        "one possible next development, and one calm response in the member's voice. Respect stated constraints on timing and further contact in every branch: no copyable outreach after a request for no contact. Allow silence, waiting or stepping back as a valid outcome. Avoid invented motives or facts. "
                        "Write meaning and outcome addressing the member as 'you'. "
                        'Return JSON {"options": [{"reaction": "their reply, as they might text it", "tone": "warm|neutral|difficult", '
                        '"meaning": "one sentence: one possible reading of this reply", "outcome": "one possible development, expressed conditionally", "suggested": "the member’s next line"}]}', ctx)
    pause_options = advice_wording.current_pause_options(x.get("messages"))
    if pause_options is not None:
        return pause_options
    return sorted([o for o in (out.get("options") or []) if isinstance(o, dict) and o.get("reaction")][:3],
                  key=lambda o: {"warm": 0, "neutral": 1, "difficult": 2}.get(str(o.get("tone") or "").strip().lower(), 1))


@api.get("/practice")
async def list_practice(person_id: str = "", member: dict = Depends(subscribed_member), page_size: Annotated[int, Query(ge=0, le=100)] = 0,
                        cursor: Annotated[str, Query(max_length=1024)] = ""):
    q = {"user_id": member["user_id"], **({"person_id": person_id} if person_id else {})}
    listing = await member_pages.page(db.practice, q, size=page_size or 100, cursor=cursor,
        projection={"_id": 0, "id": 1, "created_at": 1, "topic": 1, "mode": 1, "person_id": 1} if page_size else None)
    if page_size:
        return listing
    people = {row.get("person_id") for row in listing["items"]}
    limits = dict(zip(people, await asyncio.gather(*(practice_contact_limit(pid, member) for pid in people))))
    return [practice_view(row, limits[row.get("person_id")]) for row in listing["items"]]


@api.post("/practice")
async def start_practice(body: PracticeIn, member: dict = Depends(subscribed_member)):
    if body.mode not in ("live", "likely"):
        raise HTTPException(status_code=422, detail="Unknown mode")
    await own_person(body.person_id, member)
    doc = {"id": new_id(), "user_id": member["user_id"], "person_id": body.person_id, "topic": body.topic.strip(), "mode": body.mode,
           "messages": [], "options": [], "feedback": None, "ended": False, "created_at": now()}
    await db.practice.insert_one(doc)
    return practice_view(strip(doc), await practice_contact_limit(body.person_id, member))


@api.get("/practice/{sid}")
async def get_practice(sid: str, member: dict = Depends(subscribed_member)):
    return await own_session(sid, member)


class PracticeReflection(BaseModel):
    result: Literal["similar", "different", "did_not_try"] | None = None
    note: str = Field("", max_length=500)
    round_index: int | None = Field(None, ge=0)
    expected_revision: int = Field(ge=0)
    expected_turn_revision: int | None = Field(None, ge=0)
    keep_saved_branch: bool = False


@api.put("/practice/{sid}/reflection")
async def save_practice_reflection(sid: str, body: PracticeReflection, member: dict = Depends(subscribed_member)):
    session = await own_session(sid, member)
    if not session.get("ended") and not session.get("reflection") and not session.get("contact_pause"):
        raise HTTPException(409, "Finish the rehearsal before comparing it with a real conversation.")
    rounds = session.get("rounds") or []
    branch = None
    if body.keep_saved_branch:
        branch = (session.get("reflection") or {}).get("branch")
        if not branch or body.round_index is not None or not body.result:
            raise HTTPException(422, "Choose a saved comparison or a current round.")
    elif body.round_index is not None and body.result:
        if body.expected_turn_revision is not None and body.expected_turn_revision != session.get("turn_revision", 0):
            raise HTTPException(409, "The rehearsal changed on another device. Reopen it to choose a current branch.")
        if body.round_index >= len(rounds):
            raise HTTPException(422, "That rehearsal round is unavailable.")
        selected = rounds[body.round_index]
        picked, choices = selected.get("picked"), selected.get("options") or []
        if not isinstance(picked, int) or not 0 <= picked < len(choices):
            raise HTTPException(422, "Choose a completed branch.")
        branch = {"round": body.round_index + 1, "reaction": str(choices[picked].get("reaction") or "")[:1500]}
    reflection = {"result": body.result, "note": body.note.strip(), "branch": branch} if body.result else None
    previous = session.get("reflection")
    if previous == reflection:
        return session
    revision = {"$in": [None, 0]} if body.expected_revision == 0 else body.expected_revision
    query = {"id": sid, "user_id": member["user_id"], "reflection_revision": revision, "ended": session.get("ended", False)}
    if branch and not body.keep_saved_branch:
        query["rounds"] = rounds
    updated = await db.practice.update_one(query, {"$set": {"reflection": reflection}, "$inc": {"reflection_revision": 1}})
    if not updated.matched_count:
        raise HTTPException(409, "This rehearsal changed. Reopen it before saving your reflection.")
    return await own_session(sid, member)


@api.post("/practice/{sid}/say")
async def practice_say(sid: str, body: PracticeTurnIn, request: Request, member: dict = Depends(reply_member)):
    session = await own_session(sid, member)
    require_practice_contact(session)
    explicit = contact_boundary.evidence_from(body.text)
    if explicit:
        await db.practice.update_one({"id": sid, "user_id": member["user_id"]}, {"$set": {"practice_boundary": explicit, "practice_boundary_at": now()}, "$inc": {"turn_revision": 1}})
        return await own_session(sid, member)
    if session.get("contact_pause"):
        return session
    async def generate(reply_id):
        return await practice_turn(sid, body, member, reply_id)
    # An interrupted turn submitted by an older client retains its original receipt signature.
    receipt_body = body if body.expected_revision is not None else TextIn(text=body.text)
    return await metered_reply(member, request, receipt_body, "practice", sid, generate)


async def practice_turn(sid, body, member, reply_id):
    x = await own_session(sid, member)
    require_practice_contact(x)
    if x.get("contact_pause"):
        raise HTTPException(409, x["contact_pause"])
    await own_person(x["person_id"], member)
    revision = x.get("turn_revision", 0)
    expected = getattr(body, "expected_revision", None)
    if expected is not None and expected != revision:
        raise HTTPException(409, "This rehearsal changed on another device. Review the saved turns before sending.")
    previous_messages = x["messages"][:]
    if x["mode"] == "likely" and x.get("options"):
        raise HTTPException(status_code=409, detail="Pick one of the scenarios first")
    if x.get("rounds") and x["rounds"][-1].get("picked") is None:
        x["rounds"] = x["rounds"][:-1]
    x["messages"].append({"role": "me", "text": body.text.strip()[:2000]})
    if x["mode"] == "live":
        p, ctx = await practice_ctx(x, member)
        out = await ai_json("practice_reply", f"This is a PRACTICE rehearsal. Play {p['nickname']} in character, replying to the member's last line the way they plausibly would, "
                            "based only on what is known about them (tone, habits, how they text). Stay realistic, not cruel, not a pushover. One message, texting length. "
                            'Return JSON {"reply": "..."}', ctx)
        x["messages"].append({"role": "them", "text": str(out.get("reply") or "…")[:1500]})
        x["options"] = []
    else:
        x["options"] = await likely_options(x, member)
    current = await own_session(sid, member)
    if current.get("contact_pause"):
        raise HTTPException(409, current["contact_pause"])
    x.update(feedback=None, ended=False)
    update = {"$set": {"messages": x["messages"], "options": x["options"], "feedback": None, "ended": False}}
    if "rounds" in x:
        update["$set"]["rounds"] = x["rounds"]
    if member.get("role") != "admin":
        update["$addToSet"] = {"free_reply_ids": reply_id}
    update["$inc"] = {"turn_revision": 1}
    changed = await db.practice.update_one({"user_id": member["user_id"], "id": sid, "messages": previous_messages,
                                           "turn_revision": {"$in": [None, 0]} if revision == 0 else revision}, update)
    if not changed.matched_count:
        raise HTTPException(409, "The practice session changed. Refresh before sending again.")
    x.pop("free_reply_ids", None)
    x["turn_revision"] = revision + 1
    return x


async def change_rehearsal(session, member, fields):
    revision = session.get("turn_revision", 0)
    changed = await db.practice.update_one(
        {"id": session["id"], "user_id": member["user_id"], "turn_revision": {"$in": [None, 0]} if revision == 0 else revision},
        {"$set": fields, "$inc": {"turn_revision": 1}})
    if not changed.matched_count:
        raise HTTPException(409, "The rehearsal changed. Reopen it before making this change.")
    session["turn_revision"] = revision + 1


@api.post("/practice/{sid}/pick")
async def practice_pick(sid: str, body: PickIn, member: dict = Depends(subscribed_member)):
    x = await own_session(sid, member)
    require_practice_contact(x)
    if x.get("contact_pause"):
        return x
    if not 0 <= body.index < len(x["options"]):
        raise HTTPException(status_code=404, detail="Option not found")
    o = x["options"][body.index]
    line = next((m["text"] for m in reversed(x["messages"]) if m["role"] == "me"), "")
    mi = max((i for i, m in enumerate(x["messages"]) if m["role"] == "me"), default=0)
    rounds = (x.get("rounds") or []) + [{"line": line, "options": x["options"], "picked": body.index, "msg_index": mi}]
    x["messages"].append({"role": "them", "text": o["reaction"]})
    x["options"] = []
    x["rounds"] = rounds
    await change_rehearsal(x, member, {"messages": x["messages"], "options": [], "rounds": rounds})
    return await own_session(sid, member)


@api.post("/practice/{sid}/to-profile")
async def practice_to_profile(sid: str, member: dict = Depends(subscribed_member)):
    x = await own_session(sid, member)
    require_practice_contact(x)
    if x.get("contact_pause"):
        raise HTTPException(409, x["contact_pause"])
    if not any(m["role"] == "me" for m in x["messages"]):
        raise HTTPException(status_code=422, detail="Practise a little first")
    await own_person(x["person_id"], member)
    await require_generation_access(member)
    _, ctx = await practice_ctx(x, member)
    src = f"practice:{sid}"
    allp = await db.patterns.find({"user_id": member["user_id"]}, {"_id": 0, "id": 1, "text": 1, "sources": 1}).to_list(50)
    already = [p["text"] for p in allp if src in p.get("sources", [])]
    pats = [{"id": p["id"], "observation": p["text"]} for p in allp if src not in p.get("sources", [])]
    out = await ai_json("practice_insights", "Describe up to three things the member actually did in THIS rehearsal, or none if their words give too little evidence. "
                        "Describe the specific choice in this exercise. Do not infer a lasting trait or treat a simulated reply as a real event. Existing observations are for duplicate matching only. "
                        "Only what the member's lines clearly show. Write in second person, addressing the member as 'you', max 25 words, no clinical labels. "
                        'Return JSON {"insights": [{"text": "...", "matches_existing": "existing pattern id or null"}]}',
                        f"{ctx}\n\nEXISTING PATTERNS: {json.dumps(pats)}\nALREADY SAVED FROM THIS SESSION: {json.dumps(already)}")
    saved = []
    for it in (out.get("insights") or [])[:3]:
        if not isinstance(it, dict) or not isinstance(it.get("text"), str):
            continue
        record, created = await pattern_evidence.save(db, member["user_id"], it["text"], [src], now(), it.get("matches_existing"), allp)
        if record:
            saved.append({"id": record["id"], "text": record["text"], "merged": not created})
    prev = x.get("profile_saved") or []
    saved = prev + [s for s in saved if s["id"] not in {p["id"] for p in prev}]
    await db.practice.update_one({"id": sid}, {"$set": {"profile_saved": saved}})
    return {"saved": saved}


@api.post("/practice/{sid}/branch")
async def practice_branch(sid: str, body: BranchIn, member: dict = Depends(subscribed_member)):
    x = await own_session(sid, member)
    if x.get("contact_pause"):
        return x
    rounds = x.get("rounds") or []
    if not 0 <= body.round < len(rounds) or not 0 <= body.index < len(rounds[body.round]["options"]):
        raise HTTPException(status_code=404, detail="Reply not found")
    r = rounds[body.round]
    cut = r.get("msg_index")
    if cut is None:
        seen = 0
        for i, m in enumerate(x["messages"]):
            if m["role"] == "me":
                if seen == body.round:
                    cut = i
                    break
                seen += 1
    if cut is None or cut >= len(x["messages"]) or x["messages"][cut]["role"] != "me":
        raise HTTPException(status_code=404, detail="Reply not found")
    msgs = x["messages"][:cut + 1] + [{"role": "them", "text": r["options"][body.index]["reaction"]}]
    rounds = rounds[:body.round] + [{**r, "picked": body.index}]
    await change_rehearsal(x, member, {"messages": msgs, "rounds": rounds, "options": [], "feedback": None, "ended": False})
    return await own_session(sid, member)


@api.post("/practice/{sid}/restart")
async def practice_restart(sid: str, member: dict = Depends(subscribed_member)):
    current = await own_session(sid, member)
    if current.get("contact_pause"):
        return current
    await change_rehearsal(current, member, {"messages": [], "options": [], "rounds": [], "feedback": None, "ended": False})
    return await own_session(sid, member)


@api.post("/practice/{sid}/end")
async def practice_end(sid: str, member: dict = Depends(subscribed_member)):
    x = await own_session(sid, member)
    require_practice_contact(x)
    if x.get("contact_pause"):
        return x
    if x.get("ended") and x.get("feedback") is not None:
        return x
    if not any(m["role"] == "me" for m in x["messages"]):
        raise HTTPException(status_code=422, detail="Say something first")
    await own_person(x["person_id"], member)
    await require_generation_access(member)
    _, ctx = await practice_ctx(x, member)
    out = await ai_json("practice_feedback", "This was a PRACTICE rehearsal. Give the member short, kind, honest feedback on their own lines. Everyday words. "
                        'Return JSON {"landed": ["1-3 things that worked"], "backfire": ["1-3 things that might backfire"], "cleaned": "a cleaned-up version of the member’s main message in their own voice"}', ctx)
    fb = {"landed": [str(v) for v in out.get("landed") or []][:3], "backfire": [str(v) for v in out.get("backfire") or []][:3], "cleaned": str(out.get("cleaned") or "")[:1500]}
    rounds = x.get("rounds") or []
    if x.get("options"):
        rounds = rounds + [{"line": next((m["text"] for m in reversed(x["messages"]) if m["role"] == "me"), ""), "options": x["options"], "picked": None,
                            "msg_index": max((i for i, m in enumerate(x["messages"]) if m["role"] == "me"), default=0)}]
    await change_rehearsal(x, member, {"feedback": fb, "ended": True, "rounds": rounds, "options": []})
    return await own_session(sid, member)


@api.delete("/practice/{sid}")
async def delete_practice(sid: str, member: dict = Depends(subscribed_member)):
    await own_session(sid, member)
    await db.practice.delete_one({"id": sid})
    await drop_pattern_sources(member["user_id"], [f"practice:{sid}"])
    return {"ok": True}


@api.post("/ocr")
async def ocr(body: OcrIn, member: dict = Depends(subscribed_member),
              upload_attempt: Annotated[uuid.UUID | None, Header(alias="X-Upload-Attempt")] = None):
    await require_generation_access(member)
    with message_costs.preparation_context(message_charge.upload_id(member["user_id"], upload_attempt)):
        return {"lines": await transcribe_screenshot(body.image_base64, body.nickname)}


# ---------------- prepare ----------------
PREP_SHAPE = """Describe 3 or 4 different ways the conversation could go. For each, describe something the member
could hear or see and give a practical next action or line in their voice. Leave out predictions of motives,
odds, probabilities and ranked likelihoods.
{"opening_line": "a line the member could say", "reactions": [{"reaction": "what the other person might say or do",
"how_to_handle": "a concrete next step or words to use"}], "avoid": ["specific pitfalls"],
"exit_line": "words for pausing or ending the conversation", "uncertainty": "what the available evidence leaves open"}"""


@api.get("/people/{pid}/preps")
async def list_preps(pid: str, member: dict = Depends(subscribed_member)):
    await own_person(pid, member)
    docs = await db.preps.find({"person_id": pid, "user_id": member["user_id"]}, {"_id": 0}).sort([("pinned", -1), ("created_at", -1)]).to_list(100)
    return [game_plans.saved_plan(doc) for doc in docs]


class PinIn(BaseModel):
    pinned: bool


@api.get("/people/{pid}/preps/{xid}")
async def focused_prep(pid: str, xid: str, member: dict = Depends(subscribed_member)):
    await own_person(pid, member)
    plan = await db.preps.find_one({"user_id": member["user_id"], "person_id": pid, "id": xid,
                                    "deleting": {"$ne": True}}, {"_id": 0})
    if not plan:
        raise HTTPException(404, "Game plan unavailable.")
    return game_plans.saved_plan(plan)


async def own_prep(xid: str, member: dict) -> dict:
    x = await db.preps.find_one({"id": xid, "user_id": member["user_id"], "deleting": {"$ne": True}}, {"_id": 0})
    if not x:
        raise HTTPException(status_code=404, detail="Not found")
    return x


@api.patch("/preps/{xid}")
async def pin_prep(xid: str, body: PinIn, member: dict = Depends(subscribed_member)):
    await own_prep(xid, member)
    await db.preps.update_one({"id": xid}, {"$set": {"pinned": body.pinned}})
    return {"ok": True, "pinned": body.pinned}


class PlanOutcomeIn(TextIn):
    use_for_coaching: bool = True  # Older clients used this action to create a coaching memory.
    expected_revision: int | None = Field(None, ge=0)


@api.post("/preps/{xid}/outcome")
async def prep_outcome(xid: str, body: PlanOutcomeIn, bg: BackgroundTasks, member: dict = Depends(subscribed_member)):
    x = await own_prep(xid, member)
    await own_person(x["person_id"], member)
    text = body.text.strip()[:2000]
    if not text:
        raise HTTPException(status_code=422, detail="Tell us what happened")
    owner = {"user_id": member["user_id"], "person_id": x["person_id"]}
    prep_query = {**owner, "id": xid, "deleting": {"$ne": True}}
    include = getattr(body, "use_for_coaching", True)
    expected = getattr(body, "expected_revision", None)
    revision = x.get("outcome_revision", 0)
    stored_choice = x.get("outcome_use_for_coaching", bool(x.get("outcome")))
    memories = {**owner, "prep_id": xid, "kind": "outcome"}
    same_value = text == x.get("outcome") and include == stored_choice
    replay = same_value and (not include or x.get("outcome_memory_id"))
    previous = set(await db.memories.distinct("id", memories))
    previous.update(x.get("outcome_cleanup_ids") or [])
    if x.get("outcome_memory_id"):
        previous.add(x["outcome_memory_id"])
    if replay:
        memory_id, stamp = x.get("outcome_memory_id"), x.get("outcome_at") or now()
        previous.discard(memory_id)
    else:
        if expected is not None and expected != revision:
            raise HTTPException(409, "This outcome changed. Reopen the saved version before editing.")
        memory_id, stamp = new_id() if include else None, now()
        previous_revision = {"$in": [None, 0]} if revision == 0 else revision
        updated = await db.preps.update_one({**prep_query, "outcome_revision": previous_revision},
            {"$set": {"outcome": text, "outcome_at": stamp, "outcome_memory_id": memory_id,
                      "outcome_cleanup_ids": list(previous), "outcome_use_for_coaching": include}, "$inc": {"outcome_revision": 1}})
        if not updated.matched_count:
            raise HTTPException(409, "The game plan changed. Refresh before recording the outcome.")
        revision += 1
    materialize = include and not await db.memories.find_one({**memories, "id": memory_id}, {"_id": 1})
    if materialize:
        try:
            await db.memories.insert_one({**memories, "_id": memory_id, "id": memory_id, "text": text[:500],
                "source": f"Outcome of game plan {x['created_at'][:10]}: {x['situation'][:60]}", "derived_from": [], "created_at": stamp})
        except DuplicateKeyError:
            pass  # A retry already materialized the same revision.
    current_revision = {"$in": [None, 0]} if revision == 0 else revision
    current = {**prep_query, "outcome_revision": current_revision}
    if not await db.preps.find_one(current, {"_id": 1}):
        if materialize:
            await game_plans.clear_outcome_memories(db, {**memories, "id": memory_id})
        raise HTTPException(409, "The game plan changed. Refresh before recording the outcome.")
    if previous:
        await game_plans.clear_outcome_memories(db, {**memories, "id": {"$in": list(previous)}})
    if replay and not previous and not materialize:
        return {"ok": True, "outcome": text, "outcome_at": stamp, "outcome_revision": revision}
    await db.notifications.update_many({"user_id": member["user_id"], "key": f"checkin:{xid}"}, {"$set": {"read": True}})
    await db.people.update_one({"id": x["person_id"]}, {"$set": {"last_activity": now()}})
    if previous:
        await db.people.update_one({"user_id": owner["user_id"], "id": owner["person_id"]},
                                   {"$set": {"report": None, "working_read": None, "report_status": "empty"}, "$unset": {"report_run": ""}})
    if previous:
        await db.preps.update_one(current, {"$unset": {"outcome_cleanup_ids": ""}})
    if include or previous:
        with await message_charge.saved_context(db, xid, ENV):
            await message_charge.enqueue(db, bg, regenerate_report, x["person_id"],
                                         ["Coaching memories for a game plan changed"], source_removed=bool(previous))
    return {"ok": True, "outcome": text, "outcome_at": stamp, "outcome_revision": revision}


@api.delete("/preps/{xid}/outcome")
async def remove_plan_outcome(xid: str, expected_revision: int = Query(ge=0), member: dict = Depends(subscribed_member)):
    plan = await own_prep(xid, member)
    owner = {"user_id": member["user_id"], "person_id": plan["person_id"]}
    memories = {**owner, "prep_id": xid, "kind": "outcome"}
    previous = set(plan.get("outcome_cleanup_ids") or [])
    if plan.get("outcome"):
        previous.update(await db.memories.distinct("id", memories))
        if plan.get("outcome_memory_id"):
            previous.add(plan["outcome_memory_id"])
        version = {"$in": [None, 0]} if expected_revision == 0 else expected_revision
        updated = await db.preps.update_one({**owner, "id": xid, "outcome_revision": version},
                {"$set": {"outcome": None, "outcome_at": None, "outcome_cleanup_ids": list(previous),
                          "outcome_memory_id": None, "outcome_use_for_coaching": False}, "$inc": {"outcome_revision": 1}})
        if not updated.matched_count:
            raise HTTPException(409, "This outcome changed. Reopen it before removing it.")
    if previous:
        await game_plans.clear_outcome_memories(db, {**memories, "id": {"$in": list(previous)}})
        await db.people.update_one({"user_id": owner["user_id"], "id": owner["person_id"]},
            {"$set": {"report": None, "working_read": None, "report_status": "empty"}, "$unset": {"report_run": ""}})
        await db.preps.update_one({**owner, "id": xid}, {"$pull": {"outcome_cleanup_ids": {"$in": list(previous)}}})
    return {"ok": True}


@api.post("/people/{pid}/preps")
async def create_prep(pid: str, body: PrepIn, request: Request, member: dict = Depends(reply_member)):
    p = await own_person(pid, member)

    async def generate(result_id: str):
        s = await get_settings()
        ctx, bo = await full_context(p, member)
        prompt = f"{ctx}\n\nUPCOMING ENCOUNTER: {body.situation}\n\nShape:\n{PREP_SHAPE}"
        for attempt in range(2):
            out = await ai_json("prepare", f"You are Imladri.\n{MEMBER_ADVICE_RULE}", prompt, settings=s, tone=prompt_key(p))
            out.pop("contact_boundary", None)
            quote = out.pop("boundary_evidence", None)
            evidence, reopened = contact_boundary.turn_evidence([], body.situation, quote)
            if not evidence and not reopened:
                evidence = await recent_contact_boundary(p)
            if evidence:
                out = contact_boundary.plan(p["nickname"], evidence)
            try:
                plan = game_plans.clean_plan(out, generated=True)
                break
            except game_plans.IncompletePlan:
                if attempt:
                    raise
                prompt += ("\nReturn a complete plan with an opening_line, an exit_line, and exactly 3 distinct "
                           "reactions. Each reaction needs its own observable reaction and practical how_to_handle.")
        doc = {"id": result_id, "user_id": member["user_id"], "person_id": pid, "situation": body.situation,
               "plan": plan, "pinned": False, "outcome": None, "based_on": bo, "created_at": now()}
        if not await db.people.find_one({"id": pid}, {"_id": 1}):
            raise HTTPException(status_code=404, detail="This person was deleted")
        await db.preps.insert_one(doc)
        await db.people.update_one({"id": pid}, {"$set": {"last_activity": now()}})
        return strip(doc)

    return await metered_reply(member, request, body, "prep", pid, generate)


# ---------------- memories ----------------
@api.get("/people/{pid}/memories")
async def list_memories(pid: str, member: dict = Depends(subscribed_member),
                        page_size: Annotated[int, Query(ge=0, le=100)] = 0,
                        cursor: Annotated[str, Query(max_length=1024)] = ""):
    p = await own_person(pid, member)
    owner = {"user_id": member["user_id"], "person_id": pid}
    details = [{"id": f"intake:{k}", "kind": "profile_detail", "text": v, "source": f"Profile: {FIELD_LABELS[k]}",
                "label": FIELD_LABELS[k], "derived_from": []} for k, v in (p.get("intake") or {}).items() if v and k in FIELD_LABELS]
    if p.get("background"):
        details.insert(0, {"id": "intake:background", "kind": "profile_detail", "text": p["background"], "source": "Profile: background", "label": "Background", "derived_from": []})
    listing = await member_pages.page(db.memories, owner, size=page_size or 1000, cursor=cursor)
    if page_size:
        return {**listing, "items": ([] if cursor else details) + listing["items"], "total": listing["total"] + len(details)}
    return details + listing["items"]


@api.get("/people/{pid}/evidence/{kind}/{key}")
async def read_person_evidence(pid: str, kind: Literal["exchange", "correction", "memory", "profile"], key: str,
                               member: dict = Depends(subscribed_member)):
    person = await own_person(pid, member)
    if kind == "profile":
        value = person.get("background") if key == "background" else (person.get("intake") or {}).get(key)
        if value and (key == "background" or key in FIELD_LABELS):
            return {"text": value, "label": FIELD_LABELS.get(key, "Background")}
    else:
        table = {"exchange": db.exchanges, "memory": db.memories, "correction": db.corrections}[kind]
        row = await table.find_one({"user_id": member["user_id"], "person_id": pid, "id": key},
                                   {"_id": 0, "text": 1, "question": 1, "conversation": 1, "outcome": 1,
                                    "note": 1, "created_at": 1, "edited_at": 1})
        if row:
            text = row.get("text") or row.get("question") or "Saved conversation"
            if kind == "correction":
                text = "Flagged interpretation: " + (row.get("text") or "Previous interpretation")
                if row.get("note"):
                    text += "\nMember correction: " + row["note"]
            elif kind == "exchange":
                pieces = [report_sources.reviewed_messages(row)]
                if row.get("question"):
                    pieces.append("Member question: " + row["question"])
                if isinstance(row.get("outcome"), str) and row["outcome"]:
                    pieces.append("Member-reported outcome: " + row["outcome"])
                text = "\n".join(filter(None, pieces)) or "No saved transcript"
            return {"text": text,
                    "created_at": row.get("edited_at") or row.get("created_at"), "label": kind.title()}
    raise HTTPException(404, "This source is no longer available.")


@api.get("/exchanges/{eid}/context-memories")
async def exchange_memories(eid: str, member: dict = Depends(subscribed_member)):
    exchange = await own_exchange(eid, member)
    ids = exchange.get("memory_context_ids")
    if ids is None:
        return {"recorded": False, "items": []}
    query = {"user_id": member["user_id"], "person_id": exchange["person_id"], "id": {"$in": ids}}
    rows = await db.memories.find(query, {"_id": 0, "id": 1, "kind": 1, "text": 1, "edited_at": 1}).to_list(300)
    return {"recorded": True, "items": rows, "removed": max(0, len(ids)-len(rows))}


class MemoryPriority(BaseModel):
    prioritized: bool


@api.put("/memories/{mid}/priority")
async def prioritize_memory(mid: str, body: MemoryPriority, member: dict = Depends(subscribed_member)):
    updated = await db.memories.update_one({"id": mid, "user_id": member["user_id"]}, {"$set": {"prioritized": body.prioritized}})
    if not updated.matched_count:
        raise HTTPException(404, "This memory is unavailable.")
    return {"prioritized": body.prioritized}


@api.patch("/memories/{mid}")
async def edit_memory(mid: str, body: TextIn, member: dict = Depends(subscribed_member)):
    owner = {"id": mid, "user_id": member["user_id"]}
    value = body.text.strip()[:500]
    changed = await db.memories.find_one_and_update({**owner, "text": {"$ne": value}},
        {"$set": {"text": value, "edited_at": now()}}, projection={"person_id": 1})
    if changed:
        await invalidate_person_report(changed["person_id"], member["user_id"])
        await track(member["user_id"], "memory_edit", {"action": "edit"})
    elif not await db.memories.find_one(owner, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


@api.delete("/memories/{mid}")
async def delete_memory(mid: str, bg: BackgroundTasks, member: dict = Depends(subscribed_member)):
    m = await db.memories.find_one({"id": mid, "user_id": member["user_id"]}, {"_id": 0})
    if not m:
        raise HTTPException(status_code=404, detail="Not found")
    removed = await db.memories.delete_one({"id": mid, "user_id": member["user_id"]})
    derived_count = await game_plans.clear_derived_memories(db, {"user_id": member["user_id"], "person_id": m["person_id"]}, [mid])
    if m["kind"] == "outcome" and m.get("exchange_id"):
        await db.exchanges.update_one({"id": m["exchange_id"], "user_id": member["user_id"]},
                                      {"$set": {"outcome": None, "outcome_at": None, "outcome_result": None, "outcome_note": "", "read_update": None}})
    if m["kind"] == "outcome" and m.get("prep_id"):
        await db.preps.update_one({"id": m["prep_id"], "user_id": member["user_id"],
                                   "$or": [{"outcome_memory_id": mid}, {"outcome_memory_id": {"$exists": False}}]},
                                  {"$set": {"outcome_memory_id": None, "outcome_use_for_coaching": False},
                                   "$inc": {"outcome_revision": 1}})
    if removed.deleted_count:
        await track(member["user_id"], "memory_edit", {"action": "delete"})
    await invalidate_person_report(m["person_id"], member["user_id"])
    bg.add_task(ai_meter.in_context, "background", regenerate_report, m["person_id"], ["A memory was deleted"], source_removed=True)
    return {"ok": True, "derived_removed": derived_count}


@api.delete("/people/{pid}/details/{key}")
async def delete_detail(pid: str, key: str, bg: BackgroundTasks, member: dict = Depends(subscribed_member)):
    p = await own_person(pid, member)
    if key == "background":
        changed = await db.people.update_one({"id": pid, "background": {"$nin": [None, ""]}}, {"$set": {"background": ""}})
    elif key in FIELD_LABELS:
        changed = await db.people.update_one({"id": pid}, {"$unset": {f"intake.{key}": ""}})
    else:
        raise HTTPException(status_code=404, detail="Not found")
    if changed.modified_count:
        await track(member["user_id"], "memory_edit", {"action": "delete"})
        if p.get("report"):
            bg.add_task(ai_meter.in_context, "background", regenerate_report, pid, ["A profile detail was deleted"], source_removed=True)
    return {"ok": True}


# ---------------- patterns ----------------
@api.get("/patterns")
async def list_patterns(member: dict = Depends(assessment_member)):
    uid = member["user_id"]
    query = {"user_id": uid, "$or": [{"sources.0": {"$exists": True}}, {"edited": True}, {"member_confirmed": True}]}
    rows = await db.patterns.find(query, {"_id": 0, "generated_text_key": 0}).to_list(100)
    assessed = await pattern_evidence.assess(db, uid, rows)
    ids = {pid for row in assessed for pid in row["source_people"]}
    names = {p["id"]: p["nickname"] async for p in db.people.find({"user_id": uid, "id": {"$in": list(ids)}},
                                                                 {"_id": 0, "id": 1, "nickname": 1})}
    for row in assessed:
        row["people"] = sorted({names[pid] for pid in row.pop("source_people") if pid in names})
    return assessed


@api.post("/patterns/scan")
async def scan_patterns(member: dict = Depends(subscribed_member)):
    await require_generation_access(member)
    uid = member["user_id"]
    query = {"user_id": uid}
    exs = await db.exchanges.find(query, {"_id": 0, "id": 1, "person_id": 1, "conversation": 1, "question": 1, "outcome": 1}).sort("created_at", -1).to_list(20)
    if not exs:
        raise HTTPException(status_code=422, detail="Share a conversation or ask about someone first")
    names = {p["id"]: p["nickname"] for p in await db.people.find({"user_id": uid}, {"_id": 0, "id": 1, "nickname": 1}).to_list(500)}
    pats = await db.patterns.find({"user_id": uid}, {"_id": 0}).to_list(50)
    blocks = []
    for e in exs:
        convo = " | ".join(f"{'MEMBER' if l['speaker'] == 'me' else 'THEM'}: {l['text']}" for l in (e.get("conversation") or [])[:30])
        blocks.append(f"[{e['id']}] with {names.get(e['person_id'], 'someone')}: question='{(e.get('question') or '')[:300]}' convo='{convo[:1500]}' outcome='{e.get('outcome') or '-'}'")
    out = await ai_json("profile_patterns", "Find specific observations about the member's own behavior in the supplied exchanges. Cite only exchanges whose member-authored words support that observation. "
                        "One occasion is tentative. Copies, questions about the same incident and rehearsals add no independent evidence. Return none if evidence is weak. "
                        "Match existing wording by meaning only if newly cited words independently support it. "
                        "Always write in second person, addressing the member as 'you'. "
                        'Return JSON {"patterns": [{"text": "max 25 words, speaking as you", "sources": ["exchange ids"], "matches_existing": "existing id or null"}]} with 0-5 items.',
                        f"OBSERVATIONS FOR DUPLICATE MATCHING: {json.dumps([{'id': x['id'], 'observation': x['text']} for x in pats])}\n\nEXCHANGES:\n" + "\n".join(blocks))
    valid = {e["id"] for e in exs}
    added = 0
    for hp in out.get("patterns") or []:
        if not isinstance(hp, dict) or not isinstance(hp.get("text"), str) or not isinstance(hp.get("sources"), list):
            continue
        src = list(dict.fromkeys(s for s in hp["sources"] if isinstance(s, str) and s in valid))
        if src:
            row, created = await pattern_evidence.save(db, uid, hp["text"], src, now(), hp.get("matches_existing"), pats)
            if created and row:
                pats.append(row)
                added += 1
    return {"added": added}


# ---------------- group chat ----------------
STARTER_SYS = ("You are Imladri. Write exactly 3 short questions (max 12 words each) the member could tap to ask about the person(s) below, "
               "specific to what is known (stage, goal, recent chats, archetype). Write in the first person ('Should I…', 'How could I ask…'). "
               'Return JSON {"starters": ["q1", "q2", "q3"]}.')


async def starter_prompt(people: list, member: dict, s: dict) -> str:
    profile = await saved_profile(member, s["quiz"])
    blocks = [(await full_context(p, member, limit_ex=2, profile=profile))[0] for p in people]
    return "\n\n=====\n\n".join(blocks)


async def make_starters(people: list, member: dict, prompt: str, s: dict) -> list:
    if not await has_reply_allowance(member):
        return []
    out = await ai_json("starters", STARTER_SYS, prompt, settings=s)
    return [str(x)[:120] for x in (out.get("starters") or []) if x][:3]


@api.get("/people/{pid}/starters")
async def person_starters(pid: str, member: dict = Depends(subscribed_member)):
    started = now()
    p = await own_person(pid, member)
    s = await get_settings()
    prompt = await starter_prompt([p], member, s)
    sig = context_signature(STARTER_SYS, s["prompts"]["starters"], prompt)
    cached = p.get("starters") or {}
    if cached.get("sig") == sig and cached.get("items"):
        return {"starters": cached["items"], "batch_id": cached.get("batch_id")}
    batch = str(uuid.uuid4())
    with message_costs.preparation_context(message_charge.starter_id(member["user_id"], pid, batch)):
        items = await make_starters([p], member, prompt, s)
    if items:
        await db.people.update_one({"id": pid, "$or": [{"starters.started": {"$exists": False}}, {"starters.started": {"$lte": started}}]},
                                   {"$set": {"starters": {"sig": sig, "items": items, "started": started, "batch_id": batch}}})
    return {"starters": items, "batch_id": batch}


async def verify_group_members_after_write(ids, uid, collection, selector):
    # A member can delete one of these profiles while the provider is answering.
    count = await db.people.count_documents({"user_id": uid, "id": {"$in": ids}, "deleting": {"$ne": True}})
    if count != len(ids):
        await collection.delete_one({"user_id": uid, **selector})
        raise HTTPException(404, "A profile in this group was deleted.")


@api.post("/group-chat/starters")
async def group_starters(body: GroupIdsIn, member: dict = Depends(subscribed_member)):
    started = now()
    ids = sorted(set(body.person_ids))
    if len(ids) < 2:
        raise HTTPException(status_code=422, detail="Pick at least two different people")
    people = [await own_person(i, member) for i in ids]
    group = {"user_id": member["user_id"], "key": ",".join(ids)}
    n = await db.group_chats.count_documents(group)
    s = await get_settings()
    prompt = await starter_prompt(people, member, s)
    sig = context_signature(STARTER_SYS, s["prompts"]["starters"], prompt, n)
    cache_id = context_signature(member["user_id"], ids)
    cached = await db.group_starters.find_one({"_id": cache_id})
    if cached and cached.get("sig") == sig and cached.get("items"):
        return {"starters": cached["items"], "batch_id": cached.get("batch_id")}
    batch = str(uuid.uuid4())
    with message_costs.preparation_context(message_charge.starter_id(member["user_id"], group["key"], batch)):
        items = await make_starters(people, member, prompt, s)
    if items:
        # One document per group, even on two simultaneous first requests.
        await db.group_starters.update_one({"_id": cache_id}, {"$setOnInsert": group}, upsert=True)
        await db.group_starters.update_one({"_id": cache_id, "$or": [{"started": {"$exists": False}}, {"started": {"$lte": started}}]},
                                           {"$set": {"sig": sig, "items": items, "started": started, "batch_id": batch}})
        await verify_group_members_after_write(ids, member["user_id"], db.group_starters, {"_id": cache_id})
    return {"starters": items, "batch_id": batch}


@api.get("/chat-groups")
async def list_chat_groups(member: dict = Depends(subscribed_member), page_size: Annotated[int, Query(ge=0, le=100)] = 0,
                           cursor: Annotated[str, Query(max_length=1024)] = "", q: Annotated[str, Query(max_length=80)] = ""):
    criteria = {"user_id": member["user_id"]}
    if q.strip():
        criteria["name"] = {"$regex": re.escape(q.strip()), "$options": "i"}
    listing = await member_pages.page(db.chat_groups, criteria, size=page_size or 50, cursor=cursor, order=1)
    return listing if page_size else listing["items"]


@api.post("/chat-groups")
async def save_chat_group(body: ChatGroupIn, member: dict = Depends(subscribed_member)):
    ids = sorted(set(body.person_ids))
    if len(ids) < 2 or not body.name.strip():
        raise HTTPException(status_code=422, detail="Give the group a name and at least two different people")
    for i in ids:
        await own_person(i, member)
    doc = {"id": new_id(), "user_id": member["user_id"], "name": body.name.strip(), "person_ids": ids, "created_at": now()}
    await db.chat_groups.insert_one(doc)
    await verify_group_members_after_write(ids, member["user_id"], db.chat_groups, {"id": doc["id"]})
    return strip(doc)


@api.delete("/chat-groups/{gid}")
async def delete_chat_group(gid: str, member: dict = Depends(subscribed_member)):
    r = await db.chat_groups.delete_one({"id": gid, "user_id": member["user_id"]})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


@api.get("/group-chat")
async def list_group_chat(ids: str, member: dict = Depends(subscribed_member), limit: int = Query(200, ge=1, le=200),
                          before_time: str = Query("", max_length=64), before_id: str = Query("", max_length=256)):
    person_ids = sorted(set(ids.split(",")))
    key = ",".join(person_ids)
    docs = await conversation_page(db.group_chats, {"user_id": member["user_id"], "key": key}, before_time, before_id, limit)
    if not docs:
        return []
    names = await group_contact_names(person_ids, member)
    if len(names) != len(person_ids):
        return []
    return [contact_boundary.saved_group(doc, names) for doc in docs]


async def group_contact_names(ids, member):
    rows = db.people.find({"id": {"$in": ids}, "user_id": member["user_id"], "deleting": {"$ne": True}},
                         {"nickname": 1, "_id": 0})
    return [row["nickname"] async for row in rows]


@api.post("/group-chat")
async def ask_group(body: GroupAskIn, request: Request, member: dict = Depends(reply_member),
                    starter_batch: Annotated[uuid.UUID | None, Header(alias="X-Starter-Batch")] = None):
    ids = sorted(set(body.person_ids))
    if len(ids) < 2:
        raise HTTPException(422, "Pick at least two different people")
    async def generate(reply_id):
        return await generate_group_answer(ids, body.question, member, reply_id)
    return await metered_reply(member, request, body, "group", ",".join(ids), generate, starter_batch=starter_batch)


async def generate_group_answer(ids, question, member, reply_id):
    s = await get_settings()
    people = [await own_person(i, member) for i in ids]
    blocks, based = [], []
    for p in people:
        ctx, bo = await full_context(p, member, limit_ex=2)
        blocks.append(ctx)
        based.append(p["nickname"])
    names = ", ".join(p["nickname"] for p in people)
    shape = EX_SHAPE.split('\n"proposed_memories"')[0] + '\n"deepen_prompt": null}'
    out = await ai_json("group_read", f"You are Imladri, an advisor helping the member understand relationships in their life. The member is asking about several people at once ({names}). "
                        f"Weigh how they relate to each other and to the member.\n{MEMBER_ADVICE_RULE}",
                         "\n\n=====\n\n".join(blocks) + f"\n\nMEMBER QUESTION: {question}\n\nShape:\n{shape}", settings=s)
    out.pop("contact_boundary", None)
    quote = out.pop("boundary_evidence", None)
    evidence, reopened = contact_boundary.turn_evidence([], question, quote)
    target = contact_boundary.group_target(question, evidence, based)
    if not target and not reopened:
        for person in people:
            if contact_boundary.relevant_to_group(question, person["nickname"], based):
                evidence = await recent_contact_boundary(person)
                if evidence:
                    target = person["nickname"]
                    break
    if target and evidence:
        out = contact_boundary.group_read(target, evidence, based)
    advice_wording.respect_undecided_time(out, question)
    advice_wording.respect_stated_days(out, question)
    out.pop("her_pattern", None)
    out.pop("proposed_memories", None)
    doc = {"id": reply_id, "user_id": member["user_id"], "key": ",".join(ids), "person_ids": ids, "question": question,
           "response": out, "based_on": [f"profiles: {names}"], "created_at": now()}
    await db.group_chats.insert_one(doc)
    await verify_group_members_after_write(ids, member["user_id"], db.group_chats, {"id": reply_id})
    return strip(doc)


@api.delete("/group-chat/{cid}")
async def delete_group_chat(cid: str, member: dict = Depends(subscribed_member)):
    r = await db.group_chats.delete_one({"id": cid, "user_id": member["user_id"]})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


@api.patch("/patterns/{pid}")
async def edit_pattern(pid: str, body: TextIn, member: dict = Depends(assessment_member)):
    text = body.text.strip()[:300]
    if not text:
        raise HTTPException(422, "Enter the observation in your words.")
    owner = {"id": pid, "user_id": member["user_id"]}
    pattern = await db.patterns.find_one(owner, {"_id": 0, "text": 1})
    if not pattern:
        raise HTTPException(404, "Not found")
    if pattern["text"] != text:
        # Previous sources and confirmation described the old wording.
        result = await db.patterns.update_one({**owner, "text": pattern["text"]},
            {"$set": {"text": text, "edited": True, "sources": [], "wording_evidence_version": 1}, "$unset": {"member_confirmed": "", "generated_text_key": ""}})
        if not result.matched_count:
            raise HTTPException(409, "The wording changed. Review it again.")
        await db.practice.update_many({"user_id": member["user_id"], "profile_saved.id": pid}, {"$pull": {"profile_saved": {"id": pid}}})
    return {"ok": True}


class PatternConfirmation(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    confirmed: bool


@api.put("/patterns/{pid}/confirmation")
async def confirm_pattern(pid: str, body: PatternConfirmation, member: dict = Depends(assessment_member)):
    owner = {"id": pid, "user_id": member["user_id"]}
    result = await db.patterns.update_one({**owner, "text": body.text}, {"$set": {"member_confirmed": body.confirmed}})
    if not result.matched_count:
        if await db.patterns.find_one(owner, {"_id": 1}):
            raise HTTPException(409, "The wording changed. Review it again.")
        raise HTTPException(404, "Not found")
    return {"ok": True}


@api.delete("/patterns/{pid}")
async def delete_pattern(pid: str, member: dict = Depends(assessment_member)):
    r = await db.patterns.delete_one({"id": pid, "user_id": member["user_id"]})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    await db.practice.update_many({"user_id": member["user_id"], "profile_saved.id": pid}, {"$pull": {"profile_saved": {"id": pid}}})
    return {"ok": True}


async def couples_inbox_items(member):
    reminders, offer = await asyncio.gather(couples_reminders.inbox(db, member), partner_offers.inbox_notice(db, member))
    if offer:
        return sorted([*reminders, offer], key=lambda item: datetime.fromisoformat(item["created_at"]), reverse=True)
    return reminders


@api.get("/couples/notifications")
async def couples_notifications(response: Response, member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "no-store"
    items = await couples_inbox_items(member)
    return {"items": items, "unread": sum(not item["read"] for item in items)}


@api.post("/couples/notifications/read-all")
@api.post("/couples/notifications/{nid}/read")
async def read_couples_notifications(nid: str | None = None, member: dict = Depends(current_member)):
    items = [item for item in await couples_inbox_items(member) if not item["read"] and (nid is None or item["id"] == nid)]
    keys = [item["id"] for item in items if item.get("type") != "billing"]
    if keys:
        await db.users.update_one({"user_id": member["user_id"], "closing": {"$ne": True}},
                                  {"$addToSet": {"couples_reminders_seen": {"$each": keys}}})
    for item in items:
        if item.get("type") == "billing":
            await db.notifications.update_one({"user_id": member["user_id"], "id": item["id"], "type": "billing"},
                                              {"$set": {"read": True}})
    return {"ok": True}


def notification_for_member(doc):
    kind = doc["type"]
    if kind not in QUIET:
        return doc
    title, body = QUIET[kind]
    link = doc.get("link", "")
    key = doc.get("key") or ""
    if kind == "checkin" and key.startswith("checkin:"):
        for suffix, parameter in (("?tab=history", "exchange"), ("?tab=prepare", "plan")):
            if link.startswith("/app/person/") and link.endswith(suffix):
                link += "&" + parameter + "=" + quote(key[len("checkin:"):], safe="")
    return {**doc, "title": title, "body": body, "link": link}


@api.get("/notifications")
async def list_notifications(member: dict = Depends(current_member),
                             after_time: str = Query("", max_length=50), after_id: str = Query("", max_length=100)):
    uid = member["user_id"]
    if bool(after_time) != bool(after_id):
        raise HTTPException(422, "Include the notification time and ID to continue.")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    soon = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    async for e in db.exchanges.find({"user_id": uid, "choice.at": {"$lte": soon}, "outcome": {"$in": [None, ""]}}, {"_id": 0, "id": 1, "person_id": 1}):
        await notify(uid, "checkin", *QUIET["checkin"], f"/app/person/{e['person_id']}?tab=history", f"checkin:{e['id']}")
    async for x in db.preps.find({"user_id": uid, "created_at": {"$lte": cutoff}, "outcome": {"$in": [None, ""]}}, {"_id": 0, "id": 1, "person_id": 1}):
        await notify(uid, "checkin", *QUIET["checkin"], f"/app/person/{x['person_id']}?tab=prepare", f"checkin:{x['id']}")
    query = {"user_id": uid}
    if after_id:
        query["$or"] = [{"created_at": {"$lt": after_time}}, {"created_at": after_time, "id": {"$lt": after_id}}]
    rows = await db.notifications.find(query, {"_id": 0}).sort([("created_at", -1), ("id", -1)]).limit(51).to_list(51)
    docs = rows[:50]
    docs = [notification_for_member(doc) for doc in docs]
    next_page = {"after_time": docs[-1]["created_at"], "after_id": docs[-1]["id"]} if len(rows) > 50 else None
    return {"items": docs, "unread": await db.notifications.count_documents({"user_id": uid, "read": False}), "next": next_page}


@api.get("/admin/activity")
async def admin_activity(_: dict = Depends(require_role("admin")), kind: Literal["all", "signup", "chat", "group", "practice", "couples", "your_type", "quick_advice"] = "all",
                         include_couples: bool = False):
    eligible = {**IN_ENV, "role": {"$ne": "admin"}, "email": {"$not": TESTER_RE}, "closing": {"$ne": True}}
    return await trends.live_activity(db, eligible, kind, include_couples)


@api.post("/notifications/read-all")
async def read_all(member: dict = Depends(current_member)):
    await db.notifications.update_many({"user_id": member["user_id"]}, {"$set": {"read": True}})
    return {"ok": True}


@api.post("/notifications/{nid}/read")
async def read_one(nid: str, member: dict = Depends(current_member)):
    await db.notifications.update_one({"id": nid, "user_id": member["user_id"]}, {"$set": {"read": True}})
    return {"ok": True}


@api.get("/me/partner-offer")
async def my_partner_offer(response: Response, member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    _, offer = await partner_offers.details(db, member)
    return offer


@api.get("/upgrade")
async def upgrade_info(member: dict = Depends(current_member)):
    s = await get_settings()
    _, offer = await partner_offers.details(db, member)
    plan = member.get("plan_offer", "standard") if member.get("tier") == "paid" else "standard"
    displayed = partner_offers.MONTHLY if offer["monthly_eligible"] else plan
    available = False
    prices = {}
    complimentary = complimentary_access(member)
    if not complimentary and ops.billing_enabled() and stripe.api_key:
        try:
            stripe_plan.checkout_mode()
            prices = await asyncio.to_thread(stripe_membership.catalog)
            if displayed in (partner_offers.MONTHLY, partner_offers.ANNUAL):
                available = bool(await asyncio.to_thread(partner_offers.price_for, displayed))
            else:
                available = bool(prices.get(membership_plans.member_plan(member)["id"]))
            offer["monthly_available"] = bool(available and offer["monthly_eligible"])
            if offer["annual_eligible"] or offer["annual_pending"]:
                offer["annual_available"] = bool(await asyncio.to_thread(partner_offers.price_for, partner_offers.ANNUAL))
        except (stripe.StripeError, ValueError):
            pass
    existing = bool(member.get("tier") == "paid" and member.get("stripe_subscription_id"))
    active = billing_tier(member) == "paid"
    current = membership_plans.member_plan(member)
    plans = []
    for p in membership_plans.public_plans():
        price = prices.get(p["id"])
        upgrade = (active and stripe_membership.can_upgrade(member) and p["messages"] > current["messages"]
                   and ((price or {}).get("metadata") or {}).get("upgrade_portal"))
        plans.append({**p, "available": bool(price), "upgrade_available": bool(upgrade)})
    acceptance = billing_acceptance.requested()
    acceptance_owner = billing_acceptance.intro_allowed(member)
    if acceptance:
        for p in plans:
            p["available"] = bool(p["available"] and acceptance_owner and p["id"] == "plus_100")
            p["upgrade_available"] = False
        offer = {**offer, "monthly_available": False, "annual_available": False}
    return {**s["upgrade"], "plan_offer": plan, "price_label": partner_offers.LABELS.get(displayed, stripe_plan.PRICE_LABEL), "partner_offer": offer,
            "plans": plans, "membership_plan_id": current["id"] if active else None,
            "upgrade_available": any(p["upgrade_available"] for p in plans),
            "topup": {key: membership_plans.TOPUP[key] for key in ("messages", "price_cents")},
            "topup_available": bool(not acceptance and active and member.get("stripe_customer_id") and prices.get("topup_100")
                                     and not (type(member.get("topup_balance")) is int and member["topup_balance"] < 0)),
            "topup_blocked_reason": ("A previous refund adjusted already-used refill credits. Contact support before buying another refill."
                                     if type(member.get("topup_balance")) is int and member["topup_balance"] < 0 else None),
            "checkout_available": not acceptance and available and not existing and not complimentary and not intro_card.required(member),
            "intro_billing_required": intro_card.required(member),
            "complimentary_access": complimentary,
            "existing_subscription": existing,
            "billing_enabled": ops.billing_enabled() and (not acceptance or acceptance_owner),
            "portal_available": bool(stripe.api_key and member.get("stripe_customer_id")),
            "people_limit": s["people_limit"], "tier": billing_tier(member),
            "subscription_status": member.get("subscription_status"), "usage": await plan_usage(member)}


# ---------------- payments (Stripe) ----------------
PLUS_KEY = stripe_plan.LOOKUP_KEY
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")


class BillingRequest(BaseModel):
    expected_member: str | None = Field(None, max_length=256)
    purchase_policy_accepted: StrictBool = False
    purchase_policy_version: str | None = Field(None, max_length=80)

    def check_member(self, member):
        if self.expected_member is not None and self.expected_member != member["user_id"]:
            raise HTTPException(409, "The signed-in account changed. Reopen Plans before continuing.")

    def check_purchase(self):
        if self.purchase_policy_accepted is not True or self.purchase_policy_version != purchase_policy.VERSION:
            raise HTTPException(422, "Review and accept the current Terms and Billing policy in Plans before purchasing.")


class CheckoutIn(BillingRequest):
    origin_url: str = Field(min_length=8, max_length=300)
    offer: Literal["standard", "plus_100", "plus_250", "plus_500", "partner_monthly"] = "standard"


class UpgradeIn(BillingRequest):
    origin_url: str = Field(min_length=8, max_length=300)
    offer: Literal["plus_250", "plus_500"]


class RefillIn(BillingRequest):
    origin_url: str = Field(min_length=8, max_length=300)


class AnnualAcceptance(BillingRequest):
    accept: Literal[True]
    expected_starts_at: str | None = Field(None, max_length=40)


@api.post("/payments/partner-annual")
async def accept_partner_year(body: AnnualAcceptance, member: dict = Depends(current_member)):
    body.check_member(member)
    billing_acceptance.forbid_ordinary_purchase()
    billing_risk.require_action(member, "new_purchase")
    if complimentary_access(member):
        raise HTTPException(409, "Your complimentary unlimited access does not require a paid offer.")
    if not ops.billing_enabled() or not stripe.api_key:
        raise HTTPException(503, "Plus billing is not open yet.")
    body.check_purchase()
    try:
        return await partner_offers.accept(db, member, expected_starts_at=body.expected_starts_at,
            purchase_acknowledgment=purchase_policy.snapshot(now(), partner_offers.ANNUAL))
    except (stripe.StripeError, ValueError):
        raise HTTPException(503, "Stripe has not confirmed your one-year offer. Retry to check its status.") from None


@api.post("/payments/cancel-partner-year")
async def cancel_partner_year(body: AnnualAcceptance, member: dict = Depends(current_member)):
    body.check_member(member)
    # Existing commitments can be cancelled even if the sales gate is later closed.
    if not stripe.api_key:
        raise HTTPException(503, "Billing management is temporarily unavailable.")
    try:
        return await partner_offers.cancel_before_charge(db, member)
    except stripe.StripeError:
        raise HTTPException(503, "Stripe has not confirmed cancellation yet. Retry to check its status.") from None


async def billing_event(kind: str, identifier: str, uid: str, data: dict, occurred_at: str):
    await db.events.update_one({"_id": f"stripe:{kind}:{identifier}"},
                               {"$setOnInsert": {"user_id": uid, "type": kind, "data": data, "ts": occurred_at}}, upsert=True)


async def record_billing_transaction(reference: dict, transaction: dict):
    # Earlier transactions can have ObjectId keys. Match their Stripe reference on replay.
    try:
        await db.payment_transactions.update_one(reference, {"$setOnInsert": {**transaction, "env": ENV}}, upsert=True)
    except DuplicateKeyError:
        # Two first deliveries can both attempt the same deterministic _id insert.
        winner = {**reference, "_id": transaction["_id"], "user_id": transaction["user_id"], "kind": transaction["kind"]}
        if not await db.payment_transactions.find_one(winner, {"_id": 1}):
            raise


async def hydrate_subscription_period(member: dict):
    sub_id = member.get("stripe_subscription_id")
    if not stripe.api_key or not sub_id:
        return member
    try:
        if member.get("plan_offer") in (partner_offers.MONTHLY, partner_offers.ANNUAL):
            receipt = await asyncio.to_thread(partner_offers.recover_period, member)
        elif member.get("plan_offer") in ("plus_250", "plus_500"):
            price = await asyncio.to_thread(partner_offers.price_for, member["plan_offer"])
            charge = await asyncio.to_thread(stripe_plan.subscription_charge, sub_id, price) if price else None
            receipt = charge[:3] if charge else None
        else:
            receipt = await asyncio.to_thread(stripe_plan.subscription_receipt, sub_id)
    except (stripe.StripeError, ValueError):
        return member
    if not receipt:
        return member
    price_id, (start, end), customer = receipt
    changes = {"subscription_period_start": start, "subscription_period_end": end, "stripe_price_id": price_id,
               "stripe_customer_id": customer}
    updated = await db.users.find_one_and_update(
        {"user_id": member["user_id"], "tier": "paid", "stripe_subscription_id": sub_id,
         "$or": [{"subscription_period_start": None}, {"subscription_period_end": None},
                 {"stripe_price_id": None, "subscription_period_start": {"$lte": start}}]},
        {"$set": changes}, projection={"_id": 0}, return_document=ReturnDocument.AFTER)
    return updated or (await db.users.find_one({"user_id": member["user_id"]}, {"_id": 0})) or member


async def mark_paid(tx: dict, session: dict):
    if tx.get("kind") == "intro_setup":
        # A zero-dollar Managed Checkout reports paid. Only the separately
        # verified positive conversion invoice may become a paid purchase.
        return
    if tx.get("kind") == "topup":
        await stripe_refills.settle(db, tx, session, now(), env=ENV)
        return
    sub_id = stripe_plan.resource_id(session.get("subscription"))
    if not sub_id or tx.get("payment_status") == "paid":
        return
    price_id = tx.get("stripe_price_id")
    offer = tx.get("plan_offer", "standard")
    if offer != "standard" or tx.get("lookup_key") != PLUS_KEY:
        canonical = await asyncio.to_thread(partner_offers.price_for, offer)
        if not canonical or (price_id and price_id != canonical.id):
            return
        price_id = canonical.id
    invoice = session.get("invoice")
    if isinstance(invoice, str):
        invoice = await asyncio.to_thread(stripe.Invoice.retrieve, invoice, expand=["payments"])
    verified_intro = (tx.get("intro_conversion_id") and tx.get("intro_conversion_verified_invoice") == (invoice or {}).get("id"))
    if offer == partner_offers.MONTHLY and not verified_intro and (not invoice or invoice.get("billing_reason") != "subscription_create"):
        invoice = await asyncio.to_thread(partner_offers.first_paid_invoice, sub_id)
    charge = await asyncio.to_thread(stripe_plan.paid_plan_charge, invoice or {}, sub_id, price_id)
    period, revenue_cents = charge if charge else (None, None)
    paid_at = stripe_plan.invoice_paid_at(invoice)
    customer = stripe_plan.resource_id(session.get("customer"))
    if not period and (offer == "standard" or offer in membership_plans.PLANS) and (
            not invoice or (not price_id and tx.get("lookup_key") == "plus_monthly")):
        # Some Checkout events only link the subscription. Always resolve the price selected at checkout.
        if offer in ("plus_250", "plus_500"):
            receipt = await asyncio.to_thread(stripe_plan.subscription_charge, sub_id, canonical)
        else:
            receipt = await asyncio.to_thread(stripe_plan.subscription_charge, sub_id)
        if receipt and (not price_id or receipt[0] == price_id):
            price_id, period, customer, revenue_cents, paid_at = receipt
    if not period:
        raise HTTPException(503, "Awaiting the paid invoice for this subscription")
    start, end = period
    if "_id" not in tx:
        tx = await db.payment_transactions.find_one({"session_id": tx["session_id"], "user_id": tx["user_id"]})
    if not invoice or not invoice.get("id"):
        await billing_adjustments.review_required(db, tx, "legacy_invoice_basis_missing", env=ENV)
        raise HTTPException(503, "The historical invoice needs billing reconciliation before activation.")
    await billing_adjustments.prepare_subscription(db, tx, invoice, session, sub_id, price_id, period,
        membership_plans.PLANS.get(offer, membership_plans.BASE_PLAN)["messages"], revenue_cents, env=ENV)
    activated = await db.users.update_one(
        {"user_id": tx["user_id"], "cancelled_stripe_subscriptions": {"$ne": sub_id},
         "$and": [{"$or": [{"stripe_purchase_created_at": None}, {"stripe_purchase_created_at": {"$lte": tx["created_at"]}}]},
                  {"$or": [{"stripe_subscription_id": {"$ne": sub_id}}, {"subscription_period_start": None},
                           {"subscription_period_start": {"$lt": start}},
                           {"subscription_period_start": start, "stripe_price_id": {"$in": [None, price_id]}}]}]},
        {"$set": {"tier": "paid", "subscription_status": "active", "stripe_subscription_id": sub_id,
                  "stripe_price_id": price_id, "stripe_customer_id": customer, "plan_offer": offer,
                  "membership_plan_id": offer if offer in membership_plans.PLANS else "plus_100",
                  "subscription_period_start": start, "subscription_period_end": end,
                  "stripe_purchase_created_at": tx["created_at"]},
         "$min": {"intro_converted_at": now()}})
    if offer == partner_offers.MONTHLY and activated.matched_count and tx.get("pair_id"):
        if not paid_at:
            raise HTTPException(503, "Awaiting Stripe's confirmed payment time for the partner offer.")
        if (((invoice or {}).get("billing_reason") == "subscription_create" or verified_intro)
                and await asyncio.to_thread(partner_offers.fully_paid_offer, invoice, price_id, 1000)):
            await partner_offers.unlock(db, tx, sub_id, paid_at, notify)
    paid_at = paid_at or now()
    await billing_event("purchase", tx["session_id"], tx["user_id"], {"amount": tx["amount"], "currency": tx["currency"]}, paid_at)
    if tx.get("checkout_attempt_id"):
        await db.users.update_one({"user_id": tx["user_id"], "checkout_attempt.id": tx["checkout_attempt_id"]},
                                  {"$unset": {"checkout_attempt": ""}})
    await db.payment_transactions.update_one({"session_id": tx["session_id"]},
        {"$set": {"status": "completed", "payment_status": "paid", "stripe_subscription_id": sub_id,
                  "payment_verified": True, "credit_delivery_status": "fulfilled",
                  "env": ENV, "subscription_revenue_cents": revenue_cents,
                  "balance_source_ids": membership_admin.invoice_refs(invoice, session),
                  "stripe_price_id": price_id,
                  "fulfilled_at": paid_at, "updated_at": now()}})


def checkout_origin(origin: str, request: Request) -> str:
    value = origin.rstrip("/")
    allowed = {o.strip().rstrip("/") for o in os.environ.get("CORS_ORIGINS", "").split(",")}
    for setting in ("PUBLIC_APP_URL", "RENDER_EXTERNAL_URL"):
        if os.environ.get(setting):
            allowed.add(os.environ[setting].rstrip("/"))
    if request.base_url.hostname in ("localhost", "127.0.0.1", "::1"):
        allowed.add(str(request.base_url).rstrip("/"))
    parsed = urlsplit(value)
    if (value not in allowed or parsed.scheme not in ("http", "https") or not parsed.netloc or
            parsed.username is not None or parsed.path or parsed.query or parsed.fragment):
        raise HTTPException(status_code=422, detail="Unknown checkout return origin")
    return value


def make_checkout_session(kw, mode, attempt_id):
    billing_acceptance.forbid_ordinary_purchase()
    if mode in ("auto", "managed"):
        try:
            # Managed Payments controls hosted wording and rejects custom_text.
            # The authenticated app acknowledgment is retained separately.
            managed_kw = {key: value for key, value in kw.items() if key != "custom_text"}
            return stripe.checkout.Session.create(**managed_kw, managed_payments={"enabled": True}, idempotency_key=attempt_id + ":managed")
        except stripe.InvalidRequestError as e:
            msg = (e.user_message or str(e)).lower()
            if mode == "managed" or not any(term in msg for term in ("managed", "ineligible", "unknown parameter")):
                raise
    try:
        return stripe.checkout.Session.create(**kw, automatic_tax={"enabled": True}, billing_address_collection="required",
                                              idempotency_key=attempt_id + ":tax")
    except stripe.InvalidRequestError as e:
        message = (e.user_message or str(e)).lower()
        if not (mode == "auto" and stripe.api_key.startswith(("sk_test_", "rk_test_")) and "head office address" in message
                and "test mode" in message):
            raise
        # A fresh sandbox can exercise subscriptions before Stripe Tax has a business address.
        return stripe.checkout.Session.create(**kw, idempotency_key=attempt_id + ":sandbox")


def complimentary_snapshot(member):
    """A grant and a payable checkout must not win the same account update."""
    if "complimentary_entitlement" not in member:
        return {"$expr": {"$eq": [{"$type": "$complimentary_entitlement"}, "missing"]}}
    return {"$expr": {"$eq": ["$complimentary_entitlement", {"$literal": member["complimentary_entitlement"]}]}}


@api.post("/payments/checkout")
async def create_checkout(body: CheckoutIn, request: Request, member: dict = Depends(current_member)):
    body.check_member(member)
    billing_acceptance.forbid_ordinary_purchase()
    billing_risk.require_action(member, "new_purchase")
    if intro_card.required(member):
        raise HTTPException(409, "Choose a plan and complete card-first introductory setup in Plans.")
    if complimentary_access(member):
        raise HTTPException(409, "Your complimentary unlimited access does not require checkout.")
    if not ops.billing_enabled():
        raise HTTPException(503, "Plus billing is not open yet.")
    if member.get("tier") == "paid" and member.get("stripe_subscription_id"):
        raise HTTPException(status_code=409, detail="Manage your existing subscription in the billing portal.")
    if not stripe.api_key:
        raise HTTPException(status_code=503, detail="Payments are not configured")
    body.check_purchase()
    origin = checkout_origin(body.origin_url, request)
    selected = "standard" if body.offer == "plus_100" else body.offer
    pair = None
    if body.offer == partner_offers.MONTHLY:
        pair, eligibility = await partner_offers.details(db, member)
        if not eligibility["monthly_eligible"]:
            raise HTTPException(409, "The partner price requires your original confirmed sponsored couple.")
    lookup_key = partner_offers.lookup(selected)
    try:
        mode = stripe_plan.checkout_mode()
        price = await asyncio.to_thread(partner_offers.price_for, selected)
    except (stripe.StripeError, ValueError):
        raise HTTPException(status_code=503, detail="Unable to load the payment plan")
    if not price:
        raise HTTPException(status_code=503, detail="The monthly plan is not configured yet")
    # Repeated submissions must create one payable Stripe session per member.
    attempt = {"id": new_id(), "origin": origin, "price": price.id, "email": member.get("email"), "mode": mode,
               "offer": selected, "pair_id": pair["_id"] if pair else None,
               "amount": (price.unit_amount or 0) / 100.0, "currency": price.currency,
               "explicit_expiry": True, "expires": (datetime.now(timezone.utc) + timedelta(hours=23)).isoformat(),
               "purchase_policy": purchase_policy.snapshot(now(), selected)}
    user_query = billing_risk.guard_query({"user_id": member["user_id"], "closing": {"$ne": True}, **complimentary_snapshot(member)}, member, "new_purchase")
    # Some old accounts were manually tagged paid and have never had a Stripe subscription.
    eligible = {"$or": [{"tier": {"$ne": "paid"}}, {"stripe_subscription_id": {"$in": [None, ""]}}]}
    await db.users.update_one({**user_query, "$and": [eligible, {"$or": [{"checkout_attempt": None},
                              {"checkout_attempt.expires": {"$lte": now()}}]}]}, {"$set": {"checkout_attempt": attempt}})
    user = await db.users.find_one({**user_query, **eligible}, {"checkout_attempt": 1})
    if not user or "checkout_attempt" not in user:
        raise HTTPException(status_code=409, detail="Checkout is no longer available for this account")
    attempt = user["checkout_attempt"]
    if attempt["price"] != price.id or attempt.get("pair_id") != (pair["_id"] if pair else None):
        old = await db.payment_transactions.find_one({"user_id": member["user_id"], "kind": "purchase",
                                                     "checkout_attempt_id": attempt["id"], "payment_status": "pending"})
        if not old:
            raise HTTPException(409, "Your earlier checkout is being prepared. Try again shortly.")
        try:
            previous = await asyncio.to_thread(stripe.checkout.Session.retrieve, old["session_id"])
            if previous.get("status") == "open":
                previous = await asyncio.to_thread(stripe.checkout.Session.expire, old["session_id"])
        except stripe.StripeError:
            raise HTTPException(409, "Refresh Plans to check the earlier checkout.") from None
        if previous.get("status") != "expired":
            raise HTTPException(409, "Your earlier checkout has already been submitted. Refresh Plans to check payment.")
        await close_checkout(old["session_id"], "expired")
        # Reserve the requested price with a fresh key after the old payable session is gone.
        return await create_checkout(body, request, member)
    # Older attempts started with the managed-first checkout. A different mode could create a second payable session.
    if attempt.get("mode", "auto") != mode:
        raise HTTPException(status_code=409, detail="An earlier checkout is still open. Try again after it expires.")
    o = attempt["origin"]
    kw = dict(line_items=[{"price": attempt["price"], "quantity": 1}], mode="subscription", customer_email=attempt.get("email") or None,
              success_url=f"{o}/app/upgrade?session_id={{CHECKOUT_SESSION_ID}}", cancel_url=f"{o}/app/upgrade?cancelled=1",
              metadata={"user_id": member["user_id"], "lookup_key": lookup_key},
              subscription_data={"metadata": {"user_id": member["user_id"], "lookup_key": lookup_key}})
    if attempt.get("explicit_expiry") or mode != "auto":
        # Stay inside Stripe’s 24-hour maximum with clock skew. Preserve legacy auto retries' parameters.
        kw["expires_at"] = int(datetime.fromisoformat(attempt["expires"]).timestamp())
    if attempt.get("purchase_policy"):
        kw["custom_text"] = purchase_policy.checkout_text()

    try:
        session = await asyncio.to_thread(make_checkout_session, kw, mode, attempt["id"])
    except stripe.StripeError:
        raise HTTPException(status_code=503, detail="Unable to start checkout right now")
    await record_billing_transaction({"session_id": session.id}, {
        "_id": "checkout:" + session.id, "user_id": member["user_id"], "checkout_attempt_id": attempt["id"],
        "lookup_key": lookup_key, "stripe_price_id": price.id, "plan_offer": selected, "pair_id": attempt.get("pair_id"),
        "amount": attempt["amount"], "currency": attempt["currency"], "kind": "purchase",
        **({"purchase_policy": attempt["purchase_policy"]} if attempt.get("purchase_policy") else {}),
        "status": "initiated", "payment_status": "pending", "created_at": now(), "updated_at": now()})
    if pair:
        registration = {"user_id": member["user_id"], "env": ENV, "pair_id": pair["_id"]}
        if not await partner_checkout.eligible(db, registration, member_info, partner_offers.pair_context):
            # Pair closure may have finished while Stripe was creating this session.
            await partner_checkout.expire_pair(db, pair["_id"], ENV)
            raise HTTPException(409, "This partner invitation is no longer eligible for checkout.")
    return {"checkout_url": session.url, "session_id": session.id}


@api.post("/payments/upgrade")
async def upgrade_subscription(body: UpgradeIn, request: Request, member: dict = Depends(current_member)):
    body.check_member(member)
    billing_acceptance.forbid_ordinary_purchase()
    billing_risk.require_action(member, "new_purchase")
    if complimentary_access(member):
        raise HTTPException(409, "Your complimentary unlimited access does not require an upgrade.")
    if not ops.billing_enabled() or not stripe.api_key:
        raise HTTPException(503, "Plan changes are temporarily unavailable.")
    body.check_purchase()
    origin = checkout_origin(body.origin_url, request)
    current, target = membership_plans.member_plan(member), membership_plans.PLANS[body.offer]
    if (billing_tier(member) != "paid" or not stripe_membership.can_upgrade(member)
            or target["messages"] <= current["messages"]):
        raise HTTPException(409, "This plan change is unavailable for your current membership.")
    try:
        session, price_id = await asyncio.to_thread(stripe_membership.upgrade_session, member, body.offer, origin)
    except (ValueError, stripe.InvalidRequestError):
        raise HTTPException(409, "Your subscription or plan options changed. Refresh Plans and review your options.") from None
    except stripe.StripeError:
        raise HTTPException(503, "Stripe could not open the upgrade confirmation. Try again.") from None
    recorded = await db.users.update_one(billing_risk.guard_query({"user_id": member["user_id"], "stripe_subscription_id": member["stripe_subscription_id"],
        "stripe_price_id": member["stripe_price_id"], "tier": "paid", "closing": {"$ne": True},
        "annual_agreement": member.get("annual_agreement")}, member, "new_purchase"),
        {"$set": {f"plan_upgrade_authorizations.{body.offer}": {"subscription": member["stripe_subscription_id"],
                                  "price_id": price_id, "source_price": member["stripe_price_id"],
                                  "purchase_policy": purchase_policy.snapshot(now(), body.offer)}}})
    if not recorded.matched_count:
        raise HTTPException(409, "Your membership changed. Refresh Plans before continuing.")
    return {"checkout_url": session.url}


@api.post("/payments/topup")
async def purchase_refill(body: RefillIn, request: Request, member: dict = Depends(current_member)):
    body.check_member(member)
    billing_acceptance.forbid_ordinary_purchase()
    billing_risk.require_action(member, "new_purchase")
    if complimentary_access(member):
        raise HTTPException(409, "Your complimentary unlimited access does not require message refills.")
    if not ops.billing_enabled() or not stripe.api_key:
        raise HTTPException(503, "Message refills are temporarily unavailable.")
    if billing_tier(member) != "paid" or not member.get("stripe_customer_id"):
        raise HTTPException(402, "An active paid plan is required for a message refill.")
    body.check_purchase()
    origin = checkout_origin(body.origin_url, request)
    uid = member["user_id"]
    supplied = request.headers.get("Idempotency-Key") or new_id()
    if not 1 <= len(supplied.strip()) <= 200:
        raise HTTPException(422, "Invalid request key.")
    request_key = hashlib.sha256((uid + ":" + supplied).encode()).hexdigest()
    try:
        price = (await asyncio.to_thread(stripe_membership.catalog))["topup_100"]
        mode = stripe_plan.checkout_mode()
    except (ValueError, stripe.StripeError):
        raise HTTPException(503, "The refill price is temporarily unavailable.") from None
    if not price:
        raise HTTPException(503, "The refill price is temporarily unavailable.")
    spec = membership_plans.TOPUP
    existing = await db.payment_transactions.find_one({"kind": "topup", "user_id": uid, "topup_request_key": request_key})
    if existing:
        if existing.get("payment_status") == "paid":
            return {"checkout_url": origin + "/app/upgrade?session_id=" + existing["session_id"], "session_id": existing["session_id"]}
        if existing.get("payment_status") != "pending":
            raise HTTPException(410, "This refill checkout expired. Start a new refill.")
    actual_balance = await db.users.find_one({"user_id": uid}, {"topup_balance": 1})
    if type((actual_balance or {}).get("topup_balance")) is int and actual_balance["topup_balance"] < 0:
        raise HTTPException(409, "A previous refund adjusted already-used refill credits. Contact support before buying another refill.")
    expires = datetime.now(timezone.utc) + timedelta(hours=23)
    attempt = {"id": new_id(), "origin": origin, "price": price.id, "request_key": request_key,
               "customer": member["stripe_customer_id"], "mode": mode, "expires": expires.isoformat(),
               "purchase_policy": purchase_policy.snapshot(now(), "topup_100")}
    scope = billing_risk.guard_query({"user_id": uid, "closing": {"$ne": True}, "tier": "paid", "stripe_subscription_id": member["stripe_subscription_id"],
             "$or": [{"topup_balance": {"$exists": False}}, {"topup_balance": {"$gte": 0}}],
             **complimentary_snapshot(member)}, member, "new_purchase")
    await db.users.update_one({**scope, "$and": [{"$or": scope["$or"]}, {"$or": [{"topup_checkout_attempt": None}, {"topup_checkout_attempt.expires": {"$lte": now()}}]}]},
                              {"$set": {"topup_checkout_attempt": attempt}})
    owner = await db.users.find_one(scope, {"topup_checkout_attempt": 1})
    if not owner or not owner.get("topup_checkout_attempt"):
        raise HTTPException(409, "Your membership changed. Refresh Plans.")
    attempt = owner["topup_checkout_attempt"]
    if existing and existing.get("checkout_attempt_id") != attempt["id"]:
        raise HTTPException(410, "This refill checkout expired. Start a new refill.")
    pending = await db.payment_transactions.find_one({"user_id": uid, "kind": "topup", "checkout_attempt_id": attempt["id"]})
    if pending:
        try:
            session = await asyncio.to_thread(stripe.checkout.Session.retrieve, pending["session_id"])
        except stripe.StripeError:
            raise HTTPException(503, "Stripe could not confirm your open refill checkout. Try again.") from None
        if session.get("status") == "expired":
            await close_checkout(pending["session_id"], "expired")
            raise HTTPException(410, "This refill checkout expired. Start a new refill.")
        return {"checkout_url": session.url if session.get("status") == "open" else origin + "/app/upgrade?session_id=" + session.id,
                "session_id": session.id}
    meta = {"user_id": uid, "lookup_key": spec["lookup_key"], "topup_attempt": attempt["id"]}
    kw = dict(mode="payment", customer=attempt["customer"], line_items=[{"price": attempt["price"], "quantity": 1}],
              success_url=attempt["origin"] + "/app/upgrade?session_id={CHECKOUT_SESSION_ID}",
              cancel_url=attempt["origin"] + "/app/upgrade?cancelled=1", metadata=meta, payment_intent_data={"metadata": meta},
              expires_at=int(datetime.fromisoformat(attempt["expires"]).timestamp()))
    if attempt.get("purchase_policy"):
        kw["custom_text"] = purchase_policy.checkout_text()
    if attempt["mode"] != "managed":
        kw["customer_update"] = {"address": "auto"}
    try:
        session = await asyncio.to_thread(make_checkout_session, kw, attempt["mode"], attempt["id"])
    except stripe.StripeError:
        raise HTTPException(503, "Stripe could not open the refill checkout. Try again.") from None
    await record_billing_transaction({"session_id": session.id}, {"_id": "checkout:" + session.id, "session_id": session.id,
        "kind": "topup", "user_id": uid, "checkout_attempt_id": attempt["id"], "topup_request_key": attempt["request_key"],
        "stripe_customer_id": attempt["customer"], "stripe_price_id": attempt["price"], "lookup_key": spec["lookup_key"],
        "amount": spec["price_cents"] / 100, "currency": "usd", "payment_status": "pending", "status": "initiated",
        **({"purchase_policy": attempt["purchase_policy"]} if attempt.get("purchase_policy") else {}),
        "created_at": now(), "updated_at": now()})
    return {"checkout_url": session.url, "session_id": session.id}


@api.post("/payments/portal")
async def billing_portal(body: CheckoutIn, request: Request, member: dict = Depends(current_member)):
    body.check_member(member)
    # Closing new sales must not prevent existing customers from cancelling.
    origin = checkout_origin(body.origin_url, request)
    if not stripe.api_key or not member.get("stripe_customer_id"):
        raise HTTPException(503, "Billing management is unavailable")
    options = {}
    if config_id := os.environ.get("STRIPE_PORTAL_CONFIGURATION"):
        options["configuration"] = config_id
    try:
        if stripe.api_key.startswith(("sk_live_", "rk_live_")):
            if not config_id:
                raise HTTPException(503, "Billing management is awaiting its portal configuration.")
            configuration = await asyncio.to_thread(stripe.billing_portal.Configuration.retrieve, config_id)
            if ((configuration.get("features") or {}).get("subscription_update") or {}).get("enabled"):
                raise HTTPException(503, "Billing management is awaiting its invite-only price settings.")
        session = await asyncio.to_thread(stripe.billing_portal.Session.create,
            customer=member["stripe_customer_id"], return_url=f"{origin}/app/upgrade", **options)
    except stripe.StripeError:
        raise HTTPException(503, "Billing management is unavailable")
    return {"portal_url": session.url}


async def close_checkout(session_id: str, status: str):
    tx = await db.payment_transactions.find_one_and_update({"session_id": session_id, "payment_status": {"$in": ["pending", status]}},
        {"$set": {"status": status, "payment_status": status, "updated_at": now()}})
    if tx and tx.get("checkout_attempt_id"):
        field = "topup_checkout_attempt" if tx.get("kind") == "topup" else "checkout_attempt"
        await db.users.update_one({"user_id": tx["user_id"], field + ".id": tx["checkout_attempt_id"]},
                                  {"$unset": {field: ""}})


@api.get("/payments/status/{session_id}")
async def payment_status(session_id: str, member: dict = Depends(current_member)):
    query = {"session_id": session_id, "user_id": member["user_id"]}
    rec = await db.payment_transactions.find_one(query, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if rec.get("payment_status") == "pending" and stripe.api_key:
        try:
            s = await asyncio.to_thread(stripe.checkout.Session.retrieve, session_id)
            if s.payment_status == "paid":
                await mark_paid(rec, s)
            elif s.status == "expired":
                await close_checkout(session_id, "expired")
            rec = await db.payment_transactions.find_one(query, {"_id": 0})
        except stripe.StripeError:
            pass
        except HTTPException:
            current = await db.payment_transactions.find_one(query, {"_id": 0})
            if not current or current.get("credit_delivery_status") != "review_required":
                raise
            rec = current
    user = await db.users.find_one({"user_id": member["user_id"]},
        {field: 1 for field in (*BILLING_FIELDS, "role", "closing", "complimentary_entitlement")})
    return {"session_id": rec["session_id"], "status": rec["status"], "payment_status": rec["payment_status"], "kind": rec["kind"],
            "tier": billing_tier(user or {}), "payment_verified": rec.get("payment_verified") is True,
            "credit_delivery_status": rec.get("credit_delivery_status") or ("fulfilled" if rec.get("fulfilled_at") else "pending"),
            "review_required": rec.get("credit_delivery_status") == "review_required"}


@api.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="Payment webhook is not configured")
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload, request.headers.get("stripe-signature", ""), secret)
    except (stripe.SignatureVerificationError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid signature")
    obj, t = event["data"]["object"], event["type"]
    metadata = obj.get("metadata") or {}
    details = (obj.get("parent") or {}).get("subscription_details") or obj.get("subscription_details") or {}
    uid = metadata.get("user_id") or (details.get("metadata") or {}).get("user_id")
    if not uid and t.startswith("checkout.session."):
        tx = await db.payment_transactions.find_one({"session_id": obj["id"]}, {"user_id": 1})
        uid = tx and tx.get("user_id")
    if not uid and t.startswith("invoice."):
        sub_id = stripe_plan.invoice_subscription(obj)
        if sub_id:
            row = await db.users.find_one({"stripe_subscription_id": sub_id}, {"user_id": 1})
            if not row:
                row = await db.payment_transactions.find_one(
                    {"kind": "purchase", "payment_status": "paid", "stripe_subscription_id": sub_id}, {"user_id": 1})
            uid = row and row.get("user_id")
    if not uid and t.startswith(("charge.", "refund.")):
        uid = await billing_adjustments.event_owner(db, obj)
    if not uid:
        return await handle_stripe_event(t, obj, event_created=event.get("created"))
    lease = await accounts.reserve_request(db, uid)
    if not lease:
        if await db.users.find_one({"user_id": uid, "closing": {"$ne": True}}, {"_id": 1}):
            raise HTTPException(503, "Payment update will retry after the in-progress account operation.")
        # Closure reconciles pending checkouts directly with Stripe before purging records.
        if t.startswith(("charge.", "refund.")):
            # A financial review can remain anonymous after account closure;
            # never resurrect content or silently call gross revenue net.
            return await handle_stripe_event(t, obj, event_created=event.get("created"))
        return {"status": "ok"}
    try:
        return await handle_stripe_event(t, obj, event_created=event.get("created"))
    finally:
        await db.users.update_one({"user_id": uid}, {"$unset": {"request_leases." + lease: ""}})


async def handle_stripe_event(t: str, obj: dict, *, event_created=None):
    if await intro_card.handle_event(db, t, obj, fulfill=mark_paid):
        return {"status": "ok"}
    if t in ("checkout.session.completed", "checkout.session.async_payment_succeeded") and obj.get("payment_status") == "paid":
        tx = await db.payment_transactions.find_one({"session_id": obj["id"]})
        if tx:
            await mark_paid(tx, obj)
        elif (obj.get("metadata") or {}).get("lookup_key") in (*stripe_membership.ALL_KEYS, partner_offers.lookup(partner_offers.MONTHLY)):
            raise HTTPException(status_code=503, detail="Checkout has not finished registering")
    elif t in ("invoice.paid", "invoice.payment_succeeded"):
        if obj.get("billing_reason") in ("subscription_cycle", "subscription_update"):
            await renew_subscription(obj)
        elif obj.get("billing_reason") == "subscription_create":
            details = (obj.get("parent") or {}).get("subscription_details") or obj.get("subscription_details") or {}
            metadata = details.get("metadata") or {}
            if metadata.get("user_id") and metadata.get("lookup_key") == partner_offers.lookup(partner_offers.MONTHLY):
                sub_id = stripe_plan.invoice_subscription(obj)
                tx = await db.payment_transactions.find_one({"user_id": metadata["user_id"], "plan_offer": partner_offers.MONTHLY,
                    "$or": [{"stripe_subscription_id": sub_id}, {"payment_status": "pending"}]}, sort=[("created_at", -1)])
                if not tx:
                    raise HTTPException(503, "Partner checkout has not finished registering.")
                if tx.get("payment_status") != "paid":
                    session = await asyncio.to_thread(stripe.checkout.Session.retrieve, tx["session_id"])
                    if stripe_plan.resource_id(session.get("subscription")) != sub_id or session.get("payment_status") != "paid":
                        raise HTTPException(503, "Awaiting confirmation of the matching partner checkout.")
                    await mark_paid(tx, {**dict(session), "invoice": obj})
    elif t in billing_adjustments.EVENTS:
        reversal = await billing_adjustments.handle_event(db, obj, env=ENV, source_event=t)
        if reversal.get("status") == "unlinked_payment_not_applied":
            return reversal
        lost = t == "charge.dispute.closed" and obj.get("status") == "lost"
        if reversal.get("kind") == "topup" and (lost or t == "charge.refunded") and type(event_created) is int and event_created > 0:
            # Duplicate notifications for the same cumulative refund are one reportable change.
            key = "refill_dispute_lost" if lost else "refill_refunded"
            reference = (reversal["payment_intent_id"] + ":" + str(obj.get("id")) if lost else
                         reversal["payment_intent_id"] + ":" + str(reversal["refunded_cents"]))
            stamp = datetime.fromtimestamp(event_created, timezone.utc).isoformat()
            await db.events.update_one(
                {"_id": f"stripe:{key}:{reference}"},
                {"$setOnInsert": {"type": key, "user_id": reversal["user_id"], "data": {}}, "$min": {"ts": stamp}},
                upsert=True)
    elif t == "customer.subscription.updated" and obj.get("id"):
        await sync_subscription_status(obj["id"])
    elif t == "customer.subscription.deleted" and obj.get("id"):
        sub = obj["id"]
        meta = obj.get("metadata") or {}
        if meta.get("user_id") and meta.get("lookup_key") in (*stripe_membership.ALL_KEYS, "plus_monthly", partner_offers.lookup(partner_offers.MONTHLY)):
            await db.users.update_one({"user_id": meta["user_id"]}, {"$addToSet": {"cancelled_stripe_subscriptions": sub}})
        await db.users.update_one({"stripe_subscription_id": sub}, {"$addToSet": {"cancelled_stripe_subscriptions": sub},
                                  "$set": {"tier": "unsubscribed", "subscription_status": "cancelled"}})
    elif t in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        await close_checkout(obj["id"], "expired" if t == "checkout.session.expired" else "failed")
    return {"status": "ok"}


async def sync_subscription_status(sub_id: str):
    # Stripe can deliver webhooks out of order. Read the current subscription, and
    # update only the local snapshot we inspected. Paid invoices alone grant a period.
    owner = await db.users.find_one({**IN_ENV, "stripe_subscription_id": sub_id, "tier": "paid", "closing": {"$ne": True}},
                                    {"subscription_status": 1, "subscription_period_start": 1, "subscription_period_end": 1})
    if not owner:
        return
    subscription = await asyncio.to_thread(stripe.Subscription.retrieve, sub_id)
    state = subscription.get("status")
    if state not in ("active", "past_due", "unpaid") or subscription.get("id") != sub_id:
        return
    status = "active" if state == "active" else "past_due"
    if owner.get("subscription_status") == status:
        return
    current = {key: owner.get(key) for key in ("subscription_status", "subscription_period_start", "subscription_period_end")}
    saved = await db.users.update_one({"_id": owner["_id"], **current, "stripe_subscription_id": sub_id,
        "tier": "paid", "closing": {"$ne": True}}, {"$set": {"subscription_status": status}})
    if not saved.matched_count:
        raise HTTPException(503, "Subscription changed during reconciliation. Retry the update.")


async def renew_subscription(invoice: dict):
    # A duplicate paid invoice cannot create another grant or depend on a fresh
    # provider read. Refund/dispute events have their own reconciliation path.
    if await db.payment_transactions.find_one({"invoice_id": invoice.get("id"), "payment_status": "paid"}, {"_id": 1}):
        return
    sub_id = stripe_plan.invoice_subscription(invoice)
    if not sub_id:
        return
    owner = await db.users.find_one({"stripe_subscription_id": sub_id, "closing": {"$ne": True}},
                                    {"user_id": 1, "stripe_price_id": 1, "plan_offer": 1, "annual_agreement": 1,
                                     "plan_upgrade_authorizations": 1})
    if not owner:
        # A replaced subscription still owns its paid invoices while its member account exists.
        owner = await db.payment_transactions.find_one(
            {"kind": "purchase", "payment_status": "paid", "stripe_subscription_id": sub_id},
            {"user_id": 1, "stripe_price_id": 1, "plan_offer": 1})
        if owner:
            retained = await db.users.find_one({"user_id": owner["user_id"], "closing": {"$ne": True}}, {"annual_agreement": 1})
            if not retained:
                return
            owner["annual_agreement"] = retained.get("annual_agreement")
    if not owner:
        details = (invoice.get("parent") or {}).get("subscription_details") or invoice.get("subscription_details") or {}
        if (details.get("metadata") or {}).get("lookup_key") in (*stripe_membership.ALL_KEYS, partner_offers.lookup(partner_offers.MONTHLY)):
            raise HTTPException(503, "Subscription has not finished registering")
        return
    offer = owner.get("plan_offer", "standard")
    agreement = owner.get("annual_agreement") or {}
    # A manually assigned portal/dashboard price alone cannot confer the fixed-year offer.
    authorized_year = agreement.get("subscription") == sub_id and agreement.get("status") in ("pending", "scheduled")
    if authorized_year:
        charge = await asyncio.to_thread(stripe_plan.paid_plan_charge, invoice, sub_id, agreement["annual_price"])
        if (charge and charge[0] == (agreement["starts"], agreement["ends"])
                and await asyncio.to_thread(partner_offers.fully_paid_offer, invoice, agreement["annual_price"], 8000)):
            offer = partner_offers.ANNUAL
        elif offer == partner_offers.ANNUAL:
            # Only the explicitly purchased term may be fulfilled, including on webhook replays.
            return
    elif offer == partner_offers.ANNUAL:
        return
    authorized_price = None
    changed_plan = False
    source_price = None
    if not authorized_year and offer != partner_offers.ANNUAL:
        # Reading the verified full-period line makes invoice delivery order immaterial.
        # A proration line or an arbitrary price manually placed on a subscription cannot activate a new tier.
        for target, record in (owner.get("plan_upgrade_authorizations") or {}).items():
            if target in ("plus_250", "plus_500") and record.get("subscription") == sub_id:
                charge = await asyncio.to_thread(stripe_plan.paid_plan_charge, invoice, sub_id, record["price_id"])
                if charge:
                    offer, authorized_price, source_price = target, record["price_id"], record["source_price"]
                    changed_plan = invoice.get("billing_reason") == "subscription_update"
                    break
    if invoice.get("billing_reason") == "subscription_update" and offer != partner_offers.ANNUAL and not changed_plan:
        return
    canonical = await asyncio.to_thread(partner_offers.price_for, offer)
    expected_price = authorized_price or (agreement.get("annual_price") if authorized_year and offer == partner_offers.ANNUAL else owner.get("stripe_price_id"))
    if not canonical or (expected_price and expected_price != canonical.id):
        return
    charge = await asyncio.to_thread(stripe_plan.paid_plan_charge, invoice, sub_id, canonical.id)
    if not charge:
        return
    period, revenue_cents = charge
    start, end = period
    uid = owner["user_id"]
    eligible_periods = [{"subscription_period_start": None}, {"subscription_period_start": {"$lt": start}}]
    if changed_plan:
        eligible_periods.append({"subscription_period_start": start, "stripe_price_id": source_price})
    kind = "upgrade" if changed_plan else "renewal"
    if changed_plan:
        ex_tax = invoice.get("total_excluding_tax")
        if type(ex_tax) is int:
            revenue_cents = max(0, min(revenue_cents, ex_tax))
    amount = (invoice.get("amount_paid") or 0) / 100.0
    paid_at = stripe_plan.invoice_paid_at(invoice) or now()
    authorization = (owner.get("plan_upgrade_authorizations") or {}).get(offer) or {}
    policy = (authorization.get("purchase_policy") if authorization.get("subscription") == sub_id
              and authorization.get("price_id") == canonical.id else None)
    if offer == partner_offers.ANNUAL and agreement.get("subscription") == sub_id:
        policy = agreement.get("purchase_policy")
    if not policy:
        original = await db.payment_transactions.find_one({"kind": "purchase", "user_id": uid,
            "stripe_subscription_id": sub_id, "purchase_policy": {"$exists": True}}, {"purchase_policy": 1})
        policy = (original or {}).get("purchase_policy")
    await record_billing_transaction({"invoice_id": invoice["id"]}, {
        "_id": "invoice:" + invoice["id"], "user_id": uid, "stripe_subscription_id": sub_id, "kind": kind,
        "amount": amount, "subscription_revenue_cents": revenue_cents, "currency": invoice.get("currency"), "plan_offer": offer,
        "balance_source_ids": membership_admin.invoice_refs(invoice),
        "status": "awaiting_verification", "payment_status": "pending",
        **({"purchase_policy": policy} if isinstance(policy, dict) else {}),
        "created_at": paid_at, "updated_at": now()})
    tx = await db.payment_transactions.find_one({"invoice_id": invoice["id"], "user_id": uid})
    await billing_adjustments.prepare_subscription(db, tx, invoice, {}, sub_id, canonical.id, period,
        membership_plans.PLANS.get(offer, membership_plans.BASE_PLAN)["messages"], revenue_cents, env=ENV)
    await db.users.update_one(
        {"user_id": uid, "stripe_subscription_id": sub_id, "cancelled_stripe_subscriptions": {"$ne": sub_id},
         "tier": "paid", "$or": eligible_periods},
        {"$set": {"subscription_period_start": start, "subscription_period_end": end,
                  "stripe_price_id": canonical.id, "plan_offer": offer, "subscription_status": "active",
                  "membership_plan_id": offer if offer in membership_plans.PLANS else "plus_100"},
         "$min": {"intro_converted_at": now()}})
    await db.payment_transactions.update_one({"_id": tx["_id"]}, {"$set": {"status": "completed", "payment_status": "paid",
        "payment_verified": True, "credit_delivery_status": "fulfilled"}})
    await billing_event(kind, invoice["id"], uid, {"amount": amount}, paid_at)


# ---------------- admin ----------------
admin_dep = require_role("admin")


@api.get("/admin/main-consent")
async def admin_main_consent(response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    return await main_consent.aggregate(db, IN_ENV, ENV)


@api.get("/admin/research/notices")
async def admin_research_notices(response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    return await research_notices.aggregate(db, IN_ENV)


@api.get("/me/answer-history-permission")
async def own_answer_history_permission(response: Response, member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return admin_answer_history.preferences(member, ENV)


@api.patch("/me/answer-history-permission")
async def change_answer_history_permission(body: admin_answer_history.Permission, response: Response,
                                           member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await admin_answer_history.set_permission(db, member, ENV, body)


@api.get("/admin/answer-history-couples")
async def answer_history_couple_targets(response: Response, q: str = Query("", max_length=100),
                                        limit: int = Query(50, ge=1, le=100), before: str | None = None,
                                        admin: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    return await admin_answer_history.targets(db, admin, ENV, q=q, limit=limit, before=before)


@api.get("/admin/answer-history/{scope}/{target}.csv")
async def answer_history_csv(scope: Literal["member", "couple"], target: str,
                              limit: int = Query(50, ge=1, le=100), before: str | None = None,
                              domain: str | None = None, question_id: str | None = None,
                              from_date: str | None = None, to_date: str | None = None,
                              admin: dict = Depends(admin_dep)):
    return await admin_answer_history.export_csv(db, admin, ENV, scope, target,
        limit=limit, before=before, domain=domain, question_id=question_id,
        from_date=from_date, to_date=to_date)


@api.get("/admin/answer-history/{scope}/{target}")
async def answer_history_page(scope: Literal["member", "couple"], target: str, response: Response,
                               limit: int = Query(50, ge=1, le=100), before: str | None = None,
                               domain: str | None = None, question_id: str | None = None,
                               from_date: str | None = None, to_date: str | None = None,
                               admin: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    return await admin_answer_history.page(db, admin, ENV, scope, target,
        limit=limit, before=before, domain=domain, question_id=question_id,
        from_date=from_date, to_date=to_date)


@api.get("/admin/database-migration")
async def database_migration_status(response: Response, admin: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    return await database_migration_admin.status(db, ENV)


@api.post("/admin/database-migration/repair")
async def repair_answer_history(body: HistoryRepair, response: Response, admin: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    return await database_migration_admin.repair_history(db, ENV, body.limit)


@api.get("/admin/operations")
async def admin_operations(response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "no-store"
    last = await db.ops_state.find_one({"_id": f"service_checks:{ENV}"}, {"_id": 0, "checks": 1, "checked_at": 1}) or {}
    checked_at = last.get("checked_at")
    if checked_at:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return {"checked_at": checked_at, "checks": last.get("checks", {}), "billing_enabled": ops.billing_enabled(),
            "stale": bool(checked_at and datetime.now(timezone.utc) - checked_at > timedelta(minutes=35))}


@api.get("/admin/public-quiz")
async def admin_public_quiz(response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "no-store"
    s = await db.settings.find_one({"key": "global"}, {"public_quiz": 1, "public_quiz_revision": 1, "public_quiz_can_restore": 1}) or {}
    types = await db.archetypes.find({}, {"_id": 0, "slug": 1, "name": 1, "unlocked": 1}).sort("number", 1).to_list(None)
    return {"quiz": s.get("public_quiz"), "revision": s.get("public_quiz_revision"), "can_restore": bool(s.get("public_quiz_can_restore")), "types": types}


@api.put("/admin/public-quiz")
async def publish_public_quiz(body: dict, response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "no-store"
    revision = body.get("revision")
    if "revision" not in body or (revision is not None and not isinstance(revision, str)):
        raise HTTPException(422, "Load the current quiz revision before publishing.")
    await db.settings.update_one({"key": "global"}, {"$setOnInsert": default_settings()}, upsert=True)
    target = {"key": "global", "public_quiz_revision": revision}
    current = await db.settings.find_one(target, {"public_quiz": 1, "public_quiz_previous": 1, "public_quiz_can_restore": 1})
    if current is None:
        raise HTTPException(409, "Another admin published this quiz. Reload the published version.")
    if body.get("restore_previous") is True:
        if not current.get("public_quiz_can_restore") or "quiz" in body:
            raise HTTPException(422, "Choose an available previous publication to restore.")
        previous = current.get("public_quiz_previous")
        quiz = public_quiz.validate(previous, set(await db.archetypes.distinct("slug"))) if previous else None
    else:
        quiz = public_quiz.validate(body.get("quiz"), set(await db.archetypes.distinct("slug")))
    new_revision = uuid.uuid4().hex
    result = await db.settings.update_one(target, {"$set": {"public_quiz": quiz, "public_quiz_revision": new_revision,
        "public_quiz_previous": current.get("public_quiz"), "public_quiz_can_restore": True}})
    if not result.matched_count:
        raise HTTPException(409, "Another admin published this quiz. Reload the published version to continue.")
    return {"quiz": quiz, "revision": new_revision, "can_restore": True}


@api.get("/admin/archetypes")
async def admin_archetypes(summary: bool = False, _: dict = Depends(admin_dep)):
    projection = {"_id": 0, "editorial": 0}
    if summary:
        projection = {"_id": 0, "slug": 1, "name": 1, "number": 1, "gender": 1, "wave": 1, "unlocked": 1}
    return await db.archetypes.find({}, projection).sort("number", 1).to_list(None)


async def catalog_visibility(group, slug, body):
    row = await catalog_editorial.load(db, group, slug)
    flag = catalog_editorial.FLAGS[group]
    content_patch = {k: v for k, v in body.items() if k not in (flag, "notify")}
    if any(row.get(k) != v for k, v in content_patch.items()):
        raise HTTPException(428, "Open the article editor to save a draft and publish its reviewed revision.")
    if flag in body:
        value = body[flag]
        if type(value) is not bool:
            raise HTTPException(422, "Choose a visibility setting.")
        if value and not row.get(flag):
            await catalog_editorial.validate(db, group, catalog_editorial.content(group, row), row.get("number"))
        await db[group].update_one({"slug": slug}, {"$set": {flag: value}})
        row[flag] = value
        if value and body.get("notify") is True:
            if group == "archetypes":
                await notify_all("new_page", f"New archetype: {row['name']}", row["one_liner"], f"/types/{slug}", f"page:types/{slug}")
            else:
                await notify_all("new_page", f"New public figure: {row['name']}", row["identity"][:160], f"/figures/{slug}", f"page:figures/{slug}")
    row.pop("editorial", None)
    return row


@api.put("/admin/archetypes/{slug}")
async def admin_put_archetype(slug: str, body: dict, _: dict = Depends(admin_dep)):
    return await catalog_visibility("archetypes", slug, body)


@api.post("/admin/waves/{wave}")
async def admin_wave(wave: int, body: catalog_release.WavePublication, _: dict = Depends(admin_dep)):
    return await catalog_release.publish_wave(db, wave, body, notify_all)


@api.get("/admin/figures")
async def admin_figures(summary: bool = False, _: dict = Depends(admin_dep)):
    if not summary:
        return await db.figures.find({}, {"_id": 0, "editorial": 0}).to_list(None)
    docs = await db.figures.find({}, {"_id": 0, "name": 1, "slug": 1, "published": 1, "matches.number": 1}).to_list(None)
    names = {a["number"]: a["name"] async for a in db.archetypes.find({}, {"_id": 0, "number": 1, "name": 1})}
    for doc in docs:
        matches = doc.pop("matches", [])
        if matches:
            n = matches[0].get("number")
            doc["lead_type"] = {"number": n, "name": names.get(n)}
    return docs


@api.get("/admin/editorial/figure-reviews")
async def figure_review_queue(_: dict = Depends(admin_dep)):
    return await figure_evidence.staff_queue(db)


@api.put("/admin/editorial/figure-reviews/{slug}")
async def decide_figure_review(slug: str, body: figure_evidence.Decision, _: dict = Depends(admin_dep)):
    return await figure_evidence.record_decision(db, slug, body, now(), str(uuid.uuid4()))


@api.get("/admin/figures/{slug}")
async def admin_figure_detail(slug: str, _: dict = Depends(admin_dep)):
    doc = await db.figures.find_one({"slug": slug}, {"_id": 0, "editorial": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    return doc


@api.put("/admin/figures/{slug}")
async def admin_put_figure(slug: str, body: dict, _: dict = Depends(admin_dep)):
    return await catalog_visibility("figures", slug, body)


@api.get("/admin/editorial/articles/{group}/{slug}")
async def catalog_edit(group: catalog_editorial.Group, slug: str, response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    return catalog_editorial.view(group, await catalog_editorial.load(db, group, slug))


@api.post("/admin/editorial/articles/{group}/{slug}/preview")
async def catalog_preview(group: catalog_editorial.Group, slug: str, body: catalog_editorial.Draft, response: Response,
                          _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    row = await catalog_editorial.load(db, group, slug)
    if body.version != catalog_editorial.signature(group, row):
        catalog_editorial.conflict()
    await catalog_editorial.validate(db, group, body.content, row.get("number"))
    for k in ("editorial", "sections_edited", "dating_seeded"):
        row.pop(k, None)
    row.update(body.content)
    return await (archetype_reader(row) if group == "archetypes" else figure_reader(row))


@api.put("/admin/editorial/articles/{group}/{slug}/draft")
async def catalog_draft(group: catalog_editorial.Group, slug: str, body: catalog_editorial.Draft, member: dict = Depends(admin_dep)):
    return await catalog_editorial.change(db, group, slug, body, "draft", member.get("user_id"))


@api.post("/admin/editorial/articles/{group}/{slug}/publish")
async def catalog_publish(group: catalog_editorial.Group, slug: str, body: catalog_editorial.Version, member: dict = Depends(admin_dep)):
    return await catalog_editorial.change(db, group, slug, body, "publish", member.get("user_id"))


@api.post("/admin/editorial/articles/{group}/{slug}/restore")
async def catalog_restore(group: catalog_editorial.Group, slug: str, body: catalog_editorial.Restore, member: dict = Depends(admin_dep)):
    return await catalog_editorial.change(db, group, slug, body, "restore", member.get("user_id"))


@api.get("/admin/settings")
async def admin_settings(_: dict = Depends(admin_dep)):
    return await get_settings()


@api.get("/admin/prompts")
async def admin_prompts(response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "no-store"
    s = await get_settings()
    return {"prompts": s["prompts"], "areas": prompt_library.AREAS, "usage_features": prompt_library.USAGE_FEATURES, "revision": s.get("prompts_revision"),
            "published_at": s.get("prompts_published_at"), "max_length": prompt_library.MAX_LENGTH}


@api.post("/admin/prompts/preview")
async def preview_prompt(body: dict, response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "no-store"
    key = body.get("key")
    if not isinstance(key, str):
        raise HTTPException(422, "Choose an AI instruction to preview.")
    return prompt_library.preview(DEFAULT_PROMPTS, (await get_settings())["prompts"], key, body.get("text"), body.get("context", "six_part"))


@api.get("/admin/prompts/versions")
async def prompt_versions(response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    return await prompt_library.versions(db, ENV, DEFAULT_PROMPTS)


@api.post("/admin/prompts/evaluate")
async def evaluate_candidate(body: dict, response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    changes = prompt_library.validate_changes(DEFAULT_PROMPTS, body.get("prompts"))
    settings = await get_settings()
    if body.get("prompts_revision") != settings.get("prompts_revision"):
        raise HTTPException(409, "The published instructions changed. Refresh before evaluating this draft.")
    return await prompt_candidate.evaluate(db, ENV, settings, changes, ai_json)


@api.post("/admin/prompts/review")
async def review_prompt_release(body: dict, actor: dict = Depends(admin_dep)):
    revision, answers = body.get("revision"), body.get("answers")
    if not isinstance(answers, dict) or answers.keys() != prompt_library.EVAL_CASES.keys() or any(
            value not in ("pass", "fail", "unreviewed") for value in answers.values()):
        raise HTTPException(422, "Choose a review outcome for each example.")
    if revision != ((await get_settings()).get("prompts_revision") or "initial"):
        raise HTTPException(409, "The published instructions changed. Reload before reviewing.")
    review = {"protocol": prompt_library.EVAL_VERSION, "at": now(), "answers": answers}
    await db.prompt_reviews.update_one({"_id": ENV + ":" + revision}, {"$set": {"result": review, "by": actor["user_id"]}}, upsert=True)
    return review


@api.post("/admin/prompts/rollback")
async def rollback_prompt_release(body: dict, actor: dict = Depends(admin_dep)):
    target = body.get("target")
    if not isinstance(target, str):
        raise HTTPException(422, "Choose a saved version.")
    previous = await db.settings.find_one({"key": "global", "prompt_history.revision": target},
        {"prompt_history": {"$elemMatch": {"revision": target}}})
    if not previous:
        raise HTTPException(404, "That prompt version is no longer retained.")
    archived = previous["prompt_history"][0]
    return await update_admin_settings({"prompts_revision": body.get("current"), "prompts":
        prompt_library.normalized(DEFAULT_PROMPTS, archived["prompts"])}, actor, restored=archived)


@api.get("/admin/quiz/everyday/draft")
async def everyday_draft(response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    doc = await db.settings.find_one({"key": "global"}, {"everyday_draft": 1, "everyday_draft_revision": 1, "quiz_revision": 1}) or {}
    return {"draft": doc.get("everyday_draft"), "revision": doc.get("everyday_draft_revision"),
            "published_revision": doc.get("quiz_revision")}


@api.put("/admin/quiz/everyday/draft")
async def save_everyday_draft(body: dict, actor: dict = Depends(admin_dep)):
    settings = await get_settings()
    if body.get("revision") is not None and not isinstance(body["revision"], str):
        raise HTTPException(422, "Supply a valid staff draft revision.")
    if "quiz_revision" not in body or "revision" not in body or body["quiz_revision"] != settings.get("quiz_revision"):
        raise HTTPException(409, "Load the current publication and draft before saving.")
    everyday_catalog.prepare(body.get("quiz"), settings["quiz"], safe_quiz_id)
    revision = uuid.uuid4().hex
    draft = {"sets": everyday_catalog.section_data(body["quiz"]), "base_revision": body["quiz_revision"], "saved_at": now()}
    result = await db.settings.update_one({"key": "global", "quiz_revision": body["quiz_revision"],
        "everyday_draft_revision": body["revision"]}, {"$set": {"everyday_draft_revision": revision, "everyday_draft": draft}})
    if not result.matched_count:
        raise HTTPException(409, "Another editor changed the draft. Reload it before saving.")
    return {"draft": draft, "revision": revision, "published_revision": body["quiz_revision"]}


@api.post("/admin/quiz/everyday/preview")
async def preview_everyday_quiz(body: dict, _: dict = Depends(admin_dep)):
    settings = await get_settings()
    draft = body.get("quiz")
    candidate = everyday_catalog.prepare(draft, settings["quiz"], safe_quiz_id)
    choices = body.get("choices", {})
    if not isinstance(choices, dict) or len(choices) > 600:
        raise HTTPException(422, "Choose a sample answer from the draft.")
    answers = {}
    for section in quiz_answers.SETS:
        for original, row in zip(draft[section], candidate[section]):
            if original["id"] not in choices:
                continue
            index = choices[original["id"]]
            if type(index) is not int or not 0 <= index < len(row["options"]):
                raise HTTPException(422, "Choose an available answer.")
            answers[row["id"]] = {"choice": index, "question": row["q"], "label": row["options"][index]["label"]}
    if len(answers) != len(choices):
        raise HTTPException(422, "A selected preview question is no longer in the draft.")
    about = {"answers": answers}
    return {"answered": len(answers), "axes": radar(about, candidate), "type": about_summary(about, candidate)["personality"]}


@api.get("/admin/quiz/intimacy")
async def admin_intimacy_questions(response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "no-store"
    return {"overrides": await intimacy_catalog.overrides(db, staff=True), "drafts": await intimacy_catalog.drafts(db)}


@api.put("/admin/quiz/intimacy/draft")
async def save_intimacy_question_draft(body: intimacy_catalog.DraftEdit, _: dict = Depends(admin_dep)):
    return await intimacy_catalog.save_draft(db, body)


@api.get("/admin/quiz/results")
async def admin_quiz_results(_: dict = Depends(admin_dep)):
    return {"types": [{"code": code, "name": name, "line": line} for code, (name, line) in TYPES.items()]}


@api.put("/admin/quiz/intimacy")
async def publish_intimacy_question(body: intimacy_catalog.QuestionEdit, _: dict = Depends(admin_dep)):
    return await intimacy_catalog.publish(db, body)


@api.get("/admin/prompts/usage")
async def prompts_usage(response: Response, days: int = 30, end_date: str | None = None, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "no-store"
    return await prompt_library.usage(db, ENV, days, end_date)


@api.put("/admin/settings")
async def admin_put_settings(body: dict, actor: dict = Depends(admin_dep)):
    return await update_admin_settings(body, actor)


async def update_admin_settings(body, actor, restored=None):
    upd = {}
    query = {"key": "global"}
    history = {}
    if body.get("people_limit") is not None:
        raise HTTPException(422, "Plus includes unlimited active people.")
    if "waitlist_mode" in body:
        upd["waitlist_mode"] = bool(body["waitlist_mode"])
    if "tiktok_url" in body:
        upd["tiktok_url"] = str(body["tiktok_url"])[:300]
    if "prompts" in body:
        changes = prompt_library.validate_changes(DEFAULT_PROMPTS, body["prompts"])
        query["prompts_revision"] = body.get("prompts_revision")
        await db.settings.update_one({"key": "global"}, {"$setOnInsert": default_settings()}, upsert=True)
        previous = await get_settings()
        if previous.get("prompts_revision") != query["prompts_revision"]:
            raise HTTPException(409, "Another admin published a change. Reload the instructions.")
        candidate = {**previous["prompts"], **changes}
        prompt_library.composition_checks(candidate)
        if restored is None:
            expected = prompt_candidate.identity(ENV, previous.get("prompts_revision"), candidate)
            observation = None
            if body.get("reviewed_candidate") == expected:
                observation = await db.prompt_candidates.find_one({"_id": expected, "report.eligible": True,
                    "report.protocol": prompt_candidate.PROTOCOL,
                    "expires_at": {"$gt": datetime.now(timezone.utc)}}, {"report": 1})
            if not observation:
                raise HTTPException(409, "Evaluate and review the examples for this exact draft before publishing.")
            evidence = {"protocol": prompt_candidate.PROTOCOL, "id": expected, "cases": len(observation["report"]["cases"]),
                        "accepted_by": actor["user_id"], "at": now()}
        else:
            evidence = restored.get("evaluation")
        snapshot = {"revision": previous.get("prompts_revision") or "initial", "at": previous.get("prompts_published_at"),
                    "prompts": previous["prompts"], "evaluation": previous.get("prompts_evaluation")}
        history = {"$push": {"prompt_history": {"$each": [snapshot], "$slice": -prompt_library.HISTORY_LIMIT}}}
        upd.update({f"prompts.{key}": value for key, value in changes.items()})
        upd.update(prompts_revision=uuid.uuid4().hex, prompts_published_at=now(), prompts_published_by=actor["user_id"])
        upd["prompts_evaluation"] = evidence
    if isinstance(body.get("upgrade"), dict):
        offered = body["upgrade"]
        if "free_features" in offered or offered.get("price_label", stripe_plan.PRICE_LABEL) != stripe_plan.PRICE_LABEL:
            raise HTTPException(422, "Standard prices and reply allowances are managed in the billing catalog.")
        upd["upgrade"] = paid_plan_copy(offered)
    qz = body.get("quiz")
    rollback_quiz = body.get("restore_previous_quiz") is True
    if qz is not None or rollback_quiz:
        previous = await get_settings()
        query["quiz_revision"] = previous.get("quiz_revision")
        if body.get("quiz_revision") != query["quiz_revision"]:
            raise HTTPException(409, "Another edit changed the quiz. Reload the editor before saving.")
        if "everyday_draft_revision" in body:
            if body["everyday_draft_revision"] is not None and not isinstance(body["everyday_draft_revision"], str):
                raise HTTPException(422, "Supply a valid staff draft revision.")
            query["everyday_draft_revision"] = body["everyday_draft_revision"]
        if rollback_quiz:
            if qz is not None:
                raise HTTPException(422, "Choose one action: publish the draft or restore the previous quiz.")
            doc = await db.settings.find_one(query, {"everyday_previous": 1})
            if not doc or not doc.get("everyday_previous"):
                raise HTTPException(409, "There is no matching previous publication to restore.")
            qz = doc["everyday_previous"]["sets"]
        candidate = everyday_catalog.prepare(qz, previous["quiz"], safe_quiz_id, rollback=rollback_quiz)
        upd.update(quiz=candidate, quiz_revision=uuid.uuid4().hex, quiz_published_at=now(), quiz_can_restore=True,
                   everyday_previous={"sets": everyday_catalog.section_data(previous["quiz"]), "at": previous.get("quiz_published_at")})
        if "everyday_draft_revision" in body:
            upd.update(everyday_draft_revision=uuid.uuid4().hex, everyday_draft=None)
    result = await db.settings.update_one(query, {"$set": upd, **history}, upsert="quiz" not in upd and "prompts" not in body)
    if "prompts" in body and not result.matched_count:
        raise HTTPException(409, "Another admin published a change. Reload the published instructions before editing again.")
    if "quiz" in upd and not result.matched_count:
        raise HTTPException(409, "Another edit changed the quiz. Reload the editor before saving.")
    return await get_settings()


BILLING_FIELDS = ("tier", "subscription_status", "stripe_subscription_id", "subscription_period_start", "subscription_period_end", "plan_offer", "subscription_allowance_adjustments")
TESTER_RE = {"$regex": r"(clerk_test|@example\.com$|\+test)", "$options": "i"}


def row_export_population():
    return {**IN_ENV, "role": "member", "closing": {"$ne": True}, "email": {"$not": TESTER_RE}}


@api.get("/your-type/preferences")
async def my_your_type_preferences(response: Response, member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await your_type_preferences.read(db, member["user_id"])


@api.put("/your-type/preferences")
async def change_your_type_preferences(body: your_type_preferences.Change, response: Response,
        member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await your_type_preferences.save(db, member["user_id"], body)


@api.get("/your-type/research")
async def my_your_type_research(response: Response, member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return your_type_research.preferences(member)


@api.patch("/your-type/research")
async def change_your_type_research(body: your_type_research.Consent, response: Response, member: dict = Depends(current_member)):
    response.headers["Cache-Control"] = "private, no-store"
    return await your_type_research.consent(db, member, body)


@api.get("/admin/research/your-type")
async def admin_your_type_research(response: Response, x: str = Query("audience", max_length=40),
        y: str = Query("progress", max_length=40), edition: str | None = Query(None, max_length=64), _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    return await your_type_research.report(db, row_export_population(), x, y, edition, quiz=(await get_settings())["quiz"])


@api.get("/admin/your-type/catalog")
async def admin_your_type_catalog(response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    return your_type_research.catalog()


@api.get("/admin/users/{uid}/your-type")
@api.get("/users/{uid}/your-type")
async def admin_member_your_type(uid: str, response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    if not await db.users.find_one({**IN_ENV, "user_id": uid}, {"_id": 1}):
        raise HTTPException(404, "Member not found.")
    return await your_type_research.member_admin(db, uid)


@api.get("/users/{uid}/your-type.csv")
async def admin_member_your_type_csv(uid: str, _: dict = Depends(admin_dep)):
    return await your_type_research.member_csv(db, row_export_population(), uid, now())


@api.get("/admin/research/exports/{scope}/units")
async def research_export_units(scope: research_rows.Scope, response: Response, after: str | None = None,
                                limit: int = Query(50, ge=1, le=100), _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    return await research_rows.page(db, row_export_population(), scope, after, limit, research_couple_export.iter_codes)


@api.get("/admin/research/exports/{scope}.csv")
async def research_export_csv(scope: research_rows.Scope, unit_code: str | None = None, _: dict = Depends(admin_dep)):
    research_rows.check_code(scope, unit_code)
    stamp = now()
    eligible = row_export_population()
    if scope == "member":
        source = research_rows.member_rows(db, eligible, stamp, await get_settings(), unit_code)
    else:
        source = research_couple_export.iter_rows(db, eligible, stamp, unit_code)
    return await research_rows.export_response(source, scope, stamp, unit_code)


@api.get("/admin/research/tone")
async def admin_tone_research(response: Response, unit: Literal["member", "couple"] = "member",
                              x: str = Query("sent_style", max_length=40), y: str = Query("helped", max_length=40),
                              incoming: str | None = Query(None, max_length=32), _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    eligible = {**IN_ENV, "role": "member", "email": {"$not": TESTER_RE}, "closing": {"$ne": True}}
    return await tone_research.report(db, ENV, eligible, unit, x, y, incoming)


@api.get("/admin/research/readers")
async def independent_results(_: dict = Depends(admin_dep)):
    return await human_review.results(db, ENV)


@api.get("/admin/research/readers/{slot}")
async def independent_packet(slot: Literal["A", "B"], _: dict = Depends(admin_dep)):
    return Response(human_review.reader_html(slot), media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="imladri-reader-{slot}.html"', "Cache-Control": "private, no-store"})


@api.post("/admin/research/readers")
async def independent_import(body: human_review.Ballot, staff: dict = Depends(admin_dep)):
    return await human_review.save(db, ENV, body, staff["user_id"], now())


@api.get("/admin/research/rehearsals")
async def rehearsal_study_results(response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    return await rehearsal_review.results(db, ENV)


@api.get("/admin/research/rehearsals/{slot}")
async def rehearsal_study_packet(slot: Literal["A", "B"], _: dict = Depends(admin_dep)):
    return Response(rehearsal_review.reader_html(slot), media_type="text/html", headers={
        "Content-Disposition": f'attachment; filename="rehearsal-reader-{slot}.html"', "Cache-Control": "private, no-store"})


@api.post("/admin/research/rehearsals")
async def rehearsal_study_import(body: rehearsal_review.Submission, staff: dict = Depends(admin_dep)):
    return await rehearsal_review.save(db, ENV, body, staff["user_id"], now())


@api.delete("/admin/research/rehearsals/{edition}/{slot}")
async def rehearsal_study_withdraw(edition: str, slot: Literal["A", "B"], _: dict = Depends(admin_dep)):
    return await rehearsal_review.withdraw(db, ENV, edition, slot)


@api.get("/admin/research/evidence")
async def admin_assessment_evidence(response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    eligible = {**IN_ENV, "role": "member", "email": {"$not": TESTER_RE}, "closing": {"$ne": True}}
    stamp = datetime.now(timezone.utc)
    questionnaire, individual, couples, member_fit, couple_fit, wording, pair_coverage = await asyncio.gather(
        assessment_evidence.questionnaire_summary(db, eligible, (await get_settings())["quiz"], stamp),
        assessment_evidence.outcome_summary(db, ENV, eligible, "member", stamp),
        assessment_evidence.outcome_summary(db, ENV, eligible, "couple", stamp),
        assessment_evidence.fit_summary(db, ENV, eligible, "member", stamp),
        assessment_evidence.fit_summary(db, ENV, eligible, "couple", stamp),
        wording_feedback.summary(db, eligible), assessment_evidence.pair_feedback_coverage(db, ENV, eligible, stamp))
    return {"as_of": stamp.isoformat(), "minimum": assessment_evidence.MINIMUM, "minimum_cell": assessment_evidence.MIN_CELL,
            "round_to": assessment_evidence.STEP, "questionnaire": questionnaire, "individual": individual, "couples": couples,
            "tone_fit":{"individual":member_fit,"couples":couple_fit}, "intimacy_wording":wording, "shared_tone_raters":pair_coverage}


@api.get("/public/research/repeat")
async def public_repeat_evidence(response: Response):
    response.headers["Cache-Control"] = "no-store"
    eligible = {**IN_ENV, "role": "member", "email": {"$not": TESTER_RE}, "closing": {"$ne": True}}
    quiz = (await get_settings())["quiz"]
    rows = await assessment_evidence.repeated_items(db, eligible, quiz)
    return {"instrument": assessment_evidence.instrument(quiz), "minimum": assessment_evidence.MINIMUM,
            "minimum_cell": assessment_evidence.MIN_CELL, "round_to": assessment_evidence.STEP,
            "repeat": [row for row in rows if row["sample"] is not None]}


@api.get("/admin/research/trends")
async def admin_research_trends(response: Response, x: str | None = Query(None, max_length=256),
                                y: str | None = Query(None, max_length=256),
                                x_condition: str | None = Query(None, max_length=40),
                                y_condition: str | None = Query(None, max_length=40),
                                unit: Literal["member", "couple", "couple_inviter", "couple_invitee"] = "member",
                                _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "no-store"
    eligible = {**IN_ENV, "role": {"$ne": "admin"}, "email": {"$not": TESTER_RE}, "closing": {"$ne": True}}
    return await research_trends.report(db, eligible, (await get_settings())["quiz"], x, y, x_condition, y_condition, unit)


@api.get("/admin/research/registrations")
async def research_registrations(response: Response, after: str = Query("", max_length=64, pattern="^(?:[0-9a-f]{64})?$"), _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    eligible = {**IN_ENV, "role":{"$ne":"admin"}, "email":{"$not":TESTER_RE}, "closing":{"$ne":True}}
    query = {"env": ENV}
    if after:
        query["_id"] = {"$gt":after}
    docs = await db.research_registrations.find(query).sort("_id", 1).limit(26).to_list(26)
    return {"items":[research_registration.brief(row) for row in docs[:25]],
            "next":docs[24]["_id"] if len(docs) > 25 else None, "coverage":await research_registration.coverage(db,eligible)}


@api.post("/admin/research/registrations")
async def register_research_comparison(body: research_registration.Registration, response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    eligible = {**IN_ENV, "role":{"$ne":"admin"}, "email":{"$not":TESTER_RE}, "closing":{"$ne":True}}
    return await research_registration.register(db,ENV,eligible,(await get_settings())["quiz"],body,now())


@api.get("/admin/research/registrations/{key}")
async def research_followup(key: str, response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    eligible = {**IN_ENV, "role":{"$ne":"admin"}, "email":{"$not":TESTER_RE}, "closing":{"$ne":True}}
    return await research_registration.follow_up(db,ENV,eligible,(await get_settings())["quiz"],key)


@api.get("/admin/research/distribution")
async def questionnaire_distribution(response: Response, key: str = Query(min_length=1, max_length=256),
                                     condition: str | None = Query(None, max_length=40), _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    eligible = {**IN_ENV, "role": {"$ne": "admin"}, "email": {"$not": TESTER_RE}, "closing": {"$ne": True}}
    return await research_trends.distribution(db, eligible, (await get_settings())["quiz"], key, condition)


class QuestionnaireScanIn(BaseModel, extra="forbid"):
    unit: Literal["member", "couple", "couple_inviter", "couple_invitee"] = "member"
    plan: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-f0-9]+$")
    cursor: int = Field(default=0, ge=0, strict=True)
    review: list[tuple[int, int]] | None = Field(default=None, max_length=40)


@api.post("/admin/research/scan")
async def scan_questionnaire_associations(body: QuestionnaireScanIn, response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    eligible = {**IN_ENV, "role": {"$ne": "admin"}, "email": {"$not": TESTER_RE}, "closing": {"$ne": True}}
    return await research_scan.page(db, eligible, (await get_settings())["quiz"], body.unit, body.plan, body.cursor, body.review)


@api.get("/admin/research/discover")
async def discover_questionnaire_associations(response: Response, x: str = Query(max_length=256),
        unit: Literal["member", "couple", "couple_inviter", "couple_invitee"] = "member",
        x_condition: str | None = Query(None, max_length=40), cursor: int = Query(0, ge=0),
        catalog_version: str | None = Query(None, min_length=64, max_length=64), _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    eligible = {**IN_ENV, "role": {"$ne": "admin"}, "email": {"$not": TESTER_RE}, "closing": {"$ne": True}}
    return await research_trends.discover(db, eligible, (await get_settings())["quiz"], x, unit, x_condition,
                                          cursor, catalog_version)


@api.get("/admin/trends")
async def admin_trends(response: Response, days: int = 30, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "no-store"
    eligible = {**IN_ENV, "role": {"$ne": "admin"}, "email": {"$not": TESTER_RE}}
    return await trends.report(db, eligible, (await get_settings())["quiz"], days)


@api.get("/admin/reports/workspace.csv")
async def download_workspace_report(days: int = 30, member: dict = Depends(admin_dep)):
    if days not in (7, 30, 90):
        raise HTTPException(422, "Choose a 7, 30 or 90 day window.")
    eligible = {**IN_ENV, "role": {"$ne": "admin"}, "email": {"$not": TESTER_RE}}
    current = datetime.now(timezone.utc)
    workspace, couples, dashboard, prompts, membership = await asyncio.gather(
        trends.report(db, eligible, (await get_settings())["quiz"], days, current.date()),
        couples_analytics.report(db, ENV, eligible, days, current), admin_metrics(member), prompt_library.usage(db, ENV, days),
        membership_admin.summary(db, ENV, IN_ENV, current))
    filename = f'imladri-workspace-{workspace["start"]}-{workspace["end"]}.csv'
    return Response(content=trends_export.export_report(workspace, couples, dashboard, prompts, membership), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@api.get("/admin/couples/analytics")
async def get_couples_analytics(response: Response, days: int = 30, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "no-store"
    eligible = {**IN_ENV, "role": {"$ne": "admin"}, "email": {"$not": TESTER_RE}}
    return await couples_analytics.report(db, ENV, eligible, days)


@api.get("/admin/couples/catalog")
async def couples_editor(response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "no-store"
    return await couples_catalog.editor(db, COUPLES_STARTERS)


@api.put("/admin/couples/catalog")
async def edit_couples_catalog(body: couples_catalog.CatalogEdit, member: dict = Depends(admin_dep)):
    return await couples_catalog.edit(db, COUPLES_STARTERS, body, member["user_id"], now())


async def admin_people_metrics(uids, tier_of):
    fields = {"$objectToArray": {"$ifNull": ["$intake", {}]}}
    text = {"$cond": [{"$eq": [{"$type": "$$field.v"}, "string"]}, "$$field.v", ""]}
    filled = {"$filter": {"input": fields, "as": "field", "cond": {"$and": [
        {"$in": ["$$field.k", list(FIELD_LABELS)]},
        {"$gte": [{"$strLenCP": {"$trim": {"input": text}}}, MIN_INTAKE_LENGTH]},
    ]}}}
    has_detail = {"$anyElementTrue": [{"$map": {"input": fields, "as": "field", "in": {"$and": [
        {"$in": ["$$field.k", list(FIELD_LABELS)]}, {"$ne": [{"$trim": {"input": text}}, ""]},
    ]}}}]}
    exchange_counts = {"$group": {"_id": None,
        "conversations": {"$sum": {"$cond": [{"$eq": ["$has_conversation", True]}, 1, 0]}},
        "outcomes": {"$sum": {"$cond": [{"$ne": [{"$ifNull": ["$outcome", ""]}, ""]}, 1, 0]}}}}
    rows = db.people.aggregate([
        {"$match": {"user_id": {"$in": uids}}},
        {"$project": {"_id": 0, "id": 1, "user_id": 1, "rel_type": 1, "filled": {"$size": filled}, "has_detail": has_detail}},
        {"$lookup": {"from": "exchanges", "localField": "id", "foreignField": "person_id",
                     "let": {"owner": "$user_id"},
                     "pipeline": [{"$match": {"$expr": {"$eq": ["$user_id", "$$owner"]}}}, exchange_counts], "as": "counts"}},
    ])
    relationships, groups, tiers = Counter(), Counter(), Counter()
    relationship_scores, tier_scores = Counter(), Counter()
    started, detailed = set(), set()
    async for row in rows:
        started.add(row["user_id"])
        if row["has_detail"]:
            detailed.add(row["user_id"])
        relation = row.get("rel_type") if row.get("rel_type") in GROUP_OF else "other"
        tier = tier_of[row["user_id"]]
        counts = row["counts"][0] if row["counts"] else {}
        score = detail_score(row["filled"], counts.get("conversations", 0), counts.get("outcomes", 0))
        relationships[relation] += 1
        groups[GROUP_OF.get(relation, "other")] += 1
        tiers[tier] += 1
        relationship_scores[relation] += score
        tier_scores[tier] += score
    return {"profile_progress": {"members": len(uids), "started": len(started), "with_details": len(detailed)},
            "people_by_relationship": dict(relationships), "people_by_group": dict(groups),
            "avg_detail_by_relationship": {k: round(v / relationships[k]) for k, v in relationship_scores.items()},
            "avg_detail_by_tier": {k: round(v / tiers[k]) for k, v in tier_scores.items()}}


def metric_time(value):
    if not isinstance(value, (str, datetime)):
        return None
    try:
        return couples_analytics.timestamp(value)
    except (ValueError, OverflowError):
        return None


def metric_date_field(field):
    return {"$convert": {"input": {"$cond": [{"$in": [{"$type": field}, ["string", "date"]]}, field, None]},
                         "to": "date", "onError": None, "onNull": None}}


async def admin_metric_read_rows(uids, users, first_ex, cutoff):
    # These independent, owner-cohort-scoped reads previously waited in series.
    # The remote repository retains its existing two-request concurrency bound.
    # Preserve each predicate/projection and the post-read calculation exactly.
    day = {"$dateToString": {"date": "$ts", "format": "%Y-%m-%d", "timezone": "UTC"}}
    session_rows = db.events.aggregate([
        {"$match": {"type": "session", "user_id": {"$in": uids}}},
        {"$project": {"user_id": 1, "ts": metric_date_field("$ts")}},
        {"$match": {"ts": {"$ne": None, "$lte": cutoff}}},
        {"$group": {"_id": "$user_id", "visits": {"$sum": 1}, "first": {"$min": day}, "last": {"$max": day}}},
        {"$group": {"_id": None, "sessions": {"$sum": "$visits"},
                    "returning_users": {"$sum": {"$cond": [{"$gt": ["$visits", 1]}, 1, 0]}},
                    "returning_days": {"$sum": {"$cond": [{"$ne": ["$first", "$last"]}, 1, 0]}}}},
        {"$project": {"_id": 0}},
    ], allowDiskUse=True).to_list(1)
    deepen_rows = db.events.find({"type": "deepen", "user_id": {"$in": list(first_ex)}}, {"_id": 0, "user_id": 1, "ts": 1}).to_list(None)
    scalar_uid = {"$eq": [{"$type": "$user_id"}, "string"]}
    memory_holder_rows = db.memories.distinct("user_id", {"user_id": {"$in": uids}, "$expr": scalar_uid})
    kinds = ("upload_started", "upload_completed", "memory_edit")
    count_rows = db.events.aggregate([
        {"$match": {"type": {"$in": list(kinds)}, "user_id": {"$in": uids},
                    "$expr": {"$and": [scalar_uid, {"$eq": [{"$type": "$type"}, "string"]}]}}},
        {"$project": {"type": 1, "user_id": 1, "ts": metric_date_field("$ts")}},
        {"$match": {"ts": {"$ne": None, "$lte": cutoff}}},
        {"$group": {"_id": {"type": "$type", "uid": "$user_id"}, "count": {"$sum": 1}}},
    ], allowDiskUse=True).to_list(None)
    tier_of = {u["user_id"]: billing_tier(u) for u in users}
    return await asyncio.gather(
        db.payment_transactions.distinct("user_id", {"user_id": {"$in": uids}, "kind": "purchase", "payment_status": "paid"}),
        db.payment_transactions.distinct("user_id", {"user_id": {"$in": uids}, "kind": "renewal", "payment_status": "paid"}),
        trends.public_entry_pages(db), session_rows, trends.onboarding_progress(db, users),
        admin_people_metrics(uids, tier_of), deepen_rows, memory_holder_rows, count_rows,
        db.waitlist.count_documents(IN_ENV))


@api.get("/admin/metrics")
async def admin_metrics(_: dict = Depends(admin_dep)):
    report_as_of = now()
    cutoff = datetime.fromisoformat(report_as_of)
    quiz = (await get_settings())["quiz"]
    sections = {section: {"questions": len(quiz[section]), "halfway_at": (len(quiz[section]) + 1) // 2,
                          "started": 0, "halfway": 0, "complete": 0} for section in quiz_answers.SETS}
    # Stream only the fields needed to resolve current or legacy choices. Freeform notes and
    # profile text stay in the database; identifiers and individual answers never enter the quiz stats.
    projection = {"_id": 0, "user_id": 1, "origin": 1, "tier": 1, "created_at": 1,
                  "onboarding_step": 1, "onboarded": 1, "onboarding_skipped": 1}
    projection.update({field: 1 for field in BILLING_FIELDS})
    for qid in quiz_answers.questions_by_id(quiz):
        if safe_quiz_id(qid):
            for field in ("question", "label", "choice"):
                projection[f"about.answers.{qid}.{field}"] = 1
    users = []
    async for user in db.users.find({**IN_ENV, "role": {"$ne": "admin"}, "email": {"$not": TESTER_RE}, "closing": {"$ne": True}}, projection):
        about = user.pop("about", None)
        users.append(user)
        if not (about or {}).get("answers"):
            continue
        for section, count in quiz_answer_counts(about, quiz).items():
            row = sections[section]
            if row["questions"]:
                row["started"] += count > 0
                row["halfway"] += count >= row["halfway_at"]
                row["complete"] += count == row["questions"]
    uids = [u["user_id"] for u in users]
    first_ex, first_week_end, activated = {}, {}, set()
    exchanges = db.exchanges.aggregate([
        {"$match": {"user_id": {"$in": uids}}},
        {"$project": {"_id": 0, "user_id": 1, "created_at": metric_date_field("$created_at"),
                      "outcome_at": {"$cond": [{"$and": [{"$eq": [{"$type": "$outcome"}, "string"]},
                                                          {"$ne": ["$outcome", ""]}]},
                                               metric_date_field("$outcome_at"), None]}}},
        {"$match": {"created_at": {"$ne": None, "$lte": cutoff}}},
        {"$sort": {"created_at": 1}},
    ], allowDiskUse=True)
    async for exchange in exchanges:
        uid, created = exchange["user_id"], metric_time(exchange["created_at"])
        if uid not in first_ex:
            first_ex[uid] = created
            first_week_end[uid] = created + timedelta(days=7)
        elif created <= first_week_end[uid]:
            activated.add(uid)
        outcome_at = metric_time(exchange.get("outcome_at"))
        if outcome_at and created <= outcome_at <= min(first_week_end[uid], cutoff):
            activated.add(uid)
    (bought, renewed, catalog_pages, session_rows, (steps, skipped), people_metrics,
     deepen_rows, memory_holder_rows, count_rows, waitlist) = await admin_metric_read_rows(uids, users, first_ex, cutoff)
    bought, renewed = set(bought), set(renewed)
    activation = [{"name": "Signed up", "value": len(uids)}, {"name": "First read", "value": len(first_ex)},
                  {"name": "2nd situation or outcome in 7d", "value": len(activated)}, {"name": "Purchased", "value": len(bought)}, {"name": "First renewal", "value": len(renewed)}]
    by_origin: dict = {}
    origins: dict = {}
    for u in users:
        o = trends.entry_page(u.get("origin"), catalog_pages)
        r = by_origin.setdefault(o, {"origin": o, "signups": 0, "first_read": 0, "activated": 0, "purchased": 0, "renewed": 0})
        r["signups"] += 1
        r["first_read"] += u["user_id"] in first_ex
        r["activated"] += u["user_id"] in activated
        r["purchased"] += u["user_id"] in bought
        r["renewed"] += u["user_id"] in renewed
        if u.get("origin"):
            origins[o] = origins.get(o, 0) + 1
    session_counts = session_rows[0] if session_rows else {"sessions": 0, "returning_users": 0, "returning_days": 0}
    by_day: dict = {}
    for u in users:
        signup = metric_time(u.get("created_at"))
        if signup and signup <= cutoff:
            day_key = signup.date().isoformat()
            by_day[day_key] = by_day.get(day_key, 0) + 1
    deepened = set()
    for event in deepen_rows:
        uid = event["user_id"]
        at = metric_time(event.get("ts"))
        if at and first_ex[uid] <= at <= min(first_week_end[uid], cutoff):
            deepened.add(uid)
    mature = {uid for uid, deadline in first_week_end.items() if deadline <= cutoff}
    memory_holders = set(memory_holder_rows)
    counts = dict(upload_started=0, upload_completed=0, memory_edit=0)
    holder_edits = 0
    for row in count_rows:
        kind = row["_id"]["type"]
        counts[kind] += row["count"]
        if kind == "memory_edit" and row["_id"]["uid"] in memory_holders:
            holder_edits += row["count"]
    up_s, up_c, edits = counts["upload_started"], counts["upload_completed"], counts["memory_edit"]
    return {"as_of": report_as_of, "users_with_memories": len(memory_holders), "users": len(users), "activation": activation, "by_origin": sorted(by_origin.values(), key=lambda r: (-r["signups"], r["origin"])),
            **session_counts, **people_metrics, "onboarding_steps": steps, "onboarding_skipped": skipped, "signups_by_origin": origins,
            "deepening_rate": {"first_session_users": len(first_ex), "deepened_within_7d": len(deepened),
                               "rate": round(len(deepened) / len(first_ex) * 100) if first_ex else 0},
            "deepening_window": {"complete": len(mature), "deepened": len(mature & deepened), "open": len(first_ex) - len(mature)},
            "upload_completion": {"started": up_s, "completed": up_c, "rate": round(up_c / up_s * 100) if up_s else 0},
            "memory_edits": edits,
            "memory_edits_by_current_holders": holder_edits,
            "memory_edit_rate": round(holder_edits / len(memory_holders), 2) if memory_holders else 0,
            "waitlist": waitlist,
            "signups_by_day": [{"day": d, "count": by_day[d]} for d in sorted(by_day)],
            "quiz": {"users": len(users), "sections": sections, "short": sections["short"]["complete"],
                     "extended": sections["extended"]["halfway"], "further": sections["further"]["halfway"]}}


@api.get("/admin/waitlist")
async def admin_waitlist(response: Response, after: str = Query("", max_length=24),
                         limit: int = Query(25, ge=1, le=100), _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "no-store"
    query = dict(IN_ENV)
    if after:
        if not ObjectId.is_valid(after):
            raise HTTPException(422, "Invalid waitlist cursor.")
        query["_id"] = {"$lt": ObjectId(after)}
    entries, catalog_pages = await asyncio.gather(
        db.waitlist.find(query, {"email": 1, "origin": 1, "ts": 1}).sort("_id", DESCENDING).limit(limit + 1).to_list(limit + 1),
        trends.public_entry_pages(db))
    cursor = str(entries[limit - 1]["_id"]) if len(entries) > limit else None
    return {"entries": [{"email": row["email"], "ts": row.get("ts"), "origin": trends.entry_page(row.get("origin"), catalog_pages)}
                        for row in entries[:limit]], "next": cursor}


@api.post("/admin/announce/preview")
async def announcement_preview(body: announcements.Announcement, response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    return await announcements.preview(db, IN_ENV, body, now())


@api.post("/admin/announce")
async def admin_announce(body: announcements.Announcement, _: dict = Depends(admin_dep)):
    return await announcements.send(db, ENV, body, now(), notify_all)


@api.get("/admin/announcements")
async def admin_announcements(response: Response, _: dict = Depends(admin_dep), limit: int = Query(50, ge=1, le=51),
                              after_time: str = Query("", max_length=50), after_id: str = Query("", max_length=100)):
    response.headers["Cache-Control"] = "no-store"
    return await announcements.history(db, IN_ENV, limit, after_time, after_id)


@api.get("/admin/membership/summary")
async def membership_summary(response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "no-store"
    return await membership_admin.summary(db, ENV, IN_ENV, datetime.now(timezone.utc))


@api.get("/admin/membership/reconciliation")
async def membership_reconciliation(response: Response, month: str | None = Query(None, pattern=r"^(20\d\d)-(0[1-9]|1[0-2])$"),
                                    _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    current = datetime.now(timezone.utc)
    if month and month > current.strftime("%Y-%m"):
        raise HTTPException(422, "Choose the current month or a previous month.")
    return await membership_admin.reconcile(db, ENV, IN_ENV, current, month)


@api.get("/users")
async def list_users(response: Response, admin: dict = Depends(admin_dep), q: str = Query("", max_length=256),
                     after: str = Query("", max_length=256), limit: int = Query(1000, ge=1, le=1000),
                     status: str = Query("", max_length=32)):
    response.headers["Cache-Control"] = "no-store"
    if status and status not in membership_admin.STATUSES:
        raise HTTPException(422, "Unknown membership status.")
    public_fields = ("user_id", "email", "nickname", "role", "origin", "created_at")
    fields = {field: 1 for field in (*public_fields, *BILLING_FIELDS, "plan_offer", "membership_plan_id",
                                     "paid_reply_cycles", "topup_balance", "closing", "replies_paused",
                                     "complimentary_entitlement", "complimentary_reply_totals", "intro_entitlement",
                                     "intro_billing", "intro_reply_totals", "intro_access_paused", "intro_converted_at",
                                     "billing_risk", "topup_payment_tracking", "subscription_allowance_adjustments")}
    query = dict(IN_ENV)
    if after:
        query["user_id"] = {"$gt": after}
    term = q.strip().casefold()
    search_fields = ("email", "user_id", "nickname")
    # Role and subscription values can depend on legacy fields and today's billing cycle.
    # Ordinary email/name searches can be filtered before reading any account details.
    if term and not any(term in value for value in (*ROLES, *membership_admin.STATUSES, "paid", "plus")):
        query["$or"] = [{field: {"$regex": re.escape(term), "$options": "i"}} for field in search_fields]
    users = []
    timestamp = datetime.now(timezone.utc).timestamp()
    catalog_pages = await trends.public_entry_pages(db)
    async for person in db.users.find(query, {"_id": 0, **fields}).sort("user_id", ASCENDING):
        usage = reply_usage(person)
        state = membership_admin.membership_status(person, usage, timestamp)
        if status and state != status:
            continue
        plan = person.get("plan_offer", "standard") if person.get("stripe_subscription_id") else None
        user = {**{field: person.get(field) for field in public_fields}, "tier": billing_tier(person),
                "plan_offer": plan if plan in ("standard", "partner_monthly", "couple_annual", *membership_plans.PLANS) else None,
                "origin": trends.entry_page(person.get("origin"), catalog_pages),
                "role": effective_role(person.get("role")), "membership_status": state, "usage": usage,
                "billing_review": billing_risk.status(person),
                "intro_billing": intro_card.status_fields(person)["billing"] if person.get("intro_billing") else None,
                "intro_selected_plan": ({key: (person["intro_billing"].get("selected_plan") or {}).get(key)
                    for key in ("id", "name", "price_cents", "messages")} if person.get("intro_billing") else None),
                "complimentary_entitlement": person.get("complimentary_entitlement") if complimentary_access(person) else None,
                "replies_paused": person.get("replies_paused") is True and person.get("role") != "admin"}
        values = [user[field] for field in (*search_fields, "role", "tier", "membership_status")]
        if term and not any(term in str(value or "").casefold() for value in [*values, usage["plan"]]):
            continue
        users.append(user)
        if len(users) > limit:
            break
    return {"roles": [r for r in ROLES if RANK[r] <= RANK[admin["role"]]],
            "users": users[:limit], "next_cursor": users[limit - 1]["user_id"] if len(users) > limit else None}


@api.patch("/users/{user_id}/complimentary-access")
async def change_complimentary_access(user_id: str, body: membership_admin.ComplimentaryAccess,
                                      admin: dict = Depends(admin_dep)):
    return await membership_admin.set_complimentary_access(db, dict(IN_ENV), user_id, body, admin["user_id"])


@api.get("/users/{user_id}/billing-records")
async def member_billing_records(user_id: str, response: Response, limit: int = Query(25, ge=1, le=50),
                                 _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    return await billing_adjustments.member_records(db, user_id, ENV, dict(IN_ENV), limit)


@api.get("/admin/membership/adjustment-reviews")
async def billing_adjustment_reviews(response: Response, limit: int = Query(50, ge=1, le=50),
                                      _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "private, no-store"
    return await billing_adjustments.global_reviews(db, ENV, limit)


@api.patch("/users/{user_id}/reply-access")
async def change_reply_access(user_id: str, body: membership_admin.ReplyAccess, admin: dict = Depends(admin_dep)):
    if user_id == admin["user_id"]:
        raise HTTPException(403, "Staff reply access cannot be paused.")
    eligible = {**IN_ENV, "user_id": user_id, "role": {"$ne": "admin"}, "closing": {"$ne": True}}
    if not await membership_admin.set_access_flag(db, eligible, "replies_paused", body.paused,
            "replies_paused" if body.paused else "replies_resumed", admin["user_id"]):
        protected = await db.users.find_one({**IN_ENV, "user_id": user_id}, {"_id": 1})
        if not protected:
            raise HTTPException(404, "Account not found.")
        raise HTTPException(409, "Only an open member account can have its reply access changed.")
    return {"user_id": user_id, "replies_paused": body.paused}


@api.get("/users/{user_id}/actions")
async def member_actions(user_id: str, response: Response, _: dict = Depends(admin_dep)):
    response.headers["Cache-Control"] = "no-store"
    person = await db.users.find_one({**IN_ENV, "user_id": user_id},
        {"_id": 0, "user_id": 1, "admin_member_actions": 1, "reply_access_admin_change": 1, "couples_pilot_admin_change": 1})
    if not person:
        raise HTTPException(404, "Account not found.")
    return {"actions": membership_admin.recent_actions(person), "limit": membership_admin.ACTION_LIMIT}


@api.patch("/users/{user_id}/role")
async def set_role(user_id: str, body: RoleIn, admin: dict = Depends(admin_dep)):
    if body.role not in ROLES or RANK[body.role] > RANK[admin["role"]]:
        raise HTTPException(status_code=403, detail="Not allowed")
    target = await db.users.find_one({"user_id": user_id, **IN_ENV}, {"role": 1, "closing": 1})
    if target is None:
        raise HTTPException(status_code=404, detail="Not found")
    if target.get("closing"):
        raise HTTPException(409, "Account closure is in progress.")
    if target.get("role") == body.role:
        return {"user_id": user_id, "role": body.role}
    current = {"_id": target["_id"], "role": target.get("role"), "closing": {"$ne": True}}
    update = {"$set": {"role": body.role}, "$unset": {"admin_demotion": ""},
              "$push": membership_admin.action_push("made_admin" if body.role == "admin" else "made_member", admin["user_id"])}
    if current["role"] == "admin" and body.role != "admin":
        # Exclude this account as a remaining admin before checking the others.
        # The same database clock determines whether a reservation can be counted or committed.
        token = uuid.uuid4().hex
        until = datetime.now(timezone.utc) + timedelta(minutes=1)
        await db.users.update_one(current, {"$set": {"admin_demotion": {"token": token, "until": until}}})
        reserved = {**current, "admin_demotion.token": token}
        try:
            other = await db.users.find_one({**IN_ENV, "_id": {"$ne": target["_id"]},
                "role": "admin", "closing": {"$ne": True}, "$or": [{"admin_demotion": None},
                {"$expr": {"$lte": ["$admin_demotion.until", "$$NOW"]}}]}, {"_id": 1})
            if not other:
                raise HTTPException(409, "Keep at least one admin")
            saved = await db.users.update_one({**reserved, "$expr": {"$gt": ["$admin_demotion.until", "$$NOW"]}}, update)
        finally:
            await db.users.update_one(reserved, {"$unset": {"admin_demotion": ""}})
    else:
        saved = await db.users.update_one(current, update)
    if not saved.matched_count:
        raise HTTPException(409, "The account role changed. Refresh before trying again.")
    return {"user_id": user_id, "role": body.role}


api.include_router(your_type.make_router(lambda: db, current_member, subscribed_member))
api.include_router(dating.make_router(lambda: db, current_member, admin_dep))
async def refresh_intro_member(member):
    try:
        return await intro_card.refresh(db, member, fulfill=mark_paid)
    except stripe.StripeError:
        return (await db.users.find_one({"user_id": member["user_id"]})) or member


api.include_router(intro_access.make_router(lambda: db, current_member, admin_dep, _clerk_user, refresh_intro_member))
api.include_router(intro_card.make_router(lambda: db, current_member, _clerk_user, checkout_origin, mark_paid))
api.include_router(couples_router(lambda: db, current_member))
api.include_router(couples_compatibility.make_router(lambda: db, current_member, lambda: couples_compatibility.load_quiz(db)))
api.include_router(billing_risk.make_router(lambda: db, admin_dep, IN_ENV))
api.include_router(message_cost_admin.make_router(lambda: db, admin_dep, ENV))
api.include_router(deepseek_connection.router(ENV))
api.include_router(journey_router(lambda: db, current_member, lambda *args: ai_json(*args)))

async def quick_advice_profile(member):
    return saved_profile_text(*await saved_profile(member, (await get_settings())["quiz"]))

api.include_router(quick_advice.make_router(lambda: db, current_member, lambda *args: ai_json(*args), quick_advice_profile))
app.include_router(api)
app.include_router(clerk_router)
app.include_router(support_router(db, admin_dep))

app.add_middleware(DatabaseMaintenance)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=[origin.strip() for origin in os.environ.get(
        "CORS_ORIGINS", "" if os.environ.get("RENDER") else "*"
    ).split(",") if origin.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Imladri-Limit", "Retry-After"],
)


@app.on_event("startup")
async def startup():
    if not startup_allowed():
        return
    await ensure_indexes()
    # Grandfather a previously confirmed pair before admitting its first promotional checkout.
    await db.couples_pairs.update_many({"state": "active", "members.1": {"$exists": True},
        "benefit_members": {"$exists": False}, "self_funded": {"$ne": True}}, [{"$set": {"benefit_members": "$members"}}])
    await seed_base(db)
    await game_plans.repair_deleted_outcomes(db)
    for old, new in RETIRED_ROLES.items():
        await db.users.update_many({"role": old}, {"$set": {"role": new}})
    app.state.closure_recovery = asyncio.create_task(accounts.retry_closures(db))
    app.state.answer_history_recovery = asyncio.create_task(history_lifecycle.recover(db))
    app.state.intro_billing_recovery = asyncio.create_task(intro_card.recover(db, mark_paid))
    if getattr(type(db), "maintain", None) is not None:
        app.state.postgres_maintenance = asyncio.create_task(db.maintain())


@app.on_event("shutdown")
async def shutdown_db_client():
    intro_recovery = getattr(app.state, "intro_billing_recovery", None)
    if intro_recovery:
        intro_recovery.cancel()
        await asyncio.gather(intro_recovery, return_exceptions=True)
    postgres_maintenance = getattr(app.state, "postgres_maintenance", None)
    if postgres_maintenance:
        postgres_maintenance.cancel()
        await asyncio.gather(postgres_maintenance, return_exceptions=True)
    history_recovery = getattr(app.state, "answer_history_recovery", None)
    if history_recovery:
        history_recovery.cancel()
        await asyncio.gather(history_recovery, return_exceptions=True)
    recovery = getattr(app.state, "closure_recovery", None)
    if recovery:
        recovery.cancel()
        await asyncio.gather(recovery, return_exceptions=True)
    if getattr(type(client), "aclose", None) is not None:
        await client.aclose()
    else:
        client.close()
