import { useLayoutEffect, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { useMessageSend } from "@/components/app/PlanUsage";
import { ToneCoach } from "@/components/app/ToneCoach";
import { errMsg } from "@/lib/api";
import { field, quiet, secondary } from "./CouplesCommon";

const goalDefault = "Acknowledge what my partner said and ask one clarifying question.";

export default function CouplesTone({ mode, sourceKey, version, pairId, lines, onUseDraft }) {
  const { api, refresh, workspace, me } = useOutletContext();
  const send = useMessageSend();
  const [open, setOpen] = useState(false);
  const [reading, setReading] = useState(null);
  const [line, setLine] = useState(() => Math.max(0, lines.findIndex(row => row.text?.trim())));
  const [goal, setGoal] = useState(goalDefault);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(false);
  const activeReading = useRef(null);
  useLayoutEffect(() => {
    activeReading.current = open ? reading?.id : null;
    return () => { activeReading.current = null; };
  }, [open, reading?.id]);
  const privateDraft = mode === "private";
  const ownAllowance = workspace.can_sponsor;
  const outOfReplies = ownAllowance && me?.usage?.messages_remaining === 0;
  const source = { pair_id: pairId || null, version };
  const path = `/couples/tone/${mode}/${sourceKey}`;
  const heading = privateDraft ? "How might my draft come across?" : "How does this exchange read?";
  const focus = privateDraft ? [lines[line]] : lines;

  const [checked, setChecked] = useState(false);

  async function start(savedOnly = false) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const input = { ...source, line, goal: privateDraft ? "" : goal };
      const { data } = savedOnly ? await api.post(path + "/saved", input) : await send(path, input);
      setChecked(savedOnly && data.available === false);
      if (data.id) setReading(data);
    } catch (e) {
      setError(errMsg(e));
      if ([402, 403, 404, 409].includes(e.response?.status)) await refresh();
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  async function feedback(change) {
    try {
      const { data } = await api.patch(path, { ...source, coach_id: reading.id, change });
      setReading(previous => previous?.id === reading.id ? { ...previous, tone_feedback: data } : previous);
      return data;
    } catch (e) {
      if ([402, 403, 404, 409].includes(e.response?.status)) {
        if (activeReading.current === reading.id) {
          setReading(null);
          setError(errMsg(e));
        }
        await refresh();
      }
      throw e;
    }
  }

  return <div className="space-y-3 rounded-xl border border-[var(--line)] p-4" data-testid="couples-tone">
    <button type="button" className="font-semibold text-sm underline underline-offset-4"
      aria-expanded={open} onClick={() => setOpen(!open)}>{heading}</button>
    {open && <>
      <p className={quiet}>{privateDraft
        ? "Read my tone sends the selected draft line to DeepSeek. The feedback is private."
        : "Read the exchange sends both shared perspectives and your reply goal to DeepSeek. Your partner cannot see your feedback or suggested reply. Staff can download later fixed-choice feedback only if both partners allow tone research and Couples research CSVs."}</p>
      {!reading && !me?.usage?.unlimited && <p className="text-sm">
        {ownAllowance ? "A new read uses one message from your balance, shared with Individual." :
            "A new read uses one message from your sponsoring partner’s balance."}
        {outOfReplies ? " Your balance is zero. You can still reopen the current saved read for the same selection." :
          " Only the latest read for this card and selection can be reopened. Switching styles is free."}
      </p>}
      {privateDraft && lines.filter(row => row.text?.trim()).length > 1 &&
        <label className="block text-sm">Draft line
          <select value={line} disabled={busy} className={field} onChange={event => {
            setLine(Number(event.target.value)); setReading(null); setChecked(false); setError("");
          }}>{lines.map((row, i) => row.text?.trim() && <option key={i} value={i}>{row.text}</option>)}</select>
        </label>}
      {!privateDraft && <label className="block text-sm">What do you want your reply to say?
        <input className={field + " mt-1"} value={goal} maxLength={420} disabled={busy}
          onChange={event => { setGoal(event.target.value); setReading(null); setChecked(false); setError(""); }} />
      </label>}
      {!reading && <div className="flex flex-wrap gap-2 items-center">
        {!outOfReplies && <button type="button" disabled={busy || (!privateDraft && !goal.trim())}
          className={secondary} onClick={() => start()}>{busy ? "Reading…" : privateDraft ? "Read my tone" : "Read the exchange"}</button>}
        <button type="button" disabled={busy || (!privateDraft && !goal.trim())} className="text-sm underline py-2"
          onClick={() => start(true)}>Check for a saved read · free</button>
        {checked && <p role="status" className="text-sm">No saved read matches this selection. No message was used.</p>}
      </div>}
      {reading?.boundary ? <p role="status" className="text-sm">The shared words set a limit on contact. Give your partner that space and follow any specific invitation they offered.</p> :
        reading && <ToneCoach key={reading.id} tone={reading.tone} feedback={reading.tone_feedback}
          onFeedback={feedback} lines={focus} heading={privateDraft ? "How your draft reads" : "How your partner’s words read"}
          allowOutcome={!privateDraft}
          onUseDraft={(text, style) => onUseDraft(text, line, { coachId: reading.id, style })} />}
      {error && <p role="alert" className="text-sm text-[var(--crimson)]">{error}</p>}
    </>}
  </div>;
}
