"""Partner offer prices share the product and tax classification of Imladri Plus."""

import stripe

if __package__:
    from . import stripe_plan
else:
    import stripe_plan


LOOKUP_KEYS = {
    "partner_monthly": "imladri_partner_plus_100_monthly_usd10",
    "couple_annual": "imladri_couple_plus_100_annual_usd80",
}
OFFERS = {
    "partner_monthly": (1000, "month", "Invited partner monthly"),
    "couple_annual": (8000, "year", "Couples one-year offer"),
}


def matches_offer(price, offer):
    amount, interval, _ = OFFERS[offer]
    recurring = price.get("recurring") or {}
    return (
        price.get("lookup_key") == LOOKUP_KEYS[offer]
        and price.get("active") is True
        and price.get("currency") == "usd"
        and price.get("unit_amount") == amount
        and price.get("unit_amount_decimal") in (None, str(amount))
        and price.get("billing_scheme") == "per_unit"
        and not price.get("transform_quantity")
        and recurring.get("interval") == interval
        and recurring.get("interval_count") == 1
        and recurring.get("usage_type") == "licensed"
        and not recurring.get("trial_period_days")
    )


def _find_price(offer, base, rows):
    match = next((p for p in rows if p.get("lookup_key") == LOOKUP_KEYS[offer]), None)
    if match is None:
        return None
    if (not matches_offer(match, offer)
            or match.get("livemode") != base.get("livemode")
            or stripe_plan.resource_id(match.get("product")) != stripe_plan.resource_id(base.get("product"))
            or match.get("tax_behavior") != base.get("tax_behavior")):
        raise ValueError(f"The Stripe price configured for {offer} does not match the Imladri offer.")
    return match


def _base_price(rows):
    base = next((p for p in rows if p.get("lookup_key") == stripe_plan.LOOKUP_KEY), None)
    if not base or not base.get("active") or not stripe_plan.matches_offer(base) or not base.get("product"):
        raise ValueError("Configure the standard Imladri Plus price first.")
    return base


def catalog_price(offer):
    """Read both lookup keys in one request so a different product cannot grant this offer."""
    rows = stripe.Price.list(lookup_keys=[stripe_plan.LOOKUP_KEY, LOOKUP_KEYS[offer]], limit=10).data
    return _find_price(offer, _base_price(rows), rows)


def provision_catalog(*, live):
    """Create missing offers as prices on the existing product. Never change the default price."""
    rows = stripe.Price.list(lookup_keys=[stripe_plan.LOOKUP_KEY, *LOOKUP_KEYS.values()], limit=10).data
    base = _base_price(rows)
    product = stripe.Product.retrieve(stripe_plan.resource_id(base.get("product")))
    if base.get("livemode") is not live or product.get("livemode") is not live or not product.get("active"):
        raise ValueError("The Imladri Plus product must be active in the selected Stripe mode.")
    tax_code = stripe_plan.resource_id(product.get("tax_code"))
    if not live and not tax_code:
        product = stripe.Product.modify(product.id, tax_code=stripe_plan.LIVE_PRODUCT_TAX_CODE)
    if stripe_plan.resource_id(product.get("tax_code")) != stripe_plan.LIVE_PRODUCT_TAX_CODE:
        raise ValueError("Review the tax code on the Imladri Plus product before enabling Managed Payments.")
    prices = {offer: _find_price(offer, base, rows) for offer in OFFERS}
    for offer, (amount, interval, label) in OFFERS.items():
        if prices[offer] is None:
            args = {}
            if base.get("tax_behavior") in ("inclusive", "exclusive"):
                args["tax_behavior"] = base["tax_behavior"]
            price = stripe.Price.create(
                product=product.id,
                currency="usd",
                unit_amount=amount,
                recurring={"interval": interval, "interval_count": 1, "usage_type": "licensed"},
                lookup_key=LOOKUP_KEYS[offer],
                nickname=label,
                metadata={"imladri_offer": offer, "monthly_replies": "100"},
                idempotency_key=f"{LOOKUP_KEYS[offer]}-v1",
                **args,
            )
            prices[offer] = _find_price(offer, base, [price])
            if prices[offer] is None:
                raise ValueError(f"Stripe did not return the configured lookup key for {offer}.")
    return prices
