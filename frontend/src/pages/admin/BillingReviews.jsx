import { useEffect, useState } from "react";

export default function BillingReviews({ api }) {
  const [data,setData]=useState(null);
  const [error,setError]=useState(false);
  const [attempt,setAttempt]=useState(0);
  useEffect(()=>{
    const request=new AbortController();setError(false);
    api.get('/admin/membership/adjustment-reviews',{params:{limit:50},signal:request.signal})
      .then(({data:next})=>{if(!request.signal.aborted)setData(next);})
      .catch(()=>{if(!request.signal.aborted)setError(true);});
    return()=>request.abort();
  },[api,attempt]);
  if(error)return <p role="alert" className="text-xs">Payment adjustment reviews couldn’t load. <button className="underline" onClick={()=>setAttempt(n=>n+1)}>Retry payment reviews</button></p>;
  if(!data)return <p role="status" className="text-xs text-[var(--muted)]">Checking payment adjustment reviews…</p>;
  if(!data.items?.length)return null;
  return <details className="card p-4 text-sm" data-testid="billing-adjustment-reviews">
    <summary className="cursor-pointer font-semibold">Payment adjustments need review{data.truncated?'':` (${data.items.length})`}</summary>
    <p className="mt-2">Some payment links could not be verified, including payments whose account records were removed. Affected net revenue stays unverified. Review the payment evidence in Stripe; no refund or approval is submitted here.</p>
    <ul className="mt-3 max-h-72 space-y-3 overflow-auto">{data.items.map(row=><li key={row.id} className="border-t border-[var(--line)] pt-2 text-xs">
      <p>{row.reason}</p><p>{row.opened_at}</p>
      {row.user_id && <p className="break-all">Account: {row.user_id}</p>}
      {row.charge_id && <p className="break-all">Charge: {row.charge_id}</p>}
      {row.payment_intent_id && <p className="break-all">Payment: {row.payment_intent_id}</p>}
    </li>)}</ul>
    {data.truncated&&<p className="mt-2 text-xs">Showing 50 pending reviews; additional reviews remain.</p>}
  </details>;
}
