import { act } from "react";
import { createRoot } from "react-dom/client";
import { IntimacyPanel } from "./Intimacy";
import { intimacyTopics } from "./intimacyTopics";
import { buildSavedPortrait } from "./personalPortraitModel";

const adult = /I['’]m 18 or older/i;
const communication = /Intimacy & communication/i;
const boundaries = /BDSM interests & boundaries/i;
const sections = Object.values(intimacyTopics).flat();
const talkSection = intimacyTopics.talk[0];
const bdsmSection = intimacyTopics.bdsm[0];
let container, root, onExit;

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  onExit = jest.fn();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  jest.restoreAllMocks();
});

function show(underage = false, savedProfile, props = {}) {
  act(() => root.render(<IntimacyPanel underage={underage} savedProfile={savedProfile} onExit={onExit} {...props} />));
}

function button(label) {
  return Array.from(container.querySelectorAll("button")).find((node) => {
    const text = node.textContent.trim();
    return typeof label === "string" ? text.includes(label) : label.test(text);
  });
}

function click(label) {
  const node = button(label);
  expect(node).toBeDefined();
  act(() => node.click());
}

function start(activity, section) {
  show();
  click(adult);
  click(activity);
  click(section.title);
}

function heading(text) {
  return Array.from(container.querySelectorAll("h1,h2,h3,h4")).find((node) => node.textContent.trim() === text);
}

function finishSection() {
  // Bound the walk so a broken Skip cannot hang the suite.
  for (let i = 0; i < 8 && button(/^Skip$/); i += 1) click(/^Skip$/);
  expect(heading("Your section results")).toBeDefined();
}

test("both activities contain distinct eight-question sections with unique prompts", () => {
  expect(intimacyTopics.talk).toHaveLength(8);
  expect(intimacyTopics.bdsm).toHaveLength(14);
  expect(new Set(sections.map((section) => section.id)).size).toBe(sections.length);
  const questions = sections.flatMap((section) => {
    expect(section.prompts).toHaveLength(8);
    const labels = section.prompts.map(([topic]) => topic);
    expect(new Set(labels).size).toBe(labels.length);
    return section.prompts.map(([, question]) => question);
  });
  expect(new Set(questions).size).toBe(176);
});

test("known minors can exit and cannot see activities or the external test", () => {
  show(true);
  expect(button(adult)).toBeUndefined();
  expect(button(communication)).toBeUndefined();
  expect(button(boundaries)).toBeUndefined();
  expect(container.querySelector("a")).toBeNull();
  click(/Back to Personal/i);
  expect(onExit).toHaveBeenCalledTimes(1);
});

test("a saved member can inspect and remove one earlier choice without losing an earlier skip or current answer", async () => {
  const archivedId = "i:" + talkSection.id + ":1";
  const currentId = "i:" + talkSection.id + ":0";
  const earlierSkip = "i:" + talkSection.id + ":2";
  const api = {
    get: jest.fn(async () => ({data:{revision:4, answers:{[currentId]: {choice:"Off limits",conditions:[]}}, previous:2,
      previous_answers:[
        {id:archivedId,question:"Earlier choice wording?",answer:"It depends",conditions:["In private"],skipped:false},
        {id:earlierSkip,question:"Earlier skipped wording?",answer:null,conditions:[],skipped:true},
      ]}})),
    delete: jest.fn(async () => ({data:{revision:5}})),
  };
  show(false, null, {api, owner:"member", accountSaving:true});
  await act(async () => button(/save my progress/i).click());
  expect(container.textContent).toContain("Answers from earlier question versions (2)");
  expect(container.textContent).toContain("It depends · In private");
  await act(async () => button("Remove earlier answer").click());
  expect(api.delete).toHaveBeenCalledWith("/me/intimacy-answers",{headers:{"X-Questionnaire-Owner":"member"},params:{revision:4,item:archivedId}});
  expect(container.textContent).not.toContain("Earlier choice wording?");
  expect(container.textContent).toContain("Earlier skipped wording?");
  expect(container.textContent).toContain("Answers from earlier question versions (1)");
  click(/^See my summary$/);
  expect(container.textContent).toContain("1 answered · 0 skipped");
  expect(container.textContent).toContain("Off limits");
});

