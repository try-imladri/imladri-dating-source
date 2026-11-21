import { useState } from "react";
import { errMsg } from "@/lib/api";

const money = cents => Number.isFinite(cents) ? new Intl.NumberFormat("en-US", {style:"currency", currency:"USD"}).format(cents / 100) : "Pending reconciliation";
const plans = {plus_100:"100 replies", plus_250:"250 replies", plus_500:"500 replies", partner_monthly:"Partner monthly", couple_annual:"Prepaid year"};

export default function MembershipReconciliation({ api }) {
  const today = new Date().toISOString().slice(0,7);
  const [month,setMonth] = useState(today);
  const [report,setReport] = useState(null);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState("");
  async function reconcile() {
    setBusy(true); setError(""); setReport(null);
    try { const {data} = await api.get("/admin/membership/reconciliation", {params:{month}}); setReport(data); }
    catch(e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  }
  return <section className="card p-4 space-y-3">
    <h2 className="font-display font-bold text-xl uppercase">Membership and Stripe reconciliation</h2>
    <div className="filter-grid"><label className="filter-field text-sm"><span>UTC month</span><input className="filter-control rounded-xl border border-[var(--line)] bg-[var(--bg)] px-3 py-2" type="month" max={today} value={month} disabled={busy} onChange={e=>{setMonth(e.target.value);setReport(null);}} /></label>
      <button className="btn-ghost h-10 px-3 text-sm" disabled={busy || !month || month>today} onClick={reconcile}>{busy?"Reading accounts and Stripe…":"Reconcile month"}</button></div>
    {error && <p role="alert">{error}</p>}
    {report && <div className="space-y-3 text-sm">
      <p>{report.environment === "live" ? "Live" : "Sandbox"} · As of {new Date(report.as_of).toUTCString()}</p>
      <p><strong>Current access</strong> · {report.entitlements.paid} paid Individual accounts · {report.entitlements.sponsored_only} sponsored Couples-only seats. {Object.entries(report.entitlements.by_plan).map(([plan,n]) => (plans[plan] || plan) + " " + n).join(" · ")}</p>
      <p><strong>App receipts for {month}</strong> · {money(report.subscriptions)} net subscriptions · {money(report.refills)} net refills. USD after discounts and verified refund or lost-dispute adjustments, before fees. Original paid amounts remain recorded; adjustments use their reconciliation date. Older subscription receipts can include tax.</p>
      {report.net_revenue_verified === false && <p role="status">Some Personal payment adjustments need review. Net app revenue is unverified for this month.</p>}
      {report.revenue_breakdown && <dl>{Object.entries(report.revenue_breakdown).map(([kind, row]) => <div key={kind}><dt className="capitalize">{kind}</dt><dd>{money(row.gross_cents)} original gross · {money(row.net_cents)} net</dd></div>)}</dl>}
      <h3 className="font-semibold">Stripe account balance activity</h3>
      {report.stripe.state === "complete" ? <>
        <p>USD entries created during the month, including other products, tax, adjustments and payouts in this Stripe account. Refunds and standalone fees have their own signed rows. The fee column uses Stripe’s fee field. Net includes the fee on that entry.</p>
        {Object.keys(report.stripe.categories).length ? <div className="overflow-auto"><table className="text-left w-full"><thead><tr>{["Category","Entries","Amount","Fee","Net"].map(h=><th key={h}>{h}</th>)}</tr></thead>
          <tbody>{Object.entries(report.stripe.categories).sort(([a],[b])=>a.localeCompare(b)).map(([key,v])=><tr key={key}><th scope="row">{key.replaceAll("_"," ")}</th><td>{v.rows}</td><td>{money(v.amount)}</td><td>{money(v.fee)}</td><td>{money(v.net)}</td></tr>)}</tbody></table></div> : <p>No USD entries in this Stripe period.</p>}
        {report.stripe.other_currency>0 && <p>{report.stripe.other_currency} entries in other currencies excluded.</p>}
        {report.stripe.invoice_link_status === "partial" && <p role="status">Some historical invoice payments still need matching. Run this report again to continue.</p>}
        <h4 className="font-semibold">Matched to app receipts</h4>
        {report.stripe.totals_by_product && <dl className="grid gap-2 sm:grid-cols-3">
          {Object.entries(report.stripe.totals_by_product).map(([product, total]) => <div className="rounded-xl border border-[var(--line)] p-3" key={product}>
            <dt className="capitalize">{product} · net balance activity</dt>
            <dd className="font-semibold">{money(total.net)}</dd><dd>{total.rows} entries · {money(total.amount)} signed amount · {money(total.fee)} attached fees</dd>
          </div>)}
        </dl>}
        <p>Matches use exact Stripe charge, payment or invoice IDs. Unlinked entries include payouts, standalone fees and older receipts with missing identifiers.</p>
        <div className="overflow-auto"><table className="text-left w-full"><thead><tr>{["Product","Category","Entries","Amount","Fee","Net"].map(h=><th key={h}>{h}</th>)}</tr></thead>
          <tbody>{Object.entries(report.stripe.products).flatMap(([product,items])=>Object.entries(items).map(([key,v])=><tr key={product+key}><th scope="row">{product}</th><td>{key.replaceAll("_"," ")}</td><td>{v.rows}</td><td>{money(v.amount)}</td><td>{money(v.fee)}</td><td>{money(v.net)}</td></tr>))}</tbody></table></div>
      </> : <p role="status">{report.stripe.state==="over_limit" ? "This month exceeds 1,000 entries. Use Stripe’s balance report for the complete month." : "The Stripe balance report is unavailable. Fees and refunds are unknown for this period."}</p>}
    </div>}
  </section>;
}
