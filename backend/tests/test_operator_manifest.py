"""Optional migration code must be covered by the immutable image inventory."""
from backend import runtime_manifest as manifest


def test_operator_archive_and_scripts_change_the_attested_application(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest, "APP_ROOT", tmp_path)
    before = manifest._application_inventory()
    bundle = tmp_path / "postgres-operator"
    bundle.mkdir()
    (bundle / "run_postgres_shadow.py").write_text("print('bounded runner')\n")
    (bundle / "drivers.zip").write_bytes(b"PK\x03\x04fixture-driver")
    (bundle / "sbom.json").write_text('{"packages": []}\n')
    initial = manifest._application_inventory()
    assert initial["count"] == before["count"] + 3
    assert initial["sha256"] != before["sha256"]
    (bundle / "drivers.zip").write_bytes(b"PK\x03\x04changed-driver")
    changed = manifest._application_inventory()
    assert changed["count"] == initial["count"]
    assert changed["sha256"] != initial["sha256"]
    (bundle / "run_postgres_shadow.py").write_text("print('different runner')\n")
    assert manifest._application_inventory()["sha256"] != changed["sha256"]


def test_no_operator_directory_keeps_api_inventory_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest, "APP_ROOT", tmp_path)
    assert manifest._application_inventory()["count"] == 0
