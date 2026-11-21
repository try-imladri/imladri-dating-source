import { act, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ResearchFirstUse from "./ResearchFirstUse";
import AppShell from "./AppShell";
import { mainConsentFixture, consentScopes, consentDigest, applyMainConsent, installConsentCrypto } from "@/testFixtures/mainConsent";

jest.mock("react-router-dom", () => { global.TextEncoder = require("util").TextEncoder; return jest.requireActual("react-router"); });
jest.mock("@clerk/react", () => ({ Show: ({ when, children }) => when === "signed-in" ? children : null,
  useAuth: () => ({ isLoaded: true, isSignedIn: true, userId: "fictional-adult-A", sessionId: "fictional-session" }), useClerk: () => ({ signOut: jest.fn() }) }));
jest.mock("@/lib/api", () => ({ useApi: () => mockApi, applySignupOrigin: async (_, me) => me, errMsg: error => error.message }));
jest.mock("@/lib/browserSession", () => ({ recordMemberVisit: async () => {}, endBrowserSession: jest.fn() }));
jest.mock("./NotificationBell", () => ({ NotificationBell: () => null }));
jest.mock("@/lib/theme", () => ({ ThemeToggle: () => null }));
let mockApi, host, root, autosave, deferredRender, restoreCrypto;
const owner = "fictional-adult-A";
const clone = value => JSON.parse(JSON.stringify(value));
const member = (options = {}, changes = {}) => ({ user_id: owner, role: "member", age: 30, onboarded: true, personal_access: true,
  main_consent: mainConsentFixture(owner, { reviewed: false, ...options }), ...changes });
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
const apiFor = me => {
  let state = clone(me.main_consent);
  const api = { get: jest.fn(async () => ({ data: clone(state) })),
    patch: jest.fn(async (_, body) => { state = applyMainConsent(state, body); return { data: clone(state) }; }),
    put: jest.fn(async (_, body) => {
      state.pair_sharing = { ...state.pair_sharing, reviewed: true, expected_revision: state.pair_sharing.expected_revision + 1,
        personality: body.personality, intimacy: body.intimacy, state_sha256: consentDigest(state.pair_sharing.state_sha256 + ":saved") };
      return { data: { pair_id: body.pair_id, notice_version: body.notice_version,
        sharing: { version: body.notice_version, revision: state.pair_sharing.expected_revision, personality: body.personality, intimacy: body.intimacy } } };
    }), saved: () => clone(state), replace: value => { state = clone(value); } };
  return api;
};
function Workspace() { useEffect(() => { autosave(); }, []); return <p data-testid="workspace-child">Workspace may mount</p>; }
function Screen({ api, initial, bypass = false, onUpdate = () => {} }) {
  const [me, setMe] = useState(initial);
  const update = next => setMe(previous => { const value = typeof next === "function" ? next(previous) : next; onUpdate(value); return value; });
  return <><ResearchFirstUse api={api} me={me} setMe={update} bypass={bypass}>{() => { deferredRender(); return <Workspace />; }}</ResearchFirstUse>
    <output data-testid="member-state">{JSON.stringify(me)}</output></>;
}
const render = async (api, me = member(), props = {}) => act(async () => root.render(<Screen key={me.user_id} api={api} initial={me} {...props} />));
const element = id => host.querySelector('[data-testid="' + id + '"]');
const current = () => JSON.parse(element("member-state").textContent);
const click = async node => { expect(node).toBeTruthy(); await act(async () => node.click()); };
const checkAdult = () => click(element("main-consent-adult"));
const customize = () => click(element("main-consent-customize"));
const decline = async () => { await customize(); await click(element("main-consent-decline-all")); await click(element("main-consent-save")); };
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; restoreCrypto = installConsentCrypto();
  host = document.body.appendChild(document.createElement("div")); root = createRoot(host); autosave = jest.fn(); deferredRender = jest.fn(); });
afterEach(() => { act(() => root.unmount()); host.remove(); restoreCrypto(); delete global.IS_REACT_ACT_ENVIRONMENT; });

