import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Share2, ArrowRight, ArrowLeft, ChevronLeft, ChevronRight } from "lucide-react";
import { toast } from "sonner";
import { BACKEND_URL, pad, setOrigin } from "@/lib/api";
import { useConfig } from "@/components/public/PublicLayout";
import { ArchIcon } from "@/components/public/ArchIcon";
import { focusPublicSection } from "./publicNavigation";

export const mediaUrl = (u) => (u?.startsWith("/") ? `${BACKEND_URL}${u}` : u || null);
export const PAGE_X = "px-5 sm:px-8 lg:px-12";

export function ArchetypeCard({ a, i = 0, highlight = [], directorySearch }) {
  if (a.unlocked === false) {
    return (
      <div className="card p-6 min-h-[210px] flex flex-col justify-between text-[var(--muted)]" data-testid={`archetype-card-${a.number}`}>
        <p className="font-mono text-xs">#{pad(a.number)}</p>
        <p className="font-display font-black text-3xl uppercase">Not available yet</p>
      </div>
    );
  }
  const hits = (a.red_flag_tags || []).filter((f) => highlight.includes(f));
  return (
    <Link to={`/types/${a.slug}`} state={directorySearch ? { directorySearch } : undefined} className="card p-6 relative overflow-hidden group rise hover:border-[var(--ink)] transition-colors min-h-[210px] flex flex-col" style={{ animationDelay: `${Math.min(i * 25, 300)}ms` }} data-testid={`archetype-card-${a.number}`}>
      <span aria-hidden="true" className="num-watermark absolute -right-1 -bottom-3 text-[6.5rem] group-hover:[-webkit-text-stroke:1px_var(--ink)] transition-all">{pad(a.number)}</span>
      <span className="absolute top-4 right-4 text-[var(--line)] group-hover:text-[var(--ink)] transition-colors" data-testid="card-icon"><ArchIcon number={a.number} className="w-20 h-20" stroke={1} /></span>
      <p className="font-mono text-xs text-[var(--muted)] relative">#{pad(a.number)}</p>
      <p className="font-display font-black text-3xl uppercase leading-none mt-auto pt-6 relative pr-24">{a.name}</p>
      <p className="text-sm text-[var(--muted)] mt-2 relative pr-24">{a.one_liner}</p>
      {hits.length > 0 && <div className="flex flex-wrap gap-1 mt-3 relative pr-24">{hits.map((h) => <span key={h} className="text-[10px] px-2 py-0.5 rounded-full bg-red-600 text-white">{h}</span>)}</div>}
    </Link>
  );
}

export function Bubbles({ items = [] }) {
  return (
    <div className="flex flex-col gap-2" data-testid="chat-bubbles">
      {items.map((b, i) => (
        <div key={i} className={`relative px-4 py-2.5 text-[15px] max-w-[82%] ${b.from === "you" ? "bubble-you self-end" : "bubble-him self-start"}`}>
          <span className="sr-only">{b.from === "you" ? "You said " : "They said "}</span>{b.text}
        </div>
      ))}
    </div>
  );
}

