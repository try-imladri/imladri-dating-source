import { useEffect, useRef, useState } from "react";

export default function AnswerHistoryPreference({ api, me, setMe }) {
  const owner = me?.user_id;
  const [preference, setPreference] = useState(me?.answer_history_permission || null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const pending = useRef(null);

  useEffect(() => {
    if (me?.role !== "member") return undefined;
    const controller = new AbortController();
    setPreference(null);
    setBusy(false);
    setNotice("");
    api.get("/me/answer-history-permission", { signal: controller.signal })
      .then(({ data }) => { if (!controller.signal.aborted) setPreference(data); })
      .catch(() => { if (!controller.signal.aborted) setNotice("Your answer-history permission could not be loaded. Refresh to try again."); });
    return () => { controller.abort(); pending.current?.abort(); pending.current = null; };
  }, [api, owner, me?.role]);

  if (me?.role !== "member") return null;

  async function update(enabled) {
    if (!preference || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setNotice("");
    try {
      const { data } = await api.patch("/me/answer-history-permission", {
        expected_member: owner, version: preference.version, revision: preference.revision,
        enabled, adult_confirmed: enabled,
      }, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setPreference(data);
      setMe(previous => previous?.user_id === owner ? { ...previous, answer_history_permission: data } : previous);
      setNotice(enabled ? "Permission saved for answers recorded from now on. Earlier answers are not added automatically."
        : "Staff access under this permission is off. Earlier downloads may still exist.");
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error.response?.status === 409) {
        try {
          const { data } = await api.get("/me/answer-history-permission", { signal: controller.signal });
          if (!controller.signal.aborted) setPreference(data);
        } catch { /* Keep the last confirmed choice. */ }
      }
      if (!controller.signal.aborted) setNotice(typeof error.response?.data?.detail === "string"
        ? error.response.data.detail : "Your choice could not be confirmed. Review the current setting before trying again.");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      if (pending.current === controller) pending.current = null;
    }
  }

  return <section className="space-y-2 border-t border-[var(--line)] pt-3 text-sm" aria-label="Staff answer history">
    <h3 className="font-semibold text-[var(--ink)]">Staff answer history</h3>
    <p>Allow authorized Imladri staff to review and download the answers and changes you save while this permission is on. This can include demographic snapshots, written questionnaire answers, Sex &amp; Intimacy answers and Your Type choices, subject to your other research permissions. Private Couples drafts are excluded.</p>
    <p>This does not share private answers with your partner. It is separate from saving answers to your own profile. Turning it off stops future staff access under this permission; earlier downloads may still exist.</p>
    {preference ? <label className="flex items-start gap-2 font-semibold text-[var(--ink)]">
      <input type="checkbox" className="mt-1 shrink-0" checked={preference.enabled === true} disabled={busy}
        onChange={event => update(event.target.checked)} />
      <span>I am 18 or older and allow staff to review my answer history recorded from now on.</span>
    </label> : !notice && <p role="status">Loading your permission…</p>}
    {notice && <p role="status" className="text-xs text-[var(--muted)]">{notice}</p>}
  </section>;
}
