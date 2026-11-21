import { act } from "react";
import { createRoot } from "react-dom/client";
import ResearchNoticeSummary from "./ResearchNoticeSummary";

let host, root;
const scope = { eligible: 8, reviewed: 6, unreviewed: 2, enabled: 3,
  decisions: { allow: 4, decline: 1, unavailable: 1 } };
const report = { notice_version: "main-consent-v1", notice_sha256: "a".repeat(64),
  members: 10, reviewed: 7, unreviewed: 3, current_context_complete: 6,
  scopes: Object.fromEntries(["questionnaire", "your_type", "tone", "member_csv", "couple_csv", "staff_history",
    "trends", "couples_statistics", "couples_return"].map(key => [key, { ...scope, enabled: key === "your_type" ? 1 : 3 }])) };
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

test("separates historical notice decisions from current enabled permissions and the research denominator", async () => {
  const api = { get: jest.fn(async () => ({ data: report })) };
  await act(async () => root.render(<ResearchNoticeSummary api={api} />));
  expect(api.get.mock.calls[0][0]).toBe("/admin/main-consent");
  expect(host.textContent).toContain("10 current members");
  expect(host.textContent).toContain("4 allowed · 1 declined · 1 unavailable at review");
  expect(host.textContent).toContain("do not contain answers or measure the research sample size");
  const cards = [...host.querySelectorAll("h3")].map(title => title.parentElement.textContent);
  expect(cards[0]).toContain("Currently enabled3");
  expect(cards[1]).toContain("Currently enabled1");
  expect(cards).toHaveLength(9);
  expect(host.textContent).toContain("Current-partner assessment sharing has its own pair-specific decisions");
});

test("unavailable totals stay unavailable instead of being represented as zero", async () => {
  const api = { get: jest.fn().mockResolvedValueOnce({ data: { members: 0 } }).mockResolvedValueOnce({ data: report }) };
  await act(async () => root.render(<ResearchNoticeSummary api={api} />));
  expect(host.querySelector('[role="alert"]').textContent).toContain("could not load");
  expect(host.querySelector("dd")).toBeNull();
  await act(async () => host.querySelector("button").click());
  expect(host.querySelector('[role="alert"]')).toBeNull();
  expect(host.textContent).toContain("7 reviewed the current main notice");
});

test.each([
  ["missing purpose", value => { delete value.scopes.staff_history; }],
  ["enabled above eligibility", value => { value.scopes.tone.enabled = 9; }],
  ["inconsistent review denominator", value => { value.reviewed = 10; }],
  ["invalid scope decisions", value => { value.scopes.couple_csv.decisions.decline = 99; }],
  ["wrong notice", value => { value.notice_version = "unknown"; }],
])("a %s response never appears as valid consent totals", async (_, corrupt) => {
  const value = JSON.parse(JSON.stringify(report)); corrupt(value);
  await act(async () => root.render(<ResearchNoticeSummary api={{ get: async () => ({ data: value }) }} />));
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(host.querySelector("dd")).toBeNull();
});

test("refresh aborts the previous read and an out-of-order result cannot replace newer counts", async () => {
  let finishOld;
  const api = { get: jest.fn().mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
    .mockResolvedValueOnce({ data: report }) };
  await act(async () => root.render(<ResearchNoticeSummary api={api} />));
  const firstSignal = api.get.mock.calls[0][1].signal;
  await act(async () => host.querySelector("button").click());
  expect(firstSignal.aborted).toBe(true);
  await act(async () => finishOld({ data: { ...report, members: 999 } }));
  expect(host.textContent).toContain("10 current members");
  expect(host.textContent).not.toContain("999");
});
