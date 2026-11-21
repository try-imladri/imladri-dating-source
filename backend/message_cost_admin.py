"""Admin-only reconciliation of allowance-funded DeepSeek work with closed provider statements."""
import asyncio
import os
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pymongo.errors import DuplicateKeyError

try:
    from . import deepseek_statements as statements, message_charge, message_costs
    from .message_cost_receipts import make_receipts_router
except ImportError:
    import deepseek_statements as statements
    import message_charge
    import message_costs
    from message_cost_receipts import make_receipts_router

UTC = timezone.utc
SPLITS = ("cache_hit_tokens", "cache_miss_tokens", "output_tokens")
CALL_FIELDS = {k: 1 for k in ("message_id", "preparation_id", "message_run", "environment", "key_id", "dedicated_key", "billing_hour_certain",
                              "mode", "model", "billing_at", "retry", "status", *SPLITS, "low_nusd", "high_nusd")}


def key_config(env):
    fingerprint = message_costs.key_id()
    label = os.getenv("DEEPSEEK_PRODUCTION_KEY_NAME", "").strip()
    expected = os.getenv("DEEPSEEK_PRODUCTION_KEY_ID", "").strip()
    return fingerprint, label, bool(env == "live" and label and expected and fingerprint == expected)


async def import_days(db, env, key, records):
    # A daily export and 24 hourly buckets describe the same spend. Store the total once.
    # Keep the highest resolution. A conflicting re-upload never overwrites accepted hours.
    # Preflight the whole file so ordinary conflicts cause no partial writes. Each
    # individual day is atomic and safely retryable if another admin races the import.
    ids = [f"{env}:{key}:{day}" for day in records]
    async for old in db.ai_billing_days.find({"_id": {"$in": ids}}):
        rec = records[old["day"]]
        if old["totals"] != rec["totals"] or (rec["hours"] and old.get("hours", rec["hours"]) != rec["hours"]):
            raise HTTPException(409, f"The stored statement conflicts with the uploaded totals for {old['day']}.")
    accepted = 0
    for day, rec in sorted(records.items()):
        ident = f"{env}:{key}:{day}"
        doc = {"day": day, "key_id": key, "env": env, "totals": rec["totals"]}
        match = {"_id": ident, "$or": [{"totals": rec["totals"]}, {"totals": {"$exists": False}}]}
        patch = {"$setOnInsert": doc}
        if rec["hours"]:
            match["$and"] = [{"$or": [{"hours": rec["hours"]}, {"hours": {"$exists": False}}]}]
            patch["$set"] = {"hours": rec["hours"]}
        try:
            change = await db.ai_billing_days.update_one(match, patch, upsert=True)
        except DuplicateKeyError:
            # BSON document equality includes field order. Compare canonical data in Python too.
            old = await db.ai_billing_days.find_one({"_id": ident})
            if not old or old["totals"] != doc["totals"] or (rec["hours"] and old.get("hours", rec["hours"]) != rec["hours"]):
                raise HTTPException(409, f"A concurrent statement conflicts on {day}. Updated {accepted} earlier days.")
            if rec["hours"] and "hours" not in old:
                if (await db.ai_billing_days.update_one({"_id": ident, "hours": {"$exists": False}},
                                                        {"$set": {"hours": rec["hours"]}})).modified_count:
                    accepted += 1
                else:
                    reread = await db.ai_billing_days.find_one({"_id": ident}, {"hours": 1})
                    if reread["hours"] != rec["hours"]:
                        raise HTTPException(409, f"A concurrent hourly export conflicts on {day}. Updated {accepted} earlier days.")
            continue
        accepted += int(bool(change.modified_count or change.upserted_id))
    return {"days": len(records), "changed": accepted, "start": min(records), "end": max(records)}


def bucket(stamp):
    # PyMongo returns naive datetimes for UTC unless the client requests tz-aware BSON.
    return stamp.strftime("%Y-%m-%d"), stamp.strftime("%H")


def split_known(row):
    return all(isinstance(row.get(field), int) and not isinstance(row.get(field), bool) and row[field] >= 0 for field in SPLITS)


