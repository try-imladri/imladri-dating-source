"""Membership batching against real isolated PostgreSQL and the signed gateway.

All people, payments and adjustments are fictional local records. No payment,
identity or other external provider is invoked.
"""
import asyncio
from datetime import datetime, timezone
import os
from types import SimpleNamespace

import pytest

from backend import billing_adjustments, membership_admin
from backend.postgres_remote import RemotePostgresDatabase
from backend.tests.test_individual_answer_history import run_case
from backend.tests.test_loading_performance import GatewayTimings, timed, evidence
from backend.tests.test_postgres_rpc import SECRET, ORIGIN, URL

pytestmark = pytest.mark.skipif(not os.getenv('IMLADRI_TEST_POSTGRES_DSN'), reason='Needs disposable loopback PostgreSQL.')


async def original_amounts(db, env, scope, start, end):
    """The four unchanged helper calls used by the previous summary."""
    received, offsets, refills, refill_offsets = await asyncio.gather(
        membership_admin.monthly_payments(db, env, scope, start, end, gross=True),
        membership_admin.monthly_offsets(db, env, start),
        membership_admin.monthly_payments(db, env, scope, start, end, refills=True, gross=True),
        membership_admin.monthly_offsets(db, env, start, refills=True))
    return dict(zip(('subscription', 'subscription_adjustment', 'topup', 'topup_adjustment'),
                    (received, offsets, refills, refill_offsets)))