test("one comprehensive welcome lists all nine purposes before tab content, redirects or saving", async () => {
  const me = member(), api = apiFor(me); await render(api, me);
  expect(element("research-welcome")).not.toBeNull(); expect(element("workspace-child")).toBeNull();
  expect(deferredRender).not.toHaveBeenCalled(); expect(autosave).not.toHaveBeenCalled(); expect(api.get).not.toHaveBeenCalled(); expect(api.patch).not.toHaveBeenCalled();
  for (const scope of consentScopes) expect(element("main-consent-purpose-" + scope)).not.toBeNull();
  expect(element("main-consent-notice").textContent).toBe(me.main_consent.notice);
  expect(element("main-consent-choice-questionnaire")).toBeNull(); expect(element("main-consent-adult").checked).toBe(false);
  expect(element("main-consent-customize").disabled).toBe(false); expect(host.textContent).toContain("unfinished answers");
});

test("Agree to all is a positive adult-confirmed action that enables only currently eligible purposes", async () => {
  const me = member(), api = apiFor(me), pending = deferred(); api.patch.mockReturnValue(pending.promise); await render(api, me);
  expect(element("main-consent-agree-all").disabled).toBe(true); await click(element("main-consent-agree-all")); expect(api.patch).not.toHaveBeenCalled();
  await checkAdult(); await click(element("main-consent-agree-all")); await click(element("main-consent-agree-all"));
  expect(api.patch).toHaveBeenCalledTimes(1); expect(element("workspace-child")).toBeNull();
  const [path, body] = api.patch.mock.calls[0]; expect(path).toBe("/me/main-consent");
  expect(body).toMatchObject({ expected_member: owner, notice_version: "main-consent-v1", notice_sha256: me.main_consent.notice_sha256,
    expected_pair_id: null, revision: 0, action: "agree_all", adult_confirmed: true });
  expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/); expect(Object.keys(body.choices)).toEqual(consentScopes);
  for (const scope of consentScopes) expect(body.choices[scope]).toEqual({ ...me.main_consent.scopes[scope].permission, enabled: me.main_consent.scopes[scope].eligible });
  await act(async () => pending.resolve({ data: applyMainConsent(me.main_consent, body) }));
  expect(element("workspace-child")).not.toBeNull(); expect(autosave).toHaveBeenCalledTimes(1); expect(api.put).not.toHaveBeenCalled();
  expect(current().main_consent.scopes.couple_csv.permission.enabled).toBe(false);
});

test("same-screen customization starts at confirmed choices and grants only independently selected purposes", async () => {
  const me = member(), api = apiFor(me); await render(api, me); await customize();
  for (const scope of consentScopes) expect(element("main-consent-choice-" + scope).checked).toBe(false);
  await click(element("main-consent-choice-questionnaire")); await checkAdult(); await click(element("main-consent-choice-staff_history"));
  expect(element("main-consent-adult").checked).toBe(false); expect(element("main-consent-save").disabled).toBe(true);
  await checkAdult(); await click(element("main-consent-save"));
  const body = api.patch.mock.calls[0][1]; expect(body.action).toBe("save_choices");
  for (const scope of consentScopes) expect(body.choices[scope].enabled).toBe(["questionnaire", "staff_history"].includes(scope));
  expect(element("workspace-child")).not.toBeNull(); expect(current().answer_history_permission.enabled).toBe(true);
});

test("optional uses can all be declined without an adult affirmation or loss of app access", async () => {
  const me = member(), api = apiFor(me); await render(api, me); await decline();
  expect(api.patch.mock.calls[0][1]).toMatchObject({ action: "save_choices", adult_confirmed: false });
  for (const scope of consentScopes) expect(api.patch.mock.calls[0][1].choices[scope].enabled).toBe(false);
  expect(element("workspace-child")).not.toBeNull(); expect(current().everyday_trends_opt_in).toBe(false);
});

test("existing enabled choices are clearly current choices and can be preserved or withdrawn", async () => {
  const me = member({ permissions: { questionnaire: true, tone: true } }), api = apiFor(me); await render(api, me); await customize();
  expect(element("main-consent-choice-questionnaire").checked).toBe(true); expect(element("main-consent-choice-tone").checked).toBe(true);
  await click(element("main-consent-choice-tone")); await click(element("main-consent-save"));
  expect(current().questionnaire_research).toMatchObject({ enabled: true, revision: 3 });
  expect(current().tone_research).toMatchObject({ enabled: false, revision: 4 }); expect(api.patch.mock.calls[0][1].adult_confirmed).toBe(false);
});

