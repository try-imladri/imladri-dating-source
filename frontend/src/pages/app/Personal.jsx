import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useOutletContext, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ArrowRight } from "lucide-react";
import { Textarea } from "@/components/ui/textarea";
import { QuizForm, answerDiff, answersForQuiz, mergeChanges, personalQuestionnaireComplete } from "@/components/app/QuizForm";
import { Ring } from "@/components/app/Meter";
import { PatternsPanel } from "@/components/app/Patterns";
import { DemoFields } from "@/components/app/Demographics";
import { errMsg } from "@/lib/api";
import { MemberLoadState } from "@/components/app/MemberResource";
import { currentPlanName } from "@/components/app/PlanUsage";
import { sectionNames } from "@/lib/personalQuestionnaires";
import { personalSectionRedirect } from "@/lib/workspaceAccess";
import { trackProfileWrite, waitForProfileWrites } from "@/lib/profileWriteBarrier";
import { readAssessmentProfile } from "@/lib/assessmentResource";

const PersonalityResults = lazy(() => import("./PersonalityPage").then(module => ({ default: module.PersonalityResults })));

const TABS = [["basics", sectionNames.short, "short"], ["deeper", sectionNames.extended, "extended"], ["further", sectionNames.further, "further"], ["patterns", "Your patterns"]];

function profilePatch(before, after) {
  const patch = { answers: answerDiff(before.answers, after.answers) };
  if (before.describe !== after.describe) patch.describe = after.describe;
  const demographics = answerDiff(before.demo, after.demo);
  if (Object.keys(demographics).length) patch.demographics_patch = demographics;
  return patch;
}

function demographicsForSave(draft, saved) {
  const demo = { ...draft };
  let invalid = "";
  for (const [key, min, max, error] of [
    ["age", 13, 120, "Enter an age between 13 and 120."],
    ["height_cm", 50, 300, "Enter your height in whole centimeters from 50 to 300."],
  ]) {
    const value = draft[key];
    if (value == null || (Number.isInteger(value) && value >= min && value <= max)) continue;
    if (!invalid) invalid = error;
    if (Object.hasOwn(saved, key)) demo[key] = saved[key];
    else delete demo[key];
  }
  return { demo, invalid };
}

function hasEdits(patch) {
  return Boolean(Object.keys(patch.answers).length || "describe" in patch || patch.demographics_patch);
}

function Snapshot({ summary, describe, setDescribe, demo, setDemo, save, busy, canSave, firstTime }) {
  return (
    <aside className="card min-w-0 max-w-full p-5 space-y-4 [overflow-wrap:anywhere]" data-testid="personal-snapshot">
      <details>
        <summary className="flex cursor-pointer items-center gap-3 rounded-lg">
          <span aria-hidden="true"><Ring score={summary?.score || 0} size={56} testid="personal-completeness" /></span>
          <span><span className="block font-display font-black uppercase text-lg leading-none">Your profile</span>
            <span className="sr-only">{summary?.score || 0}% complete. </span>
            <span className="block text-xs text-[var(--muted)] underline mt-1">View completion details</span></span>
        </summary>
        <div className="mt-2 pl-3 text-xs" data-testid="personal-missing">
          {summary?.missing?.length ? <ul className="list-disc list-inside space-y-1">{summary.missing.map(m => <li key={m}>{m}</li>)}</ul> : <p>Every Personal question is complete.</p>}
        </div>
      </details>
      {summary?.tags?.length > 0 && <div className="flex flex-wrap gap-1" data-testid="personal-tags">{summary.tags.map((t) => <span key={t} className="min-w-0 max-w-full text-[11px] px-2 py-0.5 rounded-full bg-[var(--ink)] text-[var(--bg)]">{t}</span>)}</div>}
      <div data-testid="personal-describe">
        <label htmlFor="personal-describe" className="block text-sm font-semibold">How would your best friend describe you?</label>
        <Textarea id="personal-describe" rows={2} className="mt-2 text-sm" maxLength={2000} value={describe} onChange={(e) => setDescribe(e.target.value)} placeholder="e.g. loyal, funny, a bit of an over-thinker" data-testid="personal-describe-input" />
      </div>
      <div id="about-demographics" className="scroll-mt-6">
        <p className="text-sm font-semibold mb-1.5">About you <span className="text-[var(--muted)] font-normal">(optional)</span></p>
        <DemoFields own value={demo} onChange={setDemo} prefix="personal" />
        <a className="inline-block mt-2 text-xs underline" href="/app/settings#questionnaire-research">Manage questionnaire research</a>
      </div>
      <button disabled={busy || !canSave} onClick={save} className="w-full py-2.5 rounded-full bg-[var(--ink)] text-[var(--bg)] font-bold disabled:opacity-40 inline-flex items-center justify-center gap-2" data-testid="personal-save">
        {busy ? "Saving…" : firstTime ? <>Save & add your person<ArrowRight className="w-4 h-4" /></> : "Save"}
      </button>
      <p className="text-[11px] text-[var(--muted)]">Your saved profile helps personalize your comparisons and conversation suggestions.</p>
    </aside>
  );
}

