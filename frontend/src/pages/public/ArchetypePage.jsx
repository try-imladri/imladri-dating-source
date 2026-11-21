import { Link, useLocation, useParams } from "react-router-dom";
import { pad } from "@/lib/api";
import { useArticleHead } from "./publicHead";
import { CatalogRetry, useCatalogEntry, useCatalogTitle } from "./catalog";
import { ArchIcon } from "@/components/public/ArchIcon";
import { CharacterRow, Threads, Section, Paras, CardGrid, TwoCol, ShareButton, CtaBridge, Loading, DocLayout, Bubbles, BackLink } from "@/components/public/Bits";

const DATING_TITLE = "What it’s like to date them";

const TOC = (his, hasChars, hasDating) => [
  ...(hasChars ? [{ id: "characters", label: "On screen" }] : []),
  { id: "summary", label: "Summary" },
  { id: "texting", label: `${his} texting patterns` },
  { id: "interest", label: "Interested vs. not" },
  ...(hasDating ? [{ id: "dating", label: DATING_TITLE }] : []),
  { id: "flags", label: "Red / green flags" },
  { id: "say", label: "What to say / never say" },
  { id: "manipulation", label: `${his} manipulation patterns` },
  { id: "leverage", label: "Your leverage" },
  { id: "cousin", label: "Closest cousin" },
];

export default function ArchetypePage() {
  const { slug } = useParams();
  const { data: a, missing, failed, retry } = useCatalogEntry(`/public/archetypes/${slug}`);
  useCatalogTitle(a ? `#${pad(a.number)} ${a.name}` : "Archetypes");

  useArticleHead("types", slug, a, failed);
  if (missing) return <div className="px-12 py-24" data-testid="archetype-missing"><p className="font-display text-4xl uppercase font-black">Not available yet.</p><Link to="/types" className="underline mt-4 inline-block">← Back to archetypes</Link></div>;
  if (failed) return <CatalogRetry retry={retry} />;
  if (!a) return <Loading />;
  return <ArchetypeArticle a={a} />;
}

