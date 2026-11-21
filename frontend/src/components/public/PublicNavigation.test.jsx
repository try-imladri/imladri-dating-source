import { act } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import PublicLayout from "./PublicLayout";
import { DocLayout, Section } from "./Bits";
import { HowItWorks, LegacyHowItWorks, Privacy } from "@/pages/public/StaticPages";
import Support from "@/pages/public/Support";
import { publicApi } from "@/lib/api";
import { setOrigin } from "@/lib/api";
import { LANDING_SUMMARY } from "./landingCopy";
import { invalidatePublicCatalog } from "@/lib/publicCatalogCache";

let mockSignedIn = false;

jest.mock("react-router-dom", () => {
  global.TextEncoder = require("util").TextEncoder;
  return jest.requireActual("react-router");
});
jest.mock("@clerk/react", () => ({ Show: ({ when, children }) => when === (mockSignedIn ? "signed-in" : "signed-out") ? children : null }));
jest.mock("@/components/auth/ClerkAuthProvider", () => ({ PublicSessionAction: ({ fallback, children }) => mockSignedIn ? children : fallback }));
jest.mock("@/lib/theme", () => ({ ThemeToggle: () => null }));
jest.mock("@/lib/api", () => ({ publicApi: { get: jest.fn(), post: jest.fn() }, setOrigin: jest.fn(), pad: value => String(value), errMsg: () => "error" }));

const priorCrypto = Object.getOwnPropertyDescriptor(window, "crypto");
beforeAll(() => Object.defineProperty(window, "crypto", { configurable: true, value: require("crypto").webcrypto }));
afterAll(() => {
  if (priorCrypto) Object.defineProperty(window, "crypto", priorCrypto);
  else delete window.crypto;
});

let host, root;
beforeEach(() => {
  invalidatePublicCatalog("/public/archetypes");
  invalidatePublicCatalog("/public/figures");
  mockSignedIn = false;
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.spyOn(window, "scrollTo").mockImplementation(() => {});
  global.IntersectionObserver = class { observe() {} disconnect() {} };
  publicApi.get.mockResolvedValue({ data: {} });
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  invalidatePublicCatalog("/public/archetypes");
  invalidatePublicCatalog("/public/figures");
  host.remove();
  window.history.replaceState(null, "", "/");
  jest.clearAllMocks();
  jest.restoreAllMocks();
});

const render = page => act(async () => root.render(<BrowserRouter><Routes>
  <Route element={<PublicLayout />}><Route path="*" element={page} /></Route>
</Routes></BrowserRouter>));

test("dating navigation keeps editorial catalogs behind Learn without loading member or catalog data", async () => {
  await render(<h1>Home</h1>);
  expect(publicApi.get.mock.calls.map(([path]) => path)).toEqual(["/public/config"]);
  expect(host.querySelector('[data-testid="nav-types"]')).toBeNull();
  expect(host.querySelector('[data-testid="nav-figures"]')).toBeNull();
  const learn = host.querySelector('[data-testid="nav-learn"]');
  expect(learn.getAttribute("href")).toBe("/learn");
  await act(async () => learn.focus());
  expect(publicApi.get.mock.calls.map(([path]) => path)).toEqual(["/public/config"]);
});

test("keyboard navigation to another public page moves focus into the new content", async () => {
  await render(<h1>Library</h1>);
  const link = host.querySelector('[data-testid="nav-learn"]');
  link.focus();
  await act(async () => link.click());
  expect(window.location.pathname).toBe("/learn");
  expect(document.activeElement).toBe(host.querySelector("main"));
});

test("skipping repeated navigation keeps a private support request open and retains its return link", async () => {
  const receipt = "a".repeat(64);
  window.history.replaceState(null, "", "/support#" + receipt);
  publicApi.post.mockResolvedValue({ data: {status:"open", message:"My request", expires_at:"2027-01-01"} });
  await render(<Support />);
  const skip = host.querySelector('a[href="#public-main"]');
  skip.focus();
  await act(async () => skip.click());
  expect(document.activeElement).toBe(host.querySelector("main"));
  expect(window.location.hash).toBe("#" + receipt);
  expect(host.querySelector('[data-testid="support-receipt"]').textContent).toContain("My request");
  expect(publicApi.post).toHaveBeenCalledTimes(1);
});

test("an article section shared before the article finishes loading receives focus and has real links in both menus", async () => {
  window.history.replaceState(null, "", "/types/example#flags");
  await render(<p>Loading</p>);
  const toc = [{ id:"summary", label:"Summary" }, { id:"flags", label:"Flags" }];
  await render(<DocLayout toc={toc} head={{ back:{to:"/types", label:"Archetypes"}, title:"Example" }}>
    <Section id="summary" n={1} title="Summary" /><Section id="flags" n={2} title="Flags" />
  </DocLayout>);
  expect(document.activeElement).toBe(host.querySelector("#flags"));
  expect([...host.querySelectorAll('nav a[href="#flags"]')]).toHaveLength(2);
  expect(host.querySelector('[data-testid="doc-compact-head"]').hasAttribute("inert")).toBe(true);
});

