import { ContextMemories } from "@/components/app/CoachingEvidence";
import { useEffect, useRef, useState } from "react";
import { ConversationPaging, useConversationPage } from "@/components/app/ConversationHistory";
import { MemberLoadState } from "@/components/app/MemberResource";
import { Starters } from "@/components/app/Starters";
import { toast } from "sonner";
import { ChevronDown, Plus, Send, Trash2 } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { UploadWizard } from "@/components/app/UploadWizard";
import { LimitNotice, messageLimit, useMessageSend } from "@/components/app/PlanUsage";
import { SixPart, MemoryReview } from "@/components/app/Advice";
import { ShowWhy, ActionPicker, HowDidItGo, OutcomeView } from "@/components/app/ShowWhy";
import { IndividualTone } from "@/components/app/ToneCoach";
import { QuickQuestion } from "@/components/app/QuickQuestion";
import { errMsg } from "@/lib/api";

function HowDidItGoLink(props) {
  const [open, setOpen] = useState(false);
  return open ? <HowDidItGo {...props} onDone={() => setOpen(false)} /> : <button onClick={() => setOpen(true)} className="text-xs btn-ghost px-3 py-1.5" data-testid={`outcome-button-${props.ex.id}`}>What actually happened?</button>;
}

function Exchange({ ex, p, api, reload, setTab, narrow, last, onContinue }) {
  const why = Array.isArray(ex.response?.seen) && ex.response.seen.length > 0;
  const del = async () => {
    if (!window.confirm("Delete this conversation, its advice and the memories saved from it?")) return;
    try {
      await api.delete(`/exchanges/${ex.id}`);
      reload();
    } catch (error) { toast.error(errMsg(error)); }
  };
  return (
    <div className="space-y-3" data-testid={`exchange-${ex.id}`}>
      <div className="flex items-center justify-between text-xs text-[var(--muted)] font-mono">
        <span>{new Date(ex.created_at).toLocaleString()}</span>
        <button onClick={del} aria-label={`Delete conversation from ${new Date(ex.created_at).toLocaleString()}`} className="hover:text-[var(--crimson)]" data-testid={`exchange-delete-${ex.id}`}><Trash2 className="w-3.5 h-3.5" /></button>
      </div>
      {ex.question && <p className="bubble-you px-4 py-2.5 text-sm ml-auto max-w-[80%] w-fit whitespace-pre-wrap">{ex.question}</p>}
      <IndividualTone ex={ex} api={api} />
      {why ? <ShowWhy ex={ex} api={api} reload={reload} nickname={p.nickname} /> : (
        <>
          {ex.conversation?.length > 0 && (
            <Collapsible>
              <CollapsibleTrigger className="text-sm inline-flex items-center gap-1 text-[var(--muted)] hover:text-[var(--ink)]" data-testid={`exchange-toggle-${ex.id}`}>
                <ChevronDown className="w-4 h-4" />Conversation ({ex.conversation.length} messages)
              </CollapsibleTrigger>
              <CollapsibleContent className="mt-2 flex flex-col gap-1.5">
                {ex.conversation.map((l, i) => (
                  <div key={i} className={`px-3 py-2 text-sm max-w-[80%] ${l.speaker === "me" ? "bubble-you self-end" : "bubble-him self-start"}`}>{l.text}</div>
                ))}
              </CollapsibleContent>
            </Collapsible>
          )}
          <SixPart r={ex.response} hideDraft={(!!ex.tone || ex.tone_invalidated) && !ex.response?.limited_reply} basedOn={ex.based_on} narrow={narrow} />
        </>
      )}
      <ContextMemories exchange={ex.id} api={api} />
      {!ex.memories_reviewed && ex.proposed_memories?.length > 0 && <MemoryReview ex={ex} api={api} onSaved={reload} />}
      {why && !ex.outcome && <ActionPicker ex={ex} api={api} reload={reload} />}
      {ex.outcome ? <OutcomeView ex={ex} api={api} reload={reload} nickname={p.nickname} /> : ex.choice && <HowDidItGo ex={ex} api={api} reload={reload} nickname={p.nickname} />}
      {!why && !ex.outcome && !ex.choice && <HowDidItGoLink ex={ex} api={api} reload={reload} nickname={p.nickname} />}
      {!!ex.conversation?.length && onContinue && <button type="button" className="text-sm underline" onClick={() => onContinue(ex.id)}>Continue this thread</button>}
      {last && <QuickQuestion api={api} />}
      {ex.response?.deepen_prompt && (
        <button onClick={() => setTab("profile")} className="text-xs text-left text-[var(--emerald)] hover:underline" data-testid="deepen-prompt">
          Want a fuller read? {ex.response.deepen_prompt} →
        </button>
      )}
    </div>
  );
}

