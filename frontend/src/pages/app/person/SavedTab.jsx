import { ListContinuation, useMemberPages } from "@/components/app/MemberPages";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Download, Trash2, ShieldCheck } from "lucide-react";
import { DeletePerson } from "@/pages/app/PersonPage";
import { errMsg } from "@/lib/api";
import { MemberLoadState, useMemberResource } from "@/components/app/MemberResource";

const day = (s) => new Date(s).toLocaleDateString();

function Group({ title, count, testid, children, empty }) {
  return (
    <section className="card p-4" data-testid={testid}>
      <p className="font-display font-black uppercase text-lg leading-none mb-3">{title} <span className="font-mono text-xs text-[var(--muted)]">{count}</span></p>
      {count ? <ul className="divide-y divide-[var(--line)]">{children}</ul> : <p className="text-xs text-[var(--muted)]">{empty || "Nothing saved."}</p>}
    </section>
  );
}

function SavedGroup({ api, pid, section, title, revision, hidden, children }) {
  const page = useMemberPages(api, `/people/${pid}/saved/${section}`, { revision });
  const rows = page.rows?.filter(row => !hidden.includes(`/${section}/${row.id}`)) || [];
  return <section className="card p-4" data-testid={"saved-" + section}>
    <h3 className="font-display font-black uppercase text-lg leading-none mb-3">{title} {page.rows && <span className="font-mono text-xs text-[var(--muted)]">{page.total}</span>}</h3>
    {!page.rows ? <MemberLoadState label={title.toLowerCase()} error={page.error} retry={page.reload} /> :
      rows.length ? <ul className="divide-y divide-[var(--line)]">{rows.map(children)}</ul> :
      <p className="text-xs text-[var(--muted)]">Nothing saved on this page.</p>}
    <ListContinuation page={page} label={title.toLowerCase()} />
  </section>;
}

function Row({ text, sub, onDelete, testid }) {
  return (
    <li className="py-2 flex items-start gap-3" data-testid={testid}>
      <div className="flex-1 min-w-0"><p className="text-sm break-words">{text}</p>{sub && <p className="text-[11px] text-[var(--muted)]">{sub}</p>}</div>
      <button onClick={onDelete} className="text-[var(--muted)] hover:text-[var(--crimson)] mt-0.5 shrink-0" aria-label="Delete" data-testid={`${testid}-delete`}><Trash2 className="w-3.5 h-3.5" /></button>
    </li>
  );
}

