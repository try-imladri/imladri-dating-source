import { act } from "react";
import { createRoot } from "react-dom/client";
import MembersAdmin from "./MembersAdmin";

jest.mock("@/lib/api", () => ({ errMsg: e => e.message }));
jest.mock("sonner", () => ({ toast: { success: jest.fn() } }));
jest.mock("react-router-dom", () => ({ Link: () => null, useOutletContext: jest.fn() }));

const summary = { total_users: 314, monthly_revenue_cents: 2450, month_start: "2026-11-01", environment: "preview" };
const paid = { user_id: "paid", nickname: "Customer", email: "customer@example.test", role: "member",
  membership_status: "active", plan_offer: "partner_monthly", usage: { plan: "plus", period_verified: true, message_limit: 100, messages_used: 73, period_end: 1796083200 } };
const staff = { user_id: "staff", email: "staff@example.test", role: "admin", membership_status: "staff", usage: { unlimited: true } };
const former = { user_id: "former", email: "former@example.test", role: "member", membership_status: "expired", usage: { plan: "unsubscribed", message_limit: 0 } };
const result = (users, next_cursor = null) => ({ data: { users, roles: ["admin", "member"], next_cursor } });
const click = el => act(async () => el.click());
const choose = (select, value) => act(async () => { select.value = value; select.dispatchEvent(new Event("change", { bubbles: true })); });
let root, host;
const byTestId = id => document.querySelector(`[data-testid="${id}"]`);
const tick = () => act(async () => { jest.advanceTimersByTime(200); });
const mount = async (api, otherProps) => {
  await act(async () => root.render(<MembersAdmin api={api} me={staff} settings={<p>Plan editor</p>} {...otherProps} />));
  await tick();
};

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.useFakeTimers();
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  jest.useRealTimers();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});

test("shows account-wide totals above paged, filtered current-period usage", async () => {
  const api = { get: jest.fn(async (path, config) => {
    if (path.includes("summary")) return { data: summary };
    if (config.params.status === "expired") return result([former]);
    if (config.params.after) return result([staff]);
    return result([paid], "paid");
  }) };
  await mount(api);
  expect(byTestId("members-total-users").textContent).toContain("314");
  expect(byTestId("members-monthly-revenue").textContent).toContain("$24.50");
  expect(byTestId("members-monthly-revenue").textContent).toContain("Sandbox · Nov 2026 UTC");
  expect(host.querySelector("progress").value).toBe(73);
  expect(host.querySelector("progress").max).toBe(100);
  expect(host.textContent).toContain("$10/month · invited partner");
  await click([...host.querySelectorAll("button")].find(el => el.textContent === "Next"));
  await tick();
  expect(host.querySelector("tbody").textContent).toContain("Unlimited");
  await choose(host.querySelector("select"), "expired");
  await tick();
  expect(api.get.mock.calls.at(-1)[1].params).toEqual({ q: "", status: "expired", after: "", limit: 50 });
  expect(host.querySelector("tbody").textContent).toContain("No current allowance");
  expect(api.get.mock.calls.filter(([url]) => url === "/admin/membership/summary")).toHaveLength(1);
  await click([...host.querySelectorAll("button")].find(el => el.textContent === "Plan settings"));
  expect(document.querySelector('[role="dialog"]').textContent).toContain("Plan editor");
});

test("changing access requires a named action, preserves the row on failure, and refreshes success", async () => {
  const customer = { ...paid };
  const api = {
    get: jest.fn(async path => path.includes("summary") ? { data: summary } : result([customer])),
    patch: jest.fn().mockRejectedValueOnce(new Error("Permission changed")).mockImplementationOnce(async () => { customer.replies_paused = true; }),
  };
  await mount(api);
  await click(byTestId("admin-manage-paid"));
  const dialog = () => document.querySelector('[role="dialog"]');
  expect(dialog().querySelector('#member-action option[value="couples"]')).toBeNull();
  await choose(dialog().querySelector("#member-action"), "replies");
  expect(dialog().textContent).toContain("subscription billing continues");
  expect(api.patch).not.toHaveBeenCalled();
  await click(dialog().querySelector('#member-action-form [type="submit"]'));
  expect(dialog().querySelector('[role="alert"]').textContent).toContain("Permission changed");
  await click(dialog().querySelector('#member-action-form [type="submit"]'));
  await tick();
  expect(api.patch).toHaveBeenLastCalledWith("/users/paid/reply-access", { paused: true });
  expect(host.querySelector("tbody").textContent).toContain("Replies paused");
  await click(byTestId("admin-manage-paid"));
  expect(dialog().textContent).toContain("Resume new replies");
});

