import { act, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation, useOutletContext } from "react-router-dom";
import AppShell from "@/components/app/AppShell";
import { readAssessmentProfile } from "@/lib/assessmentResource";

jest.mock("react-router-dom", () => { global.TextEncoder = require("util").TextEncoder; return jest.requireActual("react-router"); });
jest.mock("@clerk/react", () => ({ Show: ({ when, children }) => when === "signed-in" ? children : null,
  useAuth: () => ({ userId: "own-account", sessionId: "own-session", isLoaded: true }), useClerk: () => ({ signOut: jest.fn() }) }));
jest.mock("@/lib/api", () => ({ useApi: () => mockApi, applySignupOrigin: async (_, member) => member }));
jest.mock("@/components/app/NotificationBell", () => ({ NotificationBell: () => null }));
jest.mock("@/lib/theme", () => ({ ThemeToggle: () => null }));
jest.mock("@/lib/browserSession", () => ({ endBrowserSession: jest.fn(), recordMemberVisit: async () => {} }));
jest.mock("@/components/app/ResearchFirstUse", () => ({ __esModule: true, default: ({ children }) => typeof children === "function" ? children() : children }));

let mockApi, host, root, member;
const access = (paired = true) => ({ version: "workspace-access-v1", mode: paired ? "couples" : "individual",
  couples_only: paired, pair_id: paired ? "accepted-pair" : null, individual: !paired, your_type: !paired, assessments: true });
function Screen() { const { pathname, search } = useLocation(); return <output data-testid="location">{pathname + search}</output>; }
async function show(path = "/app/personal?tab=intimacy") {
  await act(async () => root.render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/app" element={<AppShell />}><Route index element={<Screen />} /><Route path="*" element={<Screen />} /></Route>
    <Route path="/admin" element={<AppShell admin />}><Route index element={<Screen />} /></Route>
  </Routes></MemoryRouter>));
}
const nav = id => host.querySelector(`[data-testid="${id}"]`);
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  member = { user_id: "own-account", role: "member", onboarded: true, personal_access: true,
    workspace_access: access(), usage: { plan: "plus", period_verified: true, messages_remaining: 86 } };
  mockApi = { get: jest.fn(async () => ({ data: member })), interceptors: { response: { use: jest.fn(), eject: jest.fn() } } };
  host = document.body.appendChild(document.createElement("div")); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); sessionStorage.clear(); });

test("active paired accounts have only Couples and personal/intimacy assessment navigation", async () => {
  await show();
  expect(nav("location").textContent).toBe("/app/couples/intimacy");
  expect(nav("workspace-individual")).toBeNull();
  expect(nav("app-nav-your-type")).toBeNull();
  expect(nav("app-nav-couples-personal").getAttribute("href")).toBe("/app/couples/personal");
  expect(nav("app-nav-couples-personality")).toBeNull();
  expect(nav("app-nav-couples-intimacy").getAttribute("href")).toBe("/app/couples/intimacy");
  expect(nav("app-nav-couples-compatibility").getAttribute("href")).toBe("/app/couples/compatibility");
});

test("initial own assessment read overlaps /me but renders only after its fresh authorization", async () => {
  member.workspace_access=access(false);
  member.main_consent={reviewed:true,welcome_complete:true,pair_id:null,pair_sharing:{available:false}};
  let finishMe;
  mockApi.get.mockImplementation(path => path === "/me" ? new Promise(resolve=>{finishMe=resolve;})
    : Promise.resolve({data:{owner_id:member.user_id,about:{describe:"Own profile"}}}));
  function Assessment() {
    const {api,me}=useOutletContext(); const [data,setData]=useState(null);
    useEffect(()=>{
      const controller=new AbortController();
      readAssessmentProfile(api,me.user_id,{signal:controller.signal}).then(value=>{if(!controller.signal.aborted)setData(value);});
      return ()=>controller.abort();
    },[api,me.user_id]);
    return <output>{data?.about.describe}</output>;
  }
  await act(async () => root.render(<MemoryRouter initialEntries={["/app/personal"]}><Routes>
    <Route path="/app" element={<AppShell />}><Route path="personal" element={<Assessment />} /></Route>
  </Routes></MemoryRouter>));
  expect(mockApi.get.mock.calls.map(([path])=>path).sort()).toEqual(["/me","/me/assessments"]);
  expect(host.textContent).not.toContain("Own profile");
  await act(async () => finishMe({data:member}));
  expect(host.textContent).toContain("Own profile");
  expect(mockApi.get.mock.calls.filter(([path])=>path==="/me/assessments")).toHaveLength(1);
});

test("Your Type deep link goes to Couples overview without mounting that screen", async () => {
  await show("/app/your-type"); expect(nav("location").textContent).toBe("/app/couples");
});

