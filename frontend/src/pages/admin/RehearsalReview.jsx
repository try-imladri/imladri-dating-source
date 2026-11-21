import { useRef, useState } from "react";

const route = "/admin/research/rehearsals";
const names = { context:"Context", uncertainty:"Uncertainty", boundaries:"Boundaries", action:"Next move", variety:"Branch variety" };

export default function RehearsalReview({api}) {
  const [report,setReport] = useState(null);
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(false);
  const [verified,setVerified] = useState(false);
  const [removing,setRemoving] = useState("");
  const input = useRef(null);
  async function run(operation) {
    setError(""); setBusy(true);
    try { await operation(); }
    catch (err) { setError(typeof err.response?.data?.detail === "string" ? err.response.data.detail : err.message || "Could not open the rehearsal study."); }
    finally { setBusy(false); }
  }
  const download = slot => run(async()=>{
    const {data} = await api.get(route+"/"+slot,{responseType:"blob"});
    const url=URL.createObjectURL(data), a=document.createElement("a");
    a.href=url; a.download="Imladri rehearsal reader "+slot+".html"; a.click();
    setTimeout(()=>URL.revokeObjectURL(url),2000);
  });
  function record(event) {
    event.preventDefault();
    const file=input.current?.files[0];
    if (!file || !verified) return;
    run(async()=>{
      if (file.size>50000) throw Error("Choose the JSON file downloaded by the reader.");
      let ballot;
      try { ballot=JSON.parse(await file.text()); } catch { throw Error("Choose a valid rehearsal ballot JSON file."); }
      setReport((await api.post(route,{ballot,verified:true})).data);setVerified(false);if(input.current) input.current.value="";
    });
  }
  const withdraw = () => run(async()=>{
    setReport((await api.delete(route+"/"+report.edition+"/"+removing)).data);setRemoving("");
  });
  return <section className="card p-4 sm:p-5 space-y-3" aria-label="Independent rehearsal readings">
    <h2 className="font-display text-xl font-bold uppercase">Independent rehearsal readings</h2>
    <p className="text-sm">Give each adult reader a different offline packet. Both see the same six fictional situations and 18 possible branches in different orders. They return their ballots separately. Only the assigned reader and the staff member receiving their file need to handle it.</p>
    <button className="btn-ghost px-3 py-2 text-sm" disabled={busy} onClick={()=>run(async()=>setReport((await api.get(route)).data))}>{report ? "Refresh rehearsal readings" : "Open rehearsal study"}</button>
    {error && <p role="alert">{error}</p>}
    {report && <><p className="text-sm" role="status">Edition {report.edition} · {report.received.length} of 2 ballots available · {report.status}.</p>
      <div className="flex flex-wrap gap-2">{["A","B"].filter(slot=>!report.withdrawn.includes(slot)).map(slot=><button key={slot} disabled={busy} className="btn-ghost px-3 py-2 text-sm" onClick={()=>download(slot)}>Download rehearsal packet {slot}</button>)}</div>
      {report.received.length+report.withdrawn.length < 2 && <form onSubmit={record} className="space-y-2">
        <label className="block text-sm">Returned rehearsal ballot <input ref={input} required type="file" accept=".json,application/json" onChange={()=>setVerified(false)} /></label>
        <label className="block text-sm"><input type="checkbox" checked={verified} onChange={e=>setVerified(e.target.checked)} /> I verified this adult reader worked independently, has their own slot, and gave permission to record this ballot.</label>
        <button disabled={busy||!verified} className="btn-lime px-3 py-2 text-sm">Import rehearsal ballot</button>
      </form>}
      {report.received.length===2 && <div className="space-y-2 text-sm">
        <p>Exact agreement across jointly scored ratings: {report.matched_total} / {report.paired_total}. The sample covers fictional situations.</p>
        {report.criteria.map(row=><div key={row.id}><h3 className="font-semibold">{names[row.id]}</h3><p>Both scored {row.paired} of {row.per_reader}. {row.matching} matched.</p>
          {Object.entries(row.readers).map(([slot,v])=><p key={slot}>Reader {slot} · {v.full} fully met, {v.mixed} mixed, {v.unmet} unmet, {v.unknown} could not judge, {v.skipped} skipped.</p>)}</div>)}
        {Object.entries(report.concern_coverage).map(([slot,v])=><p key={slot}>Reader {slot} checked {v.checked} of 6 cases for critical concerns. {v.unknown} could not judge and {v.skipped} skipped.</p>)}
        {report.concerns.map(c=><p key={c.case+c.reader}><strong>Flagged case {c.case}, reader {c.reader}</strong> · {c.reason}</p>)}
        <details><summary>Scoring thresholds</summary><p>Each reader must score at least 15 of 18 branches on each criterion and five of six sets for variety, with at least 80% fully met on every measure. At least 80% of jointly scored judgments must match. All six critical-concern checks from both readers must be complete and free of flags. This study describes this sample only.</p></details>
      </div>}
      {report.withdrawn.length>0 && <p className="text-sm">Withdrawn slots: {report.withdrawn.join(", ")}. Their answers and reasons have been erased.</p>}
      {report.received.map(slot=><button key={slot} disabled={busy} className="btn-ghost px-3 py-2 text-sm mr-2" onClick={()=>setRemoving(slot)}>Withdraw reader {slot}</button>)}
      {removing && <div className="space-y-2 text-sm" role="group" aria-label="Confirm reader withdrawal"><p>Erase reader {removing}’s answers and remove their scores? Their old file cannot be imported again for this edition.</p><button className="btn-ghost px-3 py-2" disabled={busy} onClick={()=>setRemoving("")}>Keep ballot</button> <button className="btn-ghost px-3 py-2" disabled={busy} onClick={withdraw}>Confirm withdrawal</button></div>}
    </>}
  </section>;
}
