import { Link, useLocation, useParams } from "react-router-dom";
import { AlertTriangle } from "lucide-react";
import { pad } from "@/lib/api";
import { useArticleHead } from "./publicHead";
import { CatalogRetry, useCatalogEntry, useCatalogTitle } from "./catalog";
import { Section, Paras, CardGrid, TwoCol, ShareButton, CtaBridge, Loading, DocLayout, BackLink } from "@/components/public/Bits";
import { FigureAvatar, figurePhotoUrl, figureLeadLabel } from "@/pages/public/Figures";

const TOC = [
  { id: "pattern", label: "The Pattern" },
  { id: "communicates", label: "How He Communicates" },
  { id: "dating", label: "What it’s like to date them" },
  { id: "spectrum", label: "Interested / Invested / Done" },
  { id: "handling", label: "Handling Him" },
  { id: "power", label: "His Power Moves" },
  { id: "leverage", label: "Your Leverage" },
  { id: "map", label: "Archetype Map" },
];

const WOMEN_TOC = TOC.map(item => ({
  ...item,
  label: { communicates: "How She Communicates", handling: "Handling Her", power: "Her Power Moves" }[item.id] || item.label,
}));

function SourceLink({ url, children }) {
  return /^https?:\/\//i.test(url || "")
    ? <a href={url} className="underline underline-offset-2">{children}</a>
    : <span>{children}</span>;
}

function CareerPassage({ location, checkedOn }) {
  const base = (location.passage_url || location.url).split("#")[0];
  const page = Number.isInteger(location.page) && location.page > 0 ? location.page : null;
  const isPdf = /\.pdf(?:[?]|$)/i.test(base);
  const anchor = isPdf ? "#search=" : "#:~:text=";
  const target = page ? `${base}#page=${page}` : base + anchor + encodeURIComponent(location.locator);
  return <p className="text-xs mt-2">
    {page ? `PDF page ${page}` : isPdf ? "Find in PDF" : "Find in source"} · <SourceLink url={target}>“{location.locator}”</SourceLink> · checked {checkedOn}
  </p>;
}

function ArchRef({ n, refs }) {
  const r = refs?.[String(n)];
  if (!r) return <span>#{pad(n)}</span>;
  if (r.unlocked === false) return <span>#{pad(n)} · Not available yet</span>;
  return <Link to={`/types/${r.slug}`} className="underline underline-offset-4">#{pad(n)} {r.name}</Link>;
}

const figureDirectory = { to: "/figures", label: "All public figures" };

export default function FigurePage() {
  const { slug } = useParams();
  const directorySearch = useLocation().state?.directorySearch;
  const back = typeof directorySearch === "string" && directorySearch.startsWith("?")
    ? { to: "/figures" + directorySearch, label: "Back to results" } : figureDirectory;
  const { data: f, missing, failed, retry } = useCatalogEntry(`/public/figures/${slug}`);
  useCatalogTitle(f?.name || "Public figures");
  useArticleHead("figures", slug, f, failed);
  if (missing) return <div className="px-12 py-24" data-testid="figure-missing"><h1 className="font-display text-4xl uppercase font-black">Figure unavailable</h1><Link to={back.to} className="underline mt-4 inline-block">{back === figureDirectory ? "Browse public figures" : back.label}</Link></div>;
  if (failed) return <CatalogRetry retry={retry} />;
  if (!f) return <Loading />;
  return <FigureArticle f={f} back={back} />;
}

