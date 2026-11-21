"""Prospective, content-free purchase terms; never a waiver of mandatory rights."""
import hashlib

VERSION = "imladri-purchases-2026-11-v1"
TEXT = (
    "I agree to the Terms and Billing policy. Monthly plans renew until canceled; "
    "included messages reset each billing month. Top-ups are paid once and need an active membership to use. "
    "Imladri does not offer change-of-mind refunds for delivered access or messages, including unused allowances. "
    "Mandatory consumer rights and Stripe/Link's applicable refund policy still apply."
)
HASH = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()


def snapshot(accepted_at, offer):
    # Authenticated app acknowledgment, not proof of payment or Stripe-hosted consent.
    return {"version": VERSION, "sha256": HASH, "text": TEXT,
            "accepted_at": accepted_at, "offer": offer, "source": "authenticated-plans-form"}


def checkout_text():
    return {"submit": {"message":
        "Review the [Billing and refund policy](https://imladri.com/billing). "
        "No change-of-mind refunds for delivered access or messages, including unused allowances. "
        "Mandatory consumer rights and Stripe/Link's applicable refund policy still apply."}}
