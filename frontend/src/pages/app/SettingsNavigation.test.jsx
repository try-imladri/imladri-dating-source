import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { useEffect, useState } from "react";
import Settings from "./Settings";
import { SETTINGS_APPEARANCE, settingsDestination } from "./settings/SettingsNavigation";
import { clearPracticeTurn } from "@/lib/practiceTurn";
import { clearStartDraft } from "@/lib/startDraft";

jest.mock("react-router-dom", () => { global.TextEncoder = require("util").TextEncoder; return jest.requireActual("react-router"); });
jest.mock("@clerk/react", () => {
  const React = require("react");
  const view = globalThis.window;
  function UserProfile({ children }) {
    const [hash, setHash] = React.useState(view.location.hash);
    React.useEffect(() => {
      const changed = () => setHash(view.location.hash);
      view.addEventListener("hashchange", changed);
      return () => view.removeEventListener("hashchange", changed);
    }, []);
    const pages = React.Children.toArray(children);
    const active = hash.replace(/^#\/?/, "").split(/[/?]/)[0] || "account";
    const selected = pages.find(page => (page.props.url || page.props.label) === active);
    return <div data-testid="clerk-settings-fixture"><nav aria-label="Account settings">
      {pages.map(page => <button key={page.props.url || page.props.label} type="button"
        aria-current={(page.props.url || page.props.label) === active ? "page" : undefined}
        onClick={() => { const id = page.props.url || page.props.label; view.location.hash = id === "account" ? "/" : "/" + id; view.dispatchEvent(new globalThis.HashChangeEvent("hashchange")); }}>
        {page.props.label === "account" ? "Profile" : page.props.label === "security" ? "Security" : page.props.label}
      </button>)}
    </nav><div>{selected?.props.children || <p>{active === "security" ? "Clerk sign-in security" : "Clerk profile"}</p>}</div></div>;
  }
  UserProfile.Page = () => null;
  return { UserProfile };
});
jest.mock("@/lib/theme", () => ({ useTheme: () => ({ theme: "dark", toggle: jest.fn() }) }));
jest.mock("sonner", () => ({ toast: { error: jest.fn(), success: jest.fn() } }));
jest.mock("@/lib/practiceTurn", () => ({ clearPracticeTurn: jest.fn() }));
jest.mock("@/lib/startDraft", () => ({ clearStartDraft: jest.fn() }));
jest.mock("@/components/app/ContentImpact", () => () => <p>Content impact notice</p>);
jest.mock("@/components/app/AccountClosure", () => props => <button data-testid="settings-close-account"
  data-owner={props.memberId} disabled={props.closing}>Close Personal</button>);
jest.mock("@/components/app/AnswerHistoryPreference", () => props => <p data-testid="history-preference">History for {props.me.user_id}</p>);
jest.mock("@/components/app/WordingFeedback", () => props => <p data-testid="wording-preference">Wording for {props.owner}</p>);
jest.mock("@/components/app/YourTypeData", () => props => <p id="your-type-data" data-testid="your-type-preference">Your Type for {props.owner}</p>);
jest.mock("@/components/app/IntimacyResearch", () => ({ IntimacyResearch: () => <p data-testid="one-time-submission">One-time submission controls</p> }));
jest.mock("@/components/app/CouplesDataPreferences", () => props => <p data-testid="couples-data-preference">Couples data for {props.me.user_id}</p>);
jest.mock("./CouplesCompatibility", () => ({ CouplesAssessmentSharing: props => <p id="partner-assessment-sharing" data-testid="partner-sharing-preference">Sharing for {props.me.user_id}</p> }));

const el = id => document.querySelector(`[data-testid="${id}"]`);
const owner = id => ({ user_id: id, role: "member", usage: { plan: "none", messages_remaining: 0 },
  questionnaire_research: { enabled: false, version: "research-v1", revision: 4 },
  tone_research: { enabled: false, version: "tone-v1", revision: 2 },
  research_exports: { member: { enabled: false, version: "rows-v1", revision: 3 },
    couple: { enabled: false, version: "rows-v1", revision: 5, pair_id: "pair-a" } } });
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
let host, root, api, initial, mockSetMe, anchorClick;

function Frame({ member }) {
  const [me, setMe] = useState(member);
  useEffect(() => setMe(member), [member]);
  mockSetMe = setMe;
  return <Outlet context={{ me, setMe, api }} />;
}
function tree(member, entry) {
  return <MemoryRouter initialEntries={[entry]}><Routes><Route element={<Frame member={member} />}>
    <Route path="/app/settings" element={<Settings />} />
  </Route></Routes></MemoryRouter>;
}
async function show(entry = "/app/settings", member = initial) {
  window.history.replaceState({}, "", entry);
  await act(async () => root.render(tree(member, entry)));
}
async function page(label) {
  const nav = document.querySelector('[aria-label="Account settings"]');
  const button = [...nav.querySelectorAll("button")].find(item => item.textContent === label);
  await act(async () => button.click());
}
async function purpose(key) { await act(async () => el(`settings-purpose-${key}`).click()); }

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  initial = owner("member-a"); api = { get: jest.fn(), delete: jest.fn(), patch: jest.fn() };
  api.patch.mockImplementation(async (_path, body) => ({ data: { ...body, revision: body.revision + 1 } }));
  host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
  window.history.replaceState({}, "", "/app/settings");
  URL.createObjectURL = jest.fn(() => "blob:fixture"); URL.revokeObjectURL = jest.fn();
  anchorClick = jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  jest.clearAllMocks();
});
afterEach(() => {
  act(() => root.unmount()); host.remove(); anchorClick.mockRestore();
  window.history.replaceState({}, "", "/");
});

