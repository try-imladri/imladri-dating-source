import { act } from "react";
import { createRoot } from "react-dom/client";
import { useOutletContext, useSearchParams } from "react-router-dom";
import IntroAccessCard, { INTRO_AUTHORIZATION_VERSION, introProviderUrl } from "./IntroAccessCard";
import Upgrade from "@/pages/app/Upgrade";

jest.mock("react-router-dom", () => ({ Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a>, useOutletContext: jest.fn(), useSearchParams: jest.fn() }));
jest.mock("@/lib/api", () => ({ errMsg: error => error.message || "Request failed" }));

const plans = [100, 250, 500].map((messages, i) => ({ id: `plus_${messages}`, name: `Plus ${messages}`, messages, price_cents: [1200, 2000, 4000][i], available: true }));
const quotes = Object.fromEntries(plans.map((plan, index) => [plan.id, { ...plan, currency: "usd", remaining_intro_messages: 10,
  authorization_version: "card-first-intro-v1", authorization_text_sha256: "a".repeat(64), quote_sha256: ["b", "c", "d"][index].repeat(64),
  authorization_text: `I agree to the Terms and Billing policy. I authorize saving my card and charging $${(plan.price_cents / 100).toFixed(2)} plus applicable tax for ${plan.name} after my tenth free AI message is delivered, then monthly until canceled. I can cancel before the first charge. My paid plan includes ${plan.messages} messages per billing month. Mandatory consumer rights apply.` }]));
const eligible = { enabled: true, state: "eligible", can_claim: true, messages_granted: 0,
  messages_used: 0, messages_remaining: 0, requires_payment_method: true, automatic_charge: true,
  authorization_version: INTRO_AUTHORIZATION_VERSION, quotes,
  billing: { state: "not_started", revision: 0, can_cancel: false }, challenge_required: true,
  turnstile_site_key: "public-site-key", challenge: { required: true, site_key: "public-site-key", action: "intro-access" } };
const activeInfo = (overrides = {}) => ({ ...eligible, state: "active", can_claim: false, messages_granted: 10,
  messages_used: 3, messages_remaining: 7, selected_plan: plans[1],
  billing: { state: "active", revision: 2, agreement_id: "fictional-agreement", can_cancel: true }, ...overrides });
let host, root, api, challenge, onClaimed, navigate, serial;
const query = id => host.querySelector(`[data-testid="${id}"]`);
const click = node => act(async () => node.click());
const mount = (props = {}) => act(async () => root.render(<IntroAccessCard api={api} owner="fictional-owner" plans={plans} onClaimed={onClaimed} navigate={navigate} {...props} />));
const check = () => click(query("intro-billing-authorize"));
const solve = () => act(async () => challenge.callback("server-verifiable-token"));
const choose = value => act(async () => {
  query("intro-plan-select").value = value;
  query("intro-plan-select").dispatchEvent(new Event("change", { bubbles: true }));
});
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear(); sessionStorage.clear();
  host = document.body.appendChild(document.createElement("div")); root = createRoot(host);
  challenge = null; serial = 0;
  window.turnstile = { render: jest.fn((_, options) => { challenge = options; return "fictional-widget"; }), remove: jest.fn() };
  Object.defineProperty(window, "crypto", { configurable: true, value: { randomUUID: () => `00000000-0000-4000-8000-${String(++serial).padStart(12, "0")}` } });
  api = { get: jest.fn(async () => ({ data: eligible })), post: jest.fn(async () => ({ data: { url: "https://checkout.stripe.com/c/pay/fictional-setup", state: "checkout_pending", selected_plan: plans[0] } })) };
  onClaimed = jest.fn(); navigate = jest.fn();
  useSearchParams.mockReturnValue([new URLSearchParams(), jest.fn()]);
});
afterEach(() => {
  act(() => root.unmount()); host.remove(); delete window.turnstile; localStorage.clear(); sessionStorage.clear();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});