test("unavailable purposes remain off and cannot be newly enabled", async () => {
  const eligibility = Object.fromEntries(consentScopes.map(scope => [scope, false]));
  const me = member({ eligibility }), api = apiFor(me); await render(api, me); await customize();
  expect(host.textContent).toContain("Not available now"); for (const scope of consentScopes) expect(element("main-consent-choice-" + scope).disabled).toBe(true);
  expect(element("main-consent-adult")).toBeNull(); await click(element("main-consent-agree-all"));
  for (const scope of consentScopes) expect(api.patch.mock.calls[0][1].choices[scope].enabled).toBe(false);
  expect(element("workspace-child")).not.toBeNull();
});

test.each([
  ["wrong owner", value => { value.expected_member = "other"; }], ["missing purpose", value => { delete value.scopes.tone; }],
  ["unknown permission version", value => { value.scopes.tone.permission.version = "future-version"; }],
  ["unreviewed complete", value => { value.reviewed = false; value.welcome_complete = true; }],
  ["stale receipt", value => { value.revision = 0; }], ["unrequested permission", value => { value.scopes.questionnaire.permission.enabled = true; }],
  ["wrong permission revision", value => { value.scopes.tone.permission.revision = 99; }],
  ["wrong pair", value => { value.pair_id = "other-pair"; value.pair_sharing.pair_id = "other-pair"; }],
  ["changed notice bytes", value => { value.notice += "changed"; }], ["malformed notice hash", value => { value.notice_sha256 = "bad"; }],
])("a %s response cannot apply half a receipt or unlock the app", async (_, corrupt) => {
  const me = member(), api = apiFor(me), updates = jest.fn(); api.patch.mockImplementation(async (_, body) => {
    const response = applyMainConsent(me.main_consent, body); corrupt(response); return { data: response };
  });
  await render(api, me, { onUpdate: updates }); await decline();
  expect(element("workspace-child")).toBeNull(); expect(updates).not.toHaveBeenCalled(); expect(api.patch).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain("Some choices may already be saved"); expect(api.get).not.toHaveBeenCalled();
});

test.each(["missing", "malformed", "unknown version", "inconsistent reviewed state"])("%s initial metadata fails closed until an explicit saved-status read", async kind => {
  const me = member({ reviewed: true }), accepted = clone(me.main_consent);
  if (kind === "missing") delete me.main_consent;
  else if (kind === "malformed") me.main_consent.scopes = {};
  else if (kind === "unknown version") me.main_consent.scopes.questionnaire.permission.version = "new-unknown";
  else { me.main_consent.reviewed = false; me.main_consent.welcome_complete = true; }
  const api = { get: jest.fn(async () => ({ data: accepted })), patch: jest.fn(), put: jest.fn() };
  await render(api, me); expect(element("workspace-child")).toBeNull(); expect(api.get).not.toHaveBeenCalled();
  await click([...host.querySelectorAll("button")].find(node => node.textContent === "Check saved status"));
  expect(api.get.mock.calls[0][0]).toBe("/me/main-consent"); expect(api.patch).not.toHaveBeenCalled(); expect(element("workspace-child")).not.toBeNull();
});

test("unknown writes recover through explicit GET only, with no automatic write replay", async () => {
  const me = member(), api = apiFor(me); api.patch.mockImplementation(async (_, body) => { api.replace(applyMainConsent(me.main_consent, body)); throw Error("response lost"); });
  await render(api, me); await decline(); expect(api.get).not.toHaveBeenCalled(); expect(element("workspace-child")).toBeNull();
  await click([...host.querySelectorAll("button")].find(node => node.textContent === "Check saved status"));
  expect(api.patch).toHaveBeenCalledTimes(1); expect(api.get).toHaveBeenCalledTimes(1); expect(element("workspace-child")).not.toBeNull();
});

test("account changes abort pending writes and ignore old account responses", async () => {
  const me = member(), api = apiFor(me), pending = deferred(), updates = jest.fn(); api.patch.mockReturnValue(pending.promise);
  await render(api, me, { onUpdate: updates }); await decline(); const signal = api.patch.mock.calls[0][2].signal;
  const next = member({}, { user_id: "fictional-B" }); next.main_consent = mainConsentFixture(next.user_id, { reviewed: false });
  await render(apiFor(next), next, { onUpdate: updates }); expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve({ data: applyMainConsent(me.main_consent, api.patch.mock.calls[0][1]) }));
  expect(current().user_id).toBe("fictional-B"); expect(updates).not.toHaveBeenCalled(); expect(element("workspace-child")).toBeNull();
});

