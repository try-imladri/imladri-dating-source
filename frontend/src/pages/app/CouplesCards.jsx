import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useNavigate, useOutletContext } from "react-router-dom";
import { ArrowLeft, ArrowRight, Check } from "lucide-react";
import { BASE, agreementUrl, answerUrl, futureDate, learningUrl, pageHeading, threadUrl } from "./CouplesCommon";
import { CARDS } from "@/lib/couplesCards";

const heading = "font-display font-black uppercase leading-none";
const primaryButton = "btn-lime inline-flex items-center justify-center gap-2 px-5 py-2.5 text-sm";

function nextCardStep(question, thread) {
  const agreements = thread?.agreements || [];
  const due = agreements.filter(a => a.status === "active" && a.review_on <= futureDate(0) && !a.my_checkin?.shared)
    .sort((a, b) => a.review_on.localeCompare(b.review_on))[0];
  if (due) return { label: "Open my check-in", to: key => agreementUrl(key, due) };
  const proposal = agreements.find(a => a.status === "proposed" && !a.accepted_by_me);
  if (proposal) return { label: "Review proposal", to: key => agreementUrl(key, proposal) };
  const pinToReview = agreements.find(a => a.pin && !a.pin.active && !a.pin.accepted_by_me);
  if (pinToReview) return { label: "Review proposed learning", to: key => learningUrl(key, pinToReview) };
  const checkedIn = agreements.find(a => !a.pin && a.my_checkin?.shared && a.partner_checkin);
  if (checkedIn) return { label: "Record what worked", to: key => learningUrl(key, checkedIn) };
  if (agreements.length) return { label: "View agreements", to: key => agreementUrl(key) };
  if (thread?.my_share?.length) return { label: "Open conversation", to: threadUrl };
  if (thread?.partner_shared) return { label: "Add my perspective", to: answerUrl };
  return { label: question ? "Continue my answer" : "Try with my partner", to: answerUrl };
}

