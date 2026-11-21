"""Read-only operator diagnosis, static reasons and isolated real role counts."""
import asyncio
import ast
from pathlib import Path
import json
import os
import secrets
from urllib.parse import quote
from uuid import uuid4

from bson import BSON, ObjectId
import pytest

from backend import postgres_copy_diagnostics as diagnostics, postgres_operator as worker
from backend.postgres_cutover import FrozenCopyBlocked
from backend.tests.test_postgres_operator_control import owner_database, environment, request
from backend.tests.test_postgres_operator_copy import local_config, local_only
from backend.tests.test_postgres_snapshot import source_fixture


def test_static_reason_allowlist_covers_all_cutover_code_constants():
    tree = ast.parse(Path(__file__).parents[1].joinpath('postgres_cutover.py').read_text())
    messages = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            args = node.args[1:] if node.func.id == '_require' else node.args if node.func.id == 'FrozenCopyBlocked' else []
            messages.update(arg.value for arg in args if isinstance(arg, ast.Constant) and isinstance(arg.value, str))
    assert messages == set(diagnostics.FROZEN_COPY_REASONS)
    assert len(set(diagnostics.FROZEN_COPY_REASONS.values())) == len(messages)


def test_only_static_allowlisted_reasons_can_leave_error_handler():
    actual = diagnostics.safe_error(FrozenCopyBlocked('An inconsistent answer-history identity or alias blocks the copy.'))
    assert actual == {'error_type': 'FrozenCopyBlocked', 'reason_code': 'history_identity_inconsistent'}
    for error in [FrozenCopyBlocked('synthetic-private-contact@example.invalid'), RuntimeError('synthetic-private-provider-details')]:
        assert 'synthetic-private' not in json.dumps(diagnostics.safe_error(error))
    assert diagnostics.safe_error(FrozenCopyBlocked('synthetic-private'))['reason_code'] == 'unclassified_frozen_copy_block'


@pytest.mark.parametrize('change', [{'state': 'completed'}, {'source': 'personal_live'}, {'command': 'copy_market'}])
def test_failed_copy_link_rejects_other_source_command_or_nonunknown_before_data_access(change):
    previous = {'state': 'unknown', 'source': 'personal_preview', 'command': 'copy_personal', **change}
    class Journal:
        def lookup(self, _identity): return previous
    def forbidden(*_args, **_kwargs): pytest.fail('Mismatched prior intent reached source/target data')
    async def run():
        with pytest.raises(worker.OperatorBlocked, match='reference_scope_or_state'):
            await diagnostics.inspect_copy_state({}, {'source': 'personal_preview', 'failed_copy_id': str(uuid4())},
                Journal(), mongo_factory=forbidden, inventory=forbidden)
    asyncio.run(run())


def test_readonly_command_validates_fixed_scope_and_failed_reference_only_here():
    env = environment()
    assert worker.validate_request(request('inspect_copy_state', source='personal_preview', failed_copy_id=str(uuid4())), env)
    with pytest.raises(worker.OperatorBlocked):
        worker.validate_request(request('inspect_copy_state', source='legacy_staging'), env)
    with pytest.raises(worker.OperatorBlocked):
        worker.validate_request(request('inspect', failed_copy_id=str(uuid4())), env)
    body = request('inspect_copy_state', source='personal_preview')
    with pytest.raises(worker.OperatorBlocked, match='own_operation_identity'):
        worker.validate_request({**body, 'failed_copy_id': body['operation_id']}, env)


def test_readonly_run_never_reserves_finishes_or_rewrites_prior_journal(monkeypatch):
    import pg8000.dbapi
    class Connection:
        def close(self): pass
    class Journal:
        def reserve(self, *_): pytest.fail('Read-only diagnostic reserved a journal write')
        def finish(self, *_args, **_kwargs): pytest.fail('Read-only diagnostic modified a prior intent')
    monkeypatch.setattr(pg8000.dbapi, 'connect', lambda **_kwargs: Connection())
    async def execute(*_args): return {'diagnostic_only': True, 'target_row_count': 0}
    monkeypatch.setattr(worker, 'execute', execute)
    config = {'owner_options': {}, 'configuration_sha256': 'c' * 64}
    result = asyncio.run(worker.run({'action': 'execute', 'request': request('inspect_copy_state', source='personal_preview')},
        environment(), config_loader=lambda _: config, journal_factory=lambda _: Journal()))
    assert result['status'] == 'completed' and result['read_only_diagnostic'] is True
    assert result['diagnostic_result_durable_in_operator_journal'] is False


