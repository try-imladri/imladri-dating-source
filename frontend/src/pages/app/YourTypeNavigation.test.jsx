import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import AppShell from "@/components/app/AppShell";
import { PersonalRoute } from "./Personal";
import YourTypePage from "./YourTypePage";
import PersonalityPage from "./PersonalityPage";
import { mainConsentFixture, consentSubtle } from "@/testFixtures/mainConsent";

jest.mock("react-router-dom", () => {
  global.TextEncoder = require("util").TextEncoder;
  return jest.requireActual("react-router");
});
jest.mock("@clerk/react", () => ({
  Show: ({ when, children }) => when === "signed-in" ? children : null,
  useClerk: () => ({}),
  useAuth: () => ({ isLoaded: true, userId: mockMember.user_id, sessionId: "session-" + mockMember.user_id }),
}));
jest.mock("@/components/app/NotificationBell", () => ({ NotificationBell: () => null }));
jest.mock("@/lib/theme", () => ({ ThemeToggle: () => null }));
jest.mock("@/lib/browserSession", () => ({ endBrowserSession: jest.fn(), recordMemberVisit: async () => {} }));
jest.mock("@/lib/api", () => ({
  useApi: () => mockApi,
  applySignupOrigin: async (_, member) => member,
  errMsg: error => error.response?.data?.detail || error.message || "Request failed",
}));

let mockApi, mockMember, mockAge, mockGender, mockSexuality, host, root;
const query = id => host.querySelector(`[data-testid="${id}"]`);
const click = node => act(async () => node.click());
function Location() {
  const { pathname, search } = useLocation();
  const nav = useNavigate();
  return <><output data-testid="location">{pathname}{search}</output><button onClick={() => nav(-1)}>Back</button></>;
}

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  sessionStorage.clear();
  mockAge = 30;
  mockGender = "Man";
  mockSexuality = null;
  mockMember = { user_id: "fictional-owner", role: "member", main_consent: mainConsentFixture("fictional-owner"), onboarded: true, personal_access: true,
    workspace_access:{version:"workspace-access-v1",mode:"individual",pair_id:null} };
  const quiz = { short: [{id: "s1", q: "Personal source question", options: [{label: "First answer"}, {label: "Second answer"}]}], extended: [], further: [] };
  const profile = () => ({ about: { answers: {}, describe: "", demographics: { age: mockAge, gender: mockGender } }, summary: {}, radar: [] });
  mockApi = {
    get: jest.fn(async path => {
      if (path === "/me") return { data: mockMember };
      if (path === "/me/assessments") return { data: { owner_id: mockMember.user_id, quiz, ...profile() } };
      if (path === "/me/about") return { data: profile() };
      if (path === "/quiz") return { data: quiz };
      if (path === "/your-type") return { data: { audiences: [{ id: "women" }, { id: "men" }, {id: "both"}],
        profile: { adult_eligible: mockAge != null && mockAge >= 18, gender: mockGender, sexuality: mockSexuality }, sessions: [], history_next: null } };
      if (path === "/your-type/research") return { data: { enabled: false, version: "your-type-research-v1", revision: 0 } };
      if (path === "/your-type/history") return { data: { sessions: [], next: null } };
      throw new Error("Unexpected fixture GET " + path);
    }),
    post: jest.fn(async () => { throw new Error("Synthetic save stopped after inspecting its owner"); }),
    put: jest.fn(async (_, body) => ({data: {about: {answers: body.answers, describe: "", demographics: {age: mockAge, gender: mockGender}}, summary: {}, radar: []}})),
    interceptors: { response: { use: jest.fn(), eject: jest.fn() } },
  };
  let next = 0;
  Object.defineProperty(window, "crypto", { configurable: true,
    value: { subtle: consentSubtle, randomUUID: () => `00000000-0000-4000-8000-${String(++next).padStart(12, "0")}` } });
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount()); host.remove(); sessionStorage.clear();
});

