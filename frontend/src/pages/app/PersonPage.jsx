import { useEffect } from "react";
import { MemberLoadState, useMemberResource } from "@/components/app/MemberResource";
import { Link, useNavigate, useOutletContext, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ArrowLeft, Archive, ArchiveRestore, Trash2, Camera, FileText, Target, Bookmark, UserPen, MessagesSquare, History, ShieldCheck } from "lucide-react";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog";
import { Meter } from "@/components/app/Meter";
import { STAGE_LABEL, isRomantic, relLabel, errMsg, pad } from "@/lib/api";
import { resize } from "@/pages/app/Settings";
import ReportTab from "@/pages/app/person/ReportTab";
import PrepareTab from "@/pages/app/person/PrepareTab";
import MemoriesTab from "@/pages/app/person/MemoriesTab";
import ProfileTab from "@/pages/app/person/ProfileTab";
import HistoryTab from "@/pages/app/person/HistoryTab";
import SavedTab from "@/pages/app/person/SavedTab";
import { ArchIcon } from "@/components/public/ArchIcon";

const SECTIONS = [
  ["report", "The read", "What they're like", FileText],
  ["history", "History", "What you did, what happened", History],
  ["prepare", "Game plans", "Get ready for a moment", Target],
  ["memories", "Memories", "What you've saved", Bookmark],
  ["profile", "Details", "Who they are", UserPen],
  ["saved", "Saved data", "See & delete it all", ShieldCheck],
];

