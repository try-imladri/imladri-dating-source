"""Completed-history registration is read-only and does not weaken status."""
import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest

from backend import postgres_operator as operator


CURRENT = 'b' * 40
OLD = 'a' * 40


def environment():
    return {'RENDER_SERVICE_ID': operator.SERVICE_ID, 'RENDER_SERVICE_NAME': operator.SERVICE_NAME,
        'RENDER_GIT_COMMIT': CURRENT, 'IMLADRI_DATABASE_MAINTENANCE': 'paused'}


def request():
    return {'operation_id': str(uuid4()), 'expected_release_sha': CURRENT, 'command': 'inspect_completed_operation',
        'source': 'personal_live', 'completed_operation_id': str(uuid4()),
        'completion_proof': {'historical_release_sha': OLD, 'command': 'inspect', 'source': 'personal_live',
            'result_sha256': operator.digest({'count': 1})}}


def test_current_readonly_command_accepts_only_its_explicit_bounded_historical_metadata():
    value = request()
    assert operator.validate_request(value, environment()) == value
    assert value['command'] in operator.COMMANDS


@pytest.mark.parametrize('change', ['missing_id', 'same_id', 'bad_id', 'wrong_proof_source', 'wrong_source', 'all_source',
    'bad_hash', 'bad_release', 'wrong_current_release', 'extra_proof', 'fence', 'approval', 'old_link',
    'prepare_access', 'rehearsal', 'discovery', 'recursive', 'wrong_family', 'nonstr_command'])
def test_wrong_historical_metadata_or_execution_fields_reject_before_any_child_or_journal(change):
    value = request()
    proof = value['completion_proof']
    if change == 'missing_id': value.pop('completed_operation_id')
    elif change == 'same_id': value['completed_operation_id'] = value['operation_id']
    elif change == 'bad_id': value['completed_operation_id'] = 'invalid'
    elif change == 'wrong_proof_source': proof['source'] = 'personal_preview'
    elif change == 'wrong_source': value['source'] = 'unreviewed'
    elif change == 'all_source': value['source'] = None; proof['source'] = None
    elif change == 'bad_hash': proof['result_sha256'] = 'not-a-hash'
    elif change == 'bad_release': proof['historical_release_sha'] = 'latest'
    elif change == 'wrong_current_release': value['expected_release_sha'] = OLD
    elif change == 'extra_proof': proof['unsafe'] = 'not allowed'
    elif change == 'fence': value['fence'] = {}
    elif change == 'approval': value['approved_workflow'] = True
    elif change == 'old_link': value['failed_copy_id'] = str(uuid4())
    elif change == 'prepare_access': proof['command'] = 'prepare_access'
    elif change == 'rehearsal': proof['command'] = 'rehearse_archive'
    elif change == 'discovery': proof['command'] = 'discover_sources'
    elif change == 'recursive': proof['command'] = 'inspect_completed_operation'
    elif change == 'wrong_family': proof['command'] = 'archive_legacy'
    else: proof['command'] = []
    with pytest.raises(operator.OperatorBlocked): operator.validate_request(value, environment())


def test_historical_fields_cannot_be_added_to_regular_execute_or_status_requests():
    value = request(); value['command'] = 'inspect'
    with pytest.raises(operator.OperatorBlocked, match='outside_readonly'): operator.validate_request(value, environment())
    with pytest.raises(operator.OperatorBlocked, match='unexpected_request_field'):
        operator.validate_request(request(), environment(), status=True)


def test_diagnostic_dispatch_precedes_reserve_and_never_finishes_or_reexecutes_history(monkeypatch):
    import pg8000.dbapi
    from backend import postgres_completed_operation as history
    value = request(); calls = []
    class Connection:
        def close(self): calls.append('close')
    class Journal:
        def __init__(self, connection): pass
        def reserve(self, *_): pytest.fail('readonly history reserved a journal row')
        def finish(self, *_a, **_k): pytest.fail('readonly history finished a journal row')
    async def inspect(config, actual, journal):
        assert actual['command'] == 'inspect_completed_operation' and actual['configuration_sha256'] == 'c' * 64
        calls.append('readonly-inspection')
        return {'historical_only': True, 'operation_reexecuted': False, 'operator_journal_writes': 0}
    monkeypatch.setattr(pg8000.dbapi, 'connect', lambda **_: Connection())
    monkeypatch.setattr(history, 'inspect_completed_operation', inspect)
    result = asyncio.run(operator.run({'action': 'execute', 'request': value}, environment(),
        config_loader=lambda _: {'owner_options': {}, 'configuration_sha256': 'c' * 64}, journal_factory=Journal))
    assert result['status'] == 'completed' and result['operation_id'] == value['operation_id']
    assert result['read_only_diagnostic'] is True and result['needs_manual_review'] is True
    assert result['diagnostic_result_durable_in_operator_journal'] is False
    assert calls == ['readonly-inspection', 'close']


