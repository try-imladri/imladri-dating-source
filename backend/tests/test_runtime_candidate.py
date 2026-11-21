"""Keep migration verification isolated from host credentials and production."""
import pytest

from scripts import verify_runtime_candidate as verification


IMAGE = 'sha256:' + 'a' * 64
NETWORK = 'imladri-runtime-mongo-' + 'b' * 12


def test_fixture_environment_does_not_inherit_paid_credentials(monkeypatch):
    monkeypatch.setenv('MONGO_URL', 'mongodb+srv://must-not-be-used.invalid')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'must-not-be-inherited')
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'must-not-be-inherited')
    environment = verification.fixture_environment('imladri_fixture')
    assert environment['MONGO_URL'] == 'mongodb://127.0.0.1:27017'
    assert environment['PYTHON_DOTENV_DISABLED'] == '1'
    assert environment['DEEPSEEK_API_KEY'] == environment['STRIPE_SECRET_KEY'] == ''
    assert not any('must-not' in value for value in environment.values())


def test_regressions_use_a_private_namespace_and_read_only_test_inputs():
    command = verification.application_command(IMAGE, NETWORK, 'imladri_fixture', regressions=True)
    assert command[command.index('--network') + 1] == 'container:' + NETWORK
    mounts = [command[index + 1] for index, item in enumerate(command) if item == '--mount']
    assert len(mounts) == 5 and all(mount.endswith(',readonly') for mount in mounts)
    assert command[-3:] == [IMAGE, '-c', verification.REGRESSION_RUNNER]
    assert "not path.name.startswith('test_imaldri')" in verification.REGRESSION_RUNNER


@pytest.mark.parametrize('image,network', [('mutable:latest', NETWORK), (IMAGE, 'host'), (IMAGE, 'production')])
def test_mutable_images_and_nonisolated_networks_are_rejected(image, network):
    with pytest.raises(ValueError):
        verification.application_command(image, network, 'fixture', regressions=True)


def test_api_exercises_its_actual_default_startup_and_render_uses_no_shell():
    api = verification.application_command(IMAGE, NETWORK, 'fixture', role='api', name='fixture-api')
    assert api[-1] == IMAGE and '--entrypoint' not in api
    render = verification.application_command(IMAGE, NETWORK, 'fixture', role='standby', name='fixture-standby')
    assert render[-6:] == ['--entrypoint', 'python', IMAGE, '-m', 'backend.render_entrypoint', 'standby']
    assert 'QA_ROLE=standby' in render and 'RENDER_ORIGIN_SECRET=fixture-origin-only' in render


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError):
        verification.fixture_environment('fixture', 'unexpected')


def test_native_probe_compiles_and_output_replacement_is_complete(tmp_path):
    compile(verification.NATIVE_PROBE, '<runtime-probe>', 'exec')
    compile(verification.ROLE_PROBE, '<role-probe>', 'exec')
    target = tmp_path / 'proof.json'
    target.write_text('old', encoding='utf8')
    verification.atomic_json(target, {'status': 'passed'})
    assert target.read_text(encoding='utf8') == '{\n  "status": "passed"\n}\n'
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize('log', ['', 'IMLADRI_RUNTIME_FINGERPRINT_v2 {"schema_version":2,"attestation_error":"ValueError"}',
                                'IMLADRI_RUNTIME_FINGERPRINT_v2 {"schema_version":1}',
                                'IMLADRI_RUNTIME_FINGERPRINT_v2 {"schema_version":2}\nIMLADRI_RUNTIME_FINGERPRINT_v2 {"schema_version":2}'])
def test_startup_proof_requires_one_complete_current_manifest(log):
    with pytest.raises(ValueError):
        verification.startup_manifest(log)


def test_startup_proof_ignores_other_application_log_lines():
    assert verification.startup_manifest('unrelated\nIMLADRI_RUNTIME_FINGERPRINT_v2 {"schema_version":2,"uid":65532}\nready') == {'schema_version': 2, 'uid': 65532}


def test_qa_tools_cannot_shadow_the_production_app_os_or_dependencies():
    production = {'application_files': {'sha256': 'app'}, 'apk_database': {'sha256': 'os'},
                  'uid': 65532, 'gid': 65532, 'effective_capabilities': '0'}
    verification.validate_qa_overlay(production, dict(production), [('fastapi', '1')], [('fastapi', '1'), ('pytest', '9')])
    with pytest.raises(ValueError, match='replaced'):
        verification.validate_qa_overlay(production, dict(production), [('fastapi', '1')], [('fastapi', '2')])
    with pytest.raises(ValueError, match='Unexpected'):
        verification.validate_qa_overlay(production, dict(production), [('fastapi', '1')], [('fastapi', '1'), ('numpy', '2')])
    with pytest.raises(ValueError, match='application_files'):
        verification.validate_qa_overlay(production, {**production, 'application_files': {}}, [], [])