async function open(path, initialEntries = [path]) {
  await act(async () => root.render(<MemoryRouter initialEntries={initialEntries}>
    <Location />
    <Routes>
      <Route path="/app" element={<AppShell />}>
        <Route index element={<h1>Individual overview</h1>} />
        <Route path="start" element={<h1>Individual start</h1>} />
        <Route path="personal" element={<PersonalRoute />} />
        <Route path="personality" element={<PersonalityPage />} />
        <Route path="your-type" element={<YourTypePage />} />
        <Route path="couples" element={<h1>Couples overview</h1>} />
        <Route path="settings/upgrade" element={<h1>Choose your plan</h1>} />
      </Route>
      <Route path="/admin" element={<AppShell admin />}><Route index element={<h1>Staff dashboard</h1>} /></Route>
    </Routes>
  </MemoryRouter>));
}

test("Your Type is a top-level Individual peer with its own canonical active link", async () => {
  await open("/app/your-type");
  expect(query("location").textContent).toBe("/app/your-type");
  const personal = query("app-nav-personal"), yourType = query("app-nav-your-type");
  expect(yourType.getAttribute("href")).toBe("/app/your-type");
  expect(personal.parentNode).toBe(yourType.parentNode);
  expect(query("app-nav-personality")).toBeNull();
  expect(personal.nextElementSibling).toBe(query("app-nav-intimacy"));
  expect(query("app-nav-intimacy").nextElementSibling).toBe(yourType);
  expect(yourType.getAttribute("aria-current")).toBe("page");
  expect(personal.hasAttribute("aria-current")).toBe(false);
  expect(yourType.querySelector("svg").classList.contains("lucide-heart")).toBe(true);
  expect(query("your-type-page")).not.toBeNull();
  expect(query("personal-page")).toBeNull();
  expect(query("personal-tabs")).toBeNull();
  expect(host.textContent).toContain("Discover Your Type");
  expect(mockApi.get.mock.calls.some(([path]) => path === "/quiz")).toBe(false);
});

test("a complimentary member uses real Individual pages without a paid plan ID or staff access", async () => {
  mockMember = { ...mockMember, tier: "unsubscribed", membership_plan_id: null,
    usage: { plan: "complimentary", plan_id: "complimentary_unlimited", unlimited: true, messages_used: 4 } };
  await open("/app/your-type");
  expect(query("location").textContent).toBe("/app/your-type");
  expect(query("your-type-page")).not.toBeNull();
  expect(query("complimentary-access").textContent).toBe("Unlimited");
  expect(query("replies-left")).toBeNull();
  expect(host.textContent).not.toContain("Admin dashboard");
  await click(query("app-nav-personal"));
  expect(query("personal-plan").textContent).toBe("Complimentary Unlimited");
  expect(mockApi.get).toHaveBeenCalledWith("/me/assessments", expect.objectContaining({headers: {"X-Questionnaire-Owner": "fictional-owner"}}));
  expect(mockApi.get.mock.calls.some(([path]) => path === "/quiz" || path === "/me/about")).toBe(false);
  expect(mockApi.post).not.toHaveBeenCalled();
  expect(mockMember.role).toBe("member");
  expect(mockMember.membership_plan_id).toBeNull();
});

test("a usage label cannot override an explicit false Personal access decision", async () => {
  mockMember = { ...mockMember, personal_access: false,
    usage: { plan: "complimentary", plan_id: "complimentary_unlimited", unlimited: true } };
  await open("/app/your-type");
  expect(query("location").textContent).toBe("/app/settings/upgrade");
  expect(host.textContent).toContain("Choose your plan");
  expect(mockApi.get.mock.calls.filter(([path]) => path === "/your-type").every(([,options])=>options.signal.aborted)).toBe(true);
});

test("an introductory member sees one shared free counter and keeps the ordinary Individual navigation", async () => {
  mockMember = { ...mockMember, membership_plan_id: null, usage: {
    plan: "intro", plan_id: "intro_10", introductory: true, unlimited: false,
    message_limit: 10, messages_remaining: 7, messages_used: 3, period_verified: false,
  } };
  await open("/app/your-type");
  expect(query("intro-messages-left").textContent).toBe("Free messages: 7 of 10 remaining");
  expect(query("replies-left")).toBeNull();
  expect(query("complimentary-access")).toBeNull();
  expect(query("your-type-page")).not.toBeNull();
  await click(query("workspace-couples"));
  expect(query("intro-messages-left").textContent).toBe("Free messages: 7 of 10 remaining");
  expect(query("location").textContent).toBe("/app/couples");
});

