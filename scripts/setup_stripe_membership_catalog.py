"""Verify or provision Imladri's standard tiers, refill and hosted upgrade confirmations."""
import argparse
import os
import sys
from pathlib import Path

import stripe

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import stripe_membership


def setup(account, *, live, create=False):
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    mode = "live" if live else "test"
    if not account or not key.startswith((f"sk_{mode}_", f"rk_{mode}_")):
        raise ValueError(f"An explicit account and a {mode} Stripe key are required.")
    stripe.api_key = key
    if stripe.Account.retrieve().id != account:
        raise ValueError("The Stripe account does not match.")
    prices = stripe_membership.provision(live=live) if create else stripe_membership.catalog()
    for name, price in prices.items():
        if not price or price.get("livemode") is not live:
            raise ValueError(f"Missing Stripe price for {name}.")
        if name in ("plus_250", "plus_500"):
            config = (price.get("metadata") or {}).get("upgrade_portal")
            if not config or not stripe_membership.valid_portal(stripe_membership.portal_config(config), price):
                raise ValueError(f"Missing upgrade portal for {name}.")
    return prices


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--mode", required=True, choices=["test", "live"])
    parser.add_argument("--create", action="store_true")
    args = parser.parse_args()
    try:
        result = setup(args.account, live=args.mode == "live", create=args.create)
    except (ValueError, stripe.StripeError) as e:
        sys.exit(str(e) if isinstance(e, ValueError) else f"Stripe request failed: {e.user_message or type(e).__name__}")
    for name, price in result.items():
        print(name, price["id"], args.mode)
