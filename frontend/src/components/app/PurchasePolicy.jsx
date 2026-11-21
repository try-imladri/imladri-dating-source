import { Link } from "react-router-dom";

export const PURCHASE_POLICY_VERSION = "imladri-purchases-2026-11-v1";
export const purchaseAcceptance = () => ({ purchase_policy_accepted: true, purchase_policy_version: PURCHASE_POLICY_VERSION });

export default function PurchasePolicy({ accepted, onChange }) {
  return <section className="card p-4 text-sm" aria-label="Purchase terms">
    <label className="flex items-start gap-3">
      <input type="checkbox" className="mt-1 shrink-0" checked={accepted} onChange={e => onChange(e.target.checked)} data-testid="purchase-policy-accept" />
      <span>I agree to the <Link className="underline" to="/terms">Terms</Link> and <Link className="underline" to="/billing">Billing policy</Link>. Monthly plans renew until canceled; included messages reset each billing month. Top-ups are paid once and need an active membership to use. Imladri does not offer change-of-mind refunds for delivered access or messages, including unused allowances. Mandatory consumer rights and Stripe/Link's applicable refund policy still apply.</span>
    </label>
  </section>;
}
