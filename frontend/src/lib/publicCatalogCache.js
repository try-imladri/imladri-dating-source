import { publicApi } from "./api";

// Keep this deliberately limited to the two anonymous directory responses.
// Details and configuration always go to the server, including withdrawal checks.
const paths = new Set(["/public/archetypes", "/public/figures"]);
const entries = new Map();
export const PUBLIC_CATALOG_TTL_MS = 45_000;
export const isPublicCatalogPath = path => paths.has(path);

export function getCachedPublicCatalog(path) {
  const entry = entries.get(path);
  return entry && Date.now() < entry.expiresAt ? entry.data : undefined;
}

export function invalidatePublicCatalog(path) {
  if (paths.has(path)) entries.delete(path);
}

function aborted() {
  return new DOMException("The catalog view was closed", "AbortError");
}

// A view owns its subscription, not the shared network request. Navigating away
// must not cancel a prefetch or another mounted view that still needs the data.
function subscribe(promise, signal) {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(aborted());
  return new Promise((resolve, reject) => {
    const onAbort = () => { cleanup(); reject(aborted()); };
    const cleanup = () => signal.removeEventListener("abort", onAbort);
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      data => { cleanup(); if (!signal.aborted) resolve(data); },
      error => { cleanup(); if (!signal.aborted) reject(error); },
    );
  });
}

export function loadPublicCatalog(path, { signal } = {}) {
  if (!paths.has(path)) return Promise.reject(new Error("Not a public catalog directory"));
  if (signal?.aborted) return Promise.reject(aborted());
  const cached = getCachedPublicCatalog(path);
  if (cached !== undefined) return subscribe(Promise.resolve(cached), signal);
  let entry = entries.get(path);
  if (!entry?.promise) {
    entry = {};
    entries.set(path, entry);
    entry.promise = publicApi.get(path).then(
      ({ data }) => {
        // Invalidating for retry or a detail 404 also retires an older request.
        if (entries.get(path) === entry) {
          entry.data = data;
          entry.expiresAt = Date.now() + PUBLIC_CATALOG_TTL_MS;
          entry.promise = null;
        }
        return data;
      },
      error => {
        if (entries.get(path) === entry) entries.delete(path);
        throw error;
      },
    );
  }
  return subscribe(entry.promise, signal);
}

export function prefetchPublicCatalog(path) {
  return loadPublicCatalog(path).catch(() => undefined);
}
