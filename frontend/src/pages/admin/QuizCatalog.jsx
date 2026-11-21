import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { choices, intimacyTopics } from "@/components/app/intimacyTopics";
import QuizResultGuidance from "./QuizResultGuidance";
import { errMsg } from "@/lib/api";
import { CARDS } from "@/lib/couplesCards";
import { reflections, followUp } from "@/lib/couplesQuestions";
import { CouplesStarters } from "./CouplesAnalytics";
import PublicQuizAdmin, { ProfileIntakeCatalog } from "./PublicQuizAdmin";
import { SubTabs } from "./SubTabs";
import { YourTypeCatalog } from "./YourTypeResearch";

const tracks = { talk: "Intimacy & communication", bdsm: "BDSM interests & boundaries" };
const categories = [["everyday", "Everyday personality"], ["intimacy", "Sex & intimacy"], ["couples", "Couples"], ["your_type", "Your Type"], ["demo", "Public demo"], ["intake", "Profile intake"], ["results", "Results guidance"]];
const field = "mt-1.5 block w-full rounded-xl border border-[var(--line)] bg-[var(--bg)] px-3 py-2 text-sm";
const action = "rounded-full bg-[var(--ink)] px-5 py-2 text-sm font-bold text-[var(--bg)] disabled:opacity-50";

