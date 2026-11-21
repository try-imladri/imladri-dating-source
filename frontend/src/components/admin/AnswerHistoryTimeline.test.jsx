import { act } from "react";
import { createRoot } from "react-dom/client";
import AnswerHistoryTimeline from "./AnswerHistoryTimeline";

let root, host;
const click = node => act(async () => node.click());
const button = label => [...host.querySelectorAll("button")].find(node => node.textContent === label);
const submit = () => act(async () => host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
const item = (id = "one") => ({ event_id: id, observed_at_utc: "2026-11-14T12:00:00+00:00", domain: "everyday", baseline: false,
  status: "available", visibility: "private", question_id: "e:q1", question: { label: "How do you choose?" }, edition: "wording-v1",
  attempt_id: "attempt-one", kind: "answer_changed", before_exists: true, before: { choice: "A plan" }, after: { choice: "Time to think" },
  demographics_at_observation: { age: 25, height_cm: 170 } });
const response = (items = [item()], next = null) => ({ data: { items, next, limit: 50, scope: "member", notice: "Baselines are observations, not earlier attempts." } });

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.body.appendChild(document.createElement("div")); root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); delete global.IS_REACT_ACT_ENVIRONMENT;
});
const render = (api, props = {}) => act(async () => root.render(<AnswerHistoryTimeline api={api} target="member-one" {...props} />));

test("loads on request and distinguishes historical snapshots, wording, private visibility and baselines", async () => {
  const api = { get: jest.fn(async () => response([{ ...item(), baseline: true }])) };
  await render(api);
  expect(api.get).not.toHaveBeenCalled();
  await submit();
  expect(api.get).toHaveBeenCalledWith("/admin/answer-history/member/member-one", expect.objectContaining({ params: { limit: 50 }, signal: expect.any(AbortSignal) }));
  expect(host.textContent).toContain("Baseline observation");
  expect(host.textContent).toContain("Private · staff permission");
  expect(host.textContent).toContain("How do you choose?");
  expect(host.textContent).toContain("wording-v1");
  expect(host.textContent).toContain('"age": 25');
  expect(button("Previous observations").disabled).toBe(true);
});

test("withheld entries never render absent sensitive values or invented demographic data", async () => {
  const api = { get: jest.fn(async () => response([{ event_id: "withheld", observed_at_utc: "2026-11-14", domain: "intimacy", baseline: false,
    status: "withheld", withheld_reason: "Permission was not recorded." }, { ...item(), demographics_at_observation: null, before: null,
      before_withheld: "Earlier value has no matching permission." }])) };
  await render(api); await submit();
  expect(host.textContent).toContain("Details withheld. Permission was not recorded.");
  expect(host.textContent).toContain("Current profile values are not substituted.");
  expect(host.textContent).toContain("Earlier value has no matching permission.");
  expect(host.querySelectorAll("details")).toHaveLength(1);
});

test("pagination uses the signed cursor and changing filters restarts the first page", async () => {
  const api = { get: jest.fn(async (path, config) => response([item(config.params.before ? "two" : "one")], config.params.before ? null : "signed-next")) };
  await render(api); await submit();
  await click(button("Older observations"));
  expect(api.get.mock.calls.at(-1)[1].params).toEqual({ limit: 50, before: "signed-next" });
  expect(host.textContent).toContain("Page 2");
  const select = host.querySelector("select");
  await act(async () => { select.value = "intimacy"; select.dispatchEvent(new Event("change", { bubbles: true })); });
  await submit();
  expect(api.get.mock.calls.at(-1)[1].params).toEqual({ limit: 50, domain: "intimacy" });
  expect(host.textContent).toContain("Page 1");
});

test.each([
  ["person_intake", "People observations"],
  ["assessment_repeat", "Questionnaire retakes"],
  ["wording_feedback", "Intimacy wording feedback"],
])("the %s area sends its actual retained producer domain", async (domain, label) => {
  const api = { get: jest.fn(async () => response([{ ...item(), domain }])) };
  await render(api);
  const select = host.querySelector("select");
  expect([...select.options].find(option => option.value === domain)?.textContent).toBe(label);
  await act(async () => { select.value = domain; select.dispatchEvent(new Event("change", { bubbles: true })); });
  await submit();
  expect(api.get.mock.calls.at(-1)[1].params).toEqual({ limit: 50, domain });
  expect(host.querySelector('[aria-label="Answer observations"]').textContent).toContain(label);
});

test("clears previous target data and ignores late responses for another selected member", async () => {
  let complete;
  const api = { get: jest.fn(() => new Promise(resolve => { complete = resolve; })) };
  await render(api); await submit();
  const signal = api.get.mock.calls[0][1].signal;
  await render(api, { target: "member-two" });
  expect(signal.aborted).toBe(true);
  await act(async () => complete(response([{ ...item(), after: "Old member private value" }])));
  expect(host.textContent).not.toContain("Old member private value");
  expect(button("Load answer history")).toBeTruthy();
});

test("failed next-page loads clear stale rows and show a reload explanation", async () => {
  const api = { get: jest.fn().mockResolvedValueOnce(response([item()], "signed-next")).mockRejectedValueOnce({ response: { status: 409 } }) };
  await render(api); await submit(); await click(button("Older observations"));
  expect(host.querySelector('[role="alert"]').textContent).toContain("This page changed");
  expect(host.querySelector('[aria-label="Answer observations"]')).toBeNull();
});

test("CSV explicitly downloads one page and reuses the applied filters", async () => {
  const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = jest.fn(() => "blob:history-fixture"); URL.revokeObjectURL = jest.fn();
  const anchorClick = jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const api = { get: jest.fn(async path => path.endsWith(".csv") ? { data: "event_id\none\n" } : response()) };
  try {
    await render(api); await submit(); await click(button("Download this page CSV"));
    expect(api.get.mock.calls.at(-1)[0]).toBe("/admin/answer-history/member/member-one.csv");
    expect(api.get.mock.calls.at(-1)[1]).toEqual(expect.objectContaining({ params: { limit: 50 }, responseType: "blob" }));
    expect(host.textContent).toContain("It contains this page only");
    expect(anchorClick).toHaveBeenCalledTimes(1);
  } finally {
    anchorClick.mockRestore(); URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke;
  }
});

test("Couples scope uses the pair endpoint and excludes individual preference filter options", async () => {
  const api = { get: jest.fn(async () => response([])) };
  await render(api, { scope: "couple", target: "pair one/encoded" }); await submit();
  expect(api.get.mock.calls[0][0]).toBe("/admin/answer-history/couple/pair%20one%2Fencoded");
  expect([...host.querySelectorAll("option")].map(node => node.value)).toEqual(["", "couples"]);
  expect(host.textContent).toContain("No retained observations match this page");
});