export function DeletePerson({ p, api, onDone, trigger }) {
  const del = async () => {
    try {
      await api.delete(`/people/${p.id}`);
      toast.success(`${p.nickname} deleted`);
      onDone();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>{trigger}</AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader><AlertDialogTitle>Delete {p.nickname}?</AlertDialogTitle><AlertDialogDescription>This also deletes all memories of them, every conversation, report and game plan. It can't be undone.</AlertDialogDescription></AlertDialogHeader>
        <AlertDialogFooter><AlertDialogCancel data-testid="person-delete-cancel">Cancel</AlertDialogCancel><AlertDialogAction onClick={del} data-testid="person-delete-confirm">Delete everything</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function HeaderPhoto({ p, api, reload }) {
  const pick = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    try {
      await api.patch(`/people/${p.id}`, { avatar: await resize(f, 320) });
      reload();
    } catch (err) {
      toast.error(err.response ? errMsg(err) : err.message || errMsg(err));
    }
  };
  return (
    <label className="relative w-20 h-20 sm:w-24 sm:h-24 rounded-2xl border border-[var(--line)] bg-[var(--surface-2)] overflow-hidden cursor-pointer group shrink-0 flex items-center justify-center" title="Change photo" data-testid="person-header-photo">
      {p.avatar ? <img src={p.avatar} alt={p.nickname} className="w-full h-full object-cover" /> : <Camera className="w-6 h-6 text-[var(--muted)]" />}
      <span className="absolute inset-0 bg-black/50 text-white opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center"><Camera className="w-5 h-5" /></span>
      <input type="file" accept="image/png,image/jpeg,image/webp" aria-label={`Change photo for ${p.nickname}`} className="sr-only" onChange={pick} data-testid="person-header-photo-input" />
    </label>
  );
}

function Header({ p, api, reload }) {
  const nav = useNavigate();
  const archive = async () => {
    try {
      await api.patch(`/people/${p.id}`, { archived: !p.archived });
      toast.success(p.archived ? "Restored" : "Archived");
      reload();
    } catch (error) {
      toast.error(errMsg(error));
    }
  };
  const icon = "w-10 h-10 rounded-full border border-[var(--line)] flex items-center justify-center hover:border-[var(--ink)] transition-colors";
  return (
    <header className="card p-5 sm:p-6 grid gap-5 lg:grid-cols-[1fr_auto_1fr] lg:items-center" data-testid="person-header">
      <div className="flex items-center gap-4 min-w-0">
        <HeaderPhoto p={p} api={api} reload={reload} />
        <div className="min-w-0">
          <h1 className="font-display font-black uppercase text-4xl sm:text-5xl leading-none truncate" data-testid="person-name">{p.nickname}</h1>
          <div className="flex flex-wrap gap-1.5 mt-2.5 text-xs">
            <span className="px-2.5 py-1 rounded-full bg-[var(--surface-2)]" data-testid="person-reltype">{relLabel(p)}</span>
            <span className="px-2.5 py-1 rounded-full bg-[var(--surface-2)]" data-testid="person-stage">{STAGE_LABEL[p.stage] || p.stage}</span>
            {p.archived && <span className="px-2.5 py-1 rounded-full border border-[var(--line)]">Archived</span>}
            {isRomantic(p.rel_type) && p.archetype_tag && <span className="px-2.5 py-1 rounded-full bg-[var(--ink)] text-[var(--bg)] inline-flex items-center gap-1.5" data-testid="person-archetype-tag"><ArchIcon number={p.archetype_tag} className="w-3.5 h-3.5" />{p.archetype_tag > 100 ? "Female" : "Male"} #{pad(p.archetype_tag)}</span>}
          </div>
          {p.goal && <p className="text-sm text-[var(--muted)] mt-2 truncate" data-testid="person-goal">Your goal: <span className="text-[var(--ink)]">{p.goal}</span></p>}
        </div>
      </div>
      <div className="lg:px-6 lg:border-x border-[var(--line)] lg:max-w-md" data-testid="person-meter-strip"><Meter meter={p.meter} size={56} showMissing /></div>
      <div className="flex items-center gap-2 lg:flex-col lg:items-end">
        <Link to={`/app/people?chat=${p.id}`} className="px-5 py-2.5 rounded-full bg-[var(--emerald)] text-[var(--on-accent)] font-bold text-sm inline-flex items-center gap-2" data-testid="person-open-chat"><MessagesSquare className="w-4 h-4" />Chat about {p.nickname}</Link>
        <span className="flex gap-2">
          <button onClick={archive} className={icon} title={p.archived ? "Restore" : "Archive"} aria-label={p.archived ? "Restore" : "Archive"} data-testid="person-archive-button">{p.archived ? <ArchiveRestore className="w-4 h-4" /> : <Archive className="w-4 h-4" />}</button>
          <DeletePerson p={p} api={api} onDone={() => nav("/app/people")} trigger={<button className={`${icon} text-[var(--crimson)] hover:border-[var(--crimson)]`} title="Delete" aria-label="Delete" data-testid="person-delete-button"><Trash2 className="w-4 h-4" /></button>} />
        </span>
      </div>
    </header>
  );
}

function SectionNav({ tab, go }) {
  return (
    <nav className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2" data-testid="person-tabs">
      {SECTIONS.map(([k, label, hint, I]) => (
        <button key={k} onClick={() => go(k)} data-testid={`tab-${k}`}
          className={`flex items-center gap-3 p-3 sm:p-4 rounded-2xl border text-left transition-colors ${tab === k ? "bg-[var(--ink)] text-[var(--bg)] border-[var(--ink)]" : "border-[var(--line)] hover:border-[var(--ink)]"}`}>
          <I className="w-5 h-5 shrink-0" />
          <span className="min-w-0"><span className="block font-semibold text-sm">{label}</span><span className={`block text-[11px] truncate ${tab === k ? "opacity-70" : "text-[var(--muted)]"}`}>{hint}</span></span>
        </button>
      ))}
    </nav>
  );
}

export default function PersonPage() {
  const { id } = useParams();
  const { me, api } = useOutletContext();
  return <PersonDetails key={me.user_id + ":" + id} id={id} me={me} api={api} />;
}

function PersonDetails({ id, me, api }) {
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "report";
  const { data: p, error, status, reload } = useMemberResource(api, `/people/${id}`);
  useEffect(() => {
    if (tab === "chat") nav(`/app/people?chat=${id}`, { replace: true });
  }, [tab, id, nav]);

  if (status === 404) return <p data-testid="person-missing">This person doesn't exist. <Link to="/app/people" className="underline">Back</Link></p>;
  if (!p || error) return <MemberLoadState label="this person" error={error} retry={reload} />;
  const ctx = { p, api, me, reload, setTab: (t) => setParams({ tab: t }) };

  return (
    <div className="space-y-5" data-testid="person-page">
      <Link to="/app/people" className="text-sm text-[var(--muted)] inline-flex items-center gap-1 hover:text-[var(--ink)]" data-testid="person-back"><ArrowLeft className="w-4 h-4" />Your people</Link>
      <Header p={p} api={api} reload={reload} />
      <SectionNav tab={tab} go={(t) => setParams({ tab: t })} />
      <div className="min-w-0 pt-1">
        {tab === "report" && <ReportTab {...ctx} />}
        {tab === "history" && <HistoryTab {...ctx} exchange={params.get("exchange")} />}
        {tab === "prepare" && <PrepareTab {...ctx} selectedPlan={params.get("plan")} />}
        {tab === "memories" && <MemoriesTab {...ctx} />}
        {tab === "profile" && <ProfileTab {...ctx} />}
        {tab === "saved" && <SavedTab {...ctx} />}
      </div>
    </div>
  );
}
