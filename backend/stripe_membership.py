"""Verified public plan prices and the dedicated hosted plan-change configurations."""
import stripe

if __package__:
    from . import membership_plans, stripe_plan, billing_acceptance
else:
    import membership_plans, stripe_plan, billing_acceptance


PRODUCTS = {**membership_plans.PLANS, "topup_100": membership_plans.TOPUP}
EXTRA = ("plus_250", "plus_500", "topup_100")
ALL_KEYS = tuple(item["lookup_key"] for item in PRODUCTS.values())


def valid_price(price, item):
    recurring = price.get("recurring")
    if item["id"] == "topup_100":
        correct_interval = price.get("type") == "one_time" and not recurring
    else:
        recurring = recurring or {}
        correct_interval = (price.get("type") == "recurring" and recurring.get("interval") == "month"
                            and recurring.get("interval_count") == 1 and recurring.get("usage_type") == "licensed"
                            and not recurring.get("trial_period_days"))
    return (price.get("active") is True and price.get("currency") == "usd"
            and price.get("unit_amount") == item["price_cents"] and price.get("billing_scheme") == "per_unit"
            and price.get("unit_amount_decimal") in (None, str(item["price_cents"]))
            and not price.get("transform_quantity") and correct_interval and bool(price.get("product")))


def catalog():
    rows = stripe.Price.list(lookup_keys=list(ALL_KEYS), active=True, limit=10).data
    by_key = {p.get("lookup_key"): p for p in rows}
    values = {}
    for key, spec in PRODUCTS.items():
        price = by_key.get(spec["lookup_key"])
        if price and not valid_price(price, spec):
            raise ValueError(f"Invalid Stripe price for {key}.")
        values[key] = price
    return values


def confirm_features(price):
    return {
        "subscription_update": {"enabled": True, "default_allowed_updates": ["price"],
            "billing_cycle_anchor": "now", "proration_behavior": "always_invoice",
            "products": [{"product": stripe_plan.resource_id(price["product"]), "prices": [price["id"]]}]},
        "payment_method_update": {"enabled": True}, "invoice_history": {"enabled": False},
    }


def portal_config(config_id):
    return stripe.billing_portal.Configuration.retrieve(config_id, expand=["features.subscription_update.products"])


def valid_portal(config, price):
    feat = (config.get("features") or {}).get("subscription_update") or {}
    products = feat.get("products") or []
    return (config.get("active") is True and config.get("livemode") == price.get("livemode")
            and feat.get("enabled") and feat.get("billing_cycle_anchor") == "now"
            and feat.get("proration_behavior") == "always_invoice"
            and set(feat.get("default_allowed_updates") or []) == {"price"} and len(products) == 1
            and products[0].get("product") == stripe_plan.resource_id(price["product"])
            and list(products[0].get("prices") or []) == [price["id"]])


def provision(*, live):
    prices = catalog()
    base = prices["plus_100"]
    if not base or base.get("livemode") is not live:
        raise ValueError("Configure the existing $12 Plus price in the selected Stripe account first.")
    for key in EXTRA:
        spec = PRODUCTS[key]
        price = prices[key]
        if not price:
            kwargs = {"currency": "usd", "unit_amount": spec["price_cents"], "lookup_key": spec["lookup_key"],
                      "product_data": {"name": "Imladri " + spec["name"], "tax_code": stripe_plan.LIVE_PRODUCT_TAX_CODE},
                      "metadata": {"imladri_offer": key}, "idempotency_key": spec["lookup_key"] + "-v1"}
            if base.get("tax_behavior") in ("exclusive", "inclusive"):
                kwargs["tax_behavior"] = base["tax_behavior"]
            if key != "topup_100":
                kwargs["recurring"] = {"interval": "month"}
            price = stripe.Price.create(**kwargs)
        if not valid_price(price, spec) or price.get("livemode") is not live:
            raise ValueError("Unexpected Stripe price for " + key)
        product = stripe.Product.retrieve(stripe_plan.resource_id(price["product"]))
        if not product.get("active") or stripe_plan.resource_id(product.get("tax_code")) != stripe_plan.LIVE_PRODUCT_TAX_CODE:
            raise ValueError("Check the product and its tax category for " + key)
        if key != "topup_100":
            config_id = (price.get("metadata") or {}).get("upgrade_portal")
            if config_id:
                config = portal_config(config_id)
            else:
                config = stripe.billing_portal.Configuration.create(
                    features=confirm_features(price), default_return_url="https://imladri.com/app/settings/upgrade",
                    business_profile={"privacy_policy_url": "https://imladri.com/privacy",
                                      "terms_of_service_url": "https://imladri.com/terms"},
                    metadata={"imladri_upgrade_price": price["id"]},
                    idempotency_key="imladri-confirm-" + price["id"] + "-v2")
                config = portal_config(config["id"])
            if not valid_portal(config, price):
                raise ValueError("Unexpected Stripe upgrade confirmation settings for " + key)
            if not config_id:
                price = stripe.Price.modify(price["id"], metadata={"upgrade_portal": config["id"]})
        prices[key] = price
    return prices


def can_upgrade(member):
    agreement = member.get("annual_agreement") or {}
    return (member.get("tier") == "paid" and bool(member.get("stripe_customer_id"))
            and bool(member.get("stripe_subscription_id")) and member.get("subscription_status") in ("active", None)
            and member.get("plan_offer") != "couple_annual"
            and (agreement.get("subscription") != member.get("stripe_subscription_id")
                 or agreement.get("status") not in ("pending", "scheduled", "cancelled")))


def upgrade_session(member, target, origin):
    billing_acceptance.forbid_ordinary_purchase()
    price = catalog()[target]
    config_id = (price.get("metadata") or {}).get("upgrade_portal") if price else None
    if not config_id or not valid_portal(portal_config(config_id), price):
        raise ValueError("The plan-change configuration is unavailable.")
    sub = stripe.Subscription.retrieve(member["stripe_subscription_id"])
    items = (sub.get("items") or {}).get("data") or []
    if (sub.get("status") != "active" or sub.get("schedule") or sub.get("pending_update") or sub.get("cancel_at_period_end")
            or sub.get("cancel_at") or stripe_plan.resource_id(sub.get("customer")) != member["stripe_customer_id"]
            or len(items) != 1 or items[0].get("quantity") != 1
            or stripe_plan.resource_id(items[0].get("price")) != member.get("stripe_price_id")):
        raise ValueError("The current subscription is unavailable for a plan change.")
    back = origin + "/app/upgrade"
    session = stripe.billing_portal.Session.create(customer=member["stripe_customer_id"], configuration=config_id, return_url=back,
        flow_data={"type": "subscription_update_confirm", "subscription_update_confirm": {
            "subscription": sub["id"], "items": [{"id": items[0]["id"], "price": price["id"], "quantity": 1}]},
            "after_completion": {"type": "redirect", "redirect": {"return_url": back + "?upgrade=complete&plan=" + target}}})
    return session, price["id"]
