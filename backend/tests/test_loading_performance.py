"""Real isolated PostgreSQL through the signed gateway, with a labelled RTT fixture."""
import asyncio
from collections import Counter
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import time

from bson import BSON
import httpx
import pytest

from backend import trends
from backend.postgres_remote import RemotePostgresDatabase
from backend.tests.test_individual_answer_history import run_case
from backend.tests.test_postgres_rpc import app_for,ORIGIN,SECRET,URL

pytestmark=pytest.mark.skipif(not os.getenv('IMLADRI_TEST_POSTGRES_DSN'),reason='Needs disposable loopback PostgreSQL.')


class GatewayTimings(httpx.AsyncBaseTransport):
    def __init__(self,db):
        self.inner=httpx.ASGITransport(app=app_for(db));self.requests=[];self.active=0;self.peak=0
    async def handle_async_request(self,request):
        value=BSON(await request.aread()).decode()
        self.requests.append((value['operation'],value.get('collection')))
        self.active+=1;self.peak=max(self.peak,self.active)
        try:
            # Models only a fixed gateway RTT. Native DB, BSON, signatures,
            # repository snapshots and the unchanged2-call admission are real.
            await asyncio.sleep(.025)
            return await self.inner.handle_async_request(request)
        finally:self.active-=1
    async def aclose(self):await self.inner.aclose()


async def timed(transport,call):
    transport.requests.clear();start=time.perf_counter();result=await call()
    return result,{'RPCs':len(transport.requests),'elapsed_ms':round((time.perf_counter()-start)*1000,2),
      'operations':dict(Counter(operation+':'+str(collection)for operation,collection in transport.requests)),
      'injected_RTT_ms':25,'peak_physical_requests':transport.peak}


def evidence(name,value):
    folder=os.getenv('IMLADRI_PERFORMANCE_EVIDENCE_DIR')
    if folder:
        path=Path(folder)/name
        with path.open('x',encoding='utf8')as stream:json.dump(value,stream,indent=2);stream.write('\n')


def test_people_page_batches_exact_meter_counts_and_preserves_pagination_owner_and_access(monkeypatch):
    async def scenario(db,call,quiz,server):
        stamp=datetime.now(timezone.utc).isoformat()
        people=[{'id':'person-'+str(i),'user_id':'owner','nickname':'Person '+str(i),'rel_type':'friend',
          'created_at':stamp,'last_activity':stamp,'intake':{},'archived':i==11}for i in range(12)]
        await db.people.insert_many(people+[{'id':'other-person','user_id':'peer','nickname':'PRIVATE_PEER','rel_type':'friend'},
          {'id':'deleted-person','user_id':'owner','nickname':'DELETING','rel_type':'friend','deleting':True}])
        await db.exchanges.insert_many([{'id':str(i)+'-'+str(j),'user_id':'owner','person_id':person['id'],
          'created_at':stamp,'has_conversation':j in(0,1),**({'outcome':value}if j else{})}
          for i,person in enumerate(people)for j,value in enumerate((None,None,'','done',False,0))]+[
          {'id':'peer-exchange','user_id':'peer','person_id':'other-person','has_conversation':True,'outcome':'private'}])
        transport=GatewayTimings(db);remote=RemotePostgresDatabase(URL,SECRET,ORIGIN,db.name,transport=transport)
        monkeypatch.setenv('IMLADRI_POSTGRES_GATEWAY_SECRET',SECRET);monkeypatch.setattr(server,'db',remote)
        try:
            await remote.command('ping')
            actual_bulk=server.listed_people_meters
            async def unbatched(rows):return {p['id']:await server.meter(p)for p in rows}
            monkeypatch.setattr(server,'listed_people_meters',unbatched)
            old,old_stats=await timed(transport,lambda:call('GET','/people?page_size=12'))
            monkeypatch.setattr(server,'listed_people_meters',actual_bulk)
            new,new_stats=await timed(transport,lambda:call('GET','/people?page_size=12'))
            assert new==old and new['total']==12 and len(new['people'])==12
            assert old_stats['operations']['count:exchanges']==24
            assert new_stats['operations']['aggregate:exchanges']==2
            assert new_stats['RPCs']==old_stats['RPCs']-22
            assert new_stats['elapsed_ms']<old_stats['elapsed_ms']*.6
            assert new_stats['peak_physical_requests']<=2
            assert 'PRIVATE_PEER'not in str(new)and'DELETING'not in str(new)
            first=await call('GET','/people?page_size=5');second=await call('GET','/people?page_size=5&cursor='+first['next_cursor'])
            assert not({p['id']for p in first['people']}&{p['id']for p in second['people']})
            await call('GET','/people',uid='unpaid',status=402);await call('GET','/people',uid=None,status=401)
            await db.exchanges.delete_many({'person_id':'person-0'})
            latest=await call('GET','/people?page_size=12')
            assert next(p for p in latest['people']if p['id']=='person-0')['meter']['conversations']==0
            evidence('people-gateway-rpc-01.json',{'scope':'Same endpoint with legacy2N meter helper vs bulk helper;25ms synthetic RTT, real signed gateway/PostgreSQL.',
              'before':old_stats,'after':new_stats,'responses_equal':True,'private_cross_request_cache':False})
        finally:await remote.aclose()
    run_case(monkeypatch,scenario)


