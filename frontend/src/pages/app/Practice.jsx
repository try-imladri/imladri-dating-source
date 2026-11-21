import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ListContinuation, useMemberPages } from "@/components/app/MemberPages";
import { MemberLoadState } from "@/components/app/MemberResource";
import { useOutletContext, useSearchParams, Link } from "react-router-dom";
import { clearPracticeTurn, readPracticeTurn, storePracticeTurn } from "@/lib/practiceTurn";
import { toast } from "sonner";
import { Copy, Drama, RotateCcw, Send, Square, Trash2, UserCheck } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { errMsg } from "@/lib/api";
import { LimitNotice, messageLimit, useMessageSend } from "@/components/app/PlanUsage";

const MODES = [["live", "Live", "They reply in character"], ["likely", "Scenarios", "Rehearse 3 ways it could go"]];

function Setup({ people, onStart, pid, setPid, initialTopic, limit }) {
  const [topic, setTopic] = useState(initialTopic || "");
  const [mode, setMode] = useState("live");
  const [starting, setStarting] = useState(false);
  const request = useRef(false);
  const start = async () => {
    if (request.current) return;
    request.current = true;
    setStarting(true);
    try {
      if (await onStart({ person_id: pid, topic, mode })) setTopic("");
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      request.current = false;
      setStarting(false);
    }
  };
  return (
    <div className="card p-4 space-y-3" data-testid="practice-setup">
      <p className="font-display font-black uppercase text-xl">New practice</p>
      <Select value={pid} onValueChange={(v) => v && setPid(v)}>
        <SelectTrigger aria-label="Person to practice with" data-testid="practice-person-select"><SelectValue placeholder="Pick a person" /></SelectTrigger>
        <SelectContent>{people.map((p) => <SelectItem key={p.id} value={p.id} data-testid={`practice-person-${p.id}`}>{p.nickname}</SelectItem>)}</SelectContent>
      </Select>
      {limit ? <LimitNotice reason={limit} /> : <>
      <p className="text-xs text-[var(--muted)]">Each practice turn uses one reply, including a turn with three scenarios.</p>
      <Textarea rows={2} maxLength={500} value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="What do you want to bring up? e.g. I need more notice before plans" className="text-sm" data-testid="practice-topic" />
      <div className="grid grid-cols-2 gap-1.5">
        {MODES.map(([k, l, h]) => (
          <button key={k} type="button" aria-pressed={mode === k} onClick={() => setMode(k)} className={`text-left p-2.5 rounded-xl border transition-colors ${mode === k ? "bg-[var(--ink)] text-[var(--bg)] border-[var(--ink)]" : "border-[var(--line)] hover:border-[var(--ink)]"}`} data-testid={`practice-mode-${k}`}>
            <span className="block text-sm font-semibold">{l}</span><span className="block text-[11px] opacity-70">{h}</span>
          </button>
        ))}
      </div>
      <button disabled={starting || !pid || !topic.trim()} onClick={start} className="w-full py-2.5 rounded-full bg-[#16a34a] text-white font-bold text-sm disabled:opacity-40" data-testid="practice-start">{starting ? "Opening rehearsal…" : "Start practising"}</button>
      </>}
    </div>
  );
}

function Feedback({ fb }) {
  async function copy() {
    try {
      await navigator.clipboard.writeText(fb.cleaned);
      toast.success("Copied");
    } catch {
      toast.error("Couldn’t copy. Select the message text to copy it manually.");
    }
  }
  return (
    <div className="card p-4 space-y-3" data-testid="practice-feedback">
      <p className="font-display font-black uppercase text-xl">How you did</p>
      <div><p className="font-mono text-[10px] uppercase text-[var(--muted)] mb-1">What landed</p><ul className="text-sm space-y-1">{fb.landed.map((x, i) => <li key={i} data-testid={`practice-landed-${i}`}>+ {x}</li>)}</ul></div>
      <div><p className="font-mono text-[10px] uppercase text-[var(--muted)] mb-1">What might backfire</p><ul className="text-sm space-y-1">{fb.backfire.map((x, i) => <li key={i} data-testid={`practice-backfire-${i}`}>– {x}</li>)}</ul></div>
      {fb.cleaned && (
        <div className="rounded-xl border-2 border-[var(--ink)] p-3">
          <p className="font-mono text-[10px] uppercase text-[var(--muted)] mb-1">Cleaned-up message</p>
          <p className="text-sm" data-testid="practice-cleaned">“{fb.cleaned}”</p>
          <button onClick={copy} className="mt-2 text-xs inline-flex items-center gap-1 btn-ghost px-3 py-1.5" data-testid="practice-copy"><Copy className="w-3 h-3" />Copy</button>
        </div>
      )}
    </div>
  );
}

