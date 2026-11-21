import { useEffect, useRef, useState } from "react";
import ResearchDiscovery from "./ResearchDiscovery";
import CatalogDiscovery from "./CatalogDiscovery";
import { deferredPage, preloadRoute } from "@/lib/routePages";
import { researchTool, researchTools } from "@/lib/adminResearchNavigation";
import { SubTabs } from "./SubTabs";

import VariablePicker, { groupNames } from "./ResearchVariablePicker";

const ResearchNoticeSummary = deferredPage("researchConsent");
const ResearchRecordExports = deferredPage("researchExports");
const DatabaseMigration = deferredPage("researchMigration");
const YourTypeResearch = deferredPage("researchYourType");
const ToneResearch = deferredPage("researchTone");
const AssessmentEvidence = deferredPage("researchEvidence");
const IndependentReview = deferredPage("researchIndependent");
const RehearsalReview = deferredPage("researchRehearsal");
const ProspectiveResearch = deferredPage("researchProspective");

const scopes = { member: "Same person", couple: "Partners · either direction", couple_inviter: "Partners · inviter answers rows", couple_invitee: "Partners · invitee answers rows" };
function scopeLabel(data) {
  if (!data.scope_label) return scopes[data.unit || "member"];
  return data.scope_label + (data.incoming ? ` · incoming tone ${data.incoming}` : "");
}

function axisLabel(data, axis) {
  const label = data.variables.find(variable => variable.id === data[axis])?.label || data[axis];
  const condition = data[axis + "_condition"];
  return condition ? label + " — " + condition + " (among ‘It depends’ answers)" : label;
}
const percent = (count, total) => Math.round(100 * count / total);

function ResearchCoverage({ data, api }) {
  const [key, setKey] = useState(data.variables[0].id);
  const [condition, setCondition] = useState();
  const [result, setResult] = useState(null);
  const [status, setStatus] = useState("ready");
  const active = useRef(null);
  useEffect(() => () => active.current?.abort(), []);
  const counts = data.variables.reduce((total, variable) => {
    total[variable.domain] = (total[variable.domain] || 0) + 1;
    return total;
  }, {});
  function clear() {
    active.current?.abort();
    setResult(null);
    setStatus("ready");
  }
  async function load(event) {
    event.preventDefault();
    clear();
    const controller = new AbortController();
    active.current = controller;
    setStatus("loading");
    try {
      const { data: report } = await api.get("/admin/research/distribution", { params: { key, condition }, signal: controller.signal });
      if (controller.signal.aborted) return;
      setResult(report);
      setStatus("ready");
    } catch {
      if (!controller.signal.aborted) setStatus("error");
    }
  }
  return <section className="card space-y-4 p-4 sm:p-5" aria-label="Research coverage">
    <details>
      <summary className="cursor-pointer font-display text-xl font-bold uppercase">Research coverage · {data.variables.length} variables</summary>
      <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">{Object.entries(groupNames).filter(([domain]) => counts[domain]).map(([domain, name]) =>
        <div key={domain} className="flex justify-between gap-2 border-b border-[var(--line)] py-1"><dt>{name}</dt><dd className="font-semibold">{counts[domain]}</dd></div>)}</dl>
      <div className="mt-3 space-y-2 text-sm text-[var(--muted)]">
        <p>These counts describe the published questionnaire. Ongoing participants contribute their currently saved choices, including partial questionnaires. Edits update the current choice used here; retained history is counted separately below. Older one-time participants contribute only what they submitted.</p>
        <p>The profile covers age, height, sex, gender, ethnicity, education and self-reported personality type. Age and height appear in ranges. Intimacy conditions apply only when someone chose “It depends.”</p>
        <p>Archived Couples starters keep the exact wording and choices from their publication. Each version is a separate variable. Couples starters use the author’s selected option. Check-ins use each partner’s latest recorded outcome in an active couple. Equally recent, conflicting outcomes are excluded. Linked comparisons require both partners to participate.</p>
        <p>Skipped, unanswered, withheld and incompatible answers stay outside the denominator. The report also excludes unsubmitted private sessions, written reflections, notes, third-party profiles and inferred personality types. <a className="underline" href="/admin/activity">Activity</a> tracks usage and follow-ups.</p>
      </div>
    </details>
    <details className="border-t border-[var(--line)] pt-3">
      <summary className="cursor-pointer font-display text-xl font-bold uppercase">Single-question results</summary>
      <form onSubmit={load} className="mt-3 space-y-3">
        <p className="text-sm text-[var(--muted)]">See how members answered one question. Each eligible respondent counts once, including people who stopped partway through.</p>
        <VariablePicker label="Question or profile field" value={key} variables={data.variables} conditions={data.conditions}
          onChange={next => { clear(); setKey(next); setCondition(undefined); }} condition={condition}
          onCondition={next => { clear(); setCondition(next); }} />
        <button type="submit" disabled={status === "loading"} className="btn-ghost px-4 py-2 text-sm disabled:opacity-50">Show answer counts</button>
      </form>
      <div className="mt-3 text-sm" aria-live="polite">
        {status === "loading" && <p>Loading answer counts…</p>}
        {status === "error" && <p>Answer counts couldn’t load. Select Show answer counts to retry.</p>}
        {result && (result.available ? <>
          <table className="w-full text-left">
            <caption className="pb-2 text-left font-semibold">Approximately {result.sample_size} members supplied this {condition ? "condition measure" : "answer"}</caption>
            <thead><tr><th scope="col">Answer</th><th scope="col">Approx. members</th><th scope="col">Share</th></tr></thead>
            <tbody>{result.rows.map(row => <tr key={row.label} className="border-t border-[var(--line)]">
              <th scope="row" className="py-1 font-normal">{row.label}</th><td>{row.count}</td><td>{percent(row.count, result.sample_size)}%</td></tr>)}</tbody>
          </table>
          <p className="mt-2 text-xs text-[var(--muted)]">Counts are rounded to the nearest {result.round_to}. Percentages use the rounded total.</p>
        </> : <p>Answer counts withheld. A distribution needs at least {result.minimum} respondents, at least two selected categories, and at least {result.minimum_cell} respondents in each occupied category.</p>)}
      </div>
    </details>
  </section>;
}

