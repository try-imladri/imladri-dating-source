"""Bounded Render-only PostgreSQL shadow operator; MongoDB remains live.

Invoke directly with Python. No shell, pip, datasource switch, payment, mail or
identity operation is available. Environment files and driver error text are
never loaded or printed. Driver packages are imported only in the child job.
"""
import argparse
import asyncio
from contextlib import aclosing
import hashlib
import importlib.metadata
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = Path(__file__).resolve().parent
MAX_SECONDS = 600
MAX_PREFLIGHT_BYTES = 32 * 1024 * 1024
MAX_SOURCE_BYTES = 96 * 1024 * 1024
MAX_TARGET_PAYLOAD_BYTES = 512 * 1024 * 1024
PACKAGES = {"pg8000": "1.31.6", "scramp": "1.5.0", "asn1crypto": "1.5.1"}
SOURCE_KEYS = ("MONGO_URL", "DB_NAME", "IMLADRI_POSTGRES_SHADOW_DSN")
CHILD_BOOTSTRAP = "import runpy,sys; m=runpy.run_path(sys.argv[1],run_name='imladri_operator_child'); raise SystemExit(m['child_main'](sys.argv[2]))"


class BudgetExceeded(Exception):
    def __init__(self, stage, budget):
        super().__init__("Operator byte budget exceeded")
        self.stage, self.safe_budget = stage, dict(budget)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("Invalid operator arguments")


def parser():
    result = SafeParser(description=__doc__)
    result.add_argument("--namespace")
    result.add_argument("--environment")
    result.add_argument("--apply", action="store_true")
    result.add_argument("--publish-report", action="store_true")
    result.add_argument("--check-bundle", action="store_true", help="Verify kernel hardening and bundle without networking.")
    result.add_argument("--timeout-seconds", type=int, default=MAX_SECONDS)
    result.add_argument("--preflight-source-bytes", type=int, default=MAX_PREFLIGHT_BYTES)
    result.add_argument("--max-source-bytes", type=int, default=MAX_SOURCE_BYTES)
    result.add_argument("--max-target-payload-bytes", type=int, default=MAX_TARGET_PAYLOAD_BYTES)
    result.add_argument("--allow-local-plaintext", action="store_true", help="Loopback MongoDB AND PostgreSQL fixtures only.")
    return result


def validate_args(args):
    for value, maximum in ((args.timeout_seconds, MAX_SECONDS), (args.preflight_source_bytes, MAX_PREFLIGHT_BYTES),
                           (args.max_source_bytes, MAX_SOURCE_BYTES), (args.max_target_payload_bytes, MAX_TARGET_PAYLOAD_BYTES)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError("Operator limits must be bounded positive integers")
    if not args.check_bundle and any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}", value)
                                     for value in (args.namespace, args.environment)):
        raise ValueError("An explicit bounded namespace and environment are required")
    if args.publish_report and not args.apply:
        raise ValueError("Report publication requires apply")
    if args.check_bundle and (args.apply or args.publish_report):
        raise ValueError("Bundle inspection cannot apply a migration")
    return args


