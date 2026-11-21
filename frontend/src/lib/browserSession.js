const cookieName = "imladri_browser_session";
const legacyTabKey = "imaldri_alive";
const activityKey = "imladri_tab_visit";
const rememberKey = "imaldri_remember";
let tabVisit;
const cookieOptions = () => `Path=/; SameSite=Lax${window.location.protocol === "https:" ? "; Secure" : ""}`;
const cookieValue = name => {
  try { return document.cookie.split(";").map(value => value.trim()).find(value => value.startsWith(name+"="))?.slice(name.length+1); }
  catch { return undefined; }
};
const setCookie = (name, value, lifetime = "") => {
  try { document.cookie = `${name}=${value}; ${lifetime}${cookieOptions()}`; } catch { /* Site cookies may be disabled. */ }
};

export function rememberPreference() {
  const fallback = cookieValue(rememberKey);
  if (fallback === "0" || fallback === "1") return fallback === "1";
  try { return localStorage.getItem(rememberKey) !== "0"; } catch { return true; }
}

export function setRememberPreference(on) {
  const value = on ? "1" : "0";
  try {
    localStorage.setItem(rememberKey, value);
    if (cookieValue(rememberKey) !== undefined) setCookie(rememberKey, "", "Max-Age=0; ");
  } catch {
    // Only the preference goes in this host-only fallback. The login session is owned by Clerk.
    setCookie(rememberKey, value, "Max-Age=34560000; ");
    if (cookieValue(rememberKey) !== value) throw new Error("Allow site data for Imladri in your browser to continue signing in.");
  }
  if (on) endBrowserSession();
  else {
    beginBrowserSession();
    if (!hasBrowserSession()) throw new Error("Allow session storage or cookies for Imladri to sign in with Remember me off.");
  }
}

// The browser owns this host-only session cookie's lifetime. It carries no account or session identifier.
export function beginBrowserSession() {
  setCookie(cookieName, "1");
  try {
    if (cookieValue(cookieName) === "1") sessionStorage.removeItem(legacyTabKey);
    else sessionStorage.setItem(legacyTabKey, "1");
  } catch { /* The shared cookie suffices when tab storage is disabled. */ }
}

export function hasBrowserSession() {
  if (cookieValue(cookieName) === "1") return true;
  try { if (!sessionStorage.getItem(legacyTabKey)) return false; } catch { return false; }
  beginBrowserSession();
  return true;
}

export function endBrowserSession() {
  setCookie(cookieName, "", "Max-Age=0; ");
  try { sessionStorage.removeItem(legacyTabKey); sessionStorage.removeItem(activityKey); } catch { /* Optional storage. */ }
  tabVisit = undefined;
}

export async function recordMemberVisit(api, memberId, day = new Date().toISOString().slice(0, 10)) {
  if (!memberId) return;
  if (!tabVisit) {
    try { tabVisit = JSON.parse(sessionStorage.getItem(activityKey)); } catch { /* Optional browser storage. */ }
  }
  const persist = () => {
    try { sessionStorage.setItem(activityKey, JSON.stringify(tabVisit)); } catch { /* Keep the in-memory marker. */ }
  };
  if (tabVisit?.memberId !== memberId || tabVisit.day !== day) {
    tabVisit = { memberId, day, attempt: crypto.randomUUID(), recorded: false };
    persist();
  }
  if (tabVisit.recorded) return;
  const current = tabVisit;
  await api.post("/me/event", { type: "session", attempt_id: current.attempt });
  if (tabVisit === current) {
    current.recorded = true;
    persist();
  }
}
