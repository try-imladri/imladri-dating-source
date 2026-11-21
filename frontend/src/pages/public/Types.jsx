import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigationType, useSearchParams } from "react-router-dom";
import { X, Flag, ChevronDown, ArrowLeft } from "lucide-react";
import { ArchetypeCard, PAGE_X } from "@/components/public/Bits";
import { Silhouette, themed } from "@/components/public/Silhouette";
import { useTheme } from "@/lib/theme";
import { CatalogRetry, useCatalogEntry, useCatalogTitle } from "./catalog";

function FlagToggle({ open, setOpen, selected }) {
  return (
    <button onClick={() => setOpen(!open)} data-testid="redflag-toggle" aria-expanded={open} aria-controls="archetype-flags"
      className={`inline-flex items-center gap-2 px-5 h-12 rounded-full font-semibold border transition-colors ${open || selected.length ? "bg-red-600 border-red-600 text-white" : "border-red-300 text-red-700 dark:text-red-300 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-950/50"}`}>
      <Flag className="w-4 h-4" fill="currentColor" />Red flags{selected.length > 0 && <span className="ml-1 px-2 py-0.5 rounded-full bg-white text-red-600 text-xs">{selected.length}</span>}
      <ChevronDown className={`w-4 h-4 transition-transform ${open ? "rotate-180" : ""}`} />
    </button>
  );
}

function FlagPanel({ flags, selected, toggle, clear, count }) {
  return (
    <aside className="h-fit rounded-2xl border border-red-200 bg-red-50 text-red-950 dark:bg-red-950/40 dark:border-red-900 dark:text-red-50 p-5" data-testid="redflag-panel">
      <div className="flex items-center justify-between">
        <p className="font-display font-black uppercase text-2xl">Spot the red flags</p>
        {selected.length > 0 && <button onClick={clear} className="text-xs underline" data-testid="redflag-clear">Clear</button>}
      </div>
      <p className="text-xs opacity-75 mt-1 mb-4">Pick what they do. We'll show the types that match all of them.</p>
      <div className="flex flex-wrap gap-2">
        {flags.map((f) => {
          const on = selected.includes(f);
          return (
            <button key={f} onClick={() => toggle(f)} aria-pressed={on} data-testid={`redflag-${f.replace(/[^a-z0-9]+/gi, "-")}`}
              className={`px-3 py-1.5 rounded-full text-xs font-semibold border transition-colors inline-flex items-center gap-1 ${on ? "bg-red-600 border-red-600 text-white" : "border-red-300 dark:border-red-800 hover:bg-red-100 dark:hover:bg-red-900/50"}`}>
              {f}{on && <X className="w-3 h-3" />}
            </button>
          );
        })}
      </div>
      {selected.length > 0 && <p className="mt-4 text-sm font-semibold" data-testid="redflag-count">{count} type{count === 1 ? "" : "s"} match</p>}
    </aside>
  );
}

const GENDERS = [["male", "Male"], ["female", "Female"]];
const emptyTypes = [];

function GenderPick({ types, loading = false }) {
  const { theme } = useTheme();
  return (
    <div className="grid grid-cols-2 gap-4 sm:gap-16 mt-10 max-w-4xl mx-auto" data-testid="gender-picker">
      {GENDERS.map(([g, label], i) => {
        const list = types.filter((t) => t.gender === g);
        const base = g === "male" ? "/img/solo-man" : "/img/solo-woman";
        return (
          <Link key={g} to={`/types?g=${g}`} className="group flex flex-col items-center text-center rise" style={{ animationDelay: `${i * 120}ms` }} data-testid={`gender-option-${g}`}>
            <div className="couple-enter relative h-[min(22rem,40vh)] w-full max-w-full flex items-end justify-center">
              <span className="h-full w-full flex items-end justify-center transition-opacity duration-300 group-hover:opacity-0"><Silhouette src={`${base}.webp`} className="h-full w-full object-contain object-bottom" testid={`gender-silhouette-${g}`} /></span>
              <img src={themed(`${base}-front.png`, theme)} alt="" aria-hidden="true" className="absolute inset-0 h-full w-full object-contain object-bottom opacity-0 scale-95 transition-[opacity,transform] duration-300 group-hover:opacity-100 group-hover:scale-100 pointer-events-none" data-testid={`gender-front-${g}`} />
            </div>
            <p className="font-display font-black uppercase text-[clamp(1.75rem,8vw,3rem)] sm:text-7xl leading-none mt-5 group-hover:underline underline-offset-8 decoration-4">{label}</p>
            {!loading && <p className="font-mono text-xs uppercase tracking-widest text-[var(--muted)] mt-2">{list.length} archetypes</p>}
          </Link>
        );
      })}
    </div>
  );
}


function FindArchetype({value, onChange}) {
  return <label className="block text-left max-w-xl mx-auto mt-6">
    <span className="block font-semibold text-sm mb-1">Find an archetype</span>
    <input type="search" autoComplete="off" placeholder="Try a name, a trait, or a red flag"
      value={value} onChange={event => onChange(event.target.value)}
      className="w-full bg-transparent border border-[var(--line)] rounded-full py-3 px-5" />
  </label>;
}

