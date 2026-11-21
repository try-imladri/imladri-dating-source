import { sectionNames as sections } from "@/lib/personalQuestionnaires";
import { intimacyTopics } from "./intimacyTopics";

const privateTopics = new Map(Object.values(intimacyTopics).flat().map((section) => [
  section.id, new Set(section.prompts.map(([label]) => label)),
]));

// Match the shipped prompt AND option text. The admin can edit either without changing the question ID.
export const readings = {
  s1: ["When something bothers you, you usually…", {
    "Say it straight away": "When something bothers me, I say so straight away.",
    "Hint and hope they notice": "When something bothers me, I tend to hint and hope the other person notices.",
    "Wait until I've cooled down, then talk": "When something bothers me, I like to cool down before I talk about it.",
    "Keep it to myself": "When something bothers me, I usually keep it to myself.",
  }],
  s3: ["In an argument you tend to…", {
    "Stand my ground calmly": "In an argument, I tend to stand my ground calmly.",
    "Get heated, then make up fast": "I can get heated in an argument and make up quickly afterward.",
    "Over-explain until they understand": "In an argument, I tend to keep explaining until I feel understood.",
    "Go quiet and avoid it": "In an argument, I tend to go quiet and avoid the discussion.",
  }],
  s4: ["What do you need most from the people close to you?", {
    "Consistency — do what you said": "I need the people close to me to follow through on what they say.",
    "Words — tell me how you feel": "I need the people close to me to tell me how they feel.",
    "Space and trust": "I need space and trust from the people close to me.",
    "Effort — show up and plan": "It matters to me that the people close to me show up and make plans.",
  }],
  s5: ["Your usual dating pattern is…", {
    "I fall fast and go all in": "When I date, I tend to fall fast and go all in.",
    "I take ages to open up": "I take a long time to open up to someone I’m dating.",
    "I pick people who aren't fully available": "I tend to date people who aren’t fully available.",
    "Steady — I take it as it comes": "When I date, I usually take things as they come.",
  }],
  s9: ["After a big week, you recharge by…", {
    "Going out with friends": "After a busy week, I recharge by going out with friends.",
    "A small dinner with one or two people": "After a busy week, a small dinner with one or two people helps me recharge.",
    "A quiet night in": "After a busy week, a quiet night in helps me recharge.",
    "Total alone time, phone off": "After a busy week, I recharge with time alone and my phone off.",
  }],
  s12: ["Your ideal weekend plan is…", {
    "Planned to the hour": "I like to plan my weekend down to the hour.",
    "A rough plan": "A rough plan is enough for my ideal weekend.",
    "One thing booked, the rest open": "I like one thing booked for the weekend and the rest open.",
    "No plan, see what happens": "I like to leave my weekend open and see what happens.",
  }],
  e2: ["After a fight you need…", {
    "Time alone first": "After a fight, I need some time alone first.",
    "To talk it through right away": "After a fight, I need to talk things through right away.",
    "A hug and no more words": "After a fight, I want a hug and some quiet.",
    "A clear apology": "After a fight, I need a clear apology.",
  }],
  e3: ["You feel most loved when someone…", {
    "Says it out loud": "I feel most loved when someone says it out loud.",
    "Spends real time with me": "I feel most loved when someone spends time with me.",
    "Does things for me": "I feel most loved when someone does things for me.",
    "Is physically affectionate": "I feel most loved when someone is physically affectionate.",
  }],
  e4: ["You show love mostly by…", {
    "Kind words": "I show love through kind words.",
    "Making time": "I show love by making time for someone.",
    "Doing favours": "I show love by doing favours.",
    "Gifts and little surprises": "I show love through gifts and little surprises.",
  }],
  e6: ["Saying no to people is…", {
    "Easy": "It feels easy for me to say no.",
    "Fine, if I explain why": "I feel comfortable saying no when I can explain why.",
    "Hard — I feel guilty": "I find it hard to say no because I feel guilty.",
    "Almost impossible": "Saying no can feel almost impossible for me.",
  }],
  e7: ["When a boundary is crossed, you…", {
    "Say so the same day": "When someone crosses a boundary, I say so that day.",
    "Let it go once, then say it": "When someone crosses a boundary, I tend to let it go once and then speak up.",
    "Let it slide and resent it": "When someone crosses a boundary, I tend to let it slide even though I resent it.",
    "Cut them off": "When someone crosses a boundary, I tend to cut off contact.",
  }],
  e8: ["You trust people…", {
    "Easily, until they prove otherwise": "I tend to trust people easily until they give me a reason to reconsider.",
    "Slowly, after time": "I build trust slowly, over time.",
    "Only after they've been tested": "I trust someone once I’ve seen that trust put to the test.",
    "Rarely, fully": "I rarely trust someone fully.",
  }],
  f3: ["When you argue, winning means…", {
    "We both feel heard": "In an argument, success means we both feel heard.",
    "We feel close again": "In an argument, success means we feel close again.",
    "We find the fix": "In an argument, success means we find a fix.",
    "I was right": "In an argument, I think of winning as being right.",
  }],
  f16: ["Surprise plans from him…", {
    "Stress me out": "Surprise plans from a partner stress me out.",
    "Are fine with some notice": "I’m fine with surprise plans from a partner if I get some notice.",
    "Are fun": "I find surprise plans from a partner fun.",
    "Are the best thing ever": "I love it when a partner surprises me with plans.",
  }],
  f21: ["The compliments you need most are about…", {
    "How much I matter": "The compliments that matter most to me tell me how much I mean to someone.",
    "How I look": "The compliments that matter most to me are about how I look.",
    "What I do": "The compliments that matter most to me are about what I do.",
    "I don't need them": "I don’t feel that I need compliments.",
  }],
  f23: ["The love you give most is…", {
    Words: "I most often show love through words.",
    Time: "I most often show love by giving my time.",
    Help: "I most often show love by helping out.",
    Touch: "I most often show love through touch.",
  }],
  f30: ["On dates you're the one who…", {
    "Suggests plans": "On dates, I tend to suggest the plans.",
    "Shares plans": "On dates, I share the planning.",
    "Goes along": "On dates, I usually go along with the plans.",
    "Waits to be asked": "On dates, I tend to wait to be asked.",
  }],
  f40: ["Big decisions about your life you make…", {
    Alone: "I make big decisions about my life on my own.",
    "With advice": "I ask for advice when making big decisions about my life.",
    Together: "I make big decisions about my life together with someone else.",
    "I let others decide": "When it comes to big decisions about my life, I tend to let others decide.",
  }],
  f45: ["Talking about your feelings is…", {
    Natural: "Talking about my feelings comes naturally to me.",
    Fine: "I feel fine talking about my feelings.",
    Hard: "Talking about my feelings is hard for me.",
    Painful: "Talking about my feelings is painful for me.",
  }],
  f52: ["What would make you feel truly safe with someone?", {
    Words: "Someone’s words help me feel safe with them.",
    Consistency: "Consistency helps me feel safe with someone.",
    Space: "Having space helps me feel safe with someone.",
    Loyalty: "Loyalty helps me feel safe with someone.",
  }],
  f59: ["Your relationship on social media…", {
    "I post it all": "I post openly about my relationship on social media.",
    "Now and then": "I post about my relationship now and then.",
    "Kept private": "I keep my relationship private on social media.",
    "Never, ever": "I never post about my relationship on social media.",
  }],
};

