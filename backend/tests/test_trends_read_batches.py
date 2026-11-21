"""Admin trends read batches preserve legacy payments, eligibility and disclosure."""
import asyncio
from datetime import datetime,timedelta,timezone
import os

import pytest

from backend import trends
from backend.postgres_remote import RemotePostgresDatabase
from backend.tests.test_individual_answer_history import run_case
from backend.tests.test_loading_performance import GatewayTimings,timed,evidence
from backend.tests.test_postgres_rpc import ORIGIN,SECRET,URL

pytestmark=pytest.mark.skipif(not os.getenv('IMLADRI_TEST_POSTGRES_DSN'),reason='Needs disposable loopback PostgreSQL.')


def test_admin_trends_batches_payments_and_saved_records_with_exact_old_counts_and_disclosure(monkeypatch):
    async def scenario(db,call,quiz,server):
        stamp=datetime.now(timezone.utc).isoformat();old=(datetime.now(timezone.utc)-timedelta(days=40)).isoformat()
        answers={q['id']:{'choice':0,'question':q['q'],'label':q['options'][0]['label'],'note':'PRIVATE_MEMBER_NOTE'}for q in trends.EVERYDAY}
        await db.users.insert_many([{'user_id':'trends-'+str(i),'role':'member','env':server.ENV,'email':'fixture'+str(i)+'@invalid.test',
          'everyday_trends_opt_in':True,'about':{'answers':answers},'created_at':stamp}for i in range(12)])
        await db.users.update_one({'user_id':'peer'},{'$set':{'env':'other-environment'}})
        transactions=[
          {'kind':'purchase','payment_status':'paid','created_at':old,'fulfilled_at':stamp},
          {'kind':'purchase','payment_status':'paid','created_at':stamp},
          {'kind':'purchase','payment_status':'pending','created_at':stamp},
          {'kind':'topup','payment_status':'paid','created_at':old,'fulfilled_at':stamp,'topup_credits':100},
          {'kind':'topup','payment_status':'paid','created_at':stamp,'topup_credits':50},
          {'kind':'topup','payment_status':'pending','created_at':stamp,'topup_credits':0},
          {'kind':'topup','payment_status':'paid','created_at':stamp,'fulfilled_at':stamp,'topup_credits':70},
          {'kind':'renewal','payment_status':'paid','created_at':stamp},
          {'kind':'upgrade','payment_status':'paid','created_at':stamp},
        ]
        rows=[{'_id':'tx-'+str(i),'user_id':'owner','env':server.ENV,'memo':'PRIVATE_BILLING_NOTE',**row}for i,row in enumerate(transactions)]
        rows +=[{'_id':'excluded-'+str(i),'user_id':uid,'env':env,'kind':'purchase','payment_status':'paid',
          'created_at':stamp,'fulfilled_at':stamp}for i,(uid,env)in enumerate((('owner','other-environment'),('peer',server.ENV),('admin',server.ENV),('missing',server.ENV)))]
        await db.payment_transactions.insert_many(rows)
        await db.exchanges.insert_many([{'id':'read-'+str(i),'user_id':uid,'created_at':stamp,'outcome':'done','outcome_at':stamp}
          for i,uid in enumerate(('owner','peer','admin','missing'))])
        transport=GatewayTimings(db);remote=RemotePostgresDatabase(URL,SECRET,ORIGIN,db.name,transport=transport)
        monkeypatch.setenv('IMLADRI_POSTGRES_GATEWAY_SECRET',SECRET);monkeypatch.setattr(server,'db',remote)
        async def unbatched(collection,records,dates,eligible,*,users=False):
            groups=await asyncio.gather(*(trends.daily_counts(collection,metric,stamp,dates,eligible,filters,users)
              for metric,stamp,filters in records))
            return[row for group in groups for row in group]
        try:
            await remote.command('ping');batched=trends.daily_counts_group
            monkeypatch.setattr(trends,'daily_counts_group',unbatched)
            before,before_stats=await timed(transport,lambda:call('GET','/admin/trends?days=30',uid='admin'))
            monkeypatch.setattr(trends,'daily_counts_group',batched)
            after,after_stats=await timed(transport,lambda:call('GET','/admin/trends?days=30',uid='admin'))
            assert after==before
            assert before_stats['RPCs']-after_stats['RPCs']==16
            assert before_stats['operations']['aggregate:payment_transactions']==10
            assert after_stats['operations']['aggregate:payment_transactions']==1
            assert after_stats['peak_physical_requests']<=2
            totals=after['totals']
            assert {key:totals[key]for key in('checkouts_started','paid_purchases','paid_renewals','paid_upgrades','refill_checkouts','paid_refills','adjusted_refills')}=={
              'checkouts_started':2,'paid_purchases':2,'paid_renewals':1,'paid_upgrades':1,'refill_checkouts':3,'paid_refills':3,'adjusted_refills':2}
            assert totals['reads']==1 and totals['outcomes']==1
            assert all(row['available']and row['respondents']==12 for row in after['everyday']['questions'])
            assert 'PRIVATE_'not in str(after)
            await db.users.update_one({'user_id':'trends-0'},{'$set':{'everyday_trends_opt_in':False}})
            fresh=await call('GET','/admin/trends?days=30',uid='admin')
            assert all(row['respondents']==11 for row in fresh['everyday']['questions'])
            await db.users.update_many({'user_id':{'$in':['trends-1','trends-2']}},{'$set':{'everyday_trends_opt_in':False}})
            hidden=await call('GET','/admin/trends?days=30',uid='admin')
            assert all(not row['available']and row['respondents']is None for row in hidden['everyday']['questions'])
            await call('GET','/admin/trends?days=30',status=403);await call('GET','/admin/trends?days=30',uid=None,status=401)
            evidence('admin-trends-gateway-rpc-01.json',{'scope':'Same Admin report with original separate daily reads versus fixedpercollectionfacets;25ms injectedRTT andactualsignedPG.',
              'before':before_stats,'after':after_stats,'responses_equal':True,'duplicate_metric_legacy_payment_fallbacks_retained':True,
              'fresh_consent_and_suppression_tests_pass':True,'no_billing_mutations':True})
        finally:await remote.aclose()
    run_case(monkeypatch,scenario)
