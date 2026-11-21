import { act } from "react";
import { createRoot } from "react-dom/client";
import { SixPart } from "./Advice";
import { ActionPicker } from "./ShowWhy";

jest.mock("react-router-dom", () => ({ useNavigate: () => jest.fn() }));
jest.mock("@/lib/api", () => ({ errMsg: () => "Request failed" }));

test("a contact-boundary card shows the private next step and never offers a script to copy or rehearse", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement("div");
  const root = createRoot(host);
  const r = { contact_boundary: true, best_move: "Write down your thoughts and keep them unsent.",
    what_to_say: "Obsolete outbound script", actions: [{ label: "Write privately", detail: "Use a notebook.", say: "Obsolete copy action" }] };
  try {
    await act(async () => root.render(<><SixPart r={r}/><ActionPicker ex={{ id: "boundary", response: r }} /></>));
    expect(host.textContent).toContain("keep them unsent");
    expect(host.textContent).toContain("Write privately");
    expect(host.textContent).not.toMatch(/Obsolete|word for word|Practise this/);
    await act(async () => root.render(<SixPart r={{what_to_say: "See you Thursday."}} />));
    expect(host.textContent).toContain("See you Thursday.");
    expect(host.textContent).not.toMatch(/undefined|null/);
  } finally {
    await act(async () => root.unmount());
  }
});
