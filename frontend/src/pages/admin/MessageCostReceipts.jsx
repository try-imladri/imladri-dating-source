import { useEffect, useState } from "react";
import { errMsg } from "@/lib/api";

const endpoint = "/admin/prompts/message-costs/receipts";
const button = "btn-ghost px-3 py-1 text-sm disabled:opacity-50";
const usd = new Intl.NumberFormat("en-US", {style:"currency", currency:"USD", maximumFractionDigits:9});
const stamp = new Intl.DateTimeFormat("en-US", {timeZone:"UTC", month:"short", day:"numeric", hour:"2-digit", minute:"2-digit", second:"2-digit", hourCycle:"h23"});

function usePage(api, url) {
  const [reload, setReload] = useState(0);
  const [result, setResult] = useState(null);
  useEffect(() => {
    const abort = new AbortController();
    api.get(url, {signal:abort.signal}).then(({data}) => {
      if (!abort.signal.aborted) setResult({url, api, reload, data});
    }).catch(error => {
      if (!abort.signal.aborted) setResult({url, api, reload, error:errMsg(error)});
    });
    return () => abort.abort();
  }, [api, url, reload]);
  const active = result?.url === url && result.api === api && result.reload === reload;
  return {data:active && result.data, error:active && result.error, retry:() => setReload(n => n+1)};
}

function Feedback({error, retry}) {
  return error ? <p role="alert">{error} <button className={button} onClick={retry}>Retry</button></p>
    : <p role="status" className="text-[var(--muted)]">Loading receipts…</p>;
}

function Paging({pages, setPages, next}) {
  if (!next && pages.length === 1) return null;
  return <div className="flex gap-2">
    <button className={button} disabled={pages.length === 1} onClick={() => setPages(p => p.slice(0,-1))}>Previous page</button>
    <button className={button} disabled={!next} onClick={() => setPages(p => [...p,next])}>Next page</button>
  </div>;
}

function time(value) {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? stamp.format(date) : "—";
}

function estimate(row) {
  if (!Number.isFinite(row.low_nusd) || !Number.isFinite(row.high_nusd)) return "Unknown";
  const low = usd.format(row.low_nusd/1e9), high = usd.format(row.high_nusd/1e9);
  return low === high ? low : low+"–"+high;
}

const rejectionLabels = {
  quick_advice_shape: "Quick Advice failed response validation",
  tone_status: "Unrecognized tone assessment",
  tone_drafts: "Missing or repeated reply styles",
  tone_length: "Reply exceeded the length limit",
  tone_intent: "Added an unsupported commitment or belief",
  reply_tone_shape: "Unrecognized tone response",
  reply_style_shape: "Incomplete reply styles",
  reply_style_duplicate: "Repeated reply styles",
  reply_intent: "Added an unsupported commitment or belief",
};

function Attempts({api, id}) {
  const [pages, setPages] = useState([""]);
  const cursor = pages[pages.length-1];
  const {data, error, retry} = usePage(api, endpoint+"/"+id+(cursor ? "?cursor="+encodeURIComponent(cursor) : ""));
  if (!data) return <Feedback error={error} retry={retry}/>;
  return <div className="space-y-2">
    <p className="text-xs break-all">Message receipt {id}</p>
    {data.message.background_pending > 0 && <p role="status">Background work is still pending.</p>}
    {data.calls.length ? <div className="overflow-x-auto"><table className="w-full text-left text-xs">
      <thead><tr>{["Started (UTC)","Provider call","Status","Input / output tokens","Estimated USD"].map(s => <th key={s} scope="col" className="p-2">{s}</th>)}</tr></thead>
      <tbody>{data.calls.map(call => <tr key={call.id} className="border-t border-[var(--line)]">
        <td className="p-2 whitespace-nowrap">{time(call.started_at)}</td>
        <td className="p-2">{call.feature} · {call.preparation ? "preparation" : call.mode}{call.retry ? " · provider retry" : ""}
          {call.message_run > 1 && <span className="block">Member retry · run {call.message_run}</span>}
          <span className="block text-[var(--muted)]">{call.model || call.requested_model}</span></td>
        <td className="p-2">{call.status}{call.http_status ? " · "+call.http_status : ""}
          {rejectionLabels[call.application_rejection] && <span className="block">App rejected reply · {rejectionLabels[call.application_rejection]}</span>}
        </td>
        <td className="p-2">{Number.isFinite(call.input_tokens) && Number.isFinite(call.output_tokens) ? call.input_tokens+" / "+call.output_tokens : Number.isFinite(call.total_tokens) ? call.total_tokens+" total" : "Unknown"}
          {Number.isFinite(call.cache_hit_tokens) && Number.isFinite(call.cache_miss_tokens) && <span className="block text-[var(--muted)]">Cache hits {call.cache_hit_tokens} · misses {call.cache_miss_tokens}</span>}
        </td>
        <td className="p-2">{estimate(call)}</td>
      </tr>)}</tbody></table></div> : <p>No provider calls are recorded for this receipt.</p>}
    <Paging pages={pages} setPages={setPages} next={data.next_cursor}/>
  </div>;
}

function ReceiptList({api, start, end, workspace}) {
  const [pages, setPages] = useState([""]);
  const [selected, setSelected] = useState("");
  const params = new URLSearchParams({start,end});
  if (workspace) params.set("workspace",workspace);
  if (pages[pages.length-1]) params.set("cursor", pages[pages.length-1]);
  const {data, error, retry} = usePage(api, endpoint+"?"+params.toString());
  if (!data) return <Feedback error={error} retry={retry}/>;
  return <div className="space-y-3">
    {data.items.length ? <ul className="divide-y divide-[var(--line)]">{data.items.map(item => <li key={item.id} className="py-1">
      <button className="w-full p-2 text-left underline" aria-expanded={selected === item.id} onClick={() => setSelected(s => s===item.id ? "" : item.id)}>
        {time(item.started_at)} UTC · {item.workspace} · {item.kind} · {item.status}
      </button>
      {selected === item.id && <Attempts api={api} id={item.id} key={item.id}/>}
    </li>)}</ul> : <p>No message receipts started in this UTC period.</p>}
    <Paging pages={pages} setPages={setPages} next={data.next_cursor}/>
  </div>;
}

export default function MessageCostReceipts({api, start, end}) {
  const [open, setOpen] = useState(false);
  const [workspace, setWorkspace] = useState("");
  return <div className="rounded-xl border border-[var(--line)] p-3">
    <button type="button" className="font-semibold underline" aria-expanded={open} onClick={() => setOpen(o => !o)}>Message receipts</button>
    {open && <div className="mt-3 space-y-2">
      <label className="block text-sm">Workspace <select className="rounded border border-[var(--line)] bg-[var(--bg)] p-1" value={workspace} onChange={e => setWorkspace(e.target.value)}>
        <option value="">All workspaces</option><option value="individual">Individual</option><option value="couples">Couples</option>
      </select></label>
      <ReceiptList key={[start,end,workspace].join("|")} api={api} start={start} end={end} workspace={workspace}/>
    </div>}
  </div>;
}
