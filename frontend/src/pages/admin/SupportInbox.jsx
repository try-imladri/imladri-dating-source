import { useEffect, useState } from "react";
import { Textarea } from "@/components/ui/textarea";
import { errMsg } from "@/lib/api";

function duration(start,end) {
  const minutes=Math.max(0,Math.floor((new Date(end)-new Date(start))/60000));
  if(minutes<60) return minutes+" min";
  if(minutes<1440) return Math.floor(minutes/60)+" hr";
  return Math.floor(minutes/1440)+" d";
}

export default function SupportInbox({ api }) {
  const [status, setStatus] = useState("open");
  const [rows, setRows] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [error, setError] = useState("");
  const [listError, setListError] = useState("");
  const [busy, setBusy] = useState(null);
  const [refresh, setRefresh] = useState(0);
  const [metrics,setMetrics]=useState(null),[metricError,setMetricError]=useState(false);
  useEffect(()=>{
    let active=true;
    setMetricError(false);
    api.get("/admin/support/summary").then(({data})=>{if(active)setMetrics(data);})
      .catch(()=>{if(active){setMetrics(null);setMetricError(true);}});
    return ()=>{active=false;};
  },[api,refresh]);
  const [pages, setPages] = useState([null]);
  const cursor = pages[pages.length - 1];
  const pageSize = 50;
  useEffect(() => {
    let active = true;
    setRows(null);
    setListError("");
    api.get("/admin/support", { params: { status, limit: pageSize + 1, after_time: cursor?.created_at, after_id: cursor?.id } })
      .then(({ data }) => { if (active) setRows(data); })
      .catch(e => { if (active) setListError(errMsg(e)); });
    return () => { active = false; };
  }, [api, status, refresh, cursor]);
  async function assignment(row) {
    setBusy(row.id);setError("");
    try {await api.put("/admin/support/"+row.id+"/assignment",{claim:!row.assigned_to_me,expected_owner:row.assigned_to || null});setRefresh(n=>n+1);}
    catch(e){setError(errMsg(e));}finally{setBusy(null);}
  }
  const draftKey = (id, round) => id + "/" + (round || 0);
  const reply = async (id,round) => {
    const key = draftKey(id, round);
    setBusy(id); setError("");
    try {
      await api.put(`/admin/support/${id}`, { answer: drafts[key].trim(), round:round || 0 });
      setDrafts(d => { const next = { ...d }; delete next[key]; return next; });
      setRefresh(n => n + 1);
    } catch (e) {
      if (e.response?.status === 409) setRefresh(n => n + 1);
      setError(errMsg(e));
    }
    finally { setBusy(null); }
  };
  return (
    <section className="overflow-y-auto space-y-4" data-testid="support-inbox">
      {metrics && <div className="card p-4 text-sm"><p><strong>{metrics.open}</strong> requests awaiting reply</p>
        <p>{metrics.mean_first_response_minutes==null?"No timed first replies yet":metrics.mean_first_response_minutes+" min average to first reply"} · {metrics.answered_with_time} timed requests opened in the last 30 days.</p></div>}
      {metricError && <p role="alert">Support totals couldn’t load. <button className="underline" onClick={()=>setRefresh(n=>n+1)}>Retry</button></p>}
      <div className="flex flex-wrap gap-4 text-sm items-center">
        <label>Requests <select value={status} onChange={e => { setStatus(e.target.value); setPages([null]); }} className="bg-[var(--bg)] border border-[var(--line)] p-1 rounded"><option value="open">Awaiting reply</option><option value="answered">Answered</option></select></label>
        <button className="underline" onClick={() => { setError(""); setRefresh(n => n + 1); }}>Refresh</button>
        {(cursor || rows?.length > pageSize) && <nav aria-label="Support pages" className="flex items-center gap-3">
          <button className="underline disabled:opacity-40" disabled={!cursor || !rows} onClick={() => setPages(p => p.slice(0, -1))}>Previous</button>
          <span>Page {pages.length}</span>
          <button className="underline disabled:opacity-40" disabled={!rows || rows.length <= pageSize} onClick={() => setPages(p => [...p, rows[pageSize - 1]])}>Next</button>
        </nav>}
      </div>
      {error && <p role="alert">{error}</p>}
      {listError && <p role="alert">{listError}</p>}
      {rows === null && !listError && <p role="status">Loading requests…</p>}
      {rows?.length === 0 && <p>No {status} requests.</p>}
      {rows?.slice(0, pageSize).map(r => <article key={r.id} className="card p-4 space-y-3">
        <h2 className="font-semibold">{r.category} <span className="text-sm font-normal text-[var(--muted)]">{new Date(r.created_at).toLocaleString()}</span></h2>
        <p className="whitespace-pre-wrap break-words">{r.message}</p>
        {(r.thread || (r.answer ? [{speaker:"support",text:r.answer}] : [])).map((entry,i)=><p key={i} className="whitespace-pre-wrap break-words"><strong>{entry.speaker==="support" ? "Reply" : "Follow-up"}</strong> {entry.text}</p>)}
        <p className="text-xs text-[var(--muted)]">{r.status==="open" && <>Awaiting reply for {duration(r.open_since || r.created_at,Date.now())} · </>}
          {r.first_answered_at ? "First response in "+duration(r.created_at,r.first_answered_at) : (r.answer || r.round ? "First response time unavailable" : "Awaiting first response")}</p>
        {r.status==="open" && <><div className="flex gap-3 text-sm"><span>{r.assigned_to ? (r.assigned_to_me?"Assigned to you":"Assigned to "+(r.assigned_name || "staff")) : "Unassigned"}</span>
          <button className="underline" disabled={busy!==null} onClick={()=>assignment(r)}>{r.assigned_to_me?"Release assignment":r.assigned_to?"Reassign to me":"Assign to me"}</button></div>
          <label className="block text-sm">Reply to this request<Textarea className="mt-1" maxLength={2400} value={drafts[draftKey(r.id, r.round)] || ""} onChange={e => setDrafts(d => ({...d, [draftKey(r.id, r.round)]: e.target.value}))} /></label>
          <button className="btn-lime px-4 py-2 text-sm disabled:opacity-50" disabled={busy !== null || (r.assigned_to && !r.assigned_to_me) || !drafts[draftKey(r.id, r.round)]?.trim()} onClick={() => reply(r.id,r.round)}>Post reply</button>
        </>}
      </article>)}
    </section>
  );
}
