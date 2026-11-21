import { act } from "react";
import { createRoot } from "react-dom/client";
import { useNavigate, useOutletContext, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import Personal from "./Personal";
import { PatternsPanel } from "@/components/app/Patterns";

jest.mock("react-router-dom", () => ({ useNavigate: jest.fn(), useOutletContext: jest.fn(), useSearchParams: jest.fn(),
  Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a> }));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn(), message: jest.fn() } }));
jest.mock("@/lib/api", () => ({ errMsg: () => "Save failed" }));
jest.mock("@/components/app/Personality", () => ({ PersonalityPanel: () => null }));
jest.mock("@/components/app/Patterns", () => ({ PatternsPanel: jest.fn(() => null) }));

const quiz = { short: [{ id: "s1", q: "Pick one", options: [{ label: "One" }] }], extended: [], further: [] };
const member = { user_id: "one", onboarded: true };
let root, host, api, setMe, navigate, about;

function response(profile) {
  return { data: { about: profile, summary: { describe: profile.describe || "" }, radar: [] } };
}

function show(me = member, withinCouples = false) {
  useOutletContext.mockReturnValue({ me, api, setMe });
  return act(async () => root.render(<Personal withinCouples={withinCouples} />));
}

const control = (id) => host.querySelector(`[data-testid="${id}"]`);
const click = (id) => act(async () => control(id).click());

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  setMe = jest.fn();
  navigate = jest.fn();
  useNavigate.mockReturnValue(navigate);
  useSearchParams.mockReturnValue([new URLSearchParams(), jest.fn()]);
  about = { answers: {}, describe: "", demographics: {} };
  api = {
    get: jest.fn(async (path) => {
      if (path === "/me") return { data: member };
      if (path === "/me/assessments") return { data: { quiz, owner_id: "one", ...response(about).data } };
      return path === "/quiz" ? { data: quiz } : response(about);
    }),
    put: jest.fn(async () => response({ answers: {}, describe: "", demographics: {} })),
  };
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

test("Personal retains its questionnaire sections without a nested Your Type tab", async () => {
  await show();
  expect(control("personal-tab-your-type")).toBeNull();
  expect(control("personal-tabs").querySelectorAll("button")).toHaveLength(4);
  expect(control("personal-tab-basics").getAttribute("aria-current")).toBe("page");
});

test("Couples reuses its four Personal sections, explains separate sharing and saves only the signed-in member", async () => {
  const paired = { ...member, onboarded: false, onboarding_step: 1 };
  await show(paired, true);
  expect(control("personal-title").textContent).toBe("My assessments");
  expect(control("personal-tabs").querySelectorAll("button")).toHaveLength(4);
  expect(control("personal-tab-your-type")).toBeNull();
  expect(control("couples-assessments-privacy").textContent).toContain("Joining Couples does not share");
  expect(control("couples-assessments-privacy").querySelector('a[href="/app/couples/compatibility"]')).not.toBeNull();
  expect(control("personal-save").textContent).toBe("Save");
  api.get.mockResolvedValueOnce({ data: { ...paired, onboarding_step: 2 } });
  await click("quiz-opt-s1-0"); await click("personal-save");
  expect(api.put).toHaveBeenCalledWith("/me/about", { expected_member: "one", answers: {
    s1: { choice: 0, question: "Pick one", label: "One", note: "" },
  } });
  expect(navigate).not.toHaveBeenCalled();
  expect(api.put.mock.calls.every(([path]) => path === "/me/about")).toBe(true);
});

test("Couples keeps saved observations available while excluding Individual generation", async () => {
  useSearchParams.mockReturnValue([new URLSearchParams("tab=patterns"), jest.fn()]);
  await show(member, true);
  expect(PatternsPanel.mock.calls.at(-1)[0]).toMatchObject({ api, allowScan: false });
  expect(api.put).not.toHaveBeenCalled();
});