def test_overview_facets_keep_UTC_filters_whole_counts_and_fresh_removal(monkeypatch):
    async def scenario(db,call,quiz,server):
        today=datetime.now(timezone.utc).date();current=today.isoformat()+'T12:00:00+00:00';old=(today-timedelta(days=40)).isoformat()+'T12:00:00Z'
        offset=(today-timedelta(days=1)).isoformat()+'T23:30:00-02:00'
        await db.people.insert_many([{'id':'p','user_id':'owner','nickname':'Fixture','rel_type':'friend','created_at':current,'report':{'updated_at':offset}},
          {'id':'other','user_id':'peer','nickname':'PRIVATE_PEER','rel_type':'friend','created_at':current}])
        await db.exchanges.insert_many([{'id':str(i),'user_id':'owner','person_id':'p','created_at':current,
          'has_conversation':True,'outcome':value,'outcome_at':stamp}for i,(value,stamp)in enumerate((('done',offset),('',current),(None,current),('old',old),('bson',datetime.combine(today,datetime.min.time(),timezone.utc))))])
        await db.preps.insert_many([{'id':'one','user_id':'owner','person_id':'p','situation':'fixture','created_at':current,'outcome':'done','outcome_at':offset},
          {'id':'two','user_id':'owner','person_id':'p','situation':'fixture','created_at':old,'outcome':'','outcome_at':current}])
        await db.quick_advice.insert_many([{'id':str(i),'user_id':uid,'workspace':workspace,'deleted':deleted,'created_at':current}
          for i,(uid,workspace,deleted)in enumerate((('owner','individual',False),('owner','individual',True),('owner','couples',False),('peer','individual',False)))])
        transport=GatewayTimings(db);remote=RemotePostgresDatabase(URL,SECRET,ORIGIN,db.name,transport=transport)
        monkeypatch.setenv('IMLADRI_POSTGRES_GATEWAY_SECRET',SECRET);monkeypatch.setattr(server,'db',remote)
        async def unbatched(database,uid,dates):
            owner={'user_id':uid}
            people,reads,preps,practice,groups,quick=await asyncio.gather(
              database.people.find(owner,{'_id':0,'id':1,'nickname':1,'rel_type':1,'archived':1,'report.updated_at':1,'created_at':1}).to_list(None),
              database.exchanges.find(owner,{'_id':0,'id':1,'person_id':1,'has_conversation':1,'created_at':1,'outcome':1,'choice':1,'outcome_at':1,'memories_reviewed':1,'proposed_memories':1}).sort('created_at',-1).to_list(500),
              database.preps.find({**owner,'deleting':{'$ne':True}},{'_id':0,'person_id':1,'situation':1,'created_at':1,'outcome_at':1}).sort('created_at',-1).to_list(300),
              database.practice.find(owner,{'person_id':1,'created_at':1}).sort('created_at',-1).limit(20).to_list(20),
              database.group_chats.find(owner,{'person_ids':1,'created_at':1}).sort('created_at',-1).limit(20).to_list(20),
              database.quick_advice.find({**owner,'workspace':'individual','deleted':{'$ne':True}},{'id':1,'created_at':1}).sort('created_at',-1).limit(20).to_list(20))
            metrics={'people_added','person_reports','reads','outcomes','game_plans','plan_followups','practice','group_chats','quick_advice'}
            counts=await asyncio.gather(*(trends.daily_counts(database[name],metric,stamp,dates,owner,filters,users=True)
              for metric,name,stamp,filters in trends.SAVED_RECORDS if metric in metrics))
            eligible={**owner,'person_id':{'$in':[p['id']for p in people]}}
            pending={**eligible,'choice.label':{'$exists':True},'outcome':{'$in':[None,'']}}
            review={**eligible,'memories_reviewed':{'$ne':True},'proposed_memories.0':{'$exists':True}}
            fields={'_id':0,'id':1,'person_id':1,'created_at':1,'choice.label':1,'proposed_memories':1};latest=[('created_at',-1),('id',-1)]
            pending_rows,review_rows,pending_count,review_count=await asyncio.gather(
              database.exchanges.find(pending,fields).sort(latest).limit(5).to_list(5),database.exchanges.find(review,fields).sort(latest).limit(5).to_list(5),
              database.exchanges.count_documents(pending),database.exchanges.count_documents(review))
            return{'people':people,'exchanges':reads,'preps':preps,'practice':practice,'group_chats':groups,'quick_advice':quick,'counts':counts,
              'pending':pending_rows,'review':review_rows,'pending_count':pending_count,'review_count':review_count,
              'unread':await database.notifications.count_documents({**owner,'read':False})}
        try:
            await remote.command('ping');bulk=server.member_overview.read
            monkeypatch.setattr(server.member_overview,'read',unbatched)
            old_result,old_stats=await timed(transport,lambda:call('GET','/overview'))
            monkeypatch.setattr(server.member_overview,'read',bulk)
            new_result,new_stats=await timed(transport,lambda:call('GET','/overview'))
            assert new_result==old_result
            assert old_stats['RPCs']==25 and new_stats['RPCs']==12
            assert new_stats['peak_physical_requests']<=2
            assert sum(row['count']for row in new_result['activity'])==12
            assert 'PRIVATE_PEER'not in str(new_result)
            await db.quick_advice.update_one({'id':'0'},{'$set':{'deleted':True}})
            fresh=await call('GET','/overview')
            assert sum(row['count']for row in fresh['activity'])==11
            assert not any(row.get('advice_id')=='0'for row in fresh['feed'])
            await call('GET','/overview',uid='unpaid',status=402);await call('GET','/overview',uid=None,status=401)
            evidence('overview-gateway-rpc-01.json',{'scope':'Same fullHTTP endpoint; original separate list/count/day reads vs percollection facets;25ms synthetic RTT, real signed gateway/PostgreSQL.',
              'before':old_stats,'after':new_stats,'responses_equal':True,'UTC_offset_BSON_and_deleted_workspace_filters_preserved':True})
        finally:await remote.aclose()
    run_case(monkeypatch,scenario)


