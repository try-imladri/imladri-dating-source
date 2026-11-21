import { useEffect, useState } from "react";

const money = (cents, currency) => {
  if (!Number.isSafeInteger(cents)) return "Not recorded";
  if (typeof currency !== "string" || !/^[a-z]{3}$/i.test(currency)) return `${(cents / 100).toFixed(2)} (currency not recorded)`;
  try { return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(cents / 100); }
  catch { return `${(cents / 100).toFixed(2)} (currency unavailable)`; }
};

export default function BillingRecords({ api, userId }) {
  const [open, setOpen] = useState(false);
  const [limit, setLimit] = useState(25);
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (!open) return;
    const request = new AbortController();
    setData(null); setError(false);
    api.get(`/users/${encodeURIComponent(userId)}/billing-records`, {params: {limit}, signal: request.signal})
      .then(({data: next}) => {if (!request.signal.aborted) setData(next);})
      .catch(() => {if (!request.signal.aborted) setError(true);});
    return () => request.abort();
  }, [api, userId, open, limit, attempt]);
  return <div className="mt-2 text-xs font-normal">
    <button type="button" className="underline" aria-expanded={open} onClick={() => setOpen(v => !v)}>Billing records</button>
    {open && <section className="mt-2 max-h-96 space-y-3 overflow-auto rounded-lg border border-[var(--line)] p-3" aria-label="Member billing records">
      {error ? <p role="alert">Billing records couldn’t load. <button className="underline" onClick={() => setAttempt(v => v + 1)}>Retry</button></p>
        : !data ? <p role="status">Loading billing records…</p>
        : <>
          <p>Payment and credit evidence only. Private conversations and questionnaire answers are excluded. Net revenue is before provider fees.</p>
          {data.pending_reviews?.length > 0 && <div role="status"><strong>Payment review needed</strong>
            <ul>{data.pending_reviews.map(row => <li key={row.id}>{row.reason} · {row.opened_at}</li>)}</ul>
          </div>}
          {!data.items?.length ? <p>No recorded purchases.</p> : <ol className="space-y-4">{data.items.map(row => <li key={row.id} className="border-t border-[var(--line)] pt-2">
            <p className="font-semibold">{row.kind} · {row.payment_status || row.status}</p>
            <p className="break-all">{row.id}</p><p>{row.created_at}</p>
            <dl className="mt-1 space-y-1">
              <div><dt className="inline">Original payment: </dt><dd className="inline">{money(row.original_amount_cents,row.currency)}</dd></div>
              <div><dt className="inline">Original revenue: </dt><dd className="inline">{money(row.original_revenue_cents,row.currency)}</dd></div>
              <div><dt className="inline">Refund / lost-dispute offset: </dt><dd className="inline">{money(row.refund_dispute_offset_cents,row.currency)}</dd></div>
              <div><dt className="inline">Net revenue: </dt><dd className="inline">{money(row.net_revenue_cents,row.currency)}</dd></div>
              {Number.isInteger(row.net_credits) && <div><dt className="inline">Remaining granted entitlement: </dt><dd className="inline">{row.net_credits} messages before usage</dd></div>}
            </dl>
            <p className="mt-1">Adjustment: {row.adjustment_status === "verified" ? "Verified with Stripe" : row.adjustment_status === "review_required" ? "Review required" : "No verified adjustment recorded"}</p>
            <p>{row.fulfilled_at ? `Server fulfillment recorded ${row.fulfilled_at}` : "Server fulfillment time not recorded"}</p>
            {row.policy_recorded && row.purchase_policy ? <details className="mt-1"><summary className="cursor-pointer underline">Purchase acknowledgment · {row.purchase_policy.version}</summary>
              <p>{row.purchase_policy.accepted_at}</p><p>{row.purchase_policy.text}</p>
              <p className="break-all">Wording SHA-256: {row.purchase_policy.sha256}</p>
              <p>Recorded in the authenticated app. This does not assert Stripe-hosted consent or waive mandatory consumer rights.</p>
            </details> : <p>Purchase acknowledgment not recorded for this transaction. No retrospective acceptance is assumed.</p>}
          </li>)}</ol>}
          {data.truncated && <p>Showing the latest {limit} records. {limit < 50 && <button className="underline" onClick={() => setLimit(50)}>Load latest 50</button>}</p>}
        </>}
    </section>}
  </div>;
}
