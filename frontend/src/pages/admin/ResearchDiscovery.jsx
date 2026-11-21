import { useEffect, useRef, useState } from "react";

const order = (a, b) => b.cramers_v - a.cramers_v || b.sample_size - a.sample_size ||
  a.y.localeCompare(b.y) || (a.y_condition || "").localeCompare(b.y_condition || "");

export default function ResearchDiscovery({ api, focus, labels, onCompare }) {
  const active = useRef(null);
  const [search, setSearch] = useState(null);
  const [limit, setLimit] = useState(10);
  useEffect(() => () => active.current?.abort(), []);
  async function start() {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setSearch({ phase: "searching", checked: 0 });
    setLimit(10);
    let cursor = 0;
    let revision;
    const found = [];
    try {
      do {
        const { data } = await api.get("/admin/research/discover", { signal: controller.signal,
          params: { x: focus.x, x_condition: focus.x_condition, unit: focus.unit, cursor, catalog_version: revision } });
        if (controller.signal.aborted) return;
        if ((revision && revision !== data.catalog_version) || (data.next_cursor !== null && !(data.next_cursor > cursor))) {
          throw Error("The search changed.");
        }
        found.push(...data.matches);
        revision = data.catalog_version;
        cursor = data.next_cursor;
        setSearch({ phase: "searching", checked: data.checked, total: data.total });
      } while (cursor !== null);
      setSearch({ phase: "complete", matches: found.sort(order) });
    } catch (error) {
      if (!controller.signal.aborted) setSearch({ phase: "error", changed: error?.response?.status === 409 });
    }
  }
  function cancel() {
    active.current?.abort();
    setSearch(null);
  }
  return <section className="card min-w-0 space-y-3 p-4 sm:p-5" aria-label="Find related answers">
    <h2 className="font-display text-xl font-bold uppercase">Find related answers</h2>
    <p className="text-sm text-[var(--muted)]">Search all questionnaire variables for associations with <strong>{labels[focus.x]}</strong>
      {focus.x_condition && <> · {focus.x_condition}</>}, using the comparison you selected. The search includes “It depends” conditions.</p>
    {search?.phase === "searching" ? <div className="flex flex-wrap items-center gap-3">
      <p role="status" className="text-sm">{search.total ? `Checked ${search.checked} of ${search.total} variables…` : "Checking variables…"}</p>
      <button type="button" className="btn-ghost px-3 py-1 text-sm" onClick={cancel}>Cancel search</button>
    </div> : <button type="button" className="btn-ghost px-4 py-2 text-sm" onClick={start}>
      {search ? "Run search again" : "Search all variables"}
    </button>}
    {search?.phase === "error" && <p role="alert" className="text-sm">
      {search.changed ? "The published questions changed. Run the search again." : "The search couldn’t finish. Run it again to see the complete ranking."}
    </p>}
    {search?.phase === "complete" && (search.matches.length ? <div className="space-y-2">
      <h3 className="font-semibold">Strongest observed associations</h3>
      <p className="text-xs text-[var(--muted)]">{search.matches.length} comparisons met the reporting thresholds. Checking many questions increases the chance of coincidental patterns. These rankings describe the participating {focus.unit === "member" ? "members" : "couples"}.</p>
      <ol className="divide-y divide-[var(--line)]">{search.matches.slice(0, limit).map((m, i) =>
        <li key={m.y + "|" + (m.y_condition || "")} className="py-2">
          <button type="button" className="w-full rounded-lg p-1 text-left text-sm hover:underline" onClick={() => onCompare(m)}>
            <span className="font-medium">{i + 1}. {labels[m.y]}{m.y_condition && <> · {m.y_condition}</>}</span>
            <span className="block text-xs text-[var(--muted)]">Cramér’s V {m.cramers_v.toFixed(3)} · About {m.sample_size.toLocaleString()} {focus.unit === "member" ? "members" : "couples"}</span>
          </button>
        </li>)}</ol>
      {limit < search.matches.length && <button type="button" className="btn-ghost px-3 py-1 text-sm" onClick={() => setLimit(limit + 20)}>Show more associations</button>}
    </div> : <p role="status" className="text-sm">No comparisons met the reporting thresholds for this selection.</p>)}
  </section>;
}
