"""Rebuild the disclosed static backport using its pinned producer toolchain.

The native ELF must match the reviewed binary. A new toolchain intentionally
requires a new review if it produces different bytes. This script has no
application/provider credentials and never deploys or changes production.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4
import zipfile


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--context', default='desktop-linux')
    parser.add_argument('--toolchain-image')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    manifest = json.loads((here / 'build-manifest.json').read_bytes())
    out = args.output.resolve()
    if out.exists():
        raise RuntimeError('A new output directory is required')
    out.mkdir(parents=True)
    context, cache, outputs = out / 'context', out / 'cargo-cache', out / 'outputs'
    context.mkdir()
    cache.mkdir()
    outputs.mkdir()
    checks = []
    for row in manifest['source_inputs']:
        relative = Path(row['path'])
        source = here / ('patches' if relative.parts[0] == 'patches' else 'sources') / relative.name
        if digest(source) != row['sha256']:
            raise RuntimeError('Pinned source input mismatch')
        destination = context / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        checks.append(row['sha256'] + '  ' + relative.as_posix() + '\n')
    (context / 'SHA256SUMS').write_text(''.join(checks), newline='\n')
    script = (here / 'rebuild.sh').read_bytes()
    (context / 'build_static.sh').write_bytes(script)
    source = out / 'fetch-source'
    source.mkdir()
    # Only the audited cryptography Cargo manifest/lock files are needed for
    # cargo fetch; safe extraction rejects links and traversal before writes.
    import tarfile
    with tarfile.open(context / 'cryptography-50.0.2.tar.gz') as archive:
        members = archive.getmembers()
        if not all(not member.issym() and not member.islnk() and not member.isdev()
            and (source / member.name).resolve().is_relative_to(source) for member in members):
            raise RuntimeError('Unexpected source archive member')
        archive.extractall(source, filter='data')
    docker = ['docker', '--context', args.context]
    image = args.toolchain_image or manifest['toolchain_image']
    inspected = json.loads(subprocess.check_output(docker + ['image', 'inspect', image]))[0]['Id']
    if inspected != image:
        raise RuntimeError('An immutable toolchain image ID is required')
    owner = uuid4().hex
    common = ['run', '--rm', '--label', 'imladri.native.rebuild=' + owner, '--cpus', '2', '--memory', '4g',
              '--pids-limit', '512', '--cap-drop=ALL', '--security-opt=no-new-privileges:true']
    commands = [docker + common + ['--network', 'bridge', '--env', 'CARGO_HOME=/cargo',
        '--mount', f'type=bind,src={source},dst=/inputs,readonly',
        '--mount', f'type=bind,src={cache},dst=/cargo', image, 'cargo', 'fetch', '--locked',
        '--manifest-path', '/inputs/cryptography-50.0.2/Cargo.toml'],
        docker + common + ['--network', 'none', '--env', 'SOURCE_DATE_EPOCH=' + str(manifest['source_date_epoch']),
        '--mount', f'type=bind,src={context},dst=/inputs,readonly',
        '--mount', f'type=bind,src={cache},dst=/cargo-input,readonly',
        '--mount', f'type=bind,src={outputs},dst=/outputs', image, 'sh', '/inputs/build_static.sh']]
    for index, command in enumerate(commands):
        with (out / ('phase-' + str(index) + '.log')).open('xb') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=7200)
        if result.returncode:
            raise RuntimeError('Rebuild failed; preserve the bounded run logs')
    wheels = list((outputs / 'wheelhouse').glob('*.whl'))
    if len(wheels) != 1:
        raise RuntimeError('One native wheel required')
    with zipfile.ZipFile(wheels[0]) as archive:
        data = archive.read(manifest['native_module']['path'])
    if hashlib.sha256(data).hexdigest() != manifest['native_module']['sha256']:
        raise RuntimeError('Native binary differs; renewed source/runtime tests required')
    (out / 'verified.json').write_text(json.dumps({'native_ELF_reproduced': True,
        'sha256': manifest['native_module']['sha256'], 'provider_mutations': 0}, indent=2) + '\n')
    print(json.dumps({'native_ELF_reproduced': True, 'output': str(out)}))


if __name__ == '__main__':
    main()
