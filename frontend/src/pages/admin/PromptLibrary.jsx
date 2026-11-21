import { useEffect, useState } from "react";
import { errMsg } from "@/lib/api";
import { CouplesStarters } from "./CouplesAnalytics";
import { SubTabs } from "./SubTabs";
import PromptVersions from "./PromptVersions";
import ProviderComparison from "./ProviderComparison";
import MessageCosts from "./MessageCosts";

const field = "block w-full rounded-xl border border-[var(--line)] bg-[var(--bg)] p-3 text-base";
const filterField = "filter-control rounded-xl border border-[var(--line)] bg-[var(--bg)] px-3 py-2 text-sm";
const button = "btn-ghost px-4 py-2 text-sm disabled:opacity-50";

const workLabels = {metered: "Allowance-funded features", included: "Included features",
  background: "Background work", evaluation: "Staff evaluations"};
const dollars = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD", maximumFractionDigits: 8});

function WorkCosts({ work }) {
  const rows = Object.entries(work || {});
  if (!rows.length) return null;
  return <details className="rounded-xl border border-[var(--line)] p-3">
    <summary className="cursor-pointer font-semibold">Provider cost by type of work</summary>
    <div className="mt-2 overflow-auto"><table className="w-full text-left">
      <thead><tr>{["Work", "Generations", "Provider attempts", "Priced attempts", "Estimated cost of priced attempts"].map(label=><th scope="col" key={label} className="pr-2">{label}</th>)}</tr></thead>
      <tbody>{rows.map(([mode, row])=><tr key={mode}><th scope="row" className="py-1 pr-2">{workLabels[mode] || mode}</th>
        <td>{row.generations}</td><td>{row.attempts}</td><td>{row.priced} / {row.attempts}</td>
        <td>{row.priced ? `${dollars.format(row.low_nusd/1e9)}–${dollars.format(row.high_nusd/1e9)}` : row.attempts ? "Unknown" : "—"}</td>
      </tr>)}</tbody>
    </table></div>
    <p className="mt-1 text-xs text-[var(--muted)]">Generation and retry counts measure provider work. The allowance totals above count completed member debits.</p>
  </details>;
}

function FeatureUsage({ api, features }) {
  const [days, setDays] = useState(30);
  const [day, setDay] = useState(() => new Date(Date.now() - 86400000).toISOString().slice(0, 10));
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const ctl = new AbortController();
    setReport(null); setError("");
    if (days === 1 && !day) return;
    api.get("/admin/prompts/usage", { params: days === 1 ? { days, end_date: day } : { days }, signal: ctl.signal })
      .then(({ data }) => { if (!ctl.signal.aborted) setReport(data); })
      .catch(e => { if (!ctl.signal.aborted) setError(errMsg(e)); });
    return () => ctl.abort();
  }, [api, days, day, attempt]);
  return <section className="card p-5 space-y-4">
    <h2 className="font-display font-black text-2xl uppercase">AI activity by feature</h2>
    <div className="filter-grid">
    <label className="filter-field text-sm"><span>Reporting period</span>
      <select value={days} className={filterField} onChange={e => setDays(Number(e.target.value))}>
        <option value={1}>One UTC calendar day</option>
        {[7, 30, 90].map(n => <option key={n} value={n}>Last {n} days</option>)}
      </select>
    </label>
    {days === 1 && <label className="filter-field text-sm"><span>UTC date</span><input type="date" className={filterField} min="2000-01-01" max={new Date().toISOString().slice(0,10)} value={day} onChange={e => setDay(e.target.value)} /></label>}
    </div>
    {report && <><p className="text-sm text-[var(--muted)]">From {report.start} through {report.end} UTC. Each generation request counts once. Individual reads include tone guidance. Optional Couples coaching appears in its own row.</p>
      <table className="w-full text-sm text-left" data-testid="admin-prompt-usage"><thead><tr>
        <th scope="col">Feature</th><th scope="col">Succeeded</th><th scope="col">Failed</th></tr></thead>
        <tbody>{Object.entries(features).map(([key, label]) => <tr key={key} className="border-t border-[var(--line)]">
          <th scope="row" className="py-2 pr-2 font-medium">{label}</th><td>{report.counts[key]?.succeeded || 0}</td><td>{report.counts[key]?.failed || 0}</td>
        </tr>)}</tbody>
      </table></>}
    {report?.activity && <div className="space-y-3 text-sm">
      <h3 className="font-semibold">Provider calls and member allowances</h3>
      <p>Current accounts used {report.settled.plan} plan replies and {report.settled.refill} refill replies in this period since detailed tracking began. Each settled reply counts once, even when its generation needed multiple calls.</p>
      <WorkCosts work={report.work} />
      <p>Attempts include requests without reported tokens. Metered, included and background columns count generation requests. Cost ranges use reported input and output, cache rates and peak pricing. Missing usage is excluded. <a className="underline" href="https://api-docs.deepseek.com/quick_start/pricing/">DeepSeek pricing</a></p>
      <div className="overflow-auto"><table className="w-full text-left"><thead><tr>{["Feature","Metered","Included","Background","Staff evaluations","Attempts","Automatic retries","Tokens in / out","Usage coverage","Cached / fresh input","Mean provider response time","Estimated USD"].map(h=><th key={h} className="pr-2">{h}</th>)}</tr></thead>
        <tbody>{Object.entries(report.activity).map(([key,v])=><tr key={key}><th scope="row">{features[key]||key}</th>
          <td>{v.metered||0}</td><td>{v.included||0}</td><td>{v.background||0}</td><td>{v.evaluation||0}</td><td>{v.attempts}</td><td>{v.retries}</td>
          <td>{v.reported ? v.input_tokens+" / "+v.output_tokens : "—"}</td><td>{v.reported} / {v.attempts} with input and output counts{v.total_only_reports > 0 && <span className="block">{v.total_only_reports} reported only a total · {v.total_only_tokens} tokens</span>}</td>
          <td>{v.cache_reported ? `${v.cache_hit_tokens} / ${v.cache_miss_tokens} (${v.cache_reported} calls)` : "—"}</td>
          <td>{v.timed_attempts ? `${(v.provider_ms / v.timed_attempts / 1000).toFixed(2)} s (${v.timed_attempts} calls)` : "—"}</td>
          <td>{v.priced ? "$"+(v.low_nusd/1e9).toPrecision(3)+"–$"+(v.high_nusd/1e9).toPrecision(3) : "—"}</td></tr>)}</tbody></table></div>
      {days === 1 && <ProviderComparison key={report.start} report={report} />}
      {!Object.keys(report.activity).length && <p>No instrumented generations in this period.</p>}
    </div>}
    {!report && !error && (days !== 1 || day) && <p role="status">Loading activity…</p>}
    {error && <p role="alert">{error} <button className="underline" onClick={() => setAttempt(n => n + 1)}>Retry</button></p>}
  </section>;
}