def harden():
    spec = importlib.util.spec_from_file_location("imladri_operator_hardening", ROOT / "entrypoint.py")
    if not spec or not spec.loader:
        raise RuntimeError("Reviewed process hardening module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.harden_process()


def bundle_proof():
    archive_path, sbom_path = BUNDLE / "drivers.zip", BUNDLE / "sbom.json"
    sbom = json.loads(sbom_path.read_bytes())
    if sbom.get("version") != 1 or sbom.get("native_or_compiled_files") != 0:
        raise ValueError("Unexpected operator bundle inventory")
    distributions = sbom.get("python_distributions") or []
    if len(distributions) != 3 or {row.get("name"): row.get("version") for row in distributions} != PACKAGES:
        raise ValueError("Unexpected operator driver distributions")
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != sbom.get("zip_sha256"):
        raise ValueError("Operator ZIP does not match its reviewed SBOM")
    expected = {row["path"]: row for row in sbom["files"]}
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError("Operator ZIP contents differ from its SBOM")
        for name in names:
            if (name.startswith("/") or ".." in Path(name).parts or "\\" in name or ":" in name
                    or "__pycache__" in name or Path(name).suffix.lower() in {".so", ".pyd", ".dll", ".dylib", ".exe", ".a", ".o", ".pyc", ".pyo"}):
                raise ValueError("Compiled or unsafe operator content")
            data = archive.read(name)
            if len(data) != expected[name]["bytes"] or hashlib.sha256(data).hexdigest() != expected[name]["sha256"]:
                raise ValueError("Operator file differs from its reviewed SBOM")
    return {"zip_sha256": sbom["zip_sha256"], "operator_python_distributions": distributions,
            "operator_distribution_count": 3, "native_or_compiled_files": 0,
            "main_python_distribution_count": len(list(importlib.metadata.distributions())),
            "driver_zip_on_main_python_path": str(archive_path) in sys.path}


def loopback(url, scheme):
    parsed = urlsplit(url)
    if parsed.scheme not in scheme or not parsed.hostname or "," in parsed.netloc:
        return False
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return parsed.hostname == "localhost"


def validate_connections(args, migration):
    mongo = os.environ.get("MONGO_URL", "")
    database = os.environ.get("DB_NAME", "")
    postgres = os.environ.get("IMLADRI_POSTGRES_SHADOW_DSN", "")
    if not mongo or not database or (args.apply and not postgres):
        raise ValueError("Explicit source and target configuration is required")
    query = {key.lower(): values[-1].lower() for key, values in parse_qs(urlsplit(mongo).query).items()}
    if any(query.get(key) == "true" for key in ("tlsinsecure", "tlsallowinvalidcertificates", "tlsallowinvalidhostnames")):
        raise ValueError("Source TLS verification cannot be disabled")
    local = loopback(mongo, {"mongodb"})
    if args.allow_local_plaintext:
        if not local or not loopback(postgres, {"postgres", "postgresql"}):
            raise ValueError("Plaintext requires two explicit loopback fixtures")
    elif local or (urlsplit(mongo).scheme != "mongodb+srv" and query.get("tls") != "true") or query.get("tls") == "false":
        raise ValueError("The external source requires verified TLS")
    if postgres:
        migration.connection_options(postgres, allow_local_plaintext=args.allow_local_plaintext)
    migration.validate_name(database)


def payload_bytes(value):
    # Conservative application-payload accounting; protocol/TLS overhead and
    # server responses are deliberately not represented as an exact wire cost.
    if isinstance(value, (bytes, bytearray, memoryview)):
        return 2 * len(value) + 16
    if isinstance(value, (list, tuple)):
        return sum(payload_bytes(item) for item in value) + 16
    if value is None:
        return 16
    return 2 * len(str(value).encode("utf8")) + 16


async def execute_child(args):
    from motor.motor_asyncio import AsyncIOMotorClient
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("imladri_fixed_migration_cli", BUNDLE / "migrate_postgres.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    migration = module.migration
    validate_connections(args, migration)
    budget = {"preflight_source_bytes": None, "source_bson_bytes": 0, "target_payload_bytes": 0,
              "max_preflight_source_bytes": args.preflight_source_bytes, "max_source_bson_bytes": args.max_source_bytes,
              "max_target_payload_bytes": args.max_target_payload_bytes, "exact_wire_bandwidth_measured": False}
    source = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=15000, connectTimeoutMS=10000,
                               appname="imladri-postgres-operator-preflight")
    try:
        stats = await source[os.environ["DB_NAME"]].command("dbStats", scale=1)
        size = stats.get("dataSize")
        if not isinstance(size, (int, float)) or isinstance(size, bool) or size < 0:
            raise ValueError("Source size preflight is unavailable")
        budget["preflight_source_bytes"] = int(size)
        if size > args.preflight_source_bytes:
            raise BudgetExceeded("preflight_source_size", budget)
    finally:
        source.close()
    original_rows, original_execute = migration.source_rows, migration.PostgresShadow.execute

    async def bounded_rows(db, name):
        async with aclosing(original_rows(db, name)) as cursor:
            async for row in cursor:
                budget["source_bson_bytes"] += len(migration.raw_document(row))
                if budget["source_bson_bytes"] > args.max_source_bytes:
                    raise BudgetExceeded("aggregate_source_scan", budget)
                yield row

    def bounded_execute(target, sql, params=()):
        budget["target_payload_bytes"] += len(sql.encode("utf8")) + payload_bytes(params) + 64
        if budget["target_payload_bytes"] > args.max_target_payload_bytes:
            raise BudgetExceeded("target_payload", budget)
        return original_execute(target, sql, params)

    migration.source_rows, migration.PostgresShadow.execute = bounded_rows, bounded_execute
    cli = SimpleNamespace(namespace=args.namespace, environment=args.environment, apply=args.apply,
        publish_report=args.publish_report, allow_local_plaintext=args.allow_local_plaintext,
        mongo_url_env="MONGO_URL", mongo_db_env="DB_NAME", postgres_url_env="IMLADRI_POSTGRES_SHADOW_DSN")
    try:
        result = await module.run(cli)
        safe = migration.public_report(result) if args.apply else {key: result[key] for key in (
            "mode", "mongo_authoritative", "cutover_eligible", "postgres_configured", "postgres_connection_verified",
            "collections", "source_write_operations", "destination_write_operations")}
        return {"status": result.get("status", "dry_run_inventory"), "migration": safe, "budget": budget,
                "report_publication": {"requested": bool(args.publish_report),
                    "published": (result.get("report_publication") or {}).get("published") is True,
                    "application_data_mutated": False}}
    finally:
        migration.source_rows, migration.PostgresShadow.execute = original_rows, original_execute


def child_main(serialized):
    security = None
    try:
        security = harden()
        args = validate_args(SimpleNamespace(**json.loads(serialized)))
        result = asyncio.run(execute_child(args))
        print(json.dumps({**result, "security": security, "mongo_authoritative": True, "cutover_eligible": False,
                          "credentials_or_source_records_printed": False}))
        return 2 if result["status"] == "needs_reconciliation" else 0
    except BaseException as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__, "security": security,
                          "mongo_authoritative": True, "cutover_eligible": False, "credentials_or_source_records_printed": False,
                          **({"budget_stage": error.stage, "budget": error.safe_budget} if isinstance(error, BudgetExceeded) else {})}))
        return 1