test("the adult gate reveals both activities without an outbound test link", () => {
  show();
  expect(button(communication)).toBeUndefined();
  expect(button(boundaries)).toBeUndefined();
  expect(container.querySelector("a")).toBeNull();
  click(adult);
  expect(button(communication)).toBeDefined();
  expect(button(boundaries)).toBeDefined();
  expect(container.querySelector('a[href*="bdsmtest.org"]')).toBeNull();
  expect(container.textContent).not.toContain("Take the original test");
});

test("a saved adult profile opens both full-width activities directly and keeps account saving", async () => {
  const api = {
    get: jest.fn(async () => ({data:{revision:2,answers:{},previous:0}})),
    patch: jest.fn(async () => ({data:{revision:3}})),
  };
  await act(async () => show(false, null, {api, owner:"adult-member", accountSaving:true, profileAdultEligible:true}));
  expect(button(adult)).toBeUndefined();
  expect(button(communication)).toBeDefined();
  expect(button(boundaries)).toBeDefined();
  expect(api.get).toHaveBeenCalledWith("/me/intimacy-answers",expect.objectContaining({headers:{"X-Questionnaire-Owner":"adult-member"}}));
  const chooser = container.querySelector('[data-testid="intimacy-activity-chooser"]');
  expect(chooser.classList.contains("w-full")).toBe(true);
  expect(chooser.className).not.toMatch(/max-w-/);
  expect(button(communication).parentElement.classList.contains("sm:grid-cols-2")).toBe(true);
  expect(button(communication).classList.contains("w-full")).toBe(true);
  expect(container.querySelector('a[href="/app/settings#questionnaire-research"]')).not.toBeNull();
  click(communication);click(talkSection.title);
  await act(async () => button(/^Curious$/).click());
  expect(api.patch).toHaveBeenCalledWith("/me/intimacy-answers",expect.objectContaining({
    revision:2,id:`i:${talkSection.id}:0`,selection:{choice:"Curious",conditions:[]},
  }),{headers:{"X-Questionnaire-Owner":"adult-member"}});
});

test("direct activity entry cannot override a minor or unknown-profile gate", () => {
  const api = {get:jest.fn()};
  show(true,null,{api,owner:"minor",accountSaving:true,profileAdultEligible:true});
  expect(button(communication)).toBeUndefined();expect(api.get).not.toHaveBeenCalled();
  show(false,null,{api,owner:"unknown",accountSaving:true});
  expect(button(/save my progress/)).toBeDefined();expect(api.get).not.toHaveBeenCalled();
});

test("changing account discards the former adult's loaded answers and ignores a late read", async () => {
  let resolveFirst;
  const api = {get:jest.fn()
    .mockImplementationOnce(() => new Promise(resolve => {resolveFirst=resolve;}))
    .mockResolvedValueOnce({data:{revision:0,answers:{},previous:0}})};
  show(false,null,{api,owner:"first",accountSaving:true,profileAdultEligible:true});
  const firstSignal=api.get.mock.calls[0][1].signal;
  await act(async () => show(false,null,{api,owner:"second",accountSaving:true,profileAdultEligible:true}));
  expect(firstSignal.aborted).toBe(true);
  await act(async () => resolveFirst({data:{revision:9,answers:{[`i:${talkSection.id}:0`]:{choice:"Off limits",conditions:[]}},previous:0}}));
  expect(button(/^See my summary$/)).toBeUndefined();
  click(communication);click(talkSection.title);
  expect(heading(talkSection.prompts[0][1])).toBeDefined();
  expect(button(/^Off limits$/).getAttribute("aria-pressed")).toBe("false");
});

