import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";

export function Starters({ api, method = "get", url, body, onPick, disabled }) {
  const [batch, setBatch] = useState(null);
  const items = batch?.starters;
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  const sig = JSON.stringify(body || {});
  useEffect(() => {
    let active = true;
    setBatch(null);
    setFailed(false);
    const req = method === "post" ? api.post(url, body) : api.get(url);
    req.then((r) => { if (active) setBatch({starters:r.data.starters || [], id:r.data.batch_id}); }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [api, method, url, sig, attempt]); // eslint-disable-line react-hooks/exhaustive-deps
  if (failed) return <p role="alert" className="text-xs text-[var(--muted)]">Couldn’t load suggested questions. <button type="button" className="underline" onClick={() => setAttempt(n => n + 1)}>Try again</button></p>;
  if (items && !items.length) return null;
  return (
    <div className="shrink-0 space-y-1.5" data-testid="chat-starters">
      <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)] inline-flex items-center gap-1"><Sparkles className="w-3 h-3" />Try asking</p>
      {!items && <p className="text-xs text-[var(--muted)]" data-testid="chat-starters-loading">Thinking of good questions…</p>}
      {items?.map((s, i) => (
        <button key={s} disabled={disabled} onClick={() => onPick(s, batch.id)} data-testid={`chat-starter-${i}`}
          className="block w-full text-left text-xs px-3 py-2 rounded-xl border border-[var(--line)] hover:border-[var(--ink)] hover:bg-[var(--surface-2)] transition-colors disabled:opacity-40">{s}</button>
      ))}
    </div>
  );
}
