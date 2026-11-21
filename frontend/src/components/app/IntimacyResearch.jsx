import { useEffect, useRef, useState } from "react";
import { DEMO_LABELS, DEMO_OPTS } from "./Demographics";
import { matchesResearchCatalog } from "./intimacyTopics";

const endpoint = "/me/research";
const consentVersion = "research-v1";
const buttonClass = "btn-ghost px-4 py-2 text-sm disabled:opacity-50";

function ContributionForm({ api, answers, catalogVersion, intimacySignature, sections }) {
  const [receipt, setReceipt] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [selectedFields, setSelectedFields] = useState([]);
  const [confirmRemoval, setConfirmRemoval] = useState(false);
  const [savedThisVisit, setSavedThisVisit] = useState(false);
  const [currentCatalog, setCurrentCatalog] = useState(false);
  const [profile, setProfile] = useState(null);
  const [pending, setPending] = useState(false);
  const busy = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      api.get(endpoint, { signal: controller.signal }),
      answers ? matchesResearchCatalog(sections, intimacySignature).catch(() => false) : false,
      answers ? api.get("/me/about", { signal: controller.signal }) : null,
    ]).then(([{ data }, matches, personal]) => {
      if (!controller.signal.aborted) {
        setCurrentCatalog(matches);
        setProfile(personal?.data.about || null);
        setReceipt(data);
      }
    }).catch(() => {
      if (!controller.signal.aborted) setError("Your contribution status couldn’t be loaded.");
    });
    return () => controller.abort();
  }, [api, attempt, answers, intimacySignature, sections]);

  async function update(remove) {
    if (busy.current || !receipt || (remove ? !confirmRemoval : !agreed)) return;
    busy.current = true;
    setPending(true);
    setNotice("");
    setError("");
    try {
      const { data } = remove
        ? await api.delete(endpoint, { params: { revision: receipt.revision } })
        : await api.put(endpoint, {
          consent: true, consent_version: consentVersion, adult_confirmed: true,
          revision: receipt.revision, catalog_version: catalogVersion, profile_updated_at: profile.updated_at ?? null,
          answers, demographic_fields: selectedFields,
        });
      setReceipt(data);
      setSavedThisVisit(!remove);
      setNotice(remove ? "Your saved contribution was withdrawn. Future reports will exclude it."
        : "Your contribution is saved. You can withdraw it here or in Settings.");
    } catch (e) {
      const detail = e?.response?.data?.detail;
      setError(typeof detail === "string" ? detail : "The change couldn’t be confirmed. Check your saved status before trying again.");
      setReceipt(null);
    } finally {
      setAgreed(false);
      setSelectedFields([]);
      setConfirmRemoval(false);
      setPending(false);
      busy.current = false;
    }
  }

  const everyday = Object.entries(profile?.answers || {});
  const demographics = profile?.demographics || {};
  const adult = demographics.age == null || demographics.age === "" || (demographics.age >= 18 && demographics.age <= 120);
  const availableFields = Object.keys(DEMO_OPTS).filter((key) =>
    demographics[key] !== "Prefer not to say" && DEMO_OPTS[key].includes(demographics[key]));
  const savedFields = receipt?.demographic_fields?.map((key) => DEMO_LABELS[key]).filter(Boolean) || [];
  return <div className="space-y-3 text-sm text-[var(--ink)]">
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert">{error}</p>}
    {!receipt && (error ? <button type="button" className={buttonClass}
      onClick={() => { setError(""); setAttempt((count) => count + 1); }}>Check saved status</button> : <p role="status">Checking your contribution…</p>)}
    {receipt && <>
      {receipt.submitted ? <div className="space-y-1">
        <p className="font-semibold">Saved research contribution</p>
        <p>{receipt.answered} intimacy {receipt.answered === 1 ? "answer" : "answers"} · {receipt.skipped} skipped · {receipt.everyday_answered} everyday {receipt.everyday_answered === 1 ? "answer" : "answers"} · {receipt.age_band ? `Age band ${receipt.age_band}` : "Age band withheld"}</p>
        {savedFields.length > 0 && <p>Additional details included · {savedFields.join(", ")}</p>}
        <p className="text-xs text-[var(--muted)]">View the saved record in Settings → Your data → Export everything.</p>
        {confirmRemoval ? <div className="space-y-2">
          <p>Remove your saved answers from this research dataset?</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={buttonClass} disabled={pending} onClick={() => update(true)}>Confirm withdrawal</button>
            <button type="button" className={buttonClass} disabled={pending} onClick={() => setConfirmRemoval(false)}>Keep contribution</button>
          </div>
        </div> : <button type="button" className="py-2 underline underline-offset-4" disabled={pending}
          onClick={() => { setAgreed(false); setConfirmRemoval(true); }}>Withdraw contribution</button>}
      </div> : <p>You have no saved research contribution.</p>}
      {answers && !savedThisVisit && !confirmRemoval && adult && (currentCatalog && profile && receipt.consent_version === consentVersion ? <>
        <p>Imladri will store your intimacy choices, any conditions you selected and the questions you skipped in a record linked to your account. It also includes the fixed-choice everyday quiz answers you’ve saved and your age band if you’ve provided an age. Written notes and conversations are excluded.</p>
        <p>Imladri staff use aggregate reports to compare answers across groups. Submitting again replaces your previous record. You can withdraw in Settings at any time.</p>
        {everyday.length > 0 && <details>
          <summary className="cursor-pointer underline underline-offset-4">Review your saved everyday choices ({everyday.length})</summary>
          <dl className="mt-2 space-y-2">{everyday.map(([id, { question, label }]) => <div key={id}>
            <dt>{question}</dt><dd className="font-semibold">{label}</dd>
          </div>)}</dl>
        </details>}
        {availableFields.length > 0 && <fieldset className="space-y-2">
          <legend className="mb-1 font-semibold">Include additional profile details (optional)</legend>
          {availableFields.map((key) => <label key={key} className="flex items-start gap-2">
            <input type="checkbox" className="mt-1 shrink-0" checked={selectedFields.includes(key)} disabled={pending}
              onChange={(event) => {
                const include = event.target.checked;
                setSelectedFields((current) => include ? [...current, key] : current.filter((field) => field !== key));
                setAgreed(false);
              }} />
            {DEMO_LABELS[key]} · {demographics[key]}
          </label>)}
        </fieldset>}
        <label className="flex items-start gap-2 font-semibold">
          <input type="checkbox" className="mt-1 shrink-0" checked={agreed} disabled={pending} onChange={(event) => setAgreed(event.target.checked)} />
          I agree to Imladri storing and comparing these answers for group research.
        </label>
        <button type="button" className="btn-lime px-4 py-2 text-sm disabled:opacity-50" disabled={pending || !agreed}
          onClick={() => update(false)}>{pending ? "Saving…" : receipt.submitted ? "Replace my contribution" : "Submit my contribution"}</button>
      </> : <p>This visit couldn’t be matched to the current research questionnaire and terms. You can keep exploring your answers here. To contribute, open a new visit with the latest version.</p>)}
    </>}
  </div>;
}

export function IntimacyResearch({ api, ...props }) {
  const [open, setOpen] = useState(false);
  const { answers } = props;
  return <section className="space-y-2 border-t border-[var(--line)] pt-3" aria-label="Questionnaire research">
    <h3 className="font-semibold text-sm text-[var(--ink)]">{answers ? "Contribute to questionnaire research?" : "Questionnaire research"}</h3>
    {answers && <p className="text-sm text-[var(--muted)]">Your summary is ready. Sharing this visit with Imladri for research is optional.</p>}
    <button type="button" aria-expanded={open} className={buttonClass} onClick={() => setOpen(!open)}>
      {open ? "Hide research details" : answers ? "Review research contribution" : "Manage research contribution"}
    </button>
    {open && <ContributionForm api={api} {...props} />}
  </section>;
}
