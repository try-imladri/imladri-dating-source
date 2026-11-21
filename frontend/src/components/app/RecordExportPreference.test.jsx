import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { RecordExportPreference } from "./RecordExportPreference";

const memberChoice={enabled:false,revision:0,version:"row-member-v1"};
const coupleChoice={enabled:false,revision:0,version:"row-couple-v1",pair_id:"pair-A"};
const me={user_id:"actor-A",role:"member",research_exports:{member:memberChoice,couple:coupleChoice}};
function Screen({api,member=me,scope="member",pairId}) {
  const [current,setCurrent]=useState(member);
  return <RecordExportPreference scope={scope} me={current} setMe={setCurrent} api={api} pairId={pairId}/>;
}
let host,root;
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;host=document.createElement("div");root=createRoot(host);});
afterEach(()=>{act(()=>root.unmount());delete global.IS_REACT_ACT_ENVIRONMENT;});
const toggle=()=>act(async()=>host.querySelector("input").click());
test("permission starts off and enabling and revoking each bind to the displayed member and revision",async()=>{
  const api={patch:jest.fn(async(_,body)=>({data:{...memberChoice,enabled:body.enabled,revision:body.revision+1}}))};
  await act(async()=>root.render(<Screen api={api}/>));
  expect(host.querySelector("input").checked).toBe(false);
  await toggle();
  expect(api.patch).toHaveBeenCalledWith("/me/research-exports/member",
    {enabled:true,adult_confirmed:true,expected_member:"actor-A",version:"row-member-v1",revision:0},expect.anything());
  expect(host.querySelector("input").checked).toBe(true);
  await toggle();
  expect(api.patch.mock.calls[1][1]).toMatchObject({adult_confirmed:false,revision:1,enabled:false});
  expect(host.querySelector("input").checked).toBe(false);
});
test("a changed active couple refreshes consent and an older pair cannot authorize the new pair",async()=>{
  const next={...coupleChoice,pair_id:"pair-B",revision:5,previous_pair_grant:true};
  const api={get:jest.fn(async()=>({data:{...me,research_exports:{...me.research_exports,couple:next}}})),
    patch:jest.fn(async(_,body)=>({data:{...next,enabled:body.enabled,revision:6,previous_pair_grant:false}}))};
  await act(async()=>root.render(<Screen api={api} scope="couple" pairId="pair-B"/>));
  expect(api.get).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain("Remove previous couple");
  expect(host.querySelector("input").checked).toBe(false);
  await toggle();
  expect(api.patch.mock.calls[0][1]).toMatchObject({expected_member:"actor-A",expected_pair_id:"pair-B",revision:5});
});
test("a stale grant updates from the server and no permission is silently enabled",async()=>{
  const api={patch:jest.fn(async()=>{throw Object.assign(new Error(),{response:{status:409,data:{detail:"Review the current permission."}}});}),
    get:jest.fn(async()=>({data:{...me,research_exports:{...me.research_exports,member:{...memberChoice,revision:2}}}}))};
  await act(async()=>root.render(<Screen api={api}/>));
  await toggle();
  expect(host.querySelector("input").checked).toBe(false);
  expect(host.textContent).toContain("Review the current permission.");
});
