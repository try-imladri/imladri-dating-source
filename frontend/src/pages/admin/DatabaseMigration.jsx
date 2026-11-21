import { useEffect, useRef, useState } from "react";

const endpoint = "/admin/database-migration";
const domains = { individual: "Individual", couples: "Couples", billing: "Billing & message credits", admin: "Admin", research: "Research" };
const historyDomains = { everyday: "Everyday answers", intimacy: "Sex & Intimacy", demographics: "Demographic observations", research: "Research submissions", couples: "Couples", consent: "Consent & preferences", assessment_repeat: "Questionnaire retakes", wording_feedback: "Intimacy wording feedback", profile: "Profile descriptions", person_intake: "Person intake observations", your_type: "Your Type actions & results", other: "Other history" };
const sources = { users: "Profiles & Everyday", intimacy_answers: "Sex & Intimacy", research_responses: "Research submissions", couples_drafts: "Couples private answers", couples_pairs: "Couples shared activity", people: "People & intake observations", your_type_sessions: "Your Type sessions" };
const isCount = value => Number.isSafeInteger(value) && value >= 0;
const count = value => isCount(value) ? value.toLocaleString() : "Not recorded";
const sum = (rows, key) => rows.every(row => isCount(row[key])) ? rows.reduce((n, row) => n + row[key], 0) : null;
const parity = value => value === true ? "Matched in snapshot" : value === false ? "Needs reconciliation" : "Not checked";
const reportLabel = value => value === "observed_parity_only" ? "Observed parity snapshot" : value === "needs_reconciliation" ? "Needs reconciliation" : "Report status unavailable";
const validReport = value => value?.version === 1 && ["mongo_authoritative", "postgres_authoritative"].includes(value.mode)
  && typeof value.postgres?.configured === "boolean" && value.domains && typeof value.domains === "object" && !Array.isArray(value.domains)
  && Array.isArray(value.history?.domains) && value.history.domains.every(row => row && typeof row.domain === "string"
    && [row.baseline, row.new_observations, row.total].every(isCount) && row.baseline + row.new_observations === row.total)
  && Array.isArray(value.history?.sources) && value.history.sources.every(row => row && typeof row.source === "string"
    && [row.queued_documents, row.pending_events, row.redactions_pending].every(isCount))
  && [value.history.pending_events, value.history.redactions_pending, value.history.database_tombstones].every(isCount)
  && Array.isArray(value.remaining_gates);

function Metric({ label, value }) {
  return <div className="rounded-xl bg-[var(--surface-2)] p-3"><dt className="text-xs text-[var(--muted)]">{label}</dt>
    <dd className="mt-1 text-2xl font-semibold tabular-nums">{count(value)}</dd></div>;
}

function RepairResult({ value }) {
  return <p className="text-sm" role="status">Last processing batch: {count(value.attempted)} source records checked · {count(value.archived)} observations archived · {count(value.redacted)} history records redacted · {count(value.failed)} failed.</p>;
}

