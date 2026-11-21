import { useEffect, useState } from "react";
import { X, ChevronLeft, ChevronRight } from "lucide-react";
import { Input } from "@/components/ui/input";

function QuestionCard({ q, i, a, onChange }) {
  return (
    <div className={`card min-w-0 max-w-full p-3 flex flex-col transition-colors ${a ? "border-[var(--ink)]" : ""}`} id={`quiz-question-${q.id}`} tabIndex={-1} role="group" aria-labelledby={`quiz-label-${q.id}`} data-testid={`quiz-q-${q.id}`}>
      <div className="flex min-w-0 items-start justify-between gap-2 mb-2">
        <p id={`quiz-label-${q.id}`} className="min-w-0 flex-1 text-[13px] font-semibold leading-snug min-h-[2.4em] [overflow-wrap:anywhere]"><span className="font-mono text-[10px] text-[var(--muted)] mr-1.5">{String(i + 1).padStart(2, "0")}</span>{q.q}</p>
        {a && <button onClick={() => onChange(q.id, null)} className="text-[var(--muted)] hover:text-[var(--crimson)] shrink-0" aria-label={`Remove answer to ${q.q}`} data-testid={`quiz-clear-${q.id}`}><X className="w-3.5 h-3.5" /></button>}
      </div>
      <div className="grid min-w-0 grid-cols-2 auto-rows-fr gap-1.5 flex-1">
        {q.options.map((o, k) => (
          <button key={k} type="button" onClick={() => onChange(q.id, { choice: k, question: q.q, label: o.label, note: a?.note || "" })} data-testid={`quiz-opt-${q.id}-${k}`}
            aria-pressed={a?.choice === k} title={o.label} className={`flex min-w-0 max-w-full items-center whitespace-normal text-left text-xs leading-snug px-2 py-1.5 rounded-lg border transition-colors ${a?.choice === k ? "bg-[var(--ink)] text-[var(--bg)] border-[var(--ink)]" : "border-[var(--line)] hover:border-[var(--ink)]"}`}><span className="min-w-0 w-full [overflow-wrap:anywhere]">{o.label}</span></button>
        ))}
      </div>
      {q.note && a && (
        <Input className="mt-1.5 h-7 min-w-0 max-w-full text-xs" maxLength={500} aria-label={`Optional note for ${q.q}`} value={a.note || ""} onChange={(e) => onChange(q.id, { ...a, note: e.target.value })} placeholder="Tell me more (optional)" data-testid={`quiz-note-${q.id}`} />
      )}
    </div>
  );
}

export function QuizForm({ questions, answers, onChange, pageSize = 12, showTopics = false }) {
  const firstOpen = questions.findIndex(q => !answers[q.id]);
  const resumePage = firstOpen < 0 ? 0 : Math.floor(firstOpen / pageSize);
  const [page, setPage] = useState(resumePage);
  const [topic, setTopic] = useState("");
  const topics = [...new Set(questions.map(q => q.axis || "Other"))];
  const included = topic ? questions.filter(q => (q.axis || "Other") === topic) : questions;
  const [focusId, setFocusId] = useState(null);
  useEffect(() => {
    if (focusId) {
      document.getElementById("quiz-question-" + focusId)?.focus();
      setFocusId(null);
    }
  }, [page, focusId]);
  const pages = Math.ceil(included.length / pageSize);
  const slice = included.slice(page * pageSize, (page + 1) * pageSize);
  return (
    <div className="min-w-0 max-w-full flex-1 flex flex-col" data-testid="quiz-form">
      {showTopics && <nav aria-label="Questionnaire topics" className="mb-3">
        <p className="text-sm font-semibold mb-1">Explore by topic</p>
        <div className="flex flex-wrap gap-2">{["", ...topics].map(name => {
          const rows = name ? questions.filter(q => (q.axis || "Other") === name) : questions;
          return <button type="button" key={name} aria-pressed={name === topic} className={`max-w-full whitespace-normal text-left [overflow-wrap:anywhere] ${name === topic ? "btn-lime px-3 py-1 text-xs" : "btn-ghost px-3 py-1 text-xs"}`}
            onClick={() => { setTopic(name); const next = rows.findIndex(q => !answers[q.id]); setPage(Math.max(0, Math.floor(next / pageSize))); }}>
            {name || "All topics"} · {rows.filter(q => answers[q.id]).length}/{rows.length}
          </button>;
        })}</div>
      </nav>}
      {firstOpen >= 0 && questions.some(q => answers[q.id]) && <button type="button" className="self-start text-xs underline mb-2"
        onClick={() => { setTopic(""); setPage(resumePage); setFocusId(questions[firstOpen].id); }}>Resume at question {firstOpen + 1}</button>}
      <div className="grid min-w-0 grid-cols-[repeat(auto-fit,minmax(min(100%,17rem),1fr))] gap-2.5 flex-1" data-testid="quiz-question-grid">
        {slice.map(q => <QuestionCard key={q.id} q={q} i={questions.indexOf(q)} a={answers[q.id]} onChange={onChange} />)}
      </div>
      {pages > 1 && (
        <div className="flex flex-wrap items-center justify-end gap-3 mt-2.5" data-testid="quiz-pager">
          <button aria-label="Previous question page" disabled={!page} onClick={() => setPage(page - 1)} className="w-8 h-8 rounded-full border border-[var(--line)] flex items-center justify-center disabled:opacity-30" data-testid="quiz-prev"><ChevronLeft className="w-4 h-4" /></button>
          <span className="font-mono text-xs" data-testid="quiz-page">{page + 1} / {pages}</span>
          <button aria-label="Next question page" disabled={page >= pages - 1} onClick={() => setPage(page + 1)} className="w-8 h-8 rounded-full border border-[var(--line)] flex items-center justify-center disabled:opacity-30" data-testid="quiz-next"><ChevronRight className="w-4 h-4" /></button>
        </div>
      )}
    </div>
  );
}

export function answerDiff(before, after) {
  const out = {};
  new Set([...Object.keys(before), ...Object.keys(after)]).forEach((k) => {
    if (JSON.stringify(before[k] || null) !== JSON.stringify(after[k] || null)) out[k] = after[k] || null;
  });
  return out;
}

export function mergeChanges(saved, changes) {
  const next = { ...saved };
  for (const [key, value] of Object.entries(changes)) {
    if (value == null) delete next[key];
    else next[key] = value;
  }
  return next;
}

// An administrator can publish a new ordering between the quiz GET and the
// profile GET, or while the form is open. Render the literal saved selection.
export function answersForQuiz(about, quiz) {
  const questions = new Map(["short", "extended", "further"].flatMap((set) => quiz[set] || []).map((q) => [q.id, q]));
  const active = {};
  const previous = { ...about.previous_answers };
  for (const [id, answer] of Object.entries(about.answers || {})) {
    const q = questions.get(id);
    if (!answer) continue;
    const index = answer.label === undefined ? answer.choice : q?.options.findIndex((option) => option.label === answer.label);
    if (q && (answer.question === undefined || q.q === answer.question) && Number.isInteger(index) && q.options[index]) {
      active[id] = { ...answer, choice: index, question: q.q, label: q.options[index].label };
    } else {
      previous[id] = answer;
    }
  }
  return { active, previous };
}

export function personalQuestionnaireComplete(quiz, answers) {
  const questions = ["short", "extended", "further"].flatMap(set => quiz[set] || []);
  const { active } = answersForQuiz({ answers }, quiz);
  return questions.length > 0 && questions.every(question => active[question.id]);
}
