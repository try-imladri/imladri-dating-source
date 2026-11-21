import { useState } from "react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { GroupIcon } from "@/components/app/GroupIcon";
import { REL_GROUPS, stagesFor, errMsg } from "@/lib/api";

function TypePicker({ value, onPick, part }) {
  const [romantic, ...rest] = REL_GROUPS;
  const chip = (t, big) => (
    <button type="button" key={t.v} onClick={() => onPick(t.v)} data-testid={`reltype-${t.v}`}
      className={`rounded-xl border transition-colors text-left ${big ? "px-3 py-3 font-display font-bold uppercase text-base leading-tight" : "px-3 py-1.5 rounded-full text-xs"} ${value === t.v ? "border-[var(--ink)] bg-[var(--ink)] text-[var(--bg)]" : "border-[var(--line)] text-[var(--muted)] hover:text-[var(--ink)]"}`}>{t.l}</button>
  );
  if (part === "romantic") {
    return (
      <div data-testid="reltype-picker">
        <p className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-widest text-[var(--muted)] mb-2"><GroupIcon group="romantic" className="w-3.5 h-3.5" />Romantic</p>
        <div className="grid grid-cols-2 gap-2">{romantic.types.map((t) => chip(t, true))}</div>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      {rest.map((g) => (
        <div key={g.key}>
          <p className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-widest text-[var(--muted)] mb-2"><GroupIcon group={g.key} className="w-3.5 h-3.5" />{g.label}</p>
          <div className="flex flex-wrap gap-1.5">{g.types.map((t) => chip(t, false))}</div>
        </div>
      ))}
    </div>
  );
}

export function AddPersonForm({ api, onCreated, submitLabel = "Create profile" }) {
  const [f, setF] = useState({ nickname: "", rel_type: "crush", rel_label: "", stage: "crush", background: "", goal: "" });
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setF((x) => ({ ...x, [k]: v }));
  const pickType = (t) => setF((x) => ({ ...x, rel_type: t, stage: stagesFor(t)[0].v }));

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const { data } = await api.post("/people", { ...f, rel_label: f.rel_type === "other" ? f.rel_label : "" });
      onCreated(data);
    } catch (err) {
      toast.error(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="grid lg:grid-cols-3 gap-6 lg:gap-8" data-testid="add-person-form">
      <div><Label className="block mb-3">1. Who are they to you?</Label><TypePicker part="romantic" value={f.rel_type} onPick={pickType} /></div>
      <div className="lg:pt-8"><TypePicker part="rest" value={f.rel_type} onPick={pickType} /></div>
      <div className="space-y-4">
      <Label className="block">2. A few details</Label>
      {f.rel_type === "other" && <div><Label>What are they to you?</Label><Input className="mt-1.5" required maxLength={40} value={f.rel_label} onChange={(e) => set("rel_label", e.target.value)} placeholder="e.g. neighbour" data-testid="person-rel-label-input" /></div>}
      <div className="grid grid-cols-2 gap-3">
        <div><Label>Name</Label><Input className="mt-1.5" required maxLength={40} value={f.nickname} onChange={(e) => set("nickname", e.target.value)} placeholder="e.g. Gym Jake" data-testid="person-nickname-input" /></div>
        <div>
          <Label>Stage</Label>
          <Select value={f.stage} onValueChange={(v) => v && set("stage", v)}>
            <SelectTrigger className="mt-1.5" data-testid="person-stage-select"><SelectValue /></SelectTrigger>
            <SelectContent>{stagesFor(f.rel_type).map((s) => <SelectItem key={s.v} value={s.v} data-testid={`stage-option-${s.v}`}>{s.l}</SelectItem>)}</SelectContent>
          </Select>
        </div>
      </div>
      <div><Label>One line about the situation</Label><Input className="mt-1.5" required maxLength={500} value={f.background} onChange={(e) => set("background", e.target.value)} placeholder="e.g. met on Hinge 3 weeks ago, 2 dates, texts every day" data-testid="person-background-input" /></div>
      <div><Label>Your goal with them <span className="text-[var(--muted)]">(optional)</span></Label><Input className="mt-1.5" value={f.goal} onChange={(e) => set("goal", e.target.value)} placeholder="e.g. figure out if he's serious" data-testid="person-goal-input" /></div>
      <button disabled={busy} className="w-full py-3 rounded-full bg-[var(--ink)] text-[var(--bg)] font-bold hover:opacity-90 transition-opacity" data-testid="add-person-submit">{busy ? "Creating…" : submitLabel}</button>
      </div>
    </form>
  );
}
