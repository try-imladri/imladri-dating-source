import { useEffect, useState } from "react";
import { errMsg } from "@/lib/api";

export default function PromptVersions({ api, onRestore }) {
  const [data,setData] = useState(null);
  const [answers,setAnswers] = useState({});
  const [target,setTarget] = useState("");
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(false);
  const [refresh,setRefresh] = useState(0);
  useEffect(()=>{
    const ctl = new AbortController();
    setData(null); setError(""); setTarget("");
    api.get("/admin/prompts/versions",{signal:ctl.signal}).then(({data})=>{
      if (!ctl.signal.aborted) { setData(data); setAnswers(data.versions[0].review?.answers || {}); }
    }).catch(e=>{ if(!ctl.signal.aborted) setError(errMsg(e)); });
    return ()=>ctl.abort();
  },[api,refresh]);
  async function submit(restore) {
    setBusy(true); setError("");
    const revision = data.versions[0].revision;
    try {
      if (restore) { await api.post("/admin/prompts/rollback",{current:revision==="initial"?null:revision,target}); onRestore(); }
      else await api.post("/admin/prompts/review",{revision,answers:Object.fromEntries(Object.keys(data.cases).map(k=>[k,answers[k]||"unreviewed"]))});
      setRefresh(n=>n+1);
    } catch(e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  }
  return <section className="card p-5 space-y-3 text-sm"><h2 className="font-display font-black text-2xl uppercase">Prompt versions and evaluations</h2>
    {data && <><p>Current version <strong>{data.versions[0].revision}</strong>. {data.versions[0].combinations} feature and relationship combinations resolve to guidance.</p>
      <p>{data.versions[0].candidate ? data.versions[0].candidate.cases+" synthetic candidate responses reviewed before publication." : "This version predates candidate evaluations."}</p>
      <h3 className="font-semibold">Human review · {data.protocol}</h3>
      <p>Use synthetic examples in the app for this published version. Record each observed outcome. Leave any case you have yet to run unreviewed.</p>
      {Object.entries(data.cases).map(([k,label])=><label key={k} className="flex flex-wrap items-center justify-between gap-2">{label}
        <select value={answers[k]||"unreviewed"} disabled={busy} onChange={e=>setAnswers({...answers,[k]:e.target.value})}>
          <option value="unreviewed">Unreviewed</option><option value="pass">Passed</option><option value="fail">Failed</option></select></label>)}
      <button className="btn-ghost px-3 py-1.5" disabled={busy} onClick={()=>submit(false)}>Save review for this version</button>
      {data.versions[0].review && <p>Last review {new Date(data.versions[0].review.at).toUTCString()}.</p>}
      <h3 className="font-semibold">Previous versions</h3>
      {data.versions.length<2 ? <p>History starts with the next publication.</p> : <>
        <label>Version to restore <select value={target} onChange={e=>setTarget(e.target.value)} disabled={busy}><option value="">Choose a version</option>
          {data.versions.slice(1).map(v=><option key={v.revision} value={v.revision}>{v.at?new Date(v.at).toUTCString():"Starting instructions"} · {v.revision.slice(0,12)} · {v.review ? Object.values(v.review.answers).filter(x=>x==="pass").length+" of "+Object.keys(data.cases).length+" passed" : "unreviewed"}</option>)}</select></label>
        <button className="btn-ghost px-3 py-1.5" disabled={busy||!target} onClick={()=>submit(true)}>Publish selected version</button></>}
    </>}
    {error && <p role="alert">{error} <button className="underline" onClick={()=>setRefresh(n=>n+1)}>Reload versions</button></p>}
    {!data && !error && <p role="status">Loading version history…</p>}
  </section>;
}
