import { useState } from "react";
import { MemberLoadState, useMemberResource } from "@/components/app/MemberResource";
import { useOutletContext } from "react-router-dom";
import { toast } from "sonner";
import { Star } from "lucide-react";
import { Textarea } from "@/components/ui/textarea";
import { errMsg } from "@/lib/api";
import { BasedOn } from "@/components/app/GroupIcon";
import { OutcomeButton } from "@/components/app/Advice";
import { LimitNotice, messageLimit, useMessageSend } from "@/components/app/PlanUsage";

function Plan({ x, api, onPin, reload }) {
  const pl = x.plan || {};
  const avoid = [pl.avoid || []].flat().filter((item) => typeof item === "string");
  const reactions = [pl.reactions || []].flat().filter(Boolean);
  return (
    <div className={`card p-5 space-y-4 ${x.pinned ? "border-[var(--ink)]" : ""}`} data-testid={`prep-${x.id}`}>
      <div className="flex items-start gap-3">
        <p className="flex-1 min-w-0 text-xs font-mono text-[var(--muted)]">{x.pinned && <span className="text-[var(--ink)] font-semibold" data-testid={`prep-pinned-label-${x.id}`}>PINNED · </span>}{new Date(x.created_at).toLocaleString()} · {x.situation}</p>
        <button onClick={() => onPin(x)} aria-label={x.pinned ? "Unpin game plan" : "Pin game plan"} title={x.pinned ? "Unpin" : "Pin to top"} className="shrink-0 w-8 h-8 rounded-full grid place-items-center border border-[var(--line)] hover:border-[var(--ink)] transition-colors" data-testid={`prep-pin-${x.id}`}>
          <Star className="w-4 h-4" fill={x.pinned ? "currentColor" : "none"} />
        </button>
      </div>
      <BasedOn items={x.based_on} testid={`prep-based-on-${x.id}`} />
      {pl.contact_boundary ? (
        <section className="space-y-2" data-testid="prep-respect-boundary">
          <h3 className="font-semibold">Give them the space they asked for</h3>
          <ul className="list-disc pl-5 text-sm space-y-1">{avoid.map((item, i) => <li key={i}>{item}</li>)}</ul>
        </section>
      ) : <>
        <div className="p-4 rounded-xl bg-[var(--emerald)]/10 border border-[var(--emerald)]/40"><p className="font-mono text-[10px] uppercase text-[var(--emerald)] mb-1">Opening line</p><p>“{pl.opening_line}”</p></div>
        <div>
          <p className="font-semibold text-sm mb-2">Ways the conversation could go</p>
          <div className="space-y-2">
            {reactions.map((r, i) => (
              <div key={i} className="p-3 rounded-xl border border-[var(--line)]" data-testid={`prep-reaction-${i}`}>
                <div className="flex justify-between gap-3"><p className="text-sm font-semibold">{typeof r === "string" ? r : r.reaction}</p><span className="font-mono text-[10px] uppercase text-[var(--muted)] shrink-0">scenario {i + 1}</span></div>
                {r.how_to_handle && <p className="text-sm mt-1"><span className="font-medium">Your next move</span> · {r.how_to_handle}</p>}
              </div>
            ))}
          </div>
        </div>
        <div><p className="font-semibold text-sm mb-1">Avoid</p><ul className="list-disc pl-5 text-sm space-y-1">{avoid.map((a, i) => <li key={i}>{a}</li>)}</ul></div>
        <div className="p-3 rounded-xl border border-[var(--crimson)]/40"><p className="font-mono text-[10px] uppercase text-[var(--crimson)] mb-1">If it goes badly</p><p className="text-sm">“{pl.exit_line}”</p></div>
      </>}
      <p className="text-xs text-[var(--muted)] italic">{pl.uncertainty}</p>
      <div className="pt-3 border-t border-[var(--line)] flex flex-wrap items-center gap-3">
        {x.outcome && <p className="text-xs text-[var(--emerald)] flex-1 min-w-0" data-testid={`prep-outcome-text-${x.id}`}>How it went: {x.outcome}</p>}
        <OutcomeButton ex={x} api={api} onSaved={reload} url={`/preps/${x.id}/outcome`} label="How did it go?" testid={`prep-outcome-button-${x.id}`} />
      </div>
    </div>
  );
}

export default function PrepareTab({ p, api, reload: reloadPerson, selectedPlan }) {
  const { me } = useOutletContext();
  const limit = messageLimit(me.usage, p);
  const send = useMessageSend();
  const planPath = `/people/${p.id}/preps`;
  const { data, error, status, reload: load } = useMemberResource(api, selectedPlan ? planPath + "/" + encodeURIComponent(selectedPlan) : planPath);
  const list = selectedPlan ? (data ? [data] : null) : data;
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const pin = async (x) => {
    try {
      await api.patch(`/preps/${x.id}`, { pinned: !x.pinned });
      toast.success(x.pinned ? "Unpinned" : "Pinned to the top");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  const go = async () => {
    setBusy(true);
    try {
      await send(`/people/${p.id}/preps`, { situation: text });
      await load();
      setText("");
    } catch (e) {
      toast.error(errMsg(e));
    }
    setBusy(false);
  };
  return (
    <div className="grid lg:grid-cols-2 gap-5 items-start" data-testid="prepare-tab">
      {limit ? <LimitNotice reason={limit} /> : <div className="card p-5 lg:sticky lg:top-4" data-testid="prepare-input-panel">
        <p className="font-display font-black uppercase text-2xl">I'm about to talk to {p.nickname}</p>
        <p className="text-sm text-[var(--muted)] mt-1 mb-3">Describe what's coming up — a hard conversation, a date, asking for a raise.</p>
        <Textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Dinner Friday. I want to ask where this is going without scaring him off." data-testid="prepare-input" />
        {me.usage && !me.usage.unlimited && <p className="text-xs mt-2 text-[var(--muted)]">A new game plan uses one reply.</p>}
        <button disabled={text.trim().length < 3 || busy} onClick={go} className="mt-3 px-5 py-2.5 rounded-full bg-[var(--emerald)] text-[var(--on-accent)] font-bold text-sm disabled:opacity-40" data-testid="prepare-submit">{busy ? "Building your plan…" : "Get my game plan"}</button>
      </div>}
      <div className="space-y-5 min-w-0" data-testid="prepare-output-panel">
        {busy && <div className="card p-5 font-mono text-sm text-[var(--muted)]" data-testid="prepare-thinking">Building your plan…</div>}
        {selectedPlan && <a className="text-sm underline" href={"/app/person/" + encodeURIComponent(p.id) + "?tab=prepare"}>View all game plans</a>}
        {selectedPlan && status === 404 ? <p role="status">This game plan is no longer available.</p> :
          (!list || error) && <MemberLoadState label={selectedPlan ? "this game plan" : "the saved game plans"} error={error} retry={load} />}
        {list && !error && !list.length && !busy && (
          <div className="card p-5 text-sm text-[var(--muted)]" data-testid="prepare-empty">Your game plan shows up here — opening line, possible reactions to rehearse, what to avoid and a graceful exit.</div>
        )}
        {list?.map((x) => <Plan key={x.id} x={x} api={api} onPin={pin} reload={() => { load(); reloadPerson(); }} />)}
      </div>
    </div>
  );
}
