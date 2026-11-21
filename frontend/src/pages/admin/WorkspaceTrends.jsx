import { useEffect, useState } from "react";
import CouplesAnalytics from "./CouplesAnalytics";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const formatDay = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const shortDay = (iso) => iso ? formatDay.format(new Date(iso + "T00:00:00Z")) : "";

function RecentChange({ data, selected }) {
  const length = { 7: 3, 30: 7, 90: 30 }[data.days];
  const completed = data.daily.filter(row => row.day < data.end);
  if (!length || completed.length < length * 2) return null;
  const earlier = completed.slice(-length * 2, -length);
  const recent = completed.slice(-length);
  const sum = rows => rows.reduce((total, row) => total + row[selected], 0);
  const before = sum(earlier);
  const after = sum(recent);
  const change = after - before;
  const range = rows => shortDay(rows[0].day) + " – " + shortDay(rows[rows.length - 1].day);
  return (
    <div className="rounded-xl bg-[var(--surface-2)] px-3 py-2 text-sm" data-testid="trend-change">
      <p className="font-semibold">{change === 0 ? "Same count" : Math.abs(change).toLocaleString() + (change > 0 ? " more" : " fewer")} in the last {length} complete days</p>
      <p className="text-xs text-[var(--muted)]">{range(recent)} · {after.toLocaleString()} &nbsp; / &nbsp; {range(earlier)} · {before.toLocaleString()}</p>
    </div>
  );
}

function ResponseDistribution({ group }) {
  if (!group.available) return <p className="text-sm text-[var(--muted)]">Waiting for enough opted-in responses.</p>;
  return (
    <>
      <p className="text-xs text-[var(--muted)]">{group.respondents.toLocaleString()} participating members</p>
      <ul className="space-y-2">
        {group.options.map(({ label, count }) => {
          const percent = group.respondents ? Math.round(count * 100 / group.respondents) : 0;
          return (
            <li key={label} className="space-y-1">
              <div className="flex justify-between gap-3 text-sm"><span>{label}</span><span className="whitespace-nowrap tabular-nums">{count.toLocaleString()} · {percent}%</span></div>
              <div aria-hidden="true" className="h-1 rounded bg-[var(--surface-2)]"><div style={{ width: percent + "%" }} className="h-full rounded bg-[var(--ink)]" /></div>
            </li>
          );
        })}
      </ul>
    </>
  );
}