test("the profile identifies the actual active subscription and opens Plans", async () => {
  await show({ ...member, usage: { plan: "plus", period_verified: true, plan_id: "plus_500", plan_name: "Plus 500" } });
  expect(control("personal-plan").textContent).toBe("Plus 500");
  expect(control("personal-plan").getAttribute("href")).toBe("/app/settings/upgrade");
});

test.each(["answer", "self-description"])("the final saved %s can be cleared", async (kind) => {
  if (kind === "answer") about.answers = { s1: { choice: 0, note: "" } };
  else about.describe = "Thoughtful";
  await show();
  if (kind === "answer") {
    await click("quiz-clear-s1");
  } else {
    const textarea = control("personal-describe-input");
    const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
    act(() => {
      setValue.call(textarea, "");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
  expect(control("personal-save").disabled).toBe(false);
  await click("personal-save");
  expect(api.put).toHaveBeenCalledWith("/me/about", { expected_member:"one", ...(kind === "answer" ? { answers: { s1: null } } : { answers: {}, describe: "" }) });
  expect(toast.success).toHaveBeenCalledWith("Your everyday Personal answers were saved.");
  expect(control("personal-save").disabled).toBe(true);
});

test("a failed profile request can be retried without leaving Personal", async () => {
  api.get.mockRejectedValueOnce(new Error("Temporary failure"));
  await show();
  expect(host.querySelector('[role="alert"]').textContent).toContain("couldn’t load your Personal answers");
  await click("personal-retry");
  expect(host.querySelector('[role="alert"]')).toBeNull();
  expect(control("quiz-opt-s1-0")).toBeTruthy();
});

test("an onboarding save advances the member who started it", async () => {
  const current = { user_id: "one", onboarded: false, onboarding_step: 1 };
  const fresh = { ...current, onboarding_step: 2 };
  await show(current);
  api.get.mockResolvedValueOnce({ data: fresh });
  await click("quiz-opt-s1-0");
  await click("personal-save");
  expect(setMe).toHaveBeenCalledWith(fresh);
  expect(toast.success).toHaveBeenCalledWith("Saved. Now add the person on your mind.");
  expect(navigate).toHaveBeenCalledWith("/app/people?add=1");
});

test("saving a question preserves another tab’s description and demographics on the next save", async () => {
  about.describe = "Original description";
  about.demographics = { age: 30 };
  const answer = { choice: 0, note: "", question: "Pick one", label: "One" };
  api.put.mockResolvedValue(response({
    answers: { s1: answer }, describe: "From the other tab", demographics: { age: 32 },
  }));
  await show();
  await click("quiz-opt-s1-0");
  await click("personal-save");
  expect(api.put).toHaveBeenNthCalledWith(1, "/me/about", { expected_member:"one", answers: { s1: answer } });
  expect(control("personal-age").value).toBe("32");
  expect(control("personal-describe-input").value).toBe("From the other tab");
  await click("personal-save");
  expect(api.put).toHaveBeenNthCalledWith(2, "/me/about", { expected_member:"one", answers: {} });
});

test("profile entry fields have visible labels associated with their controls", async () => {
  await show();
  const fields = [
    ["personal-describe-input", "How would your best friend describe you?"],
    ["personal-age", "Age"],
    ["personal-sex", "Sex"],
    ["personal-ethnicity", "Ethnicity"],
    ["personal-sexuality", "Sexuality"],
    ["personal-education", "Education"],
  ];
  for (const [testId, text] of fields) {
    const field = control(testId);
    const label = Array.from(host.querySelectorAll("label")).find(element => element.textContent === text);
    expect(label).toBeDefined();
    expect(field.id).toBeTruthy();
    expect(label.htmlFor).toBe(field.id);
  }
});

test.each([false, true])("sexuality is an optional saved profile field below ethnicity in Couples=%s", async withinCouples => {
  about.demographics = { age: 30, gender: "Man", ethnicity: "Mixed", sexuality: "bi" };
  api.put.mockImplementation(async (_, body) => {
    for (const [key, value] of Object.entries(body.demographics_patch || {})) {
      if (value == null) delete about.demographics[key];
      else about.demographics[key] = value;
    }
    return response(about);
  });
  const originalScroll = HTMLElement.prototype.scrollIntoView;
  HTMLElement.prototype.scrollIntoView = jest.fn();
  const choose = async option => {
    await act(async () => control("personal-sexuality").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
    await act(async () => document.querySelector(`[data-testid="personal-sexuality-${option}"]`).click());
  };
  try {
    await show(member, withinCouples);
    expect(control("personal-sexuality").textContent).toContain("Bisexual");
    const ethnicity = control("personal-ethnicity").parentElement;
    expect(ethnicity.nextElementSibling.contains(control("personal-sexuality"))).toBe(true);
    expect(ethnicity.classList.contains("col-span-2")).toBe(true);
    expect(ethnicity.nextElementSibling.classList.contains("col-span-2")).toBe(true);
    await choose("gay"); await click("personal-save");
    expect(api.put).toHaveBeenLastCalledWith("/me/about", { expected_member: "one", answers: {}, demographics_patch: { sexuality: "gay" } });
    expect(about.demographics).toEqual({ age: 30, gender: "Man", ethnicity: "Mixed", sexuality: "gay" });
    await choose("clear"); await click("personal-save");
    expect(api.put).toHaveBeenLastCalledWith("/me/about", { expected_member: "one", answers: {}, demographics_patch: { sexuality: null } });
    expect(about.demographics.sexuality).toBeUndefined();
    expect(control("personal-sexuality").textContent).toContain("Choose");
  } finally { HTMLElement.prototype.scrollIntoView = originalScroll; }
});

test("saved gender and sex do not infer a sexuality selection", async () => {
  about.demographics = { gender: "Man", sex: "Male" };
  await show();
  expect(control("personal-sexuality").textContent).toContain("Choose");
  expect(api.put).not.toHaveBeenCalled();
});

test("a partial answer and height autosave without pressing Save or advancing onboarding", async () => {
  jest.useFakeTimers();
  const selected = { choice: 0, question: "Pick one", label: "One", note: "" };
  api.put.mockResolvedValue(response({ ...about, answers: { s1: selected }, demographics: { height_cm: 175 } }));
  try {
    await show();
    await click("quiz-opt-s1-0");
    act(() => {
      const height = control("personal-height");
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(height, "175");
      height.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { jest.advanceTimersByTime(500); });
    expect(api.put).toHaveBeenCalledWith("/me/about", {
      expected_member: "one", answers: { s1: selected }, demographics_patch: { height_cm: 175 },
    });
    expect(host.textContent).toContain("Profile saved");
    expect(navigate).not.toHaveBeenCalled();
  } finally {
    jest.useRealTimers();
  }
});

test("the first answer saves while an unfinished height stays in the input for correction", async () => {
  jest.useFakeTimers();
  const selected = { choice:0, question:"Pick one", label:"One", note:"" };
  const enterHeight = value => act(() => {
    const input = control("personal-height");
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,value);
    input.dispatchEvent(new Event("input",{bubbles:true}));
  });
  try {
    api.put.mockResolvedValueOnce(response({...about,answers:{s1:selected}}))
      .mockResolvedValueOnce(response({...about,answers:{s1:selected},demographics:{height_cm:175}}));
    await show();
    enterHeight("1");
    await click("quiz-opt-s1-0");
    await act(async () => jest.advanceTimersByTime(451));
    expect(api.put).toHaveBeenNthCalledWith(1,"/me/about",{expected_member:"one",answers:{s1:selected}});
    expect(control("personal-height").value).toBe("1");
    enterHeight("175");
    await act(async () => jest.advanceTimersByTime(451));
    expect(api.put).toHaveBeenNthCalledWith(2,"/me/about",{expected_member:"one",answers:{},demographics_patch:{height_cm:175}});
  } finally {
    jest.useRealTimers();
  }
});
