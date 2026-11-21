import { ReportEvidence } from "@/components/app/CoachingEvidence";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { RefreshCw } from "lucide-react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { LimitNotice, messageLimit } from "@/components/app/PlanUsage";
import { ConfBar } from "@/components/app/Meter";
import { BasedOn } from "@/components/app/GroupIcon";
import { TraitRadar, PersonTimeline } from "@/components/app/PersonCharts";
import { errMsg } from "@/lib/api";
import { formatDistanceToNow } from "date-fns";

function Compatibility({ c, p, api }) {
  if (!c) {
    return <p className="card p-4 text-sm text-[var(--muted)]" data-testid="compat-empty">Fill in your <a href="/app/personal" className="underline">Personal</a> tab, then refresh the report to see how your style compares with {p.nickname}'s.</p>;
  }
  return (
    <section className="card p-5 space-y-3" data-testid="report-compatibility">
      <p className="font-display font-black uppercase text-xl">You &amp; {p.nickname}</p>
      {c.summary && <p className="text-sm" data-testid="compat-summary">{c.summary}</p>}
      {c.summary && <ReportEvidence refs={c.claim_evidence?.summary} api={api} person={p.id} />}
      <div className="grid md:grid-cols-2 gap-3">
        <div className="rounded-xl p-4 border border-emerald-500/40 bg-emerald-500/10" data-testid="compat-works"><p className="font-mono text-[10px] uppercase tracking-widest text-emerald-600 mb-1.5">Where you fit</p><ul className="text-sm space-y-1 list-disc pl-4">{(c.works || []).map((x, i) => <li key={i}>{x}<ReportEvidence refs={c.claim_evidence?.works?.[i]} api={api} person={p.id} /></li>)}</ul></div>
        <div className="rounded-xl p-4 border border-[var(--crimson)]/40 bg-[var(--crimson)]/10" data-testid="compat-friction"><p className="font-mono text-[10px] uppercase tracking-widest text-[var(--crimson)] mb-1.5">Where you clash</p><ul className="text-sm space-y-1 list-disc pl-4">{(c.friction || []).map((x, i) => <li key={i}>{x}<ReportEvidence refs={c.claim_evidence?.friction?.[i]} api={api} person={p.id} /></li>)}</ul></div>
      </div>
      {c.tip && <p className="text-sm" data-testid="compat-tip"><span className="font-semibold">Try this: </span>{c.tip}</p>}
      {c.tip && <ReportEvidence refs={c.claim_evidence?.tip} api={api} person={p.id} />}
    </section>
  );
}

export default function ReportTab({ p, api, reload }) {
  const nav = useNavigate();
  const { me } = useOutletContext();
  const limit = messageLimit(me.usage, p);
  const [busy, setBusy] = useState(false);
  const r = p.report;
  useEffect(() => {
    if (p.report_status !== "updating") return;
    const t = setInterval(reload, 4000);
    return () => clearInterval(t);
  }, [p.report_status, reload]);

  const refresh = async () => {
    setBusy(true);
    try {
      const { data } = await api.post(`/people/${p.id}/report/refresh`);
      await reload();
      if (data.report_status === "ready") toast.success("Report refreshed");
      else if (data.report_status === "error") toast.error("The report could not be refreshed. Try again.");
    } catch (e) {
      toast.error(errMsg(e));
    }
    setBusy(false);
  };

  const updating = busy || p.report_status === "updating";
  return (
    <div className="grid xl:grid-cols-[1fr_340px] gap-6 items-start">
    <div className="space-y-5 min-w-0" data-testid="report-tab">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-display font-black uppercase text-3xl">The read on {p.nickname}</h2>
        <button onClick={refresh} disabled={updating || !!limit} className="btn-ghost px-4 py-2 text-sm inline-flex items-center gap-2" data-testid="report-refresh-button">
          <RefreshCw className={`w-4 h-4 ${updating ? "animate-spin" : ""}`} />{updating ? "Updating…" : r ? "Refresh" : "Build report"}
        </button>
      </div>
      {limit && <LimitNotice reason={limit} />}
      {p.report_status === "source_changed" && <p role="status" className="text-sm">Your evidence changed. Build a new report to use the current information.</p>}
      {p.report_stale && <p role="status" className="rounded-xl border border-[var(--line)] p-3 text-sm" data-testid="report-stale">
        This is an earlier report. Refresh it to use your current Personal profile.
      </p>}
      {!r ? (
        <div className="card p-6" data-testid="report-empty">
          <p className="font-semibold">No report yet.</p>
          <p className="text-sm text-[var(--muted)] mt-1">It builds from conversations and profile details. A partly blank report is normal — every section says what it still needs.</p>
          <button onClick={() => nav(`/app/people?chat=${p.id}`)} className="mt-4 px-4 py-2 rounded-full bg-[var(--emerald)] text-[var(--on-accent)] text-sm font-bold" data-testid="report-go-chat">Add a conversation</button>
        </div>
      ) : (
        <>
          {r.change_note && (
            <div className="p-4 rounded-xl border border-[var(--amber)]/50 bg-[var(--amber)]/10 text-sm" data-testid="report-change-note">
              <p className="font-mono text-[10px] uppercase text-[var(--amber)] mb-1">Report updated · {r.reasons?.join("; ")} · {formatDistanceToNow(new Date(r.updated_at))} ago</p>
              {r.change_note}
              <ReportEvidence refs={r.change_evidence} api={api} person={p.id} />
            </div>
          )}
          {r.interpretation_note && <p className="text-sm text-[var(--muted)] italic" data-testid="report-interp-note">{r.interpretation_note}</p>}
          {r.interpretation_note && <ReportEvidence refs={r.interpretation_evidence} api={api} person={p.id} />}
          <BasedOn items={r.based_on} testid="report-based-on" />
          <Compatibility c={r.compatibility} p={p} api={api} />
          {r.compatibility?.evidence_refs?.length > 0 && <ReportEvidence refs={r.compatibility.evidence_refs} api={api} person={p.id} label="Comparison sources" />}
          <div className="grid md:grid-cols-2 2xl:grid-cols-3 gap-3">
            {r.sections.map((s) => (
              <div key={s.key} className={`card p-5 ${!s.read ? "border-dashed" : ""}`} data-testid={`report-section-${s.key}`}>
                <div className="flex items-center gap-2 mb-2"><p className="font-semibold">{s.title}</p></div>
                {s.read && <div className="mb-3"><ConfBar level={s.confidence} /></div>}
                {s.read ? <p className="text-sm leading-relaxed text-[var(--ink)]/85">{s.read}</p>
                  : <p className="text-sm text-[var(--muted)]" data-testid={`report-empty-${s.key}`}>Unclear — {s.empty_note || "not enough evidence yet."}</p>}
                {s.read && <ReportEvidence refs={s.evidence_refs} api={api} person={p.id} />}
                {s.sources?.length > 0 && <p className="text-[11px] text-[var(--muted)] mt-2 font-mono">From: {s.sources.join(" · ")}</p>}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
    <aside className="space-y-4 xl:sticky xl:top-20">
      <div className="card p-5"><p className="font-display font-black uppercase text-xl mb-2">Trait picture</p><TraitRadar traits={r?.traits} />{r?.traits?.map(t=><div key={t.trait} className="mt-2 text-xs">{t.trait}<ReportEvidence refs={t.evidence_refs} api={api} person={p.id} /></div>)}</div>
      <div className="card p-5"><p className="font-display font-black uppercase text-xl mb-4">Timeline</p><PersonTimeline p={p} api={api} /></div>
    </aside>
    </div>
  );
}
