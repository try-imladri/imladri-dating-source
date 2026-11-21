"""Inventory or reconcile an explicitly configured PostgreSQL shadow.

No production switch, payment call, login, email or restore operation is present.
Environment files are never loaded implicitly. All displayed results are counts,
fingerprints and migration metadata, never source records or credentials.
"""
import argparse
import asyncio
import ipaddress
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import postgres_migration as migration


def loopback_mongo(uri):
    parsed = urlsplit(uri)
    if parsed.scheme != "mongodb" or not parsed.hostname or "," in parsed.netloc:
        return False
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return parsed.hostname == "localhost"


async def run(args):
    from motor.motor_asyncio import AsyncIOMotorClient
    source_uri = os.environ.get(args.mongo_url_env)
    database = os.environ.get(args.mongo_db_env)
    target_uri = os.environ.get(args.postgres_url_env)
    if not source_uri or not database:
        raise ValueError("An explicit source MongoDB configuration is required.")
    if args.allow_local_plaintext and not loopback_mongo(source_uri):
        raise ValueError("Plaintext fixture targets require a loopback source fixture too.")
    migration.validate_name(args.namespace)
    migration.validate_name(args.environment)
    client = AsyncIOMotorClient(source_uri, serverSelectionTimeoutMS=15000, appname="imladri-postgres-shadow-readonly")
    destination = None
    try:
        db = client[database]
        names = sorted(n for n in await db.list_collection_names() if not n.startswith("system."))
        if not args.apply:
            return {"mode": "dry_run_inventory", "mongo_authoritative": True, "cutover_eligible": False,
                    "postgres_configured": bool(target_uri), "postgres_connection_verified": False,
                    "collections": [{"name": n, "domain": migration.domain_for_collection(n),
                                     "excluded_control": n in migration.EXCLUDED_COLLECTIONS} for n in names],
                    "source_write_operations": 0, "destination_write_operations": 0}
        if not target_uri:
            raise ValueError("An explicit PostgreSQL shadow DSN is required for apply.")
        destination = migration.PostgresShadow.connect(target_uri, allow_local_plaintext=args.allow_local_plaintext)
        destination.initialize()
        report = await migration.reconcile(db, destination, namespace=args.namespace, environment=args.environment)
        if args.publish_report:
            report["report_publication"] = await migration.publish_report(db, report)
        return report
    finally:
        if destination:
            destination.close()
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True, help="Stable logical source database identifier; never a DSN.")
    parser.add_argument("--environment", required=True)
    parser.add_argument("--mongo-url-env", default="MONGO_URL")
    parser.add_argument("--mongo-db-env", default="DB_NAME")
    parser.add_argument("--postgres-url-env", default="IMLADRI_POSTGRES_SHADOW_DSN")
    parser.add_argument("--apply", action="store_true", help="Write the fixed PostgreSQL shadow schema and mirror.")
    parser.add_argument("--publish-report", action="store_true", help="Explicitly publish count-only status to MongoDB control metadata.")
    parser.add_argument("--allow-local-plaintext", action="store_true", help="Allow only loopback PostgreSQL and MongoDB fixtures without TLS.")
    args = parser.parse_args()
    if args.publish_report and not args.apply:
        parser.error("--publish-report requires --apply")
    try:
        result = asyncio.run(run(args))
    except Exception as error:
        # Driver exception text can include connection strings or SQL values.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__, "mongo_authoritative": True,
                          "cutover_eligible": False, "credentials_or_source_records_printed": False}))
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2))
    if result.get("status") == "needs_reconciliation":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
