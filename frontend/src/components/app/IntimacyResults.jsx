import { useState } from "react";
import { IntimacyReflections } from "./IntimacyReflections";
import { PersonalPortrait, hasSavedPortrait } from "./PersonalPortrait";
import { conversationStarter, intimacyStatement, takeawayEntries } from "./intimacyLanguage";
import { choices } from "./intimacyTopics";

function AnswerGroups({ entries }) {
  const groups = choices.map((choice) => ({ choice, items: entries.filter((entry) => entry.choice === choice) }));
  return <dl className="space-y-3">
    {groups.filter(({ items }) => items.length > 0).map(({ choice, items }) => <div key={choice}>
      <dt className="text-sm font-semibold">{choice}</dt>
      <dd className="mt-1 space-y-1">
        {items.map(({ key, topic, prompt, conditions }) => <details key={key} className="rounded-xl border border-[var(--line)] px-3 py-2 text-sm">
          <summary className="cursor-pointer">{topic}{conditions.length > 0 && <span className="text-xs text-[var(--muted)]"> · {conditions.join(" · ")}</span>}</summary>
          <p className="mt-2 text-[var(--muted)]">{prompt}</p>
        </details>)}
      </dd>
    </div>)}
  </dl>;
}

function ConversationStarter({ summaries, entries, title = "A way to say it" }) {
  const [selected, setSelected] = useState("");
  const chosen = entries.find((entry) => entry.key === selected) || entries[0];
  if (!chosen) return null;
  return <div className="border-t border-[var(--line)] pt-4 space-y-2">
    <h3 className="font-semibold">{title}</h3>
    <blockquote aria-live="polite" className="border-l-2 border-[var(--ink)] pl-3 text-sm leading-relaxed">“{conversationStarter(chosen)}”</blockquote>
    {entries.length > 1 && <>
      <label className="block text-sm" htmlFor="intimacy-starter">Pick a topic to talk about</label>
      <select id="intimacy-starter" className="block w-full min-w-0 rounded-xl border border-[var(--line)] bg-[var(--bg)] p-2 text-sm"
        value={chosen.key} onChange={(event) => setSelected(event.target.value)}>
        {summaries.filter(({ entries: group }) => group.length > 0).map(({ section, entries: group }) => (
          <optgroup key={section.id} label={section.title}>
            {group.map((entry) => <option key={entry.key} value={entry.key}>{entry.topic} · {entry.choice}</option>)}
          </optgroup>
        ))}
      </select>
    </>}
  </div>;
}

function LimitsAndConditions({ entries }) {
  const groups = [
    { choice: "Off limits", label: "Firm limits" },
    { choice: "It depends", label: "Conditional answers" },
  ].map(group => ({ ...group, rows: entries.filter(entry => entry.choice === group.choice) }))
    .filter(group => group.rows.length);
  if (!groups.length) return null;
  return <section className="space-y-2" aria-label="Your limits and conditions">
    {groups.map(({ choice, label, rows }) => <details key={choice} className="rounded-xl border border-[var(--line)] p-3">
      <summary className="cursor-pointer text-sm font-semibold">{label} ({rows.length})</summary>
      <ul className="mt-2 space-y-2">{rows.map(entry => <li key={entry.key} className="text-sm">
        <span className="block text-xs text-[var(--muted)]">{entry.sectionTitle} · {entry.topic}</span>
        {intimacyStatement(entry)}
      </li>)}</ul>
    </details>)}
  </section>;
}

export function IntimacyResults({ sections, answers, combined, profile, includeProfile, onToggleProfile, onReview }) {
  const visited = sections.filter((section) => answers[section.id]?.length);
  const summaries = visited.map((section) => {
    const responses = answers[section.id];
    const entries = responses.flatMap((answer, i) => answer ? [{
      key: section.id + "/" + i, sectionId: section.id, sectionTitle: section.title,
      topic: section.prompts[i][0], prompt: section.prompts[i][1], phrase: section.prompts[i][2],
      editedQuestion: section.editedTopics?.includes(section.prompts[i][0]),
      ...answer, followUp: section.followUp,
    }] : []);
    return { section, entries, seen: Object.keys(responses).length, skipped: responses.filter(answer => answer === null).length };
  });
  const entries = summaries.flatMap((summary) => summary.entries);
  const totalSkipped = summaries.reduce((sum, group) => sum + group.skipped, 0);
  const starter = <ConversationStarter summaries={summaries} entries={entries} title={combined && entries.length === 1 ? "In your own words" : "A way to say it"} />;
  const finished = summaries.filter(({ section, seen }) => seen === section.prompts.length).length;
  return <>
    {combined ? <>
      {entries.length > 1 && <div>
        <h3 className="font-semibold">In your own words</h3>
        <p className="mt-1 text-base leading-relaxed">{takeawayEntries(entries).map(intimacyStatement).join(" ")}</p>
      </div>}
      {entries.length === 1 && starter}
      {entries.length === 0 && <p className="text-sm">Choose any question to start your summary. You can return to the ones you skipped.</p>}
      {entries.length > 1 && <LimitsAndConditions entries={entries} />}
      {entries.length > 0 && hasSavedPortrait(profile) && <div className="space-y-3 rounded-xl border border-[var(--line)] p-4">
        <button type="button" aria-expanded={includeProfile} className="btn-ghost px-4 py-2 text-sm" onClick={onToggleProfile}>
          {includeProfile ? "Hide my Personal answers" : "Include my Personal answers"}
        </button>
        {includeProfile ? <PersonalPortrait profile={profile} entries={entries} /> :
          <p className="text-xs text-[var(--muted)]">See how your everyday and intimate preferences fit together. This combined view stays on your device.</p>}
      </div>}
      <IntimacyReflections entries={entries.filter(entry => !entry.editedQuestion)} />
      {entries.length > 1 && starter}
      <div className="space-y-2">
        <h3 className="font-semibold">Your answers by section</h3>
        <p className="text-sm text-[var(--muted)]">{finished === sections.length
          ? `You finished all ${sections.length} sections.` : `${finished} of ${sections.length} sections finished`} · {entries.length} answered · {totalSkipped} skipped</p>
        {summaries.map(({ section, entries: group, seen }) => (
          <details key={section.id} className="rounded-xl border border-[var(--line)] p-3">
            <summary className="cursor-pointer text-sm font-semibold">{section.title}
              <span className="ml-2 font-mono text-xs text-[var(--muted)]">{seen} / {section.prompts.length}</span>
            </summary>
            <div className="mt-3 space-y-3">
              {group.length ? <AnswerGroups entries={group} /> : <p className="text-sm text-[var(--muted)]">You skipped the questions you visited here.</p>}
              <button type="button" className="text-sm underline underline-offset-4" onClick={() => onReview(section)}>Review answers</button>
            </div>
          </details>
        ))}
      </div>
    </> : <>
      {starter}
      <p className="text-sm text-[var(--muted)]">{entries.length} answered · {totalSkipped} skipped</p>
      {entries.length ? <AnswerGroups entries={entries} /> : <p className="text-sm">You skipped every question in this section. You can revisit any of them.</p>}
    </>}
  </>;
}