def compare(statement, observed):
    if not statement:
        return "missing", {}
    hourly = bool(statement.get("hours"))
    bill = statement["hours"] if hourly else {"day": statement["totals"]}
    actual = defaultdict(Counter)
    for (hour, model), counts in observed.items():
        actual[(hour if hourly else "day", model)].update(counts)
    rates = {}
    for slot, models in bill.items():
        for model, row in models.items():
            group = (slot, model)
            counts = actual.pop(group, Counter())
            if counts["attempts"] != row["requests"] or counts["unknown"]:
                return "mismatch", {}
            prices = []
            ambiguous = False
            for field, kind in zip(SPLITS, statements.TOKEN_TYPES):
                unit_prices = row["prices"][kind]
                if counts[field] != sum(unit_prices.values()):
                    return "mismatch", {}
                priced = [int(p) for p, qty in unit_prices.items() if qty]
                ambiguous |= len(priced) > 1
                prices.append(priced[0] if priced else 0)
            rates[group] = None if ambiguous else prices
    if actual:
        return "mismatch", {}
    if any(value is None for value in rates.values()):
        return "mixed_rates", {}
    return "matched", rates


def totals():
    return {"messages": 0, "calls": 0, "retries": 0, "customer_retry_calls": 0, "background": 0, "preparation": 0,
            "pending_background": 0, "credits_without_calls": 0, "priced": 0, "low_nusd": 0,
            "high_nusd": 0, "reconciled_calls": 0, "reconciled_nusd": 0}


def ratio(amount, messages):
    return amount * 100 / messages / 1_000_000_000 if messages else None


def finish(row):
    complete = (row["messages"] > 0 and row["calls"] == row["reconciled_calls"] and
                not row["credits_without_calls"] and not row["pending_background"])
    estimated = (row["messages"] > 0 and row["calls"] == row["priced"] and
                 not row["credits_without_calls"] and not row["pending_background"])
    return {**row, "actual_per_100_usd": ratio(row["reconciled_nusd"], row["messages"]) if complete else None,
            "estimated_per_100_usd": [ratio(row[k], row["messages"]) for k in ("low_nusd", "high_nusd")] if estimated else None}


def public_day(day, coverage):
    return {"day": day, **{k: v for k,v in coverage.items() if k not in ("rates", "hourly")}}


