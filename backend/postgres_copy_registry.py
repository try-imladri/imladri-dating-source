"""Fixed, reviewed application scopes for native datastore conversion only.

An operator selects an application name, never an arbitrary collection allowlist.
Historical whole-database archives use their separate lossless archive path.
"""
from .postgres_migration import COLLECTION_DOMAINS


PERSONAL_COLLECTIONS = frozenset(COLLECTION_DOMAINS)
MARKET_COLLECTIONS = frozenset({
    "admin_2fa", "admin_audit", "ai_messages", "ai_usage", "blocks", "clerk_email_events",
    "conversations", "cron_runs", "deleted_users", "email_log", "error_logs", "files",
    "jobs", "listing_views", "listings", "messages", "missed_picks", "missions",
    "notifications", "price_checks", "reports", "requests", "reviews", "saved",
    "saved_searches", "search_history", "settings", "users", "worker_heartbeats",
    # Content-free majority-write barrier metadata, created only by the operator.
    "database_migration_reports",
})


def application_collections(application):
    if application == "personal":
        return PERSONAL_COLLECTIONS
    if application == "market":
        return MARKET_COLLECTIONS
    raise ValueError("Select an explicitly reviewed application copy scope.")