def test_future_copy_failure_persists_static_reason_and_no_private_text(monkeypatch):
    import pg8000.dbapi
    class Connection:
        def close(self): pass
    captured = []
    class Journal:
        def reserve(self, *_): return {'execute': True}
        def finish(self, req, result, *, success): captured.append((result, success))
    monkeypatch.setattr(pg8000.dbapi, 'connect', lambda **_: Connection())
    async def execute(*_args):
        raise FrozenCopyBlocked('An index option has no reviewed PostgreSQL equivalent.')
    monkeypatch.setattr(worker, 'execute', execute)
    config = {'owner_options': {}, 'configuration_sha256': 'c' * 64}
    result = asyncio.run(worker.run({'action': 'execute', 'request': request()}, environment(),
        config_loader=lambda _: config, journal_factory=lambda _: Journal()))
    assert result['status'] == 'unknown' and captured[0][1] is False
    assert captured[0][0]['reason_code'] == 'index_option_unsupported'


integration = pytest.mark.skipif(not os.getenv('IMLADRI_TEST_POSTGRES_DSN') or not os.getenv('IMLADRI_SNAPSHOT_TEST_MONGO_URL'),
    reason='Requires explicit disposable loopback PostgreSQL and Mongo replica set.')


@integration
@pytest.mark.parametrize('partial_rows', [0, 1])
def test_source_preflight_failure_keeps_actual_RLS_target_counts_and_never_writes(
        owner_database, monkeypatch, partial_rows):
    from backend import postgres_snapshot, postgres_cutover
    from backend.postgres_access import identifier
    connection, options = owner_database
    store_type = local_only(monkeypatch)
    monkeypatch.setattr(postgres_snapshot, 'create_majority_barrier', lambda *_args, **_kwargs:
                        pytest.fail('Diagnostic created a source write barrier'))
    monkeypatch.setattr(postgres_cutover, 'copy_frozen', lambda *_args, **_kwargs:
                        pytest.fail('Diagnostic invoked potentially writing copy_frozen'))
    logical = 'personal_preview'
    role = {**options, 'user': 'imladri_app_diag_' + uuid4().hex[:12], 'password': secrets.token_urlsafe(32)}

    async def run():
        async with source_fixture() as source:
            # Ordinary preexisting event schema: Mongo generated its ObjectId.
            await source.answer_history.insert_one({'_id': ObjectId(), 'id': 'legacy-event',
                'after': {'answer': 'synthetic-private'}, 'owner_id': 'synthetic-owner'})
            original = await source.answer_history.find_one({})
            config = local_config(source, options, logical)
            password = quote(role['password'], safe='')
            config['role_options'][logical] = role
            config['role_dsns'][logical] = (f'postgresql://{role["user"]}:{password}@127.0.0.1:{options["port"]}/'
                                           f'{options["database"]}?sslmode=disable')
            worker.prepare_access(config, request('prepare_access', approved_workflow=True, allow_public_acl_hardening=True))
            if partial_rows:
                target = store_type(config['owner_dsn'], worker.NAMESPACES[logical],
                                    allow_local_plaintext=True, initialize_schema=True)
                try:
                    await target.users.insert_one({'_id': 'one', 'email': 'synthetic-private@example.invalid'})
                finally:
                    await target.aclose()
            class Journal:
                def lookup(self, _identity): return {'state': 'unknown', 'source': logical, 'command': 'copy_personal'}
            result = await diagnostics.inspect_copy_state(config, {'source': logical, 'failed_copy_id': str(uuid4())},
                Journal(), mongo_factory=worker._mongo, inventory=worker._inventory)
            counts = result['target'][logical]
            assert counts['table_row_counts']['documents'] == partial_rows
            assert counts['namespace_completely_empty'] is (partial_rows == 0)
            assert counts['forced_RLS_verified'] is True and counts['transaction_read_only'] is True
            assert result['source_preflight'] == {'complete': False, 'error_type': 'FrozenCopyBlocked',
                                                   'reason_code': 'history_identity_inconsistent'}
            assert result['source_history_identity_counts']['identity_inconsistent'] == 1
            assert result['source_application_write_operations'] == result['target_application_write_operations'] == 0
            assert result['prior_intent_modified'] is False and worker.safe_result(result)
            assert 'synthetic-private' not in json.dumps(result) and 'legacy-event' not in json.dumps(result)
            assert BSON.encode(await source.answer_history.find_one({})) == BSON.encode(original)
            assert diagnostics._role_counts(role, worker.NAMESPACES[logical])['table_row_counts'] == counts['table_row_counts']
    try:
        asyncio.run(run())
    finally:
        cursor = connection.cursor()
        cursor.execute('SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=%s', (role['user'],))
        if cursor.fetchone():
            cursor.execute('DROP OWNED BY ' + identifier(role['user']))
            cursor.execute('DROP ROLE ' + identifier(role['user']))
        connection.commit();cursor.close()
