"""Recovery export aliases stay archive-only and need a separate control DB."""
import asyncio
import json

import pytest

from backend import postgres_operator as worker
from backend.tests.test_postgres_operator_control import environment, fence, request, trusted_configuration


@pytest.mark.parametrize("source,database", [("recovery_staging", "imladri_staging"),
                                             ("recovery_production", "imladri_production")])
def test_recovery_configuration_requires_explicit_isolated_barrier_database(source, database):
    env = trusted_configuration()
    sources = json.loads(env["IMLADRI_MIGRATION_SOURCES_JSON"])
    sources[source] = {"mongo_url": "mongodb+srv://fixture:fixture@recovery.example.invalid/", "database": database}
    env["IMLADRI_MIGRATION_SOURCES_JSON"] = json.dumps(sources)
    with pytest.raises(worker.OperatorBlocked, match="recovery_requires_separate_control"):
        worker.configuration_proof_input(env)
    sources[source]["control_database"] = "imladri_migration_control"
    env["IMLADRI_MIGRATION_SOURCES_JSON"] = json.dumps(sources)
    approved = worker.configuration_proof_input(env)
    env["IMLADRI_MIGRATION_CONFIG_SHA256"] = approved["configuration_sha256"]
    parsed = worker.load_config(env)
    assert parsed["sources"][source]["database"] == database
    assert parsed["sources"][source]["control_database"] == "imladri_migration_control"
    assert set(parsed["role_dsns"]) == set(worker.ROLE_NAMESPACES)
    assert not set(parsed["role_dsns"]) & set(worker.RECOVERY_SOURCES)


@pytest.mark.parametrize("source", ["recovery_staging", "recovery_production"])
def test_recovery_fence_only_authorizes_exact_alias_and_archive_target(source):
    env = environment()
    proof = fence(env, source)
    assert worker.validate_request(request("archive_legacy", source=source, fence=proof), env)
    trusted = json.loads(env["IMLADRI_MIGRATION_FENCE_JSON"])
    assert trusted["scope"]["target_namespace"] == "imladri-legacy-archive"
    other = "legacy_staging" if source == "recovery_staging" else "legacy_production"
    def forbidden(*args):
        pytest.fail("Cross-origin archive proof reached configuration or journal")
    async def run():
        with pytest.raises(worker.OperatorBlocked, match="scope_mismatch"):
            await worker.run({"action": "execute", "request": request("archive_legacy", source=other, fence=proof)},
                             env, config_loader=forbidden, journal_factory=forbidden)
    asyncio.run(run())
    for command in ("copy_personal", "copy_market", "verify"):
        with pytest.raises(worker.OperatorBlocked):
            worker.validate_request(request(command, source=source, fence=proof), env)
