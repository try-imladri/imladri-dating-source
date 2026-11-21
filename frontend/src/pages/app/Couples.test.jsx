import { act } from "react";
import { createRoot } from "react-dom/client";
import { useNavigate, useOutletContext } from "react-router-dom";
import { ConversationCards, Couples } from "./Couples";

jest.mock("react-router-dom", () => ({
  Link: ({ children, to }) => <a href={to}>{children}</a>,
  Outlet: () => null,
  useLocation: () => ({ pathname: "/app/couples", search:"" }),
  useOutletContext: jest.fn(),
  useNavigate: jest.fn(),
}));
jest.mock("@/lib/api", () => ({ errMsg: () => "Request failed" }));

let root, host, api;
const button = (id) => host.querySelector(`[data-testid="${id}"]`);
const click = (id) => act(() => button(id).click());

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  api = { post: jest.fn(async () => ({ data: { ok: true } })), get: jest.fn(async()=>({data:{adult:false}})) };
  useOutletContext.mockReturnValue({ api });
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

test("entering the workspace records only the fixed open action", async () => {
  await act(async () => root.render(<Couples />));
  expect(api.post.mock.calls).toEqual([["/me/couples-activity", { action: "opened" }]]);
});

test("a freshly paired shell overlaps workspace and journey, but exposes neither before the workspace resolves", async () => {
  let finish;
  api.get.mockImplementation(path => path === "/couples" ? new Promise(resolve => {finish=resolve;}) : Promise.resolve({data:{pair_id:"pair",prompts:[],threads:[]}}));
  useOutletContext.mockReturnValue({api, me:{user_id:"owner", workspace_access:{version:"workspace-access-v1",mode:"couples",pair_id:"pair"}}});
  await act(async () => root.render(<Couples />));
  expect(api.get.mock.calls.map(([path])=>path)).toEqual(["/couples","/couples/journey"]);
  expect(api.post).not.toHaveBeenCalled();
  await act(async () => finish({data:{adult:true,state:"active",pair_id:"pair"}}));
  expect(api.get).toHaveBeenCalledTimes(2);
});

test("an unpaired shell waits for the current workspace before requesting the journey", async () => {
  let finish;
  api.get.mockImplementation(path => path === "/couples" ? new Promise(resolve => {finish=resolve;}) : Promise.resolve({data:{pair_id:null}}));
  await act(async () => root.render(<Couples />));
  expect(api.get.mock.calls.map(([path])=>path)).toEqual(["/couples"]);
  await act(async () => finish({data:{adult:true,can_sponsor:true,state:"unpaired"}}));
  expect(api.get.mock.calls.map(([path])=>path)).toEqual(["/couples","/couples/journey"]);
});

test("paired speculative reads are aborted together on unmount and an obsolete response cannot record a visit", async () => {
  let finish;
  api.get.mockImplementation(path => path === "/couples" ? new Promise(resolve => {finish=resolve;}) : Promise.resolve({data:{pair_id:"pair"}}));
  useOutletContext.mockReturnValue({api,me:{user_id:"owner",workspace_access:{version:"workspace-access-v1",mode:"couples"}}});
  await act(async () => root.render(<Couples />));
  const signals=api.get.mock.calls.map(([,options])=>options.signal);
  act(() => root.unmount()); root=createRoot(host);
  await act(async () => finish({data:{adult:true,state:"active",pair_id:"pair"}}));
  expect(signals.every(signal=>signal.aborted)).toBe(true);
  expect(api.post).not.toHaveBeenCalled();
});

test("a visible refresh retries a failed workspace visit once", async () => {
  api.post.mockRejectedValueOnce(new Error("Offline"));
  await act(async () => root.render(<Couples />));
  await act(async () => window.dispatchEvent(new Event("focus")));
  await act(async () => window.dispatchEvent(new Event("focus")));
  expect(api.post.mock.calls).toEqual(Array(2).fill(["/me/couples-activity", { action: "opened" }]));
});

