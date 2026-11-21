import { act } from "react";
import { createRoot } from "react-dom/client";
import WaitlistSignups from "./WaitlistSignups";
jest.mock("@/lib/api", () => ({ errMsg: () => "Could not load signups" }));

test("the Overview tile loads signup details on demand and paginates without exposing them behind the dialog", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const api = { get: jest.fn((_url, { params }) => Promise.resolve({ data: {
    entries: [{ email: params.after ? "older@example.com" : "latest@example.com", origin: "demo", ts: "2026-11-08T12:00:00Z" }],
    next: params.after ? null : "next-page",
  } })) };
  try {
    await act(async () => root.render(<WaitlistSignups api={api} total={26} />));
    expect(api.get).not.toHaveBeenCalled();
    await act(async () => host.querySelector('[data-testid="metric-waitlist"]').click());
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog.textContent).toContain("latest@example.com");
    const button = text => [...dialog.querySelectorAll("button")].find(el => el.textContent === text);
    await act(async () => button("Next").click());
    expect(dialog.textContent).toContain("older@example.com");
    expect(dialog.textContent).not.toContain("latest@example.com");
    await act(async () => button("Previous").click());
    expect(dialog.textContent).toContain("latest@example.com");
    await act(async () => button("Close").click());
    expect(document.body.textContent).not.toContain("latest@example.com");
  } finally {
    await act(async () => root.unmount());
    host.remove();
    delete global.IS_REACT_ACT_ENVIRONMENT;
  }
});