function enterSupportMessage(value) {
  const field = host.querySelector("textarea");
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
const submitSupport = () => act(async () => {
  host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
});

test("leaving a pending support request keeps its private return token off the next page", async () => {
  let accept;
  window.history.replaceState(null, "", "/support?topic=Privacy");
  publicApi.post.mockReturnValueOnce(new Promise(resolve => { accept = resolve; }));
  await act(async () => root.render(<BrowserRouter><Routes><Route element={<PublicLayout />}>
    <Route path="/support" element={<Support />} /><Route path="/privacy" element={<Privacy />} />
  </Route></Routes></BrowserRouter>));
  enterSupportMessage("Please explain my export.");
  await submitSupport();
  expect(publicApi.post).toHaveBeenCalledWith("/support/requests", {
    category: "Privacy", message: "Please explain my export.", receipt: expect.stringMatching(/^[a-f0-9]{64}$/),
  });
  await act(async () => host.querySelector('[data-testid="footer-privacy-link"]').click());
  await act(async () => accept({ data: {} }));
  expect(window.location.pathname).toBe("/privacy");
  expect(window.location.hash).toBe("");
});

test("retrying a support request after a lost response reuses its original private link", async () => {
  window.history.replaceState(null, "", "/support?topic=Privacy");
  publicApi.post.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ data: {} })
    .mockResolvedValue({ data: { status: "open", message: "I need a copy of my records.", expires_at: "2027-01-01" } });
  await render(<Support />);
  enterSupportMessage("I need a copy of my records.");
  await submitSupport();
  expect(host.querySelector('[role="alert"]')).toBeTruthy();
  await submitSupport();
  const calls = publicApi.post.mock.calls.filter(([path]) => path === "/support/requests");
  expect(calls).toHaveLength(2);
  expect(calls[0][1]).toEqual(calls[1][1]);
  expect(window.location.hash).toBe("#" + calls[0][1].receipt);
});

test("the walkthrough and privacy guide explain when a partner sees the check-in result and note", async () => {
  await render(<LegacyHowItWorks />);
  expect(host.querySelector("#couples-example").textContent).toContain("only after Jordan also shares");
  await render(<Privacy />);
  expect(host.textContent).toContain("check-in result and any note once both of you choose to share");
});

test("the compact footer uses the same explanation as the landing page and retains every public help route", async () => {
  await render(<h1>Home</h1>);
  const footer = host.querySelector('[data-testid="site-footer"]');
  expect(footer.querySelector('[data-testid="footer-logo"] img').alt).toBe("Imladri");
  expect(footer.querySelector('[data-testid="footer-summary"]').textContent).toBe(LANDING_SUMMARY);
  expect(footer.querySelector('[data-testid="footer-privacy"]')).toBeNull();
  expect(footer.textContent).not.toContain("You can blur screenshots");
  expect(footer.textContent).not.toContain("For insight and entertainment");
  const destinations = [...footer.querySelectorAll('nav a')].map(link => link.getAttribute("href"));
  expect(destinations).toEqual([
    "/learn", "/how-it-works", "/about", "/legacy",
    "/privacy", "/terms", "/billing", "/support",
  ]);
});

test("the narrow-screen footer menu exposes its links and closes after navigating without constraining article pages", async () => {
  await render(<h1>Home</h1>);
  expect(host.querySelector(".public-layout--home")).toBeTruthy();
  const toggle = host.querySelector('[data-testid="footer-menu-toggle"]');
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  await act(async () => toggle.click());
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(host.querySelector("#" + toggle.getAttribute("aria-controls")).classList.contains("is-open")).toBe(true);
  await act(async () => host.querySelector('[data-testid="footer-privacy-link"]').click());
  expect(window.location.pathname).toBe("/privacy");
  expect(host.querySelector(".public-layout--home")).toBeNull();
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
});

test("the header routes founding enrollment through dating with attribution regardless of the old waitlist setting", async () => {
  await render(<h1>Home</h1>);
  let start = host.querySelector('[data-testid="nav-signup"]');
  expect(start.getAttribute("href")).toBe("/dating?origin=home");
  expect(start.textContent).toBe("Join dating");
  await act(async () => start.click());
  expect(setOrigin).toHaveBeenCalledWith("home");
  await act(async () => root.unmount());
  root = createRoot(host);
  window.history.replaceState(null, "", "/plans");
  publicApi.get.mockResolvedValue({ data: { waitlist_mode: true } });
  await render(<h1>Plans</h1>);
  start = host.querySelector('[data-testid="nav-signup"]');
  expect(start.textContent).toBe("Join dating");
  expect(start.getAttribute("href")).toBe("/dating?origin=plans");
  expect(start.classList.contains("start-prompt")).toBe(false);
});

test("the signed-in public header opens dating while old membership management stays in private tools", async () => {
  mockSignedIn = true;
  await render(<h1>Home</h1>);
  expect(host.querySelector('[data-testid="nav-signup"]')).toBeNull();
  expect(host.querySelector('[data-testid="nav-open-app"]').classList.contains("start-prompt")).toBe(false);
  expect(host.querySelector('[data-testid="nav-open-app"]').getAttribute("href")).toBe("/dating");
  await act(async () => host.querySelector('[data-testid="nav-plans"]').click());
  expect(host.querySelector('[data-testid="nav-open-app"]').getAttribute("href")).toBe("/dating");
});

test("the dating walkthrough does not publish legacy records or make intimacy mandatory", async () => {
  await render(<HowItWorks />);
  expect(host.textContent).toContain("not automatically turned into a public profile");
  expect(host.textContent).toContain("skip intimacy questions and still use dating");
  expect(host.querySelector('[data-testid="how-cta"]').getAttribute("href")).toBe("/dating?origin=how-it-works");
});
