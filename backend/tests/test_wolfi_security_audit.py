import copy
import io
import json
import tarfile
import sys

import pytest
import requests

from scripts import audit_wolfi_security as audit


def example():
    installed = {"name": "libsqlite3", "version": "3.51.0-r1", "origin": "sqlite", "architecture": "x86_64"}
    inventory = {"image_id": "sha256:reviewed", "inventory": {"operating_system": {"ID": "wolfi"}, "apk_packages": [installed]}}
    record = {"id": "CGA-test", "modified": "2026-11-10T00:00:00Z", "upstream": ["CVE-2026-12345"], "affected": [{
        "package": {"ecosystem": "Wolfi", "name": "libsqlite3", "purl": "pkg:apk/wolfi/libsqlite3?arch=x86_64"},
        "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "3.51.0-r2"}]}],
        "ecosystem_specific": {"components": [{"latest_event_status": "fixed", "architecture": "x86_64"}]}}]}
    return inventory, record


def test_fix_in_vendor_repository_does_not_mean_installed_image_is_fixed():
    inventory, record = example()
    result = audit.evaluate(inventory, [record], {("3.51.0-r1", "3.51.0-r2"): "<"}, {"CVE-2026-12345"}, {})
    assert result["raw_open_package_advisory_matches"] == 1
    row = result["records"][0]
    assert row["status"] == "affected"
    assert row["vendor_component_states"] == ["fixed"]
    assert row["unapplied_vendor_fix_versions"] == ["3.51.0-r2"]
    assert row["fix_repository_availability"] == "not_checked"
    assert result["known_exploited_open"] == ["CVE-2026-12345"]


def test_native_apk_comparator_handles_revision_and_prerelease_without_pep440(monkeypatch):
    calls = []

    def native(arguments, **kwargs):
        calls.append((arguments, kwargs))
        assert kwargs["text"] is False
        assert isinstance(kwargs["input"], bytes) and b"\r" not in kwargs["input"]
        return b"<\n>\n"

    monkeypatch.setattr(audit, "command", native)
    pairs = [("3.12.16_git20261030-r0", "3.12.16-r0"), ("1.0-r10", "1.0-r2")]
    result = audit.apk_comparisons(pairs, audit.DEFAULT_HELPER)
    assert result[sorted(pairs)[0]] == "<"
    assert calls[0][0][:5] == ["docker", "run", "--rm", "-i", "--network"]
    assert calls[0][1]["input"] == "".join(a + " " + b + "\n" for a, b in sorted(pairs)).encode()
    with pytest.raises(ValueError, match="pinned"):
        audit.apk_comparisons(pairs, "wolfi:latest")
    monkeypatch.setattr(audit, "command", lambda *args, **kwargs: b"=\n")
    with pytest.raises(ValueError, match="Incomplete"):
        audit.apk_comparisons(pairs, audit.DEFAULT_HELPER)


def test_vendor_false_positive_is_distinct_from_a_fixed_installed_version():
    inventory, record = example()
    entry = record["affected"][0]
    entry["ranges"][0]["events"] = [{"introduced": "0"}, {"fixed": "0"}]
    entry["ecosystem_specific"]["components"][0]["latest_event_status"] = "false_positive_determination"
    result = audit.evaluate(inventory, [record], {}, set(), {})
    assert result["records"][0]["status"] == "not_affected"
    assert result["raw_open_package_advisory_matches"] == 0
    _, record = example()
    result = audit.evaluate(inventory, [record], {("3.51.0-r1", "3.51.0-r2"): ">"}, set(), {})
    assert result["records"][0]["status"] == "fixed_or_outside_affected_range"


