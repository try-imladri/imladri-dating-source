import { useState } from "react";
import { MemoryReview } from "@/components/app/Advice";
import { ConversationPaging, useConversationPage } from "@/components/app/ConversationHistory";
import { MemberLoadState, useMemberResource } from "@/components/app/MemberResource";
import { toast } from "sonner";
import { errMsg } from "@/lib/api";
import { SentReplyFollowup } from "@/components/app/ToneCoach";

function LaterOutcome(props) {
  const [open, setOpen] = useState(false);
  return open ? <HowDidItGo {...props} onDone={() => setOpen(false)} /> : <button onClick={() => setOpen(true)} className="text-xs btn-ghost px-3 py-1.5" data-testid={`timeline-log-${props.ex.id}`}>Log what happened</button>;
}
import { Trash2 } from "lucide-react";
import { ActionPicker, HowDidItGo, OutcomeView } from "@/components/app/ShowWhy";

function Step({ n, title, children, last }) {
  return (
    <div className="relative pl-8 pb-4" data-testid={`timeline-step-${title.toLowerCase().replace(/\W+/g, "-")}`}>
      {!last && <span className="absolute left-[11px] top-6 bottom-0 w-px bg-[var(--line)]" />}
      <span className="absolute left-0 top-0 w-6 h-6 rounded-full border border-[var(--ink)] grid place-items-center font-mono text-[10px]">{n}</span>
      <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)] mb-1">{title}</p>
      {children}
    </div>
  );
}

function Entry({ ex, p, api, reload }) {
  const del = async () => {
    if (!window.confirm("Delete this step: the conversation, what you chose and what happened?")) return;
    try {
      await api.delete(`/exchanges/${ex.id}`);
      reload();
    } catch (error) { toast.error(errMsg(error)); }
  };
  const first = ex.conversation?.[0]?.text || ex.question || "";
  return (
    <article className="card p-4 sm:p-5" data-testid={`timeline-entry-${ex.id}`}>
      <div className="flex items-center justify-between mb-3">
        <span className="font-mono text-xs text-[var(--muted)]">{new Date(ex.created_at).toLocaleDateString()}</span>
        <button onClick={del} className="text-[var(--muted)] hover:text-[var(--crimson)]" aria-label="Delete step" data-testid={`timeline-delete-${ex.id}`}><Trash2 className="w-3.5 h-3.5" /></button>
      </div>
      <Step n={1} title={ex.has_conversation ? "Conversation" : "What you asked"}>
        <p className="text-sm line-clamp-2">{first}</p>
        {ex.response?.what_it_means && <p className="text-xs text-[var(--muted)] mt-1">Read: {ex.response.what_it_means}</p>}
      </Step>
      <Step n={2} title="What you chose">
        {ex.outcome && !ex.choice ? <p className="text-xs text-[var(--muted)]">Not recorded</p> : ex.response?.actions?.length || ex.choice ? <ActionPicker ex={ex} api={api} reload={reload} /> : <p className="text-xs text-[var(--muted)]">No suggested actions on this older answer</p>}
      </Step>
      <Step n={3} title="What happened" last>
        <SentReplyFollowup key={ex.id + "/" + (ex.tone_revision || 0)} ex={ex} api={api} />
        {ex.outcome ? <OutcomeView ex={ex} api={api} reload={reload} nickname={p.nickname} /> : ex.choice ? <HowDidItGo ex={ex} api={api} reload={reload} nickname={p.nickname} /> : <LaterOutcome ex={ex} api={api} reload={reload} nickname={p.nickname} />}
      </Step>
    </article>
  );
}

function FocusedExchange({ p, api, exchange, reload: reloadPerson, setTab }) {
  const target = useMemberResource(api, `/people/${p.id}/exchanges/${encodeURIComponent(exchange)}`);
  const reload = () => Promise.all([target.reload(), reloadPerson()]);
  return <section className="space-y-4" data-testid="focused-exchange">
    <button type="button" className="text-sm underline" onClick={() => setTab("history")}>All history</button>
    {target.status === 404 ? <p>This conversation is no longer available.</p> : !target.data || target.error ?
      <MemberLoadState label="this conversation" error={target.error} retry={target.reload} /> :
      <><Entry ex={target.data} p={p} api={api} reload={reload} />
        {!target.data.memories_reviewed && target.data.proposed_memories?.length > 0 && <MemoryReview key={target.data.id} ex={target.data} api={api} onSaved={reload} />}</>}
  </section>;
}

export default function HistoryTab(props) {
  return props.exchange ? <FocusedExchange key={props.exchange} {...props} /> : <FullHistory {...props} />;
}
function FullHistory({ p, api, reload: reloadPerson }) {
  const page = useConversationPage(api, `/people/${p.id}/exchanges`);
  const exs = page.rows;
  const load = () => Promise.all([page.reload(), reloadPerson()]);
  if (!exs || page.error) return <MemberLoadState label="the conversation history" error={page.error} retry={page.reload} />;
  const pending = exs.filter((e) => e.choice && !e.outcome);
  return (
    <div className="space-y-4" data-testid="history-tab">
      <div>
        <h2 className="font-display font-black uppercase text-3xl">History with {p.nickname}</h2>
        <p className="text-sm text-[var(--muted)]">Conversation → what you chose → what happened. Edit or delete any step and the advice adjusts.</p>
      </div>
      <ConversationPaging {...page} />
      {pending.length > 0 && <p className="text-sm font-semibold" data-testid="history-pending">{pending.length} on this page waiting for "How did it go?"</p>}
      {!exs.length && <p className="card p-5 text-sm text-[var(--muted)]" data-testid="history-empty">Nothing yet. Chat about {p.nickname} and pick what you'll do.</p>}
      <div className="grid lg:grid-cols-2 gap-4">{[...exs].reverse().map((ex) => <Entry key={ex.id} ex={ex} p={p} api={api} reload={load} />)}</div>
    </div>
  );
}
