import { useCallback, useLayoutEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import "./settings.css";

export const PRIVACY_PURPOSES = [
  ["overview", "Overview"], ["history", "Questionnaire"], ["questionnaires", "Questionnaire research"],
  ["exports", "Research downloads"], ["tone", "Tone research"], ["your-type", "Your Type"],
  ["trends", "Group statistics"], ["partner", "Partner sharing"],
];
const PURPOSES = new Set(PRIVACY_PURPOSES.map(([key]) => key));
const LEGACY = {
  "data-permissions": ["privacy", "overview"],
  "questionnaire-research": ["privacy", "questionnaires"],
  "partner-assessment-sharing": ["privacy", "partner"],
  "your-type-data": ["privacy", "your-type"],
  "settings-export": ["data"], "settings-delete-all": ["data"], "your-data": ["data"],
  appearance: ["account"], "/appearance": ["account"], plan: ["plan"], profile: ["account"], security: ["security"],
};

// Clerk owns #/page. Keep our purpose outside that hash to avoid two routers
// interpreting the old #questionnaire-research anchor as a Clerk page.
export function settingsDestination(hash = "", search = "") {
  const alias = LEGACY[hash.replace(/^#/, "")];
  const query = new URLSearchParams(search);
  const pane = alias?.[0] || hash.replace(/^#\/?/, "").split(/[/?]/)[0] || "account";
  const requested = alias?.[1] || query.get("settingsPurpose");
  const removedSubmission = requested === "submission";
  const purpose = removedSubmission ? "history" : PURPOSES.has(requested) ? requested : "overview";
  if (alias?.[1] || removedSubmission) query.set("settingsPurpose", purpose);
  return { pane, purpose, hash: alias || removedSubmission ? (pane === "account" ? "#/" : `#/${pane}`) : hash,
    search: query.toString() ? `?${query}` : "", legacy: !!alias || removedSubmission };
}

export function useSettingsNavigation() {
  const location = useLocation();
  const [destination, setDestination] = useState(() => settingsDestination(location.hash, location.search));
  useLayoutEffect(() => {
    const sync = (hash, search) => {
      const next = settingsDestination(hash, search);
      setDestination(next);
      if (next.legacy) {
        window.history.replaceState(window.history.state, "", `${window.location.pathname}${next.search}${next.hash}`);
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      }
    };
    const changed = () => sync(window.location.hash, window.location.search);
    window.addEventListener("hashchange", changed);
    sync(location.hash, location.search);
    return () => window.removeEventListener("hashchange", changed);
  }, [location.hash, location.search]);
  const selectPurpose = useCallback(purpose => {
    if (!PURPOSES.has(purpose)) return;
    const query = new URLSearchParams(window.location.search);
    query.set("settingsPurpose", purpose);
    window.history.replaceState(window.history.state, "", `${window.location.pathname}?${query}#/privacy`);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  }, []);
  return { ...destination, selectPurpose };
}

export function PrivacyPurposeNavigation({ purpose, onSelect }) {
  return <nav className="settings-purpose-nav" aria-label="Privacy purposes">
    {PRIVACY_PURPOSES.map(([key, label]) => <button key={key} type="button"
      aria-current={purpose === key ? "page" : undefined} onClick={() => onSelect(key)}
      data-testid={`settings-purpose-${key}`}>{label}</button>)}
  </nav>;
}

export const SETTINGS_APPEARANCE = { elements: {
  rootBox: { width: "100%", maxWidth: "none" },
  cardBox: { width: "100%", maxWidth: "none", height: "auto", maxHeight: "none", boxShadow: "none",
    border: "1px solid var(--line)", borderRadius: "1rem" },
  card: { width: "100%", maxWidth: "none", height: "auto", maxHeight: "none" },
  scrollBox: { height: "auto", maxHeight: "none", overflow: "visible" },
  pageScrollBox: { height: "auto", maxHeight: "none", overflow: "visible" },
  profilePage: { minWidth: 0, width: "100%", maxWidth: "none" },
  profileSection__danger: { display: "none" },
  formFieldInputShowPasswordButton: { color: "var(--ink)", minWidth: "2.5rem", minHeight: "2.5rem" },
  formFieldInputShowPasswordIcon: { width: "1.125rem", height: "1.125rem" },
} };
