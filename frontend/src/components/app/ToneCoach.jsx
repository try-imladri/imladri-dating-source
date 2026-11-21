import { useLayoutEffect, useRef, useState } from "react";
import { errMsg } from "@/lib/api";

export const STYLE_LABELS = { warm: "Warm", neutral: "Neutral", direct: "Direct", playful: "Playful", cool: "Cool or reserved" };
const READ_LABELS = { warm: "Warm", neutral: "Neutral", reserved: "Reserved", tense: "Tense", playful: "Playful", mixed: "Mixed" };
const button = "btn-ghost px-3 py-1.5 text-sm disabled:opacity-50";

function UsefulnessQuestion({ feedback, busy, rate, clear }) {
  return <div className="flex flex-wrap items-center gap-2">
    <span className="text-sm">Thinking back after the conversation, did your {STYLE_LABELS[feedback.sent_style]?.toLowerCase() || "suggested"} reply help?</span>
    {[[true,"Yes"],[false,"No"]].map(([value,label]) => <button type="button" key={label} className={button}
      disabled={busy} aria-pressed={feedback.helped === value} onClick={() => rate(value)}>{feedback.helped === value ? "✓ " : ""}{label}</button>)}
    {typeof feedback.helped === "boolean" && <button type="button" disabled={busy} className="text-sm underline"
      onClick={clear}>Remove my helpfulness rating</button>}
  </div>;
}

export function SentReplyFollowup({ ex, api }) {
  const [saved, setSaved] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!ex.tone || ex.tone_invalidated || ex.tone_contact_paused || ex.response?.contact_boundary
      || ex.response?.limited_reply || !ex.tone_feedback?.sent) return null;
  const feedback = saved || ex.tone_feedback;
  async function update(change) {
    setBusy(true); setError("");
    try {
      const {data} = await api.post(`/exchanges/${ex.id}/tone-feedback`, change);
      setSaved(data);
    } catch (e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  }
  return <div className="my-2" aria-label="Sent reply follow-up">
    <UsefulnessQuestion feedback={feedback} busy={busy} rate={value => update({action:"helped", style:feedback.sent_style, value})}
      clear={() => update({action:"clear_helped", style:feedback.sent_style})} />
    {error && <p role="alert">{error}</p>}
  </div>;
}