const promptTabs = [["ai", "AI instructions"], ["couples", "Couples starters"], ["usage", "AI activity"], ["costs", "Message costs"], ["versions", "Versions & evaluations"]];

export default function PromptLibrary({ api, view, onViewChange }) {
  const [localTab, setLocalTab] = useState("ai");
  const selected = onViewChange ? view : localTab;
  const tab = promptTabs.some(([id]) => id === selected) ? selected : "ai";
  const [library, setLibrary] = useState(null);
  const [key, setKey] = useState("six_part");
  const [context, setContext] = useState("six_part");
  const toneGuidance = key === "incoming_tone" || key === "reply_style";
  const [drafts, setDrafts] = useState({});
  const [preview, setPreview] = useState(null);
  const [candidate, setCandidate] = useState(null);
  const [reviewed, setReviewed] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const ctl = new AbortController();
    setError(""); setPreview(null);
    api.get("/admin/prompts", { signal: ctl.signal }).then(({ data }) => {
      if (!ctl.signal.aborted) setLibrary(data);
    }).catch(e => { if (!ctl.signal.aborted) setError(errMsg(e)); });
    return () => ctl.abort();
  }, [api, attempt]);
  const current = library?.prompts[key] ?? "";
  const draft = drafts[key] ?? current;
  const labels = Object.assign({}, ...Object.values(library?.areas || {}));
  function resetCandidate() { setCandidate(null); setReviewed(false); }
  function select(value) { setKey(value); setPreview(null); resetCandidate(); setNotice(""); setError(""); }
  async function apply(action) {
    setBusy(true); setError(""); setNotice("");
    try {
      if (action === "preview") {
        const { data } = await api.post("/admin/prompts/preview", { key, text: draft, ...(toneGuidance && { context }) });
        setPreview(data);
      } else if (action === "evaluate") {
        resetCandidate();
        const {data} = await api.post("/admin/prompts/evaluate",{prompts_revision:library.revision,prompts:{[key]:draft.trim()}});
        setCandidate(data);
      } else {
        const { data } = await api.put("/admin/settings", { prompts_revision: library.revision, prompts: { [key]: draft.trim() }, reviewed_candidate:candidate.id });
        setLibrary({ ...library, prompts: data.prompts, revision: data.prompts_revision, published_at: data.prompts_published_at });
        setDrafts(saved => { const remaining = { ...saved }; delete remaining[key]; return remaining; });
        setPreview(null); resetCandidate(); setNotice("Published. New generations use this guidance.");
      }
    } catch (e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  }
  return <div className="space-y-4 min-h-0 overflow-y-auto pb-6" data-testid="admin-prompts">
    <SubTabs value={tab} onChange={onViewChange || setLocalTab} items={promptTabs} testid="admin-prompt-groups" />
    {tab === "costs" ? <MessageCosts api={api}/> : tab === "versions" ? <PromptVersions api={api} onRestore={()=>{setDrafts({});setAttempt(n=>n+1);}}/> : tab === "couples" ? <CouplesStarters api={api} /> : tab === "usage" && library ? <FeatureUsage api={api} features={library.usage_features || Object.assign({}, ...Object.entries(library.areas).filter(([area]) => !["Relationship tone", "Relationship context", "Tone guidance"].includes(area)).map(([, group]) => group))} />
      : library && <section className="card space-y-4 p-4 sm:p-5">
        <h2 className="font-display text-2xl font-black uppercase">AI instructions</h2>
        <label className="block space-y-1 text-sm">Choose an instruction
          <select value={key} onChange={e => select(e.target.value)} disabled={busy} className={field} data-testid="admin-prompt-select">
            {Object.entries(library.areas).map(([area, group]) => <optgroup key={area} label={area}>
              {Object.entries(group).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </optgroup>)}
          </select>
        </label>
        <label className="block space-y-1 text-sm">{labels[key]} guidance
          <textarea className={field} rows={6} maxLength={library.max_length} value={draft} disabled={busy}
            data-testid={`admin-prompt-${key}`} onChange={e => {
              setDrafts({ ...drafts, [key]: e.target.value }); setPreview(null); resetCandidate(); setNotice("");
            }} />
        </label>
        {toneGuidance && <label className="block text-sm">Preview this guidance in
          <select className={field} value={context} disabled={busy} onChange={e => { setContext(e.target.value); setPreview(null); }}>
            <option value="six_part">Individual conversation</option><option value="couples_tone">Couples coaching</option>
          </select>
        </label>}
        <div className="flex flex-wrap gap-2">
          <button type="button" disabled={busy || !draft.trim()} className={button} onClick={() => apply("preview")}>Preview guidance</button>
          <button type="button" disabled={busy || !draft.trim() || draft.trim() === current} className={button} onClick={()=>apply("evaluate")}>Evaluate draft with DeepSeek</button>
          <button type="button" disabled={busy || !reviewed || !candidate?.eligible || !draft.trim() || draft.trim() === current} className="btn-lime px-4 py-2 text-sm disabled:opacity-50" data-testid="admin-prompts-save" onClick={() => apply("publish")}>Publish instruction</button>
        </div>
        <p className="text-xs text-[var(--muted)]">Evaluating runs synthetic examples against this draft and uses provider tokens. Previewing the guidance is free.</p>
        {candidate && <div className="space-y-3 rounded-xl border border-[var(--line)] p-4 text-sm">
          {candidate.cases.map((c,i)=><div key={i}><h3 className="font-semibold">{labels[c.feature]||c.feature}{c.context && " · "+c.context}</h3>
            <p>{c.input}</p><p className="whitespace-pre-wrap my-1">{c.answer||"No readable answer."}</p>
            {c.reply_options?.length>0 && <ul className="list-disc pl-5">{c.reply_options.map((option,n)=><li key={n}>{option}</li>)}</ul>}<p>{c.expectation}</p>
            {!c.passed && <p role="alert">The response failed the format or no-contact check.</p>}</div>)}
          {candidate.eligible && <label><input type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)} /> I reviewed these candidate responses against each expectation and approve publication.</label>}
        </div>}
        {preview && <div className="rounded-xl border border-[var(--line)] p-4" data-testid="admin-prompt-preview">
          <h3 className="font-semibold">Guidance preview</h3>
          <p className="my-2 text-xs text-[var(--muted)]">{preview.keys.map(id => labels[id]).join(" + ")}. The app supplies the task, response format and private context at generation time.</p>
          <p className="whitespace-pre-wrap break-words text-sm">{preview.guidance}</p>
        </div>}
        {library.published_at && <p className="text-xs text-[var(--muted)]">Last AI instruction published {new Date(library.published_at).toLocaleString()}.</p>}
        {notice && <p role="status" className="text-sm">{notice}</p>}
      </section>}
    {!library && !error && !["couples","costs"].includes(tab) && <p role="status">Loading instructions…</p>}
    {error && !["couples","costs"].includes(tab) && <p role="alert" className="text-sm">{error} <button className="underline" onClick={() => setAttempt(n => n + 1)}>Reload published instructions</button></p>}
  </div>;
}