test("current-pair all-off is explicitly saved and confirmed before the workspace mounts", async () => {
  const me = member({ pairId: "pair-a" }), api = apiFor(me); await render(api, me); await decline();
  expect(api.put).toHaveBeenCalledWith("/couples/compatibility/sharing", { pair_id: "pair-a", expected_member: owner,
    expected_revision: 0, notice_version: "couples-assessment-sharing-v1", personality: false, intimacy: false }, { signal: expect.any(AbortSignal) });
  expect(api.get).toHaveBeenCalledTimes(2); expect(current().main_consent.pair_sharing).toMatchObject({ reviewed: true, expected_revision: 1, personality: false, intimacy: false });
  expect(element("workspace-child")).not.toBeNull();
});

test("Agree to all includes only the present pair, never Your Type or a future pair", async () => {
  const me = member({ pairId: "pair-a" }), api = apiFor(me); await render(api, me); await checkAdult(); await click(element("main-consent-agree-all"));
  expect(api.put.mock.calls[0][1]).toMatchObject({ pair_id: "pair-a", personality: true, intimacy: true });
  expect(Object.keys(api.put.mock.calls[0][1]).sort()).toEqual(["expected_member", "expected_revision", "intimacy", "notice_version", "pair_id", "personality"].sort());
  expect(element("workspace-child")).not.toBeNull();
});

test("the main screen can grant one current-partner category while declining other purposes", async () => {
  const me = member({ pairId: "pair-a" }), api = apiFor(me); await render(api, me); await customize();
  await click(element("main-consent-pair-personality")); await checkAdult(); await click(element("main-consent-save"));
  expect(api.put.mock.calls[0][1]).toMatchObject({ personality: true, intimacy: false });
  for (const scope of consentScopes) expect(api.patch.mock.calls[0][1].choices[scope].enabled).toBe(false);
});

test("a pair or adult-epoch change discovered at preflight prevents the sharing write", async () => {
  const me = member({ pairId: "pair-a" }), api = apiFor(me); api.get.mockImplementation(async () => {
    const value = api.saved(); value.pair_sharing.state_sha256 = consentDigest("different-adult-epoch"); return { data: value };
  });
  await render(api, me); await decline(); expect(api.put).not.toHaveBeenCalled(); expect(element("workspace-child")).toBeNull();
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
});

test("an unknown pair save is never replayed; a fresh confirmed GET can finish the screen", async () => {
  const me = member({ pairId: "pair-a" }), api = apiFor(me), realPut = api.put.getMockImplementation();
  api.put.mockImplementation(async (...args) => { await realPut(...args); throw Error("lost pair response"); });
  await render(api, me); await decline(); expect(api.put).toHaveBeenCalledTimes(1); expect(api.get).toHaveBeenCalledTimes(1);
  expect(element("workspace-child")).toBeNull(); await click([...host.querySelectorAll("button")].find(node => node.textContent === "Check saved status"));
  expect(api.put).toHaveBeenCalledTimes(1); expect(api.patch).toHaveBeenCalledTimes(1); expect(element("workspace-child")).not.toBeNull();
});

test("a malformed pair receipt cannot unlock the app or trigger another write", async () => {
  const me = member({ pairId: "pair-a" }), api = apiFor(me); api.put.mockResolvedValue({ data: { pair_id: "pair-a", notice_version: "couples-assessment-sharing-v1", sharing: { revision: 0 } } });
  await render(api, me); await decline(); expect(element("workspace-child")).toBeNull(); expect(api.put).toHaveBeenCalledTimes(1);
  expect(api.get).toHaveBeenCalledTimes(1); expect(host.textContent).toContain("nothing will be resent automatically");
});

test("an unavailable current pair does not claim sharing or block unrelated app use", async () => {
  const me = member({ pairId: "pair-a" }); me.main_consent.pair_sharing = { ...me.main_consent.pair_sharing, available: false, eligible: false,
    state_sha256: null, unavailable_reason: "Current pair access is unavailable." }; const api = apiFor(me);
  await render(api, me); expect(host.textContent).toContain("Current pair access is unavailable"); await decline();
  expect(api.put).not.toHaveBeenCalled(); expect(element("workspace-child")).not.toBeNull();
});

