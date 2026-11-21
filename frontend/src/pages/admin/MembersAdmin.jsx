import { useEffect, useRef, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { errMsg } from "@/lib/api";
import Stat from "./Stat";
import MembershipReconciliation from "./MembershipReconciliation";
import { YourTypeMember } from "./YourTypeResearch";
import { isComplimentary, isIntroductory } from "@/components/app/PlanUsage";
import BillingRecords from "./BillingRecords";
import BillingReviews from "./BillingReviews";
import BillingRiskReviews from "./BillingRiskReviews";
import AnswerHistoryTimeline from "@/components/admin/AnswerHistoryTimeline";
import IntroAccessAdmin from "@/components/admin/IntroAccessAdmin";

const STATUS = {
  active: "Active Plus", past_due: "Past due", expired: "Expired", cancelled: "Canceled",
  unsubscribed: "No subscription", unverified: "Unverified", complimentary: "Complimentary Unlimited", staff: "Staff access", closing: "Closing",
  intro: "Introductory access", intro_exhausted: "Introduction completed",
};
const selectClass = "h-9 min-w-0 rounded-md border border-[var(--line)] bg-[var(--bg)] px-2 text-sm";
const buttonClass = "btn-ghost px-3 py-1.5 text-sm font-semibold disabled:opacity-50";
const month = new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
const date = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const priceLabels = { plus_100: "$12/month", plus_250: "$20/month", plus_500: "$40/month", standard: "$12/month", partner_monthly: "$10/month · invited partner", couple_annual: "$80 · one year" };

function MembershipTotals({ api, refresh }) {
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    api.get("/admin/membership/summary", { signal: controller.signal })
      .then(({ data }) => { if (!controller.signal.aborted) setSummary(data); })
      .catch(e => { if (!controller.signal.aborted) setError(errMsg(e)); });
    return () => controller.abort();
  }, [api, attempt, refresh]);
  return <section aria-label="Membership totals" className="space-y-2">
    <div className="grid max-w-[20rem] grid-cols-2 gap-3">
      <Stat square testid="members-total-users" label="Total users" value={summary ? summary.total_users.toLocaleString() : "—"} sub="All accounts" />
      <Stat square testid="members-monthly-revenue" label="Monthly net subscriptions"
        value={summary ? Number.isFinite(summary.monthly_revenue_cents) ? dollars.format(summary.monthly_revenue_cents / 100) : "Pending reconciliation" : "—"}
        sub={summary ? `${summary.environment === "preview" ? "Sandbox · " : ""}${month.format(new Date(summary.month_start + "T00:00:00Z"))} UTC` : undefined} />
    </div>
    {error ? <p role="alert" className="text-xs">Totals couldn’t load. <button className="underline" onClick={() => setAttempt(n => n + 1)}>Retry</button></p>
      : summary ? <div className="space-y-1 text-xs text-[var(--muted)]"><p>Paid USD subscriptions after discounts and verified refund or lost-dispute adjustments, before fees. Sales tax is excluded when invoice details are available. Adjustments are recognized when reconciled; original paid amounts remain recorded.</p>
        {Number.isFinite(summary.monthly_gross_revenue_cents) && <p>Subscriptions: {dollars.format(summary.monthly_gross_revenue_cents / 100)} gross · {dollars.format(summary.monthly_refund_dispute_adjustments_cents / 100)} adjustments · {Number.isFinite(summary.monthly_net_revenue_cents) ? `${dollars.format(summary.monthly_net_revenue_cents / 100)} net` : "net pending reconciliation"}.</p>}
        {Number.isFinite(summary.monthly_refill_gross_revenue_cents) && <p>Refills: {dollars.format(summary.monthly_refill_gross_revenue_cents / 100)} gross · {dollars.format(summary.monthly_refill_adjustments_cents / 100)} adjustments · {Number.isFinite(summary.monthly_refill_net_revenue_cents) ? `${dollars.format(summary.monthly_refill_net_revenue_cents / 100)} net` : "net pending reconciliation"}.</p>}
      </div>
      : <p role="status" className="text-xs text-[var(--muted)]">Loading totals…</p>}
  </section>;
}

