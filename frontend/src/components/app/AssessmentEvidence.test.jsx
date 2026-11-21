import { act } from "react";
import { createRoot } from "react-dom/client";
import RepeatBasics from "./RepeatBasics";
import { SentReplyFollowup } from "./ToneCoach";
import AssessmentEvidence from "@/pages/admin/AssessmentEvidence";
import AssessmentMethods from "@/components/public/AssessmentMethods";
import { publicApi } from "@/lib/api";

jest.mock("@/lib/api", () => ({publicApi:{get:jest.fn()},errMsg:e=>e.message}));
let host,root;
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;host=document.createElement("div");root=createRoot(host);});
afterEach(()=>{act(()=>root.unmount()); delete global.IS_REACT_ACT_ENVIRONMENT;});
const button = label => [...host.querySelectorAll("button")].find(node=>node.textContent === label);
const me = {user_id:"member",questionnaire_research:{enabled:true}};

test("the voluntary retest starts blank, captures explicit permission and never patches the regular profile",async()=>{
  const initial={phase:"first",instrument:"current",revision:2,consent_revision:3,questions:[{id:"s1",q:"Current question",labels:["One","Two"]}]};
  const api={get:jest.fn(async()=>({data:initial})),post:jest.fn(async()=>({data:{phase:"waiting",revision:3,has_record:true,opens:"2026-08-07T12:00:00Z",closes:"2026-08-28T12:00:00Z"}})),
    delete:jest.fn(async()=>({data:{ok:true}}))};
  await act(async()=>root.render(<RepeatBasics api={api} me={me}/>));
  await act(async()=>button("Open optional repeat").click());
  expect(host.querySelectorAll("input:checked").length).toBe(0);
  await act(async()=>host.querySelectorAll('input[type="radio"]')[1].click());
  expect(button("Submit first set").disabled).toBe(true);
  await act(async()=>host.querySelector('input[type="checkbox"]').click());
  await act(async()=>host.querySelector("form").dispatchEvent(new Event("submit",{bubbles:true,cancelable:true})));
  expect(api.post).toHaveBeenCalledWith("/me/assessment-repeat",{instrument:"current",phase:"first",revision:2,consent_revision:3,expected_member:"member",answers:{s1:1},publish_group_results:true});
  expect(host.textContent).toContain("First set saved.");
  await act(async()=>button("Remove my study entry").click());
  expect(api.delete).toHaveBeenCalledWith("/me/assessment-repeat", {data:{expected_member:"member",revision:3}});
  expect(host.querySelectorAll("input:checked").length).toBe(0);
});

test("history can record real post-send usefulness and keeps the original sent style",async()=>{
  const feedback={sent:true,sent_style:"warm",style:"cool"};
  const api={post:jest.fn(async()=>({data:{...feedback,helped:false}}))};
  await act(async()=>root.render(<SentReplyFollowup ex={{id:"ex",tone:{status:"estimated"},tone_feedback:feedback}} api={api}/>));
  await act(async()=>button("No").click());
  expect(api.post).toHaveBeenCalledWith("/exchanges/ex/tone-feedback",{action:"helped",style:"warm",value:false});
  expect(button("✓ No").getAttribute("aria-pressed")).toBe("true");
});

test("public and staff evidence views keep missing data distinct from zero usefulness",async()=>{
  publicApi.get.mockResolvedValue({data:{minimum:50,minimum_cell:10,round_to:5,repeat:[]}});
  await act(async()=>root.render(<AssessmentMethods />));
  await act(async()=>button("See repeat results").click());
  expect(host.textContent).toContain("No items meet the publication thresholds");
  expect(publicApi.get).toHaveBeenCalledWith("/public/research/repeat");
  const data={as_of:"2026-09-10",minimum:50,minimum_cell:10,round_to:5,
    questionnaire:{scales:[{name:"Directness",items:2,alpha:null,sample:null}],repeat:[]},
    individual:{available:true,answered:20,sample:50,helped_percent:0,groups:{helped:0,did_not_help:20,awaiting_outcome:10,not_sent:10,send_unknown:10}},
    couples:{available:false}};
  await act(async()=>root.render(<AssessmentEvidence api={{get:async()=>({data})}}/>));
  await act(async()=>button("Load evidence").click());
  expect(host.textContent).toContain("0% of respondents");
  expect(host.textContent).toContain("20 reported an outcome from 50 eligible reads.");
  expect(host.textContent).toContain("Withheld until");
});
