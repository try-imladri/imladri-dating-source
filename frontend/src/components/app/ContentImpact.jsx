import { useEffect, useState } from "react";

const labels = {people:"People profiles", exchanges:"Conversations", memories:"Memories", preps:"Game plans",
  practice:"Practice sessions", couples_drafts:"Your Couples drafts", quick_advice:"Quick Advice replies"};

export default function ContentImpact({ api, action }) {
  const [data,setData] = useState(null);
  const [failed,setFailed] = useState(false);
  const [attempt,setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setFailed(false);
    Promise.resolve().then(() => api.get("/me/content-impact", {signal:controller.signal}))
      .then(result => { if (!controller.signal.aborted) setData(result.data); })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [api, attempt]);
  return <div className="text-sm space-y-2 max-h-[36vh] overflow-y-auto">
    {data?.counts && <dl className="grid grid-cols-2 gap-x-3">{Object.entries(labels).map(([key,label])=>
      <div key={key} className="flex justify-between gap-1"><dt>{label}</dt><dd>{data.counts[key]}</dd></div>)}</dl>}
    {!data && !failed && <p role="status">Counting your saved records…</p>}
    {failed && <p role="alert">Your record counts couldn’t load. <button type="button" className="underline" onClick={()=>setAttempt(i=>i+1)}>Try again</button></p>}
    {(!data || data.shared_space_active) && <p>{data ? "Your current Couples space will close for both partners." : "If you have a Couples space, it will close for both partners."} Shared conversations, agreements and check-ins disappear for both of you. Your partner keeps their own private drafts and Individual records.</p>}
    <p>{action === "close" ? "Imladri retains billing totals and the membership record used to recognize a closed account." : "Your billing records and existing message balance stay with your account."}</p>
  </div>;
}