def test_default_status_guard_still_rejects_old_release_rows_without_historical_fallback(monkeypatch):
    import pg8000.dbapi
    calls = []
    class Connection:
        def close(self): calls.append('close')
    class Journal:
        def __init__(self, _connection): pass
        def lookup(self, _identity):
            calls.append('lookup')
            return {'release_sha': OLD, 'state': 'completed', 'result': {'count': 1}}
        def reserve(self, *_): pytest.fail('status reserved a journal row')
    monkeypatch.setattr(pg8000.dbapi, 'connect', lambda **_: Connection())
    value = {'operation_id': str(uuid4()), 'expected_release_sha': CURRENT}
    with pytest.raises(operator.OperatorBlocked, match='journal_release_mismatch'):
        asyncio.run(operator.run({'action': 'status', 'request': value}, environment(),
            config_loader=lambda _: {'owner_options': {}, 'configuration_sha256': 'c' * 64}, journal_factory=Journal))
    assert calls == ['lookup', 'close']


def test_parent_HTTP_route_subprocess_dispatch_and_GET_poll_cache_completed_diagnostic_without_journal_row(monkeypatch):
    """Actual auth/ASGI/controller/supervisor/worker path; only subprocess/PG mocked."""
    import json
    import httpx
    import pg8000.dbapi
    from starlette.requests import Request
    from backend import postgres_operator_api as api

    async def run():
        value = request()
        env = {**environment(), 'IMLADRI_DATABASE_OPERATOR_SECRET': 'fixture-operator-' * 3,
            'RENDER_ORIGIN_SECRET': 'fixture-origin-' * 3, 'IMLADRI_POSTGRES_GATEWAY_SECRET': 'fixture-gateway-' * 3,
            'STRIPE_SECRET_KEY': 'EXCLUDED_FIXTURE_VALUE'}
        original = {'state': 'completed', 'release_sha': OLD, 'command': 'inspect', 'source': 'personal_live',
            'request_sha256': 'c' * 64, 'result': {'count': 1}}
        calls, lookups = [], []
        started, release = asyncio.Event(), asyncio.Event()

        class Connection:
            def close(self): pass

        class Journal:
            def __init__(self, _connection): pass
            def lookup(self, identity):
                lookups.append(identity)
                return deepcopy(original) if identity == value['completed_operation_id'] else None
            def reserve(self, *_): pytest.fail('HTTP historical diagnostic reserved a journal row')
            def finish(self, *_a, **_k): pytest.fail('HTTP historical diagnostic finished a journal row')

        monkeypatch.setattr(pg8000.dbapi, 'connect', lambda **_: Connection())

        class Stdin:
            def __init__(self, process): self.process = process
            def write(self, payload): self.process.payload = payload
            async def drain(self):
                started.set()
                await release.wait()
                envelope = json.loads(self.process.payload)
                assert envelope['action'] == 'execute' and envelope['request'] == value
                result = await operator.run(envelope, self.process.env,
                    config_loader=lambda _: {'sources': {'personal_live': {'database': 'imladri_personal_live'}},
                        'owner_options': {}, 'configuration_sha256': 'd' * 64}, journal_factory=Journal)
                result.update(version=1, security={**{key: '0000000000000000' for key in
                    ('CapEff', 'CapPrm', 'CapInh')}, 'NoNewPrivs': '1'})
                self.process.stdout.feed_data(json.dumps(result).encode())
                self.process.stdout.feed_eof()
            def close(self): pass

        class Process:
            pid, returncode = 123456, None
            def __init__(self, child_env):
                self.env, self.stdout = child_env, asyncio.StreamReader()
                self.stdin = Stdin(self)
            async def wait(self): self.returncode = 0; return 0

        async def process_factory(*args, **kwargs):
            calls.append(args)
            assert args[1:] == ('-m', 'backend.postgres_operator') and kwargs['start_new_session'] is True
            assert 'STRIPE_SECRET_KEY' not in kwargs['env'] and 'IMLADRI_DATABASE_OPERATOR_SECRET' not in kwargs['env']
            return Process(kwargs['env'])

        async def runner(envelope, child_env):
            return await api.run_child(envelope, child_env, process_factory=process_factory)

        controller = api.OperatorController(env, runner=runner)
        async def app(scope, receive, send):
            response = await api.endpoint(Request(scope, receive=receive), controller)
            await response(scope, receive, send)
        auth = {'Authorization': 'Bearer ' + env['IMLADRI_DATABASE_OPERATOR_SECRET'],
            'X-Imladri-Origin': env['RENDER_ORIGIN_SECRET']}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://fixture.local') as client:
            response = await client.post(api.PATH, json=value, headers=auth)
            assert response.status_code == 202 and response.json()['status'] == 'running'
            await started.wait()
            pending = await client.get(api.PATH, params={'operation_id': value['operation_id']}, headers=auth)
            assert pending.status_code == 200 and pending.json()['status'] == 'running'
            release.set()
            for _ in range(100):
                result = await client.get(api.PATH, params={'operation_id': value['operation_id']}, headers=auth)
                if result.json()['status'] == 'completed': break
                await asyncio.sleep(0)
            assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
            body = result.json()
            assert body['status'] == 'completed' and body['operation_id'] == value['operation_id']
            assert body['read_only_diagnostic'] is True and body['needs_manual_review'] is True
            assert body['diagnostic_result_durable_in_operator_journal'] is False
            assert body['result']['historical_result'] == original['result']
            assert body['result']['current_archive_target_verified'] is False
            before = list(lookups)
            cached = await client.get(api.PATH, params={'operation_id': value['operation_id']}, headers=auth)
            assert cached.json() == body and lookups == before and len(calls) == 1
        await controller.shutdown()
    asyncio.run(run())
