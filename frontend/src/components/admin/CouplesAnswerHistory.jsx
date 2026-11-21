import { useEffect, useState } from "react";
import AnswerHistoryTimeline from "./AnswerHistoryTimeline";

export default function CouplesAnswerHistory({ api }) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [cursors, setCursors] = useState([null]);
  const [data, setData] = useState(null);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const before = cursors[cursors.length - 1];
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError(false); setSelected("");
    api.get("/admin/answer-history-couples", { params: { q: search, limit: 50, ...(before ? { before } : {}) }, signal: controller.signal })
      .then(({ data: result }) => { if (!controller.signal.aborted) setData(result); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [api, search, before, attempt]);

  return <section className="card space-y-4 p-4 sm:p-5" aria-label="Couples answer history">
    <h2 className="font-display text-xl font-bold uppercase">Couples answer history</h2>
    <p className="text-sm text-[var(--muted)]">Choose a couple to review recorded changes. Answers require both partners’ current staff-history permissions and the applicable permission at the time of each answer. Private answers stay private from partners.</p>
    <form className="flex flex-wrap gap-2" onSubmit={event => { event.preventDefault(); setSearch(query.trim()); setCursors([null]); setAttempt(n => n + 1); }}>
      <label className="min-w-0 flex-1 text-sm">Find a couple by pair ID
        <input value={query} maxLength={100} onChange={event => setQuery(event.target.value)}
          className="mt-1 block w-full rounded-lg border border-[var(--line)] bg-[var(--surface)] p-2" />
      </label>
      <button type="submit" className="btn-ghost self-end px-4 py-2 text-sm">Find</button>
    </form>
    {error ? <p role="alert" className="text-sm">Couples couldn’t load. <button className="underline" onClick={() => setAttempt(n => n + 1)}>Retry</button></p>
      : !data ? <p role="status" className="text-sm">Loading couples…</p>
      : !data.items.length ? <p className="text-sm">No active couples match this page.</p> : <label className="block text-sm">Couple
        <select value={selected} onChange={event => setSelected(event.target.value)} className="mt-1 block w-full rounded-lg border border-[var(--line)] bg-[var(--surface)] p-2">
          <option value="">Choose a couple</option>
          {data.items.map(pair => <option key={pair.pair_id} value={pair.pair_id}>{pair.label} · {pair.pair_id}</option>)}
        </select>
      </label>}
    {data && (cursors.length > 1 || data.next) && <nav aria-label="Couple history pages" className="flex items-center gap-3 text-sm">
      <button type="button" disabled={cursors.length === 1} onClick={() => setCursors(values => values.slice(0, -1))} className="underline disabled:opacity-50">Previous couples</button>
      <span>Page {cursors.length}</span>
      <button type="button" disabled={!data.next} onClick={() => setCursors(values => [...values, data.next])} className="underline disabled:opacity-50">Next couples</button>
    </nav>}
    {selected && <AnswerHistoryTimeline key={selected} api={api} scope="couple" target={selected} />}
  </section>;
}