def test_unresolved_vendor_states_architecture_and_source_family_history_are_preserved():
    inventory, record = example()
    entry = record["affected"][0]
    entry["ranges"][0]["events"] = [{"introduced": "0"}]
    entry["ecosystem_specific"]["components"][0]["latest_event_status"] = "detection"
    wrong_arch = copy.deepcopy(entry)
    wrong_arch["package"]["purl"] = "pkg:apk/wolfi/libsqlite3?arch=aarch64"
    wrong_arch["ecosystem_specific"]["components"][0]["architecture"] = "aarch64"
    record["affected"].append(wrong_arch)
    previous = {"records": [{"source": "sqlite3", "cve": "CVE-2026-12345", "decision": "needs_review"},
                            {"source": "sqlite3", "cve": "CVE-2026-88888", "decision": "needs_review"},
                            {"source": "systemd", "cve": "CVE-2026-99999", "decision": "not_affected"}]}
    result = audit.evaluate(inventory, [record], {}, set(), previous)
    assert len(result["records"]) == 1
    assert result["records"][0]["status"] == "under_investigation"
    assert [r["comparison"] for r in result["previous_debian_records"]] == [
        "vendor_records_available", "shared_source_family_needs_review", "debian_source_not_installed"]


def test_ambiguous_architecture_and_unsupported_ranges_never_become_a_clean_result():
    inventory, record = example()
    record["affected"][0]["package"]["purl"] = "pkg:apk/wolfi/libsqlite3"
    result = audit.evaluate(inventory, [record], {("3.51.0-r1", "3.51.0-r2"): ">"}, set(), {})
    assert result["records"][0]["status"] == "under_investigation"
    with pytest.raises(ValueError, match="range type"):
        audit.range_status("1.0-r0", [{"type": "SEMVER", "events": [{"introduced": "0"}]}], {})


def test_streaming_feed_filters_packages_without_extracting_archive_paths(tmp_path):
    _, record = example()
    archive = tmp_path / "feed.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, value in [("../../escape.json", record), ("unrelated.json", {"id": "other", "affected": []})]:
            raw = json.dumps(value).encode()
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            bundle.addfile(info, io.BytesIO(raw))
    rows, counts = audit.read_vendor_records(archive, {"libsqlite3"})
    assert rows == [record]
    assert counts == {"archive_records_examined": 2, "relevant_advisory_records": 1}
    assert not (tmp_path.parent.parent / "escape.json").exists()


def test_duplicate_advisory_data_fails_instead_of_double_counting():
    inventory, record = example()
    with pytest.raises(ValueError, match="Duplicate"):
        audit.evaluate(inventory, [record, record], {("3.51.0-r1", "3.51.0-r2"): "<"}, set(), {})


def test_failed_feed_refresh_cannot_replace_last_verified_cache(monkeypatch, tmp_path):
    archive = tmp_path / "chainguard-v3.tar.gz"
    archive.write_bytes(b"previous verified archive")
    source = {"sha256": audit.sha256_file(archive), "etag": "old"}
    metadata = tmp_path / "chainguard-v3.source.json"
    metadata.write_text(json.dumps(source))

    def failed(*args, **kwargs):
        raise requests.ConnectionError("feed unavailable")

    monkeypatch.setattr(audit.requests, "get", failed)
    with pytest.raises(requests.ConnectionError):
        audit.download_archive(tmp_path)
    assert archive.read_bytes() == b"previous verified archive"
    assert json.loads(metadata.read_text()) == source


def test_failed_audit_cannot_overwrite_a_previous_report_with_a_false_zero(monkeypatch, tmp_path):
    inventory, _ = example()
    inventory["inventory"]["pypi"] = []
    ledger, report = tmp_path / "ledger.json", tmp_path / "report.json"
    ledger.write_text('{"records": []}')
    report.write_text("previous verified result")
    monkeypatch.setattr(sys, "argv", ["audit", "--image", "candidate", "--debian-ledger", str(ledger), "--output", str(report)])
    monkeypatch.setattr(audit, "inventory_image", lambda *_: inventory)

    def unavailable(*_):
        raise requests.ConnectionError("vendor unavailable")

    monkeypatch.setattr(audit, "download_archive", unavailable)
    monkeypatch.setattr(audit, "fetch_kev", lambda: (set(), {}))
    monkeypatch.setattr(audit, "osv", lambda *_: [])
    with pytest.raises(requests.ConnectionError):
        audit.main()
    assert report.read_text() == "previous verified result"


