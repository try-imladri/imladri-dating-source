import { buildSavedPortrait, portraitConnections } from "./personalPortraitModel";

const quiz = {
  short: [{
    id: "s1", q: "When something bothers you, you usually…", axis: "Directness",
    options: [
      { label: "Say it straight away" }, { label: "Hint and hope they notice" },
      { label: "Wait until I've cooled down, then talk" }, { label: "Keep it to myself" },
    ],
  }],
  extended: [], further: [],
};
const summary = { describe: "I think things over.", personality: { code: "ISFP", name: "The Artist", self_set: true } };
const privateEntry = {
  key: "conversations/0", sectionId: "conversations", topic: "Choosing a time to talk", choice: "It depends",
  prompt: "Would you like to pick a quiet time in advance to talk about intimacy?",
  conditions: ["In private"], followUp: "What would make it easier to talk?",
};

test("the portrait uses the exact saved answer and its source without including free-text notes", () => {
  const profile = buildSavedPortrait(quiz, { s1: { choice: 0, note: "private note sentinel" } }, summary);
  expect(profile.count).toBe(1);
  expect(profile.type).toMatchObject({ code: "ISFP", selfSet: true });
  expect(profile.description).toBe(summary.describe);
  expect(profile.groups.flatMap(({ items }) => items)).toEqual([
    expect.objectContaining({ id: "s1", axis: "Directness", question: quiz.short[0].q, answer: "Say it straight away", section: "The basics" }),
  ]);
  expect(JSON.stringify(profile)).not.toContain("private note sentinel");
});

test("missing and outdated quiz answers cannot create evidence or turn a tentative type guess into a type", () => {
  for (const saved of [{}, { s1: null }, { s1: { choice: 20 }, gone: { choice: 0 } }]) {
    const profile = buildSavedPortrait(quiz, saved, { personality: { code: "", guess: "ENFJ" } });
    expect(profile.count).toBe(0);
    expect(profile.type).toBeNull();
    expect(portraitConnections(profile, [privateEntry])).toEqual([]);
  }
});

test("a cross-topic connection requires matching saved and private evidence and preserves conditions", () => {
  const profile = buildSavedPortrait(quiz, { s1: { choice: 2 } }, summary);
  expect(portraitConnections(profile, [])).toEqual([]);
  const connections = portraitConnections(profile, [privateEntry]);
  expect(connections).toHaveLength(1);
  expect(connections[0].entry).toMatchObject({ choice: "It depends", conditions: ["In private"] });
  expect(connections[0].everyday).toMatchObject({ id: "s1", answer: quiz.short[0].options[2].label });
  expect(portraitConnections(profile, [{ ...privateEntry, key: "unrelated/0", sectionId: "unrelated", topic: "Unrelated interest" }])).toEqual([]);
});

test("admin-edited questions remain literal and cannot trigger a relationship interpretation from a reused ID", () => {
  const edited = { ...quiz, short: [{ ...quiz.short[0], q: "How do you learn a new board game?" }] };
  const profile = buildSavedPortrait(edited, { s1: { choice: 1 } }, {});
  expect(profile.groups[0].items[0].question).toBe(edited.short[0].q);
  expect(portraitConnections(profile, [privateEntry])).toEqual([]);
});

test("everyday decision-making coexists with the stated BDSM boundary and prioritizes it for the comparison", () => {
  const customQuiz = { ...quiz, further: [{
    id: "f40", q: "Big decisions about your life you make…", axis: "Independence",
    options: [{ label: "Alone" }, { label: "With advice" }, { label: "Together" }],
  }] };
  const saved = buildSavedPortrait(customQuiz, { f40: { choice: 2 } }, {});
  const base = { sectionId: "exchange", conditions: [] };
  const entries = [
    { ...base, topic: "Taking the lead", choice: "Interested" },
    { ...base, topic: "Being guided", choice: "Off limits" },
  ];
  const [match] = portraitConnections(saved, entries);
  expect(match.id).toBe("decisions");
  expect(match.everyday.answer).toBe("Together");
  expect(match.entry).toBe(entries[1]);
  expect(portraitConnections(saved, [{ ...base, topic: "Unrecognized topic", choice: "Interested" }])).toEqual([]);
});
