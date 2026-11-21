import { useMemo } from "react";
import { MessageSquare, HelpCircle, CheckCircle2, Target } from "lucide-react";
import { RadarChart, Radar, PolarGrid, PolarAngleAxis, ResponsiveContainer } from "recharts";
import { Conf } from "@/components/app/Meter";
import { MemberLoadState, useMemberResource } from "@/components/app/MemberResource";

export function TraitRadar({ traits }) {
  if (!traits?.length) {
    return <p className="text-sm text-[var(--muted)]" data-testid="trait-radar-empty">Not enough evidence for a trait picture yet. Traits appear once what they did or said supports them.</p>;
  }
  return (
    <div data-testid="trait-radar">
      <div className="h-60">
        <ResponsiveContainer width="100%" height="100%">
          <RadarChart data={traits} outerRadius="70%">
            <PolarGrid stroke="var(--line)" />
            <PolarAngleAxis dataKey="trait" tick={{ fill: "var(--muted)", fontSize: 10 }} />
            <Radar dataKey="score" stroke="var(--ink)" fill="var(--ink)" fillOpacity={0.25} />
          </RadarChart>
        </ResponsiveContainer>
      </div>
      <ul className="space-y-1.5 mt-2">
        {traits.map((t) => <li key={t.trait} className="flex items-center justify-between gap-2 text-xs"><span>{t.trait}</span><span className="flex items-center gap-2"><span className="font-mono">{t.score}</span><Conf level={t.confidence} /></span></li>)}
      </ul>
      <p className="text-[11px] text-[var(--muted)] mt-3">Estimates from the evidence. Traits without enough evidence are left out.</p>
    </div>
  );
}

export function PersonTimeline({ p, api }) {
  const exchanges = useMemberResource(api, `/people/${p.id}/exchanges`);
  const preps = useMemberResource(api, `/people/${p.id}/preps`);
  const items = useMemo(() => {
    if (!exchanges.data || !preps.data) return null;
    const out = [];
    exchanges.data.forEach((ex) => {
      out.push({ ts: ex.created_at, I: ex.has_conversation ? MessageSquare : HelpCircle, text: ex.has_conversation ? "Conversation shared" : `Asked: ${(ex.question || "").slice(0, 80)}`, sub: ex.response?.best_move });
      if (ex.outcome) out.push({ ts: ex.outcome_at || ex.created_at, I: CheckCircle2, text: "Outcome logged", sub: ex.outcome });
    });
    preps.data.forEach((pr) => out.push({ ts: pr.created_at, I: Target, text: `Game plan: ${(pr.situation || "").slice(0, 80)}` }));
    return out.sort((a, b) => b.ts.localeCompare(a.ts));
  }, [exchanges.data, preps.data]);
  const error = exchanges.error || preps.error;
  if (!items || error) return <MemberLoadState label="the timeline" error={error} retry={() => Promise.all([exchanges.reload(), preps.reload()])} />;
  return (
    <div data-testid="person-timeline">
      {!items.length && <p className="text-sm text-[var(--muted)]">Nothing yet. Conversations, advice and outcomes show up here.</p>}
      <ol className="relative border-l border-[var(--line)] ml-3">
        {items.map((it, i) => (
          <li key={i} className="ml-6 pb-4 last:pb-0" data-testid={`timeline-item-${i}`}>
            <span className="absolute -left-3 w-6 h-6 rounded-full bg-[var(--bg)] border border-[var(--line)] flex items-center justify-center"><it.I className="w-3 h-3" /></span>
            <p className="text-sm font-medium">{it.text}</p>
            {it.sub && <p className="text-xs text-[var(--muted)] line-clamp-2 mt-0.5">{it.sub}</p>}
            <p className="text-[10px] font-mono text-[var(--muted)] mt-0.5">{new Date(it.ts).toLocaleDateString()}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}