export function ToneCoach({ tone, feedback, onFeedback, lines = [], earlierEvidence, heading = "How their messages read",
  draftHeading = "How do you want to sound?", allowOutcome = true, allowFit = true, onUseDraft }) {
  const [local, setLocal] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [unavailable, setUnavailable] = useState(false);
  const [editing, setEditing] = useState(false);
  const [note, setNote] = useState(feedback?.note || "");
  const [earlier, setEarlier] = useState(null);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [copied, setCopied] = useState("");
  const active = useRef(false);
  useLayoutEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  if (!tone) return null;
  const state = local || feedback || {};
  const drafts = tone.drafts || {};
  const style = state.style;
  const draft = drafts[style];
  const estimated = tone.status === "estimated";
  const select = async (body, onConfirmed) => {
    setError(""); setBusy(true);
    try {
      const updated = await onFeedback(body);
      if (!active.current) return;
      setLocal(updated);
      if (onConfirmed) await onConfirmed();
      if (body.action === "fit" || body.action === "clear_fit") { setEditing(false); setNote(updated.note || ""); }
    } catch (e) {
      setError(errMsg(e));
      if ([403, 404, 409].includes(e.response?.status)) setUnavailable(true);
    }
    finally { setBusy(false); }
  };
  const showEarlier = async () => {
    setError("");
    setLoadingEarlier(true);
    try {
      const messages = await earlierEvidence();
      if (messages.length !== tone.baseline_evidence.length) {
        setError("Earlier context for this tone read changed. Continue the conversation for a fresh read.");
        setUnavailable(true);
      } else {
        setEarlier(messages);
      }
    } catch (e) {
      setError(errMsg(e));
      if ([403, 404, 409].includes(e.response?.status)) setUnavailable(true);
    } finally {
      setLoadingEarlier(false);
    }
  };
  if (unavailable) return <p role="alert" className="text-sm text-[var(--muted)]">{error}</p>;
  return <section className="card p-4 sm:p-5 space-y-4" aria-label="Conversation tone" data-testid="tone-coach">
    <div>
      <h3 className="font-semibold">{heading}</h3>
      <p className="text-sm mt-1" data-testid="tone-verdict">{estimated ? <><strong>{READ_LABELS[tone.incoming]}</strong> · {tone.confidence === "moderate" ? "Moderate evidence" : "Limited evidence"}</> : "Too little context to tell"}</p>
      <p className="text-sm text-[var(--muted)] mt-1">{tone.explanation}</p>
      {tone.evidence?.length > 0 && <details className="mt-2 text-sm">
        <summary className="cursor-pointer underline">See the messages behind this read ({tone.evidence.length})</summary>
        <ul className="space-y-1 mt-2">{tone.evidence.map(i => lines[i] && <li key={i} className="border-l-2 border-[var(--line)] pl-2">
          {["me","them"].includes(lines[i].speaker) && <span className="block text-xs text-[var(--muted)]">{lines[i].speaker === "me" ? "Your words" : "Their words"}</span>}
          <span className="whitespace-pre-wrap">{lines[i].text}</span>
        </li>)}</ul>
        {!!tone.baseline_evidence?.length && earlierEvidence && (earlier
          ? <div className="mt-2"><p className="text-xs text-[var(--muted)]">Earlier in this thread</p>{earlier.map((t,i) => <p key={i} className="mt-1 border-l-2 border-[var(--line)] pl-2">{t}</p>)}</div>
          : <button type="button" className="underline mt-2" disabled={loadingEarlier} onClick={showEarlier}>{loadingEarlier ? "Loading earlier messages…" : "See earlier messages"}</button>)}
      </details>}
      {allowFit && estimated && <div className="mt-3">
        <div className="flex flex-wrap gap-2">
          <button type="button" className={button} disabled={busy} aria-pressed={state.fit === "fits"} onClick={() => select({action:"fit", fit:"fits"})}>{state.fit === "fits" ? "✓ That fits" : "That fits"}</button>
          <button type="button" className={button} disabled={busy} aria-expanded={editing} onClick={() => setEditing(!editing)}>{state.fit === "corrected" ? "Edit my correction" : "That’s not how I read it"}</button>
        </div>
        {state.fit && <button type="button" className="mt-1 py-1 text-sm underline" disabled={busy}
          onClick={() => select({action:"clear_fit"})}>Remove my tone rating</button>}
        {editing && <div className="mt-2 space-y-2">
          <label className="block text-sm">What should we take into account? <span className="text-[var(--muted)]">(optional)</span>
            <textarea maxLength={300} rows={2} className="mt-1 w-full rounded-lg border border-[var(--line)] bg-transparent p-2" value={note} onChange={e => setNote(e.target.value)} /></label>
          <button type="button" className={button} disabled={busy} onClick={() => select({action:"fit", fit:"corrected", note})}>Save correction</button>
        </div>}
      </div>}
    </div>
    {!!Object.keys(drafts).length && <div className="border-t border-[var(--line)] pt-4">
      <h3 className="font-semibold">{draftHeading}</h3>
      {tone.intent && <p className="text-sm text-[var(--muted)] mt-1">{tone.intent}</p>}
      <div className="flex flex-wrap gap-2 mt-3">
        {Object.entries(STYLE_LABELS).filter(([key]) => drafts[key]).map(([key,label]) =>
          <button type="button" key={key} disabled={busy} aria-pressed={key === style} className={key === style ? "rounded-full px-3 py-1.5 text-sm bg-[var(--ink)] text-[var(--bg)]" : button}
            onClick={() => select({action:"select",style:key})}>{label}</button>)}
      </div>
      {draft && <div className="mt-3 space-y-2">
        <p className="bubble-you w-fit px-4 py-2.5 text-sm whitespace-pre-wrap" data-testid="tone-selected-draft">{draft}</p>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={button} disabled={busy} onClick={() => select({action:"select", style}, async () => {
            if (!navigator.clipboard?.writeText) throw new Error("Select the draft text to copy it in this browser.");
            await navigator.clipboard.writeText(draft);
            setCopied(style);
            setLocal(await onFeedback({action:"copy", style}));
          })}>{copied === style ? "Copied" : "Copy reply"}</button>
          {onUseDraft && <button type="button" className={button} disabled={busy}
            onClick={() => select({action:"select", style}, () => onUseDraft(draft, style))}>Use this wording</button>}
        </div>
        {allowOutcome && <div className="space-y-2 pt-1">
          <div className="flex flex-wrap items-center gap-2"><span className="text-sm">Did you send these words?</span>
            <button type="button" className={button} disabled={busy} aria-pressed={state.sent === true && state.sent_style === style} onClick={() => select({action:"sent",style,value:true})}>{state.sent && state.sent_style === style ? "✓ Sent" : "I sent this"}</button>
            <button type="button" className={button} disabled={busy} aria-pressed={state.sent === false} onClick={() => select({action:"sent",style,value:false})}>I didn’t send a reply</button>
            {typeof state.sent === "boolean" && <button type="button" className="text-sm underline" disabled={busy}
              onClick={() => select({action:"clear_sent", value:state.sent, style:state.sent_style})}>Remove my send response</button>}
          </div>
          {state.sent === true && <UsefulnessQuestion feedback={state} busy={busy}
            rate={value => select({action:"helped",style:state.sent_style,value})}
            clear={() => select({action:"clear_helped",style:state.sent_style})} />}
        </div>}
      </div>}
    </div>}
    {error && <p role="alert" className="text-sm text-[var(--crimson)]">{error}</p>}
  </section>;
}

export function IndividualTone({ ex, api }) {
  if (ex.tone_contact_paused && !ex.response?.contact_boundary) return <p className="text-sm text-[var(--muted)]">Reply wording is paused for their current contact boundary.</p>;
  if (ex.tone_invalidated) return <p className="text-sm text-[var(--muted)]">Earlier context for this tone read changed. Continue the conversation for a fresh read.</p>;
  if (!ex.tone || ex.response?.contact_boundary || ex.response?.limited_reply) return null;
  return <ToneCoach key={ex.id + "/" + (ex.tone_revision || 0)} tone={ex.tone} feedback={ex.tone_feedback} lines={ex.conversation}
    earlierEvidence={async () => (await api.get(`/exchanges/${ex.id}/tone-evidence`)).data}
    onFeedback={async body => (await api.post(`/exchanges/${ex.id}/tone-feedback`, body)).data} />;
}
