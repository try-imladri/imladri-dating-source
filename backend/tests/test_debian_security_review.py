import copy
import json
import sys

import pytest
import requests

from scripts import review_debian_security as security


def example():
    source, cve, version = "sqlite3", "CVE-2026-119586", "3.46.1-7+deb13u2"
    tracker = {source: {cve: {"description": "A command-line component issue.",
                             "releases": {"trixie": {"status": "undetermined"}}}}}
    row = {"source": source, "cve": cve, "version": version,
           "decision": "not_affected", "next_action": "Recheck if the missing component is installed.",
           "owner": "Imladri security maintenance",
           "vendor_record_sha256": security.vendor_fingerprint(tracker[source][cve], "trixie")}
    system = {"id": "debian", "version": "13", "codename": "trixie"}
    inventory = {"image_id": "sha256:reviewed", "inventory": {
        "operating_system": system, "debian_sources": [[source, version]]}}
    ledger = {"artifact": {"image_id": inventory["image_id"], "operating_system": system}, "records": [row]}
    matches = [{"name": source, "version": version, "advisory": "DEBIAN-" + cve}]
    return inventory, ledger, matches, tracker


def test_absent_component_closure_is_bound_to_the_reviewed_image_and_vendor_record():
    inventory, ledger, matches, tracker = example()
    result = security.review(inventory, ledger, matches, tracker, set())
    assert result["records"][0]["decision"] == "not_affected"
    assert security.review(inventory, ledger, matches, tracker, set(), evidence_current=False)["records"][0]["decision"] == "needs_review"
    inventory["image_id"] = "sha256:different"
    assert security.review(inventory, ledger, matches, tracker, set())["records"][0]["decision"] == "needs_review"
    inventory["image_id"] = ledger["artifact"]["image_id"]
    tracker["sqlite3"]["CVE-2026-119586"]["description"] = "Updated conditions."
    assert security.review(inventory, ledger, matches, tracker, set())["records"][0]["decision"] == "needs_review"
    inventory, ledger, matches, tracker = example()
    tracker["sqlite3"]["CVE-2026-119586"]["scope"] = "remote"
    assert security.review(inventory, ledger, matches, tracker, set())["records"][0]["decision"] == "needs_review"


def test_a_new_advisory_and_known_exploitation_are_visible():
    inventory, ledger, matches, tracker = example()
    matches.append({**matches[0], "advisory": "DEBIAN-CVE-2026-999999"})
    result = security.review(inventory, ledger, matches, tracker, {"CVE-2026-999999"})
    assert result["raw_matches"] == 2
    assert result["records"][1]["decision"] == "needs_review"
    assert result["known_exploited"] == ["CVE-2026-999999"]


def test_fresh_vendor_not_affected_evidence_survives_an_image_change():
    inventory, ledger, matches, tracker = example()
    inventory["image_id"] = "sha256:new-image"
    tracker["sqlite3"]["CVE-2026-119586"]["releases"]["trixie"] = {"status": "resolved", "fixed_version": "0"}
    result = security.review(inventory, ledger, matches, tracker, set())
    assert result["records"][0]["decision"] == "not_affected"
    assert result["proofs_requiring_review"] == 0


def test_a_vendor_fix_requires_installed_version_verification():
    inventory, ledger, matches, tracker = example()
    tracker["sqlite3"]["CVE-2026-119586"]["releases"]["trixie"] = {
        "status": "resolved", "fixed_version": "3.46.1-7+deb13u3"}
    result = security.review(inventory, ledger, matches, tracker, set())
    assert result["records"][0]["decision"] == "verify_vendor_fix"


def test_missing_feed_records_retain_their_history():
    inventory, ledger, _, tracker = example()
    result = security.review(inventory, ledger, [], tracker, set())
    assert result["raw_matches"] == 0
    assert result["no_longer_reported"][0]["status"] == "not_currently_reported"
    assert result["no_longer_reported"][0]["previous_decision"] == "not_affected"
    assert result["no_longer_reported"][0]["source_installed"] is True


def test_release_drift_and_duplicate_feed_rows_fail_explicitly():
    inventory, ledger, matches, tracker = example()
    system = inventory["inventory"]["operating_system"] = copy.deepcopy(inventory["inventory"]["operating_system"])
    system.pop("codename")
    system["version"] = "14"
    with pytest.raises(ValueError, match="codename"):
        security.review(inventory, ledger, matches, tracker, set())
    system["version"] = "13"
    with pytest.raises(ValueError, match="Duplicate"):
        security.review(inventory, ledger, matches + matches, tracker, set())


def test_failed_advisory_fetch_preserves_the_previous_report(monkeypatch, tmp_path):
    inventory, ledger, _, _ = example()
    inventory_path, ledger_path = tmp_path / "inventory.json", tmp_path / "ledger.json"
    inventory_path.write_text(json.dumps(inventory))
    ledger_path.write_text(json.dumps(ledger))
    output = tmp_path / "report.json"
    output.write_text("previous verified report")
    monkeypatch.setattr(sys, "argv", ["review_debian_security", "--inventory", str(inventory_path),
                                     "--ledger", str(ledger_path), "--output", str(output)])

    def unavailable(_):
        raise requests.ConnectionError("Feed unavailable")

    monkeypatch.setattr(security, "fetch", unavailable)
    monkeypatch.setattr(security, "osv", lambda *_: [])
    with pytest.raises(requests.ConnectionError):
        security.main()
    assert output.read_text() == "previous verified report"


def test_supporting_evidence_tampering_or_loss_invalidates_local_closures(monkeypatch, tmp_path):
    proof = tmp_path / "security" / "proof.json"
    proof.parent.mkdir()
    value = {"component_absent": True}
    proof.write_text(json.dumps(value))
    ledger = {"proof_files": [{"path": "security/proof.json",
                               "canonical_json_sha256": security.fingerprint(value)}]}
    monkeypatch.setattr(security, "__file__", str(tmp_path / "scripts" / "review_debian_security.py"))
    assert security.evidence_files_current(ledger)
    proof.write_text(json.dumps({"component_absent": False}))
    assert not security.evidence_files_current(ledger)
    proof.unlink()
    assert not security.evidence_files_current(ledger)
