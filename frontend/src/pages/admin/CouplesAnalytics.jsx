import { useEffect, useState } from "react";
import CouplesAnswerHistory from "@/components/admin/CouplesAnswerHistory";
import { errMsg } from "@/lib/api";

const panel = "card space-y-4 p-4 sm:p-5";
const title = "font-display font-black uppercase text-xl";
const field = "w-full rounded-xl border border-[var(--line)] bg-[var(--bg)] px-3 py-2 text-sm";
const button = "btn-ghost px-4 py-2 text-sm disabled:opacity-50";

function Cohort({ label, rate, observed, note }) {
  const pct = rate.eligible ? Math.round(rate.count * 100 / rate.eligible) + "%" : "—";
  const display = observed ?? rate;
  return <div className="rounded-xl border border-[var(--line)] p-3">
    <p className="text-sm font-semibold">{label}</p>
    <p className="font-display text-2xl">{display.count} / {display.eligible} <span className="font-sans text-xs text-[var(--muted)]">{observed ? "observed so far" : pct}</span></p>
    <p className="text-xs text-[var(--muted)]">{note}</p>
    {observed && <details className="text-xs text-[var(--muted)] mt-2">
      <summary className="cursor-pointer">Completed windows</summary>
      <p>{rate.count} / {rate.eligible} · {pct}</p>
    </details>}
  </div>;
}

function ScheduledReturns({ data }) {
  const observed = data.observed_so_far;
  const summary = counts => `${counts.returned} with a return · ${counts.both_returned} with both partners returning · ${counts.both_shared} with both check-ins shared.`;
  return <div className="space-y-1 text-sm">
    {observed && <p>{observed.eligible} due so far · {summary(observed)}</p>}
    <p className="text-xs text-[var(--muted)]">{data.pending} open review windows.</p>
    <details open={observed ? undefined : true} className="text-xs text-[var(--muted)]">
      <summary className="cursor-pointer">Completed review windows</summary>
      <p>{data.eligible} completed · {summary(data)}</p>
    </details>
  </div>;
}

