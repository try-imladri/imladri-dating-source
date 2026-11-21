import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useOutletContext } from "react-router-dom";
import { MessageSquareText, Send, Trash2 } from "lucide-react";
import { SixPart } from "@/components/app/Advice";
import { LimitNotice, messageLimit, useMessageSend } from "@/components/app/PlanUsage";
import { Textarea } from "@/components/ui/textarea";
import { errMsg } from "@/lib/api";

function FollowUp({ item, endpoint, api, active, onSaved }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(item.outcome || "");
  const [helped, setHelped] = useState(item.helped || "not_tried");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const save = async () => {
    setBusy(true); setError("");
    try {
      await api.patch(`${endpoint}/${item.id}/outcome`, { text, helped });
      if (active()) { setOpen(false); onSaved(); }
    } catch (failure) { if (active()) setError(errMsg(failure)); }
    finally { if (active()) setBusy(false); }
  };
  if (!open) return <button className="text-sm underline" onClick={() => setOpen(true)}>{item.outcome ? "Edit follow-up" : "What happened next?"}</button>;
  return <div className="card p-4 space-y-3">
    <label className="block text-sm font-semibold" htmlFor={`outcome-${item.id}`}>What happened next?</label>
    <Textarea id={`outcome-${item.id}`} value={text} maxLength={2000} onChange={e => setText(e.target.value)} rows={3} />
    <label className="block text-sm">Did this advice help?
      <select className="block mt-1 input w-full" value={helped} onChange={e => setHelped(e.target.value)}>
        <option value="not_tried">Not tried yet</option><option value="helped">It helped</option>
        <option value="mixed">Mixed</option><option value="didnt_help">It didn't help</option>
      </select>
    </label>
    {error && <p role="alert" className="text-sm">{error}</p>}
    <div className="flex gap-3"><button className="btn-lime px-4 py-2 text-sm" onClick={save} disabled={busy}>Save follow-up</button>
      <button className="btn-ghost px-4 py-2 text-sm" onClick={() => setOpen(false)} disabled={busy}>Cancel</button></div>
  </div>;
}

