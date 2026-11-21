import { act } from "react";
import { createRoot } from "react-dom/client";
import { deferredPage, preloadRoute, routePageKeys, routePreloadEvents } from "./routePages";

jest.mock("@/pages/app/Personal", () => { mockLoads.personal++; return {__esModule:true,default:props => <h1>Personal for {props.owner}</h1>}; });
jest.mock("@/pages/app/Couples", () => { mockLoads.couples++; return {Couples:() => null}; });
jest.mock("@/pages/admin/Admin", () => { mockLoads.admin++; return {__esModule:true,default:() => <h1>Admin ready</h1>}; });
jest.mock("@/pages/admin/ResearchTrends", () => { mockLoads.research++; return {__esModule:true,default:() => null}; });
jest.mock("@/pages/admin/MembersAdmin", () => { mockLoads.members++; return {__esModule:true,default:() => null}; });
jest.mock("@/pages/admin/YourTypeResearch", () => { mockLoads.yourTypeResearch++; return {__esModule:true,default:() => null}; });
const mockLoads={personal:0,couples:0,admin:0,research:0,members:0,yourTypeResearch:0};

test("importing navigation does not load heavy pages; intent preloads code once before rendering", async () => {
  expect(Object.values(mockLoads).every(count=>count===0)).toBe(true);
  await Promise.all([preloadRoute("/app/personality"),preloadRoute("/app/personality")]);
  expect(mockLoads).toMatchObject({personal:1,couples:0,admin:0,research:0,members:0,yourTypeResearch:0});
  global.IS_REACT_ACT_ENVIRONMENT=true;
  const host=document.createElement("div"),root=createRoot(host),Page=deferredPage("personal");
  await act(async () => root.render(<Page owner="fictional member" />));
  expect(host.textContent).toBe("Personal for fictional member");
  expect(mockLoads.personal).toBe(1); act(() => root.unmount());
});

test("paired assessment intent preloads parent and child; keyboard focus preloads Admin", async () => {
  await preloadRoute("/app/couples/personality");
  expect(mockLoads.couples).toBe(1); expect(mockLoads.personal).toBe(1);
  routePreloadEvents("/admin/research").onFocus(); await preloadRoute("/admin/research");
  expect(mockLoads.admin).toBe(1);
  expect(mockLoads.research).toBe(1);
  expect(mockLoads.members).toBe(0);
  expect(mockLoads.yourTypeResearch).toBe(0);
});

test("staff navigation intent warms only the selected section and research tool", async () => {
  await preloadRoute("/admin/research#your-type-research");
  expect(mockLoads.yourTypeResearch).toBe(1);
  expect(mockLoads.members).toBe(0);
  await preloadRoute("/admin/users");
  expect(mockLoads.members).toBe(1);
  expect(routePageKeys("/admin/research?tool=exports")).toEqual(["admin","adminResearch","researchExports"]);
  expect(routePageKeys("/admin/research#database-migration")).toEqual(["admin","adminResearch","researchMigration"]);
  expect(routePageKeys("/admin/activity?view=live")).toEqual(["admin","adminActivity"]);
});

test("routing maps separate profiles and public details, excluding external or unknown destinations", async () => {
  expect(routePageKeys("/dating/messages/fixture")).toEqual(["dating"]);
  expect(routePageKeys("/legacy")).toEqual(["legacy"]);
  expect(routePageKeys("/legacy/plans")).toEqual(["legacyPlans"]);
  expect(routePageKeys("/learn")).toEqual(["learn"]);
  expect(routePageKeys("/app/settings/upgrade?from=nav")).toEqual(["upgrade"]);
  expect(routePageKeys("/app/personality")).toEqual(["personal"]);
  expect(routePageKeys("/app/couples/personality")).toEqual(["couples","personal"]);
  expect(routePageKeys("/app/quick-advice")).toEqual(["quickAdvice"]);
  expect(routePageKeys("/app/couples/quick-advice")).toEqual(["couples","quickAdvice"]);
  expect(routePageKeys("/app/couples/intimacy")).toEqual(["couples","intimacy"]);
  expect(routePageKeys("/app/couples/conversation-cards")).toEqual(["couples"]);
  expect(routePageKeys("/figures/example-person")).toEqual(["figure"]);
  expect(routePageKeys("/types/example")).toEqual(["archetype"]);
  for(const path of ["https://external.invalid/app/personality","/unknown","/sign-in"]) expect(routePageKeys(path)).toEqual([]);
});
