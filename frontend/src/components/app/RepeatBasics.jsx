import { useState } from "react";
import { errMsg } from "@/lib/api";

export default function RepeatBasics({ me, api }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(null);
  const [answers, setAnswers] = useState({});
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!me.questionnaire_research?.enabled) return <p className="text-sm text-[var(--muted)]">
    Adults who opt in to <a className="underline" href="/app/settings#questionnaire-research">questionnaire research</a> can also start a separate consistency check now. Its two sets are taken 7–28 days apart.
  </p>;
  async function load() {
    setBusy(true); setError("");
    try {
      const { data } = await api.get("/me/assessment-repeat");
      setForm(data); setAnswers({}); setAgree(false);
    } catch (e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  }
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const { data } = await api.post("/me/assessment-repeat", { instrument: form.instrument, phase: form.phase,
        revision: form.revision, consent_revision: form.consent_revision, expected_member: me.user_id,
        answers, publish_group_results: agree });
      setForm(data); setAnswers({}); setAgree(false);
    } catch (e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  }
  async function remove() {
    setBusy(true); setError("");
    try { await api.delete("/me/assessment-repeat", {data:{expected_member:me.user_id, revision:form.revision}}); await load(); }
    catch (e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  }
  const format = date => new Date(date).toLocaleString(undefined, {dateStyle:"medium", timeStyle:"short"});
  return <section className="card p-5 space-y-3" aria-label="Optional Basics repeat">
    <h3 className="font-semibold">Optional research consistency check</h3>
    <p className="text-sm text-[var(--muted)]">For this check, answer two fresh sets 7–28 days apart. Staff compare how often the answers agree. You can edit your regular Basics answers at any time.</p>
    <button type="button" className="btn-ghost px-3 py-1.5 text-sm" aria-expanded={open} onClick={() => { if (!open) load(); setOpen(!open); }}>{open ? "Close repeat" : "Open optional repeat"}</button>
    {open && <div className="space-y-3">
      {busy && <p role="status">Saving or loading your repeat…</p>}
      {error && <p role="alert">{error} <button className="underline" onClick={load} disabled={busy}>Reload repeat</button></p>}
      {form?.phase === "waiting" && <p role="status">First set saved. Come back between {format(form.opens)} and {format(form.closes)} to answer again.</p>}
      {form?.phase === "complete" && <p role="status">Both sets are saved. Your answers can now contribute to group results.</p>}
      {form?.questions?.length > 0 && <form onSubmit={submit} className="space-y-4">
        <p className="font-semibold">{form.phase === "second" ? "Second set" : form.has_record ? "Start a new first set" : "First set"}</p>
        {form.phase === "first" && form.has_record && <p className="text-sm">The form changed or the repeat window ended. A new first set replaces your earlier study entry.</p>}
        {form.questions.map((q,i) => <fieldset key={q.id} className="space-y-1">
          <legend className="text-sm font-semibold">{i+1}. {q.q}</legend>
          {q.labels.map((label,k) => <label key={k} className="flex items-start gap-2 text-sm"><input type="radio" name={q.id} checked={answers[q.id] === k} disabled={busy} onChange={() => setAnswers(v => ({...v,[q.id]:k}))} />{label}</label>)}
        </fieldset>)}
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={agree} disabled={busy} onChange={e => setAgree(e.target.checked)} />
          I agree to save these study answers and include them in group statistics published on How it works. I can remove my study entry here.</label>
        <button className="btn-lime px-4 py-2" disabled={busy || !agree || Object.keys(answers).length !== form.questions.length}>Submit {form.phase === "second" ? "second" : "first"} set</button>
      </form>}
      {form?.has_record && <button type="button" className="underline text-sm" disabled={busy} onClick={remove}>Remove my study entry</button>}
    </div>}
  </section>;
}
