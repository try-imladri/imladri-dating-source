import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";
import { Check, Copy, Eye, HelpCircle, Lightbulb, Undo2, X, Drama } from "lucide-react";
import { SafeShot } from "@/components/app/SafeShot";
import { Textarea } from "@/components/ui/textarea";
import { BasedOn } from "@/components/app/GroupIcon";
import { errMsg } from "@/lib/api";

const LIKE = { "more likely": "bg-[var(--ink)] text-[var(--bg)]", possible: "border border-[var(--ink)]", "less likely": "border border-[var(--line)] text-[var(--muted)]" };
export const RESULTS = [["better", "Better"], ["same", "Same"], ["worse", "Worse"], ["no_reply", "No reply yet"]];

function Convo({ lines, glow, nickname }) {
  const box = useRef(null);
  useEffect(() => {
    if (!glow.length || !box.current) return;
    box.current.querySelector(`[data-msg="${glow[0]}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [glow]);
  return (
    <div ref={box} className="flex flex-col gap-1.5 max-h-72 overflow-y-auto p-1" data-testid="why-conversation">
      {lines.map((l, i) => (
        <div key={i} data-msg={i} data-testid={`why-msg-${i}`} data-glow={glow.includes(i) ? "true" : "false"}
          className={`px-3 py-2 text-sm max-w-[85%] transition-shadow duration-300 ${l.speaker === "me" ? "bubble-you self-end" : "bubble-him self-start"} ${glow.includes(i) ? "ring-2 ring-[#16a34a] shadow-[0_0_18px_rgba(22,163,74,.45)]" : glow.length ? "opacity-50" : ""}`}>
          <span className="block font-mono text-[9px] uppercase opacity-60">{l.speaker === "me" ? "You" : nickname}</span>{l.text}
        </div>
      ))}
    </div>
  );
}

function Point({ x, k, i, ex, api, reload, active, onFocus }) {
  const key = `${k}:${i}`;
  const wrong = (ex.corrected || []).includes(key);
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const mark = async () => {
    try {
      await api.post(`/exchanges/${ex.id}/correct`, { kind: k, index: i, note });
      toast.success("Got it. The next answer will take this into account.");
      setOpen(false);
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  const undo = async () => {
    await api.delete(`/exchanges/${ex.id}/correct?kind=${k}&index=${i}`);
    reload();
  };
  return (
    <li className={`rounded-xl p-3 transition-colors ${active ? "bg-[var(--surface-2)] ring-1 ring-[var(--ink)]" : "hover:bg-[var(--surface-2)]"}`} data-testid={`why-${k}-${i}`}>
      <button onClick={onFocus} className="text-left w-full" data-testid={`why-${k}-${i}-focus`}>
        <span className={`text-sm leading-relaxed ${wrong ? "line-through text-[var(--muted)]" : ""}`}>{x.text}</span>
        <span className="flex flex-wrap items-center gap-1.5 mt-1.5">
          {x.likelihood && x.msgs?.length > 0 && <span className={`font-mono text-[10px] uppercase px-2 py-0.5 rounded-full ${LIKE[x.likelihood] || LIKE.possible}`} data-testid={`why-${k}-${i}-likelihood`}>{x.likelihood}</span>}
          {x.likelihood && !x.msgs?.length && <span className="font-mono text-[10px] uppercase px-2 py-0.5 rounded-full border border-dashed border-[var(--line)] text-[var(--muted)]" data-testid={`why-${k}-${i}-likelihood`}>possible · no messages yet</span>}
          {x.msgs?.length > 0 && <span className="text-[10px] font-mono text-[var(--muted)] inline-flex items-center gap-1"><Eye className="w-3 h-3" />{x.msgs.length} message{x.msgs.length > 1 ? "s" : ""}</span>}
        </span>
      </button>
      {x.tells_apart && <p className="text-xs text-[var(--muted)] mt-1.5">What would tell: {x.tells_apart}</p>}
      {wrong ? (
        <button onClick={undo} className="mt-1.5 text-[11px] inline-flex items-center gap-1 text-[var(--muted)] hover:text-[var(--ink)]" data-testid={`why-${k}-${i}-undo`}><Undo2 className="w-3 h-3" />Marked not right · Undo</button>
      ) : open ? (
        <div className="mt-2 flex gap-1.5">
          <input autoFocus value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} placeholder="What's off? (optional)" className="flex-1 min-w-0 text-xs px-3 py-1.5 rounded-full border border-[var(--line)] bg-transparent outline-none focus:border-[var(--ink)]" data-testid={`why-${k}-${i}-note`} />
          <button onClick={mark} className="text-xs px-3 py-1.5 rounded-full bg-[var(--ink)] text-[var(--bg)] font-semibold" data-testid={`why-${k}-${i}-confirm`}>Send</button>
          <button onClick={() => setOpen(false)} aria-label="Cancel" className="text-[var(--muted)]"><X className="w-3.5 h-3.5" /></button>
        </div>
      ) : (
        <button onClick={() => setOpen(true)} className="mt-1.5 text-[11px] text-[var(--muted)] hover:text-[var(--crimson)]" data-testid={`why-${k}-${i}-wrong`}>That's not right</button>
      )}
    </li>
  );
}

function Section({ title, I, tone, children, testid }) {
  return (
    <section className={`rounded-2xl border border-[var(--line)] border-l-[3px] ${tone} p-3`} data-testid={testid}>
      <p className="font-mono text-[10px] uppercase tracking-widest mb-1 inline-flex items-center gap-1.5 px-3"><I className="w-3.5 h-3.5" />{title}</p>
      {children}
    </section>
  );
}

export function ActionPicker({ ex, api, reload }) {
  const [own, setOwn] = useState("");
  const [later, setLater] = useState(false);
  const nav = useNavigate();
  const acts = ex.response?.actions || [];
  const showActionWording = !(ex.tone || ex.tone_invalidated) || ex.response?.limited_reply;
  const practise = (a) => nav(`/app/practice?person=${ex.person_id}&topic=${encodeURIComponent([a.label, (showActionWording && a.say) || a.detail].filter(Boolean).join(": "))}`);
  const pick = async (label, custom = false) => {
    try {
      await api.post(`/exchanges/${ex.id}/choice`, { label, custom });
      toast.success("Saved. I'll ask how it went.");
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  if (ex.choice) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm" data-testid={`choice-made-${ex.id}`}>
        <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-[#16a34a] text-white font-semibold"><Check className="w-3.5 h-3.5" />You chose: {ex.choice.label}</span>
        <button onClick={async () => { await api.delete(`/exchanges/${ex.id}/choice`); reload(); }} className="text-xs text-[var(--muted)] hover:text-[var(--ink)]" data-testid={`choice-change-${ex.id}`}>Change</button>
      </div>
    );
  }
  if (later) return <button onClick={() => setLater(false)} className="text-xs text-[var(--muted)] hover:text-[var(--ink)]" data-testid={`choice-reopen-${ex.id}`}>Pick what to do</button>;
  if (!acts.length) return null;
  return (
    <div className="space-y-2" data-testid={`actions-${ex.id}`}>
      <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)]">What will you do?</p>
      {acts.map((a, i) => (
        <div key={i} className="rounded-xl border border-[var(--line)] p-3 flex items-start gap-3" data-testid={`action-${ex.id}-${i}`}>
          <div className="flex-1 min-w-0">
            <p className="font-semibold text-sm">{a.label}</p>
            {a.detail && <p className="text-xs text-[var(--muted)] mt-0.5">{a.detail}</p>}
            {a.say && showActionWording && !ex.response?.contact_boundary && (
              <button onClick={() => { navigator.clipboard?.writeText(a.say); toast.success("Copied"); }} className="mt-1.5 text-xs text-left inline-flex gap-1.5 items-start hover:underline" data-testid={`action-copy-${ex.id}-${i}`}>
                <Copy className="w-3 h-3 mt-0.5 shrink-0" />“{a.say}”
              </button>
            )}
          </div>
          <span className="shrink-0 flex flex-col items-end gap-1.5">
            <button onClick={() => pick(a.label)} className="px-3 py-1.5 rounded-full bg-[#15803d] text-white text-xs font-bold" data-testid={`action-pick-${ex.id}-${i}`}>Do this</button>
            {!ex.response?.contact_boundary && <button onClick={() => practise(a)} className="text-[11px] inline-flex items-center gap-1 text-[var(--muted)] hover:text-[var(--ink)]" data-testid={`action-practise-${ex.id}-${i}`}><Drama className="w-3 h-3" />Practise this first</button>}
          </span>
        </div>
      ))}
      <div className="flex gap-1.5">
        <input value={own} maxLength={200} onChange={(e) => setOwn(e.target.value)} placeholder="Or write your own" className="flex-1 min-w-0 text-xs px-3 py-2 rounded-full border border-[var(--line)] bg-transparent outline-none focus:border-[var(--ink)]" data-testid={`action-own-${ex.id}`} />
        <button disabled={!own.trim()} onClick={() => pick(own.trim(), true)} className="text-xs px-3 py-2 rounded-full bg-[var(--ink)] text-[var(--bg)] font-semibold disabled:opacity-40" data-testid={`action-own-save-${ex.id}`}>Save</button>
      </div>
      <button onClick={() => setLater(true)} className="text-xs text-[var(--muted)] hover:text-[var(--ink)]" data-testid={`action-later-${ex.id}`}>I'll decide later</button>
    </div>
  );
}

export function HowDidItGo({ ex, api, reload, nickname, onDone }) {
  const [result, setResult] = useState(ex.outcome_result || "");
  const [note, setNote] = useState(ex.outcome_note || "");
  const [busy, setBusy] = useState("");
  const shot = async (b64) => {
    setBusy("Reading screenshot…");
    try {
      const { data } = await api.post("/ocr", { image_base64: b64 });
      if (!data.lines.length) {
        toast.error("Couldn't find any messages in that image");
        return false;
      }
      setNote((n) => `${n ? `${n}\n` : ""}${data.lines.map((l) => `${l.speaker === "me" ? "Me" : nickname || "Them"}: ${l.text}`).join("\n")}`);
      return true;
    } catch (err) {
      toast.error(errMsg(err));
      return false;
    } finally { setBusy(""); }
  };
  const save = async () => {
    setBusy("Updating the read…");
    try {
      await api.post(`/exchanges/${ex.id}/outcome`, { result: result || null, text: note });
      toast.success("Saved. The read is updated.");
      onDone?.();
      reload();
    } catch (err) {
      toast.error(errMsg(err));
    }
    setBusy("");
  };
  return (
    <div className="card p-4 space-y-3" data-testid={`how-did-it-go-${ex.id}`}>
      <p className="font-display font-black uppercase text-xl leading-none">How did it go?</p>
      {ex.choice && <p className="text-xs text-[var(--muted)]">You chose: {ex.choice.label}</p>}
      <div className="flex flex-wrap gap-1.5">
        {RESULTS.map(([k, l]) => (
          <button key={k} onClick={() => setResult(k)} className={`px-3 py-1.5 rounded-full text-xs font-semibold border transition-colors ${result === k ? "bg-[var(--ink)] text-[var(--bg)] border-[var(--ink)]" : "border-[var(--line)] hover:border-[var(--ink)]"}`} data-testid={`outcome-result-${k}`}>{l}</button>
        ))}
      </div>
      <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a note (optional)" className="text-sm" data-testid="outcome-note" />
      <div data-testid="outcome-screenshot"><SafeShot compact onConfirm={shot} busy={!!busy} label="Read it" /></div>
      <div className="flex items-center gap-2">
        <span className="flex-1" />
        <button disabled={!!busy || (!result && !note.trim())} onClick={save} className="px-4 py-2 rounded-full bg-[#15803d] text-white text-xs font-bold disabled:opacity-40" data-testid="outcome-save">{busy || "Save"}</button>
      </div>
    </div>
  );
}

export function OutcomeView({ ex, api, reload, nickname }) {
  const [edit, setEdit] = useState(false);
  if (edit) return <HowDidItGo ex={ex} api={api} reload={reload} nickname={nickname} onDone={() => setEdit(false)} />;
  const u = ex.read_update;
  return (
    <div className="rounded-xl border border-[var(--line)] p-3 space-y-1.5" data-testid={`outcome-view-${ex.id}`}>
      <p className="text-sm"><span className="font-mono text-[10px] uppercase text-[var(--muted)] mr-1.5">What happened</span>{ex.outcome}</p>
      {u?.changed && u.earlier && <p className="text-xs border-l-2 border-amber-500 pl-2" data-testid={`read-update-${ex.id}`}>Earlier we thought <em>{u.earlier}</em>; this suggests <strong>{u.now}</strong></p>}
      {u?.note && <p className="text-xs text-[var(--muted)]">{u.note}</p>}
      <div className="flex gap-3 text-[11px]">
        <button onClick={() => setEdit(true)} className="text-[var(--muted)] hover:text-[var(--ink)]" data-testid={`outcome-edit-${ex.id}`}>Edit</button>
        <button onClick={async () => { await api.delete(`/exchanges/${ex.id}/outcome`); reload(); }} className="text-[var(--muted)] hover:text-[var(--crimson)]" data-testid={`outcome-delete-${ex.id}`}>Delete</button>
      </div>
    </div>
  );
}

export function ShowWhy({ ex, api, reload, nickname }) {
  const r = ex.response || {};
  const [focus, setFocus] = useState(null);
  const glow = focus ? r[focus.k]?.[focus.i]?.msgs || [] : [];
  const tap = (k, i) => setFocus(focus?.k === k && focus?.i === i ? null : { k, i });
  const list = (k) => (
    <ul className="space-y-0.5">
      {(r[k] || []).map((x, i) => <Point key={i} x={x} k={k} i={i} ex={ex} api={api} reload={reload} active={focus?.k === k && focus?.i === i} onFocus={() => tap(k, i)} />)}
    </ul>
  );
  return (
    <div className="space-y-3" data-testid="show-why">
      {ex.based_on?.length > 0 && <BasedOn items={ex.based_on} />}
      {r.revision?.previous && <p className="text-xs border-l-2 border-amber-500 pl-2" data-testid="revision-note">Earlier we thought <em>{r.revision.previous}</em>; now <strong>{r.revision.now}</strong> because {r.revision.because}</p>}
      {ex.conversation?.length > 0 && <Convo lines={ex.conversation} glow={glow} nickname={nickname} />}
      <Section title="What we can see" I={Eye} tone="border-l-sky-500" testid="why-seen">{list("seen")}</Section>
      <Section title="Possible explanations" I={Lightbulb} tone="border-l-amber-500" testid="why-explanations">{list("explanations")}</Section>
      {r.unknowns?.length > 0 && (
        <Section title="What we don't know" I={HelpCircle} tone="border-l-zinc-400" testid="why-unknowns">
          <ul className="px-3 pb-1 space-y-1 text-sm">{r.unknowns.map((u, i) => <li key={i} className="flex gap-2" data-testid={`why-unknown-${i}`}><span className="text-[var(--muted)]">?</span>{u}</li>)}</ul>
        </Section>
      )}
    </div>
  );
}
