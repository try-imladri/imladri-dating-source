import { Component, lazy, Suspense } from "react";
import { researchTool, researchToolPages } from "./adminResearchNavigation";

// Only JavaScript modules are shared here. Member data and authorization stay
// in the existing shell/page loaders and are never cached by navigation.
const loaders = {
  dating: () => import("@/pages/dating/DatingApp"),
  legacy: () => import("@/pages/legacy/LegacyHub"),
  legacyPlans: () => import("@/pages/legacy/LegacyPlans"),
  learn: () => import("@/pages/public/Learn"),
  plans: () => import("@/pages/public/Plans"),
  types: () => import("@/pages/public/Types"),
  archetype: () => import("@/pages/public/ArchetypePage"),
  figures: () => import("@/pages/public/Figures"),
  figure: () => import("@/pages/public/FigurePage"),
  waitlist: () => import("@/pages/public/Waitlist"),
  static: () => import("@/pages/public/StaticPages"),
  support: () => import("@/pages/public/Support"),
  overview: () => import("@/pages/app/Overview"),
  people: () => import("@/pages/app/Dashboard"),
  personal: () => import("@/pages/app/Personal"),
  personality: () => import("@/pages/app/PersonalityPage"),
  intimacy: () => import("@/pages/app/IntimacyPage"),
  yourType: () => import("@/pages/app/YourTypePage"),
  person: () => import("@/pages/app/PersonPage"),
  settings: () => import("@/pages/app/Settings"),
  upgrade: () => import("@/pages/app/Upgrade"),
  start: () => import("@/pages/app/Start"),
  quickAdvice: () => import("@/pages/app/QuickAdvice"),
  practice: () => import("@/pages/app/Practice"),
  couples: () => import("@/pages/app/Couples"),
  couplesPartner: () => import("@/pages/app/CouplesPartner"),
  couplesAnswers: () => import("@/pages/app/CouplesAnswers"),
  couplesPersonal: () => import("@/pages/app/CouplesPersonal"),
  couplesCompatibility: () => import("@/pages/app/CouplesCompatibility"),
  couplesTogether: () => import("@/pages/app/CouplesTogether"),
  admin: () => import("@/pages/admin/Admin"),
  adminDating: () => import("@/pages/admin/DatingAdmin"),
  adminMetrics: () => import("@/pages/admin/AdminMetrics"),
  adminWorkspace: () => import("@/pages/admin/WorkspaceTrends"),
  adminActivity: () => import("@/pages/admin/ActivityFeed"),
  adminResearch: () => import("@/pages/admin/ResearchTrends"),
  adminMembers: () => import("@/pages/admin/MembersAdmin"),
  adminPrompts: () => import("@/pages/admin/PromptLibrary"),
  adminQuiz: () => import("@/pages/admin/QuizCatalog"),
  adminEverydayQuiz: () => import("@/pages/admin/EverydayQuizEditor"),
  adminCatalogEditor: () => import("@/pages/admin/CatalogArticleEditor"),
  adminFigureEvidence: () => import("@/pages/admin/FigureEvidence"),
  adminNotifications: () => import("@/pages/admin/NotificationsAdmin"),
  adminSupport: () => import("@/pages/admin/SupportInbox"),
  researchConsent: () => import("@/pages/admin/ResearchNoticeSummary"),
  researchYourType: () => import("@/pages/admin/YourTypeResearch"),
  researchTone: () => import("@/pages/admin/ToneResearch"),
  researchExports: () => import("@/pages/admin/ResearchRecordExports"),
  researchEvidence: () => import("@/pages/admin/AssessmentEvidence"),
  researchProspective: () => import("@/pages/admin/ProspectiveResearch"),
  researchIndependent: () => import("@/pages/admin/IndependentReview"),
  researchRehearsal: () => import("@/pages/admin/RehearsalReview"),
  researchMigration: () => import("@/pages/admin/DatabaseMigration"),
};
const pending = new Map();
function load(key) {
  if (!pending.has(key)) {
    const request = loaders[key]().catch(error => { pending.delete(key); throw error; });
    pending.set(key, request);
  }
  return pending.get(key);
}

