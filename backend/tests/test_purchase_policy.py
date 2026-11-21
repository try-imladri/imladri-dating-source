"""Purchase acknowledgment is prospective, authenticated and separate from privacy."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from backend import purchase_policy


def test_all_purchase_models_require_explicit_current_acceptance_without_blocking_cancellation():
    from backend import server
    for cls, fields in ((server.CheckoutIn, {"origin_url": "https://fixture.invalid"}),
                        (server.UpgradeIn, {"origin_url": "https://fixture.invalid", "offer": "plus_250"}),
                        (server.RefillIn, {"origin_url": "https://fixture.invalid"}),
                        (server.AnnualAcceptance, {"accept": True})):
        for accepted, version in ((False, None), (False, purchase_policy.VERSION), (True, "old")):
            with pytest.raises(HTTPException) as caught:
                cls(**fields, purchase_policy_accepted=accepted, purchase_policy_version=version).check_purchase()
            assert caught.value.status_code == 422
        with pytest.raises(ValidationError):
            cls(**fields, purchase_policy_accepted="true", purchase_policy_version=purchase_policy.VERSION)
        cls(**fields, purchase_policy_accepted=True, purchase_policy_version=purchase_policy.VERSION).check_purchase()
    # Portal and cancellation share the identity guard, not the purchase guard.
    import inspect
    assert "check_purchase" not in inspect.getsource(server.billing_portal)
    assert "check_purchase" not in inspect.getsource(server.cancel_partner_year)


def test_snapshot_is_content_free_and_does_not_claim_hosted_consent_or_payment():
    result = purchase_policy.snapshot("2026-11-12T00:00:00+00:00", "topup_100")
    assert set(result) == {"version", "sha256", "text", "accepted_at", "offer", "source"}
    assert result["sha256"] == hashlib.sha256(result["text"].encode()).hexdigest()
    assert "Mandatory consumer rights" in result["text"]
    assert "Stripe/Link" in result["text"] and "unused allowances" in result["text"]
    assert result["source"] == "authenticated-plans-form"


def test_purchase_routes_reject_missing_acceptance_before_any_provider_or_database_work(monkeypatch):
    from backend import server
    monkeypatch.setenv('PERSONAL_BILLING_MODE','enabled')
    monkeypatch.setattr(server.stripe,'api_key','sk_test_fixture')
    async def member():
        return {'user_id':'fixture-policy-member','role':'member','tier':'paid',
                'stripe_customer_id':'cus_fixture','stripe_subscription_id':'sub_fixture',
                'subscription_period_start':1,'subscription_period_end':4102444800}
    class NoDatabase:
        def __getattr__(self,name):pytest.fail('Unaccepted purchase reached database: '+name)
    monkeypatch.setattr(server,'db',NoDatabase())
    async def scenario():
        server.app.dependency_overrides[server.current_member]=member
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='https://fixture.invalid')as client:
            for path,fields in [('/api/payments/topup',{'origin_url':'https://fixture.invalid'}),
                                ('/api/payments/upgrade',{'origin_url':'https://fixture.invalid','offer':'plus_250'}),
                                ('/api/payments/partner-annual',{'accept':True})]:
                response=await client.post(path,json=fields)
                assert response.status_code==422,(path,response.text)
                assert 'Review and accept' in response.text
    try:asyncio.run(scenario())
    finally:server.app.dependency_overrides.clear()


def test_hosted_disclosure_preserves_provider_rights_without_claiming_hosted_consent():
    text=purchase_policy.checkout_text()
    assert set(text)=={'submit'}
    assert 'https://imladri.com/billing' in text['submit']['message']
    assert 'Mandatory consumer rights' in text['submit']['message']
    assert len(text['submit']['message'])<500


def test_managed_checkout_omits_unsupported_hosted_wording_without_mutating_standard_fallback(monkeypatch):
    from backend import server
    sent=[]
    monkeypatch.setattr(server.stripe.checkout.Session,'create',lambda **kw:sent.append(kw)or{'id':'fixture'})
    kw={'mode':'payment','custom_text':purchase_policy.checkout_text()}
    server.make_checkout_session(kw,'managed','fixture-managed')
    assert 'custom_text' not in sent[0] and sent[0]['managed_payments']=={'enabled':True}
    assert kw['custom_text']==purchase_policy.checkout_text()
    server.make_checkout_session(kw,'standard','fixture-standard')
    assert sent[1]['custom_text']==purchase_policy.checkout_text()
    assert sent[1]['automatic_tax']=={'enabled':True}


def test_closed_sales_do_not_block_existing_customer_billing_management(monkeypatch):
    from backend import server
    from starlette.requests import Request
    monkeypatch.setenv('PERSONAL_BILLING_MODE','disabled')
    monkeypatch.setenv('PUBLIC_APP_URL','https://fixture.invalid')
    monkeypatch.setattr(server.stripe,'api_key','sk_live_fixture')
    monkeypatch.setenv('STRIPE_PORTAL_CONFIGURATION','bpc_fixture')
    calls=[]
    monkeypatch.setattr(server.stripe.billing_portal.Configuration,'retrieve',lambda _: {'features':{'subscription_update':{'enabled':False}}})
    monkeypatch.setattr(server.stripe.billing_portal.Session,'create',lambda **kw:calls.append(kw)or SimpleNamespace(url='https://billing.stripe.test/fixture'))
    body=server.CheckoutIn(origin_url='https://fixture.invalid',expected_member='fixture-member')
    request=Request({'type':'http','scheme':'https','server':('fixture.invalid',443),'path':'/','headers':[]})
    result=asyncio.run(server.billing_portal(body,request,{'user_id':'fixture-member','stripe_customer_id':'cus_fixture'}))
    assert result['portal_url']=='https://billing.stripe.test/fixture'
    assert len(calls)==1 and calls[0]['customer']=='cus_fixture'
    assert calls[0]['configuration']=='bpc_fixture'
    assert body.purchase_policy_accepted is False


def test_all_admin_routes_reject_anonymous_and_ordinary_members(monkeypatch):
    from backend import server
    from fastapi.routing import APIRoute
    def has_admin_guard(dependency):
        return dependency.call is server.admin_dep or any(has_admin_guard(child) for child in dependency.dependencies)
    routes=[r for r in server.app.routes if isinstance(r, APIRoute) and
            (r.path.startswith('/api/admin/') or has_admin_guard(r.dependant))]
    assert len(routes) >= 60
    async def ordinary():
        return {"user_id": "fixture-ordinary", "role": "member", "env": server.ENV}
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="https://fixture.invalid") as client:
            for member in (None, ordinary):
                server.app.dependency_overrides.clear()
                if member: server.app.dependency_overrides[server.current_member]=member
                for route in routes:
                    path=route.path
                    for name in route.param_convertors: path=path.replace('{'+name+'}', '1')
                    for method in route.methods-{'HEAD','OPTIONS'}:
                        response=await client.request(method,path,json={} if method!='GET' else None)
                        # Retired study routes may intentionally answer410 before the guard.
                        assert response.status_code in ({403,410} if member else {401,410}), (method,path,response.status_code,response.text[:200])
                        assert response.headers.get('location') is None
    try: asyncio.run(scenario())
    finally: server.app.dependency_overrides.clear()
