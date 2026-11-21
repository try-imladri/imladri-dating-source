"""Request-only discovery batching retains all late sharing/adult/funding reads."""
import os

from bson import BSON
import pytest

from backend.postgres_remote import RemotePostgresDatabase
from backend.tests.test_individual_answer_history import run_case
from backend.tests.test_loading_performance import GatewayTimings
from backend.tests.test_postgres_rpc import ORIGIN,SECRET,URL

pytestmark=pytest.mark.skipif(not os.getenv('IMLADRI_TEST_POSTGRES_DSN'),reason='Needs disposable loopback PostgreSQL.')


class AfterDiscovery(GatewayTimings):
    after_discovery=None
    async def handle_async_request(self,request):
        value=BSON(await request.aread()).decode();response=await super().handle_async_request(request)
        if value.get('operation')=='aggregate'and value.get('collection')=='couples_pairs'and self.after_discovery:
            change=self.after_discovery;self.after_discovery=None;await change()
        return response


async def pair(db,server):
    member=await db.users.find_one({'user_id':'owner'})
    sharing={'version':server.couples_compatibility.SHARING_VERSION,'notice_sha256':server.couples_compatibility.NOTICE_SHA,
      'revision':1,'actor_id':'owner','couples_adult_at':member['couples_adult_at'],'personality':True,'intimacy':True}
    await db.couples_pairs.insert_one({'_id':'fixture-pair','env':server.ENV,'state':'active','members':['owner','peer'],
      'inviter':'owner','invitee':'peer','sponsor':'owner','benefit_members':['owner','peer'],'compatibility_sharing':{'a':sharing}})


def test_paired_cohort_matches_existing_me_out_and_preserves_late_live_checks(monkeypatch):
    async def scenario(db,call,quiz,server):
        await pair(db,server)
        transport=AfterDiscovery(db);remote=RemotePostgresDatabase(URL,SECRET,ORIGIN,db.name,transport=transport)
        monkeypatch.setenv('IMLADRI_POSTGRES_GATEWAY_SECRET',SECRET);monkeypatch.setattr(server,'db',remote)
        try:
            await remote.command('ping');member=await db.users.find_one({'user_id':'owner'},{'_id':0})
            old=await server.me_out(member)
            context=await server.account_read_context.AccountReadContext.read(remote,member)
            new=await server.me_out(member,database=context)
            assert new==old and new['main_consent']['pair_sharing']['personality']is True
            async def withdraw():
                await db.couples_pairs.update_one({'_id':'fixture-pair'},{'$set':{
                  'compatibility_sharing.a.personality':False,'compatibility_sharing.a.intimacy':False,'compatibility_sharing.a.revision':2}})
            transport.after_discovery=withdraw;transport.requests.clear()
            result=await call('GET','/me')
            sharing=result['main_consent']['pair_sharing']
            assert sharing['expected_revision']==2 and not sharing['personality']and not sharing['intimacy']
            # These are fresh real reads after the initial discovery snapshot.
            assert transport.requests.count(('find','couples_pairs'))>=3
            assert transport.requests.count(('find','users'))>=4
            async def underage():await db.users.update_one({'user_id':'peer'},{'$set':{'about.demographics.age':17}})
            transport.after_discovery=underage
            assert(await call('GET','/me'))['main_consent']['pair_sharing']['available']is False
            await db.users.update_one({'user_id':'peer'},{'$set':{'about.demographics.age':30}})
            async def unpair():await db.couples_pairs.update_one({'_id':'fixture-pair'},{'$set':{'state':'closed'}})
            transport.after_discovery=unpair
            await call('GET','/me',status=409)
            assert(await call('GET','/me'))['workspace_access']['mode']=='individual'
        finally:await remote.aclose()
    run_case(monkeypatch,scenario)


def test_request_context_does_not_reuse_user_role_pair_or_global_controls_across_requests(monkeypatch):
    async def scenario(db,call,quiz,server):
        await db.intro_controls.insert_one({'_id':'global','paused':False})
        transport=AfterDiscovery(db);remote=RemotePostgresDatabase(URL,SECRET,ORIGIN,db.name,transport=transport)
        monkeypatch.setenv('IMLADRI_POSTGRES_GATEWAY_SECRET',SECRET);monkeypatch.setattr(server,'db',remote)
        try:
            await remote.command('ping')
            member=await db.users.find_one({'user_id':'owner'})
            first=await server.account_read_context.AccountReadContext.read(remote,member)
            assert(await server.intro_access.settings(first))['paused']is False
            await db.intro_controls.update_one({'_id':'global'},{'$set':{'paused':True}})
            second=await server.account_read_context.AccountReadContext.read(remote,member)
            assert(await server.intro_access.settings(second))['paused']is True
            assert(await call('GET','/me',uid='admin'))['role']=='admin'
            await db.users.update_one({'user_id':'admin'},{'$set':{'role':'member'}})
            assert(await call('GET','/me',uid='admin'))['role']=='member'
            await pair(db,server)
            assert(await call('GET','/me'))['workspace_access']['mode']=='couples'
            await db.couples_pairs.update_one({'_id':'fixture-pair'},{'$set':{'state':'closed'}})
            assert(await call('GET','/me'))['workspace_access']['mode']=='individual'
            await db.users.update_one({'user_id':'owner'},{'$set':{'tier':'unsubscribed','subscription_period_end':0}})
            assert(await call('GET','/me'))['personal_access']is False
            await call('GET','/overview',status=402)
        finally:await remote.aclose()
    run_case(monkeypatch,scenario)
