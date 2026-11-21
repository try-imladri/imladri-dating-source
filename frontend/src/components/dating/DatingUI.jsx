import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Heart, ShieldCheck, ImagePlus, X, Flag, Ban, CalendarDays } from "lucide-react";
import { datingPhotoPath, humanLabel } from "@/lib/dating";
import { useDating, useDatingAction } from "./DatingContext";

export function ActionFeedback({ action, success }) {
  return <>{action.error && <p role="alert" className="dating-alert">{action.error}</p>}{action.success && success && <p role="status" className="dating-success">{success}</p>}</>;
}

export function EmptyState({ icon: Icon = Heart, eyebrow, title, children, action }) {
  return <section className="dating-empty"><span className="dating-empty__icon"><Icon size={30} strokeWidth={1.4} /></span>{eyebrow && <p className="dating-kicker">{eyebrow}</p>}<h2>{title}</h2><div className="dating-empty__copy">{children}</div>{action}</section>;
}

export function PageHeading({ eyebrow, title, children, action }) {
  return <header className="dating-page-heading"><div>{eyebrow && <p className="dating-kicker">{eyebrow}</p>}<h1>{title}</h1>{children && <p className="dating-page-intro">{children}</p>}</div>{action}</header>;
}

export function Loading({ children = "Opening your dating space…" }) { return <p className="dating-loading" role="status">{children}</p>; }
export function ErrorState({ error, retry }) { return <div className="dating-alert" role="alert"><p>{error}</p>{retry && <button className="dating-text-button" onClick={retry}>Try again</button>}</div>; }

export function AuthorizedPhoto({ photo, alt, className = "", fallback = true }) {
  const { api, identity } = useDating();
  const path = datingPhotoPath(photo?.url);
  const [image, setImage] = useState(null);
  useEffect(() => {
    let active = true, localUrl;
    const abort = new AbortController(); setImage(null);
    if (path) api.get(path, { responseType: "blob", signal: abort.signal }).then(({ data }) => {
      if (active) { localUrl = URL.createObjectURL(data); setImage({ path, identity, url: localUrl }); }
    }).catch(() => {});
    return () => { active = false; abort.abort(); if (localUrl) URL.revokeObjectURL(localUrl); };
  }, [api, identity, path]);
  return image?.path === path && image.identity === identity ? <img className={className} src={image.url} alt={alt || "Member profile"} /> : fallback ? <div className={`dating-photo-placeholder ${className}`} aria-label="Photo unavailable"><ImagePlus size={28} strokeWidth={1.2} /></div> : null;
}

