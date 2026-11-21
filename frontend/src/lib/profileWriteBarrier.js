// Navigation may leave Personal before its debounced save starts. Keep only
// pending completion promises (never answers), scoped to this API and owner.
const pending = new WeakMap();
const revisions = new WeakMap();

export function profileWriteRevision(api, owner) {
  return revisions.get(api)?.get(owner) || 0;
}

export function trackProfileWrite(api, owner, request) {
  let versions = revisions.get(api);
  if (!versions) { versions = new Map(); revisions.set(api, versions); }
  versions.set(owner, profileWriteRevision(api, owner) + 1);
  let owners = pending.get(api);
  if (!owners) { owners = new Map(); pending.set(api, owners); }
  let writes = owners.get(owner);
  if (!writes) { writes = new Set(); owners.set(owner, writes); }
  const settled = Promise.resolve(request).then(() => undefined, () => undefined);
  writes.add(settled);
  void settled.then(() => {
    writes.delete(settled);
    if (!writes.size) owners.delete(owner);
  });
  return request;
}

export async function waitForProfileWrites(api, owner) {
  const writes = pending.get(api)?.get(owner);
  if (writes?.size) await Promise.all([...writes]);
}
