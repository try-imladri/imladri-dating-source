"""Verify immutable API/Render images using fixture credentials and isolated MongoDB.

The candidate never receives host environment variables, provider credentials or a
production database address. All application traffic stays in a network-disabled
Docker namespace shared only with its disposable MongoDB.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import time
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PREFIX = 'IMLADRI_RUNTIME_FINGERPRINT_v2 '
MANIFEST_RUNNER = 'import json; from backend.runtime_manifest import runtime_manifest; print(json.dumps(runtime_manifest()))'
NATIVE_PROBE = r'''
from pathlib import Path
import hashlib, importlib.metadata, importlib.util, json, os, platform, shutil, ssl, stat, sys
import bcrypt, bson, cryptography, pydantic, pymongo, pyexpat, sqlite3
from decimal import Decimal, localcontext
from cryptography.fernet import Fernet

for unused in ('numpy', 'pandas', 'jq', 'pytest', 'black', 'isort', 'flake8', 'mypy'):
    assert importlib.util.find_spec(unused) is None, unused + ' is not a production dependency'
with localcontext() as decimal_context:
    decimal_context.prec = 80
    assert Decimal('12.00') * 100 == 1200
    assert (Decimal(1) / Decimal(7)).quantize(Decimal('.0001')) == Decimal('.1429')
    assert (Decimal('0.1') + Decimal('0.2')) == Decimal('0.3')
pruning = json.loads(Path('/app/backend/runtime_pruning.json').read_bytes())
assert pruning['status'] == 'applied'
assert pruning['apk_database_sha256_before'] == pruning['apk_database_sha256_after']
assert not pruning['remaining_removed_library_dynamic_dependencies']
for removed in pruning['removed_files']:
    assert not Path(removed['path']).exists() and not Path(removed['path']).is_symlink()
import curses, curses.panel
password = b'fixture only'
assert bcrypt.checkpw(password, bcrypt.hashpw(password, bcrypt.gensalt(rounds=4)))
fernet = Fernet(Fernet.generate_key())
assert fernet.decrypt(fernet.encrypt(password)) == password
assert bson.BSON.encode({'fixture': 3}).decode()['fixture'] == 3
class Model(pydantic.BaseModel):
    value: int
assert Model.model_validate({'value': '3'}).value == 3
with sqlite3.connect(':memory:') as db:
    db.execute('create table fixture (value integer)')
    db.execute('insert into fixture values (?)', (3,))
    assert db.execute('select sum(value) from fixture').fetchone()[0] == 3
    assert db.execute('pragma integrity_check').fetchone()[0] == 'ok'
context = ssl.create_default_context()
assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
assert context.cert_store_stats()['x509_ca'] > 0
shells = [name for name in ('sh', 'ash', 'bash', 'dash', 'zsh', 'busybox') if shutil.which(name)]
managers = [name for name in ('apk', 'apt', 'apt-get', 'dpkg', 'rpm', 'yum', 'dnf', 'pip', 'pip3') if shutil.which(name)]
privileged = set()
for directory in ('/usr', '/bin', '/sbin', '/lib', '/opt', '/app'):
    for path in Path(directory).rglob('*'):
        try:
            if path.is_file() and path.stat().st_mode & (stat.S_ISUID | stat.S_ISGID):
                privileged.add(str(path.resolve()))
        except (FileNotFoundError, PermissionError):
            continue
caps = next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:'))
assert os.getuid() == os.getgid() == 65532, (os.getuid(), os.getgid())
assert int(caps, 16) == 0, caps
assert not shells, shells
assert not managers, managers
assert not importlib.util.find_spec('pip'), 'pip module remains in the production image'
assert not privileged, sorted(privileged)
files = []
for path in sorted(Path('/app/backend').rglob('*')):
    if path.is_file() and path.suffix in {'.py', '.json', '.html', '.jpg', '.png'}:
        data = path.read_bytes()
        if path.suffix in {'.py', '.json', '.html'}:
            data = data.replace(b'\r\n', b'\n')
        files.append((str(path.relative_to('/app')), hashlib.sha256(data).hexdigest()))
packages = sorted({(dist.metadata['Name'], dist.version) for dist in importlib.metadata.distributions()})
libraries = sorted({line.rsplit(None, 1)[-1] for line in Path('/proc/self/maps').read_text().splitlines() if '/' in line and '.so' in line})
print(json.dumps({'python': sys.version.split()[0], 'os': platform.freedesktop_os_release(),
    'uid': os.getuid(), 'gid': os.getgid(), 'effective_capabilities': caps,
    'shells': shells, 'package_managers': managers, 'privileged_helpers': sorted(privileged),
    'openssl': ssl.OPENSSL_VERSION, 'expat': pyexpat.EXPAT_VERSION, 'sqlite': sqlite3.sqlite_version,
    'ca_certificates': context.cert_store_stats()['x509_ca'],
    'native_operations': ['decimal', 'bcrypt', 'cryptography', 'bson', 'pydantic', 'sqlite'],
    'runtime_pruning': {'removed_files': len(pruning['removed_files']), 'package_database_preserved': True},
    'loaded_libraries': libraries, 'python_distributions': packages,
    'application_files': {'count': len(files), 'sha256': hashlib.sha256(json.dumps(files, separators=(',', ':')).encode()).hexdigest()}}))
'''

REGRESSION_RUNNER = r'''
from pathlib import Path
import json, platform, pytest, sys
files = sorted(str(path) for path in Path('backend/tests').glob('test_*.py') if not path.name.startswith('test_imaldri'))
print(json.dumps({'python': sys.version, 'os': platform.freedesktop_os_release(), 'test_files': len(files)}), flush=True)
raise SystemExit(pytest.main(['-q', '-c', '/app/backend/pytest.ini', '--disable-warnings', '--tb=short', '-p', 'no:cacheprovider', *files]))
'''

ROLE_PROBE = r'''
import hashlib, json, os, re, time, urllib.error, urllib.request
from pathlib import Path
role = os.environ['QA_ROLE']
base = 'http://127.0.0.1:' + os.environ['PORT']
def get(path, headers=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers or {}), timeout=4) as response:
            return response.status, {key.lower(): value for key, value in response.headers.items()}, response.read()
    except urllib.error.HTTPError as error:
        return error.code, {key.lower(): value for key, value in error.headers.items()}, error.read()
for _ in range(160):
    try:
        code, _, body = get('/api/ready')
        if code == 200 and json.loads(body)['ready']:
            break
    except (OSError, ValueError):
        pass
    time.sleep(.5)
else:
    raise AssertionError('The isolated candidate did not become ready')
headers = {'X-Imladri-Origin': 'fixture-origin-only'} if role == 'standby' else {}
status, _, body = get('/api/public/plans', headers)
assert status == 200 and len(json.loads(body)['plans']) == 3
assert get('/api/admin/prompts', headers)[0] == 401
catalog = {}
for name, expected in [('figures', 500), ('archetypes', 121)]:
    code, _, body = get('/api/public/' + name, headers)
    assert code == 200 and len(json.loads(body)) == expected, (name, code)
    catalog[name] = expected
result = {'ready': True, 'plans': 3, 'admin_without_session': 401, 'catalog': catalog}
if role == 'api':
    fields = dict(line.split(':', 1) for line in Path('/proc/1/status').read_text().splitlines() if ':' in line)
    security = {name: fields[name].strip() for name in ('CapEff', 'CapPrm', 'CapInh', 'NoNewPrivs')}
    assert all(int(security[name], 16) == 0 for name in ('CapEff', 'CapPrm', 'CapInh'))
    assert security['NoNewPrivs'] == '1'
    assert set(fields['Uid'].split()) == {'65532'}
    result['main_process_security'] = security
if role == 'standby':
    assert get('/api/public/plans')[0] == 404
    result['without_origin'] = 404
    result['authorized_origin'] = 200
elif role == 'preview':
    assets = {}
    for path in ['/', '/figures', '/app/start']:
        code, content_headers, html = get(path, {'Accept': 'text/html'})
        assert code == 200 and 'text/html' in content_headers['content-type'], (path, code)
        names = re.findall(rb'(?:src|href)="(/static/[^" ]+\.(?:js|css))"', html)
        assert any(name.endswith(b'.js') for name in names)
        for name in names:
            asset_status, _, content = get(name.decode())
            assert asset_status == 200 and not content.lower().lstrip().startswith(b'<!doctype')
            assets[name.decode()] = hashlib.sha256(content).hexdigest()
    assert get('/static/fixture-missing.js')[0] == 404
    result.update(spa_paths=['/', '/figures', '/app/start'], missing_asset=404, assets=assets)
print(json.dumps(result))
'''


def fixture_environment(database, role=None):
    """Only literal nonproduction credentials enter the candidate namespace."""
    values = {
        'PYTHON_DOTENV_DISABLED': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'MONGO_URL': 'mongodb://127.0.0.1:27017',
        'IMLADRI_TEST_MONGO_URL': 'mongodb://127.0.0.1:27017',
        'IMLADRI_BILLING_TEST_MONGO': 'mongodb://127.0.0.1:27017',
        'IMLADRI_TEST_MONGO': 'mongodb://127.0.0.1:27017',
        'DB_NAME': database,
        'CLERK_PUBLISHABLE_KEY': 'pk_test_Zml4dHVyZS5jbGVyay5hY2NvdW50cy5kZXYk',
        'CLERK_SECRET_KEY': 'sk_test_fixture',
        'PUBLIC_APP_URL': 'https://app.example.test',
        'PERSONAL_BILLING_MODE': 'enabled' if role is None else 'disabled',
        'RENDER_ORIGIN_SECRET': 'fixture-origin-only',
        'DEEPSEEK_API_KEY': '', 'STRIPE_SECRET_KEY': '',
        'CLERK_WEBHOOK_SIGNING_SECRET': '', 'STRIPE_WEBHOOK_SECRET': '',
        'OPS_CHECK_SECRET': '', 'CF_DIAG_EGRESS_ON_START': 'false',
        'PYTHONPATH': '/app:/app/backend',
    }
    if role:
        if role not in {'api', 'preview', 'standby'}:
            raise ValueError('Unknown runtime role')
        values.update(QA_ROLE=role, PORT='8042' if role != 'api' else '8080')
    return values


def test_mounts(root=ROOT):
    return [f'type=bind,source={root / source},target=/app/{target},readonly'
            for source, target in [('backend/tests', 'backend/tests'),
                                   ('frontend/public', 'frontend/public'),
                                   ('scripts', 'scripts'), ('security', 'security'),
                                   ('backend/pytest.ini', 'backend/pytest.ini')]]


def application_command(image_id, network, database, role=None, name=None, regressions=False):
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image_id):
        raise ValueError('Run a resolved immutable image ID')
    if not re.fullmatch(r'imladri-runtime-mongo-[a-f0-9]{12}', network):
        raise ValueError('Use only the private disposable MongoDB namespace')
    command = ['run', '--rm', '--network', 'container:' + network, '--workdir', '/app']
    if name:
        command += ['-d', '--name', name]
    for key, value in fixture_environment(database, role).items():
        command += ['--env', key + '=' + value]
    if regressions:
        for mount in test_mounts():
            command += ['--mount', mount]
        command += ['--entrypoint', 'python', image_id, '-c', REGRESSION_RUNNER]
    elif role == 'api':
        # Exercise the actual Cloudflare CMD and its database-wait/exec sequence.
        command.append(image_id)
    else:
        command += ['--entrypoint', 'python', image_id, '-m', 'backend.render_entrypoint', role]
    return command


def docker(*arguments, timeout=600):
    result = subprocess.run(['docker', *arguments], cwd=ROOT, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'Docker {arguments[0]} failed ({result.returncode}):\n{result.stdout[-20000:]}')
    return result.stdout.strip()


def resolve_image(reference):
    details = json.loads(docker('image', 'inspect', reference))[0]
    return {'reference': reference, 'id': details['Id'], 'repo_digests': details.get('RepoDigests', [])}


def validate_qa_overlay(production, qa_manifest, production_packages, qa_packages):
    """QA may add test tools, but cannot replace the app, OS or runtime dependencies."""
    for field in ('application_files', 'apk_database', 'uid', 'gid', 'effective_capabilities'):
        if production[field] != qa_manifest[field]:
            raise ValueError('QA image differs from the production base: ' + field)
    normalize = lambda name: name.lower().replace('_', '-')
    installed = {normalize(name): version for name, version in qa_packages}
    required = {normalize(name): version for name, version in production_packages}
    if any(installed.get(name) != version for name, version in required.items()):
        raise ValueError('QA tools replaced a production dependency')
    allowed = {'pytest', 'pytest-xdist', 'execnet', 'iniconfig', 'packaging', 'pluggy', 'pygments'}
    if set(installed) - set(required) - allowed:
        raise ValueError('Unexpected package in the QA overlay')


def startup_manifest(log):
    markers = [line[len(MANIFEST_PREFIX):] for line in log.splitlines() if line.startswith(MANIFEST_PREFIX)]
    if len(markers) != 1:
        raise ValueError('The runtime must emit exactly one startup attestation')
    value = json.loads(markers[0])
    if value.get('schema_version') != 2 or 'attestation_error' in value:
        raise ValueError('The startup attestation is incomplete')
    return value


def atomic_json(path, payload):
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf8')
    temporary.replace(path)


def verify(api_image, render_image, output, skip_regressions=False, qa_image=None):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    images = {'api': resolve_image(api_image)}
    if render_image:
        images['render'] = resolve_image(render_image)
    qa = resolve_image(qa_image) if qa_image else None
    report = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'images': images,
              'isolation': 'network none; only disposable loopback MongoDB; literal fixture credentials',
              'production_changed': False, 'runtime': {}, 'roles': {}, 'regressions': None,
              'expected_attestations': {}, 'qa_image': qa}
    label = uuid4().hex[:12]
    mongo_name = 'imladri-runtime-mongo-' + label
    started = False
    try:
        for role, image in images.items():
            report['runtime'][role] = json.loads(docker('run', '--rm', '--network', 'none',
                '--read-only', '--entrypoint', 'python', image['id'], '-c', NATIVE_PROBE))
            report['expected_attestations'][role] = json.loads(docker('run', '--rm', '--network', 'none',
                '--read-only', '--entrypoint', 'python', image['id'], '-c', MANIFEST_RUNNER))
        if render_image:
            assert report['runtime']['api']['application_files'] == report['runtime']['render']['application_files']
            assert report['runtime']['api']['python_distributions'] == report['runtime']['render']['python_distributions']
        if qa:
            qa_manifest = json.loads(docker('run', '--rm', '--network', 'none', '--read-only',
                '--entrypoint', 'python', qa['id'], '-c', MANIFEST_RUNNER))
            package_program = "import importlib.metadata as m,json;print(json.dumps(sorted({(d.metadata['Name'],d.version) for d in m.distributions()})))"
            qa_packages = json.loads(docker('run', '--rm', '--network', 'none', '--read-only',
                '--entrypoint', 'python', qa['id'], '-c', package_program))
            validate_qa_overlay(report['expected_attestations']['api'], qa_manifest,
                                report['runtime']['api']['python_distributions'], qa_packages)
            report['qa_overlay_matches_production_base'] = True
        docker('run', '-d', '--rm', '--network', 'none', '--name', mongo_name, 'mongo:8.0')
        started = True
        for _ in range(80):
            try:
                docker('exec', mongo_name, 'mongosh', '--quiet', '--eval', 'quit(db.adminCommand({ping:1}).ok ? 0 : 1)', timeout=10)
                break
            except RuntimeError:
                time.sleep(.5)
        else:
            raise RuntimeError('The isolated MongoDB did not become ready')
        if not skip_regressions:
            regression_image = qa['id'] if qa else images['api']['id']
            log = docker(*application_command(regression_image, mongo_name,
                         'imladri_runtime_test_' + label, regressions=True))
            log_path = output.with_name(output.stem + '-regressions.log')
            log_path.write_text(log + '\n', encoding='utf8')
            summary = next((line for line in reversed(log.splitlines()) if re.search(r'\d+ passed', line)), None)
            assert summary and not re.search(r'\d+ (?:failed|error)', summary), log[-3000:]
            report['regressions'] = {'summary': summary, 'log': str(log_path)}
            print('Backend candidate regressions: ' + summary, flush=True)
        roles = [('api', images['api']['id'])]
        if render_image:
            roles += [('preview', images['render']['id']), ('standby', images['render']['id'])]
        for role, image_id in roles:
            name = 'imladri-runtime-' + role + '-' + label
            try:
                docker(*application_command(image_id, mongo_name, 'imladri_runtime_' + role + '_' + label,
                                            role=role, name=name))
                report['roles'][role] = json.loads(docker('exec', name, 'python', '-c', ROLE_PROBE, timeout=160))
                observed = startup_manifest(docker('logs', name))
                assert observed == report['expected_attestations']['api' if role == 'api' else 'render'], 'Runtime startup differs from the immutable image'
                report['roles'][role]['startup_attestation_matches_image'] = True
                print('Verified isolated ' + role + ' role', flush=True)
            except Exception:
                print(docker('logs', name)[-12000:], flush=True)
                raise
            finally:
                subprocess.run(['docker', 'rm', '-f', name], cwd=ROOT, capture_output=True)
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=str(error))
        atomic_json(output, report)
        raise
    finally:
        if started:
            subprocess.run(['docker', 'rm', '-f', mongo_name], cwd=ROOT, capture_output=True)
    atomic_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('api_image')
    parser.add_argument('--render-image')
    parser.add_argument('--qa-image', help='Production-derived image with isolated pytest tools; never deployed')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--skip-regressions', action='store_true', help='Run runtime and startup probes only')
    args = parser.parse_args()
    report = verify(args.api_image, args.render_image, args.output, args.skip_regressions, args.qa_image)
    print(json.dumps({'status': report['status'], 'images': report['images'],
                      'roles': list(report['roles']), 'regressions': report['regressions']}))


if __name__ == '__main__':
    main()