def child_environment():
    return {**{name: os.environ[name] for name in SOURCE_KEYS if name in os.environ},
            "PATH": "/opt/venv/bin:/usr/bin", "PYTHONPATH": str(BUNDLE / "drivers.zip") + os.pathsep + str(ROOT),
            "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}


def kill_child(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.communicate(timeout=5)


def run(args):
    started = time.monotonic()
    security = harden()  # Kernel-enforced postconditions precede any possible network.
    validate_args(args)
    bundle = bundle_proof()
    if bundle["driver_zip_on_main_python_path"]:
        raise ValueError("Driver bundle must stay isolated from the parent runtime")
    if args.check_bundle:
        return 0, {"status": "bundle_verified", "security": security, "bundle": bundle,
                   "network_calls": 0, "mongo_authoritative": True, "cutover_eligible": False}
    remaining = args.timeout_seconds - (time.monotonic() - started)
    if remaining <= 0:
        return 124, {"status": "timed_out", "timeout_seconds": args.timeout_seconds, "security": security}
    process = subprocess.Popen([sys.executable, "-c", CHILD_BOOTSTRAP, str(Path(__file__).resolve()), json.dumps(vars(args))],
        cwd=ROOT, env=child_environment(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf8", start_new_session=True)
    try:
        output, _ = process.communicate(timeout=remaining)
        if len(output.encode("utf8")) > 256 * 1024:
            raise ValueError("Unexpectedly large operator status")
        result = json.loads(output)
        if not isinstance(result, dict) or result.get("mongo_authoritative") is not True or result.get("cutover_eligible") is not False:
            raise ValueError("Unexpected operator status")
        return process.returncode, {**result, "bundle": bundle, "timeout_seconds": args.timeout_seconds,
                                    "elapsed_seconds": round(time.monotonic() - started, 3)}
    except subprocess.TimeoutExpired:
        kill_child(process)
        return 124, {"status": "timed_out", "timeout_seconds": args.timeout_seconds, "security": security,
                     "child_process_group_killed": True, "mongo_authoritative": True, "cutover_eligible": False}
    finally:
        if process.poll() is None:
            kill_child(process)


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        signal.signal(signal.SIGTERM, lambda *unused: (_ for _ in ()).throw(InterruptedError("Operator canceled")))
        status, result = run(args)
    except BaseException as error:
        status, result = 1, {"status": "failed", "error_type": type(error).__name__, "mongo_authoritative": True,
                             "cutover_eligible": False, "credentials_or_source_records_printed": False}
    print(json.dumps(result, indent=2))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
