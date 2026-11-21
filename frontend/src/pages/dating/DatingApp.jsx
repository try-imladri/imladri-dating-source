import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth, useClerk } from "@clerk/react";
import { Compass, Heart, MessageCircle, UserRound, LogOut, Layers, ShieldCheck, Sparkles } from "lucide-react";
import { useApi } from "@/lib/api";
import { ThemeToggle } from "@/lib/theme";
import { BrandLogo } from "@/components/BrandLogo";
import { DatingContext, useDating, useDatingAction } from "@/components/dating/DatingContext";
import { ActionFeedback, ErrorState, Loading } from "@/components/dating/DatingUI";
import { datingError } from "@/lib/dating";
import { Discover, Likes } from "./Discovery";
import DatingProfile from "./DatingProfile";
import DatingMessages from "./DatingMessages";
import "./dating.css";

const NAV = [["/dating", "Discover", Compass], ["/dating/likes", "Likes", Heart], ["/dating/messages", "Messages", MessageCircle], ["/dating/profile", "Profile", UserRound]];

export default function DatingApp() {
  const { isLoaded, isSignedIn, userId, sessionId } = useAuth();
  if (!isLoaded) return <main className="dating-app"><Loading>Checking your account…</Loading></main>;
  if (!isSignedIn) return <Navigate to="/sign-in?return_to=%2Fdating" replace />;
  return <DatingSession key={`${userId}:${sessionId}`} identity={`${userId}:${sessionId}`} />;
}

function DatingSession({ identity }) {
  const api = useApi();
  const { signOut } = useClerk();
  const location = useLocation();
  const [data, setData] = useState(null), [error, setError] = useState("");
  const request = useRef(null), mounted = useRef(true);
  const refresh = useCallback(async () => {
    request.current?.abort(); const abort = new AbortController(); request.current = abort;
    try {
      const response = await api.get("/dating/bootstrap", { signal: abort.signal });
      if (mounted.current && !abort.signal.aborted) { setData(response.data); setError(""); }
      return response.data;
    } catch (failure) { if (mounted.current && !abort.signal.aborted) setError(datingError(failure)); }
  }, [api]);
  useEffect(() => { mounted.current = true; refresh(); return () => { mounted.current = false; request.current?.abort(); }; }, [refresh]);
  useEffect(() => { document.title = "Imladri · Intentional dating"; window.scrollTo?.(0, 0); }, [location.pathname]);
  const context = useMemo(() => ({ api, identity, data, refresh }), [api, identity, data, refresh]);
  return <DatingContext.Provider value={context}><div className="dating-app">
    <aside className="dating-sidebar"><Link to="/dating" className="dating-brand"><BrandLogo /><span>imladri<span>Meet with intention.</span></span></Link>
      <nav aria-label="Dating navigation" className="dating-nav">{NAV.map(([to, label, Icon]) => <NavLink to={to} end={to === "/dating"} key={to} className={({ isActive }) => isActive ? "is-active" : ""}><Icon size={21} strokeWidth={1.7} /><span>{label}</span>{label === "Likes" && data?.counts?.likes > 0 && <span className="dating-nav-count">{data.counts.likes}</span>}</NavLink>)}</nav>
      <div className="dating-sidebar-note"><span className="dating-accent-dot" /><p>A little more understanding.<br/>A worthwhile introduction.</p></div>
      <div className="dating-sidebar-footer"><Link to="/legacy"><Layers size={17} /> Earlier tools</Link><div><button onClick={() => signOut({ redirectUrl: "/" })}><LogOut size={16} /> Sign out</button></div></div>
    </aside>
    <div className="dating-main-wrap"><header className="dating-topbar"><Link to="/dating" className="dating-mobile-brand" aria-label="Imladri dating"><BrandLogo /><span>dating</span></Link><div className="dating-topbar-right"><span className="dating-beta-pill">Founding community</span><Link to="/dating/profile" className="dating-account-link"><UserRound size={18} /><span>{data?.profile?.display_name || data?.member?.nickname || "My profile"}</span></Link><div className="dating-mobile-account-tools"><ThemeToggle /><button onClick={() => signOut({ redirectUrl: "/" })} aria-label="Sign out"><LogOut size={17} /></button></div></div></header>
      <main id="dating-main" className="dating-main">{!data ? error ? <ErrorState error={error} retry={refresh} /> : <Loading /> : !data.enrollment ? <Enrollment /> : <>{error && <ErrorState error={error} retry={refresh} />}<Routes><Route index element={<Discover />} /><Route path="likes" element={<Likes />} /><Route path="messages" element={<DatingMessages />} /><Route path="messages/:matchId" element={<DatingMessages />} /><Route path="profile/*" element={<DatingProfile />} /><Route path="*" element={<Navigate to="/dating" replace />} /></Routes></>}</main>
      <footer className="dating-page-footer"><span>Real people. Independent choices.</span><Link to="/support">Contact support</Link><Link to="/privacy">Privacy</Link><Link to="/legacy">Earlier Imladri tools</Link></footer>
    </div>
    <nav className="dating-bottom-nav" aria-label="Dating mobile navigation">{NAV.map(([to, label, Icon]) => <NavLink to={to} end={to === "/dating"} key={to} className={({ isActive }) => isActive ? "is-active" : ""}><Icon size={22} strokeWidth={1.7} /><span>{label}</span></NavLink>)}</nav>
  </div></DatingContext.Provider>;
}

