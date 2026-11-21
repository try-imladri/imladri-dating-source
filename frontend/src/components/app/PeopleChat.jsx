import { useEffect, useRef, useState } from "react";
import { ListContinuation, useMemberPages } from "@/components/app/MemberPages";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Plus, Send, Trash2, X, MessagesSquare, Pin } from "lucide-react";
import ChatTab from "@/pages/app/person/ChatTab";
import { SixPart } from "@/components/app/Advice";
import { Starters } from "@/components/app/Starters";
import { LimitNotice, messageLimit, useMessageSend } from "@/components/app/PlanUsage";
import { errMsg } from "@/lib/api";
import { ConversationPaging, useConversationPage } from "@/components/app/ConversationHistory";
import { MemberLoadState } from "@/components/app/MemberResource";

function GroupChat({ sel, api, limit }) {
  const send = useMessageSend();
  const ids = sel.map((p) => p.id).sort().join(",");
  const page = useConversationPage(api, `/group-chat?ids=${ids}`);
  const list = page.rows;
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const thread = useRef(null);
  useEffect(() => {
    if (thread.current) thread.current.scrollTop = thread.current.scrollHeight;
  }, [list, busy]);
  const ask = async (text = q, starterBatch) => {
    setBusy(true);
    try {
      await send("/group-chat", { person_ids: ids.split(","), question: text }, starterBatch ? {"X-Starter-Batch":starterBatch} : {});
      setQ("");
      await page.latest();
    } catch (e) {
      toast.error(errMsg(e));
    }
    setBusy(false);
  };
  const names = sel.map((p) => p.nickname).join(" & ");
  return (
    <div className="flex flex-col h-full min-h-0 gap-3" data-testid="group-chat">
      <ConversationPaging {...page} />
      <div ref={thread} className="flex-1 min-h-0 overflow-y-auto space-y-5 pr-1" data-testid="group-chat-thread">
        {(!list || page.error) && <MemberLoadState label="the group conversation" error={page.error} retry={page.reload} />}
        {!limit && list && !list.length && <p className="card p-4 text-sm text-[var(--muted)]" data-testid="group-chat-empty">Ask anything about {names} together, like who to reply to first or how they affect each other.</p>}
        {list?.map((c) => (
          <div key={c.id} className="space-y-2" data-testid={`group-chat-item-${c.id}`}>
            <div className="flex justify-end items-start gap-2">
              <p className="bubble-you px-4 py-2.5 text-sm max-w-[85%] w-fit whitespace-pre-wrap">{c.question}</p>
              <button onClick={async () => {
                try { await api.delete(`/group-chat/${c.id}`); await page.reload(); }
                catch (error) { toast.error(errMsg(error)); }
              }} className="text-[var(--muted)] hover:text-[var(--crimson)] mt-2" aria-label="Delete" data-testid={`group-chat-delete-${c.id}`}><Trash2 className="w-3.5 h-3.5" /></button>
            </div>
            <SixPart r={c.response} basedOn={c.based_on} narrow />
          </div>
        ))}
        {busy && <p className="text-xs font-mono text-[var(--muted)]" data-testid="group-chat-thinking">Reading all of them…</p>}
      </div>
      {!limit && !busy && <Starters api={api} method="post" url="/group-chat/starters" body={{ person_ids: ids.split(",") }} onPick={ask} />}
      {limit ? <LimitNotice reason={limit} /> : <div className="shrink-0 flex items-end gap-1.5 rounded-2xl border border-[var(--line)] bg-[var(--surface-2)] p-1.5 focus-within:border-[var(--ink)] transition-colors">
        <textarea rows={1} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && q.trim() && !busy) { e.preventDefault(); ask(); } }} placeholder={`Ask about ${names}…`} className="flex-1 min-w-0 resize-none bg-transparent outline-none text-sm py-2 px-2 max-h-32" data-testid="group-chat-input" />
        <button disabled={!q.trim() || busy} onClick={() => ask()} aria-label="Send" className="w-9 h-9 shrink-0 rounded-full grid place-items-center bg-[var(--ink)] text-[var(--bg)] disabled:opacity-30" data-testid="group-chat-send"><Send className="w-4 h-4" /></button>
      </div>}
    </div>
  );
}

