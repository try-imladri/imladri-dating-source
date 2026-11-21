import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { publicApi, errMsg } from "@/lib/api";
import { Textarea } from "@/components/ui/textarea";
import { PublicPage } from "@/pages/public/StaticPages";

const categories = ["Account", "Billing", "Refund", "Privacy", "Other"];
const button = "btn-lime inline-flex px-5 py-2.5 text-sm disabled:opacity-50";
const newReceipt = () => Array.from(crypto.getRandomValues(new Uint8Array(32)), (b) => b.toString(16).padStart(2, "0")).join("");

function Receipt({ receipt }) {
  const [record, setRecord] = useState(null);
  const [error, setError] = useState("");
  const [expired, setExpired] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const [copied, setCopied] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [question,setQuestion]=useState(""),[sending,setSending]=useState(false),[followError,setFollowError]=useState("");
  async function follow(event) {
    event.preventDefault();setSending(true);setFollowError("");
    try {await publicApi.post("/support/follow-up",{receipt,message:question.trim(),round:record.round || 0});
      setQuestion("");setRefresh(n=>n+1);}
    catch(e){setFollowError(errMsg(e));} finally{setSending(false);}
  }
  useEffect(() => {
    let active = true;
    setError("");
    publicApi.post("/support/status", { receipt }).then(({ data }) => {
      if (active) setRecord(data);
    }).catch((e) => { if (active) { setExpired(e.response?.status === 404); setError(errMsg(e)); } });
    return () => { active = false; };
  }, [receipt, refresh]);
  const remove = async () => {
    if (!window.confirm("Delete this support request and its reply?")) return;
    try {
      await publicApi.post("/support/delete", { receipt });
      setDeleted(true);
    } catch (e) { setError(errMsg(e)); }
  };
  if (deleted) return <p role="status">Your support request was deleted.</p>;
  if (error) return <div><p role="alert">{error}</p><a className="underline" href="/support">Start a new request</a>{!expired && <button className="underline ml-4" onClick={() => setRefresh(n => n + 1)}>Try again</button>}</div>;
  if (!record) return <p role="status">Loading your request…</p>;
  return (
    <div className="space-y-5" data-testid="support-receipt">
      <p><strong>{record.status === "answered" ? "Support has replied." : "Your request was received."}</strong> Save this private link to check for a reply. Anyone with the link can read, follow up on, or delete this request.</p>
      <label className="block text-sm">Private return link
        <input className="mt-1 block w-full rounded border border-[var(--line)] bg-transparent p-2" readOnly value={window.location.href} onFocus={(e) => e.target.select()} aria-label="Private return link" />
      </label>
      <button className="underline text-sm" onClick={async () => {
        try { await navigator.clipboard.writeText(window.location.href); setCopied(true); }
        catch { document.querySelector('[aria-label="Private return link"]')?.select(); }
      }}>{copied ? "Copied" : "Copy link"}</button>
      <div><h2>Your request</h2><p className="whitespace-pre-wrap break-words">{record.message}</p></div>
      {(record.thread || (record.answer ? [{speaker:"support",text:record.answer}] : [])).map((entry,i)=><div key={i}>
        <h2>{entry.speaker==="support" ? "Support reply" : "Your follow-up"}</h2><p className="whitespace-pre-wrap break-words" data-testid={entry.speaker==="support"?"support-answer":undefined}>{entry.text}</p></div>)}
      {record.status==="answered" && (record.round || 0)<12 && <form onSubmit={follow} className="space-y-2">
        <label className="block text-sm">Follow up and reopen this request<Textarea value={question} maxLength={2400} onChange={e=>setQuestion(e.target.value)} /></label>
        <button className={button} disabled={sending || !question.trim()}>Send follow-up</button>{followError && <p role="alert">{followError}</p>}</form>}
      <p className="text-sm text-[var(--muted)]">This request expires on {new Date(record.expires_at).toLocaleDateString()}.</p>
      <div className="flex gap-5 text-sm"><button className="underline" onClick={() => setRefresh(n => n + 1)}>Check for reply</button><button className="underline" onClick={remove}>Delete request</button></div>
    </div>
  );
}

function SupportForm({ search }) {
  const initial = new URLSearchParams(search).get("topic");
  const [category, setCategory] = useState(categories.includes(initial) ? initial : "Account");
  const [message, setMessage] = useState(() => {
    const slug = new URLSearchParams(search).get("figure");
    return initial==="Other" && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug || "") && slug.length<100
      ? "Correction for https://imladri.com/figures/"+slug+"\nWhat should change? " : "";
  });
  const pending = useRef(null);
  const active = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const send = async (event) => {
    event.preventDefault();
    setError(""); setBusy(true);
    try {
      const body = { category, message: message.trim() };
      if (!pending.current || pending.current.message !== body.message || pending.current.category !== category) {
        pending.current = { ...body, receipt: newReceipt() };
      }
      const request = pending.current;
      await publicApi.post("/support/requests", request);
      if (active.current) window.location.hash = request.receipt;
    } catch (e) { if (active.current) setError(errMsg(e)); }
    finally { if (active.current) setBusy(false); }
  };
  return (
    <form className="space-y-5" onSubmit={send}>
      <p>Ask about your account, a charge, a refund or your data. You’ll get a private link where you can read the reply without signing in.</p>
      <label className="block text-sm font-semibold">What do you need help with?
        <select value={category} disabled={busy} onChange={(e) => setCategory(e.target.value)} className="mt-2 block w-full rounded border border-[var(--line)] bg-[var(--bg)] p-2">{categories.map(c => <option key={c}>{c}</option>)}</select>
      </label>
      <label className="block text-sm font-semibold">Your message
        <Textarea className="mt-2" rows={5} maxLength={2400} required disabled={busy} value={message} onChange={(e) => setMessage(e.target.value)} aria-describedby="support-note" />
      </label>
      <p id="support-note" className="text-sm text-[var(--muted)]">For billing questions, a receipt or invoice number is enough to get started. Leave out card numbers, passwords and private conversations.</p>
      {error && <p role="alert">{error}</p>}
      <button className={button} disabled={busy || !message.trim()}>{busy ? "Sending…" : "Send request"}</button>
    </form>
  );
}

export default function Support() {
  const { hash, search } = useLocation();
  const receipt = hash.slice(1);
  return (
    <PublicPage eyebrow="Help" title="Support" testid="support-page">
      {receipt ? (/^[a-f0-9]{64}$/.test(receipt) ? <Receipt key={receipt} receipt={receipt} /> : <p role="alert">This return link is incomplete. <a href="/support" className="underline">Start a new request</a>.</p>) : (
        <SupportForm key={search} search={search} />
      )}
    </PublicPage>
  );
}
