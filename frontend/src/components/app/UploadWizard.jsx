import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Trash2, ClipboardPaste, Image as ImageIcon } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SafeShot } from "@/components/app/SafeShot";
import { errMsg, stagesFor } from "@/lib/api";

import { LimitNotice, messageLimit, useMessageSend } from "@/components/app/PlanUsage";

const STEPS = ["Add", "Who's who", "Context"];

function parsePaste(text, nick) {
  const n = nick.toLowerCase();
  return text.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const m = l.match(/^([^:]{1,24}):\s*(.+)$/);
    if (m) {
      const who = m[1].trim().toLowerCase();
      const me = ["me", "i", "you", "my"].includes(who);
      return { speaker: me ? "me" : who === n || ["him", "them", "he", "her", "she"].includes(who) ? "them" : "unknown", text: m[2], sourceLabel: m[1] };
    }
    return { speaker: "unknown", text: l };
  });
}

function maskText(t, words) {
  return words.reduce((acc, w) => (w ? acc.replace(new RegExp(w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"), "[name]") : acc), t);
}

function SourceStep({ person, api, onLines, attempt }) {
  const [mode, setMode] = useState("paste");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const read = async (img) => {
    setBusy(true);
    try {
      const { data } = await api.post("/ocr", { image_base64: img, nickname: person.nickname },
        { headers: { "X-Upload-Attempt": attempt } });
      if (!data.lines.length) {
        toast.error("Couldn't find any messages in that image");
        return false;
      }
      onLines(data.lines.map(l => ({ ...l, speaker: ["me", "them"].includes(l.speaker) && l.sure !== false ? l.speaker : "unknown" })));
      return true;
    } catch (e) {
      toast.error(errMsg(e));
      return false;
    } finally { setBusy(false); }
  };
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2">
        {[["paste", "Paste text", ClipboardPaste], ["shot", "Screenshot", ImageIcon]].map(([k, l, I]) => (
          <button key={k} type="button" onClick={() => setMode(k)} data-testid={`upload-mode-${k}`}
            className={`py-3 rounded-xl border flex items-center justify-center gap-2 text-sm ${mode === k ? "border-[var(--emerald)] text-[var(--ink)]" : "border-[var(--line)] text-[var(--muted)]"}`}><I className="w-4 h-4" />{l}</button>
        ))}
      </div>
      {mode === "paste" ? (
        <>
          <Textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} placeholder={`${person.nickname}: hey you up?\nMe: why?\n${person.nickname}: just thinking about you`} data-testid="upload-paste-textarea" />
          <p className="text-xs text-[var(--muted)]">Tip: start lines with "Me:" or "{person.nickname}:" and we'll tag them for you.</p>
          <button type="button" disabled={!text.trim()} onClick={() => onLines(parsePaste(text, person.nickname))} className="w-full py-3 rounded-full bg-[var(--emerald)] text-[var(--on-accent)] font-bold disabled:opacity-40" data-testid="upload-paste-next">Next: who's who</button>
        </>
      ) : (
        <>
          <SafeShot onConfirm={read} busy={busy} label="Read this screenshot" pickLabel="Choose a screenshot" />
        </>
      )}
    </div>
  );
}

function TagStep({ lines, setLines, person, defaults, onNext }) {
  const [words, setWords] = useState(() => [...(defaults?.words || []), ...(defaults?.mask_names ? [] : [])].join(", "));
  const upd = (i, patch) => setLines((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const apply = () => {
    const list = words.split(",").map((w) => w.trim()).filter(Boolean);
    setLines((ls) => ls.map((l) => ({ ...l, text: maskText(l.text, list) })));
    toast.success("Masked");
  };
  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--muted)]">Choose who wrote each message. Remove lines from other people or fix any misread words.</p>
      <div className="space-y-2 max-h-[40vh] overflow-y-auto pr-1" data-testid="tag-lines">
        {lines.map((l, i) => (
          <div key={i} className={`flex gap-2 items-start ${l.speaker === "me" ? "flex-row-reverse" : ""}`}>
            <select aria-label={`Speaker for message ${i + 1}`} value={l.speaker} onChange={e => upd(i, { speaker: e.target.value })} data-testid={`tag-speaker-${i}`}
              className="shrink-0 max-w-[38%] rounded-lg border border-[var(--line)] bg-[var(--bg)] p-1.5 text-xs">
              <option value="unknown">{l.sourceLabel && l.speaker === "unknown" ? l.sourceLabel + " · choose" : "Choose speaker"}</option>
              <option value="me">Me</option><option value="them">{person.nickname}</option>
            </select>
            <Input value={l.text} onChange={(e) => upd(i, { text: e.target.value })} data-testid={`tag-text-${i}`} />
            <button type="button" onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))} className="mt-2 text-[var(--muted)] hover:text-[var(--crimson)]" data-testid={`tag-delete-${i}`}><Trash2 className="w-4 h-4" /></button>
          </div>
        ))}
      </div>
      <div className="card p-3">
        <Label className="text-xs">Redact words (names, places) — replaced with [name]</Label>
        <div className="flex gap-2 mt-1.5">
          <Input value={words} onChange={(e) => setWords(e.target.value)} placeholder="e.g. Jake, Brooklyn" data-testid="redact-words-input" />
          <button type="button" onClick={apply} className="btn-ghost px-4 text-sm" data-testid="redact-words-apply">Mask</button>
        </div>
      </div>
      <button type="button" disabled={!lines.length || lines.some(line => line.speaker === "unknown" || !line.text.trim())} onClick={onNext} className="w-full py-3 rounded-full bg-[var(--emerald)] text-[var(--on-accent)] font-bold" data-testid="tag-next">Next: context</button>
    </div>
  );
}

