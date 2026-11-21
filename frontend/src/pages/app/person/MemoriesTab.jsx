import { useState } from "react";
import { ListContinuation, useMemberPages } from "@/components/app/MemberPages";
import { MemberLoadState } from "@/components/app/MemberResource";
import { toast } from "sonner";
import { Pencil, Trash2, Check } from "lucide-react";
import { Input } from "@/components/ui/input";
import { errMsg } from "@/lib/api";

const GROUPS = [
  ["profile_detail", "Profile details", "What you typed in yourself"],
  ["event", "Reported events", "What they actually did or said"],
  ["interpretation", "Interpretations", "Readings derived from events"],
  ["assessment", "Working assessment", "The current overall read"],
  ["outcome", "Outcomes", "What happened after advice"],
];

function Row({ m, p, api, reload, setTab }) {
  const [edit, setEdit] = useState(false);
  const [text, setText] = useState(m.text);
  const isDetail = m.kind === "profile_detail";
  const save = async () => {
    try {
      await api.patch(`/memories/${m.id}`, { text });
      setEdit(false);
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  const del = async () => {
    const msg = isDetail ? "Delete this profile detail?" : "Delete this memory? Automatic interpretations derived from it will also be removed. Your own edited notes are kept.";
    if (!window.confirm(msg)) return;
    try {
      if (isDetail) await api.delete(`/people/${p.id}/details/${m.id.split(":")[1]}`);
      else {
        const { data } = await api.delete(`/memories/${m.id}`);
        if (data.derived_removed) toast.success(`Also removed ${data.derived_removed} derived item(s)`);
      }
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  return (
    <li className="py-3 border-b border-[var(--line)] last:border-0 flex gap-3 items-start" data-testid={`memory-${m.id}`}>
      <div className="flex-1 min-w-0">
        {edit ? <Input value={text} onChange={(e) => setText(e.target.value)} data-testid="memory-edit-input" /> : <p className="text-sm">{isDetail && <span className="text-[var(--muted)]">{m.label}: </span>}{m.text}</p>}
        <p className="text-[11px] font-mono text-[var(--muted)] mt-1">{m.source}{m.derived_from?.length ? ` · from ${m.derived_from.length} event(s)` : ""}</p>
      </div>
      {!isDetail && <button type="button" className="text-xs underline" aria-pressed={!!m.prioritized} onClick={async () => {
        try { await api.put("/memories/"+m.id+"/priority", {prioritized:!m.prioritized}); await reload(); } catch(e) { toast.error(errMsg(e)); }
      }}>{m.prioritized ? "Prioritized" : "Prioritize for coaching"}</button>}
      {edit ? <button onClick={save} data-testid="memory-edit-save"><Check className="w-4 h-4 text-[var(--emerald)]" /></button>
        : <button onClick={() => (isDetail ? setTab("profile") : setEdit(true))} className="text-[var(--muted)] hover:text-[var(--ink)]" data-testid={`memory-edit-${m.id}`}><Pencil className="w-4 h-4" /></button>}
      <button onClick={del} className="text-[var(--muted)] hover:text-[var(--crimson)]" data-testid={`memory-delete-${m.id}`}><Trash2 className="w-4 h-4" /></button>
    </li>
  );
}

export default function MemoriesTab({ p, api, setTab, reload: reloadPerson }) {
  const page = useMemberPages(api, `/people/${p.id}/memories`, { size: 100 });
  const { rows: mems, error, reload } = page;
  const load = () => Promise.all([reload(), reloadPerson()]);
  if (!mems || (error && !page.more)) return <MemberLoadState label="the saved memories" error={error} retry={reload} />;
  return (
    <div className="grid xl:grid-cols-2 gap-4 items-stretch [&>*]:h-full" data-testid="memories-tab">
      <p className="text-sm text-[var(--muted)] xl:col-span-2">Memories about {p.nickname}. Prioritize an important boundary to include it ahead of recent memories. Coaching can use up to 300 memories per person.</p>
      {GROUPS.map(([k, t, d]) => {
        const items = mems.filter((m) => m.kind === k);
        return (
          <section key={k} className="card p-5" data-testid={`memory-group-${k}`}>
            <div className="flex items-baseline gap-2"><p className="font-semibold">{t}</p><span className="text-xs text-[var(--muted)]">{d} · {items.length}</span></div>
            {items.length ? <ul className="mt-2">{items.map((m) => <Row key={m.id} m={m} p={p} api={api} reload={load} setTab={setTab} />)}</ul>
              : <p className="text-sm text-[var(--muted)] mt-2">Nothing here yet.</p>}
          </section>
        );
      })}
      <ListContinuation page={page} label="memories" />
    </div>
  );
}