test.each(["communication", "repair", "support", "priorities"])("the %s card keeps topic and progression private", async (topic) => {
  act(() => root.render(<ConversationCards />));
  click(`couples-card-${topic}`);
  expect(host.querySelectorAll("blockquote")).toHaveLength(2);
  expect(document.activeElement).toBe(host.querySelector("h1"));
  click("couples-card-next");
  expect(api.post).not.toHaveBeenCalled();
  await act(async () => button("couples-card-finish").click());
  expect(button("couples-card-complete")).not.toBeNull();
  expect(api.post.mock.calls).toEqual([["/me/couples-activity", { action: "sample_completed" }]]);
});

test("a failed sample save can be retried from the same card", async () => {
  api.post.mockRejectedValueOnce(new Error("Offline"));
  act(() => root.render(<ConversationCards />));
  click("couples-card-repair");
  click("couples-card-next");
  await act(async () => button("couples-card-finish").click());
  expect(host.querySelector('[role="alert"]').textContent).toContain("try again");
  expect(button("couples-card-complete")).toBeNull();
  await act(async () => button("couples-card-finish").click());
  expect(button("couples-card-complete")).not.toBeNull();
  expect(api.post.mock.calls).toEqual(Array(2).fill(["/me/couples-activity", { action: "sample_completed" }]));
});

test.each(["communication", "repair", "support", "priorities"])("starting %s from the list opens a private answer", async slug => {
  const navigate = jest.fn();
  useNavigate.mockReturnValue(navigate);
  const mutate = jest.fn().mockResolvedValue({ key: "pair-card" });
  useOutletContext.mockReturnValue({ api, mutate, workspace: { state: "active", writable: true, pair_id: "pair" }, journey: { prompts: [], threads: [] } });
  act(() => root.render(<ConversationCards />));
  await act(async () => button("couples-start-" + slug).click());
  expect(mutate.mock.calls).toEqual([["post", "/couples/cards/" + slug + "/start", { pair_id: "pair", origin: "card" }]]);
  expect(navigate).toHaveBeenCalledWith("/app/couples/answers?item=pair-card");
  expect(api.post).not.toHaveBeenCalled();
});

test("retrying a real start from a sample keeps the origin and resumes at the pair's agreement", async () => {
  const navigate = jest.fn();
  useNavigate.mockReturnValue(navigate);
  const mutate = jest.fn().mockRejectedValueOnce(new Error("Offline")).mockResolvedValue({ key: "pair-card" });
  useOutletContext.mockReturnValue({ api, mutate, workspace: { state: "active", writable: true, pair_id: "pair" },
    journey: { prompts: [{ id: "pair-card", card: "repair" }], threads: [{ id: "pair-card", my_share: ["My words"], agreements: [{ status: "active" }] }] } });
  act(() => root.render(<ConversationCards />));
  click("couples-card-repair");
  await act(async () => button("couples-start-repair").click());
  expect(host.querySelector('[role="alert"]').textContent).toContain("Try again");
  await act(async () => button("couples-start-repair").click());
  expect(mutate.mock.calls).toEqual(Array(2).fill(["post", "/couples/cards/repair/start", { pair_id: "pair", origin: "sample" }]));
  expect(navigate).toHaveBeenCalledWith("/app/couples/agreements?item=pair-card");
  expect(api.post).not.toHaveBeenCalled();
});

