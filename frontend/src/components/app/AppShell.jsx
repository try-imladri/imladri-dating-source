import { endBrowserSession, recordMemberVisit } from "@/lib/browserSession";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, NavLink, Outlet, useLocation, useMatch } from "react-router-dom";
import { Show, useAuth, useClerk } from "@clerk/react";
import { Activity, BarChart3, Users, Settings as Cog, Sparkles, Shield, Menu, LogOut, LayoutGrid, BookOpen, Star, MessageSquareText, ListChecks, Megaphone, UserRound, Drama, NotebookPen, MessagesSquare, Handshake, Bookmark, Link2, Heart, HeartHandshake } from "lucide-react";
import { clearCouplesInvite, pendingCouplesInvite } from "@/lib/couplesInvite";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { NotificationBell } from "@/components/app/NotificationBell";
import { useApi, applySignupOrigin } from "@/lib/api";
import { ThemeToggle } from "@/lib/theme";
import { routePreloadEvents, warmNavigation } from "@/lib/routePages";
import { beginEntryRead } from "@/lib/entryRead";
import { clearYourTypePendingExcept } from "@/components/app/yourTypeStorage";
import { isComplimentary, isIntroductory } from "@/components/app/PlanUsage";
import { BrandLogo } from "@/components/BrandLogo";
import ResearchFirstUse from "@/components/app/ResearchFirstUse";
import { couplesOnly, pairedProductRedirect, personalSectionRedirect } from "@/lib/workspaceAccess";

const sideCls = ({ isActive }) => `flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm transition-colors ${isActive ? "bg-[var(--ink)] text-[var(--bg)] font-semibold" : "text-[var(--muted)] hover:text-[var(--ink)] hover:bg-[var(--surface)]"}`;

const INDIVIDUAL_LINKS = [
  { to: "/app", end: true, label: "Overview", I: LayoutGrid, id: "overview" },
  { to: "/app/personal", label: "Personal", I: UserRound, id: "personal" },
  { to: "/app/intimacy", label: "Sex & Intimacy", I: HeartHandshake, id: "intimacy" },
  { to: "/app/your-type", label: "Your Type", I: Heart, id: "your-type" },
  { to: "/app/people", label: "People", I: Users, id: "people" },
  { to: "/app/quick-advice", label: "Quick Advice", I: MessageSquareText, id: "quick-advice" },
  { to: "/app/practice", label: "Practice", I: Drama, id: "practice" },
  { to: "/app/settings/upgrade", label: "Plans", I: Sparkles, id: "plans" },
  { to: "/app/settings", end: true, label: "Settings", I: Cog, id: "settings" },
];

const COUPLES_LINKS = [
  { to: "/app/couples", end: true, label: "Overview", I: LayoutGrid, id: "couples-overview" },
  { to: "/app/couples/personal", label: "My assessments", I: UserRound, id: "couples-personal" },
  { to: "/app/couples/intimacy", label: "Sex & Intimacy", I: HeartHandshake, id: "couples-intimacy" },
  { to: "/app/couples/compatibility", label: "Compatibility", I: Heart, id: "couples-compatibility" },
  { to: "/app/couples/answers", label: "My answers", I: NotebookPen, id: "couples-answers" },
  { to: "/app/couples/quick-advice", label: "Quick Advice", I: MessageSquareText, id: "couples-quick-advice" },
  { to: "/app/couples/together", label: "Together", I: MessagesSquare, id: "couples-together" },
  { to: "/app/couples/agreements", label: "Agreements & check-ins", I: Handshake, id: "couples-agreements" },
  { to: "/app/couples/what-works", label: "What works for us", I: Bookmark, id: "couples-what-works" },
  { to: "/app/couples/partner", label: "Partner & settings", I: Link2, id: "couples-partner" },
  { to: "/app/couples/conversation-cards", label: "Conversation cards", I: MessageSquareText, id: "couples-cards" },
  { to: "/app/settings/upgrade", label: "Plans", I: Sparkles, id: "couples-plans" },
  { to: "/app/settings", end: true, label: "Settings & data", I: Cog, id: "couples-settings" },
];

