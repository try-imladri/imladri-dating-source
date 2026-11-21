import { useState } from "react";
import { errMsg } from "@/lib/api";

function Source({ item, person, api, index }) {
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  async function load() {
    setBusy(true);
    try {
      const { data } = await api.get("/people/"+encodeURIComponent(person)+"/evidence/"+item.kind+"/"+encodeURIComponent(item.id));
      setResult({ data });
    } catch (error) {
      setResult({ error: error.response?.status === 404 ? "This source is no longer available." : errMsg(error) });
    } finally { setBusy(false); }
  }
  return <li><button type="button" className="underline" disabled={busy} onClick={load}>
    {busy ? "Opening…" : `Open ${item.kind} ${index + 1}`}</button>
    {result?.error && <p role="status">{result.error}</p>}
    {result?.data && <div className="mt-1"><p>{result.data.label}{result.data.created_at && " · "+new Date(result.data.created_at).toLocaleDateString()}</p>
      <p className="whitespace-pre-wrap">{result.data.text}</p>
      {item.kind === "exchange" && <a className="underline" href={"/app/person/"+encodeURIComponent(person)+"?"+new URLSearchParams({tab:"history",exchange:item.id})}>Read full exchange</a>}
    </div>}</li>;
}

export function ReportEvidence({ refs, person, api, label = "Supporting sources" }) {
  if (!refs?.length) return <p className="text-xs text-[var(--muted)] mt-2">No linked source for this interpretation.</p>;
  return <details className="text-xs mt-2"><summary className="cursor-pointer">{label} · {refs.length}</summary>
    <ul className="mt-1 space-y-1">{refs.map((r, i) => <Source key={r.kind + r.id} item={r} index={i} person={person} api={api} />)}</ul>
  </details>;
}

export function ContextMemories({ exchange, api }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function load() {
    setBusy(true); setError("");
    try { setData((await api.get("/exchanges/"+encodeURIComponent(exchange)+"/context-memories")).data); }
    catch (e) { setError(errMsg(e)); } finally { setBusy(false); }
  }
  return <div className="text-xs text-[var(--muted)]">
    <button type="button" className="underline" onClick={data ? () => setData(null) : load} disabled={busy}>
      {data ? "Hide coaching memories" : busy ? "Loading…" : "Which memories were supplied for this reply?"}</button>
    {error && <p role="status">{error}</p>}
    {data && (!data.recorded ? <p>Individual memory references were not recorded for this earlier reply.</p> :
      <><p>{data.items.length} available memories. Opening a memory shows its current wording.{data.removed > 0 && ` ${data.removed} subsequently removed.`}</p>
      {data.items.length > 0 && <details><summary className="cursor-pointer">Read the memories</summary><ul className="list-disc pl-4">
        {data.items.map(item => <li key={item.id}>{item.text}{item.edited_at && ` · edited ${new Date(item.edited_at).toLocaleDateString()}`}</li>)}</ul></details>}</>)}
  </div>;
}