class RouteLoadBoundary extends Component {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? <section role="alert" className="p-5 space-y-3">
      <p>This page could not load. Reload to try again.</p>
      <button type="button" className="btn-ghost px-4 py-2" onClick={() => window.location.reload()}>Reload page</button>
    </section> : this.props.children;
  }
}

export function deferredPage(key, name = "default") {
  const Page = lazy(() => load(key).then(module => ({ default: module[name] })));
  return function DeferredRoute(props) {
    return <RouteLoadBoundary><Suspense fallback={<p role="status" className="p-5 text-sm text-[var(--muted)]">Loading page…</p>}>
      <Page {...props} />
    </Suspense></RouteLoadBoundary>;
  };
}

export function routePageKeys(destination) {
  const path = destination.split(/[?#]/)[0].replace(/\/$/, "") || "/";
  if (path === "/dating" || path.startsWith("/dating/")) return ["dating"];
  if (path === "/legacy") return ["legacy"];
  if (path === "/legacy/plans") return ["legacyPlans"];
  if (path === "/legacy/how-it-works") return ["static"];
  if (path === "/learn") return ["learn"];
  if (path === "/admin" || path.startsWith("/admin/")) {
    const params = new URLSearchParams(destination.split("?")[1]?.split("#")[0] || "");
    const view = params.get("view");
    const section = path.slice("/admin/".length);
    const keys = {
      "": ["adminMetrics"], dashboard: ["adminMetrics"],
      dating: ["adminDating"],
      activity: [view === "live" ? "adminActivity" : "adminWorkspace"], couples: ["adminWorkspace"],
      research: ["adminResearch"], users: ["adminMembers"], prompts: ["adminPrompts"],
      quiz: ["adminQuiz", "adminEverydayQuiz"], notifications: ["adminNotifications"], support: ["adminSupport"],
    }[section] || [];
    const toolPage = section === "research" && researchToolPages[researchTool(params.get("tool") || destination.split("#")[1])];
    return ["admin", ...keys, ...(toolPage ? [toolPage] : [])];
  }
  if (path.startsWith("/app/couples")) {
    const section = path.slice("/app/couples".length).replace(/^\//, "");
    const key = { personal: "couplesPersonal", personality: "personal", intimacy: "intimacy", answers: "couplesAnswers", "quick-advice": "quickAdvice",
      compatibility: "couplesCompatibility", together: "couplesTogether", agreements: "couplesTogether", "what-works": "couplesTogether",
      partner: "couplesPartner", join: "couplesPartner" }[section];
    return key ? ["couples", key] : ["couples"];
  }
  if (path.startsWith("/app/person/")) return ["person"];
  if (path.startsWith("/types/")) return ["archetype"];
  if (path.startsWith("/figures/")) return ["figure"];
  const key = { "/plans": "plans", "/types": "types", "/figures": "figures", "/waitlist": "waitlist", "/support": "support",
    "/about": "static", "/privacy": "static", "/terms": "static", "/billing": "static", "/how-it-works": "static",
    "/app": "overview", "/app/people": "people", "/app/personal": "personal", "/app/personality": "personal", "/app/quick-advice": "quickAdvice",
    "/app/intimacy": "intimacy", "/app/your-type": "yourType", "/app/settings": "settings", "/app/start": "start",
    "/app/practice": "practice", "/app/upgrade": "upgrade", "/app/settings/upgrade": "upgrade", "/couples/invite": "couplesPartner" }[path];
  return key ? [key] : [];
}

export function preloadRoute(destination) {
  return Promise.all(routePageKeys(destination).map(key => load(key))).catch(() => undefined);
}

export function routePreloadEvents(destination) {
  const preload = () => { void preloadRoute(destination); };
  return { onPointerEnter: preload, onFocus: preload, onTouchStart: preload };
}

export function warmNavigation(destinations) {
  let stopped = false, timer;
  const jobs = [...new Set(destinations.flatMap(routePageKeys))];
  const schedule = () => {
    if (stopped || !jobs.length) return;
    timer = setTimeout(() => {
      if (stopped) return;
      void load(jobs.shift()).catch(() => undefined).finally(schedule);
    }, 500);
  };
  schedule();
  return () => { stopped = true; clearTimeout(timer); };
}
