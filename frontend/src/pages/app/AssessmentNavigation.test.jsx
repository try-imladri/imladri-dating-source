import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Link, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { PersonalRoute } from "./Personal";
import PersonalityPage from "./PersonalityPage";
import IntimacyPage from "./IntimacyPage";
import { CouplesPersonal } from "./CouplesPersonal";
import { PersonalityPanel } from "@/components/app/Personality";
import { IntimacyPanel } from "@/components/app/Intimacy";
import { toast } from "sonner";

jest.mock("react-router-dom", () => { global.TextEncoder = require("util").TextEncoder; return jest.requireActual("react-router"); });
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("@/lib/api", () => ({ errMsg: failure => failure.message }));
jest.mock("@/components/app/Patterns", () => ({ PatternsPanel: () => null }));
jest.mock("@/components/app/Personality", () => ({ PersonalityPanel: jest.fn(({onPick}) => <button data-testid="choose-type" onClick={() => onPick("INTJ")}>Choose type</button>) }));
jest.mock("@/components/app/Intimacy", () => ({ IntimacyPanel: jest.fn(({owner, underage, onExit}) => <section data-testid="intimacy-panel">{owner}:{String(underage)}<button onClick={onExit}>Back to Personal</button></section>) }));

let host, root, member, api, about, quiz;
const query = id => host.querySelector(`[data-testid="${id}"]`);
const click = node => act(async () => node.click());
function FixtureShell() {
  const location = useLocation();
  return <><output data-testid="location">{location.pathname}{location.search}</output>
    <Link to="/app/personality" data-testid="to-personality">Personality</Link>
    <Link to="/app/intimacy" data-testid="to-intimacy">Sex &amp; Intimacy</Link>
    <Outlet context={{me:member, api, setMe:jest.fn()}} /></>;
}
function response() { return {data:{about:JSON.parse(JSON.stringify(about)), summary:{describe:about.describe}, radar:[]}}; }
async function open(path = "/app/personality") {
  await act(async () => root.render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/app" element={<FixtureShell />}>
    <Route path="personal" element={<PersonalRoute />} /><Route path="personality" element={<PersonalityPage />} />
    <Route path="intimacy" element={<IntimacyPage />} /><Route path="couples/personal" element={<CouplesPersonal />} />
    <Route path="couples/personality" element={<PersonalityPage withinCouples />} />
    <Route path="couples/intimacy" element={<IntimacyPage withinCouples />} />
  </Route></Routes></MemoryRouter>));
}
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  PersonalityPanel.mockImplementation(({onPick}) => <button data-testid="choose-type" onClick={() => onPick("INTJ")}>Choose type</button>);
  IntimacyPanel.mockImplementation(({owner, underage, onExit}) => <section data-testid="intimacy-panel">{owner}:{String(underage)}<button onClick={onExit}>Back to Personal</button></section>);
  member = {user_id:"first", onboarded:true};
  quiz = {short:Array.from({length:12}, (_,i) => ({id:`s${i+1}`, q:`Question ${i+1}?`, options:[{label:"First choice"},{label:"Second choice"}]})),
    extended:Array.from({length:36}, (_,i) => ({id:`e${i+1}`, q:`Deeper question ${i+1}?`, options:[{label:"First choice"},{label:"Second choice"}]})),
    further:Array.from({length:60}, (_,i) => ({id:`f${i+1}`, q:`Further question ${i+1}?`, options:[{label:"First choice"},{label:"Second choice"}]})),
    intimacy_copy:{}, research_catalog_version:"fixture-edition", research_intimacy_signature:"fixture-signature"};
  about = {answers:Object.fromEntries(["short","extended","further"].flatMap(set=>quiz[set]).map(q => [q.id,{choice:0, question:q.q,label:"First choice",note:""}])), describe:"",demographics:{age:30}};
  api = {get:jest.fn(async path => path === "/quiz" ? {data:quiz} : path === "/me" ? {data:member} : path.startsWith("/me/assessments") ? {data:{quiz,owner_id:member.user_id,...response().data}} : response()), put:jest.fn(async (_,body) => {
    if (body.answers) { about.answers = {...about.answers,...body.answers}; if (body.expected_earlier_answer) about.previous_answers = {}; }
    if (body.mbti) about.mbti = body.mbti;
    return response();
  })};
  host=document.body.appendChild(document.createElement("div")); root=createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