test("a card opens the earliest due check-in this member still needs to share", async () => {
  const navigate = jest.fn();
  useNavigate.mockReturnValue(navigate);
  const agreements = [
    {id:"retired", status:"retired", review_on:"2000-01-01"},
    {id:"already-shared", status:"active", review_on:"2000-01-01", my_checkin:{shared:true}},
    {id:"later", status:"active", review_on:"2000-06-02", revision:3},
    {id:"first", status:"active", review_on:"2000-06-01", revision:2, my_checkin:{shared:false}},
  ];
  useOutletContext.mockReturnValue({ api, workspace:{state:"active",writable:true,pair_id:"pair"},
    journey:{prompts:[{id:"pair-card",card:"repair"}],threads:[{id:"pair-card",agreements,my_share:["shared"]}]},
    mutate:jest.fn().mockResolvedValue({key:"pair-card"}) });
  act(() => root.render(<ConversationCards />));
  expect(button("couples-start-repair").textContent).toContain("Open my check-in");
  await act(async () => button("couples-start-repair").click());
  expect(navigate).toHaveBeenCalledWith("/app/couples/agreements?item=pair-card&agreement=first&revision=2");
});

test("a card with both check-ins takes the partner straight to the proposed learning", async () => {
  const navigate = jest.fn();
  useNavigate.mockReturnValue(navigate);
  const agreement = {id:"check",revision:2,status:"active",review_on:"2000-01-01",my_checkin:{shared:true},
    partner_checkin:{result:"helped"},pin:{active:false,accepted_by_me:false}};
  useOutletContext.mockReturnValue({api,workspace:{state:"active",writable:true,pair_id:"pair"},
    journey:{prompts:[{id:"pair-card",card:"repair"}],threads:[{id:"pair-card",agreements:[agreement]}]},
    mutate:jest.fn().mockResolvedValue({key:"pair-card"})});
  act(() => root.render(<ConversationCards />));
  expect(button("couples-start-repair").textContent).toContain("Review proposed learning");
  await act(async () => button("couples-start-repair").click());
  expect(navigate).toHaveBeenCalledWith("/app/couples/what-works?item=pair-card&agreement=check&revision=2");
});

test.each([true, false])("reading another sample keeps the current page when an earlier start resolves (%s)", async success => {
  const navigate = jest.fn();
  useNavigate.mockReturnValue(navigate);
  let finish;
  const mutate = jest.fn(() => new Promise((resolve, reject) => {
    finish = () => success ? resolve({key:"repair-thread"}) : reject(new Error("Offline"));
  }));
  useOutletContext.mockReturnValue({api, workspace:{state:"active",writable:true,pair_id:"pair"},
    journey:{prompts:[],threads:[]}, mutate});
  act(() => root.render(<ConversationCards />));
  await act(async () => button("couples-start-repair").click());
  click("couples-card-support");
  await act(async () => finish());
  expect(mutate).toHaveBeenCalledTimes(1);
  expect(navigate).not.toHaveBeenCalled();
  expect(button("couples-card-detail")).not.toBeNull();
  expect(button("couples-start-support")).not.toBeNull();
  expect(host.querySelector('[role="alert"]')).toBeNull();
});

test("a card opens the latest step and a response from a previous couple cannot redirect", async () => {
  const navigate = jest.fn();
  useNavigate.mockReturnValue(navigate);
  let resolve;
  const active = {state:"active", writable:true, pair_id:"pair"};
  const session = {api, workspace:active, journey:{prompts:[],threads:[]}, mutate:jest.fn(() => new Promise(done => {resolve=done;}))};
  useOutletContext.mockReturnValue(session);
  act(() => root.render(<ConversationCards />));
  await act(async () => button("couples-start-repair").click());
  useOutletContext.mockReturnValue({...session, journey:{prompts:[{id:"key",card:"repair"}],
    threads:[{id:"key",agreements:[{id:"ready",revision:3,status:"proposed",accepted_by_me:false}]}]}});
  act(() => root.render(<ConversationCards />));
  await act(async () => resolve({key:"key"}));
  expect(navigate).toHaveBeenCalledWith("/app/couples/agreements?item=key&agreement=ready&revision=3");
  navigate.mockClear();
  await act(async () => button("couples-start-repair").click());
  useOutletContext.mockReturnValue({...session, workspace:{...active,pair_id:"other-pair"}});
  act(() => root.render(<ConversationCards />));
  await act(async () => resolve({key:"key"}));
  expect(navigate).not.toHaveBeenCalled();
});
