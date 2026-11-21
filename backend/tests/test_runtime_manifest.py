"""The startup proof hashes only packaged code and fixed public system metadata."""
import json
from types import SimpleNamespace

from backend import runtime_manifest as manifest


def test_application_fingerprint_ignores_tests_caches_and_local_files(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest, 'APP_ROOT', tmp_path)
    (tmp_path / 'backend/seed').mkdir(parents=True)
    (tmp_path / 'backend/code.py').write_bytes(b'print("public")\r\n')
    (tmp_path / 'backend/seed/example.json').write_text('{}', encoding='utf8')
    initial = manifest._application_inventory()
    (tmp_path / 'backend/tests').mkdir()
    (tmp_path / 'backend/tests/test_local.py').write_text('ignored', encoding='utf8')
    (tmp_path / '.local').mkdir()
    (tmp_path / '.local/secret.env').write_text('must not be read', encoding='utf8')
    (tmp_path / 'backend/__pycache__').mkdir()
    (tmp_path / 'backend/__pycache__/code.pyc').write_bytes(b'ignored')
    (tmp_path / 'backend/code.py').write_bytes(b'print("public")\n')
    assert manifest._application_inventory() == initial
    (tmp_path / 'backend/seed/example.json').write_text('{"changed":true}', encoding='utf8')
    assert manifest._application_inventory()['sha256'] != initial['sha256']


def test_apk_inventory_keeps_versions_and_origins_in_stable_order(tmp_path, monkeypatch):
    path = tmp_path / 'installed'
    path.write_text('P:zlib\nV:1.3-r2\no:zlib\n\nP:python-3.12\nV:3.12.16-r0\no:python-3.12\n\n', encoding='utf8')
    monkeypatch.setattr(manifest, 'APK_DATABASE', path)
    inventory = manifest._apk_inventory()
    assert inventory['count'] == 2
    assert inventory['packages'] == [{'name': 'python-3.12', 'version': '3.12.16-r0', 'origin': 'python-3.12'},
                                     {'name': 'zlib', 'version': '1.3-r2', 'origin': 'zlib'}]
    assert len(inventory['database_sha256']) == 64 and len(inventory['sha256']) == 64


def test_manifest_is_deterministic_and_does_not_read_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest, 'read_native_patch_manifest', lambda: {'schema_version': 1, 'fixture': True})
    monkeypatch.setattr(manifest, 'APP_ROOT', tmp_path)
    monkeypatch.setattr(manifest, 'APK_DATABASE', tmp_path / 'missing-apk')
    status = tmp_path / 'status'
    status.write_text('Name:\tpython\nCapEff:\t0000000000000000\n', encoding='utf8')
    monkeypatch.setattr(manifest, 'STATUS_FILE', status)
    monkeypatch.setattr(manifest, 'SYSTEM_ROOTS', ())
    monkeypatch.setattr(manifest, 'TOOL_DIRECTORIES', ())
    monkeypatch.setattr(manifest.platform, 'freedesktop_os_release', lambda: {'ID': 'wolfi'})
    monkeypatch.setattr(manifest.metadata, 'distributions', lambda: [SimpleNamespace(metadata={'Name': 'fixture'}, version='1.0')])
    monkeypatch.setattr(manifest.util, 'find_spec', lambda name: None)
    class EnvironmentMustNotBeRead:
        def getuid(self):
            return 65532
        def getgid(self):
            return 65532
        def __getattribute__(self, name):
            if name in {'environ', 'getenv'}:
                raise AssertionError('Runtime attestation read environment')
            return object.__getattribute__(self, name)
    monkeypatch.setattr(manifest, 'os', EnvironmentMustNotBeRead())
    observed = manifest.runtime_manifest()
    assert observed == manifest.runtime_manifest()
    assert observed['schema_version'] == 2 and observed['uid'] == observed['gid'] == 65532
    assert observed['os'] == {'ID': 'wolfi'} and observed['effective_capabilities'] == '0000000000000000'
    assert observed['apk_database'] is None and observed['os_inventory_supported'] is False
    assert observed['shells'] == observed['package_managers'] == observed['privileged_helpers'] == []


def test_emission_never_includes_exception_details(monkeypatch, capsys):
    def failure():
        raise ValueError('fixture-secret-must-never-be-logged')
    monkeypatch.setattr(manifest, 'runtime_manifest', failure)
    assert manifest.emit_runtime_manifest() is False
    output = capsys.readouterr().out
    assert output.startswith(manifest.PREFIX) and 'fixture-secret' not in output
    assert json.loads(output[len(manifest.PREFIX):]) == {'schema_version': 2, 'attestation_error': 'ValueError'}