function IntimacyQuestions({ api }) {
  const [track, setTrack] = useState("talk");
  const [sectionId, setSectionId] = useState("");
  const [published, setPublished] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [editorial, setEditorial] = useState({});
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState("");
  const [notice, setNotice] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const ctl = new AbortController();
    setProblem("");
    api.get("/admin/quiz/intimacy", { signal: ctl.signal }).then(({ data }) => {
      if (ctl.signal.aborted) return;
      const index = list => Object.fromEntries(list.map(item => [item.section + "/" + item.topic, item]));
      setPublished(index(data.overrides));
      setEditorial(index(data.drafts || []));
    }).catch(error => { if (!ctl.signal.aborted) setProblem(errMsg(error)); });
    return () => ctl.abort();
  }, [api, attempt]);

  const sections = intimacyTopics[track];
  const section = sections.find(item => item.id === sectionId) || sections[0];
  function edit(key, value) {
    setDrafts(current => ({ ...current, [key]: value }));
    setNotice("");
  }
  function clearDraft(key) {
    setDrafts(current => {
      const next = { ...current };
      delete next[key];
      return next;
    });
  }
  async function saveDraft(key, topic, draft) {
    setSaving(true); setProblem(""); setNotice("");
    try {
      const { data } = await api.put("/admin/quiz/intimacy/draft", {
        section: section.id, topic, question: draft.question, phrase: draft.phrase,
        revision: draft.revision, draft_revision: editorial[key]?.draft_revision || 0,
      });
      setEditorial(current => ({ ...current, [key]: data }));
      clearDraft(key);
      setNotice("Saved staff draft for " + topic + ".");
    } catch (error) { setProblem(errMsg(error)); }
    finally { setSaving(false); }
  }
  async function publish(event, key, topic, draft) {
    event.preventDefault();
    setSaving(true);
    setProblem("");
    setNotice("");
    try {
      const { data } = await api.put("/admin/quiz/intimacy", { section: section.id, topic, question: draft.question, phrase: draft.phrase, revision: draft.revision });
      setPublished(current => ({ ...current, [key]: data }));
      setEditorial(current => {
        const previous = current[key];
        if (previous?.revision !== draft.revision || previous.question !== draft.question || previous.phrase !== draft.phrase) return current;
        const next = { ...current };
        delete next[key];
        return next;
      });
      clearDraft(key);
      setNotice("Published updated wording for " + topic + ".");
    } catch (error) { setProblem(errMsg(error)); }
    finally { setSaving(false); }
  }
  return <section className="space-y-4" aria-label="Sex and intimacy questions" data-testid="admin-intimacy-questions">
    <SubTabs value={track} onChange={key => { setTrack(key); setSectionId(""); }} testid="admin-intimacy-track"
      items={Object.entries(tracks).map(([key, label]) => [key, label + " (" + intimacyTopics[key].reduce((n, s) => n + s.prompts.length, 0) + ")"])} />
    <p className="text-sm text-[var(--muted)]">Edit the questions adults see in Personal. Members can save answers as they go. Admin Research compares the choices of participating adults.</p>
    {problem && <p role="alert" className="text-sm">{problem} <button type="button" className="underline" onClick={() => { setDrafts({}); setAttempt(n => n + 1); }}>Reload saved and published wording</button></p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {published === null ? !problem && <p role="status">Loading questions…</p> : <>
      <label className="block max-w-xl text-sm font-semibold">Section
        <select className={field} value={section.id} onChange={e => { setSectionId(e.target.value); setNotice(""); }}>
          {sections.map(item => <option key={item.id} value={item.id}>{item.title} ({item.prompts.length})</option>)}
        </select>
      </label>
      <p className="text-sm"><strong>Section follow-up</strong> · {section.followUp}</p>
      <div className="card divide-y divide-[var(--line)]" key={section.id}>
        {section.prompts.map(([topic, question, phrase]) => {
          const key = section.id + "/" + topic;
          const old = published[key];
          const saved = { question: old?.question || question, phrase: old?.phrase || phrase, revision: old?.revision || 0 };
          const draft = drafts[key] || editorial[key] || saved;
          const privateCopy = editorial[key];
          const differsFromDraft = !privateCopy || draft.question !== privateCopy.question || draft.phrase !== privateCopy.phrase || draft.revision !== privateCopy.revision;
          const stale = draft.revision !== saved.revision;
          const dirty = draft.question !== saved.question || draft.phrase !== saved.phrase;
          return <details key={key} className="group px-4 py-3.5 sm:px-5">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-sm font-semibold">
              <span><span className="block mb-1 text-xs font-normal text-[var(--muted)]">{topic}</span>{saved.question}</span>
              <ChevronDown aria-hidden="true" className="h-4 w-4 shrink-0 group-open:rotate-180" />
            </summary>
            <form onSubmit={event => publish(event, key, topic, draft)} className="mt-4 max-w-3xl space-y-3">
              <label className="block text-sm">Question<textarea required maxLength={650} rows={2} className={field} disabled={saving}
                value={draft.question} onChange={e => edit(key, { ...draft, question: e.target.value })} /></label>
              <label className="block text-sm">First-person phrase for the results<textarea required maxLength={350} rows={2} className={field} disabled={saving}
                value={draft.phrase} onChange={e => edit(key, { ...draft, phrase: e.target.value })} /></label>
              <details className="rounded-xl border border-[var(--line)] p-3 text-sm">
                <summary className="cursor-pointer font-semibold">Preview member wording</summary>
                <p className="mt-2">{draft.question}</p><p>{choices.join(" · ")} · Skip</p>
                <p className="mt-2">Results phrase · {draft.phrase}</p>
              </details>
              {privateCopy && <p className="text-xs text-[var(--muted)]">Saved staff draft · Revision {privateCopy.draft_revision}</p>}
              {stale && <p className="text-sm" role="alert">This draft was based on older wording. <button type="button" className="underline" onClick={() => edit(key, { ...draft, revision: saved.revision })}>Rebase edits on current revision</button></p>}
              <div className="flex flex-wrap gap-3 items-center">
                <button className={action} disabled={saving || !dirty || stale || !draft.question.trim() || !draft.phrase.trim()}>Publish question</button>
                <button type="button" className="btn-ghost px-3 py-2 text-sm" disabled={saving || stale || !dirty || !differsFromDraft || !draft.question.trim() || !draft.phrase.trim()} onClick={() => saveDraft(key, topic, draft)}>Save staff draft</button>
                {drafts[key] && <button type="button" className="text-sm underline" disabled={saving} onClick={() => clearDraft(key)}>Discard changes</button>}
                {old?.previous && <button type="button" className="text-sm underline" disabled={saving}
                  onClick={() => edit(key, { ...old.previous, revision: saved.revision })}>Review previous wording</button>}
                {(old?.question || old?.phrase) && <button type="button" className="text-sm underline" disabled={saving} onClick={() => edit(key, { question, phrase, revision: saved.revision })}>Use original wording</button>}
              </div>
            </form>
          </details>;
        })}
      </div>
    </>}
  </section>;
}

