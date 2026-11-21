import { useEffect, useRef, useState } from "react";
import { BrandLogo } from "@/components/BrandLogo";
import { ThemeToggle } from "@/lib/theme";
import { MAIN_CONSENT_VERSION as VERSION, MAIN_CONSENT_PERMISSIONS } from "@/lib/mainConsent";

const PAIR_VERSION = "couples-assessment-sharing-v1";
const TITLES = {
  questionnaire: "Questionnaire research", your_type: "Your Type research", tone: "Conversation tone research",
  member_csv: "Individual research CSV", couple_csv: "Current Couples research CSV", staff_history: "Staff answer-history access",
  trends: "Everyday group statistics", couples_statistics: "Couples group statistics", couples_return: "Future Couples return tracking",
};
const SCOPES = Object.keys(TITLES);
const PAIR_SCOPES = ["personality", "intimacy"];
const integer = value => Number.isSafeInteger(value) && value >= 0;
const hash = value => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const text = value => typeof value === "string" && value.trim().length > 0;
const renderChildren = children => typeof children === "function" ? children() : children;
function validSnapshot(value, owner) {
  if (value?.expected_member !== owner || value.notice_version !== VERSION || !hash(value.notice_sha256) || !text(value.notice)
    || !integer(value.revision) || typeof value.reviewed !== "boolean" || typeof value.welcome_complete !== "boolean"
    || value.welcome_complete && !value.reviewed || value.reviewed && (!value.revision || !text(value.reviewed_at) || Number.isNaN(Date.parse(value.reviewed_at)))
    || !(value.pair_id === null || text(value.pair_id)) || !value.scopes
    || Object.keys(value.scopes).length !== SCOPES.length) return false;
  if (!SCOPES.every(scope => {
    const row = value.scopes[scope], permission = row?.permission;
    return typeof row?.eligible === "boolean" && text(row.purpose) && permission?.version === MAIN_CONSENT_PERMISSIONS[scope]
      && integer(permission.revision) && typeof permission.enabled === "boolean" && hash(permission.state_sha256);
  })) return false;
  const pair = value.pair_sharing;
  return pair?.pair_id === value.pair_id && typeof pair.available === "boolean" && typeof pair.eligible === "boolean" && (!pair.eligible || pair.available)
    && typeof pair.reviewed === "boolean" && text(pair.partner_display_name) && pair.notice_version === PAIR_VERSION
    && text(pair.notice) && hash(pair.notice_sha256) && integer(pair.expected_revision)
    && (!pair.reviewed || pair.expected_revision > 0) && PAIR_SCOPES.every(scope => typeof pair[scope] === "boolean")
    && (pair.available ? text(pair.pair_id) && hash(pair.state_sha256) : pair.state_sha256 === null || hash(pair.state_sha256));
}
async function verifiedSnapshot(value, owner, expectedPair) {
  if (!validSnapshot(value, owner) || expectedPair !== undefined && value.pair_id !== expectedPair) throw Error("The data choices do not match this account or notice.");
  for (const [notice, expected] of [[value.notice, value.notice_sha256], [value.pair_sharing.notice, value.pair_sharing.notice_sha256]]) {
    const bytes = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(notice));
    const actual = Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, "0")).join("");
    if (actual !== expected) throw Error("The displayed notice could not be confirmed.");
  }
  return value;
}
const finished = value => value.reviewed && value.welcome_complete && (!value.pair_sharing.available || value.pair_sharing.reviewed);
const selectedFrom = value => Object.fromEntries(SCOPES.map(scope => [scope, value.scopes[scope].permission.enabled]));
const pairFrom = value => Object.fromEntries(PAIR_SCOPES.map(scope => [scope, value.pair_sharing[scope]]));
const safePermission = value => ({ version: value.version, revision: value.revision, enabled: value.enabled });