test("supported Clerk pages follow Profile and Security and render one pane", async () => {
  await show();
  expect([...host.querySelectorAll('[aria-label="Account settings"] button')].map(button => button.textContent))
    .toEqual(["Profile", "Security", "Plan", "Data & privacy", "Your data", "Legal & help"]);
  expect(host.textContent).toContain("Clerk profile");
  expect(el("settings-appearance")).toBeNull(); expect(el("ai-disclosure")).toBeNull();
  await page("Security"); expect(host.textContent).toContain("Clerk sign-in security");
  expect(el("settings-password-help").textContent).toContain("eye icon");
  expect(el("settings-appearance")).toBeNull();
});

test("the embedded profile has natural height and no second viewport scrollbar", async () => {
  await show();
  expect(el("settings-page").className).not.toMatch(/grid-cols|overflow-auto|h-\[/);
  for (const key of ["scrollBox", "pageScrollBox"]) {
    expect(SETTINGS_APPEARANCE.elements[key]).toEqual({ height: "auto", maxHeight: "none", overflow: "visible" });
  }
  expect(SETTINGS_APPEARANCE.elements.profileSection__danger.display).toBe("none");
});

test.each([
  ["questionnaire-research", "questionnaires", "questionnaire-research-consent"],
  ["data-permissions", "overview", "settings-privacy-overview"],
  ["partner-assessment-sharing", "partner", "partner-sharing-preference"],
  ["your-type-data", "your-type", "your-type-preference"],
])("legacy #%s selects privacy and the exact purpose", async (hash, target, marker) => {
  await show(`/app/settings?keep=yes#${hash}`);
  expect(el(marker)).not.toBeNull();
  expect(window.location.hash).toBe("#/privacy");
  expect(new URLSearchParams(window.location.search).get("settingsPurpose")).toBe(target);
  expect(new URLSearchParams(window.location.search).get("keep")).toBe("yes");
  expect(api.patch).not.toHaveBeenCalled(); expect(api.delete).not.toHaveBeenCalled();
});

test("existing data anchor opens unpaid export, clear and closure without upsell", async () => {
  await show("/app/settings#settings-export");
  expect(el("settings-export").disabled).toBe(false);
  expect(el("settings-delete-all")).not.toBeNull();
  expect(el("settings-close-account").getAttribute("data-owner")).toBe("member-a");
  expect(el("settings-plan")).toBeNull();
});

test("unpaid plan and legal links remain accessible in their own panes", async () => {
  await show(); await page("Plan");
  expect(el("settings-plan").textContent).toContain("No active subscription");
  expect(el("settings-plan-link").getAttribute("href")).toBe("/app/settings/upgrade");
  await page("Legal & help");
  expect(["how", "privacy", "terms", "support"].map(key => el(`settings-link-${key}`).getAttribute("href")))
    .toEqual(["/how-it-works", "/privacy", "/terms", "/support"]);
});

test("all private purposes stay distinct and visiting makes no consent writes", async () => {
  await show("/app/settings#/privacy");
  for (const target of ["questionnaires", "exports", "history", "tone", "your-type", "trends", "partner", "overview"]) {
    await purpose(target);
    expect(el(`settings-privacy-${target}`)).not.toBeNull();
    expect(!!el("settings-about-note")).toBe(target === "overview");
  }
  expect(api.patch).not.toHaveBeenCalled(); expect(api.delete).not.toHaveBeenCalled();
});

test("Questionnaire explains unconditional saving without changing research or partner grants", async () => {
  await show("/app/settings#/privacy"); await purpose("history");
  expect(el("settings-purpose-history").textContent).toBe("Questionnaire");
  expect(el("settings-purpose-questionnaires").textContent).toBe("Questionnaire research");
  expect(el("questionnaire-saving-status").textContent).toBe("Answer saving is always on");
  expect(el("history-preference")).not.toBeNull();
  expect(el("wording-preference")).toBeNull();
  expect(el("settings-purpose-submission")).toBeNull();
  expect(api.patch).not.toHaveBeenCalled();
});

test("removed Appearance and submission links resolve to an available section", async () => {
  expect(settingsDestination("#/appearance")).toMatchObject({ pane: "account", hash: "#/", legacy: true });
  await show("/app/settings?settingsPurpose=submission#/privacy");
  expect(el("questionnaire-saving-status")).not.toBeNull();
  expect(new URLSearchParams(window.location.search).get("settingsPurpose")).toBe("history");
  expect(el("one-time-submission")).toBeNull();
  await page("Your data");
  expect(el("settings-earlier-contribution")).not.toBeNull();
  expect(api.patch).not.toHaveBeenCalled(); expect(api.delete).not.toHaveBeenCalled();
});

test.each(["admin", "staff"])("%s sees an explanation in member-only privacy panes without consent controls", async role => {
  await show("/app/settings#/privacy", { ...initial, role });
  for (const target of ["questionnaires", "exports", "tone"]) {
    await purpose(target);
    const pane = el(`settings-privacy-${target}`);
    expect(el(`settings-member-only-${target}`)).not.toBeNull();
    expect(pane.textContent).toContain("available only to member accounts");
    expect(pane.querySelector('input[type="checkbox"]')).toBeNull();
  }
  expect(api.patch).not.toHaveBeenCalled(); expect(api.delete).not.toHaveBeenCalled();
});

test.each([
  ["questionnaires", 1, "Questionnaire research"],
  ["exports", 2, "Individual research CSV"],
  ["tone", 1, "Conversation tone research"],
])("member %s pane retains its original unchecked consent controls", async (target, choices, notice) => {
  await show("/app/settings#/privacy"); await purpose(target);
  const pane = el(`settings-privacy-${target}`);
  expect(el(`settings-member-only-${target}`)).toBeNull();
  expect(pane.textContent).toContain(notice);
  const controls = [...pane.querySelectorAll('input[type="checkbox"]')];
  expect(controls).toHaveLength(choices);
  expect(controls.every(control => !control.checked && !control.disabled)).toBe(true);
  expect(api.patch).not.toHaveBeenCalled(); expect(api.delete).not.toHaveBeenCalled();
});

test("the actual questionnaire checkbox preserves notice, CAS and explicit adult confirmation", async () => {
  await show("/app/settings#questionnaire-research");
  const checkbox = el("questionnaire-research-consent");
  expect(checkbox.checked).toBe(false); expect(host.textContent).toContain("Written answers, notes and conversations stay outside the report");
  await act(async () => checkbox.click());
  expect(api.patch).toHaveBeenCalledWith("/me/questionnaire-research", {
    enabled: true, revision: 4, version: "research-v1", adult_confirmed: true, expected_member: "member-a",
  });
  expect(el("questionnaire-research-consent").checked).toBe(true);
  await act(async () => el("questionnaire-research-consent").click());
  expect(api.patch.mock.calls[1][1]).toMatchObject({ enabled: false, adult_confirmed: false, expected_member: "member-a", revision: 5 });
});

test("research export controls preserve exact pair binding without implicit opt-in", async () => {
  await show("/app/settings#/privacy"); await purpose("exports");
  const choices = host.querySelectorAll('input[type="checkbox"]');
  expect([...choices].every(item => !item.checked)).toBe(true);
  await act(async () => choices[1].click());
  expect(api.patch).toHaveBeenCalledWith("/me/research-exports/couple", {
    enabled: true, version: "rows-v1", revision: 5, adult_confirmed: true,
    expected_member: "member-a", expected_pair_id: "pair-a",
  }, expect.objectContaining({ signal: expect.anything() }));
});

test("exports stay owner-bound, deduplicated and aborted on identity change", async () => {
  const pending = deferred(); api.get.mockReturnValue(pending.promise);
  await show("/app/settings#/data");
  await act(async () => { el("settings-export").click(); el("settings-export").click(); });
  expect(api.get).toHaveBeenCalledTimes(1);
  const [, options] = api.get.mock.calls[0]; expect(options.headers).toEqual({ "X-Settings-Member": "member-a" });
  await act(async () => root.render(tree(owner("member-b"), "/app/settings#/data")));
  expect(options.signal.aborted).toBe(true);
  await act(async () => pending.resolve({ data: new Blob(["old-private-data"]) }));
  expect(anchorClick).not.toHaveBeenCalled();
  expect(el("settings-close-account").getAttribute("data-owner")).toBe("member-b");
});

test("unmount aborts a pending export without creating a download", async () => {
  const pending = deferred(); api.get.mockReturnValue(pending.promise);
  await show("/app/settings#/data"); await act(async () => el("settings-export").click());
  const signal = api.get.mock.calls[0][1].signal;
  await act(async () => root.render(<p>Other route</p>));
  expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve({ data: new Blob(["private"]) }));
  expect(anchorClick).not.toHaveBeenCalled();
});