export default function SavedTab({ p, api, reload: reloadPerson }) {
  const nav = useNavigate();
  const { data: d, error, reload: load } = useMemberResource(api, `/people/${p.id}/saved?summary=true`);
  const [hidden, setHidden] = useState([]);
  const timers = useRef({});
  const toasts = useRef({});
  const commit = useCallback(async (url) => {
    delete timers.current[url];
    try {
      await api.delete(url);
      load();
      reloadPerson();
    } catch (e) {
      toast.error(errMsg(e));
    }
    setHidden((h) => h.filter((x) => x !== url));
  }, [api, load]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => {
    Object.entries(timers.current).forEach(([url, t]) => { clearTimeout(t); toast.dismiss(toasts.current[url]); api.delete(url).catch(() => {}); });
  }, [api]);
  const del = (url) => {
    setHidden((h) => [...h, url]);
    timers.current[url] = setTimeout(() => commit(url), 6000);
    toasts.current[url] = toast("Deleted", {
      duration: 6000,
      action: { label: "Undo", onClick: () => { clearTimeout(timers.current[url]); delete timers.current[url]; setHidden((h) => h.filter((x) => x !== url)); toast.success("Restored"); } },
    });
  };
  const exportIt = async () => {
    try {
      const { data } = await api.get(`/people/${p.id}/export`);
      const a = document.createElement("a");
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      a.href = url;
      a.download = `imladri-${p.nickname}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) {
      toast.error(errMsg(failure));
    }
  };
  if (!d || error) return <MemberLoadState label="the saved data" error={error} retry={load} />;
  const keep = (url) => !hidden.includes(url);
  const details = d.details.filter((x) => keep(`/people/${p.id}/details/${x.key}`));
  const report = d.report && keep(`/people/${p.id}/report`);
  const group = { api, pid: p.id, revision: d, hidden };
  return (
    <div className="space-y-4" data-testid="saved-tab">
      <div className="flex flex-wrap items-end gap-3">
        <div className="basis-full sm:basis-auto flex-1 min-w-0">
          <h2 className="font-display font-black uppercase text-3xl inline-flex items-center gap-2"><ShieldCheck className="w-6 h-6" />Everything saved about {p.nickname}</h2>
          <p className="text-sm text-[var(--muted)]">Delete an item to stop using it in advice. You have a few seconds to undo. For screenshots, Imladri keeps the text you submit.</p>
        </div>
        <button onClick={exportIt} className="btn-ghost px-4 py-2 text-sm inline-flex items-center gap-1.5" data-testid="saved-export"><Download className="w-4 h-4" />Export {p.nickname}</button>
        <DeletePerson p={p} api={api} onDone={() => nav("/app/people")} trigger={<button className="px-4 py-2 text-sm rounded-full border border-[var(--crimson)] text-[var(--crimson)]" data-testid="saved-delete-all">Delete everything about {p.nickname}</button>} />
      </div>
      <div className="grid lg:grid-cols-2 gap-4">
        <Group title="Details" count={details.length + (report ? 1 : 0)} testid="saved-details">
          {details.map((x) => <Row key={x.key} text={`${x.label}: ${Array.isArray(x.value) ? x.value.join(", ") : x.value}`} onDelete={() => del(`/people/${p.id}/details/${x.key}`)} testid={`saved-detail-${x.key}`} />)}
          {report && <Row text="The AI-written read (report)" sub="It will be rewritten next time you share something" onDelete={() => del(`/people/${p.id}/report`)} testid="saved-report" />}
        </Group>
        <SavedGroup {...group} section="memories" title="Memories">
          {(m) => <Row key={m.id} text={m.text} sub={`${m.kind} · ${day(m.created_at)}`} onDelete={() => del(`/memories/${m.id}`)} testid={`saved-memory-${m.id}`} />}
        </SavedGroup>
        <SavedGroup {...group} section="exchanges" title="Conversations & answers">
          {(e) => <Row key={e.id} text={e.question || `${e.messages} messages`} sub={[day(e.created_at), e.messages ? `${e.messages} messages` : "", e.choice ? `chose: ${e.choice}` : "", e.outcome ? `result: ${e.outcome}` : ""].filter(Boolean).join(" · ")} onDelete={() => del(`/exchanges/${e.id}`)} testid={`saved-exchange-${e.id}`} />}
        </SavedGroup>
        <SavedGroup {...group} section="preps" title="Game plans">
          {(x) => <Row key={x.id} text={x.situation} sub={day(x.created_at)} onDelete={() => del(`/preps/${x.id}`)} testid={`saved-prep-${x.id}`} />}
        </SavedGroup>
        <SavedGroup {...group} section="corrections" title="Your corrections">
          {(c) => <Row key={c.id} text={c.text} sub={`marked not right${c.note ? `: ${c.note}` : ""} · ${day(c.created_at)}`} onDelete={() => del(`/corrections/${c.id}`)} testid={`saved-correction-${c.id}`} />}
        </SavedGroup>
        <SavedGroup {...group} section="practice" title="Practice sessions">
          {(x) => <Row key={x.id} text={x.topic} sub={`${x.mode === "live" ? "Live" : "Likely replies"} · ${day(x.created_at)}`} onDelete={() => del(`/practice/${x.id}`)} testid={`saved-practice-${x.id}`} />}
        </SavedGroup>
      </div>
      {d.group_chats > 0 && <p className="text-xs text-[var(--muted)]" data-testid="saved-group-note">{p.nickname} is also in {d.group_chats} group chat answer{d.group_chats > 1 ? "s" : ""}. Delete those from the group chat.</p>}
    </div>
  );
}
