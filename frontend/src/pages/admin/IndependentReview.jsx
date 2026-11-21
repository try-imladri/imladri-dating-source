import { useRef, useState } from "react";
const ballotError = e => typeof e.response?.data?.detail === "string" ? e.response.data.detail : e.message || "Could not open the reader study.";

const labels = { tentative: "Tentative figure matches", holdout: "Previously unreviewed figures", tone: "Fictional tone examples" };

export default function IndependentReview({ api }) {
  const [report, setReport] = useState(null), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [verified, setVerified] = useState(false);
  const file = useRef(null);
  async function run(fn) {
    setBusy(true); setError("");
    try { await fn(); } catch (e) { setError(ballotError(e)); } finally { setBusy(false); }
  }
  const load = () => run(async () => setReport((await api.get("/admin/research/readers")).data));
  const download = slot => run(async () => {
    const { data } = await api.get("/admin/research/readers/" + slot, {responseType: "blob"});
    const url = URL.createObjectURL(data), link = document.createElement("a");
    link.href = url; link.download = "Imladri reader " + slot + ".html"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  });
  function record(event) {
    event.preventDefault();
    const ballot = file.current?.files?.[0];
    if (!ballot || !verified) return;
    run(async () => {
      if (ballot.size > 750000) throw Error("This file is too large for a reader ballot.");
      let data;
      try { data = JSON.parse(await ballot.text()); } catch { throw Error("Choose the JSON ballot downloaded by the reader."); }
      const saved = await api.post("/admin/research/readers", data);
      setReport(saved.data); setVerified(false); file.current.value = "";
    });
  }
  return <section className="card p-4 sm:p-5 space-y-3">
    <h2 className="font-display text-xl font-bold uppercase">Independent human readings</h2>
    <p className="text-sm">Give one packet to each of two independent readers. Each contains career sources, archetype definitions and fictional conversations. Readers can save progress and return a completed ballot. Published labels and the other reader’s answers are hidden.</p>
    <button className="btn-ghost px-3 py-2 text-sm" disabled={busy} onClick={load}>{report ? "Refresh readings" : "Open reader study"}</button>
    {error && <p role="alert">{error}</p>}
    {report && <><p className="text-sm">Study edition {report.edition} · {report.received.length} of 2 ballots received.</p>
      <div className="flex flex-wrap gap-2">{["A","B"].map(slot => <button key={slot} disabled={busy} className="btn-ghost px-3 py-2 text-sm" onClick={()=>download(slot)}>Download reader {slot} packet</button>)}</div>
      {report.received.length < 2 && <form onSubmit={record} className="space-y-2">
        <label className="block text-sm">Completed ballot <input ref={file} type="file" required accept=".json,application/json" /></label>
        <label className="block text-sm"><input type="checkbox" checked={verified} onChange={e=>setVerified(e.target.checked)} /> I verified this slot belongs to a different person from the other slot and the reader worked independently.</label>
        <button disabled={busy || !verified} className="btn-lime px-3 py-2 text-sm">Record ballot</button>
      </form>}
      <p className="text-sm">The frozen sample contains every profile flagged tentative at setup and a separate 16-case holdout. Two readers can describe agreement on the examples they assess. The study does not measure the accuracy of all 500 profiles or what a person is like privately.</p>
      {report.guide_entries && <p className="text-sm">The ballot offers {report.selectable_names} names across {report.guide_entries} guide entries. A name used in both editions can establish agreement only on the common label.</p>}
      <ul className="text-sm">{report.groups.map(g=><li key={g.group}><strong>{labels[g.group]}</strong> · {g.total} cases · {g.both_assessed} assessed by both · {g.agree} matching readings{g.both_uncertain ? ` (including ${g.both_uncertain} where both found insufficient evidence)` : ""}. {report.received.length === 2 && <span>{g.reference_matches} of {g.rated_votes} individual readings matched the {g.group === "tone" ? "model’s recorded tone" : "published lead"}.</span>}{g.shared_reference_names>0 && <span> {g.shared_reference_names} case references use a name shared by both editions.</span>}</li>)}</ul>
      {report.cases.length>0 && <details><summary>Compare individual readings</summary><ul className="space-y-3 mt-2">{report.cases.map(r=><li key={r.name} className="text-sm">
        <strong>{r.slug ? <a className="underline" href={"/figures/"+r.slug}>{r.name}</a> : r.name}</strong>
        {r.reference && <p>Study reference · {r.reference}</p>}{Object.entries(r.readings).map(([slot,v])=><p key={slot}>{slot} · {v.choice}. {v.reason}</p>)}
      </li>)}</ul></details>}
    </>}
  </section>;
}