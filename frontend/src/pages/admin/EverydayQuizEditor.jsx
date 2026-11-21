import { sectionNames as labels } from "@/lib/personalQuestionnaires";
import { useEffect, useState } from "react";
import { errMsg } from "@/lib/api";
import { SubTabs } from "./SubTabs";

const keys = Object.keys(labels);
const field = "mt-1.5 block w-full min-w-0 rounded-xl border border-[var(--line)] bg-[var(--bg)] px-3 py-2 text-sm";
const toText = quiz => Object.fromEntries(keys.map(key => [key, JSON.stringify(quiz[key], null, 2)]));
function parse(text) {
  try {
    const rows = JSON.parse(text);
    if (Array.isArray(rows) && rows.every(row =>
      row && typeof row.q === "string" && Array.isArray(row.options) && row.options.length >= 2 &&
      row.options.every(option => option && typeof option.label === "string"))) return rows;
  } catch { /* The advanced editor can temporarily contain incomplete JSON. */ }
  return null;
}

export default function EverydayQuizEditor({ api, settings, save, saving, reload }) {
  const [text, setText] = useState(() => toText(settings.quiz));
  const [section, setSection] = useState("short");
  const [selected, setSelected] = useState({});
  const [advanced, setAdvanced] = useState(false);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [draftRevision, setDraftRevision] = useState(settings.everyday_draft_revision ?? null);
  const [baseRevision, setBaseRevision] = useState(settings.quiz_revision ?? null);
  const [loadingDraft, setLoadingDraft] = useState(!!api);
  const [notice, setNotice] = useState("");
  const [testChoices, setTestChoices] = useState({});
  const [preview, setPreview] = useState(null);
  const [restore, setRestore] = useState(false);
  useEffect(() => {
    if (!api) return;
    const controller = new AbortController();
    api.get("/admin/quiz/everyday/draft", { signal: controller.signal }).then(({ data }) => {
      if (controller.signal.aborted) return;
      setDraftRevision(data.revision);
      if (data.draft) {
        setText(toText(data.draft.sets));
        setNotice("Loaded the saved staff draft.");
      }
      if (data.published_revision !== baseRevision || (data.draft && data.draft.base_revision !== data.published_revision)) {
        setConflict(true); setError("The publication changed. Review the current version before editing.");
      }
    }).catch(failure => { if (!controller.signal.aborted) { setError(errMsg(failure)); setConflict(true); } })
      .finally(() => { if (!controller.signal.aborted) setLoadingDraft(false); });
    return () => controller.abort();
  }, [api]); // eslint-disable-line react-hooks/exhaustive-deps -- only hydrate on entry; preserve subsequent local edits
  const rows = parse(text[section]);
  const index = Math.min(selected[section] || 0, Math.max(0, (rows?.length || 0) - 1));
  const question = rows?.[index];
  const updateText = value => {
    setText(current => ({ ...current, [section]: value }));
    setPreview(null); setNotice("");
    if (!conflict) setError("");
  };
  const change = patch => updateText(JSON.stringify(rows.map((row, i) => i === index ? { ...row, ...patch } : row), null, 2));
  function parsedDraft() {
    const quiz = {};
    for (const key of keys) {
      quiz[key] = parse(text[key]);
      if (!quiz[key]) {
        setSection(key);
        setAdvanced(true);
        setError("Check " + labels[key] + ". Enter a JSON list of questions with at least two labeled choices each.");
        return;
      }
      const invalid = quiz[key].findIndex(q => !q.q.trim() || q.options.some(option => !option.label.trim()));
      if (invalid !== -1) {
        setSection(key);
        setSelected(values => ({ ...values, [key]: invalid }));
        setError(labels[key] + ", question " + (invalid + 1) + " needs wording for the question and every choice.");
        return;
      }
    }
    return quiz;
  }
  async function perform(action) {
    const quiz = action === "restore" ? {} : parsedDraft();
    if (!quiz) return;
    setReloading(true);
    setNotice(""); setError("");
    try {
      if (action === "draft") {
        const { data } = await api.put("/admin/quiz/everyday/draft", { quiz, quiz_revision: baseRevision, revision: draftRevision });
        setDraftRevision(data.revision);
        setNotice("Staff draft saved. Members still see the published quiz.");
      } else if (action === "preview") {
        const { data } = await api.post("/admin/quiz/everyday/preview", { quiz, choices: testChoices });
        setPreview(data);
      } else {
        const result = await save({ quiz_revision: baseRevision, ...(api ? { everyday_draft_revision: draftRevision } : {}),
          ...(action === "restore" ? { restore_previous_quiz: true } : { quiz }) });
        if (!result.data) { setError(result.error || "Publication failed."); setConflict(result.status === 409); return; }
        setText(toText(result.data.quiz)); setBaseRevision(result.data.quiz_revision);
        setDraftRevision(result.data.everyday_draft_revision ?? null);
        setPreview(null); setTestChoices({}); setRestore(false);
        setNotice(action === "restore" ? "Previous quiz restored as a new publication." : "Quiz published.");
      }
    } catch (failure) {
      setError(errMsg(failure)); setConflict(failure.response?.status === 409);
    } finally { setReloading(false); }
  }
  async function loadLatest() {
    setReloading(true);
    try {
      const latest = await reload();
      const workspace = api && (await api.get("/admin/quiz/everyday/draft")).data;
      if (workspace && workspace.published_revision !== (latest.quiz_revision ?? null)) throw Error("Publication changed while loading");
      setBaseRevision(latest.quiz_revision ?? null); setDraftRevision(workspace?.revision ?? null);
      const compatible = workspace?.draft && workspace.draft.base_revision === workspace.published_revision;
      setText(toText(compatible ? workspace.draft.sets : latest.quiz));
      setPreview(null); setTestChoices({}); setConflict(false); setError(""); setNotice("Loaded current version.");
    } catch { setError("Couldn’t load the current quiz. Your draft is still here."); }
    finally { setReloading(false); }
  }
  const locked = saving || reloading || loadingDraft;
  const setOption = (i, patch) => change({ options: question.options.map((option, j) => j === i ? { ...option, ...patch } : option) });
  return <section aria-label="Everyday personality questions" className="space-y-4">
    <SubTabs value={section} onChange={setSection} items={keys.map(key => [key, labels[key] + " (" + (parse(text[key])?.length ?? settings.quiz[key].length) + ")"])} testid="admin-quiz-sets" />
    <div className="card space-y-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-display text-xl font-black uppercase">{labels[section]}</h2>
        {api && !advanced && Array.isArray(rows) && <button type="button" className="btn-ghost px-4 py-2 text-sm" disabled={locked}
          onClick={() => { const row = { id: "q-" + crypto.randomUUID(), q: "", axis: settings.quiz.axes[0], options: [{ label: "", tag: "" }, { label: "", tag: "" }] };
            updateText(JSON.stringify([...rows, row], null, 2)); setSelected(prev => ({ ...prev, [section]: rows.length })); }}>Add question</button>}
        <button type="button" aria-pressed={advanced} data-testid="admin-quiz-advanced"
          className="btn-ghost px-4 py-2 text-sm" onClick={() => setAdvanced(v => !v)}>{advanced ? "Question editor" : "Advanced JSON"}</button>
      </div>
      {advanced ? <>
        <label className="block text-sm font-semibold">Question JSON
          <textarea spellCheck={false} className={field + " min-h-60 font-mono"} rows={16} value={text[section]}
            disabled={locked} onChange={e => updateText(e.target.value)} data-testid={`admin-quiz-${section}`} />
        </label>
        <p className="text-xs text-[var(--muted)]">Keep each question’s ID, axis and option tags. Axis scores of 1–4 run from lower to higher on the named axis. A type score of 4 favors E, N, F or J in its dimension, and 1 favors the opposite. Set <code>note</code> to true to allow notes.</p>
      </> : question ? <>
        <label className="block text-sm font-semibold">Choose a question
          <select className={field} value={index} onChange={e => setSelected(prev => ({ ...prev, [section]: Number(e.target.value) }))}>
            {rows.map((row, i) => <option key={i} value={i}>{i + 1}. {row.q}</option>)}
          </select>
        </label>
        <label className="block text-sm font-semibold">Question wording
          <textarea rows={2} className={field} data-testid="admin-everyday-question" disabled={locked}
            value={question.q} onChange={e => change({ q: e.target.value })} />
        </label>
        {api && <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-sm">Relationship axis<select className={field} value={question.axis} disabled={locked}
            onChange={e => change({ axis: e.target.value })}>{(settings.quiz.axes || []).map(axis => <option key={axis}>{axis}</option>)}</select></label>
          <label className="text-sm">Type dimension<select className={field} value={question.dim || ""} disabled={locked}
            onChange={e => { const dim = e.target.value; const next = { ...question }; if (dim) next.dim = dim; else {
              delete next.dim; next.options = next.options.map(option => { const copy = { ...option }; delete copy.type_score; return copy; });
            } updateText(JSON.stringify(rows.map((row, i) => i === index ? next : row), null, 2)); }}>
            <option value="">Unscored</option>{["EI", "SN", "TF", "JP"].map(dim => <option key={dim}>{dim}</option>)}</select></label>
        </div>}
        <fieldset className="space-y-3">
          <legend className="text-sm font-semibold">Answer choices</legend>
          {question.options.map((option, i) => <div key={i} className="space-y-2">
            <label className="flex items-center gap-3 text-sm"><span className="shrink-0">Choice {i + 1}</span>
              <input className={field + " mt-0"} disabled={locked} value={option.label} onChange={e => setOption(i, { label: e.target.value })} /></label>
            {api && <div className="grid gap-2 sm:grid-cols-3">
              <label className="text-xs">Choice tag<input className={field} value={option.tag || ""} disabled={locked} onChange={e => setOption(i, { tag: e.target.value })} /></label>
              {["axis_score", "type_score"].map(key => <label key={key} className="text-xs">{key === "axis_score" ? "Axis score" : "Type score"}
                <select className={field} value={option[key] ?? ""} disabled={locked || (key === "type_score" && !question.dim)}
                  onChange={e => setOption(i, { [key]: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">Unscored</option>{[1, 2, 3, 4].map(n => <option key={n}>{n}</option>)}</select></label>)}
            </div>}
          </div>)}
        </fieldset>
        {api && <div className="flex flex-wrap gap-3 text-sm">
          <button type="button" className="underline" disabled={locked || question.options.length >= 32} onClick={() => change({ options: [...question.options, { label: "", tag: "" }] })}>Add answer choice</button>
          <button type="button" className="underline" disabled={locked || question.options.length <= 2} onClick={() => change({ options: question.options.slice(0, -1) })}>Remove last choice</button>
          <button type="button" className="underline" disabled={locked} onClick={() => {
            updateText(JSON.stringify(rows.filter((_, i) => i !== index), null, 2));
            setTestChoices(prev => { const next = { ...prev }; delete next[question.id]; return next; });
          }}>Remove question from draft</button></div>}
      </> : rows?.length === 0 ? <p className="text-sm">This section has no questions.</p> : <p role="alert" className="text-sm">Open Advanced JSON to finish correcting this section.</p>}
      {api && question && <section aria-label="Preview questionnaire scores" className="space-y-2 rounded-xl border border-[var(--line)] p-3">
        <label className="block text-sm">Sample answer for this question<select className={field} value={testChoices[question.id] ?? ""}
          onChange={e => { const choices = { ...testChoices }; if (e.target.value === "") delete choices[question.id]; else choices[question.id] = Number(e.target.value);
            setTestChoices(choices); setPreview(null); }}><option value="">No answer</option>{question.options.map((option, i) => <option value={i} key={i}>{option.label}</option>)}</select></label>
        <button type="button" className="btn-ghost px-4 py-2 text-sm" disabled={locked} onClick={() => perform("preview")}>Preview sample scores</button>
        {preview && <div className="text-sm" data-testid="everyday-score-preview"><p>{preview.answered} sample answers · Suggested type {preview.type.guess || "insufficient evidence"}</p>
          {preview.axes.filter(axis => axis.answered).map(axis => <p key={axis.axis}>{axis.axis} · {axis.score}/100 ({axis.answered} scored)</p>)}</div>}
      </section>}
      {notice && <p role="status" className="text-sm">{notice}</p>}
      {api && <p className="text-xs text-[var(--muted)]">Question wording, choices and scoring changes create a new item version. Earlier answers retain their original meaning. Reordering alone preserves the existing item.</p>}
      {error && <p role="alert" className="text-sm">{error} {conflict && <button type="button" className="underline" disabled={reloading} onClick={loadLatest}>Discard draft and load published quiz</button>}</p>}
      {api && <button type="button" disabled={locked || conflict} onClick={() => perform("draft")} className="btn-ghost px-4 py-2 text-sm mr-2">Save staff draft</button>}
      <button type="button" disabled={locked || conflict} onClick={() => perform("publish")} className="btn-lime px-5 py-2 text-sm" data-testid="admin-quiz-save">{saving ? "Saving…" : api ? "Publish quiz" : "Save quiz"}</button>
      {api && settings.quiz_can_restore && <div className="text-sm">
        {restore ? <><p>Restore the previous publication for members and replace this draft?</p>
          <button type="button" className="underline mr-3" disabled={locked || conflict} onClick={() => perform("restore")}>Confirm restore</button>
          <button type="button" className="underline" onClick={() => setRestore(false)}>Cancel</button></> :
          <button type="button" className="underline" disabled={locked || conflict} onClick={() => setRestore(true)}>Restore previous publication</button>}
      </div>}
    </div>
  </section>;
}
