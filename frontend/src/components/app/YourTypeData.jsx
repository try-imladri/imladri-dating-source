import { useEffect, useRef, useState } from "react";
import { ResearchChoice } from "./YourType";

const validPreference = value => value?.version === "your-type-research-v2" && typeof value.enabled === "boolean"
  && Number.isSafeInteger(value.revision) && value.revision >= 0;
const validRow = row => typeof row?.id === "string" && row.id.length > 0 && row.id.length <= 128
  && Number.isSafeInteger(row.revision) && row.revision >= 0;
const button = "btn-ghost px-3 py-1.5 text-sm disabled:opacity-50";

export default function YourTypeData(props) {
  return <YourTypeDataWorkspace key={props.owner} {...props} />;
}

function YourTypeDataWorkspace({ api, owner }) {
  const [preference, setPreference] = useState(null);
  const [preferencePhase, setPreferencePhase] = useState("loading");
  const [history, setHistory] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [historyPhase, setHistoryPhase] = useState("idle");
  const [historyMessage, setHistoryMessage] = useState("");
  const [confirm, setConfirm] = useState(null);
  const [removing, setRemoving] = useState(null);
  const [removalBlocked, setRemovalBlocked] = useState(false);
  const alive = useRef(false);
  const requests = useRef(new Set());

  async function loadPreference() {
    const controller = new AbortController(); requests.current.add(controller);
    setPreferencePhase("loading");
    try {
      const { data } = await api.get("/your-type/research", { signal: controller.signal });
      if (!validPreference(data)) throw Error("Invalid permission response");
      if (alive.current && !controller.signal.aborted) { setPreference(data); setPreferencePhase("ready"); }
    } catch (_) { if (alive.current && !controller.signal.aborted) setPreferencePhase("error"); }
    finally { requests.current.delete(controller); }
  }

  async function loadHistory(before) {
    if (historyPhase === "loading") return;
    const controller = new AbortController(); requests.current.add(controller);
    setHistoryPhase("loading"); setHistoryMessage("");
    try {
      const { data } = await api.get("/your-type/history", { params: { limit: 20, ...(before ? { before } : {}) }, signal: controller.signal });
      if (!Array.isArray(data?.sessions) || !data.sessions.every(validRow)
          || !(data.next === null || typeof data.next === "string")) throw Error("Invalid history response");
      if (alive.current && !controller.signal.aborted) {
        setHistory(rows => before ? [...new Map([...rows, ...data.sessions].map(row => [row.id, row])).values()] : data.sessions);
        setCursor(data.next); setHistoryPhase("ready"); setRemovalBlocked(false); setConfirm(null);
      }
    } catch (error) {
      if (alive.current && !controller.signal.aborted) {
        setHistoryPhase("error");
        setHistoryMessage(error?.response?.status === 403 ? "Attempt history is unavailable for this account. You can still change the research permission above."
          : "Attempt history could not load. Your research permission can still be managed separately.");
      }
    } finally { requests.current.delete(controller); }
  }

  async function remove(row) {
    if (removing || removalBlocked) return;
    const controller = new AbortController(); requests.current.add(controller);
    setRemoving(row.id); setHistoryMessage("");
    try {
      // History already supplies the revision. Never call the paid session-read
      // or results endpoints just to exercise an owner's deletion right.
      const { data } = await api.delete(`/your-type/sessions/${encodeURIComponent(row.id)}`,
        { params: { expected_revision: row.revision }, signal: controller.signal });
      if (data?.removed !== true) throw Error("Removal not confirmed");
      if (alive.current && !controller.signal.aborted) {
        setHistory(rows => rows.filter(item => item.id !== row.id)); setConfirm(null);
        setHistoryMessage("Attempt and its retained answer history removed.");
      }
    } catch (error) {
      if (alive.current && !controller.signal.aborted) {
        setRemovalBlocked(true); setConfirm(null);
        setHistoryMessage(error?.response?.status === 409 ? "This attempt changed elsewhere. Refresh history and review its current revision before removing it."
          : "Removal was not confirmed. Refresh history before trying again.");
      }
    } finally { requests.current.delete(controller); if (alive.current) setRemoving(null); }
  }

  useEffect(() => {
    const activeRequests = requests.current;
    alive.current = true; loadPreference();
    return () => { alive.current = false; for (const request of activeRequests) request.abort(); activeRequests.clear(); };
    // The keyed workspace gives each owner independent request lifetimes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, owner]);

  return <section id="your-type-data" className="space-y-3 border-t border-[var(--line)] pt-3 text-sm" aria-label="Your Type data management">
    <h3 className="font-semibold text-[var(--ink)]">Your Type data</h3>
    <p>You can withdraw research permission or remove saved attempts without an active plan. These controls do not start an attempt, generate results or use message credits.</p>
    {preferencePhase === "loading" && <p role="status">Loading Your Type permission…</p>}
    {preferencePhase === "error" && <p role="alert">The research permission could not load. <button type="button" className="underline" onClick={loadPreference}>Retry research permission</button></p>}
    {preferencePhase === "ready" && preference && <>
      {preference.requires_expanded_consent && <p>Your earlier research permission did not cover the expanded sexuality and viewer-gender questions. Research remains off until you review the new scope and opt in again.</p>}
      <ResearchChoice key={`${owner}:${preference.revision}`} api={api} owner={owner} initial={preference} onChange={setPreference} />
      <button type="button" className="underline" onClick={loadPreference}>Refresh research permission</button>
    </>}
    <details data-testid="your-type-data-history" onToggle={event => { if (event.currentTarget.open && historyPhase === "idle") loadHistory(); }}>
      <summary className="cursor-pointer font-semibold text-[var(--ink)]">Manage saved Your Type attempts</summary>
      <div className="mt-3 space-y-3">
        <p>Only dates, audience and progress appear here. Your private portraits and model results remain in Your Type; the full owned export is available through Export data in Settings.</p>
        <p>Current women’s and men’s results remain separate. Historical combined attempts keep their original model and image edition; opening or exporting one does not create separate audience results.</p>
        <button type="button" className={button} disabled={historyPhase === "loading" || Boolean(removing)} onClick={() => loadHistory()}>Refresh attempt history</button>
        {historyPhase === "loading" && <p role="status">Loading your attempts…</p>}
        {historyMessage && <p role={historyPhase === "error" || removalBlocked ? "alert" : "status"}>{historyMessage}</p>}
        {historyPhase === "ready" && !history.length && <p>No saved Your Type attempts.</p>}
        {history.map(row => <div key={row.id} className="border-t border-[var(--line)] pt-3 space-y-2">
          <p><strong className="capitalize">{row.audience}</strong> · {row.state?.completed_crowds || 0} crowds · {row.status}</p>
          <p className="text-xs">{row.updated_at || row.created_at || "Date not recorded"} · {row.asset_edition}</p>
          {confirm === row.id ? <div className="space-y-2"><p>Remove this attempt and its retained answer history permanently?</p>
            <button type="button" className={button} disabled={Boolean(removing) || removalBlocked} onClick={() => remove(row)}>Confirm attempt removal</button>
            <button type="button" className="underline ml-3" disabled={Boolean(removing)} onClick={() => setConfirm(null)}>Keep attempt</button>
          </div> : <button type="button" className="underline" disabled={Boolean(removing) || removalBlocked} onClick={() => setConfirm(row.id)}>Remove attempt</button>}
        </div>)}
        {cursor && <button type="button" className={button} disabled={historyPhase === "loading" || Boolean(removing)} onClick={() => loadHistory(cursor)}>Load older attempts</button>}
      </div>
    </details>
  </section>;
}
