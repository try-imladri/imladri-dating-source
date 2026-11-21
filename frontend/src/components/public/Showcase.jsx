import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, MessagesSquare, UserRound, Shapes, Sparkles, RotateCcw } from "lucide-react";
import { pad } from "@/lib/api";
import { alternativeResult, clearQuizHandoff, keepQuizHandoff } from "@/lib/quizHandoff";
import { defaultPublicQuiz } from "@/lib/publicQuiz";

const TABS = [
  { k: "practice", label: "Practice", icon: MessagesSquare, line: "Rehearse the reply before you send it." },
  { k: "personal", label: "Personal", icon: UserRound, line: "Learn your own patterns, not just theirs." },
  { k: "types", label: "Archetypes", icon: Shapes, line: "Spot the type texting you." },
  { k: "quiz", label: "Quiz", icon: Sparkles, line: "Who's texting you? 3 taps." },
];

const PATHS = [
  { t: "Ask directly", you: "I'd like that. Does Thursday work?", sam: [
    { mood: "warm", msg: "Thursday works! 7 at that taco place?", learn: "Sam named a time. The read moves toward \"busy, still interested\"." },
    { mood: "difficult", msg: "ugh this week is insane, I'll let you know", learn: "A second vague answer. If it repeats, it becomes a pattern worth noticing." },
  ] },
  { t: "Match the energy", you: "Ha, sounds good. Let me know.", sam: [
    { mood: "warm", msg: "ok actually, free Saturday?", learn: "Sam followed up without being chased. \"Soon\" meant something." },
    { mood: "difficult", msg: "(no reply for 5 days)", learn: "You learned this without chasing, and kept your footing." },
  ] },
];

function Practice({ onPick }) {
  const [pick, setPick] = useState(null);
  const [s, setS] = useState(0);
  const choose = (i) => { setPick(i); setS(0); onPick(); };
  const p = pick === null ? null : PATHS[pick];
  const r = p && p.sam[s];
  return (
    <div className="space-y-2" data-testid="showcase-practice">
      <p className="bubble-him px-3 py-2 text-sm w-fit">Haha yeah, been a crazy week</p>
      <p className="bubble-him px-3 py-2 text-sm w-fit">we should hang soon tho</p>
      {!p ? (
        <>
          <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)] pt-1">Tap a reply to rehearse it</p>
          <div className="grid grid-cols-2 gap-2">
            {PATHS.map((x, i) => (
              <button key={x.t} onClick={() => choose(i)} className="text-left rounded-xl border border-[var(--line)] p-3 hover:border-[var(--ink)] transition-colors" data-testid={`showcase-reply-${i}`}>
                <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)]">{x.t}</p>
                <p className="bubble-you px-3 py-1.5 text-xs w-fit ml-auto mt-2">{x.you}</p>
              </button>
            ))}
          </div>
        </>
      ) : (
        <div className="rise space-y-2" data-testid="showcase-outcome">
          <p className="bubble-you px-3 py-2 text-sm w-fit ml-auto">{p.you}</p>
          <p key={s} className="rise bubble-him px-3 py-2 text-sm w-fit" data-testid="showcase-sam-reply">{r.msg}</p>
          <p className="text-xs"><span className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)] mr-1.5">Scenario · {r.mood}</span>{r.learn}</p>
          <div className="flex flex-wrap gap-3 text-xs font-semibold">
            <button onClick={() => setS((s + 1) % 2)} className="hover:text-[var(--lime)]" data-testid="showcase-another-scenario">See another way Sam might reply</button>
            <button onClick={() => setPick(null)} className="inline-flex items-center gap-1 text-[var(--muted)] hover:text-[var(--ink)]" data-testid="showcase-practice-reset"><RotateCcw className="w-3 h-3" />Try the other reply</button>
          </div>
        </div>
      )}
    </div>
  );
}

function CatalogStatus({ state, retry }) {
  if (state === "error") return <p role="alert" className="text-sm">Archetypes couldn’t load.{" "}
    <button type="button" className="underline" onClick={retry}>Try again</button>
  </p>;
  return <p role="status" className="text-sm">Loading archetypes…</p>;
}

