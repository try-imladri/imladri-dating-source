import { act } from "react";
import { createRoot } from "react-dom/client";
import { useOutletContext, useSearchParams } from "react-router-dom";
import Upgrade from "./Upgrade";
import { purchaseAcceptance } from "@/components/app/PurchasePolicy";

jest.mock("react-router-dom", () => ({
  Link: ({ children, to }) => <a href={to}>{children}</a>,
  useOutletContext: jest.fn(),
  useSearchParams: jest.fn(),
}));
jest.mock("@/lib/api", () => ({ errMsg: () => "Payment could not be confirmed." }));
jest.mock("sonner", () => ({ toast: { error: jest.fn() } }));

let root, host, api, membership;
const button = label => [...host.querySelectorAll("button")].find(el => el.textContent === label);
const mount = async () => {
  await act(async () => root.render(<Upgrade />));
  const ack=host.querySelector('[data-testid="purchase-policy-accept"]');
  if(ack && !ack.checked) await act(async()=>ack.click());
};
const click = el => act(async () => el.click());

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.useFakeTimers();
  membership = { price_label: "$12/month", usage: {}, paid_features: ["Full memory history"], billing_enabled: true,
    checkout_available: true, topup_available: false, partner_offer: {}, headline: "Plans",
    plans: [[100, 1200], [250, 2000], [500, 4000]].map(([messages, price_cents]) => ({
      id: `plus_${messages}`, name: `Plus ${messages}`, messages, price_cents, available: true,
    })) };
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  api = { get: jest.fn(async () => ({ data: membership })), post: jest.fn() };
  useOutletContext.mockReturnValue({ api, me: {}, refreshUsage: jest.fn() });
  useSearchParams.mockReturnValue([new URLSearchParams(), jest.fn()]);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  jest.useRealTimers();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});

test("purchase acknowledgment is unchecked, required for each account, and never required to manage billing", async () => {
  active(100);
  membership.portal_available=true;
  useOutletContext.mockReturnValue({api,me:{user_id:'policy-a'},refreshUsage:jest.fn()});
  await act(async()=>root.render(<Upgrade />));
  expect(host.querySelector('[data-testid="purchase-policy-accept"]').checked).toBe(false);
  expect(button('Add 100 messages · $12').disabled).toBe(true);
  expect(button('Upgrade to Plus 250').disabled).toBe(true);
  expect(button('Manage billing').disabled).toBe(false);
  await click(host.querySelector('[data-testid="purchase-policy-accept"]'));
  expect(button('Add 100 messages · $12').disabled).toBe(false);
  useOutletContext.mockReturnValue({api,me:{user_id:'policy-b'},refreshUsage:jest.fn()});
  await act(async()=>root.render(<Upgrade />));
  expect(host.querySelector('[data-testid="purchase-policy-accept"]').checked).toBe(false);
  expect(button('Add 100 messages · $12').disabled).toBe(true);
  expect(api.post).not.toHaveBeenCalled();
});

test("a spent refunded refill requires review before accepting another top-up payment", async () => {
  active(100);
  membership.topup_available=false;
  membership.topup_blocked_reason="Refunded messages were already used. Contact billing support before buying more.";
  await mount();
  expect(button('Add 100 messages · $12').disabled).toBe(true);
  expect(host.textContent).toContain('Refunded messages were already used');
  expect(host.querySelector('a[href="/support?topic=Billing"]')).not.toBeNull();
  expect(api.post).not.toHaveBeenCalled();
});

test("a paid pack held for billing review never displays a false top-up success or starts another purchase", async () => {
  active(100);
  useSearchParams.mockReturnValue([new URLSearchParams('session_id=cs_review_fixture'),jest.fn()]);
  api.get.mockImplementation(async path=>({data:path.startsWith('/payments/status/')?
    {payment_status:'paid',kind:'topup',review_required:true,payment_verified:true,credit_delivery_status:'review_required'}:membership}));
  await mount();
  expect(host.querySelector('[data-testid="payment-review"]').textContent).toContain('payment was received');
  expect(host.querySelector('[data-testid="payment-topped-up"]')).toBeNull();
  expect(button('Add 100 messages · $12').disabled).toBe(true);
  expect(api.post).not.toHaveBeenCalled();
  expect(host.querySelector('a[href="/support?topic=Billing"]')).not.toBeNull();
});

