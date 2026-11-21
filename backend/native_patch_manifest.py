"""Bind reviewed native backports to the actual installed library bytes.

The fixed image manifest contains public build provenance only. No environment,
credentials, application records, commands, or user-controlled paths are read.
"""
import hashlib
import importlib
import json
from pathlib import Path
import re
import stat


MANIFEST = Path('/app/native_security_inventory.json')
MAPS = Path('/proc/self/maps')
LIBRARY_ROOTS = tuple(Path(path) for path in ('/usr/lib', '/usr/local/lib', '/opt/venv/lib'))
EXECUTABLES = frozenset(Path(path) for path in ('/usr/bin/infocmp', '/usr/bin/tic', '/usr/bin/openssl',
    '/usr/bin/clear', '/usr/bin/tabs', '/usr/bin/toe', '/usr/bin/tput', '/usr/bin/tset',
    '/usr/bin/ldconfig', '/usr/sbin/ldconfig', '/usr/sbin/ldconfig.real'))
COMPONENTS = {'glibc', 'openssl', 'cryptography', 'sqlite', 'ncurses'}
REQUIRED = {'glibc', 'openssl', 'cryptography'}
HASH = re.compile(r'[0-9a-f]{64}')
CVE = re.compile(r'CVE-[0-9]{4}-[0-9]{4,}')


def _require(condition):
    if not condition:
        raise RuntimeError('Native patch inventory failed verification')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _sha(value):
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _load_native():
    # Imports initialize the same mandatory native extensions used by the app;
    # these modules perform no provider, database, or application-data requests.
    importlib.import_module('ssl')
    importlib.import_module('sqlite3')
    importlib.import_module('cryptography.hazmat.bindings._rust')
    importlib.import_module('_curses')


def _mapped_component(path):
    name = path.name
    if name == 'libc.so.6' or name.startswith('ld-linux'):
        return 'glibc'
    if name.startswith(('libssl.so.', 'libcrypto.so.')):
        return 'openssl'
    if name.startswith('_rust.') and name.endswith('.so') and '/cryptography/hazmat/bindings/' in path.as_posix():
        return 'cryptography'
    if name.startswith('libsqlite3.so.'):
        return 'sqlite'
    if name.startswith(('libncurses.so.', 'libncursesw.so.', 'libtinfo.so.')):
        return 'ncurses'
    return None


def _loaded_identity(cache, names):
    _load_native()
    _require(MAPS.is_file() and MAPS.stat().st_size <= 2 * 1024 * 1024)
    with MAPS.open('rb') as handle:
        raw = handle.read(2 * 1024 * 1024 + 1)
    _require(len(raw) <= 2 * 1024 * 1024)
    found = set()
    for line in raw.decode('utf8').splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) != 6 or not fields[5].startswith('/'):
            continue
        deleted = fields[5].endswith(' (deleted)')
        mapped = Path(fields[5].removesuffix(' (deleted)'))
        name = _mapped_component(mapped)
        if name not in names:
            continue
        _require(not deleted)
        resolved = str(mapped.resolve(strict=True))
        _require(resolved in cache and cache[resolved][1] == name)
        found.add((resolved, cache[resolved][0], name))
    # Every declared application component must actually be mapped, not merely
    # present on disk. Bind both OS OpenSSL DSOs and the static Python copy.
    covered = {name: set() for name in names}
    for path, _, name in found:
        covered[name].add(Path(path).name)
    _require('libc.so.6' in covered['glibc'] and any('ld-linux' in p for p in covered['glibc'])
        and any(p.startswith('libssl.so.') for p in covered['openssl'])
        and any(p.startswith('libcrypto.so.') for p in covered['openssl'])
        and bool(covered['cryptography']))
    if 'sqlite' in names:
        _require(any(p.startswith('libsqlite3.so.') for p in covered['sqlite']))
    if 'ncurses' in names:
        _require(any(p.startswith(('libncurses.so.', 'libncursesw.so.'))
            for p in covered['ncurses']))
    return {'mapped_files': len(found), 'sha256': _digest(sorted(found))}