test("an exhausted introduction returns to plans and retains its zero counter without a fake paid period", async () => {
  mockMember = { ...mockMember, personal_access: false, usage: {
    plan: "intro", plan_id: "intro_10", introductory: true, unlimited: false,
    message_limit: 10, messages_remaining: 0, messages_used: 10, period_verified: false,
  } };
  await open("/app/your-type");
  expect(query("location").textContent).toBe("/app/settings/upgrade");
  expect(query("intro-messages-left").textContent).toContain("0 of 10 remaining");
  expect(query("your-type-page")).toBeNull();
  expect(query("replies-left")).toBeNull();
});

test("Personal navigation opens the separate Your Type page without a duplicate nested tab", async () => {
  await open("/app/personal");
  expect(query("personal-tabs").querySelectorAll("button")).toHaveLength(4);
  expect(query("personal-tab-your-type")).toBeNull();
  await click(query("app-nav-your-type"));
  expect(query("location").textContent).toBe("/app/your-type");
  expect(query("personal-page")).toBeNull();
  expect(query("app-nav-your-type").getAttribute("aria-current")).toBe("page");
  expect(query("app-nav-personal").hasAttribute("aria-current")).toBe(false);
});

test("legacy Personal Your Type links replace history before loading Personal", async () => {
  await open("/app/personal?tab=your-type", ["/app", "/app/personal?tab=your-type"]);
  expect(query("location").textContent).toBe("/app/your-type");
  expect(mockApi.get.mock.calls.filter(([path]) => path === "/quiz")).toHaveLength(0);
  expect(mockApi.get.mock.calls.filter(([path]) => path === "/me/about")).toHaveLength(0);
  expect(mockApi.get.mock.calls.filter(([path]) => path === "/your-type")).toHaveLength(1);
  expect(query("personal-page")).toBeNull();
  await click([...host.querySelectorAll("button")].find(node => node.textContent === "Back"));
  expect(query("location").textContent).toBe("/app");
  expect(host.textContent).toContain("Individual overview");
});

test("other Personal tab links keep their own route and selection", async () => {
  await open("/app/personal?tab=deeper");
  expect(query("location").textContent).toBe("/app/personal?tab=deeper");
  expect(query("personal-tab-deeper").getAttribute("aria-current")).toBe("page");
  expect(query("app-nav-personal").getAttribute("aria-current")).toBe("page");
  expect(query("app-nav-your-type").hasAttribute("aria-current")).toBe(false);
  expect(mockApi.get.mock.calls.some(([path]) => path.startsWith("/your-type"))).toBe(false);
});

test.each(["/app/personality", "/app/personal?tab=personality"])("old personality entry %s opens Personal results without another sidebar tab", async path => {
  await open(path);
  expect(query("location").textContent).toBe("/app/personal?tab=results");
  expect(query("app-nav-personality")).toBeNull();
  expect(query("app-nav-personal").getAttribute("aria-current")).toBe("page");
  expect(query("personality-incomplete")).not.toBeNull();
  expect(mockApi.get.mock.calls.filter(([name]) => name === "/me/assessments")).toHaveLength(1);
  expect(mockApi.get.mock.calls.some(([name]) => name === "/your-type")).toBe(false);
});

test("Your Type uses the existing Individual entitlement gate", async () => {
  mockMember = { ...mockMember, personal_access: false };
  await open("/app/your-type");
  expect(query("location").textContent).toBe("/app/settings/upgrade");
  expect(query("app-nav-your-type")).toBeNull();
  expect(query("your-type-page")).toBeNull();
  expect(mockApi.get.mock.calls.some(([path]) => path === "/me/about")).toBe(false);
  expect(mockApi.get.mock.calls.filter(([path]) => path === "/your-type").every(([,options])=>options.signal.aborted)).toBe(true);
  expect(mockApi.get.mock.calls.some(([path]) => path.startsWith("/your-type/"))).toBe(false);
});