export function FigureArticle({ f, back = figureDirectory }) {
  const s = f.sections || {};
  const isWoman = f.gender === "woman" || f.gender === "female";
  const dating = Array.isArray(s.dating) ? s.dating.filter(p => typeof p === "string" && p.trim()) : [];
  const toc = (isWoman ? WOMEN_TOC : TOC).filter(item => item.id !== "dating" || dating.length > 0);
  const section = id => {
    const index = toc.findIndex(item => item.id === id);
    return { id, n: index + 1, title: toc[index].label };
  };
  const credit = f.photo_attribution;
  const refs = f.archetype_refs;
  const mp = s.map || {};
  const review = f.evidence_review;
  const status = review?.lead_status || review?.staff_result || review?.status;
  const unassessed = !status || status === "unassessed";
  const lead = figureLeadLabel(status);
  const tentative = ["one", "neither", "insufficient", "revise", "changed", "career_limited"].includes(status);
  const insufficient = tentative && review.readers.length === 2 && review.readers.every(r => r.label === "Insufficient evidence");
  const qualification = status === "changed" ? "The career evidence changed after the last assessment." :
    status === "revise" ? "An editor flagged this comparison for revision." :
    status === "career_limited" ? "The public record supports this comparison only tentatively. See the evidence and the closest alternative below." :
    insufficient ? "Both AI readings found too little evidence to choose a lead." : "The career examples support more than one reading.";
  const examples = s.communicates?.examples || [];
  const citations = (s.communicates?.example_sources || []).filter(ref => examples.includes(ref.example));
  const repeated = new Map();
  for (const ref of citations) repeated.set(ref.url,(repeated.get(ref.url)||0)+1);
  const background = (f.sources || []).filter(ref => !repeated.has(ref.url));
  const alternateLabels = [...new Set((review?.readers || []).map(reader => reader.label))].filter(label =>
    label && label !== "Insufficient evidence" && label !== refs?.[String(mp.closest?.number)]?.name);

  return (
    <article data-testid="figure-page">
      <header id="doc-hero" className="px-5 sm:px-8 lg:px-12 pt-8 pb-10 border-b border-[var(--line)]">
        <div className="mb-6"><BackLink {...back} /></div>
        <div className="grid grid-cols-1 md:grid-cols-[minmax(0,380px)_minmax(0,1fr)] gap-8 lg:gap-14 items-end">
        <div className="min-w-0 [overflow-wrap:anywhere]">
          <FigureAvatar f={f} className="w-full aspect-[4/5]" />
          {f.photo_url && <p className="text-[10px] text-[var(--muted)] mt-1">{credit ? <>
            <SourceLink url={credit.source}>{credit.artist}</SourceLink>{" · "}<SourceLink url={credit.license_url}>{credit.license}</SourceLink>{" · Resized for display."}
          </> : f.photo_credit}</p>}
        </div>
        <div className="min-w-0">
          <p className="eyebrow">Public figure</p>
          <h1 className="font-display font-black uppercase text-6xl sm:text-8xl lg:text-9xl leading-[.85] mt-3 [overflow-wrap:anywhere]" data-testid="figure-name">{f.name}</h1>
          <p className="text-sm text-[var(--muted)] mt-3 flex items-center gap-2" data-testid="figure-disclaimer"><AlertTriangle className="w-4 h-4 shrink-0" />Analysis of public persona, based on publicly available information, for insight and entertainment. Not a clinical assessment.</p>
          <p className="text-lg sm:text-2xl text-[var(--muted)] mt-5 max-w-3xl" data-testid="figure-identity">{f.identity}</p>
          <p className="text-[11px] text-[var(--muted)] mt-3 max-w-3xl border-l-2 border-[var(--line)] pl-3" data-testid="figure-sourcing">{f.sources?.length ? <>
            {citations.length > 0 && <><a className="underline underline-offset-2" href="#communicates">See the career examples and their sources</a>. </>}
            {background.length > 0 && <>Sources for the public record: {background.map(({ label, url }, i) => <span key={url}>{i > 0 && " · "}<SourceLink url={url}>{label}</SourceLink></span>)}. </>}
            The archetype analysis is Imladri’s interpretation.
          </> : "Sourcing: this page summarises widely reported public statements, interviews and news coverage. Individual claims are not yet linked to sources and may be incomplete or out of date. It is interpretation for entertainment, not a statement of fact about this person's private life. Photo credit as shown; images are from Wikipedia/Wikimedia Commons and remain under their original licences."}</p>
          <div className="flex flex-wrap gap-2 mt-6" data-testid="figure-match-chips">
            {f.matches?.map((m, i) => (
              <div key={m.number} className={`px-4 py-2.5 rounded-xl text-sm max-w-sm ${i === 0 ? "bg-[var(--ink)] text-[var(--bg)]" : "border border-[var(--line)]"}`}>
                <span className="font-mono font-semibold">{i === 0 ? lead : "Also"}: #{pad(m.number)}{refs?.[String(m.number)]?.unlocked && ` ${refs[String(m.number)].name}`}</span>
                {i === 0 && review?.career?.scope && <p className="text-xs font-medium mt-1">{review.career.scope}</p>}
                {m.why && <span className="block text-xs opacity-80 mt-0.5">{m.why}</span>}
                {i === 0 && unassessed && <p className="text-xs mt-1">This comparison is awaiting its first recorded lead review. <a href="#communicates" className="underline">Read the career examples</a>.</p>}
                {i === 0 && tentative && <><p className="text-xs mt-1">{qualification}</p>
                  <a href={status === "changed" ? "#communicates" : "#map"} className="block text-xs underline mt-1">{status === "changed" ? "Read the current examples" : "See the basis for this match"}</a></>}
              </div>
            ))}
          </div>
          {review?.staff_reviewed_at && <p className="mt-3 text-xs text-[var(--muted)]">Editorial decision recorded {review.staff_reviewed_at.slice(0,10)}.</p>}
          <div className="mt-6 flex flex-wrap items-center gap-3"><ShareButton title={f.name} />
            <Link className="text-xs underline" to={"/support?topic=Other&figure="+encodeURIComponent(f.slug)}>Suggest a correction</Link></div>
        </div>
        </div>
      </header>
      <DocLayout toc={toc} head={{ back, eyebrow: "Public figure", title: f.name, photo: figurePhotoUrl(f.photo_url) }}>
        <Section {...section("pattern")}><Paras v={s.pattern} /></Section>
        <Section {...section("communicates")}>
          <Paras v={s.communicates?.text} />
          {examples.length > 0 && <>
            <h3 className="eyebrow mt-8 mb-3">Career examples</h3>
            <ul className="grid md:grid-cols-2 gap-3">{examples.map((example, i) => (
              <li key={i} id={`career-example-${i + 1}`} className="bubble-him px-5 py-4 scroll-mt-20"><p>{example}</p>
                {citations.filter(ref => ref.example === example).map(source => {
                  const locator = f.source_locators?.find(row => row.example === example && row.url === source.url);
                  return <div className="mt-3" key={source.url}><p className="text-xs text-[var(--muted)]"><SourceLink url={source.url}>{source.label}</SourceLink></p>
                    {locator && <CareerPassage location={locator} checkedOn={f.source_locators_checked_on} />}
                    {!locator && repeated.get(source.url)>1 && <p className="text-xs text-[var(--muted)]">This source covers several examples. A passage for this example still needs verification.</p>}
                  </div>;
                })}
              </li>
            ))}</ul>
          </>}
        </Section>
        {dating.length > 0 && <Section {...section("dating")}>
          <p className="text-sm text-[var(--muted)] mb-5">Imagine dating someone with this public communication style. These scenes are fictional.</p>
          <Paras v={dating} />
        </Section>}
        <Section {...section("spectrum")} title="Interested vs. Invested vs. Done">
          <div className="grid lg:grid-cols-3 gap-3">
            {[["Interested", "interested"], ["Invested", "invested"], ["Done", "done"]].map(([t, k]) => (
              <div key={k} className="card p-5"><p className="font-mono text-xs uppercase tracking-widest mb-3">{t}</p><p className="leading-relaxed">{s.spectrum?.[k]}</p></div>
            ))}
          </div>
        </Section>
        <Section {...section("handling")}>
          <p className="text-[var(--muted)] mb-6">Try these approaches when you recognize the same communication pattern in your own conversation.</p>
          <TwoCol left={s.handling?.works} right={s.handling?.never} lt="What works" rt="What never works" />
        </Section>
        <Section {...section("power")}><CardGrid items={s.power_moves} tone="red" /></Section>
        <Section {...section("leverage")}><CardGrid items={s.leverage} tone="green" /></Section>
        <Section {...section("map")}>
          <div className="grid lg:grid-cols-3 gap-3">
            {mp.closest?.number && <div className="card p-5 lg:col-span-3"><p className="font-mono text-xs uppercase tracking-widest mb-2">{lead}</p><p className="font-display font-black uppercase text-3xl"><ArchRef n={mp.closest.number} refs={refs} /></p><div className="mt-3"><Paras v={mp.closest.rationale} /></div>
              {mp.basis && <div className="mt-4 border-l-2 border-[var(--line)] pl-3"><p className="font-semibold">{mp.basis.style}</p><p className="text-sm mt-1">{mp.basis.observation}</p>
                <p className="text-xs text-[var(--muted)] mt-2">{mp.basis.sources?.map((ref, i) => <span key={ref.url}>{i > 0 && " · "}<SourceLink url={ref.url}>{ref.label}</SourceLink></span>)}</p>
                <p className="text-xs text-[var(--muted)] mt-2">The match describes these public examples. The dating scenes above are fictional.</p>
                {status === "changed" && <p className="mt-3 text-sm">{qualification} <a href="#communicates" className="underline">Read the current career examples</a>.</p>}
                {review?.career && <details className="mt-3 text-sm">
                  <summary className="cursor-pointer font-semibold">Career evidence behind this match</summary>
                  {review.career.rationale !== mp.closest.rationale && <p className="my-2">{review.career.rationale}</p>}
                  <p className="text-xs">{review.career.reviewer} · {review.career.reviewed_on}</p>
                  {review.career.human_reason && <p className="my-2"><strong>What remains uncertain:</strong> {review.career.human_reason}</p>}
                  <ul className="mt-2 space-y-2">{review.career.sources.map(source =>
                    <li key={source.url}><SourceLink url={source.url}>{source.label}</SourceLink>
                        {source.evidence && <p className="mt-1">{source.evidence}</p>}</li>)}</ul>
                </details>}
                {f.evidence_review && status !== "changed" && (review.staff_result || review.readers.length > 0) && <details className="mt-3 text-sm" data-testid="figure-evidence-review">
                  <summary className="cursor-pointer font-semibold">{f.evidence_review.staff_result === "revise" ? "Lead match under editorial review" :
                    f.evidence_review.staff_result === "retain" ? "Lead match reviewed by staff" : f.evidence_review.status === "both" ?
                    "Two readings of the evidence" : "Other readings of the evidence"}</summary>
                  {!!f.evidence_review.readers.length && <><p className="mt-2">Two AI readers independently considered the career examples without seeing the published match.</p>
                    {f.evidence_review.readers.map((r, i) => <div key={i} className="mt-2"><p><strong>Reader {i + 1} · {r.label}.</strong> {r.reason}</p>
                      <div className="flex flex-wrap gap-3">{r.examples.map(n => <a key={n} className="text-xs underline" href={`#career-example-${n}`}>Example {n}</a>)}</div></div>)}</>}
                  {tentative && alternateLabels.map(label => <Link key={label} className="block mt-2 underline" to={"/types?q="+encodeURIComponent(label)}>Compare {label} in the archetype guide</Link>)}
                  <a href="#communicates" className="inline-block mt-2 underline">Read the career examples</a>
                </details>}
              </div>}</div>}
            {mp.cousin?.number && <div className="card p-5"><p className="font-mono text-xs uppercase tracking-widest mb-2">Closest cousin</p><p className="font-semibold"><ArchRef n={mp.cousin.number} refs={refs} /></p><p className="text-sm mt-2">{mp.cousin.tell}</p></div>}
            {mp.secondary?.map((x) => <div key={x.number} className="card p-5"><p className="font-mono text-xs uppercase tracking-widest mb-2">Secondary affinity</p><p className="font-semibold"><ArchRef n={x.number} refs={refs} /></p><p className="text-sm mt-2">{x.why}</p></div>)}
          </div>
        </Section>
        <CtaBridge origin={`figures/${f.slug}`} text={`Now imagine this level of insight on your situation. Not a celebrity — ${isWoman ? "her" : "him"}.`} button={isWoman ? "Make her profile" : "Make his profile"} />
      </DocLayout>
    </article>
  );
}