function CouplesQuestions({ api }) {
  return <div className="space-y-4">
    <CouplesStarters api={api} />
    <section className="card space-y-3 p-4 sm:p-5" aria-label="Couples reflections and follow-up">
      <h2 className="font-display font-black uppercase text-xl">Reflections and follow-up</h2>
      {Object.entries(reflections).map(([key, item]) => <details key={key} className="border-t border-[var(--line)] pt-3 text-sm">
        <summary className="cursor-pointer font-semibold">{item.title}</summary>
        <ol className="mt-3 list-decimal space-y-1 pl-5">{item.questions.map(question => <li key={question}>{question}</li>)}</ol>
        <p className="mt-3"><strong>After both share</strong><br />{item.discussion}</p>
      </details>)}
      <details className="border-t border-[var(--line)] pt-3 text-sm">
        <summary className="cursor-pointer font-semibold">Agreements, check-ins and saved learnings</summary>
        <dl className="mt-3 space-y-3">
          <div><dt className="font-semibold">Agreement</dt><dd>{followUp.agreement}</dd><dd>{followUp.reviewDate}</dd></div>
          <div><dt className="font-semibold">Check-in</dt><dd>{followUp.checkin}</dd><dd>{Object.values(followUp.choices).join(" · ")}</dd><dd>{followUp.note}</dd></div>
          <div><dt className="font-semibold">What works for us</dt><dd>{followUp.learning}</dd></div>
        </dl>
      </details>
    </section>
    <section className="card space-y-3 p-4 sm:p-5">
      <h2 className="font-display font-black uppercase text-xl">Conversation cards</h2>
      <p className="text-sm text-[var(--muted)]">Each question opens a partner exercise. The sample answers and agreement below are optional examples.</p>
      <a className="inline-block text-sm underline" href="/admin/couples">View card participation in Couples</a>
      {CARDS.map(card => <details key={card.id} className="border-t border-[var(--line)] pt-3 text-sm">
        <summary className="cursor-pointer font-semibold">{card.name}</summary>
        <p className="mt-3 font-semibold">{card.question}</p>
        <ul className="list-disc pl-5">{card.perspectives.map(line => <li key={line}>{line}</li>)}</ul>
        <p className="mt-2"><strong>Agreement</strong> {card.agreement}</p><p><strong>Check-in</strong> {card.checkIn}</p>
      </details>)}
    </section>
  </div>;
}

export default function QuizCatalog({ api, children }) {
  const [tab, setTab] = useState("everyday");
  const [visited, setVisited] = useState({ everyday: true });
  function select(key) {
    setVisited(prev => ({ ...prev, [key]: true }));
    setTab(key);
  }
  return <div className="flex h-full min-h-0 flex-col gap-4" data-testid="admin-quiz">
    <SubTabs value={tab} onChange={select} items={categories} testid="admin-quiz-catalog" />
    <div role="region" aria-label={categories.find(([key]) => key === tab)[1] + " catalog"} tabIndex={0} className="min-h-0 flex-1 overflow-y-auto pb-4 focus-visible:outline-2 focus-visible:outline-offset-[-2px]">
      <div hidden={tab !== "everyday"} className="h-full">{children}</div>
      {visited.intimacy && <div hidden={tab !== "intimacy"}><IntimacyQuestions api={api} /></div>}
      {visited.couples && <div hidden={tab !== "couples"}><CouplesQuestions api={api} /></div>}
      {visited.your_type && <div hidden={tab !== "your_type"}><YourTypeCatalog api={api} /></div>}
      {visited.demo && <div hidden={tab !== "demo"}><PublicQuizAdmin api={api} /></div>}
      {visited.intake && <div hidden={tab !== "intake"}><ProfileIntakeCatalog api={api} /></div>}
      {visited.results && <div hidden={tab !== "results"}><QuizResultGuidance api={api} /></div>}
    </div>
  </div>;
}