test("fresh eligibility, an unchecked exact-plan authorization and bot verification precede card setup; setup never grants credits", async () => {
  await mount();
  expect(api.get).toHaveBeenCalledWith("/me/intro-access", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(api.post).not.toHaveBeenCalled();
  expect(query("intro-billing-authorize").checked).toBe(false);
  expect(query("setup-intro-card").disabled).toBe(true);
  expect(query("intro-authorization-copy").textContent).toContain("$12.00 plus applicable tax for Plus 100");
  expect(query("intro-authorization-copy").textContent).toContain("after my tenth free AI message is delivered");
  expect(query("intro-authorization-copy").textContent).toContain("then monthly until canceled");
  expect(query("intro-authorization-copy").textContent).toContain("Mandatory consumer rights");
  expect(query("intro-authorization-copy").textContent).toBe(quotes.plus_100.authorization_text);
  await check(); expect(query("setup-intro-card").disabled).toBe(true);
  await solve(); await click(query("setup-intro-card"));
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post).toHaveBeenCalledWith("/me/intro-access/setup", {
    expected_member: "fictional-owner", plan_id: "plus_100", request_id: "00000000-0000-4000-8000-000000000001",
    expected_price_cents: 1200, expected_messages: 100, authorization_text_sha256: "a".repeat(64), quote_sha256: "b".repeat(64), authorization_version: INTRO_AUTHORIZATION_VERSION,
    authorize_after_messages: true, policy_accepted: true, origin_url: window.location.origin,
    challenge_token: "server-verifiable-token", device_token: "00000000-0000-4000-8000-000000000002",
  });
  expect(navigate).toHaveBeenCalledWith("https://checkout.stripe.com/c/pay/fictional-setup");
  expect(query("intro-card-balance")).toBeNull(); expect(onClaimed).not.toHaveBeenCalled();
  expect(host.textContent).toContain("card setup is not confirmed yet");
  expect(api.post.mock.calls.some(([path]) => path.endsWith("/claim"))).toBe(false);
});

test("choosing another plan clears authorization and binds its price and full paid allowance", async () => {
  await mount(); await check(); await solve();
  await choose("plus_250");
  expect(query("intro-billing-authorize").checked).toBe(false);
  expect(query("setup-intro-card").disabled).toBe(true);
  expect(query("intro-authorization-copy").textContent).toContain("$20.00 plus applicable tax for Plus 250");
  expect(query("intro-authorization-copy").textContent).toContain("250 messages per billing month");
  await check(); await click(query("setup-intro-card"));
  expect(api.post).toHaveBeenCalledWith("/me/intro-access/setup", expect.objectContaining({ plan_id: "plus_250", expected_price_cents: 2000, expected_messages: 250 }));
});

test("a same-plan quote change also requires a fresh unchecked authorization", async () => {
  await mount(); await check(); await solve();
  await mount({ plans: plans.map(plan => ({ ...plan, price_cents: plan.price_cents + 100 })) });
  expect(query("intro-billing-authorize")).toBeNull();
  expect(host.textContent).toContain("current plan quote and exact authorization terms could not be verified");
  expect(query("setup-intro-card").disabled).toBe(true); expect(api.post).not.toHaveBeenCalled();
});

test("an expired token and repeated setup clicks cannot bypass verification or create duplicate pending requests", async () => {
  await mount(); await check(); await solve();
  await act(async () => challenge["expired-callback"]());
  expect(query("setup-intro-card").disabled).toBe(true); await solve();
  let finish; api.post.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await act(async () => { query("setup-intro-card").click(); query("setup-intro-card").click(); });
  expect(api.post).toHaveBeenCalledTimes(1); expect(query("setup-intro-card").disabled).toBe(true);
  await act(async () => finish({ data: { url: "https://checkout.stripe.com/c/pay/fictional", selected_plan: plans[0] } }));
  expect(navigate).toHaveBeenCalledTimes(1);
});