const folded = value => String(value ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
  .toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
const matchesSearch = (item, value) => {
  const words = folded(value).trim().split(/\s+/).filter(Boolean);
  if (words.length && item.unlocked === false) return false;
  const text = folded([item.name, item.slug, item.one_liner, ...(item.texting_tags || []), ...(item.red_flag_tags || [])].join(" "));
  return words.every(word => text.includes(word));
};

export default function Types() {
  const [params, setParams] = useSearchParams();
  const { search } = useLocation();
  const urlQuery = params.get("q") || "";
  const navigation = useNavigationType();
  const g = ["male", "female"].includes(params.get("g")) ? params.get("g") : null;
  useCatalogTitle(g === "male" ? "Male archetypes" : g === "female" ? "Female archetypes" : "Archetypes");
  const { data, failed, retry } = useCatalogEntry("/public/archetypes");
  const all = data || emptyTypes;
  const [open, setOpen] = useState(params.has("flag"));
  const [query,setQuery] = useState(urlQuery);
  useEffect(() => {
    if (navigation !== "REPLACE") setQuery(urlQuery);
  }, [navigation,urlQuery]);
  function changeQuery(value) {
    setQuery(value);
    const next = new URLSearchParams(params);
    if (value) next.set("q",value);
    else next.delete("q");
    setParams(next,{replace:true,preventScrollReset:true});
  }

  const types = useMemo(() => all.filter((t) => t.gender === g), [all, g]);
  const flags = useMemo(() => [...new Set(types.flatMap((t) => t.red_flag_tags || []))].filter((f) => f !== "rare").sort(), [types]);
  const selected = flags.filter(flag => params.getAll("flag").includes(flag));
  function setSelected(values) {
    const next = new URLSearchParams(params);
    next.delete("flag");
    for (const value of values) next.append("flag", value);
    setParams(next, { replace: true, preventScrollReset: true });
  }
  const acrossEditions = useMemo(() => query.trim() ? all.filter(t => matchesSearch(t,query)) : [], [all,query]);
  const shown = types.filter((t) => matchesSearch(t,query) && selected.every((f) => (t.red_flag_tags || []).includes(f)));
  const toggle = flag => setSelected(selected.includes(flag) ? selected.filter(s => s !== flag) : [...selected,flag]);

  if (failed) return <CatalogRetry retry={retry} />;
  if (!g) {
    return (
      <div className={`${PAGE_X} pt-12 pb-16 text-center`}>
        <h1 className="font-display font-black uppercase text-[clamp(2rem,12vw,4.5rem)] sm:text-[clamp(4.5rem,12vw,8rem)] leading-none" data-testid="types-title">Archetypes</h1>
        <p className="text-[var(--muted)] text-lg mt-4 max-w-xl mx-auto">Search a behavior across both editions, or browse one edition.</p>
        <FindArchetype value={query} onChange={changeQuery} />
        {!data && <p role="status" className="mt-6">Loading archetypes…</p>}
        {query.trim() ? data && <div className="mt-6 text-left">
          <p role="status" className="text-sm text-[var(--muted)] mb-3">{acrossEditions.length} matching {acrossEditions.length === 1 ? "archetype" : "archetypes"} across both editions.</p>
          {GENDERS.map(([gender, edition]) => {
            const entries = acrossEditions.filter(a => a.gender === gender);
            return entries.length > 0 && <section key={gender} aria-label={edition+" edition"} className="mb-6">
              <h2 className="font-semibold mb-2">{edition} edition</h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
                {entries.map((a,i) => <ArchetypeCard key={a.slug} a={a} i={i} directorySearch={search} />)}
              </div>
            </section>;
          })}
          <button className="underline mt-4" onClick={()=>changeQuery("")}>Clear search</button>
        </div> : <GenderPick types={all} loading={!data} />}
      </div>
    );
  }
  return (
    <div className={`${PAGE_X} pt-12`}>
      <Link to="/types" className="inline-flex items-center gap-2 text-sm font-semibold hover:-translate-x-1 transition-transform" data-testid="types-back"><ArrowLeft className="w-4 h-4" />Back to archetypes</Link>
      <div className="flex flex-wrap items-end gap-4 mt-4">
        <h1 className="font-display font-black uppercase text-7xl sm:text-9xl leading-none" data-testid="types-title">{g === "female" ? "Female" : "Male"}</h1>
        <span role="status" className="font-mono text-xs uppercase tracking-widest text-[var(--muted)] pb-3" data-testid="types-count">{!data ? "Loading archetypes…" : `${query.trim() || selected.length ? shown.length + " of " : ""}${types.length} archetypes`}</span>
        <FlagToggle open={open} setOpen={setOpen} selected={selected} />
      </div>
      <div id="archetype-flags" hidden={!open} className="mt-6">
        {open && <FlagPanel flags={flags} selected={selected} toggle={toggle} clear={() => setSelected([])} count={shown.length} />}
      </div>
      <FindArchetype value={query} onChange={changeQuery} />
      <div className="mt-8">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-3" data-testid="types-grid">
          {shown.map((a, i) => <ArchetypeCard key={a.slug} a={a} i={i} directorySearch={search} highlight={selected} />)}
        </div>
        {!shown.length && types.length > 0 && <p className="card p-6 text-[var(--muted)]" data-testid="types-empty">No matching archetype. Try a shorter search or remove a flag.</p>}
      </div>
    </div>
  );
}
