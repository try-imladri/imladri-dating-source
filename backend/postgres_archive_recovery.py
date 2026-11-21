"""Explicit manual Recovery archive, linked to two unchanged unknown intents."""
from copy import deepcopy
import re

from .postgres_archive_rehearsal import _absent_owner_schema, _verified_owner_options


def recovery_link(request, environ, journal):
    """Verify the exact original request chain before reserving a new intent."""
    from .postgres_operator import RECOVERY_SOURCES, decode_json, digest, require
    source = request.get("source")
    require(source in RECOVERY_SOURCES, "manual_archive_recovery_requires_fixed_Recovery_source")
    trusted = decode_json(environ.get("IMLADRI_MIGRATION_FENCE_JSON", "").encode(), maximum=64 * 1024)
    evidence = trusted.get("manual_archive_recovery")
    require(isinstance(evidence, dict) and set(evidence) == {"original_archive_request",
        "original_archive_configuration_sha256", "prior_rehearsal_request", "prior_rehearsal_configuration_sha256"},
        "manual_archive_recovery_request_evidence_required")
    old_id, diagnostic_id = request["failed_archive_id"], request["failed_rehearsal_id"]
    old, diagnostic = journal.lookup(old_id), journal.lookup(diagnostic_id)
    require(old and diagnostic and old["state"] == diagnostic["state"] == "unknown"
        and old["command"] == "archive_legacy" and diagnostic["command"] == "rehearse_archive"
        and old["source"] == diagnostic["source"] == source, "manual_archive_recovery_prior_state_or_origin_mismatch")
    archive_request, rehearsal_request = evidence["original_archive_request"], evidence["prior_rehearsal_request"]
    common = {"operation_id", "expected_release_sha", "command", "source", "fence"}
    require(isinstance(archive_request, dict) and set(archive_request) == common
        and isinstance(rehearsal_request, dict) and set(rehearsal_request) == common | {"failed_archive_id"}
        and archive_request["operation_id"] == old_id and rehearsal_request["operation_id"] == diagnostic_id
        and archive_request["command"] == "archive_legacy" and rehearsal_request["command"] == "rehearse_archive"
        and archive_request["source"] == rehearsal_request["source"] == source
        and rehearsal_request["failed_archive_id"] == old_id
        and archive_request["expected_release_sha"] == old["release_sha"]
        and rehearsal_request["expected_release_sha"] == diagnostic["release_sha"],
        "manual_archive_recovery_original_request_chain_mismatch")
    require(evidence["original_archive_configuration_sha256"] == evidence["prior_rehearsal_configuration_sha256"]
        == request.get("configuration_sha256"), "manual_archive_recovery_configuration_changed")
    for body, cfg_key, previous in ((archive_request, "original_archive_configuration_sha256", old),
            (rehearsal_request, "prior_rehearsal_configuration_sha256", diagnostic)):
        cfg = evidence[cfg_key]
        require(isinstance(cfg, str) and re.fullmatch(r"[a-f0-9]{64}", cfg)
            and previous["request_sha256"] == digest({**body, "configuration_sha256": cfg}),
            "manual_archive_recovery_original_request_digest_mismatch")
    original, finding = old.get("result"), diagnostic.get("result")
    require(isinstance(original, dict) and original.get("error_type") == "ArchiveBlocked"
        and isinstance(finding, dict) and finding.get("error_type") == "ArchiveRehearsalBlocked"
        and finding.get("reason_code") == "rehearsal_failed" and finding.get("stage") == "archive_core"
        and finding.get("archive_path_error") == {"error_type": "ArchiveBlocked",
            "reason_code": "archive_source_target_parity_failed", "stage": "target_verification"}
        and finding.get("target_schema_absent_independently_verified") is True
        and finding.get("prior_unknown_unchanged") is True and finding.get("diagnostic_only") is True
        and finding.get("archive_committed") is False and type(finding.get("target_commit_operations")) is int
        and finding["target_commit_operations"] == 0 and type(finding.get("source_application_write_operations")) is int
        and finding["source_application_write_operations"] == 0 and "sql_failure" not in finding,
        "manual_archive_recovery_requires_verified_rollback_parity_finding")
    return {"version": 1, "kind": "manual_archive_recovery", "source_alias": source,
        "source_database": RECOVERY_SOURCES[source], "operation_id": request["operation_id"],
        "original_archive_id": old_id, "original_archive_request_sha256": old["request_sha256"],
        "original_archive_state_sha256": digest(old), "prior_rehearsal_id": diagnostic_id,
        "prior_rehearsal_request_sha256": diagnostic["request_sha256"], "prior_rehearsal_state_sha256": digest(diagnostic)}


async def recover_archive(config, request, journal, *, archive_runner, allow_local_plaintext=False):
    import asyncio
    from .postgres_operator import RECOVERY_SOURCES, digest, require
    link = config["_verified_archive_recovery_link"]
    source = request["source"]
    require(source in RECOVERY_SOURCES and link["operation_id"] == request["operation_id"]
        and request["recovery_links_sha256"] == digest(link), "manual_archive_recovery_verified_link_changed")
    row = config["sources"][source]
    require(row["database"] == RECOVERY_SOURCES[source] and row.get("control_database") == "imladri_migration_control",
        "manual_archive_recovery_source_or_control_changed")
    _verified_owner_options(config["owner_options"], allow_local_plaintext=allow_local_plaintext)
    await asyncio.to_thread(_absent_owner_schema, config["owner_options"], source, row["database"])
    result = await archive_runner(config, request, _manual_recovery=deepcopy(link))
    receipt = result.get("receipt") if isinstance(result, dict) else None
    require(isinstance(receipt, dict) and receipt.get("status") == "verified_legacy_archive"
        and receipt.get("manual_recovery") == link and receipt.get("source_two_pass_pinned_verified") is True
        and receipt.get("target_two_pass_raw_verified") is True, "manual_archive_recovery_receipt_not_verified")
    require(digest(journal.lookup(link["original_archive_id"])) == link["original_archive_state_sha256"]
        and digest(journal.lookup(link["prior_rehearsal_id"])) == link["prior_rehearsal_state_sha256"],
        "manual_archive_recovery_prior_unknown_changed")
    return {**result, "manual_recovery_completed": True, "prior_archive_and_rehearsal_unknowns_unchanged": True}
