"""Offline reproducible producer for the custom OpenSSL bundle.

Run in the recorded immutable Wolfi toolchain, with this directory at /inputs,
no network and a clean /build. The output is not authorized for deployment by
this script; it must exactly reproduce the reviewed library hashes.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile


HERE = Path(__file__).resolve().parent
SOURCE = Path('/build/openssl')
OUTPUT = Path('/output')
EPOCH = '1794528477'
CFLAGS = '-O2 -g -ffile-prefix-map=/build/openssl=/usr/src/imladri-openssl-4.0.3'
FLAGS = ['--prefix=/usr', '--libdir=lib', '--openssldir=/etc/ssl', 'enable-brotli',
    'enable-ec_nistp_64_gcc_128', 'enable-ktls', 'enable-pie', 'no-aria', 'enable-bf',
    'enable-camellia', 'enable-cast', 'no-dtls1', 'no-ec_explicit_curves', 'no-ec2m',
    'no-gost', 'no-idea', 'no-integrity-only-ciphers', 'no-mdc2', 'enable-ocb',
    'enable-psk', 'no-pvkkdf', 'enable-rc2', 'enable-rc4', 'enable-rc5',
    'enable-rmd160', 'no-seed', 'no-sm2', 'no-sm2-precomp', 'enable-sm3', 'no-sm4',
    'enable-srp', 'no-tls1', 'no-tls1_1', 'no-weak-ssl-ciphers', 'no-whirlpool', '-Wa,--noexecstack']


def main():
    manifest = json.loads((HERE / 'build-manifest.json').read_bytes())
    for item in manifest['inputs']:
        path = HERE / item['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise RuntimeError('Source input differs from the reviewed bundle')
    if SOURCE.exists():
        raise RuntimeError('A clean /build/openssl is required')
    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    archive = HERE / 'sources/openssl-af1775b6.tar.gz'
    with tarfile.open(archive) as source:
        members = source.getmembers()
        prefix = 'openssl-af1775b60dfa141a4ad762585052cabeb9f37e9e'
        if not all(member.name == prefix or member.name.startswith(prefix + '/') for member in members):
            raise RuntimeError('Unexpected upstream archive root')
        source.extractall(SOURCE.parent, filter='data')
    (SOURCE.parent / prefix).rename(SOURCE)
    env = dict(os.environ, SOURCE_DATE_EPOCH=EPOCH, CFLAGS=CFLAGS)
    patches = ['patches/upstream/vendor-x509-7c7b9684.patch',
        'patches/vendor/0001-fips-block-HMAC-calculation-with-unapproved-digests.patch',
        'patches/vendor/0001-fips-retain-FIPS-140-2-TLSv1.2-connectivity.patch',
        'patches/vendor/0001-Remove-pre-share-from-default-TLS-groups.patch',
        'patches/vendor/0001-Look-for-providers-in-ossl-modules-4-before-ossl-mod.patch',
        'patches/vendor/0001-Revert-fips-Align-PKCS5_PBKDF2_HMAC-defaults-with-EV.patch',
        'patches/vendor/0001-Chainguard-Cryptographic-Hardening.patch']
    for case in ('88823', '107496', '88822', '95692', '95691'):
        paths = list((HERE / 'patches/upstream').glob('CVE-2026-' + case + '-*.patch'))
        if len(paths) != 1:
            raise RuntimeError('One exact reviewed upstream fix is required')
        patches.append(paths[0].relative_to(HERE).as_posix())
    for patch in patches:
        subprocess.run(['patch', '--batch', '--forward', '--fuzz=0', '-p1', '-i', str(HERE / patch)],
            cwd=SOURCE, env=env, check=True)
    subprocess.run(['perl', './Configure', *FLAGS], cwd=SOURCE, env=env, check=True)
    subprocess.run(['make', '-j2', 'build_libs', 'apps/openssl'], cwd=SOURCE, env=env, check=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for item in manifest['libraries']:
        name = Path(item['install_path']).name
        target = OUTPUT / name
        target.write_bytes((SOURCE / name).read_bytes())
        subprocess.run(['strip', '--strip-debug', str(target)], check=True)
        if hashlib.sha256(target.read_bytes()).hexdigest() != item['sha256']:
            raise RuntimeError('Rebuilt artifact does not match the reviewed runtime bytes')
    print(json.dumps({'reviewed_libraries_reproduced_exactly': 2, 'SOURCE_DATE_EPOCH': EPOCH}))


if __name__ == '__main__':
    main()
