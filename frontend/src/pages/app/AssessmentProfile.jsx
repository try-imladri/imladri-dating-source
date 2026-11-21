import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { answersForQuiz } from "@/components/app/QuizForm";
import { buildSavedPortrait } from "@/components/app/personalPortraitModel";
import { MemberLoadState } from "@/components/app/MemberResource";
import { currentPlanName } from "@/components/app/PlanUsage";
import { errMsg } from "@/lib/api";
import { trackProfileWrite } from "@/lib/profileWriteBarrier";
import { readAssessmentProfile } from "@/lib/assessmentResource";

// Each page owns only its signed-in member's current server data. No profile or
// questionnaire answers are cached across owners, routes or browser sessions.
export function useAssessmentProfile(api, owner, { includeIntimacy = false } = {}) {
  const [loaded, setLoaded] = useState(null);
  const data = loaded?.api === api && loaded.owner === owner ? loaded.value : null;
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const request = useRef(null);
  const initialIntimacy = useRef(null);
  useEffect(() => {
    const controller = new AbortController();
    request.current = controller;
    initialIntimacy.current = null;
    setLoaded(null);
    setError("");
    readAssessmentProfile(api, owner, { signal: controller.signal, includeIntimacy })
      .then(result => {
        if (controller.signal.aborted) return;
        if (includeIntimacy && result.owner_id === owner && result.intimacy_eligible === true && result.intimacy_answers) {
          initialIntimacy.current = { api, owner, controller, value: result.intimacy_answers };
        }
        setLoaded({api, owner, value: result});
      }).catch(failure => {
        if (!controller.signal.aborted) setError(errMsg(failure));
      });
    return () => { controller.abort(); initialIntimacy.current = null; };
  }, [api, owner, attempt, includeIntimacy]);
  const takeInitialIntimacyAnswers = useCallback(() => {
    const entry = initialIntimacy.current;
    initialIntimacy.current = null;
    return entry && entry.api === api && entry.owner === owner && entry.controller === request.current
      && !entry.controller.signal.aborted ? entry.value : undefined;
  }, [api, owner]);
  const save = useCallback(async patch => {
    const controller = request.current;
    if (!controller || controller.signal.aborted) return null;
    let response;
    try {
      // Navigation aborts reads and suppresses stale UI, but must not abandon an
      // already-started mutation. Other assessment routes wait for its actual
      // completion before reading the same owner's profile.
      const write = api.put("/me/about", { ...patch, expected_member: owner });
      response = await trackProfileWrite(api, owner, write);
    } catch (failure) {
      if ((controller.signal.aborted || request.current !== controller) && failure.code === "ERR_CANCELED") return null;
      throw failure;
    }
    if (controller.signal.aborted || request.current !== controller) return null;
    setLoaded(current => current?.api === api && current.owner === owner
      ? { ...current, value: { ...current.value, ...response.data } } : current);
    return response.data;
  }, [api, owner]);
  const answers = useMemo(() => data ? answersForQuiz(data.about, data.quiz) : { active: {}, previous: {} }, [data]);
  const portrait = useMemo(() => data ? buildSavedPortrait(data.quiz, answers.active, data.summary) : null, [data, answers]);
  return { data, error, retry: () => setAttempt(value => value + 1), save, answers, portrait, takeInitialIntimacyAnswers };
}

export function AssessmentLoadState({ resource, label }) {
  return <MemberLoadState label={label} error={resource.error} retry={resource.retry} testId="assessment-retry" />;
}

export function AssessmentHeader({ title, description, me, withinCouples }) {
  const planName = currentPlanName(me.usage, me.membership_plan_id);
  return <header>
    <div className="flex flex-wrap items-center gap-3">
      <h1 className="font-display font-black uppercase text-4xl sm:text-5xl leading-none">{title}</h1>
      {planName && <Link to="/app/settings/upgrade" className="rounded-full border border-[var(--line)] px-3 py-1 text-xs font-semibold hover:border-[var(--ink)]"
        aria-label={`Your plan, ${planName}. View plans`}>{planName}</Link>}
    </div>
    <p className="text-sm text-[var(--muted)] mt-2 max-w-2xl">{description}</p>
    {withinCouples && <p className="mt-2 max-w-2xl text-sm text-[var(--muted)]" data-testid="couples-assessments-privacy">
      These are your own private answers. Joining Couples does not share them with your partner. Review sharing with your current partner and optional research in <Link to="/app/settings#data-permissions" className="underline">Settings → Data &amp; privacy</Link>. <Link to="/app/couples/compatibility" className="underline">Compatibility</Link> uses only assessments you both choose to share.
    </p>}
  </header>;
}
