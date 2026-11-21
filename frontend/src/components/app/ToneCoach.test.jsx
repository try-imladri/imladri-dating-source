import { act } from "react";
import { createRoot } from "react-dom/client";
import { IndividualTone } from "./ToneCoach";

jest.mock("@/lib/api", () => ({ errMsg: e => e.message }));
const drafts = {
  warm:"I’d love to see you. Does Friday work?", neutral:"Does Friday work?",
  direct:"Can you confirm Friday?", playful:"Is Friday still pencilled in?", cool:"Friday still on?",
};
let host, root, api, feedback, denied;
const ex = { id:"exchange", response:{}, conversation:[{speaker:"them",text:"Does six work for you?"}],
  tone:{status:"estimated", incoming:"neutral", confidence:"limited", explanation:"They ask a practical question.",
    evidence:[0], baseline_evidence:[{exchange_id:"old",index:0}], intent:"Confirm Friday.", drafts} };
const open = item => act(async () => root.render(<IndividualTone ex={item} api={api} />));
const click = label => act(async () => [...host.querySelectorAll("button")].find(el => el.textContent === label).click());
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div"); root = createRoot(host); feedback = {}; denied = false;
  Object.defineProperty(navigator, "clipboard", { configurable:true, value:{writeText:jest.fn(async () => {})} });
  api = {get:jest.fn(async () => ({data:["I will get back to you Friday."]})), post:jest.fn(async (_,body) => {
    if (denied) throw Object.assign(new Error("This thread was withdrawn."), {response:{status:409}});
    if (body.action === "select") feedback.style = body.style;
    if (body.action === "fit") Object.assign(feedback, {fit:body.fit,note:body.note});
    if (body.action === "clear_fit") { delete feedback.fit; delete feedback.note; }
    if (body.action === "sent") Object.assign(feedback, {sent:body.value,sent_style:body.style});
    if (body.action === "helped") feedback.helped = body.value;
    if (body.action === "clear_helped") delete feedback.helped;
    if (body.action === "clear_sent") { delete feedback.sent; delete feedback.sent_style; delete feedback.helped; }
    return {data:{...feedback}};
  })};
});
afterEach(() => {act(() => root.unmount());delete navigator.clipboard;});

test("an evidence-linked read precedes style choice, copy and separately reported outcomes", async () => {
  await open(ex);
  expect(host.textContent).toContain("Does six work for you?");
  expect(host.textContent).toContain("Confirm Friday.");
  Object.values(drafts).forEach(draft => expect(host.textContent).not.toContain(draft));
  await click("See earlier messages");
  expect(host.textContent).toContain("I will get back to you Friday.");
  await click("Warm"); expect(host.textContent).toContain(drafts.warm);
  await click("Cool or reserved"); expect(host.textContent).toContain(drafts.cool);
  expect(host.textContent).not.toContain(drafts.warm);
  await click("Copy reply"); expect(navigator.clipboard.writeText).toHaveBeenCalledWith(drafts.cool);
  expect(api.post.mock.calls.some(([,v]) => v.action === "sent")).toBe(false);
  await click("I sent this"); await click("Yes");
  expect(feedback).toMatchObject({style:"cool",sent:true,sent_style:"cool",helped:true});
  await click("That’s not how I read it"); await click("Save correction");
  expect(feedback.fit).toBe("corrected");
  await click("Remove my tone rating");
  expect(feedback).not.toHaveProperty("fit");
  expect(feedback).toMatchObject({ sent_style:"cool", helped:true });
  expect(host.textContent).not.toContain("Remove my tone rating");
});

test("a withdrawn read vanishes before a previously selected draft can reach the clipboard", async () => {
  await open(ex); await click("Warm"); denied = true;
  await click("Copy reply");
  expect(navigator.clipboard.writeText).not.toHaveBeenCalled();
  expect(host.textContent).toBe("This thread was withdrawn.");
});

test("changing exchanges during a pending permission check abandons the old clipboard action", async () => {
  await open(ex);
  await click("Warm");
  let finish;
  api.post.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await click("Copy reply");
  await open({ ...ex, id: "other" });
  await act(async () => finish({ data: { style: "warm" } }));
  expect(navigator.clipboard.writeText).not.toHaveBeenCalled();
  expect(api.post.mock.calls.some(([, body]) => body.action === "copy")).toBe(false);
});

test("a member can withdraw a negative helpfulness rating or their entire send response", async () => {
  await open(ex); await click("Warm"); await click("I sent this"); await click("No");
  await click("Remove my helpfulness rating");
  expect(api.post).toHaveBeenLastCalledWith("/exchanges/exchange/tone-feedback", {action:"clear_helped",style:"warm"});
  expect(feedback).toMatchObject({sent:true, sent_style:"warm"});
  expect(feedback).not.toHaveProperty("helped");
  await click("Yes"); await click("Remove my send response");
  expect(api.post).toHaveBeenLastCalledWith("/exchanges/exchange/tone-feedback", {action:"clear_sent",style:"warm",value:true});
  expect(feedback).not.toHaveProperty("helped");
  expect(feedback).not.toHaveProperty("sent");
  expect(host.textContent).not.toContain("Thinking back after");
  await click("I didn’t send a reply"); await click("Remove my send response");
  expect(feedback).not.toHaveProperty("sent");
});

test("removing an earlier supporting exchange retires the visible read and wording", async () => {
  await open(ex);
  await click("Warm");
  api.get.mockResolvedValueOnce({data:[]});
  await click("See earlier messages");
  expect(host.querySelector('[role="alert"]').textContent).toContain("Earlier context");
  expect(host.textContent).not.toContain(drafts.warm);
  expect(host.querySelector("button")).toBeNull();
});

test("historical or restricted records do not render stale style options",async () => {
  await open({...ex,response:{contact_boundary:true}}); expect(host.textContent).toBe("");
  await open({...ex,tone_invalidated:true}); expect(host.textContent).toContain("Earlier context");
  await open({id:"old", response:{}}); expect(host.textContent).toBe("");
});