test("handles self-demotion and preserves the last-admin error from the server", async () => {
  const onOwnRoleChange = jest.fn();
  const api = {
    get: jest.fn(async path => path.includes("summary") ? { data: summary } : result([staff])),
    patch: jest.fn().mockRejectedValueOnce(new Error("Keep at least one admin")).mockResolvedValueOnce({ data: { role: "member" } }),
  };
  await mount(api, { onOwnRoleChange });
  await click(byTestId("admin-manage-staff"));
  const dialog = document.querySelector('[role="dialog"]');
  expect(dialog.querySelector('option[value="replies"]')).toBeNull();
  expect(dialog.querySelector('#member-action option[value="couples"]')).toBeNull();
  await choose(dialog.querySelector("#member-action"), "role-member");
  await click(dialog.querySelector('#member-action-form [type="submit"]'));
  expect(dialog.querySelector('[role="alert"]').textContent).toBe("Keep at least one admin");
  expect(onOwnRoleChange).not.toHaveBeenCalled();
  await click(dialog.querySelector('#member-action-form [type="submit"]'));
  expect(api.patch).toHaveBeenLastCalledWith("/users/staff/role", { role: "member" });
  expect(onOwnRoleChange).toHaveBeenCalledWith("member");
});

test("staff history loads for the opened member and distinguishes recorded changes", async () => {
  const api = { get: jest.fn(async path => {
    if (path.endsWith("/actions")) return {data:{limit:50, actions:[{action:"couples_enrolled", actor:"staff-account", at:"2026-11-08T12:00:00Z"}]}};
    return path.includes("summary") ? {data:summary} : result([paid]);
  }) };
  await mount(api);
  await click(byTestId("admin-manage-paid"));
  const dialog = document.querySelector('[role="dialog"]');
  expect(api.get.mock.calls.some(([path]) => path.endsWith("/actions"))).toBe(false);
  await click([...dialog.querySelectorAll("button")].find(el => el.textContent === "Staff action history"));
  expect(api.get.mock.calls.at(-1)[0]).toBe("/users/paid/actions");
  expect(dialog.querySelector("ol").textContent).toContain("Enabled Couples invitations during early access");
  expect(dialog.querySelector("ol").textContent).toContain("staff-account");
});

test("complimentary usage remains an ordinary member status with actionable reply pause and no paid price", async () => {
  const member = { user_id: "complimentary-owner", role: "member", email: "fictional@example.test",
    membership_status: "complimentary", replies_paused: true, plan_offer: "partner_monthly",
    usage: { plan: "complimentary", plan_id: "complimentary_unlimited", plan_name: "Complimentary Unlimited",
      unlimited: true, messages_used: 17 } };
  const api = {
    get: jest.fn(async path => path.includes("summary") ? { data: summary } : result([member])),
    patch: jest.fn(async () => { member.replies_paused = false; return { data: member }; }),
  };
  await mount(api);
  const row = host.querySelector("tbody");
  expect(row.textContent).toContain("Complimentary Unlimited");
  expect(row.textContent).toContain("17 complimentary messages used · all time");
  expect(row.textContent).toContain("Replies paused");
  expect(row.textContent).not.toContain("$10/month");
  expect(row.textContent).not.toContain("Staff access");
  await choose(host.querySelector("select"), "complimentary"); await tick();
  expect(api.get.mock.calls.at(-1)[1].params.status).toBe("complimentary");
  await click(byTestId("admin-manage-complimentary-owner"));
  const dialog = document.querySelector('[role="dialog"]');
  await choose(dialog.querySelector("#member-action"), "replies");
  expect(dialog.textContent).toContain("complimentary unlimited access");
  expect(dialog.textContent).not.toContain("subscription billing continues");
  await click(dialog.querySelector('#member-action-form [type="submit"]'));
  expect(api.patch).toHaveBeenCalledWith("/users/complimentary-owner/reply-access", { paused: false });
  expect(member.role).toBe("member");
});

