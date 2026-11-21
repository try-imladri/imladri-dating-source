import { useCallback, useEffect, useRef, useState } from "react";
import { consumeEntryRead } from "@/lib/entryRead";
import { ArrowRight, Check, Pause, Play, RotateCcw, X } from "lucide-react";
import { errMsg } from "@/lib/api";
import { waitForProfileWrites } from "@/lib/profileWriteBarrier";
import { YourTypeQueue } from "./yourTypeQueue";
import { pendingStorageAvailable, readPending, storePending } from "./yourTypeStorage";
import Portrait from "./YourTypePortrait";
import "./YourType.css";
export { clearYourTypePendingExcept } from "./yourTypeStorage";

const RESEARCH_NOTICE = "your-type-sensitive-research-v2";
const audienceLabel = value => value === "both" ? "women and men" : value;
export function preferenceAudience(sexuality, gender) {
  if (sexuality === "bi") return "both";
  if (sexuality === "lesbian") return "women";
  if (!["Man", "Woman"].includes(gender)) return null;
  if (sexuality === "straight") return gender === "Man" ? "women" : "men";
  if (sexuality === "gay") return gender === "Man" ? "men" : "women";
  return null;
}
const date = value => new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
const percent = n => `${Math.round(100 * n)}%`;
const AUDIENCES = [["women", "Women"], ["men", "Men"]];
const PROFILE_GENDERS = ["Woman", "Man", "Non-binary", "Another gender", "Prefer not to say"];
const ATTEMPTS_PER_PAGE = 4;
const STATED_VERSION = "your-type-stated-preferences-v1";
const RANGES = [["age_min", "Minimum age", 18, 100], ["age_max", "Maximum age", 18, 100],
  ["height_min_cm", "Minimum height (cm)", 120, 230], ["height_max_cm", "Maximum height (cm)", 120, 230]];
const preferenceDraft = value => Object.fromEntries(AUDIENCES.map(([audience]) => [audience,
  Object.fromEntries(RANGES.map(([key]) => [key, value?.[audience]?.[key] == null ? "" : String(value[audience][key])]))]));

function statedRanges(draft) {
  return Object.fromEntries(AUDIENCES.map(([audience, label]) => {
    const values = Object.fromEntries(RANGES.map(([key, title, min, max]) => {
      const raw = draft[audience][key].trim();
      if (!raw) return [key, null];
      if (!/^\d+$/.test(raw) || !Number.isSafeInteger(Number(raw)) || Number(raw) < min || Number(raw) > max)
        throw Error(`${label}: ${title.toLowerCase()} must be a whole number from ${min} to ${max}.`);
      return [key, Number(raw)];
    }));
    for (const [min, max, name] of [["age_min", "age_max", "age"], ["height_min_cm", "height_max_cm", "height"]]) {
      if ((values[min] === null) !== (values[max] === null)) throw Error(`${label}: enter both ends of the ${name} range, or leave both blank.`);
      if (values[min] !== null && values[min] > values[max]) throw Error(`${label}: minimum ${name} cannot exceed maximum ${name}.`);
    }
    return [audience, values];
  }));
}

function validStated(value) {
  if (value?.version !== STATED_VERSION || !Number.isSafeInteger(value.revision) || value.revision < 0
      || value.research_included !== false || !value.by_audience || Object.keys(value.by_audience).sort().join() !== "men,women") return false;
  if (!AUDIENCES.every(([audience]) => RANGES.every(([key]) => value.by_audience[audience]?.[key] === null
      || Number.isSafeInteger(value.by_audience[audience]?.[key])))) return false;
  try { statedRanges(preferenceDraft(value.by_audience)); return true; } catch { return false; }
}

export function StatedPreferences(props) {
  return <StatedPreferenceWorkspace key={props.owner} {...props} />;
}

function StatedPreferenceWorkspace({ api, owner }) {
  const [saved, setSaved] = useState(null);
  const [draft, setDraft] = useState(() => preferenceDraft());
  const [phase, setPhase] = useState("idle");
  const [message, setMessage] = useState("");
  const [blocked, setBlocked] = useState(false);
  const alive = useRef(false);
  const request = useRef(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; request.current?.abort(); }; }, []);
  async function load() {
    if (request.current) return;
    const controller = new AbortController(); request.current = controller; setPhase("loading"); setMessage("");
    try {
      const { data } = await api.get("/your-type/preferences", { signal: controller.signal });
      if (!validStated(data)) throw Error("Invalid preference response");
      if (alive.current && !controller.signal.aborted) { setSaved(data); setDraft(preferenceDraft(data.by_audience)); setPhase("ready"); setBlocked(false); }
    } catch { if (alive.current && !controller.signal.aborted) { setPhase("error"); setMessage("Your stated preferences could not load."); } }
    finally { if (request.current === controller) request.current = null; }
  }
  async function save(event) {
    event.preventDefault(); if (!saved || request.current || blocked) return;
    let byAudience;
    try { byAudience = statedRanges(draft); } catch (error) { setMessage(error.message); return; }
    const controller = new AbortController(); request.current = controller; setPhase("saving"); setMessage("");
    try {
      const { data } = await api.put("/your-type/preferences", { version: STATED_VERSION, revision: saved.revision,
        by_audience: byAudience, expected_member: owner }, { signal: controller.signal });
      if (!validStated(data) || data.revision !== saved.revision + 1
          || !AUDIENCES.every(([audience]) => RANGES.every(([key]) => data.by_audience[audience][key] === byAudience[audience][key]))) throw Error("Save not confirmed");
      if (alive.current && !controller.signal.aborted) { setSaved(data); setDraft(preferenceDraft(data.by_audience)); setPhase("ready"); setMessage("Your private stated preferences were saved."); }
    } catch (error) {
      if (alive.current && !controller.signal.aborted) { setPhase("ready"); setBlocked(true); setMessage(error?.response?.status === 409
        ? "Your preferences changed elsewhere. Reload the saved preferences before making another change."
        : "Saving was not confirmed. Reload the saved preferences before trying again."); }
    } finally { if (request.current === controller) request.current = null; }
  }
  const busy = phase === "loading" || phase === "saving";
  return <details className="card p-4 text-sm" data-testid="your-type-stated-preferences"
    onToggle={event => { if (event.currentTarget.open && phase === "idle") load(); }}>
    <summary className="font-semibold cursor-pointer">Your stated age & height preferences</summary>
    <div className="mt-3 space-y-3">
      <p>Record the age and height ranges you say you prefer, separately for women and men. These private preferences are not inferred from portraits and do not change your portrait model or enroll you in automatic matching.</p>
      <p className="text-xs text-[var(--muted)]">Leave a complete range blank if you do not want to state one. These ranges are separate from your own age and height and are excluded from the current optional research permissions.</p>
      {phase === "loading" && <p role="status">Loading stated preferences…</p>}
      {phase === "error" && <button type="button" className="underline" onClick={load}>Retry stated preferences</button>}
      {saved && <form onSubmit={save} className="space-y-4" noValidate>
        <div className="grid gap-4 md:grid-cols-2">{AUDIENCES.map(([audience, label]) => <fieldset key={audience} disabled={busy || blocked} className="space-y-3">
          <legend className="font-semibold">Preferences for {label.toLowerCase()}</legend>
          <div className="grid grid-cols-2 gap-3">{RANGES.map(([key, title, min, max]) => <label key={key} className="block text-xs">{label}: {title}
            <input className="mt-1 block w-full rounded-lg border border-[var(--line)] bg-[var(--surface)] p-2 text-sm" type="number" inputMode="numeric"
              min={min} max={max} step="1" value={draft[audience][key]} onChange={event => {
                setDraft(value => ({ ...value, [audience]: { ...value[audience], [key]: event.target.value } })); setMessage("");
              }} />
          </label>)}</div>
        </fieldset>)}</div>
        <button type="submit" className="btn-ghost px-4 py-2 disabled:opacity-50" disabled={busy || blocked}>{phase === "saving" ? "Saving preferences…" : "Save stated preferences"}</button>
        <button type="button" className="ml-3 underline" disabled={busy} onClick={load}>Reload saved preferences</button>
      </form>}
      {message && <p role={phase === "error" || blocked || !message.endsWith("were saved.") ? "alert" : "status"}>{message}</p>}
    </div>
  </details>;
}

