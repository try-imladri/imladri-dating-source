import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, MessageCircle, SlidersHorizontal } from "lucide-react";
import { choices, conditions, publishedTopics } from "./intimacyTopics";
import { SavedIntimacySession } from "./SavedIntimacySession";
import { IntimacyResults } from "./IntimacyResults";
import WordingFeedback from "./WordingFeedback";
import { IntimacyResearch } from "./IntimacyResearch";

const activityInfo = {
  talk: {
    title: "Intimacy & communication",
    description: "Explore affection, desire, privacy and how you like to talk about intimacy.",
    icon: MessageCircle,
  },
  bdsm: {
    title: "BDSM interests & boundaries",
    description: "Explore power, roles, sensation and the boundaries that matter to you.",
    icon: SlidersHorizontal,
  },
};
const headingClass = "font-display font-black uppercase text-2xl sm:text-3xl leading-tight scroll-mt-20";
const primaryButton = "btn-lime px-5 py-2.5 text-sm";
const secondaryButton = "btn-ghost px-4 py-2.5 text-sm";
const textButton = "py-2 text-sm text-[var(--muted)] underline underline-offset-4";
const cardClass = "card max-w-2xl p-5 sm:p-6 space-y-4";
const countQuestions = (sections) => sections.reduce((total, section) => total + section.prompts.length, 0);

function StepHeading({ children }) {
  const ref = useRef(null);
  useEffect(() => { ref.current?.focus(); }, [children]);
  return <h2 ref={ref} tabIndex={-1} className={headingClass}>{children}</h2>;
}

function ConditionalAnswer({ prompt, initial, onContinue, onBack, onExit, exitLabel }) {
  const [selected, setSelected] = useState(initial);
  function toggle(condition) {
    setSelected((current) => current.includes(condition)
      ? current.filter((item) => item !== condition) : [...current, condition]);
  }
  return <div className={cardClass}>
    <p className="text-sm text-[var(--muted)]">{prompt}</p>
    <StepHeading>What does it depend on?</StepHeading>
    <p className="text-sm">Choose any conditions that fit, or continue with “It depends.”</p>
    <div className="flex flex-wrap gap-2">
      {conditions.map((condition) => <button type="button" key={condition} aria-pressed={selected.includes(condition)}
        className={selected.includes(condition) ? primaryButton : secondaryButton} onClick={() => toggle(condition)}>{condition}</button>)}
    </div>
    <div className="flex flex-wrap gap-3 items-center border-t border-[var(--line)] pt-3">
      <button type="button" className={primaryButton} onClick={() => onContinue(selected)}>Continue</button>
      <button type="button" className={textButton} onClick={onBack}>Back to answers</button>
      <button type="button" className={textButton} onClick={onExit}>{exitLabel}</button>
    </div>
  </div>;
}

