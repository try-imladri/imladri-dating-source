import { act } from "react";
import { createRoot } from "react-dom/client";
import FigureEvidence from "./FigureEvidence";

jest.mock("@/lib/api", () => ({errMsg: error => error.message}));

test("staff can find a first-review profile, record its source observation and retrieve the decision", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement("div");
  const root = createRoot(host);
  const rows = [
    { name:"Assessed", slug:"assessed", status:"one", readers:[], version:"old" },
    { name:"New figure", slug:"new-figure", status:"unassessed", readers:[], version:"current" },
  ];
  const decision = { id:"saved", result:"retain", note:"Supported by two dated speeches." };
  const api = { get:jest.fn(async () => ({data:rows})), put:jest.fn(async () => ({data:decision})) };
  const button = label => [...host.querySelectorAll("button")].find(el => el.textContent === label);
  const filter = async value => act(async () => {
    const field = host.querySelector('[aria-label="Review status"]');
    field.value = value;
    field.dispatchEvent(new Event("change", {bubbles:true}));
  });
  try {
    await act(async () => root.render(<FigureEvidence api={api} openEditor={() => {}} />));
    await act(async () => button("Reload evidence").click());
    await filter("unassessed");
    expect(host.textContent).toContain("New figure · No prior reader assessment");
    expect(host.querySelector('a').getAttribute("href")).toBe("/figures/new-figure#communicates");
    await act(async () => {
      const field = host.querySelector("textarea");
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(field, decision.note);
      field.dispatchEvent(new Event("input", {bubbles:true}));
    });
    await act(async () => button("Retain lead").click());
    expect(api.put).toHaveBeenCalledWith("/admin/editorial/figure-reviews/new-figure",
      {version:"current", previous_id:null, result:"retain", note:decision.note});
    expect(host.textContent).toContain("0 of 2 profiles");
    await filter("retain");
    expect(host.textContent).toContain(decision.note);
  } finally {
    act(() => root.unmount());
    delete global.IS_REACT_ACT_ENVIRONMENT;
  }
});
