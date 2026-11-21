import { act } from "react";
import { createRoot } from "react-dom/client";
import CouplesAnswerHistory from "./CouplesAnswerHistory";

jest.mock("./AnswerHistoryTimeline", () => ({ __esModule: true, default: ({ scope, target }) => <div data-testid="selected-history">{scope}:{target}</div> }));
let host, root;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; host = document.createElement("div"); root = createRoot(host); });
afterEach(() => { act(() => root.unmount()); delete global.IS_REACT_ACT_ENVIRONMENT; });
const change = async (element, value) => act(async () => { element.value = value; element.dispatchEvent(new Event("change", { bubbles: true })); });
const initial = { items: [{ pair_id: "fictional-pair-A", label: "Alex · Sam" }], next: "bound-next-page" };

test("paging clears the previous couple's history and selects only a returned target", async () => {
  const api = { get: jest.fn(async (_, options) => ({ data: options.params.before ? { items: [{ pair_id: "fictional-pair-B", label: "Jo · Lee" }], next: null } : initial })) };
  await act(async () => root.render(<CouplesAnswerHistory api={api} />));
  await change(host.querySelector("select"), "fictional-pair-A");
  expect(host.querySelector('[data-testid="selected-history"]').textContent).toBe("couple:fictional-pair-A");
  await act(async () => [...host.querySelectorAll("button")].find(button => button.textContent === "Next couples").click());
  expect(api.get.mock.calls[1][1].params.before).toBe("bound-next-page");
  expect(host.querySelector('[data-testid="selected-history"]')).toBeNull();
  await change(host.querySelector("select"), "fictional-pair-B");
  expect(host.querySelector('[data-testid="selected-history"]').textContent).toBe("couple:fictional-pair-B");
});

test("a later search wins over a stale pending page response", async () => {
  let finish;
  const api = { get: jest.fn().mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValue({ data: { items: [{ pair_id: "fictional-pair-B", label: "Jo · Lee" }], next: null } }) };
  await act(async () => root.render(<CouplesAnswerHistory api={api} />));
  const firstSignal = api.get.mock.calls[0][1].signal;
  await change(host.querySelector("input"), "fictional-pair-B");
  await act(async () => host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(firstSignal.aborted).toBe(true);
  await act(async () => finish({ data: initial }));
  expect([...host.querySelectorAll("option")].map(option => option.value)).toEqual(["", "fictional-pair-B"]);
});
