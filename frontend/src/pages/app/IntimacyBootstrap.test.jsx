import { act } from "react";
import { createRoot } from "react-dom/client";
import { useOutletContext } from "react-router-dom";
import IntimacyPage from "./IntimacyPage";

jest.mock("react-router-dom", () => ({ Link: ({to, children}) => <a href={to}>{children}</a>,
  useNavigate: () => jest.fn(), useOutletContext: jest.fn() }));
jest.mock("@/lib/api", () => ({errMsg: error => error.message}));

const endpoint = "/me/assessments?include_intimacy=true";
const answers = (label = "Earlier private answer") => ({ revision: 4, answers: {}, previous: 1,
  previous_answers: [{id:"i:conversations:0", question:label, answer:"Curious", conditions:[], skipped:false}] });
const profile = (owner = "first", age = 30, extra = {}) => ({ owner_id: owner,
  quiz: {short:[], extended:[], further:[], intimacy_copy:{}}, about:{answers:{}, demographics:{age}}, summary:{}, radar:[], ...extra });
let host, root, api, me;
const button = label => [...host.querySelectorAll("button")].find(node => node.textContent.includes(label));
async function show() {
  useOutletContext.mockReturnValue({api,me});
  await act(async () => root.render(<IntimacyPage />));
}
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host=document.body.appendChild(document.createElement("div")); root=createRoot(host);
  me={user_id:"first"};
  api={get:jest.fn(async () => ({data:profile("first",30,{intimacy_eligible:true,intimacy_answers:answers()})}))};
});
afterEach(() => {act(() => root.unmount()); host.remove();});

test("an eligible member opens saved answers in one owner-bound request", async () => {
  await show();
  expect(api.get).toHaveBeenCalledTimes(1);
  expect(api.get).toHaveBeenCalledWith(endpoint,expect.objectContaining({headers:{"X-Questionnaire-Owner":"first"}}));
  expect(host.textContent).toContain("Earlier private answer");
  expect(button("Intimacy & communication")).toBeDefined();
});

test("an older API response falls back to the existing saved-answer read", async () => {
  api.get.mockImplementation(async path => ({data:path === endpoint ? profile() : answers()}));
  await show();
  expect(api.get.mock.calls.map(([path])=>path)).toEqual([endpoint,"/me/intimacy-answers"]);
  expect(host.textContent).toContain("Earlier private answer");
});

test.each([null,17])("unknown or underage profile %s cannot mount or fetch saved answers", async age => {
  api.get.mockResolvedValue({data:profile("first",age,{intimacy_eligible:false,intimacy_answers:null})});
  await show();
  expect(api.get).toHaveBeenCalledTimes(1);
  expect(host.textContent).not.toContain("Earlier private answer");
  expect(button("Intimacy & communication")).toBeUndefined();
});

test("replacing the same member's API session clears old answers until the new read finishes", async () => {
  await show(); expect(host.textContent).toContain("Earlier private answer");
  let finish;
  api={get:jest.fn(() => new Promise(resolve => {finish=resolve;}))};
  await show();
  expect(host.textContent).not.toContain("Earlier private answer");
  await act(async () => finish({data:profile("first",30,{intimacy_eligible:true,intimacy_answers:answers("New session answer")})}));
  expect(host.textContent).toContain("New session answer");
  expect(api.get).toHaveBeenCalledTimes(1);
});

test("a delayed previous owner's bootstrap never replaces the active member", async () => {
  let finish;
  api.get.mockImplementationOnce(() => new Promise(resolve => {finish=resolve;}));
  await show();
  const signal=api.get.mock.calls[0][1].signal;
  me={user_id:"second"};
  api.get.mockResolvedValue({data:profile("second",30,{intimacy_eligible:true,intimacy_answers:answers("Second member answer")})});
  await show(); expect(signal.aborted).toBe(true);
  await act(async () => finish({data:profile("first",30,{intimacy_eligible:true,intimacy_answers:answers()})}));
  expect(host.textContent).toContain("Second member answer");
  expect(host.textContent).not.toContain("Earlier private answer");
});

test("a bootstrap owner mismatch fails closed without a second answers request", async () => {
  api.get.mockResolvedValue({data:profile("other",30,{intimacy_eligible:true,intimacy_answers:answers()})});
  await show();
  expect(host.textContent).toContain("signed-in account changed");
  expect(host.textContent).not.toContain("Earlier private answer");
  expect(api.get).toHaveBeenCalledTimes(1);
});
