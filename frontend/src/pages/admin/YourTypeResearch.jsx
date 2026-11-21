import { useEffect, useRef, useState } from "react";

const field = "block w-full rounded-lg border border-[var(--line)] bg-[var(--surface)] p-2 text-sm";
const button = "btn-ghost px-3 py-2 text-sm disabled:opacity-50";

function useReport(api, path, opened, params) {
  const [data, setData] = useState(null);
  const [phase, setPhase] = useState("idle");
  const [retry, setRetry] = useState(0);
  const key = JSON.stringify(params || {});
  useEffect(() => {
    if (!opened) return;
    const controller = new AbortController();
    setPhase("loading"); setData(null);
    api.get(path, { params: JSON.parse(key), signal: controller.signal }).then(({ data: report }) => {
      if (!controller.signal.aborted) { setData(report); setPhase("ready"); }
    }).catch(() => { if (!controller.signal.aborted) setPhase("error"); });
    return () => controller.abort();
  }, [api, path, opened, key, retry]);
  return { data, phase, refresh: () => setRetry(n => n + 1) };
}

function Status({ report }) {
  return report.phase === "loading" ? <p role="status" className="text-sm">Loading Your Type…</p>
    : report.phase === "error" ? <p role="alert" className="text-sm">Your Type couldn’t load. <button className="underline" onClick={report.refresh}>Retry</button></p> : null;
}

export function YourTypeCatalog({ api }) {
  const report = useReport(api, "/admin/your-type/catalog", true);
  const data = report.data;
  return <section className="card space-y-4 p-4 sm:p-5" data-testid="admin-your-type-catalog">
    <div className="flex items-center justify-between gap-3"><h2 className="font-display text-xl font-black uppercase">Your Type</h2>
      <button className={button} onClick={report.refresh}>Refresh catalog</button></div>
    <Status report={report} />
    {data && <>
      <p className="text-sm">{data.meaning}</p>
      <dl className="grid gap-3 sm:grid-cols-3">{[["Original portraits", data.assets], ["Portraits per crowd", data.cards_per_crowd], ["Recommended crowds", data.recommended_crowds]].map(([label, value]) =>
        <div className="rounded-xl bg-[var(--surface-2)] p-3" key={label}><dt className="text-xs text-[var(--muted)]">{label}</dt><dd className="mt-1 text-2xl font-semibold">{value}</dd></div>)}</dl>
      <p className="break-all text-xs text-[var(--muted)]">Asset edition: {data.asset_edition} · Model edition: {data.model_edition}<br />Manifest SHA-256: {data.asset_manifest_sha256}</p>
      <div><h3 className="font-semibold">Recorded actions</h3><p className="mt-1 text-sm">{data.actions.join(" · ")}</p></div>
      <ul className="list-disc space-y-1 pl-5 text-sm">{data.recorded.map(label => <li key={label}>{label}</li>)}</ul>
      <p className="text-sm">Current audience profiles need {data.required_likes || 30} committed likes and 3 explicit or viewed passes per audience, after the crowd milestone. Women’s and men’s models and checks remain separate. Historical attempts keep their recorded edition. Retakes are available immediately. Undo adds a linked revision; it does not erase the original action. Full member exports include owned history. Research rows require separate permissions.</p>
      {data.association_scope && <p className="text-sm">{data.association_scope}</p>}
      {data.research_exclusions?.length > 0 && <ul className="list-disc space-y-1 pl-5 text-sm">{data.research_exclusions.map(text => <li key={text}>{text}</li>)}</ul>}
      <p className="text-sm">Zero AI calls · Zero message credits. {data.population_baseline}</p>
      <details className="border-t border-[var(--line)] pt-3 text-sm"><summary className="cursor-pointer font-semibold">Feature and asset provenance</summary>
        <p className="mt-2">{data.features.join(" · ")}</p>
        {data.catalog_schema_version != null && <p className="mt-2 text-xs">Catalog schema {data.catalog_schema_version}. Coverage describes authored annotations, not proven preference accuracy.</p>}
        {(data.feature_groups || []).map(group => <section key={group.id} className="mt-3 border-t border-[var(--line)] pt-3" aria-label={`${group.label} catalog coverage`}>
          <h4 className="font-semibold">{group.label}</h4>
          <dl className="mt-2 grid gap-2 sm:grid-cols-2">{["women", "men"].map(audience => {
            const coverage = group.audience_coverage?.[audience];
            if (!coverage) return null;
            return <div key={audience}><dt className="font-semibold capitalize">{audience}</dt><dd>{coverage.applicable === false ? "Not applicable to this audience" : <>
              {coverage.covered_portraits} of {coverage.total_portraits} portraits annotated.
              {coverage.coverage_available === false ? " No usable coverage in this edition; missing labels are not negatives."
                : coverage.variation_available === false ? " No usable variation in this audience’s catalog." : " Variation is available for this audience."}
            </>}</dd></div>;
          })}</dl>
        </section>)}
        <pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(data.rights, null, 2)}</pre>
      </details>
    </>}
  </section>;
}

