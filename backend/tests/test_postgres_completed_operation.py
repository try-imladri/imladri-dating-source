"""Historical completed evidence is SELECT-only; fresh archive checks need no Mongo."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import ssl
from uuid import uuid4

from bson import BSON, Binary, Decimal128, Int64, ObjectId
import pytest

from backend import postgres_archive as archive, postgres_operator as worker
from backend.postgres_migration import (canonical_document, document_key, fingerprint_add,
    fingerprint_finish, jsonb_text)
from backend.tests.test_postgres_operator_control import owner_database


DSN=os.getenv('IMLADRI_TEST_POSTGRES_DSN')
integration=pytest.mark.skipif(not DSN,reason='Requires explicitly configured loopback PostgreSQL fixture')
OLD='a'*40
CURRENT='b'*40
ALIAS='legacy_staging'
DATABASE='imladri_staging'


def module():
    from backend import postgres_completed_operation
    return postgres_completed_operation


def metadata(payload=None, *, source=ALIAS, command='archive_legacy'):
    identity,new_id=str(uuid4()),str(uuid4())
    payload=payload or {'records':2,'credentials_or_record_values_included':False}
    row={'state':'completed','command':command,'source':source,'release_sha':OLD,
        'request_sha256':'c'*64,'result':deepcopy(payload)}
    request={'operation_id':new_id,'expected_release_sha':CURRENT,'command':'inspect_completed_operation',
        'source':source,'completed_operation_id':identity,'completion_proof':{
            'historical_release_sha':OLD,'command':command,'source':source,'result_sha256':worker.digest(payload)}}
    config={'sources':{source:{'database':DATABASE if source==ALIAS else 'imladri_production'}},
        'owner_options':{'host':'fixture.virginia-postgres.render.com','port':5432,'database':'imladri_personal',
            'user':'fixture_owner','password':'FIXTURE_OWNER_PASSWORD','ssl_context':ssl.create_default_context()}}
    return config,request,{identity:row}


class Journal:
    def __init__(self, rows):self.rows=rows;self.lookups=[]
    def lookup(self, identity):self.lookups.append(identity);return deepcopy(self.rows.get(identity))
    def reserve(self,*_):pytest.fail('Historical diagnostic reserved a journal intent')
    def finish(self,*_args,**_kwargs):pytest.fail('Historical diagnostic modified a journal intent')


def invoke(config,request,journal,**kwargs):
    return asyncio.run(module().inspect_completed_operation(config,request,journal,**kwargs))


@pytest.mark.parametrize('change',['missing','unknown','running','release','source','command','hash','request_hash','new_UUID_used','same_UUID','unsafe_result'])
def test_historical_identity_state_hash_and_unused_new_UUID_are_required_before_any_connection(change):
    config,request,rows=metadata(command='inspect');identity=request['completed_operation_id'];row=rows[identity]
    if change=='missing':rows.clear()
    elif change in {'unknown','running'}:row['state']=change
    elif change=='release':row['release_sha']='d'*40
    elif change=='source':row['source']='recovery_staging'
    elif change=='command':row['command']='copy_market'
    elif change=='hash':request['completion_proof']['result_sha256']='d'*64
    elif change=='request_hash':row['request_sha256']='not-a-hash'
    elif change=='new_UUID_used':rows[request['operation_id']]=deepcopy(row)
    elif change=='same_UUID':request['operation_id']=identity
    else:row['result']={'password':'PRIVATE_NEVER_RETURN'};request['completion_proof']['result_sha256']=worker.digest(row['result'])
    with pytest.raises(worker.OperatorBlocked):
        invoke(config,request,Journal(rows),connection_factory=lambda **_:pytest.fail('Invalid history reached PostgreSQL'))


@pytest.mark.parametrize('command',['prepare_access','rehearse_archive','discover_sources','inspect_copy_state','inspect_archive_state','inspect_completed_operation'])
def test_unapproved_historical_workflows_are_never_returned_or_reexecuted(command):
    config,request,rows=metadata(command=command)
    with pytest.raises(worker.OperatorBlocked):
        invoke(config,request,Journal(rows),connection_factory=lambda **_:pytest.fail('Unapproved command touched SQL'))


@pytest.mark.parametrize('change',['missing_alias','wrong_database'])
def test_current_config_keeps_the_fixed_historical_source_identity(change):
    config,request,rows=metadata(command='inspect')
    if change=='missing_alias':config['sources'].clear()
    else:config['sources'][ALIAS]['database']='imladri_production'
    with pytest.raises(worker.OperatorBlocked,match='canonical_source'):
        invoke(config,request,Journal(rows),connection_factory=lambda **_:pytest.fail('Wrong current source connected'))


@pytest.mark.parametrize('source,command',[('market_live','copy_market'),('market_live','reconcile_indexes'),
    ('market_live','verify'),('personal_live','copy_personal'),('legacy_staging','inspect')])
def test_completed_nonarchive_result_is_historical_only_and_no_source_or_target_connection(source,command,monkeypatch):
    config,request,rows=metadata(source=source,command=command)
    if source=='personal_live':config['sources'][source]['database']='imladri_personal_live'
    monkeypatch.setattr(worker,'_mongo',lambda *_:pytest.fail('Historical metadata attempted MongoDB'))
    journal=Journal(rows);before=deepcopy(rows)
    result=invoke(config,request,journal,connection_factory=lambda **_:pytest.fail('Nonarchive history attempted PostgreSQL'))
    assert result['kind']=='historical_completed_operation_inspection' and result['historical_only'] is True
    assert result['current_release_sha']==CURRENT and result['historical_release_sha']==OLD
    assert result['historical_result']==rows[request['completed_operation_id']]['result']
    assert result['current_active_data_parity_claimed'] is False and result['operator_journal_writes']==0
    assert result['operation_reexecuted'] is result['original_operation_modified'] is False
    assert rows==before and worker.safe_result(result)


@pytest.mark.parametrize('change',['old_row_changed','new_UUID_claimed'])
def test_journal_history_and_new_identity_are_rechecked_after_readonly_work(change):
    config,request,rows=metadata(source='market_live',command='verify')
    class ChangingJournal(Journal):
        def lookup(self,identity):
            result=super().lookup(identity)
            if len(self.lookups)==2:
                if change=='old_row_changed':self.rows[request['completed_operation_id']]['state']='unknown'
                else:self.rows[request['operation_id']]=deepcopy(result)
            return result
    with pytest.raises(worker.OperatorBlocked,match='journal_changed'):
        invoke(config,request,ChangingJournal(rows),connection_factory=lambda **_:pytest.fail('Metadata history connected'))


def seed_archive(connection, *, alias=ALIAS, database=DATABASE):
    """Seed exact owner-only persisted rows, not a fake Mongo archive source."""
    cursor=connection.cursor();archive._initialize(cursor)
    documents={
        '_control':[{'_id':ObjectId(),'fixture_private_answer':'FICTIONAL_PRIVATE_TEXT',
            'decimal':Decimal128('12.30'),'binary':Binary(b'\x00\xff',128)}],
        'A_records':[{'_id':Int64(8),'nul':'a\x00b'},
            {'_id':{'nested':Int64(4),'order':'preserved'},'score':3}],
        'é_empty':[]}
    manifests=[]
    for name,rows in documents.items():
        spec=BSON.encode({'name':name,'type':'collection','options':{}})
        indexes=BSON.encode({'indexes':[{'name':'_id_','key':{'_id':1},'v':2}]})
        total=unavailable=0
        cursor.execute('INSERT INTO imladri_archive.collections VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s)',
            (archive.ARCHIVE_NAMESPACE,alias,database,name,'collection',spec,hashlib.sha256(spec).hexdigest(),
                indexes,hashlib.sha256(indexes).hexdigest(),'0'*64))
        for document in rows:
            raw=BSON.encode(document);key=document_key(document);sha=hashlib.sha256(raw).hexdigest()
            projected=jsonb_text(document);unavailable+=int(projected is None)
            cursor.execute('INSERT INTO imladri_archive.documents VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)',
                (archive.ARCHIVE_NAMESPACE,alias,database,name,key,BSON.encode({'_id':document['_id']}),
                    raw,canonical_document(document),projected,sha))
            total=fingerprint_add(total,key,sha)
        fingerprint=fingerprint_finish(total,len(rows))
        cursor.execute('UPDATE imladri_archive.collections SET record_count=%s,fingerprint=%s WHERE namespace=%s AND source_alias=%s AND name=%s',
            (len(rows),fingerprint,archive.ARCHIVE_NAMESPACE,alias,name))
        manifests.append({'name':name,'kind':'collection','records':len(rows),'fingerprint':fingerprint,
            'spec_sha256':hashlib.sha256(spec).hexdigest(),'indexes_sha256':hashlib.sha256(indexes).hexdigest(),
            'jsonb_unavailable_records':unavailable})
    manifests=sorted(manifests,key=lambda row:row['name'])
    binding={'source_alias':alias,'source_database':database,'source_transport':{
        'verified_tls':True,'explicit_loopback_plaintext_fixture':False,'configured_endpoints_sha256':'d'*64}}
    receipt={'version':2,'status':'verified_legacy_archive','schema':'imladri_archive',
        'namespace':archive.ARCHIVE_NAMESPACE,'source_alias':alias,'source_database':database,
        'source_binding':binding,'source_binding_sha256':archive._manifest_hash(binding),'collections':manifests,
        'collection_count':len(manifests),'physical_records':sum(row['records'] for row in manifests),
        'jsonb_unavailable_records':sum(row['jsonb_unavailable_records'] for row in manifests),
        'manifest_sha256':archive._manifest_hash({'source_binding':binding,'collections':manifests}),
        'source_two_pass_pinned_verified':True,'target_two_pass_raw_verified':True,
        'analysis_text_and_optional_jsonb_verified':True,'active_workflows_created':False,'research_consent_inferred':False,
        'completed_at':datetime.now(timezone.utc).isoformat()}
    raw=BSON.encode(receipt)
    cursor.execute('INSERT INTO imladri_archive.receipts(namespace,source_alias,source_database,receipt_bson,receipt_sha256) VALUES(%s,%s,%s,%s,%s)',
        (archive.ARCHIVE_NAMESPACE,alias,database,raw,hashlib.sha256(raw).hexdigest()))
    connection.commit();cursor.close();return receipt


class CursorSpy:
    def __init__(self,cursor,connection):self.cursor=cursor;self.connection=connection
    def execute(self,query,args=()):
        self.connection.statements.append(query)
        value=self.cursor.execute(query,args)
        if query.lstrip().upper().startswith('SET TRANSACTION'):
            self.cursor.execute('SHOW transaction_read_only');self.connection.readonly=self.cursor.fetchone()[0]
            self.cursor.execute('SHOW transaction_isolation');self.connection.isolation=self.cursor.fetchone()[0]
        return value
    def __getattr__(self,name):return getattr(self.cursor,name)


class ConnectionSpy:
    def __init__(self,connection):
        self.connection=connection;self.statements=[];self.rollbacks=0;self.commits=0;self.closed=False
        self.readonly=self.isolation=None
    def cursor(self):return CursorSpy(self.connection.cursor(),self)
    def rollback(self):self.rollbacks+=1;return self.connection.rollback()
    def commit(self):self.commits+=1;pytest.fail('Readonly completed diagnostic attempted commit')
    def close(self):self.closed=True;return self.connection.close()
    def __getattr__(self,name):return getattr(self.connection,name)


def factory_for(options,spies):
    import pg8000.dbapi
    def factory(**_validated_options):
        spy=ConnectionSpy(pg8000.dbapi.connect(**options));spies.append(spy);return spy
    return factory


def persisted_fingerprint(connection):
    cursor=connection.cursor();values=[]
    for table in archive.TABLES:
        cursor.execute('SELECT row_to_json(t)::text FROM imladri_archive.'+table+' t ORDER BY 1')
        values.extend(cursor.fetchall())
    connection.rollback();cursor.close()
    return hashlib.sha256(repr(values).encode()).hexdigest()


@integration
def test_real_PG_archive_is_verified_twice_readonly_all_BSON_types_and_no_Mongo_or_journal_writes(owner_database,monkeypatch):
    connection,options=owner_database;receipt=seed_archive(connection)
    config,request,rows=metadata({'receipt':receipt,'active_application_records_written':0})
    monkeypatch.setattr(worker,'_mongo',lambda *_:pytest.fail('Archive revalidation attempted MongoDB'))
    journal=Journal(rows);before=deepcopy(rows);fingerprint=persisted_fingerprint(connection);spies=[]
    result=invoke(config,request,journal,connection_factory=factory_for(options,spies))
    verified=result['archive_verification']
    assert verified['status']=='verified_current_owner_archive' and verified['two_pass_target_verified'] is True
    assert verified['full_receipt_matches_historical_result'] is True and result['historical_only'] is True
    assert 'FICTIONAL_PRIVATE_TEXT' not in json.dumps(result) and worker.safe_result(result)
    assert rows==before and persisted_fingerprint(connection)==fingerprint
    assert len(spies)==1 and spies[0].closed and spies[0].rollbacks and spies[0].commits==0
    assert spies[0].readonly=='on' and spies[0].isolation=='repeatable read'
    assert all(sql.lstrip().split()[0].upper() in {'SET','SELECT','SHOW','DECLARE','FETCH','CLOSE'} for sql in spies[0].statements)
    assert sum('DECLARE imladri_archive_verify' in sql for sql in spies[0].statements)==6


@integration
@pytest.mark.parametrize('change',['raw_BSON','typed_id','canonical_text','optional_JSONB','catalog','receipt','database','foreign_key'])
def test_real_PG_mutations_cannot_pass_full_archival_revalidation_or_rewrite_rows(owner_database,change):
    connection,options=owner_database;receipt=seed_archive(connection)
    cursor=connection.cursor()
    if change=='raw_BSON':cursor.execute("UPDATE imladri_archive.documents SET raw_bson=%s WHERE collection='_control'",(BSON.encode({'_id':ObjectId(),'wrong':True}),))
    elif change=='typed_id':cursor.execute("UPDATE imladri_archive.documents SET id_bson=%s WHERE collection='A_records' AND octet_length(id_bson)<25",(BSON.encode({'_id':8}),))
    elif change=='canonical_text':cursor.execute("UPDATE imladri_archive.documents SET canonical_json='{}' WHERE collection='_control'")
    elif change=='optional_JSONB':cursor.execute("UPDATE imladri_archive.documents SET query_document=NULL WHERE collection='_control'")
    elif change=='catalog':cursor.execute("UPDATE imladri_archive.collections SET spec_bson=%s WHERE name='_control'",(BSON.encode({'name':'wrong'}),))
    elif change=='receipt':
        cursor.execute('ALTER TABLE imladri_archive.receipts DISABLE TRIGGER immutable_receipt')
        changed={**receipt,'physical_records':999};raw=BSON.encode(changed)
        cursor.execute('UPDATE imladri_archive.receipts SET receipt_bson=%s,receipt_sha256=%s',(raw,hashlib.sha256(raw).hexdigest()))
    elif change=='database':cursor.execute("UPDATE imladri_archive.collections SET source_database='imladri_production'")
    else:
        cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid='imladri_archive.documents'::regclass AND contype='f'")
        from backend.postgres_access import identifier
        cursor.execute('ALTER TABLE imladri_archive.documents DROP CONSTRAINT '+identifier(cursor.fetchone()[0]))
    connection.commit();cursor.close()
    config,request,rows=metadata({'receipt':receipt,'active_application_records_written':0});before=deepcopy(rows)
    snapshot=persisted_fingerprint(connection);spies=[]
    with pytest.raises((worker.OperatorBlocked,archive.ArchiveBlocked)):
        invoke(config,request,Journal(rows),connection_factory=factory_for(options,spies))
    assert rows==before and persisted_fingerprint(connection)==snapshot
    assert all(spy.commits==0 and spy.closed for spy in spies)


@integration
def test_current_actual_archive_must_match_old_completed_result_not_just_its_own_hash(owner_database):
    connection,options=owner_database;receipt=seed_archive(connection)
    wrong=deepcopy(receipt);wrong['physical_records']+=1
    config,request,rows=metadata({'receipt':wrong,'active_application_records_written':0})
    with pytest.raises(worker.OperatorBlocked):invoke(config,request,Journal(rows),connection_factory=factory_for(options,[]))


@integration
def test_unreviewed_partitioned_catalog_shape_is_refused_before_archive_records(owner_database):
    connection,options=owner_database;receipt=seed_archive(connection)
    config,request,rows=metadata({'receipt':receipt,'active_application_records_written':0})
    class PartitionCatalogCursor(CursorSpy):
        def execute(self,query,args=()):
            self.partition_catalog='SELECT c.relname,c.relkind' in query
            return super().execute(query,args)
        def fetchall(self):
            values=self.cursor.fetchall()
            if self.partition_catalog:
                values=[(name,'p' if name=='documents' else kind,*rest) for name,kind,*rest in values]
            return values
    class PartitionCatalogConnection(ConnectionSpy):
        def cursor(self):return PartitionCatalogCursor(self.connection.cursor(),self)
    import pg8000.dbapi
    spies=[]
    def factory(**_):
        spy=PartitionCatalogConnection(pg8000.dbapi.connect(**options));spies.append(spy);return spy
    with pytest.raises(worker.OperatorBlocked,match='V2_owner_schema'):
        invoke(config,request,Journal(rows),connection_factory=factory)
    assert spies[0].closed and spies[0].rollbacks and spies[0].commits==0
    assert not any('DECLARE imladri_archive_verify' in q for q in spies[0].statements)


@integration
def test_any_application_role_archive_privilege_refuses_fresh_verification(owner_database):
    from backend.postgres_access import identifier
    connection,options=owner_database;receipt=seed_archive(connection)
    role='imladri_app_completed_'+uuid4().hex[:16]
    cursor=connection.cursor();cursor.execute('CREATE ROLE '+identifier(role))
    cursor.execute('GRANT USAGE ON SCHEMA imladri_archive TO '+identifier(role));connection.commit();cursor.close()
    try:
        config,request,rows=metadata({'receipt':receipt,'active_application_records_written':0})
        with pytest.raises(worker.OperatorBlocked,match='application_access'):
            invoke(config,request,Journal(rows),connection_factory=factory_for(options,[]))
    finally:
        cursor=connection.cursor();cursor.execute('REVOKE USAGE ON SCHEMA imladri_archive FROM '+identifier(role))
        cursor.execute('DROP ROLE '+identifier(role));connection.commit();cursor.close()


@integration
@pytest.mark.parametrize('relationship',['outside_parent','outside_child'])
def test_ordinary_archive_tables_cannot_have_outside_schema_inheritance(owner_database,relationship):
    connection,options=owner_database;receipt=seed_archive(connection)
    cursor=connection.cursor()
    if relationship=='outside_parent':
        cursor.execute('CREATE TABLE public.completed_parent (LIKE imladri_archive.documents)')
        cursor.execute('ALTER TABLE imladri_archive.documents INHERIT public.completed_parent')
    else:
        cursor.execute('CREATE TABLE public.completed_child () INHERITS (imladri_archive.documents)')
    connection.commit();cursor.close()
    config,request,rows=metadata({'receipt':receipt,'active_application_records_written':0});spies=[]
    with pytest.raises(worker.OperatorBlocked,match='noninherited_owner_tables'):
        invoke(config,request,Journal(rows),connection_factory=factory_for(options,spies))
    assert spies[0].closed and spies[0].rollbacks and spies[0].commits==0
    assert not any('DECLARE imladri_archive_verify' in q for q in spies[0].statements)


@integration
def test_valid_catalog_FK_metadata_cannot_hide_an_existing_uncataloged_record(owner_database):
    connection,options=owner_database;receipt=seed_archive(connection)
    raw=BSON.encode({'_id':'fixture_orphan','private':'FICTIONAL_ORPHAN'})
    document=BSON(raw).decode();cursor=connection.cursor()
    try:
        # Only this disposable owner connection bypasses triggers to model an
        # existing orphan; FK metadata itself remains present and validated.
        cursor.execute("SET session_replication_role='replica'")
        cursor.execute('INSERT INTO imladri_archive.documents VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)',
            (archive.ARCHIVE_NAMESPACE,ALIAS,DATABASE,'not_in_catalog',document_key(document),
                BSON.encode({'_id':document['_id']}),raw,canonical_document(document),jsonb_text(document),hashlib.sha256(raw).hexdigest()))
    finally:
        cursor.execute("SET session_replication_role='origin'")
    connection.commit();cursor.close()
    config,request,rows=metadata({'receipt':receipt,'active_application_records_written':0})
    with pytest.raises(worker.OperatorBlocked,match='uncataloged_records'):
        invoke(config,request,Journal(rows),connection_factory=factory_for(options,[]))


@pytest.mark.parametrize('change',['insecure_TLS','app_role','other_database'])
def test_default_archive_owner_profile_fails_before_opening_connection(change):
    pytest.importorskip('pg8000')
    config,request,rows=metadata({'receipt':{'version':2}})
    if change=='insecure_TLS':config['owner_options']['ssl_context']=False
    elif change=='app_role':config['owner_options']['user']='imladri_app_forbidden'
    else:config['owner_options']['database']='other_database'
    with pytest.raises(worker.OperatorBlocked):
        invoke(config,request,Journal(rows),connection_factory=lambda **_:pytest.fail('Invalid owner profile connected'))