def test_selection_cache_reopens_when_the_feed_or_package_set_changes(monkeypatch, tmp_path):
    archive = tmp_path / "archive"
    archive.write_bytes(b"version one")
    calls = []

    def read(path, names):
        calls.append(set(names))
        return [{"id": "example"}], {"archive_records_examined": 4, "relevant_advisory_records": 1}

    monkeypatch.setattr(audit, "read_vendor_records", read)
    assert not audit.cached_vendor_records(archive, {"foo"}, tmp_path)[1]["selection_cache_reused"]
    assert audit.cached_vendor_records(archive, {"foo"}, tmp_path)[1]["selection_cache_reused"]
    archive.write_bytes(b"version two")
    assert not audit.cached_vendor_records(archive, {"foo"}, tmp_path)[1]["selection_cache_reused"]
    assert not audit.cached_vendor_records(archive, {"foo", "bar"}, tmp_path)[1]["selection_cache_reused"]
    assert len(calls) == 3


def test_a_system_interpreter_without_application_distributions_fails_closed(monkeypatch):
    inspection = [{"Id": "sha256:exact", "Created": "2026-11-10T00:00:00Z"}]
    results = iter([json.dumps(inspection), json.dumps({"pypi": []})])
    monkeypatch.setattr(audit, "command", lambda *_: next(results))
    with pytest.raises(ValueError, match="virtual environment"):
        audit.inventory_image("candidate", "/usr/bin/python")


def test_versioned_shared_library_origins_never_appear_removed_due_to_a_name_change():
    inventory, _ = example()
    inventory["inventory"]["apk_packages"] = [{"name": "glibc-2.44", "origin": "glibc-2.44", "version": "2.44-r15", "architecture": "x86_64"}]
    previous = {"records": [{"source": "glibc", "cve": "CVE-2026-99999", "decision": "needs_review"}]}
    result = audit.evaluate(inventory, [], {}, set(), previous)
    assert result["previous_debian_records"][0]["comparison"] == "shared_source_family_needs_review"


def test_contradictory_unresolved_vendor_state_or_unknown_version_schema_is_not_clean():
    inventory, record = example()
    entry = record["affected"][0]
    entry["ecosystem_specific"]["components"][0]["latest_event_status"] = "detection"
    result = audit.evaluate(inventory, [record], {("3.51.0-r1", "3.51.0-r2"): ">"}, set(), {})
    assert result["records"][0]["status"] == "under_investigation"
    entry["versions"] = ["3.51.0-r1"]
    with pytest.raises(ValueError, match="affected-version lists"):
        audit.evaluate(inventory, [record], {("3.51.0-r1", "3.51.0-r2"): ">"}, set(), {})


def test_origin_inheritance_true_or_unknown_cannot_produce_a_clean_result():
    inventory, record = example()
    entry = record["affected"][0]
    entry["package"]["name"] = "sqlite"
    entry["package"]["purl"] = "pkg:apk/wolfi/sqlite?arch=x86_64"
    for value in (True, None):
        entry["database_specific"] = {"chainguard": {"applies_to_subpackages": value}}
        with pytest.raises(ValueError, match="inheritance"):
            audit.evaluate(inventory, [record], {}, set(), {})
    entry["database_specific"]["chainguard"]["applies_to_subpackages"] = False
    result = audit.evaluate(inventory, [record], {}, set(), {})
    assert result["records"] == []
    assert result["raw_open_package_advisory_matches"] == 0


def test_selection_schema_upgrade_cannot_reuse_a_previous_feed_selection(monkeypatch, tmp_path):
    archive = tmp_path / "archive"
    archive.write_bytes(b"feed")
    calls = []

    def read(path, names):
        calls.append(set(names))
        return [], {"archive_records_examined": 1, "relevant_advisory_records": 0}

    monkeypatch.setattr(audit, "read_vendor_records", read)
    assert not audit.cached_vendor_records(archive, {"libsqlite3", "sqlite"}, tmp_path)[1]["selection_cache_reused"]
    assert audit.cached_vendor_records(archive, {"libsqlite3", "sqlite"}, tmp_path)[1]["selection_cache_reused"]
    monkeypatch.setattr(audit, "SELECTION_SCHEMA", audit.SELECTION_SCHEMA + 1)
    assert not audit.cached_vendor_records(archive, {"libsqlite3", "sqlite"}, tmp_path)[1]["selection_cache_reused"]
    assert len(calls) == 2
