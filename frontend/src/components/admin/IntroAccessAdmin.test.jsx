import { act } from "react";
import { createRoot } from "react-dom/client";
import IntroAccessAdmin from "./IntroAccessAdmin";

jest.mock("react-router-dom", () => ({ Link: ({ children, to }) => <a href={to}>{children}</a> }));
jest.mock("@/lib/api", () => ({ errMsg: error => error.message }));
let host, root, api;
const report = {
  enabled: true, settings: { paused: false }, retention_days: 365,
  totals: { granted_accounts: 17, active_accounts: 9, exhausted_accounts: 8, used_messages: 99, converted_accounts: 3, suspected_accounts: 2 },
  today: { day: "2026-11-14", tokens_reserved: 1500, token_limit: 100000, spend_reserved_nusd: 1000000, spend_limit_nusd: 1000000000, attempts: 8 },
  costs: { low_nusd: 500000, high_nusd: 1000000, attempts: 9, unknown: 1 },
};
const query = id => host.querySelector(`[data-testid="${id}"]`);
const mount = () => act(async () => root.render(<IntroAccessAdmin api={api} />));
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.body.appendChild(document.createElement("div")); root = createRoot(host);
  api = { get: jest.fn(async () => ({ data: report })), patch: jest.fn(async () => ({ data: { ...report, settings: { paused: true } } })) };
});
afterEach(() => { act(() => root.unmount()); host.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; });

test("shows the introductory cohort, risk flags, bounded spending and costs with their actual meanings", async () => {
  await mount();
  expect(query("intro-admin-granted_accounts").textContent).toBe("17");
  expect(query("intro-admin-converted_accounts").textContent).toBe("3");
  expect(query("intro-admin-suspected_accounts").textContent).toBe("2");
  expect(query("intro-admin-budget").textContent).toContain("Reserved spend: $0.001 / $1.00");
  expect(query("intro-admin-budget").textContent).toContain("Reserved tokens: 1,500 / 100,000");
  expect(query("intro-admin-costs").textContent).toContain("Calls with unknown cost: 1");
  expect(host.textContent).toContain("Estimates are not verified provider charges");
  expect(api.patch).not.toHaveBeenCalled();
});

test("flagged members have a scoped management link and shared-device signals are not described as proof", async () => {
  api.get.mockResolvedValue({ data: { ...report, review_accounts: [{ user_id: "fictional-owner+one", nickname: "Alex",
    reasons: ["Multiple introductions on a shared device"] }], review_accounts_more: true } });
  await mount();
  const review = host.querySelector('[aria-label="Introductions flagged for review"]');
  expect(review.textContent).toContain("People can legitimately share devices");
  expect(review.textContent).toContain("Showing the first 20");
  expect(review.querySelector("a").getAttribute("href")).toBe("/admin/users?q=fictional-owner%2Bone");
  expect(review.textContent).toContain("Alex");
});

test("an explicit pause applies once, waits for confirmation and leaves paid memberships alone", async () => {
  await mount();
  let finish;
  api.patch.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const button = query("intro-admin-pause");
  await act(async () => { button.click(); button.click(); });
  expect(api.patch).toHaveBeenCalledTimes(1);
  expect(api.patch).toHaveBeenCalledWith("/admin/intro-access/settings", { paused: true });
  expect(query("intro-admin-pause").disabled).toBe(true);
  expect(host.textContent).not.toContain("Paid memberships are unaffected");
  await act(async () => finish({ data: { ...report, settings: { paused: true } } }));
  expect(query("intro-admin-pause").textContent).toBe("Resume introductory access");
  expect(host.querySelector('[role="status"]').textContent).toContain("Paid memberships are unaffected");
});

test("a possibly successful pause is reconciled by GET and never automatically repeated", async () => {
  await mount();
  api.patch.mockRejectedValue(new Error("Connection interrupted"));
  api.get.mockResolvedValue({ data: { ...report, settings: { paused: true } } });
  await act(async () => query("intro-admin-pause").click());
  expect(api.patch).toHaveBeenCalledTimes(1);
  expect(api.get).toHaveBeenCalledTimes(2);
  expect(query("intro-admin-pause").textContent).toBe("Resume introductory access");
  expect(host.querySelector('[role="status"]').textContent).toContain("could not be confirmed");
});

test("an incomplete report stays visibly unknown instead of turning missing data into zero", async () => {
  api.get.mockResolvedValue({ data: { enabled: true, totals: {}, today: {}, costs: {} } });
  await mount();
  expect(query("intro-admin-granted_accounts").textContent).toBe("Unavailable");
  expect(query("intro-admin-budget").textContent).toContain("UTC day unavailable");
  expect(query("intro-admin-pause").disabled).toBe(true);
  expect(host.textContent).toContain("Pause status is unavailable");
});


test("card-first agreement states remain separate from paid conversions and revenue", async () => {
  api.get.mockResolvedValue({ data: { ...report, card_first: { authorization_version: "card-first-intro-v1",
    denominator: "retained_nonclosing_member_agreements", zero_invoices_grant_paid_messages: false,
    states: { checkout_pending: 4, active: 7, billing_pending: 2, requires_action: 1, payment_failed: 0,
      review_required: 3, cancelled: 6, converted: 2 } } } });
  await mount();
  expect(query("intro-admin-billing-checkout_pending").textContent).toBe("4");
  expect(query("intro-admin-billing-active").textContent).toBe("7");
  expect(query("intro-admin-billing-requires_action").textContent).toBe("1");
  expect(query("intro-admin-billing-payment_failed").textContent).toBe("0");
  expect(query("intro-admin-billing-converted").textContent).toBe("2");
  expect(query("intro-admin-billing-states").textContent).toContain("zero-dollar setup invoice is separate");
  expect(query("intro-admin-billing-states").textContent).not.toContain("private-agreement");
  expect(api.patch).not.toHaveBeenCalled();
});