export function PersonalRoute({ withinCouples = false }) {
  const [params] = useSearchParams();
  const redirect = personalSectionRedirect(params, withinCouples);
  return redirect ? <Navigate to={redirect} replace /> : <Personal withinCouples={withinCouples} />;
}

export default function Personal({ withinCouples = false }) {
  const context = useOutletContext();
  return <MemberPersonal key={context.me.user_id} {...context} withinCouples={withinCouples} />;
}

function MemberPersonal({ me, setMe, api, withinCouples = false }) {
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const requestedTab = params.get("tab");
  const tab = TABS.some(([key]) => key === requestedTab) ? requestedTab : "basics";
  const [quiz, setQuiz] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [orig, setOrig] = useState({});
  const [savedDetails, setSavedDetails] = useState({ describe: "", demographics: {} });
  const [answers, setAnswers] = useState({});
  const [describe, setDescribe] = useState("");
  const [demo, setDemo] = useState({});
  const [summary, setSummary] = useState(null);
  const [savedAbout, setSavedAbout] = useState(null);
  const [radar, setRadar] = useState([]);
  const planName = currentPlanName(me.usage, me.membership_plan_id);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [hasSaved, setHasSaved] = useState(false);
  const profileState = useRef(null);
  profileState.current = { orig, savedDetails };
  const pendingSave = useRef(null);
  const leaving = useRef(null);
  const mounted = useRef(false);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const firstTime = !withinCouples && !me.onboarded && (me.onboarding_step || 1) <= 1;
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setLoadError("");
    readAssessmentProfile(api, me.user_id, { signal: controller.signal }).then(result => {
      if (!active) return;
      setQuiz(result.quiz);
      const { active: saved } = answersForQuiz(result.about, result.quiz);
      setOrig(saved);
      setAnswers(saved);
      setSavedDetails({ describe: result.about.describe || "", demographics: result.about.demographics || {} });
      setDescribe(result.about.describe || "");
      setDemo(result.about.demographics || {});
      setSummary(result.summary);
      setSavedAbout(result.about);
      setRadar(result.radar);
    }).catch((error) => {
      if (active) setLoadError(errMsg(error));
    });
    return () => { active = false; controller.abort(); };
  }, [api, me.user_id, loadAttempt]);

  const change = (id, v) => setAnswers((x) => {
    const n = { ...x };
    if (v) n[id] = v;
    else delete n[id];
    return n;
  });
  const validated = demographicsForSave(demo, savedDetails.demographics);
  const invalid = validated.invalid;
  const draft = { answers, describe, demo: validated.demo };
  const body = profilePatch({ answers: orig, describe: savedDetails.describe, demo: savedDetails.demographics }, draft);
  const ready = hasEdits(body);
  const dirty = ready || Boolean(invalid);
  const draftKey = JSON.stringify(body);
  const save = async (automatic = false) => {
    if (pendingSave.current) return;
    if (invalid && !automatic) return toast.error(invalid);
    setSaveError("");
    setBusy(true);
    const request = trackProfileWrite(api, me.user_id, api.put("/me/about", { ...body, expected_member: me.user_id }));
    pendingSave.current = { request, draft };
    try {
      const { data } = await request;
      if (!mounted.current) return;
      const { active: saved } = answersForQuiz(data.about, quiz);
      const savedDemo = data.about.demographics || {};
      const savedDescribe = data.about.describe || "";
      setOrig(saved);
      setSavedDetails({ describe: savedDescribe, demographics: savedDemo });
      // Apply the server's merged profile without dropping edits typed while saving.
      setAnswers((current) => mergeChanges(saved, answerDiff(answers, current)));
      setDemo((current) => mergeChanges(savedDemo, answerDiff(draft.demo, current)));
      setDescribe((current) => current === describe ? savedDescribe : current);
      setSummary(data.summary);
      setSavedAbout(data.about);
      setRadar(data.radar);
      setHasSaved(true);
      if (automatic) return;
      const fresh = (await api.get("/me")).data;
      if (!mounted.current || fresh.user_id !== me.user_id) return;
      setMe(fresh);
      if (!withinCouples && !fresh.onboarded && fresh.onboarding_step === 2) {
        toast.success("Saved. Now add the person on your mind.");
        nav("/app/people?add=1");
      } else toast.success("Your everyday Personal answers were saved.");
    } catch (e) {
      if (mounted.current) {
        setSaveError("Your edits haven’t been saved. Use Save to retry.");
        if (!automatic) toast.error(errMsg(e));
      }
    } finally {
      pendingSave.current = null;
      if (mounted.current) setBusy(false);
    }
  };
  const saveResult = async patch => {
    await waitForProfileWrites(api, me.user_id);
    if (!mounted.current) return null;
    const previous = profileState.current;
    let data;
    try {
      const response = await trackProfileWrite(api, me.user_id,
        api.put("/me/about", { ...patch, expected_member: me.user_id }));
      data = response.data;
    } catch (failure) {
      if (!mounted.current && failure.code === "ERR_CANCELED") return null;
      throw failure;
    }
    if (!mounted.current) return null;
    const saved = answersForQuiz(data.about, quiz).active;
    const savedDemo = data.about.demographics || {};
    const savedDescribe = data.about.describe || "";
    setOrig(saved);
    setAnswers(current => mergeChanges(saved, answerDiff(previous.orig, current)));
    setSavedDetails({ describe: savedDescribe, demographics: savedDemo });
    setDemo(current => mergeChanges(savedDemo, answerDiff(previous.savedDetails.demographics, current)));
    setDescribe(current => current === previous.savedDetails.describe ? savedDescribe : current);
    setSavedAbout(data.about);
    setSummary(data.summary);
    setRadar(data.radar);
    return data;
  };
  const autosave = useRef(null);
  autosave.current = () => save(true);
  leaving.current = quiz ? { body, draft, api, owner: me.user_id } : null;
  useEffect(() => {
    if (!quiz || !ready || busy || saveError) return;
    const timer = setTimeout(() => autosave.current(), 450);
    return () => clearTimeout(timer);
  }, [quiz, ready, draftKey, busy, saveError]);
  useEffect(() => {
    if (!dirty && !busy) return;
    const warn = event => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, busy]);
  useEffect(() => () => {
    // SPA navigation can unmount between the last edit and its scheduled save.
    const last = leaving.current;
    if (!last) return;
    const flush = patch => {
      if (hasEdits(patch)) return last.api.put("/me/about", { ...patch, expected_member: last.owner }).catch(() => {
        toast.error("Your last Personal edits couldn’t be saved.");
      });
    };
    const active = pendingSave.current;
    const completion = active ? active.request.then(() => flush(profilePatch(active.draft, last.draft)), () => flush(last.body)) : flush(last.body);
    if (completion) trackProfileWrite(last.api, last.owner, completion);
  }, []);
  if (!quiz) return <MemberLoadState label="your Personal answers" error={loadError} retry={() => setLoadAttempt((attempt) => attempt + 1)} testId="personal-retry" />;
  const doneIn = (set) => quiz[set].filter((q) => answers[q.id]).length;
  const removedAnswer = Object.keys(orig).some((id) => !answers[id]);
  const canSave = doneIn("short") > 0 || doneIn("extended") > 0 || doneIn("further") > 0 || removedAnswer ||
    describe.trim() || summary?.describe || Object.values(demo).some(Boolean) || Object.values(summary?.demographics || {}).some(Boolean);
  const set = TABS.find((t) => t[0] === tab)?.[2];
  const resultsReady = personalQuestionnaireComplete(quiz, orig) && personalQuestionnaireComplete(quiz, answers);
  const showingResults = requestedTab === "results";
  const resultResource = showingResults ? { data: { quiz, about: savedAbout, summary, radar },
    answers: answersForQuiz(savedAbout, quiz), save: saveResult } : null;

  return (
    <div className="min-w-0 max-w-full space-y-4" data-testid="personal-page">
      {(dirty || busy || saveError || hasSaved) && <p role="status" className="text-xs text-[var(--muted)]">
        {invalid || saveError || (dirty || busy ? "Saving your profile…" : "Profile saved")}
      </p>}
      <header className="flex flex-col 2xl:flex-row 2xl:items-end justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-display font-black uppercase text-4xl sm:text-5xl leading-none" data-testid="personal-title">{withinCouples ? "My assessments" : firstTime ? "First, you" : "Personal"}</h1>
            {planName && <Link to="/app/settings/upgrade" className="rounded-full border border-[var(--line)] px-3 py-1 text-xs font-semibold hover:border-[var(--ink)]" data-testid="personal-plan" aria-label={`Your plan, ${planName}. View plans`}>{planName}</Link>}
          </div>
          <p className="text-sm text-[var(--muted)] mt-2 max-w-2xl">{firstTime ? "12 quick taps and one line about you. Then we add the person on your mind and get your first report." : "Come back any time to answer more and add detail. The more you add, the more personal the advice."}</p>
          {withinCouples && <p className="mt-2 max-w-2xl text-sm text-[var(--muted)]" data-testid="couples-assessments-privacy">
            These are your own private answers. Joining Couples does not share them with your partner. Review sharing with your current partner and optional research in <Link to="/app/settings#data-permissions" className="underline">Settings → Data &amp; privacy</Link>. <Link to="/app/couples/compatibility" className="underline">Compatibility</Link> uses only assessments you both choose to share.
          </p>}
        </div>
      <nav aria-label="Personal sections" className="flex gap-1 flex-wrap" data-testid="personal-tabs">
        {[...TABS, ...(resultsReady ? [["results", "Your Personality"]] : [])].map(([k, l, s]) => (
          <button key={k} id={`personal-tab-${k}`} aria-current={(showingResults ? k === "results" : tab === k) ? "page" : undefined} onClick={() => setParams({ tab: k })} data-testid={`personal-tab-${k}`}
            className={`shrink-0 px-4 py-2 rounded-full text-sm border transition-colors ${(showingResults ? k === "results" : tab === k) ? "bg-[var(--ink)] text-[var(--bg)] border-[var(--ink)] font-semibold" : "border-[var(--line)] text-[var(--muted)] hover:text-[var(--ink)]"}`}>
            {l}{s && <span className="font-mono text-xs ml-1.5">{doneIn(s)}/{quiz[s].length}</span>}
          </button>
        ))}
      </nav>
      </header>
      {showingResults ? <Suspense fallback={<p className="text-sm text-[var(--muted)]">Loading…</p>}><PersonalityResults me={me} api={api} resource={resultResource} withinCouples={withinCouples} allowResults={resultsReady} /></Suspense> : <div className="grid min-w-0 gap-4 items-start xl:grid-cols-[minmax(0,1fr)_20rem]" data-testid="personal-questionnaire-layout">
        <div className="min-w-0 flex flex-col">
          {set && <QuizForm key={set} showTopics={set === "further"} questions={quiz[set]} answers={answers} onChange={change} />}
          {tab === "patterns" && <PatternsPanel api={api} className="flex-1" allowScan={!withinCouples} />}
        </div>
        <Snapshot summary={resultsReady ? summary : { ...summary, tags: [] }} describe={describe} setDescribe={setDescribe} demo={demo} setDemo={setDemo} save={() => save()} busy={busy} canSave={canSave} firstTime={firstTime} />
      </div>}
    </div>
  );
}
