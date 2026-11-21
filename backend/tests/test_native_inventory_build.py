"""Meaningful provenance, virtual-root and complete native-inventory guards."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SPEC = importlib.util.spec_from_file_location('native_inventory_build', Path(__file__).resolve().parents[2] / 'scripts/build_native_inventory.py')
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)
ELF = b'\x7fELF fictional isolated unit fixture\0'


def put(directory, name, content=ELF):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf8')


def symlink(path, target, directory=False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip('Host lacks symlink privilege; required Linux image run covers this guard: ' + type(error).__name__)


@pytest.fixture
def layout(tmp_path):
    root, inputs, venv = (tmp_path / name for name in ('runtime', 'inputs', 'venv'))
    root.mkdir(); inputs.mkdir(); venv.mkdir(); (root / 'app').mkdir()
    paths = ['/usr/lib/libc.so.6', '/usr/lib/ld-linux-x86-64.so.2', '/usr/lib/libcrypto.so.4',
             '/usr/lib/libssl.so.4', '/usr/lib/libsqlite3.so.0', '/usr/lib/libncursesw.so.6.6']
    native = {path: put(root, path[1:]) for path in paths}
    glibc = inputs / 'glibc'; glibc.mkdir()
    source = put(glibc, 'sources/base.tar.gz', b'public glibc source')
    patch = put(glibc, 'patches/fix.patch', b'public glibc fix')
    put(glibc, 'rebuild.sh', b'public producer recipe')
    provenance = {'schema_version': 1, 'source': {'archive_sha256': source},
        'sources_and_tests': [{'path': 'sources/base.tar.gz', 'sha256': source}, {'path': 'patches/fix.patch', 'sha256': patch}],
        'patches': [{'cve': 'CVE-2026-100290'}], 'already_present_source_fixes': []}
    save(glibc / 'provenance.json', provenance)
    artifact = put(glibc, 'payload.tar.gz', b'public built glibc artifact')
    save(glibc / 'manifest.json', {'schema_version': 1, 'provenance_sha256': inventory.digest(glibc / 'provenance.json'),
        'payload': {'file': 'payload.tar.gz', 'sha256': artifact},
        'files': [{'path': path, 'sha256': native[path], 'type': 'file'} for path in paths[:2]]})
    openssl = inputs / 'openssl'; openssl.mkdir()
    source = put(openssl, 'sources/openssl-af1775b6.tar.gz', b'public OpenSSL source')
    patch = put(openssl, 'patches/fix.patch', b'public OpenSSL fix')
    recipe = put(openssl, 'rebuild.py', b'public OpenSSL producer')
    artifact = put(openssl, 'payload.tar.gz', b'public OpenSSL artifact')
    save(openssl / 'build-manifest.json', {'schema_version': 1, 'source_sha256': source,
        'inputs': [{'path': 'sources/openssl-af1775b6.tar.gz', 'sha256': source}, {'path': 'patches/fix.patch', 'sha256': patch}],
        'patches': [{'path': 'patches/fix.patch', 'sha256': patch}], 'recipe_sha256': recipe,
        'artifact_sha256': artifact, 'libraries': [{'install_path': path[1:], 'sha256': native[path]} for path in paths[2:4]],
        'upstream_fixes': [{'case': 'CVE-2026-88823'}]})
    crypto = inputs / 'cryptography'; crypto.mkdir()
    source = put(crypto, 'sources/cryptography-50.0.2.tar.gz', b'public cryptography source')
    ssl_source = put(crypto, 'sources/openssl-af1775b6.tar.gz', b'public static OpenSSL source')
    patch = put(crypto, 'patches/CVE-2026-88823.patch', b'public static OpenSSL fix')
    recipe = put(crypto, 'rebuild.py', b'public wheel producer')
    script = put(crypto, 'rebuild.sh', b'public wheel shell producer')
    wheel = put(crypto, 'custom.whl', b'public custom wheel artifact')
    rust = 'lib/python3.12/site-packages/cryptography/hazmat/bindings/_rust.abi3.so'
    rust_sha = put(venv, rust)
    save(crypto / 'build-manifest.json', {'schema_version': 1, 'source_inputs': [
        {'path': 'cryptography-50.0.2.tar.gz', 'sha256': source}, {'path': 'openssl-af1775b6.tar.gz', 'sha256': ssl_source},
        {'path': 'patches/CVE-2026-88823.patch', 'sha256': patch}], 'recipe_sha256': recipe,
        'rebuild_script_sha256': script, 'wheel': {'filename': 'custom.whl', 'sha256': wheel},
        'native_module': {'path': 'cryptography/hazmat/bindings/_rust.abi3.so', 'sha256': rust_sha}})
    retained = inputs / 'vendor-retained'; retained.mkdir()
    refs, components = [], []
    for name in ('sqlite', 'ncurses'):
        values = {}
        for kind in ('source', 'artifact', 'recipe'):
            filename = name + '-' + kind
            values[kind] = put(retained, filename, ('public ' + filename).encode())
            refs.append({'path': filename, 'sha256': values[kind]})
        components.append({'name': name, 'mode': 'signed_vendor', 'source_sha256': values['source'],
            'artifact_sha256': values['artifact'], 'recipe_sha256': values['recipe'], 'patches': [], 'cases': []})
    packages = [{'component': 'sqlite', 'package': 'sqlite-libs', 'version': '3.54.1-r0', 'control_checksum': 'Q1sqlite'},
                {'component': 'ncurses', 'package': 'ncurses', 'version': '6.6.20261107-r0', 'control_checksum': 'Q1ncurses'}]
    put(root, 'lib/apk/db/installed', '\n\n'.join('P:'+p['package']+'\nV:'+p['version']+'\nC:'+p['control_checksum']for p in packages).encode())
    save(retained / 'facts.json', {'schema_version': 1, 'inputs': refs, 'components': components, 'vendor_package_evidence': packages,
        'files': [{'path': path, 'resolved_path': path, 'sha256': native[path], 'component': name}
                  for path, name in zip(paths[4:], ('sqlite', 'ncurses'))]})
    return root, venv, inputs


def test_all_five_components_and_packaged_crypto_sources(layout):
    root, venv, inputs = layout
    value = inventory.build_inventory(root, venv, inputs)
    assert set(value) == {'schema_version', 'components', 'files'}
    assert {row['name'] for row in value['components']} == inventory.COMPONENTS
    assert len(value['files']) == 7
    assert next(row for row in value['files'] if row['component'] == 'cryptography')['path'].startswith('/opt/venv/lib/')


@pytest.mark.parametrize('relative', ['../outside', '/outside', 'double//name', 'folder/./name', 'E:/outside'])
def test_input_path_escape_rejected(tmp_path, relative):
    with pytest.raises(ValueError):
        inventory.input_file(tmp_path, relative)


def test_virtual_absolute_symlink_stays_inside_image_root(tmp_path):
    root = tmp_path / 'runtime'; put(root, 'usr/lib/libc.so.6')
    symlink(root / 'lib', '/usr/lib', True)
    physical, virtual = inventory.resolve_virtual(root, '/lib/libc.so.6')
    assert physical == root / 'usr/lib/libc.so.6'
    assert virtual == '/usr/lib/libc.so.6'


@pytest.mark.parametrize('kind', ['escape', 'cycle'])
def test_virtual_symlink_escape_and_cycle_rejected(tmp_path, kind):
    root = tmp_path / 'runtime'; (root / 'usr/lib').mkdir(parents=True)
    symlink(root / 'usr/lib/bad.so', '../../../outside' if kind == 'escape' else 'bad.so')
    with pytest.raises(ValueError):
        inventory.resolve_virtual(root, '/usr/lib/bad.so')


def test_source_symlink_outside_component_rejected(tmp_path):
    directory = tmp_path / 'inputs'; directory.mkdir(); put(tmp_path, 'outside', b'public source')
    symlink(directory / 'source', tmp_path / 'outside')
    with pytest.raises(ValueError):
        inventory.input_file(directory, 'source')


@pytest.mark.parametrize('target', ['native', 'source', 'wheel', 'recipe'])
def test_actual_bytes_must_match_all_descriptor_layers(layout, target):
    root, venv, inputs = layout
    path = {'native': root / 'usr/lib/libssl.so.4', 'source': inputs / 'glibc/sources/base.tar.gz',
            'wheel': inputs / 'cryptography/custom.whl', 'recipe': inputs / 'openssl/rebuild.py'}[target]
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(ValueError):
        inventory.build_inventory(root, venv, inputs)


def test_cryptography_binding_escape_rejected_even_when_bytes_match(layout, tmp_path):
    root, venv, inputs = layout
    original = venv / 'lib/python3.12/site-packages/cryptography/hazmat/bindings/_rust.abi3.so'
    put(tmp_path, 'outside-rust.so', original.read_bytes()); original.unlink()
    symlink(original, tmp_path / 'outside-rust.so')
    with pytest.raises(ValueError):
        inventory.build_inventory(root, venv, inputs)


def test_two_installed_cryptography_copies_rejected(layout):
    root, venv, inputs = layout
    put(venv, 'lib/python3.13/site-packages/cryptography/hazmat/bindings/_rust.abi3.so')
    with pytest.raises(ValueError):
        inventory.build_inventory(root, venv, inputs)


def test_all_five_components_are_required(layout):
    root, venv, inputs = layout
    (inputs / 'vendor-retained').rename(inputs / 'retired-facts')
    with pytest.raises(ValueError):
        inventory.build_inventory(root, venv, inputs)


def test_non_ELF_metadata_ignored_without_access(layout):
    root, _, _ = layout
    native = '/usr/lib/libcrypto.so.4'; expected = inventory.digest(root / native[1:])
    result = inventory.collect_files(root, 'openssl', {native: {'type': 'file', 'ELF': True, 'sha256': expected},
        '/etc/unused-private-data': {'type': 'file', 'ELF': False, 'sha256': '0' * 64}})
    assert len(result) == 1


def test_retained_vendor_version_rejected_even_with_identical_ELF_bytes(layout):
    root, venv, inputs = layout
    path = root / 'lib/apk/db/installed'
    path.write_bytes(path.read_bytes().replace(b'3.54.1-r0', b'3.54.1-r1'))
    with pytest.raises(ValueError):
        inventory.build_inventory(root, venv, inputs)


def test_signed_vendor_glibc_and_retained_identity_conflict(layout):
    root, venv, inputs = layout
    # A signed baseline is a distinct input: it cannot silently replace the
    # simultaneously supplied custom-backport descriptor.
    path = inputs / 'vendor-retained/facts.json'
    value = json.loads(path.read_bytes())
    item = {**value['components'][0], 'name': 'glibc', 'cases': []}
    value['components'].append(item)
    value['vendor_package_evidence'].append({'component': 'glibc', 'package': 'glibc-2.44',
        'version': '2.44-r17', 'control_checksum': 'Q1glibc'})
    db = root / 'lib/apk/db/installed'
    db.write_bytes(db.read_bytes() + b'\n\nP:glibc-2.44\nV:2.44-r17\nC:Q1glibc')
    save(path, value)
    with pytest.raises(ValueError, match='conflict'):
        inventory.build_inventory(root, venv, inputs)


def test_retained_signed_debian_identity_and_status(tmp_path):
    put(tmp_path, 'var/lib/dpkg/status', b'Package: libc6\nVersion: 2.41-12+deb13u4\nArchitecture: amd64\nStatus: install ok installed\n')
    evidence = [{'manager': 'dpkg', 'package': 'libc6', 'version': '2.41-12+deb13u4', 'architecture': 'amd64'}]
    inventory.retained_package_versions(tmp_path, evidence)
    path = tmp_path / 'var/lib/dpkg/status'
    path.write_bytes(path.read_bytes().replace(b'install ok installed', b'deinstall ok config-files'))
    with pytest.raises(ValueError):
        inventory.retained_package_versions(tmp_path, evidence)


def test_ELF_cannot_be_hidden_as_metadata(layout):
    root, _, _ = layout
    with pytest.raises(ValueError):
        inventory.collect_files(root, 'openssl', {'/usr/lib/libssl.so.4': {'type': 'file', 'ELF': False, 'sha256': '0' * 64}})


def test_native_alias_canonical_target_verified(layout):
    root, _, _ = layout
    symlink(root / 'usr/lib/alias.so', 'libssl.so.4')
    value = inventory.digest(root / 'usr/lib/libssl.so.4')
    with pytest.raises(ValueError):
        inventory.collect_files(root, 'openssl', {'/usr/lib/libssl.so.4': {'type': 'file', 'sha256': value},
            '/usr/lib/alias.so': {'type': 'symlink', 'target': 'libssl.so.4', 'resolved_path': '/usr/lib/wrong.so'}})


def test_package_set_aggregate_is_order_independent_and_bound_to_artifacts(tmp_path):
    a = put(tmp_path, 'a.deb', b'public package a'); b = put(tmp_path, 'b.deb', b'public package b')
    rows = [{'package': 'a', 'file': 'a.deb', 'sha256': a}, {'package': 'b', 'file': 'b.deb', 'sha256': b}]
    expected = hashlib.sha256(json.dumps([['a', a], ['b', b]], separators=(',', ':')).encode()).hexdigest()
    assert inventory.package_set_digest(tmp_path, rows) == expected == inventory.package_set_digest(tmp_path, list(reversed(rows)))
    with pytest.raises(ValueError):
        inventory.package_set_digest(tmp_path, [rows[0], rows[0]])


@pytest.mark.parametrize('raw', [b'{"schema_version":1,"schema_version":1}', b'{"schema_version":true}', b'{"schema_version":NaN}'])
def test_ambiguous_or_nonfinite_descriptor_rejected(tmp_path, raw):
    put(tmp_path, 'bad.json', raw)
    with pytest.raises(ValueError):
        inventory.load(tmp_path / 'bad.json')


def test_custom_rebuild_can_have_no_invented_patches():
    item = inventory.component('sqlite', 'custom_rebuild', *(['0' * 64] * 3), [], [])
    assert item['mode'] == 'custom_rebuild' and item['patches'] == []
    with pytest.raises(ValueError):
        inventory.component('sqlite', 'custom_backport', *(['0' * 64] * 3), [], [])


@pytest.mark.parametrize('name,patched', [('openssl', True), ('sqlite', False), ('ncurses', True)])
def test_debian_adapter_preserves_package_set_and_actual_upstream_archive(tmp_path, name, patched):
    source = put(tmp_path, 'source/' + name + '.orig.tar.xz', b'public upstream source')
    www = put(tmp_path, 'source/' + name + '.orig-www.tar.xz', b'public auxiliary website source')
    patch = put(tmp_path, 'patches/fix.patch', b'public reviewed fix')
    recipe = put(tmp_path, 'rebuild.py', b'public component recipe')
    docker = put(tmp_path, 'Dockerfile.rebuild', b'public build environment recipe')
    license_sha = put(tmp_path, 'licenses/COPYING', b'public license')
    a = put(tmp_path, 'payload/a.deb', b'public built package a')
    b = put(tmp_path, 'payload/b.deb', b'public built package b')
    packages = [{'package': name + '-a', 'file': 'payload/a.deb', 'sha256': a},
                {'package': name + '-b', 'file': 'payload/b.deb', 'sha256': b}]
    rows = {'/usr/lib/example.so': {'type': 'file', 'ELF': True, 'sha256': a}}
    save(tmp_path / 'runtime-candidate.json', {'schema_version': 1, 'component': name,
         'packages': packages, 'runtime_files': rows})
    save(tmp_path / 'source-manifest.json', {'schema_version': 1, 'component': name,
         'source_files': [{'path': 'source/' + name + '.orig.tar.xz', 'sha256': source},
                          {'path': 'source/' + name + '.orig-www.tar.xz', 'sha256': www}],
         'patch_files': [{'path': 'patches/fix.patch', 'sha256': patch}] if patched else [],
         'rebuild_script_sha256': recipe, 'case_scope': ['CVE-2026-88823'],
         'license_files': [{'path': 'licenses/COPYING', 'sha256': license_sha}],
         'rebuild_recipe': {'path': 'Dockerfile.rebuild', 'sha256': docker}})
    item, actual_rows = inventory.debian_descriptor(tmp_path, name)
    assert actual_rows == rows and item['source_sha256'] == source != www
    assert item['artifact_sha256'] == inventory.package_set_digest(tmp_path, packages)
    assert item['mode'] == ('custom_backport' if patched else 'custom_rebuild')
    assert item['patches'] == ([patch] if patched else [])
    put(tmp_path, 'licenses/COPYING', b'changed license')
    with pytest.raises(ValueError):
        inventory.debian_descriptor(tmp_path, name)


def test_cli_does_not_create_output_on_failed_verification(layout, monkeypatch):
    root, venv, inputs = layout
    put(root, 'usr/lib/libssl.so.4', ELF + b'changed')
    output = root / 'app/native_security_inventory.json'
    monkeypatch.setattr(sys, 'argv', ['build_native_inventory.py', '--root', str(root), '--venv', str(venv), '--inputs', str(inputs), '--output', str(output)])
    with pytest.raises(ValueError):
        inventory.main()
    assert not output.exists()


def test_cli_only_new_fixed_output_and_exact_schema(layout, monkeypatch):
    root, venv, inputs = layout
    output = root / 'app/native_security_inventory.json'
    monkeypatch.setattr(sys, 'argv', ['build_native_inventory.py', '--root', str(root), '--venv', str(venv), '--inputs', str(inputs), '--output', str(output)])
    inventory.main()
    assert set(json.loads(output.read_bytes())) == {'schema_version', 'components', 'files'}
    with pytest.raises(ValueError):
        inventory.main()


def test_input_size_bound_checked_before_hashing(tmp_path):
    path = tmp_path / 'oversize'
    with path.open('wb') as handle:
        handle.truncate(inventory.MAX_INPUT + 1)
    with pytest.raises(ValueError):
        inventory.digest(path)