export function YourTypeMember({ api, userId }) {
  const [opened, setOpened] = useState(false);
  const report = useReport(api, `/users/${encodeURIComponent(userId)}/your-type`, opened);
  const [download, setDownload] = useState("");
  const activeDownload = useRef(null);
  useEffect(() => () => activeDownload.current?.abort(), [userId]);
  async function csv() {
    if (activeDownload.current) return;
    const controller = new AbortController(); activeDownload.current = controller;
    setDownload("Preparing CSV…");
    try {
      const response = await api.get(`/users/${encodeURIComponent(userId)}/your-type.csv`, { responseType: "blob", signal: controller.signal });
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(new Blob(["\uFEFF", response.data], { type: "text/csv;charset=utf-8" }));
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = "imladri-your-type-research.csv";
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setDownload("CSV download started.");
    } catch (_) { if (!controller.signal.aborted) setDownload("CSV unavailable. Current adult and both research and row-export permissions are required."); }
    finally { if (activeDownload.current === controller) activeDownload.current = null; }
  }
  const data = report.data;
  return <details className="mt-2 text-xs font-normal" onToggle={e => setOpened(e.currentTarget.open)}>
    <summary className="cursor-pointer underline">Your Type records</summary>
    <div className="mt-2 space-y-2"><Status report={report} />{data && <>
      <p>{data.attempts} attempts · Research {data.research.enabled ? "enabled" : "off"} · Zero AI calls / credits</p>
      {data.latest && <p>Latest: {data.latest.state?.completed_crowds ?? 0} crowds · {data.latest.asset_edition} / {data.latest.model_edition}</p>}
      {data.latest?.preference_snapshot && <p>Self-reported at this attempt’s start: {data.latest.preference_snapshot.sexuality} · {data.latest.preference_snapshot.viewer_gender || "Gender not recorded"} · {data.latest.preference_snapshot.rated_audience} portraits. Attempt {data.latest.attempt_code || "code unavailable"}.</p>}
      <p>Private choices stay out of this summary. CSV access follows the member’s current permissions.</p>
      <p>Sexuality and viewer-gender snapshots appear only with both current research and Individual CSV permissions. Older attempts remain unknown.</p>
      <p>Privately stated age and height preferences are excluded from this research summary and CSV. Existing research permissions do not include them.</p>
      <button type="button" className="underline" onClick={csv} disabled={download === "Preparing CSV…"}>Download consented Your Type CSV</button>
      {download && <p role="status">{download}</p>}
    </>}</div>
  </details>;
}

