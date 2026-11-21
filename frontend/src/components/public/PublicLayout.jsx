import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { Show } from "@clerk/react";
import { PublicSessionAction } from "@/components/auth/ClerkAuthProvider";
import { publicApi, setOrigin } from "@/lib/api";
import { routePreloadEvents } from "@/lib/routePages";
import { usePublicPageHead } from "@/pages/public/publicHead";
import { ThemeToggle } from "@/lib/theme";
import { focusPublicSection } from "./publicNavigation";
import { BrandLogo } from "@/components/BrandLogo";
import { LANDING_SUMMARY } from "./landingCopy";
import "./StartPrompt.css";
import "./PublicLayout.css";

const PublicConfig = createContext({});
export const useConfig = () => useContext(PublicConfig);
export const signupPath = (_config, origin) => "/dating?origin=" + encodeURIComponent(origin || "navigation");

const navCls = ({ isActive }) =>
  `px-2 min-[520px]:px-3 py-1.5 rounded-full text-sm transition-colors ${isActive ? "text-[var(--lime)]" : "text-[var(--muted)] hover:text-[var(--ink)]"}`;

function Header() {
  const config = useConfig();
  const { pathname } = useLocation();
  const section = pathname.split("/")[1];
  const origin = !section ? "home" : ["plans", "types", "figures", "how-it-works", "about", "billing", "support", "terms", "privacy"].includes(section) ? section : "navigation";
  const actionClass = "btn-lime h-11 px-3 min-[520px]:px-5 inline-flex items-center text-sm whitespace-nowrap";
  const startLink = <Link to={signupPath(config, origin)} onClick={() => setOrigin(origin)}
    className={actionClass} data-testid="nav-signup">
    Join dating
  </Link>;
  return (
    <header className="public-header sticky top-0 z-40 backdrop-blur-xl glass border-b border-[var(--line)]">
      <div className="public-header__inner px-2 min-[520px]:px-5 sm:px-8 lg:px-12 grid grid-cols-[1fr_auto] items-center gap-y-1 py-1 min-[380px]:flex min-[380px]:gap-x-1 min-[380px]:py-0 min-[380px]:h-14">
        <Link to="/" data-testid="nav-logo" className="inline-flex shrink-0 items-center">
          <BrandLogo />
        </Link>
        <nav aria-label="Main navigation" className="public-header__nav row-start-2 col-span-2 flex items-center justify-between gap-1 min-[380px]:ml-auto">
          <NavLink to="/learn" {...routePreloadEvents("/learn")} className={navCls} data-testid="nav-learn">Learn</NavLink>
          <NavLink to="/plans" {...routePreloadEvents("/plans")} className={navCls} data-testid="nav-plans">Plans</NavLink>
          <PublicSessionAction fallback={startLink}>
            <Show when="signed-in">
              <Link to="/dating" className={actionClass} data-testid="nav-open-app">Open dating</Link>
            </Show>
            <Show when="signed-out">{startLink}</Show>
          </PublicSessionAction>
        </nav>
        <div className="row-start-1 col-start-2 justify-self-end"><ThemeToggle /></div>
      </div>
    </header>
  );
}

function Footer() {
  const { tiktok_url } = useConfig();
  const { pathname, hash } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const tiktokUrl = tiktok_url?.trim();
  useEffect(() => { setMenuOpen(false); }, [pathname, hash]);
  return (
    <footer className="public-footer border-t border-[var(--line)]" data-testid="site-footer">
      <div className="public-footer__inner">
        <div className="public-footer__brand">
          <Link to="/" aria-label="Imladri home" className="public-footer__logo" data-testid="footer-logo"><BrandLogo /></Link>
          <p className="public-footer__summary" data-testid="footer-summary">{LANDING_SUMMARY}</p>
        </div>
        <div className="public-footer__menu">
          <button type="button" className="public-footer__menu-toggle" aria-expanded={menuOpen}
            aria-controls="public-footer-links" onClick={() => setMenuOpen(open => !open)} data-testid="footer-menu-toggle">
            Explore &amp; support <span aria-hidden="true">{menuOpen ? "−" : "+"}</span>
          </button>
          <nav id="public-footer-links" aria-label="Explore and support" className={`public-footer__links${menuOpen ? " is-open" : ""}`} data-testid="footer-links">
            <Link to="/learn" data-testid="footer-learn">Learn</Link>
            <Link to="/how-it-works" data-testid="footer-how">How it works</Link>
            <Link to="/about" data-testid="footer-about">About</Link>
            <Link to="/legacy" data-testid="footer-legacy">Private tools</Link>
            <Link to="/privacy" data-testid="footer-privacy-link">Privacy</Link>
            <Link to="/terms" data-testid="footer-terms">Terms</Link>
            <Link to="/billing" data-testid="footer-billing">Billing &amp; refunds</Link>
            <Link to="/support" data-testid="footer-support">Contact support</Link>
            {tiktokUrl && !tiktokUrl.includes("[your-handle]") && (
              <a href={tiktokUrl} target="_blank" rel="noreferrer" data-testid="footer-tiktok">TikTok</a>
            )}
          </nav>
        </div>
      </div>
    </footer>
  );
}

export default function PublicLayout() {
  const { pathname, hash } = useLocation();
  const previousPath = useRef(pathname);
  usePublicPageHead(pathname, hash);
  const [config, setConfig] = useState({});
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    publicApi.get("/public/config", { signal: controller.signal }).then(
      r => { if (!controller.signal.aborted) setConfig(r.data); },
      () => { if (!controller.signal.aborted) setConfig({ configError: true }); }
    );
    return () => controller.abort();
  }, [attempt]);
  useEffect(() => {
    if (hash) focusPublicSection(hash.slice(1));
    else {
      if (pathname !== previousPath.current) document.getElementById("public-main")?.focus({preventScroll: true});
      window.scrollTo(0, 0);
    }
    previousPath.current = pathname;
  }, [pathname, hash]);
  return (
    <PublicConfig.Provider value={{ ...config, retry: () => { setConfig({}); setAttempt(n => n + 1); } }}><div className={`public-layout${pathname === "/" ? " public-layout--home" : ""}`}>
      <a href="#public-main" onClick={event => {
        event.preventDefault();
        focusPublicSection("public-main");
      }} className="sr-only focus:not-sr-only focus:fixed focus:top-1 focus:left-2 focus:z-[100] focus:px-4 focus:py-2 focus:bg-[var(--bg)]">Skip to content</a>
      <Header />
      <main id="public-main" tabIndex={-1} className="public-layout__main relative z-10"><Outlet /></main>
      <Footer />
    </div></PublicConfig.Provider>
  );
}
