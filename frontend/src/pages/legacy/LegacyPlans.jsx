import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Check } from "lucide-react";
import { publicApi } from "@/lib/api";
import { useCatalogTitle } from "@/pages/public/catalog";
import "@/pages/public/Plans.css";

export const PLAN_BENEFITS = [
  "Individual and group coaching, with people profiles",
  "Tone insights and response styles",
  "Conversation practice and game plans",
  "Personality questionnaires and personal insights",
  "And many more",
];

export default function LegacyPlans() {
  const [plans, setPlans] = useState(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [introAvailable, setIntroAvailable] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setFailed(false);
    publicApi.get("/public/plans", { signal: controller.signal }).then(({ data }) => {
      if (controller.signal.aborted) return;
      if (data.plans?.length) setPlans(data.plans);
      else setFailed(true);
    }, () => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [attempt]);
  useEffect(() => {
    const controller = new AbortController();
    setIntroAvailable(false);
    (async () => {
      try {
        const { data } = await publicApi.get("/public/intro-access", { signal: controller.signal });
        if (!controller.signal.aborted) setIntroAvailable(data.enabled === true && (data.messages ?? data.messages_granted) === 10
          && data.requires_payment_method === true && data.automatic_charge === true
          && data.authorization_version === "card-first-intro-v1");
      } catch {
        if (!controller.signal.aborted) setIntroAvailable(false);
      }
    })();
    return () => controller.abort();
  }, [attempt]);
  useCatalogTitle("Plans");
  return (
    <section className="public-plans mx-auto max-w-6xl px-5 sm:px-8 lg:px-12 pt-10 sm:pt-12 pb-10" data-testid="public-plans">
      <p className="eyebrow mb-3">Private tools · Existing memberships</p>
      <h1 className="public-plans__title font-display font-black uppercase text-6xl sm:text-8xl leading-none">Private tool plans</h1>
      <p className="mt-4"><Link className="underline" to="/legacy">Back to private tools</Link> · <Link className="underline" to="/plans">Dating founding beta</Link></p>
      <p className="public-plans__intro mt-4 max-w-2xl text-lg text-[var(--muted)]">Every plan includes Individual, Couples and Your Type. Choose the monthly AI reply allowance that fits you.</p>
      {plans?.length ? (
        <div className="public-plans__grid mt-7 sm:mt-8 grid gap-4 md:grid-cols-3">
          {plans.map(plan => (
            <article key={plan.id} className="public-plans__card card p-5 lg:p-7" aria-label={plan.name}>
              <h2 className="font-display text-2xl font-bold uppercase">{plan.name}</h2>
              <p className="public-plans__price mt-4 flex items-baseline gap-1"><strong className="font-display text-5xl sm:text-6xl font-black">${plan.price_cents / 100}</strong><span className="text-sm text-[var(--muted)]">/ month</span></p>
              <p className="public-plans__allowance mt-5 border-t border-[var(--line)] pt-4 text-sm font-semibold">{plan.messages} replies per billing month</p>
              <p className="public-plans__includes mt-4 text-xs font-semibold uppercase tracking-wider text-[var(--muted)]">All features included</p>
              <ul className="public-plans__benefits mt-3 flex flex-col gap-2.5 text-sm leading-5" aria-label={`Included with ${plan.name}`} data-testid={`plan-benefits-${plan.id}`}>
                {PLAN_BENEFITS.map(benefit => <li key={benefit} className="flex items-start gap-2">
                  <Check aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" /><span>{benefit}</span>
                </li>)}
              </ul>
            </article>
          ))}
        </div>
      ) : failed ? (
        <p role="alert" className="mt-8">Plans couldn’t load. <button type="button" className="underline" onClick={() => setAttempt(n => n + 1)}>Try again</button></p>
      ) : <p role="status" className="mt-8 text-sm text-[var(--muted)]">Loading plans…</p>}
      {plans?.length > 0 && <div className="public-plans__notes mt-5 flex flex-col gap-2 text-xs leading-relaxed text-[var(--muted)]" data-testid="plan-inclusion-details">
        {introAvailable && <p data-testid="public-intro-offer"><Link className="underline font-semibold" to="/sign-up">Start with 10 free AI messages</Link> once with an eligible verified account. Choose a monthly plan and verify your card first. After your tenth free AI message is delivered, we automatically charge your chosen plan, plus applicable tax, and start its full paid allowance. Cancel before using all 10 to prevent that first charge. Monthly plans then renew until canceled.</p>}
        <p>AI coaching and practice across Individual and Couples use your monthly allowance. Questionnaires, Your Type and non-AI Couples tools do not use reply credits.</p>
        <p>Couples requires two adults and a confirmed pairing. One invited partner can use that Couples space through your membership; their own Individual access requires a separate membership.</p>
        <p>Prices in USD. Monthly plans renew until canceled. <Link className="underline" to="/billing#message-allowance">How replies, refills and refunds work</Link>.</p>
      </div>}
    </section>
  );
}
