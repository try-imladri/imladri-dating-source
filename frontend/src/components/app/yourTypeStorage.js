const PREFIX = "imladri.your-type.pending.v1:";

export function pendingStorageAvailable(owner) {
  try { sessionStorage.getItem(PREFIX + owner); return true; }
  catch { return false; }
}

export function clearYourTypePendingExcept(owner) {
  try {
    for (const key of Object.keys(sessionStorage)) if (key.startsWith(PREFIX) && (!owner || key !== PREFIX + owner)) sessionStorage.removeItem(key);
  } catch { /* Storage can be disabled without exposing another account's buffer. */ }
}
export function readPending(owner) {
  let raw;
  try {
    // Never offer another signed-in member's unsaved choices in this browser.
    clearYourTypePendingExcept(owner);
    raw = sessionStorage.getItem(PREFIX + owner);
  } catch { return null; }
  try {
    if (!raw) return null;
    if (raw.length > 64_000) return { invalid: true };
    const parsed = JSON.parse(raw);
    if (parsed?.start && parsed.start.expected_member !== owner) return { invalid: true };
    return parsed?.owner === owner ? parsed : { invalid: true };
  } catch { return { invalid: true }; }
}
export function storePending(owner, value) {
  try {
    if (value) sessionStorage.setItem(PREFIX + owner, JSON.stringify({ owner, ...value }));
    else sessionStorage.removeItem(PREFIX + owner);
    return true;
  } catch { return false; /* Server saving still works; the in-memory queue stays authoritative. */ }
}
