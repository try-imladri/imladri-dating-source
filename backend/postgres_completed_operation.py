"""Explicit current-code inspection of a completed historical owner operation.

This is neither an old operation replay nor a substitute for current active
datastore parity. Archive receipts additionally receive two complete target-only
checks using a dedicated verified-TLS owner connection and a read-only snapshot.
No MongoDB client, schema initializer, journal reservation or commit is used.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
import re
import ssl
from uuid import UUID

from bson import BSON

from . import postgres_archive as archive
from .postgres_operator import (ARCHIVE_SOURCES, MARKET_SOURCES, NAMESPACES, RECOVERY_SOURCES,
    SOURCE_DATABASES, MAX_OUTPUT, OperatorBlocked, digest, require, safe_result)


HISTORICAL_COMMANDS = frozenset({'inspect', 'archive_legacy', 'recover_archive', 'copy_personal',
    'copy_market', 'reconcile_indexes', 'verify'})
MAX_RESULT_BYTES = MAX_OUTPUT - 8192


def validate_completed_request(request):
    require(set(request) == {'operation_id', 'expected_release_sha', 'command', 'source',
        'completed_operation_id', 'completion_proof'}, 'completed_inspection_fields_invalid')
    require(request.get('command') == 'inspect_completed_operation' and isinstance(request.get('source'), str)
        and request['source'] in SOURCE_DATABASES,
        'completed_inspection_fixed_source_required')
    identity = request.get('completed_operation_id')
    try:
        canonical = isinstance(identity, str) and str(UUID(identity)) == identity
    except ValueError:
        canonical = False
    require(canonical and identity != request['operation_id'], 'completed_inspection_requires_distinct_historical_identity')
    proof = request.get('completion_proof')
    require(isinstance(proof, dict) and set(proof) == {'historical_release_sha', 'command', 'source', 'result_sha256'}
        and isinstance(proof.get('historical_release_sha'), str) and re.fullmatch('[a-f0-9]{40}', proof['historical_release_sha'])
        and isinstance(proof.get('result_sha256'), str) and re.fullmatch('[a-f0-9]{64}', proof['result_sha256'])
        and proof.get('source') == request['source'] and isinstance(proof.get('command'), str)
        and proof['command'] in HISTORICAL_COMMANDS,
        'completed_inspection_historical_proof_invalid')
    command, source = proof['command'], request['source']
    allowed = (SOURCE_DATABASES if command == 'inspect' else ARCHIVE_SOURCES if command == 'archive_legacy'
        else RECOVERY_SOURCES if command == 'recover_archive' else NAMESPACES if command == 'copy_personal'
        else MARKET_SOURCES if command == 'copy_market' else {**NAMESPACES, **MARKET_SOURCES})
    require(source in allowed, 'completed_inspection_historical_source_command_mismatch')


def _schema_readonly(cursor):
    cursor.execute("SELECT CURRENT_USER=SESSION_USER,rolsuper OR oid=(SELECT datdba FROM pg_database "
        "WHERE datname=current_database()),rolname FROM pg_roles WHERE rolname=CURRENT_USER")
    same, owner, role = cursor.fetchone()
    require(same and owner and not role.startswith('imladri_app_'), 'completed_archive_dedicated_owner_required')
    cursor.execute("""SELECT c.relname,c.relkind,pg_get_userbyid(c.relowner)=CURRENT_USER,
        EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='source_alias'
        AND a.attnotnull AND NOT a.attisdropped),
        (SELECT array_agg(a.attname ORDER BY k.position) FROM pg_constraint p
        CROSS JOIN LATERAL unnest(p.conkey) WITH ORDINALITY k(attnum,position)
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
        WHERE p.conrelid=c.oid AND p.contype='p')
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='imladri_archive' AND c.relname IN ('collections','documents','receipts')""")
    rows = cursor.fetchall()
    keys = {'collections': ['namespace', 'source_alias', 'name'],
        'documents': ['namespace', 'source_alias', 'collection', 'id_key'], 'receipts': ['namespace', 'source_alias']}
    require(len(rows) == 3 and all(kind == 'r' and owned and alias_not_null and key == keys[name]
        for name, kind, owned, alias_not_null, key in rows), 'completed_archive_exact_V2_owner_schema_required')
    cursor.execute("""SELECT EXISTS(SELECT 1 FROM pg_inherits i
        JOIN pg_class c ON c.oid IN (i.inhrelid,i.inhparent)
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='imladri_archive' AND c.relname IN ('collections','documents','receipts'))""")
    require(cursor.fetchone()[0] is False, 'completed_archive_noninherited_owner_tables_required')
    archive._validate_foreign_key(cursor)
    cursor.execute("""SELECT EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname LIKE 'imladri_app_%'
        AND (has_schema_privilege(r.oid,'imladri_archive','USAGE,CREATE') OR EXISTS(
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='imladri_archive'
        AND has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))))""")
    require(cursor.fetchone()[0] is False, 'completed_archive_application_access_must_be_denied')


def _verify_archive(config, alias, database, expected_receipt, *, connection_factory=None):
    import pg8000.dbapi
    options = config['owner_options']
    context = options.get('ssl_context')
    require(isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        and options.get('database') == 'imladri_personal' and not options.get('user', '').startswith('imladri_app_'),
        'completed_archive_strict_owner_TLS_profile_required')
    factory = connection_factory or pg8000.dbapi.connect
    connection = factory(**{**options, 'timeout': 20, 'application_name': 'imladri-completed-archive-readonly'})
    cursor, stage = None, 'transaction'
    try:
        require(connection.autocommit is False, 'completed_archive_transaction_required')
        cursor = connection.cursor()
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cursor.execute("SET LOCAL statement_timeout='20s'")
        cursor.execute("SET LOCAL lock_timeout='10s'")
        cursor.execute("SET LOCAL search_path='pg_catalog'")
        cursor.execute('SHOW transaction_read_only')
        require(cursor.fetchone()[0] == 'on', 'completed_archive_actual_readonly_transaction_required')
        cursor.execute('SHOW transaction_isolation')
        require(cursor.fetchone()[0] == 'repeatable read', 'completed_archive_actual_repeatable_snapshot_required')
        stage = 'schema'
        _schema_readonly(cursor)
        stage = 'receipt'
        cursor.execute("SELECT CASE WHEN octet_length(receipt_bson)<=2097152 THEN receipt_bson END,receipt_sha256 "
            "FROM imladri_archive.receipts WHERE namespace=%s AND source_alias=%s AND source_database=%s LIMIT 2",
            (archive.ARCHIVE_NAMESPACE, alias, database))
        rows = cursor.fetchall()
        require(len(rows) == 1 and rows[0][0] is not None, 'completed_archive_one_bounded_stored_receipt_required')
        raw, stored_hash = bytes(rows[0][0]), rows[0][1]
        require(hashlib.sha256(raw).hexdigest() == stored_hash, 'completed_archive_stored_receipt_hash_changed')
        receipt = BSON(raw).decode()
        require(receipt == expected_receipt and receipt.get('version') == 2
            and receipt.get('status') == 'verified_legacy_archive' and receipt.get('schema') == 'imladri_archive'
            and receipt.get('namespace') == archive.ARCHIVE_NAMESPACE and receipt.get('source_alias') == alias
            and receipt.get('source_database') == database, 'completed_archive_receipt_not_identical_to_history')
        binding = receipt.get('source_binding', {})
        transport = binding.get('source_transport', {})
        require(binding.get('source_alias') == alias and binding.get('source_database') == database
            and transport.get('verified_tls') is True and transport.get('explicit_loopback_plaintext_fixture') is False
            and isinstance(transport.get('configured_endpoints_sha256'), str)
            and re.fullmatch('[a-f0-9]{64}', transport['configured_endpoints_sha256'])
            and receipt.get('source_binding_sha256') == archive._manifest_hash(binding),
            'completed_archive_source_binding_changed')
        budget = archive._Budget(200000, 64 * 1024 * 1024, 300, 16 * 1024 * 1024, 512 * 1024 * 1024)
        manifests = []
        for attempt in range(2):
            stage = 'manifest_' + str(attempt + 1)
            manifests.append(archive._target_manifest(cursor, alias, database, budget))
        require(manifests[0] == manifests[1] == receipt.get('collections')
            and receipt.get('collection_count') == len(manifests[0])
            and receipt.get('physical_records') == sum(row['records'] for row in manifests[0])
            and receipt.get('jsonb_unavailable_records') == sum(row['jsonb_unavailable_records'] for row in manifests[0])
            and receipt.get('manifest_sha256') == archive._manifest_hash({'source_binding': binding, 'collections': manifests[0]}),
            'completed_archive_full_current_target_manifest_changed')
        return {'status': 'verified_current_owner_archive', 'schema': 'imladri_archive', 'namespace': archive.ARCHIVE_NAMESPACE,
            'source_alias': alias, 'source_database': database, 'receipt_bson_sha256': stored_hash,
            'receipt_semantic_sha256': digest(receipt), 'manifest_sha256': receipt['manifest_sha256'],
            'collections': len(manifests[0]), 'physical_records': receipt['physical_records'],
            'two_pass_target_verified': True, 'full_receipt_matches_historical_result': True,
            'transaction_isolation': 'repeatable_read', 'transaction_read_only': True,
            'source_connections': 0, 'application_writes': 0}
    except archive.ArchiveBlocked as error:
        raise OperatorBlocked('completed_archive_' + stage + '_' + archive.safe_archive_error(error).get('reason_code', 'integrity_failed')) from None
    except OperatorBlocked:
        raise
    except Exception:
        raise OperatorBlocked('completed_archive_' + stage + '_verification_failed') from None
    finally:
        try:
            connection.rollback()
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()


async def inspect_completed_operation(config, request, journal, *, connection_factory=None):
    # config adds no fields to the client request here; operator.run's internal
    # configuration digest is excluded only for this fixed structural validator.
    validate_completed_request({key: value for key, value in request.items() if key != 'configuration_sha256'})
    alias = request['source']
    require(alias in config['sources'] and config['sources'][alias].get('database') == SOURCE_DATABASES[alias],
        'completed_inspection_current_canonical_source_required')
    require(journal.lookup(request['operation_id']) is None, 'completed_inspection_requires_unused_identity')
    previous = deepcopy(journal.lookup(request['completed_operation_id']))
    proof = request['completion_proof']
    require(isinstance(previous, dict) and previous.get('state') == 'completed'
        and previous.get('source') == alias and previous.get('command') == proof['command']
        and previous.get('release_sha') == proof['historical_release_sha']
        and isinstance(previous.get('request_sha256'), str) and re.fullmatch('[a-f0-9]{64}', previous['request_sha256']),
        'completed_inspection_only_exact_completed_historical_rows_allowed')
    result = previous.get('result')
    require(safe_result(result) and isinstance(result, dict) and digest(result) == proof['result_sha256']
        and len(json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()) <= MAX_RESULT_BYTES,
        'completed_inspection_safe_bounded_result_hash_required')
    verified = None
    if proof['command'] in {'archive_legacy', 'recover_archive'}:
        verified = await asyncio.to_thread(_verify_archive, config, alias, SOURCE_DATABASES[alias], result.get('receipt'),
            connection_factory=connection_factory)
    require(journal.lookup(request['completed_operation_id']) == previous
        and journal.lookup(request['operation_id']) is None, 'completed_inspection_journal_changed_during_readonly_check')
    response = {'kind': 'historical_completed_operation_inspection', 'historical_only': True,
        'current_release_sha': request['expected_release_sha'], 'completed_operation_id': request['completed_operation_id'],
        'historical_release_sha': proof['historical_release_sha'], 'historical_command': proof['command'],
        'source_alias': alias, 'source_database': SOURCE_DATABASES[alias],
        'historical_request_sha256': previous['request_sha256'], 'historical_result_sha256': proof['result_sha256'],
        'historical_result': result, 'operation_reexecuted': False, 'original_operation_modified': False,
        'operator_journal_writes': 0, 'current_active_data_parity_claimed': False,
        'current_archive_target_verified': verified is not None}
    if verified is not None:
        response['archive_verification'] = verified
    require(safe_result(response) and len(json.dumps(response, sort_keys=True, separators=(',', ':')).encode()) <= MAX_OUTPUT - 4096,
        'completed_inspection_output_bound_or_safety')
    return response
