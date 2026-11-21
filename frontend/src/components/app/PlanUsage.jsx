import { useRef } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { couplesOnly } from "@/lib/workspaceAccess";

export function isComplimentary(usage) {
  return usage?.plan === "complimentary" && usage.plan_id === "complimentary_unlimited" && usage.unlimited === true;
}

export function isIntroductory(usage) {
  return usage?.plan === "intro" && usage.plan_id === "intro_10"
    && usage.introductory === true && usage.message_limit === 10 && usage.unlimited === false;
}

export function messageLimit(usage) {
  if (!usage || usage.unlimited) return null;
  if (usage.messages_remaining === 0 && usage.payment_dispute_hold === true) return "payment-review";
  if (isIntroductory(usage)) return usage.messages_remaining > 0 ? null : "intro";
  if (usage.plan === "plus" && !usage.period_verified) return "unverified";
  if (usage.plan !== "plus") return "subscription";
  if (usage.messages_remaining === 0) return usage.allowance_adjusted ? "adjusted" : "monthly";
  return null;
}

export function personLimit(usage) {
  if (!usage || usage.unlimited) return null;
  if (isIntroductory(usage)) return usage.messages_remaining > 0 ? null : "intro";
  return usage.plan === "plus" && usage.period_verified ? null : "subscription";
}

export function periodEnd(usage) {
  if (!usage?.period_end) return "";
  return new Date(usage.period_end * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function currentPlanName(usage, membershipPlanId) {
  if (isComplimentary(usage)) return "Complimentary Unlimited";
  if (isIntroductory(usage)) return "10 introductory messages";
  if (usage?.plan === "admin" && usage.unlimited === true) {
    const paid = /^plus_(100|250|500)$/.exec(membershipPlanId || "");
    return paid ? `Plus ${paid[1]} · Administrator` : "Administrator";
  }
  if (usage?.plan !== "plus" || !usage.period_verified) return null;
  return usage.plan_name || (usage.message_limit ? `Plus ${usage.message_limit}` : "Plus");
}

export function LimitNotice({ reason }) {
  const { me } = useOutletContext();
  const end = periodEnd(me.usage);
  const paired = couplesOnly(me);
  const paymentReview = reason === "payment-review";
  const descriptions = {
    intro: "Your introductory messages are finished. Open Plans to check your selected plan and payment status. Your paid allowance is available after payment is verified. Your saved content is retained; you can export it from Settings.",
    subscription: paired ? "A membership funds your Couples AI replies." : "Choose a plan to use Individual.",
    monthly: `You've used your available messages.${end ? ` Your monthly allowance resets ${end}.` : ""} You can still read your history and record outcomes.`,
    adjusted: "Your allowance was adjusted after a refund or lost dispute. Open Plans to review the balance. Your saved history and outcomes remain available.",
    unverified: "Your Plus billing period is awaiting confirmation. Your saved history is available.",
    "payment-review": "Some unused messages are temporarily unavailable while a payment is reviewed. They have not been counted as used or permanently removed. Your saved content and cancellation options remain available.",
  };
  return (
    <div className="rounded-xl border border-[var(--line)] p-3 text-sm" data-testid={`plan-limit-${reason}`}>
      <p>{descriptions[reason]}</p>
      <div className="flex flex-wrap gap-4 mt-2 font-semibold">
        <Link to={reason === "intro" || paymentReview ? "/app/settings" : paired ? "/app/couples" : "/app/people"} className="underline">{reason === "intro" || paymentReview ? "Settings and export" : paired ? "Couples" : "Your people"}</Link>
        <Link to={reason === "monthly" || reason === "adjusted" ? "/app/settings/upgrade#top-up" : "/app/settings/upgrade"} className="underline">{paymentReview ? "Billing & cancellation" : reason === "monthly" || reason === "adjusted" ? "View plans and top-up" : "View plans"}</Link>
        {paymentReview && <Link to="/support?topic=Billing" className="underline">Billing support</Link>}
      </div>
    </div>
  );
}

// A retry of an uncertain send should retrieve the original reply and spend only once.
export function useMessageSend() {
  const { api, refreshUsage } = useOutletContext();
  const pending = useRef(null);
  return async (url, body, headers = {}) => {
    const signature = JSON.stringify([url, body]);
    if (pending.current?.signature !== signature) {
      pending.current = { signature, key: crypto.randomUUID() };
    }
    const request = pending.current;
    try {
      const result = await api.post(url, body, { headers: { ...headers, "Idempotency-Key": headers["Idempotency-Key"] || request.key } });
      if (pending.current === request) pending.current = null;
      refreshUsage();
      return result;
    } catch (error) {
      const status = error.response?.status;
      if (pending.current === request && status >= 400 && status < 500 && status !== 409) pending.current = null;
      throw error;
    }
  };
}
