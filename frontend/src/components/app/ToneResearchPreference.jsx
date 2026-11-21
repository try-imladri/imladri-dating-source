import { useState } from "react";

export default function ToneResearchPreference({ me, setMe, api }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const pref = me?.tone_research;
  if (me?.role !== "member" || !pref) return null;

  const change = async enabled => {
    setBusy(true);
    setMessage("");
    try {
      const { data } = await api.patch("/me/tone-research", {
        enabled, version: pref.version, revision: pref.revision, expected_member: me.user_id, adult_confirmed: enabled,
      });
      setMe(previous => previous?.user_id === me.user_id ? { ...previous, tone_research: data } : previous);
      setMessage(enabled ? "Future eligible conversations can now contribute." : "Your contributions are excluded when reports reload.");
    } catch (error) {
      if (error.response?.status === 409) {
        try {
          const { data } = await api.get("/me");
          setMe(previous => previous?.user_id === data.user_id ? { ...previous, tone_research: data.tone_research } : previous);
        } catch { /* Keep the last confirmed preference. */ }
      }
      setMessage(error.response?.data?.detail || "Your preference couldn’t be saved. Try again.");
    } finally { setBusy(false); }
  };

  return <section className="space-y-2 border-t border-[var(--line)] pt-3 text-sm" aria-label="Conversation tone research">
    <h3 className="font-semibold">Conversation tone research</h3>
    <p>Allow group comparisons of tone estimates, reply styles and your optional feedback on whether a read fit, you sent the reply, or it helped. Only new reads after you opt in qualify. Message wording, drafts and written corrections stay private.</p>
    <p>Shared Couples reads require both partners to opt in. If you also joined questionnaire research, comparisons can include your own selected demographics, with age and height grouped into ranges. If you separately allow research CSV downloads, new reads can contribute coded feedback rows under that permission. You can withdraw here at any time.</p>
    <label className="flex items-start gap-2 font-semibold"><input type="checkbox" className="mt-1 shrink-0"
      checked={pref.enabled} disabled={busy} onChange={e => change(e.target.checked)} />
      I’m 18 or older and agree to conversation tone research.</label>
    {message && <p role="status">{message}</p>}
  </section>;
}