test("paid staff retain the same Individual access previously available under Personal", async () => {
  mockMember = { ...mockMember, role: "admin", membership_plan_id: "plus_500" };
  await open("/app/your-type");
  expect(query("location").textContent).toBe("/app/your-type");
  expect(host.textContent).toContain("Discover Your Type");
});

test("a saved minor sees the server adult gate without loading portraits or sessions", async () => {
  mockAge = 17;
  mockMember = { ...mockMember, about: { demographics: { age: 17 } } };
  await open("/app/your-type");
  expect(host.textContent).toContain("Your Type is for adults");
  expect(host.textContent).not.toContain("Discover Your Type");
  expect(query("your-type-page").querySelector("img")).toBeNull();
  expect(mockApi.get.mock.calls.filter(([path]) => path.startsWith("/your-type")).map(([path]) => path)).toEqual(["/your-type"]);
  expect(mockApi.post).not.toHaveBeenCalled();
});

test("a cached minor shell cannot override a newly saved eligible profile", async () => {
  mockMember = { ...mockMember, about: { demographics: { age: 17 } } };
  mockAge = 18;
  await open("/app/your-type");
  expect(host.textContent).toContain("Discover Your Type");
  expect(mockApi.get.mock.calls.filter(([path]) => path.startsWith("/your-type")).map(([path]) => path)).toEqual(["/your-type"]);
  expect(host.querySelector('input[type="checkbox"]')).toBeNull();
});

test("missing age still uses the server eligibility refusal and existing profile link", async () => {
  mockAge = null;
  const get = mockApi.get.getMockImplementation();
  mockApi.get.mockImplementation(path => path === "/your-type" ?
    Promise.reject({ response: { status: 403, data: { detail: "Save an adult profile age" } } }) : get(path));
  await open("/app/your-type");
  expect(host.textContent).toContain("Save an adult profile age");
  expect(host.querySelector("a[href='/app/personal?tab=basics']").textContent).toBe("Review your profile age");
  expect(mockApi.post).not.toHaveBeenCalled();
});

test("standalone Start keeps the current owner expected_member contract", async () => {
  mockSexuality = "straight";
  await open("/app/your-type");
  expect([...host.querySelectorAll("button")].find(node => node.textContent === "Straight")).toBeUndefined();
  expect(query("your-type-page").querySelector("input[type='checkbox']")).toBeNull();
  await click([...host.querySelectorAll("button")].find(node => node.textContent.startsWith("Start Your Type")));
  expect(mockApi.post).toHaveBeenCalledWith("/your-type/sessions", expect.objectContaining({
    expected_member: "fictional-owner", audience: "women", sexuality: "straight", viewer_gender: "Man",
  }));
  expect(mockApi.post.mock.calls[0][1]).not.toHaveProperty("adult_confirmed");
});

test("an admin without any paid plan deliberately enters real Personal and Your Type without role or subscription edits", async () => {
  mockMember = {...mockMember, role: "admin", membership_plan_id: null, tier: "unsubscribed", usage: {plan: "admin", unlimited: true}};
  await open("/app");
  expect(query("location").textContent).toBe("/admin");
  await click(query("admin-view-individual"));
  expect(query("location").textContent).toBe("/app/personal");
  expect(host.textContent).toContain("Personal source question");
  await click(query("quiz-opt-s1-0")); await click(query("personal-save"));
  expect(mockApi.put).toHaveBeenCalledWith("/me/about", expect.objectContaining({expected_member: "fictional-owner", answers: {s1: expect.objectContaining({choice: 0})}}));
  await click(query("app-nav-your-type"));
  expect(query("location").textContent).toBe("/app/your-type");
  expect(host.textContent).toContain("Discover Your Type");
  await click(query("app-nav-personal"));
  expect(query("location").textContent).toBe("/app/personal");
  await click(query("individual-view-admin"));
  expect(query("location").textContent).toBe("/admin");
  expect(mockMember.role).toBe("admin"); expect(mockMember.membership_plan_id).toBeNull();
  expect(mockApi.put.mock.calls.every(([path]) => path === "/me/about")).toBe(true);
  expect(mockApi.post).not.toHaveBeenCalled();
});

