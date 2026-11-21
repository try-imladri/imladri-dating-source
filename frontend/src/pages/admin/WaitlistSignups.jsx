import { useEffect, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { errMsg } from "@/lib/api";

function Signups({ api }) {
  const [history, setHistory] = useState([""]);
  const [page, setPage] = useState(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const after = history[history.length - 1];
  useEffect(() => {
    const request = new AbortController();
    setPage(null); setError("");
    api.get("/admin/waitlist", { params: { after, limit: 25 }, signal: request.signal }).then(
      ({ data }) => { if (!request.signal.aborted) setPage(data); },
      e => { if (!request.signal.aborted) setError(errMsg(e)); },
    );
    return () => request.abort();
  }, [api, after, attempt]);
  return <>
    {error ? <p role="alert" className="text-sm">{error} <button type="button" className="underline" onClick={() => setAttempt(n => n + 1)}>Retry</button></p>
      : !page ? <p role="status">Loading signups…</p>
      : page.entries.length ? <ul aria-label="Waitlist signups" className="divide-y divide-[var(--line)]">
        {page.entries.map(row => <li key={row.email} className="grid gap-1 py-3 text-sm">
          <span className="break-all font-semibold">{row.email}</span>
          <span className="break-words text-xs text-[var(--muted)]">{row.origin || "Direct"} · {row.ts ? new Date(row.ts).toLocaleDateString() : "Date unavailable"}</span>
        </li>)}
      </ul> : <p>No waitlist signups on this page.</p>}
    {(after || page?.next) && <nav aria-label="Waitlist pages" className="flex items-center gap-3 text-sm">
      <button type="button" className="underline disabled:opacity-40" disabled={!after} onClick={() => setHistory(current => current.slice(0, -1))}>Previous</button>
      <span>Page {history.length}</span>
      <button type="button" className="underline disabled:opacity-40" disabled={!page?.next} onClick={() => setHistory(current => [...current, page.next])}>Next</button>
    </nav>}
  </>;
}

export default function WaitlistSignups({ api, total }) {
  return <Dialog>
    <DialogTrigger asChild><button type="button" className="card min-w-0 px-4 py-3 text-left" data-testid="metric-waitlist">
      <span className="block font-mono text-[10px] uppercase tracking-widest text-[var(--muted)]">Waitlist</span>
      <span className="mt-1.5 block font-display text-3xl font-black leading-none">{total}</span>
      <span className="mt-1 block text-[11px] underline">All recorded signups · View signups</span>
    </button></DialogTrigger>
    <DialogContent className="max-h-[86dvh] w-[calc(100%-2rem)] max-w-lg overflow-auto">
      <DialogHeader><DialogTitle>Waitlist signups</DialogTitle><DialogDescription>Newest signups appear first. Staff follow up by email manually when a place opens.</DialogDescription></DialogHeader>
      <Signups api={api} />
    </DialogContent>
  </Dialog>;
}
