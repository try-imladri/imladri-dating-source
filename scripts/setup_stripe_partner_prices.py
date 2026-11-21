"""Inspect partner offer prices or explicitly provision them in a named Stripe account."""

import argparse
import os
import sys
from pathlib import Path

import stripe

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import stripe_partner_prices


def setup(account, *, live, create=False):
    mode = "live" if live else "test"
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key.startswith((f"sk_{mode}_", f"rk_{mode}_")):
        raise ValueError(f"Set a {mode} STRIPE_SECRET_KEY for the selected account.")
    stripe.api_key = key
    if stripe.Account.retrieve().id != account:
        raise ValueError("The key belongs to a different Stripe account.")
    if create:
        return stripe_partner_prices.provision_catalog(live=live)
    prices = {offer: stripe_partner_prices.catalog_price(offer) for offer in stripe_partner_prices.OFFERS}
    if any(price is None or price.livemode != live for price in prices.values()):
        raise ValueError("Partner prices missing for the selected mode. Use --create to provision.")
    return prices


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--mode", choices=["test", "live"], required=True)
    parser.add_argument("--create", action="store_true")
    args = parser.parse_args()
    try:
        prices = setup(args.account, live=args.mode == "live", create=args.create)
    except ValueError as exc:
        sys.exit(str(exc))
    except stripe.StripeError as exc:
        sys.exit(f"Stripe catalog request failed ({type(exc).__name__}).")
    for offer, price in prices.items():
        print(f"{offer}: {price.id} ({args.mode})")
