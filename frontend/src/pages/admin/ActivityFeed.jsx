import { useEffect, useRef, useState } from "react";
import { UserPlus, MessageSquareText, Users, Drama, Handshake, Heart } from "lucide-react";

const ICON = { signup: UserPlus, chat: MessageSquareText, group: Users, practice: Drama, couples: Handshake, your_type: Heart, quick_advice: MessageSquareText };
const LABEL = { signup: "Signups", chat: "Chats", group: "Group chats", practice: "Practice", couples: "Couples", your_type: "Your Type", quick_advice: "Quick Advice" };
const utcHour = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric", hour: "2-digit", hourCycle: "h23", minute: "2-digit" });

function couplesDetail({ visits, samples }) {
  return `${visits} ${visits === 1 ? "visit" : "visits"} · ${samples} ${samples === 1 ? "sample" : "samples"} completed`;
}

const ago = (iso, nowMs) => {
  const s = Math.max(0, Math.round((nowMs - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};

export function ActivityFeed({ api }) {
  const [d, setD] = useState(null);
  const [filter, setFilter] = useState("all");
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [tick, setTick] = useState(Date.now());
  const seen = useRef(null);
  const [fresh, setFresh] = useState([]);
  useEffect(() => {
    const controller = new AbortController();
    let poll;
    setFailed(false);
    const load = () => api.get("/admin/activity", {params: {kind: filter, include_couples: true}, signal: controller.signal}).then((r) => {
      if (controller.signal.aborted) return;
      const keys = r.data.items.map((i) => `${i.type}${i.at}${i.user}`);
      if (seen.current) {
        const nw = keys.filter((k) => !seen.current.has(k));
        if (nw.length) setFresh(nw);
      }
      seen.current = new Set(keys);
      setFailed(false);
      setD({...r.data, filter});
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); })
      .finally(() => { if (!controller.signal.aborted) poll = setTimeout(load, 10000); });
    load();
    const clock = setInterval(() => setTick(Date.now()), 5000);
    return () => { controller.abort(); clearTimeout(poll); clearInterval(clock); };
  }, [api, filter, attempt]);
  const problem = <p role="alert" className="p-4 text-sm">Activity couldn’t refresh. <button className="underline" onClick={() => setAttempt(n => n + 1)}>Retry</button></p>;
  if (!d) return failed ? problem : <p role="status" className="font-mono text-sm text-[var(--muted)]">Loading activity…</p>;
  const items = d.filter === filter ? d.items : [];
  return (
    <div className="grid lg:grid-cols-[240px_minmax(0,1fr)] gap-3 flex-1 min-h-0" data-testid="admin-activity">
      <div className="card p-4 space-y-2 self-start">
        <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)] inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-[#16a34a] animate-pulse" data-testid="activity-live-dot" />Live · last 24h</p>
        <button onClick={() => setFilter("all")} className={`w-full text-left px-3 py-2 rounded-xl text-sm ${filter === "all" ? "bg-[var(--ink)] text-[var(--bg)]" : "hover:bg-[var(--surface-2)]"}`} data-testid="activity-filter-all">Everything</button>
        {Object.keys(LABEL).filter(t => t in d.last24).map((t) => {
          const I = ICON[t];
          return (
            <button key={t} onClick={() => setFilter(t)} className={`w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm ${filter === t ? "bg-[var(--ink)] text-[var(--bg)]" : "hover:bg-[var(--surface-2)]"}`} data-testid={`activity-filter-${t}`}>
              <I className="w-4 h-4" /><span className="flex-1 text-left">{LABEL[t]}</span><span className="font-mono text-xs" title="in the last 24 hours" data-testid={`activity-count-${t}`}>{d.last24[t]}<span className="opacity-60"> /24h</span></span>
            </button>
          );
        })}
        <p className="text-[11px] text-[var(--muted)] pt-1">Refreshes every 10 seconds. Counts cover the last 24 hours{d.from_utc && d.now ? " ("+utcHour.format(new Date(d.from_utc))+" to "+utcHour.format(new Date(d.now))+" UTC)" : ""}. The feed shows up to 60 recent entries and can include earlier days. Staff, test and closing accounts are excluded. Couples totals combine visits and completed samples, grouped by UTC hour. Each action counts once per member per UTC day.</p>
      </div>
      <div className="card relative min-h-[300px] lg:min-h-0 overflow-y-auto divide-y divide-[var(--line)]" data-testid="activity-list">
        {failed && problem}
        {!failed && !items.length && <p role="status" className="p-5 text-sm text-[var(--muted)]" data-testid="activity-empty">{d.filter === filter ? "Nothing yet." : "Loading activity…"}</p>}
        {items.map((i, index) => {
          const I = ICON[i.type];
          const k = `${i.type}${i.at}${i.user}`;
          return (
            <div key={k + index} className={`flex items-center gap-3 px-4 py-2.5 text-sm transition-colors ${fresh.includes(k) ? "bg-[#16a34a]/10" : ""}`} data-testid="activity-item">
              <span className="w-8 h-8 rounded-full border border-[var(--line)] grid place-items-center shrink-0"><I className="w-4 h-4" /></span>
              <span className="flex-1 min-w-0"><span className="block">{i.type === "couples" ? couplesDetail(i) : i.detail}</span><span className="block text-[11px] text-[var(--muted)] truncate">{i.type === "couples" ? "Couples · hourly total" : i.user}</span></span>
              <time dateTime={i.at} title={new Date(i.at).toUTCString()} className="font-mono text-[11px] text-[var(--muted)] shrink-0">{i.type === "couples" ? utcHour.format(new Date(i.at)) + " UTC" : ago(i.at, tick)}</time>
            </div>
          );
        })}
      </div>
    </div>
  );
}
