import { act } from "react";
import { createRoot } from "react-dom/client";
import { useOutletContext, useSearchParams } from "react-router-dom";
import { useMemberPages } from "@/components/app/MemberPages";
import { useMessageSend } from "@/components/app/PlanUsage";
import { readPracticeTurn, storePracticeTurn } from "@/lib/practiceTurn";
import Practice from "./Practice";

jest.mock("react-router-dom", () => ({
  useOutletContext: jest.fn(), useSearchParams: jest.fn(),
  Link: ({to, children}) => <a href={to}>{children}</a>,
}));
jest.mock("@/components/app/MemberPages", () => ({useMemberPages: jest.fn(), ListContinuation: () => null}));
jest.mock("@/components/app/PlanUsage", () => ({useMessageSend: jest.fn(), messageLimit: () => null}));
jest.mock("@/components/ui/select", () => ({
  Select: ({children}) => <div>{children}</div>, SelectTrigger: () => null, SelectValue: () => null,
  SelectContent: () => null, SelectItem: () => null,
}));
jest.mock("@/lib/api", () => ({errMsg: e => e.message}));

const people = [{id:"a", nickname:"Alex"}, {id:"b", nickname:"Ben"}];
const sessions = [
  {id:"one", person_id:"a", topic:"Planning Sundays", mode:"likely", ended:true, messages:[],
    rounds:[{line:"What about Sundays?", picked:0, options:[
      {reaction:"Yes.", outcome:"You settle on a day."}, {reaction:"Maybe.", outcome:"You ask what works."},
    ]}]},
  {id:"two", person_id:"b", topic:"A later start", mode:"live", messages:[]},
];
let root, host, api, params, updateParams;
const control = id => host.querySelector('[data-testid="' + id + '"]');
const show = async query => {
  params = new URLSearchParams(query);
  await act(async () => root.render(<Practice />));
};
const click = id => act(async () => control(id).click());
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  sessionStorage.clear();
  useMessageSend.mockReturnValue(jest.fn());
  updateParams = jest.fn();
  useSearchParams.mockImplementation(() => [params, updateParams]);
  api = {get:jest.fn(async path => ({data:sessions.find(row => path === "/practice/" + row.id)})),
    post:jest.fn(async () => ({data:sessions[0]}))};
  useOutletContext.mockReturnValue({me:{user_id:"viewer", usage:{}}, api});
  useMemberPages.mockImplementation((_, path) => ({rows:path === "/people" ? people : sessions.filter(row => path?.endsWith(row.person_id))}));
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  jest.restoreAllMocks();
});

test("replay exposes two independent native buttons for opening and trying an alternative", async () => {
  await show("person=a&session=one");
  const branch = control("replay-branch-0-1");
  expect(branch.tagName).toBe("BUTTON");
  expect(branch.getAttribute("aria-expanded")).toBe("false");
  await click("replay-branch-0-1");
  expect(branch.getAttribute("aria-expanded")).toBe("true");
  expect(branch.contains(control("replay-retry-0-1"))).toBe(false);
  expect(document.getElementById(branch.getAttribute("aria-controls")).textContent).toContain("You ask what works.");
  jest.spyOn(window, "confirm").mockReturnValue(true);
  await click("replay-retry-0-1");
  expect(api.post).toHaveBeenCalledWith("/practice/one/branch", {round:0, index:1});
});

test.each([0,1])("a saved rehearsal at revision %s retains the interrupted turn until recovery with its original key", async savedRevision => {
  const member = {user_id:"viewer"};
  const turn = {key:"original-turn", body:{text:"Sunday afternoon?", expected_revision:0}};
  storePracticeTurn(member, "one", turn);
  const send = jest.fn(async () => ({data:{...sessions[0], turn_revision:1, messages:[{role:"me", text:turn.body.text}]}}));
  useMessageSend.mockReturnValue(send);
  await show("person=a&session=one");
  expect(control("practice-restart").disabled).toBe(true);
  await click("replay-branch-0-1");
  expect(control("replay-retry-0-1").disabled).toBe(true);
  const byLabel = label => [...host.querySelectorAll("button")].find(button => button.textContent === label);
  let settle;
  api.get.mockImplementationOnce(() => new Promise(resolve => {settle = resolve;}));
  await act(async () => byLabel("Show saved conversation").click());
  expect(byLabel("Recover my turn")).toBeUndefined();
  await act(async () => settle({data:{...sessions[0], turn_revision:savedRevision}}));
  expect(readPracticeTurn(member, "one")).toEqual(turn);
  expect(control("practice-input").value).toBe(turn.body.text);
  expect(send).not.toHaveBeenCalled();
  await act(async () => byLabel("Recover my turn").click());
  expect(send).toHaveBeenCalledTimes(1);
  expect(send).toHaveBeenCalledWith("/practice/one/say", turn.body, {"Idempotency-Key":turn.key});
  expect(readPracticeTurn(member, "one")).toBeNull();
  expect(control("practice-msg-0").textContent).toBe(turn.body.text);
  expect(control("practice-restart").disabled).toBe(false);
});

test("following browser history to another person immediately removes the previous transcript", async () => {
  await show("person=a&session=one");
  expect(control("practice-session").textContent).toContain("Planning Sundays");
  let finish;
  api.get.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await show("person=b&session=two");
  expect(control("practice-session")).toBeNull();
  expect(api.get).toHaveBeenLastCalledWith("/practice/two");
  await act(async () => finish({data:sessions[1]}));
  expect(control("practice-session").textContent).toContain("A later start");
  expect(control("practice-session").textContent).not.toContain("Planning Sundays");
  await show("person=a&session=one");
  expect(control("practice-session").textContent).toContain("Planning Sundays");
});

test("deleting the open rehearsal removes its deep link and a delayed deletion preserves a newer selection", async () => {
  await show("person=a&session=one");
  api.delete = jest.fn(async () => ({}));
  await click("practice-delete-one");
  expect(updateParams).toHaveBeenCalledWith({person:"a"}, {replace:true});
  expect(control("practice-session")).toBeNull();
  await show("person=b&session=two");
  let finish;
  api.delete.mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
  await click("practice-delete-two");
  await show("person=a&session=one");
  updateParams.mockClear();
  await act(async () => finish({}));
  expect(updateParams).not.toHaveBeenCalled();
  expect(control("practice-session").textContent).toContain("Planning Sundays");
});
