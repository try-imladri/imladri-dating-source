import { act } from "react";
import { createRoot } from "react-dom/client";
import MessageCostReceipts from "./MessageCostReceipts";

jest.mock("@/lib/api", () => ({errMsg:error => error.message}));

test.each([
  ["tone_intent", "couples", "Added an unsupported commitment or belief"],
  ["reply_tone_shape", "individual", "Unrecognized tone response"],
  ["reply_style_shape", "individual", "Incomplete reply styles"],
  ["reply_style_duplicate", "individual", "Repeated reply styles"],
  ["reply_intent", "individual", "Added an unsupported commitment or belief"],
])("Admin shows %s on its affected %s provider call", async (reason, workspace, label) => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.body.appendChild(document.createElement("div"));
  const root = createRoot(host);
  const unit = {id:"validation-receipt", workspace, kind:"exchange", status:"refunded"};
  const calls = [{id:"blocked", feature:workspace === "couples" ? "couples_tone" : "six_part",
    status:"succeeded", http_status:200, application_rejection:reason},
    {id:"valid", status:"succeeded", http_status:200, message_run:2}];
  const api = {get:async url => ({data:url.endsWith("/validation-receipt") ? {message:unit,calls} : {items:[unit]}})};
  try {
    await act(async () => root.render(<MessageCostReceipts api={api} start="2026-01-02" end="2026-01-02"/>));
    await act(async () => host.querySelector("button").click());
    await act(async () => [...host.querySelectorAll("button")].find(button => button.textContent.includes("exchange")).click());
    const rows = host.querySelectorAll("tbody tr");
    expect(rows[0].textContent).toContain("succeeded · 200");
    expect(rows[0].textContent).toContain("App rejected reply · "+label);
    expect(rows[1].textContent).not.toContain("App rejected reply");
  } finally {
    act(() => root.unmount());
    host.remove();
    delete global.IS_REACT_ACT_ENVIRONMENT;
  }
});

test("a billed provider success and the app's rejection appear together on the affected call", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.body.appendChild(document.createElement("div"));
  const root = createRoot(host);
  const unit = {id:"couples-receipt",workspace:"couples",kind:"couples_tone",status:"refunded"};
  const calls = [{id:"rejected",status:"succeeded",http_status:200,application_rejection:"tone_drafts"},
    {id:"accepted",status:"succeeded",http_status:200,message_run:2}];
  const api = {get:async url => ({data:url.endsWith("/couples-receipt") ? {message:unit,calls} : {items:[unit]}})};
  try {
    await act(async () => root.render(<MessageCostReceipts api={api} start="2026-01-02" end="2026-01-02"/>));
    await act(async () => host.querySelector("button").click());
    await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent.includes("couples_tone")).click());
    const rows = host.querySelectorAll("tbody tr");
    expect(rows[0].textContent).toContain("succeeded · 200");
    expect(rows[0].textContent).toContain("App rejected reply · Missing or repeated reply styles");
    expect(rows[1].textContent).not.toContain("App rejected reply");
  } finally {
    act(() => root.unmount());
    host.remove();
    delete global.IS_REACT_ACT_ENVIRONMENT;
  }
});

test("receipts open on demand and stale Individual calls disappear when switching to Couples", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.body.appendChild(document.createElement("div"));
  const root = createRoot(host);
  let finishIndividual;
  const unit = {id:"fixture-message",started_at:"2026-01-02T01:00:00Z",workspace:"individual",kind:"exchange",status:"settled"};
  const api = {get:jest.fn(url => {
    if (url.endsWith("/fixture-message")) return new Promise(resolve => {finishIndividual=resolve;});
    if (url.includes("workspace=couples")) return Promise.resolve({data:{items:[],next_cursor:null}});
    return Promise.resolve({data:{items:[unit],next_cursor:null}});
  })};
  try {
    await act(async () => root.render(<MessageCostReceipts api={api} start="2026-01-02" end="2026-01-02"/>));
    expect(api.get).not.toHaveBeenCalled();
    await act(async () => host.querySelector("button").click());
    const individual = [...host.querySelectorAll("button")].find(x => x.textContent.includes("exchange"));
    await act(async () => individual.click());
    await act(async () => {
      const select=host.querySelector("select");
      select.value="couples";
      select.dispatchEvent(new Event("change",{bubbles:true}));
    });
    await act(async () => finishIndividual({data:{message:unit,calls:[{id:"a",feature:"old private call"}]}}));
    expect(host.textContent).toContain("No message receipts started");
    expect(host.textContent).not.toContain("old private call");
  } finally {
    act(() => root.unmount());
    host.remove();
    delete global.IS_REACT_ACT_ENVIRONMENT;
  }
});
