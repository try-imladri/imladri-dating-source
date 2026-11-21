import { sectionNames as names } from "@/lib/personalQuestionnaires";
import { useEffect, useState } from "react";
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, Cell } from "recharts";
import { SubTabs } from "@/pages/admin/SubTabs";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import Stat from "./Stat";
import WaitlistSignups from "./WaitlistSignups";
import OperationsStatus from "./OperationsStatus";

const tip = { contentStyle: { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, fontSize: 12, color: "var(--ink)" }, cursor: { fill: "var(--surface-2)" } };
const axis = { tick: { fill: "var(--muted)", fontSize: 11 }, axisLine: false, tickLine: false };

function IndividualQuizStat({ quiz }) {
  const sections = quiz?.sections;
  if (!sections) return <Stat label="Individual quiz" value="—" sub="Section counts unavailable" testid="metric-quiz" />;
  const formatCount = (count) => quiz.users ? `${count} (${Math.round(count * 100 / quiz.users)}%)` : "0";
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button type="button" className="card px-4 py-3 min-w-0 text-left" data-testid="metric-quiz">
          <span className="block font-mono text-[10px] uppercase tracking-widest text-[var(--muted)]">Basics completed</span>
          <span className="block font-display font-black text-3xl leading-none mt-1.5">{quiz.users && sections.short.questions ? `${Math.round(sections.short.complete * 100 / quiz.users)}%` : "—"}</span>
          <span className="block text-[11px] mt-1 underline">{sections.short.started ?? "—"} started · {quiz.users} current members · View quiz progress</span>
        </button>
      </DialogTrigger>
      <DialogContent className="w-11/12 sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Individual quiz progress</DialogTitle>
          <DialogDescription>Current saved answers across {quiz.users} eligible {quiz.users === 1 ? "member" : "members"}. The first saved answer counts as started. Both Personal and Quick Question count. Halfway includes members who completed the section.</DialogDescription>
        </DialogHeader>
        <table className="w-full text-sm">
          <thead><tr className="text-left"><th scope="col">Section</th><th scope="col">Started</th><th scope="col">At least halfway</th><th scope="col">Complete</th></tr></thead>
          <tbody>
            {Object.entries(names).map(([key, name]) => {
              const row = sections[key];
              return <tr key={key} className="border-t border-[var(--line)]">
                <th scope="row" className="py-3 pr-2 text-left font-medium">{name}</th>
                <td className="py-3 pr-2">{row.questions && row.started != null ? formatCount(row.started) : "—"}</td>
                <td className="py-3 pr-2">{row.questions ? <>{formatCount(row.halfway)}<small className="block text-[var(--muted)]">{row.halfway_at} answers</small></> : "—"}</td>
                <td className="py-3">{row.questions ? <>{formatCount(row.complete)}<small className="block text-[var(--muted)]">{row.questions} answers</small></> : "—"}</td>
              </tr>;
            })}
          </tbody>
        </table>
      </DialogContent>
    </Dialog>
  );
}

function ChartCard({ title, children, testid }) {
  return (
    <div className="card p-4 flex flex-col min-h-[220px] lg:min-h-0" data-testid={testid}>
      <p className="font-display font-black uppercase text-base mb-2">{title}</p>
      <div className="flex-1 min-h-[160px] lg:min-h-0">{children}</div>
    </div>
  );
}

function HBars({ data, testid }) {
  if (!data.length) return <p className="text-xs text-[var(--muted)]" data-testid={`${testid}-empty`}>No data yet</p>;
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} layout="vertical" margin={{ left: 10, right: 16 }}>
        <XAxis type="number" {...axis} allowDecimals={false} />
        <YAxis type="category" dataKey="name" {...axis} width={110} />
        <Tooltip {...tip} />
        <Bar dataKey="value" fill="var(--ink)" radius={[0, 6, 6, 0]} barSize={12} />
      </BarChart>
    </ResponsiveContainer>
  );
}

const STEP_NAMES = { 1: "1 · Started onboarding", 2: "2 · Profile questions", 3: "3 · Person added", 4: "4 · First read", 5: "5 · Report ready" };
const toRows = (o) => Object.entries(o || {}).map(([name, value]) => ({ name: name.replace(/_/g, " "), value }));

