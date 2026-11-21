import { useState } from "react";
import { errMsg } from "@/lib/api";

const statusText = { both: "Both chose the published lead", one: "One chose the published lead",
  neither: "Neither chose the published lead", changed: "Source or match changed",
  unassessed: "No prior reader assessment", career_supported: "Career evidence supports the lead",
  career_reassigned: "Lead changed after career review", career_limited: "Career review found limited evidence" };

export default function FigureEvidence({ api, openEditor }) {
  const [rows, setRows] = useState(null), [slug, setSlug] = useState(""), [filter, setFilter] = useState("pending");
  const [note, setNote] = useState(""), [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const filtered = (rows || []).filter(row => {
    if (filter === "all") return true;
    if (filter === "pending") return ["one", "neither", "changed", "career_limited"].includes(row.status) && !row.decision;
    if (filter === "career") return Boolean(row.career);
    if (filter === "career_limited") return row.career?.result === filter && !row.decision;
    if (filter === "unassessed") return row.status === "unassessed" && !row.decision;
    return row.decision?.result === filter;
  });
  const item = filtered.find(row => row.slug === slug) || filtered[0];
  function reset() { setNote(""); setMessage(""); }
  async function load() {
    setBusy(true); reset();
    try { const { data } = await api.get("/admin/editorial/figure-reviews"); setRows(data); }
    catch (e) { setMessage(errMsg(e)); } finally { setBusy(false); }
  }
  async function save(result) {
    if (!item || busy) return;
    setBusy(true); setMessage("");
    try {
      const { data } = await api.put("/admin/editorial/figure-reviews/" + item.slug, {
        version: item.version, previous_id: item.decision?.id || null, result, note: note.trim(),
      });
      setRows(old => old.map(row => row.slug === item.slug ? { ...row, decision: data } : row));
      setNote(""); setMessage("Decision saved for " + item.name + ".");
    } catch (e) { setMessage(errMsg(e)); } finally { setBusy(false); }
  }
  return <details className="md:col-span-2 card p-4 my-3" onToggle={e => {
    if (e.currentTarget.open && rows === null && !busy) load();
  }}>
    <summary className="cursor-pointer font-semibold">Review lead matches</summary>
    <p className="text-sm text-[var(--muted)] mt-3">Open the career sources and record the observation that supports your decision. Some profiles also have two AI readings made with the published labels hidden.</p>
    {rows && <div className="space-y-3 mt-3">
      <div className="flex flex-wrap gap-2">
        <select disabled={busy} aria-label="Review status" value={filter} onChange={e => { setFilter(e.target.value); setSlug(""); reset(); }}>
          <option value="pending">Disagreements or changes awaiting review</option>
          <option value="unassessed">Awaiting first review</option><option value="all">All published profiles</option>
          <option value="career">AI career reviews</option><option value="career_limited">Career matches with limited evidence</option>
          <option value="retain">Retained by staff</option><option value="revise">Marked for revision</option>
        </select>
        <select disabled={busy || !item} aria-label="Profile to review" value={item?.slug || ""} onChange={e => { setSlug(e.target.value); reset(); }}>
          {filtered.map(row => <option key={row.slug} value={row.slug}>{row.name}</option>)}
        </select>
      </div>
      <p className="text-xs">{filtered.length} of {rows.length} profiles</p>
      {item && <div className="space-y-2 text-sm">
        <p className="font-semibold">{item.name} · {statusText[item.status]}</p>
        {item.career && <div>{item.career.scope && <p className="mb-1 font-medium">{item.career.scope}</p>}<p>{item.career.rationale}</p><p className="mt-1 text-xs">{item.career.reviewer} · {item.career.reviewed_on}</p>
          {item.career.human_reason && <p className="mt-2"><strong>Question for an editor:</strong> {item.career.human_reason}</p>}
          <ul className="mt-2 space-y-2">{item.career.sources.map(source => <li key={source.url}><a href={source.url} target="_blank" rel="noreferrer" className="underline">{source.label}</a>{source.evidence && <p>{source.evidence}</p>}</li>)}</ul></div>}
        {item.readers.map((r, i) => <p key={i}><strong>Reader {i + 1} · {r.label}.</strong> {r.reason}</p>)}
        <div className="flex flex-wrap gap-3"><a href={"/figures/" + item.slug + "#communicates"} target="_blank" rel="noreferrer" className="underline">Open career sources</a>
          <button type="button" className="underline" onClick={() => openEditor(item.slug)}>Edit article</button></div>
        {item.decision && <p>Latest staff decision · {item.decision.result === "retain" ? "Retain lead" : "Revise lead"}. {item.decision.note}</p>}
        <label className="block">Evidence for your decision<textarea value={note} maxLength={500} disabled={busy}
          onChange={e => setNote(e.target.value)} className="block w-full rounded-md bg-[var(--bg)] border p-2 mt-1" /></label>
        <div className="flex flex-wrap gap-2">{[["retain","Retain lead"],["revise","Mark for revision"]].map(([value,label]) =>
          <button key={value} className="btn-ghost px-3 py-2" disabled={busy || note.trim().length < 8} onClick={() => save(value)}>{label}</button>)}</div>
      </div>}
    </div>}
    {message && <p className="mt-3 text-sm" role="status">{message}</p>}
    <button className="mt-3 text-xs underline" type="button" disabled={busy} onClick={load}>{busy ? "Loading…" : "Reload evidence"}</button>
  </details>;
}
