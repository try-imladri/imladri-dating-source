import { createContext, useContext, useEffect, useMemo, useRef, useState, useCallback } from "react";
import { datingError } from "@/lib/dating";

export const DatingContext = createContext(null);
export const useDating = () => useContext(DatingContext);

export function useDatingResource(path, refreshKey = 0) {
  const { api, identity } = useDating();
  const scope = useMemo(() => ({ api, path, identity, active: true }), [api, path, identity]);
  const [saved, setSaved] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const request = useRef(null);
  const load = useCallback(async () => {
    if (!path) { setLoading(false); return; }
    request.current?.abort();
    const abort = new AbortController(); request.current = abort;
    setLoading(true); setError(null);
    try {
      const response = await api.get(path, { signal: abort.signal });
      if (scope.active && !abort.signal.aborted) setSaved({ scope, value: response.data });
    } catch (failure) {
      if (scope.active && !abort.signal.aborted) { setSaved(null); setError({ scope, value: datingError(failure) }); }
    } finally { if (scope.active && !abort.signal.aborted) setLoading(false); }
  }, [api, path, scope]);
  useEffect(() => {
    scope.active = true; setSaved(null); setError(null); load();
    return () => { scope.active = false; request.current?.abort(); };
  }, [scope, load, refreshKey]);
  return { data: saved?.scope === scope ? saved.value : null, error: error?.scope === scope ? error.value : null, loading, reload: load };
}

export function useDatingAction() {
  const { identity } = useDating();
  const scope = useMemo(() => ({ identity, active: true }), [identity]);
  const [status, setStatus] = useState(null);
  const lock = useRef(false);
  useEffect(() => { scope.active = true; lock.current = false; setStatus(null); return () => { scope.active = false; }; }, [scope]);
  const run = async (work, done) => {
    if (lock.current) return;
    lock.current = true; setStatus({ scope, busy: true });
    try {
      const result = await work();
      if (scope.active) { await done?.(result); setStatus({ scope, busy: false, success: true }); }
      return result;
    } catch (failure) { if (scope.active) setStatus({ scope, busy: false, error: datingError(failure) }); }
    finally { if (scope.active) lock.current = false; }
  };
  const current = status?.scope === scope ? status : {};
  return { ...current, run, clear: () => { if (!lock.current) setStatus(null); }, active: () => scope.active };
}