test("invited partner explicitly requests the discounted price, including when Stripe is unavailable", async () => {
  membership.partner_offer = { monthly_eligible: true, monthly_available: true };
  api.post.mockRejectedValue(new Error());
  await mount();
  expect(host.querySelector('[data-testid="plan-price"]').textContent).toContain("$10");
  await click(button("Get Plus for $10/month"));
  expect(api.post.mock.calls).toEqual([["/payments/checkout", { ...purchaseAcceptance(), origin_url: window.location.origin, offer: "partner_monthly" }]]);
});

test("the live billing gate disables partner checkout", async () => {
  membership.billing_enabled = false;
  membership.checkout_available = false;
  membership.partner_offer = { monthly_eligible: true, monthly_available: false };
  await mount();
  expect(button("Get Plus for $10/month").disabled).toBe(true);
  expect(host.textContent).toContain("New purchases are currently closed");
});

test("closing new sales leaves billing management available without accepting another purchase", async () => {
  active(100);membership.billing_enabled=false;membership.portal_available=true;
  api.post.mockRejectedValue(new Error('fixture portal unavailable'));
  await act(async()=>root.render(<Upgrade/>));
  expect(host.querySelector('[data-testid="purchase-policy-accept"]').checked).toBe(false);
  expect(button('Add 100 messages · $12').disabled).toBe(true);
  expect(button('Manage billing').disabled).toBe(false);
  await click(button('Manage billing'));
  expect(api.post).toHaveBeenCalledWith('/payments/portal',{origin_url:window.location.origin});
});

function active(count = 250) {
  membership.usage = { plan: "plus", plan_id: `plus_${count}`, plan_name: `Plus ${count}`, message_limit: count,
    messages_remaining: count + 65, monthly_messages_remaining: count - 35, topup_remaining: 100, period_verified: true };
  Object.assign(membership, { existing_subscription: true, checkout_available: false, upgrade_available: true, topup_available: true });
}

test("paired Plans explains Couples-only access, keeps the own allowance and preserves the chosen upgrade request", async () => {
  active(100);
  membership.pitch = "Individual and Your Type are included";
  membership.paid_features = ["Individual access", "Your Type"];
  useOutletContext.mockReturnValue({ api, me: { user_id: "paired-member", workspace_access: {
    version: "workspace-access-v1", mode: "couples", couples_only: true, pair_id: "active-pair",
    individual: false, your_type: false, assessments: true,
  } }, refreshUsage: jest.fn() });
  api.post.mockRejectedValue(new Error("Fixture checkout unavailable"));
  await mount();
  expect(host.querySelector('[data-testid="paired-plan-scope"]').textContent).toContain("Individual and Your Type are unavailable");
  expect(host.textContent).not.toContain("Individual access");
  expect(host.textContent).not.toContain("Individual and Your Type are included");
  expect(host.textContent).toContain("Your monthly AI reply allowance");
  expect(host.querySelector('a[href="/app/couples"]').textContent).toBe("Continue to Couples");
  expect(host.querySelector('[data-testid="plan-plus_250"]').textContent).toContain("$20");
  expect(host.querySelector('[data-testid="plan-plus_500"]').textContent).toContain("$40");
  await click(button("Upgrade to Plus 250"));
  expect(api.post).toHaveBeenCalledWith("/payments/upgrade", { ...purchaseAcceptance(), origin_url: window.location.origin,
    expected_member: "paired-member", offer: "plus_250" });
});

test("Plans distinguishes unused messages under temporary review from a refund adjustment", async () => {
  active(100);
  membership.usage = { ...membership.usage, allowance_adjusted: true, original_message_limit: 100,
    payment_dispute_hold: true, held_message_count: 23 };
  await mount();
  expect(host.querySelector('[data-testid="plan-payment-review"]').textContent).toContain("23 unused messages are temporarily unavailable");
  expect(host.textContent).not.toContain("Your monthly allowance was adjusted after a refund or lost dispute");
  expect(api.post).not.toHaveBeenCalled();
});