function ActivityHistory({ data, initialMeasure }) {
  const [choice, setSelected] = useState(initialMeasure || "reads");
  const measures = data.measures || Object.keys(data.totals).map(key => ({ key, label: key.replace(/_/g, " ") }));
  const metric = measures.find(item => item.key === choice) || measures[0];
  const selected = metric.key;
  return (
    <section className="card p-4 sm:p-5 space-y-4" aria-labelledby="trends-activity">
      <h2 id="trends-activity" className="font-display text-xl font-bold uppercase">Activity by day</h2>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        {measures.slice(0, 6).map(({ key, label }) => (
          <button type="button" key={key} aria-pressed={selected === key} onClick={() => setSelected(key)}
            className={`rounded-xl border p-3 text-left ${selected === key ? "bg-[var(--ink)] text-[var(--bg)] border-[var(--ink)]" : "border-[var(--line)] hover:border-[var(--ink)]"}`}>
            <span className="block text-xs">{label}</span><strong className="block text-2xl tabular-nums">{data.totals[key].toLocaleString()}</strong>
          </button>
        ))}
      </div>
      <div className="space-y-1">
        <label className="flex items-center gap-2 text-sm flex-wrap">Measure
          <select data-testid="trend-measure" value={selected} onChange={e => setSelected(e.target.value)} className="bg-[var(--surface)] border border-[var(--line)] rounded-lg p-2 max-w-full">
            {measures.map(({ key, label }) => <option key={key} value={key}>{label}</option>)}
          </select>
          <strong>{data.totals[selected].toLocaleString()} in this period</strong>
        </label>
        {metric.unit && <p className="text-xs text-[var(--muted)]">{metric.unit}</p>}
      </div>
      {data.totals[selected] > 0 ? (
        <div className="h-44" aria-hidden="true" data-testid="trend-chart">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data.daily} margin={{ top: 6, right: 10, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--line)" />
              <XAxis dataKey="day" tickFormatter={shortDay} minTickGap={32} tick={{ fill: "var(--muted)", fontSize: 11 }} />
              <YAxis width={32} allowDecimals={false} tick={{ fill: "var(--muted)", fontSize: 11 }} />
              <Tooltip labelFormatter={shortDay} contentStyle={{ color: "var(--ink)", background: "var(--surface)", borderColor: "var(--line)" }} />
              <Line type="linear" dataKey={selected} name={metric.label} stroke="var(--ink)" strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : <p className="text-sm text-[var(--muted)]">No recorded activity for this measure in the selected period.</p>}
      <RecentChange data={data} selected={selected} />
      <details key={selected} className="text-sm">
        <summary className="cursor-pointer underline underline-offset-2">Daily counts for {metric.label.toLowerCase()}</summary>
        <div className="mt-3 max-h-48 overflow-auto"><table className="w-full text-left"><thead><tr><th scope="col">Date (UTC)</th><th scope="col" className="text-right">{metric.label}</th></tr></thead>
          <tbody>{data.daily.map(row => <tr key={row.day} className="border-t border-[var(--line)]"><td className="py-1">{row.day}</td><td className="text-right tabular-nums">{row[selected]}</td></tr>)}</tbody></table></div>
      </details>
    </section>
  );
}

function ReportControls({ api, days, setDays }) {
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState(false);
  async function download() {
    setDownloading(true);
    setDownloadError(false);
    try {
      const response = await api.get("/admin/reports/workspace.csv", { params: { days }, responseType: "blob" });
      const url = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = response.headers?.["content-disposition"]?.match(/filename="([^"]+)"/)?.[1] || `imladri-workspace-${days}-days.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setDownloadError(true);
    } finally {
      setDownloading(false);
    }
  }
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label className="text-sm flex items-center gap-2">Period
          <select value={days} onChange={e => setDays(Number(e.target.value))} className="bg-[var(--surface)] border border-[var(--line)] rounded-lg p-2" data-testid="trends-period">
            {[7, 30, 90].map(n => <option key={n} value={n}>Last {n} days</option>)}
          </select>
        </label>
        <button type="button" className="btn-ghost px-4 py-2 text-sm" onClick={download} disabled={downloading}
          data-testid="trends-download">{downloading ? "Preparing report…" : "Download workspace report (CSV)"}</button>
      </div>
      {downloadError && <p role="alert" className="text-sm">The report couldn’t be downloaded. Try again.</p>}
    </>
  );
}

export function CouplesReport({ api }) {
  const [days, setDays] = useState(30);
  return (
    <div className="space-y-4 min-h-0 overflow-y-auto pb-3" data-testid="couples-report">
      <ReportControls api={api} days={days} setDays={setDays} />
      <CouplesAnalytics api={api} days={days} />
    </div>
  );
}

