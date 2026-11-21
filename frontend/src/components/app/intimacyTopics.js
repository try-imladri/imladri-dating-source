// Original reflection questions. Two scenarios were informed by the public sample at bdsmtest.org.
import topics from "../../../../backend/seed/intimacy.json";

export const intimacyTopics = topics;
export const choices = ["Interested", "Curious", "It depends", "Not for me", "Off limits"];
export const conditions = ["With more trust", "With a clear plan", "In private", "In fantasy only"];

export async function matchesResearchCatalog(sections, expected) {
  if (!expected || !window.crypto?.subtle) return false;
  const pairs = sections.map(({ id, prompts }) => [id, prompts]);
  pairs.sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0);
  const bytes = new TextEncoder().encode(JSON.stringify([choices, conditions, pairs]));
  const hash = await window.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("") === expected;
}

export function publishedTopics(overrides = []) {
  if (!overrides.length) return topics;
  const edits = new Map(overrides.map(item => [item.section + "/" + item.topic, item]));
  return Object.fromEntries(Object.entries(topics).map(([track, sections]) => [track, sections.map(section => {
    const editedTopics = [];
    const prompts = section.prompts.map(([topic, question, phrase]) => {
      const edit = edits.get(section.id + "/" + topic);
      const copy = [topic, edit?.question || question, edit?.phrase || phrase];
      if (copy[1] !== question || copy[2] !== phrase) editedTopics.push(topic);
      return copy;
    });
    return { ...section, editedTopics, prompts };
  })]));
}
