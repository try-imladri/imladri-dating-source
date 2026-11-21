"""Pure driver packaging and bounded one-off runner safety contracts."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from scripts import build_postgres_operator as builder
from scripts import run_postgres_shadow as operator


SECURITY = {"CapEff": "0000000000000000", "CapPrm": "0000000000000000", "CapInh": "0000000000000000", "NoNewPrivs": "1"}


def args(**changes):
    values = vars(operator.parser().parse_args(["--namespace", "fixture", "--environment", "live"]))
    return SimpleNamespace(**{**values, **changes})


def wheels(tmp_path, *, unsafe=None, tags=True):
    folder = tmp_path / "wheels"
    folder.mkdir()
    pins = []
    for name, version in operator.PACKAGES.items():
        target = folder / (name + "-" + version + "-py3-none-any.whl")
        metadata = name + "-" + version + ".dist-info/"
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr(metadata + "METADATA", "Name: " + name + "\nVersion: " + version + "\n")
            archive.writestr(metadata + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\n" + ("Tag: py3-none-any\n" if tags else ""))
            archive.writestr(name + "/__init__.py", "__version__ = '" + version + "'\n")
            if unsafe and name == "pg8000":
                archive.writestr(unsafe, "fixture")
        pins.append(name + "==" + version + " --hash=sha256:" + hashlib.sha256(target.read_bytes()).hexdigest())
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("\n".join(pins) + "\n")
    return folder, requirements


def test_driver_zip_is_reproducible_pure_and_separate_from_the_application_path(tmp_path, monkeypatch):
    folder, requirements = wheels(tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"
    proof = builder.build(folder, requirements, first)
    builder.build(folder, requirements, second)
    assert (first / "drivers.zip").read_bytes() == (second / "drivers.zip").read_bytes()
    assert (first / "sbom.json").read_bytes() == (second / "sbom.json").read_bytes()
    assert len(proof["python_distributions"]) == 3 and proof["native_or_compiled_files"] == 0
    assert {row["name"] for row in proof["python_distributions"]} == set(operator.PACKAGES)
    with zipfile.ZipFile(first / "drivers.zip") as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
    monkeypatch.setattr(operator, "BUNDLE", first)
    actual = operator.bundle_proof()
    assert actual["operator_distribution_count"] == 3 and actual["driver_zip_on_main_python_path"] is False
    assert actual["zip_sha256"] == proof["zip_sha256"]
    with (first / "drivers.zip").open("ab") as output:
        output.write(b"tampered")
    with pytest.raises(ValueError, match="ZIP does not match"):
        operator.bundle_proof()


@pytest.mark.parametrize("unsafe", ["pg8000/native.so", "pg8000/__pycache__/x.pyc", "../outside.py", "/absolute.py", "x.data/scripts/tool"])
def test_compiled_or_unsafe_wheel_content_is_rejected_before_packaging(tmp_path, unsafe):
    folder, requirements = wheels(tmp_path, unsafe=unsafe)
    with pytest.raises(ValueError, match="Unsafe or compiled"):
        builder.build(folder, requirements, tmp_path / "output")


def test_missing_pure_wheel_tags_and_unreviewed_wheel_bytes_are_rejected(tmp_path):
    tags_case = tmp_path / "missing-tags"
    tags_case.mkdir()
    folder, requirements = wheels(tags_case, tags=False)
    with pytest.raises(ValueError, match="Native wheels"):
        builder.build(folder, requirements, tmp_path / "output")
    # Isolate the hash case from the missing-tag case. File iteration order
    # differs across hosts and must not decide which validation fails first.
    hash_case = tmp_path / "unreviewed-bytes"
    hash_case.mkdir()
    folder, requirements = wheels(hash_case)
    wheel = next(folder.glob("*.whl"))
    with wheel.open("ab") as output:
        output.write(b"changed")
    with pytest.raises(ValueError, match="reviewed version and hash"):
        builder.build(folder, requirements, tmp_path / "changed")


@pytest.mark.parametrize("change", [{"timeout_seconds": 601}, {"timeout_seconds": 0}, {"max_source_bytes": operator.MAX_SOURCE_BYTES + 1},
                                   {"max_target_payload_bytes": operator.MAX_TARGET_PAYLOAD_BYTES + 1}, {"publish_report": True}, {"namespace": "../foreign"}])
def test_operator_limits_and_explicit_report_publication_are_enforced(change):
    with pytest.raises(ValueError):
        operator.validate_args(args(**change))


def test_child_environment_passes_only_needed_database_secrets_and_isolated_driver_path(monkeypatch):
    monkeypatch.setenv("MONGO_URL", "mongodb://fixture:fixture@127.0.0.1/source")
    monkeypatch.setenv("DB_NAME", "fixture")
    monkeypatch.setenv("IMLADRI_POSTGRES_SHADOW_DSN", "postgresql://fixture:fixture@127.0.0.1/target")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "NEVER PASS THIS")
    monkeypatch.setenv("CLERK_SECRET_KEY", "NEVER PASS THIS")
    monkeypatch.setenv("HTTP_PROXY", "NEVER PASS THIS")
    environment = operator.child_environment()
    assert all(name in environment for name in operator.SOURCE_KEYS)
    assert not {"STRIPE_SECRET_KEY", "CLERK_SECRET_KEY", "HTTP_PROXY", "SSL_CERT_FILE"}.intersection(environment)
    assert environment["PYTHON_DOTENV_DISABLED"] == "1"
    assert str(operator.BUNDLE / "drivers.zip") in environment["PYTHONPATH"]


def test_external_tls_and_two_loopback_fixture_conditions_fail_closed(monkeypatch):
    from backend import postgres_migration
    monkeypatch.setenv("DB_NAME", "fixture")
    monkeypatch.setenv("MONGO_URL", "mongodb+srv://fixture:PRIVATE@cluster.invalid/fixture")
    monkeypatch.setenv("IMLADRI_POSTGRES_SHADOW_DSN", "postgresql://fixture:PRIVATE@external.invalid/fixture?sslmode=disable")
    with pytest.raises(ValueError):
        operator.validate_connections(args(apply=True), postgres_migration)
    with pytest.raises(ValueError, match="two explicit loopback"):
        operator.validate_connections(args(apply=True, allow_local_plaintext=True), postgres_migration)
    monkeypatch.setenv("MONGO_URL", "mongodb://fixture:PRIVATE@127.0.0.1/fixture")
    monkeypatch.setenv("IMLADRI_POSTGRES_SHADOW_DSN", "postgresql://fixture:PRIVATE@127.0.0.1/fixture?sslmode=disable")
    operator.validate_connections(args(apply=True, allow_local_plaintext=True), postgres_migration)
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1/fixture?tlsAllowInvalidCertificates=true")
    with pytest.raises(ValueError, match="TLS verification"):
        operator.validate_connections(args(apply=True, allow_local_plaintext=True), postgres_migration)


def test_hardening_failure_stops_before_bundle_loading_or_child_launch(monkeypatch):
    def fail():
        raise RuntimeError("Kernel rejected hardening")
    monkeypatch.setattr(operator, "harden", fail)
    monkeypatch.setattr(operator, "bundle_proof", lambda: pytest.fail("Bundle must follow kernel hardening"))
    monkeypatch.setattr(operator.subprocess, "Popen", lambda *a, **k: pytest.fail("No process or networking after hardening failure"))
    with pytest.raises(RuntimeError, match="Kernel"):
        operator.run(args())


def test_hard_deadline_kills_and_reaps_only_the_child_process_group(monkeypatch):
    monkeypatch.setattr(operator, "harden", lambda: dict(SECURITY))
    monkeypatch.setattr(operator, "bundle_proof", lambda: {"driver_zip_on_main_python_path": False})
    monkeypatch.setattr(operator.signal, "SIGKILL", 9, raising=False)
    process = SimpleNamespace(pid=98765, returncode=None, calls=0)
    def communicate(timeout):
        process.calls += 1
        if process.returncode is None:
            raise subprocess.TimeoutExpired("fixed-child", timeout)
        return "", None
    process.poll, process.communicate = lambda: process.returncode, communicate
    def launch(command, **kwargs):
        assert kwargs["start_new_session"] is True
        assert kwargs["stderr"] == subprocess.DEVNULL and kwargs["stdin"] == subprocess.DEVNULL
        assert command[1] == "-c" and "shell" not in kwargs
        return process
    monkeypatch.setattr(operator.subprocess, "Popen", launch)
    killed = []
    def kill(pid, sig):
        killed.append((pid, sig))
        process.returncode = -9
    monkeypatch.setattr(operator.os, "killpg", kill, raising=False)
    code, proof = operator.run(args(timeout_seconds=1))
    assert code == 124 and proof["status"] == "timed_out" and proof["child_process_group_killed"]
    assert len(killed) == 1 and killed[0][0] == process.pid
    assert process.calls == 2


def test_budget_failure_returns_only_safe_stage_and_counts(monkeypatch, capsys):
    monkeypatch.setattr(operator, "harden", lambda: dict(SECURITY))
    async def capped(value):
        raise operator.BudgetExceeded("aggregate_source_scan", {"source_bson_bytes": 12, "max_source_bson_bytes": 10})
    monkeypatch.setattr(operator, "execute_child", capped)
    assert operator.child_main(json.dumps(vars(args()))) == 1
    proof = json.loads(capsys.readouterr().out)
    assert proof["budget_stage"] == "aggregate_source_scan" and proof["budget"]["source_bson_bytes"] == 12
    assert proof["error_type"] == "BudgetExceeded" and proof["credentials_or_source_records_printed"] is False


def test_source_byte_budget_immediately_closes_its_cursor_and_restores_shared_functions(tmp_path, monkeypatch):
    import motor.motor_asyncio
    from backend import postgres_migration
    (tmp_path / "migrate_postgres.py").write_text(
        "from backend import postgres_migration as migration\n"
        "async def run(args):\n"
        "    async for row in migration.source_rows(None, 'users'):\n"
        "        pass\n")
    monkeypatch.setattr(operator, "BUNDLE", tmp_path)
    monkeypatch.setenv("DB_NAME", "fixture")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1/fixture")
    monkeypatch.setenv("IMLADRI_POSTGRES_SHADOW_DSN", "postgresql://fixture:fixture@127.0.0.1/fixture?sslmode=disable")
    closed = []
    async def rows(db, name):
        try:
            yield {"_id": "fixture", "text": "NEVER PRINT THIS"}
        finally:
            closed.append(True)
    monkeypatch.setattr(postgres_migration, "source_rows", rows)
    class Source:
        def __getitem__(self, key):
            return self
        async def command(self, *a, **k):
            return {"dataSize": 1}
        def close(self):
            pass
    monkeypatch.setattr(motor.motor_asyncio, "AsyncIOMotorClient", lambda *a, **k: Source())
    with pytest.raises(operator.BudgetExceeded) as capped:
        asyncio.run(operator.execute_child(args(apply=True, allow_local_plaintext=True, max_source_bytes=1)))
    assert capped.value.stage == "aggregate_source_scan" and closed == [True]
    assert postgres_migration.source_rows is rows