export function Scroller({ children, testid, label }) {
  const ref = useRef(null);
  const [canScroll, setCanScroll] = useState(true);
  useEffect(() => {
    const el = ref.current;
    const check = () => setCanScroll(el.scrollWidth > el.clientWidth + 4);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const by = (d) => ref.current.scrollBy({ left: d * ref.current.clientWidth * 0.8, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  const Btn = ({ d, I }) => (
    <button onClick={() => by(d)} aria-label={d < 0 ? "Previous" : "Next"} data-testid={`${testid}-${d < 0 ? "prev" : "next"}`}
      className="w-10 h-10 rounded-full border border-[var(--line)] flex items-center justify-center hover:bg-[var(--ink)] hover:text-[var(--bg)] transition-colors"><I className="w-4 h-4" /></button>
  );
  return (
    <div>
      {canScroll && <div className="flex justify-end gap-2 mb-3"><Btn d={-1} I={ChevronLeft} /><Btn d={1} I={ChevronRight} /></div>}
      <div ref={ref} role="region" aria-label={label} tabIndex={canScroll ? 0 : undefined} className="flex gap-4 overflow-x-auto snap-x snap-mandatory no-scrollbar pb-2 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2" data-testid={testid}>{children}</div>
    </div>
  );
}

export function CharacterRow({ items = [] }) {
  return (
    <Scroller testid="characters-scroller" label="Character examples">
      {items.map((c, i) => (
        <div key={i} className="snap-start shrink-0 w-[70%] sm:w-[45%] lg:w-[calc(25%-12px)] card overflow-hidden" data-testid={`character-card-${i}`}>
          <div className="aspect-[4/5] bg-[var(--surface-2)] overflow-hidden">
            {c.image_url ? <img src={mediaUrl(c.image_url)} alt={c.name} loading="lazy" className="w-full h-full object-cover object-top" />
              : <div className="w-full h-full flex items-center justify-center font-display font-black text-7xl text-[var(--muted)]">{c.name?.[0]}</div>}
          </div>
          <div className="p-4">
            <p className="font-display font-black uppercase text-2xl leading-tight">{c.name}</p>
            <p className="font-mono text-[10px] uppercase text-[var(--muted)] mt-1">{c.show}{c.actor ? ` · played by ${c.actor}` : ""}</p>
            <p className="text-sm mt-2 opacity-80">{c.why}</p>
          </div>
        </div>
      ))}
    </Scroller>
  );
}

export function Threads({ threads = [] }) {
  return (
    <Scroller testid="texting-threads" label="Texting examples">
      {threads.map((t, i) => (
        <div key={i} className="snap-start shrink-0 w-[88%] md:w-[48%] xl:w-[calc(33.333%-11px)] card p-5" data-testid={`thread-${i}`}>
          <p className="font-mono text-[10px] uppercase text-[var(--muted)]">{pad(i + 1)} · {t.title}</p>
          {t.note && <p className="text-xs mt-1 mb-4 opacity-70">{t.note}</p>}
          <Bubbles items={t.bubbles} />
        </div>
      ))}
    </Scroller>
  );
}

export function Paras({ v }) {
  const list = Array.isArray(v) ? v : String(v || "").split(/\n\n+/);
  return <div className={`text-[17px] leading-relaxed ${list.length > 1 ? "xl:columns-2 gap-12" : ""}`}>{list.map((p, i) => <p key={i} className="mb-4 break-inside-avoid">{p}</p>)}</div>;
}

export function Item({ it }) {
  if (typeof it === "string") return <p>{it}</p>;
  const title = it.point || it.line || it.name;
  const body = it.detail || it.why || it.how_it_works;
  return (
    <div>
      <p className="font-semibold">{it.line ? `“${title}”` : title}</p>
      {body && <p className="text-sm opacity-75 mt-1.5 leading-relaxed">{body}</p>}
      {it.defense && <p className="text-sm mt-3 pt-3 border-t border-current/20"><span className="font-mono text-[10px] uppercase tracking-widest">Your defense — </span>{it.defense}</p>}
    </div>
  );
}

const BOX = {
  ink: "card",
  red: "rounded-2xl border bg-red-50 border-red-200 text-red-950 dark:bg-red-950/50 dark:border-red-900 dark:text-red-50",
  green: "rounded-2xl border bg-emerald-50 border-emerald-200 text-emerald-950 dark:bg-emerald-950/50 dark:border-emerald-900 dark:text-emerald-50",
};
const HEAD = {
  ink: "bg-[var(--ink)] text-[var(--bg)]",
  red: "bg-red-600 text-white",
  green: "bg-emerald-700 text-white",
};

export function CardGrid({ items = [], tone = "ink", cols = "sm:grid-cols-2 xl:grid-cols-3" }) {
  return (
    <div className={`grid gap-3 ${cols}`}>
      {items.map((it, i) => (
        <div key={i} className={`p-5 ${BOX[tone]}`} data-testid={`item-${tone}`}><Item it={it} /></div>
      ))}
    </div>
  );
}

export function TwoCol({ left, right, lt, rt, ltone = "green", rtone = "red" }) {
  return (
    <div className="grid lg:grid-cols-2 gap-6">
      <div><p className={`inline-block font-mono text-xs uppercase mb-3 tracking-widest px-3 py-1 rounded-full ${HEAD[ltone]}`}>{lt}</p><CardGrid items={left} tone={ltone} cols="" /></div>
      <div><p className={`inline-block font-mono text-xs uppercase mb-3 tracking-widest px-3 py-1 rounded-full ${HEAD[rtone]}`}>{rt}</p><CardGrid items={right} tone={rtone} cols="" /></div>
    </div>
  );
}

export function Section({ id, n, title, children }) {
  return (
    <section id={id} className="py-12 border-t border-[var(--line)] scroll-mt-24" data-testid={`sec-${id}`}>
      <p className="eyebrow">{pad(n)}</p>
      <h2 className="font-display font-black uppercase text-4xl sm:text-5xl mt-2 mb-6">{title}</h2>
      {children}
    </section>
  );
}

export function BackLink({ to, label }) {
  return (
    <Link to={to} className="inline-flex items-center gap-1.5 text-sm font-semibold hover:opacity-70 transition-opacity" data-testid="back-link">
      <ArrowLeft className="w-4 h-4" />{label}
    </Link>
  );
}

// Long-read layout: sticky side table of contents tracking the section in view;
// once the hero (#doc-hero) scrolls away, its title and share button merge into the sidebar.
export function DocLayout({ toc, head, children }) {
  const { hash } = useLocation();
  const [active, setActive] = useState(toc[0]?.id);
  const [compact, setCompact] = useState(false);
  // The article arrives after navigation, so the browser may miss the initial fragment.
  useEffect(() => {
    if (hash) focusPublicSection(hash.slice(1));
  }, [hash]);
  useEffect(() => {
    const obs = new IntersectionObserver((es) => {
      es.forEach((e) => {
        if (e.isIntersecting) setActive(e.target.id);
      });
    }, { rootMargin: "-25% 0px -65% 0px" });
    toc.forEach((t) => {
      const el = document.getElementById(t.id);
      if (el) obs.observe(el);
    });
    const hero = document.getElementById("doc-hero");
    const hobs = new IntersectionObserver(([e]) => setCompact(!e.isIntersecting), { rootMargin: "-56px 0px 0px 0px" });
    if (hero) hobs.observe(hero);
    return () => {
      obs.disconnect();
      hobs.disconnect();
    };
  }, [toc]);
  const idx = Math.max(0, toc.findIndex((t) => t.id === active));
  return (
    <div className={`grid lg:grid-cols-[250px_1fr] xl:grid-cols-[290px_1fr] gap-8 lg:gap-14 ${PAGE_X}`}>
      <aside className="hidden lg:block">
        <nav aria-label="On this page" className="sticky top-20 py-2" data-testid="doc-sidebar">
          <div inert={!compact} className={`overflow-hidden transition-all duration-500 ease-out ${compact ? "max-h-96 opacity-100 translate-y-0" : "max-h-0 opacity-0 -translate-y-3"}`} data-testid="doc-compact-head">
            <BackLink {...head.back} />
            <div className="flex items-center gap-3 mt-5">
              <div className="min-w-0 flex-1">
                <p className="eyebrow">{head.eyebrow}</p>
                <p className="font-display font-black uppercase text-3xl leading-none mt-2">{head.title}</p>
                <div className="mt-3"><ShareButton title={head.title} /></div>
              </div>
              {head.photo && <img src={head.photo} alt={head.title} className="w-20 h-24 xl:w-24 xl:h-28 rounded-xl object-cover object-top shrink-0" data-testid="doc-compact-photo" />}
            </div>
          </div>
          <p className={`eyebrow mb-4 ${compact ? "mt-6 pt-5 border-t border-[var(--line)]" : ""}`}>On this page · {idx + 1}/{toc.length}</p>
          <div className="relative border-l border-[var(--line)]">
            {toc.map((t) => (
              <a key={t.id} href={`#${t.id}`} aria-current={active === t.id ? "location" : undefined} data-testid={`toc-${t.id}`}
                className={`block w-full text-left pl-4 py-2 text-sm -ml-px border-l-2 transition-colors ${active === t.id ? "border-[var(--ink)] font-semibold text-[var(--ink)]" : "border-transparent text-[var(--muted)] hover:text-[var(--ink)]"}`}>{t.label}</a>
            ))}
          </div>
        </nav>
      </aside>
      <nav aria-label="On this page" className="lg:hidden sticky top-14 z-30 -mx-5 px-5 py-2 glass backdrop-blur-xl border-b border-[var(--line)] flex items-center gap-2 overflow-x-auto no-scrollbar" data-testid="doc-topbar">
        <Link to={head.back.to} className="shrink-0 w-8 h-8 rounded-full border border-[var(--line)] flex items-center justify-center" aria-label={head.back.label} data-testid="back-link-mobile"><ArrowLeft className="w-4 h-4" /></Link>
        {toc.map((t) => (
          <a key={t.id} href={`#${t.id}`} aria-current={active === t.id ? "location" : undefined} className={`shrink-0 px-3 py-1 rounded-full text-xs border ${active === t.id ? "bg-[var(--ink)] text-[var(--bg)] border-[var(--ink)]" : "border-[var(--line)] text-[var(--muted)]"}`}>{t.label}</a>
        ))}
      </nav>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function ShareButton({ title }) {
  const [manualUrl, setManualUrl] = useState("");
  const linkField = useRef(null);
  useEffect(() => {
    if (manualUrl && linkField.current) {
      linkField.current.focus();
      linkField.current.select();
    }
  }, [manualUrl]);
  const share = async () => {
    const url = window.location.href;
    if (navigator.share) {
      try {
        await navigator.share({ title, url });
        return;
      } catch (error) {
        if (error?.name === "AbortError") return;
      }
    }
    try {
      await navigator.clipboard.writeText(url);
      toast.success("Link copied");
    } catch {
      setManualUrl(url);
      linkField.current?.select();
    }
  };
  return (
    <div>
      <button onClick={share} className="btn-ghost px-4 py-2 text-sm inline-flex items-center gap-2" data-testid="share-button">
        <Share2 className="w-4 h-4" /> Share
      </button>
      {manualUrl === window.location.href && <label className="block mt-2 text-sm">
        Copy link to this page
        <input ref={linkField} readOnly value={manualUrl} onFocus={e => e.target.select()}
          className="block mt-1 w-full max-w-sm rounded border border-[var(--line)] bg-[var(--bg)] px-2 py-1" />
      </label>}
    </div>
  );
}

export function CtaBridge({ text, sub, origin, button = "Make his profile" }) {
  const { waitlist_mode } = useConfig();
  const to = waitlist_mode ? `/waitlist?origin=${encodeURIComponent(origin)}` : "/sign-up";
  return (
    <section className="my-14 rounded-3xl p-8 sm:p-12 bg-[var(--ink)] text-[var(--bg)]" data-testid="cta-bridge">
      <p className="font-display font-black uppercase text-3xl sm:text-5xl leading-tight max-w-4xl">{text}</p>
      {sub && <p className="text-base sm:text-lg opacity-75 mt-4 max-w-2xl">{sub}</p>}
      <Link to={to} onClick={() => setOrigin(origin)} className="inline-flex items-center gap-2 px-7 py-3.5 mt-8 rounded-full bg-[var(--bg)] text-[var(--ink)] font-bold hover:opacity-90 transition-opacity" data-testid="cta-signup-button">
        {button} <ArrowRight className="w-4 h-4" />
      </Link>
    </section>
  );
}

export function Loading() {
  return <div className={`${PAGE_X} py-24 font-mono text-sm text-[var(--muted)]`} data-testid="loading">Loading…</div>;
}
