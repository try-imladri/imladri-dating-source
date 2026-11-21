"""Partner prices must use the existing Plus product, currency, units and tax configuration."""

from types import SimpleNamespace

import pytest
import stripe

from backend import stripe_partner_prices as partner
from backend import stripe_plan
from scripts.setup_stripe_partner_prices import setup


def make_price(lookup, amount, interval, **overrides):
    fields = {
        "id": "price_" + lookup,
        "lookup_key": lookup,
        "product": "prod_qa",
        "active": True,
        "livemode": True,
        "currency": "usd",
        "unit_amount": amount,
        "unit_amount_decimal": str(amount),
        "recurring": {"interval": interval, "interval_count": 1, "usage_type": "licensed"},
        "billing_scheme": "per_unit",
        "tax_behavior": "inclusive",
    }
    return stripe.StripeObject.construct_from({**fields, **overrides}, None)


def base(**changes):
    return make_price(stripe_plan.LOOKUP_KEY, 1200, "month", **changes)


@pytest.mark.parametrize("offer", partner.OFFERS)
def test_each_catalog_price_reuses_plus_product(monkeypatch, offer):
    amount, interval, _ = partner.OFFERS[offer]
    price = make_price(partner.LOOKUP_KEYS[offer], amount, interval)
    rows = [base(), price]

    def listed(*, lookup_keys, limit):
        assert set(lookup_keys) == {stripe_plan.LOOKUP_KEY, partner.LOOKUP_KEYS[offer]}
        return SimpleNamespace(data=rows)

    monkeypatch.setattr(stripe.Price, "list", listed)
    assert partner.catalog_price(offer).id == price.id
    rows.pop()
    assert partner.catalog_price(offer) is None


@pytest.mark.parametrize("change", [
    {"product": "prod_external"}, {"active": False}, {"currency": "eur"}, {"unit_amount": 999},
    {"unit_amount_decimal": "1000.5"}, {"tax_behavior": "exclusive"}, {"livemode": False},
    {"transform_quantity": {"divide_by": 100}}, {"billing_scheme": "tiered"},
    {"recurring": {"interval": "month", "interval_count": 1, "usage_type": "licensed", "trial_period_days": 5}},
    {"recurring": {"interval": "month", "interval_count": 1, "usage_type": "metered"}},
    {"recurring": {"interval": "month", "interval_count": 2, "usage_type": "licensed"}},
])
def test_misconfigured_partner_price_fails_closed(monkeypatch, change):
    price = make_price(partner.LOOKUP_KEYS["partner_monthly"], 1000, "month", **change)
    monkeypatch.setattr(stripe.Price, "list", lambda **_: SimpleNamespace(data=[base(), price]))
    with pytest.raises(ValueError, match="does not match"):
        partner.catalog_price("partner_monthly")


def test_provision_validates_tax_and_reuses_existing_prices(monkeypatch):
    rows = [base()]
    product = stripe.StripeObject.construct_from(
        {"id": "prod_qa", "active": True, "livemode": True, "tax_code": "wrong"}, None,
    )
    monkeypatch.setattr(stripe.Price, "list", lambda **_: SimpleNamespace(data=list(rows)))
    monkeypatch.setattr(stripe.Product, "retrieve", lambda _id: product)

    def create(**params):
        assert params["product"] == product.id and params["tax_behavior"] == "inclusive"
        assert params["metadata"]["monthly_replies"] == "100"
        assert params["idempotency_key"] == params["lookup_key"] + "-v1"
        price = make_price(params["lookup_key"], params["unit_amount"], params["recurring"]["interval"])
        rows.append(price)
        return price

    monkeypatch.setattr(stripe.Price, "create", create)
    with pytest.raises(ValueError, match="tax code"):
        partner.provision_catalog(live=True)
    assert len(rows) == 1
    product.tax_code = stripe_plan.LIVE_PRODUCT_TAX_CODE
    first = partner.provision_catalog(live=True)
    second = partner.provision_catalog(live=True)
    assert first == second and len(rows) == 3
    with pytest.raises(ValueError, match="selected Stripe mode"):
        partner.provision_catalog(live=False)


def test_setup_rejects_wrong_account_and_wrong_mode_before_mutating(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fixture")
    with pytest.raises(ValueError, match="live"):
        setup("acct_expected", live=True, create=True)
    monkeypatch.setattr(stripe, "api_key", None)
    monkeypatch.setattr(stripe.Account, "retrieve", lambda: SimpleNamespace(id="acct_other"))
    with pytest.raises(ValueError, match="different Stripe account"):
        setup("acct_expected", live=False, create=True)