function ReplyUsage({ usage }) {
  if (!usage) return <span>Unavailable</span>;
  if (isIntroductory(usage)) return <span>{usage.messages_remaining} of 10 free messages remaining
    <span className="block text-xs text-[var(--muted)]">{usage.messages_used} introductory messages used · once per eligible account</span></span>;
  if (isComplimentary(usage)) return <span>Unlimited<span className="block text-xs text-[var(--muted)]">{usage.messages_used ?? 0} complimentary messages used · all time</span></span>;
  if (usage.unlimited) return <span>Unlimited</span>;
  if (!usage.period_verified) return <span className="text-[var(--muted)]">No current allowance</span>;
  if (usage.message_limit === 0) return <div>
    <span>No monthly messages available</span>
    <span className="block text-xs">{usage.monthly_messages_used ?? usage.messages_used ?? 0} monthly messages used</span>
    {usage.topup_remaining > 0 && <span className="block text-xs">{usage.topup_remaining} refill replies left</span>}
  </div>;
  return <div className="max-w-48 space-y-1">
    <p className="font-semibold tabular-nums">{usage.monthly_messages_used ?? usage.messages_used} <span className="font-normal text-[var(--muted)]">/ {usage.message_limit}</span></p>
    <progress aria-label="Monthly replies used" className="block h-1.5 w-full overflow-hidden rounded-full border-0 bg-[var(--surface-2)] appearance-none [&::-moz-progress-bar]:bg-[var(--ink)] [&::-webkit-progress-bar]:bg-[var(--surface-2)] [&::-webkit-progress-value]:bg-[var(--ink)]" value={usage.monthly_messages_used ?? usage.messages_used} max={usage.message_limit} />
    {usage.topup_remaining > 0 && <p className="text-xs text-[var(--muted)]">{usage.topup_remaining} refill replies left</p>}
    {usage.period_end && <p className="text-xs text-[var(--muted)]">Ends {date.format(usage.period_end * 1000)} UTC</p>}
  </div>;
}

const INTRO_BILLING_STATUS = {
  checkout_pending: "Card setup pending", active: "Free messages active", billing_pending: "First payment pending",
  requires_action: "Payment authentication required", payment_failed: "Payment not confirmed",
  review_required: "Billing review required", cancelled: "Introduction canceled", converted: "Paid conversion confirmed",
};
function IntroBillingStatus({ member }) {
  if (!member.intro_billing) return null;
  const state = member.intro_billing.state;
  const plan = member.intro_selected_plan;
  const validPlan = /^plus_(100|250|500)$/.test(plan?.id || "") && Number.isSafeInteger(plan.price_cents)
    && plan.price_cents > 0 && Number.isSafeInteger(plan.messages) && plan.messages > 0;
  return <div className="mt-2 space-y-1 text-xs" data-testid="member-intro-billing">
    <p className="font-semibold">Intro billing: {INTRO_BILLING_STATUS[state] || "Unverified status"}</p>
    {validPlan && <p>Introductory selection: {plan.name} · {dollars.format(plan.price_cents / 100)}/month · {plan.messages} paid messages.</p>}
    {state !== "converted" && <p className="text-[var(--muted)]">A paid conversion has not been recorded for this introduction.</p>}
  </div>;
}

const ACTION_LABELS = { made_admin: "Granted administrator access", made_member: "Changed to member",
  replies_paused: "Paused new replies", replies_resumed: "Resumed new replies", couples_enrolled: "Enabled Couples invitations during early access",
  couples_removed: "Removed Couples invitations during early access", reply_access_changed: "Changed reply access", enrollment_changed: "Changed Couples enrollment during early access",
  complimentary_unlimited_granted: "Granted complimentary unlimited access", complimentary_unlimited_revoked: "Revoked complimentary unlimited access" };

function MemberActionHistory({ api, memberPath }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!open) return;
    const request = new AbortController();
    setData(null); setError(false);
    api.get(memberPath + "/actions", { signal: request.signal })
      .then(({data}) => { if (!request.signal.aborted) setData(data); })
      .catch(() => { if (!request.signal.aborted) setError(true); });
    return () => request.abort();
  }, [api, memberPath, open, attempt]);
  return <div className="text-sm"><button type="button" aria-expanded={open} className="font-semibold underline" onClick={() => setOpen(v => !v)}>Staff action history</button>
    {open && <div className="mt-2">{error ? <p role="alert">History couldn’t load. <button className="underline" onClick={() => setAttempt(n => n + 1)}>Retry</button></p>
      : !data ? <p role="status">Loading history…</p> : !data.actions.length ? <p>No recorded staff actions.</p> : <>
        <p className="text-xs text-[var(--muted)]">Up to the latest {data.limit} recorded changes.</p>
        <ol className="max-h-48 overflow-y-auto divide-y divide-[var(--line)]">{data.actions.map((row, i) => <li key={i} className="py-2">
          <strong>{ACTION_LABELS[row.action] || row.action}</strong>
          <span className="block break-all text-xs">{new Date(row.at).toLocaleString(undefined, {timeZone: "UTC"})} UTC · {row.actor}</span>
        </li>)}</ol></>}</div>}
  </div>;
}