def test_overview_feed_sorts_owned_BSON_ISO_offsets_and_ignores_invalid_stamps(monkeypatch):
    async def scenario(db,call,quiz,server):
        await db.people.insert_many([{'id':'p','user_id':'owner','nickname':'Owner','rel_type':'friend',
          'created_at':datetime(2026,1,1,12)}, {'id':'p2','user_id':'owner','nickname':'Owner2','rel_type':'friend',
          'created_at':'2026-01-01T16:00:00+02:00'}, {'id':'peer','user_id':'peer','nickname':'PRIVATE_PEER','rel_type':'friend',
          'created_at':'2026-01-02T00:00:00Z'}])
        await db.exchanges.insert_many([{'id':str(i),'user_id':'owner','person_id':'p','created_at':stamp}
          for i,stamp in enumerate(('2026-01-01T08:30:00-05:00','not-a-date',None,'2026-01-01T11:00:00'))])
        await db.group_chats.insert_many([{'id':'group','user_id':'owner','person_ids':['p','p2'],'created_at':datetime(2026,1,1,13)},
          {'id':'invalid-group','user_id':'owner','person_ids':['p','p2'],'created_at':'bad'}])
        await db.quick_advice.insert_many([{'id':'advice','user_id':'owner','workspace':'individual','created_at':datetime(2026,1,1,15,tzinfo=timezone.utc)},
          {'id':'bad-advice','user_id':'owner','workspace':'individual','created_at':'bad'}])
        result=await call('GET','/overview')
        assert [row['type']for row in result['feed']]==['quick_advice','person','question','group','person']
        assert [row['ts']for row in result['feed']]==['2026-01-01T15:00:00+00:00','2026-01-01T16:00:00+02:00',
          '2026-01-01T08:30:00-05:00','2026-01-01T13:00:00+00:00','2026-01-01T12:00:00+00:00']
        assert result['feed'][0]['advice_id']=='advice'and result['feed'][1]['person_id']=='p2'
        assert all(isinstance(row['ts'],str)for row in result['feed'])and'PRIVATE_PEER'not in str(result)
    run_case(monkeypatch,scenario)


