import { useCallback, useEffect, useState } from "react";
import { Navigate, useLocation, useOutletContext, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { deferredPage } from "@/lib/routePages";
import { SubTabs } from "@/pages/admin/SubTabs";
import { errMsg, pad } from "@/lib/api";
import { filterFigures } from "@/lib/figureSearch";

const SupportInbox = deferredPage("adminSupport");
const DatingAdmin = deferredPage("adminDating");
const CatalogArticleEditor = deferredPage("adminCatalogEditor");
const NotificationsAdmin = deferredPage("adminNotifications");
const MembersAdmin = deferredPage("adminMembers");
const PromptLibrary = deferredPage("adminPrompts");
const QuizCatalog = deferredPage("adminQuiz");
const FigureEvidence = deferredPage("adminFigureEvidence");
const EverydayQuizEditor = deferredPage("adminEverydayQuiz");
const AdminMetrics = deferredPage("adminMetrics");
const ResearchTrends = deferredPage("adminResearch");
const ActivityFeed = deferredPage("adminActivity", "ActivityFeed");
const WorkspaceTrends = deferredPage("adminWorkspace");
const CouplesReport = deferredPage("adminWorkspace", "CouplesReport");

const PANEL = "card relative flex-1 min-h-0 overflow-y-auto";

function ArchetypesAdmin({ api, notifyNew }) {
  const [list, setList] = useState([]);
  const [edit, setEdit] = useState(null);
  const [savingWave, setSavingWave] = useState(false);
  const [loadError,setLoadError]=useState(false);
  const load = useCallback(() => {setLoadError(false);return api.get("/admin/archetypes?summary=true").then((r) => setList(r.data)).catch(()=>setLoadError(true));}, [api]);
  useEffect(() => {
    load();
  }, [load]);
  const put = async (slug, body) => {
    try {
      await api.put(`/admin/archetypes/${slug}`, body);
      toast.success("Saved");
      setEdit(null);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  const wave = async (w, unlocked) => {
    setSavingWave(true);
    try {
      await api.post(`/admin/waves/${w}`, { unlocked, notify: unlocked && notifyNew });
      await load();
      toast.success(`Wave ${w} ${unlocked ? "released" : "locked"}`);
    } catch (e) { toast.error(errMsg(e)); }
    finally { setSavingWave(false); }
  };
  const waves = [...new Set(list.map((a) => a.wave))];
  const [g, setG] = useState("male");
  const shown = list.filter((a) => (a.gender === "female" ? "female" : "male") === g);
  return (
    <div className="flex flex-col gap-3 h-full min-h-0">
      <div className="flex flex-wrap items-center gap-3 shrink-0">
        <SubTabs value={g} onChange={setG} items={[["male", `Male (${list.filter((a) => a.gender !== "female").length})`], ["female", `Female (${list.filter((a) => a.gender === "female").length})`]]} testid="admin-arch-gender" />
        <div className="flex flex-wrap gap-1.5" data-testid="admin-waves">
        {waves.map((w) => {
          const all = list.filter((a) => a.wave === w).every((a) => a.unlocked);
          return <button key={w} disabled={savingWave} onClick={() => wave(w, !all)} className={`px-3 py-1.5 rounded-full text-xs border disabled:opacity-50 ${all ? "border-[var(--ink)] text-[var(--ink)]" : "border-[var(--line)] text-[var(--muted)]"}`} data-testid={`admin-wave-${w}`}>Wave {w}: {all ? "released — lock" : "locked — release"}</button>;
        })}
        </div>
      </div>
      {loadError && <button className="underline" onClick={load}>Archetypes couldn’t load. Retry</button>}
      <div className={`${PANEL} grid md:grid-cols-2 2xl:grid-cols-3 content-start gap-x-4 px-2`} data-testid="admin-content-table">
        {shown.map((a) => (
          <div key={a.slug} className="flex items-center gap-2.5 px-2 py-2 text-sm border-b border-[var(--line)] min-w-0">
            <span className="font-mono text-xs w-7 text-[var(--muted)]">{pad(a.number)}</span>
            <span className="sr-only" data-testid={`admin-gender-${a.slug}`}>{a.gender === "female" ? "Female" : "Male"}</span>
            <span className="flex-1 truncate min-w-0">{a.name}</span>
            <Switch checked={a.unlocked} onCheckedChange={(v) => put(a.slug, { unlocked: v, notify: notifyNew })} data-testid={`admin-unlock-${a.slug}`} />
            <button onClick={() => setEdit(a)} className="text-xs btn-ghost px-3 py-1" data-testid={`admin-edit-${a.slug}`}>Edit</button>
          </div>
        ))}
      </div>
      {edit && <CatalogArticleEditor key={edit.slug} group="archetypes" slug={edit.slug} api={api} onClose={() => setEdit(null)} onPublished={load} />}
    </div>
  );
}

function FiguresAdmin({ api, notifyNew }) {
  const [list, setList] = useState([]);
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [loadError,setLoadError]=useState(false);
  const [edit, setEdit] = useState(null);
  const types = new Map();
  for (const f of list) {
    const name = f.lead_type?.name || "No named match";
    types.set(name, (types.get(name) || 0) + 1);
  }
  const visible = filterFigures(list, query).filter(f => !type || (f.lead_type?.name || "No named match") === type);
  const load=useCallback(() => {setLoadError(false); return api.get("/admin/figures?summary=true").then(r=>setList(r.data)).catch(()=>setLoadError(true));},[api]);
  const open = slug => setEdit(slug);
  useEffect(() => {
    load();
  }, [load]);
  const put = async (slug, body) => {
    try {
      await api.put(`/admin/figures/${slug}`, body);
      toast.success("Saved");
      setEdit(null);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  return (
    <div className={`${PANEL} grid md:grid-cols-2 content-start gap-x-4 px-2`} data-testid="admin-figures-table">
      <div className="md:col-span-2 flex flex-wrap items-center gap-3 py-3 px-2">
        <Input type="search" value={query} onChange={e => setQuery(e.target.value)} aria-label="Find a public figure by name" placeholder="Find a public figure by name" className="max-w-sm" />
        <select aria-label="Filter by lead archetype" value={type} onChange={e => setType(e.target.value)} className="rounded-md border border-[var(--line)] bg-[var(--bg)] px-2 py-2 text-sm">
          <option value="">All lead archetypes</option>
          {[...types].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([name, count]) => <option key={name} value={name}>{name} ({count})</option>)}
        </select>
        <span className="text-xs text-[var(--muted)]" role="status">{query.trim() || type ? `${visible.length} of ${list.length}` : list.length} profiles</span>
      </div>
      {loadError && <button className="underline" onClick={load}>Figures couldn’t load. Retry</button>}
      <FigureEvidence api={api} openEditor={open} />
      {visible.map((f) => (
        <div key={f.slug} className="flex items-center gap-3 px-2 py-2.5 text-sm border-b border-[var(--line)]">
          <span className="flex-1">{f.name}{f.lead_type?.name && <span className="block text-xs text-[var(--muted)]">{f.lead_type.name}</span>}</span>
          <span className="text-xs text-[var(--muted)]">{f.published ? "Published" : "Hidden"}</span>
          <Switch checked={f.published} onCheckedChange={(v) => put(f.slug, { published: v, notify: notifyNew })} data-testid={`admin-publish-${f.slug}`} />
          <button onClick={() => open(f.slug)} className="text-xs btn-ghost px-3 py-1" data-testid={`admin-edit-figure-${f.slug}`}>Edit</button>
        </div>
      ))}
      {edit && <CatalogArticleEditor key={edit} group="figures" slug={edit} api={api} onClose={() => setEdit(null)} onPublished={load} />}
    </div>
  );
}

function SettingsAdmin({ api, section }) {
  const [s, setS] = useState(null);
  const [loadError, setLoadError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setLoadError(false);
    api.get("/admin/settings", { signal: controller.signal }).then(({data}) => setS(data))
      .catch(() => { if (!controller.signal.aborted) setLoadError(true); });
    return () => controller.abort();
  }, [api, attempt]);
  const save = async (body) => {
    setSaving(true);
    try {
      const { data } = await api.put("/admin/settings", body);
      setS(data);
      toast.success("Saved");
      return { data };
    } catch (e) {
      toast.error(errMsg(e));
      return { error: errMsg(e), status: e.response?.status };
    } finally { setSaving(false); }
  };
  if (!s) return loadError ? <button className="underline" onClick={() => setAttempt(n => n + 1)}>Settings couldn’t load. Try again.</button> : <p role="status">Loading settings…</p>;
  if (section === "quiz") return <QuizCatalog api={api}><EverydayQuizEditor api={api} settings={s} save={save} saving={saving} reload={async () => {
    const { data } = await api.get("/admin/settings");
    setS(data);
    return data;
  }} /></QuizCatalog>;
  const updateCopy = (key, value) => setS(prev => ({ ...prev, upgrade: { ...prev.upgrade, [key]: value } }));
  return (
    <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)] gap-3 h-full min-h-0" data-testid="admin-tiers">
      <div className="card p-5 space-y-4 self-start">
        <p className="text-sm">Monthly plans include 100 replies for $12, 250 for $20, or 500 for $40. Paying members can add 100 replies for $12. Individual and Couples use the same balance.</p>
        <p className="text-sm">A sponsored partner uses the sponsor’s allowance. Their $10 monthly offer and each partner’s $80 one-year offer include 100 replies per month. The annual term ends automatically.</p>
        <div className="flex items-center gap-3"><Switch id="membership-waitlist" checked={s.waitlist_mode} onCheckedChange={(v) => setS({ ...s, waitlist_mode: v })} data-testid="admin-waitlist-mode" /><Label htmlFor="membership-waitlist">Send new signups to the waitlist</Label></div>
        <div><Label htmlFor="membership-tiktok">TikTok link</Label><Input id="membership-tiktok" className="mt-1.5" value={s.tiktok_url} onChange={(e) => setS({ ...s, tiktok_url: e.target.value })} data-testid="admin-tiktok-url" /></div>
        <button disabled={saving} onClick={() => save({ waitlist_mode: s.waitlist_mode, tiktok_url: s.tiktok_url, upgrade: s.upgrade })} className="px-6 py-2.5 rounded-full bg-[var(--ink)] text-[var(--bg)] font-bold" data-testid="admin-tiers-save">{saving ? "Saving…" : "Save changes"}</button>
      </div>
      <div className="card p-5 space-y-4 self-start">
        <h2 className="font-display text-xl font-black uppercase">Member-facing offer</h2>
        <div><Label htmlFor="offer-headline">Headline</Label><Input id="offer-headline" className="mt-1.5" value={s.upgrade.headline} onChange={e => updateCopy("headline", e.target.value)} /></div>
        <div><Label htmlFor="offer-pitch">Description</Label><Textarea id="offer-pitch" className="mt-1.5" value={s.upgrade.pitch} onChange={e => updateCopy("pitch", e.target.value)} /></div>
        <div><Label htmlFor="offer-features">Features, one per line</Label><Textarea id="offer-features" className="mt-1.5" rows={5} value={s.upgrade.paid_features.join("\n")} onChange={e => updateCopy("paid_features", e.target.value.split("\n"))} /></div>
      </div>
    </div>
  );
}

const TITLES = { dating: "Dating & safety", dashboard: "Dashboard", activity: "Activity", couples: "Couples", research: "Research", archetypes: "Archetypes", figures: "Public Figures", prompts: "Prompts", quiz: "Quizzes", users: "Users & Membership", notifications: "Notifications", support: "Support" };
const ACTIVITY_VIEWS = [["workspace", "Workspace activity"], ["live", "Live activity"]];
const LEGACY_REPORT_PATHS = new Map([
  ["trends", "/admin/activity"],
  ["activity", "/admin/activity?view=live"],
  ["couples", "/admin/couples"],
]);

export default function Admin() {
  const { api, me, setMe } = useOutletContext();
  const { section = "dashboard" } = useParams();
  const [query, setQuery] = useSearchParams();
  const { hash } = useLocation();
  const [notifyNew, setNotifyNew] = useState(true);
  const view = query.get("view");
  if (!Object.hasOwn(TITLES, section)) return <Navigate to="/admin" replace />;
  if (section === "dashboard" && LEGACY_REPORT_PATHS.has(view)) return <Navigate to={LEGACY_REPORT_PATHS.get(view)} replace />;
  const publish = section === "archetypes" || section === "figures";
  return (
    <div className="flex flex-col lg:h-[calc(100vh-6.5rem)] min-h-0" data-testid="admin-page">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3 shrink-0">
        <h1 className="font-display font-black uppercase text-3xl sm:text-4xl" data-testid="admin-title">{TITLES[section] || "Admin"}</h1>
        {publish && <label className="flex items-center gap-2 text-sm"><Switch checked={notifyNew} onCheckedChange={setNotifyNew} data-testid="admin-notify-toggle" />Notify users when publishing</label>}
      </div>
      <div className="flex-1 min-h-0 flex flex-col">
      {section === "dating" && <DatingAdmin api={api} />}
      {section === "dashboard" && <AdminMetrics api={api} view={view}
        onViewChange={value => setQuery(value === "growth" ? {} : {view: value})} />}
      {section === "activity" && <div className="flex flex-col gap-3 h-full min-h-0">
        <SubTabs items={ACTIVITY_VIEWS} value={view === "live" ? "live" : "workspace"}
          onChange={value => setQuery(value === "live" ? {view: "live"} : {})} testid="admin-activity-views" />
        {view === "live" ? <ActivityFeed api={api} /> : <WorkspaceTrends api={api} initialMeasure={query.get("measure")} initialDays={Number(query.get("days"))} />}
      </div>}
      {section === "couples" && <CouplesReport api={api} />}
      {section === "research" && <ResearchTrends key={me?.user_id} api={api} initialUnit={query.get("unit")}
        tool={query.get("tool") || hash || "comparisons"} onToolChange={value => {
          const next = new URLSearchParams(query);
          next.set("tool", value);
          setQuery(next);
        }} />}
      {section === "archetypes" && <ArchetypesAdmin api={api} notifyNew={notifyNew} />}
      {section === "figures" && <FiguresAdmin api={api} notifyNew={notifyNew} />}
      {section === "prompts" && <PromptLibrary api={api} view={view} onViewChange={value => setQuery(value === "ai" ? {} : {view:value})} />}
      {section === "quiz" && <SettingsAdmin key={section} api={api} section={section} />}
      {section === "users" && <MembersAdmin api={api} me={me} onOwnRoleChange={role => setMe(current => ({ ...current, role }))}
        settings={<SettingsAdmin api={api} section="tiers" />} />}
      {section === "notifications" && <NotificationsAdmin api={api} />}
      {section === "support" && <SupportInbox api={api} />}
      </div>
    </div>
  );
}
