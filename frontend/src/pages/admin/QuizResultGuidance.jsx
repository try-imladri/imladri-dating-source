import { MemberLoadState, useMemberResource } from "@/components/app/MemberResource";
import { readings, themes } from "@/components/app/personalPortraitModel";
import { reflectionPairs } from "@/components/app/IntimacyReflections";
import { intimacyTopics } from "@/components/app/intimacyTopics";

function WordingGroup({ title, count, children }) {
  return <details className="card p-4 sm:p-5">
    <summary className="cursor-pointer font-semibold">{title} ({count})</summary>
    <div className="mt-3 space-y-3 text-sm">{children}</div>
  </details>;
}

export default function QuizResultGuidance({ api }) {
  const { data, error, reload } = useMemberResource(api, "/admin/quiz/results");
  const sections = Object.values(intimacyTopics).flat();
  return <section className="space-y-3" aria-label="Quiz results guidance" data-testid="admin-result-guidance">
    <h3 className="font-display text-xl font-black uppercase">Results guidance</h3>
    <p className="text-sm text-[var(--muted)]">Read the wording used to build member results. Each section contains the app’s published text.</p>
    {data && !error ? <WordingGroup title="Four-letter personality results" count={data.types.length}>
      <dl className="grid gap-3 sm:grid-cols-2">{data.types.map(type => <div key={type.code}>
        <dt className="font-semibold">{type.code} · {type.name}</dt><dd>{type.line}</dd>
      </div>)}</dl>
    </WordingGroup> : <MemberLoadState label="personality results" error={error} retry={reload} />}
    <WordingGroup title="Everyday result statements" count={Object.keys(readings).length}>
      <p>These statements appear when the original question and selected answer match. Other answers appear with their saved wording.</p>
      {Object.entries(readings).map(([id, [question, answers]]) => <details className="border-t border-[var(--line)] pt-2" key={id}>
        <summary className="cursor-pointer font-semibold">{question}</summary>
        <dl className="mt-2 space-y-2">{Object.entries(answers).map(([answer, text]) =>
          <div key={answer}><dt className="font-semibold">{answer}</dt><dd>{text}</dd></div>)}</dl>
      </details>)}
    </WordingGroup>
    <WordingGroup title="Intimacy section follow-ups" count={sections.length}>
      <dl className="grid gap-3 sm:grid-cols-2">{sections.map(section => <div key={section.id}>
        <dt className="font-semibold">{section.title}</dt><dd>{section.followUp}</dd>
      </div>)}</dl>
    </WordingGroup>
    <WordingGroup title="Combined profile reflection prompts" count={themes.length}>
      <p>These prompts connect an everyday preference to an intimacy response when a member chooses to include their Personal answers.</p>
      <dl className="grid gap-3 sm:grid-cols-2">{themes.map(theme => <div key={theme.id}>
        <dt className="font-semibold">{theme.title}</dt><dd>{theme.followUp}</dd>
      </div>)}</dl>
    </WordingGroup>
    <WordingGroup title="Paired intimacy reflections" count={reflectionPairs.length}>
      <p>Each pair connects two original questions. Where answers differ, the summary describes the member’s chosen stance on each topic.</p>
      <dl className="grid gap-3 sm:grid-cols-2">{reflectionPairs.map(([[one, first], [two, second], together]) => <div key={one}>
        <dt className="font-semibold">{one} · {two}</dt><dd>{first} / {second}</dd>{together && <dd>{together}</dd>}
      </div>)}</dl>
    </WordingGroup>
  </section>;
}
