import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Outlet, Route, Routes, useLocation } from "react-router-dom";
import Overview from "./Overview";
import Dashboard from "./Dashboard";

jest.mock("react-router-dom", () => {
  global.TextEncoder = require("util").TextEncoder;
  return jest.requireActual("react-router");
});

test.each([
  [{advice_id:"saved/advice?one"}, "/app/quick-advice?reply=saved%2Fadvice%3Fone"],
  [{quick_advice_id:"couples-advice",workspace:"couples"}, "/app/couples/quick-advice?reply=couples-advice"],
])("Quick Advice activity opens its saved reply without a People profile: %s", async (fields,destination) => {
  global.IS_REACT_ACT_ENVIRONMENT=true;
  const overview={about:{personality:{}},radar:[],total:0,groups:{},unread:0,checkins:[],unreviewed:[],activity:[],
    feed:[{type:"quick_advice",tab:"quick-advice",ts:new Date().toISOString(),text:"You asked for Quick Advice",...fields}]};
  const api={get:jest.fn(async()=>({data:overview}))};
  const host=document.body.appendChild(document.createElement("div"));const root=createRoot(host);
  function Destination(){const location=useLocation();return <output>{location.pathname}{location.search}</output>;}
  try {
    await act(async()=>root.render(<MemoryRouter initialEntries={["/app"]}><Routes>
      <Route path="/app" element={<Outlet context={{me:{user_id:"demo"},api}}/>}>
        <Route index element={<Overview/>}/><Route path="quick-advice" element={<Destination/>}/>
        <Route path="couples/quick-advice" element={<Destination/>}/>
      </Route>
    </Routes></MemoryRouter>));
    const activity=host.querySelector('[data-testid="activity-0"]');
    expect(activity.getAttribute("href")).toBe(destination);
    expect(activity.getAttribute("href")).not.toContain("person/undefined");
    await act(async()=>activity.click());expect(host.querySelector("output").textContent).toBe(destination);
  } finally {act(()=>root.unmount());host.remove();delete global.IS_REACT_ACT_ENVIRONMENT;}
});
jest.mock("@clerk/react", () => ({ useUser: () => ({}) }));
jest.mock("@/lib/api", () => ({ REL_GROUPS: [], GROUP_OF: {}, GROUP_LABEL: {} }));
jest.mock("recharts", () => ({ ResponsiveContainer: () => null, PieChart: () => null, Pie: () => null,
  Cell: () => null, AreaChart: () => null, Area: () => null, XAxis: () => null, Tooltip: () => null }));
jest.mock("@/components/app/Personality", () => ({
  DimBars: () => null, pendingTypeText: () => "", RelationshipAxes: () => null,
}));
jest.mock("@/components/app/PeopleChat", () => ({ PeopleChat: ({ ids }) => <output>{ids.join(" + ")}</output> }));
jest.mock("@/pages/app/PersonPage", () => ({}));
jest.mock("@/pages/app/Settings", () => ({}));
jest.mock("@/components/app/AddPersonForm", () => ({}));

test("recent activity opens the correct practice contact or selects both members of a group", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const ts = new Date().toISOString();
  const overview = { about: { personality: {} }, radar: [], total: 0, groups: {}, unread: 0, checkins: [],
    unreviewed: [], activity: [], feed: [
      { type: "practice", tab: "practice", person_id: "first", text: "Practice", ts },
      { type: "group", tab: "group", person_ids: ["first", "second"], text: "Group chat", ts },
    ] };
  const api = { get: async path => ({ data: path === "/overview" ? overview : { people: [] } }) };
  const host = document.body.appendChild(document.createElement("div"));
  const root = createRoot(host);
  try {
    await act(async () => root.render(<MemoryRouter initialEntries={["/app"]}><Routes>
      <Route path="/app" element={<Outlet context={{ me: { user_id: "demo" }, api }} />}>
        <Route index element={<Overview />} /><Route path="people" element={<Dashboard />} />
      </Route>
    </Routes></MemoryRouter>));
    expect(host.querySelector('[data-testid="activity-0"]').getAttribute("href")).toBe("/app/practice?person=first");
    await act(async () => host.querySelector('[data-testid="activity-1"]').click());
    expect(host.querySelector("output").textContent).toBe("first + second");
  } finally {
    act(() => root.unmount());
    host.remove();
    localStorage.clear();
    delete global.IS_REACT_ACT_ENVIRONMENT;
  }
});
