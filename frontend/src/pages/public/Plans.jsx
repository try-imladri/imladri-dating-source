import { Link } from "react-router-dom";
import { Check } from "lucide-react";
import "./Plans.css";

const FOUNDING_BENEFITS = [
  "Create an intentional dating profile",
  "Describe your relationship intentions and preferences",
  "Choose which personality insights to share",
  "Keep intimacy questions optional and private by default",
  "Meet through mutual interest when your community is ready",
];

export default function Plans() {
  return (
    <section className="public-plans mx-auto max-w-5xl px-5 sm:px-8 lg:px-12 pt-10 sm:pt-12 pb-12" data-testid="public-plans">
      <p className="eyebrow">Founding community · Adults 18+</p>
      <h1 className="public-plans__title font-display font-black uppercase text-5xl sm:text-8xl leading-none mt-3">Start with intention.</h1>
      <p className="mt-5 max-w-2xl text-lg text-[var(--muted)]">We’re bringing the first Imladri dating community together. Join the founding beta while we build a local pool of people who want to meet.</p>
      <div className="mt-8 grid gap-6 md:grid-cols-[1.1fr_1fr]">
        <article className="card p-6 sm:p-8" aria-label="Free founding beta" data-testid="dating-founding-offer">
          <h2 className="font-display text-2xl font-bold uppercase">Founding beta</h2>
          <p className="mt-4"><strong className="font-display text-5xl font-black">Free</strong></p>
          <p className="mt-3 text-[var(--muted)]">No card required for dating enrollment. No automatic dating subscription.</p>
          <ul className="mt-6 flex flex-col gap-4" aria-label="Founding beta features">
            {FOUNDING_BENEFITS.map(benefit => <li key={benefit} className="flex gap-3"><Check className="h-5 w-5 shrink-0 mt-0.5" aria-hidden="true" /><span>{benefit}</span></li>)}
          </ul>
          <Link to="/dating?origin=plans" className="btn-lime inline-flex px-6 py-3 mt-7" data-testid="founding-join">Join the founding community</Link>
        </article>
        <div className="space-y-6 py-2">
          <section><h2 className="font-display font-bold text-2xl uppercase">What joining means</h2><p className="mt-3 leading-relaxed">Enrollment is the first step. Profile review, local participation, and reciprocal preferences affect when introductions become available. Joining does not guarantee a match or date.</p></section>
          <section><h2 className="font-display font-bold text-2xl uppercase">Before anything paid</h2><p className="mt-3 leading-relaxed">If we offer a paid dating membership later, we’ll show its features, price, renewal terms, and cancellation options before you decide to purchase.</p></section>
          <section><h2 className="font-display font-bold text-2xl uppercase">Already use Imladri?</h2><p className="mt-3 leading-relaxed">Your existing Individual and Couples memberships stay separate from dating enrollment. <Link to="/legacy/plans" className="underline">View private tool plans</Link> or <Link to="/app/settings/upgrade" className="underline">manage an existing membership</Link>.</p></section>
        </div>
      </div>
      <p className="mt-8 text-sm text-[var(--muted)]"><Link to="/how-it-works" className="underline">How dating works</Link> · <Link to="/privacy" className="underline">Your data and choices</Link> · <Link to="/billing" className="underline">Existing membership billing</Link></p>
    </section>
  );
}