def read_native_patch_manifest(path=MANIFEST):
    path = Path(path)
    _require(not path.is_symlink() and path.is_file() and 0 < path.stat().st_size <= 2 * 1024 * 1024)
    raw = path.read_bytes()
    _require(len(raw) <= 2 * 1024 * 1024)
    value = json.loads(raw, object_pairs_hook=_unique,
        parse_constant=lambda _: (_require(False)))
    _require(isinstance(value, dict) and set(value) == {'schema_version', 'components', 'files'}
        and type(value['schema_version']) is int and value['schema_version'] == 1)
    components, files = value['components'], value['files']
    _require(isinstance(components, list) and 3 <= len(components) <= len(COMPONENTS)
        and isinstance(files, list) and 4 <= len(files) <= 1024)
    names = set()
    for item in components:
        _require(isinstance(item, dict) and set(item) == {'name', 'mode', 'source_sha256', 'artifact_sha256',
                                                       'recipe_sha256', 'patches', 'cases'})
        name = item['name']
        _require(isinstance(name, str) and name in COMPONENTS and name not in names
            and item['mode'] in ('custom_backport', 'custom_rebuild', 'signed_vendor')
            and all(_sha(item[key]) for key in ('source_sha256', 'artifact_sha256', 'recipe_sha256'))
            and isinstance(item['patches'], list) and len(item['patches']) <= 64
            and all(_sha(patch) for patch in item['patches']) and len(set(item['patches'])) == len(item['patches'])
            and (item['mode'] != 'custom_backport' or bool(item['patches']))
            and isinstance(item['cases'], list) and len(item['cases']) <= 64
            and all(isinstance(case, str) and CVE.fullmatch(case) for case in item['cases'])
            and len(set(item['cases'])) == len(item['cases']))
        names.add(name)
    _require(REQUIRED <= names)
    roots = tuple(root.resolve(strict=True) for root in LIBRARY_ROOTS if root.is_dir())
    _require(bool(roots))
    observed, seen, cache, covered = [], set(), {}, {name: set() for name in names}
    total_bytes = 0
    for item in files:
        _require(isinstance(item, dict) and set(item) == {'path', 'resolved_path', 'sha256', 'component'})
        name, declared, resolved_text = item['component'], item['path'], item['resolved_path']
        _require(isinstance(name, str) and name in names and _sha(item['sha256'])
            and isinstance(declared, str) and isinstance(resolved_text, str)
            and len(declared) <= 512 and len(resolved_text) <= 512
            and declared.startswith('/') and resolved_text.startswith('/')
            and not any(part in ('.', '..') for part in declared.split('/')[1:] + resolved_text.split('/')[1:])
            and '//' not in declared and '//' not in resolved_text and declared not in seen)
        seen.add(declared)
        library = Path(declared)
        _require(library.as_posix() == declared and Path(resolved_text).as_posix() == resolved_text)
        resolved = library.resolve(strict=True)
        _require(str(resolved) == resolved_text and (any(resolved.is_relative_to(root) for root in roots)
            or resolved in EXECUTABLES))
        before = resolved.stat()
        _require(stat.S_ISREG(before.st_mode) and 4 <= before.st_size <= 64 * 1024 * 1024)
        if resolved_text not in cache:
            total_bytes += before.st_size
            _require(total_bytes <= 256 * 1024 * 1024)
            digest = hashlib.sha256()
            with resolved.open('rb') as handle:
                header = handle.read(4)
                _require(header == b'\x7fELF')
                digest.update(header)
                for block in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(block)
            after = resolved.stat()
            _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns))
            cache[resolved_text] = (digest.hexdigest(), name)
        _require(cache[resolved_text] == (item['sha256'], name))
        covered[name].add(resolved.name)
        observed.append((declared, resolved_text, item['sha256'], name))
    _require(all(covered.values()) and 'libc.so.6' in covered['glibc']
        and any('ld-linux' in basename for basename in covered['glibc'])
        and any(basename.startswith('libssl.so.') for basename in covered['openssl'])
        and any(basename.startswith('libcrypto.so.') for basename in covered['openssl'])
        and any(basename.startswith('_rust.') and basename.endswith('.so') for basename in covered['cryptography']))
    loaded = _loaded_identity(cache, names)
    return {'schema_version': 1, 'manifest_sha256': hashlib.sha256(raw).hexdigest(),
        'components': sorted(components, key=lambda item: item['name']), 'file_count': len(observed),
        'resolved_files': len(cache), 'files_sha256': _digest(sorted(observed)), 'bytes_hashed': total_bytes,
        'loaded_native_files': loaded}