test("dashboard offers each catalog price and only takes action on the chosen plan", async () => {
  useSearchParams.mockReturnValue([new URLSearchParams("plan=plus_250"), jest.fn()]);
  api.post.mockRejectedValue(new Error());
  await mount();
  expect(host.querySelector('[data-testid="current-plan"]').textContent).toContain("No active subscription");
  expect(host.querySelector('[data-testid="plan-plus_250"]').textContent).toContain("$20");
  expect(host.querySelector('[data-testid="plan-plus_500"]').textContent).toContain("$40");
  expect(host.querySelector('[data-testid="buy-topup"]').disabled).toBe(true);
  expect(api.post).not.toHaveBeenCalled();
  await click(button("Choose Plus 250"));
  expect(api.post).toHaveBeenCalledWith("/payments/checkout", { ...purchaseAcceptance(), origin_url: window.location.origin, offer: "plus_250" });
});

test("a paying member sees the verified plan and separate balances, and upgrades their existing subscription", async () => {
  active();
  api.post.mockRejectedValue(new Error());
  await mount();
  const current = host.querySelector('[data-testid="current-plan"]');
  expect(current.textContent).toContain("Plus 250");
  expect(current.textContent).toContain("215 of 250");
  expect(current.textContent).toContain("Top-up messages left100");
  expect(button("Choose Plus 100")).toBeUndefined();
  expect(button("Upgrade to Plus 250")).toBeUndefined();
  expect(host.textContent).toContain("starts a new billing month");
  await click(button("Upgrade to Plus 500"));
  expect(api.post).toHaveBeenCalledWith("/payments/upgrade", { ...purchaseAcceptance(), origin_url: window.location.origin, offer: "plus_500" });
});

test("a one-time refill keeps the same request key on an uncertain retry", async () => {
  active(100);
  global.crypto = { randomUUID: require("crypto").randomUUID };
  api.post.mockRejectedValue(new Error());
  await mount();
  const topUp = host.querySelector('[data-testid="buy-topup"]');
  expect(topUp.textContent).toBe("Add 100 messages · $12");
  await click(topUp);
  await click(topUp);
  expect(api.post.mock.calls).toHaveLength(2);
  const [url, body, opts] = api.post.mock.calls[0];
  expect(url).toBe("/payments/topup");
  expect(body).toEqual({ ...purchaseAcceptance(), origin_url: window.location.origin });
  expect(opts.headers["Idempotency-Key"]).toBeTruthy();
  expect(api.post.mock.calls[1]).toEqual(api.post.mock.calls[0]);

});

test("a reload reuses an uncertain refill for the same member only", async () => {
  active(100);
  global.crypto = { randomUUID: require("crypto").randomUUID };
  api.post.mockRejectedValue(new Error("Connection lost"));
  const owner = id => useOutletContext.mockReturnValue({ api, me: { user_id: id }, refreshUsage: jest.fn() });
  const reset = () => { act(() => root.unmount()); root = createRoot(host); };
  owner("refill-a"); await mount(); await click(button("Add 100 messages · $12"));
  const original = api.post.mock.calls[0][2].headers["Idempotency-Key"];
  reset(); owner("refill-a"); await mount(); await click(button("Add 100 messages · $12"));
  expect(api.post.mock.calls[1][2].headers["Idempotency-Key"]).toBe(original);
  reset(); owner("refill-b"); await mount(); await click(button("Add 100 messages · $12"));
  expect(api.post.mock.calls[2][2].headers["Idempotency-Key"]).not.toBe(original);
});

test("a failed plan refresh labels old balances and disables purchases until recovery", async () => {
  active(100); await mount();
  api.get.mockRejectedValueOnce(new Error("Connection lost"));
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(host.querySelector('[role="alert"]').textContent).toContain("couldn’t refresh");
  expect(button("Add 100 messages · $12").disabled).toBe(true);
  await click(button("Refresh plan"));
  expect(host.querySelector('[role="alert"]')).toBeNull();
  expect(button("Add 100 messages · $12").disabled).toBe(false);
});