export function ResearchChoice({ api, initial, onChange, owner }) {
  const [value, setValue] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function toggle(enabled) {
    setBusy(true); setError("");
    try {
      const { data } = await api.patch("/your-type/research", { version: value.version, expected_member: owner,
        revision: value.revision, adult_confirmed: true, enabled,
        ...(enabled ? { notice_version: RESEARCH_NOTICE } : {}) });
      setValue(data); onChange(data);
    } catch (e) { setError(errMsg(e)); }
    finally { setBusy(false); }
  }
  return <details className="card p-4 text-sm">
    <summary className="font-semibold cursor-pointer">Your Type data & research</summary>
    <div className="mt-3 space-y-3 text-[var(--muted)]">
      <p>Your choices and results are saved privately to your profile. Your partner cannot see them. They do not change your personality score or enroll you in matchmaking.</p>
      <p>Optional research includes your recorded choices and results, plus sexuality, viewer gender and portrait audience you explicitly reported when each new attempt began. Older attempts stay unknown; current profile edits never relabel them.</p>
      {value && <label className="flex items-start gap-2 text-[var(--ink)]"><input type="checkbox" checked={value.enabled} disabled={busy}
        onChange={e => toggle(e.target.checked)} className="mt-1" />
        <span>Include my Your Type choices in optional research. I am 18 or older and agree to comparisons of sexuality, viewer gender, image choices and visual features. This is optional and off by default.</span></label>}
      <p>Comparisons with age, height, gender and questionnaire answers also require your separate <a className="underline" href="/app/settings#questionnaire-research">questionnaire research permission</a>. Only attempts started after permission are eligible. Group results hide small samples.</p>
      <p>Staff downloads of coded action records require both this permission and your separate Individual record-export permission in Settings.</p>
      <p>Change this permission at any time. Export your saved data from <a href="/app/settings" className="underline">Settings</a>, or remove a Your Type attempt below.</p>
      {error && <p role="alert" className="text-[var(--ink)]">{error}</p>}
    </div>
  </details>;
}

