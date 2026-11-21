import { useEffect, useState } from "react";
import { errMsg } from "@/lib/api";
import MessageCostReceipts from "./MessageCostReceipts";

const endpoint = "/admin/prompts/message-costs";
const number = value => value.toLocaleString("en-US");
const usd = value => value == null ? "—" : value.toLocaleString("en-US", {
  style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 6,
});
const estimate = pair => pair ? pair.map(usd).join(" – ") : "—";
const field = "rounded-xl border border-[var(--line)] bg-[var(--bg)] px-3 py-2";
const statuses = {matched:"Matched", mixed_rates:"Export hourly rates", mismatch:"Counts or timing differ", missing:"Awaiting export"};
const workLabels = {settled:"Settled messages", refunded:"Refunded messages", pending:"Pending messages",
  internal_qa:"Internal QA", unassigned:"Unassigned messages", unclaimed_upload:"Unused preparations", unlinked:"Other work"};

function CostSummary({ report }) {
  const all = report.cohorts.all;
  const dayRows = report.coverage.filter(d => d.calls || d.provider_requests != null);
  return <>
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
      {[
        ["Settled messages", number(all.messages)],
        ["Billed cost per 100", usd(all.actual_per_100_usd)],
        ["Estimated cost per 100", estimate(all.estimated_per_100_usd)],
      ].map(([label,value]) => <div className="rounded-xl border border-[var(--line)] p-3" key={label}>
        <div className="text-sm">{label}</div><div className="mt-1 text-lg font-semibold">{value}</div>
      </div>)}
    </div>
    <p className="text-sm">Each message reserved on the current key counts once on its settlement date. Its costs include linked uploads or starters, retries and background work, even when those calls finish on a different UTC day.</p>
    <div className="overflow-x-auto"><table className="w-full text-left text-sm">
      <caption className="sr-only">DeepSeek costs by message workspace</caption>
      <thead><tr>{["Workspace", "Messages", "Calls", "Provider repair retries", "Calls on member retries", "Background", "Preparations", "Statement-matched calls", "Billed / 100", "Estimated / 100"].map(h=><th scope="col" key={h} className="pr-4">{h}</th>)}</tr></thead>
      <tbody>{[["individual","Individual"],["couples","Couples"]].map(([key,label])=>{
        const row = report.cohorts[key];
        return <tr key={key}><th scope="row" className="py-2 pr-4">{label}</th>
          {[row.messages,row.calls,row.retries,row.customer_retry_calls ?? 0,row.background,row.preparation].map((n,i)=><td key={i} className="pr-4">{number(n)}</td>)}
          <td className="pr-4">{number(row.reconciled_calls)} / {number(row.calls)}</td>
          <td className="pr-4">{usd(row.actual_per_100_usd)}</td><td>{estimate(row.estimated_per_100_usd)}</td></tr>;
      })}</tbody></table></div>
    {all.messages === 0 ? <p className="text-sm text-[var(--muted)]">No settled message credits in this period.</p>
      : all.actual_per_100_usd == null && <p className="text-sm text-[var(--muted)]">Billed cost per 100 appears once every linked call matches the provider statement and background work is complete. Open background tasks · {all.pending_background}. Credits with no recorded foreground call · {all.credits_without_calls}.</p>}
    <details className="rounded-xl border border-[var(--line)] p-3 text-sm" open>
      <summary className="cursor-pointer font-semibold">Production key reconciliation</summary>
      <p className="my-2">All work on the key is grouped by the UTC day DeepSeek billed it. Charges include any granted balance. A day outside the selected dates can appear when a linked call crossed midnight.</p>
      <p className="my-2"><strong>Whole-key spend per 100 settled credits · {usd(report.keywide_per_100_usd)}</strong><br/>Includes internal QA, failed messages, unused preparations and other work billed in the selected dates. Requires statements for the entire period.</p>
      {dayRows.length ? <div className="overflow-x-auto"><table className="w-full text-left">
        <thead><tr>{["UTC day","App calls","Provider requests","Provider charge","Coverage","Work billed that day"].map(h=><th scope="col" key={h} className="pr-4">{h}</th>)}</tr></thead>
        <tbody>{dayRows.map(day=><tr key={day.day}><th scope="row" className="py-2 pr-4 whitespace-nowrap">{day.day}</th>
          <td>{number(day.calls)}</td><td>{day.provider_requests == null ? "—" : number(day.provider_requests)}</td>
          <td className="pr-2">{usd(day.billed_nusd == null ? null : day.billed_nusd / 1e9)}</td>
          <td className="pr-4">{statuses[day.status]}</td>
          <td>{Object.entries(day.work || {}).map(([kind,row])=><div key={kind} className="whitespace-nowrap">
            {workLabels[kind] || "Unknown work type"} · {number(row.attempts)} {row.attempts === 1 ? "call" : "calls"} · {row.billed_nusd != null ? usd(row.billed_nusd/1e9)
              : row.priced && row.priced === row.attempts ? estimate([row.low_nusd/1e9,row.high_nusd/1e9])+" est." : "cost pending"}
          </div>)}</td></tr>)}</tbody></table></div>
        : <p>No recorded key activity or imported statement for these dates.</p>}
    </details>
  </>;
}

