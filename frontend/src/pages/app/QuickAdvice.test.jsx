import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import QuickAdvice from "./QuickAdvice";

jest.mock("react-router-dom", () => {
  global.TextEncoder = require("util").TextEncoder;
  return jest.requireActual("react-router");
});
jest.mock("@/lib/api", () => ({ errMsg: e => e.response?.data?.detail || e.message || "Failed" }));
jest.mock("@/components/app/Advice", () => ({ SixPart: ({ r }) => <p>{r.best_move}</p> }));
let host, root, api, context, history, serial;
const usage = { plan: "plus", plan_id: "plus_100", period_verified: true, messages_remaining: 99 };
const record = { id: "00000000-0000-4000-8000-000000000001", thread_id: "thread", question: "Original question", created_at: "2026-11-18T12:00:00Z", response: { best_move: "Ask one clarifying question.", unknowns: [] } };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
async function render(path="/app/quick-advice") {
  await act(async () => root.render(<MemoryRouter initialEntries={[path]}><Routes><Route element={<Outlet context={context} />}>
    <Route path="/app/quick-advice" element={<QuickAdvice />} /><Route path="/app/couples/quick-advice" element={<QuickAdvice />} />
  </Route></Routes></MemoryRouter>));
}
async function type(text) {
  await act(async () => {
    const input = host.querySelector("#quick-advice-question");
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
const click = async node => act(async () => node.click());
const send = () => click(host.querySelector('button[type="submit"]'));
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  serial = 0;
  Object.defineProperty(window, "crypto", { configurable: true, value: { randomUUID: () => `key-${++serial}` } });
  window.confirm = jest.fn(() => true);
  history = { items: [], next_cursor: null, total: 0, usage };
  api = { get: jest.fn(async () => ({ data: history })), post: jest.fn(async () => ({ data: record })),
    patch: jest.fn(async () => ({ data: { ok: true } })), delete: jest.fn(async () => ({ data: { ok: true } })) };
  context = { api, me: { user_id: "owner", usage }, refreshUsage: jest.fn() };
  host = document.body.appendChild(document.createElement("div")); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

test("a situation needs no People profile and uses the shared metered send hook", async () => {
  await render();
  expect(api.get).toHaveBeenCalledWith("/quick-advice", expect.objectContaining({ signal: expect.anything() }));
  expect(host.textContent).toContain("No People profile needed");
  await type("They changed the plan. What should I ask?"); await send();
  expect(api.post).toHaveBeenCalledWith("/quick-advice", { question: "They changed the plan. What should I ask?" }, { headers: { "Idempotency-Key": "key-1" } });
  expect(context.refreshUsage).toHaveBeenCalledTimes(1);
  expect(host.querySelector("textarea").value).toBe("");
});

test("uncertain retry retains its request key and double clicks do not issue a second request", async () => {
  const pending = deferred(); api.post.mockImplementationOnce(() => pending.promise);
  await render(); await type("A situation"); await send(); await send();
  expect(api.post).toHaveBeenCalledTimes(1);
  await act(async () => pending.reject(new Error("Connection lost")));
  expect(host.querySelector("textarea").value).toBe("A situation");
  await send();
  expect(api.post).toHaveBeenCalledTimes(2);
  expect(api.post.mock.calls[1][2].headers["Idempotency-Key"]).toBe("key-1");
});

test("Couples uses its own endpoint and sponsor usage rather than an unfunded guest allowance", async () => {
  context.me.usage = { plan: "none", messages_remaining: 0 };
  await render("/app/couples/quick-advice"); await type("We disagree about plans");
  expect(host.querySelector('button[type="submit"]').disabled).toBe(false);
  await send();
  expect(api.post.mock.calls[0][0]).toBe("/couples/quick-advice");
  expect(host.textContent).toContain("Your advice stays private");
});

test("exhaustion blocks new messages but preserves saved advice and follow-up controls", async () => {
  history = { ...history, items: [record], usage: { ...usage, messages_remaining: 0 } };
  await render(); await type("New situation");
  expect(host.querySelector('button[type="submit"]').disabled).toBe(true);
  expect(host.textContent).toContain(record.response.best_move);
  expect(host.textContent).toContain("What happened next?");
  expect(api.post).not.toHaveBeenCalled();
});

test("withdrawn Couples context hides old advice and disables continuing it", async () => {
  history = { ...history, items: [{ ...record, response: undefined, context_changed: true }] };
  await render("/app/couples/quick-advice");
  expect(host.textContent).toContain("changed or was withdrawn");
  expect(host.textContent).not.toContain("Ask a follow-up");
  expect(host.textContent).not.toContain(record.response.best_move);
});

test("owner switch ignores an in-flight old answer and clears the input", async () => {
  const pending = deferred(); api.post.mockImplementationOnce(() => pending.promise);
  await render(); await type("Private first owner situation"); await send();
  context = { ...context, me: { ...context.me, user_id: "other" } };
  await render();
  await act(async () => pending.resolve({ data: { ...record, question: "Private first owner situation" } }));
  expect(host.textContent).not.toContain("Private first owner situation");
  expect(host.querySelector("textarea").value).toBe("");
});

test("API session replacement rejects an old history response for the same owner", async () => {
  const pending = deferred(); api.get.mockImplementationOnce(() => pending.promise);
  await render();
  const oldRequest = api.get.mock.calls[0][1].signal;
  const freshApi = { ...api, get: jest.fn(async () => ({ data: history })) };
  context = { ...context, api: freshApi };
  await render();
  expect(oldRequest.aborted).toBe(true);
  expect(freshApi.get).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve({ data: { ...history, items: [record] } }));
  expect(host.textContent).not.toContain(record.response.best_move);
});

test("API session replacement rejects an old answer and resets its input", async () => {
  const pending = deferred(); api.post.mockImplementationOnce(() => pending.promise);
  await render(); await type("Previous session's situation"); await send();
  const freshApi = { ...api, get: jest.fn(async () => ({ data: history })) };
  context = { ...context, api: freshApi };
  await render();
  await act(async () => pending.resolve({ data: record }));
  expect(host.textContent).not.toContain(record.response.best_move);
  expect(host.querySelector("textarea").value).toBe("");
  expect(freshApi.get).toHaveBeenCalledTimes(1);
});

test("saved conversation can be continued and deleted through its owner endpoint", async () => {
  history = { ...history, items: [record] };
  await render();
  await click([...host.querySelectorAll("button")].find(b => b.textContent.includes("Ask a follow-up")));
  await type("And what next?"); await send();
  expect(api.post.mock.calls[0][1]).toEqual({ question: "And what next?", parent_id: record.id });
  await click(host.querySelector('[aria-label="Delete this advice conversation"]'));
  expect(api.delete).toHaveBeenCalledWith(`/quick-advice/${record.id}`);
});

test("follow-up feedback saves without using an AI message", async () => {
  history = { ...history, items: [record] };
  await render();
  await click([...host.querySelectorAll("button")].find(b => b.textContent === "What happened next?"));
  await click([...host.querySelectorAll("button")].find(b => b.textContent === "Save follow-up"));
  expect(api.patch).toHaveBeenCalledWith(`/quick-advice/${record.id}/outcome`, { text: "", helped: "not_tried" });
  expect(api.post).not.toHaveBeenCalled();
});

test("an Overview link focuses a visible reply without an extra request", async () => {
  history = { ...history, items: [record] };
  await render(`/app/quick-advice?reply=${record.id}`);
  expect(document.activeElement.id).toBe(`quick-advice-record-${record.id}`);
  expect(api.get).toHaveBeenCalledTimes(1);
});

test("an older linked reply is fetched through the owner endpoint and focused", async () => {
  api.get.mockImplementation(async path => ({ data: path === `/quick-advice/${record.id}` ? record : history }));
  await render(`/app/quick-advice?reply=${record.id}`);
  expect(api.get).toHaveBeenCalledTimes(2);
  expect(document.activeElement.id).toBe(`quick-advice-record-${record.id}`);
  expect(host.textContent).toContain(record.response.best_move);
});

test("a deleted or other-owner linked reply cannot expose advice", async () => {
  api.get.mockImplementation(async path => {
    if (path === `/quick-advice/${record.id}`) throw { response: { status: 404, data: { detail: "Advice unavailable." } } };
    return { data: history };
  });
  await render(`/app/quick-advice?reply=${record.id}`);
  expect(host.textContent).toContain("Advice unavailable.");
  expect(host.textContent).not.toContain(record.response.best_move);
});
