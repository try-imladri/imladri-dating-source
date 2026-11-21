export const DATING_ROOT = "/dating";

export function datingError(error) {
  const detail = error?.response?.data?.detail;
  if (Array.isArray(detail)) return detail.map(item => item.msg?.replace(/^Value error, /, "")).filter(Boolean).join(" ") || "Check your entries and try again.";
  return typeof detail === "string" ? detail : detail?.message || error?.message || "That didn’t finish. Please try again.";
}

export function datingPhotoPath(url) {
  // Only the protected, same-application photo endpoint is a valid image source.
  const path = typeof url === "string" ? url : "";
  return /^\/api\/dating\/photos\/[A-Za-z0-9_-]+\/content$/.test(path) ? path.slice(4) : null;
}

export function humanLabel(value) {
  const labels = { long_term: "Long-term relationship", life_partner: "Life partner", figuring_out: "Figuring it out", monogamous: "Monogamous", nonmonogamous: "Non-monogamous", open_to_discuss: "Open to discussing", nonbinary: "Non-binary", another: "Another gender", woman: "Woman", man: "Man", active: "Discoverable", pending: "In review", draft: "Private draft", paused: "Paused", rejected: "Needs an update", suspended: "Suspended", closed: "Closed", approved: "Reviewed" };
  return labels[value] || String(value || "").replace(/_/g, " ");
}

export function profilePayload(profile) {
  return Object.fromEntries(["display_name", "date_of_birth", "gender", "city", "bio", "intent", "relationship_structure"].map(key => [key, profile[key] || ""]));
}

export function preferencePayload(preferences) {
  const keys = ["age_min", "age_max", "genders", "cities", "intents", "relationship_structures", "personality", "intimacy", "personality_matching", "intimacy_matching"];
  return { ...Object.fromEntries(keys.filter(key => preferences[key] !== undefined).map(key => [key, preferences[key]])), expected_revision: preferences.revision || 0 };
}

export function requestId() {
  return globalThis.crypto?.randomUUID?.() || `dating-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function photoList(profile) {
  return Array.isArray(profile?.photos) ? profile.photos : [];
}

export function safeTextDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