export default function DatabaseMigration({ api }) {
  const [data, setData] = useState(null);
  const [phase, setPhase] = useState("idle");
  const [repairing, setRepairing] = useState(false);
  const [repairError, setRepairError] = useState(false);
  const [repairResult, setRepairResult] = useState(null);
  const reading = useRef(null);
  const processing = useRef(null);

  useEffect(() => {
    setData(null); setPhase("idle"); setRepairing(false); setRepairError(false); setRepairResult(null);
    return () => {
      reading.current?.abort(); processing.current?.abort();
      reading.current = null; processing.current = null;
    };
  }, [api]);

  async function load(keepRepair = false) {
    reading.current?.abort();
    const controller = new AbortController();
    reading.current = controller;
    setData(null); setPhase("loading");
    if (!keepRepair) { setRepairResult(null); setRepairError(false); }
    try {
      const { data: report } = await api.get(endpoint, { signal: controller.signal });
      if (controller.signal.aborted) return;
      if (!validReport(report)) throw new Error("Unsupported database status response");
      setData(report); setPhase("ready");
    } catch {
      if (!controller.signal.aborted) setPhase("error");
    } finally {
      if (reading.current === controller) reading.current = null;
    }
  }

  async function repair() {
    if (processing.current || phase !== "ready") return;
    const controller = new AbortController();
    processing.current = controller;
    setRepairing(true); setRepairError(false); setRepairResult(null);
    try {
      const response = await api.post(endpoint + "/repair", { limit: 50 }, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setRepairResult(response.data);
    } catch (error) {
      if (controller.signal.aborted) return;
      const partial = error.response?.data?.detail?.counts;
      if (partial && typeof partial === "object") setRepairResult(partial);
      setRepairError(true);
    } finally {
      if (!controller.signal.aborted) {
        await load(true);
        if (!controller.signal.aborted) setRepairing(false);
      }
      if (processing.current === controller) processing.current = null;
    }
  }

  const report = data?.latest_report;
  const postgresPrimary = data?.mode === "postgres_authoritative";
  const history = data?.history;
  const queued = history && ((history.pending_events > 0) || (history.redactions_pending > 0));
  const lastRepair = repairResult || data?.latest_repair;
  const gates = data ? [...new Set([...data.remaining_gates, ...(!postgresPrimary && Array.isArray(report?.remaining_gates) ? report.remaining_gates : [])])].filter(value => typeof value === "string") : [];
  const completed = typeof report?.completed_at === "string" ? new Date(report.completed_at) : null;

  return <section className="card space-y-4 p-4 sm:p-5" aria-label="Database migration and answer history">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-1">
        <h2 className="font-display text-xl font-bold uppercase">Answer history & PostgreSQL</h2>
        <p className="text-sm text-[var(--muted)]">Check the active application database, retained answer history and saved migration evidence here.</p>
      </div>
      <button type="button" className="btn-ghost px-3 py-1.5 text-sm disabled:opacity-50" disabled={phase === "loading" || repairing}
        onClick={() => load()}>{phase === "error" ? "Retry database status" : data ? "Refresh database status" : "Load database status"}</button>
    </div>
    {phase === "idle" && <p className="text-sm text-[var(--muted)]">Load the current counts and latest saved reconciliation report. No database switch is available on this page.</p>}
    {phase === "loading" && <p className="text-sm" role="status">Loading database status…</p>}
    {phase === "error" && <p className="text-sm" role="alert">Database status couldn’t load. Retry to check the current state.</p>}
    {repairError && <p className="text-sm" role="alert">Some queued history could not be processed. The counts have been requested again; retry the bounded batch after checking the current status.</p>}
    {lastRepair && <RepairResult value={lastRepair} />}
    {phase === "ready" && <div className="space-y-4">
      <dl className="grid gap-3 text-sm sm:grid-cols-3">
        <div className="rounded-xl border border-[var(--line)] p-3"><dt className="text-xs text-[var(--muted)]">Live application database</dt><dd className="mt-1 font-semibold">{postgresPrimary ? "Render PostgreSQL" : "MongoDB"}</dd></div>
        <div className="rounded-xl border border-[var(--line)] p-3"><dt className="text-xs text-[var(--muted)]">PostgreSQL connection setting</dt><dd className="mt-1 font-semibold">{postgresPrimary && data.postgres.connected ? "Verified by this request" : data.postgres.configured ? "Configured · connection not verified" : "Not configured"}</dd></div>
        <div className="rounded-xl border border-[var(--line)] p-3"><dt className="text-xs text-[var(--muted)]">Application data source</dt><dd className="mt-1 font-semibold">{postgresPrimary ? "PostgreSQL active" : "Migration in progress"}</dd></div>
      </dl>
      <p className="text-xs text-[var(--muted)]">{postgresPrimary ? "These current history counts were read from PostgreSQL. The saved reconciliation report below is historical migration evidence." : "This status request does not test a live PostgreSQL connection. A saved report records one observed snapshot; matching records do not establish ongoing synchronization or completed customer-flow testing."}</p>
      <div className="space-y-2">
        <h3 className="font-semibold">Latest migration report</h3>
        {report ? <div className="space-y-1 text-sm">
          <p className="font-medium">{reportLabel(report.status)}</p>
          {completed && !Number.isNaN(completed.getTime()) && <p className="text-xs text-[var(--muted)]">Completed <time dateTime={report.completed_at}>{completed.toLocaleString()}</time>.</p>}
          {report.unknown_collections?.length > 0 && <p>{count(report.unknown_collections.length)} collections still need a domain mapping.</p>}
        </div> : <p className="text-sm text-[var(--muted)]">No reconciliation report has been recorded for this environment. Coverage is not checked yet.</p>}
        <div className="overflow-x-auto"><table className="w-full text-left text-sm">
          <caption className="sr-only">Recorded MongoDB to PostgreSQL coverage</caption>
          <thead><tr><th scope="col" className="py-2 pr-3">App area</th><th scope="col" className="pr-3">MongoDB records</th><th scope="col" className="pr-3">PostgreSQL records</th><th scope="col">Snapshot comparison</th></tr></thead>
          <tbody>{Object.entries(domains).map(([key, label]) => <tr key={key} className="border-t border-[var(--line)]">
            <th scope="row" className="py-2 pr-3 font-normal">{label}</th><td className="pr-3 tabular-nums">{count(data.domains[key]?.source_records)}</td>
            <td className="pr-3 tabular-nums">{count(data.domains[key]?.target_records)}</td><td>{parity(data.domains[key]?.parity)}</td>
          </tr>)}</tbody>
        </table></div>
      </div>
      <div className="space-y-3 border-t border-[var(--line)] pt-4">
        <h3 className="font-semibold">Retained answer history</h3>
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Metric label="Baseline observations" value={sum(history.domains, "baseline")} />
          <Metric label="New history observations" value={sum(history.domains, "new_observations")} />
          <Metric label="Pending history events" value={history.pending_events} />
          <Metric label="Pending erasures" value={history.redactions_pending} />
        </dl>
        <p className="text-xs text-[var(--muted)]">Baselines describe retained data when tracking began; they are not reconstructed earlier attempts. New observations record accepted changes from tracking onward. These are observations, not unique people or completed questionnaires.</p>
        {history.domains.length ? <div className="overflow-x-auto"><table className="w-full text-left text-sm">
          <caption className="sr-only">History observations by area</caption>
          <thead><tr><th scope="col" className="py-2 pr-3">History area</th><th scope="col" className="pr-3">Baseline</th><th scope="col" className="pr-3">New observations</th><th scope="col">Total</th></tr></thead>
          <tbody>{history.domains.map(row => <tr key={row.domain} className="border-t border-[var(--line)]">
            <th scope="row" className="py-2 pr-3 font-normal">{historyDomains[row.domain] || "Other history"}</th>
            <td className="pr-3 tabular-nums">{count(row.baseline)}</td><td className="pr-3 tabular-nums">{count(row.new_observations)}</td><td className="tabular-nums">{count(row.total)}</td>
          </tr>)}</tbody>
        </table></div> : <p className="text-sm text-[var(--muted)]">No archived history observations have been recorded in this environment yet.</p>}
        <p className="text-xs text-[var(--muted)]">{count(history.database_tombstones)} database-wide erasure fences are retained separately. They contain no answer payload or owner and are not research observations.</p>
        <details className="space-y-2 text-sm"><summary className="cursor-pointer font-medium">Pending records by source</summary>
          <div className="overflow-x-auto"><table className="mt-2 w-full text-left">
            <caption className="sr-only">Answer history processing backlog</caption>
            <thead><tr><th scope="col" className="py-2 pr-3">Source</th><th scope="col" className="pr-3">Queued records</th><th scope="col" className="pr-3">Events</th><th scope="col">Erasures</th></tr></thead>
            <tbody>{history.sources.map(row => <tr key={row.source} className="border-t border-[var(--line)]">
              <th scope="row" className="py-2 pr-3 font-normal">{sources[row.source] || "Other source"}</th>
              <td className="pr-3 tabular-nums">{count(row.queued_documents)}</td><td className="pr-3 tabular-nums">{count(row.pending_events)}</td><td className="tabular-nums">{count(row.redactions_pending)}</td>
            </tr>)}</tbody>
          </table></div>
        </details>
        {queued && <div className="space-y-2">
          <button type="button" className="btn-ghost px-3 py-1.5 text-sm disabled:opacity-50" disabled={repairing} onClick={repair}>
            {repairing ? "Processing pending history…" : "Process up to 50 pending records"}
          </button>
          <p className="text-xs text-[var(--muted)]">Archives already saved history and applies queued erasures in a bounded batch. This does not copy data to PostgreSQL or change billing.</p>
        </div>}
      </div>
      {gates.length > 0 && <div className="space-y-2 border-t border-[var(--line)] pt-4 text-sm">
        <h3 className="font-semibold">Remaining migration checks</h3>
        <ul className="list-disc space-y-1 pl-5 text-[var(--muted)]">{gates.map(gate => <li key={gate}>{gate}</li>)}</ul>
      </div>}
    </div>}
  </section>;
}
