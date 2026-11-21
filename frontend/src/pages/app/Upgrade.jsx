import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useOutletContext, useSearchParams } from "react-router-dom";
import { Check } from "lucide-react";
import { currentPlanName, isComplimentary, isIntroductory, periodEnd } from "@/components/app/PlanUsage";
import IntroAccessCard from "@/components/app/IntroAccessCard";
import { PartnerAnnualOffer } from "@/components/app/PartnerOffers";
import { errMsg } from "@/lib/api";
import PurchasePolicy, { purchaseAcceptance } from "@/components/app/PurchasePolicy";
import { couplesOnly } from "@/lib/workspaceAccess";

const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2, minimumFractionDigits: 0 });
const buttonClass = "btn-lime w-full px-4 py-2.5 text-sm disabled:opacity-50";
const noticeClass = "card px-4 py-3 text-sm";

const REFILL_REQUEST_TTL = 23 * 60 * 60 * 1000;
function refillRetry(owner, next) {
  if (!owner) return null;
  const key = "imladri:refill-retry:" + owner;
  try {
    if (next === null) { sessionStorage.removeItem(key); return null; }
    if (next) { sessionStorage.setItem(key, JSON.stringify({ key: next, until: Date.now() + REFILL_REQUEST_TTL })); return next; }
    const saved = JSON.parse(sessionStorage.getItem(key));
    if (saved?.until > Date.now() && typeof saved.key === "string") return saved.key;
    sessionStorage.removeItem(key);
  } catch { /* An in-memory key still protects retries on this page. */ }
  return null;
}

function usePaymentReturn(api, onConfirmed) {
  const [params, setParams] = useSearchParams();
  const session = params.get("session_id");
  const upgrade = params.get("upgrade") === "complete" && params.get("plan");
  const [state, setState] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!session && !upgrade) return;
    let tries = 0, stopped = false, timer;
    setState("checking");
    async function verify() {
      try {
        const { data } = await api.get(session ? `/payments/status/${encodeURIComponent(session)}` : "/me");
        if (stopped) return;
        if (session && data.review_required === true) {
          setState("review");
          onConfirmed("review");
          return;
        }
        const confirmed = session ? data.payment_status === "paid" : data.usage?.period_verified && (data.membership_plan_id || data.usage?.plan_id) === upgrade;
        if (confirmed) {
          if (!session) setState("upgraded");
          else if (data.kind === "topup") setState("topped-up");
          else setState(data.tier === "unsubscribed" ? "inactive" : "paid");
          onConfirmed(session ? data.kind : "upgrade");
          setParams({}, { replace: true });
          return;
        }
        if (session && (["expired", "failed"].includes(data.status) || data.payment_status === "failed")) {
          setState("expired");
          return;
        }
      } catch (error) {
        if (stopped) return;
        if ([401, 403, 404].includes(error.response?.status)) {
          setState("unavailable");
          return;
        }
      }
      if (++tries < 10) timer = setTimeout(verify, 2000);
      else setState("pending");
    }
    verify();
    return () => { stopped = true; clearTimeout(timer); };
  }, [api, onConfirmed, session, setParams, upgrade, attempt]);
  return { state: params.get("cancelled") ? "cancelled" : state, retry: () => setAttempt(n => n + 1) };
}

const notices = {
  checking: "Checking your payment with Stripe…",
  paid: "Your subscription payment is confirmed.",
  upgraded: "Your upgraded plan is active.",
  "topped-up": "Your top-up payment is confirmed. Your current balance is shown below.",
  inactive: "Payment confirmed. This subscription has ended.",
  cancelled: "Checkout cancelled.",
  expired: "This checkout closed before payment was confirmed.",
  unavailable: "We couldn’t verify that payment for this account.",
  pending: "Payment confirmation is still pending. Your balance updates when payment is verified.",
  review: "Your payment was received. This message pack needs billing review before its credits can be delivered. Please contact billing support.",
};

function BillingPeriod({ info, usage }) {
  const end = periodEnd(usage);
  if (info.plan_offer === "couple_annual") return <>Prepaid year · no automatic renewal{end && ` · monthly messages reset ${end}`}</>;
  if (info.partner_offer?.annual_cancelled || info.partner_offer?.annual_accepted) return <>Current monthly term ends {end || "at the end of this billing period"}.</>;
  return end ? <>Current billing period ends {end}.</> : null;
}

export default function Upgrade() {
  const context = useOutletContext();
  return <MemberPlans key={context.me.user_id} {...context} />;
}

