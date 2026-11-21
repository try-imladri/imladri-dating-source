import { act } from "react";
import { createRoot } from "react-dom/client";
import MembershipReconciliation from "./MembershipReconciliation";
import PromptVersions from "./PromptVersions";
import SupportInbox from "./SupportInbox";

jest.mock("@/lib/api",()=>({errMsg:e=>e.message}));
let host,root;
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;host=document.createElement("div");root=createRoot(host);});
afterEach(()=>{act(()=>root.unmount());delete global.IS_REACT_ACT_ENVIRONMENT;});
const button = value => [...host.querySelectorAll("button")].find(el=>el.textContent===value);
const click = el => act(async()=>el.click());
const change = (el,value) => act(async()=>{el.value=value;el.dispatchEvent(new Event("change",{bubbles:true}));});

test("membership reconciles on demand and displays linked refunds and unknown Stripe access",async()=>{
  const row={rows:1,amount:-300,fee:0,net:-300};
  const data={environment:"live",as_of:"2026-11-09T10:00:00Z",entitlements:{paid:1,sponsored_only:1,by_plan:{plus_250:1}},
    subscriptions:2000,refills:1200,stripe:{state:"complete",categories:{refund:row},products:{subscriptions:{},refills:{refund:row},unlinked:{}},other_currency:0}};
  const api={get:jest.fn().mockResolvedValueOnce({data}).mockResolvedValueOnce({data:{...data,stripe:{state:"unavailable"}}})};
  await act(async()=>root.render(<MembershipReconciliation api={api}/>));
  expect(api.get).not.toHaveBeenCalled();
  await click(button("Reconcile month"));
  expect(host.textContent).toContain("1 sponsored Couples-only seats");
  expect(host.textContent).toContain("$20.00 net subscriptions");
  expect(host.textContent).toContain("-$3.00");
  await click(button("Reconcile month"));
  expect(host.textContent).toContain("Fees and refunds are unknown");
  expect(host.querySelector("table")).toBeNull();
});

test("unresolved payment adjustments never render missing app net revenue as zero",async()=>{
  const data={environment:'live',entitlements:{paid:1,sponsored_only:0,by_plan:{}},subscriptions:null,refills:null,
    net_revenue_verified:false,stripe:{state:'unavailable'}};
  const api={get:jest.fn(async()=>({data}))};
  await act(async()=>root.render(<MembershipReconciliation api={api}/>));
  await click(button('Reconcile month'));
  expect(host.textContent).toContain('Pending reconciliation net subscriptions');
  expect(host.textContent).toContain('Net app revenue is unverified');
  expect(host.textContent).not.toContain('$0.00 net subscriptions');
});

test("staff can record version-bound review and deliberately restore one retained version",async()=>{
  const data={protocol:"fixture-v1",cases:{boundary:"Respect no contact."},versions:[{revision:"current",combinations:35},{revision:"initial"}]};
  const api={get:jest.fn(async()=>({data})),post:jest.fn(async()=>({data:{}}))};
  const onRestore=jest.fn();
  await act(async()=>root.render(<PromptVersions api={api} onRestore={onRestore}/>));
  await change(host.querySelectorAll("select")[0],"pass");
  await click(button("Save review for this version"));
  expect(api.post).toHaveBeenCalledWith("/admin/prompts/review",{revision:"current",answers:{boundary:"pass"}});
  expect(button("Publish selected version").disabled).toBe(true);
  await change(host.querySelectorAll("select")[1],"initial");
  await click(button("Publish selected version"));
  expect(api.post).toHaveBeenCalledWith("/admin/prompts/rollback",{current:"current",target:"initial"});
  expect(onRestore).toHaveBeenCalledTimes(1);
});

test("a draft for the previous support message cannot be posted to a reopened thread", async () => {
  let round = 0;
  const row = {id:"ticket", category:"Account", message:"First question", status:"open", created_at:"2026-11-09T12:00:00Z"};
  const api = {get: jest.fn(async url => ({data: url.endsWith("/summary") ? {open:1, answered_with_time:0} : [{...row, round}]})),
    put: jest.fn(async () => { round++; throw Object.assign(new Error("Refresh this thread"), {response:{status:409}}); })};
  await act(async () => root.render(<SupportInbox api={api} />));
  const draft = host.querySelector("textarea");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(draft, "First response");
    draft.dispatchEvent(new Event("input", {bubbles:true}));
  });
  await click(button("Post reply"));
  expect(api.put).toHaveBeenCalledWith("/admin/support/ticket", {answer:"First response", round:0});
  expect(host.querySelector("textarea").value).toBe("");
  expect(button("Post reply").disabled).toBe(true);
  expect(host.querySelector('[role="alert"]').textContent).toContain("Refresh this thread");
});
