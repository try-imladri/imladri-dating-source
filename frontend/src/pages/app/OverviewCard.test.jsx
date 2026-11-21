import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import Overview from "./Overview";

jest.mock("react-router-dom", () => {
  global.TextEncoder = require("util").TextEncoder;
  return jest.requireActual("react-router");
});
jest.mock("@clerk/react", () => ({ useUser: () => ({}) }));
jest.mock("@/lib/api", () => ({ REL_GROUPS: [], GROUP_OF: {}, GROUP_LABEL: {} }));
jest.mock("recharts", () => ({ ResponsiveContainer: () => null, PieChart: () => null, Pie: () => null,
  Cell: () => null, AreaChart: () => null, Area: () => null, XAxis: () => null, Tooltip: () => null }));
jest.mock("@/components/app/Personality", () => ({
  DimBars: () => null, pendingTypeText: () => "Not answered yet",
  RelationshipAxes: ({axes}) => <output data-testid="retained-relationship-axes">{JSON.stringify(axes)}</output>,
}));

let host, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT=true;
  host=document.body.appendChild(document.createElement("div"));root=createRoot(host);
});
afterEach(() => {act(()=>root.unmount());host.remove();delete global.IS_REACT_ACT_ENVIRONMENT;});

async function show(about) {
  const radar=[{axis:"Directness",score:50,answered:1}];
  const data={about:{personality:{},tags:[],...about},radar,total:0,groups:{},unread:0,
    checkins:[],unreviewed:[],activity:[],feed:[]};
  const api={get:jest.fn(async()=>({data}))};
  await act(async()=>root.render(<MemoryRouter initialEntries={["/app"]}><Routes>
    <Route path="/app" element={<Outlet context={{me:{user_id:"fictional-member"},api}}/>}>
      <Route index element={<Overview/>}/><Route path="personal" element={<h1>Personal destination</h1>}/>
    </Route>
  </Routes></MemoryRouter>));
  return {radar,api};
}

test("empty YOU card puts demographics in its header and keeps the concise questionnaire link functional",async()=>{
  const {radar}=await show({demographics:{age:30,sex:"Male"}});
  const card=host.querySelector('[data-testid="about-card"]');
  const header=card.querySelector('[data-testid="about-card-header"]');
  const badge=card.querySelector('[data-testid="about-demographics-chip"]');
  expect(header.contains(badge)).toBe(true);
  expect(badge.textContent).toBe("30 · Male");expect(badge.getAttribute("href")).toBe("/app/personal");
  expect(card.querySelectorAll('[data-testid="about-demographics-chip"]')).toHaveLength(1);
  expect(card.querySelector("output")).toBeNull();
  expect(host.querySelector('[data-testid="personality-card"]')).toBeNull();
  expect(card.classList.contains("xl:col-span-8")).toBe(true);
  const start=card.querySelector('[data-testid="about-start"]');
  expect(start.textContent).toBe("Answer 12 questions");
  await act(async()=>start.click());expect(host.querySelector("h1").textContent).toBe("Personal destination");
});

test("answered YOU card retains portrait, chart and edit links with one header badge",async()=>{
  const {radar}=await show({short_done:12,extended_done:36,further_done:60,questionnaire_complete:true,score:85,tags:["Thoughtful","Direct"],describe:"Fictional profile summary",
    demographics:{age:38,sex:"Female",ethnicity:"Prefer not to say",education:"Postgraduate education"}});
  const card=host.querySelector('[data-testid="about-card"]');
  expect(card.classList.contains("xl:col-span-5")).toBe(true);
  expect(card.querySelector('[data-testid="about-start"]')).toBeNull();
  expect(card.querySelector('[data-testid="about-snapshot"]').textContent).toContain("Thoughtful");
  expect(card.querySelector('[data-testid="about-describe-text"]').textContent).toContain("Fictional profile summary");
  expect(card.querySelector('[data-testid="about-ring"]')).not.toBeNull();
  expect(card.querySelector('[data-testid="about-extended"]').getAttribute("href")).toBe("/app/personal?tab=deeper");
  expect(card.querySelector('[data-testid="about-edit"]').getAttribute("href")).toBe("/app/personal");
  expect(card.querySelectorAll('[data-testid="about-demographics-chip"]')).toHaveLength(1);
  expect(card.querySelector("header").textContent).toContain("38 · Female · Postgraduate education");
  expect(card.querySelector("header").textContent).not.toContain("Prefer not to say");
  expect(JSON.parse(card.querySelector("output").textContent)).toEqual(radar);
  expect(host.querySelector('[data-testid="overview-personality-link"]').getAttribute("href")).toBe("/app/personal?tab=results");
});

test("partial answers and an explicitly selected type cannot expose personality results", async()=>{
  await show({short_done:12,extended_done:36,further_done:59,questionnaire_complete:false,
    tags:["Partial result"],personality:{code:"INTJ",self_set:true},describe:"Optional description"});
  expect(host.querySelector('[data-testid="personality-card"]')).toBeNull();
  expect(host.querySelector('[data-testid="about-radar"]')).toBeNull();
  expect(host.querySelector('[data-testid="about-snapshot"]')).toBeNull();
  expect(host.querySelector('[data-testid="about-describe-text"]').textContent).toContain("Optional description");
});

test("no demographics leaves the YOU header and start action available",async()=>{
  await show({demographics:{sex:"Prefer not to say",ethnicity:"Prefer not to say",education:"Prefer not to say"}});
  const card=host.querySelector('[data-testid="about-card"]');
  expect(card.querySelector('[data-testid="about-card-header"]').textContent).toBe("You");
  expect(card.querySelector('[data-testid="about-demographics-chip"]')).toBeNull();
  expect(card.querySelector('[data-testid="about-start"]')).not.toBeNull();
});
