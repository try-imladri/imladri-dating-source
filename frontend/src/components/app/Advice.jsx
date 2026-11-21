import { useState } from "react";
import { toast } from "sonner";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Conf } from "@/components/app/Meter";
import { BasedOn } from "@/components/app/GroupIcon";
import { errMsg } from "@/lib/api";

const PARTS = [
  ["what_they_are_doing", "What they're actually doing"],
  ["what_it_means", "What it likely means"],
  ["what_changed", "What changed since last time"],
  ["best_move", "Your best move"],
  ["what_to_say", "What to say — word for word"],
  ["what_not_to_do", "What not to do"],
];

export function SixPart({ r, basedOn, narrow, hideDraft = false }) {
  if (r.contact_boundary === true) {
    return (
      <div className="card p-5 space-y-2" data-testid="contact-boundary">
        <p className="font-semibold">Give them the space they asked for</p>
        <p className="text-sm">{r.best_move}</p>
        {r.what_not_to_do && <p className="text-sm text-[var(--muted)]">{r.what_not_to_do}</p>}
        <BasedOn items={basedOn} />
      </div>
    );
  }
  if (r.no_new_evidence) {
    return <div className="space-y-2"><p className="card p-5 text-sm" data-testid="no-new-evidence">The evidence is the same as last time. You can leave this here until something changes.</p><BasedOn items={basedOn} /></div>;
  }
  return (
    <div className={`grid gap-3 ${narrow ? "grid-cols-1 [&>*]:!col-span-1" : "md:grid-cols-2"}`} data-testid="six-part-response">
      {basedOn?.length > 0 && <div className="md:col-span-2"><BasedOn items={basedOn} /></div>}
      {r.revision?.previous && (
        <div className="md:col-span-2 p-4 rounded-xl border border-[var(--amber)] text-sm" data-testid="revision-note">
          <p className="font-mono text-[10px] uppercase text-[var(--amber)] mb-1">Revised read</p>
          Previously I read this as <em>{r.revision.previous}</em>; now <strong>{r.revision.now}</strong> — because {r.revision.because}
        </div>
      )}
      {PARTS.filter(([key]) => r[key] && !(hideDraft && key === "what_to_say")).map(([k, t], i) => (
        <div key={k} className={`p-4 rounded-xl border ${k === "what_to_say" ? "md:col-span-2 border-[var(--ink)] border-2" : "border-[var(--line)] bg-[var(--surface)]"}`} data-testid={`part-${k}`}>
          <div className="flex items-center gap-2 mb-1.5">
            <span className="font-mono text-[10px] text-[var(--emerald)]">{i + 1}</span>
            <p className="font-semibold text-sm">{t}</p>
            {k === "what_it_means" && <Conf level={r.confidence} />}
          </div>
          <p className={`text-sm leading-relaxed ${k === "what_to_say" ? "text-base" : "text-[var(--ink)]/85"}`}>{k === "what_to_say" ? `“${r[k]}”` : r[k]}</p>
        </div>
      ))}
    </div>
  );
}

const KIND_LABEL = { event: "Reported event", interpretation: "Interpretation", assessment: "Working assessment" };

export function MemoryReview({ ex, api, onSaved }) {
  const [items, setItems] = useState(() => (ex.proposed_memories || []).map((m) => ({ ...m, keep: true })));
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      const { data } = await api.post(`/exchanges/${ex.id}/memories`, { memories: items.filter((m) => m.keep).map(({ kind, text }) => ({ kind, text })) });
      toast.success(`${data.saved} memories saved`);
      onSaved();
    } catch (e) {
      toast.error(errMsg(e));
    }
    setBusy(false);
  };
  return (
    <div className="card p-4 border-dashed" data-testid="memory-review">
      <p className="font-semibold text-sm">What should I remember about this?</p>
      <p className="text-xs text-[var(--muted)] mb-3">Only what you approve is saved. Edit anything that's off.</p>
      <div className="space-y-2">
        {items.map((m, i) => (
          <div key={i} className="flex gap-2 items-center">
            <Checkbox aria-label={`Keep suggested memory ${i + 1}`} checked={m.keep} onCheckedChange={(v) => setItems((x) => x.map((y, j) => (j === i ? { ...y, keep: !!v } : y)))} data-testid={`memory-keep-${i}`} />
            <span className="font-mono text-[10px] uppercase text-[var(--muted)] w-20 shrink-0">{KIND_LABEL[m.kind] || m.kind}</span>
            <Input aria-label={`Suggested memory ${i + 1}`} value={m.text} onChange={(e) => setItems((x) => x.map((y, j) => (j === i ? { ...y, text: e.target.value } : y)))} data-testid={`memory-text-${i}`} />
          </div>
        ))}
      </div>
      <button disabled={busy} onClick={save} className="mt-3 px-5 py-2 rounded-full bg-[var(--emerald)] text-[var(--on-accent)] text-sm font-bold" data-testid="memory-approve-button">{busy ? "Saving…" : "Save approved"}</button>
    </div>
  );
}

export function OutcomeButton({ ex, api, onSaved, url, label = "What actually happened?", testid }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(ex.outcome || "");
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState(false);
  const isPlan = Boolean(url?.startsWith("/preps/"));
  const edit = next => {
    if (next) {
      setText(ex.outcome || "");
      setRemember(ex.outcome_use_for_coaching ?? Boolean(ex.outcome));
    }
    setOpen(next);
  };
  const save = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await api.post(url || `/exchanges/${ex.id}/outcome`, isPlan
        ? { text, use_for_coaching: remember, expected_revision: ex.outcome_revision || 0 } : { text });
      toast.success(isPlan && !remember ? "Saved to this game plan" : "Outcome saved");
      setOpen(false);
      onSaved();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (busy || !window.confirm("Remove this outcome and its automatic coaching memory?")) return;
    setBusy(true);
    try {
      await api.delete(url, { params: { expected_revision: ex.outcome_revision || 0 } });
      setOpen(false);
      onSaved();
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
  };
  return (
    <Dialog open={open} onOpenChange={edit}>
      <DialogTrigger asChild>
        <button className="text-xs btn-ghost px-3 py-1.5" data-testid={testid || `outcome-button-${ex.id}`}>{ex.outcome ? "Edit what happened" : label}</button>
      </DialogTrigger>
      <DialogContent data-testid="outcome-dialog">
        <DialogHeader><DialogTitle>{label}</DialogTitle></DialogHeader>
        <Textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. I sent the line, he replied in 10 minutes and asked me out for Friday" data-testid="outcome-textarea" />
        {isPlan && <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} className="mt-1" />
          <span>Use this outcome as a coaching memory for future replies and reports. Leave unchecked to save it only on this plan.</span>
        </label>}
        {isPlan && ex.outcome && <button type="button" className="underline text-sm" onClick={remove} disabled={busy}>Remove outcome</button>}
        <button disabled={busy || !text.trim()} onClick={save} className="py-2.5 rounded-full bg-[var(--emerald)] text-[var(--on-accent)] font-bold" data-testid="outcome-save">Save outcome</button>
      </DialogContent>
    </Dialog>
  );
}
