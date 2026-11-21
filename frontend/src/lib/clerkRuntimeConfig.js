const entries = new Map();
const TTL = 60_000;

// Public provider configuration only. Never store a member, token or permission.
export function readClerkRuntimeConfig(url, fetcher = fetch) {
  const existing = entries.get(url);
  if (existing?.fetcher === fetcher && (existing.pending || existing.expires > Date.now())) return existing.promise;
  const entry = { pending: true, expires: 0, fetcher };
  entry.promise = Promise.resolve().then(() => fetcher(url)).then(async response => {
    const config = response.ok ? await response.json() : null;
    const result = { ok: response.ok, config,
      reconnecting: response.status === 503 && response.headers?.get("X-Imladri-Reconnecting") === "1" };
    entry.pending = false;
    if (config?.enabled === true && typeof config.publishable_key === "string" && config.publishable_key) {
      entry.expires = Date.now() + TTL;
    } else if (entries.get(url) === entry) entries.delete(url);
    return result;
  }).catch(error => { if (entries.get(url) === entry) entries.delete(url); throw error; });
  entries.set(url, entry);
  return entry.promise;
}