test("a return URL alone cannot mark an upgrade paid, and an inactive member keeps their purchased balance", async () => {
  useSearchParams.mockReturnValue([new URLSearchParams("upgrade=complete&plan=plus_500"), jest.fn()]);
  membership.usage = { plan: "unsubscribed", topup_remaining: 45, messages_remaining: 0 };
  await mount();
  expect(host.textContent).toContain("45 purchased messages are saved");
  expect(host.textContent).toContain("Checking your payment");
  expect(host.textContent).not.toContain("Your upgraded plan is active");
  expect(host.querySelector('[data-testid="buy-topup"]').disabled).toBe(true);
});

test("a verified refill return reports a one-time payment and refreshes the member's balance", async () => {
  active(100);
  useSearchParams.mockReturnValue([new URLSearchParams("session_id=cs_fixture"), jest.fn()]);
  api.get.mockImplementation(async path => ({ data: path.includes("/payments/status/") ?
    { payment_status: "paid", kind: "topup", tier: "paid" } : membership }));
  await mount();
  expect(host.textContent).toContain("Your top-up payment is confirmed");
  expect(useOutletContext().refreshUsage).toHaveBeenCalled();
});

test("administrator access keeps its verified paid plan visible without offering unnecessary refills", async () => {
  active(500);
  membership.membership_plan_id = "plus_500";
  membership.usage = { plan: "admin", unlimited: true, period_verified: true };
  await mount();
  expect(host.querySelector('[data-testid="current-plan"]').textContent).toContain("Plus 500 · Administrator");
  expect(host.textContent).toContain("unlimited personal messages");
  expect(host.querySelector('[data-testid="buy-topup"]')).toBeNull();
});

function annualOffer(seconds = 100) {
  return { annual_eligible: true, annual_available: true, annual_expires_at: new Date(Date.now() + seconds * 1000).toISOString(),
    annual_starts_at: new Date(Date.now() + 86400000).toISOString(), annual_ends_at: new Date(Date.now() + 366 * 86400000).toISOString() };
}

test("one account separately reviews and authorizes its year, including after a recoverable failure", async () => {
  membership.partner_offer = annualOffer();
  api.post.mockRejectedValueOnce(new Error()).mockResolvedValueOnce({ data: { accepted: true } });
  await mount();
  await click(button("Review $80 year"));
  expect(api.post).not.toHaveBeenCalled();
  expect(host.textContent).toContain("no automatic renewal");
  await click(button("Confirm my $80 year"));
  expect(host.querySelector('[role="alert"]').textContent).toContain("could not be confirmed");
  expect(host.textContent).not.toContain("Your $80 year is scheduled");
  await click(button("Review $80 year"));
  await click(button("Confirm my $80 year"));
  expect(api.post.mock.calls[1]).toEqual(["/payments/partner-annual", { ...purchaseAcceptance(), accept: true, expected_starts_at: membership.partner_offer.annual_starts_at }]);
  expect(host.textContent).toContain("Your $80 year is scheduled");
  expect(button("Confirm my $80 year")).toBeUndefined();
});

test("an open review becomes unavailable at the exact deadline", async () => {
  membership.partner_offer = annualOffer(1);
  await mount();
  await click(button("Review $80 year"));
  act(() => jest.advanceTimersByTime(1000));
  expect(button("Confirm my $80 year")).toBeUndefined();
  expect(api.post).not.toHaveBeenCalled();
});

test("a scheduled year remains visible after the offer expires", async () => {
  membership.partner_offer = { ...annualOffer(-1), annual_eligible: false, annual_accepted: true };
  await mount();
  expect(host.textContent).toContain("Your $80 year is scheduled");
  expect(button("Review $80 year")).toBeUndefined();
});

test("an authorization recorded before the deadline can finish confirmation afterward", async () => {
  membership.partner_offer = { ...annualOffer(-1), annual_eligible: false, annual_pending: true };
  api.post.mockResolvedValueOnce({ data: { accepted: true } });
  await mount();
  await click(button("Retry confirmation"));
  expect(api.post.mock.calls).toEqual([["/payments/partner-annual", { ...purchaseAcceptance(), accept: true }]]);
  expect(host.textContent).toContain("Your $80 year is scheduled");
});

