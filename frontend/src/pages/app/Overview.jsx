import { MemberLoadState, useMemberResource } from "@/components/app/MemberResource";
import { Link, useOutletContext } from "react-router-dom";
import { useUser } from "@clerk/react";
import { formatDistanceToNow } from "date-fns";
import { ArrowRight, MessageSquare, MessageSquareText, FileText, CheckCircle2, Target, UserPlus, HelpCircle, Brain } from "lucide-react";
import { PieChart, Pie, Cell, ResponsiveContainer, AreaChart, Area, XAxis, Tooltip } from "recharts";
import { GroupIcon, Initials } from "@/components/app/GroupIcon";
import { Ring } from "@/components/app/Meter";
import { GROUP_LABEL, REL_GROUPS } from "@/lib/api";
import { DimBars, pendingTypeText, RelationshipAxes } from "@/components/app/Personality";

const SHADES = ["var(--ink)", "var(--crimson)", "var(--muted)", "var(--amber)", "var(--line)"];
const FEED_ICON = { person: UserPlus, report: FileText, conversation: MessageSquare, question: HelpCircle, outcome: CheckCircle2, prep: Target, quick_advice: MessageSquareText };
const tip = { contentStyle: { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, fontSize: 12, color: "var(--ink)" } };
const H = "font-display font-black uppercase text-xl";

function Tile({ label, value, testid }) {
  return (
    <div className="card px-4 py-3 min-w-[120px]" data-testid={testid}>
      <p className="font-mono text-[9px] uppercase tracking-widest text-[var(--muted)]">{label}</p>
      <p className="font-display font-black text-3xl leading-none mt-1">{value}</p>
    </div>
  );
}

function DemoChip({ d }) {
  const parts = [d?.age && `${d.age}`, ...["sex", "ethnicity", "education"].map((k) => d?.[k] !== "Prefer not to say" && d?.[k])].filter(Boolean);
  if (!parts.length) return null;
  return <Link to="/app/personal" className="inline-flex min-w-0 max-w-full text-right break-words text-[11px] px-2.5 py-1 rounded-full border border-[var(--line)] hover:border-[var(--ink)] transition-colors" data-testid="about-demographics-chip">{parts.join(" · ")}</Link>;
}

function AboutCard({ d }) {
  const a = d.about;
  const empty = !a.short_done && !a.extended_done && !a.further_done && !a.describe;
  return (
    <section className={`card p-5 ${a.questionnaire_complete === true ? "xl:col-span-5" : "xl:col-span-8"} space-y-4`} data-testid="about-card">
      <header className="flex items-start justify-between gap-3" data-testid="about-card-header">
        <div className="flex shrink-0 items-center gap-2"><Brain aria-hidden="true" className="w-4 h-4" /><p className={H}>You</p></div>
        <DemoChip d={a.demographics} />
      </header>
      <div className="flex flex-wrap items-center gap-4">
        <div className="min-w-0 flex-[1_1_13rem]">
          {empty ? (
            <Link to="/app/personal" className="btn-lime inline-flex items-center justify-center whitespace-nowrap gap-1.5 px-4 py-2 text-sm" data-testid="about-start">Answer 12 questions<ArrowRight aria-hidden="true" className="w-4 h-4 shrink-0" /></Link>
          ) : (
            <>
              {a.questionnaire_complete === true && <div className="flex flex-wrap gap-1 mt-3" data-testid="about-snapshot">{a.tags.slice(0, 6).map((t) => <span key={t} className="text-[11px] px-2 py-0.5 rounded-full bg-[var(--ink)] text-[var(--bg)]">{t}</span>)}</div>}
              {a.describe && <p className="text-xs mt-3 italic line-clamp-2" data-testid="about-describe-text">“{a.describe}”</p>}
              <div className="flex flex-wrap items-center gap-3 mt-3">
                <Ring score={a.score} size={48} testid="about-ring" />
                <Link to="/app/personal?tab=deeper" className="btn-lime px-3 py-1.5 text-xs" data-testid="about-extended">Answer more</Link>
                <Link to="/app/personal" className="btn-ghost px-3 py-1.5 text-xs" data-testid="about-edit">Edit</Link>
              </div>
            </>
          )}
        </div>
        {a.questionnaire_complete === true && <div className="self-center w-full min-w-0 flex-[1_1_15rem]" data-testid="about-radar"><RelationshipAxes axes={d.radar} compact /></div>}
      </div>
    </section>
  );
}

