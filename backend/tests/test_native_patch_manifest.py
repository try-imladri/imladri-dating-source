"""Changed native bytes cannot inherit a historical source/patch attestation."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from backend import native_patch_manifest as native


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root = tmp_path / 'lib'
    root.mkdir()
    monkeypatch.setattr(native, 'LIBRARY_ROOTS', (root,))
    monkeypatch.setattr(native, '_load_native', lambda: None)
    groups = {'glibc': ['libc.so.6', 'ld-linux-x86-64.so.2'],
              'openssl': ['libssl.so.4', 'libcrypto.so.4'],
              'cryptography': ['cryptography/hazmat/bindings/_rust.abi3.so']}
    components, files = [], []
    for name, basenames in groups.items():
        components.append({'name': name, 'mode': 'custom_backport', 'source_sha256': '1' * 64,
            'artifact_sha256': '2' * 64, 'recipe_sha256': '3' * 64, 'patches': ['4' * 64],
            'cases': ['CVE-2026-100290']})
        for basename in basenames:
            path = root / basename
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = b'\x7fELF' + name.encode() + basename.encode()
            path.write_bytes(raw)
            files.append({'path': str(path), 'resolved_path': str(path),
                          'sha256': hashlib.sha256(raw).hexdigest(), 'component': name})
    value = {'schema_version': 1, 'components': components, 'files': files}
    maps = tmp_path / 'maps'
    maps.write_text(''.join('100-200 r--p 00000000 00:00 0 ' + row['path'] + '\n' for row in files))
    monkeypatch.setattr(native, 'MAPS', maps)
    path = tmp_path / 'inventory.json'
    def write(item=value):
        path.write_text(json.dumps(item), encoding='utf8')
        return path
    write()
    return path, value, write


def test_reads_actual_bytes_without_environment_or_application_data(bundle, monkeypatch):
    path, value, _ = bundle
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'test-value-must-not-appear')
    result = native.read_native_patch_manifest(path)
    assert result == native.read_native_patch_manifest(path)
    assert result['resolved_files'] == result['file_count'] == 5
    assert len(result['files_sha256']) == 64
    assert result['manifest_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert 'test-value' not in json.dumps(result)


def test_changed_library_bytes_fail_even_with_identical_versions_and_metadata(bundle):
    path, value, _ = bundle
    Path(value['files'][0]['path']).write_bytes(b'\x7fELFdifferent-native-build')
    with pytest.raises(RuntimeError):
        native.read_native_patch_manifest(path)


@pytest.mark.parametrize('change', [
    lambda x: x.update(schema_version=True),
    lambda x: x.update(unreviewed=True),
    lambda x: x['components'].pop(),
    lambda x: x['components'].append(copy.deepcopy(x['components'][0])),
    lambda x: x['components'][0].update(source_sha256='unverified'),
    lambda x: x['components'][0].update(patches=[]),
    lambda x: x['components'][0].update(patches=[{}]),
    lambda x: x['components'][0].update(cases=['not-an-advisory']),
    lambda x: x['components'][0].update(cases=['CVE-2026-100290'] * 2),
    lambda x: x['files'].append(copy.deepcopy(x['files'][0])),
    lambda x: x['files'][0].update(sha256='0' * 64),
    lambda x: x['files'][0].update(component='unreviewed'),
    lambda x: x['files'][0].update(path=x['files'][0]['path'] + '/'),
    lambda x: x['files'][0].update(resolved_path=x['files'][1]['resolved_path']),
    lambda x: x['files'][0].update(path=x['files'][0]['path'].replace('/lib/', '/lib/../lib/')),
])
def test_malformed_or_incomplete_provenance_blocks_attestation(bundle, change):
    _, value, write = bundle
    change(value)
    with pytest.raises((RuntimeError, FileNotFoundError)):
        native.read_native_patch_manifest(write(value))


def test_duplicate_json_keys_and_nonfinite_numbers_rejected(bundle):
    path, _, _ = bundle
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(RuntimeError):
        native.read_native_patch_manifest(path)
    path.write_text('{"schema_version":NaN}')
    with pytest.raises(RuntimeError):
        native.read_native_patch_manifest(path)


def test_manifest_cannot_be_a_symlink(bundle):
    path, _, _ = bundle
    linked = path.with_name('linked.json')
    linked.symlink_to(path)
    with pytest.raises(RuntimeError):
        native.read_native_patch_manifest(linked)


def test_library_symlink_cannot_escape_fixed_library_roots(bundle):
    path, value, write = bundle
    library = Path(value['files'][0]['path'])
    outside = path.with_name('outside.so')
    outside.write_bytes(library.read_bytes())
    library.unlink()
    library.symlink_to(outside)
    value['files'][0]['resolved_path'] = str(outside)
    with pytest.raises(RuntimeError):
        native.read_native_patch_manifest(write(value))


def test_non_elf_content_is_not_accepted_as_a_native_library(bundle):
    path, value, write = bundle
    library = Path(value['files'][0]['path'])
    library.write_bytes(b'not a native library')
    value['files'][0]['sha256'] = hashlib.sha256(library.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError):
        native.read_native_patch_manifest(write(value))


def test_cross_component_aliases_are_rejected(bundle):
    _, value, write = bundle
    alias = copy.deepcopy(value['files'][0])
    path = Path(alias['path']).with_name('unreviewed-alias.so')
    path.symlink_to(alias['path'])
    alias.update(path=str(path), component='openssl')
    value['files'].append(alias)
    with pytest.raises(RuntimeError):
        native.read_native_patch_manifest(write(value))


def test_missing_inventory_fails_closed(tmp_path):
    with pytest.raises(RuntimeError):
        native.read_native_patch_manifest(tmp_path / 'missing.json')


def test_unmodified_source_custom_rebuild_is_disclosed_without_inventing_patches(bundle):
    _, value, write = bundle
    value['components'][0].update(mode='custom_rebuild', patches=[])
    result = native.read_native_patch_manifest(write(value))
    assert next(row for row in result['components'] if row['name'] == 'glibc')['mode'] == 'custom_rebuild'


def test_unlisted_loaded_ssl_copy_cannot_inherit_on_disk_proof(bundle):
    path, value, _ = bundle
    alternate = Path(value['files'][2]['path']).with_name('libssl.so.unknown')
    alternate.write_bytes(b'\x7fELFalternate SSL library')
    with native.MAPS.open('a') as handle:
        handle.write('100-200 r--p 00000000 00:00 0 ' + str(alternate) + '\n')
    with pytest.raises(RuntimeError):
        native.read_native_patch_manifest(path)


def test_disk_presence_without_actually_loaded_components_is_insufficient(bundle):
    path, _, _ = bundle
    native.MAPS.write_text('100-200 r--p 00000000 00:00 0 [heap]\n')
    with pytest.raises(RuntimeError):
        native.read_native_patch_manifest(path)


def test_deleted_but_mapped_library_is_rejected(bundle):
    path, _, _ = bundle
    native.MAPS.write_text(native.MAPS.read_text().replace('libc.so.6\n', 'libc.so.6 (deleted)\n'))
    with pytest.raises(RuntimeError):
        native.read_native_patch_manifest(path)


@pytest.mark.parametrize(('component', 'basename'), [
    ('sqlite', 'libsqlite3.so.3'), ('ncurses', 'libncursesw.so.6')])
def test_declared_optional_components_must_be_actually_loaded(bundle, component, basename):
    _, value, write = bundle
    library = Path(value['files'][0]['path']).with_name(basename)
    raw = b'\x7fELF' + component.encode()
    library.write_bytes(raw)
    value['components'].append({**value['components'][0], 'name': component,
        'mode': 'signed_vendor', 'patches': [], 'cases': []})
    value['files'].append({'path': str(library), 'resolved_path': str(library),
        'sha256': hashlib.sha256(raw).hexdigest(), 'component': component})
    path = write(value)
    with pytest.raises(RuntimeError):
        native.read_native_patch_manifest(path)
    with native.MAPS.open('a') as handle:
        handle.write('100-200 r--p 00000000 00:00 0 ' + str(library) + '\n')
    result = native.read_native_patch_manifest(path)
    assert result['loaded_native_files']['mapped_files'] == 6