function SignupHistory({ entries, asOf }) {
  const [days, setDays] = useState(30);
  const end = (asOf ? new Date(asOf) : new Date()).toISOString().slice(0, 10);
  const midnight = Date.parse(end + "T00:00:00Z");
  const counts = new Map(entries.map(row => [row.day, row.count]));
  const daily = Array.from({ length: days }, (_, i) => {
    const day = new Date(midnight - (days - 1 - i) * 86400000).toISOString().slice(0, 10);
    return {day, count: counts.get(day) || 0};
  });
  const total = daily.reduce((n, row) => n + row.count, 0);
  return <div className="h-full overflow-auto space-y-3">
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <label>Period <select value={days} onChange={e => setDays(Number(e.target.value))} className="rounded border border-[var(--line)] bg-[var(--surface)] p-1">
        {[7, 30, 90].map(n => <option key={n} value={n}>Last {n} UTC days</option>)}</select></label>
      <span>{total} {total === 1 ? "signup" : "signups"} through {end} UTC</span>
    </div>
    <div className="h-40" aria-hidden="true"><ResponsiveContainer width="100%" height="100%">
      <BarChart data={daily}>
        <CartesianGrid vertical={false} stroke="var(--line)" />
        <XAxis dataKey="day" {...axis} tickFormatter={v => v.slice(5)} minTickGap={20} />
        <YAxis {...axis} allowDecimals={false} width={30} /><Tooltip {...tip} />
        <Bar dataKey="count" name="Signups" fill="var(--ink)" />
      </BarChart>
    </ResponsiveContainer></div>
    <details className="text-sm"><summary className="cursor-pointer underline">Daily signup counts</summary>
      <table className="w-full text-left"><caption className="sr-only">Signups by UTC date</caption>
        <thead><tr><th scope="col">Date (UTC)</th><th scope="col">Signups</th></tr></thead>
        <tbody>{daily.map(row => <tr key={row.day}><th scope="row" className="font-normal">{row.day}</th><td>{row.count}</td></tr>)}</tbody>
      </table>
    </details>
    <details className="text-sm"><summary className="cursor-pointer underline">All recorded signup dates</summary>
      {entries.length ? <ul>{entries.map(row => <li key={row.day}>{row.day} UTC · {row.count}</li>)}</ul> : <p>No dated signups recorded.</p>}
    </details>
  </div>;
}

function Growth({ m }) {
  const funnel = m.activation;
  return (
    <div className="grid lg:grid-cols-2 gap-3 flex-1 min-h-0">
      <ChartCard title="Members reaching each milestone" testid="metric-onboarding">
        <div className="h-full overflow-auto">
        <details className="mb-2 text-sm"><summary className="cursor-pointer underline">Milestone counts</summary>
          <ul>{funnel.map(row => <li key={row.name}>{row.name} · {row.value}</li>)}</ul>
        </details>
        <ResponsiveContainer width="100%" height={170}>
          <BarChart data={funnel}>
            <CartesianGrid vertical={false} stroke="var(--line)" />
            <XAxis dataKey="name" {...axis} interval={0} tick={{ fill: "var(--muted)", fontSize: 10 }} />
            <YAxis {...axis} allowDecimals={false} width={30} />
            <Tooltip {...tip} />
            <Bar dataKey="value" radius={[6, 6, 0, 0]}>{funnel.map((_, i) => <Cell key={i} fill="var(--ink)" fillOpacity={1 - i * 0.15} />)}</Bar>
          </BarChart>
        </ResponsiveContainer>
        </div>
      </ChartCard>
      <ChartCard title="Signups over time" testid="metric-signups-time">
        <SignupHistory entries={m.signups_by_day} asOf={m.as_of} />
      </ChartCard>
    </div>
  );
}

function Breakdowns({ m }) {
  return (
    <div className="grid lg:grid-cols-2 lg:grid-rows-2 gap-3 flex-1 min-h-0">
      <ChartCard title="Which entry pages lead somewhere" testid="metric-origins">
        <div className="h-full overflow-auto text-xs">
          <table className="w-full" data-testid="metric-origins-table">
            <thead><tr className="text-[var(--muted)] font-mono text-[10px] uppercase"><th className="text-left py-1">Entry page</th><th>Signups</th><th>1st read</th><th>Activated</th><th>Bought</th><th>Renewed</th></tr></thead>
            <tbody>{m.by_origin.map((r) => <tr key={r.origin} className="border-t border-[var(--line)] text-center"><td className="text-left py-1 truncate max-w-[10rem]" title={r.origin}>{r.origin}</td><td>{r.signups}</td><td>{r.first_read}</td><td>{r.activated}</td><td>{r.purchased}</td><td>{r.renewed}</td></tr>)}</tbody>
          </table>
          {!m.by_origin.length && <p className="text-[var(--muted)] mt-2">No data yet</p>}
        </div>
      </ChartCard>
      <ChartCard title="People per relationship type" testid="metric-reltypes"><HBars data={toRows(m.people_by_relationship)} testid="metric-reltypes" /></ChartCard>
      <ChartCard title="People per group" testid="metric-groups"><HBars data={toRows(m.people_by_group)} testid="metric-groups" /></ChartCard>
      <ChartCard title="Avg profile detail by relationship (%)" testid="metric-detail-rel"><HBars data={toRows(m.avg_detail_by_relationship)} testid="metric-detail-rel" /></ChartCard>
    </div>
  );
}

