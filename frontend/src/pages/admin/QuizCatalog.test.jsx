import { act } from "react";
import { createRoot } from "react-dom/client";
import { IntimacyPanel } from "@/components/app/Intimacy";
import { intimacyTopics, publishedTopics } from "@/components/app/intimacyTopics";
import { readings, themes } from "@/components/app/personalPortraitModel";
import { reflectionPairs } from "@/components/app/IntimacyReflections";
import QuizCatalog from "./QuizCatalog";
import { reflections, followUp } from "@/lib/couplesQuestions";

jest.mock("@/lib/api", () => ({ errMsg: e => e.message }));

let root, host;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});
const byTest = id => host.querySelector('[data-testid="' + id + '"]');
const click = element => act(async () => element.click());
function button(text) {
  return [...host.querySelectorAll("button")].find(node => node.textContent.includes(text));
}
function type(textarea, value) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

test("staff can browse all 22 sections, publish question wording and read it in the private member flow", async () => {
  const api = { get: jest.fn().mockResolvedValue({ data: { overrides: [] } }),
    put: jest.fn(async (_, edit) => ({ data: { ...edit, revision: 1 } })) };
  await act(async () => root.render(<QuizCatalog api={api}><p>Everyday questions</p></QuizCatalog>));
  expect(byTest("admin-quiz-catalog").querySelectorAll("button")).toHaveLength(7);
  await click(byTest("admin-quiz-catalog-intimacy"));
  const panel = byTest("admin-intimacy-questions");
  expect(panel.querySelector("select").options).toHaveLength(8);
  expect(panel.querySelectorAll("details form")).toHaveLength(8);
  await click(byTest("admin-intimacy-track-bdsm"));
  expect(panel.querySelector("select").options).toHaveLength(14);
  expect(byTest("admin-intimacy-track-bdsm").textContent).toContain("112");
  expect(panel.textContent).toContain(intimacyTopics.bdsm[0].followUp);
  await click(byTest("admin-intimacy-track-talk"));
  const form = panel.querySelector("details form");
  const [question, phrase] = form.querySelectorAll("textarea");
  type(question, "Would you enjoy discussing expectations together?");
  type(phrase, "discussing expectations together");
  // Preserve unpublished work if staff briefly visit another catalog.
  await click(byTest("admin-quiz-catalog-everyday"));
  await click(byTest("admin-quiz-catalog-intimacy"));
  expect(form.querySelector("textarea").value).toMatch(/discussing expectations/);
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(api.put).toHaveBeenCalledTimes(1);
  expect(api.put.mock.calls[0][0]).toBe("/admin/quiz/intimacy");
  const override = api.put.mock.calls[0][1];
  const section = intimacyTopics.talk[0];
  expect(override).toEqual({ section: section.id, topic: section.prompts[0][0], question: question.value, phrase: phrase.value, revision: 0 });
  expect(publishedTopics([override]).talk[0].editedTopics).toEqual([override.topic]);
  expect(panel.querySelector("summary").textContent).toContain(override.question);

  await act(async () => root.render(<IntimacyPanel onExit={() => {}} overrides={[override]} />));
  await click(button("I’m 18 or older"));
  await click(button("Intimacy & communication"));
  await click(button(section.title));
  expect(host.textContent).toContain(override.question);
  await click(button("Interested"));
  expect(api.put).toHaveBeenCalledTimes(1);
  expect(api.get).toHaveBeenCalledTimes(1);
});

test("saved staff wording survives a remount and stays distinct from the published summary", async () => {
  let draft;
  const api = {
    get: async () => ({data:{overrides:[], drafts:draft ? [draft] : []}}),
    put: async (url, body) => {
      expect(url).toBe("/admin/quiz/intimacy/draft");
      expect(body.draft_revision).toBe(0);
      draft = {...body, draft_revision:1};
      return {data:draft};
    },
  };
  const render = async key => {
    await act(async () => root.render(<QuizCatalog key={key} api={api} />));
    await click(byTest("admin-quiz-catalog-intimacy"));
    return byTest("admin-intimacy-questions").querySelector("details");
  };
  const first = await render("first");
  type(first.querySelector("textarea"), "Staff edit to consider?");
  await click(button("Save staff draft"));
  const second = await render("second");
  expect(second.querySelector("textarea").value).toBe("Staff edit to consider?");
  expect(second.querySelector("summary").textContent).not.toContain("Staff edit to consider?");
  expect(second.textContent).toContain("Saved staff draft");
  expect(second.querySelector("details").textContent).toContain("Staff edit to consider?");
});

test("guidance contains the type text and all the shipped reflection wording", async () => {
  const types = [{ code: "INFP", name: "The Soft Romantic", line: "A published result from the scoring service." }];
  const api = { get: jest.fn().mockResolvedValue({ data: { types } }) };
  await act(async () => root.render(<QuizCatalog api={api} />));
  await click(byTest("admin-quiz-catalog-results"));
  const reference = byTest("admin-result-guidance");
  const text = reference.textContent;
  expect(text).toContain(types[0].line);
  for (const section of Object.values(intimacyTopics).flat()) expect(text).toContain(section.followUp);
  for (const theme of themes) expect(text).toContain(theme.followUp);
  for (const [question, answers] of Object.values(readings)) {
    expect(text).toContain(question);
    for (const statement of Object.values(answers)) expect(text).toContain(statement);
  }
  for (const [[first], [second], together] of reflectionPairs) {
    expect(text).toContain(first + " · " + second);
    if (together) expect(text).toContain(together);
  }
  expect(api.get).toHaveBeenCalledTimes(1);
  expect(api.get.mock.calls[0][0]).toBe("/admin/quiz/results");
});

test("the Couples catalog includes every current reflection and follow-up prompt visible to partners", async () => {
  const api = { get: jest.fn().mockResolvedValue({ data: { draft: [], published: [], revision: 0 } }) };
  await act(async () => root.render(<QuizCatalog api={api} />));
  await click(byTest("admin-quiz-catalog-couples"));
  const panel = host.querySelector('[aria-label="Couples reflections and follow-up"]');
  expect(panel.querySelectorAll("details")).toHaveLength(3);
  for (const reflection of Object.values(reflections)) {
    for (const wording of [reflection.title, ...reflection.questions, reflection.discussion])
      expect(panel.textContent).toContain(wording);
  }
  for (const wording of [followUp.agreement, followUp.reviewDate, followUp.checkin, ...Object.values(followUp.choices), followUp.note, followUp.learning])
    expect(panel.textContent).toContain(wording);
  expect(api.get).toHaveBeenCalledTimes(1);
  expect(api.get.mock.calls[0][0]).toBe("/admin/couples/catalog");
});