export function Enrollment() {
  const { api, data, refresh } = useDating();
  const action = useDatingAction();
  const [adult, setAdult] = useState(false), [accepted, setAccepted] = useState(false);
  const submit = e => { e.preventDefault(); if (!adult || !accepted) return; action.run(() => api.post("/dating/enrollment", { version: data.consent?.version || "dating-enrollment-v1", accepted: true, adult_confirmed: true }), refresh); };
  return <section className="dating-enrollment" data-testid="dating-enrollment"><div className="dating-enrollment-copy"><p className="dating-kicker">A new chapter for Imladri</p><h1>Someone you’re drawn to.<br/><em>Something worth exploring.</em></h1><p className="dating-enrollment-intro">Introductions that consider your attraction, your relationship intentions, and the preferences you choose to share.</p><div className="dating-feature-lines"><div><Heart size={22} /><span><strong>Your attraction</strong><span>Explore what catches your eye, with room for surprises.</span></span></div><div><Sparkles size={22} /><span><strong>What matters to you</strong><span>Understand everyday preferences and the differences worth discussing.</span></span></div><div><ShieldCheck size={22} /><span><strong>Your own pace</strong><span>Deeper answers are optional. You decide what becomes visible.</span></span></div></div><Link className="dating-text-button" to="/legacy">Looking for your earlier tools?</Link></div>
    <form className="dating-enrollment-form dating-panel" onSubmit={submit}><p className="dating-kicker">An intentional start</p><h2>Join the founding community</h2><p>We’re starting with {data.launch?.city || "one city"}. Your profile stays private until you create it, preview it and submit it for review.</p><div className="dating-note"><strong>A separate choice</strong><p>{data.consent?.notice || "Joining dating does not publish your earlier assessments, People notes, private conversations or Couples records. You choose which information to use and share."}</p></div><label className="dating-check"><input type="checkbox" checked={adult} onChange={e => setAdult(e.target.checked)} data-testid="dating-adult-checkbox" /><span>I am at least 18 years old.</span></label><label className="dating-check"><input type="checkbox" checked={accepted} onChange={e => setAccepted(e.target.checked)} data-testid="dating-enrollment-checkbox" /><span>I choose to join Imladri Dating under the notice above. My existing private information is not automatically published or reused.</span></label><ActionFeedback action={action} /><button type="submit" className="btn-lime dating-button dating-button--dark dating-button--full" disabled={!adult || !accepted || action.busy} data-testid="dating-enroll-button">{action.busy ? "Opening your profile…" : "Create my dating profile"}</button><p className="dating-fine">Founding beta access is free. No payment method is required to create a profile. <Link to="/terms">Terms</Link> and <Link to="/privacy">privacy details</Link>.</p></form>
  </section>;
}
