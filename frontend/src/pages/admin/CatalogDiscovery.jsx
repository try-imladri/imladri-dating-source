import { useEffect, useRef, useState } from "react";

const ranking = (a, b) => b.cramers_v - a.cramers_v || b.sample_size - a.sample_size ||
  a.indices[0] - b.indices[0] || a.indices[1] - b.indices[1];

export default function CatalogDiscovery({ api, unit, scope, labels, onCompare }) {
  const [status, setStatus] = useState(null);
  const active = useRef(null);
  useEffect(() => () => active.current?.abort(), []);

  async function start() {
    active.current?.abort();
    const ctl = new AbortController();
    active.current = ctl;
    setStatus({ phase: "starting" });
    let plan;
    let cursor = 0;
    let review;
    let best = [];
    let tested = 0;
    try {
      while (!ctl.signal.aborted) {
        const { data } = await api.post("/admin/research/scan", { unit, plan, cursor, review }, { signal: ctl.signal });
        if (ctl.signal.aborted) return;
        if (data.phase === "done") {
          setStatus({ phase: "done", tested, matches: data.matches });
          return;
        }
        if (!data.plan || !(data.next_cursor > cursor)) throw Error("Incomplete catalog scan.");
        if (data.phase === "comparing") {
          tested += data.tested;
          best = [...best, ...data.matches].sort(ranking).slice(0, 40);
          if (data.verify) review = best.map(m => m.indices);
        }
        setStatus({ phase: data.verify ? "verifying" : data.phase, completed: data.completed, total: data.total });
        cursor = data.next_cursor;
        plan = data.plan;
      }
    } catch (e) {
      if (!ctl.signal.aborted) setStatus({ phase: "error", stale: e.response?.status === 409 });
    }
  }
  const running = status && !["done", "error"].includes(status.phase);
  const label = (id, condition) => labels[id] + (condition ? " · " + condition : "");
  return <section aria-label="Whole catalog search" className="card min-w-0 space-y-3 p-4 sm:p-5">
    <h2 className="font-display text-xl font-bold uppercase">Patterns across all questionnaires</h2>
    <p className="text-sm text-[var(--muted)]">Search every eligible pair of variables across Individual, Couples and profile answers. Using {scope.toLowerCase()}.</p>
    {running ? <div className="flex flex-wrap items-center gap-3">
      <p role="status" className="text-sm">{status.phase === "screening" ? `Checking which answers have enough data · ${status.completed}/${status.total}`
        : status.phase === "comparing" ? `Comparing answers · ${status.completed}/${status.total} batches`
        : status.phase === "verifying" ? "Rechecking results against current participation…" : "Checking available data…"}</p>
      <button type="button" className="btn-ghost px-3 py-1 text-sm" onClick={() => { active.current?.abort(); setStatus(null); }}>Cancel catalog search</button>
    </div> : <button type="button" className="btn-lime px-4 py-2 text-sm" onClick={start}>
      {status ? "Search whole catalog again" : "Search whole catalog"}
    </button>}
    {status?.phase === "error" && <p role="alert" className="text-sm">{status.stale
      ? "This search expired or the questions changed. Start a new search." : "The catalog search couldn’t finish. Please try again."}</p>}
    {status?.phase === "done" && (status.matches.length ? <div className="space-y-2">
      <h3 className="font-semibold">Strongest observed associations across the catalog</h3>
      <p className="text-xs text-[var(--muted)]">Showing up to 40 results from {status.tested.toLocaleString()} candidate comparisons. These rankings describe participating {unit === "member" ? "members" : "couples"}. Searching many pairs increases the chance of coincidental associations.</p>
      <ol className="divide-y divide-[var(--line)]">{status.matches.map((m, i) =>
        <li className="py-2" key={m.indices.join("-")}><button type="button" className="w-full rounded-lg p-1 text-left text-sm hover:underline"
          onClick={() => onCompare({ x: m.x, y: m.y, x_condition: m.x_condition || undefined, y_condition: m.y_condition || undefined, unit })}>
          <span className="font-medium">{i + 1}. {label(m.x, m.x_condition)} × {label(m.y, m.y_condition)}</span>
          <span className="block text-xs text-[var(--muted)]">Cramér’s V {m.cramers_v.toFixed(3)} · About {m.sample_size.toLocaleString()} {unit === "member" ? "members" : "couples"}</span>
        </button></li>)}</ol>
    </div> : <p role="status" className="text-sm">No variable pairs met the reporting thresholds for this comparison.</p>)}
  </section>;
}
