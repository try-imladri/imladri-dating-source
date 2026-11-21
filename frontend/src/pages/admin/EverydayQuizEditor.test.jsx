import { act } from "react";
import { createRoot } from "react-dom/client";
import EverydayQuizEditor from "./EverydayQuizEditor";

jest.mock("@/lib/api", () => ({ errMsg: failure => failure.message }));

const item = (id, q) => ({ id, q, axis: "Openness", dim: "SN", note: true, options: [
  { label: "Often", tag: "engaged", axis_score: 4, type_score: 3 },
  { label: "Sometimes", tag: "depends", axis_score: 1 },
] });
const quiz = { short: [item("s1", "First?")], extended: [item("e1", "Deeper?")], further: [item("f1", "Further?")] };
const settings = { quiz, quiz_revision: "v1" };
let host, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
const id = key => host.querySelector('[data-testid="' + key + '"]');
const click = node => act(async () => node.click());
function type(node, value) {
  act(() => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), "value").set.call(node, value);
    node.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

test("structured and advanced edits share a draft and preserve all scoring fields", async () => {
  const save = jest.fn(async body => ({ data: { quiz: body.quiz } }));
  await act(async () => root.render(<EverydayQuizEditor settings={settings} save={save} />));
  type(id("admin-everyday-question"), "Updated first?");
  type(host.querySelector("fieldset input"), "Usually");
  await click(id("admin-quiz-sets-extended"));
  await click(id("admin-quiz-advanced"));
  type(id("admin-quiz-extended"), "oops");
  await click(id("admin-quiz-save"));
  expect(save).not.toHaveBeenCalled();
  expect(host.querySelector('[role="alert"]').textContent).toContain("Go deeper");
  type(id("admin-quiz-extended"), JSON.stringify([{ ...quiz.extended[0], q: "Updated deeper?" }]));
  await click(id("admin-quiz-sets-short"));
  await click(id("admin-quiz-advanced"));
  expect(id("admin-everyday-question").value).toBe("Updated first?");
  await click(id("admin-quiz-save"));
  const body = save.mock.calls[0][0];
  expect(body.quiz_revision).toBe("v1");
  expect(body.quiz.short[0]).toEqual({ ...quiz.short[0], q: "Updated first?", options: [
    { ...quiz.short[0].options[0], label: "Usually" }, quiz.short[0].options[1],
  ] });
  expect(body.quiz.extended[0].q).toBe("Updated deeper?");
  expect(body.quiz.further).toEqual(quiz.further);
});

test("a stale publication keeps the draft until staff explicitly reload", async () => {
  const reload = jest.fn(async () => ({ quiz, quiz_revision: "v2" }));
  const save = jest.fn(async () => ({ error: "Another editor updated the quiz.", status: 409 }));
  await act(async () => root.render(<EverydayQuizEditor settings={settings} save={save} reload={reload} />));
  type(id("admin-everyday-question"), "Unpublished");
  await click(id("admin-quiz-save"));
  expect(id("admin-everyday-question").value).toBe("Unpublished");
  expect(id("admin-quiz-save").disabled).toBe(true);
  await click(host.querySelector('[role="alert"] button'));
  expect(reload).toHaveBeenCalledTimes(1);
  expect(id("admin-everyday-question").value).toBe("First?");
});