const explain = (dialog, text) => act(async () => {
  const input = dialog.querySelector("#complimentary-reason");
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, text);
  input.dispatchEvent(new Event("input", { bubbles: true }));
});

test("a named complimentary grant requires a reason and retains its exact UUID/body after an uncertain acknowledgment", async () => {
  const request = "33333333-3333-4333-8333-333333333333";
  Object.defineProperty(window, "crypto", { configurable: true, value: { randomUUID: jest.fn(() => request) } });
  const member = { ...former, membership_status: "unsubscribed" };
  const api = { get: jest.fn(async path => path.includes("summary") ? { data: summary } : result([member])),
    patch: jest.fn().mockRejectedValueOnce(new Error("Acknowledgment unavailable")).mockResolvedValueOnce({ data: {} }) };
  await mount(api);
  await click(byTestId("admin-manage-former"));
  const dialog = document.querySelector('[role="dialog"]');
  await choose(dialog.querySelector("#member-action"), "complimentary");
  expect(dialog.querySelector('#member-action-form [type="submit"]').disabled).toBe(true);
  await explain(dialog, "Owner approved complimentary access");
  await click(dialog.querySelector('#member-action-form [type="submit"]'));
  expect(api.patch).toHaveBeenCalledTimes(1);
  expect(dialog.querySelector('[role="alert"]').textContent).toContain("Acknowledgment unavailable");
  await click(dialog.querySelector('#member-action-form [type="submit"]'));
  const body = { expected_member: "former", request_id: request, enabled: true, expected_entitlement_id: null,
    reason: "Owner approved complimentary access" };
  expect(api.patch.mock.calls).toEqual([["/users/former/complimentary-access", body], ["/users/former/complimentary-access", body]]);
  expect(window.crypto.randomUUID).toHaveBeenCalledTimes(1);
  expect(member.role).toBe("member");
});

test("revocation binds the exact displayed entitlement and never changes the member role", async () => {
  const entitlement = "11111111-1111-4111-8111-111111111111";
  Object.defineProperty(window, "crypto", { configurable: true, value: { randomUUID: () => "44444444-4444-4444-8444-444444444444" } });
  const member = { ...former, membership_status: "complimentary",
    usage: { plan: "complimentary", plan_id: "complimentary_unlimited", unlimited: true },
    complimentary_entitlement: { version: "complimentary-unlimited-v1", id: entitlement, active: true } };
  const api = { get: jest.fn(async path => path.includes("summary") ? { data: summary } : result([member])), patch: jest.fn().mockResolvedValue({ data: {} }) };
  await mount(api); await click(byTestId("admin-manage-former"));
  const dialog = document.querySelector('[role="dialog"]');
  await choose(dialog.querySelector("#member-action"), "complimentary");
  expect(dialog.textContent).toContain("Revoke complimentary unlimited");
  await explain(dialog, "Grant no longer required"); await click(dialog.querySelector('#member-action-form [type="submit"]'));
  expect(api.patch).toHaveBeenCalledWith("/users/former/complimentary-access", { expected_member: "former",
    request_id: "44444444-4444-4444-8444-444444444444", enabled: false, expected_entitlement_id: entitlement, reason: "Grant no longer required" });
  expect(member.role).toBe("member");
});

test("missing entitlement identity never offers an unbound complimentary revocation", async () => {
  const member = { ...former, membership_status: "complimentary",
    usage: { plan: "complimentary", plan_id: "complimentary_unlimited", unlimited: true } };
  const api = { get: jest.fn(async path => path.includes("summary") ? { data: summary } : result([member])), patch: jest.fn() };
  await mount(api); await click(byTestId("admin-manage-former"));
  expect(document.querySelector('[role="dialog"] option[value="complimentary"]')).toBeNull();
  expect(api.patch).not.toHaveBeenCalled();
});