test("canceling a future year requires explicit confirmation from the owning account", async () => {
  membership.partner_offer = { ...annualOffer(-1), annual_eligible: false, annual_accepted: true, annual_cancel_available: true };
  api.post.mockResolvedValueOnce({ data: { cancelled: true } });
  await mount();
  await click(button("Cancel scheduled membership"));
  expect(api.post).not.toHaveBeenCalled();
  expect(host.textContent).toContain("ends your membership");
  await click(button("Confirm cancellation"));
  expect(api.post.mock.calls).toEqual([["/payments/cancel-partner-year", { accept: true }]]);
  expect(host.textContent).toContain("Scheduled year canceled");
});

test("switching accounts drops the previous member’s balance and late checkout redirect", async () => {
  active(500);
  let viewer = "owner-a";
  const ctx = () => ({api, me:{user_id:viewer}, refreshUsage:jest.fn()});
  useOutletContext.mockImplementation(ctx);
  let completeCheckout;
  api.post.mockImplementationOnce(() => new Promise(resolve => { completeCheckout = resolve; }));
  await mount();
  await click(button("Add 100 messages · $12"));
  expect(api.post.mock.calls[0][1].expected_member).toBe("owner-a");
  let completeReload;
  api.get.mockImplementationOnce(() => new Promise(resolve => { completeReload = resolve; }));
  viewer = "owner-b";
  await mount();
  expect(host.textContent).toContain("Loading plans");
  expect(host.textContent).not.toContain("465 of 500");
  const readUrl = jest.fn(() => window.location.origin);
  await act(async () => completeCheckout({data:Object.defineProperty({}, "checkout_url", {get:readUrl})}));
  expect(readUrl).not.toHaveBeenCalled();
  await act(async () => completeReload({data:{...membership,usage:{plan:"unsubscribed",topup_remaining:5}}}));
  expect(host.textContent).toContain("5 purchased messages");
  expect(button("Add 100 messages · $12").disabled).toBe(true);
});

test("keeping Plans open across expiry rechecks the period before enabling a refill", async () => {
  active(250);
  membership.usage.period_end = Math.floor(Date.now()/1000) + 1;
  await mount();
  expect(button("Add 100 messages · $12").disabled).toBe(false);
  let completeReload;
  api.get.mockImplementationOnce(() => new Promise(resolve => {completeReload=resolve;}));
  await act(async () => jest.advanceTimersByTime(1000));
  expect(host.textContent).toContain("Your last billing period ended");
  expect(button("Add 100 messages · $12").disabled).toBe(true);
  await act(async () => completeReload({data:{...membership,membership_plan_id:null,usage:{
    plan:"unsubscribed",period_verified:false,topup_remaining:100},topup_available:false}}));
  expect(host.textContent).toContain("100 purchased messages are saved");
  expect(host.textContent).not.toContain("215 of 250");
  expect(api.post).not.toHaveBeenCalled();
});

test("complimentary ordinary members see unlimited usage without a paid-plan or purchase offer", async () => {
  membership.complimentary = true;
  membership.membership_plan_id = null;
  membership.usage = { plan: "complimentary", plan_id: "complimentary_unlimited", plan_name: "Complimentary Unlimited",
    unlimited: true, messages_used: 14, message_limit: null, messages_remaining: null, topup_remaining: 0 };
  // Stale selling flags must not offer a charge to an explicitly complimentary account.
  Object.assign(membership, { checkout_available: true, upgrade_available: true, topup_available: true,
    partner_offer: { monthly_eligible: true, monthly_available: true, annual_eligible: true } });
  const me = { user_id: "complimentary-owner", role: "member", personal_access: true, membership_plan_id: null };
  useOutletContext.mockReturnValue({ api, me, refreshUsage: jest.fn() });
  await mount();
  expect(host.querySelector('[data-testid="current-plan"]').textContent).toContain("Complimentary Unlimited");
  expect(host.querySelector('[data-testid="complimentary-usage"]').textContent).toContain("14 complimentary messages used · all time");
  expect(host.textContent).not.toContain("administrator access");
  expect(host.querySelector('[aria-label="Monthly plans"]')).toBeNull();
  expect(host.querySelector('[data-testid="buy-topup"]')).toBeNull();
  expect(host.querySelector('[data-testid="billing-portal"]')).toBeNull();
  expect(host.textContent).not.toContain("Current allowance");
  expect(api.post).not.toHaveBeenCalled();
  expect(me.role).toBe("member");
  expect(me.membership_plan_id).toBeNull();
});

