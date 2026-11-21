import { useState } from "react";

function Outcomes({ title, data }) {
  return <div className="rounded-xl border border-[var(--line)] p-3">
    <h4 className="font-semibold">{title}</h4>
    {data.available ? <div className="space-y-1 text-sm">
      <p>{data.helped_percent == null ? "No reported post-send outcomes" : `${data.helped_percent}% of respondents said their sent reply helped`}</p>
      <p>{data.answered} reported an outcome from {data.sample} eligible reads.</p>
      <p>Helped {data.groups.helped} · Didn’t help {data.groups.did_not_help} · Sent, outcome missing {data.groups.awaiting_outcome} · Didn’t send {data.groups.not_sent} · Send status missing {data.groups.send_unknown}</p>
    </div> : <p className="text-sm text-[var(--muted)]">Withheld until the sample and group thresholds are met.</p>}
  </div>;
}

export default function AssessmentEvidence({ api }) {
  const [data,setData] = useState(null);
  const [phase,setPhase] = useState("idle");
  async function load() {
    setPhase("loading");
    try { const response=await api.get("/admin/research/evidence"); setData(response.data); setPhase("ready"); }
    catch { setData(null); setPhase("error"); }
  }
  return <section className="card p-4 sm:p-5 space-y-3">
    <h2 className="font-display text-xl font-bold uppercase">Questionnaire reliability and coaching outcomes</h2>
    <p className="text-sm text-[var(--muted)]">Check consistency across scored questions, repeatability over time, and how members rated a reply after sending it.</p>
    <button type="button" className="btn-ghost px-3 py-1.5 text-sm" disabled={phase === "loading"} onClick={load}>{data ? "Refresh evidence" : "Load evidence"}</button>
    {phase === "loading" && <p role="status">Loading evidence…</p>}
    {phase === "error" && <p role="alert">Evidence couldn’t load. Try again.</p>}
    {phase === "ready" && <div className="space-y-4">
      <p className="text-xs text-[var(--muted)]">As of {new Date(data.as_of).toLocaleString()}. Reports require {data.minimum} eligible units. Each nonempty category must include at least {data.minimum_cell}. We round group counts and totals separately to the nearest {data.round_to}, so group counts may not add up to the displayed totals. Percentages and κ use rounded group counts.</p>
      <h3 className="font-semibold">Everyday questionnaire · internal consistency</h3>
      <p className="text-xs text-[var(--muted)]">Scoring version {data.questionnaire.scale_version?.slice(0,12)}</p>
      <a className="underline text-sm" href="https://doi.org/10.1007/BF02310555">Cronbach’s coefficient alpha method</a>
      <p className="text-sm">Cronbach’s alpha uses consenting adults with a current answer to every included item of that axis. Only questions with a score on every option enter the calculation. Items span all three Everyday sets. A coefficient measures consistency within this group; evidence of real-world predictive accuracy requires independent outcomes.</p>
      <ul className="space-y-1 text-sm">{data.questionnaire.scales.map(s=><li key={s.name}><strong>{s.name}</strong> · {s.items} scored items · {s.alpha == null ? (s.items < 2 ? "too few scored items" : s.sample ? "no variation in total scores" : "sample below threshold") : `α ${s.alpha.toFixed(2)}`}{s.sample != null && ` · about ${s.sample} adults`}</li>)}</ul>
      <h3 className="font-semibold">Basics · participation and return</h3>
      <p className="text-sm">First attempt per consenting adult on this edition. Restarting cannot erase a missed return. The return rate uses only volunteers whose full 28-day window has closed; withdrawal removes their observations.</p>
      {data.questionnaire.enrollment?.available ? <div className="text-sm">
        <p>About {data.questionnaire.enrollment.first} started among {data.questionnaire.enrollment.consenting} consenting adults.</p>
        <p>Waiting for day 7: {data.questionnaire.enrollment.groups.waiting} · Window open: {data.questionnaire.enrollment.groups.open} · Already returned in an open window: {data.questionnaire.enrollment.groups.completed_early}.</p>
        <p>Of {data.questionnaire.enrollment.mature} completed windows, {data.questionnaire.enrollment.groups.completed_mature} returned and {data.questionnaire.enrollment.groups.expired} did not. {data.questionnaire.enrollment.completion_percent != null && <strong>Return rate: {data.questionnaire.enrollment.completion_percent}%.</strong>}</p>
      </div> : <p className="text-sm text-[var(--muted)]">Enrollment and return counts are withheld until the sample and each nonempty group meet reporting thresholds.</p>}
      <h3 className="font-semibold">Basics · repeat agreement</h3>
      <p className="text-xs text-[var(--muted)]">Form version {data.questionnaire.instrument?.slice(0,12)}</p>
      <p className="text-sm">One independently completed pair per consenting adult using the same published form, 7–28 days apart. Exact agreement compares the two choices. Cohen’s κ adjusts for agreement expected from choice frequencies. A constant response has no defined κ. These statistics describe the volunteers who returned.</p>
      <ul className="space-y-1 text-sm">{data.questionnaire.repeat.map(q=><li key={q.number}><strong>Item {q.number}</strong> · {q.axis} · {q.sample == null ? "below reporting thresholds" : `${q.same_percent}% same choice · κ ${q.kappa ?? "undefined"} · about ${q.sample} adults`}</li>)}</ul>
      {data.intimacy_wording && <div className="space-y-2 text-sm"><h3 className="font-semibold">Did the intimacy summary preserve the member’s meaning?</h3>
        <p>One voluntary rating per adult and current wording edition. The eligible population here is respondents. Group counts are rounded.</p>
        {Object.entries(data.intimacy_wording.measures).map(([key,bins])=><p key={key}><strong>{key==="conditions"?"Conditional answers":"Boundaries"}</strong> · {bins ? <>{bins.faithful} accurate, {bins.partly} partly accurate, {bins.missed} missed, {bins.unsure} could not judge, {bins.inapplicable} did not apply and {bins.skipped} skipped.</> : "Withheld until reporting thresholds are met."}</p>)}
        <details><summary>Wording edition</summary><p className="break-all">{data.intimacy_wording.edition}</p></details>
      </div>}
      <h3 className="font-semibold">Did the tone estimate fit?</h3>
      <p className="text-sm">The latest eligible read per consenting member or shared pair counts immediately and stays in the reporting window for 90 days. The correction rate uses only members who rated an estimate. An insufficient reading and a missing rating are reported separately.</p>
      {Object.entries(data.tone_fit || {}).map(([key,r])=><p key={key} className="text-sm"><strong>{key === "couples" ? "Shared Couples" : "Individual"}: </strong>{r.available ? <>{r.groups.fits} said it fit · {r.groups.corrected} corrected it · {r.groups.insufficient} insufficient readings · {r.groups.unanswered} unrated estimates (about {r.sample} total). {r.corrected_percent != null && <span>{r.corrected_percent}% corrected among ratings.</span>}</> : "Withheld until reporting thresholds are met."}</p>)}
      {data.shared_tone_raters && <div className="space-y-1 text-sm"><h3 className="font-semibold">Did both partners rate their shared tone read?</h3>
        <p>The most recent eligible shared conversation per active pair in the past 90 days, including today. Both partners must have opted in before the read. Ratings refer to each partner’s view of the wording they currently share.</p>
        <p>{data.shared_tone_raters.available ? <>About {data.shared_tone_raters.sample} pairs. Both partners rated {data.shared_tone_raters.groups.both}, one rated {data.shared_tone_raters.groups.one}, and neither rated {data.shared_tone_raters.groups.neither}.</> : "Withheld until reporting thresholds are met."}</p>
      </div>}
      <h3 className="font-semibold">Did a sent reply help?</h3>
      <p className="text-sm">The most recent eligible read per member or active pair in the past 90 days, including today. The percentage uses only people who reported both sending a suggested reply and whether it helped. Pair reporting requires both partners’ prior tone consent. People who gave feedback may differ from those who did not.</p>
      <div className="grid gap-2 sm:grid-cols-2"><Outcomes title="Individual members" data={data.individual} /><Outcomes title="Shared Couples conversations" data={data.couples} /></div>
    </div>}
  </section>;
}