export function ConversationCards() {
  const { api, me, workspace, journey, mutate } = useOutletContext();
  const navigate = useNavigate();
  const [card, setCard] = useState(null);
  const [step, setStep] = useState("choose");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const submitting = useRef(false);
  const title = useRef(null);
  const moveFocus = useRef(false);
  const viewRevision = useRef(0);
  const current = useRef(null);
  useLayoutEffect(() => {
    current.current = { member: me?.user_id, workspace, journey };
    return () => { current.current = null; };
  }, [me?.user_id, workspace, journey]);

  useEffect(() => {
    if (moveFocus.current) title.current?.focus();
  }, [step]);

  const go = (next) => {
    viewRevision.current += 1;
    moveFocus.current = true;
    setSaveError("");
    setStep(next);
  };
  const startAction = (item, origin) => {
    const question = journey?.prompts.find(q => q.card === item.id && !q.retired);
    const thread = question && journey.threads.find(t => t.id === question.id);
    const paired = workspace?.state === "active" && workspace.writable;
    if (!paired) return <Link to={BASE + "/partner"} className="text-sm underline underline-offset-4">
      {workspace?.state === "active" ? "Review shared access" : "Connect a partner to try it"}
    </Link>;
    const next = nextCardStep(question, thread);
    const start = async () => {
      if (submitting.current) return;
      const startedInView = viewRevision.current;
      submitting.current = true;
      setSaving(true);
      setSaveError("");
      try {
        const { key } = await mutate("post", "/couples/cards/" + item.id + "/start", { pair_id: workspace.pair_id, origin });
        const latest = current.current;
        if (startedInView !== viewRevision.current || !latest?.workspace?.writable || latest.workspace.state !== "active"
          || latest.workspace.pair_id !== workspace.pair_id || latest.member !== me?.user_id) return;
        const updatedQuestion = latest.journey?.prompts.find(q => q.id === key && !q.retired);
        if (latest.journey?.prompts.some(q => q.id === key && q.retired)) {
          setSaveError("This card has been retired. Choose another card.");
          return;
        }
        const updatedThread = latest.journey?.threads.find(t => t.id === key);
        navigate(nextCardStep(updatedQuestion, updatedThread).to(key));
      } catch {
        if (startedInView === viewRevision.current && current.current?.workspace?.pair_id === workspace.pair_id
          && current.current?.member === me?.user_id) setSaveError("Your card could not be opened. Try again.");
      } finally {
        submitting.current = false;
        setSaving(false);
      }
    };
    return <button type="button" data-testid={`couples-start-${item.id}`} className={primaryButton} disabled={saving} onClick={start}>
      {next.label}<span className="sr-only"> · {item.name}</span> <ArrowRight className="w-4 h-4" aria-hidden="true" />
    </button>;
  };
  const error = saveError && <p role="alert" className="mt-2 text-sm">{saveError}</p>;
  const finish = async () => {
    if (submitting.current) return;
    submitting.current = true;
    setSaving(true);
    setSaveError("");
    try {
      await api.post("/me/couples-activity", { action: "sample_completed" });
      go("complete");
    } catch {
      setSaveError("We couldn’t save your completion. Select Finish sample to try again.");
    } finally {
      submitting.current = false;
      setSaving(false);
    }
  };

  if (step === "choose") return (
    <section className="min-w-0 max-w-3xl" data-testid="couples-cards">
      <h1 className={pageHeading} ref={title} tabIndex={-1}>Conversation cards</h1>
      <p className="mt-2 text-sm text-[var(--muted)]">Read an example or try a card together. Each of you writes privately, chooses what to share, then agrees on something to try and a day to check in.</p>
      <details className="mt-3 text-sm"><summary className="cursor-pointer underline">Take part in the optional check-in study</summary><p className="mt-2">Both partners can choose scheduled review tracking in <Link className="underline" to={BASE+"/partner"}>Partner &amp; settings</Link> before accepting the first agreement. The study counts returns to the check-in and whether both shared a result. You can use every card with this option off.</p></details>
      {error}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-6">
        {CARDS.map(item => <article key={item.id} className="card min-w-0 p-5 flex flex-col items-start">
          <h2 className={`${heading} text-2xl break-words`}>{item.name}</h2>
          <p className="mt-2 mb-4 text-sm text-[var(--muted)]">{item.question}</p>
          <div className="flex flex-wrap items-center gap-3 mt-auto">
            {startAction(item, "card")}
            <button type="button" className="text-sm underline underline-offset-4" data-testid={`couples-card-${item.id}`}
              onClick={() => { setCard(item); go("perspectives"); }}>Read sample<span className="sr-only"> for {item.name}</span></button>
          </div>
        </article>)}
      </div>
    </section>
  );

  if (step === "complete") return (
    <section className="max-w-2xl space-y-4" data-testid="couples-card-complete">
      <div className="inline-flex p-2.5 rounded-full border border-[var(--line)]"><Check className="w-5 h-5" aria-hidden="true" /></div>
      <h1 ref={title} tabIndex={-1} className={`${heading} text-4xl`}>Sample complete</h1>
      <p className="text-sm">After you try it, use these questions to check in.</p>
      <p className="card px-5 py-4 text-base">{card.checkIn}</p>
      {error}
      <div className="flex flex-wrap items-center gap-4">
        {startAction(card, "sample")}
        <button type="button" className={primaryButton} onClick={() => go("choose")}>Choose another card <ArrowRight className="w-4 h-4" aria-hidden="true" /></button>
        <Link to="/app/couples" className="text-sm underline underline-offset-4">Couples overview</Link>
      </div>
    </section>
  );

  return (
    <section className="max-w-2xl space-y-5" data-testid="couples-card-detail">
      <button type="button" disabled={saving} className="inline-flex items-center gap-1 text-sm text-[var(--muted)]" onClick={() => go("choose")}><ArrowLeft className="w-4 h-4" aria-hidden="true" /> All cards</button>
      <div>
        <p className="eyebrow mb-2">{card.name}</p>
        <h1 ref={title} tabIndex={-1} className={`${heading} text-3xl sm:text-4xl`}>{step === "perspectives" ? card.question : "An agreement to try"}</h1>
      </div>
      {error}
      {step === "perspectives" ? (
        <>
          <div className="grid sm:grid-cols-2 gap-3" aria-label="Two example answers">
            {card.perspectives.map((answer, index) => <figure key={answer} className="card p-5">
              <figcaption className="font-semibold text-sm mb-2">{index === 0 ? "Alex" : "Jordan"} · Example</figcaption>
              <blockquote className="text-sm">“{answer}”</blockquote>
            </figure>)}
          </div>
          <button type="button" data-testid="couples-card-next" className={primaryButton} onClick={() => go("agreement")}>See a possible agreement <ArrowRight className="w-4 h-4" aria-hidden="true" /></button>
        </>
      ) : (
        <>
          <p className="card px-5 py-4 text-base">{card.agreement}</p>
          <p className="text-sm text-[var(--muted)]">What would each of you change to make this work for you?</p>

          <div className="flex flex-wrap items-center gap-4">
            <button type="button" data-testid="couples-card-finish" disabled={saving} className={primaryButton} onClick={finish}>{saving ? "Saving…" : "Finish sample"} <Check className="w-4 h-4" aria-hidden="true" /></button>
            <button type="button" disabled={saving} onClick={() => go("perspectives")} className="text-sm underline underline-offset-4">Read answers again</button>
          </div>
        </>
      )}
      <div className="border-t border-[var(--line)] pt-4">{startAction(card, "sample")}</div>
    </section>
  );
}
