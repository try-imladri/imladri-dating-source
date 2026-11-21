import { useCallback, useEffect, useRef, useState } from "react";
import { errMsg } from "@/lib/api";

// Keep the cursor from the last successful response when a continuation fails.
export function useMemberPages(api, path, { field = "items", size = 30, revision } = {}) {
  const [state, setState] = useState(null);
  const active = useRef(null);
  const load = useCallback(async cursor => {
    if (!path) return;
    active.current?.abort();
    const req = new AbortController();
    active.current = req;
    const query = new URLSearchParams({ page_size: String(size) });
    if (cursor) query.set("cursor", cursor);
    setState(old => ({ ...(cursor && old?.path === path && old.api === api ? old : { api, path, rows: null }), busy: true, error: "" }));
    try {
      const { data } = await api.get(path + (path.includes("?") ? "&" : "?") + query, { signal: req.signal });
      if (req.signal.aborted) return;
      const received = data[field] || (Array.isArray(data) ? data : []);
      setState(old => {
        const rows = new Map((cursor && old?.path === path && old.api === api ? old.rows || [] : []).map(row => [row.id, row]));
        received.forEach(row => rows.set(row.id, row));
        return { api, path, rows: [...rows.values()], data, total: data.total ?? rows.size, next: data.next_cursor || "", busy: false, error: "" };
      });
    } catch (error) {
      if (!req.signal.aborted) setState(old => ({ ...old, error: errMsg(error), busy: false }));
    }
  }, [api, path, field, size]);
  const reload = useCallback(() => load(""), [load]);
  useEffect(() => {
    reload();
    return () => active.current?.abort();
  }, [reload, revision]);
  const visible = state?.path === path && state.api === api ? state : null;
  return { ...visible, reload, more: visible?.next ? () => load(visible.next) : null };
}

export function ListContinuation({ page, label }) {
  if (!page.more) return null;
  return <div className="mt-2 space-y-1">
    {page.error && <p role="alert" className="text-xs text-[var(--muted)]">{page.error}</p>}
    <button type="button" className="btn-ghost px-3 py-1.5 text-xs disabled:opacity-50" onClick={page.more} disabled={page.busy}>
      {page.busy ? "Loading…" : page.error ? "Try loading more again" : "Load more " + label}
    </button>
  </div>;
}