function Quiz({ types, catalogState, retryCatalog, onPick, published }) {
  const [ans, setAns] = useState([]);
  const [handoff, setHandoff] = useState("");
  const [startedWith, setStartedWith] = useState(null);
  const quiz = startedWith || published || defaultPublicQuiz;
  const step = ans.length;
  const tap = (i) => { setStartedWith(quiz); setAns([...ans, i]); onPick(); };
  if (step === 3) {
    if (catalogState !== "ready") return <div data-testid="showcase-quiz-result">
      <CatalogStatus state={catalogState} retry={retryCatalog} />
    </div>;
    const slug = quiz.outcomes[ans[0]][ans[1]][ans[2]];
    const t = types.find((x) => x.slug === slug && x.unlocked !== false);
    const alternative = alternativeResult(quiz, ans, types);
    return (
      <div className="rise" data-testid="showcase-quiz-result">
        {t && <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)]">Sounds like</p>}
        <p className="font-display font-black uppercase text-3xl leading-none mt-1" data-testid="showcase-quiz-type">{t ? t.name : "This type is not available yet"}</p>
        {t && <p className="text-sm text-[var(--muted)] mt-1">{t.one_liner}</p>}
        <div className="flex flex-wrap items-center gap-4 mt-4">
          <Link to={t ? `/types/${slug}` : "/types"} className="btn-lime px-4 py-2 text-sm inline-flex items-center gap-1.5" data-testid="showcase-quiz-read">{t ? "Read the type" : "Browse archetypes"} <ArrowRight className="w-4 h-4" /></Link>
          <button onClick={() => { clearQuizHandoff(); setHandoff(""); setAns([]); setStartedWith(null); }} className="inline-flex items-center gap-1 text-xs font-semibold text-[var(--muted)] hover:text-[var(--ink)]" data-testid="showcase-quiz-restart"><RotateCcw className="w-3 h-3" />Start over</button>
        </div>
        <p className="text-xs mt-3">This result uses your choices “{quiz.questions[1].a[ans[1]]}” and “{quiz.questions[2].a[ans[2]]}”. Three answers give a starting point.</p>
        {alternative && <p className="text-xs mt-2">If you chose “{alternative.choice}” for “{alternative.question}”, the result would be <Link className="underline" to={"/types/"+alternative.type.slug}>{alternative.type.name}</Link>.</p>}
        {t && <div className="mt-3 text-sm">
          {!handoff ? <><button type="button" className="underline font-semibold" onClick={()=>setHandoff(keepQuizHandoff(t) ? "kept" : "unavailable")}>Keep this result for my Start screen</button>
            <span className="mx-2">·</span><button type="button" className="underline" onClick={()=>{clearQuizHandoff();setHandoff("declined");}}>Finish here</button></> :
            handoff==="kept" ? <p role="status">Saved in this tab for 24 hours. <Link className="underline" to="/app/start">Open Start</Link> · <button className="underline" onClick={()=>{clearQuizHandoff();setHandoff("declined");}}>Remove</button></p> :
            <p role="status">{handoff==="declined" ? "Quiz complete." : "This browser couldn’t keep the result. You can still read the type."}</p>}
        </div>}
      </div>
    );
  }
  const q = quiz.questions[step];
  return (
    <div key={step} className="rise" data-testid="showcase-quiz">
      <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)]">Question {step + 1} of 3</p>
      <p className="font-semibold mt-1" data-testid="showcase-quiz-question">{q.q}</p>
      <div className="grid gap-2 mt-3">
        {q.a.map((a, i) => (
          <button key={a} onClick={() => tap(i)} className="text-left rounded-xl border border-[var(--line)] px-3 py-2.5 text-sm hover:bg-[var(--ink)] hover:text-[var(--bg)] transition-colors" data-testid={`showcase-quiz-option-${i}`}>{a}</button>
        ))}
      </div>
    </div>
  );
}