export default function WorkspaceTrends({ api, initialMeasure, initialDays }) {
  const [days, setDays] = useState([7, 30, 90].includes(initialDays) ? initialDays : 30);
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError(false);
    api.get("/admin/trends", { params: { days }, signal: controller.signal })
      .then(response => { if (!controller.signal.aborted) setData(response.data); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [api, days, attempt]);
  return (
    <div className="space-y-4 min-h-0 overflow-y-auto pb-3" data-testid="workspace-trends">
      <ReportControls api={api} days={days} setDays={setDays} />
      {data && data.days === days && <p className="text-xs text-[var(--muted)]">{data.start} through {data.end} UTC, including today so far.</p>}
      {error ? <p role="alert" className="text-sm">Trends couldn’t load. <button className="underline" onClick={() => setAttempt(n => n + 1)}>Retry</button></p>
        : (!data || data.days !== days) ? <p role="status" className="text-sm text-[var(--muted)]">Loading trends…</p> : (
        <>
          {data.subscriptions && <section className="card p-4 sm:p-5" data-testid="trends-subscriptions">
            <h2 className="font-display text-xl font-bold uppercase">Subscriptions and usage</h2>
            <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4 text-sm">
              {[
                ["Active subscriptions today", data.subscriptions.active],
                ["Replies used in active billing periods", data.subscriptions.replies_used],
                ["Subscription checkouts opened in this period", data.totals.checkouts_started],
                ["New subscriptions paid in this period", data.totals.paid_purchases],
                ["Subscription upgrades paid in this period", data.totals.paid_upgrades],
                ["One-time refills paid in this period", data.totals.paid_refills],
                ["Refill refund events in this period", data.totals.refill_refund_events],
                ["Refill disputes lost in this period", data.totals.refill_lost_disputes],
              ].map(([name, value]) => <div key={name}><dt className="text-xs text-[var(--muted)]">{name}</dt><dd className="text-xl font-semibold">{value ?? "—"}</dd></div>)}
            </dl>
          </section>}
          <ActivityHistory data={data} initialMeasure={initialMeasure} />
          {data.tone_usage && <section className="card p-4 sm:p-5 space-y-3" aria-label="Individual conversation tone">
            <h2 className="font-display text-xl font-bold uppercase">Individual conversation tone</h2>
            <p className="text-sm text-[var(--muted)]">Feedback on reads created in this period. Each read counts once per action, including when someone tries a style more than once.</p>
            <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {[
                ["Members using tone", data.tone_usage.members],
                ["Tone estimates", `${data.tone_usage.estimated} / ${data.tone_usage.reads} reads`],
                ["Too little evidence", data.tone_usage.insufficient],
                ["Selected a style", data.tone_usage.selected], ["Tried multiple styles", data.tone_usage.switched],
                ["Copied a reply", data.tone_usage.copied],
                ["Read corrected", `${data.tone_usage.corrected} / ${data.tone_usage.reviewed} reviewed`],
                ["Reported sending", `${data.tone_usage.sent} / ${data.tone_usage.reported_sending} answered`],
                ["Rated helpfulness", `${data.tone_usage.rated_helpfulness} / ${data.tone_usage.sent} reported sent`],
              ].map(([label, value]) => <div key={label}><dt className="text-xs text-[var(--muted)]">{label}</dt><dd className="text-lg font-bold">{value}</dd></div>)}
            </dl>
            <p className="text-sm">Styles tried · {Object.entries(data.tone_usage.styles).map(([style, n]) => `${style} ${n}`).join(" · ")}</p>
          </section>}
          {data.saved_features && <section className="card p-4 sm:p-5 space-y-3">
            <h2 className="font-display text-xl font-bold uppercase">Saved across Individual today</h2>
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {data.saved_features.map(item => <div key={item.key}><dt className="text-xs text-[var(--muted)]">{item.label}</dt>
                <dd className="text-xl font-semibold">{item.count.toLocaleString()}</dd></div>)}
            </dl>
          </section>}
          <section className="card p-4 sm:p-5 space-y-3" data-testid="trends-outcomes">
            <h2 className="font-display text-xl font-bold uppercase">How members said it went</h2>
            <p className="text-sm text-[var(--muted)]">Each participating member contributes their latest selected follow-up result in this period. Results appear when each represented answer has at least {data.outcomes.minimum} members.</p>
            <ResponseDistribution group={data.outcomes} />
          </section>
          <section className="card p-4 sm:p-5 space-y-3" data-testid="trends-everyday">
            <h2 className="font-display text-xl font-bold uppercase">Everyday preferences</h2>
            <p className="text-sm text-[var(--muted)]">Current saved answers from members who opted in, across all dates. Each question appears with a breakdown once every represented choice has at least {data.everyday.minimum} members.</p>
            {data.everyday.questions.map(question => (
              <div key={question.id} className="border-t border-[var(--line)] pt-3 space-y-2">
                <h3 className="font-semibold text-sm">{question.prompt}</h3>
                {question.current === false
                  ? <p className="text-sm text-[var(--muted)]">Reporting is paused because the published question or its choices changed.</p>
                  : <ResponseDistribution group={question} />}
              </div>
            ))}
          </section>
        </>
      )}
    </div>
  );
}