test.each(["/app", "/app/start"])("the unbilled admin default entry %s remains Admin", async path => {
  mockMember = {...mockMember, role: "admin", membership_plan_id: null};
  await open(path);
  expect(query("location").textContent).toBe("/admin");
  expect(query("admin-view-individual")).not.toBeNull();
});

test("explicit admin Overview remains Individual even before onboarding", async () => {
  mockMember = {...mockMember, role: "admin", membership_plan_id: null, onboarded: false, people_count: 0};
  await open("/app/personal");
  await click(query("app-nav-overview"));
  expect(query("location").textContent).toBe("/app?workspace=individual");
  expect(host.textContent).toContain("Individual overview");
  await click(query("workspace-couples")); await click(query("workspace-individual"));
  expect(query("location").textContent).toBe("/app?workspace=individual");
});

test("a forged Individual marker never grants an unpaid normal member access", async () => {
  mockMember = {...mockMember, personal_access: false, role: "member", membership_plan_id: null};
  await open("/app?workspace=individual&role=admin");
  expect(query("location").textContent).toBe("/app/settings/upgrade");
  expect(query("admin-view-individual")).toBeNull(); expect(query("app-nav-your-type")).toBeNull();
});

test("direct unbilled admin links still enforce saved adult age", async () => {
  mockMember = {...mockMember, role: "admin", membership_plan_id: null, about: { demographics: { age: 17 } }}; mockAge = 17;
  await open("/app/your-type");
  expect(query("location").textContent).toBe("/app/your-type");
  expect(host.textContent).toContain("Your Type is for adults");
  const reads=mockApi.get.mock.calls.filter(([path]) => path.startsWith("/your-type"));
  expect(reads.map(([path])=>path)).toEqual(["/your-type","/your-type"]);
  expect(reads[0][1].signal.aborted).toBe(true);
  expect(mockApi.post).not.toHaveBeenCalled();
});

test("switching to Couples and back remembers the canonical top-level page", async () => {
  await open("/app/your-type");
  await click(query("workspace-couples"));
  expect(query("location").textContent).toBe("/app/couples");
  expect(query("workspace-individual").getAttribute("href")).toBe("/app/your-type");
  await click(query("workspace-individual"));
  expect(query("location").textContent).toBe("/app/your-type");
  expect(query("app-nav-your-type").getAttribute("aria-current")).toBe("page");
});

test("an old owner's pending bootstrap cannot reopen their page after owner changes", async () => {
  let resolveOld;
  const oldApi = { get: jest.fn(() => new Promise(resolve => { resolveOld = resolve; })) };
  const freshApi = { get: jest.fn(async () => ({ data: { audiences: [], profile: { adult_eligible: false, gender: null }, sessions: [], history_next: null } })) };
  function Context({ owner, api }) {
    return <Routes><Route element={<Outlet context={{ me: { user_id: owner }, api }} />}>
      <Route path="/" element={<YourTypePage />} />
    </Route></Routes>;
  }
  await act(async () => root.render(<MemoryRouter><Context owner="previous" api={oldApi} /></MemoryRouter>));
  await act(async () => root.render(<MemoryRouter><Context owner="current" api={freshApi} /></MemoryRouter>));
  await act(async () => resolveOld({ data: { audiences: [{id:"women"}], profile: { adult_eligible: true, gender: "Man" }, sessions: [], history_next: null } }));
  expect(host.textContent).toContain("Your Type is for adults");
  expect(oldApi.get).toHaveBeenCalledTimes(1);
  expect(oldApi.get.mock.calls[0][1].signal.aborted).toBe(true);
  expect(freshApi.get).toHaveBeenCalledTimes(1);
});
