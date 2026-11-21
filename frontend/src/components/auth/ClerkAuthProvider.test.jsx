import { act } from "react";
import { createRoot } from "react-dom/client";
import { useLocation } from "react-router-dom";
import ClerkAuthProvider, { PublicSessionAction } from "./ClerkAuthProvider";

jest.mock("react-router-dom", () => ({ useLocation: jest.fn(), useNavigate: () => jest.fn() }));
jest.mock("@clerk/react", () => ({ ClerkProvider: ({ children }) => children }));
jest.mock("@clerk/ui/themes", () => ({ dark: {} }), { virtual: true });
jest.mock("./RememberGuard", () => ({ RememberGuard: () => null }));
jest.mock("@/lib/api", () => ({ BACKEND_URL: "" }));

let root, container;
const originalFetch = global.fetch;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.useFakeTimers();
  global.fetch = jest.fn();
  container = document.createElement("div");
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  jest.useRealTimers();
  global.fetch = originalFetch;
});
const configured = { ok: true, json: async () => ({ enabled: true, publishable_key: "fixture" }) };

test.each(["/dating", "/dating/messages/fixture", "/legacy"])("%s establishes the existing protected session", async pathname => {
  useLocation.mockReturnValue({ pathname });
  fetch.mockResolvedValue(configured);
  await act(async () => root.render(<ClerkAuthProvider><p>Protected account</p></ClerkAuthProvider>));
  expect(fetch).toHaveBeenCalledWith("/api/clerk/config");
  expect(container.textContent).toBe("Protected account");
});

test("a protected entry announces its connection status across an initial failed configuration request", async () => {
  useLocation.mockReturnValue({ pathname: "/app/start" });
  fetch.mockResolvedValueOnce({ ok: false }).mockResolvedValueOnce(configured);
  await act(async () => root.render(<ClerkAuthProvider><p>Account</p></ClerkAuthProvider>));
  expect(container.querySelector('[role="status"]').textContent).toMatch(/connecting to sign-in/i);
  await act(async () => jest.advanceTimersByTime(1500));
  expect(container.textContent).toBe("Account");
  expect(fetch).toHaveBeenCalledTimes(2);
});

test("a public page remains interactive and retains its state when the optional header session connects", async () => {
  useLocation.mockReturnValue({ pathname: "/" });
  let finish;
  fetch.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await act(async () => root.render(<ClerkAuthProvider>
    <PublicSessionAction fallback={<button>Start here</button>}><button>Open app</button></PublicSessionAction>
    <input aria-label="Public demo" />
  </ClerkAuthProvider>));
  const input = container.querySelector("input");
  input.value = "In progress";
  expect(container.querySelector("button").textContent).toBe("Start here");
  expect(container.querySelector('[role="status"]')).toBeNull();
  await act(async () => finish(configured));
  expect(container.querySelector("button").textContent).toBe("Open app");
  expect(container.querySelector("input")).toBe(input);
  expect(input.value).toBe("In progress");
});

test("the support page and its public action remain available with no identity request", async () => {
  useLocation.mockReturnValue({ pathname: "/support" });
  await act(async () => root.render(<ClerkAuthProvider>
    <PublicSessionAction fallback={<span>Start here</span>}><span>Open app</span></PublicSessionAction>
    <h1>Support</h1>
  </ClerkAuthProvider>));
  expect(container.textContent).toBe("Start hereSupport");
  expect(fetch).not.toHaveBeenCalled();
});