function PeopleCard({ d }) {
  const rows = REL_GROUPS.map((g) => ({ key: g.key, ...d.groups[g.key] }));
  const pie = rows.filter((r) => r.count);
  return (
    <section className="card p-5 xl:col-span-4" data-testid="people-by-category">
      <div className="flex justify-between items-center"><p className={H}>Your people</p><Link to="/app/people" className="text-xs underline" data-testid="overview-all-people">All {d.total}</Link></div>
      <div className="grid grid-cols-[100px_1fr] gap-4 items-center mt-3">
        <div className="h-[100px] relative">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart><Pie data={pie.length ? pie : [{ key: "none", count: 1 }]} dataKey="count" innerRadius={32} outerRadius={47} stroke="var(--bg)" strokeWidth={2}>
              {(pie.length ? pie : [{}]).map((r, i) => <Cell key={i} fill={pie.length ? SHADES[i % SHADES.length] : "var(--line)"} />)}
            </Pie></PieChart>
          </ResponsiveContainer>
          <p className="absolute inset-0 flex items-center justify-center font-display font-black text-2xl">{d.total}</p>
        </div>
        <ul>
          {rows.map((r) => (
            <li key={r.key}>
              <Link to={`/app/people?group=${r.key}`} className="flex items-center gap-2.5 py-1 px-2 -mx-2 rounded-lg hover:bg-[var(--surface-2)] transition-colors" data-testid={`overview-group-${r.key}`}>
                <GroupIcon group={r.key} className="w-3.5 h-3.5" />
                <span className="text-sm flex-1">{GROUP_LABEL[r.key]}</span>
                <span className="flex -space-x-2">{r.people.slice(0, 3).map((p) => <Initials key={p.id} name={p.nickname} className="w-5 h-5 text-[8px]" />)}</span>
                <span className="font-mono text-xs w-5 text-right">{r.count}</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function PersonalityCard({ d }) {
  const p = d.about.personality;
  return (
    <section className="card p-5 xl:col-span-3" data-testid="personality-card">
      <div className="flex items-center justify-between mb-2"><p className={H}>Personality type</p><Link to="/app/personal?tab=results" className="text-xs underline" data-testid="overview-personality-link">Open</Link></div>
      {p.code ? (
        <div className="mb-3"><p className="font-display font-black text-5xl leading-none" data-testid="overview-type-code">{p.code}</p><p className="font-display font-black uppercase text-base mt-1">{p.name}</p><p className="text-[11px] text-[var(--muted)] line-clamp-2">{p.line}</p></div>
      ) : <p className="text-xs text-[var(--muted)] mb-3" data-testid="overview-type-pending">{pendingTypeText(p)}</p>}
      <DimBars dims={p.dims} selfSet={p.self_set} />
    </section>
  );
}

function Feed({ d }) {
  const destination = (item) => {
    if (item.tab === "quick-advice") {
      const base = item.workspace === "couples" ? "/app/couples/quick-advice" : "/app/quick-advice";
      const id = item.advice_id || item.quick_advice_id;
      return base + (id ? `?reply=${encodeURIComponent(id)}` : "");
    }
    if (item.tab === "group") return "/app/people?chat=" + encodeURIComponent(item.person_ids.join(","));
    if (item.tab === "practice") return "/app/practice?person=" + encodeURIComponent(item.person_id);
    if (item.tab === "chat") return "/app/people?chat=" + encodeURIComponent(item.person_id);
    return `/app/person/${item.person_id}?tab=${item.tab}`;
  };
  return (
    <section className="card p-5 xl:col-span-7" data-testid="recent-activity">
      <p className={`${H} mb-3`}>Recent activity</p>
      {!d.feed.length && <p className="text-sm text-[var(--muted)]" data-testid="activity-empty">Nothing yet. Ask for Quick Advice or add a person to get started.</p>}
      <ol className="grid grid-cols-[minmax(0,1fr)] md:grid-cols-[repeat(2,minmax(0,1fr))] gap-x-6 gap-y-2.5">
        {d.feed.slice(0, 8).map((f, i) => {
          const I = FEED_ICON[f.type] || FileText;
          return (
            <li key={i} className="min-w-0">
              <Link to={destination(f)} className="flex items-start gap-2.5 group" data-testid={`activity-${i}`}>
                <span className="w-6 h-6 rounded-full border border-[var(--line)] flex items-center justify-center shrink-0"><I className="w-3 h-3" /></span>
                <span className="min-w-0"><span className="block text-sm truncate group-hover:underline">{f.text}</span><span className="block text-[10px] text-[var(--muted)] font-mono">{formatDistanceToNow(new Date(f.ts))} ago</span></span>
              </Link>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function ActivityChart({ d }) {
  return (
    <section className="card p-5 xl:col-span-5" data-testid="activity-chart">
      <div className="flex justify-between items-baseline"><p className={H}>Last 30 days</p><p className="font-mono text-xs text-[var(--muted)]">{d.activity.reduce((s, x) => s + x.count, 0)} events</p></div>
      <div className="h-32 mt-2">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={d.activity} margin={{ left: 0, right: 0, top: 5, bottom: 0 }}>
            <XAxis dataKey="day" tickFormatter={(v) => v.slice(8)} tick={{ fill: "var(--muted)", fontSize: 10 }} axisLine={false} tickLine={false} interval={4} />
            <Tooltip {...tip} />
            <Area type="monotone" dataKey="count" stroke="var(--ink)" fill="var(--ink)" fillOpacity={0.12} strokeWidth={2} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

export default function Overview() {
  const { me, api } = useOutletContext();
  const { user } = useUser();
  const { data: d, error, reload } = useMemberResource(api, "/overview");
  if (!d) return <MemberLoadState label="your overview" error={error} retry={reload} />;
  const name = user?.firstName || me.nickname || "there";

  return (
    <div className="space-y-4" data-testid="overview-page">
      <header className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          {user?.imageUrl ? <img src={user.imageUrl} alt="" className="w-12 h-12 rounded-full object-cover border border-[var(--line)]" /> : <Initials name={name} className="w-12 h-12 text-base" />}
          <div>
            <h1 className="font-display font-black uppercase text-4xl leading-none" data-testid="overview-greeting">Hi, {name}</h1>
            <p className="text-sm text-[var(--muted)] mt-1" data-testid="overview-summary">{d.total} {d.total === 1 ? "person" : "people"} · {d.unread} unread {d.unread === 1 ? "update" : "updates"}</p>
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <Tile label="People" value={d.total} testid="tile-people" />
          <Tile label="Updates" value={d.unread} testid="tile-updates" />
          <Tile label="Check-ins" value={d.checkin_count ?? d.checkins.length} testid="tile-checkins" />
          <Tile label="To review" value={d.review_count ?? d.unreviewed.length} testid="tile-memories" />
        </div>
      </header>
      {d.checkins.length > 0 && (
        <section className="card p-4" data-testid="overview-checkins">
          <p className={H}>How did it go?</p>
          <div className="flex flex-wrap gap-2 mt-3">
            {d.checkins.map((c) => (
              <Link key={c.exchange_id} to={`/app/person/${c.person_id}?tab=history&exchange=${encodeURIComponent(c.exchange_id)}`} className="flex items-center gap-2 px-4 py-2 rounded-full border border-[var(--line)] hover:border-[var(--ink)] text-sm transition-colors" data-testid={`overview-checkin-${c.exchange_id}`}>
                <span className="font-semibold">{c.nickname}</span><span className="text-[var(--muted)] truncate max-w-[16rem]">you chose: {c.choice}</span><ArrowRight className="w-3.5 h-3.5" />
              </Link>
            ))}
          </div>
        </section>
      )}
      {d.unreviewed.length > 0 && <section className="card p-4"><h2 className={H}>Memories to review</h2>
        <div className="flex flex-wrap gap-2 mt-3">{d.unreviewed.map(row => <Link key={row.exchange_id}
          to={`/app/person/${row.person_id}?tab=history&exchange=${encodeURIComponent(row.exchange_id)}`}
          className="btn-ghost px-4 py-2 text-sm">{row.nickname} · {row.count} to review</Link>)}</div></section>}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4 [&>*]:min-w-0">
        <PeopleCard d={d} />
        <AboutCard d={d} />
        {d.about.questionnaire_complete === true && <PersonalityCard d={d} />}
        <Feed d={d} />
        <ActivityChart d={d} />
      </div>
    </div>
  );
}
