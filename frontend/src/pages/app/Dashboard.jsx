import { useEffect, useMemo, useState } from "react";
import { ListContinuation, useMemberPages } from "@/components/app/MemberPages";
import { MemberLoadState } from "@/components/app/MemberResource";
import { Link, useNavigate, useOutletContext, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Plus, X, Camera, Trash2, Check } from "lucide-react";
import PeopleLookup from "@/components/app/PeopleLookup";
import { PeopleChat } from "@/components/app/PeopleChat";
import { LimitNotice, messageLimit, personLimit } from "@/components/app/PlanUsage";
import { DeletePerson } from "@/pages/app/PersonPage";
import { resize } from "@/pages/app/Settings";
import { AddPersonForm } from "@/components/app/AddPersonForm";
import { Meter } from "@/components/app/Meter";
import { GroupIcon } from "@/components/app/GroupIcon";
import { STAGE_LABEL, REL_GROUPS, GROUP_OF, isRomantic, relLabel, errMsg, pad } from "@/lib/api";
import { formatDistanceToNow } from "date-fns";
import { ArchIcon } from "@/components/public/ArchIcon";

function PersonPhoto({ p, api, reload }) {
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
    <label className="absolute top-14 right-3.5 w-14 h-14 rounded-full border border-[var(--line)] bg-[var(--surface-2)] overflow-hidden cursor-pointer group z-10 flex items-center justify-center" title="Add a photo" data-testid={`person-photo-${p.id}`}>
      {p.avatar ? <img src={p.avatar} alt={p.nickname} className="w-full h-full object-cover" /> : <Camera className="w-5 h-5 text-[var(--muted)]" />}
      <span className="absolute inset-0 bg-black/50 text-white opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center"><Camera className="w-4 h-4" /></span>
      <input type="file" accept="image/*" aria-label={`Change photo for ${p.nickname}`} className="sr-only" onChange={pick} data-testid={`person-photo-input-${p.id}`} />
    </label>
  );
}

function ChatToggle({ p, inChat, toggle }) {
  return (
    <button onClick={() => toggle(p.id)} title={inChat ? "In the chat" : "Add to chat"} aria-label={inChat ? `Remove ${p.nickname} from chat` : `Add ${p.nickname} to chat`} data-testid={`person-chat-add-${p.id}`}
      style={{ clipPath: "polygon(0 0, 100% 0, 100% 100%, 50% 80%, 0 100%)" }}
      className={`absolute -top-6 right-5 z-20 w-9 h-12 pb-2.5 grid place-items-center shadow-md transition-transform hover:translate-y-0.5 ${inChat ? "bg-[var(--ink)] text-[var(--bg)]" : "bg-[#16a34a] text-white"}`}>
      {inChat ? <Check className="w-4 h-4" /> : <Plus className="w-5 h-5" />}
    </button>
  );
}