function Funnel({ data }) {
  return <section className={panel} data-testid="couples-admin-funnel">
    <h2 className={title}>Partner journeys</h2>
    <p className="text-sm text-[var(--muted)]">Cohorts starting {data.start} through {data.end} UTC. {data.observed_so_far ? "Each count includes sponsors or pairs as soon as they reach the starting step. Open windows can still gain completions." : "Each rate uses sponsors or pairs whose full observation window has elapsed."}</p>
    <div className="grid gap-2 sm:grid-cols-2">
      <Cohort label="First agreement and two shared check-ins" rate={data.completion} observed={data.observed_so_far?.completion} note={`Within 14 days of confirming the pair. ${data.pending_14d} more pairs are still inside that window.`} />
      <Cohort label="Invitation to confirmed pair" rate={data.pairing} observed={data.observed_so_far?.pairing} note="Sponsors whose first invitation led to a confirmed pair within seven days." />
      <Cohort label="Both shared a conversation" rate={data.mutual} observed={data.observed_so_far?.mutual} note={`Within seven days of pairing. ${data.one_share} pairs had only one side share by the end of a completed window.`} />
      <Cohort label="First shared conversation to agreement" rate={data.adoption} observed={data.observed_so_far?.adoption} note="Both accepted the same revision within seven days of the first mutual share." />
      <Cohort label="Started another shared conversation" rate={data.return} observed={data.observed_so_far?.return} note="Within 30 days of both partners sharing their first check-in." />
    </div>
    <dl className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-sm">
      {[
        ["Plus members first shown the invitation option", data.offered], ["Members who saved a first answer", data.started],
        ["Sponsors who issued a first invite", data.issued], ["Currently awaiting partner", data.awaiting_accept],
        ["Currently awaiting inviter confirmation", data.awaiting_confirm], ["Pairs confirmed", data.confirmed],
        ["First mutual conversations this period", data.milestones.first_mutual],
        ["First agreements accepted this period", data.milestones.first_agreement],
        ["First agreements with both check-ins this period", data.milestones.both_checked_in],
        ["Median hours to first mutual share for pairs who shared within seven days", data.median_hours ?? "—"],
      ].map(([label, value]) => <div key={label}><dt className="text-xs text-[var(--muted)]">{label}</dt><dd className="text-lg font-bold">{value}</dd></div>)}
    </dl>
    {data.tone_usage && <div className="border-t border-[var(--line)] pt-3" aria-label="Couples tone coaching">
      <h3 className="font-semibold">Couples tone coaching</h3>
      <p className="text-sm text-[var(--muted)]">Distinct currently active pairs using each option in this period. A pair can use both options.</p>
      <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">{[
        ["Used tone coaching", `${data.tone_usage.either} / ${data.tone_usage.active_pairs}`],
        ["Private draft feedback", data.tone_usage.private], ["Shared conversation coaching", data.tone_usage.shared],
      ].map(([label, count]) => <div key={label}><dt className="text-xs text-[var(--muted)]">{label}</dt>
        <dd className="text-lg font-bold">{count}</dd></div>)}</dl>
    </div>}
    {data.connections && <div className="border-t border-[var(--line)] pt-3">
      <h3 className="font-semibold">Connection changes this period</h3>
      <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
        {Object.entries({invitations_accepted: "Invitations accepted by a partner", invitations_expired: "Invitations expired",
          invitations_canceled: "Invitations canceled before pairing", pairs_ended: "Confirmed pairs that ended"}).map(([key, label]) =>
          <div key={key}><dt className="text-xs text-[var(--muted)]">{label}</dt><dd className="text-lg font-bold">{data.connections[key]}</dd></div>)}
      </dl>
    </div>}
    {data.membership && <div className="border-t border-[var(--line)] pt-3" data-testid="couples-membership">
      <h3 className="font-semibold">Couples memberships</h3>
      <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
        {[
          ["Sponsored pairs active today", data.membership.sponsored_pairs],
          ["Partners who upgraded for $10 this period", data.membership.partner_upgrades],
          ["Couples offered the $80 year this period", data.membership.annual_offers],
          ["Accounts that accepted a year this period", data.membership.annual_authorizations],
          ["Accounts that paid $80 this period", data.membership.annual_payments],
        ].map(([label, value]) => <div key={label}><dt className="text-xs text-[var(--muted)]">{label}</dt><dd className="text-lg font-bold">{value}</dd></div>)}
      </dl>
      <p className="mt-2 text-xs text-[var(--muted)]">Each partner accepts individually. Acceptances include years canceled before the charge. Payments count once Stripe confirms them.</p>
    </div>}
    {data.scheduled_returns && <div className="border-t border-[var(--line)] pt-3">
      <h3 className="font-semibold">Return for the first scheduled check-in</h3>
      <p className="text-xs text-[var(--muted)]">Each pair enters on its first UTC review date when both partners opted into tracking before agreeing. A return counts when a partner views the check-in on the review date or during the next seven days.</p>
      <ScheduledReturns data={data.scheduled_returns} />
    </div>}
    {data.card_funnel && <div className="border-t border-[var(--line)] pt-3" data-testid="couples-card-funnel">
      <h3 className="font-semibold">Conversation cards</h3>
      <p className="mt-1 text-xs text-[var(--muted)]">Each eligible adult pair counts once per card first started in this period. The first start can come from the card list or a sample. Later columns count pairs where both shared an answer, accepted an agreement, or shared a check-in on the same agreement. Reading a sample alone does not count.</p>
      <div className="overflow-x-auto mt-3"><table className="w-full text-sm text-left">
        <thead><tr>{["Card", "Started", "From list", "From sample", "Both shared", "Both agreed", "Both reviewed"].map(label => <th scope="col" className="px-2 py-1" key={label}>{label}</th>)}</tr></thead>
        <tbody>{data.card_funnel.map(row => <tr key={row.id} className="border-t border-[var(--line)]"><th scope="row" className="px-2 py-2">{row.name}</th>
          {["started", "from_card", "from_sample", "mutual", "agreed", "reviewed"].map(key => <td key={key} className="px-2 tabular-nums">{row[key]}</td>)}</tr>)}</tbody>
      </table></div>
    </div>}
    {data.card_scheduled_returns && <details className="border-t border-[var(--line)] pt-3">
      <summary className="cursor-pointer font-semibold">Scheduled reviews by first conversation card</summary>
      <p className="text-xs mt-2 text-[var(--muted)]">Only pairs whose first agreement came from that card and who both enrolled before agreeing. Helpfulness additionally requires both partners’ separate permission, two shared choices and sufficient group sizes. The breakdown uses their current shared choices.</p>
      <ul className="text-sm space-y-3 mt-2">{data.card_scheduled_returns.map(row=><li key={row.id}><strong>{row.name}</strong>
        <ScheduledReturns data={row} />
        {row.outcomes.available ? <p>{row.outcomes.options.map(x=>x.label+" "+x.count).join(" · ")}</p> : <p className="text-xs text-[var(--muted)]">Helpfulness withheld until reporting thresholds are met.</p>}</li>)}</ul>
    </details>}
    {data.shared_features && <div className="border-t border-[var(--line)] pt-3" data-testid="couples-shared-features">
      <h3 className="font-semibold">What active pairs share today</h3>
      <p className="mt-1 text-xs text-[var(--muted)]">Across {data.shared_features.active_pairs} active pairs. A pair counts once per activity after both partners share or approve it. Withdrawing removes it from this snapshot.</p>
      <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
        {Object.entries({prompt: "Starter conversations", moment: "Recent moments", positive: "Things that went well", learning: "Saved learnings"}).map(([key, label]) =>
          <div key={key}><dt className="text-xs text-[var(--muted)]">{label}</dt><dd className="text-lg font-bold">{data.shared_features.counts[key]}</dd></div>)}
      </dl>
    </div>}
    {data.assessment_sharing && <div className="border-t border-[var(--line)] pt-3" data-testid="couples-assessment-sharing">
      <h3 className="font-semibold">Assessment sharing today</h3>
      <p className="mt-1 text-xs text-[var(--muted)]">Current active pairs, counted once per measure. These counts describe sharing choices, not compatibility or research participation. Private answers and partner comparisons are not shown here.</p>
      <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        {Object.entries({ active_pairs: "Active confirmed pairs", any_sharing_pairs: "Pairs with at least one sharing choice",
          personality_mutual_pairs: "Both share personality", intimacy_mutual_pairs: "Both share sex & intimacy" }).map(([key, label]) =>
          <div className="rounded-xl border border-[var(--line)] p-3" key={key}><dt className="text-xs text-[var(--muted)]">{label}</dt>
            <dd className="mt-1 font-display text-2xl font-bold">{Number.isSafeInteger(data.assessment_sharing[key]) && data.assessment_sharing[key] >= 0 ? data.assessment_sharing[key] : "—"}</dd></div>)}
      </dl>
    </div>}
    <div className="border-t border-[var(--line)] pt-3">
      <h3 className="font-semibold">Follow-up on first agreements</h3>
      <p className="text-sm">{data.reviews.zero} with no shared check-in · {data.reviews.one} with one · {data.reviews.both} with two</p>
      <p className="mt-1 text-xs text-[var(--muted)]">Counts are final after the review date plus seven days. {data.reviews.pending} agreements are still in that window.</p>
    </div>
    <div className="border-t border-[var(--line)] pt-3" data-testid="couples-admin-outcomes">
      <h3 className="font-semibold">How both partners said it went</h3>
      {data.outcomes.available ? <>
        <p className="text-sm">From {data.outcomes.respondents} consenting pairs with two shared check-ins.</p>
        <ul className="mt-2 space-y-1 text-sm">{data.outcomes.options.map(o => <li key={o.label}>{o.label} · <strong>{o.count}</strong></li>)}</ul>
      </> : <p className="text-sm text-[var(--muted)]">A breakdown appears when both partners opt in and each represented result has at least {data.outcomes.minimum} pairs.</p>}
    </div>
    <div className="flex flex-wrap gap-x-5 gap-y-2 border-t border-[var(--line)] pt-3 text-sm">
      <a className="underline" href={`/admin/activity?measure=couples_opened&days=${data.days}`}>View daily Couples visits</a>
      <a className="underline" href="/admin/research?unit=couple">Compare partners’ questionnaire answers</a>
    </div>
  </section>;
}