export default function ResearchFirstUse({ api, me, setMe, bypass = false, children }) {
  if (bypass || me?.role !== "member" || me.closing) return renderChildren(children);
  const access = me.workspace_access;
  const expectedPair = access?.version === "workspace-access-v1" ? access.mode === "couples" ? access.pair_id : null : undefined;
  const key = [me.user_id, expectedPair, me.main_consent?.notice_sha256, me.main_consent?.pair_id,
    me.main_consent?.pair_sharing?.state_sha256, me.main_consent?.pair_sharing?.reviewed,
    me.main_consent?.reviewed, me.main_consent?.welcome_complete].join(":");
  return <WelcomeChoice key={key} api={api} me={me} setMe={setMe} expectedPair={expectedPair}>{children}</WelcomeChoice>;
}

function WelcomeChoice({ api, me, setMe, expectedPair, children }) {
  const owner = me.user_id;
  const [record, setRecord] = useState(me.main_consent || null);
  const [phase, setPhase] = useState("checking");
  const [selected, setSelected] = useState({});
  const [pairSelected, setPairSelected] = useState({ personality: false, intimacy: false });
  const [customize, setCustomize] = useState(false);
  const [adult, setAdult] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(null);
  const alive = useRef(false);
  const initial = useRef(me.main_consent);

  function remember(value, update = true) {
    setRecord(value); setSelected(selectedFrom(value)); setPairSelected(pairFrom(value)); setAdult(false);
    setPhase(finished(value) ? "continue" : "prompt"); setError("");
    if (update) setMe(previous => previous?.user_id === owner ? {
      ...previous, main_consent: value,
      questionnaire_research: safePermission(value.scopes.questionnaire.permission),
      tone_research: safePermission(value.scopes.tone.permission),
      answer_history_permission: safePermission(value.scopes.staff_history.permission),
      everyday_trends_opt_in: value.scopes.trends.permission.enabled,
      research_exports: { ...previous.research_exports,
        member: { ...previous.research_exports?.member, ...safePermission(value.scopes.member_csv.permission) },
        couple: { ...previous.research_exports?.couple, ...safePermission(value.scopes.couple_csv.permission), pair_id: value.pair_id } },
      couples_preferences: { ...previous.couples_preferences,
        analytics_opt_in: value.scopes.couples_statistics.permission.enabled,
        review_return_opt_in: value.scopes.couples_return.permission.enabled },
    } : previous);
  }
  useEffect(() => {
    alive.current = true;
    const stalePair = expectedPair !== undefined && initial.current?.pair_id !== expectedPair;
    const controller = stalePair ? new AbortController() : null;
    if (controller) pending.current = controller;
    const value = stalePair ? api.get("/me/main-consent", { signal: controller.signal }).then(response => response.data) : Promise.resolve(initial.current);
    value.then(data => verifiedSnapshot(data, owner, expectedPair)).then(data => { if (alive.current && !controller?.signal.aborted) remember(data, stalePair); }).catch(() => {
      if (alive.current) { setPhase("uncertain"); setError("Your current data notice could not be confirmed. Check its saved status before using the app."); }
    }).finally(() => { if (pending.current === controller) pending.current = null; });
    return () => { alive.current = false; pending.current?.abort(); pending.current = null; };
    // This component is keyed to the authenticated owner and current pair.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [owner, expectedPair]);

  async function checkSaved() {
    if (pending.current) return;
    const controller = new AbortController(); pending.current = controller; setBusy(true); setError("");
    try {
      const { data } = await api.get("/me/main-consent", { signal: controller.signal });
      const value = await verifiedSnapshot(data, owner, expectedPair);
      if (alive.current && !controller.signal.aborted) remember(value);
    } catch {
      if (alive.current && !controller.signal.aborted) setError("Your saved choices are still unavailable. You can check again or manage your data in Settings.");
    } finally { if (pending.current === controller) pending.current = null; if (alive.current && !controller.signal.aborted) setBusy(false); }
  }

  async function save(action) {
    if (pending.current || phase !== "prompt" || !validSnapshot(record, owner)) return;
    const choices = Object.fromEntries(SCOPES.map(scope => [scope, action === "agree_all" ? record.scopes[scope].eligible : selected[scope]]));
    const pair = record.pair_sharing;
    const sharing = Object.fromEntries(PAIR_SCOPES.map(scope => [scope, action === "agree_all" ? pair.available && pair.eligible : pairSelected[scope]]));
    const newGrant = SCOPES.some(scope => choices[scope] && !record.scopes[scope].permission.enabled)
      || pair.available && PAIR_SCOPES.some(scope => sharing[scope] && !pair[scope]);
    if (newGrant && !adult) return;
    if (SCOPES.some(scope => choices[scope] && !record.scopes[scope].eligible && !record.scopes[scope].permission.enabled)
      || pair.available && !pair.eligible && PAIR_SCOPES.some(scope => sharing[scope] && !pair[scope])) return;
    const controller = new AbortController(); pending.current = controller; setBusy(true); setError("");
    try {
      const body = { expected_member: owner, notice_version: record.notice_version, notice_sha256: record.notice_sha256,
        revision: record.revision, expected_pair_id: record.pair_id, request_id: globalThis.crypto.randomUUID(), action, adult_confirmed: adult,
        choices: Object.fromEntries(SCOPES.map(scope => [scope, { ...record.scopes[scope].permission, enabled: choices[scope] }])) };
      const { data } = await api.patch("/me/main-consent", body, { signal: controller.signal });
      let value = await verifiedSnapshot(data, owner, expectedPair);
      if (!alive.current || controller.signal.aborted) return;
      if (value.pair_id !== record.pair_id || value.notice_sha256 !== record.notice_sha256 || !value.reviewed || !value.welcome_complete
        || value.revision !== record.revision + 1 || !SCOPES.every(scope => {
          const before = record.scopes[scope].permission, after = value.scopes[scope].permission;
          return after.version === before.version && after.enabled === choices[scope]
            && after.revision === before.revision + Number(before.enabled !== choices[scope]);
        })) throw Error("All saved choices were not confirmed.");
      const needsPairSave = pair.available && (!pair.reviewed || PAIR_SCOPES.some(scope => sharing[scope] !== pair[scope]));
      if (needsPairSave) {
        const fresh = await api.get("/me/main-consent", { signal: controller.signal });
        value = await verifiedSnapshot(fresh.data, owner, expectedPair);
        if (!alive.current || controller.signal.aborted) return;
        const currentPair = value.pair_sharing;
        if (value.pair_id !== record.pair_id || !value.welcome_complete || !currentPair.available
          || currentPair.state_sha256 !== pair.state_sha256 || currentPair.expected_revision !== pair.expected_revision
          || currentPair.notice_sha256 !== pair.notice_sha256) throw Error("The current pair changed before saving sharing.");
        const response = await api.put("/couples/compatibility/sharing", { pair_id: pair.pair_id, expected_member: owner,
          expected_revision: pair.expected_revision, notice_version: pair.notice_version,
          personality: sharing.personality, intimacy: sharing.intimacy }, { signal: controller.signal });
        if (!alive.current || controller.signal.aborted) return;
        const receipt = response.data;
        if (receipt?.pair_id !== pair.pair_id || receipt.notice_version !== pair.notice_version
          || receipt.sharing?.version !== pair.notice_version || receipt.sharing.revision !== pair.expected_revision + 1
          || !PAIR_SCOPES.every(scope => receipt.sharing[scope] === sharing[scope])) throw Error("Partner sharing was not confirmed.");
        const confirmed = await api.get("/me/main-consent", { signal: controller.signal });
        value = await verifiedSnapshot(confirmed.data, owner, expectedPair);
        if (!alive.current || controller.signal.aborted) return;
        if (value.pair_id !== pair.pair_id || !value.pair_sharing.reviewed
          || value.pair_sharing.expected_revision !== pair.expected_revision + 1
          || !PAIR_SCOPES.every(scope => value.pair_sharing[scope] === sharing[scope])) throw Error("The current pair receipt changed.");
      }
      remember(value);
    } catch {
      if (alive.current && !controller.signal.aborted) { setPhase("uncertain"); setError("Your complete choices could not be confirmed. Some choices may already be saved. Check their saved status; nothing will be resent automatically."); }
    } finally { if (pending.current === controller) pending.current = null; if (alive.current && !controller.signal.aborted) setBusy(false); }
  }

  if (phase === "continue") return renderChildren(children);
  const pair = record?.pair_sharing;
  const needsAdultForAll = validSnapshot(record, owner) && (SCOPES.some(scope => record.scopes[scope].eligible && !record.scopes[scope].permission.enabled)
    || pair.available && pair.eligible && PAIR_SCOPES.some(scope => !pair[scope]));
  const needsAdultForSelected = validSnapshot(record, owner) && (SCOPES.some(scope => selected[scope] && !record.scopes[scope].permission.enabled)
    || pair.available && PAIR_SCOPES.some(scope => pairSelected[scope] && !pair[scope]));
  return <main className="min-h-screen bg-[var(--bg)] text-[var(--ink)] px-4 py-5 sm:px-8 sm:py-8" data-testid="research-welcome">
    <div className="mx-auto max-w-5xl space-y-5 sm:space-y-6">
      <header className="flex items-center justify-between gap-4"><BrandLogo /><ThemeToggle /></header>
      <section aria-labelledby="research-welcome-title" className="space-y-4">
        <div><p className="text-xs uppercase tracking-wider text-[var(--muted)]">Welcome to Imladri</p>
          <h1 id="research-welcome-title" className="mt-2 font-display font-black uppercase text-3xl sm:text-5xl">One place for your data choices</h1></div>
        <div className="card p-4 sm:p-5 space-y-2 text-sm leading-relaxed">
          <h2 className="font-semibold">Save your progress. Make your advice more relevant.</h2>
          <p>Your profile, personality and intimacy answers, Your Type choices, conversations and unfinished answers save as you go. Relevant saved context helps personalize the advice you request.</p>
          <p>Review the data use notice and optional purposes below before entering the app. You can agree to all available choices or customize them here. Optional refusal does not limit unrelated app features.</p>
        </div>
      </section>
      {phase === "checking" ? <p role="status">Checking your current data notice…</p>
        : phase === "uncertain" ? <section className="card p-4 sm:p-5 space-y-4" aria-label="Check saved data choices">
          <p role="alert" className="text-sm">{error || "Your complete saved choices could not be confirmed."}</p>
          <button type="button" className="btn-ghost px-5 py-3 text-sm disabled:opacity-50" disabled={busy} onClick={checkSaved}>Check saved status</button>
        </section> : <>
          <section className="card p-4 sm:p-5 text-sm leading-relaxed space-y-3" aria-label="Data use notice">
            <h2 className="font-display font-bold uppercase text-xl">What you are agreeing to</h2>
            <p className="whitespace-pre-line" data-testid="main-consent-notice">{record.notice}</p>
          </section>
          <section aria-label="Optional data purposes" className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {SCOPES.map(scope => {
              const row = record.scopes[scope], enabled = row.permission.enabled;
              return <article key={scope} className="card p-4 space-y-2 text-sm leading-relaxed" data-testid={"main-consent-purpose-" + scope}>
                <h2 className="font-semibold">{TITLES[scope]}</h2><p>{row.purpose}</p>
                {!row.eligible && <p className="font-semibold text-[var(--muted)]">Not available now. {row.unavailable_reason || "Eligibility is required before this choice can be enabled."}</p>}
                {customize ? <label className="flex items-start gap-3 rounded-xl border border-[var(--line)] p-3 font-semibold">
                  <input type="checkbox" className="mt-1 shrink-0" checked={selected[scope] === true}
                    disabled={busy || !row.eligible && !selected[scope]} data-testid={"main-consent-choice-" + scope}
                    onChange={event => { setSelected(previous => ({ ...previous, [scope]: event.target.checked })); setAdult(false); }} />
                  <span>Allow {TITLES[scope].toLowerCase()}</span>
                </label> : <p className="text-xs text-[var(--muted)]">{enabled ? "Your existing saved choice is on." : "No new permission has been enabled."}</p>}
              </article>;
            })}
          </section>
          <section className="card p-4 sm:p-5 space-y-3 text-sm leading-relaxed" aria-label="Current partner assessment sharing">
            <h2 className="font-display font-bold uppercase text-xl">Your current partner</h2>
            {pair.available ? <>
              <p>These choices apply only to {pair.partner_display_name}. Each partner decides independently. A new pair needs a new decision.</p>
              <p data-testid="main-consent-pair-notice">{pair.notice}</p>
              {!pair.eligible && <p className="font-semibold">{pair.unavailable_reason} You can still decline or withdraw sharing.</p>}
              {customize ? <div className="grid gap-3 sm:grid-cols-2">{PAIR_SCOPES.map(scope => <label key={scope} className="flex items-start gap-3 rounded-xl border border-[var(--line)] p-3 font-semibold">
                <input type="checkbox" className="mt-1 shrink-0" checked={pairSelected[scope]} disabled={busy || !pair.eligible && !pairSelected[scope]}
                  data-testid={"main-consent-pair-" + scope} onChange={event => { setPairSelected(previous => ({ ...previous, [scope]: event.target.checked })); setAdult(false); }} />
                <span>Share my {scope === "intimacy" ? "sex & intimacy" : "personality & communication"} assessments with my current partner</span>
              </label>)}</div> : <p>Agreeing to all also shares both assessment categories with this current partner when available. Your Type and private notes are excluded.</p>}
            </> : <p>{pair.unavailable_reason || "Partner sharing is not available now."} This screen does not grant access to a future partner.</p>}
          </section>
          <section className="space-y-4 text-sm">
            {(needsAdultForAll || needsAdultForSelected) && <label className="flex items-start gap-3 rounded-xl border border-[var(--line)] p-3 font-semibold">
              <input type="checkbox" className="mt-1 shrink-0" checked={adult} disabled={busy} onChange={event => setAdult(event.target.checked)} data-testid="main-consent-adult" />
              <span>I am 18 or older and explicitly agree to the optional data uses I choose on this screen.</span>
            </label>}
            <p>You can change these choices, stop partner sharing, export or delete data in Settings. Earlier staff downloads may still exist. Purchases, notifications and one-time feedback submissions require their own actions.</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <button type="button" className="btn-ghost px-5 py-3 font-semibold disabled:opacity-50" disabled={busy || needsAdultForAll && !adult} onClick={() => save("agree_all")} data-testid="main-consent-agree-all">{busy ? "Saving choices…" : "Agree to all and continue"}</button>
              <button type="button" className="btn-ghost px-5 py-3 font-semibold disabled:opacity-50" disabled={busy} onClick={() => { setCustomize(true); setAdult(false); }} data-testid="main-consent-customize">Customize choices</button>
              {customize && <>
                <button type="button" className="btn-ghost px-5 py-3 font-semibold disabled:opacity-50" disabled={busy || needsAdultForSelected && !adult} onClick={() => save("save_choices")} data-testid="main-consent-save">Save selected choices and continue</button>
                <button type="button" className="btn-ghost px-5 py-3 font-semibold disabled:opacity-50" disabled={busy} onClick={() => { setSelected(Object.fromEntries(SCOPES.map(scope => [scope, false]))); setPairSelected({ personality: false, intimacy: false }); setAdult(false); }} data-testid="main-consent-decline-all">Turn off all optional choices</button>
              </>}
            </div>
            <p className="text-xs text-[var(--muted)]">Agree to all enables only currently available purposes. Unavailable purposes stay off; no future partner receives permission. Change or withdraw permissions in Settings → Data &amp; privacy at any time.</p>
          </section>
        </>}
      <footer className="border-t border-[var(--line)] pt-4 text-xs text-[var(--muted)]"><a className="underline" href="/app/settings#data-permissions">Manage data choices in Settings</a> · <a className="underline" href="/privacy" target="_blank" rel="noreferrer">Privacy details</a></footer>
    </div>
  </main>;
}
