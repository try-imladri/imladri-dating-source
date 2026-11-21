import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { errMsg } from "@/lib/api";

const TURNSTILE_SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
let challengeLoader;

function loadChallenge() {
  if (window.turnstile?.render) return Promise.resolve(window.turnstile);
  if (challengeLoader) return challengeLoader;
  challengeLoader = new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${TURNSTILE_SCRIPT}"]`);
    const script = existing || document.createElement("script");
    let timeout;
    const cleanup = () => {
      clearTimeout(timeout);
      script.removeEventListener("load", loaded);
      script.removeEventListener("error", failed);
    };
    const loaded = () => {
      cleanup();
      if (window.turnstile?.render) resolve(window.turnstile);
      else failed();
    };
    const failed = () => {
      cleanup();
      challengeLoader = null;
      if (!existing) script.remove();
      reject(new Error("The security check couldn’t load. Please try again."));
    };
    script.addEventListener("load", loaded, { once: true });
    script.addEventListener("error", failed, { once: true });
    timeout = setTimeout(failed, 15000);
    if (!existing) {
      script.src = TURNSTILE_SCRIPT;
      script.async = true;
      script.defer = true;
      document.head.appendChild(script);
    }
  });
  return challengeLoader;
}

export function IntroSecurityCheck({ siteKey, action = "intro-access", onToken, revision = 0 }) {
  const element = useRef(null);
  const callback = useRef(onToken);
  callback.current = onToken;
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let stopped = false, widget, client;
    setError("");
    callback.current("");
    loadChallenge().then(turnstile => {
      if (stopped || !element.current) return;
      client = turnstile;
      widget = turnstile.render(element.current, {
        sitekey: siteKey,
        action,
        theme: "auto",
        size: "compact",
        callback: token => { if (!stopped) callback.current(token); },
        "expired-callback": () => { if (!stopped) callback.current(""); },
        "error-callback": () => {
          if (!stopped) {
            callback.current("");
            setError("The security check didn’t finish. Please try again.");
          }
        },
      });
    }).catch(error => { if (!stopped) setError(error.message); });
    return () => {
      stopped = true;
      if (widget !== undefined) client?.remove(widget);
    };
  }, [siteKey, action, retry, revision]);
  return <div className="mt-3" data-testid="intro-security-check">
    <div ref={element} />
    {error && <p role="alert" className="mt-2 text-sm">{error} <button type="button" className="underline" onClick={() => setRetry(n => n + 1)}>Retry security check</button></p>}
  </div>;
}

function deviceToken() {
  const key = "imladri:intro-device-v1";
  try {
    const saved = localStorage.getItem(key);
    if (/^[a-f0-9-]{36}$/.test(saved || "")) return saved;
    const token = crypto.randomUUID();
    localStorage.setItem(key, token);
    return token;
  } catch {
    // A storage failure never grants access. The server decides eligibility.
    return undefined;
  }
}

export const INTRO_AUTHORIZATION_VERSION = "card-first-intro-v1";
const money = cents => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(cents / 100);
const billingStates = new Set(["not_started", "checkout_pending", "active", "billing_pending", "requires_action", "payment_failed", "cancelled", "converted", "review_required", "unavailable"]);

export function introProviderUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password
      && ["stripe.com", "link.com"].some(host => url.hostname === host || url.hostname.endsWith("." + host)) ? url.href : null;
  } catch { return null; }
}
function visitProvider(url) { window.location.assign(url); }

export default function IntroAccessCard(props) {
  return <CardFirstIntroduction key={props.owner} {...props} />;
}