function csvCell(value) {
  const text = String(value);
  const safe = typeof value !== "number" && /^[\t\r\n]|^[\s\uFEFF]*[=+@-]/.test(text) ? "'" + text : text;
  return '"' + safe.replace(/"/g, '""') + '"';
}

export function comparisonCsv(data) {
  if (!data.available) return "";
  const linked = data.unit && data.unit !== "member";
  const lines = [["Comparison", "Row variable", "Column variable", "Row category", "Column category", linked ? "Approximate couples" : "Approximate members",
    "Percent of row", "Approximate paired sample", "Exploratory Cramér’s V",
    ...(data.pearson ? ["Pearson r (exact measurements)", "Approximate numeric sample"] : []),
    ...(data.observed_at ? ["Row variable ID","Column variable ID","Current responses as of (UTC)"] : []),
    ...(data.instrument ? ["Measure edition","Comparison unit","Row condition","Column condition","Incoming tone filter"] : [])]];
  for (const row of data.rows) {
    data.columns.forEach((column, i) => lines.push([scopeLabel(data), axisLabel(data, "x"), axisLabel(data, "y"),
      data.x_choices[row.index], data.y_choices[column], row.cells[i], percent(row.cells[i], row.total),
      data.sample_size, data.cramers_v, ...(data.pearson ? [data.pearson.r, data.pearson.sample_size] : []), ...(data.observed_at ? [data.x,data.y,data.observed_at] : []),
      ...(data.instrument ? [data.instrument, data.unit, data.x_condition || "", data.y_condition || "", data.incoming || ""] : [])]));
  }
  return lines.map(line => line.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

function Comparison({ data }) {
  const [downloadError, setDownloadError] = useState(false);
  function download() {
    setDownloadError(false);
    try {
      const url = URL.createObjectURL(new Blob(["\uFEFF", comparisonCsv(data)], { type: "text/csv;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "imladri-research-comparison.csv";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setDownloadError(true);
    }
  }
  const noun = data.unit && data.unit !== "member" ? "couples" : "members";
  if (!data.available) return <section className="card p-4 sm:p-5 space-y-2" role="status">
    <h2 className="font-display text-xl font-bold uppercase">Comparison withheld</h2>
    <p className="text-sm font-medium">{scopeLabel(data)} · {axisLabel(data, "x")} × {axisLabel(data, "y")}</p>
    <p className="text-sm text-[var(--muted)]">Results appear once at least {data.minimum} eligible {noun} have supplied both values, at least two categories appear on each side, and every occupied cell has a count of at least {data.minimum_cell} {noun}.</p>
  </section>;
  return <section className="card min-w-0 space-y-4 p-4 sm:p-5" aria-label="Comparison results">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 className="font-display text-xl font-bold uppercase">Comparison results</h2>
      {data.observed_at && <p className="text-xs text-[var(--muted)]">Currently retained responses as of {new Date(data.observed_at).toLocaleString()}. {data.instrument && <>Measure edition {data.instrument.slice(0, 12)}. </>}The CSV includes both variable IDs.</p>}
      <button type="button" className="btn-ghost px-4 py-2 text-sm" onClick={download}>Download comparison (CSV)</button>
    </div>
    {downloadError && <p role="alert" className="text-sm">The download failed. Please try again.</p>}
    <dl className="grid grid-cols-2 gap-3 text-sm">
      <div className="rounded-xl bg-[var(--surface-2)] p-3"><dt>Approximate {noun} with both answers</dt><dd className="text-2xl font-semibold tabular-nums">{data.sample_size.toLocaleString()}</dd></div>
      <div className="rounded-xl bg-[var(--surface-2)] p-3"><dt>Exploratory Cramér’s V</dt><dd className="text-2xl font-semibold tabular-nums">{data.cramers_v.toFixed(3)}</dd></div>
    </dl>
    <p className="text-sm text-[var(--muted)]">Cramér’s V ranges from 0 to 1. Higher values mean the two answers vary together more strongly among these contributors. Self-selection and chance can affect the result.</p>
    {data.pearson && <div className="space-y-1 rounded-xl bg-[var(--surface-2)] p-3 text-sm">
      <p className="font-semibold">Pearson correlation for exact measurements <span className="tabular-nums">{data.pearson.r.toFixed(3)}</span></p>
      <p>Based on exact values from approximately {data.pearson.sample_size.toLocaleString()} {noun}. A positive value means the two measurements tend to rise together. A negative value means one tends to fall as the other rises.</p>
    </div>}
    <div className="max-w-full overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="pb-2 text-left font-semibold">{data.scope_label || (data.unit && data.unit !== "member") ? scopeLabel(data) + " · " : ""}{axisLabel(data, "x")} × {axisLabel(data, "y")}</caption>
        <thead><tr><th scope="col" className="p-2">{axisLabel(data, "x")}</th>
          {data.columns.map(i => <th key={i} scope="col" className="p-2">{data.y_choices[i]}</th>)}
        </tr></thead>
        <tbody>{data.rows.map(row => <tr key={row.index} className="border-t border-[var(--line)]">
          <th scope="row" className="p-2 font-medium">{data.x_choices[row.index]}</th>
          {row.cells.map((count, i) => <td key={data.columns[i]} className="whitespace-nowrap p-2 tabular-nums">
            {count.toLocaleString()} <span className="text-[var(--muted)]">({percent(count, row.total)}%)</span>
          </td>)}
        </tr>)}</tbody>
      </table>
    </div>
    <p className="text-xs text-[var(--muted)]">Cell counts are rounded to the nearest {data.round_to}. The table’s sample size, row percentages and Cramér’s V use those rounded counts.</p>
  </section>;
}

export default function ResearchTrends({ api, initialUnit, tool, onToolChange }) {
  const [localTool, setLocalTool] = useState("comparisons");
  const selected = researchTool(tool ?? localTool);
  return <div className="min-h-0 space-y-4 overflow-y-auto pb-3" data-testid="research-trends">
    <SubTabs items={researchTools} value={selected} onChange={onToolChange || setLocalTool}
      onIntent={value => { void preloadRoute("/admin/research?tool=" + value); }} testid="research-tools" />
    {["comparisons", "prospective"].includes(selected) && <QuestionnaireComparisons key={selected} api={api}
      initialUnit={initialUnit} prospective={selected === "prospective"} />}
    {selected === "consent" && <ResearchNoticeSummary api={api} />}
    {selected === "exports" && <ResearchRecordExports api={api} />}
    {selected === "migration" && <DatabaseMigration api={api} />}
    {selected === "your-type" && <YourTypeResearch api={api} Comparison={Comparison} />}
    {selected === "tone" && <ToneResearch api={api} Comparison={Comparison} />}
    {selected === "evidence" && <AssessmentEvidence api={api} />}
    {selected === "independent" && <IndependentReview api={api} />}
    {selected === "rehearsal" && <RehearsalReview api={api} />}
  </div>;
}

function QuestionnaireComparisons({ api, initialUnit, prospective }) {
  const [request, setRequest] = useState(() => Object.hasOwn(scopes, initialUnit) ? { unit: initialUnit } : {});
  const [draft, setDraft] = useState(null);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState(false);
  const resultPanel = useRef(null);
  const focusResult = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError(false);
    api.get("/admin/research/trends", { params: request, signal: controller.signal })
      .then(({ data: report }) => {
        if (controller.signal.aborted) return;
        setData(report);
        setDraft(previous => previous || { x: report.x, y: report.y,
          unit: report.unit || "member", x_condition: report.x_condition || undefined, y_condition: report.y_condition || undefined });
        setBusy(false);
      }).catch(() => {
        if (controller.signal.aborted) return;
        setError(true);
        setBusy(false);
      });
    return () => controller.abort();
  }, [api, request]);

  useEffect(() => {
    if (!busy && focusResult.current) {
      focusResult.current = false;
      resultPanel.current?.focus();
    }
  }, [busy]);

  const intimacy = data?.variables.filter(variable => variable.domain === "intimacy") || [];
  const everyday = data?.variables.find(variable => variable.domain === "everyday")?.id;
  const couples = data?.variables.find(variable => variable.id.startsWith("c:prompt:"))?.id;
  const heights = data?.variables.find(variable => variable.id === "height_band")?.id;
  const presets = [
    ["Age and intimacy", "age_band", intimacy[0]?.id],
    ["Everyday and intimacy", everyday, intimacy[0]?.id],
    ["Two intimacy questions", intimacy[0]?.id, intimacy[1]?.id],
    ["Age and height", "age_band", heights],
    ["Personal and Couples", everyday, couples],
    ["Both partners’ answers", couples, couples, "couple"],
  ].filter(([, x, y]) => x && y);
  const comparable = draft && (draft.unit !== "member" || draft.x !== draft.y ||
    (draft.x_condition && draft.y_condition && draft.x_condition !== draft.y_condition));
  return <div className="space-y-4">
    <section className="card space-y-4 p-4 sm:p-5">
      <div className="space-y-1">
        <h2 className="font-display text-xl font-bold uppercase">Compare questionnaire answers</h2>
        <p className="text-sm text-[var(--muted)]">Compare saved answers from adults who participate in research. Partial questionnaires count whenever both selected answers are available. Skips, withheld profile fields and answers with incompatible wording are excluded.</p>
      </div>
      {data && draft && <>
        <div className="flex flex-wrap gap-2" aria-label="Suggested comparisons">{presets.map(([title, x, y, scope]) =>
          <button type="button" className="btn-ghost px-3 py-1 text-sm" key={title}
            onClick={() => { setDraft({ x, y, unit: scope || "member" }); setRequest(scope ? { x, y, unit: scope } : { x, y }); }}>{title}</button>)}</div>
        <form className="space-y-3" onSubmit={e => { e.preventDefault(); if (comparable) setRequest({ ...draft }); }}>
          <label className="block space-y-1 text-sm font-semibold">Compare answers from
            <select value={draft.unit} onChange={e => setDraft({ ...draft, unit: e.target.value })}
              className="block w-full rounded-lg border border-[var(--line)] bg-[var(--surface)] p-2 font-normal sm:w-auto">
              {Object.entries(scopes).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select>
          </label>
          <p className="text-xs text-[var(--muted)]">{draft.unit === "member"
            ? "One person supplies both answers. Each person counts once. Earlier one-time submissions contribute only the answers shared at the time."
            : draft.unit === "couple"
              ? "Both partners must participate. Each active couple counts once. If both directions have answers, each direction gets half the weight."
              : "Both partners must participate. Each active couple counts once. The other partner supplies the column answer."}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            {["x", "y"].map((axis, i) => <VariablePicker key={axis} label={i === 0 ? "Rows" : "Columns"}
              value={draft[axis]} variables={data.variables} conditions={data.conditions}
              onChange={value => setDraft({ ...draft, [axis]: value, [axis + "_condition"]: undefined })}
              condition={draft[axis + "_condition"]} onCondition={value => setDraft({ ...draft, [axis + "_condition"]: value })} />)}
          </div>
          {(draft.x_condition || draft.y_condition) && <p className="text-xs text-[var(--muted)]">Each condition compares people who selected that tag with people who left it unselected. Only “It depends” answers to the relevant question qualify.</p>}
          {!comparable && <p className="text-sm text-[var(--muted)]">Choose two different variables or two different conditions on the same question.</p>}
          <button type="submit" disabled={!comparable || busy}
            className="btn-lime px-4 py-2 text-sm disabled:opacity-50">Compare</button>
        </form>
      </>}
    </section>
    {prospective && <ProspectiveResearch api={api} comparison={busy || error ? null : data} />}
    {data && <ResearchCoverage data={data} api={api} />}
    {data && draft && <CatalogDiscovery key={draft.unit} api={api} unit={draft.unit} scope={scopes[draft.unit]}
      labels={Object.fromEntries(data.variables.map(v => [v.id, v.label]))}
      onCompare={selection => { focusResult.current = true; setDraft(selection); setRequest(selection); }} />}
    <div ref={resultPanel} tabIndex={-1}>
    {busy ? <p role="status" className="text-sm text-[var(--muted)]">Loading comparison…</p> :
      error ? <p role="alert" className="text-sm">The comparison couldn’t load. <button type="button" className="underline" onClick={() => setRequest({ ...request })}>Retry</button></p> :
      data && <Comparison key={data.unit + "|" + data.x + "|" + data.y} data={data} />}
    </div>
    {data && draft && <ResearchDiscovery key={JSON.stringify([draft.x, draft.x_condition, draft.unit])} api={api}
      focus={draft} labels={Object.fromEntries(data.variables.map(v => [v.id, v.label]))}
      onCompare={match => {
        const selection = { ...draft, y: match.y, y_condition: match.y_condition || undefined };
        focusResult.current = true;
        setDraft(selection);
        setRequest(selection);
      }} />}
  </div>;
}