export function CouplesStarters({ api }) {
  const [catalog, setCatalog] = useState(null);
  const [draft, setDraft] = useState([]);
  const [selected, setSelected] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const ctl = new AbortController();
    api.get("/admin/couples/catalog", { signal: ctl.signal }).then(({ data }) => {
      if (!ctl.signal.aborted) { setCatalog(data); setDraft(data.draft); setSelected(0); setError(""); }
    }).catch(e => { if (!ctl.signal.aborted) setError(errMsg(e)); });
    return () => ctl.abort();
  }, [api, attempt]);
  const item = draft[selected];
  function change(key, value) {
    setDraft(draft.map((row, i) => i === selected ? { ...row, [key]: value } : row));
    setNotice("");
  }
  async function apply(action) {
    setBusy(true); setError(""); setNotice("");
    try {
      const body = { action, revision: catalog.revision };
      if (action === "save") body.prompts = draft.map(row => ({...row, choices: row.choices.map(s => s.trim()).filter(Boolean)}));
      const {data} = await api.put("/admin/couples/catalog", body);
      setCatalog(data); setDraft(data.draft); setSelected(Math.min(selected, data.draft.length - 1));
      setNotice({save:"Draft saved.", publish:"Updated questions published.", rollback:"Previous questions restored."}[action]);
    } catch (e) { setError(errMsg(e)); } finally { setBusy(false); }
  }
  const dirty = catalog && JSON.stringify(draft) !== JSON.stringify(catalog.draft);
  return <section className={panel}>
    <h2 className={title}>Starter questions</h2>
    <p className="text-xs text-[var(--muted)]">Save and review a draft before publishing it. Existing shared answers keep the version their authors approved.</p>
    {item && <>
      <label className="block text-sm">Edit starter<select value={selected} onChange={e => setSelected(Number(e.target.value))} className={field}>
        {draft.map((p, i) => <option key={p.id} value={i}>{i + 1}. {p.question}</option>)}</select></label>
      {[["topic", "Topic"], ["question", "Question"], ["discussion", "Discussion prompt"]].map(([key, label]) =>
        <label className="block text-sm" key={key}>{label}<input className={field} value={item[key]} onChange={e => change(key, e.target.value)} /></label>)}
      <label className="block text-sm">Answer choices, one per line<textarea className={field} rows={4} value={item.choices.join("\n")} onChange={e => change("choices", e.target.value.split("\n"))} /></label>
      <details className="rounded-xl border border-[var(--line)] p-3 text-sm">
        <summary className="cursor-pointer font-semibold">Preview this starter</summary>
        <p className="mt-3 font-display text-xl font-bold">{item.question || "Write a question to preview it."}</p>
        <ul className="mt-2 list-disc pl-5">{item.choices.filter(s => s.trim()).map((s, i) => <li key={i}>{s}</li>)}</ul>
        {item.discussion && <p className="mt-3"><strong>After both share</strong><br />{item.discussion}</p>}
      </details>
    </>}
    {catalog && <div className="flex flex-wrap gap-2">
      <button type="button" className={button} disabled={busy || draft.length >= 64} onClick={() => { setDraft([...draft, {id: "starter-" + crypto.randomUUID(), topic:"communication", question:"", choices:[], discussion:""}]); setSelected(draft.length); }}>Add starter</button>
      <button type="button" className={button} disabled={busy || draft.length <= 1} onClick={() => { setDraft(draft.filter((_, i) => i !== selected)); setSelected(0); }}>Retire from draft</button>
      <button type="button" className={button} disabled={busy || !dirty} onClick={() => apply("save")}>Save draft</button>
      <button type="button" className={button} disabled={busy || dirty || JSON.stringify(catalog.draft) === JSON.stringify(catalog.published)} onClick={() => apply("publish")}>Publish saved draft</button>
      <button type="button" className={button} disabled={busy || dirty || !catalog.can_rollback} onClick={() => apply("rollback")}>Restore previous publication</button>
    </div>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {error && <p role="alert" className="text-sm">{error} <button className="underline" onClick={() => setAttempt(n => n + 1)}>Reload published state</button></p>}
  </section>;
}