def test_summary_batches_receipts_without_changing_dates_environments_tax_or_deduplication(monkeypatch):
    async def scenario(db, call, quiz, server):
        env=server.ENV;scope=server.IN_ENV;at=datetime(2026,12,20,tzinfo=timezone.utc)
        await db.users.insert_many([{'user_id':'legacy'}, {'user_id':'foreign','env':'other-environment'}])
        base={'kind':'purchase','currency':'usd','payment_status':'paid','user_id':'owner','env':env,
              'created_at':'2026-11-30T23:59:59Z','fulfilled_at':'2026-12-01T00:00:00Z','amount':99,
              'subscription_revenue_cents':1200,'received_cents':1324}
        rows=[{**base,'_id':'paid','session_id':'paid'},
              {**base,'_id':'duplicate','session_id':'paid'},
              {**base,'_id':'renewal','kind':'renewal','invoice_id':'renewal','created_at':datetime(2026,12,2),'subscription_revenue_cents':1100},
              {**base,'_id':'upgrade','kind':'upgrade','invoice_id':'upgrade','created_at':'2026-11-30T23:40:00-05:00','subscription_revenue_cents':800},
              {**base,'_id':'legacy','session_id':'legacy','env':None,'user_id':'legacy','fulfilled_at':None,'created_at':'2026-12-01T00:00:00'},
              {**base,'_id':'tagged-orphan','session_id':'orphan','user_id':'gone','subscription_revenue_cents':100},
              {**base,'_id':'refill','session_id':'refill','kind':'topup','fulfilled_at':'2027-01-01T00:10:00+02:00','subscription_revenue_cents':600},
              {**base,'_id':'legacy-refill','session_id':'legacy-refill','kind':'topup','env':None,'user_id':'legacy','fulfilled_at':None,
               'created_at':datetime(2026,12,2,tzinfo=timezone.utc),'subscription_revenue_cents':None,'amount':'12.50'},
              *[{**base,'_id':'excluded-'+str(i),'session_id':'excluded-'+str(i),**change} for i,change in enumerate([
                  {'fulfilled_at':'2026-12-01T00:10:00+02:00'}, {'fulfilled_at':'2026-12-31T23:40:00-05:00'},
                  {'fulfilled_at':['2026-12-01']},{'fulfilled_at':'2026-12-01-invalid'}, {'fulfilled_at':1796101200000},
                  {'env':'other-environment'}, {'env':None,'user_id':'foreign'}, {'env':None,'user_id':'orphan'},
                  {'payment_status':'pending'},{'currency':'eur'},{'subscription_revenue_cents':True},
                  {'subscription_revenue_cents':-1},{'subscription_revenue_cents':None,'amount':'invalid'}])]]
        await db.payment_transactions.insert_many(rows)
        events=[{'_id':'refund','env':env,'type':'billing_financial_adjustment','ts':'2026-11-30T23:40:00-05:00','data':{'kind':'purchase','offset_cents':300}},
                {'_id':'refill-refund','env':env,'type':'billing_financial_adjustment','ts':datetime(2026,12,3),'data':{'kind':'topup','offset_cents':100}},
                {'_id':'reversal','env':env,'type':'billing_financial_adjustment','ts':'2026-12-04T00:00:00Z','data':{'kind':'renewal','offset_cents':-50}},
                {'_id':'other-env','env':'other-environment','type':'billing_financial_adjustment','ts':'2026-12-04T00:00:00Z','data':{'offset_cents':9000}},
                {'_id':'invalid-adjustment','env':env,'type':'billing_financial_adjustment','ts':'2026-12-04T00:00:00Z','data':{'offset_cents':True}},
                {'_id':'outside-adjustment','env':env,'type':'billing_financial_adjustment','ts':'2026-12-31T23:40:00-05:00','data':{'offset_cents':9000}}]
        await db.events.insert_many(events)
        await db.closed_membership_revenue.insert_many([
            membership_admin.paid_receipt(rows[0],env), membership_admin.paid_receipt(rows[6],env),
            membership_admin.financial_receipt(events[0],env),membership_admin.financial_receipt(events[1],env),
            {'_id':'closed-legacy','env':env,'month':'2026-12-01','cents':200},
            {'_id':'closed-null','env':env,'month':'2026-12-01','kind':None,'cents':50},
            {'_id':'closed-sub','env':env,'month':'2026-12-01','kind':'subscription','cents':75},
            {'_id':'closed-topup','env':env,'month':'2026-12-01','kind':'topup','cents':25},
            {'_id':'closed-refund','env':env,'month':'2026-12-01','kind':'subscription_adjustment','cents':25},
            {'_id':'closed-topup-refund','env':env,'month':'2026-12-01','kind':'topup_adjustment','cents':15},
            {'_id':'closed-other','env':'other-environment','month':'2026-12-01','cents':99999},
            {'_id':'closed-other-month','env':env,'month':'2027-01-01','cents':99999},
            {'_id':'closed-unknown-kind','env':env,'month':'2026-12-01','kind':'invalid','cents':99999}])
        transport=GatewayTimings(db);remote=RemotePostgresDatabase(URL,SECRET,ORIGIN,db.name,transport=transport)
        monkeypatch.setenv('IMLADRI_POSTGRES_GATEWAY_SECRET',SECRET)
        try:
            await remote.command('ping');optimized=membership_admin.monthly_summary_amounts
            monkeypatch.setattr(membership_admin,'monthly_summary_amounts',original_amounts)
            before,before_stats=await timed(transport,lambda:membership_admin.summary(remote,env,scope,at))
            monkeypatch.setattr(membership_admin,'monthly_summary_amounts',optimized)
            after,after_stats=await timed(transport,lambda:membership_admin.summary(remote,env,scope,at))
            assert after==before
            assert after['monthly_gross_revenue_cents']==4725
            assert after['monthly_refund_dispute_adjustments_cents']==275
            assert after['monthly_refill_gross_revenue_cents']==1875
            assert after['monthly_refill_adjustments_cents']==115
            assert after['monthly_net_revenue_cents']==4450 and after['monthly_refill_net_revenue_cents']==1760
            assert before_stats['RPCs']==12 and after_stats['RPCs']==6
            assert after_stats['operations']['aggregate:payment_transactions']==1
            assert after_stats['operations']['find:closed_membership_revenue']==1
            assert after_stats['peak_physical_requests']<=2
            evidence('membership-summary-gateway-rpc-01.json',{'scope':'Four unchanged monthly helpers versus combined summary;25ms synthetic RTT, real signed gateway/PostgreSQL.',
                'before':before_stats,'after':after_stats,'responses_equal':True,'no_private_cross_request_cache':True})
        finally:await remote.aclose()
    run_case(monkeypatch,scenario)