test("the same account must review a new pair or renewed adult epoch before old completed content mounts", async () => {
  const prior = member({ reviewed: true, pairId: "pair-a", sharingReviewed: true }), api = apiFor(prior), setMe = jest.fn();
  await act(async () => root.render(<ResearchFirstUse api={api} me={prior} setMe={setMe}><Workspace /></ResearchFirstUse>));
  expect(element("workspace-child")).not.toBeNull();
  const next = clone(prior); next.main_consent.pair_sharing.reviewed = false; next.main_consent.pair_sharing.expected_revision = 3;
  next.main_consent.pair_sharing.state_sha256 = consentDigest("renewed-adult-epoch");
  await act(async () => root.render(<ResearchFirstUse api={api} me={next} setMe={setMe}><Workspace /></ResearchFirstUse>));
  expect(element("workspace-child")).toBeNull(); expect(element("research-welcome")).not.toBeNull(); expect(api.patch).not.toHaveBeenCalled();
});

test.each(["/app", "/app/start", "/app/personal", "/app/your-type", "/app/settings/upgrade", "/app/couples"])("first sign-in at %s shows the main notice before redirects or tab content", async entry => {
  const me = member({}, { personal_access: false, onboarded: false, people_count: 0 });
  mockApi = { ...apiFor(me), get: jest.fn(async () => ({ data: me })), interceptors: { response: { use: jest.fn(), eject: jest.fn() } } };
  await act(async () => root.render(<MemoryRouter initialEntries={[entry]}><Routes><Route path="/app" element={<AppShell />}>
    <Route index element={<p>Workspace child</p>} /><Route path="*" element={<p>Workspace child</p>} /></Route></Routes></MemoryRouter>));
  expect(element("research-welcome")).not.toBeNull(); expect(host.textContent).not.toContain("Workspace child"); expect(mockApi.patch).not.toHaveBeenCalled();
});

test("a /couples access update immediately blocks the old workspace while fresh current-pair consent loads", async () => {
  const me = member({ reviewed: true }, { workspace_access: { version: "workspace-access-v1", mode: "individual", pair_id: null },
    usage: { plan: "plus", messages_remaining: 90, period_verified: true } });
  const pending = deferred(); mockApi = { ...apiFor(me), get: jest.fn(path => path === "/me" ? Promise.resolve({ data: me }) : pending.promise),
    interceptors: { response: { use: jest.fn(), eject: jest.fn() } } };
  await act(async () => root.render(<MemoryRouter initialEntries={["/app/personal"]}><Routes><Route path="/app" element={<AppShell />}>
    <Route path="*" element={<p data-testid="workspace-child">Workspace child</p>} /></Route></Routes></MemoryRouter>));
  expect(element("workspace-child")).not.toBeNull(); const interceptor = mockApi.interceptors.response.use.mock.calls[0][0];
  await act(async () => interceptor({ data: { workspace_access: { version: "workspace-access-v1", mode: "couples", couples_only: true, pair_id: "new-pair" } }, config: { url: "/couples" } }));
  expect(element("workspace-child")).toBeNull(); expect(mockApi.get.mock.calls.map(([path]) => path)).toContain("/me/main-consent");
  await act(async () => pending.resolve({ data: mainConsentFixture(owner, { reviewed: true, pairId: "new-pair", sharingReviewed: false }) }));
  expect(element("workspace-child")).toBeNull(); expect(element("research-welcome")).not.toBeNull(); expect(mockApi.put).not.toHaveBeenCalled();
});

test.each([["staff", { role: "admin" }, false], ["closing", { closing: true }, false], ["Settings", {}, true]])("%s access does not create a new data grant", async (_, changes, bypass) => {
  const me = member({}, changes), api = apiFor(me); await render(api, me, { bypass });
  expect(element("workspace-child")).not.toBeNull(); expect(api.patch).not.toHaveBeenCalled(); expect(api.get).not.toHaveBeenCalled();
});

test("Settings remains available before completing the main consent screen", async () => {
  const me = member({}, { personal_access: false });
  mockApi = { ...apiFor(me), get: jest.fn(async () => ({ data: me })), interceptors: { response: { use: jest.fn(), eject: jest.fn() } } };
  await act(async () => root.render(<MemoryRouter initialEntries={["/app/settings#data-permissions"]}><Routes><Route path="/app" element={<AppShell />}>
    <Route path="settings" element={<p>Data controls available</p>} /></Route></Routes></MemoryRouter>));
  expect(element("research-welcome")).toBeNull(); expect(host.textContent).toContain("Data controls available"); expect(mockApi.patch).not.toHaveBeenCalled();
});