test("canonical complimentary usage never becomes a current paid plan from an unrelated Stripe plan ID", async () => {
  membership.membership_plan_id = "plus_500";
  membership.usage = { plan: "complimentary", plan_id: "complimentary_unlimited", unlimited: true, messages_used: 3 };
  await mount();
  const current = host.querySelector('[data-testid="current-plan"]');
  expect(current.textContent).toContain("Complimentary Unlimited");
  expect(current.textContent).not.toContain("$40/month");
  expect(current.textContent).not.toContain("Administrator");
  expect(host.querySelector('[data-testid="choose-plus_500"]')).toBeNull();
  expect(api.post).not.toHaveBeenCalled();
});

test("complimentary access preserves a genuine existing billing portal without offering new charges", async () => {
  membership.complimentary = true;
  membership.usage = { plan: "complimentary", plan_id: "complimentary_unlimited", unlimited: true, messages_used: 0 };
  membership.portal_available = true;
  membership.existing_subscription = true;
  api.post.mockRejectedValue(new Error("Synthetic portal stops before navigation"));
  await mount();
  await click(button("Manage billing"));
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post).toHaveBeenCalledWith("/payments/portal", { origin_url: window.location.origin });
  expect(host.querySelector('[aria-label="Monthly plans"]')).toBeNull();
});

test("introductory access has no paid period and switches to a full paid allowance only after the API confirms it", async () => {
  const introStatus = { enabled: true, state: "active", can_claim: false, messages_granted: 10,
    messages_used: 3, messages_remaining: 7, challenge_required: true };
  membership.intro_access = introStatus;
  membership.usage = { plan: "intro", plan_id: "intro_10", introductory: true, unlimited: false,
    message_limit: 10, messages_used: 3, messages_remaining: 7, period_verified: false };
  membership.membership_plan_id = null;
  api.get.mockImplementation(async path => ({ data: path === "/me/intro-access" ? { ...introStatus } : { ...membership, usage: { ...membership.usage } } }));
  useOutletContext.mockReturnValue({ api, me: { user_id: "intro-owner", intro_access: introStatus }, refreshUsage: jest.fn() });
  await act(async () => root.render(<Upgrade />));
  expect(host.querySelector('[data-testid="current-plan"]').textContent).toContain("10 introductory messages");
  expect(host.querySelector('[data-testid="intro-card-balance"]').textContent).toContain("7 of 10 remaining");
  expect(host.querySelector('[data-testid="plan-plus-usage"]')).toBeNull();
  expect(host.querySelector('[data-testid="buy-topup"]').disabled).toBe(true);
  expect(host.querySelector('[data-testid="purchase-policy-accept"]').checked).toBe(false);
  expect(api.post).not.toHaveBeenCalled();
  Object.assign(membership, { membership_plan_id: "plus_100", usage: {
    plan: "plus", plan_id: "plus_100", plan_name: "Plus 100", period_verified: true,
    message_limit: 100, messages_remaining: 100, monthly_messages_remaining: 100, topup_remaining: 0,
  } });
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(host.querySelector('[data-testid="intro-access-card"]')).toBeNull();
  expect(host.querySelector('[data-testid="current-plan"]').textContent).toContain("Plus 100");
  expect(host.querySelector('[data-testid="plan-plus-usage"]').textContent).toContain("100 of 100");
  expect(api.post).not.toHaveBeenCalled();
});


