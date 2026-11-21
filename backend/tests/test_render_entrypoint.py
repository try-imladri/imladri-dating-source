import hashlib
import json
import sys
from unittest.mock import patch

import pytest

from backend import render_entrypoint


SOURCE = 'a' * 40
SECURITY = {**{key: '0000000000000000' for key in ('CapEff', 'CapPrm', 'CapInh')}, 'NoNewPrivs': '1'}
MANIFEST = {'schema_version': 2, 'effective_capabilities': '0000000000000000', 'application_files': {'count': 1, 'sha256': 'b' * 64}}


@pytest.fixture
def launch(monkeypatch):
    monkeypatch.setenv('RENDER_GIT_COMMIT', SOURCE)
    monkeypatch.setattr(render_entrypoint, '_harden', lambda: dict(SECURITY))
    monkeypatch.setattr(render_entrypoint, '_kernel_state', lambda: dict(SECURITY))
    monkeypatch.setattr(render_entrypoint, '_runtime_manifest', lambda: dict(MANIFEST))


@pytest.mark.parametrize('role,app', [('preview', 'backend.web:app'), ('standby', 'backend.standby:app')])
@pytest.mark.parametrize('port,expected', [(None, '8080'), ('10000', '10000'), ('65535', '65535')])
def test_render_runs_the_selected_app_and_port_without_shell_parsing(monkeypatch, launch, capsys, role, app, port, expected):
    if port is None:
        monkeypatch.delenv('PORT', raising=False)
    else:
        monkeypatch.setenv('PORT', port)
    with patch.object(render_entrypoint.os, 'execv') as execute:
        render_entrypoint.main([role])
    execute.assert_called_once_with(sys.executable, [
        sys.executable, '-m', 'uvicorn', app, '--host', '0.0.0.0',
        '--port', expected, '--no-access-log',
    ])
    rows = capsys.readouterr().out.splitlines()
    assert len(rows) == 2 and rows[0].startswith(render_entrypoint.RUNTIME_PREFIX)
    raw = rows[0][len(render_entrypoint.RUNTIME_PREFIX):]
    assert json.loads(raw) == MANIFEST
    assert json.loads(rows[1][len(render_entrypoint.KERNEL_PREFIX):]) == {'source': SOURCE, 'role': role,
        'security': SECURITY, 'manifest_sha256': hashlib.sha256(raw.encode()).hexdigest()}


@pytest.mark.parametrize('port', ['not-a-number', '0', '-1', '65536'])
def test_render_rejects_an_invalid_port_before_starting_a_service(monkeypatch, port):
    monkeypatch.setenv('PORT', port)
    with patch.object(render_entrypoint.os, 'execv') as execute, patch.object(render_entrypoint, '_harden') as harden:
        with pytest.raises(SystemExit) as error:
            render_entrypoint.main(['preview'])
    assert error.value.code == 2
    execute.assert_not_called(); harden.assert_not_called()


def test_hardening_precedes_runtime_inventory_and_uvicorn_exec(monkeypatch, launch):
    calls = []
    def harden(): calls.append('harden'); return dict(SECURITY)
    def inventory():
        assert calls == ['harden']; calls.append('inventory'); return dict(MANIFEST)
    def kernel():
        assert calls == ['harden', 'inventory']; calls.append('kernel'); return dict(SECURITY)
    def execute(*_):
        assert calls == ['harden', 'inventory', 'kernel']; calls.append('exec')
    monkeypatch.setattr(render_entrypoint, '_harden', harden)
    monkeypatch.setattr(render_entrypoint, '_runtime_manifest', inventory)
    monkeypatch.setattr(render_entrypoint, '_kernel_state', kernel)
    monkeypatch.setattr(render_entrypoint.os, 'execv', execute)
    render_entrypoint.main(['standby'])
    assert calls == ['harden', 'inventory', 'kernel', 'exec']


@pytest.mark.parametrize('changed', ['', 'short', 'A' * 40, 'a' * 41])
def test_missing_or_invalid_provider_source_blocks_before_hardening_or_network(monkeypatch, changed):
    monkeypatch.setenv('RENDER_GIT_COMMIT', changed)
    with patch.object(render_entrypoint, '_harden') as harden, patch.object(render_entrypoint.os, 'execv') as execute:
        with pytest.raises(RuntimeError, match='exact source commit'):
            render_entrypoint.main(['standby'])
    harden.assert_not_called(); execute.assert_not_called()


@pytest.mark.parametrize('key,value', [('CapEff', '0000000000000001'), ('CapPrm', '0000000000000001'),
    ('CapInh', '0000000000000001'), ('NoNewPrivs', '0')])
def test_each_kernel_regression_fails_closed_before_exec(monkeypatch, launch, key, value):
    monkeypatch.setattr(render_entrypoint, '_harden', lambda: render_entrypoint._validate_security({**SECURITY, key: value}))
    with patch.object(render_entrypoint, '_runtime_manifest') as inventory, patch.object(render_entrypoint.os, 'execv') as execute:
        with pytest.raises(RuntimeError, match='kernel'):
            render_entrypoint.main(['preview'])
    inventory.assert_not_called(); execute.assert_not_called()


@pytest.mark.parametrize('manifest', [None, [], {'schema_version': 1}, {'schema_version': 2, 'attestation_error': 'RuntimeError'},
    {**MANIFEST, 'effective_capabilities': '0000000000000001'}, {**MANIFEST, 'invalid': float('nan')}])
def test_invalid_runtime_metadata_never_launches_application(monkeypatch, launch, manifest):
    monkeypatch.setattr(render_entrypoint, '_runtime_manifest', lambda: manifest)
    with patch.object(render_entrypoint.os, 'execv') as execute:
        with pytest.raises((RuntimeError, ValueError)):
            render_entrypoint.main(['standby'])
    execute.assert_not_called()


def test_kernel_must_remain_enforced_after_runtime_inventory(monkeypatch, launch):
    monkeypatch.setattr(render_entrypoint, '_kernel_state', lambda: {**SECURITY, 'NoNewPrivs': '0'})
    with patch.object(render_entrypoint.os, 'execv') as execute:
        with pytest.raises(RuntimeError, match='changed before launch'):
            render_entrypoint.main(['preview'])
    execute.assert_not_called()


def test_missing_trusted_fixed_image_hardener_never_uses_an_environment_override(monkeypatch):
    monkeypatch.setenv('IMLADRI_HARDENER_PATH', 'untrusted.py')
    paths = []
    def missing(_name, path): paths.append(path); return None
    monkeypatch.setattr(render_entrypoint.importlib.util, 'spec_from_file_location', missing)
    with pytest.raises(RuntimeError, match='trusted image hardener'):
        render_entrypoint._harden()
    assert paths == ['/app/entrypoint.py']
