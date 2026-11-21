import { useState } from "react";

export default function QuestionnaireResearchPreference({ me, setMe, api }) {
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState("");
  if (me.role !== "member" || !me.questionnaire_research) return null;
  const pref = me.questionnaire_research;

  async function change(enabled) {
    setSaving(true);
    setFeedback("");
    try {
      const { data } = await api.patch("/me/questionnaire-research", {
        enabled, revision: pref.revision, version: pref.version, adult_confirmed: enabled, expected_member: me.user_id,
      });
      setMe(previous => previous?.user_id === me.user_id ? { ...previous, questionnaire_research: data } : previous);
      setFeedback(enabled ? "Your saved answers can now count in research comparisons." : "Your answers are excluded when the Research report is next loaded.");
    } catch (error) {
      if (error.response?.status === 409) {
        try {
          const { data } = await api.get("/me");
          if (data.user_id === me.user_id) setMe(previous => previous?.user_id === me.user_id
            ? { ...previous, questionnaire_research: data.questionnaire_research } : previous);
        } catch { /* The last confirmed setting is still displayed. */ }
      }
      setFeedback(error.response?.data?.detail || "Your research preference couldn’t be saved. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return <section id="questionnaire-research" className="space-y-2 border-t border-[var(--line)] pt-3 text-sm">
    <h3 className="font-semibold text-[var(--ink)]">Questionnaire research</h3>
    <p>Your questionnaire answers save to your account. You can also let Imladri compare your past and future multiple-choice answers across members, including Individual, Sex &amp; Intimacy and Couples. This includes any age, height, gender, sex, ethnicity, sexuality and education you provide. Reports group ages and heights into ranges and can summarize how the two measurements vary together.</p>
    <p>This permission lets staff see group results. Pair comparisons require both partners’ participation. Written answers, notes and conversations stay outside the report. You can turn participation off here at any time.</p>
    <label className="flex items-start gap-2 font-semibold text-[var(--ink)]">
      <input type="checkbox" className="mt-1 shrink-0" checked={pref.enabled} disabled={saving}
        data-testid="questionnaire-research-consent" onChange={e => change(e.target.checked)} />
      I’m 18 or older and agree to ongoing questionnaire research.
    </label>
    {feedback && <p role="status">{feedback}</p>}
  </section>;
}
