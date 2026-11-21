import { useEffect, useState } from "react";

const initial = { unit: "member", x: "sent_style", y: "helped", incoming: "" };

export default function ToneResearch({ api, Comparison }) {
  const [opened, setOpened] = useState(false);
  const [draft, setDraft] = useState(initial);
  const [query, setQuery] = useState(initial);
  const [result, setResult] = useState(null);
  const [phase, setPhase] = useState("idle");

  useEffect(() => {
    if (!opened) return;
    const controller = new AbortController();
    setPhase("loading");
    const params = { ...query };
    if (!params.incoming) delete params.incoming;
    api.get("/admin/research/tone", { params, signal: controller.signal }).then(({ data }) => {
      if (!controller.signal.aborted) { setResult(data); setPhase("ready"); }
    }).catch(() => { if (!controller.signal.aborted) setPhase("error"); });
    return () => controller.abort();
  }, [api, opened, query]);

  const set = (field, value) => setDraft(previous => ({ ...previous, [field]: value }));
  const demographic = key => key.startsWith("d:") || key.endsWith("_band");
  const comparable = draft.x !== draft.y && !(demographic(draft.x) && demographic(draft.y));
  const select = "block w-full rounded-lg border border-[var(--line)] bg-[var(--surface)] p-2 font-normal";

  return <details className="card space-y-4 p-4 sm:p-5" onToggle={e => setOpened(e.currentTarget.open)}>
    <summary className="cursor-pointer font-display text-xl font-bold uppercase">Conversation tone research</summary>
    <p className="text-sm text-[var(--muted)]">Each consenting adult or active couple contributes their latest eligible read with both values. Couples comparisons use mutually shared conversations created after both adults opted in. Demographic comparisons use the responding member’s own questionnaire research permission.</p>
    {result && <form className="space-y-3" onSubmit={e => { e.preventDefault(); if (comparable) setQuery({ ...draft }); }}>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm font-semibold">Conversation source
          <select value={draft.unit} className={select} onChange={e => set("unit", e.target.value)}>
            <option value="member">Individual</option><option value="couple">Shared Couples conversations</option>
          </select>
        </label>
        <label className="text-sm font-semibold">Incoming tone filter
          <select value={draft.incoming} className={select} onChange={e => set("incoming", e.target.value)}>
            <option value="">All eligible reads</option>{result.tones.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        {["x", "y"].map((field, i) => <label key={field} className="text-sm font-semibold">{i === 0 ? "Tone comparison rows" : "Tone comparison columns"}
          <select value={draft[field]} className={select} onChange={e => set(field, e.target.value)}>
            {result.variables.map(v => <option key={v.id} value={v.id}>{v.label}</option>)}
          </select>
        </label>)}
      </div>
      {!comparable && <p className="text-sm">Choose two different variables including at least one tone or feedback measure.</p>}
      <button type="submit" disabled={phase === "loading" || !comparable} className="btn-lime px-4 py-2 text-sm disabled:opacity-50">Compare tone results</button>
    </form>}
    {phase === "loading" && <p role="status">Loading tone results…</p>}
    {phase === "error" && <p role="alert">Tone results couldn’t load. <button className="underline" onClick={() => setQuery({ ...query })}>Retry</button></p>}
    {phase === "ready" && result && <Comparison data={result} />}
  </details>;
}