test("a cached adult return reloads saved answers without repeating age confirmation", async () => {
  const api={get:jest.fn(async()=>({data:{revision:1,answers:{},previous:0}}))};
  await act(async()=>show(false,null,{api,owner:"adult",accountSaving:true,profileAdultEligible:true}));
  click(boundaries);
  act(()=>window.dispatchEvent(new Event("pagehide")));
  expect(api.get).toHaveBeenCalledTimes(1);
  const event=new Event("pageshow");Object.defineProperty(event,"persisted",{value:true});
  await act(async()=>window.dispatchEvent(event));
  expect(api.get).toHaveBeenCalledTimes(2);
  expect(button(adult)).toBeUndefined();expect(button(communication)).toBeDefined();
});

test("answers resume within a section, can be revised, and skipped topics stay out of the results", () => {
  start(communication, talkSection);
  const [[first, question], [second], [third, thirdQuestion]] = talkSection.prompts;
  expect(heading(question)).toBeDefined();
  click(/^Curious$/);
  click(/^Back$/);
  expect(button(/^Curious$/).getAttribute("aria-pressed")).toBe("true");
  click(/^Skip$/);
  click(/^Not for me$/);
  click(/^Choose another section$/i);
  click(talkSection.title);
  expect(heading(thirdQuestion)).toBeDefined();
  click(/^Off limits$/);
  finishSection();
  expect(document.activeElement).toBe(heading("Your section results"));
  expect(container.textContent).not.toContain(first);
  expect(container.textContent).toContain(second);
  expect(container.textContent).toContain(third);
  expect(container.textContent).toContain("Not for me");
  expect(container.textContent).toContain("Off limits");
  const picker = container.querySelector("select");
  const limit = Array.from(picker.options).find((option) => option.textContent.includes(third));
  act(() => {
    picker.value = limit.value;
    picker.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(container.querySelector("blockquote").textContent).toMatch(/telling a partner one thing I already enjoy/i);
  expect(container.querySelector("blockquote").textContent).toMatch(/firm limit/i);
});

test("a skipped section counts as visited without guessing any answers", () => {
  start(boundaries, bdsmSection);
  finishSection();
  expect(container.textContent).toMatch(/0 answered.*8 skipped/);
  expect(container.querySelector("dl")).toBeNull();
  expect(container.querySelector("select")).toBeNull();
  click(/^See my summary$/i);
  expect(container.textContent).toContain(`1 of ${sections.length} sections finished`);
  expect(container.querySelectorAll("details")).toHaveLength(1);
  expect(container.querySelector("details summary").textContent).toContain(bdsmSection.title);
});

test("the combined summary lists every firm limit and preserves each conditional answer", () => {
  start(boundaries, bdsmSection);
  click(/^Off limits$/);
  click(/^Off limits$/);
  click(/^It depends$/);
  click(/^In fantasy only$/);
  click(/^Continue$/i);
  click(/^See my answers so far$/i);
  const group = container.querySelector('[aria-label="Your limits and conditions"]');
  expect([...group.querySelectorAll("summary")].map(el => el.textContent)).toEqual(["Firm limits (2)", "Conditional answers (1)"]);
  const rows = [...group.querySelectorAll("li")].map(el => el.textContent);
  expect(rows[0]).toContain(bdsmSection.prompts[0][0]);
  expect(rows[1]).toContain(bdsmSection.prompts[1][0]);
  expect(rows[2]).toContain(bdsmSection.prompts[2][0]);
  expect(rows[2]).toMatch(/fantasy only/);
});

test("conditional answers keep only confirmed context and show the actual question in results", () => {
  start(communication, talkSection);
  const [, firstQuestion] = talkSection.prompts[0];
  click(/^Curious$/);
  click(/^Back$/);
  click(/^It depends$/);
  click(/^With more trust$/i);
  click(/^Back to answers$/i);
  expect(button(/^Curious$/).getAttribute("aria-pressed")).toBe("true");
  click(/^It depends$/);
  expect(button(/^With more trust$/i).getAttribute("aria-pressed")).toBe("false");
  click(/^With more trust$/i);
  click(/^In private$/i);
  click(/^Continue$/i);
  click(/^Back$/);
  expect(button(/^It depends$/).getAttribute("aria-pressed")).toBe("true");
  click(/^It depends$/);
  expect(button(/^With more trust$/i).getAttribute("aria-pressed")).toBe("true");
  expect(button(/^In private$/i).getAttribute("aria-pressed")).toBe("true");
  click(/^With more trust$/i);
  click(/^With a clear plan$/i);
  click(/^Continue$/i);
  finishSection();
  expect(container.textContent).toContain(firstQuestion);
  expect(container.textContent).toMatch(/in private/i);
  expect(container.textContent).toMatch(/with a clear plan/i);
  expect(container.textContent).not.toMatch(/with more trust/i);
  expect(container.querySelector("blockquote").textContent).toMatch(/privacy and a clear plan/i);
  click(/^See my summary$/i);
  expect(container.textContent).toContain(firstQuestion);
  act(() => container.querySelector("details summary").click());
  click(/^Review answers$/i);
  click(/^Interested$/);
  finishSection();
  expect(container.textContent).not.toMatch(/with a clear plan|in private/i);
  expect(container.textContent).toMatch(/1 answered.*7 skipped/);
});

test("an answer can depend on unspecified circumstances and can later be skipped without a preference", () => {
  start(boundaries, bdsmSection);
  click(/^It depends$/);
  click(/^Continue$/i);
  click(/^Off limits$/);
  finishSection();
  expect(container.textContent).toMatch(/2 answered.*6 skipped/);
  click(/^See my summary$/i);
  expect(container.querySelector("ul li")?.textContent || "").not.toMatch(/interested in taking the lead|curious about taking the lead/i);
  act(() => container.querySelector("details summary").click());
  click(/^Review answers$/i);
  click(/^Skip$/);
  finishSection();
  expect(container.textContent).toMatch(/0 answered.*8 skipped/);
  expect(container.querySelector("blockquote")).toBeNull();
});

test("reviewing a partial section from the overall summary updates that same set of answers", () => {
  start(boundaries, bdsmSection);
  click(/^Interested$/);
  click(/^Choose another section$/i);
  click(/^See my summary$/i);
  expect(container.textContent).toContain(`0 of ${sections.length} sections finished`);
  expect(container.textContent).toMatch(/1 answered.*0 skipped/);
  const details = container.querySelector("details");
  act(() => details.querySelector("summary").click());
  click(/^Review answers$/i);
  expect(heading(bdsmSection.prompts[0][1])).toBeDefined();
  expect(button(/^Interested$/).getAttribute("aria-pressed")).toBe("true");
  click(/^Skip$/);
  click(/^Choose another section$/i);
  click(/^See my summary$/i);
  expect(container.textContent).toMatch(/0 answered.*1 skipped/);
  expect(container.querySelector("blockquote")).toBeNull();
});

test("answers so far opens the summary directly and resumes the exact question, including during review", () => {
  start(boundaries, bdsmSection);
  expect(button(/^See my answers so far$/i)).toBeUndefined();
  click(/^It depends$/i);
  click(/^In fantasy only$/i);
  click(/^Continue$/i);
  click(/^See my answers so far$/i);
  expect(heading("Your intimacy summary")).toBeDefined();
  expect(container.textContent).toMatch(/in fantasy only/i);
  click(/^Continue where I left off$/i);
  expect(heading(bdsmSection.prompts[1][1])).toBeDefined();
  click(/^Back$/i);
  click(/^See my answers so far$/i);
  click(/^Continue where I left off$/i);
  expect(heading(bdsmSection.prompts[0][1])).toBeDefined();
  expect(button(/^It depends$/i).getAttribute("aria-pressed")).toBe("true");
});

test("including a saved portrait is optional and cached returns clear private answers and the combined view", () => {
  const description = "I’m a thoughtful listener.";
  const savedProfile = buildSavedPortrait({
    short: [{ id: "s1", axis: "Directness", q: "When something bothers you, you usually…", options: [{ label: "Say it straight away" }] }],
    extended: [], further: [],
  }, { s1: { choice: 0 } }, { describe: description, personality: { code: "", guess: "ENFJ" } });
  show(false, savedProfile);
  click(adult);
  click(communication);
  click(talkSection.title);
  click(/^It depends$/);
  click(/^In private$/);
  click(/^Continue$/);
  click(/^See my answers so far$/);
  expect(heading("Your personal portrait")).toBeUndefined();
  expect(container.textContent).not.toContain(description);
  click(/^Include my Personal answers$/i);
  expect(heading("Your personal portrait")).toBeDefined();
  expect(heading("How your answers fit together")).toBeDefined();
  expect(container.textContent).toContain("Say it straight away");
  expect(container.textContent).toContain(description);
  expect(container.textContent).toMatch(/in private|privacy/i);
  click(/^Hide my Personal answers$/i);
  expect(heading("How your answers fit together")).toBeUndefined();
  expect(container.textContent).not.toContain(description);
  click(/^Include my Personal answers$/i);
  act(() => window.dispatchEvent(new Event("pagehide")));
  expect(button(adult)).toBeDefined();
  click(adult);
  click(communication);
  click(talkSection.title);
  click(/^Interested$/);
  click(/^See my answers so far$/);
  expect(heading("Your personal portrait")).toBeUndefined();
  expect(container.textContent).not.toContain(description);
  expect(container.textContent).not.toMatch(/in private/i);
});

test("a comparison reflects two explicitly answered roles and disappears when one is skipped", () => {
  const power = intimacyTopics.bdsm.find((section) => section.id === "exchange");
  start(boundaries, power);
  click(/^Curious$/);
  click(/^Off limits$/);
  click(/^Choose another section$/i);
  click(/^See my summary$/i);
  expect(heading("Where your answers differ")).toBeDefined();
  const note = heading("Where your answers differ").parentElement.querySelector("li").textContent;
  expect(note).toMatch(/curious about taking the lead/i);
  expect(note).toMatch(/being guided by a partner as off limits/i);
  act(() => [...container.querySelectorAll("details summary")].find(el => el.textContent.includes(power.title)).click());
  click(/^Review answers$/i);
  click(/^Curious$/);
  click(/^Skip$/);
  click(/^Choose another section$/i);
  click(/^See my summary$/i);
  expect(heading("Where your answers differ")).toBeUndefined();
});

test("paired interests require an Interested answer to both prompts", () => {
  const power = intimacyTopics.bdsm.find((section) => section.id === "exchange");
  start(boundaries, power);
  click(/^Interested$/);
  click(/^Interested$/);
  click(/^Choose another section$/i);
  click(/^See my summary$/i);
  expect(heading("Paired interests")).toBeDefined();
  act(() => container.querySelector("details summary").click());
  click(/^Review answers$/i);
  click(/^Interested$/);
  click(/^It depends$/);
  click(/^In fantasy only$/i);
  click(/^Continue$/i);
  click(/^Choose another section$/i);
  click(/^See my summary$/i);
  expect(heading("Paired interests")).toBeUndefined();
  expect(heading("Where your answers differ")).toBeUndefined();
  expect(container.textContent).toMatch(/in fantasy only/i);
});

test("an overall summary includes answers across both activities without storage, requests, or history changes", () => {
  const storageWrite = jest.spyOn(Storage.prototype, "setItem");
  const networkRequest = jest.spyOn(XMLHttpRequest.prototype, "open");
  const initialUrl = window.location.href;
  const historyLength = window.history.length;
  start(communication, talkSection);
  click(/^Interested$/);
  finishSection();
  click(/^Choose another section$/i);
  click(/^Choose an activity$/i);
  click(boundaries);
  click(bdsmSection.title);
  click(/^Curious$/);
  finishSection();
  click(/^See my summary$/i);
  expect(heading("Your intimacy summary")).toBeDefined();
  expect(container.textContent).toContain(talkSection.prompts[0][0]);
  expect(container.textContent).toContain(bdsmSection.prompts[0][0]);
  expect(container.textContent).toContain(`2 of ${sections.length} sections finished`);
  expect(container.textContent).toMatch(/2 answered.*14 skipped/);
  expect(window.location.href).toBe(initialUrl);
  expect(window.history.length).toBe(historyLength);
  expect(storageWrite).not.toHaveBeenCalled();
  expect(networkRequest).not.toHaveBeenCalled();
  click(/^Clear & finish$/i);
  expect(onExit).toHaveBeenCalledTimes(1);
});

test.each(["pagehide", "pageshow"])("%s clears consent and answers before a cached return", (eventType) => {
  start(boundaries, bdsmSection);
  click(/^It depends$/);
  click(/^In fantasy only$/i);
  click(/^Continue$/i);
  click(/^It depends$/);
  click(/^In private$/i);
  const event = new Event(eventType);
  Object.defineProperty(event, "persisted", { value: true });
  act(() => window.dispatchEvent(event));
  expect(button(adult)).toBeDefined();
  expect(button(/^Off limits$/)).toBeUndefined();
  click(adult);
  expect(button(/^See my summary$/i)).toBeUndefined();
  click(boundaries);
  click(bdsmSection.title);
  expect(heading(bdsmSection.prompts[0][1])).toBeDefined();
  expect(button(/^It depends$/).getAttribute("aria-pressed")).toBe("false");
  click(/^It depends$/);
  expect(button(/^In fantasy only$/i).getAttribute("aria-pressed")).toBe("false");
  expect(button(/^In private$/i).getAttribute("aria-pressed")).toBe("false");
});

test("a completed visit submits the reviewed selections only with separate research consent", async () => {
  global.TextEncoder = require("util").TextEncoder;
  Object.defineProperty(window, "crypto", { configurable: true, value: { subtle: {
    digest: async (_, bytes) => Uint8Array.from(require("crypto").createHash("sha256").update(bytes).digest()).buffer,
  } } });
  const receipt = { submitted: false, revision: 3, consent_version: "research-v1" };
  const profile = { updated_at: "saved-version", answers: { s1: { question: "Personal question", label: "My saved choice", note: "a private note" } } };
  const api = { get: jest.fn(async (path) => ({ data: path === "/me/about" ? { about: profile } : receipt })), put: jest.fn().mockResolvedValue({
    data: { ...receipt, submitted: true, revision: 4, answered: 2, skipped: 174, everyday_answered: 1, age_band: "25-34" },
  }) };
  const saved = { count: 1, groups: [{ axis: "Directness", items: [{ id: "s1", question: "Personal question", answer: "My saved choice" }] }], description: "a private note" };
  // Compute the API-side digest independently, including any changes to the published seed.
  const inventory = sections.map(({ id, prompts }) => [id, prompts]).sort(([a], [b]) => a.localeCompare(b, "en"));
  const signature = require("crypto").createHash("sha256").update(JSON.stringify([
    ["Interested", "Curious", "It depends", "Not for me", "Off limits"], ["With more trust", "With a clear plan", "In private", "In fantasy only"], inventory,
  ])).digest("hex");
  show(false, saved, { api, catalogVersion: "observed-version", intimacySignature: signature });
  click(adult);
  for (const [activity, topics] of [[communication, intimacyTopics.talk], [boundaries, intimacyTopics.bdsm]]) {
    click(activity);
    for (const [index, topic] of topics.entries()) {
      click(topic.title);
      if (topic === talkSection) {
        click(/^It depends$/);
        click(/^In private$/);
        click(/^Continue$/);
        click(/^Off limits$/);
        click(/^See my answers so far$/);
        expect(button("Review research contribution")).toBeUndefined();
        click(/^Continue where I left off$/);
      }
      finishSection();
      if (topic !== sections[sections.length - 1]) {
        click(/^Choose another section$/);
        if (index === topics.length - 1) click(/^Choose an activity$/);
      }
    }
  }
  click(/^Review my completed questionnaire$/);
  expect(api.get).not.toHaveBeenCalled();
  expect(api.put).not.toHaveBeenCalled();
  await act(async () => button("Review research contribution").click());
  expect(api.get).toHaveBeenCalledWith("/me/research", expect.any(Object));
  const agree = container.querySelector('input[type="checkbox"]');
  expect(agree.checked).toBe(false);
  expect(button("Submit my contribution").disabled).toBe(true);
  expect(container.textContent).toContain("My saved choice");
  expect(container.textContent).not.toContain("a private note");
  act(() => agree.click());
  await act(async () => button("Submit my contribution").click());
  expect(api.put).toHaveBeenCalledTimes(1);
  const [path, body] = api.put.mock.calls[0];
  expect(path).toBe("/me/research");
  expect(body).toEqual({ consent: true, adult_confirmed: true, revision: 3, consent_version: "research-v1",
    catalog_version: "observed-version", profile_updated_at: "saved-version", demographic_fields: [], answers: Object.fromEntries(sections.map(({ id, prompts }) =>
      [id, prompts.map((_, i) => id === talkSection.id && i === 0 ? { choice: "It depends", conditions: ["In private"] } :
        id === talkSection.id && i === 1 ? { choice: "Off limits", conditions: [] } : null)])),
  });
  expect(container.textContent).toContain("2 intimacy answers · 174 skipped · 1 everyday answer · Age band 25-34");
  expect(button("Submit my contribution")).toBeUndefined();
});

test("an account resumes a conditional answer and a skip across visits and can delete that progress", async () => {
  const persisted = {};
  let revision = 0;
  const api = {
    get: jest.fn(async () => ({ data: { revision, answers: { ...persisted }, previous: 0 } })),
    patch: jest.fn(async (_, body) => {
      expect(body.revision).toBe(revision);
      persisted[body.id] = body.selection;
      return { data: { revision: ++revision } };
    }),
    delete: jest.fn(async (_, { params }) => {
      expect(params.revision).toBe(revision);
      if (params.item) delete persisted[params.item];
      else for (const key of Object.keys(persisted)) delete persisted[key];
      return { data: { revision: ++revision } };
    }),
  };
  const asyncClick = label => act(async () => { button(label).click(); });
  show(false, null, { api, accountSaving: true });
  expect(api.get).not.toHaveBeenCalled();
  await asyncClick(/save my progress/);
  click(communication);
  click(talkSection.title);
  click(/^It depends$/);
  click(/^With a clear plan$/);
  await asyncClick(/^Continue$/);
  await asyncClick(/^Skip$/);
  expect(api.patch.mock.calls.map(([path, body]) => [path, body.selection])).toEqual([
    ["/me/intimacy-answers", { choice: "It depends", conditions: ["With a clear plan"] }],
    ["/me/intimacy-answers", null],
  ]);
  const event = new Event("pagehide");
  act(() => window.dispatchEvent(event));
  await asyncClick(/save my progress/);
  click(communication);
  click(talkSection.title);
  expect(heading(talkSection.prompts[2][1])).toBeDefined();
  click(/^Back$/);
  await asyncClick(/^Remove my skip$/);
  expect(button(/^Remove my skip$/)).toBeUndefined();
  expect(Object.keys(persisted)).toEqual([`i:${talkSection.id}:0`]);
  click(/^See my answers so far$/);
  expect(container.textContent).toMatch(/1 answered.*0 skipped/);
  click(/^Continue where I left off$/);
  await asyncClick(/^Skip$/);
  click(/^See my answers so far$/);
  expect(container.textContent).toMatch(/1 answered.*1 skipped/);
  click(/^Choose another section$/);
  click(/^Choose an activity$/);
  click(/^Delete saved intimacy answers$/);
  click(/^Keep my answers$/);
  click(/^See my summary$/);
  expect(container.textContent).toMatch(/1 answered.*1 skipped/);
  click(/^Choose an activity$/);
  click(/^Delete saved intimacy answers$/);
  await asyncClick(/^Delete my answers$/);
  expect(button(/^See my summary$/)).toBeUndefined();
  expect(api.get).toHaveBeenCalledTimes(3);
  expect(api.delete).toHaveBeenCalledTimes(2);
});

test("resuming a sparse section fills only missing questions and still permits explicit sequential review", async () => {
  const answers = {};
  talkSection.prompts.forEach((_, index) => {
    if (index !== 1 && index !== 4) answers[`i:${talkSection.id}:${index}`] = index === 3 ? { choice:"Off limits", conditions:[] } : null;
  });
  const api = {
    get: jest.fn(async () => ({ data: { revision: 1, answers } })),
    patch: jest.fn(async () => ({ data: { revision: 2 } })),
  };
  show(false, null, { api, accountSaving:true });
  await act(async () => button(/save my progress/).click());
  click(communication);
  click(talkSection.title);
  expect(heading(talkSection.prompts[1][1])).toBeDefined();
  await act(async () => button(/^Skip$/).click());
  expect(heading(talkSection.prompts[4][1])).toBeDefined();
  await act(async () => button(/^Skip$/).click());
  expect(heading("Your section results")).toBeDefined();
  expect(container.textContent).toMatch(/1 answered.*7 skipped/);
  expect(api.patch.mock.calls.map(([,body]) => body.id)).toEqual([`i:${talkSection.id}:1`, `i:${talkSection.id}:4`]);
  click(/^Review answers$/);
  expect(heading(talkSection.prompts[0][1])).toBeDefined();
  await act(async () => button(/^Skip$/).click());
  expect(heading(talkSection.prompts[1][1])).toBeDefined();
});

test("the short check-in resumes across sections while preserving saved boundaries and skips", async () => {
  const api = {
    get: jest.fn(async () => ({ data: { revision: 1, answers: {
      "i:aftercare:0": null, "i:language-tone:0": {choice:"Off limits", conditions:[]},
    } } })),
    patch: jest.fn(async () => ({ data: { revision: 2 } })),
  };
  show(false, null, { api, accountSaving:true });
  await act(async () => button(/save my progress/).click());
  click(boundaries);
  click(/Short check-in/);
  expect(container.textContent).toContain("Short check-in 1 of 4");
  await act(async () => button(/^Skip$/).click());
  expect(container.textContent).toContain("Short check-in 3 of 4");
  await act(async () => button(/^Skip$/).click());
  expect(container.textContent).toMatch(/1 answered.*3 skipped/);
  expect(api.patch.mock.calls.map(([,body]) => body.id)).toEqual(["i:scene-planning:0", "i:privacy-community:0"]);
});

test("a rejected account write keeps the question unanswered and offers the saved copy", async () => {
  const api = {
    get: jest.fn(async () => ({ data: { revision: 2, previous: 0, answers: {} } })),
    patch: jest.fn().mockRejectedValue({ response: { status: 409, data: { detail: "Your saved answers changed." } } }),
  };
  show(false, null, { api, accountSaving: true });
  await act(async () => button(/save my progress/).click());
  click(communication);
  click(talkSection.title);
  await act(async () => button(/^Curious$/).click());
  expect(heading(talkSection.prompts[0][1])).toBeDefined();
  expect(container.querySelector("[role=alert]").textContent).toContain("Your saved answers changed.");
  await act(async () => button("Reload saved answers").click());
  expect(button(/^See my summary$/)).toBeUndefined();
  expect(api.get).toHaveBeenCalledTimes(2);
});
