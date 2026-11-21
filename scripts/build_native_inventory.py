"""Verify native source/artifact descriptors and fingerprint installed ELF files.

Only the requested new JSON output is written. No installers, application data,
environment values, providers or database operations are used. Debian package
sets use SHA256(canonical JSON(sorted [(package, sha256), ...])); that aggregate
is an identity of the verified package set, never a claim of a file checksum.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat


HASH = re.compile(r'[0-9a-f]{64}')
CVE = re.compile(r'CVE-[0-9]{4}-[0-9]{4,}')
COMPONENTS = {'glibc', 'openssl', 'cryptography', 'sqlite', 'ncurses'}
EXECUTABLES = {'/usr/bin/infocmp', '/usr/bin/tic', '/usr/bin/openssl', '/usr/bin/clear',
    '/usr/bin/tabs', '/usr/bin/toe', '/usr/bin/tput', '/usr/bin/tset',
    '/usr/bin/ldconfig', '/usr/sbin/ldconfig', '/usr/sbin/ldconfig.real'}
MAX_INPUT = 128 * 1024 * 1024
MAX_ELF = 64 * 1024 * 1024


def require(value, reason):
    if not value:
        raise ValueError(reason)


def unique(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'duplicate JSON key')
        value[key] = item
    return value


def load(path):
    require(not path.is_symlink() and path.is_file() and 0 < path.stat().st_size <= 2 * 1024 * 1024,
            'bounded regular descriptor required')
    raw = path.read_bytes()
    require(len(raw) <= 2 * 1024 * 1024, 'descriptor size changed')
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: require(False, 'nonfinite JSON'))
    require(isinstance(value, dict) and type(value.get('schema_version')) is int and value['schema_version'] == 1,
            'descriptor schema version required')
    return value


def digest(path, maximum=MAX_INPUT):
    before = path.stat()
    require(not path.is_symlink() and stat.S_ISREG(before.st_mode) and 0 < before.st_size <= maximum,
            'bounded regular input required')
    value, size = hashlib.sha256(), 0
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            size += len(block)
            require(size <= maximum, 'input size changed')
            value.update(block)
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) and size == before.st_size,
            'input changed while hashing')
    return value.hexdigest()


def sha(value):
    require(isinstance(value, str) and HASH.fullmatch(value), 'SHA256 required')
    return value


def relative(path):
    require(isinstance(path, str) and path and not path.startswith('/') and '\\' not in path
            and ':' not in path and '//' not in path and all(p not in ('', '.', '..') for p in path.split('/')),
            'canonical relative input path required')
    require(PurePosixPath(path).as_posix() == path, 'canonical input path required')
    return path


def input_file(directory, name, expected=None):
    path = directory / relative(name)
    require(path.resolve(strict=True).is_relative_to(directory.resolve(strict=True)) and not path.is_symlink(),
            'source input escaped component directory')
    actual = digest(path)
    if expected is not None:
        require(actual == sha(expected), 'source or artifact hash mismatch')
    return actual


def references(directory, rows):
    require(isinstance(rows, list) and len(rows) <= 256, 'bounded input references required')
    result = {}
    for row in rows:
        require(isinstance(row, dict) and row['path'] not in result, 'unique source references required')
        result[row['path']] = input_file(directory, row['path'], row['sha256'])
    return result


def source_archive(refs):
    matches = [value for path, value in refs.items()
        if re.search(r'\.orig\.tar\.(gz|xz|bz2)$', path)]
    require(len(matches) == 1, 'one actual upstream source archive required')
    return matches[0]


def component(name, mode, source, artifact, recipe, patches, cases):
    require(name in COMPONENTS and mode in ('custom_backport', 'custom_rebuild', 'signed_vendor'), 'component mode required')
    require(isinstance(patches, list) and isinstance(cases, list), 'provenance lists required')
    patches = sorted(set(sha(value) for value in patches))
    require(len(patches) <= 64 and len(cases) <= 64, 'runtime reader provenance bounds')
    require(mode != 'custom_backport' or patches, 'backport requires actual patches')
    require(all(isinstance(case, str) and CVE.fullmatch(case) for case in cases), 'case identifiers required')
    return {'name': name, 'mode': mode, 'source_sha256': sha(source), 'artifact_sha256': sha(artifact),
            'recipe_sha256': sha(recipe), 'patches': patches, 'cases': sorted(set(cases))}


def package_set_digest(directory, rows):
    require(isinstance(rows, list) and 1 <= len(rows) <= 64, 'bounded package set required')
    pairs, names = [], set()
    for row in rows:
        require(isinstance(row['package'], str) and row['package'] not in names, 'unique package names required')
        names.add(row['package'])
        pairs.append((row['package'], input_file(directory, row['file'], row['sha256'])))
    raw = json.dumps(sorted(pairs), separators=(',', ':'), sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def glibc_descriptor(directory):
    value = load(directory / 'manifest.json')
    require(input_file(directory, 'provenance.json', value['provenance_sha256']), 'glibc provenance required')
    provenance = load(directory / 'provenance.json')
    refs = references(directory, provenance['sources_and_tests'])
    payload = value['payload']
    artifact = input_file(directory, payload['file'], payload['sha256'])
    if 'archive_sha256' in provenance['source']:
        source = sha(provenance['source']['archive_sha256'])
        require(source in refs.values(), 'glibc source archive must be verified')
    else:
        for path, expected in provenance['source']['archives'].items():
            require(refs.get(path) == sha(expected), 'glibc source chain differs')
        source = source_archive(refs)
    patches = [value for path, value in refs.items() if path.endswith('.patch')]
    cases = [row['cve'] for row in provenance.get('patches', [])] + provenance.get('already_present_source_fixes', [])
    rows = {row['path']: row for row in value['files']}
    require(len(rows) == len(value['files']), 'unique glibc paths required')
    return component('glibc', 'custom_backport', source, artifact, input_file(directory, 'rebuild.sh'), patches, cases), rows


def wolfi_ssl_descriptor(directory):
    value = load(directory / 'build-manifest.json')
    refs = references(directory, value['inputs'])
    require(refs.get('sources/openssl-af1775b6.tar.gz') == sha(value['source_sha256']), 'OpenSSL source must be verified')
    require(input_file(directory, 'rebuild.py', value['recipe_sha256']), 'OpenSSL recipe required')
    patches = references(directory, value['patches'])
    require(all(refs.get(path) == expected for path, expected in patches.items()), 'OpenSSL patches must be referenced')
    rows = {}
    for row in value['libraries']:
        path = '/' + relative(row['install_path'])
        require(path not in rows, 'unique OpenSSL files required')
        rows[path] = {'type': 'file', 'sha256': sha(row['sha256']), 'ELF': True}
    return component('openssl', 'custom_backport', value['source_sha256'], input_file(directory, 'payload.tar.gz', value['artifact_sha256']),
        value['recipe_sha256'], list(patches.values()), [row['case'] for row in value['upstream_fixes']]), rows


def debian_descriptor(directory, name):
    value = load(directory / 'runtime-candidate.json')
    provenance = load(directory / 'source-manifest.json')
    require(value['component'] == provenance['component'] == name, 'Debian component identity differs')
    refs = references(directory, provenance['source_files'])
    patches = references(directory, provenance['patch_files'])
    for key in ('license_files', 'auxiliary_inputs', 'evidence_files'):
        if key in provenance:
            references(directory, provenance[key])
    if 'rebuild_recipe' in provenance:
        references(directory, [provenance['rebuild_recipe']])
    recipe = input_file(directory, 'rebuild.py', provenance['rebuild_script_sha256'])
    mode = 'custom_backport' if patches else 'custom_rebuild'
    return component(name, mode, source_archive(refs), package_set_digest(directory, value['packages']), recipe,
        list(patches.values()), provenance['case_scope']), value['runtime_files']


def absolute(path):
    require(isinstance(path, str) and path.startswith('/') and '\\' not in path and '//' not in path
            and all(p not in ('', '.', '..') for p in path.split('/')[1:]) and PurePosixPath(path).as_posix() == path,
            'canonical absolute native path required')
    return path


def resolve_virtual(root, path):
    """Resolve image-root absolute links inside the image, never on the host."""
    absolute(path)
    todo, done, count = path.split('/')[1:], [], 0
    while todo:
        part, todo = todo[0], todo[1:]
        if part in ('', '.'):
            continue
        if part == '..':
            require(done, 'symlink escapes virtual root')
            done.pop()
            continue
        candidate = root.joinpath(*done, part)
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode):
            count += 1
            require(count <= 40, 'symlink cycle or bound')
            target = os.readlink(candidate)
            require('\\' not in target, 'native symlink syntax')
            if target.startswith('/'):
                done = []
            todo = target.split('/') + todo
        else:
            done.append(part)
    physical = root.joinpath(*done)
    require(physical.resolve(strict=True).is_relative_to(root), 'native path escaped virtual root')
    return physical, '/' + '/'.join(done)


def native_namespace(path):
    return path.startswith(('/usr/lib/', '/usr/local/lib/', '/opt/venv/lib/', '/lib/', '/lib64/', '/usr/bin/', '/usr/sbin/'))


def collect_files(root, name, rows):
    require(isinstance(rows, dict) and 1 <= len(rows) <= 4096, 'bounded native file descriptors required')
    regular, aliases, result = {}, [], []
    for declared, row in rows.items():
        absolute(declared)
        require(isinstance(row, dict), 'file descriptor required')
        if not native_namespace(declared):
            require(row.get('ELF') is not True, 'unexpected native executable namespace')
            continue  # No configuration, docs, application data, or secrets read.
        physical, resolved = resolve_virtual(root, declared)
        if 'resolved_path' in row:
            require(resolved == absolute(row['resolved_path']), 'declared canonical native target differs')
        if row.get('type') == 'symlink':
            if 'target' in row:
                parent, _ = resolve_virtual(root, PurePosixPath(declared).parent.as_posix())
                link = parent / PurePosixPath(declared).name
                require(link.is_symlink() and os.readlink(link) == row['target'], 'installed symlink text differs')
            # Resolve the actual target first; a canonical target record supplies
            # the expected hash. Do not hash arbitrary link targets implicitly.
            aliases.append((declared, physical, resolved, row))
            continue
        require(row.get('type', 'file') == 'file', 'only regular native files supported')
        require(physical.is_file() and not physical.is_symlink(), 'regular installed native file required')
        with physical.open('rb') as handle:
            is_elf = handle.read(4) == b'\x7fELF'
        if not is_elf:
            require(row.get('ELF') is not True, 'declared ELF is not ELF')
            continue
        require(row.get('ELF') is not False, 'ELF hidden as configuration')
        require(resolved.startswith(('/usr/lib/', '/usr/local/lib/', '/opt/venv/lib/')) or resolved in EXECUTABLES,
                'native file outside runtime reader scope')
        expected = sha(row['sha256'])
        require(digest(physical, MAX_ELF) == expected, 'installed native hash differs')
        require(resolved not in regular or regular[resolved] == expected, 'conflicting canonical native aliases')
        regular[resolved] = expected
        result.append({'path': declared, 'resolved_path': resolved, 'sha256': expected, 'component': name})
    for declared, physical, resolved, row in aliases:
        if physical.is_dir():
            continue
        with physical.open('rb') as handle:
            is_elf = handle.read(4) == b'\x7fELF'
        if not is_elf:
            continue
        require(resolved in regular, 'ELF symlink target absent from descriptor')
        require(digest(physical, MAX_ELF) == regular[resolved], 'native alias bytes differ')
        result.append({'path': declared, 'resolved_path': resolved, 'sha256': regular[resolved], 'component': name})
    require(result, 'component has no installed ELF files')
    return result


def retained_package_versions(root, evidence):
    """Retained vendor bytes must belong to the exact reviewed APK/deb identities."""
    require(isinstance(evidence, list) and 1 <= len(evidence) <= 8, 'bounded retained vendor package identities required')
    managers = {row.get('manager', 'apk') for row in evidence}
    require(len(managers) == 1 and managers <= {'apk', 'dpkg'}, 'one supported vendor package manager required')
    manager = next(iter(managers))
    path, _ = resolve_virtual(root, '/lib/apk/db/installed' if manager == 'apk' else '/var/lib/dpkg/status')
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 4 * 1024 * 1024,
            'bounded installed package database required')
    with path.open('rb') as handle:
        raw = handle.read(4 * 1024 * 1024 + 1)
    require(len(raw) <= 4 * 1024 * 1024, 'installed package database grew')
    keys = {'P', 'V', 'C'} if manager == 'apk' else {'Package', 'Version', 'Architecture', 'Status'}
    package_key = 'P' if manager == 'apk' else 'Package'
    packages = {}
    for record in raw.decode('utf8').split('\n\n'):
        fields = {}
        for line in record.splitlines():
            if ':' in line and line.split(':', 1)[0] in keys:
                key, value = line.split(':', 1)
                require(key not in fields, 'ambiguous installed package identity')
                fields[key] = value.strip() if manager == 'dpkg' else value
        if fields:
            require(set(fields) == keys and fields[package_key] not in packages, 'unique installed package identity required')
            packages[fields[package_key]] = fields
    seen = set()
    for expected in evidence:
        require(expected['package'] not in seen, 'duplicate retained package identity')
        seen.add(expected['package'])
        actual = packages.get(expected['package'])
        if manager == 'apk':
            require(actual and actual['V'] == expected['version'] and actual['C'] == expected['control_checksum'],
                    'retained vendor package version or control checksum changed')
        else:
            require(actual and actual['Version'] == expected['version'] and actual['Architecture'] == expected['architecture']
                    and actual['Status'] == 'install ok installed', 'retained Debian package identity changed')


def crypto_descriptor(directory, venv, virtual_venv):
    value = load(directory / 'build-manifest.json')
    # The producer records logical source names; the reviewed package stores
    # archives under sources/ and patch names already include patches/.
    packaged_refs = [{**row, 'path': row['path'] if row['path'].startswith('patches/')
                     else 'sources/' + relative(row['path'])} for row in value['source_inputs']]
    refs = references(directory, packaged_refs)
    source = refs.get('sources/cryptography-50.0.2.tar.gz')
    require(source, 'cryptography source archive required')
    recipe = input_file(directory, 'rebuild.py', value['recipe_sha256'])
    input_file(directory, 'rebuild.sh', value['rebuild_script_sha256'])
    wheel = value['wheel']
    artifact = input_file(directory, wheel['filename'], wheel['sha256'])
    native = value['native_module']
    suffix = relative(native['path'])
    matches = list(venv.glob('lib/python*/site-packages/' + suffix)) + list(venv.glob('lib64/python*/site-packages/' + suffix))
    candidates = {}
    for path in matches:
        require(path.resolve(strict=True).is_relative_to(venv), 'cryptography binding escaped venv')
        candidates[str(path.resolve(strict=True))] = path
    require(len(candidates) == 1, 'exactly one installed cryptography native module required')
    path = next(iter(candidates.values())).resolve(strict=True)
    require(not path.is_symlink() and digest(path, MAX_ELF) == sha(native['sha256']), 'installed cryptography bytes differ')
    with path.open('rb') as handle:
        require(handle.read(4) == b'\x7fELF', 'cryptography binding must be ELF')
    declared = virtual_venv.rstrip('/') + '/' + path.relative_to(venv).as_posix()
    absolute(declared)
    patches = [expected for path, expected in refs.items() if path.endswith('.patch')]
    cases = sorted(set(case for path in refs for case in re.findall(r'CVE-[0-9]{4}-[0-9]{4,}', path)))
    return component('cryptography', 'custom_backport', source, artifact, recipe, patches, cases), [
        {'path': declared, 'resolved_path': declared, 'sha256': native['sha256'], 'component': 'cryptography'}]


def build_inventory(root, venv, inputs, *, virtual_venv=None):
    root, venv, inputs = Path(root).resolve(strict=True), Path(venv).resolve(strict=True), Path(inputs).resolve(strict=True)
    require(root.is_dir() and venv.is_dir() and inputs.is_dir(), 'existing roots required')
    if virtual_venv is None:
        virtual_venv = venv.as_posix() if root == Path('/') else '/opt/venv'
    absolute(virtual_venv)
    components, files = {}, []
    for name in ('glibc', 'openssl', 'sqlite', 'ncurses'):
        directory = inputs / name
        if not directory.exists():
            continue
        require(not directory.is_symlink() and directory.resolve().is_relative_to(inputs), 'component directory escaped inputs')
        if name == 'glibc':
            item, rows = glibc_descriptor(directory)
        elif (directory / 'runtime-candidate.json').is_file():
            require(not (directory / 'build-manifest.json').exists(), 'ambiguous component descriptors')
            item, rows = debian_descriptor(directory, name)
        else:
            require(name == 'openssl', 'unsupported component descriptor')
            item, rows = wolfi_ssl_descriptor(directory)
        components[name] = item
        files.extend(collect_files(root, name, rows))
    directory = inputs / 'cryptography'
    require(directory.is_dir() and not directory.is_symlink(), 'cryptography component required')
    item, rows = crypto_descriptor(directory, venv, virtual_venv)
    components['cryptography'] = item
    files.extend(rows)
    retained = inputs / 'vendor-retained'
    if retained.exists():
        value = load(retained / 'facts.json')
        refs = references(retained, value['inputs'])
        retained_package_versions(root, value['vendor_package_evidence'])
        require({row['component'] for row in value['vendor_package_evidence']} == {row['name'] for row in value['components']},
                'each retained component must have package identity evidence')
        for item in value['components']:
            name = item['name']
            require(name in ('glibc', 'sqlite', 'ncurses') and name not in components and item['mode'] == 'signed_vendor', 'retained component conflict')
            require(all(item[key] in refs.values() for key in ('source_sha256', 'artifact_sha256', 'recipe_sha256')), 'vendor provenance not tied to input bytes')
            components[name] = component(name, item['mode'], item['source_sha256'], item['artifact_sha256'], item['recipe_sha256'], item['patches'], item['cases'])
            rows = {row['path']: {**row, 'type': 'file', 'ELF': True} for row in value['files'] if row['component'] == name}
            # Retained facts contain resolved targets for aliases. Their actual
            # virtual-root resolution and hash are still rechecked below.
            for row in rows.values():
                if row['path'] != row['resolved_path']:
                    row['type'] = 'symlink'
            files.extend(collect_files(root, name, rows))
    require(set(components) == COMPONENTS, 'all five native components are required')
    require(len({row['path'] for row in files}) == len(files), 'native paths duplicated across components')
    require(len(files) <= 1024, 'runtime reader file bound')
    covered = {name: {PurePosixPath(row['resolved_path']).name for row in files if row['component'] == name}
               for name in components}
    require('libc.so.6' in covered['glibc'] and any(name.startswith('ld-linux') for name in covered['glibc'])
            and any(name.startswith('libssl.so.') for name in covered['openssl'])
            and any(name.startswith('libcrypto.so.') for name in covered['openssl'])
            and any(name.startswith('_rust.') and name.endswith('.so') for name in covered['cryptography'])
            and any(name.startswith('libsqlite3.so.') for name in covered['sqlite'])
            and any(name.startswith(('libncurses.so.', 'libncursesw.so.')) for name in covered['ncurses']),
            'all required native library families must be inventoried')
    return {'schema_version': 1, 'components': [components[name] for name in sorted(components)],
            'files': sorted(files, key=lambda row: (row['component'], row['path']))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--venv', required=True, type=Path)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    output = args.output
    require(output.name == 'native_security_inventory.json' and output.parent.resolve(strict=True) == root / 'app'
            and not output.exists() and not output.is_symlink(), 'only a new fixed application inventory output is allowed')
    value = build_inventory(root, args.venv, args.inputs)
    with output.open('x', encoding='utf8', newline='\n') as handle:
        json.dump(value, handle, sort_keys=True, separators=(',', ':'))
        handle.write('\n')
    print(json.dumps({'native_components_verified': len(value['components']), 'installed_ELF_and_alias_rows': len(value['files'])}))


if __name__ == '__main__':
    main()
