import { useCallback, useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { datingError, datingPhotoPath, humanLabel, safeTextDate } from "@/lib/dating";
import { SubTabs } from "./SubTabs";

const button = "btn-ghost px-3 py-2 text-xs disabled:opacity-50";

function ReviewPhoto({ api, photo }) {
  const [image, setImage] = useState(null);
  const path = datingPhotoPath(photo.url || `/api/dating/photos/${photo.id}/content`);
  useEffect(() => {
    let active = true, url;
    const abort = new AbortController();
    setImage(null);
    if (path) api.get(path, {responseType:"blob",signal:abort.signal}).then(({data}) => {
      if (active) { url = URL.createObjectURL(data); setImage(url); }
    }).catch(() => {});
    return () => { active = false; abort.abort(); if (url) URL.revokeObjectURL(url); };
  }, [api, path]);
  return image ? <img src={image} alt="Submitted dating profile photo for review" className="h-40 w-32 rounded-xl object-cover" /> : <p className="flex h-40 w-32 items-center justify-center rounded-xl bg-[var(--surface)] text-xs">Photo unavailable</p>;
}

function Decision({ label, choices, save, busy }) {
  const [note, setNote] = useState("");
  return <form onSubmit={e => e.preventDefault()} className="mt-3 space-y-2">
    <Textarea aria-label={`${label} review note`} value={note} onChange={e => setNote(e.target.value)} maxLength={1200} placeholder="Decision reason for the review record" rows={2} />
    <div className="flex flex-wrap gap-2">{choices.map(([value, text]) => <button type="button" key={value} className={button} disabled={busy} onClick={() => save(value, note)}>{text}</button>)}</div>
  </form>;
}

export default function DatingAdmin({ api }) {
  const [data, setData] = useState(null), [metrics, setMetrics] = useState(null);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false), [tab, setTab] = useState("profiles"), [notice, setNotice] = useState("");
  const [settings, setSettings] = useState(null);
  const load = useCallback(async signal => {
    const [queue, counts] = await Promise.all([api.get("/dating/admin/queue", {signal}), api.get("/dating/admin/metrics", {signal})]);
    if (!signal?.aborted) { setData(queue.data); setMetrics(counts.data); setSettings(queue.data.settings); }
  }, [api]);
  useEffect(() => {
    const abort = new AbortController(); setData(null); setError("");
    load(abort.signal).catch(e => { if (!abort.signal.aborted) setError(datingError(e)); });
    return () => abort.abort();
  }, [load]);
  const mutate = async (path, body, method = "post") => {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { await api[method](path, body); await load(); setNotice("Review saved."); }
    catch (e) { setError(datingError(e)); }
    finally { setBusy(false); }
  };
  if (!data) return <div className="space-y-3">{error ? <p role="alert">{error} <button className="underline" onClick={() => {setError(""); load().catch(e => setError(datingError(e)));}}>Try again</button></p> : <p role="status">Loading dating review queue…</p>}</div>;
  return <section className="space-y-5 overflow-y-auto pb-8" data-testid="dating-admin">
    <p className="text-sm text-[var(--muted)]">Review real submissions before publication. Photo review does not verify identity or age. Every decision is recorded; a report does not automatically establish wrongdoing. Restoring a suspension returns the member to their previous visibility and may make a previously active profile discoverable again.</p>
    {error && <p className="rounded-xl border border-red-400 p-3 text-sm" role="alert">{error}</p>}{notice && <p role="status" className="text-sm">{notice}</p>}
    {metrics && <div className="grid grid-cols-2 gap-3 md:grid-cols-4">{Object.entries(metrics).filter(([,value]) => typeof value === "number").map(([name,value]) => <div key={name} className="card p-4"><p className="text-xs capitalize text-[var(--muted)]">{name.replace(/_/g," ")}</p><p className="mt-1 text-2xl font-semibold">{value}</p></div>)}</div>}
    <SubTabs value={tab} onChange={setTab} items={[["profiles",`Profiles (${data.profiles?.length || 0})`],["reports",`Reports (${data.reports?.length || 0})`],["appeals",`Appeals (${data.appeals?.length || 0})`],["launch","Community settings"]]} testid="dating-admin-tabs" />
    {tab === "profiles" && <div className="space-y-4">{!data.profiles?.length && <p className="card p-6">No profiles awaiting review.</p>}{data.profiles?.map(profile => <article className="card p-5" key={profile.id}>
      <div className="flex flex-wrap justify-between gap-3"><h2 className="font-semibold">{profile.display_name || "Unnamed profile"}{profile.age ? `, ${profile.age}` : ""}</h2><span className="text-xs">{humanLabel(profile.state)} · revision {profile.revision}</span></div>
      <p className="mt-1 text-sm text-[var(--muted)]">{profile.city} · {humanLabel(profile.intent)} · {humanLabel(profile.relationship_structure)}</p><p className="mt-3 whitespace-pre-wrap text-sm">{profile.bio}</p>
      <div className="mt-4 flex flex-wrap gap-4">{profile.photos?.map(photo => <div key={photo.id} className="w-48 rounded-xl border border-[var(--line)] p-3"><ReviewPhoto api={api} photo={photo} /><p className="mt-2 text-xs">Photo: {humanLabel(photo.state || photo.status)}</p><Decision label={`Photo ${photo.id}`} busy={busy} choices={[["approve","Approve photo"],["reject","Reject photo"]]} save={(decision,note) => mutate(`/dating/admin/photos/${encodeURIComponent(photo.id)}/review`,{decision,note})} /></div>)}</div>
      <Decision label={`Profile ${profile.display_name || profile.id}`} busy={busy} choices={[["approve","Approve profile"],["reject","Request changes"],["suspend","Suspend"],["restore","Restore previous visibility"]]} save={(decision,note) => mutate(`/dating/admin/profiles/${encodeURIComponent(profile.id)}/review`,{decision,note,expected_revision:profile.revision})} />
    </article>)}</div>}
    {tab === "reports" && <div className="space-y-4">{!data.reports?.length && <p className="card p-6">No open reports.</p>}{data.reports?.map(report => <article className="card p-5 space-y-2" key={report.id}>
      <h2 className="font-semibold capitalize">{report.category} · {report.status}</h2><p className="text-xs text-[var(--muted)]">{safeTextDate(report.created_at)} · Case {report.id}</p><p className="whitespace-pre-wrap text-sm">{report.text}</p>
      {report.evidence?.length > 0 && <details className="text-sm"><summary className="cursor-pointer font-semibold">Submitted message evidence</summary>{report.evidence.map((message,i) => <blockquote className="mt-2 border-l-2 border-[var(--line)] pl-3 whitespace-pre-wrap" key={message.id || i}>{message.text || message.content || "Evidence record"}</blockquote>)}</details>}
      {report.review_note && <p className="text-xs">Last review: {report.review_note}</p>}
      <Decision label={`Report ${report.id}`} busy={busy} choices={[["reviewing","Mark reviewing"],["resolved","Resolve"],["dismissed","Dismiss"]]} save={(status,note) => mutate(`/dating/admin/reports/${encodeURIComponent(report.id)}/review`,{status,note})} />
      {report.profile_id && <Decision label={`Reported profile ${report.profile_id}`} busy={busy} choices={[["suspend","Suspend reported profile"],["restore","Restore previous visibility"]]} save={(decision,note) => mutate(`/dating/admin/profiles/${encodeURIComponent(report.profile_id)}/review`,{decision,note})} />}
    </article>)}</div>}
    {tab === "appeals" && <div className="space-y-4">{!data.appeals?.length && <p className="card p-6">No open appeals.</p>}{data.appeals?.map(appeal => <article className="card p-5" key={appeal.id}><h2 className="font-semibold">Appeal · {appeal.status}</h2><p className="mt-1 text-xs text-[var(--muted)]">{safeTextDate(appeal.created_at)} · {appeal.id}</p><p className="mt-3 whitespace-pre-wrap text-sm">{appeal.text}</p><Decision label={`Appeal ${appeal.id}`} busy={busy} choices={[["reviewing","Mark reviewing"],["resolved","Resolve appeal"],["dismissed","Dismiss appeal"]]} save={(status,note) => mutate(`/dating/admin/appeals/${encodeURIComponent(appeal.id)}/review`,{status,note})} />{appeal.profile_id && <Decision label={`Appeal profile ${appeal.profile_id}`} busy={busy} choices={[["restore","Restore previous visibility"]]} save={(decision,note) => mutate(`/dating/admin/profiles/${encodeURIComponent(appeal.profile_id)}/review`,{decision,note})} />}</article>)}</div>}
    {tab === "launch" && settings && <form className="card max-w-xl space-y-4 p-5" onSubmit={e => {e.preventDefault(); mutate("/dating/admin/settings", {city:settings.city,discovery_enabled:settings.discovery_enabled,accepting_profiles:settings.accepting_profiles,review_note:settings.review_note || ""}, "put");}}>
      <label className="block text-sm font-semibold">Founding city<Input className="mt-2" required maxLength={80} value={settings.city} onChange={e => setSettings({...settings,city:e.target.value})} /></label>
      <label className="flex items-center gap-3 text-sm"><input type="checkbox" checked={settings.accepting_profiles} onChange={e => setSettings({...settings,accepting_profiles:e.target.checked})} />Accept new profiles</label>
      <label className="flex items-center gap-3 text-sm"><input type="checkbox" checked={settings.discovery_enabled} onChange={e => setSettings({...settings,discovery_enabled:e.target.checked})} />Enable introductions between eligible members</label>
      <label className="block text-sm font-semibold">Community notice<Textarea className="mt-2" maxLength={600} value={settings.review_note || ""} onChange={e => setSettings({...settings,review_note:e.target.value})} /></label>
      <p className="text-xs text-[var(--muted)]">Community changes never relax a member’s reciprocal preferences. Dating remains a free founding beta; these controls do not enable billing.</p>
      <button type="submit" className={button} disabled={busy}>Save community settings</button>
    </form>}
  </section>;
}
