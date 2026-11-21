"""Owned fresh Intimacy bootstrap; no provider, cross-request cache, or generation."""
import os

from bson import BSON
import httpx
import pytest

from backend.postgres_remote import RemotePostgresDatabase
from backend.tests.test_individual_answer_history import run_case
from backend.tests.test_loading_performance import GatewayTimings,timed,evidence
from backend.tests.test_postgres_rpc import ORIGIN,SECRET,URL

pytestmark=pytest.mark.skipif(not os.getenv('IMLADRI_TEST_POSTGRES_DSN'),reason='Needs disposable loopback PostgreSQL.')


class AfterAnswerRead(GatewayTimings):
    after_answer=None
    async def handle_async_request(self,request):
        value=BSON(await request.aread()).decode()
        response=await super().handle_async_request(request)
        if value.get('operation')=='find'and value.get('collection')=='intimacy_answers'and self.after_answer:
            change=self.after_answer;self.after_answer=None;await change()
        return response


async def seed(db,server):
    catalog=await server.assessment_quiz()
    key,variable=next(iter(server.intimacy_answers.catalog_variables(catalog['intimacy_copy']).items()))
    await db.intimacy_answers.insert_many([{'_id':uid,'revision':revision,'values':{key:0},
      'signatures':{key:variable['signature']},'conditions':{},'wording':{}}
      for uid,revision in(('owner',3),('peer',8))])
    return key


def test_intimacy_bootstrap_matches_saved_DTO_one_catalog_and_keeps_owner_guards(monkeypatch):
    async def scenario(db,call,quiz,server):
        await seed(db,server)
        transport=AfterAnswerRead(db);remote=RemotePostgresDatabase(URL,SECRET,ORIGIN,db.name,transport=transport)
        monkeypatch.setenv('IMLADRI_POSTGRES_GATEWAY_SECRET',SECRET);monkeypatch.setattr(server,'db',remote)
        try:
            await remote.command('ping')
            async def prior_flow():
                profile=await call('GET','/me/assessments');answers=await call('GET','/me/intimacy-answers')
                return{**profile,'intimacy_eligible':True,'intimacy_answers':answers}
            old,before=await timed(transport,prior_flow)
            new,after=await timed(transport,lambda:call('GET','/me/assessments?include_intimacy=true'))
            assert new==old and new['owner_id']=='owner'and new['intimacy_answers']['revision']==3
            # The quiz/copy facet is already optimized in the reference flow.
            assert before['RPCs']==6 and after['RPCs']==5
            assert after['operations']['aggregate:settings']==1
            assert after['operations']['find:intimacy_answers']==1
            assert after['peak_physical_requests']<=2
            assert(after['elapsed_ms']<before['elapsed_ms'])
            response=await call('GET','/me/assessments?include_intimacy=true',raw=True)
            assert response.headers['cache-control']=='private, no-store'
            peer=await call('GET','/me/assessments?include_intimacy=true',uid='peer')
            assert peer['owner_id']=='peer'and peer['intimacy_answers']['revision']==8
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://localhost')as api:
                transport.requests.clear()
                wrong=await api.get('/api/me/assessments?include_intimacy=true',headers={'x-history-member':'owner','X-Questionnaire-Owner':'peer'})
                assert wrong.status_code==409 and('find','intimacy_answers')not in transport.requests
            await call('GET','/me/assessments?include_intimacy=true',uid='unpaid',status=402)
            await call('GET','/me/assessments?include_intimacy=true',uid=None,status=401)
            evidence('intimacy-bootstrap-gateway-rpc-01.json',{'scope':'Sequential already-optimized profile+answers HTTP flow vs combinedfreshowner-bound read;25ms syntheticRTT, realPG/signatures.',
              'before':before,'after':after,'responses_equal':True,'published_catalog_loads':1,'late_profile_read_retained':True})
        finally:await remote.aclose()
    run_case(monkeypatch,scenario)


def test_unknown_minor_or_closing_profile_never_reads_raw_answers(monkeypatch):
    async def scenario(db,call,quiz,server):
        await seed(db,server)
        transport=AfterAnswerRead(db);remote=RemotePostgresDatabase(URL,SECRET,ORIGIN,db.name,transport=transport)
        monkeypatch.setenv('IMLADRI_POSTGRES_GATEWAY_SECRET',SECRET);monkeypatch.setattr(server,'db',remote)
        try:
            await remote.command('ping')
            for age in(None,'',17):
                await db.users.update_one({'user_id':'owner'},{'$set':{'about.demographics.age':age}})
                transport.requests.clear()
                result=await call('GET','/me/assessments?include_intimacy=true')
                assert result['about']['demographics']['age']==age
                assert result['intimacy_eligible']is False and result['intimacy_answers']is None
                assert('find','intimacy_answers')not in transport.requests
            await db.users.update_one({'user_id':'owner'},{'$set':{'closing':True}})
            transport.requests.clear();await call('GET','/me/assessments?include_intimacy=true',status=409)
            assert('find','intimacy_answers')not in transport.requests
        finally:await remote.aclose()
    run_case(monkeypatch,scenario)


def test_late_age_profile_subscription_and_pair_changes_do_not_expose_stale_answers(monkeypatch):
    async def scenario(db,call,quiz,server):
        await seed(db,server)
        transport=AfterAnswerRead(db);remote=RemotePostgresDatabase(URL,SECRET,ORIGIN,db.name,transport=transport)
        monkeypatch.setenv('IMLADRI_POSTGRES_GATEWAY_SECRET',SECRET);monkeypatch.setattr(server,'db',remote)
        try:
            await remote.command('ping')
            async def minor():await db.users.update_one({'user_id':'owner'},{'$set':{'about.demographics.age':17}})
            transport.after_answer=minor
            result=await call('GET','/me/assessments?include_intimacy=true')
            assert result['about']['demographics']['age']==17 and result['intimacy_answers']is None and not result['intimacy_eligible']
            await db.users.update_one({'user_id':'owner'},{'$set':{'about.demographics.age':30}})
            async def profile():await db.users.update_one({'user_id':'owner'},{'$set':{'about.describe':'Fresh profile after read'}})
            transport.after_answer=profile
            assert(await call('GET','/me/assessments?include_intimacy=true'))['about']['describe']=='Fresh profile after read'
            async def revoke():await db.users.update_one({'user_id':'owner'},{'$set':{'tier':'unsubscribed','subscription_period_end':0}})
            transport.after_answer=revoke
            await call('GET','/me/assessments?include_intimacy=true',status=402)
            # Sponsored access is freshly checked at both boundaries too.
            import time
            await db.users.update_one({'user_id':'owner'},{'$set':{'tier':'paid','subscription_period_end':int(time.time())+86400}})
            await db.users.update_one({'user_id':'peer'},{'$set':{'tier':'unsubscribed','subscription_period_end':0}})
            await db.couples_pairs.insert_one({'_id':'active-fixture-pair','env':server.ENV,'state':'active',
              'members':['owner','peer'],'inviter':'owner','invitee':'peer','sponsor':'owner'})
            assert(await call('GET','/me/assessments?include_intimacy=true',uid='peer'))['intimacy_answers']['revision']==8
            async def unpair():await db.couples_pairs.update_one({'_id':'active-fixture-pair'},{'$set':{'state':'ended'}})
            transport.after_answer=unpair
            await call('GET','/me/assessments?include_intimacy=true',uid='peer',status=402)
        finally:await remote.aclose()
    run_case(monkeypatch,scenario)
