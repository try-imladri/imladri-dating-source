import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import Plans from "./Plans";

jest.mock("react-router-dom", () => {
  global.TextEncoder = require("util").TextEncoder;
  return jest.requireActual("react-router");
});

test("founding dating enrollment does not advertise a paid checkout or the legacy automatic-charge introduction", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.body.appendChild(document.createElement("div"));
  const root = createRoot(host);
  try {
    await act(async () => root.render(<MemoryRouter><Plans /></MemoryRouter>));
    expect(host.querySelector('[data-testid="dating-founding-offer"]').textContent).toContain("No card required");
    expect(host.textContent).toContain("does not guarantee a match or date");
    expect(host.textContent).not.toContain("$19");
    expect(host.textContent).not.toContain("10 free AI messages");
    expect(host.querySelector('[data-testid="founding-join"]').getAttribute("href")).toBe("/dating?origin=plans");
    expect(host.querySelector('a[href="/legacy/plans"]')).not.toBeNull();
    expect(host.querySelector('a[href="/app/settings/upgrade"]')).not.toBeNull();
  } finally {
    act(() => root.unmount());
    host.remove();
  }
});
