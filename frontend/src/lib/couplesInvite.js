const KEY = "imladri_couples_invite";
const TOKEN = /^[A-Za-z0-9_-]{24,200}$/;
export function storeCouplesInvite(value) {
  if (!TOKEN.test(value || "")) return false;
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ token: value, expires: Date.now() + 7 * 86400000 }));
    return true;
  } catch {
    return false;
  }
}
export function pendingCouplesInvite() {
  try {
    const entry = JSON.parse(sessionStorage.getItem(KEY) || "null");
    if (entry?.expires > Date.now() && TOKEN.test(entry.token)) return entry.token;
    clearCouplesInvite();
  } catch {
    clearCouplesInvite();
  }
  return "";
}
export function clearCouplesInvite() {
  try {
    sessionStorage.removeItem(KEY);
  } catch {}
}
