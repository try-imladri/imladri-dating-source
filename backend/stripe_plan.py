"""Stripe catalog and invoice fields shared by checkout and sandbox setup."""

import os
from datetime import datetime, timezone

import stripe


LOOKUP_KEY = "imladri_plus_100_monthly_usd12"
PRICE_CENTS = 1200
PRICE_LABEL = "$12/month"
LIVE_PRODUCT_TAX_CODE = "txcd_10105001"


def checkout_mode():
    mode = os.getenv("STRIPE_CHECKOUT_MODE", "auto").strip().lower()
    if mode not in ("auto", "standard", "managed"):
        raise ValueError("STRIPE_CHECKOUT_MODE must be auto, standard or managed.")
    return mode


def matches_offer(price):
    recurring = price.get("recurring") or {}
    return (price.get("currency") == "usd" and price.get("unit_amount") == PRICE_CENTS
            and price.get("billing_scheme") == "per_unit" and recurring.get("interval") == "month"
            and recurring.get("interval_count") == 1 and recurring.get("usage_type") == "licensed"
            and not recurring.get("trial_period_days"))


def catalog_price():
    prices = stripe.Price.list(lookup_keys=[LOOKUP_KEY], active=True, limit=1).data
    if not prices:
        return None
    price = prices[0]
    if not matches_offer(price):
        raise ValueError("The plan price must be USD 12 for one licensed unit per month without a trial")
    return price


def provision_catalog_price(*, live=False):
    price = catalog_price()
    if not price:
        product = {"name": "Imladri Plus", "metadata": {"monthly_replies": "100"}}
        idempotency_key = "imladri-plus-monthly-12-v1"
        if live:
            product["tax_code"] = LIVE_PRODUCT_TAX_CODE
            idempotency_key = "imladri-plus-monthly-12-live-v2"
        price = stripe.Price.create(currency="usd", unit_amount=PRICE_CENTS, recurring={"interval": "month"},
            product_data=product, lookup_key=LOOKUP_KEY, idempotency_key=idempotency_key)
    if price.livemode != live:
        raise ValueError("The returned price belongs to a different Stripe mode.")
    return price.id


def resource_id(value):
    return value if isinstance(value, str) else (value or {}).get("id")


def invoice_subscription(invoice):
    details = (invoice.get("parent") or {}).get("subscription_details") or invoice.get("subscription_details") or {}
    return resource_id(invoice.get("subscription") or details.get("subscription"))


def invoice_paid_at(invoice):
    timestamp = ((invoice or {}).get("status_transitions") or {}).get("paid_at")
    if type(timestamp) is int and timestamp > 0:
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    return None


def line_revenue(line):
    credits = line.get("pretax_credit_amounts") or line.get("discount_amounts") or []
    discount = sum(credit["amount"] for credit in credits)
    for field in ("subtotal", "amount_excluding_tax"):
        if type(line.get(field)) is int:
            return line[field] - discount
    # Older payloads include inclusive tax in the quoted price and add exclusive tax separately.
    taxes = line.get("taxes") or line.get("tax_amounts") or []
    included = sum(tax["amount"] for tax in taxes if tax.get("tax_behavior") == "inclusive" or tax.get("inclusive"))
    return line["amount"] - discount - included


def paid_plan_charge(invoice, subscription_id, price_id):
    """Resolve the verified plan period and its after-discount revenue in cents, excluding sales tax."""
    if not price_id or invoice.get("status") != "paid" or invoice_subscription(invoice) != subscription_id:
        return None
    # Stripe also marks zero-dollar trial invoices as paid. Only a billed plan item grants access.
    if not isinstance(invoice.get("total"), int) or invoice["total"] <= 0:
        return None
    lines = invoice.get("lines") or {}
    rows = lines.get("data", [])
    if lines.get("has_more"):
        rows = stripe.Invoice.list_lines(invoice["id"], limit=100).auto_paging_iter()
    periods = set()
    net = 0
    only_plan = not (invoice.get("shipping_cost") or {}).get("amount_total")
    for line in rows:
        details = (line.get("parent") or {}).get("subscription_item_details") or {}
        pricing = (line.get("pricing") or {}).get("price_details") or {}
        if (type(line.get("amount")) is not int or resource_id(pricing.get("price") or line.get("price")) != price_id
                or not (details or line.get("type") == "subscription")):
            only_plan = False
            continue
        net += line_revenue(line)
        if line["amount"] <= 0 or details.get("proration") or line.get("proration"):
            continue
        period = line.get("period") or {}
        start, end = period.get("start"), period.get("end")
        if isinstance(start, int) and isinstance(end, int) and 0 < start < end:
            periods.add((start, end))
    if len(periods) != 1:
        return None
    if only_plan:
        # Stripe has already allocated every discount and rounded both inclusive and exclusive taxes.
        if type(invoice.get("total_excluding_tax")) is int:
            net = invoice["total_excluding_tax"]
        else:
            taxes = invoice.get("total_taxes") or invoice.get("total_tax_amounts")
            if taxes:
                net = invoice["total"] - sum(tax["amount"] for tax in taxes)
            elif type(invoice.get("tax")) is int:
                net = invoice["total"] - invoice["tax"]
    net = min(invoice["total"], max(0, net))
    paid = invoice.get("amount_paid")
    if type(paid) is int and 0 <= paid < invoice["total"]:
        # A partially settled invoice allocates the received fraction across the plan, tax and other lines.
        net = (net * paid + invoice["total"] // 2) // invoice["total"]
    return periods.pop(), net


def paid_period(invoice, subscription_id, price_id):
    charge = paid_plan_charge(invoice, subscription_id, price_id)
    return charge[0] if charge else None


def subscription_charge(subscription_id, expected_price=None):
    """Resolve a legacy member's actual Stripe item and latest paid invoice."""
    sub = stripe.Subscription.retrieve(subscription_id, expand=["latest_invoice"])
    items = (sub.get("items") or {}).get("data", [])
    if sub.get("status") not in ("active", "past_due") or len(items) != 1:
        return None
    item = items[0]
    price = item.get("price") or {}
    if isinstance(price, str):
        price = stripe.Price.retrieve(price)
    if item.get("quantity", 1) != 1 or (expected_price is None and not matches_offer(price)):
        return None
    canonical = expected_price or catalog_price()
    if not canonical or price.get("id") != canonical.id:
        return None
    price_id = canonical.id
    invoice = sub.get("latest_invoice")
    if isinstance(invoice, str):
        invoice = stripe.Invoice.retrieve(invoice)
    charge = paid_plan_charge(invoice or {}, subscription_id, price_id)
    if charge:
        period, revenue_cents = charge
        return price_id, period, resource_id(sub.get("customer")), revenue_cents, invoice_paid_at(invoice)


def subscription_receipt(subscription_id):
    charge = subscription_charge(subscription_id)
    return charge[:3] if charge else None