function Personal() {
  const rows = [["How you reply when anxious", "Fast, then over-explains"], ["What you need", "Clear plans, not hints"], ["Your blind spot", "Filling silence for them"]];
  return (
    <div data-testid="showcase-personal">
      <div className="flex items-center justify-between">
        <p className="font-display font-black uppercase text-xl">Your profile</p>
        <span className="font-mono text-xs text-[var(--lime)]">72% complete</span>
      </div>
      <div className="h-1.5 rounded-full bg-[var(--line)] mt-2 overflow-hidden"><div className="h-full w-[72%] bg-[var(--lime)]" /></div>
      <div className="mt-4 space-y-2">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-3 rounded-xl border border-[var(--line)] px-3 py-2.5 text-sm">
            <span className="text-[var(--muted)]">{k}</span><span className="font-semibold text-right">{v}</span>
          </div>
        ))}
      </div>
      <p className="text-xs text-[var(--muted)] mt-3">Each answer and practice run sharpens every read you get.</p>
    </div>
  );
}

function Types({ types, catalogState, retryCatalog }) {
  return (
    <div data-testid="showcase-types">
      {catalogState !== "ready" && <CatalogStatus state={catalogState} retry={retryCatalog} />}
      <div className="grid grid-cols-2 gap-2">
        {types.slice(0, 6).map((a) => (
          <Link key={a.slug} to={`/types/${a.slug}`} className="rounded-xl border border-[var(--line)] px-3 py-2.5 hover:bg-[var(--ink)] hover:text-[var(--bg)] transition-colors" data-testid={`showcase-type-${a.slug}`}>
            <p className="font-mono text-[10px] opacity-60">#{pad(a.number)}</p>
            <p className="font-display font-black uppercase text-base leading-tight truncate">{a.name}</p>
          </Link>
        ))}
      </div>
      <Link to="/types" className="inline-flex items-center gap-1.5 text-sm font-semibold mt-3 hover:text-[var(--lime)]" data-testid="showcase-browse-types">Browse all archetypes <ArrowRight className="w-4 h-4" /></Link>
    </div>
  );
}

export const SHOWCASE_TABS = TABS.map((t) => t.k);

export function Showcase({ types, catalogState = "ready", retryCatalog, start = 0, pinned = false, publicQuiz, autoRotate = true }) {
  const [i, setI] = useState(start);
  const [hold, setHold] = useState(false);
  const [locked, setLocked] = useState(pinned);
  useEffect(() => {
    if (hold || locked || !autoRotate) { return undefined; }
    const t = setInterval(() => setI((n) => (n + 1) % TABS.length), 6000);
    return () => clearInterval(t);
  }, [hold, locked, autoRotate]);
  const tab = TABS[i];
  return (
    <div className={pinned ? "w-full" : "card p-4 sm:p-5 lg:p-7 w-full max-w-xl lg:max-w-2xl lg:text-[1.05rem]"} onMouseEnter={() => setHold(true)} onMouseLeave={() => setHold(false)} data-testid="home-showcase">
      <div className={`gap-1.5 ${pinned ? "hidden" : "flex"}`} role="tablist">
        {TABS.map((t, n) => (
          <button key={t.k} role="tab" aria-selected={n === i} onClick={() => { setI(n); setLocked(true); }}
            className={`flex-1 inline-flex items-center justify-center gap-1.5 rounded-full px-3 py-2 text-xs sm:text-sm font-semibold transition-colors ${n === i ? "bg-[var(--ink)] text-[var(--bg)]" : "text-[var(--muted)] hover:text-[var(--ink)]"}`}
            data-testid={`showcase-tab-${t.k}`}>
            <t.icon className="w-4 h-4 hidden sm:block" />{t.label}
          </button>
        ))}
      </div>
      <p className={`font-display font-black uppercase text-lg sm:text-2xl leading-tight ${pinned ? "pr-10" : "mt-4"}`} data-testid="showcase-line">{tab.line}</p>
      <div key={tab.k} className="rise mt-3">
        {tab.k === "practice" && <Practice onPick={() => setLocked(true)} />}
        {tab.k === "quiz" && <Quiz types={types} catalogState={catalogState} retryCatalog={retryCatalog} published={publicQuiz} onPick={() => setLocked(true)} />}
        {tab.k === "personal" && <Personal />}
        {tab.k === "types" && <Types types={types} catalogState={catalogState} retryCatalog={retryCatalog} />}
      </div>
      <p className="text-[10px] text-[var(--muted)] mt-3">Fictional example. Your real reads are private.</p>
    </div>
  );
}