function portraitColumns(grid) {
  const columns = getComputedStyle(grid).gridTemplateColumns;
  const repeated = /^repeat\((\d+),/.exec(columns);
  if (repeated) return Number(repeated[1]);
  const count = columns.split(/\s+/).filter(value => value && value !== "none").length;
  return count || 4;
}

function FaceCard({ face, index, disabled, onLike, onPass, observe }) {
  const holder = useRef(null);
  const [loaded, setLoaded] = useState(false);
  const [broken, setBroken] = useState(false);
  useEffect(() => loaded ? observe(holder.current, face.face_id) : undefined, [observe, face.face_id, loaded]);
  useEffect(() => {
    let active = true;
    setLoaded(false); setBroken(false);
    const image = new Image();
    image.onload = () => { if (active) setLoaded(true); };
    image.onerror = () => { if (active) setBroken(true); };
    image.src = face.url;
    return () => { active = false; image.onload = null; image.onerror = null; };
  }, [face.url]);
  const usable = loaded && !broken && !disabled;
  return <div ref={holder} data-face-id={face.face_id} className="your-type-face min-w-0 group relative overflow-hidden bg-[var(--surface)]">
    <button type="button" className="your-type-face-choice block w-full aspect-square focus-visible:outline focus-visible:outline-4 focus-visible:outline-offset-[-4px] focus-visible:outline-[var(--ink)] disabled:cursor-wait"
      data-portrait-choice aria-label={`Like ${face.image_kind === "full_body" ? "full-body image" : "face"} ${index + 1} of 12`} disabled={!usable} onClick={() => onLike(face.face_id)}
      onKeyDown={e => { if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
        e.preventDefault(); const buttons = [...holder.current.parentElement.querySelectorAll('[data-portrait-choice]')];
        const columns = portraitColumns(holder.current.parentElement);
        const step = e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : e.key === "ArrowUp" ? -columns : columns;
        buttons[(index + step + buttons.length) % buttons.length]?.focus(); }}>
      <Portrait face={face} label={`Fictional adult ${face.image_kind === "full_body" ? "full-body image" : "portrait"} ${index + 1}`} />
    </button>
    <button type="button" className="your-type-pass absolute top-1.5 right-1.5 rounded-full bg-black/65 text-white p-1.5 hover:bg-black focus-visible:outline focus-visible:outline-2"
      aria-label={`Pass ${face.image_kind === "full_body" ? "full-body image" : "face"} ${index + 1} of 12`} disabled={!usable} onClick={() => onPass(face.face_id)}><X className="w-3.5 h-3.5" /></button>
    {(!loaded || broken) && <span className="absolute inset-0 pointer-events-none flex items-center justify-center bg-[var(--surface)] text-xs px-2 text-center">{broken ? "Portrait could not load. Reload the saved attempt." : "Loading portrait…"}</span>}
  </div>;
}

export function YourTypeResults({ result, onContinue, busy }) {
  const title = useRef(null);
  useEffect(() => {
    title.current?.focus({preventScroll:true});
    title.current?.scrollIntoView?.({block:"start"});
  }, []);
  const profiles = AUDIENCES.filter(([audience]) => result.profiles_by_audience?.[audience]);
  return <div className="space-y-5" data-testid="your-type-results">
    <section className="card p-5 space-y-3">
      <div className="flex flex-wrap justify-between gap-3"><div><p className="text-xs uppercase tracking-wider text-[var(--muted)]">Private visual preferences</p>
        <h2 ref={title} tabIndex={-1} className="font-display uppercase font-black text-3xl outline-none scroll-mt-20">Your Type, so far</h2></div><button className="btn-lime px-4 py-2" disabled={busy} onClick={onContinue}>Keep refining <ArrowRight className="inline w-4 h-4 ml-1" /></button></div>
      <p className="text-sm text-[var(--muted)]">{result.interpretation}</p>
      <div className="flex flex-wrap gap-4 text-sm"><span><strong>{result.completed_crowds}</strong> crowds</span><span><strong>{result.likes}</strong> likes</span><span><strong>{result.explicit_passes}</strong> explicit passes</span><span><strong>{result.viewed_passes}</strong> viewed passes</span></div>
      {profiles.length > 1 && <p className="text-sm">Women’s and men’s profiles learn separately. Scores, feature weights and model checks describe choices within each audience; they do not compare your attraction across audiences.</p>}
      {result.legacy_combined_profile && <p className="text-sm" role="note">Historical combined profile: this earlier attempt used one model for both audiences. It is preserved as recorded and cannot be split into independent profiles. Start a fresh attempt for separate women’s and men’s results.</p>}
    </section>
    {profiles.length ? profiles.map(([audience, label]) => <AudienceProfile key={audience} result={result.profiles_by_audience[audience]} audience={audience} label={label} />)
      : <AudienceProfile result={result} />}
    <section className="card p-5 space-y-2"><h3 className="font-semibold">You compared with everyone</h3><p className="text-sm">Population comparison is not available yet.</p><p className="text-xs text-[var(--muted)]">We need enough separately opted-in adults and comparable image editions before showing reliable group patterns. No popularity, celebrity or matchmaking score is inferred.</p></section>
    <p className="text-xs text-[var(--muted)]">Saved attempt · {result.asset_edition} · model {result.model_edition} · revision {result.source_revision}</p>
  </div>;
}

function AudienceProfile({ result, audience, label }) {
  const [gallery, setGallery] = useState("rankings");
  const [limit, setLimit] = useState(24);
  const available = result.results_available !== false;
  const faces = gallery === "rankings" && !available ? [] : result[gallery] || [];
  const metric = result.prospective_pairwise;
  return <section className="space-y-4" aria-label={label ? `${label} preference profile` : "Portrait preference profile"}>
    <div className="card p-5 space-y-2">
      {label && <h3 className="font-display uppercase font-black text-2xl">{label}’s preferences</h3>}
      {label && <p className="text-sm">{result.likes || 0} likes · {result.explicit_passes || 0} explicit passes · {result.viewed_passes || 0} viewed passes in this audience.</p>}
      {!available && <p role="status" className="text-sm">Not enough choices for {label?.toLowerCase() || "this audience"} yet. Each audience needs at least {result.minimum_crowds || 20} completed crowds, {result.minimum_likes || 30} likes and {result.minimum_negative_examples || 3} explicit or viewed passes. Keep exploring only the portraits you genuinely favor.</p>}
      {result.signal === "insufficient_signal" && <p role="status" className="text-sm">We need more varied choices to distinguish your preferences. Continue and pass portraits you genuinely do not favor; there is no need to like a face in every crowd.</p>}
    </div>
    <section className="card p-5 space-y-4">
      <h4 className="font-display uppercase font-black text-2xl">{label ? `${label}’s portrait rankings` : "Portrait rankings"}</h4>
      <p className="text-sm text-[var(--muted)]">Personal model scores rank these original synthetic portraits. Scores are relative to this image bank; they are not attraction probabilities.</p>
      <div className="flex flex-wrap gap-2" role="group" aria-label={label ? `${label} portrait galleries` : "Portrait galleries"}>{[["rankings", "Predicted favorites"], ["picked_photos", "Your likes"], ["explicitly_passed_photos", "Your passes"]].map(([key, text]) =>
        <button key={key} aria-pressed={gallery === key} className={`${gallery === key ? "btn-lime" : "btn-ghost"} px-3 py-1.5 text-sm`} onClick={() => { setGallery(key); setLimit(24); }}>{text}</button>)}</div>
      {faces.length ? <ol className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-2">{faces.slice(0, limit).map((face, i) => <li key={face.id || face.face_id} className="min-w-0">
        <div className="aspect-[186/250] overflow-hidden rounded-lg"><Portrait face={face} /></div>
        <p className="text-xs mt-1">{gallery === "rankings" ? <><strong>#{i + 1}</strong> · score {Number(face.score).toFixed(2)}<span className="block text-[var(--muted)]">Uncertainty {Number(face.uncertainty).toFixed(2)}</span></> : <>Portrait {i + 1}</>}</p>
      </li>)}</ol> : <p className="text-sm text-[var(--muted)]">{gallery === "rankings" ? "Rankings will appear once there is enough varied evidence." : "No portraits in this gallery yet."}</p>}
      {faces.length > limit && <button className="btn-ghost px-4 py-2 text-sm" onClick={() => setLimit(n => n + 24)}>Show 24 more</button>}
    </section>
    <section className="card p-5 space-y-4">
      <h4 className="font-display uppercase font-black text-2xl">{label ? `Features in your ${label.toLowerCase()} portrait choices` : "Features in your choices"}</h4>
      <p className="text-sm text-[var(--muted)]">Ranked associations within this attempt, where there is enough observed variation to compare. Features overlap, and lighting and presentation can influence a choice. These patterns do not identify someone’s ethnicity, personality or suitability as a partner.</p>
      {(result.feature_preferences || []).map((group, i) => <details key={group.id} className="border-t border-[var(--line)] pt-3">
        <summary className="cursor-pointer flex justify-between gap-3 font-semibold text-sm"><span>{!available || group.applicable === false || group.coverage_available === false || group.variation_available === false ? "" : `${i + 1}. `}{group.label}</span><span className="text-[var(--muted)]">{group.applicable === false ? "Not applicable" : group.coverage_available === false ? "Not covered in this edition" : !available ? "Not enough choices" : group.variation_available === false ? "Not enough variation" : Number.isFinite(group.importance) ? `${percent(group.importance)} relative weight` : "Undetermined"}</span></summary>
        {group.applicable === false ? <p className="text-xs text-[var(--muted)] mt-2">This feature does not apply to {label?.toLowerCase() || audience || "these"} portraits in this catalog. It is not evidence of a preference.</p> : <>
        {Number.isSafeInteger(group.covered_portraits) && Number.isSafeInteger(group.total_portraits) && <p className="text-xs text-[var(--muted)] mt-2">Catalog coverage: {group.covered_portraits} of {group.total_portraits} portraits have this feature recorded.</p>}
        {group.coverage_available === false ? <p className="text-xs text-[var(--muted)] mt-2">This image edition has no usable coverage for this feature. Missing labels are not dislikes.</p> : <>
        {group.variation_available === false && <p className="text-xs text-[var(--muted)] mt-2">Your viewed examples do not yet provide enough alternatives to learn a preference for this feature.</p>}
        <ul className="mt-3 space-y-3">{(group.values || []).map(value => <li key={value.feature} className="text-sm">
          <div className="flex justify-between gap-2"><span>{value.label}</span><span>{available && group.variation_available !== false && value.sufficient_option_evidence ? value.direction === "more_often_favored" ? "More often favored" : value.direction === "less_often_favored" ? "Less often favored" : "Undetermined" : "Limited evidence"}</span></div>
          {available && group.variation_available !== false && Number.isFinite(value.association_strength) && <div className="h-1.5 bg-[var(--line)] rounded-full mt-1 overflow-hidden"><div className="h-full bg-[var(--ink)]" style={{ width: `${Math.min(100, Math.abs(value.association_strength) * 100)}%` }} /></div>}
          <p className="text-[11px] mt-1 text-[var(--muted)]">{value.option_examples} examples of this feature{available && group.variation_available !== false && Number.isFinite(value.association_strength) && <> · association {value.association_strength > 0 ? "+" : ""}{value.association_strength.toFixed(2)}</>}{available && Number.isFinite(value.standard_error) && <> · uncertainty proxy {value.standard_error.toFixed(2)}</>}</p>
        </li>)}</ul></>}
        </>}
      </details>)}
    </section>
      <div className="card p-5 space-y-2"><h4 className="font-semibold">{label ? `How the ${label.toLowerCase()} portrait model is doing` : "How the model is doing"}</h4><p className="text-sm">{metric?.pairs && Number.isFinite(metric.accuracy) ? <>{percent(metric.accuracy)} of {metric.pairs} selected-versus-passed pairs ranked correctly before their feedback, counting ties as half.</> : "No comparable pre-feedback pairs yet."}</p>
        <p className="text-xs text-[var(--muted)]">Pairs share faces and are not independent. This is a developing check on your choices, not validated accuracy or a compatibility score.</p></div>
    </section>;
}