test("missing security configuration and failed fresh eligibility block setup", async () => {
  api.get.mockResolvedValue({ data: { ...eligible, turnstile_site_key: null, challenge: {} } });
  await mount(); await check();
  expect(query("setup-intro-card").disabled).toBe(true);
  expect(host.textContent).toContain("security check is temporarily unavailable");
  api.get.mockRejectedValue(new Error("Unavailable"));
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(query("setup-intro-card")).toBeNull(); expect(api.post).not.toHaveBeenCalled();
  expect(host.textContent).toContain("couldn’t refresh your current eligibility or billing status");
});

test("a lost setup response becomes uncertain; explicit recovery reads status without replaying setup", async () => {
  await mount(); await check(); await solve();
  api.post.mockRejectedValue(new Error("Connection interrupted"));
  await click(query("setup-intro-card"));
  expect(api.post).toHaveBeenCalledTimes(1); expect(query("setup-intro-card")).toBeNull();
  expect(host.textContent).toContain("will not automatically repeat it");
  api.get.mockResolvedValue({ data: { ...eligible, can_claim: false, billing: { state: "checkout_pending", revision: 1, can_cancel: true } } });
  await click(query("refresh-intro-status"));
  expect(api.post).toHaveBeenCalledTimes(1); expect(navigate).not.toHaveBeenCalled();
  expect(host.textContent).toContain("card setup is not confirmed yet"); expect(query("intro-card-balance")).toBeNull();
});

test("the return URL alone never grants messages; only a server-verified confirmation updates the free counter", async () => {
  let finish; api.post.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await mount({ sessionId: "cs_fictional_return" });
  expect(api.post).toHaveBeenCalledWith("/me/intro-access/confirm", { expected_member: "fictional-owner", session_id: "cs_fictional_return" });
  expect(query("intro-card-balance")).toBeNull(); expect(onClaimed).not.toHaveBeenCalled();
  expect(host.textContent).toContain("Verifying your returned card setup");
  await act(async () => finish({ data: activeInfo({ messages_remaining: 10, messages_used: 0 }) }));
  expect(query("intro-card-balance").textContent).toBe("Free messages: 10 of 10 remaining");
  expect(query("intro-selected-plan").textContent).toContain("Plus 250 · $20.00/month");
  expect(onClaimed).toHaveBeenCalledTimes(1); expect(navigate).not.toHaveBeenCalled();
});

test("an uncertain card confirmation is not automatically posted again when its page rerenders", async () => {
  api.post.mockRejectedValue(new Error("Confirmation interrupted"));
  await mount({ sessionId: "cs_fictional_return" });
  await mount({ sessionId: "cs_fictional_return" });
  expect(api.post).toHaveBeenCalledTimes(1); expect(query("intro-card-balance")).toBeNull();
  api.get.mockResolvedValue({ data: activeInfo() }); await click(query("refresh-intro-status"));
  expect(api.post).toHaveBeenCalledTimes(1); expect(query("intro-card-balance").textContent).toContain("7 of 10");
});

test("active intro shows the chosen price and permits an explicit confirmed cancellation before all free messages are used", async () => {
  api.get.mockResolvedValue({ data: activeInfo() }); await mount();
  expect(query("intro-card-balance").textContent).toContain("7 of 10");
  expect(query("intro-selected-plan").textContent).toContain("250 paid messages per billing month");
  expect(host.querySelector('a[href="/app/start"]')).not.toBeNull();
  let finish; api.post.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await act(async () => { query("cancel-intro").click(); query("cancel-intro").click(); });
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post).toHaveBeenCalledWith("/me/intro-access/cancel", { expected_member: "fictional-owner", request_id: "00000000-0000-4000-8000-000000000001", revision: 2 });
  expect(query("intro-cancelled")).toBeNull(); expect(query("cancel-intro").disabled).toBe(true);
  await act(async () => finish({ data: activeInfo({ state: "cancelled", messages_remaining: 0, billing: { state: "cancelled", revision: 3, can_cancel: false } }) }));
  expect(query("intro-cancelled").textContent).toContain("No first subscription charge will be started");
  expect(query("cancel-intro")).toBeNull(); expect(host.querySelector('a[href="/app/start"]')).toBeNull();
  expect(onClaimed).toHaveBeenCalledTimes(1);
});

