import { profileWriteRevision, waitForProfileWrites } from "./profileWriteBarrier";

// One initial navigation only: no completed navigation cache, persisted data,
// shared-partner reads, staff reads or authorization decisions stored here.
const pending = new WeakMap();
const endpoints = {
  "/app/personal": "/me/assessments", "/app/intimacy": "/me/assessments?include_intimacy=true", "/app/your-type": "/your-type",
};

export function beginEntryRead(api, owner, pathname, isCurrent) {
  pending.get(api)?.discard();
  const endpoint = endpoints[pathname.replace(/\/$/, "")];
  if (!owner || !endpoint) return null;
  const controller = new AbortController();
  const entry = { owner, endpoint, authorized: false, active: true, consumed: false, revision: null,
    isCurrent, controller, timer: null,
    discard() {
      entry.active = false;
      clearTimeout(entry.timer);
      controller.abort();
      if (pending.get(api) === entry) pending.delete(api);
    },
    authorize(member) {
      const consent = member.main_consent;
      const access = member.workspace_access;
      const entitled = member.personal_access ?? (member.usage?.plan === "plus" && member.usage.period_verified);
      if (!entry.active || !isCurrent() || member.user_id !== owner || member.closing || !entitled
        || access?.version !== "workspace-access-v1" || access.mode !== "individual"
        || member.role !== "member" || !consent?.reviewed || !consent.welcome_complete
        || consent.pair_id !== null || consent.pair_sharing?.available) return entry.discard();
      entry.authorized = true;
    },
  };
  // A slow sign-in/consent gate must not retain a completed private response.
  entry.timer = setTimeout(entry.discard, 20_000);
  entry.promise = waitForProfileWrites(api, owner).then(() => {
    if (!entry.active || !isCurrent()) throw Object.assign(new Error("Entry changed"), { code: "ERR_CANCELED" });
    entry.revision = profileWriteRevision(api, owner);
    return api.get(endpoint, { signal: controller.signal,
      ...(endpoint.startsWith("/me/assessments") ? { headers: { "X-Questionnaire-Owner": owner } } : {}) });
  }).catch(error => {
    // The account may become eligible while /me is still loading. A failed
    // speculative read must not poison its later authoritative page read.
    if (!entry.authorized && !entry.consumed) entry.discard();
    throw error;
  });
  // Speculation may be denied by the server; only the eventual page consumes
  // that failure. It never creates an unhandled rejection or retries a write.
  void entry.promise.catch(() => {});
  pending.set(api, entry);
  return entry;
}

export function consumeEntryRead(api, owner, endpoint, signal) {
  const entry = pending.get(api);
  if (entry && (!entry.isCurrent() || entry.revision !== profileWriteRevision(api, entry.owner))) entry.discard();
  if (!entry || !entry.active || !entry.authorized || entry.consumed || entry.owner !== owner
    || entry.endpoint !== endpoint || !entry.isCurrent() || entry.revision !== profileWriteRevision(api, owner)
    || signal?.aborted) return null;
  pending.delete(api);
  entry.consumed = true;
  clearTimeout(entry.timer);
  const cancel = () => entry.discard();
  signal?.addEventListener("abort", cancel, { once: true });
  return entry.promise.finally(() => {
    signal?.removeEventListener("abort", cancel);
    entry.active = false;
  });
}