function individualReturnPath(pathname, search) {
  // Remember navigation within a page, without replaying drafts or checkout callbacks.
  const params = new URLSearchParams(search);
  if (pathname.replace(/\/$/, "") === "/app" && params.get("workspace") === "individual") return "/app?workspace=individual";
  const panel = params.get("tab");
  if (pathname.replace(/\/$/, "") === "/app/personal") {
    const redirect = personalSectionRedirect(search);
    if (redirect) return redirect;
  }
  if (["basics", "deeper", "further", "personality", "patterns", "intimacy", "report", "history", "prepare", "memories", "profile", "saved"].includes(panel)) {
    return `${pathname}?tab=${panel}`;
  }
  const group = params.get("group");
  if (["romantic", "family", "friends", "work", "other"].includes(group)) return `${pathname}?group=${group}`;
  return pathname;
}

function WorkspaceSwitch({ couples, individualPath, couplesPath, onPick, paired = false }) {
  const active = couples ? "Couples" : "Individual";
  if (paired) return <div aria-label="Workspace" className="rounded-xl border border-[var(--line)] bg-[var(--surface)] p-1">
    <Link to={couplesPath} {...routePreloadEvents(couplesPath)} onClick={onPick} aria-current="true" data-testid="workspace-couples"
      className="block rounded-lg bg-[var(--ink)] px-2 py-1.5 text-center text-xs font-semibold text-[var(--bg)]">Couples</Link>
  </div>;
  return (
    <div role="group" aria-label="Workspace" className="grid grid-cols-2 gap-0.5 p-1 rounded-xl bg-[var(--surface)] border border-[var(--line)]">
      {[["Individual", individualPath], ["Couples", couplesPath]].map(([label, to]) => (
        <Link key={label} to={to} {...routePreloadEvents(to)} onClick={onPick} aria-current={label === active ? "true" : undefined}
          className={`rounded-lg px-2 py-1.5 text-center text-xs font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 ${label === active ? "bg-[var(--ink)] text-[var(--bg)]" : "text-[var(--muted)] hover:text-[var(--ink)]"}`}
          data-testid={`workspace-${label.toLowerCase()}`}>{label}</Link>
      ))}
    </div>
  );
}

const ADMIN_LINKS = [
  ["dating", "Dating & safety", Heart],
  ["dashboard", "Dashboard", Shield], ["activity", "Activity", Activity], ["couples", "Couples", Handshake], ["research", "Research", BarChart3],
  ["archetypes", "Archetypes", BookOpen], ["figures", "Public Figures", Star],
  ["prompts", "Prompts", MessageSquareText], ["quiz", "Quizzes", ListChecks],
  ["users", "Users & Membership", Users], ["notifications", "Notifications", Megaphone],
  ["support", "Support", MessageSquareText],
].map(([id, label, I]) => ({ to: id === "dashboard" ? "/admin" : `/admin/${id}`, end: true, label, I, id: `admin-${id}` }));

