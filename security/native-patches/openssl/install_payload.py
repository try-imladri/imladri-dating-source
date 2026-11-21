"""Verify and install only the reviewed two-library OpenSSL backport in /runtime."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tarfile


HERE = Path(__file__).resolve().parent
NAMES = ('usr/lib/libcrypto.so.4', 'usr/lib/libssl.so.4')
HASH = re.compile(r'[0-9a-f]{64}')
MAX_SOURCE = 100 * 1024 * 1024
MAX_ARTIFACT = 32 * 1024 * 1024


def require(condition):
    if not condition:
        raise RuntimeError('OpenSSL native bundle failed verification')


def unique(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value)
        value[key] = item
    return value


def digest(path, limit=MAX_SOURCE):
    before = path.stat()
    require(not path.is_symlink() and stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit)
    value, total = hashlib.sha256(), 0
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            total += len(block)
            require(total <= limit)
            value.update(block)
    after = path.stat()
    require(total == before.st_size and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns))
    return value.hexdigest()


def safe_parent(target, root):
    require(target.is_relative_to(root))
    for parent in (target.parent, *target.parent.parents):
        if parent == root:
            break
        require(not parent.is_symlink() and (not parent.exists() or parent.is_dir()))
        require(parent.resolve(strict=False).is_relative_to(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='/runtime')
    args = parser.parse_args()
    require(args.root == '/runtime')
    root = Path(args.root).resolve(strict=True)
    require(root == Path('/runtime') and root.is_dir())
    manifest = HERE / 'build-manifest.json'
    require(not manifest.is_symlink() and manifest.is_file() and 0 < manifest.stat().st_size <= 256 * 1024)
    raw = manifest.read_bytes()
    require(len(raw) <= 256 * 1024)
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: require(False))
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and value['classification'] == 'custom_backport')
    require(isinstance(value['libraries'], list) and len(value['libraries']) == 2
            and tuple(item['install_path'] for item in value['libraries']) == NAMES)
    require(isinstance(value['inputs'], list) and 5 <= len(value['inputs']) <= 64)
    seen = {}
    for item in value['inputs']:
        require(isinstance(item, dict) and isinstance(item['path'], str) and item['path'] not in seen
                and isinstance(item['sha256'], str) and HASH.fullmatch(item['sha256']))
        path = HERE / item['path']
        require(path.resolve(strict=True).is_relative_to(HERE) and not path.is_symlink()
                and digest(path) == item['sha256'])
        seen[item['path']] = item['sha256']
    require(seen.get('sources/openssl-af1775b6.tar.gz') == value['source_sha256']
            and seen.get('rebuild.py') == value['recipe_sha256']
            and seen.get('Dockerfile.rebuild') == value['toolchain_recipe_sha256']
            and all(name in seen for name in ('LICENSE-OpenSSL.txt', 'LICENSE-Wolfi-OS.txt'))
            and all(seen.get(patch['path']) == patch['sha256'] for patch in value['patches']))
    archive = HERE / 'payload.tar.gz'
    require(archive.stat().st_size <= MAX_ARTIFACT and digest(archive, MAX_ARTIFACT) == value['artifact_sha256'])
    provenance = root / 'usr/share/imladri-native'
    for name in ('openssl-build-manifest.json', 'LICENSE-OpenSSL.txt', 'LICENSE-Wolfi-OS.txt'):
        destination = provenance / name
        safe_parent(destination, root)
        require(not destination.is_symlink() and (not destination.exists() or destination.is_file()))
    verified = []
    # Stream headers so a declared oversized member is rejected before the
    # tar reader can seek through and decompress its body.
    with tarfile.open(archive, 'r|gz') as package:
        for index, member in enumerate(package):
            require(index < len(NAMES) and member.name == NAMES[index])
            item = value['libraries'][index]
            require(member.isfile() and not member.issym() and not member.islnk()
                    and type(item['bytes']) is int and 4 <= member.size <= MAX_ARTIFACT
                    and member.size == item['bytes'] and member.mode == 0o444
                    and isinstance(item['sha256'], str) and HASH.fullmatch(item['sha256'])
                    and isinstance(item['baseline_sha256'], str) and HASH.fullmatch(item['baseline_sha256']))
            content = package.extractfile(member).read(member.size + 1)
            require(len(content) == member.size and content[:4] == b'\x7fELF'
                    and hashlib.sha256(content).hexdigest() == item['sha256'])
            target = root / member.name
            safe_parent(target, root)
            require(target.parent.resolve(strict=True).is_relative_to(root)
                    and not target.is_symlink() and target.is_file()
                    and digest(target, MAX_ARTIFACT) == item['baseline_sha256'])
            temporary = target.with_name(target.name + '.imladri-native-staging')
            require(not temporary.exists() and not temporary.is_symlink())
            verified.append((target, content, item))
    require(len(verified) == len(NAMES))
    # Both baseline files and both payload members must pass before any write.
    for target, content, item in verified:
        temporary = target.with_name(target.name + '.imladri-native-staging')
        with temporary.open('xb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o444)
        temporary.replace(target)
        require(digest(target) == item['sha256'])
    # The original APK database is retained. This marker explicitly identifies
    # the local replacements and must accompany all native inventory reviews.
    provenance.mkdir(parents=True, exist_ok=True)
    (provenance / 'openssl-build-manifest.json').write_bytes(raw)
    (provenance / 'LICENSE-OpenSSL.txt').write_bytes((HERE / 'LICENSE-OpenSSL.txt').read_bytes())
    (provenance / 'LICENSE-Wolfi-OS.txt').write_bytes((HERE / 'LICENSE-Wolfi-OS.txt').read_bytes())
    print(json.dumps({'custom_openssl_libraries_verified': 2, 'signed_vendor_binary_equivalence': False}))


if __name__ == '__main__':
    main()