async def day_coverages(db, env, key, dedicated, days):
    start = datetime.combine(date.fromisoformat(min(days)), time.min, UTC)
    end = datetime.combine(date.fromisoformat(max(days)) + timedelta(days=1), time.min, UTC)
    stored = {}
    if dedicated:
        async for doc in db.ai_billing_days.find({"_id": {"$in": [f"{env}:{key}:{day}" for day in days]}}):
            stored[doc["day"]] = doc
    # Only grouped hourly model, work and token counts leave Mongo. A sentinel prevents
    # a missing preparation reference from joining to every unit without a preparation.
    complete = {"$and": [{"$in": [{"$type": "$"+f}, ["int", "long"]]} for f in SPLITS]}
    known = {"$and": [complete, {"$eq": ["$dedicated_key", True]}, {"$eq": ["$billing_hour_certain", True]}]}
    group = {"_id": {"hour": {"$dateToString": {"date": "$billing_at", "format": "%Y-%m-%dT%H"}},
                     "model": "$model", "work": "$cost_work"},
             "attempts": {"$sum": 1}, "unknown": {"$sum": {"$cond": [known, 0, 1]}},
             **{f: {"$sum": "$"+f} for f in (*SPLITS,"low_nusd","high_nusd")},
             "priced": {"$sum": {"$cond": [{"$in": [{"$type":"$low_nusd"}, ["int", "long"]]}, 1, 0]}}}
    match = {"environment": env, "key_id": key, "billing_at": {"$gte": start, "$lt": end}}
    pipeline = [{"$match": match}, {"$set": {"cost_link": {"$ifNull": ["$message_id", "!"]},
                    "cost_upload": {"$cond": [{"$ifNull": ["$message_id", False]}, "!", {"$ifNull": ["$preparation_id", "!"]}]}}},
                {"$lookup": {"from": "message_cost_units", "localField": "cost_link", "foreignField": "_id",
                             "as": "linked", "pipeline": [{"$match": {"env": env}}, {"$project": {"status": 1}}]}},
                {"$lookup": {"from": "message_cost_units", "localField": "cost_upload", "foreignField": "preparation_id",
                             "as": "uploaded", "pipeline": [{"$match": {"env": env}}, {"$project": {"status": 1}}]}},
                {"$set": {"cost_work": {"$ifNull": [{"$arrayElemAt": ["$linked.status", 0]},
                    {"$arrayElemAt": ["$uploaded.status", 0]},
                    {"$switch": {"branches": [{"case": {"$ne": ["$cost_link", "!"]}, "then": "unassigned"},
                                              {"case": {"$ne": ["$cost_upload", "!"]}, "then": "unclaimed_upload"}],
                                 "default": "unlinked"}}]}}},
                {"$group": group}]
    observed = defaultdict(lambda: defaultdict(Counter))
    work = defaultdict(lambda: defaultdict(Counter))
    async for row in db.ai_call_ledger.aggregate(pipeline, allowDiskUse=True):
        stamp = row.pop("_id")
        day, hour = stamp["hour"].split("T")
        model = statements.model_name(stamp.get("model"))
        observed[day][hour,model].update(row)
        work[day][hour,model,stamp["work"]].update(row)
    result = {}
    for day in days:
        statement = stored.get(day)
        status, rates = compare(statement, observed[day])
        total = sum(observed[day].values(), Counter())
        provider = list(statement["totals"].values()) if statement else []
        by_work = defaultdict(Counter)
        hourly = bool(statement and statement.get("hours"))
        for (hour, model, kind), summary in work[day].items():
            line = by_work[kind]
            line.update({k: summary[k] for k in ("attempts","priced","low_nusd","high_nusd")})
            if status == "matched":
                prices = rates[(hour if hourly else "day", model)]
                line["billed_nusd"] += sum(summary[f]*p for f,p in zip(SPLITS, prices))
        result[day] = {"status": status, "calls": total["attempts"], "provider_requests": sum(r["requests"] for r in provider) if statement else None,
                       "billed_nusd": sum(r["billed"] for r in provider) if statement else None,
                       "paid_nusd": sum(r["paid"] for r in provider) if statement else None, "rates": rates, "hourly": hourly,
                       "unlinked": by_work.get("unlinked", {}).get("attempts", 0), "missing_split": total["unknown"],
                       "work": {k: dict(v) for k,v in by_work.items()}}
    return result