function SavedGroups({ api, ids, setIds }) {
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const path = query ? "/chat-groups?q=" + encodeURIComponent(query) : "/chat-groups";
  const page = useMemberPages(api, path, { size: 50 });
  const groups = page.rows || [];
  const load = page.reload;
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const key = [...ids].sort().join(",");
  const current = groups.find((g) => g.person_ids.join(",") === key);
  const save = async () => {
    try {
      await api.post("/chat-groups", { name, person_ids: ids });
      toast.success(`Saved "${name}"`);
      setName("");
      setNaming(false);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  return (
    <div className="mb-3 space-y-2" data-testid="saved-groups">
      <p className="text-xs text-[var(--muted)]">A saved group combines profiles in your private workspace. To work with another account, open Couples.</p>
      {(page.total > 10 || query || search) && <form className="flex gap-2 text-sm" onSubmit={event=>{event.preventDefault(); setQuery(search.trim());}}>
        <input type="search" aria-label="Find a saved group" placeholder="Find a saved group" maxLength={80} value={search} onChange={e=>setSearch(e.target.value)} className="min-w-0 p-1.5 rounded border border-[var(--line)] bg-transparent" />
        <button type="submit" className="underline">Find</button>
        {query && <button type="button" className="underline" onClick={()=>{setSearch("");setQuery("");}}>Clear</button>}
      </form>}
      {query && page.rows && !groups.length && <p role="status" className="text-xs">No saved group matches that name.</p>}
      {page.error && !page.more && <p role="alert" className="text-xs">Couldn’t load saved groups. <button type="button" className="underline" onClick={load}>Try again</button></p>}
      <ListContinuation page={page} label="saved groups" />
      {groups.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {groups.map((g) => (
            <span key={g.id} className={`inline-flex items-center rounded-full border text-xs ${g.id === current?.id ? "border-[var(--ink)] bg-[var(--surface-2)]" : "border-[var(--line)]"}`}>
              <button onClick={() => setIds(g.person_ids)} className="pl-3 pr-1 py-1 inline-flex items-center gap-1 font-semibold" data-testid={`saved-group-${g.id}`}><Pin className="w-3 h-3" />{g.name}</button>
              <button onClick={async () => { try { await api.delete(`/chat-groups/${g.id}`); load(); } catch (error) { toast.error(errMsg(error)); } }} className="pr-2 pl-1 py-1 text-[var(--muted)] hover:text-[var(--crimson)]" aria-label={`Delete ${g.name}`} data-testid={`saved-group-delete-${g.id}`}><X className="w-3 h-3" /></button>
            </span>
          ))}
        </div>
      )}
      {ids.length > 1 && !current && (naming ? (
        <div className="flex gap-1.5">
          <input autoFocus value={name} maxLength={40} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) save(); }} placeholder="Name this group" className="flex-1 min-w-0 text-xs px-3 py-1.5 rounded-full border border-[var(--line)] bg-transparent outline-none focus:border-[var(--ink)]" data-testid="saved-group-name-input" />
          <button disabled={!name.trim()} onClick={save} className="text-xs px-3 py-1.5 rounded-full bg-[var(--ink)] text-[var(--bg)] font-semibold disabled:opacity-40" data-testid="saved-group-save">Save</button>
          <button onClick={() => setNaming(false)} className="text-xs px-2 text-[var(--muted)]" data-testid="saved-group-cancel">Cancel</button>
        </div>
      ) : (
        <button onClick={() => setNaming(true)} className="text-xs inline-flex items-center gap-1 text-[var(--muted)] hover:text-[var(--ink)]" data-testid="saved-group-start"><Pin className="w-3 h-3" />Save this group</button>
      ))}
    </div>
  );
}

export function PeopleChat({ people, ids, setIds, api, me, reload }) {
  const nav = useNavigate();
  const sel = ids.map((id) => people.find((p) => p.id === id)).filter(Boolean);
  return (
    <aside className="card p-4 flex flex-col min-h-0 h-[75vh] xl:h-[calc(100vh-6.5rem)] xl:sticky xl:top-20" data-testid="people-chat">
      <div className="flex items-center gap-2 mb-3"><MessagesSquare className="w-5 h-5" /><p className="font-display font-black uppercase text-2xl leading-none">Chat</p></div>
      <div className="flex flex-wrap gap-1.5 mb-3" data-testid="people-chat-selected">
        {sel.map((p) => (
          <span key={p.id} className="inline-flex items-center gap-1 pl-3 pr-1 py-1 rounded-full bg-[var(--ink)] text-[var(--bg)] text-xs font-semibold" data-testid={`chat-chip-${p.id}`}>
            {p.nickname}<button onClick={() => setIds(ids.filter((x) => x !== p.id))} className="w-5 h-5 rounded-full grid place-items-center hover:bg-white/20" aria-label={`Remove ${p.nickname} from chat`} data-testid={`chat-chip-remove-${p.id}`}><X className="w-3 h-3" /></button>
          </span>
        ))}
      </div>
      <SavedGroups api={api} ids={ids} setIds={setIds} />
      <div className="flex-1 min-h-0">
        {sel.length !== ids.length && <p className="text-sm">Some selected people are unavailable or still loading. <button type="button" className="underline" onClick={() => setIds([])}>Clear selection</button></p>}
        {!ids.length && (
          <div className="h-full flex flex-col items-center justify-center text-center gap-3 text-[var(--muted)]" data-testid="people-chat-empty">
            <span className="w-12 h-12 rounded-full bg-[#16a34a] text-white grid place-items-center"><Plus className="w-6 h-6" /></span>
            <p className="text-sm max-w-[16rem]">Tap the green + on a person to open their chat. Add two or more to ask about them together.</p>
          </div>
        )}
        {sel.length === ids.length && sel.length === 1 && <ChatTab key={sel[0].id} p={sel[0]} api={api} me={me} reload={reload} setTab={(t) => nav(`/app/person/${sel[0].id}?tab=${t}`)} narrow starters />}
        {sel.length === ids.length && sel.length > 1 && <GroupChat key={sel.map((p) => p.id).join()} sel={sel} api={api} limit={messageLimit(me.usage)} />}
      </div>
    </aside>
  );
}
