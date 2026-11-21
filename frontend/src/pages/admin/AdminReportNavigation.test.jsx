import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import AppShell from "@/components/app/AppShell";
import Admin from "./Admin";

// CRA's Jest resolver predates the react-router/dom subpath. Use the same
// declarative router exports that react-router-dom re-exports to the browser.
jest.mock("react-router-dom", () => {
  global.TextEncoder = require("util").TextEncoder;
  return jest.requireActual("react-router");
});
jest.mock("@clerk/react", () => ({
  Show: ({ when, children }) => when === "signed-in" ? children : null,
  useClerk: () => ({}),
  useAuth: () => ({ isLoaded: true, userId: "fictional-staff", sessionId: "session-staff" }),
}));
jest.mock("@/components/app/NotificationBell", () => ({ NotificationBell: () => null }));
jest.mock("@/lib/theme", () => ({ ThemeToggle: () => null }));
jest.mock("@/lib/api", () => ({
  useApi: () => mockApi,
  applySignupOrigin: async (_, member) => member,
}));
jest.mock("./WorkspaceTrends", () => ({
  __esModule: true,
  default: () => <section aria-label="Workspace report" />,
  CouplesReport: () => <section aria-label="Couples report" />,
}));
jest.mock("./ResearchTrends", () => () => <section aria-label="Questionnaire research" />);
jest.mock("./ActivityFeed", () => ({
  ActivityFeed: () => <section aria-label="Live feed" />,
}));
jest.mock("./AdminMetrics", () => () => <section aria-label="Dashboard metrics" />);

const mockApi = {
  get: jest.fn(),
  post: jest.fn(),
  put: jest.fn(),
  interceptors: { response: { use: jest.fn(), eject: jest.fn() } },
};

function HistoryControls() {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  return <><output>{pathname}{search}</output><button onClick={() => navigate(-1)}>Go back</button></>;
}

let host, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  mockApi.get.mockResolvedValue({ data: { role: "admin" } });
  mockApi.post.mockResolvedValue({});
  sessionStorage.clear();
  host = document.createElement("div");
  root = createRoot(host);
});
afterEach(() => act(() => root.unmount()));

async function open(path) {
  await act(async () => {
    root.render(<MemoryRouter initialEntries={[path]}>
      <HistoryControls />
      <Routes>
        <Route path="/admin" element={<AppShell admin />}>
          <Route index element={<Admin />} />
          <Route path=":section" element={<Admin />} />
        </Route>
        <Route path="/app" element={<h1>Member home</h1>} />
      </Routes>
    </MemoryRouter>);
  });
}

function expectReport(path, title, report) {
  expect(host.querySelector("output").textContent).toBe(path);
  expect(host.querySelector("h1").textContent).toBe(title);
  expect(host.querySelector("nav a[aria-current='page']").textContent).toBe(title);
  expect([...host.querySelectorAll("main section[aria-label]")].map(el => el.getAttribute("aria-label"))).toEqual([report]);
}

test("Activity and Couples have independent URLs and top-level navigation links", async () => {
  await open("/admin/couples");
  expectReport("/admin/couples", "Couples", "Couples report");
  const activity = host.querySelector("nav a[href='/admin/activity']");
  expect(activity.textContent).toBe("Activity");
  await act(async () => activity.click());
  expectReport("/admin/activity", "Activity", "Workspace report");
  const couples = host.querySelector("nav a[href='/admin/couples']");
  await act(async () => couples.click());
  expectReport("/admin/couples", "Couples", "Couples report");
});

test("selecting live activity and going back restores the workspace report", async () => {
  await open("/admin/activity");
  const button = label => [...host.querySelectorAll("button")].find(el => el.textContent === label);
  expect(button("Workspace activity").getAttribute("aria-pressed")).toBe("true");
  await act(async () => button("Live activity").click());
  expectReport("/admin/activity?view=live", "Activity", "Live feed");
  expect(button("Live activity").getAttribute("aria-pressed")).toBe("true");
  await act(async () => button("Go back").click());
  expectReport("/admin/activity", "Activity", "Workspace report");
});

test.each([
  ["trends", "/admin/activity", "Workspace report"],
  ["activity", "/admin/activity?view=live", "Live feed"],
])("existing dashboard %s bookmarks open the corresponding Activity view", async (oldView, path, report) => {
  await open("/admin?view=" + oldView);
  expectReport(path, "Activity", report);
});

test("Research has a directly accessible report and navigation link", async () => {
  await open("/admin/research");
  expectReport("/admin/research", "Research", "Questionnaire research");
});

test("a staff dating edit saves to the same article and leaves its other sections intact", async () => {
  const row = { slug: "person", name: "Person", published: true };
  const original = { pattern: ["Earlier copy"], dating: ["First scene.", "Second scene."] };
  const published={name:"Person",identity:"An introduction",matches:[],sources:[],sections:original};
  const initial={version:"v1",visible:true,live_revision:0,published,draft:null,changes:[],history:[]};
  mockApi.get.mockImplementation(async path => ({data: path==="/admin/figures?summary=true" ? [row] : path==="/admin/editorial/articles/figures/person" ? initial : {role:"admin"}}));
  mockApi.put.mockImplementation(async (path,body)=>({data:{...initial,version:"v2",draft:body.content,changes:["sections.dating"]}}));
  await open("/admin/figures");
  await act(async () => host.querySelector('[data-testid="admin-edit-figure-person"]').click());
  const dialog = document.querySelector('[data-testid="admin-edit-dialog"]');
  const section=dialog.querySelector('[aria-label="Edit section"]');
  await act(async()=>{section.value='dating';section.dispatchEvent(new Event('change',{bubbles:true}));});
  const field = dialog.querySelector('[aria-label="Dating paragraph 2"]');
  expect(field.value).toBe("Second scene.");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(field, "A revised second scene.");
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => dialog.querySelector('[data-testid="admin-edit-save"]').click());
  expect(mockApi.put).toHaveBeenCalledWith("/admin/editorial/articles/figures/person/draft",{version:"v1",content:{...published,
    sections:{pattern:original.pattern,dating:["First scene.","A revised second scene."]}}});
});

test("the existing dashboard Couples bookmark opens the official Couples report", async () => {
  await open("/admin?view=couples");
  expectReport("/admin/couples", "Couples", "Couples report");
});

test.each(["activity", "couples", "research"])("the %s Admin report requires a staff account", async section => {
  mockApi.get.mockResolvedValue({ data: { role: "member" } });
  await open("/admin/" + section);
  expect(host.querySelector("output").textContent).toBe("/app");
  expect(host.querySelector("h1").textContent).toBe("Member home");
});
