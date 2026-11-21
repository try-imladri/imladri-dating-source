import { act } from "react";
import { createRoot } from "react-dom/client";
import AdminMetrics from "./AdminMetrics";
jest.mock("@/lib/api", () => ({ errMsg: () => "Request failed" }));

jest.mock("recharts", () => Object.fromEntries(
  ["ResponsiveContainer", "BarChart", "Bar", "XAxis", "YAxis", "Tooltip", "LineChart", "Line", "CartesianGrid", "Cell"]
    .map(name => [name, () => null])
));

test("quiz details show current section populations and distinct halfway and completion thresholds", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const metrics = {
    upload_completion: {}, activation: [{name:"First read", value:2}], as_of:"2026-03-01T22:30:00-05:00",
    signups_by_day:[{day:"2025-12-15",count:1},{day:"2026-02-28",count:1},{day:"2026-03-02",count:2}], quiz: { users: 4, sections: {
      short: { questions: 12, started: 4, halfway_at: 6, halfway: 4, complete: 2 },
      extended: { questions: 36, started: 4, halfway_at: 18, halfway: 3, complete: 1 },
      further: { questions: 60, started: 2, halfway_at: 30, halfway: 1, complete: 0 },
    } },
  };
  const api = { get: jest.fn().mockResolvedValue({ data: metrics }) };
  try {
    await act(async () => root.render(<AdminMetrics api={api} />));
    const history = host.querySelector('[data-testid="metric-signups-time"]');
    const period = history.querySelector("select");
    await act(async () => { period.value = "7"; period.dispatchEvent(new Event("change", {bubbles:true})); });
    const dayRows = [...history.querySelectorAll("tbody tr")].map(row => [...row.cells].map(cell => cell.textContent));
    expect(dayRows).toHaveLength(7);
    expect(dayRows.slice(-3)).toEqual([["2026-02-28","1"],["2026-03-01","0"],["2026-03-02","2"]]);
    expect(history.textContent).toContain("3 signups through 2026-03-02 UTC");
    expect(history.textContent).toContain("2025-12-15 UTC");
    expect(host.querySelector('[data-testid="metric-onboarding"] ul').textContent).toContain("First read · 2");
    const card = host.querySelector('[data-testid="metric-quiz"]');
    expect(card.textContent).toContain("Basics completed50%");
    await act(async () => card.click());
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog.textContent).toContain("4 eligible members");
    expect([...dialog.querySelectorAll("tbody tr")].map(row => [...row.cells].map(cell => cell.textContent))).toEqual([
      ["The basics", "4 (100%)", "4 (100%)6 answers", "2 (50%)12 answers"],
      ["Go deeper", "4 (100%)", "3 (75%)18 answers", "1 (25%)36 answers"],
      ["Values & communication", "2 (50%)", "1 (25%)30 answers", "0 (0%)60 answers"],
    ]);
  } finally {
    await act(async () => root.unmount());
    host.remove();
    delete global.IS_REACT_ACT_ENVIRONMENT;
  }
});