test("eligible card-first members choose and authorize above the plans without opening an ordinary payable checkout", async () => {
  const introStatus = { enabled: true, state: "eligible", can_claim: true, messages_granted: 0,
    messages_used: 0, messages_remaining: 0, requires_payment_method: true, automatic_charge: true,
    authorization_version: "card-first-intro-v1", challenge_required: false,
    billing: { state: "not_started", revision: 0, can_cancel: false } };
  introStatus.quotes = Object.fromEntries(membership.plans.map(plan => [plan.id, {
    ...plan, price_cents: plan.id === "plus_100" ? 1000 : plan.price_cents, currency: "usd", remaining_intro_messages: 10,
    authorization_version: "card-first-intro-v1", authorization_text_sha256: "a".repeat(64), quote_sha256: "b".repeat(64),
    authorization_text: plan.id === "plus_100" ? "$10.00 plus applicable tax for Plus 100" : "Server exact authorization text",
  }]));
  membership.intro_access = introStatus;
  membership.partner_offer = { monthly_eligible: true, monthly_available: true };
  api.get.mockImplementation(async path => ({ data: path === "/me/intro-access" ? { ...introStatus } : { ...membership, usage: { ...membership.usage } } }));
  useOutletContext.mockReturnValue({ api, me: { user_id: "new-card-owner", intro_access: introStatus }, refreshUsage: jest.fn() });
  await mount();
  expect(host.querySelector('[data-testid="intro-billing-authorize"]').checked).toBe(false);
  expect(host.querySelector('[data-testid="intro-authorization-copy"]').textContent).toContain("$10.00 plus applicable tax for Plus 100");
  expect(host.querySelector('[data-testid="purchase-policy-accept"]')).toBeNull();
  for (const plan of membership.plans) expect(host.querySelector(`[data-testid="choose-${plan.id}"]`)).toBeNull();
  expect(host.querySelector('a[href="#intro-access"]')).not.toBeNull();
  expect(api.post).not.toHaveBeenCalled();
});

test("exhausted introductory billing stays pending until a verified paid allowance replaces the free counter", async () => {
  const introStatus = { enabled: true, state: "exhausted", can_claim: false, messages_granted: 10,
    messages_used: 10, messages_remaining: 0, requires_payment_method: true, automatic_charge: true,
    authorization_version: "card-first-intro-v1", selected_plan: membership.plans[2],
    billing: { state: "billing_pending", revision: 3, can_cancel: false } };
  membership.intro_access = introStatus;
  membership.usage = { plan: "intro", plan_id: "intro_10", introductory: true, unlimited: false,
    message_limit: 10, messages_used: 10, messages_remaining: 0, period_verified: false };
  api.get.mockImplementation(async path => ({ data: path === "/me/intro-access" ? { ...introStatus } : { ...membership, usage: { ...membership.usage } } }));
  useOutletContext.mockReturnValue({ api, me: { user_id: "pending-card-owner" }, refreshUsage: jest.fn() });
  await mount();
  expect(host.querySelector('[data-testid="intro-card-balance"]').textContent).toContain("0 of 10");
  expect(host.querySelector('[data-testid="intro-selected-plan"]').textContent).toContain("Plus 500");
  expect(host.querySelector('[data-testid="plan-plus-usage"]')).toBeNull();
  expect(host.querySelector('[data-testid="buy-topup"]').disabled).toBe(true);
  expect(host.textContent).toContain("payment is being verified");
  expect(host.querySelector('[data-testid="choose-plus_500"]')).toBeNull();
  Object.assign(membership, { membership_plan_id: "plus_500", usage: {
    plan: "plus", plan_id: "plus_500", plan_name: "Plus 500", period_verified: true,
    message_limit: 500, messages_remaining: 500, monthly_messages_remaining: 500, topup_remaining: 0,
  } });
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(host.querySelector('[data-testid="intro-access-card"]')).toBeNull();
  expect(host.querySelector('[data-testid="plan-plus-usage"]').textContent).toContain("500 of 500");
  expect(api.post).not.toHaveBeenCalled();
});


test("a required but unavailable card-first flow cannot fall back to an immediate ordinary purchase", async () => {
  membership.intro_billing_required = true;
  membership.intro_access = { enabled: false, state: "unavailable", can_claim: false,
    requires_payment_method: true, automatic_charge: true, authorization_version: "card-first-intro-v1",
    billing: { state: "unavailable", revision: 0, can_cancel: false } };
  useOutletContext.mockReturnValue({ api, me: { user_id: "provider-unavailable-owner" }, refreshUsage: jest.fn() });
  await mount();
  expect(host.querySelector('[data-testid="purchase-policy-accept"]')).toBeNull();
  expect(host.querySelector('[data-testid="choose-plus_100"]')).toBeNull();
  expect(host.textContent).toContain("No purchase will start");
  expect(api.post).not.toHaveBeenCalled();
});