function Explorer({ onExit, owner, savedProfile, overrides, api, catalogVersion, intimacySignature, initialAnswers = {}, saveAnswer, removeAnswer, previous, previousAnswers = [], requestRemoval }) {
  const activities = useMemo(() => Object.fromEntries(Object.entries(publishedTopics(overrides)).map(
    ([key, sections]) => [key, { ...activityInfo[key], sections }]
  )), [overrides]);
  const allSections = useMemo(() => Object.values(activities).flatMap((activity) => activity.sections), [activities]);
  const [page, setPage] = useState({ view: "activities" });
  const [includeProfile, setIncludeProfile] = useState(false);
  const [topicSearch, setTopicSearch] = useState("");
  const shortSections = ["scene-planning","aftercare","privacy-community","language-tone"]
    .map(id => activities.bdsm.sections.find(s => s.id === id)).filter(s => s?.prompts.length);
  const quickQuestion = (index, review) => ({view:"question", activity:"bdsm", section:shortSections[index].id, step:0, quick:index, review});
  // A missing entry is unanswered. A saved null is an explicit skip.
  const [answers, setAnswers] = useState(initialAnswers);
  const [removedQuestion, setRemovedQuestion] = useState(null);
  const exitLabel = saveAnswer ? "Exit questionnaire" : "Clear & exit";
  const visitedCount = (id) => Object.keys(answers[id] || {}).length;
  const activity = activities[page.activity];
  const section = activity?.sections.find((item) => item.id === page.section);
  const hasProgress = Object.keys(answers).length > 0;
  const activityList = () => setPage({ view: "activities" });
  const sectionList = (key = page.activity) => setPage({ view: "sections", activity: key });
  const showSummary = () => setPage({
    view: "summary",
    activity: page.activity,
    resumeFrom: page.view === "question" ? page : null,
  });

  function openSection(target, review = false) {
    const activityKey = activities.talk.sections.includes(target) ? "talk" : "bdsm";
    const visited = visitedCount(target.id);
    const firstOpen = target.prompts.findIndex((_, index) => !Object.hasOwn(answers[target.id] || {}, index));
    const view = !review && visited === target.prompts.length ? "result" : "question";
    setPage({ view, activity: activityKey, section: target.id, step: review || firstOpen < 0 ? 0 : firstOpen, review });
  }
  async function forgetAnswer() {
    const key = `i:${section.id}:${page.step}`;
    if (removeAnswer && !(await removeAnswer(key))) return;
    setAnswers(current => {
      const updated = { ...current };
      const responses = current[section.id].slice();
      delete responses[page.step];
      if (Object.keys(responses).length) updated[section.id] = responses;
      else delete updated[section.id];
      return updated;
    });
    setRemovedQuestion(key);
  }

  async function respond(choice, selectedConditions = []) {
    setRemovedQuestion(null);
    const { step } = page;
    const selection = choice ? { choice, conditions: selectedConditions } : null;
    if (saveAnswer) {
      const [topic, question, phrase] = section.prompts[step];
      const saved = await saveAnswer({ id: `i:${section.id}:${step}`, topic, question, phrase, selection });
      if (!saved) return;
    }
    setAnswers((previous) => {
      const responses = (previous[section.id] || []).slice();
      responses[step] = selection;
      return { ...previous, [section.id]: responses };
    });
    if (Number.isInteger(page.quick)) {
      const next = page.review ? page.quick + 1 : shortSections.findIndex(item =>
        item.id !== section.id && !Object.hasOwn(answers[item.id] || {}, 0));
      setPage(next >= 0 && next < shortSections.length ? quickQuestion(next, page.review) : {view:"summary", activity:"bdsm"});
      return;
    }
    const next = page.review ? step + 1 : section.prompts.findIndex((_, index) =>
      index !== step && !Object.hasOwn(answers[section.id] || {}, index));
    setPage({ ...page, step: next, view: next < 0 || next >= section.prompts.length ? "result" : "question" });
  }

  if (page.view === "conditions") {
    return <ConditionalAnswer prompt={section.prompts[page.step][1]} initial={answers[section.id]?.[page.step]?.conditions || []}
      onContinue={(selected) => respond("It depends", selected)}
      onBack={() => setPage({ ...page, view: "question" })} onExit={onExit} exitLabel={exitLabel} />;
  }
  if (page.view === "question") {
    const [, prompt] = section.prompts[page.step];
    const previous = answers[section.id]?.[page.step]?.choice;
    const hasAnswers = Object.values(answers).some((responses) => responses.some(Boolean));
    return <div className={cardClass}>
      <div className="flex items-center justify-between gap-3">
        <p className="font-semibold text-sm">{section.title}</p>
        <span className="font-mono text-xs text-[var(--muted)] shrink-0">{Number.isInteger(page.quick) ? `Short check-in ${page.quick + 1} of ${shortSections.length}` : `Question ${page.step + 1} of ${section.prompts.length}`}</span>
      </div>
      <StepHeading>{prompt}</StepHeading>
      <div className="flex flex-wrap gap-2">
        {choices.map((choice) => <button key={choice} type="button" aria-pressed={previous === choice}
          className={previous === choice ? primaryButton : secondaryButton}
          onClick={() => choice === "It depends" ? setPage({ ...page, view: "conditions" }) : respond(choice)}>{choice}</button>)}
        <button type="button" className={textButton} onClick={() => respond(null)}>Skip</button>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-[var(--line)] pt-2">
        {Object.hasOwn(answers[section.id] || {}, page.step) &&
          <button type="button" className={textButton} onClick={forgetAnswer}>{previous ? "Remove my answer" : "Remove my skip"}</button>}
        {removedQuestion === `i:${section.id}:${page.step}` && <p role="status" className="text-sm">This question is unanswered again.</p>}
        {hasAnswers && <button type="button" className={secondaryButton} onClick={showSummary}>See my answers so far</button>}
        {page.step > 0 && <button type="button" className="inline-flex items-center gap-1 py-2 text-sm"
          onClick={() => setPage({ ...page, step: page.step - 1 })}><ArrowLeft aria-hidden="true" className="w-4 h-4" />Back</button>}
        <button type="button" className={textButton} onClick={() => sectionList()}>Choose another section</button>
        <button type="button" className={textButton + " ml-auto"} onClick={onExit}>{exitLabel}</button>
      </div>
    </div>;
  }

  if (page.view === "summary" || page.view === "result") {
    const combined = page.view === "summary";
    const finished = allSections.every((item) => visitedCount(item.id) === item.prompts.length);
    return <div className={cardClass}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <StepHeading>{combined ? "Your intimacy summary" : "Your section results"}</StepHeading>
          {!combined && <p className="mt-1 text-sm font-semibold">{section.title}</p>}
        </div>
        {combined && page.resumeFrom && <button type="button" className={secondaryButton}
          onClick={() => setPage(page.resumeFrom)}>Continue where I left off</button>}
      </div>
      {combined && finished && !saveAnswer && api && catalogVersion && <IntimacyResearch api={api} answers={answers}
        catalogVersion={catalogVersion} sections={allSections} intimacySignature={intimacySignature} />}
      <IntimacyResults key={combined ? "all" : section.id} combined={combined} sections={combined ? allSections : [section]}
        answers={answers} profile={combined ? savedProfile : undefined} includeProfile={includeProfile}
        onToggleProfile={() => setIncludeProfile((included) => !included)} onReview={(target) => openSection(target, true)} />
      {combined && Object.values(answers).some(items => items.some(Boolean)) && api && owner && intimacySignature && <WordingFeedback api={api} owner={owner} signature={intimacySignature} sections={allSections} />}
      <div className="flex flex-wrap gap-2 border-t border-[var(--line)] pt-4">
        {combined ? <>
          <button type="button" className={primaryButton} onClick={onExit}>{saveAnswer || finished ? "Finish this visit" : "Clear & finish"}</button>
          <button type="button" className={secondaryButton} onClick={activity ? () => sectionList() : activityList}>{activity ? "Choose another section" : "Choose an activity"}</button>
        </> : <>
          <button type="button" className={finished ? secondaryButton : primaryButton} onClick={() => sectionList()}>Choose another section</button>
          <button type="button" className={finished ? primaryButton : secondaryButton} onClick={showSummary}>{finished ? "Review my completed questionnaire" : "See my summary"}</button>
          <button type="button" className={textButton} onClick={() => openSection(section, true)}>Review answers</button>
          <button type="button" className={textButton} onClick={onExit}>{saveAnswer || finished ? "Finish this visit" : "Clear & finish"}</button>
        </>}
      </div>
    </div>;
  }

  if (page.view === "sections") return <div className="max-w-4xl space-y-4">
    <div>
      <StepHeading>{activity.title}</StepHeading>
      <p className="mt-1 text-sm text-[var(--muted)]">{saveAnswer ? "Choose a section. Your saved progress is available when you return." : "Choose any section. You can switch sections and come back while this tab is open."}</p>
    </div>
    <div className="flex flex-wrap gap-2">
      <button type="button" className={secondaryButton} onClick={activityList}>Choose an activity</button>
      {hasProgress && <button type="button" className={primaryButton} onClick={showSummary}>See my summary</button>}
    </div>
    {page.activity === "bdsm" && <div className="space-y-3">
      {shortSections.length > 0 && <button type="button" className={primaryButton} onClick={() => {
        const firstOpen = shortSections.findIndex(s => !Object.hasOwn(answers[s.id] || {}, 0));
        setPage(quickQuestion(Math.max(0, firstOpen), firstOpen < 0));
      }}>Short check-in · {shortSections.length} questions</button>}
      <p className="text-sm text-[var(--muted)]">Start with planning, aftercare, privacy and language. These are the same questions as in the full sections, so your answers carry over.</p>
      <label className="block text-sm font-semibold">Find a topic
        <input type="search" value={topicSearch} onChange={e => setTopicSearch(e.target.value)} className="block w-full rounded-lg border border-[var(--line)] px-3 py-2 mt-1 bg-transparent" placeholder="Try planning, roles or privacy" />
      </label>
      {topicSearch.trim() && !activity.sections.some(s => (s.title+" "+s.description).toLowerCase().includes(topicSearch.trim().toLowerCase())) && <p role="status">No matching topic. Try another word.</p>}
    </div>}
    <div className="grid sm:grid-cols-2 gap-2.5">
      {activity.sections.filter(item => page.activity !== "bdsm" || (item.title+" "+item.description).toLowerCase().includes(topicSearch.trim().toLowerCase())).map((item) => {
        const visited = visitedCount(item.id);
        const total = item.prompts.length;
        const progress = visited === total ? "View results" : visited ? `Resume · ${visited}/${total} visited` : total + " questions";
        return <button key={item.id} type="button" aria-labelledby={`section-title-${item.id}`} aria-describedby={`section-desc-${item.id} section-progress-${item.id}`}
          onClick={() => openSection(item)} className="card p-4 text-left hover:border-[var(--ink)] transition-colors">
          <span className="flex items-start justify-between gap-2"><span id={`section-title-${item.id}`} className="font-display font-black uppercase text-xl leading-tight">{item.title}</span>
            <ArrowRight aria-hidden="true" className="w-4 h-4 shrink-0 mt-1" /></span>
          <span id={`section-desc-${item.id}`} className="block text-sm text-[var(--muted)] mt-1">{item.description}</span>
          <span id={`section-progress-${item.id}`} className="block font-mono text-xs mt-3">{progress}</span>
        </button>;
      })}
    </div>
    <button type="button" className={textButton} onClick={onExit}>{exitLabel}</button>
  </div>;

  return <div className="w-full min-w-0 space-y-3" data-testid="intimacy-activity-chooser">
    <StepHeading>Choose an activity</StepHeading>
    <p className="text-sm text-[var(--muted)]">{countQuestions(allSections)} questions in {allSections.length} sections. Start with the topics that matter to you.</p>
    {saveAnswer && <p className="text-sm text-[var(--muted)]">Your progress saves to your account. <a className="underline" href="/app/settings#questionnaire-research">Manage questionnaire research in Settings</a>.</p>}
    <div className="grid w-full min-w-0 sm:grid-cols-2 gap-3">
      {Object.entries(activities).map(([key, { title, description, icon: Icon, sections }]) => (
        <button type="button" key={key} aria-labelledby={`activity-title-${key}`} aria-describedby={`activity-description-${key} activity-count-${key}`}
          onClick={() => sectionList(key)} className="card w-full min-w-0 p-5 text-left flex flex-col items-start hover:border-[var(--ink)] transition-colors">
          <span className="flex items-center justify-between w-full mb-3 gap-2">
            <Icon aria-hidden="true" className="w-5 h-5" />
            <span id={`activity-count-${key}`} className="font-mono text-xs text-[var(--muted)]">{sections.length} sections · {countQuestions(sections)} questions</span>
          </span>
          <span id={`activity-title-${key}`} className="font-display font-black uppercase text-2xl leading-tight">{title}</span>
          <span id={`activity-description-${key}`} className="text-sm text-[var(--muted)] mt-1">{description}</span>
          <ArrowRight aria-hidden="true" className="mt-auto pt-3 w-5 h-8" />
        </button>
      ))}
    </div>
    {previousAnswers.length > 0 ? <details className="card p-4 space-y-3 text-sm">
      <summary className="cursor-pointer font-semibold">Answers from earlier question versions ({previousAnswers.length})</summary>
      <p>These entries use the wording you originally saw. You can remove each one or answer its current version.</p>
      {previousAnswers.map(entry => <div key={entry.id} className="space-y-1 border-t border-[var(--line)] pt-2">
        <p className="font-semibold">{entry.question || entry.topic || "Original wording unavailable"}</p>
        <p>{entry.skipped ? "Skipped" : entry.answer || "Original choice unavailable"}{entry.conditions?.length ? " · " + entry.conditions.join(" · ") : ""}</p>
        <button type="button" className={textButton} onClick={() => removeAnswer(entry.id)}>
          {entry.skipped ? "Remove earlier skip" : "Remove earlier answer"}
        </button>
      </div>)}
    </details> : previous > 0 && <p className="text-sm text-[var(--muted)]">Your account export includes {previous} entries from earlier question versions.</p>}
    {requestRemoval && (hasProgress || previous > 0) && <button type="button" className={textButton} onClick={requestRemoval}>Delete saved intimacy answers</button>}
    {hasProgress && <div className="flex flex-wrap gap-3">
      <button type="button" className={primaryButton} onClick={showSummary}>See my summary</button>
      <button type="button" className={textButton} onClick={onExit}>{exitLabel}</button>
    </div>}
  </div>;
}