function AdviceSession({ workspace, requestedReply }) {
  const { api, me } = useOutletContext();
  const send = useMessageSend();
  // Keep the private-data identity and its request client together. A changed
  // account, API session or workspace gets a new scope before any response renders.
  const scope = useMemo(() => ({
    active: true, api, ownerId: me.user_id,
    endpoint: workspace === "couples" ? "/couples/quick-advice" : "/quick-advice",
  }), [api, me.user_id, workspace]);
  const endpoint = scope.endpoint;
  const [saved, setSaved] = useState(null);
  const [error, setError] = useState("");
  const [question, setQuestion] = useState("");
  const [parent, setParent] = useState(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [cursor, setCursor] = useState("");
  const [focused, setFocused] = useState(null);
  const request = useRef(null);
  const detailRequest = useRef(null);
  const located = useRef(null);
  const input = useRef(null);
  const lock = useRef(false);
  const data = saved?.scope === scope ? saved.data : null;
  const focusedItem = focused?.scope === scope ? focused.item : null;
  const items = focusedItem && !data?.items?.some(item => item.id === focusedItem.id)
    ? [focusedItem, ...(data?.items || [])] : data?.items || [];
  const current = () => scope.active;
  const load = useCallback(async (nextCursor = "") => {
    request.current?.abort();
    detailRequest.current?.abort(); located.current = null; setFocused(null);
    const abort = new AbortController(); request.current = abort;
    setLoading(true); setError("");
    try {
      const response = await scope.api.get(scope.endpoint + (nextCursor ? `?cursor=${encodeURIComponent(nextCursor)}` : ""), { signal: abort.signal });
      if (scope.active && !abort.signal.aborted) setSaved({ scope, data: response.data });
    } catch (failure) { if (scope.active && !abort.signal.aborted) setError(errMsg(failure)); }
    finally { if (scope.active && !abort.signal.aborted) setLoading(false); }
  }, [scope]);
  useEffect(() => {
    scope.active = true;
    setSaved(null); setQuestion(""); setParent(null); setCursor(""); setFocused(null); located.current = null;
    setError(""); setBusy(false); lock.current = false;
    load("");
    return () => { scope.active = false; request.current?.abort(); detailRequest.current?.abort(); };
  }, [scope, load]);
  useEffect(() => {
    if (!requestedReply || !data || located.current === requestedReply) return;
    located.current = requestedReply;
    detailRequest.current?.abort();
    const abort = new AbortController(); detailRequest.current = abort;
    const focus = () => {
      if (!scope.active || abort.signal.aborted) return;
      const element = document.getElementById(`quick-advice-record-${requestedReply}`);
      element?.focus(); element?.scrollIntoView?.({ block: "nearest" });
    };
    const present = data.items?.find(item => item.id === requestedReply);
    if (present) { focus(); return; }
    api.get(`${endpoint}/${encodeURIComponent(requestedReply)}`, { signal: abort.signal }).then(({ data: item }) => {
      if (scope.active && !abort.signal.aborted && item.id === requestedReply) setFocused({ scope, item });
    }).catch(failure => { if (scope.active && !abort.signal.aborted) setError(errMsg(failure)); });
  }, [requestedReply, data, api, endpoint, scope]);
  useEffect(() => {
    if (focusedItem?.id !== requestedReply) return;
    const element = document.getElementById(`quick-advice-record-${requestedReply}`);
    element?.focus(); element?.scrollIntoView?.({ block: "nearest" });
  }, [focusedItem, requestedReply]);
  const limit = messageLimit(data?.usage || me.usage);
  const submit = async event => {
    event.preventDefault();
    if (lock.current || !question.trim() || limit || !current()) return;
    lock.current = true; setBusy(true); setError("");
    const body = { question: question.trim(), ...(parent ? { parent_id: parent.id } : {}) };
    try {
      const response = await send(endpoint, body);
      if (!current()) return;
      setSaved({ scope, data: { ...(data || {}), items: [response.data, ...(data?.items || []).filter(x => x.id !== response.data.id)] } });
      setQuestion(""); setParent(null); setCursor("");
      await load("");
    } catch (failure) { if (current()) setError(errMsg(failure)); }
    finally { if (current()) { lock.current = false; setBusy(false); } }
  };
  const remove = async item => {
    if (!window.confirm("Delete this Quick Advice conversation and its follow-ups?")) return;
    try {
      await api.delete(`${endpoint}/${item.id}`);
      if (current()) {
        if (parent?.thread_id === item.thread_id) setParent(null);
        if (focusedItem?.thread_id === item.thread_id) setFocused(null);
        await load(cursor);
      }
    } catch (failure) { if (current()) setError(errMsg(failure)); }
  };
  return <div className="w-full min-w-0 max-w-6xl mx-auto space-y-5" data-testid="quick-advice-page">
    <header><h1 className="font-display uppercase font-black text-3xl">Quick Advice</h1>
      <p className="text-sm text-[var(--muted)] mt-2">Describe a situation and get a practical next step. No People profile needed.</p>
      <p className="text-xs text-[var(--muted)] mt-1">Each new answer uses one message from your existing allowance. {workspace === "couples" ? "Your advice stays private; relevant mutually shared assessments can inform it." : "Your saved profile can help personalize it."}</p>
    </header>
    <form onSubmit={submit} className="card p-4 sm:p-5 space-y-3">
      <label htmlFor="quick-advice-question" className="font-semibold text-sm">What's happening?</label>
      {parent && <div className="flex items-start justify-between gap-3 text-sm border-l-2 border-[var(--ink)] pl-3"><p className="break-words">Following up: {parent.question.slice(0, 160)}</p><button type="button" onClick={() => setParent(null)} className="underline shrink-0">New situation</button></div>}
      <Textarea ref={input} id="quick-advice-question" value={question} maxLength={6000} rows={4}
        onChange={e => setQuestion(e.target.value)} disabled={busy} placeholder="What happened, what did they actually say, and what would you like help with?" />
      <div className="flex flex-wrap justify-between items-center gap-3"><span className="text-xs text-[var(--muted)]">{question.length.toLocaleString()} / 6,000</span>
        <button type="submit" className="btn-lime px-5 py-2.5 text-sm inline-flex items-center gap-2" disabled={busy || !question.trim() || !!limit || !data}>
          <Send aria-hidden="true" className="w-4 h-4" />{busy ? "Preparing advice…" : "Get advice"}</button></div>
      {limit && <LimitNotice reason={limit} />}
    </form>
    {error && <div role="alert" className="card p-4 text-sm"><p>{error}</p><button className="underline mt-2" onClick={() => load(cursor)}>Refresh history</button></div>}
    <section aria-label="Saved Quick Advice" className="space-y-5">
      <h2 className="font-display uppercase font-black text-xl">Your saved advice</h2>
      {!data && loading && <p role="status" className="text-sm">Loading your saved advice…</p>}
      {data?.items?.length === 0 && <p className="text-sm text-[var(--muted)]">Your answers and follow-ups will appear here.</p>}
      {items.map(item => <article key={item.id} id={`quick-advice-record-${item.id}`} tabIndex={-1}
        className="card p-4 sm:p-5 space-y-4 focus:outline focus:outline-2 focus:outline-[var(--ink)]" data-testid={`quick-advice-${item.id}`}>
        <div className="flex items-center justify-between gap-3 text-xs text-[var(--muted)]"><span>{new Date(item.created_at).toLocaleString()}</span>
          <button aria-label="Delete this advice conversation" onClick={() => remove(item)} className="p-2"><Trash2 className="w-4 h-4" /></button></div>
        <p className="font-semibold whitespace-pre-wrap break-words">{item.question}</p>
        {item.context_changed ? <p className="text-sm">This advice used shared information that has changed or was withdrawn. Start a new situation to use current information.</p>
          : item.response && <><SixPart r={item.response} basedOn={item.based_on} />
            {item.response.unknowns?.length > 0 && <div className="text-sm"><p className="font-semibold">What could change this advice</p><ul className="list-disc pl-5 mt-1">{item.response.unknowns.map((text, index) => <li key={index}>{text}</li>)}</ul></div>}
          </>}
        {item.outcome && <p className="text-sm whitespace-pre-wrap break-words"><strong>Your follow-up: </strong>{item.outcome}</p>}
        <div className="flex flex-wrap items-center gap-4">
          {!item.context_changed && <button className="text-sm underline inline-flex items-center gap-1" onClick={() => { setParent(item); input.current?.focus(); }}><MessageSquareText className="w-4 h-4" />Ask a follow-up</button>}
          <FollowUp key={`${item.id}:${item.outcome_at || ""}`} item={item} endpoint={endpoint} api={api} active={current} onSaved={() => load(cursor)} />
        </div>
      </article>)}
      <nav className="flex gap-4 text-sm" aria-label="Quick Advice history pages">
        {cursor && <button className="underline" onClick={() => { setCursor(""); load(""); }} disabled={loading}>Latest advice</button>}
        {data?.next_cursor && <button className="underline" onClick={() => { setCursor(data.next_cursor); load(data.next_cursor); }} disabled={loading}>Earlier advice</button>}
      </nav>
    </section>
  </div>;
}

export default function QuickAdvice() {
  const { me } = useOutletContext();
  const { pathname, search } = useLocation();
  const workspace = pathname.startsWith("/app/couples/") ? "couples" : "individual";
  const requestedReply = new URLSearchParams(search).get("reply") || "";
  return <AdviceSession key={`${me.user_id}:${workspace}:${requestedReply}`} workspace={workspace} requestedReply={requestedReply} />;
}