function CardFirstIntroduction({ api, owner, initial, plans = [], initialPlan, sessionId, onClaimed, navigate = visitProvider }) {
  const [info, setInfo] = useState(initial || null);
  const [loadError, setLoadError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const [authorized, setAuthorized] = useState(false);
  const [planId, setPlanId] = useState(initialPlan || "plus_100");
  const [token, setToken] = useState("");
  const [revision, setRevision] = useState(0);
  const active = useRef(true);
  const pending = useRef(false);
  const request = useRef(null);
  const setupRequest = useRef(null);
  const cancelRequest = useRef(null);
  const confirmedReturn = useRef(null);
  const callback = useRef(onClaimed);
  callback.current = onClaimed;
  const savedBillingRequired = useRef(initial?.billing_required);
  const parentRefresh = useRef({ needed: false, pending: false });
  const catalog = plans.filter(plan => /^plus_(100|250|500)$/.test(plan.id) && plan.available !== false
    && Number.isInteger(plan.price_cents) && plan.price_cents > 0 && Number.isInteger(plan.messages) && plan.messages > 0);
  const selected = catalog.find(plan => plan.id === planId);
  const candidateQuote = info?.quotes?.[planId];
  const selectedQuote = selected && candidateQuote?.id === selected.id && candidateQuote.name === selected.name
    && candidateQuote.price_cents === selected.price_cents && candidateQuote.messages === selected.messages
    && candidateQuote.currency === "usd" && candidateQuote.authorization_version === INTRO_AUTHORIZATION_VERSION
    && Number.isInteger(candidateQuote.remaining_intro_messages) && candidateQuote.remaining_intro_messages > 0 && candidateQuote.remaining_intro_messages <= 10
    && typeof candidateQuote.authorization_text === "string" && candidateQuote.authorization_text.length > 0 && candidateQuote.authorization_text.length <= 6000
    && /^[a-f0-9]{64}$/.test(candidateQuote.authorization_text_sha256 || "") && /^[a-f0-9]{64}$/.test(candidateQuote.quote_sha256 || "") ? candidateQuote : null;
  useEffect(() => { setAuthorized(false); }, [selected?.id, selected?.name, selected?.price_cents, selected?.messages,
    selectedQuote?.authorization_text, selectedQuote?.authorization_text_sha256, selectedQuote?.quote_sha256]);
  const load = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    try {
      const { data } = await api.get("/me/intro-access", { signal: controller.signal });
      if (!controller.signal.aborted && active.current) {
        setInfo(data); setLoadError(false);
        if (billingStates.has(data.billing?.state)) setUncertain(false);
        const verifiedStatus = data.authorization_version === INTRO_AUTHORIZATION_VERSION
          && data.requires_payment_method === true && data.automatic_charge === true
          && billingStates.has(data.billing?.state) && typeof data.billing_required === "boolean";
        if (verifiedStatus) {
          if (savedBillingRequired.current === true && data.billing_required === false) parentRefresh.current.needed = true;
          savedBillingRequired.current = data.billing_required;
          // A denied setup alone cannot authorize a purchase. Refresh the
          // owning plan view only after its saved server status confirms the
          // transition; that view still requires fresh paid-purchase consent.
          if (parentRefresh.current.needed && !parentRefresh.current.pending) {
            parentRefresh.current.pending = true;
            try {
              await callback.current?.();
              parentRefresh.current.needed = false;
            } finally {
              parentRefresh.current.pending = false;
            }
          }
        }
      }
    } catch {
      if (!controller.signal.aborted && active.current) setLoadError(true);
    } finally {
      if (!controller.signal.aborted && active.current) setLoading(false);
    }
  }, [api]);
  useEffect(() => {
    active.current = true;
    load();
    const visible = () => { if (!document.hidden && !pending.current) load(); };
    document.addEventListener("visibilitychange", visible);
    return () => {
      active.current = false;
      request.current?.abort();
      document.removeEventListener("visibilitychange", visible);
    };
  }, [load]);

  async function perform(action, payload) {
    if (pending.current) return;
    pending.current = true;
    setBusy(action); setError("");
    request.current?.abort();
    try {
      const result = await api.post(`/me/intro-access/${action}`, payload);
      if (!active.current) return;
      const data = result.data;
      if (action === "setup") {
        const url = introProviderUrl(data.url);
        if (!url) throw new Error("We couldn’t verify the secure card-setup link. Check your setup status before trying again.");
        setInfo(previous => ({ ...previous, selected_plan: data.selected_plan || previous.selected_plan,
          billing: { ...previous.billing, state: "checkout_pending" } }));
        navigate(url);
      } else {
        const snapshot = data.intro_access || data;
        if (!billingStates.has(snapshot.billing?.state)) throw new Error("We couldn’t confirm the saved billing status.");
        if (action === "cancel" && snapshot.billing.state !== "cancelled") throw new Error("Cancellation is not confirmed. Check your saved status before using another message.");
        setInfo(snapshot);
        setUncertain(false);
        await callback.current?.();
      }
    } catch (failure) {
      if (!active.current) return;
      setError(errMsg(failure));
      setUncertain(true);
    } finally {
      pending.current = false;
      if (active.current) { setBusy(""); setLoading(false); setToken(""); setRevision(n => n + 1); }
    }
  }
  useEffect(() => {
    if (!sessionId || !owner || confirmedReturn.current === sessionId) return;
    confirmedReturn.current = sessionId;
    // A URL is not card verification. Only the server can confirm this session.
    // Remember the first attempt before sending, so a page reload cannot replay
    // an uncertain confirmation. GET status can reconcile the known session.
    try {
      const key = `imladri:intro-confirm:${owner}:${sessionId}`;
      if (sessionStorage.getItem(key)) { setUncertain(true); return; }
      sessionStorage.setItem(key, "attempted");
    } catch { setUncertain(true); return; }
    perform("confirm", { expected_member: owner, session_id: sessionId });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, owner]);

  if (!info) return <section className="card p-4 text-sm" data-testid="intro-access-loading">
    <p role="status">{busy === "confirm" ? "Verifying your returned card setup with Stripe. Introductory access starts only after the server confirms it." : uncertain ? "Your card setup result is not confirmed. Check the saved status; this page will not automatically repeat the request." : "Checking introductory access…"}</p>
    {(loadError || uncertain) && <button type="button" className="mt-2 underline" disabled={!!busy} onClick={load} data-testid="refresh-intro-status">Check saved status</button>}
    {loadError && <p role="alert" className="mt-2">We couldn’t check introductory access. You can still review the plans below.</p>}
    {error && <p role="alert" className="mt-2">{error}</p>}
  </section>;
  if (!info.enabled && !info.billing?.agreement_id && !["checkout_pending", "active", "billing_pending", "requires_action", "payment_failed", "review_required", "cancelled"].includes(info.billing?.state)) return null;
  const billing = info.billing || {};
  const state = billing.state || "unavailable";
  const cardFirst = info.requires_payment_method === true && info.automatic_charge === true
    && info.authorization_version === INTRO_AUTHORIZATION_VERSION;
  const agreed = info.selected_plan;
  const agreedPlan = agreed && Number.isInteger(agreed.price_cents) && agreed.price_cents > 0
    && Number.isInteger(agreed.messages) && agreed.messages > 0 ? agreed : null;
  const hasGrant = ["active", "exhausted"].includes(info.state);
  const remaining = Number.isInteger(info.messages_remaining) && info.messages_remaining >= 0 && info.messages_remaining <= 10 ? info.messages_remaining : null;
  const siteKey = info.turnstile_site_key || info.challenge?.site_key;
  const canSetup = info.enabled === true && cardFirst && info.can_claim === true && state === "not_started" && !loading && !loadError && !uncertain;
  const canCancel = cardFirst && billing.can_cancel === true && Number.isInteger(billing.revision) && !loading && !loadError && !uncertain;
  const actionUrl = introProviderUrl(billing.action_url);
  const disabled = Boolean(busy || loading || loadError || uncertain);
  function setup() {
    if (!canSetup || !selected || !selectedQuote || !authorized || (info.challenge_required && !token) || pending.current) return;
    const signature = JSON.stringify([selected.id, selected.price_cents, selected.messages, selectedQuote.quote_sha256]);
    if (setupRequest.current?.signature !== signature) setupRequest.current = { signature, id: crypto.randomUUID() };
    perform("setup", { expected_member: owner, plan_id: selected.id, request_id: setupRequest.current.id,
      expected_price_cents: selected.price_cents, expected_messages: selected.messages,
      authorization_text_sha256: selectedQuote.authorization_text_sha256, quote_sha256: selectedQuote.quote_sha256,
      authorization_version: INTRO_AUTHORIZATION_VERSION, authorize_after_messages: true, policy_accepted: true,
      origin_url: window.location.origin,
      ...(token && { challenge_token: token }),
      ...((value => value ? { device_token: value } : {})(deviceToken())),
    });
  }
  function cancel() {
    if (!canCancel || pending.current) return;
    if (cancelRequest.current?.revision !== billing.revision) cancelRequest.current = { revision: billing.revision, id: crypto.randomUUID() };
    perform("cancel", { expected_member: owner, request_id: cancelRequest.current.id, revision: billing.revision });
  }
  return <section id="intro-access" className="card scroll-mt-20 p-5 sm:p-6" aria-labelledby="intro-access-title" data-testid="intro-access-card">
    <h2 id="intro-access-title" className="font-display font-black uppercase text-2xl">{hasGrant ? "Your introductory messages" : selectedQuote?.remaining_intro_messages < 10 ? "Continue your introduction" : "Start with 10 free AI messages"}</h2>
    {hasGrant && <p className="mt-2 text-sm font-semibold" data-testid="intro-card-balance">Free messages: {remaining === null ? "Checking balance" : `${remaining} of 10 remaining`}</p>}
    {cardFirst && agreedPlan && <p className="mt-2 text-sm" data-testid="intro-selected-plan">Selected plan: <strong>{agreedPlan.name} · {money(agreedPlan.price_cents)}/month</strong> · {agreedPlan.messages} paid messages per billing month, plus applicable tax.</p>}
    {cardFirst && <p className="mt-2 text-sm text-[var(--muted)]">10 free AI messages total across Individual and Couples. After your tenth free AI message is delivered, your selected monthly plan is charged automatically, plus applicable tax. Its full paid allowance starts after payment is verified. Cancel here before using all 10 to prevent that first charge. Monthly renewals then continue until canceled.</p>}
    {canSetup && <div className="mt-4 space-y-3">
      {selectedQuote?.remaining_intro_messages < 10 && <p className="text-sm" data-testid="intro-existing-usage">You have already used {10 - selectedQuote.remaining_intro_messages} of your 10 free messages. {selectedQuote.remaining_intro_messages} remain. Verifying your card does not reset that allowance.</p>}
      <label className="block text-sm font-semibold" htmlFor="intro-plan">Choose your monthly plan</label>
      <select id="intro-plan" className="w-full min-w-0 rounded-xl border border-[var(--line)] bg-[var(--bg)] p-3 text-sm text-[var(--ink)]" value={selected?.id || ""} disabled={disabled} data-testid="intro-plan-select"
        onChange={event => { setPlanId(event.target.value); setAuthorized(false); }}>
        {!selected && <option value="" disabled>Select a plan</option>}
        {catalog.map(plan => <option key={plan.id} value={plan.id}>{plan.name} · {money(plan.price_cents)}/month · {plan.messages} paid messages</option>)}
      </select>
      {selectedQuote && <label className="flex items-start gap-3 text-sm" data-testid="intro-authorization-copy">
        <input type="checkbox" className="mt-1 shrink-0" checked={authorized} disabled={disabled} data-testid="intro-billing-authorize" onChange={event => setAuthorized(event.target.checked)} />
        <span className="whitespace-pre-line">{selectedQuote.authorization_text}</span>
      </label>}
      {!selectedQuote && <p role="status" className="text-sm">The current plan quote and exact authorization terms could not be verified. Check saved status before starting card setup.</p>}
      <p className="text-xs text-[var(--muted)]">Read the <Link className="underline" to="/terms">Terms</Link> and <Link className="underline" to="/billing">Billing &amp; refunds policy</Link> before proceeding. Mandatory consumer rights and the payment provider’s applicable refund policy still apply.</p>
      <p className="text-xs text-[var(--muted)]">No subscription payment is due at card setup. Your bank may show a temporary verification authorization, and Stripe may ask you to authenticate your card.</p>
      {info.challenge_required && siteKey && <IntroSecurityCheck siteKey={siteKey} action={info.challenge?.action || "intro-access"} onToken={setToken} revision={revision} />}
      {info.challenge_required && !siteKey && <p role="status" className="text-sm">The security check is temporarily unavailable. Please try again later.</p>}
      <button type="button" className="btn-lime px-5 py-2.5 text-sm disabled:opacity-50" disabled={disabled || !selectedQuote || !authorized || (info.challenge_required && !token)} aria-busy={busy === "setup"} onClick={setup} data-testid="setup-intro-card">{busy === "setup" ? "Opening secure card setup…" : selectedQuote?.remaining_intro_messages < 10 ? `Verify card and continue ${selectedQuote.remaining_intro_messages} free messages` : "Verify card and start 10 free messages"}</button>
    </div>}
    {busy === "confirm" && <p role="status" className="mt-3 text-sm">Verifying your returned card setup with Stripe. Introductory access starts only after the server confirms it.</p>}
    {state === "checkout_pending" && <p role="status" className="mt-3 text-sm">Your card setup is not confirmed yet. Finish the secure Stripe step, then check your saved status. No introductory messages have been granted by this screen.</p>}
    {state === "active" && remaining > 0 && !uncertain && <Link to="/app/start" className="btn-lime inline-flex mt-4 px-5 py-2.5 text-sm">Continue to Imladri</Link>}
    {state === "billing_pending" && <p role="status" className="mt-3 text-sm">Your 10 free messages are complete. Your selected plan payment is being verified. Please wait; your paid allowance starts only after confirmation.</p>}
    {["requires_action", "payment_failed"].includes(state) && <div role="status" className="mt-3 text-sm"><p>{state === "requires_action" ? "Your bank needs additional authentication before paid access can start." : "Your selected plan payment was not confirmed. Paid messages have not been added."} Do not start another purchase while this payment is unresolved.</p>{actionUrl && <a className="inline-flex mt-2 underline font-semibold" href={actionUrl} data-testid="intro-payment-action">Complete secure payment verification</a>}</div>}
    {state === "review_required" && <p role="status" className="mt-3 text-sm">Your billing status needs review before paid access can start. <Link className="underline" to="/support?topic=Billing">Contact billing support</Link>.</p>}
    {state === "cancelled" && <p role="status" className="mt-3 text-sm" data-testid="intro-cancelled">Your introduction is canceled. No first subscription charge will be started under this introductory authorization. Your saved content is retained.</p>}
    {state === "converted" && <p role="status" className="mt-3 text-sm">Your first subscription payment is confirmed. Refresh your plan to see its paid allowance.</p>}
    {(!cardFirst || state === "unavailable") && <p role="status" className="mt-3 text-sm">Card-first introductory setup is currently unavailable for this account. No card or subscription has been confirmed by this screen. You can review the plans and contact billing support.</p>}
    {cardFirst && info.state === "verification_required" && <p role="status" className="mt-3 text-sm">Verify your sign-in email before starting the introduction, then check again.</p>}
    {cardFirst && info.state === "paused" && <p role="status" className="mt-3 text-sm">New introductions and introductory AI requests are temporarily paused.</p>}
    {cardFirst && state === "not_started" && info.can_claim !== true && !["paused", "verification_required"].includes(info.state) && <p role="status" className="mt-3 text-sm">This account is not eligible to start the introduction. You can review the available paid plans below.</p>}
    {canCancel && <button type="button" className="btn-ghost mt-4 px-5 py-2.5 text-sm disabled:opacity-50" disabled={disabled} onClick={cancel} data-testid="cancel-intro">{busy === "cancel" ? "Canceling introduction…" : "Cancel introduction before first charge"}</button>}
    {(uncertain || loadError || (canSetup && !selectedQuote) || ["checkout_pending", "billing_pending", "requires_action", "payment_failed", "converted"].includes(state)) && <button type="button" className="mt-3 ms-3 underline text-sm disabled:opacity-50" disabled={!!busy} onClick={load} data-testid="refresh-intro-status">Check saved status</button>}
    {loadError && <p role="alert" className="mt-3 text-sm">We couldn’t refresh your current eligibility or billing status. Starting or canceling setup is disabled until it can be checked.</p>}
    {uncertain && <p role="status" className="mt-3 text-sm">This request’s result is not confirmed. Check the saved status before trying again; this page will not automatically repeat it.</p>}
    {error && <p role="alert" className="mt-3 text-sm">{error}</p>}
  </section>;
}
