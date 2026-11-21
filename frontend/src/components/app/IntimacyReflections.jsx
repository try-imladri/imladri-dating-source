// Each pair names two specific questions and describes only what those questions ask.
export const reflectionPairs = [
  [["Taking the lead", "taking the lead in a scene"], ["Being guided", "being guided by a partner"],
    "Leading a scene and having a partner guide one both appeal to you. What interests you about each role?"],
  [["Impact in fantasy", "imagining impact play"], ["Receiving impact", "being on the receiving end of impact play"]],
  [["Anticipation", "planning a date and looking forward to intimacy"], ["Spontaneous invitations", "getting a spontaneous invitation to be close"],
    "You’re interested in the anticipation of a planned date and the surprise of a spontaneous invitation."],
  [["Some time alone", "having time to yourself after a scene"], ["Quiet time together", "sitting quietly together afterward"],
    "Quiet company and time alone both appeal to you after a scene. Which would you welcome first?"],
  [["Offering a service", "doing a task for a partner as part of a role"], ["Receiving a service", "having a partner do something for you in that role"],
    "You’re interested in offering a small act of service and in having a partner offer one to you."],
  [["Taking the lead with restraint", "taking the active role in restraint"], ["Being restrained", "being restrained yourself"]],
  [["Imagining an audience", "imagining an audience in private"], ["A small invited audience", "being seen by a small group of invited adults"]],
  [["Giving sensation", "choosing a sensation for a partner"], ["Receiving sensation", "being on the receiving end of sensation play"]],
  [["A direct invitation", "a partner asking directly about intimacy"], ["Making the first move", "making the first move yourself"]],
  [["Flirting by text", "flirting by text"], ["Keeping intimacy in person", "keeping intimate conversations for when you’re together"]],
  [["Openness in imagination", "imagining other partners while keeping a relationship exclusive"], ["An open sexual relationship", "a mutually agreed open sexual relationship"]],
  [["Humiliation as a fictional theme", "humiliation as a theme in fiction"], ["Hearing a chosen put-down in role", "hearing a pretend put-down you chose for a scene"]],
];

const describe = {
  Interested: (topic) => `You’re interested in ${topic}.`,
  Curious: (topic) => `You’re curious about ${topic}.`,
  "Not for me": (topic) => `You said ${topic} doesn’t appeal to you.`,
  "Off limits": (topic) => `You marked ${topic} as off limits.`,
};

function displayPriority(choices) {
  const open = choices.includes("Interested") || choices.includes("Curious");
  if (choices.includes("Off limits")) return open ? 0 : 1;
  if (choices.includes("Not for me")) return 2;
  return 3;
}

export function IntimacyReflections({ entries }) {
  const answers = new Map(entries.map(({ topic, choice }) => [topic, choice]));
  const observations = reflectionPairs.flatMap(([[first, firstText], [second, secondText], bothInterested]) => {
    const a = answers.get(first);
    const b = answers.get(second);
    // Conditional and skipped answers can't support a reflection on their own.
    if (!describe[a] || !describe[b]) return [];
    if (a === b) return a === "Interested" && bothInterested
      ? [{ key: first, priority: 4, paired: true, text: bothInterested }] : [];
    return [{ key: first, priority: displayPriority([a, b]), text: describe[a](firstText) + " " + describe[b](secondText) }];
  }).sort((a, b) => a.priority - b.priority).slice(0, 3);

  if (!observations.length) return null;
  const groups = [
    ["Where your answers differ", observations.filter((entry) => !entry.paired)],
    ["Paired interests", observations.filter((entry) => entry.paired)],
  ];
  return groups.filter(([, items]) => items.length > 0).map(([heading, items]) => (
    <div key={heading} className="space-y-2">
      <h3 className="font-semibold">{heading}</h3>
      <ul className="list-disc space-y-1.5 pl-5 text-sm leading-relaxed">
        {items.map(({ key, text }) => <li key={key}>{text}</li>)}
      </ul>
    </div>
  ));
}