test("a failed or ambiguous cancellation cannot claim success or automatically replay the cancellation", async () => {
  api.get.mockResolvedValue({ data: activeInfo() }); await mount();
  api.post.mockRejectedValue(new Error("Status unavailable")); await click(query("cancel-intro"));
  expect(query("intro-cancelled")).toBeNull(); expect(query("cancel-intro")).toBeNull();
  expect(host.querySelector('a[href="/app/start"]')).toBeNull();
  await click(query("refresh-intro-status")); expect(api.post).toHaveBeenCalledTimes(1);
  expect(query("cancel-intro")).not.toBeNull();
  await click(query("cancel-intro"));
  expect(api.post.mock.calls[1]).toEqual(api.post.mock.calls[0]);
});

test.each(["billing_pending", "requires_action", "payment_failed", "review_required"])("%s never presents paid credits or a second checkout", async state => {
  api.get.mockResolvedValue({ data: activeInfo({ state: "exhausted", messages_remaining: 0, messages_used: 10,
    billing: { state, revision: 3, can_cancel: false, action_url: "https://invoice.stripe.com/i/fictional" } }) });
  await mount(); expect(query("intro-card-balance").textContent).toContain("0 of 10");
  expect(host.querySelector('a[href="/app/start"]')).toBeNull(); expect(query("setup-intro-card")).toBeNull();
  expect(api.post).not.toHaveBeenCalled();
  if (["requires_action", "payment_failed"].includes(state)) expect(query("intro-payment-action").href).toBe("https://invoice.stripe.com/i/fictional");
});

test.each(["javascript:alert(1)", "https://stripe.com.evil.test/pay", "http://checkout.stripe.com/pay", "https://user@checkout.stripe.com/pay", "/relative", "https://example.test/pay"])("untrusted provider URL %s cannot redirect or receive a payment-action link", async url => {
  expect(introProviderUrl(url)).toBeNull();
  api.get.mockResolvedValue({ data: activeInfo({ billing: { state: "requires_action", revision: 3, can_cancel: false, action_url: url } }) });
  await mount(); expect(query("intro-payment-action")).toBeNull(); expect(navigate).not.toHaveBeenCalled();
});

test("an invalid setup URL remains unconfirmed and does not redirect or grant messages", async () => {
  await mount(); await check(); await solve();
  api.post.mockResolvedValue({ data: { url: "https://checkout.stripe.com.evil.test/pay" } });
  await click(query("setup-intro-card")); expect(navigate).not.toHaveBeenCalled();
  expect(query("intro-card-balance")).toBeNull(); expect(host.textContent).toContain("couldn’t verify the secure card-setup link");
});

test("an old card-free API response cannot offer a card-first setup or claim free credits", async () => {
  api.get.mockResolvedValue({ data: { ...eligible, requires_payment_method: false, automatic_charge: false, billing: undefined } });
  await mount(); expect(query("setup-intro-card")).toBeNull(); expect(query("claim-intro-messages")).toBeNull();
  expect(host.textContent).toContain("setup is currently unavailable"); expect(api.post).not.toHaveBeenCalled();
});

test("late setup responses cannot navigate, read old results or update the next signed-in account", async () => {
  await mount(); await check(); await solve();
  let finish; api.post.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await click(query("setup-intro-card"));
  const nextApi = { get: jest.fn(async () => ({ data: { ...eligible, can_claim: false, state: "ineligible" } })), post: jest.fn() };
  await mount({ owner: "next-owner", api: nextApi });
  const getter = jest.fn(() => "https://checkout.stripe.com/c/pay/old-owner");
  await act(async () => finish({ data: Object.defineProperty({}, "url", { get: getter }) }));
  expect(getter).not.toHaveBeenCalled(); expect(navigate).not.toHaveBeenCalled(); expect(onClaimed).not.toHaveBeenCalled();
  expect(host.textContent).toContain("not eligible"); expect(nextApi.post).not.toHaveBeenCalled();
});


