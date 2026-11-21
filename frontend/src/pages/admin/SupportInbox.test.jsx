import { act } from "react";
import { createRoot } from "react-dom/client";
import SupportInbox from "./SupportInbox";

jest.mock("@/lib/api", () => ({ errMsg: error => error.message }));

let host, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});

test("a new request category clears a failed inbox read and shows its own loading and result", async () => {
  let finishAnswered;
  const answered = new Promise(resolve => { finishAnswered = resolve; });
  const api = { get: jest.fn((url, options) => {
    if (url.endsWith("/summary")) return Promise.resolve({ data: { open: 0, answered_with_time: 0 } });
    return options.params.status === "open"
      ? Promise.reject(new Error("Temporary inbox failure"))
      : answered;
  }) };
  await act(async () => root.render(<SupportInbox api={api} />));
  expect(host.textContent).toContain("Temporary inbox failure");
  await act(async () => {
    const select = host.querySelector("select");
    select.value = "answered";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(host.textContent).toContain("Loading requests…");
  expect(host.textContent).not.toContain("Temporary inbox failure");
  await act(async () => finishAnswered({ data: [] }));
  expect(host.textContent).toContain("No answered requests.");
  expect(host.querySelector('[role="alert"]')).toBeNull();
});