function PracticeReflection({ session, api, setSession }) {
  const saved = session.reflection;
  const [result, setResult] = useState(saved?.result || "");
  const [note, setNote] = useState(saved?.note || "");
  const [round, setRound] = useState(saved?.branch ? "saved" : "");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const choices = (session.rounds || []).map((item, index) => ({ index, line: item.options?.[item.picked]?.reaction }))
    .filter(item => item.line);
  async function submit(remove = false) {
    if (busy) return;
    setBusy(true);
    try {
      const { data } = await api.put("/practice/" + session.id + "/reflection", {
        result: remove ? null : result, note: remove ? "" : note,
        round_index: remove || round === "" || round === "saved" ? null : Number(round),
        keep_saved_branch: !remove && round === "saved", expected_turn_revision: session.turn_revision || 0,
        expected_revision: session.reflection_revision || 0,
      });
      setSession(data);
      setFeedback(remove ? "Reflection removed." : "Reflection saved.");
    } catch (error) { setFeedback(errMsg(error)); }
    finally { setBusy(false); }
  }
  return <section className="card p-4 space-y-3" aria-label="Real conversation reflection">
    <h3 className="font-semibold">How did the real conversation go?</h3>
    <p className="text-xs text-[var(--muted)]">This optional note stays with your rehearsal. Saving a reflection uses no replies.</p>
    {saved?.branch && <p className="text-xs">Your saved comparison was with round {saved.branch.round}: “{saved.branch.reaction}”</p>}
    {(choices.length > 0 || saved?.branch) && <label className="block text-sm">Compare with a branch
      <select className="block mt-1 max-w-full" value={round} onChange={e => setRound(e.target.value)}>
        <option value="">Entire rehearsal</option>
        {saved?.branch && <option value="saved">Keep saved comparison · {saved.branch.reaction}</option>}
        {choices.map(item => <option key={item.index} value={item.index}>Round {item.index + 1} · {item.line}</option>)}
      </select></label>}
    <label className="block text-sm">Real outcome
      <select className="block mt-1" value={result} onChange={e => setResult(e.target.value)}>
        <option value="">Choose an outcome</option><option value="similar">Similar to practice</option><option value="different">Different from practice</option><option value="did_not_try">I haven’t tried it</option>
      </select></label>
    <label className="block text-sm">What would you keep or change? (optional)
      <Textarea maxLength={500} rows={2} value={note} onChange={e => setNote(e.target.value)} /></label>
    <div className="flex gap-3"><button className="btn-ghost px-3 py-1.5 text-xs" disabled={busy || !result} onClick={() => submit()}>Save reflection</button>
      {saved && <button className="underline text-xs" disabled={busy} onClick={() => submit(true)}>Remove reflection</button>}</div>
    {feedback && <p role="status" className="text-xs">{feedback}</p>}
  </section>;
}

function ToProfile({ s, api, limit }) {
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(s.profile_saved || null);
  const go = async () => {
    setBusy(true);
    try {
      const { data } = await api.post(`/practice/${s.id}/to-profile`);
      setSaved(data.saved);
      toast.success(data.saved.length ? "Observation saved" : "Nothing clear to add yet");
    } catch (e) {
      toast.error(errMsg(e));
    }
    setBusy(false);
  };
  if (saved?.length) {
    return (
      <div className="basis-full order-last rounded-xl border border-[#16a34a] p-3 text-xs space-y-1.5" data-testid="practice-profile-saved">
        <div className="flex items-center gap-2"><p className="font-semibold inline-flex items-center gap-1.5 flex-1"><UserCheck className="w-3.5 h-3.5 text-[#16a34a]" />Added to Your patterns</p>
          <button disabled={busy || !!limit} onClick={go} className="text-[11px] underline disabled:opacity-40" data-testid="practice-to-profile-again">{busy ? "Adding…" : "Add what's new"}</button></div>
        <ul className="space-y-0.5">{saved.map((x) => <li key={x.id} data-testid={`practice-profile-item-${x.id}`}>• {x.text}{x.merged ? " (linked to an existing observation)" : ""}</li>)}</ul>
        <p className="text-[var(--muted)]">Rehearsals can suggest something to reflect on. An observation can guide advice after you confirm it or it shows up in two distinct real conversations. <Link to="/app/personal?tab=patterns" className="underline text-[var(--ink)]" data-testid="practice-profile-link">Review your observations</Link></p>
      </div>
    );
  }
  return <button disabled={busy || !!limit || !s.messages.some((m) => m.role === "me")} onClick={go} className="text-xs btn-ghost px-3 py-1.5 inline-flex items-center gap-1 disabled:opacity-40" data-testid="practice-to-profile"><UserCheck className="w-3 h-3" />{busy ? "Adding…" : "Add to my profile"}</button>;
}