function MemberActions({ api, member, roles, self, onChange }) {
  const [open, setOpen] = useState(false);
  const [action, setAction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const pendingComplimentary = useRef(null);
  const who = member.email || member.nickname || member.user_id;
  const closing = member.membership_status === "closing";
  const memberPath = `/users/${encodeURIComponent(member.user_id)}`;
  const actions = roles.filter(role => role !== member.role).map(role => ({
    key: `role-${role}`, label: role === "admin" ? "Make administrator" : "Change to member",
    detail: role === "admin" ? "Grants access to the Admin dashboard, including member management and reports." : "Removes access to the Admin dashboard.",
    run: () => api.patch(`${memberPath}/role`, { role }), role,
  }));
  if (!self && member.role === "member" && !closing) {
    const paused = Boolean(member.replies_paused);
    actions.push({
      key: "replies", label: paused ? "Resume new replies" : "Pause new replies",
      detail: isComplimentary(member.usage)
        ? paused ? "Restores new replies under the member’s complimentary unlimited access."
          : "Blocks new replies. The member keeps their saved history and complimentary entitlement."
        : paused ? "Restores new replies within the member’s current subscription allowance."
          : "Blocks new metered replies. The member keeps their saved history, and subscription billing continues.",
      run: () => api.patch(`${memberPath}/reply-access`, { paused: !paused }),
    });
    const complimentary = isComplimentary(member.usage);
    const entitlement = member.complimentary_entitlement;
    const validEntitlement = entitlement?.version === "complimentary-unlimited-v1" && entitlement.active === true
      && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(entitlement.id || "");
    if (!complimentary || validEntitlement) actions.push({
      key: "complimentary", label: complimentary ? "Revoke complimentary unlimited" : "Grant complimentary unlimited",
      detail: complimentary ? "Removes this complimentary grant without changing the member’s role, saved content or Stripe subscription. Any other access is evaluated separately."
        : "Grants unlimited Individual and Couples messages to this ordinary member. Does not create a paid subscription or change their role. Existing billing or checkout activity must be reviewed first.",
      complimentary: true, enabled: !complimentary, expectedEntitlement: complimentary ? entitlement.id : null,
      run: body => api.patch(`${memberPath}/complimentary-access`, body),
    });
  }
  const selected = actions.find(a => a.key === action);
  const changeOpen = value => {
    if (busy) return;
    setOpen(value); setAction(""); setError(""); setReason("");
  };
  async function apply(event) {
    event.preventDefault();
    if (!selected || busy) return;
    setBusy(true); setError("");
    try {
      let body;
      if (selected.complimentary) {
        const explanation = reason.trim();
        if (!explanation || explanation.length > 300) throw new Error("Enter a reason of 1 to 300 characters.");
        const signature = JSON.stringify([member.user_id, selected.enabled, selected.expectedEntitlement, explanation]);
        if (pendingComplimentary.current?.signature !== signature) pendingComplimentary.current = { signature, body: {
          expected_member: member.user_id, request_id: crypto.randomUUID(), enabled: selected.enabled,
          expected_entitlement_id: selected.expectedEntitlement, reason: explanation,
        } };
        body = pendingComplimentary.current.body;
      }
      await selected.run(body);
      if (selected.complimentary) pendingComplimentary.current = null;
      setOpen(false);
      toast.success("Member updated");
      onChange(self ? selected.role : undefined);
    } catch (e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  }
  return <Dialog open={open} onOpenChange={changeOpen}>
    <DialogTrigger asChild><button type="button" className={buttonClass} aria-label={`${closing ? "View" : "Manage"} ${who}`} data-testid={`admin-manage-${member.user_id}`}>{closing ? "View" : "Manage"}</button></DialogTrigger>
    <DialogContent className="w-[calc(100%-2rem)] sm:max-w-4xl min-w-0 max-h-[90dvh] overflow-y-auto">
      <DialogHeader><DialogTitle>{closing ? "Member details" : "Manage member"}</DialogTitle><DialogDescription className="break-all">{who}</DialogDescription></DialogHeader>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
        <dt className="text-[var(--muted)]">Account ID</dt><dd className="break-all">{member.user_id}</dd>
        <dt className="text-[var(--muted)]">Role</dt><dd>{member.role === "admin" ? "Administrator" : "Member"}{self && " (you)"}</dd>
        <dt className="text-[var(--muted)]">Member status</dt><dd>{STATUS[member.membership_status] || "Unknown"}</dd>
        {member.origin && <><dt className="text-[var(--muted)]">Entry point</dt><dd className="break-words">{member.origin}</dd></>}
      </dl>
      <MemberActionHistory api={api} memberPath={memberPath} />
      {!closing && <AnswerHistoryTimeline key={member.user_id} api={api} scope="member" target={member.user_id} />}
      {closing ? <p className="text-sm text-[var(--muted)]">Account closure is in progress.</p> : <form id="member-action-form" onSubmit={apply} className="space-y-3">
        <div><label htmlFor="member-action" className="block text-sm font-semibold">Account action</label>
          <select id="member-action" className={`${selectClass} w-full mt-1.5`} disabled={busy} value={action} onChange={e => { setAction(e.target.value); setError(""); setReason(""); }}>
            <option value="">Choose an action</option>
            {actions.map(a => <option key={a.key} value={a.key}>{a.label}</option>)}
          </select>
        </div>
        {selected && <p className="text-sm text-[var(--muted)]">{selected.detail}</p>}
        {selected?.complimentary && <div><label htmlFor="complimentary-reason" className="block text-sm font-semibold">Reason for this change</label>
          <textarea id="complimentary-reason" maxLength={300} required disabled={busy} value={reason} onChange={e => setReason(e.target.value)}
            className="w-full mt-1.5 rounded-md border border-[var(--line)] bg-[var(--bg)] p-2 text-sm" />
        </div>}
        {error && <p role="alert" className="text-sm text-[var(--crimson)]">{error}</p>}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className={buttonClass} disabled={busy} onClick={() => changeOpen(false)}>Cancel</button>
          <button type="submit" className="btn-lime px-4 py-2 text-sm disabled:opacity-50" disabled={busy || !selected || (selected.complimentary && !reason.trim())}>{busy ? "Applying…" : selected?.label || "Apply action"}</button>
        </div>
      </form>}
    </DialogContent>
  </Dialog>;
}

export default function MembersAdmin({ api, me, onOwnRoleChange, settings }) {
  const [page, setPage] = useState(null);
  const [query, setQuery] = useState(() => new URLSearchParams(window.location.search).get("q")?.slice(0, 256) || "");
  const [status, setStatus] = useState("");
  const [cursors, setCursors] = useState([""]);
  const [attempt, setAttempt] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState("");
  const after = cursors[cursors.length - 1];
  useEffect(() => {
    const controller = new AbortController();
    setPage(null); setError("");
    const timer = setTimeout(async () => {
      try {
        const { data } = await api.get("/users", { params: { q: query, status, after, limit: 50 }, signal: controller.signal });
        if (!controller.signal.aborted) setPage(data);
      } catch (e) { if (!controller.signal.aborted) setError(errMsg(e)); }
    }, query ? 180 : 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [api, query, status, after, attempt, refresh]);
  function updated(ownRole) {
    if (ownRole) onOwnRoleChange(ownRole);
    setAttempt(n => n + 1);
  }
  return <div className="min-w-0 space-y-5 pb-2 lg:min-h-0 lg:overflow-y-auto" data-testid="admin-members">
    <MembershipTotals api={api} refresh={refresh} />
    <IntroAccessAdmin api={api} />
    <BillingReviews key={refresh} api={api} />
    <BillingRiskReviews api={api} />
    <MembershipReconciliation api={api} />
    <section className="card min-w-0" data-testid="admin-users" aria-labelledby="member-directory-title">
      <div className="space-y-4 p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="member-directory-title" className="font-display font-black text-xl uppercase">Member directory</h2>
          <div className="flex flex-wrap items-center gap-2"><button type="button" className={buttonClass} onClick={() => setRefresh(n => n + 1)}>Refresh</button>
          <Dialog><DialogTrigger asChild><button className={`${buttonClass} inline-flex items-center gap-2`} type="button"><SlidersHorizontal size={15} aria-hidden="true" />Plan settings</button></DialogTrigger>
            <DialogContent className="w-[calc(100%-2rem)] max-w-4xl max-h-[90dvh] overflow-y-auto" aria-describedby={undefined}>
              <DialogHeader><DialogTitle>Plan settings</DialogTitle></DialogHeader>{settings}
            </DialogContent>
          </Dialog></div>
        </div>
        <p className="text-xs text-[var(--muted)]">Usage is shown on the paying account. It includes Individual replies, Couples coaching and coaching requested by a sponsored partner.</p>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex-auto sm:max-w-sm text-xs font-semibold">Search users
            <Input type="search" maxLength={256} value={query} onChange={e => { setQuery(e.target.value); setCursors([""]); }} placeholder="Email, name, or account ID" className="mt-1" data-testid="admin-users-search" />
          </label>
          <div><label htmlFor="directory-status" className="block text-xs font-semibold">Member status</label>
            <select id="directory-status" className={`${selectClass} mt-1 block`} value={status} onChange={e => { setStatus(e.target.value); setCursors([""]); }}>
              <option value="">All statuses</option>
              {Object.entries(STATUS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          </div>
          {(after || page?.next_cursor) && <nav aria-label="User pages" className="ml-auto flex items-center gap-2 text-sm">
            <button type="button" className={buttonClass} disabled={!after || !page} onClick={() => setCursors(p => p.slice(0, -1))}>Previous</button>
            <span>Page {cursors.length}</span>
            <button type="button" className={buttonClass} disabled={!page?.next_cursor} onClick={() => setCursors(p => [...p, page.next_cursor])}>Next</button>
          </nav>}
        </div>
      </div>
      {error ? <p role="alert" className="p-4 text-sm">{error} <button type="button" className="underline" onClick={() => setAttempt(n => n + 1)}>Retry</button></p>
        : !page ? <p role="status" className="p-4 text-sm">Loading users…</p>
        : !page.users.length ? <p className="p-4 text-sm">No users found.</p>
        : <table aria-label="Members" className="block w-full text-left text-sm md:table md:table-fixed">
          <thead className="sr-only md:not-sr-only md:table-header-group"><tr className="border-y border-[var(--line)] text-xs text-[var(--muted)]">
            <th scope="col" className="w-[34%] px-4 py-2.5">Member</th><th scope="col" className="w-[22%] px-3 py-2.5">Status</th>
            <th scope="col" className="px-3 py-2.5">Reply usage</th><th scope="col" className="w-24 px-3 py-2.5">Actions</th>
          </tr></thead>
          <tbody className="block md:table-row-group">{page.users.map(user => <tr key={user.user_id}
            className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 border-t border-[var(--line)] p-4 md:table-row">
            <th scope="row" className="min-w-0 font-semibold md:px-4 md:py-3">
              <span className="block break-words [overflow-wrap:anywhere]">{user.nickname || user.email || user.user_id}</span>
              {user.nickname && user.email && <span className="block break-all text-xs font-normal text-[var(--muted)]">{user.email}</span>}
              <YourTypeMember api={api} userId={user.user_id} />
            </th>
            <td className="order-3 md:px-3 md:py-3"><span>{STATUS[user.membership_status] || "Unknown"}</span>
              {user.usage?.plan_name && <span className="block text-xs font-semibold">{user.usage.plan_name}</span>}
              {!isComplimentary(user.usage) && priceLabels[user.plan_offer] && <span className="block text-xs text-[var(--muted)]">{priceLabels[["partner_monthly", "couple_annual"].includes(user.plan_offer) ? user.plan_offer : user.usage?.plan_id || user.plan_offer]}</span>}
              {user.replies_paused && <span className="block text-xs font-semibold">Replies paused</span>}
              {user.billing_review?.actionable && <span className="block text-xs font-semibold">{user.billing_review.hold_scopes?.length ? "Billing review hold" : "Billing review requested"}</span>}
            </td>
            <td className="order-4 col-span-2 md:px-3 md:py-3"><span className="mb-1 block text-xs text-[var(--muted)] md:hidden">Reply usage</span><ReplyUsage usage={user.usage} />
              <IntroBillingStatus member={user} />
              {user.usage?.payment_dispute_hold === true ? <p className="text-xs mt-1" data-testid="member-payment-hold">{Number.isSafeInteger(user.usage.held_message_count) && user.usage.held_message_count >= 0 ? `${user.usage.held_message_count} unused messages are` : "Some unused messages are"} temporarily unavailable during payment review.</p>
                : user.usage?.allowance_adjusted && <p className="text-xs mt-1">Allowance adjusted after a refund or lost dispute. Original monthly allowance: {user.usage.original_message_limit}.</p>}
              <BillingRecords key={user.user_id} api={api} userId={user.user_id} />
            </td>
            <td className="order-2 md:px-3 md:py-3"><MemberActions api={api} roles={page.roles} member={user} self={me?.user_id === user.user_id} onChange={updated} /></td>
          </tr>)}</tbody>
        </table>}
    </section>
  </div>;
}
