import { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import NotificationContent from "@/components/app/NotificationContent";
import { errMsg } from "@/lib/api";

const blank = { title: "", body: "", link: "/app" };
const pageSize = 50;
const action = "underline disabled:opacity-50";

export default function NotificationsAdmin({ api }) {
  const [draft, setDraft] = useState(blank);
  const [requestId, setRequestId] = useState(null);
  const [sending, setSending] = useState(false);
  const [preview,setPreview]=useState(null);
  const [notice, setNotice] = useState("");
  const [sendError, setSendError] = useState("");
  const [list, setList] = useState(null);
  const [error, setError] = useState("");
  const [pages, setPages] = useState([null]);
  const [reload, setReload] = useState(0);
  const cursor = pages[pages.length - 1];
  useEffect(() => {
    const ctl = new AbortController();
    setList(null); setError("");
    api.get("/admin/announcements", { signal: ctl.signal, params: { limit: pageSize + 1, after_time: cursor?.created_at, after_id: cursor?.id } })
      .then(({ data }) => { if (!ctl.signal.aborted) setList(data); })
      .catch(e => { if (!ctl.signal.aborted) setError(errMsg(e)); });
    return () => ctl.abort();
  }, [api, cursor, reload]);
  function change(field, value) {
    setDraft(current => ({ ...current, [field]: value }));
    setRequestId(null); setNotice(""); setSendError(""); setPreview(null);
  }
  async function seeAudience() {
    setSending(true);setSendError("");setNotice("");
    try {setPreview((await api.post("/admin/announce/preview",draft)).data);}
    catch(e){setSendError(errMsg(e));}finally{setSending(false);}
  }
  async function send(pending) {
    const id = pending ? pending.id.slice("announce:".length) : requestId || crypto.randomUUID();
    if (!pending) setRequestId(id);
    setSending(true); setSendError(""); setNotice("");
    try {
      const { title, body, link } = pending || draft;
      const asOf=pending?.audience_as_of || preview?.as_of;
      await api.post("/admin/announce", { title, body, link, request_id:id, ...(asOf ? {audience_as_of:asOf} : {}) });
      if (!pending || id === requestId) { setDraft(blank); setRequestId(null); setPreview(null); }
      setNotice("Announcement sent to Individual inboxes.");
      setPages([null]);
    } catch (e) {
      setSendError(errMsg(e));if(e.response?.status===409 && !pending)setPreview(null);
    } finally {
      setSending(false);
      setReload(n => n + 1);
    }
  }
  return <div className="grid lg:grid-cols-2 gap-3 h-full min-h-0" data-testid="admin-notifications">
    <form className="card p-5 space-y-3 self-start" onSubmit={event => { event.preventDefault(); if(preview)send();else seeAudience(); }}>
      <h2 className="font-display font-black uppercase text-xl">Send an announcement</h2>
      <label className="block text-sm">Title<Input className="mt-1.5" required maxLength={120} disabled={sending} value={draft.title} onChange={e => change("title", e.target.value)} data-testid="admin-announce-title" /></label>
      <label className="block text-sm">Message<Textarea className="mt-1.5" rows={3} maxLength={400} disabled={sending} value={draft.body} onChange={e => change("body", e.target.value)} data-testid="admin-announce-body" /></label>
      <label className="block text-sm">Imladri page link<Input className="mt-1.5" maxLength={200} disabled={sending} value={draft.link} onChange={e => change("link", e.target.value)} data-testid="admin-announce-link" /></label>
      {preview && <section className="border border-[var(--line)] rounded-xl p-3 space-y-2" aria-label="Individual inbox preview">
        <h3 className="font-semibold">Individual inbox preview</h3>
        <div className="flex gap-3"><NotificationContent item={{...preview,created_at:preview.as_of,read:false}} /></div>
        <p className="text-xs break-all">Opens {preview.link}</p>
        <p className="text-sm">{preview.eligible_inboxes} eligible inboxes as of {new Date(preview.as_of).toLocaleString()}. Closed accounts are skipped at delivery.</p>
      </section>}
      <button disabled={sending || !draft.title.trim() || (preview && !preview.eligible_inboxes)} className="px-6 py-2.5 rounded-full bg-[var(--ink)] text-[var(--bg)] font-bold disabled:opacity-40" data-testid="admin-announce-send">
        {sending ? "Working…" : preview ? "Send to Individual inboxes" : "Preview audience and message"}</button>
      {notice && <p role="status">{notice}</p>}
      {sendError && <p role="alert">{sendError}</p>}
    </form>
    <section className="card relative flex-1 min-h-0 overflow-y-auto divide-y divide-[var(--line)]" data-testid="admin-announce-history">
      <div className="p-4 flex items-center justify-between gap-2">
        <h2 className="font-display font-black uppercase text-xl">Announcement history</h2>
        <button className={action} onClick={() => setReload(n => n + 1)}>Refresh</button>
      </div>
      {error && <p className="p-4" role="alert">{error}</p>}
      {list === null && !error && <p className="p-4" role="status">Loading history…</p>}
      {list?.length === 0 && <p className="p-4 text-sm">No announcements on this page.</p>}
      {list?.slice(0, pageSize).map(a => <article key={a.id} className="p-4 text-sm space-y-1">
        <h3 className="font-semibold">{a.title}</h3>
        {a.body && <p>{a.body}</p>}
        <p className="text-xs text-[var(--muted)]">{new Date(a.created_at).toLocaleString()} · {a.link}</p>
        <p>{a.inboxes} current inboxes · {a.read} marked read</p>
        {Number.isInteger(a.inboxes_at_completion) && <p className="text-xs">Inboxes at delivery completion · {a.inboxes_at_completion}</p>}
        {a.status === "pending" && <button disabled={sending} className={action} onClick={() => send(a)}>Resume delivery</button>}
      </article>)}
      {(cursor || list?.length > pageSize) && <nav aria-label="Announcement pages" className="p-4 flex items-center gap-3 text-sm">
        <button disabled={!cursor || !list} className={action} onClick={() => setPages(p => p.slice(0, -1))}>Previous</button>
        <span>Page {pages.length}</span>
        <button disabled={!list || list.length <= pageSize} className={action} onClick={() => setPages(p => [...p, list[pageSize - 1]])}>Next</button>
      </nav>}
    </section>
  </div>;
}