function Likely({ s, sel, setSel, name }) {
  return (
    <div className="lg:min-h-0 lg:overflow-y-auto p-4 space-y-2 border-t lg:border-t-0 lg:border-l border-[var(--line)]" data-testid="practice-options">
      <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)]">1 · 3 rehearsal scenarios for {name}</p>
      <p className="text-[11px] text-[var(--muted)]">Practice only, not predictions. We don't know which is most likely yet.</p>
      {!s.options?.length && <p className="text-xs text-[var(--muted)]" data-testid="practice-options-empty">{s.messages.at(-1)?.role === "them" ? `Write your response to ${name}.` : "Send a line and three scenarios to rehearse show up here."}</p>}
      {s.options?.map((o, i) => (
        <button key={i} type="button" aria-pressed={sel === i} onClick={() => setSel(i)} className={`w-full text-left rounded-xl border p-3 transition-colors ${sel === i ? "border-[var(--ink)] bg-[var(--surface-2)]" : "border-[var(--line)] hover:border-[var(--ink)]"}`} data-testid={`practice-option-${i}`}>
          <span className="font-mono text-[10px] uppercase px-2 py-0.5 rounded-full border border-[var(--line)]">Scenario {i + 1}{o.tone ? ` · ${o.tone}` : ""}</span>
          <span className="block text-sm mt-1.5">“{o.reaction}”</span>
        </button>
      ))}
    </div>
  );
}

function Outcome({ o, onAdd, onUse, busy, name }) {
  return (
    <div className="lg:min-h-0 lg:overflow-y-auto p-4 space-y-3 border-t lg:border-t-0 lg:border-l border-[var(--line)]" data-testid="practice-outcome">
      <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)]">2 · The outcome</p>
      {!o ? <p className="text-xs text-[var(--muted)]" data-testid="practice-outcome-empty">Pick a scenario to see where it could lead.</p> : (
        <>
          <p className="text-sm bubble-him px-3 py-2 w-fit">“{o.reaction}”</p>
          {o.meaning && <div><p className="text-[10px] font-mono uppercase text-[var(--muted)]">What it could signal</p><p className="text-sm" data-testid="practice-outcome-meaning">{o.meaning}</p></div>}
          {o.outcome && <div><p className="text-[10px] font-mono uppercase text-[var(--muted)]">Where it could go</p><p className="text-sm" data-testid="practice-outcome-where">{o.outcome}</p></div>}
          {o.suggested && (
            <div className="rounded-xl border border-[var(--line)] p-3">
              <p className="text-[10px] font-mono uppercase text-[var(--muted)]">You could answer</p>
              <p className="text-sm" data-testid="practice-outcome-suggested">“{o.suggested}”</p>
              <button disabled={busy} onClick={onUse} className="mt-1.5 text-[11px] underline disabled:opacity-40" data-testid="practice-outcome-use">Use as my starting point</button>
            </div>
          )}
          <button disabled={busy} onClick={onAdd} className="w-full py-2.5 rounded-full bg-[#16a34a] text-white font-bold text-sm disabled:opacity-40" data-testid="practice-accept">Add {name}'s reply to the chat</button>
        </>
      )}
    </div>
  );
}

