import { useEffect, useRef, useState } from "react";

const inputClass = "filter-control rounded-lg border border-[var(--line)] bg-[var(--surface)] p-2 text-sm";
const emptyFilters = { domain: "", question_id: "", from_date: "", to_date: "" };
const areas = ["everyday", "intimacy", "demographics", "profile", "research", "couples", "your_type", "consent", "person_intake", "assessment_repeat", "wording_feedback"];
const areaNames = { person_intake: "People observations", people: "People observations",
  assessment_repeat: "Questionnaire retakes", wording_feedback: "Intimacy wording feedback" };
const name = value => areaNames[value] || value.replaceAll("_", " ").replace(/^./, c => c.toUpperCase());
const text = value => typeof value === "string" ? value : JSON.stringify(value, null, 2);
function questionText(question) {
  if (typeof question === "string") return question;
  const nested = question?.question;
  return question?.label || (typeof nested === "string" ? nested : nested?.q || nested?.text) || question?.text || "Recorded question";
}

export default function AnswerHistoryTimeline({ api, scope = "member", target }) {
  const [filters, setFilters] = useState(emptyFilters);
  const [applied, setApplied] = useState(emptyFilters);
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("idle");
  const [message, setMessage] = useState("");
  const [cursors, setCursors] = useState([null]);
  const [index, setIndex] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const request = useRef(null);
  const download = useRef(null);
  const generation = useRef(0);
  const base = `/admin/answer-history/${scope}/${encodeURIComponent(target || "")}`;

  useEffect(() => {
    generation.current += 1;
    request.current?.abort(); download.current?.abort();
    setFilters(emptyFilters); setApplied(emptyFilters); setData(null); setStatus("idle");
    setMessage(""); setCursors([null]); setIndex(0); setDownloading(false);
    return () => { generation.current += 1; request.current?.abort(); download.current?.abort(); };
  }, [api, scope, target]);

  function params(selected, cursor) {
    return { limit: 50, ...Object.fromEntries(Object.entries(selected).filter(([, value]) => value)), ...(cursor ? { before: cursor } : {}) };
  }

  async function load(selected = applied, cursor = cursors[index], nextIndex = index, nextCursors = cursors) {
    if (!target) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    const serial = ++generation.current;
    setData(null); setStatus("loading"); setMessage("");
    try {
      const response = await api.get(base, { params: params(selected, cursor), signal: controller.signal });
      if (controller.signal.aborted || generation.current !== serial) return;
      setData(response.data); setStatus("ready"); setApplied(selected); setIndex(nextIndex); setCursors(nextCursors);
    } catch (error) {
      if (controller.signal.aborted || generation.current !== serial) return;
      setStatus("error");
      setMessage(error.response?.status === 409 ? "This page changed. Apply the filters again to reload the timeline."
        : error.response?.status === 404 ? "This member or active couple is no longer available."
          : "Answer history could not be loaded. Please try again.");
    } finally {
      if (request.current === controller) request.current = null;
    }
  }

  async function saveCsv() {
    if (download.current || !data) return;
    const controller = new AbortController();
    download.current = controller;
    setDownloading(true); setMessage("");
    const serial = generation.current;
    try {
      const response = await api.get(base + ".csv", { params: params(applied, cursors[index]), responseType: "blob", signal: controller.signal });
      if (controller.signal.aborted || generation.current !== serial) return;
      const url = URL.createObjectURL(new Blob([response.data], { type: "text/csv;charset=utf-8" }));
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = `imladri-${scope}-answer-history-page-${index + 1}.csv`;
      document.body.appendChild(anchor);
      try { anchor.click(); } finally { anchor.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
      setMessage("CSV download started. It contains this page only, with permissions checked again at download time.");
    } catch {
      if (!controller.signal.aborted && generation.current === serial) setMessage("The history CSV could not be downloaded. Please reload and try again.");
    } finally {
      if (download.current === controller) { download.current = null; if (!controller.signal.aborted) setDownloading(false); }
    }
  }

  function apply(event) {
    event.preventDefault();
    load({ ...filters }, null, 0, [null]);
  }

  const availableAreas = scope === "couple" ? ["couples"] : areas;
  return <section className="card filter-container min-w-0 space-y-4 p-4 sm:p-5" aria-label="Historical answers" data-testid="admin-answer-history">
    <div>
      <h2 className="font-display text-xl font-bold uppercase">Answer history</h2>
      <p className="mt-1 text-sm text-[var(--muted)]">Review retained observations and changes. Private answers require the member’s recorded staff-history and applicable research permissions. They remain private from a partner.</p>
    </div>
    <form onSubmit={apply} className="space-y-3">
      <div className="filter-grid history-filter-grid" data-testid="answer-history-filters">
        <label className="filter-field text-sm font-semibold"><span>Area</span>
          <select className={inputClass} value={filters.domain} onChange={e => setFilters(previous => ({ ...previous, domain: e.target.value }))}>
            <option value="">All {scope === "couple" ? "Couples " : ""}areas</option>
            {availableAreas.map(area => <option value={area} key={area}>{name(area)}</option>)}
          </select>
        </label>
        <label className="filter-field text-sm font-semibold"><span>Question ID</span>
          <input className={inputClass} value={filters.question_id} maxLength={200} autoComplete="off" onChange={e => setFilters(previous => ({ ...previous, question_id: e.target.value }))} />
        </label>
        <label className="filter-field text-sm font-semibold"><span className="whitespace-nowrap">From date (UTC)</span>
          <input type="date" className={inputClass} value={filters.from_date} onChange={e => setFilters(previous => ({ ...previous, from_date: e.target.value }))} />
        </label>
        <label className="filter-field text-sm font-semibold"><span className="whitespace-nowrap">To date (UTC)</span>
          <input type="date" className={inputClass} value={filters.to_date} onChange={e => setFilters(previous => ({ ...previous, to_date: e.target.value }))} />
        </label>
      </div>
      <div className="flex flex-wrap gap-3">
        <button type="submit" className="btn-lime px-4 py-2 text-sm disabled:opacity-50" disabled={!target || status === "loading" || downloading}>
          {status === "loading" ? "Loading history…" : status === "idle" ? "Load answer history" : "Apply filters"}
        </button>
        {data && <button type="button" className="btn-ghost px-4 py-2 text-sm disabled:opacity-50" disabled={downloading || !data.items?.length} onClick={saveCsv}>
          {downloading ? "Preparing CSV…" : "Download this page CSV"}
        </button>}
      </div>
    </form>
    {status === "error" && <p role="alert" className="text-sm">{message}</p>}
    {status !== "error" && message && <p role="status" className="text-sm">{message}</p>}
    {status === "loading" && <p role="status" className="text-sm">Loading retained answer observations…</p>}
    {data && <>
      <p className="text-sm text-[var(--muted)]">{data.notice}</p>
      {!data.items?.length ? <p className="text-sm">No retained observations match this page. An empty history does not mean the person answered “no”.</p>
        : <ol className="space-y-3" aria-label="Answer observations">{data.items.map(item => <li key={item.event_id} className="rounded-xl border border-[var(--line)] p-3 text-sm">
          <div className="flex flex-wrap justify-between gap-2">
            <p className="font-semibold">{name(item.domain || "Answer")} · {item.baseline ? "Baseline observation" : "Recorded observation"}</p>
            <time dateTime={item.observed_at_utc}>{item.observed_at_utc || "Time unavailable"}</time>
          </div>
          {item.status === "withheld" ? <p className="mt-2 text-[var(--muted)]">Details withheld. {item.withheld_reason}</p> : <>
            <p className="mt-2 font-semibold">{questionText(item.question)}</p>
            <p className="mt-1 text-xs text-[var(--muted)]">{item.visibility === "shared" ? "Shared contribution" : item.visibility === "research" ? "Research submission" : "Private · staff permission"}{item.seat ? ` · ${name(item.seat)}` : ""} · {item.kind || "Answer recorded"}</p>
            <details className="mt-3">
              <summary className="cursor-pointer font-semibold">View observation</summary>
              <dl className="mt-3 space-y-3">
                <div><dt className="font-semibold">Question ID and edition</dt><dd className="break-all text-xs">{item.question_id || "Unavailable"}<br />{item.edition || "Edition unavailable"}</dd></div>
                <div><dt className="font-semibold">Attempt</dt><dd className="break-all text-xs">{item.attempt_id || "Unavailable"}</dd></div>
                {item.before_exists && <div><dt className="font-semibold">Before</dt><dd className="whitespace-pre-wrap break-words">{item.before_withheld || text(item.before)}</dd></div>}
                <div><dt className="font-semibold">Recorded answer</dt><dd className="whitespace-pre-wrap break-words">{text(item.after)}</dd></div>
                <div><dt className="font-semibold">Demographics at this observation</dt><dd className="whitespace-pre-wrap break-words">{item.demographics_at_observation ? text(item.demographics_at_observation) : "No authorized retained snapshot. Current profile values are not substituted."}</dd></div>
              </dl>
            </details>
          </>}
        </li>)}</ol>}
      <div className="flex items-center justify-between gap-3">
        <button type="button" className="btn-ghost px-3 py-1.5 text-sm disabled:opacity-50" disabled={index === 0 || downloading} onClick={() => load(applied, cursors[index - 1], index - 1)}>Previous observations</button>
        <span className="text-sm">Page {index + 1}</span>
        <button type="button" className="btn-ghost px-3 py-1.5 text-sm disabled:opacity-50" disabled={!data.next || downloading} onClick={() => {
          const next = [...cursors.slice(0, index + 1), data.next];
          load(applied, data.next, index + 1, next);
        }}>Older observations</button>
      </div>
    </>}
  </section>;
}