test("confirmed clear remains owner-bound and a switched identity prevents late effects", async () => {
  const pending = deferred(); api.delete.mockReturnValue(pending.promise);
  await show("/app/settings#/data");
  await act(async () => el("settings-delete-all").click());
  expect(document.querySelector('[role="alertdialog"]').textContent).toContain("Your login, reply allowance and any paid subscription stay active");
  expect(api.delete).not.toHaveBeenCalled();
  await act(async () => el("settings-delete-confirm").click());
  expect(api.delete).toHaveBeenCalledWith("/me", expect.objectContaining({ headers: { "X-Settings-Member": "member-a" } }));
  const signal = api.delete.mock.calls[0][1].signal;
  await act(async () => root.render(tree(owner("member-b"), "/app/settings#/data")));
  expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve({ data: { ok: true } }));
  expect(clearStartDraft).not.toHaveBeenCalled(); expect(clearPracticeTurn).not.toHaveBeenCalled();
});

test("a new identity gets its own unchanged unchecked permissions", async () => {
  await show("/app/settings#questionnaire-research");
  await act(async () => mockSetMe(previous => ({ ...previous, questionnaire_research: { ...previous.questionnaire_research, enabled: true } })));
  expect(el("questionnaire-research-consent").checked).toBe(true);
  await act(async () => root.render(tree(owner("member-b"), "/app/settings#questionnaire-research")));
  expect(el("questionnaire-research-consent").checked).toBe(false);
  expect(api.patch).not.toHaveBeenCalled();
});

test("Clerk nested security routes are not rewritten as app-purpose anchors", () => {
  expect(settingsDestination("#/security/mfa", "?keep=1")).toMatchObject({ hash: "#/security/mfa", pane: "security", legacy: false });
  expect(settingsDestination("#/privacy", "?settingsPurpose=not-a-purpose").purpose).toBe("overview");
});

test("legacy #profile selects the native root when Account is first", async () => {
  expect(settingsDestination("#profile", "?keep=1")).toMatchObject({ pane: "account", hash: "#/", search: "?keep=1", legacy: true });
  await show("/app/settings?keep=1#profile");
  expect(window.location.hash).toBe("#/");
  expect(host.textContent).toContain("Clerk profile");
  await page("Security"); await page("Profile");
  expect(window.location.hash).toBe("#/");
});