function Inner({ admin }) {
  const api = useApi();
  const { userId } = useAuth();
  const { pathname, search, hash } = useLocation();
  const initialPath = useRef(pathname);
  const livePath = useRef(pathname);
  livePath.current = pathname;
  const entryRead = useRef(null);
  const couples = Boolean(useMatch("/app/couples/*"));
  const focused = Boolean(useMatch("/app/start"));
  const currentPath = focused ? "/app/start" : pathname + search + hash;
  const lastIndividual = useRef("/app");
  const [me, setMe] = useState(null);
  const [menu, setMenu] = useState(false);
  const { signOut } = useClerk();
  const signOutMember = () => {
    clearYourTypePendingExcept(null);
    clearCouplesInvite();
    endBrowserSession();
    return signOut({ redirectUrl: "/" });
  };
  const [err, setErr] = useState(false);
  const refreshUsage = useCallback(() => api.get("/me").catch(() => {}), [api]);

  useEffect(() => {
    if (!admin && !couples) lastIndividual.current = individualReturnPath(pathname, search);
  }, [admin, couples, pathname, search]);

  useEffect(() => {
    let active = true;
    const interceptor = api.interceptors.response.use((response) => {
      if (!active) return response;
      const { usage, tier, personal_access, membership_plan_id, intro_access, workspace_access, billing_review } = response.data || {};
      const workspaceSnapshot = workspace_access && (response.config?.url === "/me" || /^\/couples(?:\/|$)/.test(response.config?.url || ""));
      const billingSnapshot = billing_review !== undefined && response.config?.url === "/me";
      if (usage || workspaceSnapshot || billingSnapshot) setMe((member) => member && {
        ...member,
        usage: usage ? response.config?.url === "/couples" ? { ...member.usage, ...usage } : usage : member.usage,
        tier: tier ?? member.tier,
        personal_access: personal_access ?? member.personal_access,
        membership_plan_id: membership_plan_id === undefined ? member.membership_plan_id : membership_plan_id,
        intro_access: intro_access === undefined ? member.intro_access : intro_access,
        workspace_access: workspaceSnapshot ? workspace_access : member.workspace_access,
        billing_review: billingSnapshot ? billing_review : member.billing_review,
      });
      return response;
    }, (error) => {
      if (active && error.config?.url !== "/me" && (error.response?.status === 402
        || error.response?.data?.detail?.code === "couples_workspace_required")) refreshUsage();
      return Promise.reject(error);
    });
    window.addEventListener("focus", refreshUsage);
    return () => {
      active = false;
      api.interceptors.response.eject(interceptor);
      window.removeEventListener("focus", refreshUsage);
    };
  }, [api, refreshUsage]);

  useEffect(() => {
    if (admin || me?.workspace_access?.version !== "workspace-access-v1") return undefined;
    const interval = setInterval(() => {
      if (document.visibilityState === "visible") refreshUsage();
    }, 20000);
    return () => clearInterval(interval);
  }, [admin, me?.workspace_access?.version, refreshUsage]);

  const periodEnd = me?.usage?.plan === "plus" && me.usage.period_verified && me.usage.period_end;
  useEffect(() => {
    if (!periodEnd) return undefined;
    let timer;
    const refreshAtPeriodEnd = () => {
      const remaining = periodEnd * 1000 - Date.now();
      if (remaining <= 0) refreshUsage();
      else timer = setTimeout(refreshAtPeriodEnd, Math.min(remaining, 2147483647));
    };
    refreshAtPeriodEnd();
    return () => clearTimeout(timer);
  }, [periodEnd, refreshUsage]);

  useEffect(() => {
    const request = new AbortController();
    setMe(null);
    setErr(false);
    const path = initialPath.current;
    const entry = admin ? null : beginEntryRead(api, userId, path, () => !request.signal.aborted && livePath.current === path);
    entryRead.current = entry;
    (async () => {
      try {
        let { data } = await api.get("/me", { signal: request.signal });
        if (request.signal.aborted) return;
        // Origin attribution belongs to this load too. A session change must
        // abort its write before an asynchronously obtained token can be used.
        const originApi = { patch: (path, body) => api.patch(path, body, { signal: request.signal }) };
        data = await applySignupOrigin(originApi, data);
        if (!request.signal.aborted) { entry?.authorize(data); setMe(data); }
      } catch (error) {
        entry?.discard();
        if (!request.signal.aborted) setErr(error.response?.status === 410 ? "closed" : "load");
      }
    })();
    return () => { request.abort(); entry?.discard(); };
  }, [api, userId, admin]);

  useEffect(() => {
    if (pathname !== initialPath.current) entryRead.current?.discard();
  }, [pathname]);

  const memberId = me?.user_id;
  const warmCouples = couples || couplesOnly(me);
  useEffect(() => {
    // Staff tools can be large. Their menu intent warms only the requested
    // section, without downloading every research/editor tool on entry.
    if (!memberId || admin) return undefined;
    return warmNavigation((warmCouples ? COUPLES_LINKS : INDIVIDUAL_LINKS).map(link => link.to));
  }, [memberId, admin, warmCouples]);
  useEffect(() => { if (memberId) clearYourTypePendingExcept(memberId); }, [memberId]);
  useEffect(() => {
    if (!memberId) return undefined;
    const record = () => recordMemberVisit(api, memberId).catch(() => {});
    record();
    window.addEventListener("focus", record);
    window.addEventListener("online", record);
    return () => {
      window.removeEventListener("focus", record);
      window.removeEventListener("online", record);
    };
  }, [api, memberId, pathname]);

  if (err === "closed" || err === "joining" || err === "join-failed") return (
    <section className="max-w-lg p-8 space-y-4">
      <h1 className="font-display text-3xl">Your Personal account is closed</h1>
      <p>You can reopen Personal with this sign-in. A new subscription is required to use the Individual workspace.</p>
      <button disabled={err === "joining"} className="btn-primary px-5 py-2" onClick={async () => {
        setErr("joining");
        try { await api.post("/me/rejoin"); window.location.replace("/app/start"); }
        catch { setErr("join-failed"); }
      }}>Start a new Personal profile</button>
      {err === "join-failed" && <p role="alert">Your profile could not be opened. Please try again.</p>}
      <Link to="/" className="block underline">Back to home</Link>
    </section>
  );
  if (err) return <p className="p-8 text-[var(--muted)]" data-testid="app-error">Couldn't load your account. Refresh to try again.</p>;
  if (!me) return <p className="p-8 font-mono text-sm text-[var(--muted)]" data-testid="app-loading">Loading…</p>;
  if (me.closing && pathname !== "/app/settings") return <Navigate to="/app/settings" replace />;
  // Defer workspace guards until the one-time welcome is reviewed. Exact
  // Settings remains accessible for withdrawal and other data controls.
  const renderWorkspace = () => {
  const paired = couplesOnly(me);
  const pairedRedirect = !admin && pairedProductRedirect(me, pathname, search);
  if (pairedRedirect) return <Navigate to={pairedRedirect} replace />;
  // The API already grants staff their own Individual access. The default sign-in
  // destination stays Admin; following an Individual link is an explicit choice.
  const defaultAdminEntry = ["/app", "/app/start"].includes(pathname.replace(/\/$/, ""))
    && new URLSearchParams(search).get("workspace") !== "individual";
  if (!admin && !paired && me.role === "admin" && !me.closing && defaultAdminEntry) return <Navigate to="/admin" replace />;
  if (!admin && !me.closing && (pathname === "/app" || pathname === "/app/start") && pendingCouplesInvite()) return <Navigate to="/app/couples/join" replace />;
  const personalAccess = me.personal_access ?? (me.usage?.plan === "plus" && me.usage.period_verified);
  const accountPages = ["/app/settings", "/app/settings/upgrade", "/app/upgrade"];
  if (!admin && !couples && !personalAccess && !accountPages.includes(pathname.replace(/\/$/, ""))) return <Navigate to="/app/settings/upgrade" replace />;
  if (!admin && me.role !== "admin" && !me.onboarded && !me.people_count && pathname === "/app") return <Navigate to="/app/start" replace />;
  if (admin && me.role !== "admin") return <Navigate to="/app" replace />;
  const nav = admin ? ADMIN_LINKS : couples || paired ? COUPLES_LINKS : INDIVIDUAL_LINKS
    .filter(link => personalAccess || accountPages.includes(link.to))
    .map(link => me.role === "admin" && link.id === "overview" ? { ...link, to: "/app?workspace=individual" } : link);
  const switchProps = {
    couples,
    paired,
    individualPath: couples ? lastIndividual.current : currentPath,
    couplesPath: couples ? currentPath : "/app/couples",
  };
  const billingReview = me.billing_review;
  const pendingPayment = Number.isSafeInteger(billingReview?.pending_payment_disputes) && billingReview.pending_payment_disputes > 0;
  const staffHold = Array.isArray(billingReview?.hold_scopes) && billingReview.hold_scopes.length > 0;
  const navItems = (onPick) => (
    <>
      <Link to="/dating" {...routePreloadEvents("/dating")} className={sideCls({ isActive: false })} onClick={onPick} data-testid="legacy-back-to-dating"><Heart className="w-4 h-4" />Imladri Dating</Link>
      {!admin && <Link to="/legacy" {...routePreloadEvents("/legacy")} className={`${sideCls({ isActive: false })} text-xs`} onClick={onPick}>Earlier tools</Link>}
      {admin && <Link to={couplesOnly(me) ? "/app/couples" : "/app/personal"} className={sideCls({ isActive: false })} onClick={onPick} data-testid="admin-view-individual"><UserRound className="w-4 h-4" />{couplesOnly(me) ? "View Couples" : "View Individual"}</Link>}
      {!admin && me.role === "admin" && <Link to="/admin" {...routePreloadEvents("/admin")} className={sideCls({ isActive: false })} onClick={onPick} data-testid="individual-view-admin"><Shield className="w-4 h-4" />Admin dashboard</Link>}
      {!admin && <div className="mb-3"><WorkspaceSwitch {...switchProps} onPick={onPick} /></div>}
      {nav.map(({ to, end, label, I, id }) => <NavLink key={to} to={to} {...routePreloadEvents(to)} end={end} onClick={onPick} className={sideCls} data-testid={`app-nav-${id}`}><I className="w-4 h-4" />{label}</NavLink>)}
      {!admin && !couples && (
        <div className="mt-auto mb-1 rounded-xl border border-[var(--line)] px-3 py-2.5" data-testid="interp-disclaimer">
          <p className="font-mono text-[10px] uppercase tracking-widest">Interpretation, not a diagnosis</p>
          <p className="text-[11px] text-[var(--muted)] mt-1 leading-snug">Every read is a best guess from what you share. Trust what they do.</p>
        </div>
      )}
      <button onClick={signOutMember} className={`${sideCls({ isActive: false })} ${admin || couples ? "mt-auto" : ""} w-full`} data-testid="app-nav-signout"><LogOut className="w-4 h-4" />Sign out</button>
    </>
  );

  return (
    <div className="min-h-screen">
      <header className={`${focused ? "min-h-14 flex-wrap gap-x-1.5 min-[400px]:gap-x-3 gap-y-1 px-3 py-2" : "sticky top-0 h-14 gap-3 px-4"} z-40 glass backdrop-blur-xl border-b border-[var(--line)] sm:px-8 lg:px-10 flex items-center`} data-testid="app-header">
        <Sheet open={menu} onOpenChange={setMenu}>
          <SheetTrigger asChild><button className={`lg:hidden w-9 h-9 rounded-full border border-[var(--line)] flex items-center justify-center ${focused ? "[@media(max-height:600px)]:w-auto [@media(max-height:600px)]:px-3" : ""}`} aria-label={admin ? "Menu" : "Open navigation and switch workspace"} data-testid="app-menu-button">
            <Menu className="w-4 h-4" aria-hidden="true" />
            {focused && <span className="hidden [@media(max-height:600px)]:inline pl-1.5 text-xs font-semibold">Individual</span>}
          </button></SheetTrigger>
          <SheetContent side="left" aria-describedby={undefined} className="w-64 overflow-y-auto p-5" data-testid="app-mobile-menu">
            <SheetTitle className="sr-only">Navigation</SheetTitle>
            <div className="mb-6 flex items-center gap-2 px-3"><BrandLogo />{admin && <span className="font-mono text-xs text-[var(--muted)]">admin</span>}</div>
            <nav aria-label="Main navigation" className="flex flex-col gap-1 min-h-[calc(100%-4rem)]">{navItems(() => setMenu(false))}</nav>
          </SheetContent>
        </Sheet>
        <Link to={admin ? "/admin" : "/legacy"} className={`flex shrink-0 items-center gap-2 ${focused ? "[@media(max-width:1023px)_and_(max-height:600px)]:hidden" : ""}`} data-testid="app-logo"><BrandLogo /><span className="font-mono text-xs text-[var(--muted)]">{admin ? "admin" : "earlier tools"}</span></Link>
        {focused && <div className="hidden lg:block ml-4 w-48"><WorkspaceSwitch {...switchProps} /></div>}
        <span className="flex-1" />
        {!admin && isIntroductory(me.usage) && <Link to="/app/settings/upgrade" className="text-xs text-[var(--muted)] underline decoration-dotted underline-offset-4" title="10 introductory AI messages shared across Individual and Couples. For an authorized card-first introduction, your selected plan is charged after message 10 is delivered. Open Plans to see your status or cancel before that charge." data-testid="intro-messages-left">Free messages: {me.usage.messages_remaining} of 10 remaining</Link>}
        {!admin && personalAccess && me.usage?.period_verified && !me.usage.unlimited && <Link to="/app/settings/upgrade" className={`${focused ? "max-[360px]:max-w-[4.5rem]" : ""} text-xs text-[var(--muted)] underline decoration-dotted underline-offset-4`} title="Available monthly and top-up messages across Individual and Couples" data-testid="replies-left">{me.usage.messages_remaining} messages left</Link>}
        {!admin && personalAccess && isComplimentary(me.usage) && <Link to="/app/settings/upgrade" className="text-xs text-[var(--muted)] underline decoration-dotted underline-offset-4" title="Complimentary Unlimited · no monthly message limit" aria-label="Complimentary Unlimited access" data-testid="complimentary-access">Unlimited</Link>}
        <NotificationBell key={couples ? "couples" : "individual"} api={api} couples={couples} />
        <ThemeToggle />
      </header>
      {!admin && (pendingPayment || staffHold) && <aside role="status" className="mx-4 mt-4 rounded-xl border border-[var(--line)] p-3 text-sm sm:mx-8 lg:mx-10" data-testid="billing-review-notice">
        <p>{pendingPayment ? "A payment is being reviewed. Messages tied to that payment may be temporarily unavailable." : "Some new actions are temporarily paused during billing review."} Your saved content, data controls and cancellation options remain available.</p>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-semibold">
          <Link to="/app/settings" className="underline">Settings &amp; data</Link>
          <Link to="/app/settings/upgrade" className="underline">Billing &amp; cancellation</Link>
          <Link to="/support?topic=Billing" className="underline">Billing support</Link>
        </div>
      </aside>}
      {!admin && <div className={`lg:hidden border-b border-[var(--line)] px-4 py-1.5 ${focused ? "[@media(max-height:600px)]:hidden" : ""}`} data-testid="workspace-mobile"><div className="max-w-xs mx-auto"><WorkspaceSwitch {...switchProps} /></div></div>}
      {focused ? <main className="px-4 sm:px-8 py-6 [@media(max-height:600px)]:py-1.5"><Outlet context={{ me, setMe, api, refreshUsage }} /></main> : (
      <div className="lg:grid lg:grid-cols-[220px_1fr]">
        <aside className="hidden lg:block sticky top-14 h-[calc(100vh-3.5rem)] overflow-y-auto border-r border-[var(--line)] p-4" data-testid="app-sidebar">
          <nav aria-label="Main navigation" className="flex flex-col gap-1 min-h-full">{navItems()}</nav>
        </aside>
        <main className="px-4 sm:px-8 lg:px-10 py-6 min-w-0"><Outlet context={{ me, setMe, api, refreshUsage }} /></main>
      </div>
      )}
    </div>
  );
  };
  return <ResearchFirstUse api={api} me={me} setMe={setMe} bypass={admin || pathname.replace(/\/$/, "") === "/app/settings"}>{renderWorkspace}</ResearchFirstUse>;
}

export default function AppShell({ admin = false }) {
  const { isLoaded, userId, sessionId } = useAuth();
  return (
    <>
      <Show when="signed-in">{isLoaded && userId && <Inner key={`${userId}:${sessionId || ""}:${admin}`} admin={admin} />}</Show>
      <Show when="signed-out"><SignedOutRedirect /></Show>
    </>
  );
}

function SignedOutRedirect() {
  useEffect(() => { clearYourTypePendingExcept(null); }, []);
  return <Navigate to="/sign-in" replace />;
}
