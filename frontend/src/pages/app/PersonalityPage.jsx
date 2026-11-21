import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import RepeatBasics from "@/components/app/RepeatBasics";
import { PersonalityPanel } from "@/components/app/Personality";
import { PersonalPortrait } from "@/components/app/PersonalPortrait";
import { buildSavedPortrait } from "@/components/app/personalPortraitModel";
import { errMsg } from "@/lib/api";
import { personalQuestionnaireComplete } from "@/components/app/QuizForm";

export default function PersonalityPage({ withinCouples = false }) {
  return <Navigate replace to={(withinCouples ? "/app/couples/personal" : "/app/personal") + "?tab=results"} />;
}

export function PersonalityResults({ me, api, resource, withinCouples, allowResults = false }) {
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);
  const personalPath = withinCouples ? "/app/couples/personal" : "/app/personal";
  const intimacyPath = withinCouples ? "/app/couples/intimacy" : "/app/intimacy";
  const { quiz, about, summary, radar } = resource.data;
  const complete = allowResults && personalQuestionnaireComplete(quiz, resource.answers.active);
  const underage = about.demographics?.age != null && about.demographics.age < 18;
  const pickType = async code => {
    try { await resource.save({ mbti: code }); }
    catch (failure) { toast.error(errMsg(failure)); }
  };
  const removePrevious = async id => {
    setBusy(true);
    try {
      const saved = await resource.save({ answers: { [id]: null }, expected_earlier_answer: { [id]: resource.answers.previous[id] } });
      if (saved) toast.success("Earlier answer removed.");
    } catch (failure) { toast.error(errMsg(failure)); }
    finally { setBusy(false); }
  };
  return <div className="space-y-4" data-testid="personal-results">
    {complete ? <>
      <h2 className="font-display font-black uppercase text-3xl">Your Personality</h2>
      <PersonalPortrait profile={buildSavedPortrait(quiz, resource.answers.active, summary)} onStartPersonal={() => nav(personalPath + "?tab=basics")}
        onExploreIntimacy={underage ? undefined : () => nav(intimacyPath)} />
      <PersonalityPanel quiz={quiz} savedAnswers={resource.answers.active} summary={summary} radar={radar} onPick={pickType} api={api} />
    </> : <div className="space-y-3" role="status" data-testid="personality-incomplete">
      <p>Complete and save every question in The basics, Go deeper, and Values &amp; communication to see your personality results.</p>
      <button type="button" className="btn-lime px-4 py-2 text-sm" onClick={() => nav(personalPath + "?tab=basics")}>Continue Personal</button>
    </div>}
    {Object.keys(resource.answers.previous).length > 0 && <details className="card p-5 space-y-2">
      <summary className="cursor-pointer font-semibold">Answers from earlier quiz versions</summary>
      <p className="text-sm text-[var(--muted)]">These selections use the wording you saw. Your current results use questions from the current quiz.</p>
      {Object.entries(resource.answers.previous).map(([id, answer]) => <div key={id} className="border-t border-[var(--line)] pt-2 text-sm">
        <p>{answer?.question || id}</p><p className="font-semibold">{answer?.label || "Original wording unavailable"}</p>
        <button type="button" className="underline" disabled={busy} onClick={() => removePrevious(id)}>Remove earlier answer</button>
      </div>)}
    </details>}
    {complete && <section className="card p-5 space-y-2">
      <h3 className="font-semibold">Your Basics answers</h3>
      <p className="text-sm text-[var(--muted)]">You can revisit every question and change any answer as soon as you like. Your edits update your portrait.</p>
      <button type="button" className="btn-ghost px-3 py-1.5 text-sm" data-testid="review-basics-now"
        onClick={() => nav(personalPath + "?tab=basics")}>Review my Basics now</button>
    </section>}
    {complete && !underage && <RepeatBasics key={me.questionnaire_research?.revision} me={me} api={api} />}
  </div>;
}