test("turning new introductory grants off does not hide an existing cardholder's cancellation", async () => {
  api.get.mockResolvedValue({ data: activeInfo({ enabled: false }) });
  await mount();
  expect(query("cancel-intro")).not.toBeNull();
  expect(query("setup-intro-card")).toBeNull();
});

test("a remounted return screen reads status instead of replaying a confirmation whose outcome was lost", async () => {
  api.post.mockRejectedValue(new Error("Lost response"));
  await mount({ sessionId: "cs_once_only" });
  await act(async () => root.render(null));
  api.get.mockResolvedValue({ data: activeInfo() });
  await mount({ sessionId: "cs_once_only" });
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(query("intro-card-balance").textContent).toContain("7 of 10");
});


test("fresh server authorization text is rendered verbatim and changing it clears an earlier choice", async () => {
  await mount(); await check(); await solve();
  const canonical = "Server quote: I authorize exactly $12 after the remaining ten completed messages. Then monthly; cancel beforehand. <em>plain text</em>";
  api.get.mockResolvedValue({ data: { ...eligible, quotes: { ...quotes, plus_100: { ...quotes.plus_100,
    authorization_text: canonical, authorization_text_sha256: "e".repeat(64), quote_sha256: "f".repeat(64) } } } });
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(query("intro-billing-authorize").checked).toBe(false);
  expect(query("intro-authorization-copy").textContent).toBe(canonical);
  expect(query("intro-authorization-copy").querySelector("em")).toBeNull();
  expect(query("setup-intro-card").disabled).toBe(true);
});

test.each([undefined, {}, { ...quotes, plus_100: { ...quotes.plus_100, quote_sha256: "invalid" } },
  { ...quotes, plus_100: { ...quotes.plus_100, remaining_intro_messages: 0 } }])("missing or inconsistent server agreement quote cannot be authorized", async supplied => {
  api.get.mockResolvedValue({ data: { ...eligible, quotes: supplied } });
  await mount();
  expect(query("intro-billing-authorize")).toBeNull();
  expect(query("setup-intro-card").disabled).toBe(true);
  expect(api.post).not.toHaveBeenCalled();
});


test("an existing partial introduction keeps its used count and authorizes only the remaining messages", async () => {
  const previousQuotes = Object.fromEntries(Object.entries(quotes).map(([id, quote]) => [id, { ...quote,
    remaining_intro_messages: 7, authorization_text: "I authorize this selected plan after my remaining seven messages, ten total. Cancel beforehand.",
    authorization_text_sha256: "e".repeat(64), quote_sha256: "f".repeat(64) }]));
  api.get.mockResolvedValue({ data: { ...eligible, messages_used: 3, quotes: previousQuotes,
    billing: { ...eligible.billing, legacy_intro: true } } });
  await mount();
  expect(query("intro-existing-usage").textContent).toContain("already used 3 of your 10 free messages. 7 remain");
  expect(query("setup-intro-card").textContent).toBe("Verify card and continue 7 free messages");
  expect(query("intro-authorization-copy").textContent).toBe(previousQuotes.plus_100.authorization_text);
  expect(api.post).not.toHaveBeenCalled();
});

const requiredInfo = () => ({ ...eligible, billing_required: true, can_setup: true });
const deniedInfo = () => ({ ...eligible, billing_required: false, can_claim: false, can_setup: false, state: "ineligible" });

