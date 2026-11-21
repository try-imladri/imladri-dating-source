import { useRef, useState } from "react";
import { matchesResearchCatalog } from "./intimacyTopics";

const endpoint = "/me/intimacy-wording";
const labels = {conditions:"When something depends on conditions", boundaries:"Where you set a boundary"};
export const wordingLabels = {faithful:"Came through accurately", partly:"Partly accurate", missed:"Missed or changed what I meant", unsure:"I couldn’t judge", inapplicable:"Didn’t apply to this summary"};
const blank = {conditions:"",boundaries:""};

export default function WordingFeedback({ api, owner, signature, sections }) {
  const [open,setOpen] = useState(false), [record,setRecord] = useState(null);
  const [ratings,setRatings] = useState(blank), [agreed,setAgreed] = useState(false);
  const [busy,setBusy] = useState(false), [error,setError] = useState(""), [notice,setNotice] = useState("");
  const lock=useRef(false);
  const edition=signature && "intimacy-summary-feedback-v2/"+signature;
  async function action(method) {
    if(lock.current) return;
    lock.current=true;setBusy(true);setError("");setNotice("");
    try{
      const base = record && {owner,account_created:record.account_created,revision:record.revision};
      let result;
      if(method==="read") result=await api.get(endpoint);
      if(method==="remove") result=await api.delete(endpoint,{data:base});
      if(method==="save") {
        if(!await matchesResearchCatalog(sections,signature)) throw Error("The displayed questionnaire has changed. Reopen the summary before sharing feedback.");
        result=await api.put(endpoint,{...base,edition,adult_and_consented:agreed,conditions:ratings.conditions||null,boundaries:ratings.boundaries||null});
      }
      if(result.data.owner!==owner) throw Error("Your account changed. Reopen this page.");
      setRecord(result.data);
      const prior=result.data.response?.edition===edition && result.data.response;
      setRatings({conditions:prior?.conditions||"",boundaries:prior?.boundaries||""});
      setAgreed(false);
      if(method==="save") setNotice("Your wording feedback is saved.");
      if(method==="remove") setNotice("Your wording feedback was withdrawn.");
    }catch(e){setRecord(null);setError(typeof e.response?.data?.detail==="string"?e.response.data.detail:e.response||!e.message?"Couldn’t confirm your saved wording feedback. Check the current record before retrying.":e.message);}
    finally{lock.current=false;setBusy(false);}
  }
  return <div className="space-y-2 pt-2 text-sm">
    <button type="button" className="underline py-1" aria-expanded={open} onClick={()=>{if(!open&&!record) action("read");setOpen(!open);}}>
      {edition?"Rate how this summary worded your answers":"Manage intimacy summary wording feedback"}</button>
    {open && <>
      {busy && <p role="status">Checking your feedback…</p>}
      {error && <p role="alert">{error} <button type="button" className="underline" onClick={()=>action("read")}>Check saved feedback</button></p>}
      {notice && <p role="status">{notice}</p>}
      {record && <><p>Optional feedback for group reports. Imladri saves your ratings and the wording edition with your account. You can remove them here or in Settings. This feedback includes no questionnaire answers, selected topics, messages or demographic details.</p>
        {record.response && <div><p>Saved ratings for this account</p><ul>
          {Object.entries(labels).map(([k,label])=><li key={k}>{label} · {wordingLabels[record.response[k]]||"Skipped"}</li>)}</ul>
          <button type="button" disabled={busy} className="underline py-2" onClick={()=>action("remove")}>Withdraw wording feedback</button></div>}
        {!record.response && !edition && <p>You have no saved wording feedback.</p>}
        {edition && <form className="space-y-2" onSubmit={e=>{e.preventDefault();action("save");}}>
          {Object.entries(labels).map(([k,label])=><label className="block" key={k}>{label}
            <select disabled={busy} value={ratings[k]} className="block mt-1 p-2 border border-[var(--line)] rounded bg-transparent max-w-full"
              onChange={e=>{setRatings({...ratings,[k]:e.target.value});setAgreed(false);}}>
              <option value="">Skip this rating</option>{Object.entries(wordingLabels).map(([id,text])=><option key={id} value={id}>{text}</option>)}
            </select></label>)}
          <label className="block"><input type="checkbox" checked={agreed} disabled={busy} onChange={e=>setAgreed(e.target.checked)} /> I am 18 or older and agree to use these ratings in group reports.</label>
          <button type="submit" className="btn-ghost px-4 py-2" disabled={busy||!agreed||(!ratings.conditions&&!ratings.boundaries)}>{record.response?"Replace my wording feedback":"Save wording feedback"}</button>
        </form>}
      </>}
    </>}
  </div>;
}
