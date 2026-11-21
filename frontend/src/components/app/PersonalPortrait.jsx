import { ArrowRight } from "lucide-react";
import { portraitConnections } from "./personalPortraitModel";
import { intimacyStatement } from "./intimacyLanguage";

export function hasSavedPortrait(profile) {
  return Boolean(profile && (profile.count || profile.type || profile.description));
}

function EverydayAnswer({ item }) {
  return <div className="space-y-1">
    <p className="text-xs text-[var(--muted)]">{item.question}</p>
    <p className="text-sm">{item.answer}</p>
  </div>;
}

export function PersonalPortrait({ profile, entries, onStartPersonal, onExploreIntimacy }) {
  const combined = Boolean(entries);
  const Heading = combined ? "h3" : "h2";
  const Subheading = combined ? "h4" : "h3";
  const connections = combined ? portraitConnections(profile, entries) : [];
  const highlights = profile.groups.map(({ items }) => items.find((item) => item.statement) || items[0]).filter(Boolean).slice(0, 3);

  return <section aria-label="Your personal portrait" className={combined ? "space-y-4" : "card p-5 sm:p-6 space-y-4"} data-testid="personal-portrait">
    <header>
      <Heading className="font-display font-black uppercase text-2xl sm:text-3xl leading-tight">Your personal portrait</Heading>
      {hasSavedPortrait(profile) && <p className="mt-1 text-sm text-[var(--muted)]">{combined
        ? "Your saved Personal answers alongside the intimacy answers in this summary."
        : "How you describe yourself in your saved Personal answers."}</p>}
    </header>
    {!hasSavedPortrait(profile) && <div className="space-y-3">
      <p className="text-sm">Save an answer in The basics to start your portrait.</p>
      {onStartPersonal && <button type="button" className="btn-lime px-4 py-2 text-sm" onClick={onStartPersonal}>Go to The basics</button>}
    </div>}
    <details className="text-sm"><summary className="cursor-pointer font-semibold">Who can see these answers?</summary>
      <p className="mt-2">Your portrait is private to your account. Joining Couples does not share it. You choose separately what to share with your partner.</p>
      <p className="mt-1">Questionnaire research uses eligible saved choices and optional demographic fields when you opt in. Private free-text notes are excluded from the group results. <a className="underline" href="/app/settings#questionnaire-research">Review research choices</a>.</p>
    </details>
    {!!connections.length && <div className="space-y-2" data-testid="portrait-connections">
      <Subheading className="font-semibold">How your answers fit together</Subheading>
      {connections.map(({ id, title, everyday, entry, followUp }, index) => (
        <details key={id} open={index === 0} className="rounded-xl border border-[var(--line)] px-4 py-3">
          <summary className="cursor-pointer font-semibold text-sm">{title}</summary>
          <dl className="mt-3 space-y-3 text-sm">
            <div><dt className="text-xs text-[var(--muted)]">Everyday life</dt>
              <dd>{everyday.statement || <EverydayAnswer item={everyday} />}</dd></div>
            <div><dt className="text-xs text-[var(--muted)]">{entry.sectionTitle}</dt>
              <dd>{intimacyStatement(entry)}</dd></div>
          </dl>
          <p className="mt-3 border-t border-[var(--line)] pt-2 text-sm font-medium">{followUp}</p>
        </details>
      ))}
    </div>}
    {profile.type && <p className="text-sm">
      <strong>{profile.type.code}{profile.type.name ? ` · ${profile.type.name}` : ""}</strong>
      <span className="block mt-0.5 text-xs text-[var(--muted)]">{profile.type.selfSet ? "Type you selected" : "Type suggested by your everyday quiz answers"}</span>
    </p>}
    {profile.description && <div className="border-l-2 border-[var(--line)] pl-3">
      <p className="text-xs text-[var(--muted)]">Your self-description</p>
      <p className="mt-1 text-sm whitespace-pre-line break-words">{profile.description}</p>
    </div>}
    {(!combined || !connections.length) && highlights.length > 0 && <div className="space-y-2">
      <Subheading className="text-sm font-semibold">In everyday life</Subheading>
      <div className="flex flex-col gap-2 text-sm">{highlights.map((item) => item.statement
        ? <p key={item.id}>{item.statement}</p> : <EverydayAnswer key={item.id} item={item} />)}</div>
    </div>}
    {profile.groups.length > 0 && <details className="rounded-xl border border-[var(--line)] px-4 py-3">
      <summary className="cursor-pointer text-sm font-semibold">Explore your {profile.count} saved {profile.count === 1 ? "answer" : "answers"}</summary>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {profile.groups.map(({ axis, items }) => <details key={axis} className="min-w-0 rounded-lg bg-[var(--surface-2)] px-3 py-2">
          <summary className="cursor-pointer text-sm font-medium">{axis} <span className="ml-1 text-xs text-[var(--muted)]">({items.length})</span></summary>
          <div className="mt-3 space-y-3">{items.map((item) => <EverydayAnswer key={item.id} item={item} />)}</div>
        </details>)}
      </div>
    </details>}
    {onExploreIntimacy && <div className="border-t border-[var(--line)] pt-3">
      <button type="button" className="btn-ghost px-4 py-2 text-sm inline-flex items-center gap-2" onClick={onExploreIntimacy}>
        Explore Sex &amp; Intimacy <ArrowRight aria-hidden="true" className="h-4 w-4" />
      </button>
      <p className="mt-2 text-xs text-[var(--muted)]">Adults can include this portrait in their intimacy summary.</p>
    </div>}
  </section>;
}