test("a fully adjusted monthly allowance still shows independently purchased refills", async () => {
  const member={...paid,usage:{plan:'plus',plan_id:'plus_100',period_verified:true,message_limit:0,
    allowance_adjusted:true,original_message_limit:100,topup_remaining:17,monthly_messages_used:40}};
  const api={get:jest.fn(async path=>path.includes('summary')?{data:summary}:result([member]))};
  await mount(api);
  expect(host.querySelector('tbody').textContent).toContain('No monthly messages available');
  expect(host.querySelector('tbody').textContent).toContain('17 refill replies left');
  expect(host.querySelector('tbody').textContent).toContain('40 monthly messages used');
  expect(host.querySelector('tbody').textContent).toContain('Original monthly allowance: 100');
  expect(host.querySelector('progress')).toBeNull();
});

test("pending payment holds show only affected unused messages and never label them refunded or used", async () => {
  const member = { ...paid, usage: { ...paid.usage, allowance_adjusted: true, original_message_limit: 100,
    payment_dispute_hold: true, held_message_count: 23 }, billing_review: { actionable: true, hold_scopes: [],
    pending_payment_disputes: 1, private_charge_id: "private-provider-id-must-not-render" } };
  const api = { get: jest.fn(async path => path.includes("summary") ? { data: summary } : result([member])) };
  await mount(api);
  const body = host.querySelector("tbody");
  expect(body.querySelector('[data-testid="member-payment-hold"]').textContent).toContain("23 unused messages are temporarily unavailable");
  expect(body.textContent).not.toContain("after a refund or lost dispute");
  expect(body.textContent).not.toContain("private-provider-id-must-not-render");
  expect(body.querySelector("progress").value).toBe(73);
});

test("unverified monthly net is visibly pending rather than a fabricated zero", async () => {
  const api={get:jest.fn(async path=>path.includes('summary')?{data:{...summary,monthly_revenue_cents:null}}:result([]))};
  await mount(api);
  expect(byTestId('members-monthly-revenue').textContent).toContain('Pending reconciliation');
  expect(byTestId('members-monthly-revenue').textContent).not.toContain('$0.00');
});


test("card-first members show their separate free balance, selected plan and billing state without inventing paid revenue", async () => {
  const intro = { user_id: "intro-owner", nickname: "Introductory member", email: "fictional@example.test", role: "member",
    membership_status: "intro", usage: { plan: "intro", plan_id: "intro_10", introductory: true, unlimited: false,
      message_limit: 10, messages_used: 3, messages_remaining: 7, period_verified: false },
    intro_billing: { state: "active", revision: 3, agreement_id: "private-agreement-must-not-render", card_identity: "private-fraud-key-must-not-render" },
    intro_selected_plan: { id: "plus_250", name: "Plus 250", price_cents: 2000, messages: 250 } };
  const api = { get: jest.fn(async path => path.includes("summary") ? { data: { ...summary, monthly_revenue_cents: 0 } } : result([intro])) };
  await mount(api);
  expect(host.querySelector("tbody").textContent).toContain("7 of 10 free messages remaining");
  expect(byTestId("member-intro-billing").textContent).toContain("Free messages active");
  expect(byTestId("member-intro-billing").textContent).toContain("Plus 250 · $20.00/month · 250 paid messages");
  expect(byTestId("members-monthly-revenue").textContent).toContain("$0.00");
  expect(host.textContent).not.toContain("private-agreement-must-not-render");
  expect(host.textContent).not.toContain("private-fraud-key-must-not-render");
});

test.each(["checkout_pending", "billing_pending", "requires_action", "payment_failed", "review_required", "cancelled"])("intro state %s is visible in admin without becoming an active paid allowance", async state => {
  const user = { ...former, intro_billing: { state }, intro_selected_plan: { id: "plus_100", name: "Plus 100", price_cents: 1200, messages: 100 } };
  const api = { get: jest.fn(async path => path.includes("summary") ? { data: summary } : result([user])) };
  await mount(api);
  const details = byTestId("member-intro-billing").textContent;
  expect(details).toContain("Intro billing:");
  expect(details).toContain("A paid conversion has not been recorded");
  expect(host.querySelector("tbody").textContent).toContain("No current allowance");
});
