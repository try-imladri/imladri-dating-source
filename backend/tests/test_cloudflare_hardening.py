"""Verify privilege removal only in child processes, never the pytest worker."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

try:
    import entrypoint
except ModuleNotFoundError:
    from cloudflare import entrypoint


class Call:
    def __init__(self, result=0):
        self.result = result
        self.arguments = []

    def __call__(self, *arguments):
        self.arguments.append(arguments)
        return self.result


def fake_kernel(monkeypatch, state=None):
    kernel = SimpleNamespace(syscall=Call(), prctl=Call())
    monkeypatch.setattr(entrypoint.sys, 'platform', 'linux')
    monkeypatch.setattr(entrypoint.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(entrypoint.ctypes, 'CDLL', lambda *args, **kwargs: kernel)
    monkeypatch.setattr(entrypoint, '_security_state', lambda: state or {
        'CapEff': '0000000000000000', 'CapPrm': '0000000000000000',
        'CapInh': '0000000000000000', 'NoNewPrivs': '1'})
    return kernel


def test_capability_syscall_uses_zeroed_v3_data_for_current_thread(monkeypatch):
    kernel = fake_kernel(monkeypatch)
    state = entrypoint.harden_process()
    number, raw_header, raw_data = kernel.syscall.arguments[0]
    header = entrypoint.ctypes.cast(raw_header, entrypoint.ctypes.POINTER(entrypoint._CapabilityHeader)).contents
    data = entrypoint.ctypes.cast(raw_data, entrypoint.ctypes.POINTER(entrypoint._CapabilityData * 2)).contents
    assert number == 126 and header.version == 0x20080522 and header.pid == 0
    assert all(row.effective == row.permitted == row.inheritable == 0 for row in data)
    assert kernel.prctl.arguments == [(38, 1, 0, 0, 0)]
    assert state['NoNewPrivs'] == '1'


@pytest.mark.parametrize('failure', ['syscall', 'prctl'])
def test_kernel_errors_fail_closed(monkeypatch, failure):
    kernel = fake_kernel(monkeypatch)
    getattr(kernel, failure).result = -1
    monkeypatch.setattr(entrypoint.ctypes, 'get_errno', lambda: 1)
    with pytest.raises(OSError) as error:
        entrypoint.harden_process()
    assert error.value.errno == 1
    if failure == 'syscall':
        assert not kernel.prctl.arguments


@pytest.mark.parametrize('field,value', [('CapEff', '1'), ('CapPrm', '1'), ('CapInh', '1'), ('NoNewPrivs', '0')])
def test_kernel_success_without_enforced_state_fails_closed(monkeypatch, field, value):
    state = {'CapEff': '0', 'CapPrm': '0', 'CapInh': '0', 'NoNewPrivs': '1'}
    state[field] = value
    fake_kernel(monkeypatch, state)
    with pytest.raises(RuntimeError, match='postcondition'):
        entrypoint.harden_process()


def test_unsupported_architecture_is_rejected_before_any_syscall(monkeypatch):
    kernel = fake_kernel(monkeypatch)
    monkeypatch.setattr(entrypoint.platform, 'machine', lambda: 'unsupported')
    with pytest.raises(RuntimeError, match='Unsupported architecture'):
        entrypoint.harden_process()
    assert not kernel.syscall.arguments and not kernel.prctl.arguments


def test_startup_hardens_before_all_network_and_exec(monkeypatch):
    events = []
    monkeypatch.setenv('CF_DIAG_EGRESS_ON_START', 'true')
    monkeypatch.setattr(entrypoint, 'harden_process', lambda: events.append('harden'))
    monkeypatch.setattr(entrypoint, 'observe_egress', lambda: events.append('egress') or '203.0.113.1')
    monkeypatch.setattr(entrypoint, 'wait_for_database', lambda: events.append('database'))
    from backend import native_patch_manifest
    monkeypatch.setattr(native_patch_manifest, 'read_native_patch_manifest', lambda: events.append('native'))
    from backend import runtime_manifest
    monkeypatch.setattr(runtime_manifest, 'emit_runtime_manifest', lambda: events.append('attest') or True)
    monkeypatch.setattr(entrypoint.os, 'execvp', lambda *args: events.append('exec'))
    entrypoint.main()
    assert events == ['harden', 'native', 'egress', 'database', 'attest', 'exec']


def test_native_payload_failure_blocks_network_database_and_exec(monkeypatch):
    monkeypatch.setattr(entrypoint, 'harden_process', lambda: None)
    from backend import native_patch_manifest
    def fail():
        raise RuntimeError('Native patch inventory failed verification')
    monkeypatch.setattr(native_patch_manifest, 'read_native_patch_manifest', fail)
    monkeypatch.setattr(entrypoint, 'observe_egress', lambda: pytest.fail('Network reached'))
    monkeypatch.setattr(entrypoint, 'wait_for_database', lambda: pytest.fail('Database reached'))
    monkeypatch.setattr(entrypoint.os, 'execvp', lambda *args: pytest.fail('Exec reached'))
    with pytest.raises(RuntimeError, match='Native patch inventory'):
        entrypoint.main()


def test_failed_runtime_inventory_never_executes_application(monkeypatch):
    monkeypatch.setattr(entrypoint, 'harden_process', lambda: None)
    from backend import native_patch_manifest, runtime_manifest
    monkeypatch.setattr(native_patch_manifest, 'read_native_patch_manifest', lambda: {})
    monkeypatch.setattr(entrypoint, 'wait_for_database', lambda: None)
    monkeypatch.setattr(runtime_manifest, 'emit_runtime_manifest', lambda: False)
    monkeypatch.setattr(entrypoint.os, 'execvp', lambda *args: pytest.fail('Exec reached'))
    with pytest.raises(RuntimeError, match='Runtime inventory verification'):
        entrypoint.main()


def test_failed_hardening_never_reaches_network_or_exec(monkeypatch):
    def fail():
        raise RuntimeError('Fixed hardening failure')
    monkeypatch.setattr(entrypoint, 'harden_process', fail)
    monkeypatch.setattr(entrypoint, 'observe_egress', lambda: pytest.fail('Network reached'))
    monkeypatch.setattr(entrypoint, 'wait_for_database', lambda: pytest.fail('Database reached'))
    monkeypatch.setattr(entrypoint.os, 'execvp', lambda *args: pytest.fail('Exec reached'))
    with pytest.raises(RuntimeError, match='hardening failure'):
        entrypoint.main()


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux child-process capability contract')
def test_real_child_removes_capabilities_and_preserves_no_new_privileges_across_exec():
    # A fresh interpreter performs the irreversible operation. Neither the host
    # process nor pytest worker changes its own capabilities or privilege flags.
    program = r'''
import json, os, sys
import entrypoint
before = entrypoint._security_state()
after = entrypoint.harden_process()
child = "import json,sys; import entrypoint; print(json.dumps({'before':json.loads(sys.argv[1]),'after_hardening':json.loads(sys.argv[2]),'after_exec':entrypoint._security_state()}))"
os.execv(sys.executable, [sys.executable, '-c', child, json.dumps(before), json.dumps(after)])
'''
    result = subprocess.run([sys.executable, '-c', program], text=True, capture_output=True, check=True)
    states = json.loads(result.stdout)
    for name in ('after_hardening', 'after_exec'):
        assert all(int(states[name][field], 16) == 0 for field in ('CapEff', 'CapPrm', 'CapInh'))
        assert states[name]['NoNewPrivs'] == '1'