export function UploadBody({ person, api, me, onDone, previous = [], initialContinuation = "" }) {
  const send = useMessageSend();
  const limit = messageLimit(me?.usage, person);
  const [step, setStep] = useState(0);
  const [lines, setLines] = useState([]);
  const [ctx, setCtx] = useState({ stage: person.stage, goal: "", question: "" });
  const [continuation, setContinuation] = useState(initialContinuation);
  const choices = previous.filter(ex => ex.conversation?.length).slice().reverse();
  const [attempt] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!limit) api.post("/me/event", { type: "upload_started", attempt_id: attempt }).catch(() => {});
  }, [api, attempt, limit]);
  const submit = async () => {
    setBusy(true);
    try {
      const { data } = await send(`/people/${person.id}/exchanges`, { conversation: lines.map(({speaker, text}) => ({speaker, text})), question: ctx.question, context_stage: ctx.stage, context_goal: ctx.goal, ...(continuation ? { continue_exchange_id: continuation } : {}) }, {"X-Upload-Attempt": attempt});
      onDone(data);
    } catch (e) {
      toast.error(errMsg(e));
    }
    setBusy(false);
  };
  if (limit) return <LimitNotice reason={limit} />;
  return (
    <div data-testid="upload-wizard">
      <div className="flex gap-2 mb-5">{STEPS.map((s, i) => <div key={s} className={`flex-1 h-1 rounded-full ${i <= step ? "bg-[var(--emerald)]" : "bg-[var(--line)]"}`} />)}</div>
      {step === 0 && <SourceStep person={person} api={api} attempt={attempt} onLines={(l) => { setLines(l); setStep(1); }} />}
      {step === 1 && <TagStep lines={lines} setLines={setLines} person={person} defaults={me?.redaction_defaults} onNext={() => setStep(2)} />}
      {step === 2 && (
        <div className="space-y-4">
          <div>
            {choices.length > 0 && <label className="block mb-4 text-sm font-semibold">Conversation history
              <select className="block mt-1.5 w-full rounded-md bg-transparent border border-[var(--line)] p-2" value={continuation} onChange={e => setContinuation(e.target.value)} data-testid="tone-thread-select">
                <option value="">Start a separate thread</option>
                {choices.map(ex => <option key={ex.id} value={ex.id}>{new Date(ex.created_at).toLocaleDateString()} · {ex.conversation[0].text.slice(0, 70)}</option>)}
              </select>
            </label>}
            <Label>Stage of this interaction</Label>
            <Select value={ctx.stage} onValueChange={(v) => setCtx((c) => ({ ...c, stage: v }))}>
              <SelectTrigger className="mt-1.5" data-testid="upload-stage-select"><SelectValue /></SelectTrigger>
              <SelectContent>{stagesFor(person.rel_type).map((s) => <SelectItem key={s.v} value={s.v}>{s.l}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div><Label>Goal of this exchange <span className="text-[var(--muted)]">(optional)</span></Label><Input className="mt-1.5" value={ctx.goal} onChange={(e) => setCtx((c) => ({ ...c, goal: e.target.value }))} placeholder="e.g. get a real date on the calendar" data-testid="upload-goal-input" /></div>
          <div><Label>Anything you want to ask? <span className="text-[var(--muted)]">(optional)</span></Label><Textarea className="mt-1.5" rows={2} value={ctx.question} onChange={(e) => setCtx((c) => ({ ...c, question: e.target.value }))} placeholder="e.g. should I reply tonight?" data-testid="upload-question-input" /></div>
          <div className="flex gap-2">
            <button type="button" onClick={() => setStep(1)} className="btn-ghost px-5 py-3" data-testid="upload-back">Back</button>
            <button type="button" disabled={busy} onClick={submit} className="flex-1 py-3 rounded-full bg-[var(--emerald)] text-[var(--on-accent)] font-bold" data-testid="upload-analyze">{busy ? "Reading the situation…" : "Analyze"}</button>
          </div>
        </div>
      )}
    </div>
  );
}

export function UploadWizard({ open, onOpenChange, ...props }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl max-h-[92vh] overflow-y-auto" data-testid="upload-dialog">
        <DialogHeader><DialogTitle className="font-display uppercase text-2xl">Add a conversation</DialogTitle></DialogHeader>
        {open && <UploadBody {...props} onDone={(ex) => { props.onDone(ex); onOpenChange(false); }} />}
      </DialogContent>
    </Dialog>
  );
}
