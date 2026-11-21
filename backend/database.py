"""Explicit application datastore selection; never silently fall back on failure."""
import os


def open_database():
    mode = os.environ.get("IMLADRI_DATABASE_BACKEND", "mongodb")
    if mode in ("postgresql", "render_gateway"):
        from .database_maintenance import paused
        if not paused():
            temporary = ("MONGO_URL", "IMLADRI_POSTGRES_SHADOW_DSN", "IMLADRI_MIGRATION_SOURCES_JSON",
                         "IMLADRI_MIGRATION_ROLE_DSNS_JSON", "IMLADRI_DATABASE_OPERATOR_SECRET",
                         "ATLAS_NETWORK_CLIENT_ID", "ATLAS_NETWORK_CLIENT_SECRET")
            if any(os.environ.get(key) for key in temporary):
                raise ValueError("Remove source and maintenance credentials before reopening PostgreSQL application traffic.")
    if mode == "mongodb":
        # Temporary rollback/source path while the PostgreSQL cutover is tested.
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        return client, client[os.environ["DB_NAME"]]
    if mode == "postgresql":
        from .postgres_store import PostgresDatabase
        namespace = os.environ["IMLADRI_POSTGRES_NAMESPACE"]
        # Application-only optimization. New migration/parity/diagnostic stores
        # retain the constructor's zero-cache default and read actual BSON.
        options = {} if paused() else {"public_catalog_cache_bytes": 32 * 1024 * 1024}
        store = PostgresDatabase(os.environ["IMLADRI_POSTGRES_DSN"], namespace, **options)
        return store, store
    if mode == "render_gateway":
        from .postgres_remote import RemotePostgresDatabase
        store = RemotePostgresDatabase.from_environment()
        return store, store
    raise ValueError("Unknown application database backend.")