export function Modal({ title, children, close, wide = false }) {
  const ref = useRef(null), closeRef = useRef(close), titleId = useId();
  closeRef.current = close;
  useEffect(() => {
    const previous = document.activeElement;
    const panel = ref.current;
    panel?.querySelector("button,input,textarea,select")?.focus();
    const key = event => {
      if ([...document.querySelectorAll("[data-dating-modal]")].at(-1) !== panel) return;
      if (event.key === "Escape") closeRef.current();
      if (event.key !== "Tab") return;
      const items = [...panel.querySelectorAll("button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),a[href]")];
      const first = items[0], last = items.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("keydown", key); if (previous?.isConnected) previous.focus?.(); };
  }, []);
  return <div className="dating-modal-backdrop" onMouseDown={e => e.target === e.currentTarget && close()}><section className={`dating-modal ${wide ? "dating-modal--wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId} data-dating-modal ref={ref}><header><h2 id={titleId}>{title}</h2><button type="button" className="dating-icon-button" onClick={close} aria-label="Close dialog"><X size={21} /></button></header>{children}</section></div>;
}

export function ProfileCard({ profile, onLike, onPass, onOpen, compact = false, busy = false }) {
  const { photos = [], reasons = [] } = profile;
  return <article className={`dating-profile-card ${compact ? "dating-profile-card--compact" : ""}`} data-testid={`dating-profile-${profile.id}`}>
    <button className="dating-card-photo" onClick={() => onOpen?.(profile)} aria-label={`View ${profile.display_name}'s profile`}>
      <AuthorizedPhoto photo={photos[0]} alt={`${profile.display_name}'s profile photo`} />
      <span className="dating-card-photo__caption"><strong>{profile.display_name}{profile.age ? `, ${profile.age}` : ""}</strong><span>{profile.city}</span></span>
    </button>
    <div className="dating-profile-card__body"><div className="dating-chips"><span>{humanLabel(profile.intent)}</span>{profile.verification?.photo_reviewed && <span><ShieldCheck size={13} /> Photos reviewed</span>}</div>
      {profile.bio && <p className="dating-card-bio">{profile.bio}</p>}
      {reasons.length > 0 && <div className="dating-card-reasons"><p className="dating-kicker">Worth exploring</p><ul>{reasons.slice(0, 3).map((reason, i) => <li key={i}>{reason}</li>)}</ul></div>}
      {(onLike || onPass) && <div className="dating-card-actions">{onPass && <button className="btn-ghost dating-button dating-button--quiet" disabled={busy} onClick={() => onPass(profile)}>Pass</button>}{onLike && <button className="btn-lime dating-button dating-button--lime" disabled={busy} onClick={() => onLike(profile)}><Heart size={17} /> Express interest</button>}</div>}
    </div>
  </article>;
}

export function MemberProfileModal({ profile, close, onInterest, onPass, onReported }) {
  const [safety, setSafety] = useState(null);
  return <><Modal title={`About ${profile.display_name}`} close={close} wide><div className="dating-modal-gallery">{profile.photos?.map(photo => <AuthorizedPhoto photo={photo} key={photo.id} alt={`${profile.display_name}'s profile photo`} />)}</div><h3>{profile.display_name}{profile.age ? `, ${profile.age}` : ""}</h3><p className="dating-muted">{profile.city} · {humanLabel(profile.intent)}</p><p className="dating-preserve">{profile.bio}</p>{profile.reasons?.length > 0 && <div className="dating-note"><strong>Why this introduction</strong><ul>{profile.reasons.map((x, i) => <li key={i}>{x}</li>)}</ul></div>}{profile.conversation_starter && <div className="dating-note"><strong>A question to start with</strong><p>{profile.conversation_starter}</p></div>}<p className="dating-fine">An introduction is a possibility to explore. Your decisions and real conversations matter.</p><div className="dating-button-row">{onPass && <button className="btn-ghost dating-button dating-button--quiet" onClick={() => onPass(profile)}>Pass</button>}{onInterest && <button className="btn-lime dating-button dating-button--lime" onClick={() => onInterest(profile)}><Heart size={17} /> Express interest</button>}</div><div className="dating-safety-actions"><button onClick={() => setSafety("report")}><Flag size={16} /> Report</button><button onClick={() => setSafety("block")}><Ban size={16} /> Block</button></div></Modal>{safety && <SafetyDialog profile={profile} mode={safety} close={() => setSafety(null)} onDone={() => { setSafety(null); close(); onReported?.(); }} />}</>;
}

export function SafetyDialog({ profile, matchId, mode, close, onDone }) {
  const { api, refresh } = useDating();
  const action = useDatingAction();
  const [category, setCategory] = useState("safety"), [text, setText] = useState("");
  const submit = e => { e.preventDefault(); action.run(() => mode === "block" ? api.post("/dating/blocks", { profile_id: profile.id }) : api.post("/dating/reports", { profile_id: profile.id, ...(matchId ? { match_id: matchId } : {}), category, text }), async () => { await refresh(); onDone?.(); }); };
  return <Modal title={mode === "block" ? `Block ${profile.display_name}?` : `Report ${profile.display_name}`} close={close}><form onSubmit={submit} className="dating-form">{mode === "block" ? <p>You will stop appearing to each other in dating. This also stops messages and notifications between you.</p> : <><p className="dating-muted">Tell our review team what happened. Reports are not sent to the member.</p><label>What would you like reviewed?<select value={category} onChange={e => setCategory(e.target.value)}><option value="safety">Safety concern</option><option value="profile">Profile</option><option value="photo">Photo</option><option value="messages">Messages</option><option value="other">Something else</option></select></label><label>Details<textarea rows={4} required value={text} onChange={e => setText(e.target.value)} maxLength={2400} placeholder="Describe what happened and what you would like the team to review." /></label></>}<ActionFeedback action={action} /><div className="dating-button-row"><button type="button" className="btn-ghost dating-button dating-button--quiet" onClick={close}>Cancel</button><button className="btn-lime dating-button dating-button--dark" disabled={action.busy || (mode === "report" && !text.trim())}>{action.busy ? "Saving…" : mode === "block" ? "Block member" : "Send report"}</button></div></form></Modal>;
}

export function MatchListItem({ match }) {
  const person = match.profile || {};
  return <Link to={`/dating/messages/${encodeURIComponent(match.id)}`} className="dating-match-item"><AuthorizedPhoto photo={person.photos?.[0]} alt="" className="dating-match-avatar" /><span><strong>{person.display_name}</strong><span className="dating-match-preview">{match.last_message?.text || "You both chose to connect."}</span></span>{match.unread_count > 0 && <span className="dating-unread" aria-label={`${match.unread_count} unread messages`}>{match.unread_count}</span>}</Link>;
}

export function DateFeedback({ matchId, close }) {
  const { api } = useDating(); const action = useDatingAction();
  const [value, setValue] = useState({ conversation_happened: false, date_happened: false, want_another: "not_yet", note: "" });
  return <Modal title="How did the introduction go?" close={close}><form className="dating-form" onSubmit={e => { e.preventDefault(); action.run(() => api.post(`/dating/matches/${encodeURIComponent(matchId)}/feedback`, value), close); }}><p className="dating-muted">This optional feedback is private. It is not sent to your match. You can skip anything you haven’t tried.</p><label className="dating-check"><input type="checkbox" checked={value.conversation_happened} onChange={e => setValue({ ...value, conversation_happened: e.target.checked })} /> We had a conversation</label><label className="dating-check"><input type="checkbox" checked={value.date_happened} onChange={e => setValue({ ...value, date_happened: e.target.checked, conversation_happened: e.target.checked || value.conversation_happened })} /> We met for a date</label><label>Would you like another date?<select value={value.want_another} onChange={e => setValue({ ...value, want_another: e.target.value })}><option value="not_yet">We haven’t met yet</option><option value="yes">Yes</option><option value="no">No</option><option value="unsure">I’m unsure</option></select></label><label>Anything you would like to remember? <span className="dating-muted">Optional</span><textarea rows={3} maxLength={1500} value={value.note} onChange={e => setValue({ ...value, note: e.target.value })} /></label><ActionFeedback action={action} /><button className="btn-lime dating-button dating-button--dark" disabled={action.busy}><CalendarDays size={17} />{action.busy ? "Saving…" : "Save private feedback"}</button></form></Modal>;
}