export default function MessageCosts({ api }) {
  const [days,setDays] = useState(7);
  const today = new Date().toISOString().slice(0,10);
  const [end,setEnd] = useState(today);
  const [version,setVersion] = useState(0);
  const [report,setReport] = useState(null);
  const [error,setError] = useState("");
  const [file,setFile] = useState(null);
  const [busy,setBusy] = useState(false);
  const [notice,setNotice] = useState("");
  const [importError,setImportError] = useState("");
  useEffect(()=>{
    const ctl = new AbortController();
    setReport(null); setError("");
    if (end) api.get(endpoint, {params:{days,end_date:end}, signal:ctl.signal})
      .then(({data})=>{if (!ctl.signal.aborted) setReport(data);})
      .catch(e=>{if (!ctl.signal.aborted) setError(errMsg(e));});
    return ()=>ctl.abort();
  },[api,days,end,version]);

  async function importFile(event) {
    event.preventDefault();
    if (!file || busy) return;
    const input = event.currentTarget.querySelector('input[type="file"]');
    setBusy(true); setNotice(""); setImportError("");
    try {
      const {data:result} = await api.post(endpoint+"/statement",file,{headers:{"Content-Type":"application/zip"}});
      setNotice(`Checked ${result.days} UTC days. Updated ${result.changed}. Coverage runs from ${result.start} through ${result.end}.`);
      setFile(null); input.value=""; setVersion(n=>n+1);
    } catch (e) {setImportError(errMsg(e));}
    finally {setBusy(false);}
  }
  return <section className="card p-4 sm:p-5 space-y-4" data-testid="admin-message-costs">
    <h2 className="font-display text-2xl font-black uppercase">Message costs</h2>
    <p className="text-sm text-[var(--muted)]">Your Type uses local statistical calculations: zero DeepSeek calls and zero message credits. Its actions are tracked in Research and Activity, separately from this paid-call ledger.</p>
    <div className="filter-grid text-sm">
      <label className="filter-field"><span>Period</span><select className={`${field} filter-control`} value={days} onChange={e=>setDays(Number(e.target.value))}>
        {[1,7,30].map(n=><option key={n} value={n}>{n === 1 ? "One UTC day" : n+" days"}</option>)}</select></label>
      <label className="filter-field"><span>Ending on</span><input className={`${field} filter-control`} type="date" min="2000-01-01" max={today} value={end} onChange={e=>setEnd(e.target.value)} /></label>
      <button type="button" className="btn-ghost h-10 px-3" onClick={()=>setVersion(n=>n+1)}>Refresh costs</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {end && !report && !error && <p role="status">Loading message costs…</p>}
    {report && <><CostSummary report={report}/>
      <MessageCostReceipts api={api} start={report.start} end={report.end}/>
      {report.dedicated ? <form className="space-y-2 border-t border-[var(--line)] pt-3" onSubmit={importFile}>
        <h3 className="font-semibold">Import a DeepSeek statement</h3>
        <p className="text-sm">In <a href="https://platform.deepseek.com/usage" className="underline" target="_blank" rel="noreferrer">DeepSeek Usage</a>, choose <strong>{report.key_name}</strong> and export a completed UTC day. A one-day export includes the hourly rates needed when prices change during the day.</p>
        <label className="block text-sm">Usage ZIP <input type="file" disabled={busy} accept=".zip,application/zip" onChange={e=>{setFile(e.target.files?.[0] || null);setNotice("");setImportError("");}} /></label>
        <button className="btn-ghost px-4 py-2 text-sm disabled:opacity-50" disabled={busy || !file}>{busy ? "Checking…" : "Reconcile statement"}</button>
      </form> : <p className="text-sm">Statement matching is available on the dedicated production key.</p>}
    </>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {importError && <p role="alert" className="text-sm">{importError}</p>}
  </section>;
}
