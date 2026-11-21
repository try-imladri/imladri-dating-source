import { act } from "react";
import { createRoot } from "react-dom/client";
import { useOutletContext } from "react-router-dom";
import PrepareTab from "./PrepareTab";

jest.mock("react-router-dom", () => ({ useOutletContext: jest.fn() }));
jest.mock("@/lib/api", () => ({ errMsg: () => "Request failed" }));

test("Game Plans show conditional next steps and respect a no-contact decision", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement("div");
  const root = createRoot(host);
  const record = { id: "regular", situation: "Schedule our next meeting", created_at: "2026-05-20" };
  const data = [
    { ...record, plan: { opening_line: "When could we meet?", reactions: [
      { reaction: "They offer Friday.", likelihood: 87, what_it_tells_you: "They secretly agree.", how_to_handle: "Check your calendar." }
    ], avoid: [], exit_line: "We can revisit this later." } },
    { ...record, id: "boundary", plan: { contact_boundary: true, opening_line: null, exit_line: null,
      avoid: ["Leave the next contact to them."], reactions: [] } }
  ];
  const api = { get: jest.fn().mockResolvedValue({ data }) };
  useOutletContext.mockReturnValue({ api, me: { usage: { unlimited: true } } });
  try {
    await act(async () => root.render(<PrepareTab api={api} p={{ id: "jo", nickname: "Jo" }} />));
    const regular = host.querySelector('[data-testid="prep-regular"]');
    expect(regular.textContent).toContain("They offer Friday.");
    expect(regular.textContent).toContain("Check your calendar.");
    expect(regular.textContent).not.toMatch(/87|secretly/);
    const boundary = host.querySelector('[data-testid="prep-boundary"]');
    expect(boundary.textContent).toContain("Leave the next contact to them.");
    expect(boundary.textContent).not.toMatch(/Opening line|Ways the conversation could go|If it goes badly/);
    expect(boundary.querySelector('[data-testid="prep-outcome-button-boundary"]')).toBeTruthy();
  } finally {
    await act(async () => root.unmount());
  }
});
