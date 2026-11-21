const conditionPhrases = { "With more trust": "more trust", "With a clear plan": "a clear plan", "In private": "privacy" };
const capitalize = (text) => text.charAt(0).toUpperCase() + text.slice(1);

export function intimacyStatement({ phrase, choice, conditions = [] }) {
  if (choice === "Interested") return `I’m interested in ${phrase}.`;
  if (choice === "Curious") return `I’m curious about ${phrase}.`;
  if (choice === "Not for me") return `${capitalize(phrase)} doesn’t appeal to me.`;
  if (choice === "Off limits") return `${capitalize(phrase)} is a firm limit for me.`;
  if (choice !== "It depends") return "";

  const stance = conditions.includes("In fantasy only")
    ? `I’d like to keep the idea of ${phrase} in fantasy only.`
    : `How I feel about ${phrase} depends on the circumstances.`;
  const needs = conditions.map((condition) => conditionPhrases[condition]).filter(Boolean);
  if (!needs.length) return stance;
  const list = new Intl.ListFormat("en").format(needs);
  return `${stance} ${capitalize(list)} ${needs.length === 1 ? "matters" : "matter"} to me here.`;
}

export function conversationStarter(entry) {
  const line = intimacyStatement(entry);
  if (entry.choice === "Interested") return `${line} ${entry.followUp}`;
  if (entry.choice === "Curious") return `${line} I’d like to just talk about it for now. ${entry.followUp}`;
  if (entry.choice === "Not for me") return `${line} Could we talk about something we both enjoy?`;
  return line;
}

// When space is limited, keep a firm limit and its own context visible alongside an interest.
export function takeawayEntries(entries) {
  const first = (choice) => entries.find((entry) => entry.choice === choice);
  const boundary = first("Off limits");
  const conditional = entries.find((entry) => entry.conditions?.includes("In fantasy only")) || first("It depends");
  const highlights = [first("Interested") || first("Curious"), conditional, boundary || first("Not for me")].filter(Boolean);
  return highlights;
}