def test_summary_flushes_existing_outbox_once_and_preserves_unresolved_uncertainty(monkeypatch):
    async def scenario(db,call,quiz,server):
        env=server.ENV;scope=server.IN_ENV;at=datetime(2026,12,20,tzinfo=timezone.utc)
        pending={'_id':'outbox-event','env':env,'ts':'2026-12-02T00:00:00+00:00','data':{'kind':'topup','offset_cents':75}}
        await db.payment_transactions.insert_one({'_id':'outbox-tx','env':env,'user_id':'owner','kind':'topup','payment_status':'paid',
            'currency':'usd','fulfilled_at':'2026-12-01T00:00:00Z','subscription_revenue_cents':1200,'billing_adjustment_outbox':pending})
        markers=[{'_id':'pending','env':env,'month':'unknown','kind':'unresolved_adjustment'},
                 {'_id':'resolved','env':env,'month':'2026-12-01','kind':'unresolved_adjustment','data':{'status':'resolved'}},
                 {'_id':'no-loss','env':env,'month':'2026-12-01','kind':'unresolved_adjustment','data':{'reason':'pending_dispute_no_credit_reversal'}},
                 {'_id':'other-month','env':env,'month':'2026-11-01','kind':'unresolved_adjustment'}]
        await db.closed_membership_revenue.insert_many(markers)
        actual_flush=billing_adjustments.flush_pending;calls=[]
        async def flush(database,environment,uid=None):
            calls.append((environment,uid));await actual_flush(database,environment,uid)
        monkeypatch.setattr(billing_adjustments,'flush_pending',flush)
        result=await membership_admin.summary(db,env,scope,at)
        assert calls==[(env,None)]
        assert result['monthly_refill_gross_revenue_cents']==1200 and result['monthly_refill_adjustments_cents']==75
        assert result['pending_financial_reviews']==1 and result['net_revenue_verified']is False
        assert result['monthly_net_revenue_cents']is result['monthly_revenue_cents']is result['monthly_refill_net_revenue_cents']is None
        assert 'billing_adjustment_outbox'not in await db.payment_transactions.find_one({'_id':'outbox-tx'})
        archive=await db.closed_membership_revenue.find({'kind':'topup_adjustment'}).to_list(None)
        assert len(archive)==1 and archive[0]['cents']==75
        # Repeated reads are fresh and idempotent: no duplicate publication or cached warning.
        assert await membership_admin.summary(db,env,scope,at)==result
        await db.closed_membership_revenue.update_one({'_id':'pending'},{'$set':{'data.status':'resolved'}})
        fresh=await membership_admin.summary(db,env,scope,at)
        assert fresh['pending_financial_reviews']==0 and fresh['monthly_refill_net_revenue_cents']==1125
        assert await db.closed_membership_revenue.count_documents({'kind':'topup_adjustment'})==1
        monkeypatch.setattr(server,'db',db)
        await call('GET','/admin/membership/summary',uid='owner',status=403)
        await call('GET','/admin/membership/summary',uid=None,status=401)
        await call('GET','/admin/membership/summary',uid='admin')
    run_case(monkeypatch,scenario)


def test_archive_read_after_both_live_streams_handles_closure_without_loss_or_double_count(monkeypatch):
    async def scenario(db,call,quiz,server):
        env=server.ENV;at=datetime(2026,12,20,tzinfo=timezone.utc)
        for kind in ('purchase','topup'):
            await db.payment_transactions.insert_one({'_id':kind,'user_id':'owner','env':env,'kind':kind,
                'payment_status':'paid','currency':'usd','fulfilled_at':'2026-12-01T00:00:00Z','subscription_revenue_cents':1200})
            await db.events.insert_one({'_id':kind,'user_id':'owner','env':env,'type':'billing_financial_adjustment',
                'ts':'2026-12-03T00:00:00Z','data':{'kind':kind,'offset_cents':100}})
        expected=await membership_admin.summary(db,env,server.IN_ENV,at)
        archive=db.closed_membership_revenue;calls=[]
        class Archive:
            def find(self,*args,**kwargs):
                async def stream():
                    calls.append('archive-after-live')
                    await membership_admin.retain_closed_revenue(db,'owner',env)
                    await db.payment_transactions.delete_many({'user_id':'owner'})
                    await db.events.delete_many({'user_id':'owner'})
                    async for row in archive.find(*args,**kwargs):yield row
                return stream()
            async def count_documents(self,*args,**kwargs):return await archive.count_documents(*args,**kwargs)
        wrapped=SimpleNamespace(users=db.users,payment_transactions=db.payment_transactions,events=db.events,closed_membership_revenue=Archive())
        assert await membership_admin.summary(wrapped,env,server.IN_ENV,at)==expected
        assert calls==['archive-after-live']
        assert await membership_admin.summary(db,env,server.IN_ENV,at)==expected
    run_case(monkeypatch,scenario)


@pytest.mark.parametrize('name',[
    'test_monthly_cash_uses_paid_dates_and_survives_an_account_closure',
    'test_month_end_uses_the_same_utc_day_for_revenue_activity_and_closed_accounts',
])
def test_existing_membership_accounting_contracts_on_real_postgresql(monkeypatch,name):
    from backend.tests import test_membership_dashboard as contracts
    from backend.tests.test_postgres_store import exercise
    def run_postgres(callback):
        async def scenario(db,other):await callback(db)
        exercise(scenario)
    monkeypatch.setattr(contracts,'run_case',run_postgres)
    fn=getattr(contracts,name)
    if name=='test_monthly_cash_uses_paid_dates_and_survives_an_account_closure':fn(monkeypatch)
    else:fn()