def test_account_counts_and_published_assessment_bundle_reduce_RPCs_without_caching(monkeypatch):
    async def scenario(db,call,quiz,server):
        await db.people.insert_many([{'id':str(i),'user_id':'owner','archived':archived}for i,archived in enumerate((True,False,None))]+[{'id':'peer','user_id':'peer'}])
        transport=GatewayTimings(db);remote=RemotePostgresDatabase(URL,SECRET,ORIGIN,db.name,transport=transport)
        monkeypatch.setenv('IMLADRI_POSTGRES_GATEWAY_SECRET',SECRET);monkeypatch.setattr(server,'db',remote)
        async def original_quiz():
            stored=await remote.settings.find_one({'key':'global'},{'_id':0,'quiz':1})or{}
            base=stored.get('quiz')or{};base=server.with_quiz_scoring(base)if base.get('version')==server.default_quiz()['version']else server.default_quiz()
            edits=await server.intimacy_catalog.overrides(remote);version,variables=server.research.catalog(base,edits)
            return{**{key:value for key,value in base.items()if key!='legacy_wording'},'intimacy_copy':edits,
              'research_catalog_version':version,'research_intimacy_signature':server.research.intimacy_signature(variables)}
        try:
            await remote.command('ping');member=await db.users.find_one({'user_id':'owner'},{'_id':0})
            async def original_me():
                account,count,usage=await asyncio.gather(server.me_out(member),remote.people.count_documents({'user_id':'owner'}),server.plan_usage(member))
                return{**account,'people_count':count,'usage':usage}
            old_me,me_before=await timed(transport,original_me);new_me,me_after=await timed(transport,lambda:server.get_me(member))
            assert new_me==old_me and new_me['people_count']==3 and new_me['usage']['active_people']==2
            assert me_before['RPCs']==7 and me_after['RPCs']==2
            old_quiz,quiz_before=await timed(transport,original_quiz);new_quiz,quiz_after=await timed(transport,server.assessment_quiz)
            assert new_quiz==old_quiz and quiz_before['RPCs']==2 and quiz_after['RPCs']==1
            (section,topic),(question,phrase)=next(iter(server.intimacy_catalog.questions().items()))
            await db.intimacy_question_copy.insert_one({'_id':section+'/'+topic,'section':section,'topic':topic,'changed':True,
              'question':'Fresh published wording?','phrase':phrase,'draft':{'question':'PRIVATE_DRAFT'},'revision':1})
            fresh=await call('GET','/me/assessments')
            assert fresh['quiz']['intimacy_copy']==[{'section':section,'topic':topic,'question':'Fresh published wording?','phrase':phrase}]
            assert 'PRIVATE_DRAFT'not in str(fresh)and'prompts'not in fresh['quiz']
            await db.intimacy_question_copy.update_one({'_id':section+'/'+topic},{'$set':{'changed':False}})
            assert (await call('GET','/quiz'))['intimacy_copy']==[]
            await db.settings.delete_many({'key':'global'})
            assert await server.assessment_quiz()==await original_quiz()
            await call('GET','/me/assessments',uid='unpaid',status=402);await call('GET','/me/assessments',uid=None,status=401)
            evidence('entry-assessment-gateway-rpc-01.json',{'scope':'Handler paths after same freshcurrent_member;25ms synthetic RTT, real signed gateway/PostgreSQL.',
              'account_before':me_before,'account_after':me_after,'assessment_before':quiz_before,'assessment_after':quiz_after,
              'responses_equal':True,'fresh_publication_and_empty_settings_fallback_preserved':True,'member_or_role_cache':False})
        finally:await remote.aclose()
    run_case(monkeypatch,scenario)
