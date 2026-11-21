const key = "imladri.start.private-draft";
const lifetime = 60 * 60 * 1000;

const demoKey = "imaldri_demo_who";
export function readDemoSubject() {
  try {
    const subject = localStorage.getItem(demoKey);
    return ["him", "her"].includes(subject) ? subject : "";
  } catch { return ""; }
}

export function storeDemoSubject(subject) {
  try {
    if (subject) localStorage.setItem(demoKey, subject);
    else localStorage.removeItem(demoKey);
  } catch { /* The demo and the first read work without this preference. */ }
}

export function readStartDraft(member) {
  try {
    const saved = JSON.parse(sessionStorage.getItem(key));
    if (!saved) return {};
    if (saved.owner !== member.user_id || saved.accountCreated !== member.created_at ||
        saved.revision !== (member.content_revision || 0) || Date.now() >= saved.expires) {
      sessionStorage.removeItem(key);
      return {};
    }
    return saved.form || {};
  } catch { return {}; }
}

export function clearStartDraft() {
  try { sessionStorage.removeItem(key); } catch { /* Storage can be disabled. */ }
}

export function storeStartDraft(member, form) {
  if (!member.user_id || (!form.name && !form.what && !form.paste && !form.lines && !form.hadImage && !form.request)) {
    clearStartDraft();
    return;
  }
  try {
    sessionStorage.setItem(key, JSON.stringify({
      owner: member.user_id, accountCreated: member.created_at, revision: member.content_revision || 0,
      expires: Date.now() + lifetime, form
    }));
  } catch { /* The in-memory form still works when browser storage is unavailable. */ }
}