function Branch({ o, picked, n, pending, onRetry, busy }) {
  const [open, setOpen] = useState(picked);
  return (
    <div className={`self-start w-full rounded-xl border p-2.5 ${picked ? "border-[#16a34a] bg-[var(--surface-2)]" : "border-dashed border-[var(--line)]"}`}>
      <button type="button" aria-expanded={open} aria-controls={`replay-details-${n}`} onClick={() => setOpen(!open)}
        className="w-full text-left" data-testid={`replay-branch-${n}`} data-picked={picked ? "true" : "false"}>
        <span className="flex flex-wrap items-center gap-1.5 mb-1">
          <span className={`font-mono text-[9px] uppercase px-1.5 py-0.5 rounded-full ${picked ? "bg-[#16a34a] text-white" : "border border-[var(--line)]"}`}>{picked ? "picked" : pending ? "not chosen" : "skipped"}</span>
          <span className="font-mono text-[9px] uppercase text-[var(--muted)]">{o.tone || "scenario"}</span>
        </span>
        <span className="block text-xs">“{o.reaction}”</span>
      </button>
      <div id={`replay-details-${n}`} hidden={!open}>
        {o.outcome && <p className="text-[11px] text-[var(--muted)] mt-1.5 border-t border-[var(--line)] pt-1.5">{o.outcome}</p>}
        {!picked && <button type="button" disabled={busy} onClick={onRetry} className="mt-2 text-[11px] px-3 py-1.5 rounded-full bg-[#16a34a] text-white font-bold disabled:opacity-40" data-testid={`replay-retry-${n}`}>Try this path</button>}
      </div>
    </div>
  );
}

