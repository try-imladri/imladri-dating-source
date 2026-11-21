"""Emit a fixed, public runtime inventory without reading credentials or user data."""
import hashlib
from importlib import metadata, util
import json
import os
from pathlib import Path
import platform
import pyexpat
import sqlite3
import ssl
import stat
import sys

from .native_patch_manifest import read_native_patch_manifest


PREFIX = 'IMLADRI_RUNTIME_FINGERPRINT_v2 '
APP_ROOT = Path('/app')
APK_DATABASE = Path('/lib/apk/db/installed')
STATUS_FILE = Path('/proc/self/status')
SYSTEM_ROOTS = tuple(Path(path) for path in ('/usr', '/bin', '/sbin', '/lib', '/opt'))
TOOL_DIRECTORIES = tuple(Path(path) for path in ('/bin', '/sbin', '/usr/bin', '/usr/sbin', '/usr/local/bin', '/opt/venv/bin'))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _tools(names):
    # Fixed paths avoid consulting PATH or other environment variables.
    return sorted(name for name in names if any((directory / name).is_file() for directory in TOOL_DIRECTORIES))


def _privileged_helpers():
    found = set()
    for directory in SYSTEM_ROOTS:
        for path in directory.rglob('*'):
            try:
                if path.is_file() and path.stat().st_mode & (stat.S_ISUID | stat.S_ISGID):
                    found.add(str(path.resolve()))
            except (FileNotFoundError, PermissionError):
                continue
    return sorted(found)


def _apk_inventory():
    if not APK_DATABASE.is_file():
        return None
    raw = APK_DATABASE.read_bytes()
    packages = []
    for block in raw.decode('utf8').split('\n\n'):
        fields = dict(line.split(':', 1) for line in block.splitlines() if ':' in line)
        if 'P' in fields:
            packages.append({'name': fields['P'], 'version': fields['V'], 'origin': fields.get('o', fields['P'])})
    packages.sort(key=lambda row: (row['name'], row['version'], row['origin']))
    return {'database_sha256': hashlib.sha256(raw).hexdigest(), 'count': len(packages),
            'sha256': _digest(packages), 'packages': packages}


def _application_inventory():
    paths = set((APP_ROOT / 'backend').glob('*.py'))
    paths.update([APP_ROOT / 'backend/requirements.txt', APP_ROOT / 'entrypoint.py',
                  APP_ROOT / 'backend/constraints.txt', APP_ROOT / 'backend/runtime_pruning.json'])
    paths.add(APP_ROOT / 'backend/postgres_schema.sql')
    paths.add(APP_ROOT / 'backend/postgres_store_schema.sql')
    paths.update(path for path in (APP_ROOT / 'backend/seed').rglob('*') if path.is_file())
    paths.update(path for path in (APP_ROOT / 'frontend/build').rglob('*') if path.is_file())
    # The Render-only migration runner is deliberately outside site-packages.
    # Bind its scripts, isolated driver ZIP and SBOM to the image attestation.
    paths.update(path for path in (APP_ROOT / 'postgres-operator').rglob('*') if path.is_file())
    files = []
    for path in sorted(paths):
        if not path.is_file() or '__pycache__' in path.parts or path.suffix in {'.pyc', '.pyo'}:
            continue
        data = path.read_bytes()
        if path.suffix in {'.py', '.json', '.txt', '.html', '.js', '.css'}:
            data = data.replace(b'\r\n', b'\n')
        files.append((path.relative_to(APP_ROOT).as_posix(), hashlib.sha256(data).hexdigest()))
    return {'count': len(files), 'sha256': _digest(files)}


def runtime_manifest():
    """Return a deterministic manifest that an operator can compare with an image."""
    release = platform.freedesktop_os_release()
    status = dict(line.split(':', 1) for line in STATUS_FILE.read_text().splitlines() if ':' in line)
    packages = sorted({(distribution.metadata['Name'], distribution.version) for distribution in metadata.distributions()})
    apk = _apk_inventory()
    return {
        'schema_version': 2,
        'python': sys.version.split()[0], 'openssl': ssl.OPENSSL_VERSION,
        'expat': pyexpat.EXPAT_VERSION, 'sqlite': sqlite3.sqlite_version,
        'os': {key: release[key] for key in ('ID', 'VERSION_ID', 'VERSION_CODENAME') if key in release},
        'os_inventory_supported': apk is not None,
        'apk_database': apk,
        'uid': os.getuid(), 'gid': os.getgid(),
        'effective_capabilities': status.get('CapEff', '').strip(),
        'python_distributions': {'count': len(packages), 'sha256': _digest(packages)},
        'native_patches': read_native_patch_manifest(),
        'application_files': _application_inventory(),
        'shells': _tools(('sh', 'ash', 'bash', 'dash', 'zsh', 'busybox')),
        'package_managers': _tools(('apk', 'apt', 'apt-get', 'dpkg', 'rpm', 'yum', 'dnf', 'pip', 'pip3')),
        'privileged_helpers': _privileged_helpers(),
        'pip_module': util.find_spec('pip') is not None,
        'node': bool(_tools(('node',))),
    }


def emit_runtime_manifest():
    """Attest once at startup; callers must reject an incomplete fingerprint."""
    try:
        manifest = runtime_manifest()
    except Exception as error:
        # Exception messages can contain local paths. Emit only the public type.
        manifest = {'schema_version': 2, 'attestation_error': type(error).__name__}
    print(PREFIX + json.dumps(manifest, sort_keys=True, separators=(',', ':')), flush=True)
    return 'attestation_error' not in manifest