function PersonCard({ p, myType, api, reload, inChat, toggle }) {
  const [refreshingMatch, setRefreshingMatch] = useState(false);
  const refreshMatch = async () => {
    setRefreshingMatch(true);
    try {
      await api.post(`/people/${p.id}/type-match/refresh`);
      await reload();
    } catch (e) { toast.error(errMsg(e)); }
    finally { setRefreshingMatch(false); }
  };
  return (
    <div className="flex h-full flex-col">
    <div className="relative flex-1">
    <ChatToggle p={p} inChat={inChat} toggle={toggle} />
    <PersonPhoto p={p} api={api} reload={reload} />
    <Link to={`/app/person/${p.id}`} className="card p-5 h-full hover:border-[var(--ink)] transition-colors flex flex-col gap-4" data-testid={`person-card-${p.id}`}>
      <div className="pr-20 min-w-0 min-h-[7.5rem]">
        <p className="font-display font-black uppercase text-3xl leading-none truncate">{p.nickname}</p>
        <p className="text-xs text-[var(--muted)] mt-1.5 flex items-center gap-1.5"><GroupIcon group={GROUP_OF[p.rel_type] || "other"} className="w-3.5 h-3.5" />{relLabel(p)} · {STAGE_LABEL[p.stage] || p.stage}{p.archived ? " · archived" : ""}</p>
        {isRomantic(p.rel_type) && p.archetype_tag && <span className="mt-2 font-mono text-xs px-2 py-1 rounded-full bg-[var(--ink)] text-[var(--bg)] inline-flex items-center gap-1" data-testid="person-card-archetype"><ArchIcon number={p.archetype_tag} className="w-3.5 h-3.5" />#{pad(p.archetype_tag)}</span>}
      </div>
      {p.goal && <span className="self-start text-xs px-2.5 py-1 rounded-full border border-[var(--emerald)]/50 text-[var(--emerald)]" data-testid="person-goal-chip">Goal: {p.goal}</span>}
      {isRomantic(p.rel_type) && p.archetype_tag && myType && (
        <p className="text-xs rounded-xl border border-[var(--line)] px-3 py-2" data-testid="person-card-type-match">
          <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)] block mb-0.5">{myType} × #{pad(p.archetype_tag)}</span>
          {p.type_match?.line || <span className="text-[var(--muted)]">{p.type_match?.stale ? "Your comparison needs a refresh to use your current profile." : p.type_match?.failed ? "The comparison could not be generated." : "Preparing your comparison…"}</span>}
        </p>
      )}
      <p className="text-sm text-[var(--ink)]/80 line-clamp-2">{p.working_read || "No read yet — add a conversation to get one."}</p>
      <div className="flex justify-between items-end mt-auto">
        <Meter meter={p.meter} size={56} />
        <span className="w-9 h-9" />
      </div>
    </Link>
    <DeletePerson p={p} api={api} onDone={reload} trigger={<button className="absolute bottom-5 right-5 w-9 h-9 rounded-full border border-[var(--line)] text-[var(--crimson)] hover:border-[var(--crimson)] flex items-center justify-center transition-colors z-10" title="Delete" aria-label={`Delete ${p.nickname}`} data-testid={`person-card-delete-${p.id}`}><Trash2 className="w-4 h-4" /></button>} />
    </div>
    {myType && isRomantic(p.rel_type) && p.archetype_tag && (p.type_match?.stale || p.type_match?.failed) && <button
      className="btn-ghost mt-2 px-3 py-1.5 text-xs" onClick={refreshMatch} disabled={refreshingMatch} data-testid={`refresh-type-match-${p.id}`}>
      {refreshingMatch ? "Updating comparison…" : "Refresh comparison"}
    </button>}
    </div>
  );
}

function Chips({ people, counts, group, setGroup }) {
  const count = (g) => counts?.[g] ?? people.filter((p) => g === "all" || (GROUP_OF[p.rel_type] || "other") === g).length;
  return (
    <div className="flex gap-2 overflow-x-auto no-scrollbar mb-6" data-testid="people-filters">
      {[{ key: "all", label: "All" }, ...REL_GROUPS].map((g) => (
        <button key={g.key} onClick={() => setGroup(g.key)} data-testid={`people-filter-${g.key}`}
          className={`shrink-0 inline-flex items-center gap-1.5 px-4 py-2 rounded-full border text-sm transition-colors ${group === g.key ? "bg-[var(--ink)] text-[var(--bg)] border-[var(--ink)]" : "border-[var(--line)] text-[var(--muted)] hover:text-[var(--ink)]"}`}>
          {g.key !== "all" && <GroupIcon group={g.key} className="w-3.5 h-3.5" />}{g.label}<span className="font-mono text-xs opacity-70">{count(g.key)}</span>
        </button>
      ))}
    </div>
  );
}

