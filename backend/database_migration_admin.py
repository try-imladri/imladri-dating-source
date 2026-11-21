"""Count-only administration of answer journals and the PostgreSQL shadow.

No connection string, raw answer, demographic value or member identifier is
returned. MongoDB remains authoritative and no cutover operation is exposed.
"""
from datetime import datetime, timezone
import os

from fastapi import HTTPException

from . import answer_history, postgres_migration


SOURCES = postgres_migration.JOURNAL_SOURCES
DOMAINS = ("individual", "couples", "billing", "admin", "research")
HISTORY_DOMAINS = ("everyday", "intimacy", "demographics", "research", "couples", "consent", "assessment_repeat",
                   "wording_feedback", "profile", "person_intake", "your_type")


def array(path):
    return {"$cond": [{"$isArray": "$" + path}, "$" + path, []]}


def backlog_pipeline(source, env):
    stages = [{"$match": {"$or": [{answer_history.PENDING + ".0": {"$exists": True}},
                                      {answer_history.REDACTIONS + ".0": {"$exists": True}}]}}]
    if source in ("users", "research_responses", "couples_pairs"):
        stages.append({"$match": {"env": {"$in": [env, None]}}})
    else:
        stages.extend([
            {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "user_id",
                         "pipeline": [{"$match": {"env": {"$in": [env, None]}}}, {"$project": {"_id": 1}}],
                         "as": "migration_owner"}},
            {"$match": {"migration_owner.0": {"$exists": True}}},
        ])
    return stages


async def backlog(db, env):
    rows = []
    for source in SOURCES:
        stages = [*backlog_pipeline(source, env), {"$group": {"_id": None, "queued_documents": {"$sum": 1},
                  "pending_events": {"$sum": {"$size": array(answer_history.PENDING)}},
                  "redactions_pending": {"$sum": {"$size": array(answer_history.REDACTIONS)}}}}]
        found = await db[source].aggregate(stages).to_list(1)
        counts = found[0] if found else {}
        rows.append({"source": source, **{key: int(counts.get(key, 0))
                     for key in ("queued_documents", "pending_events", "redactions_pending")}})
    return rows


async def status(db, env):
    postgres_migration.validate_name(env)
    reports = await db.database_migration_reports.find(
        {"environment": env, "report_version": postgres_migration.REPORT_VERSION,
         "status": {"$in": ["observed_parity_only", "needs_reconciliation"]}}).sort("completed_at", -1).limit(1).to_list(1)
    latest = postgres_migration.public_report(reports[0]) if reports else None
    source_rows = await backlog(db, env)
    domain = {"$cond": [{"$in": ["$domain", list(HISTORY_DOMAINS)]}, "$domain", "other"]}
    pipeline = [{"$match": {"purged": {"$ne": True}, "consent.env": {"$in": [env, None]}}},
                {"$group": {"_id": domain, "total": {"$sum": 1},
                            "baseline": {"$sum": {"$cond": [{"$eq": ["$baseline", True]}, 1, 0]}}}}]
    history = [{"domain": row["_id"], "baseline": row["baseline"],
                "new_observations": row["total"] - row["baseline"], "total": row["total"]}
               async for row in db.answer_history.aggregate(pipeline)]
    postgres_primary = getattr(type(db), "backend", None) == "postgresql"
    configured = postgres_primary or bool(os.environ.get("IMLADRI_POSTGRES_SHADOW_DSN", "").strip())
    latest_repair = await db.database_migration_reports.find_one({"_id": "answer-history-repair:" + env})
    repair = ({key: latest_repair[key] for key in ("at", "attempted", "archived", "redacted", "failed") if key in latest_repair}
              if latest_repair else None)
    return {"version": 1, "mode": "postgres_authoritative" if postgres_primary else "mongo_authoritative",
            "postgres": {"configured": configured, "connected": postgres_primary,
                         "connection_verification": "current_repository_queries" if postgres_primary else "not_checked_in_request",
                         "cutover_eligible": False},
            "latest_report": latest,
            "domains": {key: ((latest or {}).get("domains") or {}).get(key, {
                "source_records": None, "target_records": None, "parity": None}) for key in DOMAINS},
            "history": {"domains": sorted(history, key=lambda row: row["domain"]), "sources": source_rows,
                        "pending_events": sum(row["pending_events"] for row in source_rows),
                        "redactions_pending": sum(row["redactions_pending"] for row in source_rows),
                        "database_tombstones": await db.answer_history.count_documents({"purged": True}),
                        "tombstone_scope": "database-wide opaque erasure fences have no environment or owner"},
            "latest_repair": repair,
            "remaining_gates": [] if postgres_primary else ["MongoDB remains authoritative.",
                                "A consistent snapshot/change-stream handoff and tested ongoing capture are required.",
                                "Individual, Couples, billing, admin, export and erasure parity must pass before an authorized cutover."]}


async def repair_history(db, env, limit=100):
    postgres_migration.validate_name(env)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise HTTPException(422, "Choose a cleanup batch between 1 and 100 source records.")
    result = {"at": datetime.now(timezone.utc).isoformat(), "attempted": 0, "archived": 0, "redacted": 0, "failed": 0}
    async def record():
        try:
            await db.database_migration_reports.replace_one({"_id": "answer-history-repair:" + env},
                {"_id": "answer-history-repair:" + env, "environment": env, **result}, upsert=True)
        except Exception:
            return False
        return True
    for source in SOURCES:
        remaining = limit - result["attempted"]
        if remaining <= 0:
            break
        cursor = db[source].aggregate([*backlog_pipeline(source, env), {"$limit": remaining}, {"$project": {"_id": 1}}])
        async for row in cursor:
            result["attempted"] += 1
            try:
                fixed = await answer_history.flush(db, source, {"_id": row["_id"]})
            except Exception:
                fixed = {"failed": True}
            result["archived"] += int(fixed.get("archived", 0))
            result["redacted"] += int(fixed.get("redacted", 0))
            if fixed.get("failed"):
                result["failed"] += 1
                await record()
                raise HTTPException(503, {"message": "Answer-history cleanup is still pending. Retry shortly.", "counts": result},
                                    headers={"Retry-After": "5"}) from None
    if not await record():
        raise HTTPException(503, "Cleanup status could not be recorded. Retry shortly.", headers={"Retry-After": "5"}) from None
    result["remaining_sources"] = await backlog(db, env)
    return result
