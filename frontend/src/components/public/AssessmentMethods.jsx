import { useState } from "react";
import { publicApi } from "@/lib/api";

export default function AssessmentMethods() {
  const [result, setResult] = useState(null);
  const [phase, setPhase] = useState("idle");
  async function load() {
    setPhase("loading");
    try { const {data}=await publicApi.get("/public/research/repeat"); setResult(data); setPhase("ready"); }
    catch { setResult(null); setPhase("error"); }
  }
  return <section id="assessment-methods" className="space-y-3">
    <h2>How we check questionnaire results</h2>
    <p>Each scored Everyday choice contributes 1–4 points to its named dimension. The profile averages the available scored choices for that dimension and displays the result on a 0–100 scale. Descriptive choices can add context. A higher score means more of that particular trait.</p>
    <p>You can start the Basics and update your regular answers anytime. For an optional consistency check, adults can complete two fresh copies 7–28 days apart in Personal → Personality. We compare answers to the same question and scoring version. The percentage shows how often returning participants gave the same answer. Cohen’s κ also accounts for how often choices would agree by chance. These figures measure consistency among participants.</p>
    <p className="text-sm"><a className="underline" href="https://doi.org/10.1177/001316446002000104">Cohen’s agreement method</a></p>
    <button type="button" disabled={phase === "loading"} className="btn-ghost px-4 py-2 text-sm" onClick={load}>{phase === "ready" ? "Refresh repeat results" : "See repeat results"}</button>
    {phase === "loading" && <p role="status">Loading repeat results…</p>}
    {phase === "error" && <p role="alert">Repeat results couldn’t load. Please try again.</p>}
    {result && phase === "ready" && <div className="card p-4 text-sm space-y-2">
      <p>Results require at least {result.minimum} consenting adults on the current form and {result.minimum_cell} in every nonempty answer pairing. We round each count to the nearest {result.round_to} and use rounded answer-pair counts to estimate agreement and κ.</p>
      {result.repeat.length === 0 ? <p role="status">No items meet the publication thresholds yet.</p> : <ul>{result.repeat.map(q=><li key={q.number}>Basics item {q.number} · {q.axis} · {q.same_percent}% repeated the same answer · κ {q.kappa ?? "undefined"} · about {q.sample} adults.</li>)}</ul>}
    </div>}
  </section>;
}