function MemberPlans({ api, me, refreshUsage }) {
  const owner = me.user_id;
  const paired = couplesOnly(me);
  const [purchaseAccepted, setPurchaseAccepted] = useState(false);
  const mounted = useRef(false);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const [params] = useSearchParams();
  const [info, setInfo] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState(false);
  const currentRequest = useRef(null);
  const submitting = useRef(false);
  const topupKey = useRef(null);
  const load = useCallback(() => {
    if (!mounted.current) return;
    currentRequest.current?.abort();
    const request = new AbortController();
    currentRequest.current = request;
    return api.get("/upgrade", { signal: request.signal }).then(({ data }) => {
      if (!request.signal.aborted) { setInfo(data); setLoadError(false); }
    }).catch(() => { if (!request.signal.aborted) setLoadError(true); });
  }, [api]);
  useEffect(() => {
    load();
    const reloadVisible = () => { if (!document.hidden) load(); };
    document.addEventListener("visibilitychange", reloadVisible);
    return () => {
      currentRequest.current?.abort();
      document.removeEventListener("visibilitychange", reloadVisible);
    };
  }, [load]);
  useEffect(() => {
    const restored = event => {
      if (!event.persisted) return;
      submitting.current = false;
      setBusy("");
      load();
    };
    window.addEventListener("pageshow", restored);
    return () => window.removeEventListener("pageshow", restored);
  }, [load]);
  const period = info?.usage?.period_verified && info.usage.period_end;
  const [expiredPeriod, setExpiredPeriod] = useState(null);
  const verifyingPeriod = Boolean(period && expiredPeriod === period);
  useEffect(() => {
    if (!period) return;
    let timer;
    function recheck() {
      const remaining = period * 1000 - Date.now();
      if (remaining <= 0) { setExpiredPeriod(period); load(); }
      else timer = setTimeout(recheck, Math.min(remaining, 2147483647));
    }
    recheck();
    return () => clearTimeout(timer);
  }, [period, load]);
  const onConfirmed = useCallback(kind => {
    if (kind === "topup") { topupKey.current = null; refillRetry(owner, null); }
    refreshUsage(); load();
  }, [owner, load, refreshUsage]);
  const { state, retry } = usePaymentReturn(api, onConfirmed);
  async function openStripe(action, offer) {
    if (action !== "portal" && (info?.complimentary === true || isComplimentary(info?.usage || me.usage))) return;
    if (action !== "portal" && !purchaseAccepted) return;
    if (action !== "portal" && introBillingRequired) return;
    if (submitting.current) return;
    submitting.current = true;
    setBusy(offer || action);
    setError("");
    const payload = { origin_url: window.location.origin, ...(owner && { expected_member: owner }) };
    if (action !== "portal") Object.assign(payload, purchaseAcceptance());
    if (offer) payload.offer = offer;
    try {
      let result;
      if (action === "topup") {
        if (topupKey.current?.owner !== owner || !topupKey.current?.key) {
          const key = refillRetry(owner) || crypto.randomUUID();
          topupKey.current = { owner, key };
          refillRetry(owner, key);
        }
        result = await api.post("/payments/topup", payload, { headers: { "Idempotency-Key": topupKey.current.key } });
      } else {
        result = await api.post(`/payments/${action}`, payload);
      }
      if (!mounted.current) return;
      const url = action === "portal" ? result.data.portal_url : result.data.checkout_url;
      if (!url) throw new Error("Stripe checkout is unavailable.");
      window.location.assign(url);
      if (action === "topup") { topupKey.current = null; refillRetry(owner, null); }
    } catch (e) {
      if (!mounted.current) return;
      if (action === "topup" && e.response?.status >= 400 && e.response.status < 500 && e.response.status !== 409) {
        topupKey.current = null; refillRetry(owner, null);
      }
      setError(errMsg(e));
      submitting.current = false;
      setBusy("");
      load();
    }
  }
  if (!info) return loadError ? <button onClick={load} className="underline">Couldn’t load plans. Try again.</button> : <p role="status">Loading plans…</p>;

  const usage = info.usage || me.usage || {};
  const complimentary = info.complimentary === true || isComplimentary(usage);
  const intro = isIntroductory(usage);
  const currentId = intro ? null : info.membership_plan_id ?? (usage.plan === "plus" && usage.period_verified ? usage.plan_id || "plus_100" : null);
  const paid = !complimentary && Boolean(currentId);
  const partnerRate = !paid && !complimentary && info.partner_offer?.monthly_eligible;
  const plans = info.plans || [{ id: "plus_100", name: "Plus 100", messages: 100, price_cents: 1200, available: info.checkout_available }];
  const introPlans = plans.map(plan => partnerRate && plan.id === "plus_100" ? { ...plan, price_cents: 1000 } : plan);
  const introAccess = info.intro_access || me.intro_access;
  const cardFirstIntro = !paid && !complimentary && introAccess?.requires_payment_method === true
    && introAccess?.automatic_charge === true && introAccess?.authorization_version === "card-first-intro-v1"
    && (introAccess.can_claim === true || ["checkout_pending", "active", "billing_pending", "requires_action", "payment_failed", "review_required"].includes(introAccess.billing?.state));
  const introBillingRequired = cardFirstIntro || (!paid && !complimentary && info.intro_billing_required === true);
  const paymentPending = Boolean(loadError || verifyingPeriod || busy || state === "checking" || state === "pending" || state === "review" || (!paid && state === "paid"));
  const canSell = info.billing_enabled === true && !complimentary;
  const current = plans.find(plan => plan.id === currentId);
  const currentPrice = info.plan_offer === "couple_annual" ? "$80 for one year" :
    info.plan_offer === "partner_monthly" ? "$10/month" : current ? `${dollars.format(current.price_cents / 100)}/month` : info.price_label;
  const monthlyLeft = usage.monthly_messages_remaining ?? usage.messages_remaining;
  const refill = info.topup ?? { messages: 100, price_cents: 1200 };

  return <div className="max-w-5xl space-y-5 pb-6" data-testid="upgrade-page">
    <header>
      <h1 className="font-display font-black uppercase text-4xl sm:text-5xl" data-testid="upgrade-headline">{info.headline || "Plans"}</h1>
      {paired ? <p className="mt-2 max-w-2xl text-sm text-[var(--muted)]" data-testid="paired-plan-scope">Your membership funds your monthly AI reply allowance. While your pair is active, you use Couples and your private assessments; Individual and Your Type are unavailable.</p>
        : info.pitch && <p className="mt-2 max-w-2xl text-sm text-[var(--muted)]" data-testid="upgrade-pitch">{info.pitch}</p>}
    </header>
    {notices[state] && <div role="status" className={noticeClass} data-testid={`payment-${state}`}>{notices[state]}
      {["pending", "unavailable", "review"].includes(state) && <><button onClick={retry} className="underline ms-2 font-semibold">Check again</button><Link to="/support?topic=Billing" className="underline ms-3">Get billing help</Link></>}
    </div>}
    {verifyingPeriod && !loadError && <p role="status" className={noticeClass}>Your last billing period ended. Checking your current allowance. <button type="button" onClick={load} className="underline">Refresh plan</button></p>}
    {loadError && <p role="alert" className={noticeClass}>We couldn’t refresh your plan. The balances below were last loaded successfully. <button type="button" onClick={load} className="underline">Refresh plan</button></p>}
    {error && <p role="alert" className={noticeClass}>{error}</p>}
    {!paid && !complimentary && !usage.unlimited && owner && introAccess && <IntroAccessCard key={owner} api={api} owner={owner} initial={introAccess} plans={introPlans} initialPlan={params.get("plan")} sessionId={params.get("intro_session_id")} onClaimed={async () => { await refreshUsage(); await load(); }} />}
    <section className="card p-5 sm:p-6" aria-label="Current plan" data-testid="current-plan">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-mono text-xs uppercase tracking-wide text-[var(--muted)]">Current plan</h2>
          <p className="mt-1 font-display font-black uppercase text-3xl">{complimentary ? "Complimentary Unlimited" : currentPlanName(usage, currentId) || (info.existing_subscription ? "Awaiting paid period" : "No active subscription")}</p>
          {paid && <p className="mt-1 text-sm">{currentPrice}</p>}
        </div>
        {info.portal_available && <button disabled={!!busy} onClick={() => openStripe("portal")} className="btn-ghost px-4 py-2 text-sm" data-testid="billing-portal">{busy === "portal" ? "Opening Stripe…" : "Manage billing"}</button>}
      </div>
      {complimentary ? <div className="mt-3 space-y-3 text-sm" data-testid="complimentary-usage">
        <p>{paired ? "Your complimentary access includes unlimited Couples messages. No purchase is needed." : "Your complimentary access includes unlimited messages across Individual and Couples. No purchase is needed."}</p>
        <p><strong>{usage.messages_used ?? 0}</strong> complimentary messages used · all time</p>
        <Link to={paired ? "/app/couples" : "/app"} className="inline-flex font-semibold underline">Continue to {paired ? "Couples" : "Individual"}</Link>
      </div> : usage.plan === "admin" && usage.unlimited === true && <p className="mt-3 text-sm">Your administrator access includes unlimited personal messages.</p>}
      {intro && <p className="mt-3 text-sm" data-testid="intro-plan-details">Your introductory balance is separate from a paid billing month. For a verified card-first introduction, your selected plan is charged after the tenth free AI message is delivered. Its full paid allowance starts only after payment is verified. Review your authorization, status and cancellation above.</p>}
      {paid && !usage.unlimited && <div data-testid="plan-plus-usage" className="mt-4 border-t border-[var(--line)] pt-4">
        <dl className="flex flex-wrap gap-x-10 gap-y-3 text-sm">
          <div><dt className="text-[var(--muted)]">{verifyingPeriod ? "Last period’s remaining messages" : "Monthly messages left"}</dt><dd className="text-xl font-bold tabular-nums">{monthlyLeft} <span className="text-sm font-normal">of {usage.message_limit}</span></dd></div>
          <div><dt className="text-[var(--muted)]">Top-up messages left</dt><dd className="text-xl font-bold tabular-nums">{usage.topup_remaining ?? 0}</dd></div>
        </dl>
        <p className="mt-2 text-xs text-[var(--muted)]"><BillingPeriod info={info} usage={usage} /></p>
        {usage.payment_dispute_hold === true ? <p className="mt-2 text-sm" data-testid="plan-payment-review">{Number.isSafeInteger(usage.held_message_count) && usage.held_message_count >= 0 ? `${usage.held_message_count} unused messages are` : "Some unused messages are"} temporarily unavailable while a payment is reviewed. This is separate from messages you have used.</p>
          : usage.allowance_adjusted && <p className="mt-2 text-sm">Your monthly allowance was adjusted after a refund or lost dispute. The original allowance was {usage.original_message_limit} messages. Any remaining purchased top-up messages are shown separately.</p>}
        <Link to={paired ? "/app/couples" : "/app"} data-testid="membership-continue" className="inline-flex mt-3 text-sm font-semibold underline">Continue to {paired ? "Couples" : "Individual"}</Link>
      </div>}
      {info.subscription_status === "past_due" && <p role="status" className="mt-3 text-sm font-semibold">Your payment is past due. {info.portal_available ? "Open Manage billing to review it." : <Link to="/support?topic=Billing" className="underline">Get billing help</Link>}</p>}
      {!paid && usage.topup_remaining > 0 && <p className="mt-3 text-sm">Your {usage.topup_remaining} purchased messages are saved for your next active membership.</p>}
    </section>
    {!complimentary && !usage.unlimited && !introBillingRequired && <PurchasePolicy accepted={purchaseAccepted} onChange={setPurchaseAccepted} />}
    {!complimentary && <section aria-label="Monthly plans">
      <h2 className="font-display font-black uppercase text-2xl mb-2">Monthly plans</h2>
      {(paired || info.paid_features?.length > 0) && <ul className="mb-4 flex flex-wrap gap-x-4 gap-y-1.5 text-sm">{(paired
        ? ["Your monthly AI reply allowance", "Couples conversations and coaching", "Your private personality and intimacy assessments", "Compatibility through mutual sharing"]
        : info.paid_features).map(f => <li key={f} className="flex items-center gap-1.5"><Check size={15} aria-hidden="true" />{f}</li>)}</ul>}
      <div className="grid sm:grid-cols-3 gap-3">
        {plans.map(plan => {
          const isCurrent = plan.id === currentId;
          const partner = plan.id === "plus_100" && partnerRate;
          const currentYear = isCurrent && info.plan_offer === "couple_annual";
          const partnerPrice = partner || (isCurrent && info.plan_offer === "partner_monthly");
          const offer = partner ? "partner_monthly" : info.plans ? plan.id : "standard";
          const available = canSell && (paid ? info.upgrade_available && (plan.upgrade_available ?? plan.available) : info.checkout_available && (partner ? info.partner_offer.monthly_available : plan.available));
          const canChoose = paid ? !isCurrent && plan.messages > (current?.messages ?? usage.message_limit) : !info.existing_subscription && !usage.unlimited;
          const selected = params.get("plan") === plan.id;
          return <article key={plan.id} id={partner ? "partner-offer" : undefined} aria-label={plan.name} className={`card flex flex-col p-5 ${isCurrent || selected ? "border-[var(--ink)]" : ""}`} data-testid={`plan-${plan.id}`}>
            <h3 className="font-display text-xl uppercase font-bold">{plan.name}</h3>
            <p className="mt-3"><strong className="font-display font-black text-3xl" data-testid={plan.id === "plus_100" ? "plan-price" : undefined}>{dollars.format(currentYear ? 80 : partnerPrice ? 10 : plan.price_cents / 100)}</strong><span className="text-xs text-[var(--muted)]">{currentYear ? " / prepaid year" : " / month"}</span></p>
            <p className="mt-1 mb-5 text-sm">{plan.messages} messages per billing month</p>
            {partner && <p className="text-xs mb-3 text-[var(--muted)]">Your invited-partner rate. Your first paid month opens the 24-hour offer for a year at $80 per person.</p>}
            {introBillingRequired ? introAccess?.billing?.state === "unavailable" ? <p className="mt-auto text-sm text-[var(--muted)]">Card-first setup is currently unavailable. No purchase will start.</p> : <a className="mt-auto text-sm underline" href="#intro-access">{introAccess?.billing?.state === "not_started" ? "Choose your introductory plan above" : "Review introductory billing above"}</a> : isCurrent ? <p className="mt-auto font-semibold text-sm inline-flex items-center gap-1.5"><Check size={15} aria-hidden="true" />Current allowance</p> :
              canChoose && <button type="button" disabled={paymentPending || !available || !purchaseAccepted} className={`${buttonClass} mt-auto`} data-testid={`choose-${plan.id}`}
                onClick={() => openStripe(paid ? "upgrade" : "checkout", offer)}>
                {busy === offer ? "Opening Stripe…" : paid ? `Upgrade to ${plan.name}` : partner ? "Get Plus for $10/month" : `Choose ${plan.name}`}
              </button>}
          </article>;
        })}
      </div>
      {paid && info.upgrade_available && <p className="mt-3 text-sm text-[var(--muted)]">A paid upgrade starts a new billing month. Stripe credits unused time on your current plan and shows the amount due before you pay.{info.plan_offer === "partner_monthly" && " Upgrading replaces your $10 partner rate."}</p>}
      {paid && (info.plan_offer === "couple_annual" || info.partner_offer?.annual_accepted || info.partner_offer?.annual_pending) && <p className="mt-3 text-sm text-[var(--muted)]">Your one-year agreement includes 100 messages per month and ends automatically.</p>}
      {!canSell && <p role="status" className="mt-3 text-sm text-[var(--muted)]">New purchases are currently closed.</p>}
    </section>}
    {!complimentary && !usage.unlimited && <section id="top-up" aria-labelledby="topup-title" className="card scroll-mt-20 flex flex-wrap items-center justify-between gap-4 p-5 sm:p-6">
      <div className="max-w-xl"><h2 id="topup-title" className="font-display uppercase font-black text-2xl">Message top-up</h2>
        <p className="mt-1 text-sm">{refill.messages} extra messages for {dollars.format(refill.price_cents / 100)}, paid once. Your monthly messages are used first. Unused top-up messages carry forward and are available with an active membership.</p>
        {info.topup_blocked_reason && <p role="status" className="mt-2 text-sm">{info.topup_blocked_reason} <Link className="underline" to="/support?topic=Billing">Get billing help</Link>.</p>}
      </div>
      <button type="button" disabled={paymentPending || !paid || !canSell || !info.topup_available || !purchaseAccepted} className="btn-ghost px-5 py-2 text-sm disabled:opacity-50" onClick={() => openStripe("topup")} data-testid="buy-topup">{busy === "topup" ? "Opening Stripe…" : `Add ${refill.messages} messages · ${dollars.format(refill.price_cents / 100)}`}</button>
    </section>}
    {!complimentary && <PartnerAnnualOffer owner={owner} api={api} offer={info.partner_offer} billingEnabled={canSell} purchaseAccepted={purchaseAccepted} annualActive={paid && info.plan_offer === "couple_annual"} onChange={load} />}
    <p className="text-xs text-[var(--muted)]">{paired ? "New Couples AI replies use one message from the applicable membership allowance. Reopening saved reads and switching styles are free." : "Individual and Couples share one allowance. New AI replies use one message each, including coaching requested by a sponsored partner. Reopening saved reads and switching styles are free."}</p>
  </div>;
}
