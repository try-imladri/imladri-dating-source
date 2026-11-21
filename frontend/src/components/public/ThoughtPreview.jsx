import { Link } from "react-router-dom";
import { signupPath } from "./PublicLayout";
import { setOrigin } from "@/lib/api";

const DETAILS = {
  "your-type": {
    title: "Get to know your type.",
    description: "Choose and compare original synthetic adult portraits to explore the visual patterns you’re drawn to.",
    steps: ["Rate portraits at your own pace.", "Review the choices and features that stand out in this portrait bank.", "Keep your visual-preference profile separate from your personality results."],
    note: "Portrait choices do not tell us someone’s identity, personality or real-world compatibility. This is not a matchmaking service.",
    anchor: "your-type",
  },
  couples: {
    title: "Find your way together.",
    description: "Start with your own answers, choose what to share, then turn a conversation into something you can both try.",
    steps: ["Answer privately before sharing selected words.", "Work through a conversation together.", "Agree on a next step and check in on how it went."],
    note: "Your private drafts and Individual profile are not automatically visible to your partner.",
    anchor: "couples-example",
  },
  intimacy: {
    title: "Make room for an honest conversation.",
    description: "Optional questionnaires for adults 18+ help you describe preferences, comfort levels and boundaries in your own words.",
    steps: ["Explore what matters to you, with room to skip a question.", "Review your own Sex & Intimacy summary.", "Choose what you want to discuss with your partner."],
    note: "This helps you talk about intimacy. It does not predict sexual compatibility or guarantee an outcome.",
    anchor: "sexual-compatibility",
  },
};

export function ThoughtPreview({ feature, config }) {
  const detail = DETAILS[feature];
  return <div data-testid={`home-preview-${feature}`}>
    <h2 className="font-display font-bold text-3xl sm:text-4xl leading-tight">{detail.title}</h2>
    <p className="mt-3 text-sm sm:text-base">{detail.description}</p>
    <ol className="mt-5 space-y-3">
      {detail.steps.map((step, index) => <li key={step} className="flex gap-3 text-sm"><span className="font-mono text-[var(--muted)]">0{index + 1}</span><span>{step}</span></li>)}
    </ol>
    <p className="mt-5 text-xs text-[var(--muted)]">{detail.note}</p>
    <div className="mt-6 flex flex-wrap items-center gap-4 text-sm">
      <Link className="btn-lime inline-flex rounded-full px-5 py-2.5 font-semibold" to={signupPath(config, "home")} onClick={() => setOrigin("home")}>{config.waitlist_mode ? "Join the waitlist" : "Start here"}</Link>
      <Link className="underline underline-offset-4" to={`/how-it-works#${detail.anchor}`}>See how it works</Link>
    </div>
  </div>;
}
