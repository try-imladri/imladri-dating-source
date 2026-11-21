import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { errMsg } from "@/lib/api";

const count = value => Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString("en-US") : "Unavailable";
const money = value => Number.isFinite(value) && value >= 0 ? (value / 1e9).toLocaleString("en-US", {
  style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 6,
}) : "Unavailable";

export default function IntroAccessAdmin({ api }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const active = useRef(true);
  const request = useRef(null);
  const submitting = useRef(false);
  const load = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    try {
      const { data } = await api.get("/admin/intro-access/summary", { signal: controller.signal });
      if (active.current && !controller.signal.aborted) { setReport(data); setError(""); }
    } catch (error) {
      if (active.current && !controller.signal.aborted) setError(errMsg(error));
    }
  }, [api]);
  useEffect(() => {
    active.current = true;
    load();
    return () => { active.current = false; request.current?.abort(); };
  }, [load]);
  async function changePause() {
    if (submitting.current || error || typeof report?.settings?.paused !== "boolean") return;
    submitting.current = true;
    setBusy(true); setNotice("");
    request.current?.abort();
    const paused = !report.settings.paused;
    try {
      const { data } = await api.patch("/admin/intro-access/settings", { paused });
      if (!active.current) return;
      setReport(data);
      setError("");
      setNotice(paused ? "Introductory grants and AI requests are paused. Paid memberships are unaffected." : "Introductory access is resumed for eligible accounts.");
    } catch (error) {
      if (!active.current) return;
      setError(errMsg(error));
      // The mutation may have reached the server. Reconcile with a GET before
      // allowing another change; never repeat a potentially successful PATCH.
      await load();
      if (active.current) setNotice("The change could not be confirmed. Review the refreshed status before trying again.");
    } finally {
      submitting.current = false;
      if (active.current) setBusy(false);
    }
  }
  const totals = report?.totals || {};
  const today = report?.today || {};
  const costs = report?.costs || {};
  const paused = report?.settings?.paused;
  return <section className="card p-4 sm:p-5 space-y-4" aria-labelledby="intro-admin-title" data-testid="intro-admin">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h2 id="intro-admin-title" className="font-display text-2xl font-bold uppercase">Introductory messages</h2>
        <p className="text-sm text-[var(--muted)] mt-1">One grant of 10 messages per eligible account, shared across Individual and Couples.</p></div>
      <button type="button" className="btn-ghost px-4 py-2 text-sm disabled:opacity-50" onClick={load} disabled={busy}>Refresh introduction data</button>
    </div>
    {!report && !error && <p role="status" className="text-sm">Loading introductory access…</p>}
    {error && <p role="alert" className="text-sm">{error}{report && " These figures were last loaded successfully. Refresh before changing access."}</p>}
    {report && <>
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        {[
          ["Granted accounts", "granted_accounts"], ["Active introductions", "active_accounts"],
          ["Completed introductions", "exhausted_accounts"], ["Introductory messages used", "used_messages"],
          ["Converted to paid", "converted_accounts"], ["Accounts flagged for review", "suspected_accounts"],
        ].map(([label, key]) => <div key={key} className="rounded-xl border border-[var(--line)] p-3">
          <p className="text-xs text-[var(--muted)]">{label}</p><p className="mt-1 text-xl font-semibold tabular-nums" data-testid={`intro-admin-${key}`}>{count(totals[key])}</p>
        </div>)}
      </div>
      <p className="text-xs text-[var(--muted)]">Account totals describe current retained member records. Deleted accounts are excluded; they are not a lifetime sign-up or conversion total.</p>
      {report.card_first && <section aria-label="Card-first billing states" className="space-y-2" data-testid="intro-admin-billing-states">
        <h3 className="font-semibold text-sm">Card-first billing states</h3>
        <p className="text-xs text-[var(--muted)]">Current retained, non-closing member agreements. A saved card or a zero-dollar setup invoice is separate from a verified paid conversion and subscription revenue.</p>
        <dl className="grid grid-cols-2 lg:grid-cols-4 gap-3 text-sm">{[
          ["checkout_pending", "Card setup pending"], ["active", "Free messages active"],
          ["billing_pending", "First payment pending"], ["requires_action", "Payment authentication required"],
          ["payment_failed", "Payment not confirmed"], ["review_required", "Billing review required"],
          ["cancelled", "Introduction canceled"], ["converted", "Paid conversion confirmed"],
        ].map(([state, label]) => <div key={state} className="rounded-xl border border-[var(--line)] p-3">
          <dt className="text-xs text-[var(--muted)]">{label}</dt><dd className="mt-1 font-semibold tabular-nums" data-testid={`intro-admin-billing-${state}`}>{count(report.card_first.states?.[state])}</dd>
        </div>)}</dl>
      </section>}
      {!!report.review_accounts?.length && <section className="space-y-2 text-sm" aria-label="Introductions flagged for review">
        <h3 className="font-semibold">Introductions flagged for review</h3>
        <p className="text-xs text-[var(--muted)]">These are review signals. People can legitimately share devices or a connection. Open the member to review their allowance or pause new replies.</p>
        <ul className="divide-y divide-[var(--line)]">{report.review_accounts.map(member => <li key={member.user_id} className="flex flex-wrap items-center justify-between gap-2 py-2">
          <span className="min-w-0 break-words"><strong>{member.nickname || member.user_id}</strong><span className="block text-xs text-[var(--muted)]">{member.reasons.join(" · ")}</span></span>
          <a className="underline" href={`/admin/users?q=${encodeURIComponent(member.user_id)}`}>Manage member</a>
        </li>)}</ul>
        {report.review_accounts_more && <p className="text-xs text-[var(--muted)]">Showing the first 20 flagged accounts. Member search remains available below.</p>}
      </section>}
      <p className="text-sm">{report.enabled ? "The introduction is enabled." : "The introduction is disabled by deployment configuration."} {paused === true ? "New grants and introductory AI requests are paused." : paused === false ? "Introductory grants and AI requests are not paused." : "Pause status is unavailable."} Account-level reply restrictions still apply.</p>
      <button type="button" onClick={changePause} disabled={busy || !!error || typeof paused !== "boolean"} className="btn-ghost px-4 py-2 text-sm disabled:opacity-50" data-testid="intro-admin-pause">
        {busy ? "Saving introductory access…" : paused ? "Resume introductory access" : "Pause introductory access"}
      </button>
      <div className="rounded-xl border border-[var(--line)] p-3 space-y-2 text-sm" data-testid="intro-admin-budget">
        <h3 className="font-semibold">Introductory AI budget · {today.day || "UTC day unavailable"}</h3>
        <p>Reserved spend: <strong>{money(today.spend_reserved_nusd)}</strong> / {money(today.spend_limit_nusd)} daily ceiling</p>
        <p>Reserved tokens: {count(today.tokens_reserved)} / {count(today.token_limit)} · Provider attempts: {count(today.attempts)}</p>
        <p className="text-xs text-[var(--muted)]">Reservations bound cost before a call and may include calls whose final charge is unknown. They are separate from customer message credits and provider statement totals.</p>
      </div>
      <div className="text-sm space-y-1" data-testid="intro-admin-costs">
        <h3 className="font-semibold">Introductory provider calls · {Number.isSafeInteger(costs.window_days) ? `last ${costs.window_days} days` : "reporting window unavailable"}</h3>
        <p>Estimated cost of priced introductory calls: {money(costs.low_nusd)}–{money(costs.high_nusd)}</p>
        <p>Recorded attempts: {count(costs.attempts)} · Calls with unknown cost: {count(costs.unknown)}</p>
        <p className="text-xs text-[var(--muted)]">Attempts include retries and linked background work. Estimates are not verified provider charges. <Link className="underline" to="/admin/prompts">Open AI costs and statement reconciliation</Link>.</p>
      </div>
      <p className="text-xs text-[var(--muted)]">Review a flagged member and use their reply-access action to pause that account. Introductory abuse-prevention records exclude questionnaire answers and conversation text.{Number.isSafeInteger(report.retention_days) && ` Their retention limit is ${report.retention_days} days.`}</p>
    </>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
  </section>;
}
