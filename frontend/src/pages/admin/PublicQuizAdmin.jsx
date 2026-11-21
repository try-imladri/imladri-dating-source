import { useState } from "react";
import { errMsg } from "@/lib/api";
import { defaultPublicQuiz } from "@/lib/publicQuiz";
import { MemberLoadState, useMemberResource } from "@/components/app/MemberResource";

const field = "mt-1 block w-full rounded-xl border border-[var(--line)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--ink)]";
const label = "block text-sm font-semibold";
const replaceAt = (items, index, value) => items.map((item, i) => i === index ? value : item);

function Editor({ api, data, reload }) {
  const [published, setPublished] = useState(data.quiz || defaultPublicQuiz);
  const [revision, setRevision] = useState(data.revision);
  const [draft, setDraft] = useState(published);
  const [canRestore, setCanRestore] = useState(!!data.can_restore);
  const [restoring, setRestoring] = useState(false);
  const [first, setFirst] = useState(0);
  const [second, setSecond] = useState(0);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const dirty = JSON.stringify(draft) !== JSON.stringify(published);

  function update(value) {
    setNotice("");
    if (!conflict) setError("");
    setDraft(value);
  }
  function questionAt(i, patch) {
    update({ ...draft, questions: replaceAt(draft.questions, i, { ...draft.questions[i], ...patch }) });
  }
  function outcomeAt(i, slug) {
    const group = draft.outcomes[first];
    const row = replaceAt(group[second], i, slug);
    update({ ...draft, outcomes: replaceAt(draft.outcomes, first, replaceAt(group, second, row)) });
  }
  async function publish(event, restore = false) {
    event.preventDefault();
    setBusy(true); setNotice(""); setError("");
    try {
      const { data: saved } = await api.put("/admin/public-quiz", restore ? { revision, restore_previous: true } : { revision, quiz: draft });
      setDraft(saved.quiz || defaultPublicQuiz); setPublished(saved.quiz || defaultPublicQuiz); setRevision(saved.revision);
      setCanRestore(!!saved.can_restore); setRestoring(false);
      setNotice(restore ? "Previous homepage quiz restored." : "Homepage quiz published.");
    } catch (failure) {
      setError(errMsg(failure));
      setConflict(failure.response?.status === 409);
    } finally { setBusy(false); }
  }
  return <form className="space-y-4" onSubmit={publish} data-testid="admin-public-quiz">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h3 className="font-display text-xl font-black uppercase">Homepage three-tap quiz</h3>
      <button className="btn-lime px-4 py-2 text-sm disabled:opacity-50" disabled={!dirty || busy || conflict}>{busy ? "Publishing…" : "Publish homepage quiz"}</button>
    </div>
    {canRestore && <div className="text-sm">{restoring ? <><span>Replace the current homepage quiz with its previous version? </span>
      <button type="button" className="underline mr-3" disabled={busy || conflict} onClick={e => publish(e, true)}>Confirm restore</button>
      <button type="button" className="underline" onClick={() => setRestoring(false)}>Cancel</button></> :
      <button type="button" disabled={busy || conflict} className="underline" onClick={() => setRestoring(true)}>Restore previous homepage quiz</button>}</div>}
    {error && <div role="alert" className="text-sm">{error} {conflict && <button type="button" className="underline" onClick={reload}>Reload published version</button>}</div>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    <fieldset disabled={busy} className="grid min-w-0 gap-3 md:grid-cols-3">
      <legend className="sr-only">Homepage questions and choices</legend>
      {draft.questions.map((q, i) => <div key={i} className="card min-w-0 space-y-3 p-4">
        <label className={label}>Question {i + 1}
          <input className={field} value={q.q} maxLength={175} required onChange={e => questionAt(i, { q: e.target.value })} />
        </label>
        {q.a.map((choice, j) => <label key={j} className={label}>Choice {j + 1}<span className="sr-only"> for question {i + 1}</span>
          <input className={field} value={choice} maxLength={85} required onChange={e => questionAt(i, { a: replaceAt(q.a, j, e.target.value) })} />
        </label>)}
      </div>)}
    </fieldset>
    <fieldset disabled={busy} className="card min-w-0 space-y-4 p-4 sm:p-5">
      <legend className="sr-only">Match quiz answers to archetypes</legend>
      <h4 className="font-display text-lg font-black uppercase">Result by answer</h4>
      <div className="grid gap-3 sm:grid-cols-2">
        {[first, second].map((selected, i) => <label key={i} className={label}>{draft.questions[i].q}
          <select className={field} value={selected} onChange={e => (i === 0 ? setFirst : setSecond)(Number(e.target.value))}>
            {draft.questions[i].a.map((a, j) => <option key={j} value={j}>{a}</option>)}
          </select>
        </label>)}
      </div>
      <p className="text-sm text-[var(--muted)]">For these first two answers, choose the result for each final choice. Visitors can open published archetypes.</p>
      <div className="grid gap-3 md:grid-cols-3">{draft.questions[2].a.map((answer, i) => <label key={i} className={label}>{answer}
        <select className={field} value={draft.outcomes[first][second][i]} onChange={e => outcomeAt(i, e.target.value)}>
          {data.types.map(t => <option key={t.slug} value={t.slug}>{t.name}{t.unlocked ? "" : " (locked)"}</option>)}
        </select>
      </label>)}</div>
    </fieldset>
  </form>;
}

export default function PublicQuizAdmin({ api }) {
  const { data, error, reload } = useMemberResource(api, "/admin/public-quiz");
  if (!data || error) return <MemberLoadState label="the homepage quiz" error={error} retry={reload} />;
  return <Editor key={data.revision || "default"} api={api} data={data} reload={reload} />;
}

export function ProfileIntakeCatalog({ api }) {
  const { data, error, reload } = useMemberResource(api, "/intake-schema");
  if (!data || error) return <MemberLoadState label="profile intake questions" error={error} retry={reload} />;
  return <section className="space-y-3" data-testid="admin-profile-intake">
    <h3 className="font-display text-xl font-black uppercase">Profile intake</h3>
    <p className="text-sm text-[var(--muted)]">The prompts members see when filling out a person’s profile. These fields are defined by the app.</p>
    <section className="card space-y-1 p-4">
      <h4 className="font-display font-black uppercase">Member’s own questionnaire profile</h4>
      <p className="text-sm">Age, height in centimeters, gender, sex, ethnicity and education. Each partner in Couples can edit their own profile.</p>
    </section>
    <div className="grid gap-3 md:grid-cols-2">{data.map(group => <section key={group.key} className="card p-4">
      <h4 className="font-display font-black uppercase">{group.title}</h4>
      <ul className="mt-2 divide-y divide-[var(--line)]">{group.fields.map(input => <li key={input.key} className="py-2 text-sm">
        <p className="font-semibold">{input.label}</p>
        <p className="mt-1 text-xs text-[var(--muted)]">{input.hint}</p>
      </li>)}</ul>
    </section>)}</div>
  </section>;
}
