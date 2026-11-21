import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

import { errMsg } from "@/lib/api";

const ArchetypeArticle = lazy(() => import("@/pages/public/ArchetypePage").then(m => ({ default:m.ArchetypeArticle })));
const FigureArticle = lazy(() => import("@/pages/public/FigurePage").then(m => ({ default:m.FigureArticle })));

const label = key => key.replace(/_/g, " ").replace(/^./, c => c.toUpperCase());
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
const title = path => path.split(".").map(label).join(" / ");
const inputClass = "block w-full rounded-lg border border-[var(--line)] bg-[var(--bg)] px-3 py-2 text-sm";
const pair = { point: "", detail: "" };
const templates = {
  characters:{name:"", show:"", actor:"", why:"", image_url:null}, threads:{title:"", note:"", bubbles:[{from:"you",text:""}]},
  bubbles:{from:"you",text:""}, matches:{number:1,why:""}, secondary:{number:1,why:""},
  sources:{label:"",url:""}, example_sources:{example:"",label:"",url:""}, what_to_say:{line:"",why:""}, never_say:{line:"",why:""},
  manipulation:{name:"",how_it_works:"",defense:""}, power_moves:{name:"",how_it_works:"",defense:""},
  interested:pair, not_interested:pair, red_flags:pair, green_flags:pair, leverage:pair, works:pair, never:pair,
};

function Field({ field, value, set, name }) {
  if (Array.isArray(value)) {
    return <div className="space-y-3"><p className="font-semibold">{name}</p>
      {value.map((item, index) => <div key={index} className="rounded-lg border border-[var(--line)] p-3 space-y-2">
        <Field field={field} name={field === "dating" ? `Dating paragraph ${index + 1}` : `${name} ${index + 1}`} value={item}
          set={next => set(value.map((v,i) => i===index ? next : v))} />
        <button type="button" className="text-xs underline" aria-label={`Remove ${name} ${index + 1}`} onClick={() => set(value.filter((_,i) => i !== index))}>Remove</button>
      </div>)}
      <button type="button" className="btn-ghost px-3 py-1 text-xs" onClick={() => set([...value, templates[field] || ""])}>Add {name.toLowerCase()}</button></div>;
  }
  if (record(value)) {
    return <fieldset className="space-y-2"><legend className="font-semibold">{name}</legend>
      {Object.entries(value).map(([key, v]) => <Field key={key} field={key} name={label(key)} value={v} set={next => set({...value,[key]:next})} />)}
    </fieldset>;
  }
  if (field === "from") return <label className="block text-sm">{name}<select className={inputClass} value={value}
    onChange={event => set(event.target.value)}>{["you","him","her","them"].map(w => <option key={w}>{w}</option>)}</select></label>;
  const number = field === "number" || field === "wave" || field === "v";
  return <label className="block text-sm">{name}{number
    ? <input type="number" aria-label={name} min="1" step="1" className={inputClass} value={value ?? ""} onChange={e => set(e.target.value === "" ? null : Number(e.target.value))} />
    : <textarea aria-label={name} className={inputClass} rows={Math.max(2, Math.min(5, Math.ceil((value?.length || 1) / 85)))} value={value ?? ""} onChange={e => set(e.target.value)} />}</label>;
}

export function reconcile(base, local, latest, choices = {}, path = "") {
  if (same(base, local) || same(local, latest)) return {value:latest, conflicts:[]};
  if (same(base, latest)) return {value:local, conflicts:[]};
  if (record(base) && record(local) && record(latest)) {
    const value={}, conflicts=[];
    for (const key of new Set([...Object.keys(base),...Object.keys(local),...Object.keys(latest)])) {
      const result=reconcile(base[key],local[key],latest[key],choices,path ? path+"."+key : key);
      if(result.value !== undefined) value[key]=result.value;
      conflicts.push(...result.conflicts);
    }
    return {value,conflicts};
  }
  if(choices[path]) return {value:choices[path]==="mine" ? local : latest, conflicts:[]};
  return {value:local,conflicts:[{path,local,latest}]};
}