async def report(db, env, days, end_date=None):
    today = datetime.now(UTC).date()
    try:
        end = date.fromisoformat(end_date) if end_date else today
    except ValueError:
        raise HTTPException(422, "Enter a UTC calendar date.")
    if days not in (1, 7, 30) or end > today or end.year < 2000:
        raise HTTPException(422, "Choose 1, 7 or 30 days ending on or before today.")
    start = end - timedelta(days=days-1)
    start_dt = datetime.combine(start, time.min, UTC)
    end_dt = datetime.combine(end+timedelta(days=1), time.min, UTC)
    key, label, dedicated = key_config(env)
    await message_charge.flush_message_cost_outbox(db, env)
    cohorts = {name: totals() for name in ("individual", "couples")}
    period_days = [(start+timedelta(days=i)).isoformat() for i in range(days)]
    coverage = await day_coverages(db, env, key, dedicated, period_days)

    async def load(day):
        if day not in coverage:
            coverage.update(await day_coverages(db, env, key, dedicated, [day]))
        return coverage[day]

    async def account(batch):
        owners = {unit["_id"]:unit["workspace"] for unit in batch}
        uploads = {unit["preparation_id"]:unit["_id"] for unit in batch if unit.get("preparation_id")}
        related = [{"message_id": {"$in": list(owners)}}]
        if uploads:
            related.append({"message_id": None, "preparation_id": {"$in": list(uploads)}})
        found = set()
        async for attempt in db.ai_call_ledger.find({"environment": env, "$or": related}, CALL_FIELDS):
            linked = attempt.get("message_id") or uploads[attempt["preparation_id"]]
            row = cohorts[owners[linked]]
            if attempt.get("message_id") == linked and attempt.get("mode") == "metered":
                found.add(linked)
            row["calls"] += 1
            row["retries"] += int(bool(attempt.get("retry")))
            run = attempt.get("message_run")
            row["customer_retry_calls"] += int(isinstance(run, int) and run >= 2)
            row["background"] += int(attempt.get("mode") == "background")
            row["preparation"] += int(not attempt.get("message_id"))
            if all(isinstance(attempt.get(k), int) for k in ("low_nusd", "high_nusd")):
                row["priced"] += 1
                for k in ("low_nusd", "high_nusd"):
                    row[k] += attempt[k]
            if (not dedicated or attempt.get("key_id") != key or not attempt.get("dedicated_key") or attempt.get("billing_hour_certain") is not True
                    or not split_known(attempt) or not isinstance(attempt.get("billing_at"), datetime)):
                continue
            day, hour = bucket(attempt["billing_at"])
            period = await load(day)
            rates = period["rates"].get((hour if period["hourly"] else "day", statements.model_name(attempt.get("model"))))
            if rates is not None and period["status"] == "matched":
                row["reconciled_calls"] += 1
                row["reconciled_nusd"] += sum(attempt[f]*price for f,price in zip(SPLITS,rates))
        for unit in batch:
            row = cohorts[unit["workspace"]]
            row["messages"] += 1
            row["pending_background"] += max(0, unit.get("background_pending", 0))
            row["credits_without_calls"] += int(unit["_id"] not in found)

    units = {"env": env, "key_id": key, "status": "settled", "workspace": {"$in": list(cohorts)},
             "settled_at": {"$gte": start_dt, "$lt": end_dt}}
    batch = []
    async for unit in db.message_cost_units.find(units, {"workspace": 1, "preparation_id": 1, "background_pending": 1}):
        batch.append(unit)
        if len(batch) == 250:
            await account(batch)
            batch = []
    if batch:
        await account(batch)
    for day in period_days:
        await load(day)
    all_rows = totals()
    for row in cohorts.values():
        for k in all_rows:
            all_rows[k] += row[k]
    full_period = all(coverage[d]["status"] in ("matched", "mixed_rates") for d in period_days)
    calendar_spend = sum(coverage[d]["billed_nusd"] or 0 for d in period_days)
    return {"start": start.isoformat(), "end": end.isoformat(), "dedicated": dedicated,
            "keywide_per_100_usd": ratio(calendar_spend, all_rows["messages"]) if full_period else None,
            "key_name": label if dedicated else None, "environment": env,
            "cohorts": {k: finish(v) for k,v in {**cohorts, "all": all_rows}.items()},
            "coverage": [public_day(day, value) for day,value in sorted(coverage.items())]}


def make_router(get_db, admin_dep, env):
    router = APIRouter(prefix="/admin/prompts/message-costs", dependencies=[Depends(admin_dep)])
    router.include_router(make_receipts_router(get_db, env))

    @router.get("")
    async def get_costs(response: Response, days: int=7, end_date: str | None=None):
        response.headers["Cache-Control"] = "no-store"
        return await report(get_db(), env, days, end_date)

    @router.post("/statement")
    async def import_statement(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        key, label, dedicated = key_config(env)
        if not dedicated:
            raise HTTPException(409, "The dedicated production key must be active to import its statement.")
        if request.headers.get("content-type", "").split(";")[0] != "application/zip":
            raise HTTPException(415, "Choose a DeepSeek Usage ZIP.")
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > statements.MAX_UPLOAD:
                raise HTTPException(413, "Choose a DeepSeek Usage ZIP smaller than 2 MB.")
            payload.extend(chunk)
        rows = await asyncio.to_thread(statements.parse_statement, payload, label, os.environ.get("DEEPSEEK_API_KEY", "").strip())
        return await import_days(get_db(), env, key, rows)

    return router