test.each(["personality","intimacy"])("legacy Individual and Couples %s links keep their appropriate destination", async section => {
  await open(`/app/personal?tab=${section}`);
  expect(query("location").textContent).toBe(section === "personality" ? "/app/personal?tab=results" : "/app/intimacy");
  expect(query(section === "personality" ? "personal-results" : "intimacy-page")).not.toBeNull();
  expect(Boolean(query("personal-tabs"))).toBe(section === "personality");
  act(() => root.unmount()); root=createRoot(host);
  await open(`/app/couples/personal?tab=${section}`);
  expect(query("location").textContent).toBe(section === "personality" ? "/app/couples/personal?tab=results" : "/app/couples/intimacy");
  expect(query("couples-assessments-privacy").textContent).toContain("your own private answers");
  expect(query("couples-assessments-privacy").querySelector('a[href="/app/couples/compatibility"]')).not.toBeNull();
});

test.each([false,true])("regular Basics can be updated immediately from Personality with research=%s", async enabled => {
  member.questionnaire_research={enabled}; await open();
  expect(query("personal-portrait").textContent).toContain("108 saved answers");
  await click(query("review-basics-now"));
  expect(query("personal-tabs").querySelectorAll("button")).toHaveLength(5);
  await click(query("quiz-opt-s1-1")); await click(query("personal-save"));
  expect(api.put.mock.calls[0][1]).toMatchObject({expected_member:"first",answers:{s1:{choice:1,question:"Question 1?",label:"Second choice",note:""}}});
  await click(query("to-personality"));
  expect(query("personal-portrait").textContent).toContain("108 saved answers");
  expect(query("personal-portrait").textContent).toContain("Second choice");
  expect(api.get.mock.calls.some(([path]) => path.includes("assessment-repeat"))).toBe(false);
});

test("historical wording and removal use the same owner-bound profile endpoint", async () => {
  const previous={question:"Earlier question",label:"Earlier choice",choice:0}; about.previous_answers={s1:previous}; await open();
  const archive=[...host.querySelectorAll("details")].find(node => node.textContent.includes("Earlier choice"));
  expect(archive.textContent).toContain("Earlier question"); expect(query("personal-portrait").textContent).not.toContain("Earlier choice");
  await click(archive.querySelector("button"));
  expect(api.put).toHaveBeenCalledWith("/me/about", {expected_member:"first",answers:{s1:null},expected_earlier_answer:{s1:previous}});
  expect(host.textContent).not.toContain("Earlier choice");
});

test("immediate navigation waits for the final debounced Personal save before reading the portrait", async () => {
  let finishSave;
  api.put.mockImplementationOnce((_, body) => new Promise(resolve => { finishSave=() => {
    about.answers={...about.answers,...body.answers}; resolve(response());
  }; }));
  await open("/app/personal?tab=basics");
  await click(query("quiz-opt-s1-1"));
  const before=api.get.mock.calls.length;
  await click(query("to-personality"));
  expect(api.put.mock.calls[0][1].expected_member).toBe("first");
  expect(api.get.mock.calls.length).toBe(before);
  expect(query("personal-portrait")).toBeNull();
  await act(async () => finishSave());
  expect(query("personal-portrait").textContent).toContain("Second choice");
});

test("a previous owner's pending write neither blocks nor populates the next owner's profile", async () => {
  let finishSave;
  api.put.mockImplementationOnce(() => new Promise(resolve => { finishSave=resolve; }));
  await open("/app/personal?tab=basics"); await click(query("quiz-opt-s1-1")); await click(query("to-personality"));
  expect(query("personal-portrait")).toBeNull();
  member={...member,user_id:"second"}; about.describe="Second member only";
  await open();
  expect(query("personal-portrait").textContent).toContain("Second member only");
  const calls=api.get.mock.calls.length;
  await act(async () => finishSave({data:{about:{...about,describe:"First member secret"},summary:{describe:"First member secret"},radar:[]}}));
  expect(api.get.mock.calls.length).toBe(calls);
  expect(host.textContent).not.toContain("First member secret");
});