function AdultActivities({ onExit, accountSaving = false, profileAdultEligible = false, ...props }) {
  // Only the caller's freshly loaded saved profile can skip a repeated age step.
  // The server still checks eligibility when loading and saving account answers.
  const initialMode = profileAdultEligible === true ? (accountSaving ? "account" : "visit") : null;
  const [mode, setMode] = useState(initialMode);
  const [visit, setVisit] = useState(0);
  // A full-page navigation can preserve React state in the browser's back/forward cache.
  useEffect(() => {
    const clear = () => { setMode(null); setVisit(value => value + 1); };
    const restore = (event) => {
      if (event.persisted) { setMode(initialMode); setVisit(value => value + 1); }
    };
    window.addEventListener("pagehide", clear);
    window.addEventListener("pageshow", restore);
    return () => {
      window.removeEventListener("pagehide", clear);
      window.removeEventListener("pageshow", restore);
    };
  }, [initialMode]);

  if (!mode) return (
    <div className="card max-w-2xl p-5 sm:p-6">
      <h2 className={headingClass}>Sex &amp; Intimacy</h2>
      <p className="mt-2 text-sm">These activities are for adults aged 18 and over.</p>
      <p className="mt-1 text-sm text-[var(--muted)]">{accountSaving ? "Imladri saves each answer and skip to your own account as you go. You can return to either track, change or remove one answer or skip, or delete all your saved intimacy answers." : "Your answers stay in this browser while you explore. Leaving this tab or refreshing clears this visit."}</p>
      {accountSaving && <p className="mt-2 text-sm"><a className="underline" href="/app/settings#questionnaire-research">Manage questionnaire research in Settings</a></p>}
      <div className="mt-5 flex flex-wrap gap-2">
        <button type="button" className={primaryButton} onClick={() => setMode(accountSaving ? "account" : "visit")}>{accountSaving ? "I’m 18 or older · save my progress" : "I’m 18 or older"}</button>
        {accountSaving && <button type="button" className={secondaryButton} onClick={() => setMode("visit")}>I’m 18 or older · explore without saving</button>}
        <button type="button" className={secondaryButton} onClick={onExit}>Back to Personal</button>
      </div>
    </div>
  );
  if (mode === "account") return <SavedIntimacySession key={visit} owner={props.owner} api={props.api} onExit={onExit}
    takeInitialAnswers={props.takeInitialAnswers}>
    {storage => <Explorer onExit={onExit} {...props} {...storage} />}
  </SavedIntimacySession>;
  return <Explorer key={visit} onExit={onExit} {...props} />;
}

export function IntimacyPanel({ underage, profileAdultEligible = false, ...props }) {
  return (
    <section aria-label="Sex & Intimacy" data-testid="intimacy-panel">
      {underage ? <div className="card max-w-2xl p-5 sm:p-6">
        <h2 className={headingClass}>For adults aged 18 and over</h2>
        <button type="button" className={`${secondaryButton} mt-4`} onClick={props.onExit}>Back to Personal</button>
      </div> : <AdultActivities key={`${props.owner || "visit"}:${profileAdultEligible === true}:${Boolean(props.accountSaving)}`}
        profileAdultEligible={profileAdultEligible} {...props} />}
    </section>
  );
}
