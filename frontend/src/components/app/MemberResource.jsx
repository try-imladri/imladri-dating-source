import { useCallback, useEffect, useRef, useState } from "react";
import { errMsg } from "@/lib/api";

export function useMemberResource(api, path, revision) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState(null);
  const pending = useRef(null);
  const reload = useCallback(async () => {
    pending.current?.abort();
    const request = new AbortController();
    pending.current = request;
    setError("");
    setStatus(null);
    if (!path) return;
    try {
      const response = await api.get(path, { signal: request.signal });
      if (!request.signal.aborted) setData(response.data);
    } catch (failure) {
      if (!request.signal.aborted) {
        setError(errMsg(failure));
        setStatus(failure.response?.status);
      }
    }
  }, [api, path]);
  useEffect(() => {
    setData(null);
    reload();
    return () => pending.current?.abort();
  }, [reload, revision]);
  return { data, error, status, reload };
}

export function MemberLoadState({ label, error, retry, testId }) {
  if (!error) return <p className="font-mono text-sm text-[var(--muted)]">Loading…</p>;
  return (
    <div role="alert" className="space-y-3 text-sm">
      <p>We couldn’t load {label}. {error}</p>
      <button type="button" className="btn-ghost px-4 py-2" data-testid={testId} onClick={retry}>Try again</button>
    </div>
  );
}
