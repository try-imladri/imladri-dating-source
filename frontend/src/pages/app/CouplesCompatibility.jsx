import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { BASE, Empty, Heading, Panel, primary, quiet, secondary } from "./CouplesCommon";

const SCOPES = ["personality", "intimacy"];
const SHARING_VERSION = "couples-assessment-sharing-v1";
const TITLES = { personality: "Personality & communication", intimacy: "Sex & intimacy" };
const KINDS = new Set(["similar", "difference", "shared_interest", "shared_boundary", "boundary_difference", "conditions"]);
const LABELS = { similar: "A shared preference", difference: "A difference to discuss", shared_interest: "A shared interest",
  shared_boundary: "A shared boundary", boundary_difference: "A boundary to respect", conditions: "Talk about the conditions" };
const integer = value => Number.isSafeInteger(value) && value >= 0;

function validView(value, pair) {
  if (value?.version !== "couples-compatibility-v1" || value.pair_id !== pair
    || value.sharing?.version !== SHARING_VERSION || !integer(value.sharing.revision)
    || value.notice_version !== SHARING_VERSION || typeof value.notice !== "string" || !value.notice.trim()
    || typeof value.context_signature !== "string" || !Array.isArray(value.notes) || value.notes.some(note => typeof note !== "string")) return false;
  return SCOPES.every(scope => {
    const domain = value.domains?.[scope];
    if (typeof value.sharing[scope] !== "boolean" || typeof value.partner_sharing?.[scope] !== "boolean"
      || !["awaiting_sharing", "insufficient_answers", "ready"].includes(domain?.status)
      || !integer(domain.compared_questions) || !Array.isArray(domain.items) || !Array.isArray(domain.notes)
      || domain.notes.some(note => typeof note !== "string")) return false;
    const joint = value.sharing[scope] && value.partner_sharing[scope];
    if (!joint && (domain.status !== "awaiting_sharing" || domain.items.length || domain.compared_questions)) return false;
    return domain.items.every(item => ["id", "title", "me", "partner", "summary", "suggestion"].every(key => typeof item?.[key] === "string")
      && KINDS.has(item.kind) && integer(item.evidence?.me) && integer(item.evidence?.partner));
  });
}

function Comparison({ scope, view, partner }) {
  const domain = view.domains[scope];
  const joint = view.sharing[scope] && view.partner_sharing[scope];
  return <Panel title={TITLES[scope]} id={`compatibility-${scope}`}>
    {!joint ? <>
      <p className={quiet}>{!view.sharing[scope]
        ? "Your answers in this category are private. You can change sharing in Settings."
        : `${partner} hasn’t shared this category. Comparisons appear only after both of you choose to share.`}</p>
      <p className="text-xs text-[var(--muted)]">Each partner chooses independently. Sharing another category or joining a pair does not turn this one on.</p>
    </> : domain.status === "insufficient_answers" ? <p className={quiet}>Both of you allow this comparison. Save more answers in this category to find shared preferences and differences.</p>
      : <p className={quiet}>{domain.compared_questions} comparable {domain.compared_questions === 1 ? "response" : "responses"}. These are starting points for a conversation, not a prediction of your relationship.</p>}
    {joint && domain.status === "ready" && <div className="space-y-3" key={view.context_signature}>
      {domain.items.map(item => <article className="rounded-xl border border-[var(--line)] p-4 space-y-3" key={item.id} data-testid={`compatibility-item-${item.id}`}>
        <div><p className="text-xs font-semibold text-[var(--muted)]">{LABELS[item.kind]}</p>
          <h3 className="mt-1 font-display text-xl font-bold uppercase">{item.title}</h3></div>
        <dl className="grid gap-3 sm:grid-cols-2 text-sm">
          <div><dt className="text-xs text-[var(--muted)]">Your shared response</dt><dd className="mt-1 whitespace-pre-line break-words">{item.me}</dd></div>
          <div><dt className="text-xs text-[var(--muted)]">{partner}’s shared response</dt><dd className="mt-1 whitespace-pre-line break-words">{item.partner}</dd></div>
        </dl>
        <p className="text-sm">{item.summary}</p>
        <div className="border-t border-[var(--line)] pt-3"><h4 className="text-sm font-semibold">Try together</h4><p className="mt-1 text-sm">{item.suggestion}</p>
          {item.kind === "boundary_difference" && <p className="mt-2 text-sm font-semibold">Respect each person’s no. Choose only what both of you want.</p>}</div>
        <p className="text-xs text-[var(--muted)]">Responses used: you {item.evidence.me} · {partner} {item.evidence.partner}</p>
      </article>)}
    </div>}
    {joint && domain.notes.map((note, index) => <p key={index} className="text-xs text-[var(--muted)]">{note}</p>)}
    <Link className={secondary} to={BASE + (scope === "intimacy" ? "/intimacy" : "/personal?tab=basics")}>Review my {scope === "intimacy" ? "intimacy" : "personality"} answers</Link>
  </Panel>;
}

