import { useEffect, useRef, useState } from "react";

const endpoint = "/me/intimacy-answers";

function bySection(entries) {
  const sections = {};
  for (const [key, value] of Object.entries(entries)) {
    const [, section, index] = key.split(":");
    if (!sections[section]) sections[section] = [];
    sections[section][Number(index)] = value;
  }
  return sections;
}

export function SavedIntimacySession({ api, owner, onExit, takeInitialAnswers, children }) {
  const [record, setRecord] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refreshNeeded, setRefreshNeeded] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const revision = useRef(0);
  const saving = useRef(false);
  const reading = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    reading.current = controller;
    setRecord(null); setError(""); setBusy(false); saving.current = false; revision.current = 0;
    const initial = attempt === 0 ? takeInitialAnswers?.() : undefined;
    const load = initial ? Promise.resolve({ data: initial })
      : api.get(endpoint, { signal: controller.signal, ...(owner && { headers: { "X-Questionnaire-Owner": owner } }) });
    load.then(({ data }) => {
      if (controller.signal.aborted) return;
      if (!data.answers || !Number.isInteger(data.revision)) throw new Error();
      revision.current = data.revision;
      setRecord({ answers: bySection(data.answers), previous: data.previous, previousAnswers: data.previous_answers || [] });
    }).catch(() => {
      if (!controller.signal.aborted) setError("Your saved answers couldn’t load.");
    });
    return () => controller.abort();
  }, [api, attempt, owner, takeInitialAnswers]);

  function reload() {
    setRecord(null);
    setConfirmDelete(false);
    setError("");
    setAttempt(n => n + 1);
  }

  async function write(answer, item) {
    if (saving.current) return false;
    const currentRead = reading.current;
    if (!currentRead || currentRead.signal.aborted) return false;
    saving.current = true;
    setBusy(true);
    setError("");
    try {
      const config = owner ? { headers: { "X-Questionnaire-Owner": owner } } : {};
      const { data } = answer
        ? await api.patch(endpoint, { ...answer, adult_confirmed: true, revision: revision.current }, config)
        : await api.delete(endpoint, { ...config, params: { revision: revision.current, ...(item && { item }) } });
      if (currentRead.signal.aborted || currentRead !== reading.current) return false;
      revision.current = data.revision;
      if (!answer && !item) reload();
      else {
        const id = answer?.id || item;
        setRecord(current => {
          if (!current?.previousAnswers.some(entry => entry.id === id)) return current;
          const remaining = current.previousAnswers.filter(entry => entry.id !== id);
          return { ...current, previousAnswers: remaining, previous: remaining.length };
        });
      }
      return true;
    } catch (err) {
      if (currentRead.signal.aborted || currentRead !== reading.current) return false;
      const status = err.response?.status;
      setRefreshNeeded(status === 412);
      if (status === 403) setRecord(null);
      setError(status === 412 ? "This question changed. Refresh the questionnaire to see its current wording." :
        status === 409 ? (err.response.data?.detail || "Your answers changed in another visit.") :
        status === 403 ? "This activity is available to adults aged 18 and over." :
        "Imladri couldn’t confirm this change. Check your saved answers before continuing.");
      return false;
    } finally {
      if (!currentRead.signal.aborted && currentRead === reading.current) {
        saving.current = false;
        setBusy(false);
      }
    }
  }

  return <div className="space-y-3">
    {error && <div className="card max-w-2xl p-4 text-sm" role="alert">
      <p>{error}</p>
      <button type="button" className="py-2 underline" onClick={refreshNeeded ? () => window.location.reload() : reload}>{refreshNeeded ? "Refresh questionnaire" : "Reload saved answers"}</button>
      <button type="button" className="ml-4 py-2 underline" onClick={onExit}>Back to Personal</button>
    </div>}
    {!record && !error && <p role="status" className="text-sm">Opening your saved answers…</p>}
    {busy && <p role="status" className="text-sm">{confirmDelete ? "Deleting saved answers…" : "Saving your answer…"}</p>}
    {record && <fieldset disabled={busy || Boolean(error)} className="min-w-0">
      {confirmDelete && <div className="card max-w-xl p-5 space-y-3">
        <h2 className="font-display text-2xl font-bold">Delete your saved intimacy answers?</h2>
        <p className="text-sm">This clears your answers and skips in both tracks from your Personal profile.</p>
        <button type="button" className="btn-ghost px-4 py-2 text-sm" onClick={() => setConfirmDelete(false)}>Keep my answers</button>
        <button type="button" className="ml-2 py-2 text-sm underline" onClick={() => write(null)}>Delete my answers</button>
      </div>}
      <div hidden={confirmDelete}>{children({ initialAnswers: record.answers, previous: record.previous, previousAnswers: record.previousAnswers,
        saveAnswer: write, removeAnswer: item => write(null, item), requestRemoval: () => setConfirmDelete(true) })}</div>
    </fieldset>}
  </div>;
}
