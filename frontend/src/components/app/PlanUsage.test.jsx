import { act } from "react";
import { createRoot } from "react-dom/client";
import { useOutletContext } from "react-router-dom";
import { currentPlanName, isComplimentary, isIntroductory, LimitNotice, messageLimit, personLimit } from "./PlanUsage";

jest.mock("react-router-dom", () => ({ Link: ({ children, to }) => <a href={to}>{children}</a>, useOutletContext: jest.fn() }));

const complimentary = { plan: "complimentary", plan_id: "complimentary_unlimited", unlimited: true, messages_used: 9 };

test("explicit complimentary unlimited is named separately from staff and a paid plan", () => {
  expect(isComplimentary(complimentary)).toBe(true);
  expect(currentPlanName(complimentary, null)).toBe("Complimentary Unlimited");
  expect(currentPlanName(complimentary, "plus_250")).toBe("Complimentary Unlimited");
  expect(messageLimit(complimentary)).toBeNull();
  expect(personLimit(complimentary)).toBeNull();
  expect(currentPlanName({ plan: "admin", unlimited: true }, null)).toBe("Administrator");
  expect(currentPlanName({ plan: "admin", unlimited: true }, "plus_250")).toBe("Plus 250 · Administrator");
  expect(currentPlanName({ unlimited: true }, null)).toBeNull();
});

test.each([
  { ...complimentary, unlimited: false },
  { ...complimentary, unlimited: "true" },
  { ...complimentary, plan: "unsubscribed" },
  { ...complimentary, plan_id: null },
])("partial or unrelated usage does not become complimentary: %j", usage => {
  expect(isComplimentary(usage)).toBe(false);
});

test("ordinary subscription and unpaid limits retain their existing meaning", () => {
  expect(currentPlanName({ plan: "plus", period_verified: true, plan_name: "Plus 100" })).toBe("Plus 100");
  expect(currentPlanName({ plan: "plus", period_verified: false })).toBeNull();
  expect(currentPlanName({ plan: "unsubscribed" })).toBeNull();
  expect(messageLimit({ plan: "unsubscribed" })).toBe("subscription");
  expect(personLimit({ plan: "unsubscribed" })).toBe("subscription");
  expect(messageLimit({ plan: "plus", period_verified: true, messages_remaining: 0 })).toBe("monthly");
});

test("refund adjustments explain an empty allowance while retaining legitimate refill access", () => {
  const usage={plan:'plus',period_verified:true,allowance_adjusted:true,messages_remaining:0,message_limit:0,plan_name:'Plus 100'};
  expect(messageLimit(usage)).toBe('adjusted');
  expect(currentPlanName(usage)).toBe('Plus 100');
  expect(messageLimit({...usage,messages_remaining:17,topup_remaining:17})).toBeNull();
  expect(personLimit(usage)).toBeNull();
});

test("a pending payment hold is separate from used messages or a permanent refund adjustment", () => {
  const held = { plan: "plus", period_verified: true, messages_remaining: 0, allowance_adjusted: true,
    payment_dispute_hold: true, held_message_count: 23 };
  expect(messageLimit(held)).toBe("payment-review");
  expect(messageLimit({ ...held, messages_remaining: 4 })).toBeNull();
  expect(messageLimit({ ...held, payment_dispute_hold: false })).toBe("adjusted");
  expect(messageLimit({ ...held, payment_dispute_hold: "true" })).toBe("adjusted");
  expect(messageLimit({ ...held, unlimited: true })).toBeNull();
});

test.each(["payment-review", "monthly"])("paired %s notices preserve cancellation/data access and keep navigation in Couples", async reason => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.body.appendChild(document.createElement("div")), root = createRoot(host);
  useOutletContext.mockReturnValue({ me: { usage: {}, workspace_access: { version: "workspace-access-v1",
    mode: "couples", couples_only: true, pair_id: "pair" } } });
  try {
    await act(async () => root.render(<LimitNotice reason={reason} />));
    expect(host.querySelector('a[href="/app/people"]')).toBeNull();
    if (reason === "payment-review") {
      expect(host.textContent).toContain("temporarily unavailable while a payment is reviewed");
      expect(host.textContent).not.toMatch(/refund|lost dispute|top-up|View plans/);
      expect(host.querySelector('a[href="/app/settings/upgrade"]').textContent).toBe("Billing & cancellation");
      expect(host.querySelector('a[href="/app/settings"]')).not.toBeNull();
      expect(host.querySelector('a[href="/support?topic=Billing"]')).not.toBeNull();
    } else expect(host.querySelector('a[href="/app/couples"]').textContent).toBe("Couples");
  } finally { act(() => root.unmount()); host.remove(); }
});

test("the introductory pool has its own identity and ends without becoming a billing month", () => {
  const intro = { plan: "intro", plan_id: "intro_10", introductory: true, unlimited: false,
    message_limit: 10, messages_remaining: 7, messages_used: 3, period_verified: false };
  expect(isIntroductory(intro)).toBe(true);
  expect(currentPlanName(intro, "plus_500")).toBe("10 introductory messages");
  expect(messageLimit(intro)).toBeNull();
  expect(personLimit(intro)).toBeNull();
  expect(messageLimit({ ...intro, messages_remaining: 0 })).toBe("intro");
  expect(personLimit({ ...intro, messages_remaining: 0 })).toBe("intro");
  expect(isIntroductory({ ...intro, introductory: false })).toBe(false);
  expect(isIntroductory({ ...intro, message_limit: 100 })).toBe(false);
  expect(isIntroductory({ ...intro, unlimited: true })).toBe(false);
  expect(messageLimit({ ...intro, plan: "plus", period_verified: true, messages_remaining: 100 })).toBeNull();
});
