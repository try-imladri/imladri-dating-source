import { Link } from "react-router-dom";
import { PublicPage } from "./StaticPages";

export default function Learn() {
  return (
    <PublicPage eyebrow="Explore with perspective" title="Learn" testid="learn-page">
      <p className="text-lg">Attraction can start a conversation. Learning how someone communicates, what they want, and what they choose to tell you takes time.</p>
      <div className="grid sm:grid-cols-2 gap-4">
        <section className="card p-5"><h2 className="!mt-0">Archetypes</h2><p className="mt-3">Explore recognizable communication patterns and relationship scenarios. Use them as conversation prompts, never as a diagnosis or a verdict about who to date.</p><Link to="/types" className="inline-block underline mt-4 py-2">Browse archetypes</Link></section>
        <section className="card p-5"><h2 className="!mt-0">Public figures</h2><p className="mt-3">Read editorial interpretations of public personas and public work. These pages do not establish anyone’s private personality, relationship preferences, or endorsement of Imladri.</p><Link to="/figures" className="inline-block underline mt-4 py-2">Browse public figures</Link></section>
      </div>
      <h2>What a preference can tell you</h2><p>Your answers describe what you report today. They can help you discuss relationship intentions, communication, and boundaries. They do not prove chemistry or predict a successful relationship.</p>
      <p>Your Type uses original synthetic adult portraits for private preference exploration. Those portraits are not Imladri members.</p>
      <p><Link to="/how-it-works" className="underline">See how dating works</Link> · <Link to="/legacy" className="underline">Find your existing private tools</Link></p>
      <Link to="/dating?origin=learn" className="btn-lime inline-flex px-6 py-3">Join the founding community</Link>
    </PublicPage>
  );
}
