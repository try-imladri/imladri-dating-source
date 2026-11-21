import { useState } from "react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { stagesFor, isRomantic, errMsg, publicApi, pad } from "@/lib/api";
import { DemoFields } from "@/components/app/Demographics";
import { MemberLoadState, useMemberResource } from "@/components/app/MemberResource";

function Basics({ p, api, reload }) {
  const [f, setF] = useState({ nickname: p.nickname, stage: p.stage, goal: p.goal || "", background: p.background || "", archetype_tag: p.archetype_tag ? String(p.archetype_tag) : "none" });
  const [d, setD] = useState(p.demographics || {});
  const [revision, setRevision] = useState(p.profile_revision || 0);
  const [conflict, setConflict] = useState(null);
  const [saving, setSaving] = useState(false);
  const types = useMemberResource(publicApi, isRomantic(p.rel_type) ? "/public/archetypes" : null);
  const [ag, setAg] = useState(p.archetype_tag > 100 ? "female" : "male");
  const stages = stagesFor(p.rel_type);
  const save = async () => {
    const body = { nickname: f.nickname, goal: f.goal };
    if (f.background || p.background) body.background = f.background;
    if (f.stage !== p.stage) body.stage = f.stage;
    if (isRomantic(p.rel_type)) Object.assign(body, f.archetype_tag === "none" ? { clear_archetype: true } : { archetype_tag: Number(f.archetype_tag) });
    if (d.age && (d.age < 13 || d.age > 120)) return toast.error("Age should be between 13 and 120");
    if (saving) return;
    setSaving(true);
    try {
      const {data} = await api.patch(`/people/${p.id}`, { ...body, demographics: d, expected_profile_revision: revision });
      setRevision(data.profile_revision || 0);
      setConflict(null);
      toast.success("Saved");
      reload();
    } catch (e) {
      if (e.response?.status === 409) {
        try { setConflict((await api.get("/people/"+p.id)).data); } catch (error) { toast.error(errMsg(error)); }
      }
      toast.error(errMsg(e));
    } finally { setSaving(false); }
  };
  return (
    <div className="card p-5 grid sm:grid-cols-2 gap-4" data-testid="profile-basics">
      {conflict && <div role="alert" className="sm:col-span-2 space-y-2 border border-[var(--line)] rounded-lg p-3 text-sm">
        <p>Another edit was saved. Your form still contains your changes.</p>
        <details><summary className="cursor-pointer">Review the saved version</summary>
          <dl>{Object.entries({Name:conflict.nickname,Stage:conflict.stage,Goal:conflict.goal,Background:conflict.background,Archetype:conflict.archetype_tag,...(conflict.demographics || {})}).map(([key,value])=><div key={key}><dt className="inline font-semibold">{key} </dt><dd className="inline">{String(value ?? "Unspecified")}</dd></div>)}</dl>
        </details>
        <button className="underline" onClick={()=>{setRevision(conflict.profile_revision || 0);setConflict(null);}}>I reviewed it. Keep editing my version</button>
      </div>}
      <div><Label htmlFor="profile-nickname">Name</Label><Input className="mt-1.5" value={f.nickname} onChange={(e) => setF({ ...f, nickname: e.target.value })} id="profile-nickname" data-testid="profile-nickname-input" /></div>
      <div><Label htmlFor="profile-stage">Stage</Label>
        <Select value={f.stage} onValueChange={(v) => v && setF({ ...f, stage: v })}>
          <SelectTrigger className="mt-1.5" id="profile-stage" data-testid="profile-stage-select"><SelectValue /></SelectTrigger>
          <SelectContent>
            {p.stage && !stages.some((stage) => stage.v === p.stage) && <SelectItem value={p.stage} disabled>{p.stage} (previous stage)</SelectItem>}
            {stages.map((s) => <SelectItem key={s.v} value={s.v}>{s.l}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
      <div><Label htmlFor="profile-goal">Goal</Label><Input className="mt-1.5" value={f.goal} onChange={(e) => setF({ ...f, goal: e.target.value })} id="profile-goal" data-testid="profile-goal-input" /></div>
      {isRomantic(p.rel_type) && (
        <div><div className="flex items-center justify-between gap-2"><Label htmlFor="profile-archetype">Archetype tag (your interpretation)</Label>
          <span className="inline-flex p-0.5 rounded-full border border-[var(--line)]" data-testid="profile-archetype-gender">
            {[["male", "Male"], ["female", "Female"]].map(([k, l]) => <button key={k} type="button" onClick={() => setAg(k)} className={`px-2.5 py-0.5 rounded-full text-[11px] font-semibold transition-colors ${ag === k ? "bg-[var(--ink)] text-[var(--bg)]" : "text-[var(--muted)]"}`} data-testid={`profile-archetype-gender-${k}`}>{l}</button>)}
          </span></div>
          {types.error && <MemberLoadState label="the archetype tags" error={types.error} retry={types.reload} />}
          <Select value={f.archetype_tag} disabled={!types.data || !!types.error} onValueChange={(v) => setF({ ...f, archetype_tag: v })}>
            <SelectTrigger className="mt-1.5" id="profile-archetype" data-testid="profile-archetype-select"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value="none">None</SelectItem>{types.data?.filter((t) => t.unlocked && (t.gender === ag || String(t.number) === f.archetype_tag)).map((t) => <SelectItem key={t.number} value={String(t.number)}>#{pad(t.number)} {t.name}{t.gender !== ag ? ` (${t.gender})` : ""}</SelectItem>)}</SelectContent>
          </Select>
        </div>
      )}
      <div className="sm:col-span-2"><Label htmlFor="profile-background">Background</Label><Input className="mt-1.5" value={f.background} onChange={(e) => setF({ ...f, background: e.target.value })} id="profile-background" data-testid="profile-background-input" /></div>
      <div className="sm:col-span-2 pt-1" data-testid="person-demographics-card">
        <Label>About {p.nickname} <span className="text-xs text-[var(--muted)] font-normal">(optional, your best guess is fine)</span></Label>
        <div className="mt-1.5"><DemoFields value={d} onChange={setD} prefix="person" /></div>
      </div>
      <button onClick={save} disabled={saving || !!conflict} className="sm:col-span-2 py-2.5 rounded-full bg-[var(--ink)] text-[var(--bg)] font-bold text-sm" data-testid="profile-basics-save">Save basics</button>
    </div>
  );
}

function Intake({ p, api, reload }) {
  const { data: schema, error, reload: load } = useMemberResource(api, "/intake-schema");
  const [vals, setVals] = useState(p.intake || {});
  const [original, setOriginal] = useState(p.intake || {});
  const [review, setReview] = useState(null);
  const [saving, setSaving] = useState(false);
  const save = async (sec) => {
    if (saving || review) return;
    const keys = sec.fields.map(f => f.key).filter(key => (vals[key] || "") !== (original[key] || ""));
    const fields = Object.fromEntries(keys.map(key => [key, vals[key] || ""]));
    const previous = Object.fromEntries(keys.map(key => [key, original[key] || ""]));
    setSaving(true);
    try {
      const { data } = await api.patch(`/people/${p.id}/intake`, { fields, previous });
      setOriginal(old => ({ ...old, ...fields }));
      toast.success(data.report_triggered ? "Saved — the report will update with this" : "Saved");
      reload();
    } catch (e) {
      if (e.response?.status === 409) {
        try {
          const { data } = await api.get("/people/" + p.id);
          setReview(Object.fromEntries(keys.map(key => [key, data.intake?.[key] || ""])));
        } catch (error) { toast.error(errMsg(error)); }
      }
      toast.error(errMsg(e));
    } finally { setSaving(false); }
  };
  return (
    <div className="card p-5" data-testid="deepen-profile">
      {review && <div role="alert" className="text-sm space-y-2 mb-3"><p>These answers changed on another device. Your draft is still here.</p>
        {Object.entries(review).map(([key,value]) => <p key={key}><strong>{schema?.flatMap(section=>section.fields).find(field=>field.key===key)?.label || key}</strong> {value || "(empty)"}</p>)}
        <button className="underline" onClick={() => { setOriginal(old=>({...old,...review})); setReview(null); }}>I reviewed the saved answers. Keep editing</button></div>}
      <p className="font-display font-black uppercase text-2xl">Deepen this profile</p>
      <p className="text-sm text-[var(--muted)] mb-2">All optional. Real examples beat adjectives — what did they actually say or do?</p>
      {(!schema || error) && <MemberLoadState label="the profile questions" error={error} retry={load} />}
      <Accordion type="single" collapsible>
        {schema?.map((sec) => (
          <AccordionItem key={sec.key} value={sec.key}>
            <AccordionTrigger data-testid={`intake-section-${sec.key}`}>{sec.title} <span className="ml-auto mr-3 text-xs text-[var(--muted)]">{sec.fields.filter((f) => (vals[f.key] || "").length >= 8).length}/{sec.fields.length}</span></AccordionTrigger>
            <AccordionContent className="space-y-4">
              {sec.fields.map((f) => (
                <div key={f.key}><Label htmlFor={"intake-" + f.key}>{f.label}</Label><Textarea id={"intake-" + f.key} className="mt-1.5" rows={2} placeholder={f.hint} value={vals[f.key] || ""} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} data-testid={`intake-${f.key}`} /></div>
              ))}
              <button disabled={saving || !!review} onClick={() => save(sec)} className="px-5 py-2 rounded-full bg-[var(--emerald)] text-[var(--on-accent)] font-bold text-sm" data-testid={`intake-save-${sec.key}`}>Save {sec.title.toLowerCase()}</button>
            </AccordionContent>
          </AccordionItem>
        ))}
      </Accordion>
    </div>
  );
}

export default function ProfileTab({ p, api, reload }) {
  return (
    <div className="grid xl:grid-cols-2 gap-5 items-start" data-testid="profile-tab">
      <Basics p={p} api={api} reload={reload} />
      <Intake p={p} api={api} reload={reload} />
    </div>
  );
}
