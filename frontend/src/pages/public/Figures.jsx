import { Link, useLocation, useNavigationType, useSearchParams } from "react-router-dom";
import { useEffect, useMemo, useState } from "react";
import { filterFigures } from "@/lib/figureSearch";
import { AlertTriangle, Search } from "lucide-react";
import { pad } from "@/lib/api";
import { mediaUrl, PAGE_X } from "@/components/public/Bits";
import { CatalogRetry, useCatalogEntry, useCatalogTitle } from "./catalog";

export const figurePhotoUrl = url => url?.startsWith("/img/figures/") ? url : mediaUrl(url);
const PAGE_SIZE = 48;
const nameOrder = new Intl.Collator("en", { sensitivity: "base", numeric: true });

export function figureLeadLabel(status) {
  if (status === "career_reassigned") return "Revised lead";
  if (status === "career_supported") return "Reviewed lead";
  if (status === "career_limited") return "Tentative career match";
  if (!status || status === "unassessed") return "Proposed lead";
  if (status === "changed") return "Lead awaiting reassessment";
  if (status === "revise") return "Lead under revision";
  if (status === "insufficient") return "Proposed lead · limited evidence";
  if (status === "one" || status === "neither") return "Tentative match";
  return "Lead match";
}

export function FigureAvatar({ f, className = "w-24 h-24", loading = "eager" }) {
  const initials = f.name.split(" ").map((w) => w[0]).join("").slice(0, 2);
  return f.photo_url ? (
    <img src={figurePhotoUrl(f.photo_url)} alt={f.name} loading={loading} className={`object-cover object-top rounded-2xl bg-[var(--surface-2)] ${className}`} data-testid="figure-photo" />
  ) : (
    <div className={`${className} rounded-2xl bg-[var(--surface-2)] flex items-center justify-center font-display font-black text-4xl`} data-testid="figure-avatar">{initials}</div>
  );
}

