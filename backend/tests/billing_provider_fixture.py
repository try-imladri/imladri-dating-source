"""Explicit current Stripe payment/dispute read doubles for billing tests."""
from copy import deepcopy
import hashlib

import stripe


def install(monkeypatch, namespace, factory):
    invoices, charges, disputes = {}, {}, {}
    original = namespace[factory]
    def build(*args, **kwargs):
        value = original(*args, **kwargs)
        invoices[value["id"]] = value
        return value
    monkeypatch.setitem(namespace, factory, build)
    def identity(invoice):
        return hashlib.sha256(invoice["id"].encode()).hexdigest()[:24]
    def by_payment(payment):
        return next(v for v in invoices.values() if "pi_" + identity(v) == payment)
    def list_payments(*, invoice, status, limit):
        value = invoices[invoice]
        return {"data": [{"id": "inpay_" + identity(value), "invoice": invoice, "status": "paid",
            "amount_paid": value["amount_paid"], "payment": {"type": "payment_intent", "payment_intent": "pi_" + identity(value)}}],
            "has_more": False}
    def payment(payment_id, **kwargs):
        value = by_payment(payment_id)
        return {"id": payment_id, "latest_charge": "ch_" + identity(value)}
    def charge(charge_id, **kwargs):
        if charge_id in charges: return deepcopy(charges[charge_id])
        value = next(v for v in invoices.values() if "ch_" + identity(v) == charge_id)
        return {"id": charge_id, "payment_intent": "pi_" + identity(value), "amount": value["amount_paid"],
                "amount_refunded": 0, "paid": True, "currency": value["currency"], "captured": True, "amount_captured": value["amount_paid"]}
    monkeypatch.setattr(stripe.InvoicePayment, "list", list_payments)
    monkeypatch.setattr(stripe.PaymentIntent, "retrieve", payment)
    monkeypatch.setattr(stripe.Charge, "retrieve", charge)
    monkeypatch.setattr(stripe.Dispute, "list", lambda *, charge, limit: {"data": deepcopy(disputes.get(charge, [])), "has_more": False})
    return {"invoices": invoices, "charges": charges, "disputes": disputes}
