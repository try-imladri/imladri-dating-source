# imaldri — Faster answers, visible reasoning, outcome loop and rehearsal

imaldri helps a woman read the people in her life (mostly the ones texting her) and decide what to do next, using the conversations she shares.
This update gets her a useful answer in the first minute, shows what each read is based on, learns from what actually happened, and lets her practise hard conversations first.

## Who it's for
- New members who arrive with one situation on their mind ("he went quiet", "what do I reply?") and want help now, not a 60-question profile.
- Returning members who track a few people over time and want advice that gets better as things happen.
- Anyone about to set a boundary or have a difficult talk who wants to practise first.

## Core features and experience

### 1. Fast first answer (after sign-up)
- Sign-up is still required. Nobody gets an answer without an account.
- Straight after sign-up, the first screen asks three things: a nickname for them, "What happened?" (one or two lines), and a pasted conversation or screenshot (optional).
- She gets a read and one suggested next step right away, before any questionnaire.
- The Personal questionnaire becomes optional. The old first-time guide that sent her to Personal first is replaced by this flow.
- Personal questions then come up gradually: after an answer, one relevant question at a time ("Quick one: how do you usually handle silence?"). She can answer in one tap or skip. Answers save to Personal as usual.

### 2. Show why
- Every answer is split into three clearly labelled parts:
  - **What we can see:** observations taken directly from the messages.
  - **Possible explanations:** several readings, never just one, with how likely each looks.
  - **What we don't know:** missing context that would change the read.
- Tapping an observation or explanation highlights the exact messages behind it in the conversation.
- The answer never claims to know the other person's real intentions. For example, a slow reply lists several explanations and what would tell them apart.
- She can mark any point "That's not right". The next answer takes the correction into account.

### 3. Outcome loop
- Each answer ends with two or three suggested actions (e.g. "Wait a day", "Send this reply", "Ask directly").
- She picks one, or writes her own. It's saved as what she chose to do.
- Later, a "How did it go?" card appears on that person and in Overview. She picks a result (better, same, worse, no reply yet) and can add a note or a new screenshot.
- The result updates the person's read. If new evidence contradicts an earlier conclusion, the read says so ("Earlier we thought X; this suggests Y").
- Her history with each person shows as a simple timeline: conversation → action she chose → what happened. She can edit or delete any step, and the advice adjusts.

### 4. Rehearsal (new "Practice" tab)
- A new sidebar tab, **Practice**. It doesn't go inside Personal, which stays about her.
- She picks a person, says what she wants to bring up (e.g. "I need more notice before plans"), and picks a mode:
  - **Live:** the AI plays that person in character, using what's known about them. She types, they reply. She can restart or end any time.
  - **Likely replies:** she writes her opening line and sees three plausible ways they might react, each with a suggested answer in her own voice. She can tap one to keep going down that path.
- At the end she gets short feedback: what landed, what might backfire, and a cleaned-up version of her message she can copy.
- Every scenario is clearly labelled as practice. Sessions are saved per person and can be deleted.

## User flow
1. Sign up, then land on "Who's on your mind?" (nickname, what happened, optional conversation or screenshot).
2. See the read (seen / possible explanations / unknown) and tap any point to see the messages behind it.
3. Pick a suggested action, or "I'll decide later".
4. Get prompted later with "How did it go?", record the result, and see the read update.
5. Optionally answer the one-tap Personal questions that appear along the way.
6. Before a hard talk, open Practice, choose the person and topic, and rehearse live or with likely replies.

## UI/UX feel
- The same monochrome look as today: bold uppercase headings, cards, pill buttons and the green accent for actions.
- The first-answer screen is a single focused card with no sidebar clutter, so it feels like texting a smart friend.
- In answers, the three sections are colour-coded subtly; highlighted messages glow in the conversation.
- Practice looks like a chat but with a distinct "Practice" banner, so it's never confused with real advice.
- Everything works one-handed on a phone.

## Implementation phases
- **Phase 1 (MVP, built now):** fast first answer after sign-up with gradual Personal questions; show-why answers with tap-to-see messages and "That's not right" corrections; the outcome loop (suggested actions → choice → "How did it go?" → updated read, plus an editable timeline); the Practice tab with both Live and Likely-replies modes.
- **Phase 2:** visible privacy controls: reliable screenshot redaction with a preview before anything is read, a clear page of everything saved about each person with edit/delete, full export, one-tap delete of all data, and discreet notifications (no names or message text on the lock screen).
- **Phase 3:** a weekly "what changed" summary per person, practice reminders before planned talks, and progress on her own patterns over time.

## Assumptions
- Articles and community are left out entirely for now, as chosen.
- Rehearsal includes both modes because both were picked.
- Both "fast first answer / show-why / outcome loop" and "Rehearsal" go into Phase 1 because both were picked. Privacy controls move to Phase 2.
- Rehearsal gets its own sidebar tab, "Practice", instead of living inside Personal (your suggestion).
- The existing Personal tabs, people pages, group chat, starters and saved groups stay as they are; the new answer format replaces the current six-part layout in single-person chats. Group chats keep their current format.
- "How did it go?" appears in the app only (person page, Overview and the bell), not by email or push.
- Corrections and outcomes change future answers for that person only; they don't rewrite old answers, but the read shows when a conclusion changed.
- Practice uses the same AI already used in the app; no new paid service is needed.
- Existing members aren't forced through the new first-answer screen. It's for new sign-ups and appears when someone has no people yet.

## How the project rates today
- Strong: a distinctive public site; rich per-person context (profile, memories, patterns, type); a clean People + Chat layout; the admin space.
- Weak: too much to fill in before getting value; answers that don't show their evidence; the outcome loop exists but isn't central; privacy controls aren't visible yet. This plan addresses the first three now and privacy next.
