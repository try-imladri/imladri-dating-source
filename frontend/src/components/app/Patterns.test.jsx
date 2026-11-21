import { act } from "react";
import { createRoot } from "react-dom/client";
import { PatternsPanel } from "./Patterns";

jest.mock("react-router-dom", () => ({
  useOutletContext: () => ({ me: { usage: { unlimited: true } } }),
}));
jest.mock("@/lib/api", () => ({ errMsg: error => error.message }));

test.each([true, false])("a member reviews and manages a private observation with Individual scanning allowed=%s", async allowScan => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.body.appendChild(document.createElement("div"));
  const root = createRoot(host);
  const text = "You paused before answering.";
  let confirmed = false;
  const api = {
    get: jest.fn(async () => ({ data: [
      { id: "mine", text, evidence_status: confirmed ? "confirmed" : "tentative", example_count: 0, practice_count: 2 },
      { id: "twice", text: "You asked to check in.", evidence_status: "supported", example_count: 2 },
    ] })),
    put: jest.fn(async (_, body) => { confirmed = body.confirmed; }),
  };
  const row = id => host.querySelector(`[data-testid="pattern-${id}"]`);
  const click = () => act(async () => row("mine").querySelector('[data-testid="pattern-confirm"]').click());
  try {
    await act(async () => root.render(<PatternsPanel api={api} allowScan={allowScan} />));
    expect(Boolean(host.querySelector('[data-testid="patterns-scan"]'))).toBe(allowScan);
    if (!allowScan) expect(host.textContent).toContain("Your saved observations stay private");
    expect(row("mine").textContent).toContain("Tentative observation");
    expect(row("mine").textContent).toContain("2 rehearsals");
    expect(row("twice").textContent).toContain("Seen across conversations");
    await click();
    expect(api.put).toHaveBeenLastCalledWith("/patterns/mine/confirmation", { text, confirmed: true });
    expect(row("mine").textContent).toContain("Confirmed by you");
    await click();
    expect(api.put).toHaveBeenLastCalledWith("/patterns/mine/confirmation", { text, confirmed: false });
    expect(row("mine").textContent).toContain("Tentative observation");
  } finally {
    act(() => root.unmount());
    host.remove();
    delete global.IS_REACT_ACT_ENVIRONMENT;
  }
});
