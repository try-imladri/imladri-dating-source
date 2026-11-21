import { useCallback, useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { LimitNotice, messageLimit } from "@/components/app/PlanUsage";
import { Pencil, Trash2, Check, ScanSearch } from "lucide-react";
import { toast } from "sonner";
import { errMsg } from "@/lib/api";
import { Input } from "@/components/ui/input";

function PatternRow({ p, api, reload }) {
  const [edit, setEdit] = useState(false);
  const [text, setText] = useState(p.text);
  const [busy, setBusy] = useState(false);
  const update = async (operation) => {
    setBusy(true);
    try {
      await operation();
      setEdit(false);
      await reload();
    } catch (error) {
      toast.error(errMsg(error));
      await reload();
    } finally {
      setBusy(false);
    }
  };
  const state = p.evidence_status || "tentative";
  const confirmed = state === "confirmed";
  const samples = p.example_count || 0;
  const rehearsals = p.practice_count || 0;
  const origin = [
    samples > 0 && `${samples} distinct conversation${samples === 1 ? "" : "s"}`,
    rehearsals > 0 && `${rehearsals} rehearsal${rehearsals === 1 ? "" : "s"}`,
    p.people?.length > 0 && `with ${p.people.join(", ")}`,
  ].filter(Boolean).join(" · ");
  const labels = { tentative: "Tentative observation", supported: "Seen across conversations", confirmed: "Confirmed by you" };
  return (
    <li className="py-3 border-b border-[var(--line)] last:border-0" data-testid={`pattern-${p.id}`}>
      <p className="text-xs font-semibold mb-1">{labels[state]}</p>
      {edit ? <div className="flex gap-2">
        <Input value={text} aria-label="Observation wording" maxLength={300} onChange={(e) => setText(e.target.value)} data-testid="pattern-edit-input" />
        <button aria-label="Save wording" disabled={busy || !text.trim()} onClick={() => update(() => api.patch(`/patterns/${p.id}`, { text }))} data-testid="pattern-save"><Check className="w-4 h-4" /></button>
        <button className="text-xs underline" disabled={busy} onClick={() => setEdit(false)}>Cancel</button>
      </div> : <p className="text-sm">{p.text}</p>}
      {p.evidence?.length > 0 && <details className="text-xs mt-2"><summary className="cursor-pointer">View source examples and dates</summary>
        <ul className="space-y-1 mt-2">{p.evidence.map(source => <li key={source.kind + source.id}>
          <a className="underline" href={source.kind === "practice"
            ? "/app/practice?" + new URLSearchParams({person:source.person_id,session:source.id})
            : "/app/person/" + encodeURIComponent(source.person_id) + "?" + new URLSearchParams({tab:"history",exchange:source.id})}>
            {source.kind === "practice" ? "Practice rehearsal" : "Saved conversation"} · {source.created_at ? new Date(source.created_at).toLocaleDateString() : "Date unavailable"}
          </a></li>)}</ul></details>}
      {origin && <p className="text-[11px] text-[var(--muted)] mt-1">{origin}</p>}
      <div className="flex flex-wrap items-center gap-3 mt-2">
        {!edit && <>
          <button type="button" disabled={busy} className="text-xs underline" data-testid="pattern-confirm"
            onClick={() => update(() => api.put(`/patterns/${p.id}/confirmation`, { text: p.text, confirmed: !confirmed }))}>
            {confirmed ? "Withdraw my confirmation" : "Confirm this describes me"}
          </button>
          <button onClick={() => { setText(p.text); setEdit(true); }} disabled={busy} aria-label="Edit observation" className="text-[var(--muted)] hover:text-[var(--ink)]" data-testid="pattern-edit"><Pencil className="w-3.5 h-3.5" /></button>
        </>}
        <button disabled={busy} aria-label="Delete observation" onClick={() => update(() => api.delete(`/patterns/${p.id}`))} className="text-[var(--muted)] hover:text-[var(--crimson)]" data-testid="pattern-delete"><Trash2 className="w-3.5 h-3.5" /></button>
      </div>
    </li>
  );
}

export function PatternsPanel({ api, className = "", allowScan = true }) {
  const { me } = useOutletContext();
  const limit = messageLimit(me.usage);
  const [list, setList] = useState([]);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const load = useCallback(async () => {
    setLoadError("");
    try { const { data } = await api.get("/patterns"); setList(data); }
    catch (error) { setLoadError(errMsg(error)); }
  }, [api]);
  const scan = async () => {
    setBusy(true);
    try {
      const { data } = await api.post("/patterns/scan");
      toast.success(data.added ? `${data.added} new observation${data.added > 1 ? "s" : ""} found` : "Checked. Nothing new yet.");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
    setBusy(false);
  };
  useEffect(() => {
    load();
  }, [load]);
  return (
    <section className={`card p-6 ${className}`} data-testid="patterns-panel">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-display font-black uppercase text-2xl">Your Patterns</p>
          <p className="text-xs text-[var(--muted)] mt-1 mb-3">An observation can guide future advice once it appears in two distinct real conversations or you confirm it. You can edit or delete any observation.</p>
        </div>
        {allowScan && <button disabled={busy || !!limit} onClick={scan} className="btn-ghost px-3 py-1.5 text-xs inline-flex items-center gap-1.5 shrink-0" data-testid="patterns-scan"><ScanSearch className="w-3.5 h-3.5" />{busy ? "Looking…" : "Look again"}</button>}
      </div>
      {allowScan && limit && <LimitNotice reason={limit} />}
      {!allowScan && <p className="mb-3 text-xs text-[var(--muted)]">Your saved observations stay private. Use Couples conversations and What works for us to explore your relationship together.</p>}
      {loadError ? <p role="alert" className="text-sm">Couldn’t load patterns. <button type="button" className="underline" onClick={load}>Try again</button></p> : !list.length ? <p className="text-sm text-[var(--muted)]" data-testid="patterns-empty">{!allowScan || limit ? "No saved patterns yet." : "Nothing yet. Chat about your people, then press Look again."}</p>
        : <ul>{list.map((p) => <PatternRow key={p.id} p={p} api={api} reload={load} />)}</ul>}
    </section>
  );
}