function OnboardingProgress({ metrics }) {
  const firstWeek = metrics.deepening_window;
  return (
    <section className="card p-4 space-y-4" aria-label="Member onboarding" data-testid="onboarding-progress">
      <h2 className="font-display font-black text-lg uppercase">Onboarding progress</h2>
      <p className="text-xs text-[var(--muted)]">Members at or past each guide stage. Quick start can advance past optional profile questions.</p>
      <ol className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        {Object.entries(STEP_NAMES).map(([step, label]) => (
          <li className="min-w-0" key={step}>
            <span className="block text-xs text-[var(--muted)]">{label}</span>
            <span className="text-lg font-semibold">{metrics.onboarding_steps?.[step] ?? "—"}</span>
          </li>
        ))}
      </ol>
      {metrics.onboarding_skipped != null && <p className="text-sm"><strong>{metrics.onboarding_skipped}</strong> chose “Skip for now” in onboarding.</p>}
      {metrics.profile_progress && <div className="text-sm">
        <h3 className="font-semibold">Current profile progress</h3>
        <p>{metrics.profile_progress.started} of {metrics.profile_progress.members} members have started a people profile.</p>
        <p><strong>{metrics.profile_progress.with_details} of {metrics.profile_progress.members}</strong> have saved at least one intake detail. This includes members who have yet to do their first read.</p>
      </div>}
      {metrics.deepening_rate && <p className="text-sm"><strong>{metrics.deepening_rate.deepened_within_7d} of {metrics.deepening_rate.first_session_users}</strong> first-time readers have already made a substantial profile update within their first week. Readers count from their first read.</p>}
      {firstWeek && <details className="text-xs text-[var(--muted)]">
        <summary className="cursor-pointer">Completed first-week comparison</summary>
        <p>{firstWeek.deepened} of {firstWeek.complete} members who have had seven full days added substantial profile detail during that week.
          {" "}{firstWeek.open} still in their first week.</p>
      </details>}
    </section>
  );
}

const views = [["growth", "Growth"], ["breakdowns", "Breakdowns"], ["operations", "Service status"]];

export default function AdminMetrics({ api, view, onViewChange }) {
  const [m, setM] = useState(null);
  const tab = views.some(([key]) => key === view) ? view : "growth";
  const [metricsError, setMetricsError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let loading = false;
    async function refresh() {
      if (loading || document.visibilityState === "hidden") return;
      loading = true;
      try {
        const { data } = await api.get("/admin/metrics", { signal: controller.signal });
        if (!controller.signal.aborted) { setM(data); setMetricsError(false); }
      } catch {
        if (!controller.signal.aborted) setMetricsError(true);
      } finally { loading = false; }
    }
    refresh();
    const timer = window.setInterval(refresh, 60000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [api, attempt]);
  return (
    <div className="flex flex-col gap-3 h-full min-h-0" data-testid="admin-metrics">
      {m && <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--muted)]">
        <p>Totals refresh every minute while this dashboard is visible. Current accounts and saved answers. Activity totals cover their recorded history. Staff, test and closing accounts are excluded. Waitlist totals cover signups in this environment.</p>
        <div className="flex items-center gap-2">{m.as_of && <time dateTime={m.as_of}>Updated {new Date(m.as_of).toUTCString()}</time>}
          <button type="button" className="underline" onClick={() => setAttempt(n => n + 1)}>Refresh totals</button>
        </div>
      </div>}
      {m && metricsError && <p role="alert" className="text-sm">Showing the last successful report. The refresh failed.</p>}
      {m && <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-3 shrink-0">
        <Stat label="Members in analytics" value={m.users} sub="Current eligible accounts" testid="metric-users" />
        <Stat label="Returning users" value={m.returning_days} sub={`Of ${m.users} members · active on 2+ UTC days`} testid="metric-sessions" />
        <Stat label="Upload completion" value={m.upload_completion.started ? `${m.upload_completion.rate}%` : "—"} sub={`${m.upload_completion.completed ?? 0} completed / ${m.upload_completion.started ?? 0} starts`} testid="metric-upload" />
        <Stat label="Memory edits" value={m.memory_edits} sub={m.users_with_memories
          ? `${m.memory_edits_by_current_holders ?? "—"} edits by ${m.users_with_memories} current memory holders · ${m.memory_edit_rate} per holder`
          : "No current members with saved memories"} testid="metric-memory-edits" />
        <IndividualQuizStat quiz={m.quiz} />
        <WaitlistSignups api={api} total={m.waitlist} />
      </div>}
      <SubTabs value={tab} onChange={onViewChange} items={views} testid="metrics-tabs" />
      {!m && (tab === "growth" || tab === "breakdowns") && (metricsError
        ? <p role="alert" className="text-sm">Dashboard totals couldn’t load. <button className="underline" onClick={() => setAttempt(n => n + 1)}>Retry</button></p>
        : <p role="status" className="font-mono text-sm text-[var(--muted)]">Loading…</p>)}
      {tab === "growth" && m && <Growth m={m} />}
      {tab === "breakdowns" && m && <div className="flex flex-col gap-3 flex-1 min-h-0 overflow-y-auto"><OnboardingProgress metrics={m} /><Breakdowns m={m} /></div>}
      {tab === "operations" && <OperationsStatus api={api} />}
    </div>
  );
}