export default function CouplesAnalytics({ api, days }) {
  const [section, setSection] = useState("funnel");
  const [report, setReport] = useState(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const ctl = new AbortController();
    setError(false);
    api.get("/admin/couples/analytics", { params: { days }, signal: ctl.signal })
      .then(({ data }) => { if (!ctl.signal.aborted) setReport(data); })
      .catch(() => { if (!ctl.signal.aborted) setError(true); });
    return () => ctl.abort();
  }, [api, days, attempt]);
  return <div className="space-y-3" data-testid="couples-analytics-admin">
    <div className="flex flex-wrap gap-2">{[["funnel","Partner journeys"], ["starters","Starter editor"], ["history","Answer history"]].map(([key,label]) =>
      <button type="button" aria-pressed={section === key} key={key} onClick={() => setSection(key)} className={section === key ? "btn-lime px-4 py-2 text-sm" : button}>{label}</button>)}</div>
    {section === "history" ? <CouplesAnswerHistory api={api} /> : section === "starters" ? <CouplesStarters api={api} /> : error ? <p role="alert">Couples data couldn’t load. <button className="underline" onClick={() => setAttempt(n => n + 1)}>Retry</button></p>
      : !report || report.days !== days ? <p role="status">Loading Couples…</p>
      : <Funnel data={report} />}
  </div>;
}