export default function Figures() {
  useCatalogTitle("Public figures");
  const { data: figs, failed, retry } = useCatalogEntry("/public/figures");
  const [params, setParams] = useSearchParams();
  const { search: directorySearch } = useLocation();
  const urlQuery = params.get("q") || "";
  const navigation = useNavigationType();
  const [query, setQuery] = useState(urlQuery);
  // A router replacement can finish after the next keystroke. The input owns the
  // in-progress text; a link or browser history navigation restores it from the URL.
  useEffect(() => {
    if (navigation !== "REPLACE") setQuery(urlQuery);
  }, [navigation, urlQuery]);
  const alphabetical = useMemo(() => [...(figs || [])].sort((a, b) => nameOrder.compare(a.name, b.name)), [figs]);
  const field = params.get("field") || "";
  const style = params.get("style") || "";
  const type = params.get("archetype") || "";
  const leadOnly = params.get("role") === "lead";
  const fields = [...new Set(alphabetical.map(f=>f.field).filter(Boolean))].sort();
  const styles = useMemo(() => {
    const byNumber = new Map();
    for (const f of alphabetical) for (const match of f.matches || []) {
      if (match.number && match.name) byNumber.set(match.number, match);
    }
    return [...byNumber.values()].sort((a,b)=>nameOrder.compare(a.name,b.name) || a.number-b.number);
  }, [alphabetical]);
  const hasType = Boolean(type || style);
  const visible = filterFigures(alphabetical, query).filter(f => {
    if (field && f.field !== field) return false;
    if (!hasType) return true;
    const matches = f.matches || [];
    return (leadOnly ? matches.slice(0,1) : matches).some(m => type ? String(m.number) === type : m.name === style);
  });
  function setFilter(key, value) {
    const next = new URLSearchParams(params);
    if (value) next.set(key,value); else next.delete(key);
    if (key === "archetype") {
      next.delete("style");
      if (!value) next.delete("role");
    }
    next.delete("page");
    setParams(next, {replace:true, preventScrollReset:true});
  }
  function clearFilters() {
    setQuery("");
    setParams({}, {replace:true, preventScrollReset:true});
  }
  const page = Math.min(Math.max(1, Math.floor(Number(params.get("page"))) || 1), Math.max(1, Math.ceil(visible.length / PAGE_SIZE)));
  const limit = page * PAGE_SIZE;
  function changeQuery(value) {
    setQuery(value);
    const next = new URLSearchParams(params);
    if (value) next.set("q", value);
    else next.delete("q");
    next.delete("page");
    setParams(next, { replace: true, preventScrollReset: true });
  }
  function showMore() {
    const next = new URLSearchParams(params);
    next.set("page", String(page + 1));
    setParams(next, { replace: true, preventScrollReset: true });
  }
  if (failed) return <CatalogRetry retry={retry} />;
  return (
    <div className={`${PAGE_X} pt-12`}>
      <p className="eyebrow">Public persona analysis</p>
      <h1 className="font-display font-black uppercase text-6xl sm:text-8xl" data-testid="figures-title">Public Figures</h1>
      <div role="search" aria-label="Public figures" className="mt-5 flex flex-wrap items-center gap-3">
        <label className="relative w-full sm:max-w-xl">
          <Search aria-hidden="true" className="absolute left-4 top-1/2 -translate-y-1/2 h-5 w-5 pointer-events-none text-[var(--muted)]" />
          <span className="sr-only">Find a public figure by name</span>
          <input type="search" value={query} onChange={e => changeQuery(e.target.value)} placeholder="Search public figures by name" className="w-full rounded-full border border-[var(--line)] bg-[var(--surface-2)] pl-12 pr-4 py-3 text-base focus-visible:outline-2 focus-visible:outline-offset-2" />
        </label>
        <label className="text-sm">Field
          <select className="block rounded-lg border border-[var(--line)] bg-[var(--bg)] p-2 mt-1 max-w-full" value={field} onChange={e=>setFilter("field", e.target.value)}><option value="">All fields</option>{fields.map(x=><option key={x}>{x}</option>)}</select></label>
        <label className="text-sm max-w-full">Archetype
          <select className="block rounded-lg border border-[var(--line)] bg-[var(--bg)] p-2 mt-1 max-w-full" value={type || (style ? "family" : "")} onChange={e=>setFilter("archetype", e.target.value)}>
            <option value="">All archetypes</option>
            {style && !type && <option value="family">{style} · all editions</option>}
            {type && !styles.some(m => String(m.number) === type) && <option value={type}>Selected archetype</option>}
            {styles.map(m=><option key={m.number} value={m.number}>{m.name}{m.edition ? ` · ${m.edition} edition` : ""}</option>)}
          </select></label>
        {hasType && <label className="text-sm"><input className="mr-2" type="checkbox" checked={leadOnly} onChange={e=>setFilter("role",e.target.checked ? "lead" : "")}/>Lead matches only</label>}
        <span className="text-sm text-[var(--muted)]" role="status">{figs ? ((query.trim() || field || hasType) ? `${visible.length} of ${figs.length} profiles` : `${figs.length} profiles`) : "Loading profiles…"}</span>
      </div>
      <p className="text-sm text-[var(--muted)] mt-4 flex items-center gap-2" data-testid="figures-disclaimer"><AlertTriangle className="w-4 h-4 shrink-0" />Analysis of public persona, based on publicly available information, for insight and entertainment. Not a clinical assessment.</p>
      {figs && !visible.length && (query.trim() || field || hasType) && <p className="mt-6">{hasType || field ? "No profiles match these filters." : "No matching names."} <button className="underline" onClick={clearFilters}>Show all profiles</button></p>}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4 mt-6">
        {visible.slice(0, limit).map((f, i) => (
          <Link key={f.slug} to={`/figures/${f.slug}`} state={directorySearch ? { directorySearch } : undefined} aria-label={f.name} aria-describedby={f.matches?.length ? `figure-lead-${f.slug}${f.lead_review_scope ? " figure-scope-" + f.slug : ""}` : undefined} className="card overflow-hidden hover:border-[var(--ink)] transition-colors rise group" style={{ animationDelay: `${Math.min(i, 5) * 60}ms` }} data-testid={`figure-card-${f.slug}`}>
            <div className="overflow-hidden"><FigureAvatar f={f} loading={i < 6 ? "eager" : "lazy"} className="w-full aspect-[3/4] rounded-none object-[center_15%] group-hover:scale-[1.03] transition-transform duration-500" /></div>
            <div className="p-4">
              <p className="font-display font-black uppercase text-[clamp(1.15rem,5.5vw,1.875rem)] [overflow-wrap:anywhere] leading-none">{f.name}</p>
              <p className="text-xs text-[var(--muted)] mt-2 line-clamp-3">{f.identity}</p>
              <div className="flex flex-wrap gap-1.5 mt-3">
                {f.matches?.map((m,j) => <span key={m.number} id={j===0 ? `figure-lead-${f.slug}` : undefined} className={`font-mono text-xs px-2 py-1 rounded-xl ${j===0 ? "bg-[var(--ink)] text-[var(--bg)]" : "border border-[var(--line)]"}`}>{j===0 ? figureLeadLabel(f.lead_review_status) : "Also"} · #{pad(m.number)}{m.name ? ` ${m.name}` : ""}</span>)}
              </div>
              {f.lead_review_scope && <p id={`figure-scope-${f.slug}`} className="mt-2 text-xs text-[var(--muted)]">{f.lead_review_scope}</p>}
            </div>
          </Link>
        ))}
      </div>
      {visible.length > limit && <button type="button" onClick={showMore} className="my-6 rounded-full border border-[var(--line)] px-5 py-2">Show {Math.min(PAGE_SIZE, visible.length - limit)} more profiles</button>}
    </div>
  );
}