export default function YourTypeResearch({ api, Comparison }) {
  const [opened, setOpened] = useState(false);
  const [draft, setDraft] = useState({ x: "audience", y: "progress" });
  const [query, setQuery] = useState(draft);
  const report = useReport(api, "/admin/research/your-type", opened, query);
  const data = report.data;
  return <details className="card space-y-4 p-4 sm:p-5" onToggle={e => setOpened(e.currentTarget.open)} data-testid="your-type-research">
    <summary className="cursor-pointer font-display text-xl font-bold uppercase">Your Type research</summary>
    <p className="text-sm text-[var(--muted)]">Optional image-preference research is off by default. Only consenting adults contribute. Profile and questionnaire comparisons additionally require current questionnaire research permission. Partners cannot access private choices.</p>
    <Status report={report} />
    {data && <>
      <dl className="grid gap-3 sm:grid-cols-3">{[["Consenting adults", data.denominators.consenting_adults || 0], ["Adults with eligible attempts", data.denominators.members_with_sessions || 0], ["Recorded attempts", data.denominators.attempts || 0]].map(([label, count]) =>
        <div className="rounded-xl bg-[var(--surface-2)] p-3" key={label}><dt className="text-xs text-[var(--muted)]">{label}</dt><dd className="mt-1 text-2xl font-semibold">{count}</dd></div>)}</dl>
      <p className="text-sm">{data.retakes} Only activity after the current permission is eligible. Missing choices are excluded rather than counted as dislikes.</p>
      <details className="border-y border-[var(--line)] py-3 text-sm"><summary className="cursor-pointer font-semibold">Recorded action and prediction coverage</summary>
        <p className="mt-2 text-[var(--muted)]">{data.activity_meaning}</p>
        <dl className="mt-3 grid gap-x-4 gap-y-2 sm:grid-cols-2">{[["Issued portraits", "issued_portraits"], ["Declared view events", "declared_view_events"],
          ["Likes", "choice_like"], ["Explicit passes", "choice_pass"], ["Weak viewed passes", "choice_viewed_pass"], ["Skipped portraits", "skipped_portraits"],
          ["Next crowd actions", "next_crowd"], ["Undo actions", "undo"], ["Pause actions", "pause"], ["Resume actions", "resume"],
          ["Finish actions", "finish"], ["Prospective pairs", "prospective_pairs"]].map(([label, key]) =>
          <div key={key} className="flex justify-between gap-3"><dt>{label}</dt><dd className="font-semibold tabular-nums">{data.activity?.[key] || 0}</dd></div>)}</dl>
      </details>
      <form className="space-y-3" onSubmit={e => { e.preventDefault(); if (draft.x !== draft.y) setQuery({ ...draft }); }}>
        <div className="grid gap-3 sm:grid-cols-2">{["x", "y"].map((axis, index) => <label className="text-sm font-semibold" key={axis}>{index === 0 ? "Your Type rows" : "Your Type columns"}
          <select className={field} value={draft[axis]} onChange={e => setDraft({ ...draft, [axis]: e.target.value })}>{data.variables.map(v => <option key={v.id} value={v.id}>{v.label}</option>)}</select>
        </label>)}</div>
        {data.edition_choices.length > 1 && <label className="block text-sm font-semibold">Asset and model edition<select className={field} value={draft.edition || ""} onChange={e => setDraft({ ...draft, edition: e.target.value || undefined,
          ...([draft.x, draft.y].some(value => value.startsWith("feature:")) ? { x: "audience", y: "progress" } : {}) })}>
          <option value="">Choose one edition</option>{data.edition_choices.map(v => <option key={v.id} value={v.id}>{v.asset_edition} / {v.model_edition}</option>)}</select></label>}
        {data.edition_choices.length > 1 && <p className="text-xs text-[var(--muted)]">Changing editions clears feature selections. Load the edition, then choose from its own recorded features.</p>}
        <button className={button} disabled={draft.x === draft.y || report.phase === "loading"}>Compare Your Type</button>
        <button type="button" className={button} onClick={report.refresh}>Refresh research</button>
      </form>
      {!data.edition_compatible && <p className="text-sm">Choose one asset and model edition. Incompatible editions are not pooled.</p>}
      {data.edition_available === false && <p className="text-sm">That edition has no currently eligible contributors. <button className="underline" onClick={() => {
        const next = { x: draft.x, y: draft.y }; setDraft(next); setQuery(next);
      }}>Reset edition filter</button></p>}
      {data.truncated && <p role="alert" className="text-sm">This scan reached its safety limit. Correlations are withheld.</p>}
      <p className="text-sm text-[var(--muted)]">Comparisons require at least {data.minimum_paired} people and {data.minimum_cell} per occupied cell. Counts are rounded. These are associations, not causes or matchmaking scores.</p>
      <p className="text-sm text-[var(--muted)]">{data.demographics} Visual cue variables describe authored portrait features and model associations, not inferred identity or personality.</p>
      <p className="text-sm text-[var(--muted)]">{data.preference_metadata}</p>
      <p className="text-sm text-[var(--muted)]">Visual-preference variables name the portrait audience and image edition. Men’s and women’s model scores and feature weights are not a measure of relative attraction across audiences. Private stated age and height ranges are excluded.</p>
      <Comparison data={data} />
      <p className="text-sm">Population baseline unavailable until suitable real data exists. Zero AI calls · Zero message credits.</p>
      <a href="/admin/quiz" className="text-sm underline">Browse the Your Type catalog in Quizzes</a>
    </>}
  </details>;
}