test.each([["personality", "personal?tab=results", "personal"], ["intimacy", "intimacy", "intimacy"],
  ["quick-advice", "quick-advice", "quick-advice"]])("paired canonical %s opens only its Couples destination", async (section, destination, navKey) => {
  await show(`/app/${section}`);
  expect(nav("location").textContent).toBe(`/app/couples/${destination}`);
  expect(nav(`app-nav-couples-${navKey}`).getAttribute("aria-current")).toBe("page");
  expect(nav("workspace-individual")).toBeNull();
  expect(nav("app-nav-your-type")).toBeNull();
});

test.each(["personality", "intimacy", "quick-advice"])("unentitled Individual %s still goes through Plans", async section => {
  member.workspace_access=access(false); member.personal_access=false;
  await show(`/app/${section}`);
  expect(nav("location").textContent).toBe("/app/settings/upgrade");
  expect(nav(`app-nav-${section}`)).toBeNull();
  expect(mockApi.get.mock.calls.every(([path]) => ["/me", "/me/assessments", "/me/assessments?include_intimacy=true"].includes(path))).toBe(true);
  for (const [path, options] of mockApi.get.mock.calls) {
    if (path !== "/me") expect(options.signal.aborted).toBe(true);
  }
});

test("Settings/data remain accessible with Couples navigation", async () => {
  await show("/app/settings#questionnaire-research");
  expect(nav("location").textContent).toBe("/app/settings");
  expect(nav("workspace-individual")).toBeNull();
  expect(nav("app-nav-couples-settings").getAttribute("href")).toBe("/app/settings");
});

test("pending confirmations keep Individual and Your Type", async () => {
  member.workspace_access = access(false); await show("/app/your-type");
  expect(nav("location").textContent).toBe("/app/your-type");
  expect(nav("workspace-individual")).not.toBeNull(); expect(nav("app-nav-your-type")).not.toBeNull();
});

test("a /couples snapshot updates access immediately even with no usage payload", async () => {
  member.workspace_access = access(false); await show();
  expect(nav("location").textContent).toBe("/app/personal?tab=intimacy");
  const interceptor = mockApi.interceptors.response.use.mock.calls[0][0];
  await act(async () => interceptor({ data: { workspace_access: access() }, config: { url: "/couples" } }));
  expect(nav("location").textContent).toBe("/app/couples/intimacy");
  expect(nav("replies-left").textContent).toBe("86 messages left");
});

test("paired staff product paths lock while Admin stays available", async () => {
  member.role = "admin"; await show("/admin");
  expect(nav("location").textContent).toBe("/admin");
  expect(nav("admin-view-individual").textContent).toBe("View Couples");
  expect(nav("admin-view-individual").getAttribute("href")).toBe("/app/couples");
});

test("a focused /me refresh adds and clears a neutral review notice even without usage", async () => {
  await show("/app/couples");
  expect(nav("billing-review-notice")).toBeNull();
  const interceptor = mockApi.interceptors.response.use.mock.calls[0][0];
  await act(async () => interceptor({ data: { billing_review: { type: "payment_dispute_hold", hold_scopes: [],
    pending_payment_disputes: 1, temporary_payment_hold_count: 23 } }, config: { url: "/me" } }));
  const notice = nav("billing-review-notice");
  expect(notice.textContent).toContain("A payment is being reviewed");
  expect(notice.textContent).not.toMatch(/fraud|buy|upgrade|top-up/i);
  expect(notice.querySelector('a[href="/app/settings/upgrade"]').textContent).toBe("Billing & cancellation");
  expect(notice.querySelector('a[href="/app/settings"]')).not.toBeNull();
  expect(notice.querySelector('a[href="/support?topic=Billing"]')).not.toBeNull();
  expect(nav("replies-left").textContent).toBe("86 messages left");
  await act(async () => interceptor({ data: { billing_review: { type: "clear", hold_scopes: [], pending_payment_disputes: 0 } }, config: { url: "/me" } }));
  expect(nav("billing-review-notice")).toBeNull();
});

test("unheld review signals alone do not accuse or restrict the customer", async () => {
  member.billing_review = { requires_staff_review: true, hold_scopes: [], pending_payment_disputes: 0 };
  await show("/app/couples");
  expect(nav("billing-review-notice")).toBeNull();
});

test("an explicit staff hold explains temporary new-action restrictions without offering a purchase", async () => {
  member.billing_review = { type: "staff_review_hold", hold_scopes: ["new_ai"], pending_payment_disputes: 0 };
  await show("/app/settings");
  expect(nav("billing-review-notice").textContent).toContain("Some new actions are temporarily paused");
  expect(nav("location").textContent).toBe("/app/settings");
  expect(nav("billing-review-notice").textContent).not.toMatch(/buy|View plans|top-up/);
});