function Replay({ s, onRetry, busy }) {
  const rounds = s.rounds || [];
  if (!rounds.length) return null;
  return (
    <div className="card p-4" data-testid="practice-replay">
      <p className="font-display font-black uppercase text-xl">Replay</p>
      <p className="text-xs text-[var(--muted)] mb-3">Open a reply to see one possible outcome. Choose “Try this path” to rehearse it.</p>
      <ol className="space-y-1">
        {rounds.map((r, i) => (
          <li key={i} className="relative pl-6" data-testid={`replay-round-${i}`}>
            <span className="absolute left-[9px] top-0 bottom-0 w-px bg-[var(--line)]" />
            <span className="absolute left-0 top-1 w-5 h-5 rounded-full bg-[var(--ink)] text-[var(--bg)] grid place-items-center font-mono text-[9px]">{i + 1}</span>
            <p className="bubble-you px-3 py-1.5 text-xs w-fit ml-auto max-w-[85%] mb-2">{r.line}</p>
            <div className="grid sm:grid-cols-3 gap-2 pb-3">{r.options.map((o, j) => <Branch key={j} o={o} picked={j === r.picked} pending={r.picked === null || r.picked === undefined} n={`${i}-${j}`} busy={busy} onRetry={() => onRetry(i, j)} />)}</div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function Session({ s, setS, api, name, limit, me }) {
  const send = useMessageSend();
  const [pending, setPending] = useState(() => readPracticeTurn(me, s.id));
  const [text, setText] = useState(pending?.body.text || "");
  const [sendError, setSendError] = useState("");
  const sending = useRef(false);
  const [busy, setBusy] = useState("");
  const [sel, setSel] = useState(null);
  const box = useRef(null);
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [s, busy]);
  useEffect(() => {
    setSel(null);
    if (s.options?.length && window.innerWidth < 1024) setTimeout(() => document.querySelector('[data-testid="practice-options"]')?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  }, [s.options]);
  const call = async (label, fn) => {
    setBusy(label);
    try {
      setS((await fn()).data);
    } catch (e) {
      toast.error(errMsg(e));
    }
    setBusy("");
  };
  const say = async () => {
    if (sending.current) return;
    sending.current = true;
    setBusy(s.mode === "live" ? `${name} is typing…` : "Preparing scenarios…");
    const turn = pending || { key: crypto.randomUUID(), body: { text, expected_revision: s.turn_revision || 0 } };
    storePracticeTurn(me, s.id, turn);
    setPending(turn);
    setSendError("");
    try {
      const result = await send(`/practice/${s.id}/say`, turn.body, { "Idempotency-Key": turn.key });
      clearPracticeTurn(me, s.id, turn.key);
      setPending(null);
      setS(result.data);
      setText("");
    } catch (error) {
      const status = error.response?.status;
      if (status === 409 && /rehearsal changed|session changed/i.test(errMsg(error))) {
        try {
          setS((await api.get("/practice/" + s.id)).data);
          clearPracticeTurn(me, s.id, turn.key);
          setPending(null);
        } catch { /* The same turn can be recovered after reconnection. */ }
      } else if (status >= 400 && status < 500 && status !== 409) {
        clearPracticeTurn(me, s.id, turn.key);
        setPending(null);
      }
      setSendError(errMsg(error));
    } finally {
      sending.current = false;
      setBusy("");
    }
  };
  const showSaved = async () => {
    if (sending.current) return;
    sending.current = true;
    setBusy("Checking saved turns…");
    try {
      const { data } = await api.get("/practice/" + s.id);
      setS(data);
      if (data.contact_pause) {
        clearPracticeTurn(me, s.id, pending.key);
        setPending(null);
        setText("");
        setSendError("");
      } else {
        setSendError("This is the latest saved conversation. Recover your turn to confirm whether your original request was saved.");
      }
    } catch (error) { setSendError(errMsg(error)); }
    finally {
      sending.current = false;
      setBusy("");
    }
  };
  const turnLocked = Boolean(busy || pending);
  const likely = s.mode === "likely";
  const split = likely && !s.ended;
  const waitingPick = split && s.options?.length > 0;
  if (s.contact_pause) return <section className="card p-5 space-y-4" data-testid="practice-contact-pause">
    <h2 className="font-display font-black uppercase text-xl">Respect their contact preference</h2>
    <p>{s.contact_pause}</p><p className="text-sm">No reply was used to open this guidance.</p>
    <PracticeReflection key={s.id + ":" + (s.reflection_revision || 0)} session={s} api={api} setSession={setS} />
  </section>;
  return (
    <div className={`card flex flex-col min-h-0 ${s.mode === "likely" ? "lg:h-[calc(100vh-6.5rem)]" : "h-[75vh] xl:h-[calc(100vh-6.5rem)]"}`} data-testid="practice-session">
      <div className="px-4 py-2.5 bg-[var(--ink)] text-[var(--bg)] rounded-t-[inherit] flex items-center gap-2" data-testid="practice-banner">
        <Drama className="w-4 h-4" /><span className="font-mono text-[11px] uppercase tracking-widest">Practice · not real advice · {s.mode === "live" ? "Live" : "Likely replies"}</span>
      </div>
      <div className="px-4 pt-3 flex flex-wrap items-center gap-2">
        <p className="text-sm flex-1 min-w-0"><span className="text-[var(--muted)]">With {name}:</span> {s.topic}</p>
        <Link className="text-xs underline" to={"/app/practice?" + new URLSearchParams({ person: s.person_id, session: s.id })}>Link to this rehearsal</Link>
        <ToProfile key={s.id} s={s} api={api} limit={limit || turnLocked} />
        <button disabled={turnLocked} onClick={() => call("Restarting…", async () => {
          const result = await api.post(`/practice/${s.id}/restart`);
          setText(""); setSendError("");
          return result;
        })} className="text-xs btn-ghost px-3 py-1.5 inline-flex items-center gap-1" data-testid="practice-restart"><RotateCcw className="w-3 h-3" />Restart</button>
        <button disabled={turnLocked || !!limit || !s.messages.length} onClick={() => call("Writing feedback…", () => api.post(`/practice/${s.id}/end`))} className="text-xs px-3 py-1.5 rounded-full bg-[var(--ink)] text-[var(--bg)] font-semibold inline-flex items-center gap-1 disabled:opacity-40" data-testid="practice-end"><Square className="w-3 h-3" />End & get feedback</button>
      </div>
      <div className={`flex-1 min-h-0 ${split ? "flex flex-col lg:grid lg:grid-cols-[1.3fr_1fr_1fr] lg:overflow-hidden" : "flex flex-col"}`}>
      <div className={`flex flex-col ${split ? "h-[60vh] lg:h-auto lg:min-h-0" : likely ? "min-h-0 flex-1 max-lg:h-auto" : "min-h-0 flex-1"}`}>
      <div ref={box} className={`flex-1 p-4 space-y-2 ${likely && !split ? "lg:min-h-0 lg:overflow-y-auto" : "min-h-0 overflow-y-auto"}`} data-testid="practice-thread">
        {!s.messages.length && <p className="text-sm text-[var(--muted)]" data-testid="practice-empty">{s.mode === "live" ? `Say it the way you would. ${name} will answer in character.` : "Write your opening line. Three scenarios to rehearse show up next to the chat."}</p>}
        {s.messages.map((m, i) => <div key={i} className={`px-3 py-2 text-sm max-w-[80%] w-fit ${m.role === "me" ? "bubble-you ml-auto" : "bubble-him"}`} data-testid={`practice-msg-${i}`}>{m.text}</div>)}
        {busy && <p className="text-xs font-mono text-[var(--muted)]" data-testid="practice-busy">{busy}</p>}
        {s.feedback && <Feedback fb={s.feedback} />}
        {(s.ended || s.reflection) && <PracticeReflection key={s.id + ":" + (s.reflection_revision || 0)} session={s} api={api} setSession={setS} />}
        {s.ended && <Replay s={s} busy={turnLocked} onRetry={(round, index) => { if (window.confirm("Practise from this reply? Later rounds and the feedback will be replaced.")) call("Rewinding to that reply…", () => api.post(`/practice/${s.id}/branch`, { round, index })); }} />}
      </div>
      {sendError && <p role="alert" className="px-3 py-1 text-sm">{sendError}</p>}
      {pending && !busy && <div className="px-3 py-2 border-t border-[var(--line)] text-sm">
        <p>Your last turn is awaiting confirmation.</p>
        <button onClick={say} className="underline mr-3">Recover my turn</button>
        <button className="underline" onClick={showSaved}>Show saved conversation</button>
      </div>}
      {limit ? <LimitNotice reason={limit} /> : <div className="p-3 border-t border-[var(--line)] flex items-end gap-1.5">
        <textarea rows={1} value={text} disabled={waitingPick || !!busy || !!pending} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && text.trim() && !busy && !waitingPick) { e.preventDefault(); say(); } }} placeholder={waitingPick ? "Pick a scenario first…" : s.messages.length ? `Your response to ${name}…` : "Your opening line…"} className="flex-1 min-w-0 resize-none bg-[var(--surface-2)] rounded-2xl outline-none text-sm py-2.5 px-3 max-h-32 disabled:opacity-50" data-testid="practice-input" />
        <button disabled={!text.trim() || !!busy || !!pending || waitingPick} onClick={say} aria-label="Send" className="w-10 h-10 shrink-0 rounded-full grid place-items-center bg-[var(--ink)] text-[var(--bg)] disabled:opacity-30" data-testid="practice-send"><Send className="w-4 h-4" /></button>
      </div>}
      </div>
      {split && <Likely s={s} sel={sel} setSel={setSel} name={name} />}
      {split && <Outcome o={sel !== null ? s.options?.[sel] : null} busy={turnLocked} name={name} onUse={() => setText(s.options[sel].suggested)}
        onAdd={() => call("Adding to the chat…", () => api.post(`/practice/${s.id}/pick`, { index: sel }))} />}
      </div>
    </div>
  );
}

export default function Practice() {
  const { api, me } = useOutletContext();
  const [params, setParams] = useSearchParams();
  const directory = useMemberPages(api, "/people", { field: "people", size: 200 });
  const { more: moreContacts, busy: loadingContacts, error: contactError } = directory;
  const people = useMemo(() => directory.rows?.filter(p => !p.archived), [directory.rows]);
  const linkedPerson = params.get("person");
  const linkedSession = params.get("session");
  const pid = linkedPerson || (people?.find(p => p.can_message !== false) || people?.[0])?.id || "";
  const [opened, setS] = useState(null);
  const [deletions, setDeletions] = useState(0);
  const s = opened?.id === linkedSession && opened?.person_id === pid ? opened : null;
  useEffect(() => {
    if (linkedPerson && people && !people.some(p => p.id === linkedPerson) && moreContacts && !loadingContacts && !contactError) moreContacts();
  }, [people, linkedPerson, moreContacts, loadingContacts, contactError]);
  const sessions = useMemberPages(api, people?.some(p => p.id === pid) ? "/practice?person_id=" + encodeURIComponent(pid) : null, { revision: `${s?.id || ""}/${deletions}` });
  const { rows: list, error: listError, reload: load } = sessions;
  const [failedSession, setFailedSession] = useState(null);
  const selected = useRef(0);
  useEffect(() => {
    setFailedSession(null);
    return () => { selected.current += 1; };
  }, [pid, linkedSession]);
  const openSession = useCallback(async (id) => {
    const ticket = ++selected.current;
    setFailedSession(null);
    try {
      const { data } = await api.get("/practice/" + id);
      if (ticket !== selected.current) return;
      if (data.person_id !== pid) {
        setS(null);
        setFailedSession({ id, error: "This rehearsal belongs to a different person." });
        return;
      }
      setS(data);
    } catch (error) {
      if (ticket === selected.current) setFailedSession({id, error: errMsg(error)});
    }
  }, [api, pid]);
  const selectSession = (id) => {
    selected.current += 1;
    if (params.get("session") === id && params.get("person") === pid) openSession(id);
    else setParams({person: pid, session: id});
  };
  useEffect(() => {
    if (linkedSession && linkedPerson === pid) openSession(linkedSession);
  }, [linkedSession, linkedPerson, pid, openSession]);
  const changePerson = id => {
    selected.current += 1;
    setParams({person:id});
  };
  const deleteSession = async id => {
    const ticket = selected.current;
    try {
      await api.delete(`/practice/${id}`);
      if (ticket === selected.current && linkedSession === id) {
        selected.current += 1;
        setS(null);
        setFailedSession(null);
        setParams({person:pid}, {replace:true});
      }
      setDeletions(count => count + 1);
    } catch (error) { toast.error(errMsg(error)); }
  };
  const startSession = async body => {
    const ticket = ++selected.current;
    const {data} = await api.post("/practice", body);
    if (ticket !== selected.current) return false;
    setS(data);
    setParams({person:body.person_id, session:data.id});
    return true;
  };
  if (!people) return <MemberLoadState label="your practice contacts" error={directory.error} retry={directory.reload} />;
  const name = (id) => people.find((p) => p.id === id)?.nickname || "them";
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] xl:grid-cols-[360px_minmax(0,1fr)] gap-6 items-start" data-testid="practice-page">
      <div className="space-y-4 min-w-0">
        <div>
          <h1 className="font-display font-black uppercase text-5xl" data-testid="practice-title">Practice</h1>
          <p className="text-sm text-[var(--muted)]">Rehearse a hard conversation before you have it. It's only practice.</p>
        </div>
        {!people.length ? <p className="card p-5 text-sm text-[var(--muted)]" data-testid="practice-no-people">Add a person first, then practise with them here.</p> : <Setup key={pid} people={people} onStart={startSession} pid={people.some(p => p.id === pid) ? pid : ""} setPid={changePerson} initialTopic={params.get("topic") || ""} limit={messageLimit(me.usage, people.find((p) => p.id === pid))} />}
        <ListContinuation page={directory} label="practice contacts" />
        {failedSession && <MemberLoadState label="this practice session" error={failedSession.error} retry={() => openSession(failedSession.id)} />}
        {listError && !sessions.more && <MemberLoadState label="your saved sessions" error={listError} retry={load} />}
        <ListContinuation page={sessions} label="sessions" />
        {list?.length > 0 && (
          <div className="space-y-1.5" data-testid="practice-list">
            <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)]">Saved sessions with {name(pid)}</p>
            {list.map((x) => (
              <div key={x.id} className={`flex items-center gap-2 rounded-xl border px-3 py-2 ${s?.id === x.id ? "border-[var(--ink)]" : "border-[var(--line)]"}`}>
                <button onClick={() => selectSession(x.id)} className="flex-1 min-w-0 text-left text-sm truncate" data-testid={`practice-open-${x.id}`}>{x.topic}<span className="text-[11px] text-[var(--muted)] ml-1.5">{x.mode === "live" ? "Live" : "Likely"}</span></button>
                <button onClick={() => deleteSession(x.id)} className="text-[var(--muted)] hover:text-[var(--crimson)]" aria-label={`Delete rehearsal: ${x.topic}`} data-testid={`practice-delete-${x.id}`}><Trash2 className="w-3.5 h-3.5" /></button>
              </div>
            ))}
          </div>
        )}
      </div>
      {s && !Array.isArray(s.messages) ? <section className="card p-4" data-testid="practice-incomplete-session"><h2 className="font-semibold">{s.topic}</h2><p className="text-sm">This older session has no saved transcript.</p></section> : s ? <Session key={[me.user_id, me.created_at, me.content_revision, s.id].join("/")} me={me} s={s} setS={updated => setS(current => current?.id === updated.id ? updated : current)} api={api} name={name(s.person_id)} limit={messageLimit(me.usage, people.find((p) => p.id === s.person_id))} /> : (
        <div className="card p-10 text-center text-[var(--muted)] hidden xl:flex flex-col items-center justify-center h-[calc(100vh-6.5rem)]" data-testid="practice-placeholder">
          <Drama className="w-10 h-10 mb-3" /><p className="text-sm max-w-xs">Pick a person, say what you want to bring up and choose a mode.</p>
        </div>
      )}
    </div>
  );
}
