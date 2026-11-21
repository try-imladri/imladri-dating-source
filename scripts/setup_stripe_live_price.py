"""Verify the live Plus catalog and optionally its release configuration on a named account."""
import argparse
import os
import re
import sys
from pathlib import Path

import stripe

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import stripe_plan


WEBHOOK_URL = "https://imladri.com/api/stripe/webhook"
WEBHOOK_EVENTS = {
    "checkout.session.completed", "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed", "checkout.session.expired",
    "invoice.paid", "customer.subscription.updated", "customer.subscription.deleted", "charge.refunded", "charge.dispute.closed",
}


def verify_release(account, price):
    if not account.get("charges_enabled"):
        raise ValueError("Stripe has not enabled live charges on this account.")
    product = stripe.Product.retrieve(stripe_plan.resource_id(price.get("product")))
    if not product.get("active") or not product.get("livemode"):
        raise ValueError("The Plus price must belong to an active live product.")
    tax_code = stripe_plan.resource_id(product.get("tax_code"))
    if tax_code != stripe_plan.LIVE_PRODUCT_TAX_CODE:
        raise ValueError(f"The live Imladri Plus product tax code is {tax_code or 'unset'}. "
                         f"Review the product in Stripe and set {stripe_plan.LIVE_PRODUCT_TAX_CODE} for this release.")
    portal_id = os.environ.get("STRIPE_PORTAL_CONFIGURATION", "")
    if not re.fullmatch(r"bpc_[A-Za-z0-9]+", portal_id):
        raise ValueError("Provide the live STRIPE_PORTAL_CONFIGURATION.")
    portal = stripe.billing_portal.Configuration.retrieve(portal_id)
    features = portal.get("features") or {}
    if (features.get("subscription_update") or {}).get("enabled"):
        raise ValueError("Turn off price switching in the live portal before making invite-only prices available.")
    required = ("subscription_cancel", "invoice_history", "payment_method_update")
    if (not portal.get("active") or not portal.get("livemode")
            or not all((features.get(name) or {}).get("enabled") for name in required)):
        raise ValueError("The active live portal needs cancellation, invoice history and payment method updates.")
    hooks = [endpoint for endpoint in stripe.WebhookEndpoint.list(limit=100).auto_paging_iter()
             if endpoint.get("url") == WEBHOOK_URL and endpoint.get("livemode") and endpoint.get("status") == "enabled"]
    if len(hooks) != 1:
        raise ValueError(f"Expected one enabled live webhook for {WEBHOOK_URL}.")
    selected = set(hooks[0].get("enabled_events") or [])
    if "*" not in selected and not WEBHOOK_EVENTS <= selected:
        raise ValueError("Missing live webhook events: " + ", ".join(sorted(WEBHOOK_EVENTS - selected)))
    if not os.environ.get("STRIPE_WEBHOOK_SECRET", "").startswith("whsec_"):
        raise ValueError("Provide the signing secret for the live apex Stripe endpoint.")


def setup(account, *, create=False, check_release=False):
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not re.fullmatch(r"acct_[A-Za-z0-9]+", account) or not key.startswith(("sk_live_", "rk_live_")):
        raise ValueError("Provide a live Stripe key and the expected account ID.")
    stripe.api_key = key
    stripe_account = stripe.Account.retrieve()
    if stripe_account.id != account:
        raise ValueError("The Stripe key belongs to a different account.")
    if create:
        return stripe_plan.provision_catalog_price(live=True)
    price = stripe_plan.catalog_price()
    if not price or not price.livemode:
        raise ValueError("No matching live price. Use --create-live to provision the catalog.")
    if check_release:
        verify_release(stripe_account, price)
    return price.id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True, help="Expected Stripe account ID")
    options = parser.add_mutually_exclusive_group()
    options.add_argument("--create-live", action="store_true", help="Create the $12 live monthly price if missing")
    options.add_argument("--verify-release", action="store_true",
                         help="Read live charge capability, product, portal and webhook settings")
    args = parser.parse_args()
    try:
        price_id = setup(args.account, create=args.create_live, check_release=args.verify_release)
        print(f"Verified live Plus price {price_id}")
        if args.verify_release:
            print("Verified charge capability, product, portal and webhook configuration. "
                  "Signed delivery and customer-facing receipt and invoice review are separate checks.")
    except ValueError as exc:
        sys.exit(str(exc))
    except stripe.StripeError as exc:
        sys.exit(f"Stripe catalog request failed ({type(exc).__name__}).")