const cleanText = (value) => typeof value === "string" ? value.trim() : "";

function everydayStatement(id, question, answer) {
  const reading = Object.hasOwn(readings, id) ? readings[id] : null;
  if (reading?.[0] === question && Object.hasOwn(reading[1], answer)) return reading[1][answer];
  return undefined;
}

export function buildSavedPortrait(quiz, savedAnswers, summary) {
  const groups = new Map();
  const seen = new Set();
  for (const [key, section] of Object.entries(sections)) {
    const questions = quiz?.[key];
    if (!Array.isArray(questions)) continue;
    for (const row of questions) {
      const id = cleanText(row?.id);
      const question = cleanText(row?.q);
      const index = savedAnswers?.[id]?.choice;
      if (!id || !question || seen.has(id) || !Number.isInteger(index) || index < 0 || !Array.isArray(row.options)) continue;
      const answer = cleanText(row.options[index]?.label);
      if (!answer) continue;
      const axis = cleanText(row.axis) || "Other answers";
      if (!groups.has(axis)) groups.set(axis, { axis, items: [] });
      groups.get(axis).items.push({ id, question, answer, axis, section, statement: everydayStatement(id, question, answer) });
      seen.add(id);
    }
  }
  const reported = summary?.personality;
  const code = cleanText(reported?.code);
  const selfSet = reported?.self_set === true;
  const type = /^[EI][NS][FT][JP]$/.test(code) && (selfSet || seen.size > 0)
    ? { code, name: cleanText(reported.name), selfSet } : null;
  return { count: seen.size, groups: Array.from(groups.values()), type, description: cleanText(summary?.describe) };
}