export function CouplesCompatibility() {
  const context = useOutletContext();
  return <AssessmentWorkspace {...context} />;
}

export function CouplesAssessmentSharing({ api, me }) {
  const owner = me?.user_id;
  const [saved, setSaved] = useState(null);
  const [failure, setFailure] = useState("");
  const [phase, setPhase] = useState("loading");
  const pending = useRef(null);
  const metadata = value => value?.expected_member === owner && value.pair_sharing?.pair_id === value.pair_id
    && typeof value.pair_sharing?.revocable === "boolean" && integer(value.pair_sharing.revocation_revision)
    && value.pair_sharing.notice_version === SHARING_VERSION;
  const load = useCallback(async () => {
    pending.current?.abort();
    const controller = new AbortController(); pending.current = controller;
    setFailure(""); setPhase("loading");
    try {
      const [workspace, consent] = await Promise.allSettled([
        api.get("/couples", { signal: controller.signal }), api.get("/me/main-consent", { signal: controller.signal }),
      ]);
      if (!controller.signal.aborted) {
        const value = consent.status === "fulfilled" && consent.value.data?.expected_member === owner ? consent.value.data : null;
        setSaved({ owner, workspace: workspace.status === "fulfilled" ? workspace.value.data : null, consent: value });
        setPhase("ready");
        if (workspace.status !== "fulfilled") setFailure("Full partner sharing settings are unavailable. Existing sharing can still be withdrawn below when its saved status is confirmed.");
      }
    } catch {
      if (!controller.signal.aborted) { setPhase("uncertain"); setFailure("Your current connection could not be confirmed. Check again before changing partner sharing."); }
    } finally { if (pending.current === controller) pending.current = null; }
  }, [api, owner]);
  useEffect(() => { setSaved(null); load(); return () => { pending.current?.abort(); pending.current = null; }; }, [load]);
  const workspace = saved?.owner === owner ? saved.workspace : null;
  const consent = saved?.owner === owner && metadata(saved.consent) ? saved.consent : null;
  const revoke = consent?.pair_sharing;
  async function withdraw() {
    if (pending.current || phase !== "ready" || !revoke?.revocable || !revoke.pair_id) return;
    const controller = new AbortController(); pending.current = controller; setPhase("saving"); setFailure("");
    try {
      const { data } = await api.post("/me/main-consent/partner-revocation", { expected_member: owner, pair_id: revoke.pair_id,
        expected_revision: revoke.revocation_revision, notice_version: SHARING_VERSION }, { signal: controller.signal });
      if (!metadata(data) || data.pair_id !== revoke.pair_id || data.pair_sharing.revocable
        || data.pair_sharing.revocation_revision !== revoke.revocation_revision + 1) throw Error("Unconfirmed withdrawal");
      if (!controller.signal.aborted) await load();
    } catch {
      if (!controller.signal.aborted) { setPhase("uncertain"); setFailure("Withdrawal could not be confirmed. Check saved sharing; nothing will be resent automatically."); }
    } finally { if (pending.current === controller) pending.current = null; }
  }
  return <section id="partner-assessment-sharing" className="scroll-mt-5 space-y-3 text-sm" aria-label="Partner assessment sharing">
    <h3 className="font-semibold text-[var(--ink)]">Partner assessment sharing</h3>
    {phase === "loading" && <p role="status">Loading current partner sharing…</p>}
    {phase === "saving" && <p role="status">Turning off assessment sharing…</p>}
    {revoke?.revocable && phase === "ready" && <div className="space-y-2 rounded-xl border border-[var(--line)] p-3">
      <p>Turn off both personality and sex &amp; intimacy assessment sharing with your current partner. Your own saved answers are kept. This is available even if full comparisons are unavailable.</p>
      <button type="button" className={secondary} onClick={withdraw} data-testid="settings-revoke-assessment-sharing">Turn off all assessment sharing</button>
    </div>}
    {consent && !revoke.revocable && phase === "ready" && <p role="status">Assessment sharing is off for your current connection.</p>}
    {failure && <div className="space-y-2"><p role="alert">{failure}</p><button type="button" className="underline" onClick={load}>Check current connection</button></div>}
    {workspace && phase === "ready" && (workspace.underage || !workspace.adult
      ? <p>Assessment sharing requires both partners to be adults. Your private answers stay private.</p>
      : <AssessmentWorkspace key={owner + ":" + (workspace.pair_id || "none")} api={api} me={me} workspace={workspace} controls onSharingSaved={value => {
        setSaved(previous => previous?.owner === owner && previous.consent?.pair_id === value.pair_id ? { ...previous,
          consent: { ...previous.consent, pair_sharing: { ...previous.consent.pair_sharing,
            expected_revision: value.sharing.revision, revocation_revision: value.sharing.revision,
            personality: value.sharing.personality, intimacy: value.sharing.intimacy,
            revocable: value.sharing.personality || value.sharing.intimacy } } } : previous);
      }} />)}
  </section>;
}

