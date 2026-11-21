import { act } from "react";
import { createRoot } from "react-dom/client";
import AccountClosure from "./AccountClosure";

// Jest does not load the Webpack aliases used by the shared dialog components.
jest.mock("@/lib/utils", () => jest.requireActual("../../lib/utils"), { virtual: true });
jest.mock("@/components/ui/button", () => jest.requireActual("../ui/button"), { virtual: true });

const originalLocation = window.location;
const httpError = (status, detail, headers = {}) => ({ response: { status, data: { detail }, headers } });
const element = id => document.querySelector(`[data-testid="${id}"]`);
const status = () => document.querySelector('[role="status"]');
let root, container, api;

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.useFakeTimers();
  Object.defineProperty(window, "location", { configurable: true, value: { ...originalLocation, replace: jest.fn() } });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  api = { get: jest.fn(), delete: jest.fn() };
});

afterEach(() => {
  if (root) act(() => root.unmount());
  container.remove();
  Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
  jest.useRealTimers();
});

const show = (closing = false) => act(async () => root.render(<AccountClosure api={api} closing={closing} />));
const tick = () => act(async () => jest.advanceTimersByTime(10_000));
async function confirm() {
  await act(async () => element("settings-close-account").click());
  expect(document.querySelector('[role="alertdialog"]').textContent).toContain("Close Imladri Personal?");
  await act(async () => element("settings-close-confirm").click());
}

test("only the confirmation sends a closure request and immediate success returns home", async () => {
  api.delete.mockResolvedValue({ data: { ok: true } });
  await show();
  expect(api.delete).not.toHaveBeenCalled();
  await act(async () => element("settings-close-account").click());
  const cancel = Array.from(document.querySelectorAll("button")).find(button => button.textContent === "Keep account");
  await act(async () => cancel.click());
  expect(api.delete).not.toHaveBeenCalled();
  await confirm();
  expect(api.delete).toHaveBeenCalledTimes(1);
  expect(api.delete).toHaveBeenCalledWith("/me/account", expect.objectContaining({ signal: expect.anything() }));
  expect(window.location.replace).toHaveBeenCalledWith("/");
  expect(api.get.mock.calls.map(([path]) => path)).toEqual(["/me/content-impact", "/me/content-impact"]);
});

test("an accepted closure waits through the active request and leaves Settings on HTTP 410", async () => {
  api.delete.mockRejectedValue(httpError(503, "Account closure is waiting for an in-progress request.", { "retry-after": "10" }));
  api.get.mockResolvedValueOnce({ data: { counts: {}, shared_space_active: false } })
    .mockResolvedValueOnce({ data: { closing: true } }).mockRejectedValueOnce(httpError(410));
  await show();
  await confirm();
  expect(element("settings-close-account").disabled).toBe(true);
  expect(status().getAttribute("aria-atomic")).toBe("true");
  expect(status().textContent).toMatch(/closing.*return home/);
  expect(status().textContent).not.toMatch(/in-progress request/);
  expect(window.location.replace).not.toHaveBeenCalled();
  await tick();
  expect(window.location.replace).toHaveBeenCalledWith("/");
  expect(api.get.mock.calls.map(([path]) => path)).toEqual(["/me/content-impact", "/me", "/me"]);
  expect(api.delete).toHaveBeenCalledTimes(1);
});

test("genuine billing errors remain visible while a confirmed closure is pending", async () => {
  const detail = "Account closure is waiting for billing confirmation.";
  api.delete.mockRejectedValue(httpError(503, detail));
  api.get.mockResolvedValue({ data: { closing: true } });
  await show();
  await confirm();
  expect(status().textContent).toContain(detail);
  expect(status().textContent).toContain("return home");
  expect(element("settings-close-account").disabled).toBe(true);
  expect(window.location.replace).not.toHaveBeenCalled();
});

test("a rejected request on an open account shows the provider error and allows a fresh confirmation", async () => {
  const detail = "Provider is unavailable.";
  api.delete.mockRejectedValue(httpError(503, detail));
  api.get.mockResolvedValue({ data: { closing: false } });
  await show();
  await confirm();
  expect(status().textContent).toContain(detail);
  expect(status().textContent).toContain("still open");
  expect(element("settings-close-account").disabled).toBe(false);
  expect(window.location.replace).not.toHaveBeenCalled();
});

test("revisiting a pending account only checks status, stops polling, and offers a read-only check", async () => {
  api.get.mockResolvedValue({ data: { closing: true } });
  await show(true);
  for (let n = 0; n < 20; n += 1) await tick();
  const reads = api.get.mock.calls.length;
  expect(reads).toBeGreaterThan(1);
  await tick();
  expect(api.get).toHaveBeenCalledTimes(reads);
  const check = [...container.querySelectorAll("button")].find(button => button.textContent === "Check closure status");
  expect(status().textContent).toMatch(/still closing.*again/);
  api.get.mockRejectedValueOnce(httpError(410));
  await act(async () => check.click());
  expect(window.location.replace).toHaveBeenCalledWith("/");
  expect(api.delete).not.toHaveBeenCalled();
});

test("failed status checks offer a read-only recovery without assuming the account was closed", async () => {
  api.get.mockRejectedValue(httpError(401, "Please sign in again."));
  await show(true);
  expect(status().textContent).toContain("Please sign in again.");
  expect(container.textContent).toContain("Check closure status");
  expect(element("settings-close-account").disabled).toBe(true);
  expect(window.location.replace).not.toHaveBeenCalled();
});

test("leaving Settings cancels the current status check and ignores a late response", async () => {
  let reject;
  api.get.mockImplementation(() => new Promise((_, fail) => { reject = fail; }));
  await show(true);
  const signal = api.get.mock.calls[0][1].signal;
  act(() => root.unmount());
  root = null;
  expect(signal.aborted).toBe(true);
  await act(async () => reject(httpError(410)));
  await tick();
  expect(api.get).toHaveBeenCalledTimes(1);
  expect(api.delete).not.toHaveBeenCalled();
  expect(window.location.replace).not.toHaveBeenCalled();
});
