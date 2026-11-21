import { useState } from "react";

const views = {member:"Individual", couple:"Both partners", couple_inviter:"Inviter to partner", couple_invitee:"Partner to inviter"};

function CohortPatterns({ patterns, rowLabel, columnLabel }) {
  function cell(value) {
    return value ? `${value.count} of ${value.total} (${Math.round(100 * value.count / value.total)}%)` : "No respondents in this row";
  }
  return <details className="pt-1">
    <summary className="cursor-pointer underline">Compare the answer patterns</summary>
    <p className="my-2">Two cohorts can have the same Cramér’s V even when their answers move in opposite directions. Each percentage uses the respondents in that row of its cohort. Counts are rounded.</p>
    <div className="overflow-x-auto"><table className="w-full text-left text-sm">
      <caption className="sr-only">Answer combinations in the baseline and future cohorts</caption>
      <thead><tr>{[rowLabel, columnLabel, "Baseline", "Future cohort"].map((label, i) => <th key={i} scope="col" className="p-2">{label}</th>)}</tr></thead>
      <tbody>{patterns.map((p, i) => <tr key={i} className="border-t border-[var(--line)]"><th className="p-2" scope="row">{p.row}</th>
        <td className="p-2">{p.column}</td><td className="p-2 tabular-nums">{cell(p.baseline)}</td><td className="p-2 tabular-nums">{cell(p.future)}</td></tr>)}</tbody>
    </table></div>
  </details>;
}

export default function ProspectiveResearch({ api, comparison }) {
  const [page, setPage] = useState(null);
  const [results, setResults] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(action) {
    setBusy(true); setError("");
    try { await action(); } catch (e) {
      setError(typeof e.response?.data?.detail === "string" ? e.response.data.detail : "The comparison could not be loaded. Try again.");
    } finally { setBusy(false); }
  }
  async function load(after) {
    const response = await api.get("/admin/research/registrations", {params: after ? {after} : {}});
    if (!after) setResults({});
    setPage(previous => ({...response.data, items:after ? [...previous.items,...response.data.items] : response.data.items}));
  }
  async function register() {
    const {unit="member", x, y, x_condition, y_condition} = comparison;
    const {data} = await api.post("/admin/research/registrations",{unit,x,y,x_condition,y_condition,
      ...(comparison.instrument ? {expected_instrument: comparison.instrument} : {})});
    await load();
    setPage(previous=>({...previous,items:[data,...previous.items.filter(row=>row.id!==data.id)]}));
  }
  async function calculate(id) {
    const {data} = await api.get("/admin/research/registrations/"+id);
    setResults(previous=>({...previous,[id]:data}));
  }
  return <section className="card space-y-3 p-4 sm:p-5">
    <h2 className="font-display text-xl font-bold uppercase">Prospective comparisons</h2>
    <p className="text-sm">Register a reportable comparison to check the same measures in future accounts. The registration fixes the measures and cutoff date. Each check uses current answers and permissions in both groups. Both members of a new couple must qualify.</p>
    <button type="button" disabled={busy} className="btn-ghost px-3 py-1.5 text-sm" onClick={()=>run(()=>load())}>{page ? "Refresh registrations" : "Open registered comparisons"}</button>
    {error && <p role="alert" className="text-sm">{error}</p>}
    {page && <>
      <p className="text-sm">{page.coverage.available ? `Approximately ${page.coverage.participating} of ${page.coverage.eligible} current eligible member accounts participate in ongoing questionnaire research.` : "Participation counts are withheld below the reporting thresholds."} Eligibility uses the recorded age where available. Research enrollment requires adult confirmation.</p>
      {comparison?.available && <button className="btn-lime px-3 py-2 text-sm" disabled={busy} onClick={()=>run(register)}>Register the displayed comparison</button>}
      {!page.items.length && <p className="text-sm text-[var(--muted)]">There are no registrations yet.</p>}
      <ul className="divide-y divide-[var(--line)]">{page.items.map(row=>{
        const result = results[row.id];
        const label = axis => row.labels[axis]+(row.selection[axis+"_condition"] ? " · "+row.selection[axis+"_condition"] : "");
        return <li key={row.id} className="py-3 space-y-1 text-sm">
          <h3 className="font-semibold">{label("x")} × {label("y")}</h3>
          <p>{views[row.selection.unit]} · Registered {new Date(row.registered_at).toUTCString()} · Instrument {row.instrument.slice(0,12)}</p>
          {result?.baseline && <p>Baseline Cramér’s V {result.baseline.cramers_v} · approximately {result.baseline.sample_size} observations.</p>}
          {result && result.state !== "changed" && !result.baseline && <p>The baseline currently falls below the reporting thresholds.</p>}
          <button type="button" className="underline" disabled={busy} onClick={()=>run(()=>calculate(row.id))}>Check future cohort</button>
          {result && <p role="status">{result.state==="changed" ? "The instrument changed. A new registration is required." : result.state==="available" ? `Future cohort Cramér’s V ${result.cramers_v}, approximately ${result.sample_size} observations. Interpret this descriptive comparison alongside the baseline and possible selection effects.` : "Collecting new eligible observations. A result appears after the sample and each represented cell meet the reporting thresholds."}</p>}
          {result?.patterns && <CohortPatterns patterns={result.patterns} rowLabel={label("x")} columnLabel={label("y")} />}
        </li>;
      })}</ul>
      {page.next && <button className="underline text-sm" disabled={busy} onClick={()=>run(()=>load(page.next))}>More registrations</button>}
    </>}
  </section>;
}