test("a permanent setup denial waits for saved status, then refreshes the real Plans view with fresh unchecked paid consent", async () => {
  let denied = false;
  const refreshUsage = jest.fn(async () => {});
  const membership = () => ({ usage: {}, billing_enabled: true, existing_subscription: false, complimentary: false,
    checkout_available: denied, intro_billing_required: !denied, intro_access: denied ? deniedInfo() : requiredInfo(),
    plans, partner_offer: {}, topup: { messages: 100, price_cents: 1200 } });
  api.get.mockImplementation(async path => ({ data: path === "/upgrade" ? membership() : denied ? deniedInfo() : requiredInfo() }));
  api.post.mockImplementation(async () => { denied = true; throw Object.assign(new Error("Refresh Plans to purchase a regular membership"), { response: { status: 403 } }); });
  useOutletContext.mockReturnValue({ api, me: { user_id: "fictional-owner" }, refreshUsage });
  await act(async () => root.render(<Upgrade />));
  expect(query("purchase-policy-accept")).toBeNull();
  await check(); await solve(); await click(query("setup-intro-card"));
  expect(refreshUsage).not.toHaveBeenCalled();
  expect(api.get.mock.calls.filter(([path]) => path === "/upgrade")).toHaveLength(1);
  expect(query("purchase-policy-accept")).toBeNull();
  await click(query("refresh-intro-status"));
  expect(refreshUsage).toHaveBeenCalledTimes(1);
  expect(api.get.mock.calls.filter(([path]) => path === "/upgrade")).toHaveLength(2);
  expect(query("purchase-policy-accept").checked).toBe(false);
  expect(query("choose-plus_100").disabled).toBe(true);
  expect(host.textContent).toContain("not eligible to start the introduction");
  await click(query("purchase-policy-accept"));
  expect(query("choose-plus_100").disabled).toBe(false);
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post.mock.calls[0][0]).toBe("/me/intro-access/setup");
});

test("saved-status eligibility recovery notifies the owning parent once despite overlapping reads", async () => {
  api.get.mockResolvedValue({ data: requiredInfo() }); await mount({ initial: requiredInfo() });
  let finish;
  onClaimed.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  api.get.mockResolvedValue({ data: deniedInfo() });
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(onClaimed).toHaveBeenCalledTimes(1);
  await act(async () => finish());
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(onClaimed).toHaveBeenCalledTimes(1); expect(api.post).not.toHaveBeenCalled();
});

test("an unsuccessful parent refresh can be retried only after another explicit saved-status read", async () => {
  onClaimed.mockRejectedValueOnce(new Error("Owning plan refresh unavailable"));
  api.get.mockResolvedValue({ data: deniedInfo() }); await mount({ initial: requiredInfo() });
  expect(onClaimed).toHaveBeenCalledTimes(1); expect(api.get).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain("couldn’t refresh your current eligibility");
  onClaimed.mockResolvedValue(undefined);
  await click(query("refresh-intro-status"));
  expect(onClaimed).toHaveBeenCalledTimes(2); expect(api.get).toHaveBeenCalledTimes(2);
  expect(api.post).not.toHaveBeenCalled();
});

test.each([
  { ...deniedInfo(), authorization_version: "unrecognized" },
  { ...deniedInfo(), billing_required: 0 },
  { ...deniedInfo(), billing: { state: "unrecognized" } },
])("an unverified saved snapshot cannot open the owning paid plan view", async data => {
  api.get.mockResolvedValue({ data }); await mount({ initial: requiredInfo() });
  expect(onClaimed).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
});

test("a late saved-status response from the previous account cannot refresh the new owner's plan", async () => {
  let finish;
  api.get.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await mount({ initial: requiredInfo() });
  const nextApi = { get: jest.fn(async () => ({ data: deniedInfo() })), post: jest.fn() };
  await mount({ owner: "next-owner", api: nextApi, initial: deniedInfo() });
  const readOldStatus = jest.fn(() => false);
  await act(async () => finish({ data: Object.defineProperty({}, "billing_required", { get: readOldStatus }) }));
  expect(readOldStatus).not.toHaveBeenCalled(); expect(onClaimed).not.toHaveBeenCalled();
  expect(api.post).not.toHaveBeenCalled(); expect(nextApi.post).not.toHaveBeenCalled();
});
