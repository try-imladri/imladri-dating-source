"""Provision the Imladri Plus price in the configured Stripe sandbox."""

import os
import sys
from pathlib import Path

import stripe
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import stripe_plan


def setup():
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key.startswith(("sk_test_", "rk_test_")):
        raise ValueError("Set a Stripe sandbox secret or restricted test key in backend/.env")
    stripe.api_key = key
    return stripe_plan.provision_catalog_price()


if __name__ == "__main__":
    load_dotenv(ROOT / "backend" / ".env")
    try:
        print(f"Sandbox price ready: {setup()} ({stripe_plan.LOOKUP_KEY})")
    except ValueError as exc:
        sys.exit(str(exc))
    except stripe.StripeError as exc:
        # API exception text can contain sensitive configuration.
        sys.exit(f"Stripe could not load or create the sandbox price ({type(exc).__name__}).")
