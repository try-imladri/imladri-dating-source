import { act } from "react";
import { createRoot } from "react-dom/client";
import WorkspaceTrends, { CouplesReport } from "./WorkspaceTrends";
jest.mock("@/lib/api", () => ({ errMsg: () => "Request failed" }));

jest.mock("recharts", () => ({
  ResponsiveContainer: () => null, LineChart: () => null, Line: () => null, CartesianGrid: () => null,
  Tooltip: () => null, XAxis: () => null, YAxis: () => null,
}));

const totals = { signups: 0, reads: 0, outcomes: 0, practice: 0, couples_opened: 0, couples_sample_completed: 0 };
function payload(days) {
  return { data: {
    days, start: "2026-11-01", end: "2026-11-07", totals, daily: [],
    outcomes: { minimum: 10, available: false, options: [], respondents: null },
    everyday: { minimum: 10, questions: [{ id: "s1", prompt: "An everyday preference", current: false, available: false, respondents: null, options: [] }] },
  } };
}

test.each([
  ["Activity", WorkspaceTrends, ["/admin/trends"]],
  ["Couples", CouplesReport, ["/admin/couples/analytics"]],
])("%s loads its own endpoints and exports the selected period", async (_, Report, endpoints) => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const api = { get: jest.fn(() => new Promise(() => {})) };
  const host = document.createElement("div");
  const root = createRoot(host);
  try {
    await act(async () => root.render(<Report api={api} />));
    expect(api.get.mock.calls.map(([url]) => url)).toEqual(endpoints);
    const range = host.querySelector("select");
    expect(range.value).toBe("30");
    await act(async () => {
      range.value = "90";
      range.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(api.get.mock.calls.slice(endpoints.length).map(([url]) => url)).toEqual(endpoints);
    expect(api.get).toHaveBeenCalledWith(endpoints[0], expect.objectContaining({ params: { days: 90 } }));
    const download = host.querySelector('[data-testid="trends-download"]');
    expect(download.textContent).toBe("Download workspace report (CSV)");
    await act(async () => download.click());
    expect(api.get).toHaveBeenLastCalledWith("/admin/reports/workspace.csv", { params: { days: 90 }, responseType: "blob" });
  } finally {
    act(() => root.unmount());
  }
});

test("comparison uses adjacent complete UTC days and skips the partial current day", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const result = payload(7);
  const window = [1, 1, 1, 2, 2, 2, 60];
  result.data.totals = { ...totals, reads: 69 };
  result.data.daily = window.map((count, i) => ({ ...totals, day: "2026-11-0" + (i + 1), reads: count }));
  const host = document.createElement("div");
  const root = createRoot(host);
  const api = { get: jest.fn(async (_, { params }) => params.days === 7 ? result : payload(30)) };
  try {
    await act(async () => root.render(<WorkspaceTrends api={api} />));
    const selector = host.querySelector('[data-testid="trends-period"]');
    await act(async () => { selector.value = "7"; selector.dispatchEvent(new Event("change", { bubbles: true })); });
    const comparison = host.querySelector('[data-testid="trend-change"]').textContent;
    expect(comparison).toContain("3 more in the last 3 complete days");
    expect(comparison).toContain("Nov 4 – Nov 6");
    expect(comparison).not.toContain("Nov 7");
  } finally {
    act(() => root.unmount());
  }
});

test("every measure published by the report is selectable with its definition and daily counts", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const result = payload(30);
  result.data.totals = { reads: 0, future_measure: 4 };
  result.data.measures = [
    { key: "reads", label: "Individual reads", unit: "Saved reads." },
    { key: "future_measure", label: "Newly connected measure", unit: "Four saved records in this period." },
  ];
  result.data.daily = [{ day: "2026-11-07", reads: 0, future_measure: 4 }];
  const host = document.createElement("div");
  const root = createRoot(host);
  const api = { get: jest.fn(async () => result) };
  try {
    await act(async () => root.render(<WorkspaceTrends api={api} />));
    const select = host.querySelector('[data-testid="trend-measure"]');
    expect([...select.options].map(option => option.text)).toEqual(["Individual reads", "Newly connected measure"]);
    await act(async () => { select.value = "future_measure"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(host.textContent).toContain("4 in this period");
    expect(host.textContent).toContain("Four saved records in this period.");
    expect(host.querySelector("tbody tr").textContent).toBe("2026-11-074");
  } finally {
    act(() => root.unmount());
  }
});

test("a slow response from an earlier period cannot replace the selected results", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const pending = {};
  const api = { get: jest.fn((url, { params }) => new Promise(resolve => { pending[params.days] = resolve; })) };
  const host = document.createElement("div");
  const root = createRoot(host);
  try {
    await act(async () => root.render(<WorkspaceTrends api={api} />));
    const range = host.querySelector('[data-testid="trends-period"]');
    await act(async () => {
      range.value = "7";
      range.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => pending[7](payload(7)));
    expect(host.textContent).toContain("Reporting is paused");
    const former = payload(30);
    former.data.everyday.questions[0].prompt = "Old window";
    await act(async () => pending[30](former));
    expect(range.value).toBe("7");
    expect(host.textContent).toContain("An everyday preference");
    expect(host.textContent).not.toContain("Old window");
    expect(host.textContent).not.toContain("Loading trends");
  } finally {
    act(() => root.unmount());
  }
});
