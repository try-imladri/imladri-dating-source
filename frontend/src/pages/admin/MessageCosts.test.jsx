import { act } from "react";
import { createRoot } from "react-dom/client";
import MessageCosts from "./MessageCosts";
jest.mock("@/lib/api",()=>({errMsg:e=>e?.response?.data?.detail || "Error"}));

const empty = {messages:0,calls:0,retries:0,background:0,preparation:0,reconciled_calls:0,
  pending_background:0,credits_without_calls:0,actual_per_100_usd:null,estimated_per_100_usd:null};

async function mounted(api, check) {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.body.appendChild(document.createElement("div"));
  const root = createRoot(host);
  try {
    await act(async()=>root.render(<MessageCosts api={api}/>));
    await check(host);
  } finally {
    act(()=>root.unmount()); host.remove(); delete global.IS_REACT_ACT_ENVIRONMENT;
  }
}

function report(cohort, overrides={}) {
  return {start:"2026-11-08",end:"2026-11-09",key_name:"dedicated-test",dedicated:true,
    cohorts:{individual:cohort,couples:empty,all:cohort},coverage:[],keywide_per_100_usd:null,...overrides};
}

test("empty credits keep actuals unavailable and upload the original ZIP through the authenticated client", async ()=>{
  const api={get:jest.fn(async()=>({data:report(empty)})),post:jest.fn(async()=>({data:{days:1,changed:0,start:"2026-11-08",end:"2026-11-08"}}))};
  await mounted(api, async host=>{
    expect(host.textContent).toContain("No settled message credits");
    expect(host.textContent).not.toContain("$0.00");
    expect(host.textContent).toContain("dedicated-test");
    const input=host.querySelector('input[type="file"]');
    const file=new File(["data"],"usage_data.zip",{type:"application/zip"});
    Object.defineProperty(input,"files",{configurable:true,value:[file]});
    await act(async()=>input.dispatchEvent(new Event("change",{bubbles:true})));
    await act(async()=>input.closest("form").dispatchEvent(new Event("submit",{bubbles:true,cancelable:true})));
    expect(api.post).toHaveBeenCalledWith("/admin/prompts/message-costs/statement",file,{headers:{"Content-Type":"application/zip"}});
    expect(host.textContent).toContain("Updated 0");
    expect(api.get).toHaveBeenCalledTimes(2);
  });
});

test("separates linked statement costs, whole-key spend, and open coverage", async ()=>{
  const row={...empty,messages:1,calls:4,retries:1,background:1,preparation:1,reconciled_calls:4,
    actual_per_100_usd:.22245,estimated_per_100_usd:[.11,.23]};
  const api={get:async()=>({data:report(row,{keywide_per_100_usd:.5,coverage:[{day:"2026-11-09",calls:6,
    provider_requests:6,billed_nusd:5_000_000,status:"matched",work:{refunded:{attempts:1,billed_nusd:60_000}}}]})})};
  await mounted(api, async host=>{
    expect(host.textContent).toContain("$0.22245");
    expect(host.textContent).toContain("Whole-key spend per 100 settled credits · $0.50");
    expect(host.textContent).toContain("Refunded messages · 1 call");
    expect(host.textContent).toContain("Matched");
  });
});

test("identifies internal QA in key coverage when there are no settled customer messages", async ()=>{
  const coverage=[{day:"2026-11-09",calls:6,status:"missing",
    work:{internal_qa:{attempts:5},unassigned:{attempts:1}}}];
  const api={get:async()=>({data:report(empty,{coverage})})};
  await mounted(api, async host=>{
    expect(host.textContent).toContain("Internal QA · 5 calls");
    expect(host.textContent).toContain("Unassigned messages · 1 call");
    expect(host.textContent).toContain("No settled message credits");
    expect(host.textContent).toContain("Whole-key spend per 100 settled credits · —");
  });
});

test("ignores stale responses after a date change", async ()=>{
  let resolveOld;
  const wait = new Promise(resolve=>{resolveOld=resolve;});
  const api={get:jest.fn().mockReturnValueOnce(wait).mockResolvedValueOnce({data:report(empty,{dedicated:false})})};
  await mounted(api, async host=>{
    const select=host.querySelector("select");
    await act(async()=>{select.value="1";select.dispatchEvent(new Event("change",{bubbles:true}));});
    await act(async()=>resolveOld({data:report({...empty,messages:900})}));
    expect(host.textContent).toContain("No settled message credits");
    expect(host.textContent).not.toContain("900");
    expect(host.querySelector('input[type="file"]')).toBeNull();
  });
});