function blankArticle(group) {
  if(group === "archetypes") return {identity:"",summary:[],texting_patterns:{text:[]},threads:[],characters:[],interested:[],
    not_interested:[],red_flags:[],green_flags:[],what_to_say:[],never_say:[],manipulation:[],leverage:[],closest_cousin:{number:1,text:"",tell:""},dating:[],v:1};
  return {identity:"",matches:[],pattern:[],communicates:{text:[],examples:[],example_sources:[]},spectrum:{interested:"",invested:"",done:""},
    handling:{works:[],never:[]},power_moves:[],leverage:[],map:{closest:{number:1,rationale:""},cousin:{number:2,tell:""},secondary:[],basis:{style:"",observation:"",sources:[]}},dating:[],v:1};
}

function supplyMissing(defaults, item) {
  if (!record(defaults) || !record(item)) return item ?? defaults;
  const next={...item};
  for(const [key,value] of Object.entries(defaults)) next[key]=supplyMissing(value,item[key]);
  return next;
}

function compareValue(article,path) {
  return path.split(".").reduce((value,k)=>value?.[k],article);
}

export default function CatalogArticleEditor({ api, group, slug, onClose, onPublished }) {
  const url=`/admin/editorial/articles/${group}/${slug}`;
  const [saved,setSaved]=useState(null),[local,setLocal]=useState(null),[section,setSection]=useState("overview");
  const [preview,setPreview]=useState(null),[latest,setLatest]=useState(null),[choices,setChoices]=useState({});
  const [busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState(""),[closing,setClosing]=useState(false);
  const apply=useCallback(data => {setSaved(data);setLocal(data.draft ?? data.published);setLatest(null);setChoices({});setPreview(null);},[]);
  const load=useCallback(async() => {setBusy(true);setError("");
    try {const {data}=await api.get(url);apply(data);}
    catch(e){setError(errMsg(e));}finally{setBusy(false);}
  },[api,url,apply]);
  useEffect(()=>{load();},[load]);
  const base=saved?.draft ?? saved?.published;
  const defaults=group==="archetypes"
    ? {name:"",identity:"",one_liner:"",wave:1,texting_tags:[],red_flag_tags:[]}
    : {name:"",identity:"",matches:[],sources:[]};
  const complete=local && supplyMissing({...defaults,sections:blankArticle(group)},local);
  const dirty=saved && !same(local,base);
  const writing=next=>{setLocal(next);setPreview(null);setNotice("");};
  async function save(action, revision) {
    setBusy(true);setError("");setNotice("");
    try {
      const body={version:saved.version};
      if(action==="preview" || action==="draft") body.content=local;
      if(action==="restore") body.revision=revision;
      const {data}=action==="draft" ? await api.put(url+"/draft",body) : await api.post(url+"/"+action,body);
      if(action==="preview") {setPreview(data);return;}
      apply(data);setNotice(action==="draft"?"Draft saved.":action==="publish"?"Article revision published.":"Revision restored to the draft.");
      if(action==="publish") onPublished();
    } catch(e) {
      if(e.response?.status===409){
        setError("Another editor changed this article. Your edits are still here.");
        try {setLatest((await api.get(url)).data);setChoices({});}catch(loadError){setError(errMsg(loadError));}
      } else setError(errMsg(e));
    } finally{setBusy(false);}
  }
  const merge=latest && reconcile(base,local,latest.draft ?? latest.published,choices);
  const close=()=>{if(busy)return;if(dirty){setClosing(true);}else onClose();};
  return <Dialog open onOpenChange={open=>{if(!open)close();}}><DialogContent className="w-[95vw] max-w-4xl max-h-[92vh] overflow-y-auto" data-testid="admin-edit-dialog">
    <DialogHeader><DialogTitle>{local?.name || "Article editor"}</DialogTitle><DialogDescription>Save a private draft, preview the reader’s page, then publish.</DialogDescription></DialogHeader>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {closing && <div className="card p-3"><p>Discard your unsaved edits?</p><button type="button" className="underline mr-4" onClick={onClose}>Discard and close</button><button type="button" className="underline" onClick={()=>setClosing(false)}>Keep editing</button></div>}
    {!saved ? <button type="button" className="underline" disabled={busy} onClick={load}>{busy?"Loading article…":"Retry loading article"}</button> : <>
      <p className="text-xs text-[var(--muted)]">Published revision {saved.live_revision} · {saved.visible?"Visible to readers":"Hidden from readers. Release the article with its catalog visibility switch."}</p>
      {latest && <div className="card p-4 space-y-3"><h3 className="font-semibold">Review concurrent changes</h3>
        {merge.conflicts.map(c=><div key={c.path}><p>{title(c.path)}</p>
          <details><summary>Compare values</summary><pre className="whitespace-pre-wrap break-words text-xs">Your edit{String.fromCharCode(10)}{JSON.stringify(c.local,null,2)}{String.fromCharCode(10)}Saved edit{String.fromCharCode(10)}{JSON.stringify(c.latest,null,2)}</pre></details>
          <button className="underline mr-4" onClick={()=>setChoices({...choices,[c.path]:"mine"})}>Use my edit</button><button className="underline" onClick={()=>setChoices({...choices,[c.path]:"latest"})}>Use saved edit</button></div>)}
        {Object.keys(choices).length>0 && <button type="button" className="underline text-sm" onClick={()=>setChoices({})}>Review my conflict choices again</button>}
        <button disabled={merge.conflicts.length>0} className="btn-ghost px-3 py-2" onClick={()=>{apply(latest);setLocal(merge.value);setError("");setNotice("Latest saved changes loaded. Review and save your draft.");}}>Apply reviewed changes</button>
      </div>}
      <fieldset disabled={busy || !!latest} className="space-y-3">
        <label className="block text-sm">Edit section<select className={inputClass} value={section} onChange={e=>setSection(e.target.value)} aria-label="Edit section">
          <option value="overview">Overview</option>{Object.keys(local.sections || {}).map(k=><option key={k} value={k}>{label(k)}</option>)}</select></label>
        {section==="overview" ? Object.entries(local).filter(([k])=>k!=="sections").map(([k,v])=>
          <Field key={k} field={k} name={label(k)} value={v} set={next=>writing({...local,[k]:next})} />)
          : <Field key={section} field={section} name={label(section)} value={local.sections?.[section]} set={next=>writing({...local,sections:{...local.sections,[section]:next}})} />}
        {!same(complete,local) && <button type="button" className="underline" onClick={()=>writing(complete)}>Add missing article fields</button>}
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className="btn-ghost px-4 py-2" disabled={!dirty} data-testid="admin-edit-save" onClick={()=>save("draft")}>Save draft</button>
          <button type="button" className="btn-ghost px-4 py-2" onClick={()=>save("preview")}>Preview reader page</button>
          <button type="button" className="btn-lime px-4 py-2" disabled={dirty || !preview || !saved.changes.length} onClick={()=>save("publish")}>Publish revision</button>
        </div>
        {saved.changes.length>0 && <details><summary>Compare saved draft with published revision</summary>
          {saved.changes.map(path=><details className="ml-3 mt-2" key={path}><summary>{title(path)}</summary>
            {[["Published",saved.published],["Saved draft",saved.draft]].map(([label,article])=><div key={label}><strong className="text-xs">{label}</strong>
              <pre className="text-xs whitespace-pre-wrap break-words">{JSON.stringify(compareValue(article,path),null,2) ?? "Field omitted"}</pre></div>)}</details>)}</details>}
        {saved.history.length>0 && <details><summary>Published revision history</summary><p className="text-xs">The last 12 prior revisions are retained. Restoring replaces the saved draft.</p>
          {saved.history.map(h=><div className="py-2" key={h.revision}><p>Revision {h.revision} · {new Date(h.archived_at).toLocaleString()}</p><p className="text-xs">{h.changes.map(title).join(", ")}</p>
            <button type="button" disabled={dirty} className="underline text-sm" onClick={()=>save("restore",h.revision)}>Restore revision {h.revision} to draft</button></div>)}</details>}
      </fieldset>
      {preview && <section aria-label="Reader preview" className="border-t border-[var(--line)] min-w-0"><h3 className="font-semibold mt-4">Reader preview</h3>
        <div onClickCapture={e=>{if(e.target.closest("a,button")){e.preventDefault();e.stopPropagation();}}}>
          <Suspense fallback={<p role="status">Loading reader preview…</p>}>{group==="archetypes"?<ArchetypeArticle a={preview}/>:<FigureArticle f={preview}/>}</Suspense></div></section>}
    </>}
  </DialogContent></Dialog>;
}