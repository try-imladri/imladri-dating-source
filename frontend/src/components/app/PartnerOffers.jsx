import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { errMsg } from "@/lib/api";
import { purchaseAcceptance } from "./PurchasePolicy";

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "long", timeStyle: "long" });

function timestamp(value) {
  if (!value) return NaN;
  if (typeof value === "number") return value < 1e12 ? value * 1000 : value;
  return Date.parse(value);
}

function dateText(value) {
  const time = timestamp(value);
  return Number.isFinite(time) ? dateFormat.format(time) : "";
}

function useOpenWindow(value) {
  const end = timestamp(value);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    setNow(Date.now());
    if (!Number.isFinite(end) || end <= Date.now()) return undefined;
    const timer = setTimeout(() => setNow(Date.now()), Math.max(1, end - Date.now()));
    return () => clearTimeout(timer);
  }, [end]);
  return Number.isFinite(end) && Math.max(now, Date.now()) < end;
}

export function CouplesMembershipOffer({ api }) {
  const [offer, setOffer] = useState(null);
  useEffect(() => {
    let active = true;
    function fetchOffer() {
      if (document.hidden) return;
      api.get("/me/partner-offer").then(({ data }) => {
        if (active) setOffer(data);
      }).catch(() => { if (active) setOffer(null); });
    }
    fetchOffer();
    document.addEventListener("visibilitychange", fetchOffer);
    return () => { active = false; document.removeEventListener("visibilitychange", fetchOffer); };
  }, [api]);
  const open = useOpenWindow(offer?.annual_expires_at);
  const annual = offer?.annual_eligible && open && !offer.annual_accepted;
  if (!annual && !offer?.monthly_eligible) return null;
  return <section className="card p-5" aria-label="Partner membership offer">
    <h2 className="font-display font-black uppercase text-2xl">{annual ? "Your $80 year" : "Add Individual for $10/month"}</h2>
    <p className="text-sm mt-2">{annual ? `Your offer ends ${dateText(offer.annual_expires_at)}. Each partner can choose separately.` : "The partner rate adds your Individual workspace and 100 monthly replies of your own to use across Individual and Couples."}</p>
    <Link to="/app/settings/upgrade#partner-offer" className="inline-flex mt-3 underline font-semibold text-sm">View membership offer</Link>
  </section>;
}

export function PartnerAnnualOffer({ api, owner, offer, billingEnabled, purchaseAccepted, annualActive, onChange }) {
  const [review, setReview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [cancelReview, setCancelReview] = useState(false);
  const [cancelled, setCancelled] = useState(false);
  const submitting = useRef(false);
  const open = useOpenWindow(offer?.annual_expires_at);
  const accepted = offer?.annual_accepted || submitted;
  const pending = offer?.annual_pending === true;
  const start = dateText(offer?.annual_starts_at);
  if (offer?.annual_cancelled || cancelled) return <section aria-label="Partner annual offer" className="card p-6 mt-4">
    <h2 className="font-display uppercase font-black text-3xl">Scheduled year canceled</h2>
    <p className="mt-3 text-sm">Your monthly membership ends {start || "at the end of your current paid month"}. The $80 charge has been canceled.</p>
  </section>;
  if (!accepted && !pending && (!offer?.annual_eligible || !open)) return null;
  const end = dateText(offer?.annual_ends_at);
  const available = billingEnabled && offer?.annual_available === true && (open || pending);
  async function accept() {
    if (!available || !purchaseAccepted || submitting.current || (!pending && timestamp(offer.annual_expires_at) <= Date.now())) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      const authorization = { accept: true, ...purchaseAcceptance(), ...(owner && { expected_member: owner }) };
      if (!pending && offer.annual_starts_at) authorization.expected_starts_at = offer.annual_starts_at;
      const { data } = await api.post("/payments/partner-annual", authorization);
      if (data.accepted !== true) throw new Error("Pending confirmation");
      setSubmitted(true);
      onChange();
    } catch (e) {
      setError(errMsg(e));
      setReview(false);
      onChange();
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  async function cancelYear() {
    if (!offer.annual_cancel_available || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post("/payments/cancel-partner-year", { accept: true, ...(owner && { expected_member: owner }) });
      if (!data.cancelled) throw new Error("Cancellation unconfirmed");
      setCancelled(true);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      submitting.current = false;
      setBusy(false);
      onChange();
    }
  }
  return <section id="partner-offer" aria-label="Partner annual offer" className="card p-6 mt-4 border-[var(--ink)]">
    <h2 className="font-display uppercase font-black text-3xl">{annualActive ? "Your one-year membership" : accepted ? "Your $80 year is scheduled" : pending ? "Your $80 year is awaiting confirmation" : "One year of Plus for $80"}</h2>
    <p className="mt-3 text-sm">{accepted || pending ? "You authorized one $80 payment" : "Your $80 payment is due"} {start ? `on ${start}` : "at the end of your current paid month"}. Your account receives 100 replies each month for one year. {end ? `The term ends ${end}` : "The term ends after one year"} with no automatic renewal.</p>
    {error && <p role="alert" className="mt-3 text-sm text-[var(--crimson)]">{error}</p>}
    {accepted && offer?.annual_cancel_available && <div className="mt-4 space-y-3 text-sm">
      {cancelReview ? <>
        <p>This cancels your upcoming $80 charge and ends your membership {start || "at the end of this paid month"}. Your partner’s membership stays on its own schedule.</p>
        <div className="flex flex-wrap gap-4">
          <button type="button" className="btn-ghost px-4 py-2" disabled={busy} onClick={cancelYear}>{busy ? "Canceling…" : "Confirm cancellation"}</button>
          <button type="button" className="underline" disabled={busy} onClick={() => setCancelReview(false)}>Keep my scheduled year</button>
        </div>
      </> : <button type="button" className="underline" onClick={() => setCancelReview(true)}>Cancel scheduled membership</button>}
    </div>}
    {!accepted && <>
      <p className="mt-3 text-sm">Each partner chooses and pays separately. If you both accept, the combined price is $160 before any applicable tax.</p>
      {!pending && <p className="mt-3 text-sm font-semibold">Offer ends {dateText(offer.annual_expires_at)}.</p>}
      {review && <p className="mt-3 text-sm">By confirming, you authorize the charge to your saved payment method for your account, plus any applicable tax.</p>}
      {available ? <div className="mt-4 flex flex-wrap items-center gap-4">
        <button type="button" disabled={busy || !purchaseAccepted} className="btn-lime px-5 py-2 disabled:opacity-50" onClick={pending || review ? accept : () => setReview(true)}>{busy ? "Confirming…" : pending ? "Retry confirmation" : review ? "Confirm my $80 year" : "Review $80 year"}</button>
        {review && <button type="button" disabled={busy} onClick={() => setReview(false)} className="text-sm underline">Back</button>}
      </div> : <p className="mt-3 text-sm text-[var(--muted)]">{billingEnabled ? "This offer is currently unavailable for checkout." : "Billing opens later."}</p>}
    </>}
  </section>;
}
