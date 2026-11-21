import { useEffect, useState } from "react";

const labels = {
  deepseek: "DeepSeek balance",
  ai_budget: "Daily AI allowance",
  stripe: "Stripe configuration and price",
  stripe_invoices: "Paid invoice reconciliation",
  partner_checkouts: "Invited-partner checkout review",
};
const utc = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" });

export default function OperationsStatus({ api }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    async function load() {
      try {
        const { data: status } = await api.get("/admin/operations", { signal: controller.signal });
        if (!controller.signal.aborted) { setData(status); setError(false); }
      } catch { if (!controller.signal.aborted) setError(true); }
      finally { if (!controller.signal.aborted) timer = setTimeout(load, 60000); }
    }
    load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, attempt]);
  return <section className="card max-w-3xl space-y-4 p-4 sm:p-5 overflow-y-auto" aria-label="Service status">
    <div className="flex items-center justify-between gap-3">
      <h2 className="font-display font-black uppercase text-xl">Service status</h2>
      <button type="button" className="btn-ghost px-3 py-1.5 text-sm" onClick={() => setAttempt(n => n + 1)}>Refresh status</button>
    </div>
    {error && <p role="alert" className="text-sm">Couldn’t refresh the service status.</p>}
    {!data && !error && <p role="status">Loading service status…</p>}
    {data && <>
      <p className="text-sm"><strong>New subscription checkout</strong> · {data.billing_enabled ? "Enabled in this deployment" : "Paused in this deployment"}</p>
      {data.checked_at ? <>
        <p className="text-sm text-[var(--muted)]">Last completed check · {utc.format(new Date(data.checked_at))} UTC</p>
        {data.stale && <p role="status" className="text-sm font-semibold">Check overdue. These results are from the last completed check.</p>}
        <dl className="divide-y divide-[var(--line)] text-sm">
          {Object.entries(labels).map(([key, label]) => <div key={key} className="flex flex-wrap justify-between gap-3 py-2.5">
            <dt>{label}</dt><dd className="font-semibold">{data.checks[key] === true ? "Passed" : data.checks[key] === false ? "Needs attention" : "Not checked"}</dd>
          </div>)}
        </dl>
      </> : <p role="status" className="text-sm">Waiting for the first completed service check.</p>}
      <dl className="divide-y divide-[var(--line)] text-sm">
        <div className="flex flex-wrap justify-between gap-3 py-2"><dt>Clerk member sign-in</dt><dd>No sign-in probe in this report</dd></div>
        <div className="flex flex-wrap justify-between gap-3 py-2"><dt>Transactional mail delivery</dt><dd>No inbox delivery probe in this report</dd></div>
      </dl>
      <p className="text-xs text-[var(--muted)]">Production checks run every 15 minutes. The Stripe catalog and paid invoices are checked when billing is enabled.</p>
    </>}
  </section>;
}
