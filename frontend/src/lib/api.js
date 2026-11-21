import axios from "axios";
import { useEffect, useMemo, useRef } from "react";
import { useAuth, useClerk } from "@clerk/react";
import { pendingCouplesInvite } from "./couplesInvite";

export const BACKEND_URL = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/+$/, "");
export const API = `${BACKEND_URL}/api`;
function apiClient() {
  const api = axios.create({ baseURL: API });
  api.interceptors.response.use(undefined, async error => {
    const { config, response } = error;
    if (!config || !["get", "head"].includes(config.method) ||
        response?.status !== 503 ||
        response.headers?.["x-imladri-reconnecting"] !== "1" || config.signal?.aborted) throw error;
    config.reconnectUntil ??= Date.now() + 120_000;
    if (Date.now() + 3_000 > config.reconnectUntil) throw error;
    await new Promise(resolve => setTimeout(resolve, 3_000));
    if (config.signal?.aborted) throw error;
    return api.request(config);
  });
  return api;
}

export const publicApi = apiClient();

export function useApi() {
  const { getToken, userId, sessionId } = useAuth();
  const clerk = useClerk();
  const live = useRef({ getToken, clerk, userId, sessionId });
  live.current = { getToken, clerk, userId, sessionId };
  const scope = useMemo(() => {
    const client = apiClient();
    const state = { client, active: true };
    const assertCurrentSession = () => {
      // Clerk's getToken callback is stable across active-account changes and
      // reads the live session when it resolves. Retained clients must never
      // send the previous account's data with the next account's token.
      const current = live.current;
      if (!state.active || !userId || !sessionId || current.userId !== userId || current.sessionId !== sessionId
          || current.clerk.user?.id !== userId || current.clerk.session?.id !== sessionId) {
        throw new axios.CanceledError("Your signed-in session changed. Please try again.");
      }
    };
    client.interceptors.request.use(async (cfg) => {
      assertCurrentSession();
      const token = await live.current.getToken();
      assertCurrentSession();
      if (token) cfg.headers.Authorization = `Bearer ${token}`;
      return cfg;
    });
    client.interceptors.response.use((response) => {
      assertCurrentSession();
      return response;
    }, (error) => {
      assertCurrentSession();
      return Promise.reject(error);
    });
    return state;
  }, [userId, sessionId]);
  useEffect(() => {
    scope.active = true;
    return () => { scope.active = false; };
  }, [scope]);
  return scope.client;
}

export const errMsg = (e) => {
  const d = e?.response?.data?.detail;
  return typeof d === "string" ? d : "Something went wrong, please try again";
};

const ORIGIN_KEY = "imaldri_origin";
export function setOrigin(origin) {
  try { localStorage.setItem(ORIGIN_KEY, origin); } catch { /* Optional when browser storage is disabled. */ }
}
export async function applySignupOrigin(api, member) {
  if (member.closing) return member;
  let origin;
  try { origin = localStorage.getItem(ORIGIN_KEY); } catch { /* Use the direct entry label. */ }
  if (!member.origin) {
    const entryPage = pendingCouplesInvite() ? "couples/invite" : origin || "direct";
    const { data } = await api.patch("/me", { origin: entryPage });
    member = { ...member, ...data };
  }
  // Retain the entry page if saving fails, or a different tab records a newer click.
  try { if (origin && localStorage.getItem(ORIGIN_KEY) === origin) localStorage.removeItem(ORIGIN_KEY); } catch { /* Optional storage. */ }
  return member;
}

const T = (pairs) => pairs.map(([v, l]) => ({ v, l }));
export const REL_GROUPS = [
  { key: "romantic", label: "Romantic", types: T([["crush", "Crush"], ["talking", "Talking stage"], ["situationship", "Situationship"], ["partner", "Partner"], ["husband", "Husband / Fiancé"], ["fwb", "Friends with benefits"], ["long_distance", "Long-distance / Online"], ["ex", "Ex"]]) },
  { key: "family", label: "Family", types: T([["mom", "Mom"], ["dad", "Dad"], ["sister", "Sister"], ["brother", "Brother"], ["grandparent", "Grandparent"], ["aunt", "Aunt"], ["uncle", "Uncle"], ["cousin", "Cousin"], ["other_family", "Other family"]]) },
  { key: "friends", label: "Friends", types: T([["best_friend", "Best friend"], ["friend", "Friend"], ["roommate", "Roommate"]]) },
  { key: "work", label: "Work", types: T([["boss", "Boss"], ["coworker", "Coworker"], ["client", "Client"]]) },
  { key: "other", label: "Other", types: T([["other", "Other"]]) },
];
export const REL_LABEL = Object.fromEntries(REL_GROUPS.flatMap((g) => g.types.map((t) => [t.v, t.l])));
export const GROUP_OF = Object.fromEntries(REL_GROUPS.flatMap((g) => g.types.map((t) => [t.v, g.key])));
export const GROUP_LABEL = Object.fromEntries(REL_GROUPS.map((g) => [g.key, g.label]));
export const isRomantic = (t) => GROUP_OF[t] === "romantic";
export const relLabel = (p) => p.rel_label || REL_LABEL[p.rel_type] || p.rel_type;
export const STAGES = {
  romantic: [
    { v: "crush", l: "Just a crush" },
    { v: "early", l: "Early dating" },
    { v: "established", l: "Established" },
    { v: "fading", l: "Fading" },
  ],
  other: [
    { v: "good", l: "Good" },
    { v: "strained", l: "Strained" },
    { v: "distant", l: "Distant" },
    { v: "repairing", l: "Repairing" },
  ],
};
export const stagesFor = (t) => (isRomantic(t) ? STAGES.romantic : STAGES.other);
export const STAGE_LABEL = Object.fromEntries([...STAGES.romantic, ...STAGES.other].map((s) => [s.v, s.l]));
export const pad = (n) => String(n > 100 ? n - 100 : n).padStart(2, "0");
