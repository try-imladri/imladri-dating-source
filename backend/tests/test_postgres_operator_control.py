"""Paused operator gates, fixed subprocess boundaries and durable local journals."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4
import secrets

import httpx
import pytest
from starlette.requests import Request

from backend import postgres_operator as worker, postgres_operator_api as api


SHA = "a" * 40
SECURITY = {"CapEff": "0000000000000000", "CapPrm": "0000000000000000",
            "CapInh": "0000000000000000", "NoNewPrivs": "1"}
DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
integration = pytest.mark.skipif(not DSN, reason="Requires isolated loopback PostgreSQL.")


def environment():
    return {"RENDER_SERVICE_ID": worker.SERVICE_ID, "RENDER_SERVICE_NAME": worker.SERVICE_NAME,
        "RENDER_GIT_COMMIT": SHA, "IMLADRI_DATABASE_MAINTENANCE": "paused",
        "IMLADRI_DATABASE_OPERATOR_SECRET": "fixture-operator-" * 3, "RENDER_ORIGIN_SECRET": "fixture-origin-" * 3,
        "IMLADRI_POSTGRES_GATEWAY_SECRET": "fixture-gateway-" * 3,
        "IMLADRI_MIGRATION_FENCE_SHA256": "f" * 64}


def request(command="inspect", **extra):
    return {"command": command, "operation_id": str(uuid4()), "expected_release_sha": SHA, **extra}


def fence(env, source="personal_live", allowed_commands=None):
    now = datetime.now(timezone.utc)
    claims = {"reference": "fixture-distributed-fence",
        "verified_at": now.isoformat(), "valid_until": (now + timedelta(minutes=10)).isoformat(),
        "old_runtime_writers_fenced": True, "application_background_fenced": True, "ddl_fenced": True,
        "capacity_verified": True, "capacity_evidence_sha256": "c" * 64, "snapshot_payload_budget_bytes": 32 * 1024 * 1024}
    commands = (["copy_personal", "verify"] if source in worker.NAMESPACES else ["copy_market", "verify"]
                if source in worker.MARKET_SOURCES else ["archive_legacy"])
    trusted = {"claims": claims, "scope": {"source_alias": source,
        "source_namespace": worker.SOURCE_DATABASES[source], "target_namespace": "imladri-legacy-archive"
        if source in worker.ARCHIVE_SOURCES else worker.SOURCE_DATABASES[source],
        "allowed_commands": commands if allowed_commands is None else allowed_commands},
        "controller_evidence": {"provider_capacity_verified": True}}
    env["IMLADRI_MIGRATION_FENCE_JSON"] = json.dumps(trusted)
    env["IMLADRI_MIGRATION_FENCE_SHA256"] = worker.digest(trusted)
    return {"proof_sha256": env["IMLADRI_MIGRATION_FENCE_SHA256"], **claims}


def test_exact_standby_pause_release_and_fixed_commands_are_required():
    env = environment()
    assert worker.validate_request(request(), env)["command"] == "inspect"
    for key, changed in (("RENDER_SERVICE_ID", "srv-preview"), ("RENDER_SERVICE_NAME", "imladri-personal-staging-api"),
                         ("IMLADRI_DATABASE_MAINTENANCE", "off"), ("RENDER_GIT_COMMIT", "b" * 40)):
        with pytest.raises(worker.OperatorBlocked):
            worker.validate_request(request(), {**env, key: changed})
    for extra in ({"sql": "SELECT 1"}, {"dsn": "fixture-not-allowed"}, {"path": "fixture-not-allowed"},
                  {"command": "archive_not_added_yet"}, {"source": "arbitrary_database"}):
        with pytest.raises(worker.OperatorBlocked):
            worker.validate_request(request(**extra), env)


def test_distributed_fence_needs_controller_hash_flags_and_bounded_times():
    env = environment()
    body = request("copy_personal", source="personal_live", fence=fence(env))
    assert worker.validate_request(body, env)["source"] == "personal_live"
    for changed in ({"proof_sha256": "e" * 64}, {"old_runtime_writers_fenced": False},
                    {"application_background_fenced": False}, {"ddl_fenced": False},
                    {"capacity_verified": False}, {"snapshot_payload_budget_bytes": 65 * 1024 * 1024},
                    {"reference": "other-valid-reference"}, {"capacity_evidence_sha256": "d" * 64},
                    {"snapshot_payload_budget_bytes": 16 * 1024 * 1024},
                    {"verified_at": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()},
                    {"valid_until": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()},
                    {"valid_until": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}):
        bad = {**body, "fence": {**body["fence"], **changed}}
        with pytest.raises(worker.OperatorBlocked):
            worker.validate_request(bad, env)
    with pytest.raises(worker.OperatorBlocked, match="approval"):
        worker.validate_request(request("prepare_access"), env)
    assert worker.validate_request(request("prepare_access", approved_workflow=True, allow_public_acl_hardening=True), env)


def test_child_environment_contains_only_migration_configuration_not_other_service_keys():
    env = {**environment(), "PATH": "fixture-path", "IMLADRI_MIGRATION_SOURCES_JSON": "fixture-source-config",
        "IMLADRI_MIGRATION_ROLE_DSNS_JSON": "fixture-role-config", "IMLADRI_POSTGRES_SHADOW_DSN": "fixture-owner-config",
        "DEEPSEEK_API_KEY": "excluded", "STRIPE_SECRET_KEY": "excluded", "CLERK_SECRET_KEY": "excluded",
        "RESEND_API_KEY": "excluded", "OPENAI_API_KEY": "excluded", "PYTHONPATH": "excluded", "LD_PRELOAD": "excluded",
        "SSLKEYLOGFILE": "excluded", "HTTPS_PROXY": "excluded"}
    child = worker.child_environment(env)
    assert set(child) <= set(worker.CHILD_ENV_KEYS)
    assert not any(key in child for key in ("IMLADRI_DATABASE_OPERATOR_SECRET", "RENDER_ORIGIN_SECRET",
        "DEEPSEEK_API_KEY", "STRIPE_SECRET_KEY", "CLERK_SECRET_KEY", "RESEND_API_KEY", "OPENAI_API_KEY",
        "PYTHONPATH", "LD_PRELOAD", "SSLKEYLOGFILE", "HTTPS_PROXY"))
    assert child["IMLADRI_POSTGRES_SHADOW_DSN"] == "fixture-owner-config"
    assert child["PYTHON_DOTENV_DISABLED"] == "1"
    with pytest.raises(worker.OperatorBlocked, match="standby"):
        worker.child_environment({**env, "RENDER_SERVICE_NAME": "imladri-personal-staging-api"})
    with pytest.raises(worker.OperatorBlocked, match="paused"):
        worker.child_environment({**env, "IMLADRI_DATABASE_MAINTENANCE": "off"})


@pytest.mark.parametrize("command,source", [("copy_personal", "personal_live"), ("verify", "personal_live"),
    ("copy_market", "market_live"), ("verify", "market_live"), ("archive_legacy", "legacy_staging"),
    ("archive_legacy", "legacy_production")])
def test_preview_fence_reuse_is_rejected_before_config_journal_or_business_write(command, source):
    env = environment()
    proof = fence(env, "personal_preview")
    def forbidden(*_):
        pytest.fail("A different source scope reached configuration or journal creation")
    async def run():
        with pytest.raises(worker.OperatorBlocked, match="scope_mismatch"):
            await worker.run({"action": "execute", "request": request(command, source=source, fence=proof)},
                             env, config_loader=forbidden, journal_factory=forbidden)
    asyncio.run(run())


@pytest.mark.parametrize("change", [{"source_alias": "personal_live"},
    {"source_namespace": "imladri_personal_live"}, {"target_namespace": "imladri_personal_live"},
    {"allowed_commands": ["copy_market"]}, {"allowed_commands": ["copy_personal", "inspect"]},
    {"allowed_commands": ["copy_personal", "copy_personal"]}, {"allowed_commands": []}])
def test_even_controller_hashed_scope_must_match_fixed_namespaces_and_vocabulary(change):
    env = environment()
    proof = fence(env, "personal_preview")
    trusted = json.loads(env["IMLADRI_MIGRATION_FENCE_JSON"])
    trusted["scope"].update(change)
    env["IMLADRI_MIGRATION_FENCE_JSON"] = json.dumps(trusted)
    env["IMLADRI_MIGRATION_FENCE_SHA256"] = worker.digest(trusted)
    with pytest.raises(worker.OperatorBlocked, match="scope_mismatch"):
        worker.validate_request(request("copy_personal", source="personal_preview",
            fence={**proof, "proof_sha256": worker.digest(trusted)}), env)


def test_command_restriction_and_reverse_scope_reuse_are_rejected():
    env = environment()
    proof = fence(env, "personal_preview", ["copy_personal"])
    assert worker.validate_request(request("copy_personal", source="personal_preview", fence=proof), env)
    with pytest.raises(worker.OperatorBlocked, match="scope_mismatch"):
        worker.validate_request(request("verify", source="personal_preview", fence=proof), env)
    for source in ["personal_live", "market_live", "legacy_staging"]:
        proof = fence(env, source)
        with pytest.raises(worker.OperatorBlocked, match="scope_mismatch"):
            worker.validate_request(request("copy_personal", source="personal_preview", fence=proof), env)


def trusted_configuration():
    from backend.postgres_codec import connection_options
    host = "dpg-approved.example.invalid"
    owner = f"postgresql://imladri_personal_owner:fixture-owner-password@{host}/imladri_personal?sslmode=verify-full"
    roles = {logical: f"postgresql://imladri_app_{logical}:fixture-{logical}-password-01234567890123456789@{host}/imladri_personal?sslmode=verify-full"
             for logical in worker.ROLE_NAMESPACES}
    sources = {logical: {"mongo_url": "mongodb://fixture-user:fixture-password@approved-atlas.example.invalid/?tls=true",
                         "database": namespace} for logical, namespace in worker.NAMESPACES.items()}
    parsed = {"owner_options": connection_options(owner), "sources": sources,
              "role_options": {logical: connection_options(dsn) for logical, dsn in roles.items()}}
    env = {**environment(), "IMLADRI_POSTGRES_SHADOW_DSN": owner,
        "IMLADRI_MIGRATION_ROLE_DSNS_JSON": json.dumps(roles), "IMLADRI_MIGRATION_SOURCES_JSON": json.dumps(sources),
        "IMLADRI_MIGRATION_CONFIG_SHA256": worker.digest(worker.configuration_metadata(parsed))}
    return env


def test_first_run_configuration_requires_provider_review_hash_and_exact_personal_sources():
    env = trusted_configuration()
    offline = worker.configuration_proof_input(env)
    assert offline["network_calls"] == 0 and offline["external_provider_review_required"] is True
    assert offline["configuration_sha256"] == env["IMLADRI_MIGRATION_CONFIG_SHA256"]
    assert "fixture-password" not in json.dumps(offline) and "postgresql://" not in json.dumps(offline)
    config = worker.load_config(env)
    assert config["configuration_sha256"] == env["IMLADRI_MIGRATION_CONFIG_SHA256"]
    changed = {**env, "IMLADRI_MIGRATION_CONFIG_SHA256": "b" * 64}
    with pytest.raises(worker.OperatorBlocked, match="configuration_not_approved"):
        worker.load_config(changed)


def test_fixed_market_archive_discovery_scopes_and_per_source_control_binding():
    env = trusted_configuration()
    for command, source in (("copy_market", "market_live"), ("archive_legacy", "legacy_staging"),
                            ("archive_legacy", "legacy_production"), ("verify", "market_live")):
        body = request(command, source=source, fence=fence(env, source))
        assert worker.validate_request(body, env)["source"] == source
    assert worker.validate_request(request("discover_sources"), env)["command"] == "discover_sources"
    for command, source in (("copy_market", "personal_live"), ("copy_personal", "market_live"),
                            ("archive_legacy", "market_live"), ("discover_sources", "legacy_staging")):
        with pytest.raises(worker.OperatorBlocked):
            worker.validate_request(request(command, source=source, fence=fence(env, source)), env)
    sources = json.loads(env["IMLADRI_MIGRATION_SOURCES_JSON"])
    sources["personal_live"]["control_database"] = "imladri_migration_control"
    changed = {**env, "IMLADRI_MIGRATION_SOURCES_JSON": json.dumps(sources)}
    with pytest.raises(worker.OperatorBlocked, match="configuration_not_approved"):
        worker.load_config(changed)
    changed["IMLADRI_MIGRATION_CONFIG_SHA256"] = worker.configuration_proof_input(changed)["configuration_sha256"]
    assert worker.load_config(changed)["sources"]["personal_live"]["control_database"] == "imladri_migration_control"
    sources["personal_live"]["control_database"] = "other_database"
    with pytest.raises(worker.OperatorBlocked, match="control_database"):
        worker.configuration_proof_input({**env, "IMLADRI_MIGRATION_SOURCES_JSON": json.dumps(sources)})
    changed = dict(env)
    for key in ("IMLADRI_POSTGRES_SHADOW_DSN", "IMLADRI_MIGRATION_ROLE_DSNS_JSON"):
        changed[key] = changed[key].replace("dpg-approved.example.invalid", "alternate.example.invalid")
    with pytest.raises(worker.OperatorBlocked, match="configuration_not_approved"):
        worker.load_config(changed)
    sources = json.loads(env["IMLADRI_MIGRATION_SOURCES_JSON"])
    sources["personal_live"]["database"], sources["personal_preview"]["database"] = sources["personal_preview"]["database"], sources["personal_live"]["database"]
    with pytest.raises(worker.OperatorBlocked, match="source_database_changed"):
        worker.load_config({**env, "IMLADRI_MIGRATION_SOURCES_JSON": json.dumps(sources)})
    changed = {**env, "IMLADRI_MIGRATION_SOURCES_JSON": env["IMLADRI_MIGRATION_SOURCES_JSON"].replace("approved-atlas.example.invalid", "another-atlas.example.invalid")}
    with pytest.raises(worker.OperatorBlocked, match="configuration_not_approved"):
        worker.load_config(changed)


def test_worker_result_checks_reject_secrets_raw_answers_and_bad_kernel_proof():
    good = {"version": 1, "status": "completed", "security": SECURITY, "result": {"records": 3}}
    assert api.checked_result(json.dumps(good).encode())["status"] == "completed"
    for item in ({"password": "private-fixture"}, {"after": {"choice": 1}}, {"dsn": "private-fixture"},
                 {"credentials": {"api_key": "private-fixture"}}, {"credentials_or_record_values_included": True},
                 {"unexpected": "mongodb://fixture.invalid/db"}):
        bad = {**good, "result": item}
        assert api.checked_result(json.dumps(bad).encode())["status"] == "blocked"
    bad = {**good, "security": {**SECURITY, "CapPrm": "0000000000000001"}}
    assert api.checked_result(json.dumps(bad).encode())["status"] == "blocked"


def app(controller):
    async def application(scope, receive, send):
        response = await api.endpoint(Request(scope, receive=receive), controller)
        await response(scope, receive, send)
    return application


def headers(env):
    return {"Authorization": "Bearer " + env["IMLADRI_DATABASE_OPERATOR_SECRET"],
            "X-Imladri-Origin": env["RENDER_ORIGIN_SECRET"]}


def test_endpoint_authenticates_before_body_and_rejects_gateway_key_reuse():
    async def run():
        env = environment()
        calls = []
        async def runner(*args):
            calls.append(args)
            raise AssertionError("Unauthorized request spawned a child")
        controller = api.OperatorController(env, runner=runner)
        async def forbidden_body():
            raise AssertionError("Authentication read the unauthorized body")
        scope = {"type": "http", "method": "POST", "path": api.PATH, "raw_path": api.PATH.encode(),
            "headers": [], "query_string": b"", "scheme": "https", "server": ("fixture.local", 443)}
        assert (await api.endpoint(Request(scope, receive=forbidden_body), controller)).status_code == 404
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app(controller)), base_url="https://fixture.local") as http:
            assert (await http.post(api.PATH, json=request())).status_code == 404
            wrong = {**headers(env), "Authorization": "Bearer " + env["IMLADRI_POSTGRES_GATEWAY_SECRET"]}
            assert (await http.post(api.PATH, json=request(), headers=wrong)).status_code == 404
            env["IMLADRI_DATABASE_OPERATOR_SECRET"] = env["IMLADRI_POSTGRES_GATEWAY_SECRET"]
            assert (await http.post(api.PATH, json=request(), headers=headers(env))).status_code == 404
            assert calls == []
    asyncio.run(run())


def test_endpoint_fixed_operation_replay_status_body_bound_and_pause_gate():
    async def run():
        env = environment()
        started, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def runner(envelope, child_env):
            calls.append(envelope)
            started.set()
            await finish.wait()
            return {"version": 1, "status": "completed", "security": SECURITY,
                    "operation_id": envelope["request"]["operation_id"], "result": {"records": 2}}
        controller = api.OperatorController(env, runner=runner)
        body = request()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app(controller)), base_url="https://fixture.local") as http:
            auth = headers(env)
            first = await http.post(api.PATH, json=body, headers=auth)
            assert first.status_code == 202
            await started.wait()
            assert (await http.post(api.PATH, json=body, headers=auth)).status_code == 202
            assert len(calls) == 1
            assert (await http.get(api.PATH, params={"operation_id": body["operation_id"]}, headers=auth)).json()["status"] == "running"
            assert (await http.post(api.PATH, json=request(), headers=auth)).status_code == 409
            assert (await http.post(api.PATH, content=b"x" * (worker.MAX_BODY + 1), headers=auth)).status_code == 413
            finish.set()
            await controller.operations[body["operation_id"]]["task"]
            complete = await http.get(api.PATH, params={"operation_id": body["operation_id"]}, headers=auth)
            assert complete.json()["status"] == "completed" and "no-store" in complete.headers["cache-control"]
            env["IMLADRI_DATABASE_MAINTENANCE"] = "off"
            assert (await http.get(api.PATH, params={"operation_id": body["operation_id"]}, headers=auth)).status_code == 404
    asyncio.run(run())


def test_unknown_pipe_result_is_reconciled_by_status_without_repeating_business_step():
    async def run():
        calls = []
        async def runner(envelope, environ):
            calls.append(envelope["action"])
            if envelope["action"] == "execute":
                return {"version": 1, "status": "unknown", "needs_manual_review": True}
            return {"version": 1, "status": "completed", "security": SECURITY, "result": {"records": 3}}
        controller = api.OperatorController(environment(), runner=runner)
        body = request()
        await controller.submit(body)
        await controller.operations[body["operation_id"]]["task"]
        assert (await controller.status(body["operation_id"]))["status"] == "completed"
        assert calls == ["execute", "status"]
    asyncio.run(run())


class FakeProcess:
    pid = 123456
    returncode = None
    def __init__(self, output):
        self.output = output
        self.stdin = self
        self.stdout = self
        self.written = b""
        self.closed = False
    def write(self, value):
        self.written += value
    async def drain(self):
        pass
    def close(self):
        self.closed = True
    async def read(self, count):
        value, self.output = self.output[:count], self.output[count:]
        return value
    async def wait(self):
        self.returncode = 0
        return 0
    def kill(self):
        self.returncode = -9


def test_supervisor_uses_fixed_module_no_shell_and_kills_oversized_output(monkeypatch):
    async def run():
        captured = []
        process = FakeProcess(b"x" * (worker.MAX_OUTPUT + 1))
        async def factory(*args, **kwargs):
            captured.append((args, kwargs))
            return process
        killed = []
        monkeypatch.setattr(api.signal, "SIGKILL", 9, raising=False)
        monkeypatch.setattr(api.os, "killpg", lambda pid, sig: killed.append((pid, sig)), raising=False)
        result = await api.run_child({"action": "execute", "request": request()}, environment(), process_factory=factory)
        assert result["status"] == "unknown" and result["error_type"] == "worker_output_bound"
        args, options = captured[0]
        assert args[1:] == ("-m", "backend.postgres_operator")
        assert options["start_new_session"] is True and "shell" not in options
        assert options["stderr"] == asyncio.subprocess.DEVNULL
        assert killed and process.returncode == 0 and process.closed
    asyncio.run(run())


def test_supervisor_deadline_kills_reaps_and_does_not_return_stderr(monkeypatch):
    async def run():
        process = FakeProcess(b"")
        async def stalled_read(count):
            await asyncio.Event().wait()
        process.read = stalled_read
        async def factory(*args, **kwargs):
            return process
        killed = []
        monkeypatch.setattr(api.signal, "SIGKILL", 9, raising=False)
        monkeypatch.setattr(api, "MAX_SECONDS", 0.01)
        monkeypatch.setattr(api.os, "killpg", lambda pid, sig: killed.append(pid), raising=False)
        result = await api.run_child({"action": "execute", "request": request()}, environment(), process_factory=factory)
        assert result["status"] == "unknown" and result["needs_manual_review"] is True and killed
    asyncio.run(run())


def test_spawn_itself_is_inside_parent_deadline(monkeypatch):
    async def run():
        async def stalled_spawn(*args, **kwargs):
            await asyncio.Event().wait()
        monkeypatch.setattr(api, "MAX_SECONDS", 0.01)
        result = await api.run_child({"action": "execute", "request": request()}, environment(), process_factory=stalled_spawn)
        assert result["status"] == "unknown" and result["error_type"] == "worker_deadline_or_cancellation"
    asyncio.run(run())


def test_nonzero_child_exit_cannot_claim_completed_without_durable_status():
    async def run():
        process = FakeProcess(json.dumps({"version": 1, "status": "completed", "security": SECURITY, "result": {"records": 2}}).encode())
        async def failed_exit():
            process.returncode = 1
            return 1
        process.wait = failed_exit
        async def factory(*args, **kwargs):
            return process
        result = await api.run_child({"action": "execute", "request": request()}, environment(), process_factory=factory)
        assert result["status"] == "unknown" and result["error_type"] == "worker_exit_unverified"
    asyncio.run(run())


@pytest.fixture
def owner_database():
    if not DSN:
        pytest.skip("Requires a loopback PostgreSQL fixture")
    assert urlsplit(DSN).hostname in {"127.0.0.1", "localhost", "::1"}
    import pg8000.dbapi
    from backend.postgres_codec import connection_options
    options = connection_options(DSN, allow_local_plaintext=True)
    admin = pg8000.dbapi.connect(**options)
    admin.autocommit = True
    name = "fixture_operator_" + uuid4().hex
    cursor = admin.cursor(); cursor.execute('CREATE DATABASE "' + name + '"'); cursor.close()
    local = {**options, "database": name}
    connection = pg8000.dbapi.connect(**local)
    try:
        yield connection, local
    finally:
        connection.close()
        cursor = admin.cursor(); cursor.execute('DROP DATABASE "' + name + '"'); cursor.close(); admin.close()


@integration
def test_real_pg_journal_survives_new_connection_and_never_repeats_unknown_step(owner_database):
    import pg8000.dbapi
    connection, options = owner_database
    first = worker.OperatorJournal(connection)
    body = request()
    assert first.reserve(body)["execute"] is True
    other_connection = pg8000.dbapi.connect(**options)
    try:
        other = worker.OperatorJournal(other_connection)
        assert other.reserve(body) == {"state": "running", "result": None, "execute": False}
        with pytest.raises(worker.OperatorBlocked, match="unknown"):
            other.reserve(request())
        first.finish(body, {"error_type": "fixture_interruption", "needs_manual_review": True}, success=False)
        replay = other.reserve(body)
        assert replay["state"] == "unknown" and replay["execute"] is False
        assert other.lookup(body["operation_id"])["result"]["error_type"] == "fixture_interruption"
    finally:
        other_connection.close()


@integration
def test_real_pg_journal_committed_result_is_idempotent_and_identity_conflicts_fail(owner_database, monkeypatch):
    connection, _ = owner_database
    journal = worker.OperatorJournal(connection)
    body = request()
    assert journal.reserve(body)["execute"] is True
    journal.finish(body, {"record_count": 3}, success=True)
    monkeypatch.setattr(journal, "_initialize", lambda: pytest.fail("Durable replay executed DDL"))
    assert journal.reserve(body) == {"state": "completed", "result": {"record_count": 3}, "execute": False}
    with pytest.raises(worker.OperatorBlocked, match="different_request"):
        journal.reserve({**body, "source": "personal_live"})


@integration
def test_journal_never_retains_passwords_or_private_answer_images(owner_database):
    connection, _ = owner_database
    journal = worker.OperatorJournal(connection)
    body = request()
    assert journal.reserve(body)["execute"] is True
    for value in ({"password": "private-fixture"}, {"after": {"answer": "private-fixture"}}):
        with pytest.raises(worker.OperatorBlocked, match="unsafe_operation_result"):
            journal.finish(body, value, success=True)
    saved = journal.lookup(body["operation_id"])
    assert saved["state"] == "running" and saved["result"] is None


@integration
def test_operator_bootstrap_creates_new_scoped_roles_only_with_explicit_approval(owner_database):
    import pg8000.dbapi
    from backend.postgres_access import AccessConfigurationError, verify_runtime, identifier
    connection, options = owner_database
    suffix = uuid4().hex[:16]
    roles = {logical: {**options, "user": "imladri_app_" + logical + "_" + suffix,
                       "password": secrets.token_urlsafe(32)} for logical in worker.ROLE_NAMESPACES}
    config = {"owner_options": options, "role_options": roles}
    with pytest.raises(worker.OperatorBlocked, match="approval"):
        worker.prepare_access(config, request("prepare_access"))
    try:
        result = worker.prepare_access(config, request("prepare_access", approved_workflow=True, allow_public_acl_hardening=True))
        assert result["roles_created"] == 4 and result["application_data_copied"] is False
        assert worker.ROLE_NAMESPACES["market_api"] == worker.ROLE_NAMESPACES["market_worker"] == "imladri_production"
        for logical, role in roles.items():
            app = pg8000.dbapi.connect(**role)
            cursor = app.cursor()
            try:
                verified = verify_runtime(cursor, worker.ROLE_NAMESPACES[logical], expected_role=role["user"])
                assert verified["provider_owner_role"] is False
            finally:
                app.rollback(); cursor.close(); app.close()
        # A second invocation never silently alters existing credentials/roles.
        with pytest.raises(AccessConfigurationError):
            worker.prepare_access(config, request("prepare_access", approved_workflow=True, allow_public_acl_hardening=True))
        for role in roles.values():
            unchanged = pg8000.dbapi.connect(**role)
            unchanged.close()
    finally:
        cursor = connection.cursor()
        for role in roles.values():
            cursor.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=%s", (role["user"],))
            if cursor.fetchone():
                cursor.execute("DROP OWNED BY " + identifier(role["user"]))
                cursor.execute("DROP ROLE " + identifier(role["user"]))
        connection.commit(); cursor.close()


@integration
def test_worker_journals_before_execute_and_private_failure_is_not_retained(owner_database, monkeypatch):
    connection, options = owner_database
    body = request()
    calls = []
    config = {"owner_options": options, "sources": {}, "role_options": {}}
    async def failed(request, config, journal):
        saved = journal.lookup(request["operation_id"])
        assert saved["state"] == "running"
        calls.append("executed")
        raise RuntimeError("fixture-private-not-retained")
    monkeypatch.setattr(worker, "execute", failed)
    async def run():
        envelope = {"action": "execute", "request": body}
        first = await worker.run(envelope, environment(), config_loader=lambda _: config)
        second = await worker.run(envelope, environment(), config_loader=lambda _: config)
        assert first["status"] == second["status"] == "unknown" and calls == ["executed"]
        assert "fixture-private-not-retained" not in json.dumps([first, second])
        saved = worker.OperatorJournal(connection).lookup(body["operation_id"])
        assert saved["state"] == "unknown" and saved["result"]["error_type"] == "RuntimeError"
    asyncio.run(run())