export function ArchetypeArticle({ a }) {
  const s = a.sections || {};
  const cousin = s.closest_cousin || {};
  const fem = a.gender === "female";
  const [he, his, him] = fem ? ["she", "Her", "her"] : ["he", "His", "him"];
  const directorySearch = useLocation().state?.directorySearch;
  const fromDirectory = typeof directorySearch === "string" && directorySearch.startsWith("?");
  const back = fromDirectory ? "/types" + directorySearch : `/types?g=${fem ? "female" : "male"}`;
  const filters = new URLSearchParams(fromDirectory ? directorySearch : "");
  const backLabel = filters.has("q") || filters.has("flag") ? "Back to results" : "All archetypes";
  const comparisonState = fromDirectory ? { directorySearch } : undefined;
  const hasChars = !!s.characters?.length;
  const dating = Array.isArray(s.dating) ? s.dating.filter(p => typeof p === "string" && p.trim()) : [];
  const flagsNumber = dating.length ? 5 : 4;

  return (
    <article data-testid="archetype-page">
      <header id="doc-hero" className="relative overflow-hidden border-b border-[var(--line)] px-5 sm:px-8 lg:px-12 pt-8 pb-10">
        <span aria-hidden="true" className="num-watermark absolute right-4 lg:right-12 top-2 text-[11rem] sm:text-[18rem] pointer-events-none">{pad(a.number)}</span>
        <div className="relative mb-6"><BackLink to={back} label={backLabel} /></div>
        <span className="relative w-16 h-16 rounded-2xl bg-[var(--ink)] text-[var(--bg)] flex items-center justify-center mb-5"><ArchIcon number={a.number} className="w-8 h-8" /></span>
        <p className="eyebrow relative" data-testid="archetype-number">{fem ? "Female" : "Male"} archetype #{pad(a.number)}</p>
        <h1 className="font-display font-black uppercase text-6xl sm:text-8xl lg:text-9xl leading-[.85] mt-3 relative" data-testid="archetype-name">{a.name}</h1>
        <p className="text-lg sm:text-2xl text-[var(--muted)] mt-5 max-w-3xl relative" data-testid="archetype-identity">{a.identity}</p>
        <div className="mt-6 relative"><ShareButton title={a.name} /></div>
      </header>
      <DocLayout toc={TOC(his, hasChars, dating.length > 0)} head={{ back: { to: back, label: backLabel }, eyebrow: `${fem ? "Female" : "Male"} archetype #${pad(a.number)}`, title: a.name }}>
        {hasChars && <Section id="characters" n={0} title={`You've seen ${him} before`}><CharacterRow items={s.characters} /><p className="text-[10px] text-[var(--muted)] mt-2">Photos via Wikipedia / Wikimedia Commons. Characters shown for illustration and commentary.</p></Section>}
        <Section id="summary" n={1} title="Summary"><Paras v={s.summary} />
          {cousin.tell && <details className="mt-5 text-sm max-w-3xl"><summary className="cursor-pointer font-semibold">When a different type might fit</summary>
            <p className="mt-2">{cousin.tell}</p>
            {a.cousin_ref?.unlocked && <Link to={`/types/${a.cousin_ref.slug}`} state={comparisonState} className="inline-block mt-2 underline">Compare {a.cousin_ref.name}</Link>}
          </details>}
          {a.has_public_figures && <Link className="inline-block mt-4 text-sm underline" to={`/figures?archetype=${a.number}`}>See public figures compared with {a.name}</Link>}
        </Section>
        <Section id="texting" n={2} title={`${his} texting patterns`}>
          <Paras v={s.texting_patterns?.text} />
          <div className="mt-8">{s.threads?.length ? <Threads threads={s.threads} /> : <div className="card p-5 max-w-lg"><Bubbles items={s.texting_patterns?.bubbles} /></div>}</div>
        </Section>
        <Section id="interest" n={3} title="Interested vs. not interested"><TwoCol left={s.interested} right={s.not_interested} lt="Actually interested" rt="Not interested" /></Section>
        {dating.length > 0 && <Section id="dating" n={4} title={DATING_TITLE}><Paras v={dating} /></Section>}
        <Section id="flags" n={flagsNumber} title="Red flags / Green flags"><TwoCol left={s.red_flags} right={s.green_flags} lt="Red flags" rt="Green flags" ltone="red" rtone="green" /></Section>
        <Section id="say" n={flagsNumber + 1} title="What to say / never say"><TwoCol left={s.what_to_say} right={s.never_say} lt="Say" rt="Never say" ltone="green" rtone="red" /></Section>
        <Section id="manipulation" n={flagsNumber + 2} title={`${his} manipulation patterns`}><CardGrid items={s.manipulation} tone="red" /></Section>
        <Section id="leverage" n={flagsNumber + 3} title="Your leverage"><CardGrid items={s.leverage} tone="green" /></Section>
        <Section id="cousin" n={flagsNumber + 4} title="Closest cousin">
          {a.cousin_ref?.unlocked && <Link to={`/types/${a.cousin_ref.slug}`} state={comparisonState} className="font-display text-3xl uppercase font-black underline underline-offset-8" data-testid="cousin-link">#{pad(cousin.number)} {a.cousin_ref.name} →</Link>}
          {a.cousin_ref?.unlocked === false && <p>#{pad(cousin.number)} · Not available yet</p>}
          <div className="mt-5"><Paras v={cousin.text} /></div>
          {cousin.tell && <p className="card p-5 mt-5 max-w-4xl"><span className="font-mono text-[10px] uppercase tracking-widest">The tell — </span>{cousin.tell}</p>}
        </Section>
        <CtaBridge origin={`types/${a.slug}`} text={`Think ${he}'s ${a.name}?`} button={`Make ${him === "her" ? "her" : "his"} profile`} sub="Save the details you choose and get suggestions grounded in your conversation." />
      </DocLayout>
    </article>
  );
}