export default function ChatTab({ p, api, me, reload: reloadPerson, setTab, narrow, starters }) {
  const send = useMessageSend();
  const limit = messageLimit(me.usage, p);
  const page = useConversationPage(api, `/people/${p.id}/exchanges`);
  const exs = page.rows;
  const [open, setOpen] = useState(false);
  const [continuation, setContinuation] = useState("");
  const openUpload = (id = "") => { setContinuation(id); setOpen(true); };
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const thread = useRef(null);
  const input = useRef(null);
  useEffect(() => {
    const el = thread.current;
    if (!el) return;
    const last = el.querySelector("[data-exchange]:last-of-type");
    if (busy || !last) el.scrollTop = el.scrollHeight;
    else el.scrollTop = last.offsetTop - el.offsetTop;
  }, [exs, busy]);
  const load = () => Promise.all([page.reload(), reloadPerson()]);
  const showNew = () => Promise.all([page.latest(), reloadPerson()]);

  const ask = async (text = q, starterBatch) => {
    setBusy(true);
    try {
      await send(`/people/${p.id}/exchanges`, { question: text }, starterBatch ? {"X-Starter-Batch":starterBatch} : {});
      setQ("");
      if (input.current) input.current.style.height = "auto";
      await showNew();
    } catch (e) {
      toast.error(errMsg(e));
    }
    setBusy(false);
  };

  if (!exs || page.error) return <MemberLoadState label="the conversation" error={page.error} retry={page.reload} />;
  const composer = limit ? <LimitNotice reason={limit} /> : (
    <div className="shrink-0 flex items-end gap-1.5 rounded-2xl border border-[var(--line)] bg-[var(--surface-2)] p-1.5 focus-within:border-[var(--ink)] transition-colors" data-testid="chat-composer">
      <button onClick={() => openUpload()} title="Add a conversation" aria-label="Add a conversation" className="w-9 h-9 shrink-0 rounded-full grid place-items-center hover:bg-[var(--line)] transition-colors" data-testid="open-upload-wizard"><Plus className="w-5 h-5" /></button>
      <textarea ref={input} rows={1} value={q} onChange={(e) => { setQ(e.target.value); e.target.style.height = "auto"; e.target.style.height = `${e.target.scrollHeight}px`; }} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && q.trim() && !busy) { e.preventDefault(); ask(); } }} placeholder={`Ask about ${p.nickname}…`} className="flex-1 min-w-0 resize-none bg-transparent outline-none text-sm py-2 max-h-32" data-testid="chat-question-input" />
      <button disabled={!q.trim() || busy} onClick={() => ask()} aria-label="Send" className="w-9 h-9 shrink-0 rounded-full grid place-items-center bg-[var(--ink)] text-[var(--bg)] disabled:opacity-30 transition-opacity" data-testid="chat-ask-button">{busy ? "…" : <Send className="w-4 h-4" />}</button>
    </div>
  );
  const empty = !limit && !exs.length && (
    <div className="card p-5" data-testid="chat-empty">
      <p className="font-semibold">Start with a real conversation.</p>
      <p className="text-sm text-[var(--muted)] mt-1">Paste the conversation or add a screenshot. You can blur the image before transcription, then edit the words and speakers before asking for advice.</p>
    </div>
  );
  if (narrow) {
    return (
      <div className="flex flex-col h-full min-h-0 gap-3" data-testid="chat-tab">
        <ConversationPaging {...page} />
        <div ref={thread} className="flex-1 min-h-0 overflow-y-auto space-y-4 pr-1" data-testid="chat-thread">
          {empty}
          {exs.map((ex, i) => <div key={ex.id} data-exchange><Exchange onContinue={limit ? null : openUpload} ex={ex} p={p} api={api} reload={load} setTab={setTab} narrow last={i === exs.length - 1 && !busy} /></div>)}
          {busy && <p className="text-xs font-mono text-[var(--muted)]" data-testid="chat-thinking">Reading the situation…</p>}
        </div>
        {starters && !limit && !busy && <Starters api={api} url={`/people/${p.id}/starters`} onPick={ask} disabled={busy} />}
        {composer}
        <UploadWizard previous={exs} initialContinuation={continuation} open={open} onOpenChange={setOpen} person={p} api={api} me={me} onDone={showNew} />
      </div>
    );
  }
  return (
    <div className="space-y-10" data-testid="chat-tab">
      <ConversationPaging {...page} />
      {composer}
      {busy && <p className="text-xs font-mono text-[var(--muted)]" data-testid="chat-thinking">Reading the situation…</p>}
      {empty}
      {[...exs].reverse().map((ex) => <Exchange onContinue={limit ? null : openUpload} key={ex.id} ex={ex} p={p} api={api} reload={load} setTab={setTab} />)}
      <UploadWizard previous={exs} initialContinuation={continuation} open={open} onOpenChange={setOpen} person={p} api={api} me={me} onDone={showNew} />
    </div>
  );
}