function AssessmentWorkspace({ api, me, workspace, controls = false, onSharingSaved }) {
  const owner = me?.user_id;
  const pair = workspace?.state === "active" ? workspace.pair_id : null;
  const [saved, setSaved] = useState(null);
  const [selected, setSelected] = useState({ personality: false, intimacy: false });
  const [phase, setPhase] = useState("loading");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const current = useRef(null);
  const generation = useRef(0);
  const pending = useRef(null);
  const writing = useRef(null);
  const live = useRef(false);
  const view = saved?.owner === owner && saved?.pair === pair ? saved.data : null;
  const partner = workspace?.partner_name || "Your partner";

  const accept = useCallback((value, preserveEdits = false) => {
    if (!validView(value, pair)) throw Error("The saved comparison does not match this pair or its sharing choices.");
    const previous = current.current;
    current.current = { owner, pair, data: value };
    setSaved(current.current);
    if (!preserveEdits || previous?.owner !== owner || previous?.pair !== pair || previous.data.sharing.revision !== value.sharing.revision)
      setSelected({ personality: value.sharing.personality, intimacy: value.sharing.intimacy });
    setPhase("ready"); setError("");
  }, [owner, pair]);

  const load = useCallback(async (preserveEdits = true) => {
    if (!owner || !pair || writing.current) return;
    pending.current?.abort();
    const controller = new AbortController(); pending.current = controller;
    const id = ++generation.current;
    try {
      const { data } = await api.get("/couples/compatibility", { signal: controller.signal });
      if (live.current && !controller.signal.aborted && id === generation.current) accept(data, preserveEdits);
    } catch {
      if (live.current && !controller.signal.aborted && id === generation.current) {
        setPhase("uncertain"); setError("The saved comparison and sharing choices couldn’t be confirmed. Check their current status before making another change.");
      }
    } finally {
      if (pending.current === controller) pending.current = null;
    }
  }, [api, owner, pair, accept]);

  useEffect(() => {
    live.current = true; current.current = null; writing.current = null;
    setSaved(null); setSelected({ personality: false, intimacy: false }); setPhase("loading"); setError(""); setNotice("");
    load(false);
    const visible = () => { if (document.visibilityState === "visible") load(); };
    const timer = setInterval(visible, 20000);
    window.addEventListener("focus", visible); document.addEventListener("visibilitychange", visible);
    return () => {
      live.current = false; generation.current += 1; pending.current?.abort(); pending.current = null;
      clearInterval(timer); window.removeEventListener("focus", visible); document.removeEventListener("visibilitychange", visible);
    };
  }, [load]);

  async function save() {
    if (writing.current || phase !== "ready" || !view || !pair || !owner) return;
    generation.current += 1; pending.current?.abort();
    const controller = new AbortController(); pending.current = controller;
    writing.current = controller;
    const id = generation.current;
    setPhase("saving"); setError(""); setNotice("");
    try {
      const { data } = await api.put("/couples/compatibility/sharing", { pair_id: pair, expected_member: owner,
        expected_revision: view.sharing.revision, notice_version: view.notice_version,
        personality: selected.personality, intimacy: selected.intimacy }, { signal: controller.signal });
      if (live.current && !controller.signal.aborted && generation.current === id) {
        if (data?.sharing?.revision !== view.sharing.revision + 1 || !SCOPES.every(scope => data.sharing[scope] === selected[scope]))
          throw Error("The requested sharing choices were not confirmed.");
        accept(data); setNotice("Sharing choices saved."); onSharingSaved?.(data);
      }
    } catch (failure) {
      if (live.current && !controller.signal.aborted && generation.current === id) {
        setPhase("uncertain");
        setError(failure?.response?.status === 409
          ? "These sharing choices changed elsewhere. Check the latest saved choices before deciding again."
          : "Your sharing change couldn’t be confirmed. Check the saved choices before trying again. Nothing will be sent automatically.");
      }
    } finally {
      if (pending.current === controller) pending.current = null;
      if (writing.current === controller) writing.current = null;
    }
  }

  if (!pair) return <Empty title="Connect before comparing" text="Compatibility is available after both accounts confirm the pair. Your assessment answers remain private."
    to={BASE + "/partner"} action="Partner & settings" />;
  const ready = view && phase === "ready";
  const changed = view && SCOPES.some(scope => selected[scope] !== view.sharing[scope]);
  return <div className="max-w-4xl space-y-5" data-testid={controls ? "couples-sharing-settings" : "couples-compatibility"}>
    {!controls && <Heading note="Understand shared preferences, talk through differences and respect each other’s boundaries.">Compatibility</Heading>}
    <Panel title={controls ? "Choose what your partner can see" : "Your sharing status"} id="compatibility-sharing">
      <p className={quiet}>Your assessments start private. Each category requires both partners’ permission before comparisons appear.</p>
      {view && <p className={quiet} data-testid="compatibility-sharing-notice">{view.notice}</p>}
      <p className="text-xs text-[var(--muted)]">Private notes, written answers, demographics and Your Type are excluded. This does not change your optional research choices in <Link className="underline" to="/app/settings#questionnaire-research">Settings</Link>.</p>
      {phase === "loading" && <p role="status" className={quiet}>Loading your saved sharing choices…</p>}
      {controls && view && <fieldset disabled={!ready} className="space-y-3">
        <legend className="sr-only">Share my own assessment categories</legend>
        {SCOPES.map(scope => <label key={scope} className="flex items-start gap-3 rounded-xl border border-[var(--line)] p-3 text-sm">
          <input type="checkbox" checked={selected[scope]} onChange={event => setSelected(value => ({ ...value, [scope]: event.target.checked }))}
            className="mt-1 h-4 w-4 accent-[var(--ink)]" data-testid={`compatibility-share-${scope}`} />
          <span><span className="block font-semibold">Share my {scope === "intimacy" ? "sex & intimacy" : "personality"} answers and results</span>
            <span className="mt-1 block text-xs text-[var(--muted)]">{view.partner_sharing[scope] ? `${partner} has shared this category.` : `${partner} has not shared this category.`}</span></span>
        </label>)}
      </fieldset>}
      {controls ? <button type="button" className={primary} disabled={!ready || !changed} onClick={save} data-testid="compatibility-save-sharing">{phase === "saving" ? "Saving…" : "Save sharing choices"}</button>
        : <>
          {view && <dl className="grid gap-3 text-sm sm:grid-cols-2">{SCOPES.map(scope => <div key={scope} className="rounded-xl border border-[var(--line)] p-3" data-testid={`compatibility-status-${scope}`}>
            <dt className="font-semibold">{TITLES[scope]}</dt><dd className="mt-1">You: {view.sharing[scope] ? "sharing" : "private"} · {partner}: {view.partner_sharing[scope] ? "sharing" : "private"}</dd>
          </div>)}</dl>}
          <Link className={secondary} to="/app/settings#partner-assessment-sharing" data-testid="compatibility-manage-sharing">Manage partner sharing in Settings</Link>
        </>}
      {notice && <p role="status" className="text-sm">{notice}</p>}
      {error && <div role="alert" className="space-y-2 text-sm"><p>{error}</p>
        <button type="button" className="underline" onClick={() => load(false)} data-testid="compatibility-check-saved">Check saved choices</button></div>}
    </Panel>
    {!controls && ready && <>
      {SCOPES.map(scope => <Comparison scope={scope} view={view} partner={partner} key={scope} />)}
      {view.notes.map((note, index) => <p key={index} className={quiet}>{note}</p>)}
    </>}
  </div>;
}
