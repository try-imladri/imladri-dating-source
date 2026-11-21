import { useCallback, useEffect, useRef, useState } from "react";
import { errMsg } from "@/lib/api";

const SCOPE_LABELS = { new_ai: "New AI generations", new_intro: "New introductory access", new_purchase: "New purchases" };
const REASONS = { manual_payment_review: "Payment review", repeated_disputes: "Repeated disputes", suspected_identity_abuse: "Suspected identity abuse", resolved: "Review resolved" };

function RiskRow({ api, item, onChange }) {
  const [scopes, setScopes] = useState([]);
  const [reason, setReason] = useState("manual_payment_review");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(false);
  const inFlight = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const act = async action => {
    if (inFlight.current || busy || (action !== "dismiss" && !scopes.length)) return;
    inFlight.current = true;
    setBusy(true); setError("");
    try {
      await api.patch(`/admin/billing-risk/reviews/${encodeURIComponent(item.review_id)}`, {
        expected_member: item.member_id, revision: item.revision, request_id: crypto.randomUUID(),
        action, scopes: action === "dismiss" ? [] : scopes, reason: action === "hold" ? reason : "resolved",
      });
      if (mounted.current) onChange();
    } catch (e) {
      if (mounted.current) {
        setError(errMsg(e));
        onChange(); // Read back uncertain results; never automatically replay an action.
      }
    } finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  };
  return <article className="rounded-xl border border-[var(--line)] p-4 space-y-3" data-testid="billing-risk-row">
    <div className="flex flex-wrap justify-between gap-2">
      <p className="font-semibold break-all">{item.member_id}</p>
      <span className="text-xs text-[var(--muted)]">Revision {item.revision}</span>
    </div>
    <p className="text-sm">{item.hold_scopes?.length ? `Staff hold: ${item.hold_scopes.map(s => SCOPE_LABELS[s] || s).join(", ")}` : "No staff hold applied"}</p>
    <p className="text-xs text-[var(--muted)]">Review signals are not proof of fraud. Verified payment-specific holds are handled separately.</p>
    {item.counts && <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs">{Object.entries(item.counts).map(([key, value]) =>
      <div key={key}><dt className="inline text-[var(--muted)]">{key.replaceAll("_", " ")}: </dt><dd className="inline tabular-nums">{value}</dd></div>)}</dl>}
    <fieldset disabled={busy} className="space-y-2"><legend className="text-xs font-semibold mb-1">Select only the actions this review needs to hold or release</legend>
      <div className="flex flex-wrap gap-4">{Object.entries(SCOPE_LABELS).map(([key, label]) => <label key={key} className="text-xs flex gap-2 items-center">
        <input type="checkbox" checked={scopes.includes(key)} onChange={e => setScopes(old => e.target.checked ? [...old, key] : old.filter(s => s !== key))} />{label}
      </label>)}</div>
      <label className="block text-xs">Reason <select aria-label={`Review reason for ${item.member_id}`} value={reason} onChange={e => setReason(e.target.value)}
        className="ml-2 rounded-lg border border-[var(--line)] bg-[var(--bg)] p-2">{Object.entries(REASONS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <div className="flex flex-wrap gap-2">
        <button className="btn-ghost px-3 py-2 text-xs disabled:opacity-40" disabled={!scopes.length} onClick={() => act("hold")}>Apply selected hold</button>
        <button className="btn-ghost px-3 py-2 text-xs disabled:opacity-40" disabled={!scopes.length} onClick={() => act("release")}>Release selected hold</button>
        <button className="btn-ghost px-3 py-2 text-xs" onClick={() => act("dismiss")}>Mark reviewed</button>
      </div>
    </fieldset>
    <p className="text-xs text-[var(--muted)]">Marking reviewed does not remove a hold. These actions do not charge, refund, cancel a subscription, erase saved data or restrict cancellation.</p>
    {item.recent_actions?.length > 0 && <details className="text-xs"><summary className="cursor-pointer underline">Recent staff actions</summary>
      <ul className="mt-2 space-y-1">{item.recent_actions.map((a, i) => <li key={a.request_id || i}>{a.action} · {a.reason} · {a.at || a.created_at || "Recorded"}</li>)}</ul>
    </details>}
    {error && <p role="alert" className="text-sm">{error}</p>}
  </article>;
}

export default function BillingRiskReviews({ api }) {
  const [items, setItems] = useState([]);
  const [next, setNext] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const request = useRef(null);
  const load = useCallback(async (after = null) => {
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError("");
    try {
      const { data } = await api.get("/admin/billing-risk/reviews", { params: { limit: 25, ...(after ? { after } : {}) }, signal: controller.signal });
      if (!controller.signal.aborted) {
        setItems(old => after ? [...old, ...data.items] : data.items);
        setNext(data.next || null); setLoaded(true);
      }
    } catch (e) { if (!controller.signal.aborted) setError(errMsg(e)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }, [api]);
  useEffect(() => () => request.current?.abort(), []);
  return <details className="card p-4 space-y-3" onToggle={e => { if (e.currentTarget.open && !loaded && !busy) load(); }} data-testid="billing-risk-reviews">
    <summary className="cursor-pointer font-semibold">Billing risk & abuse review</summary>
    <p className="text-sm text-[var(--muted)]">Review payment disputes and repeated identity signals. A lawful refund request alone does not create an abuse hold.</p>
    <button onClick={() => load()} disabled={busy} className="underline text-xs disabled:opacity-40">Refresh reviews</button>
    {busy && <p role="status" className="text-sm">Loading billing reviews…</p>}
    {error && <p role="alert">{error}</p>}
    {loaded && !items.length && <p className="text-sm">No accounts currently need billing risk review.</p>}
    <div className="space-y-3">{items.map(item => <RiskRow key={`${item.review_id}:${item.revision}`} api={api} item={item} onChange={() => load()} />)}</div>
    {next && <button disabled={busy} onClick={() => load(next)} className="underline text-sm">Load more reviews</button>}
  </details>;
}