test("Personality saves to the existing profile; Intimacy keeps exact owner and catalog consent bindings", async () => {
  await open(); await click(query("choose-type"));
  expect(api.put).toHaveBeenCalledWith("/me/about",{mbti:"INTJ",expected_member:"first"});
  await click(query("to-intimacy"));
  expect(IntimacyPanel.mock.calls.at(-1)[0]).toMatchObject({api, owner:"first", accountSaving:true, underage:false,
    overrides:quiz.intimacy_copy,catalogVersion:"fixture-edition",intimacySignature:"fixture-signature"});
  expect(PersonalityPanel.mock.calls.at(-1)[0].savedAnswers.s1.label).toBe("First choice");
});

test("a started Personality save settles before Intimacy reads the profile", async () => {
  let finishSave;
  api.put.mockImplementationOnce((_, body) => new Promise(resolve => { finishSave=() => {
    about.mbti=body.mbti; about.describe="Completed personality change"; resolve(response());
  }; }));
  await open(); await click(query("choose-type"));
  expect(api.put.mock.calls[0]).toEqual(["/me/about", {mbti:"INTJ",expected_member:"first"}]);
  const before=api.get.mock.calls.length;
  await click(query("to-intimacy"));
  expect(api.get.mock.calls.length).toBe(before);
  expect(query("intimacy-panel")).toBeNull();
  await act(async () => finishSave());
  expect(api.get.mock.calls.slice(before).map(([path]) => path)).toEqual(["/me/assessments?include_intimacy=true"]);
  expect(IntimacyPanel.mock.calls.at(-1)[0].savedProfile.description).toBe("Completed personality change");
});

test("a rejected Personality write shows its failure and releases the navigation read barrier", async () => {
  api.put.mockRejectedValueOnce(new Error("Save is temporarily unavailable"));
  await open(); await click(query("choose-type"));
  expect(toast.error).toHaveBeenCalledWith("Save is temporarily unavailable");
  await click(query("review-basics-now"));
  expect(query("personal-page")).not.toBeNull();
  expect(about.mbti).toBeUndefined();
});

test("a real save failure after navigation still reports failure and reads the unchanged profile", async () => {
  let rejectSave;
  api.put.mockImplementationOnce(() => new Promise((_, reject) => { rejectSave=reject; }));
  await open(); await click(query("choose-type")); await click(query("to-intimacy"));
  expect(query("intimacy-panel")).toBeNull();
  await act(async () => rejectSave(new Error("Your change could not be saved")));
  expect(toast.error).toHaveBeenCalledWith("Your change could not be saved");
  expect(query("intimacy-panel")).not.toBeNull(); expect(about.mbti).toBeUndefined();
});

test("session-canceled writes after navigation do not produce a stale account alert", async () => {
  let rejectSave;
  api.put.mockImplementationOnce(() => new Promise((_, reject) => { rejectSave=reject; }));
  await open(); await click(query("choose-type"));
  member={...member,user_id:"second"}; await open();
  await act(async () => rejectSave(Object.assign(new Error("Previous session ended"), {code:"ERR_CANCELED"})));
  expect(toast.error).not.toHaveBeenCalled();
});

test("a completed previous-member Personality write cannot update the newly active member", async () => {
  let finishSave;
  api.put.mockImplementationOnce(() => new Promise(resolve => { finishSave=resolve; }));
  await open(); await click(query("choose-type"));
  member={...member,user_id:"second"}; about.describe="Second member only";
  await open(); expect(query("personal-portrait").textContent).toContain("Second member only");
  await act(async () => finishSave({data:{about:{...about,describe:"First member secret"},summary:{describe:"First member secret"},radar:[]}}));
  expect(query("personal-portrait").textContent).toContain("Second member only");
  expect(host.textContent).not.toContain("First member secret");
  expect(api.put.mock.calls[0][1].expected_member).toBe("first");
});

test("a failed profile load retries, while a saved minor keeps the existing age gate", async () => {
  api.get.mockImplementationOnce(async () => {throw new Error("Temporary profile outage");});
  about.demographics.age=17; await open("/app/intimacy");
  expect(host.textContent).toContain("Temporary profile outage");
  await click(query("assessment-retry")); expect(query("intimacy-panel").textContent).toContain("first:true");
});

test.each([[18, true], [30, true], [17, false], [undefined, false], [null, false], ["30", false]])(
  "Intimacy receives saved-profile adult eligibility for age %s", async (age, eligible) => {
    about.demographics = { age };
    await open("/app/intimacy");
    expect(IntimacyPanel.mock.calls.at(-1)[0].profileAdultEligible).toBe(eligible);
    expect(IntimacyPanel.mock.calls.at(-1)[0].underage).toBe(age != null && age < 18);
  }
);