export const themes = [
  { id: "communication", title: "Starting a conversation", everyday: ["s1", "f45"], section: "conversations",
    followUp: "When something personal comes up, what would make the first few words easier for you?" },
  { id: "repair", title: "Finding your way back", everyday: ["e2", "f3", "s3"], section: "reconnect",
    followUp: "After a difficult moment, how would you like someone to check what you need first?" },
  { id: "boundaries", title: "Speaking up about limits", everyday: ["e6", "e7"], section: "boundaries",
    followUp: "How would you like someone to respond when you tell them where you stand?" },
  { id: "decisions", title: "Everyday decisions and decisions in a scene", everyday: ["f40", "f30"], section: "exchange",
    topics: ["Taking the lead", "Being guided", "Delegating a choice", "Choosing the scope of a role", "Sharing in-scene decisions"],
    followUp: "What would you want someone to ask about decision-making in each setting?" },
  { id: "praise", title: "Everyday compliments and praise in a scene", everyday: ["f21"], section: "playful-challenge",
    topics: ["Receiving praise", "Praise about effort"],
    followUp: "What would you want someone to know about how their words land in those two settings?" },
  { id: "planning", title: "Making everyday plans and planning a scene", everyday: ["f16", "s12"], section: "scene-planning",
    topics: ["A shared menu", "Choosing the room setup yourself", "One new element at a time", "A planned interval", "A change of plan"],
    followUp: "Which parts, if any, would you want to discuss in advance in each setting?" },
  { id: "service", title: "Everyday favours and service in a scene", everyday: ["e4", "f23"],
    answers: { e4: "Doing favours", f23: "Help" }, section: "service",
    topics: ["Offering a service", "An everyday act of care", "Choosing your own gesture", "A ceremonial task", "Reciprocal service"],
    followUp: "What would you like someone to understand about an everyday favour and the role you described?" },
  { id: "closeness", title: "Feeling close", everyday: ["e3", "e4", "f23", "s4"], section: "affection",
    followUp: "What small gesture would you welcome from a partner on an ordinary day?" },
  { id: "pace", title: "Choosing a pace", everyday: ["s5", "s12"], section: "desire-pace",
    topics: ["Time to warm up", "Anticipation", "Spontaneous invitations", "Trying something unfamiliar"],
    followUp: "How much time would you like between an invitation and giving your answer?" },
  { id: "privacy", title: "Choosing what stays private", everyday: ["f59"], section: "digital-privacy",
    topics: ["Keeping relationship updates offline", "Keeping intimacy in person", "Keeping an agreed message as a memento", "A neutral text to check for privacy"],
    followUp: "Which details would you want a partner to ask about before sharing them?" },
  { id: "space", title: "Space and company", everyday: ["s9", "e2"], section: "aftercare",
    topics: ["Some time alone", "Quiet time together", "Physical closeness afterward"],
    followUp: "What would be a comfortable way for someone to ask whether you want space or company?" },
  { id: "trust", title: "What trust means to you", everyday: ["e8", "f52"], condition: "With more trust",
    followUp: "What would help someone understand what trust means to you in this particular conversation?" },
];

function priority(entry) {
  if (entry.choice === "Off limits") return 0;
  if (entry.choice === "It depends") return entry.conditions?.includes("In fantasy only") ? 1 : 2;
  return 3;
}

export function portraitConnections(profile, entries) {
  if (!Array.isArray(profile?.groups) || !Array.isArray(entries)) return [];
  const facts = new Map(profile.groups.flatMap((group) => Array.isArray(group?.items) ? group.items : [])
    .filter((item) => item && everydayStatement(item.id, item.question, item.answer))
    .map((item) => [item.id, item]));
  const validEntries = entries.filter((item) => !item?.editedQuestion && privateTopics.get(item?.sectionId)?.has(item.topic)
    && ["Interested", "Curious", "It depends", "Not for me", "Off limits"].includes(item.choice)
    && (!item.conditions || Array.isArray(item.conditions)));
  const candidates = validEntries.sort((a, b) => priority(a) - priority(b));
  const usedEntries = new Set();
  const connections = [];
  for (const theme of themes) {
    const everyday = theme.everyday.map((id) => facts.get(id)).find((item) =>
      item && (!theme.answers || theme.answers[item.id] === item.answer));
    if (!everyday) continue;
    const entry = candidates.find((item) => {
      if (usedEntries.has(item)) return false;
      if (theme.condition) return item.choice === "It depends" && Array.isArray(item.conditions) && item.conditions.includes(theme.condition);
      return item.sectionId === theme.section && (!theme.topics || theme.topics.includes(item.topic));
    });
    if (!entry) continue;
    connections.push({ id: theme.id, title: theme.title, everyday, entry, followUp: theme.followUp });
    facts.delete(everyday.id);
    usedEntries.add(entry);
  }
  return connections;
}