export default function Dashboard() {
  const { me, api } = useOutletContext();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const group = params.get("group") || "all";
  const directory = useMemberPages(api, "/people", { field: "people", size: 200 });
  const { error, reload: load } = directory;
  const { rows, data: metadata, more, busy } = directory;
  const data = useMemo(() => rows && { ...metadata, people: rows }, [metadata, rows]);
  const [open, setOpen] = useState(false);
  const store = `imaldri_chat_${me.user_id || "me"}`;
  const [chatIds, setChatIdsRaw] = useState(() => JSON.parse(localStorage.getItem(store) || "[]"));
  const setChatIds = (v) => {
    setChatIdsRaw(v);
    localStorage.setItem(store, JSON.stringify(v));
  };
  const missingSelected = directory.rows && chatIds.some(id => !directory.rows.some(person => person.id === id));
  useEffect(() => {
    if (missingSelected && more && !busy && !error) more();
  }, [missingSelected, more, busy, error]);
  const toggle = (id) => setChatIds(chatIds.includes(id) ? chatIds.filter((x) => x !== id) : [...chatIds, id].slice(-6));
  const pending = data?.my_type && data.people.some((p) => !messageLimit(me.usage, p) && isRomantic(p.rel_type) && p.archetype_tag && !p.type_match);
  useEffect(() => {
    if (!pending) return;
    const t = setTimeout(load, 10000);
    return () => clearTimeout(t);
  }, [pending, data, load]);

  const add = () => setParams({ add: "1" });
  useEffect(() => {
    setOpen(params.get("add") === "1");
    const c = params.get("chat");
    if (c) {
      setChatIds([...new Set(c.split(",").filter(Boolean))].slice(0, 6));
      setParams({}, { replace: true });
    }
  }, [params]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!data) return <MemberLoadState label="your people" error={error} retry={load} />;
  const inGroup = (p) => group === "all" || (GROUP_OF[p.rel_type] || "other") === group;
  const active = data.people.filter((p) => !p.archived && inGroup(p));
  const archived = data.people.filter((p) => p.archived && inGroup(p));
  if (open) {
    const limit = personLimit(me.usage);
    return (
      <section data-testid="add-person-panel">
        <div className="flex items-end justify-between mb-6">
          <div>
            <p className="font-mono text-xs text-[var(--muted)] uppercase tracking-widest">People</p>
            <h1 className="font-display font-black uppercase text-5xl" data-testid="add-person-title">Add a person</h1>
          </div>
          <button onClick={() => { setOpen(false); setParams({}); }} className="btn-ghost px-4 py-2 text-sm inline-flex items-center gap-1.5" data-testid="add-person-cancel"><X className="w-4 h-4" />Cancel</button>
        </div>
        {limit ? <LimitNotice reason={limit} /> : <div className="card p-6 lg:p-8"><AddPersonForm api={api} onCreated={async (p) => { await load(); nav(`/app/people?chat=${p.id}`); }} /></div>}
      </section>
    );
  }

  return (
    <div className="grid xl:grid-cols-[1fr_420px] gap-6 items-start">
      <section className="min-w-0">
        <div className="mb-6">
          <p className="font-mono text-xs text-[var(--muted)] uppercase tracking-widest">Hi {me.nickname || "there"}</p>
          <div className="flex flex-wrap items-center gap-4">
            <h1 className="font-display font-black uppercase text-5xl" data-testid="dashboard-title">Your People</h1>
            <button onClick={add} className="px-4 py-2.5 rounded-full bg-[var(--emerald)] text-[var(--on-accent)] font-bold text-sm inline-flex items-center gap-1.5" data-testid="add-person-button"><Plus className="w-4 h-4" />Add person</button>
          </div>
        </div>
        <PeopleLookup api={api} />
        <Chips counts={data.active_counts} people={data.people.filter((p) => !p.archived)} group={group} setGroup={(g) => setParams(g === "all" ? {} : { group: g })} />
        {!active.length && <button onClick={add} className="card w-full p-12 text-left hover:border-[var(--ink)] transition-colors" data-testid="people-empty"><p className="font-display font-black uppercase text-4xl">Add the person on your mind.</p><p className="text-sm text-[var(--muted)] mt-2">A nickname and one line is enough to start.</p></button>}
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-2 2xl:grid-cols-3 gap-x-3 gap-y-6 pt-3">{active.map((p) => <PersonCard key={p.id} p={p} myType={data.my_type} api={api} reload={load} inChat={chatIds.includes(p.id)} toggle={toggle} />)}</div>
        {archived.length > 0 && (
          <>
            <p className="font-mono text-xs uppercase text-[var(--muted)] mt-10 mb-3">Archived</p>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-2 2xl:grid-cols-3 gap-x-3 gap-y-6 pt-3 opacity-60">{archived.map((p) => <PersonCard key={p.id} p={p} myType={data.my_type} api={api} reload={load} inChat={chatIds.includes(p.id)} toggle={toggle} />)}</div>
          </>
        )}
        <ListContinuation page={directory} label="people" />
      </section>
      <div className={chatIds.length ? "order-first xl:order-none" : ""}><PeopleChat people={data.people} ids={chatIds} setIds={setChatIds} api={api} me={me} reload={load} /></div>
    </div>
  );
}