test("late previous-member reads cannot populate the next account", async () => {
  let resolveOld; const originalGet=api.get;
  api.get=jest.fn(path => path === "/me/assessments" ? new Promise(resolve => {resolveOld=resolve;}) : originalGet(path));
  await open(); const oldSignal=api.get.mock.calls.find(([path]) => path === "/me/assessments")[1].signal;
  member={...member,user_id:"second"}; about.describe="Second member only"; api={...api,get:originalGet};
  await open(); expect(oldSignal.aborted).toBe(true);
  await act(async () => resolveOld({data:{quiz,owner_id:"first",about:{...about,describe:"First member secret"},summary:{},radar:[]}}));
  expect(host.textContent).toContain("Second member only"); expect(host.textContent).not.toContain("First member secret");
});

test.each([false,true])("all 108 saved current answers reveal embedded results without optional fields in Couples=%s", async withinCouples => {
  about.demographics = {}; about.describe = "";
  await open(withinCouples ? "/app/couples/personality" : "/app/personality");
  expect(query("location").textContent).toBe((withinCouples ? "/app/couples/personal" : "/app/personal") + "?tab=results");
  expect(query("personal-page")).not.toBeNull();
  expect(query("personal-portrait").textContent).toContain("108 saved answers");
  expect(query("choose-type")).not.toBeNull();
  expect(api.get.mock.calls.map(([path]) => path)).toEqual(["/me/assessments"]);
});

test.each(["missing", "old wording", "invalid choice", "previous only"])("a %s current answer hides personality results, including a preselected type", async kind => {
  const original = about.answers.f60;
  if (kind === "missing") delete about.answers.f60;
  if (kind === "old wording") about.answers.f60 = { ...original, question:"Earlier wording" };
  if (kind === "invalid choice") about.answers.f60 = { choice:99 };
  if (kind === "previous only") { delete about.answers.f60; about.previous_answers = { f60:original }; }
  about.mbti = "INTJ"; about.describe = "Optional description is complete"; about.demographics = {age:30,sex:"Male"};
  await open();
  expect(query("personality-incomplete")).not.toBeNull();
  expect(query("personal-portrait")).toBeNull(); expect(query("choose-type")).toBeNull();
  expect(query("personal-tab-results")).toBeNull();
  expect(about.mbti).toBe("INTJ"); expect(api.put).not.toHaveBeenCalled();
});

test("the final draft answer opens results only after saving; removing a draft answer hides them immediately", async () => {
  delete about.answers.f60;
  let finishSave;
  api.put.mockImplementationOnce((_,body) => new Promise(resolve => { finishSave=()=>{
    about.answers={...about.answers,...body.answers}; resolve(response());
  }; }));
  await open("/app/personal?tab=further");
  await click(query("quiz-opt-f60-0"));
  expect(query("personal-tab-results")).toBeNull();
  await click(query("personal-save"));
  expect(query("personal-tab-results")).toBeNull();
  await act(async()=>finishSave());
  expect(query("personal-tab-results")).not.toBeNull();
  await click(query("personal-tab-results"));
  expect(query("personal-portrait").textContent).toContain("108 saved answers");
  expect(api.get.mock.calls.filter(([path])=>path === "/me/assessments")).toHaveLength(1);
  await click(query("personal-tab-basics"));
  await click(query("quiz-clear-s1"));
  expect(query("personal-tab-results")).toBeNull(); expect(query("personal-tags")).toBeNull();
  expect(about.answers.s1).toBeDefined();
});

test("a results save refreshes the merged Personal profile without another read", async () => {
  await open();
  api.put.mockImplementationOnce(async (_, body) => {
    about.mbti=body.mbti; about.describe="Fresh description from the other tab";
    about.demographics={age:38};
    about.answers.s1={...about.answers.s1,choice:1,label:"Second choice"};
    return response();
  });
  await click(query("choose-type")); await click(query("review-basics-now"));
  expect(query("personal-describe-input").value).toBe("Fresh description from the other tab");
  expect(query("personal-age").value).toBe("38");
  expect(query("quiz-opt-s1-1").getAttribute("aria-pressed")).toBe("true");
  expect(api.get.mock.calls.map(([path])=>path)).toEqual(["/me/assessments"]);
});
