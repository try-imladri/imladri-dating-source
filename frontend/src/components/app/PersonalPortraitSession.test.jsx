import { act } from "react";
import { createRoot } from "react-dom/client";
import { useOutletContext, useSearchParams } from "react-router-dom";
import Personal from "../../pages/app/Personal";
import IntimacyPage from "../../pages/app/IntimacyPage";

jest.mock("react-router-dom", () => ({ useOutletContext: jest.fn(), useSearchParams: jest.fn(), useNavigate: () => jest.fn() }));
jest.mock("@/lib/api", () => ({ errMsg: jest.fn() }));
jest.mock("@/components/app/Personality", () => ({ PersonalityPanel: () => null }));
jest.mock("@/components/app/Patterns", () => ({ PatternsPanel: () => null }));

test("a member change immediately removes the previous adult's results and rechecks the new member's saved age", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement("div");
  const root = createRoot(host);
  const quiz = { short: [], extended: [], further: [] };
  let adult = true, finishFetch;
  const api = { get: jest.fn((path) => {
    if (path === "/quiz") return Promise.resolve({ data: quiz });
    if (path === "/me/intimacy-answers") return Promise.resolve({ data: { revision: 0, answers: {}, previous: 0 } });
    if (path !== "/me/about" && path !== "/me/assessments?include_intimacy=true") throw new Error(`Unexpected API path ${path}`);
    const age = adult ? 32 : 17;
    const data = { quiz, owner_id: adult ? "member-a" : "member-b", about: { answers: {}, demographics: { age } }, summary: {}, radar: [] };
    if (adult) return Promise.resolve({ data });
    return new Promise((resolve) => { finishFetch = () => resolve({ data }); });
  }), patch: jest.fn(async () => ({ data: { revision: 1 } })) };
  function show(id) {
    useOutletContext.mockReturnValue({ me: { user_id: id, onboarded: true }, setMe: jest.fn(), api });
    return act(async () => root.render(<IntimacyPage />));
  }
  function click(label) {
    const node = [...host.querySelectorAll("button")].find(b => label.test(b.textContent));
    expect(node).toBeDefined();
    return act(async () => node.click());
  }
  try {
    await show("member-a");
    expect(host.textContent).not.toContain("I’m 18 or older");
    await click(/Intimacy & communication/);
    await click(/Starting the conversation/);
    await click(/^Curious$/);
    await click(/^See my answers so far$/);
    expect(host.textContent).toContain("Your intimacy summary");
    adult = false;
    await show("member-b");
    expect(host.textContent).not.toMatch(/Your intimacy summary|I’m 18 or older|Choosing a time to talk/);
    await act(async () => finishFetch());
    expect(host.textContent).toContain("For adults aged 18 and over");
    expect(host.textContent).not.toMatch(/Your intimacy summary|I’m 18 or older|Choosing a time to talk/);
    expect(api.get.mock.calls.filter(([path]) => path === "/me/assessments?include_intimacy=true")).toHaveLength(2);
    expect(api.get.mock.calls.filter(([path]) => path === "/me/intimacy-answers")).toEqual([
      ["/me/intimacy-answers", expect.objectContaining({ headers: { "X-Questionnaire-Owner": "member-a" } })],
    ]);
    expect(api.patch).toHaveBeenCalledWith("/me/intimacy-answers", expect.objectContaining({ revision: 0, adult_confirmed: true }),
      { headers: { "X-Questionnaire-Owner": "member-a" } });
  } finally {
    act(() => root.unmount());
  }
});

test.each(["profile update", "account refresh"])("an old member's delayed %s cannot replace the active account", async (pending) => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement("div");
  const root = createRoot(host);
  const setMe = jest.fn();
  const quiz = { short: [], extended: [], further: [] };
  const saved = { about: { answers: {}, describe: "Quiet weekends" }, summary: {}, radar: [] };
  let finishRequest;
  const delayed = new Promise((resolve) => { finishRequest = resolve; });
  const api = {
    get: jest.fn((path, options) => {
      if (path === "/quiz") return Promise.resolve({ data: quiz });
      if (path === "/me/about") return Promise.resolve({ data: saved });
      if (path === "/me/assessments") return Promise.resolve({ data: { quiz, ...saved, owner_id: options.headers["X-Questionnaire-Owner"] } });
      if (path === "/me") return pending === "account refresh" ? delayed : Promise.resolve({ data: { user_id: "a" } });
      throw new Error(`Unexpected API path ${path}`);
    }),
    put: jest.fn(() => pending === "profile update" ? delayed : Promise.resolve({ data: saved })),
  };
  function show(id) {
    useOutletContext.mockReturnValue({ me: { user_id: id, onboarded: true }, api, setMe });
    return act(async () => root.render(<Personal />));
  }
  useSearchParams.mockReturnValue([new URLSearchParams("tab=basics"), jest.fn()]);
  try {
    await show("a");
    await act(async () => host.querySelector('[data-testid="personal-save"]').click());
    expect(api.put).toHaveBeenCalledTimes(1);
    if (pending === "account refresh") expect(api.get).toHaveBeenCalledWith("/me");
    await show("b");
    await act(async () => finishRequest({ data: pending === "profile update" ? saved : { user_id: "a" } }));
    expect(setMe).not.toHaveBeenCalled();
    if (pending === "profile update") expect(api.get).not.toHaveBeenCalledWith("/me");
  } finally {
    act(() => root.unmount());
  }
});