export default function YourType(props) {
  return <YourTypeWorkspace key={`${props.owner}:${Boolean(props.underage)}`} {...props} />;
}

function YourTypeWorkspace({ api, owner, underage = false, profileGender = null, onPending = () => {} }) {
  const [bootstrap, setBootstrap] = useState(null);
  const authoritativeGender = bootstrap?.profile ? bootstrap.profile.gender : profileGender;
  const savedGender = PROFILE_GENDERS.includes(authoritativeGender) ? authoritativeGender : null;
  const [history, setHistory] = useState([]);
  const [historyPage, setHistoryPage] = useState(0);
  const [cursor, setCursor] = useState(null);
  const [state, setState] = useState(null);
  const [result, setResult] = useState(null);
  const sexuality = ["straight", "bi", "gay", "lesbian"].includes(bootstrap?.profile?.sexuality)
    ? bootstrap.profile.sexuality : "";
  const [audience, setAudience] = useState("");
  const automaticAudience = preferenceAudience(sexuality, savedGender);
  const selectedAudience = automaticAudience || audience;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [requiresProfileAge, setRequiresProfileAge] = useState(false);
  const [notice, setNotice] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [retakeOf, setRetakeOf] = useState(null);
  const [recovery, setRecovery] = useState(null);
  const [viewEpoch, setViewEpoch] = useState(0);
  const [temporaryOnly, setTemporaryOnly] = useState(false);
  const alive = useRef(false);
  const queue = useRef(null);
  const quizHeading = useRef(null);
  const sessionId = state?.session?.id;
  const showingResults = Boolean(result);
  const pendingCallback = useRef(onPending);
  pendingCallback.current = onPending;
  const seen = useRef(new Map());
  const visible = useRef(new Map());
  useEffect(() => {
    if (!sessionId || showingResults) return;
    const frame = requestAnimationFrame(() => {
      if (!alive.current) return;
      quizHeading.current?.focus({preventScroll:true});
      quizHeading.current?.scrollIntoView?.({block:"start"});
    });
    return () => cancelAnimationFrame(frame);
  }, [sessionId, showingResults]);

  const observe = useCallback((node, id) => {
    if (!node) return;
    // Called only after the portrait loaded. A later issuance of the same face
    // begins fresh dwell evidence instead of inheriting a previous crowd's view.
    seen.current.delete(id); visible.current.delete(id);
    const enter = () => { if (document.visibilityState === "visible" && !visible.current.has(id)) visible.current.set(id, performance.now()); };
    const leave = () => {
      if (visible.current.has(id)) { const ms = performance.now() - visible.current.get(id); visible.current.delete(id); seen.current.set(id, (seen.current.get(id) || 0) + ms); }
    };
    if (!window.IntersectionObserver) {
      const check = () => {
        if (document.visibilityState !== "visible") { leave(); return; }
        const rect = node.getBoundingClientRect();
        const area = rect.width * rect.height;
        const width = Math.max(0, Math.min(rect.right, window.innerWidth) - Math.max(rect.left, 0));
        const height = Math.max(0, Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0));
        area > 0 && width * height / area >= .5 ? enter() : leave();
      };
      check(); window.addEventListener("scroll", check, true); window.addEventListener("resize", check);
      document.addEventListener("visibilitychange", check);
      return () => { leave(); window.removeEventListener("scroll", check, true); window.removeEventListener("resize", check); document.removeEventListener("visibilitychange", check); };
    }
    const observer = new IntersectionObserver(entries => { for (const entry of entries) entry.isIntersecting && entry.intersectionRatio >= .5 ? enter() : leave(); }, { threshold: .5 });
    observer.observe(node);
    const visibility = () => { if (document.visibilityState === "hidden") leave(); else { observer.unobserve(node); observer.observe(node); } };
    document.addEventListener("visibilitychange", visibility);
    return () => { leave(); observer.disconnect(); document.removeEventListener("visibilitychange", visibility); };
  }, []);
  function declaration(selectedId) {
    const now = performance.now();
    const cards = queue.current.snapshot().session.cards;
    const dwell = {};
    for (const face of cards) {
      const ms = (seen.current.get(face.face_id) || 0) + (visible.current.has(face.face_id) ? now - visible.current.get(face.face_id) : 0);
      if (ms >= 250) dwell[face.face_id] = Math.min(3_600_000, Math.floor(ms));
    }
    // A deliberate click declares its selected loaded portrait viewed even on
    // a quick tap; it never invents a minimum dwell duration.
    const viewed = Object.keys(dwell);
    if (selectedId && !viewed.includes(selectedId)) viewed.push(selectedId);
    return { viewed_face_ids: viewed, dwell_ms: dwell };
  }
  function attach(dto, restored) {
    queue.current?.close(); seen.current.clear(); visible.current.clear();
    setViewEpoch(n => n + 1);
    queue.current = new YourTypeQueue({ session: dto, restored,
      send: async (id, body) => (await api.post(`/your-type/sessions/${id}/actions${body.single ? "" : "/batch"}`, body.single || body)).data,
      save: payload => { if (!storePending(owner, payload ? { session_id: dto.id, payload } : null)) setTemporaryOnly(true); },
      changed: value => { if (alive.current) {
        setState(value); pendingCallback.current(Boolean(value.pending));
        if (!value.pending) setHistory(rows => rows.map(row => row.id === value.session.id ? { ...row,
          revision: value.session.revision, status: value.session.status, updated_at: value.session.updated_at || row.updated_at,
          state: { ...row.state, completed_crowds: value.session.completed_crowds } } : row));
      } } });
  }
  async function refreshHistory(before) {
    const { data } = await api.get(`/your-type/history${before ? `?before=${encodeURIComponent(before)}` : ""}`);
    if (alive.current) {
      setHistory(prior => before ? [...prior, ...data.sessions] : data.sessions); setCursor(data.next);
      setHistoryPage(before ? Math.floor(history.length / ATTEMPTS_PER_PAGE) : 0);
    }
  }
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    const pending = readPending(owner);
    if (!pendingStorageAvailable(owner)) setTemporaryOnly(true);
    if (underage) { storePending(owner, null); return () => { alive.current = false; }; }
    setRecovery(pending?.invalid || pending?.start ? pending : null);
    setRetakeOf(pending?.start?.retake_of || null);
    if (pending?.start) pendingCallback.current(true);
    setError(""); setRequiresProfileAge(false);
    async function load() {
      await waitForProfileWrites(api, owner);
      if (!alive.current || controller.signal.aborted) return;
      const { data: boot } = await (consumeEntryRead(api, owner, "/your-type", controller.signal)
        || api.get("/your-type", { signal: controller.signal }));
      if (!alive.current || controller.signal.aborted) return;
      // Current servers return age eligibility, gender and history together.
      // Only an older server needs the two compatibility reads.
      const hasProfile = Object.prototype.hasOwnProperty.call(boot, "profile");
      const hasHistory = Object.prototype.hasOwnProperty.call(boot, "history_next");
      const [legacyProfile, legacyHistory] = await Promise.all([
        hasProfile ? null : api.get("/me/about", { signal: controller.signal }),
        hasHistory ? null : api.get("/your-type/history", { signal: controller.signal }),
      ]);
      if (!alive.current || controller.signal.aborted) return;
      let profile = boot.profile;
      if (!hasProfile) {
        const demographics = legacyProfile?.data?.about?.demographics;
        const age = demographics?.age;
        const adultEligible = age !== null && age !== undefined && age !== ""
          && Number.isInteger(Number(age)) && Number(age) >= 18 && Number(age) <= 120;
        profile = { adult_eligible: adultEligible, gender: demographics?.gender ?? null,
          sexuality: demographics?.sexuality ?? null };
      }
      if (profile?.adult_eligible !== true) {
        const failure = Error("Your Type is for adults. Review your saved profile age to continue.");
        failure.profileAge = true; throw failure;
      }
      const attempts = hasHistory ? { sessions: boot.sessions, next: boot.history_next } : legacyHistory?.data;
      if (!Array.isArray(boot.audiences) || !Array.isArray(attempts?.sessions)
          || !(attempts.next === null || typeof attempts.next === "string")) throw Error("Your Type could not load. Please retry.");
      setBootstrap({ ...boot, profile }); setHistory(attempts.sessions); setCursor(attempts.next);
      const recoveredIndex = attempts.sessions.findIndex(row => row.id === pending?.start?.retake_of);
      setHistoryPage(recoveredIndex < 0 ? 0 : Math.floor(recoveredIndex / ATTEMPTS_PER_PAGE));
      if (pending?.session_id) {
        const response = await api.get(`/your-type/sessions/${pending.session_id}`, { signal: controller.signal });
        if (!alive.current || controller.signal.aborted) return;
        try { attach(response.data, pending.payload); }
        catch (e) { setRecovery({ invalid: true }); throw e; }
        setNotice("Unsaved choices were recovered in this tab. Retry saving them to continue.");
      }
    }
    load().catch(e => { if (alive.current && !controller.signal.aborted) {
      setError(errMsg(e)); setRequiresProfileAge(Boolean(e.profileAge) || e?.response?.status === 403);
      if (pending?.session_id && [404, 410, 412].includes(e?.response?.status)) setRecovery({ invalid: true });
    } });
    return () => { alive.current = false; controller.abort(); queue.current?.close(); pendingCallback.current(false); };
    // The component is keyed by owner; a new load attempt replaces its queue.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, owner, underage, attempt]);
  useEffect(() => {
    if (!state?.pending && !recovery) return;
    const warn = e => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [state?.pending, recovery]);

  async function start(previousAttempt = retakeOf) {
    setBusy(true); setError(""); setNotice("");
    try {
      if (queue.current?.snapshot().pending) await queue.current.flush();
      if (recovery?.invalid) throw Error("Discard the unverifiable browser recovery buffer before starting a new attempt.");
      if (!recovery?.start && !selectedAudience) throw Error("Choose which portraits you want to rate.");
      const body = recovery?.start || { request_id: crypto.randomUUID(), expected_member: owner,
        audience: selectedAudience, use_saved_profile: true,
        ...(sexuality ? { sexuality, viewer_gender: savedGender } : {}),
        ...(previousAttempt ? { retake_of: previousAttempt } : {}) };
      setRecovery({ start: body }); pendingCallback.current(true);
      if (!storePending(owner, { start: body })) setTemporaryOnly(true);
      const { data } = await api.post("/your-type/sessions", body);
      if (!alive.current) return;
      storePending(owner, null); setRecovery(null); setRetakeOf(null); attach(data); setResult(null); await refreshHistory();
    } catch (e) { if (alive.current) setError(errMsg(e)); }
    finally { if (alive.current) setBusy(false); }
  }
  async function resume(id) {
    setBusy(true); setError(""); setNotice("");
    try {
      if (queue.current?.snapshot().pending) await queue.current.flush();
      const { data } = await api.get(`/your-type/sessions/${id}`);
      if (!alive.current) return;
      attach(data); setResult(null); setRetakeOf(null);
    } catch (e) { if (alive.current) setError(errMsg(e)); }
    finally { if (alive.current) setBusy(false); }
  }
  function beginRetake(id) {
    if (busy || recovery || queue.current?.snapshot().pending) return;
    const index = history.findIndex(row => row.id === id);
    if (index >= 0) setHistoryPage(Math.floor(index / ATTEMPTS_PER_PAGE));
    setRetakeOf(id); setAudience(""); setConfirmDelete(null); setError(""); setNotice("");
    queue.current?.close(); queue.current = null; setState(null); setResult(null);
  }
  function choice(kind, id) {
    try { if (queue.current.enqueue({ kind, face_id: id, ...declaration(id) })) setNotice(kind === "like" ? "Liked. A new portrait is in its place." : "Passed. A new portrait is in its place."); }
    catch (e) { setError(e.message); }
  }
  async function next() {
    try { queue.current.enqueue({ kind: "next_crowd", ...declaration() }); await queue.current.flush(); }
    catch { /* Queue shows the precise retry/conflict state. */ }
  }
  async function control(kind) {
    setBusy(true); setError("");
    try { await queue.current.control(kind); setNotice(kind === "undo" ? "Last action undone, including its effect on your results." : kind === "pause" ? "Attempt paused. You can return at any time." : "Attempt resumed."); }
    catch (e) { setError(errMsg(e)); }
    finally { if (alive.current) setBusy(false); }
  }
  async function retrySaving() {
    setBusy(true); setError("");
    try {
      if (state.conflict) {
        const { data } = await api.get(`/your-type/sessions/${session.id}`);
        queue.current.discardConflict(data);
      } else await queue.current.flush();
    } catch (e) {
      setError(errMsg(e));
      if ([404, 410, 412].includes(e?.response?.status)) setRecovery({ invalid: true });
    } finally { if (alive.current) setBusy(false); }
  }
  async function showResults() {
    setBusy(true); setError("");
    try {
      let current = await queue.current.flush();
      if (!current.results_available && current.likes >= current.required_likes && current.completed_crowds >= 19) {
        queue.current.enqueue({ kind: "next_crowd", ...declaration() }); current = await queue.current.flush();
      }
      if (current.status === "active") current = await queue.current.control("finish");
      const { data } = await api.get(`/your-type/sessions/${current.id}/results`);
      if (alive.current) { setResult(data); await refreshHistory(); }
    } catch (e) { if (alive.current) setError(errMsg(e)); }
    finally { if (alive.current) setBusy(false); }
  }
  async function continueResults() {
    setBusy(true); setError("");
    try {
      if (queue.current.session.status !== "active") await queue.current.control("resume");
      if (alive.current) setResult(null);
    } catch (e) { if (alive.current) setError(errMsg(e)); }
    finally { if (alive.current) setBusy(false); }
  }
  async function erase(row) {
    setBusy(true); setError("");
    try {
      if (queue.current?.snapshot().pending) await queue.current.flush();
      const { data } = await api.get(`/your-type/sessions/${row.id}`);
      await api.delete(`/your-type/sessions/${row.id}?expected_revision=${data.revision}`);
      if (!alive.current) return;
      if (queue.current?.session.id === row.id) { queue.current.close(); queue.current = null; setState(null); setResult(null); storePending(owner, null); }
      if (retakeOf === row.id) setRetakeOf(null);
      setConfirmDelete(null); setNotice("Attempt and its answer history removed."); await refreshHistory();
    } catch (e) { if (alive.current) setError(errMsg(e)); }
    finally { if (alive.current) setBusy(false); }
  }

  if (underage) return <section className="card p-5"><h2 className="font-display uppercase text-2xl font-black">Your Type is for adults</h2><p className="mt-2 text-sm">This section is available from age 18.</p></section>;
  if (!bootstrap) return <section className="card p-5" aria-label="Your Type loading">{error ? <p role="alert">{error} {requiresProfileAge && <a href="/app/personal?tab=basics" className="underline">Review your profile age</a>} <button className="underline" onClick={() => setAttempt(n => n + 1)}>Retry</button></p> : <p role="status">Loading Your Type…</p>}</section>;
  const session = state?.session;
  const blocked = busy || Boolean(recovery) || state?.conflict || Boolean(state?.error) || state?.advancing || (state?.pending || 0) >= 12;
  const canResults = session && (typeof session.results_available === "boolean" ? session.results_available
    : session.likes >= session.required_likes && session.completed_crowds >= 19);
  const historyPages = Math.max(1, Math.ceil(history.length / ATTEMPTS_PER_PAGE));
  const visibleHistory = history.slice(historyPage * ATTEMPTS_PER_PAGE, (historyPage + 1) * ATTEMPTS_PER_PAGE);
  const recoveryControls = recovery && <section className="rounded-xl border border-[var(--line)] p-4 space-y-3 text-sm" aria-label="Browser recovery">
      <p>{recovery.invalid ? "The browser recovery buffer could not be verified. Your saved server attempts are unchanged." : "The previous start request did not return a confirmed result. Recover that exact request before starting another attempt."}</p>
      {recovery.start && <button className="btn-lime px-3 py-2" disabled={busy} onClick={() => start()}>Recover previous start</button>}
      <button className="btn-ghost px-3 py-2" disabled={busy} onClick={() => {
        storePending(owner, null); setRecovery(null); setError(""); queue.current?.close(); queue.current = null;
        setState(null); setResult(null); pendingCallback.current(false);
        setNotice("Only this tab’s unsaved recovery buffer was discarded. Saved attempts are unchanged.");
        refreshHistory().catch(e => setError(errMsg(e)));
      }}>Discard browser buffer</button>
    </section>;
  const startControls = <>
    {automaticAudience ? <p className="text-sm" role="status">Portrait audience: <strong className="capitalize">{audienceLabel(automaticAudience)}</strong>. Based on your saved profile.</p> :
      <fieldset className="space-y-2"><legend className="font-semibold text-sm mb-2">Who would you like to rate?</legend>
        {sexuality && <p className="text-xs text-[var(--muted)]">Your saved gender does not determine one audience here. Choose explicitly; we do not infer it from sex or other profile answers.</p>}
        <div className="flex flex-wrap gap-2">{bootstrap.audiences.filter(item => item.id !== "both" || sexuality).map(item => <button type="button" key={item.id} aria-pressed={audience === item.id} disabled={busy || Boolean(recovery)} className={`${audience === item.id ? "btn-lime" : "btn-ghost"} px-4 py-2 text-sm capitalize`} onClick={() => setAudience(item.id)}>{audienceLabel(item.id)}</button>)}</div>
      </fieldset>}
    <p className="text-xs text-[var(--muted)]">Manage sexuality and gender in <a href="/app/personal?tab=basics#about-demographics" className="underline">Personal → About you</a>. Changes apply to new attempts; earlier attempts keep their original details.</p>
    <button className="btn-lime px-6 py-3 font-semibold disabled:opacity-50" disabled={busy || Boolean(recovery) || !selectedAudience} onClick={() => start()}>Start Your Type <ArrowRight className="inline w-4 h-4 ml-1" /></button>
  </>;
  const retakeControls = retakeOf && <section className="mt-3 rounded-xl border border-[var(--line)] p-4 space-y-3" aria-label="Fresh attempt controls">
    <h4 className="font-semibold">Start a fresh attempt</h4>
    <p className="text-xs text-[var(--muted)]">Your earlier choices stay saved in this attempt. A fresh attempt starts a separate profile.</p>
    {error && <p role="alert" className="text-sm">{error}</p>}
    {recoveryControls}
    {startControls}
    <div className="flex flex-wrap gap-3 items-center">
      <button className="btn-ghost px-4 py-2 text-sm disabled:opacity-50" disabled={busy || Boolean(recovery)} onClick={() => resume(retakeOf)}>Continue this attempt</button>
      <button className="underline text-sm disabled:opacity-50" disabled={busy || Boolean(recovery)} onClick={() => { setRetakeOf(null); setError(""); }}>Cancel</button>
    </div>
  </section>;
  return <div className="space-y-4" data-testid="your-type">
    {error && !retakeOf && <p role="alert" className="card p-4 text-sm">{error}</p>}
    {temporaryOnly && <p className="card p-4 text-sm" role="status">Browser storage is unavailable. Unsaved choices stay only in memory until the server confirms them. Keep this page open while saving.</p>}
    {!retakeOf && recoveryControls}
    {notice && <p className="sr-only" role="status">{notice}</p>}
    <div className={!session && !result ? "your-type-start-layout" : "space-y-4"}>
    {result ? <YourTypeResults result={result} busy={busy} onContinue={continueResults} /> : session ? <>
      <section className="card p-3 sm:p-5 your-type-crowd-shell" aria-label="Your Type portrait selection">
        <div className="your-type-crowd-layout">
        <div className="space-y-4 your-type-crowd-summary">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs text-[var(--muted)] uppercase tracking-wider">Rating {audienceLabel(session.audience)} · private</p>
          <h2 ref={quizHeading} tabIndex={-1} className="font-display uppercase text-2xl sm:text-3xl font-black outline-none scroll-mt-20">{session.audience_progress ? "Your Type is taking shape" : session.likes_remaining ? `${session.likes_remaining} more likes` : "Your Type is taking shape"}</h2></div>
          <span className="text-xs rounded-full border border-[var(--line)] px-3 py-1">No message credits used</span></div>
        <p className="text-sm text-[var(--muted)]">Tap any face that catches your eye. A new one takes its place. Choose none, one or several—there is no right number.</p>
        <div className="your-type-progress-grid grid grid-cols-2 gap-3 text-xs">
          <div><div className="flex justify-between mb-1"><span>Completed crowds</span><strong>{session.completed_crowds} / {session.next_milestone_crowds || 20}</strong></div><progress className="w-full h-2 accent-[var(--ink)]" value={session.completed_crowds} max={session.next_milestone_crowds || 20} /></div>
          {session.audience_progress ? <ul className="space-y-2" aria-label="Progress by portrait audience">{AUDIENCES.filter(([audience]) => session.audience_progress[audience]).map(([audience, label]) => {
            const progress = session.audience_progress[audience];
            return <li key={audience}><strong>{label}</strong>: {progress.completed_crowds || 0} crowds · {progress.committed_likes || 0} saved likes · {progress.negative_examples || 0} passes · {progress.results_available ? "Profile available" : "Needs 20 crowds, 30 likes and 3 passes"}</li>;
          })}</ul> : <div><div className="flex justify-between mb-1"><span>Likes for first results</span><strong>{session.likes} / {session.required_likes}</strong></div><progress className="w-full h-2 accent-[var(--ink)]" value={Math.min(session.likes, session.required_likes)} max={session.required_likes} /></div>}
        </div>
        <div className="flex flex-wrap gap-2 items-center">
          <button className="btn-ghost px-3 py-1.5 text-sm" disabled={blocked || !session.undo_available || state.pending > 0} onClick={() => control("undo")}><RotateCcw className="inline w-3.5 h-3.5 mr-1" />Undo</button>
          <button className="btn-ghost px-3 py-1.5 text-sm" disabled={blocked || state.pending > 0} onClick={() => control(session.status !== "active" ? "resume" : "pause")}>{session.status !== "active" ? <Play className="inline w-3.5 h-3.5 mr-1" /> : <Pause className="inline w-3.5 h-3.5 mr-1" />}{session.status !== "active" ? "Resume" : "Pause"}</button>
          <span className="text-xs text-[var(--muted)] ml-auto" role="status">{state.error ? "Choices need attention" : state.pending ? `Saving ${state.pending} choice${state.pending === 1 ? "" : "s"}…` : <><Check className="inline w-3 h-3 mr-1" />Saved · crowd {session.crowd}</>}</span>
        </div>
        {state.error && <div className="rounded-lg border border-[var(--line)] p-3 text-sm" role="alert"><p>{state.conflict ? "This attempt changed in another tab. Reload its saved state; unsaved selections will not be applied to different portraits." : "Your choices are kept in this tab while saving is interrupted. Retry the same request to avoid duplicate votes."}</p>
          <button className="underline mt-2" disabled={busy} onClick={retrySaving}>{state.conflict ? "Reload saved attempt" : "Retry saving"}</button></div>}
        </div>
        {session.status !== "active" ? <p className="py-8 text-center text-sm your-type-paused">{session.status === "finished" ? "Finished" : "Paused"}. Resume whenever you are ready.</p> : <div className="your-type-portrait-grid" role="group" aria-label="Twelve adult portraits. Tap to like; X to pass. Arrow keys move between faces.">
          {session.cards.map((face, i) => <FaceCard key={`${session.id}:${session.crowd}:${viewEpoch}:${face.face_id}`} face={face} index={i} disabled={blocked || session.current_crowd_choices >= session.max_choices_per_crowd || !session.replacement_queue.length} onLike={id => choice("like", id)} onPass={id => choice("pass", id)} observe={observe} />)}
        </div>}
        <div className="space-y-3 your-type-crowd-actions">
        <div className="flex flex-wrap justify-between gap-3 items-center">
          <div className="text-xs text-[var(--muted)] max-w-sm">Use Next Crowd when you are ready to move on. Only the remaining faces you viewed count as passes.</div>
          <button className="btn-lime w-full px-5 py-3 text-sm" disabled={blocked || session.status !== "active"} onClick={next}>Next Crowd <ArrowRight className="inline w-4 h-4 ml-1" /></button>
        </div>
        {session.completed_crowds >= 4 && session.likes < 2 && <p className="text-sm text-[var(--muted)]">Nothing catching your eye? You can keep exploring or start a separate attempt with another audience. Only choose portraits you actually favor.</p>}
        <button className="btn-ghost w-full py-2.5 text-sm" disabled={blocked || !canResults} onClick={showResults}>{session.results_available ? "View your results" : canResults ? "Save crowd & view results" : session.audience_progress ? "Keep rating: 30 likes and 3 passes per audience" : "Results open after 20 crowds and 30 likes"}</button>
        </div>
        </div>
      </section>
    </> : <section className="card p-5 space-y-4 your-type-discover" aria-labelledby="your-type-discover-heading">
      <div><p className="text-xs uppercase text-[var(--muted)] tracking-wider">See the patterns in your attraction</p><h2 id="your-type-discover-heading" className="font-display font-black uppercase text-3xl mt-1">Discover Your Type</h2><p className="text-sm text-[var(--muted)] mt-2">Choose the fictional adult faces that catch your eye. Build your private visual profile with 20 crowds, 30 likes and 3 passes per audience. Women’s and men’s results stay separate.</p></div>
      {retakeOf ? <p className="text-sm text-[var(--muted)]">Continue or start fresh using the controls below your selected attempt.</p> : startControls}
      <p className="text-xs text-[var(--muted)]">No message credits used. Retake immediately. Your partner cannot see your choices.</p>
    </section>}
    <section className="card p-5 space-y-3 your-type-history" aria-label="Your Type history">
      <div className="flex flex-wrap gap-3 items-center justify-between"><h3 className="font-display font-black uppercase text-2xl">Your attempts</h3>{session && <button className="btn-ghost px-3 py-1.5 text-sm" disabled={busy || Boolean(recovery) || state.pending > 0} onClick={() => beginRetake(session.id)}>Start a fresh attempt</button>}</div>
      <p className="text-xs text-[var(--muted)]">Earlier attempts stay separate so changes in your choices and image editions are clear. No waiting period.</p>
      {!history.length && <p className="text-sm">Your first saved attempt will appear here.</p>}
      {visibleHistory.map(row => <div key={row.id} data-attempt-id={row.id} className="border-t border-[var(--line)] py-3 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-2"><div className="min-w-0"><strong className="capitalize">{audienceLabel(row.audience)}</strong> · {row.state?.completed_crowds || 0} {(row.state?.completed_crowds || 0) === 1 ? "crowd" : "crowds"} · {row.status}<p className="text-xs text-[var(--muted)] break-words">{date(row.updated_at || row.created_at)} · {row.asset_edition}</p></div><div className="flex flex-wrap gap-3"><button className="underline" disabled={busy || Boolean(recovery) || Boolean(state?.pending)} onClick={() => resume(row.id)}>Open</button><button className="underline" disabled={busy || Boolean(recovery) || Boolean(state?.pending)} onClick={() => beginRetake(row.id)}>Restart</button><button className="underline" disabled={busy || Boolean(recovery) || Boolean(state?.pending)} onClick={() => setConfirmDelete(row.id)}>Remove</button></div></div>
        {retakeOf === row.id && retakeControls}
        {confirmDelete === row.id && <div className="mt-3 space-y-2"><p>Remove this attempt, its choices and its research history permanently?</p><button className="btn-ghost px-3 py-1.5 mr-2" disabled={busy} onClick={() => erase(row)}>Remove this attempt</button><button className="underline" disabled={busy} onClick={() => setConfirmDelete(null)}>Keep it</button></div>}
      </div>)}
      {retakeOf && !visibleHistory.some(row => row.id === retakeOf) && retakeControls}
      {historyPages > 1 && <nav aria-label="Attempt history pages" className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--line)] pt-3 text-sm">
        <button className="btn-ghost px-3 py-1.5 disabled:opacity-50" disabled={busy || historyPage === 0} onClick={() => { setConfirmDelete(null); setHistoryPage(page => page - 1); }}>Previous attempts</button>
        <span role="status">Page {historyPage + 1} of {historyPages}</span>
        <button className="btn-ghost px-3 py-1.5 disabled:opacity-50" disabled={busy || historyPage + 1 >= historyPages} onClick={() => { setConfirmDelete(null); setHistoryPage(page => page + 1); }}>Next attempts</button>
      </nav>}
      {cursor && historyPage + 1 === historyPages && <button className="underline text-sm" disabled={busy} onClick={() => refreshHistory(cursor).catch(e => setError(errMsg(e)))}>Load earlier attempts</button>}
    </section>
    </div>
  </div>;
}
