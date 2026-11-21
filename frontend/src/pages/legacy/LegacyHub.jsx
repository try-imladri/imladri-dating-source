import { Link } from "react-router-dom";

const TOOLS = [
  ["Individual", "Your private people profiles, conversation reads, and saved context.", "/app?workspace=individual"],
  ["Couples", "Your existing partner space, shared agreements, and check-ins.", "/app/couples"],
  ["Personality questions", "Return to your private questionnaires and personal insights.", "/app/personal"],
  ["Sex & Intimacy", "Your optional private questions, preferences, and boundaries.", "/app/intimacy"],
  ["Your Type", "Explore visual preferences using original synthetic adult portraits.", "/app/your-type"],
  ["People", "Review the people profiles you created in your private workspace.", "/app/people"],
  ["Conversation practice", "Keep using your private AI rehearsal and communication tools.", "/app/practice"],
  ["Settings & data", "Review account settings, permissions, exports, and deletion controls.", "/app/settings"],
];

export default function LegacyHub() {
  return (
    <section className="max-w-6xl mx-auto px-5 sm:px-8 py-10 sm:py-14" data-testid="legacy-hub">
      <p className="eyebrow">Your existing Imladri tools</p>
      <h1 className="font-display font-black uppercase text-5xl sm:text-7xl mt-3">Private tools</h1>
      <p className="text-lg leading-relaxed max-w-3xl mt-5">Individual, Couples, and your exploration tools have their own space here as Imladri focuses on intentional dating. Your existing records remain in their original workspaces.</p>
      <div className="card p-5 mt-6 max-w-3xl"><h2 className="font-semibold">Dating is a separate choice</h2><p className="mt-2 leading-relaxed">Opening dating does not publish your private profiles, questionnaire answers, or conversations. You choose whether to enroll and what goes into your dating profile.</p></div>
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4 mt-8">
        {TOOLS.map(([title, body, path]) => <article className="card p-5 flex flex-col items-start" key={path}><h2 className="font-display font-bold text-2xl">{title}</h2><p className="mt-3 text-[var(--muted)] leading-relaxed flex-1">{body}</p><Link to={path} className="underline mt-5 py-2">Open {title}</Link></article>)}
      </div>
      <nav aria-label="Private tool help" className="flex flex-wrap gap-x-6 gap-y-3 mt-8"><Link to="/legacy/plans" className="underline py-2">Existing membership plans</Link><Link to="/legacy/how-it-works" className="underline py-2">Private tool walkthrough</Link><Link to="/dating" className="underline py-2">Open dating</Link><Link to="/" className="underline py-2">Imladri home</Link></nav>
    </section>
  );
}
