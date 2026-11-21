import { useEffect, useRef, useState } from "react";

const scopes = {member:"Individual", couple:"Couples"};
const field = "block w-full rounded-lg border border-[var(--line)] bg-[var(--surface)] p-2 text-sm";

function FilePicker({ api, scope }) {
  const [bulk, setBulk] = useState(false);
  const [code, setCode] = useState("");
  const [browse, setBrowse] = useState(false);
  const [codes, setCodes] = useState([]);
  const [cursor, setCursor] = useState("");
  const [next, setNext] = useState(null);
  const [listStatus, setListStatus] = useState("idle");
  const [retry, setRetry] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const [result, setResult] = useState("");
  const download = useRef(null);
  const unit = scope === "member" ? "member" : "couple";
  const valid = (scope === "member" ? /^m_[0-9a-f]{32}$/ : /^c_[0-9a-f]{32}$/).test(code.trim());
  const base = "/admin/research/exports/" + scope;

  useEffect(() => () => download.current?.abort(), []);
  useEffect(() => {
    if (!browse || bulk) return;
    const controller = new AbortController();
    setListStatus("loading");
    api.get(base + "/units", {params:{limit:50, ...(cursor ? {after:cursor} : {})}, signal:controller.signal})
      .then(({data}) => {
        if (controller.signal.aborted) return;
        setCodes(previous => cursor ? [...new Set([...previous, ...data.items])] : data.items);
        setNext(data.next);
        setListStatus("ready");
      }).catch(() => { if (!controller.signal.aborted) setListStatus("error"); });
    return () => controller.abort();
  }, [api, base, browse, bulk, cursor, retry]);

  async function save(event) {
    event.preventDefault();
    if (download.current || (!bulk && !valid)) return;
    const controller = new AbortController();
    download.current = controller;
    setDownloading(true);
    setResult("");
    try {
      const response = await api.get(base + ".csv", {params:bulk ? {} : {unit_code:code.trim()}, responseType:"blob", signal:controller.signal});
      if (controller.signal.aborted) return;
      // Excel uses the UTF-8 marker when a downloaded CSV is opened directly on Windows.
      const url = URL.createObjectURL(new Blob(["\uFEFF", response.data], {type:"text/csv;charset=utf-8"}));
      try {
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "imladri-" + scope + "-" + (bulk ? "all" : code.trim()) + "-" + new Date().toISOString().slice(0,10) + ".csv";
        document.body.appendChild(anchor);
        try { anchor.click(); } finally { anchor.remove(); }
      } finally { window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
      setResult(String(response.headers?.["x-research-empty"]) === "1" ? "There are no eligible records. The CSV contains column headings only." : "CSV download started.");
    } catch (error) {
      if (!controller.signal.aborted) setResult(error.response?.status === 404
        ? "This code is unavailable. Refresh the eligible codes and choose a current record."
        : "The CSV could not be downloaded. Please try again.");
    } finally {
      if (!controller.signal.aborted) setDownloading(false);
      download.current = null;
    }
  }

  function refreshCodes() {
    setCodes([]); setNext(null); setCursor(""); setRetry(n => n+1); setBrowse(true);
  }

  return <form className="space-y-3 pt-3" onSubmit={save}>
    <label className="block text-sm font-semibold">File contents
      <select className={field} disabled={downloading} value={bulk ? "all" : "one"} onChange={e=>{setBulk(e.target.value==="all");setResult("");}}>
        <option value="one">One {unit}</option><option value="all">All eligible {scope === "member" ? "members" : "couples"}</option>
      </select>
    </label>
    {!bulk && <div className="space-y-2">
      <label className="block text-sm font-semibold">Research code
        <input className={field} type="text" autoComplete="off" spellCheck={false} maxLength={34} value={code} disabled={downloading}
          onChange={e=>{setCode(e.target.value.trim());setResult("");}} />
      </label>
      <button type="button" className="underline text-sm" disabled={downloading} onClick={refreshCodes}>{browse ? "Refresh eligible codes" : "Browse eligible codes"}</button>
      {browse && <>
        {codes.length>0 && <select className={field} aria-label="Eligible research codes" disabled={downloading}
          value={codes.includes(code) ? code : ""} onChange={e=>{setCode(e.target.value);setResult("");}}>
          <option value="">Choose a code</option>{codes.map(value=><option key={value} value={value}>{value}</option>)}
        </select>}
        {listStatus==="loading" && <p role="status" className="text-sm">Loading codes…</p>}
        {listStatus==="error" && <p role="alert" className="text-sm">Codes could not be loaded. <button type="button" className="underline" onClick={()=>setRetry(n=>n+1)}>Retry</button></p>}
        {listStatus==="ready" && !codes.length && <p className="text-sm">No {scope === "member" ? "members" : "couples"} currently qualify for this export.</p>}
        {next && listStatus==="ready" && <button type="button" className="underline text-sm" onClick={()=>setCursor(next)}>Load more codes</button>}
      </>}
    </div>}
    {bulk && <p className="text-sm text-[var(--muted)]">Each {unit} has a research code so you can group its rows in a spreadsheet. An empty file contains the column headings.</p>}
    <button className="btn-ghost px-4 py-2 text-sm disabled:opacity-50" disabled={downloading || (!bulk && !valid)}>
      {downloading ? "Preparing CSV…" : "Download " + scopes[scope] + " CSV"}
    </button>
    {result && <p role="status" className="text-sm">{result}</p>}
  </form>;
}

export default function ResearchRecordExports({ api }) {
  const [scope, setScope] = useState("member");
  return <section className="card space-y-3 p-4 sm:p-5" aria-label="Research CSV downloads">
    <h2 className="font-display text-xl font-bold uppercase">Research CSV downloads</h2>
    <p className="text-sm">Download current structured records for one consenting member or couple, or all eligible records together. Partial questionnaires count from the first save. Each file identifies the question, edition, answer status and research code.</p>
    <div className="flex gap-2" role="group" aria-label="CSV scope">
      {Object.entries(scopes).map(([key,label])=><button key={key} type="button" aria-pressed={scope===key}
        className={(scope===key ? "btn-lime" : "btn-ghost")+" px-4 py-1.5 text-sm"} onClick={()=>setScope(key)}>{label}</button>)}
    </div>
    <FilePicker key={scope} api={api} scope={scope} />
    <details className="border-t border-[var(--line)] pt-3 text-sm text-[var(--muted)]">
      <summary className="cursor-pointer">Reading and joining these files</summary>
      <p className="mt-2">Group rows by unit_code. In Couples files, seat distinguishes the inviter, invitee and pair-level observations. observation_code groups related shared records. observed_on_utc is present when a date is known. exported_at_utc is the download time.</p>
      <p className="mt-2">Join on member_code only when it is populated. A Couples seat includes that code when both partners enabled both record exports. Keep missing answers and skips separate from negative answers. Compare equivalent question editions and count distinct members or couples when calculating percentages.</p>
      <p className="mt-2">Each download is a snapshot. Use one snapshot per comparison to avoid double-counting. Archived answers belong to earlier wording. Older written Couples starters without a recorded option have no choice row.</p>
      <p className="mt-2">Tone feedback comes from reads created after the applicable tone and CSV permissions. Helpfulness is reported only for a reply the member said they sent. Missing feedback carries no positive or negative judgment.</p>
      <p className="mt-2">Your Type action rows require its separate research permission and the Individual CSV grant. They retain explicit likes, explicit passes, weaker viewed passes, issued portraits, prospective predictions and undo links with asset and model editions. New attempts also retain their explicitly self-reported sexuality, viewer gender and rated-audience snapshot; older or unverified snapshots remain unknown. Use attempt_code in conditions to separate retakes. Current profile edits never relabel past attempts. Private visual preferences are never copied into a partner’s Couples file.</p>
      <p className="mt-2">Access follows current record-level permissions at download time. Group-report participation alone does not grant access to individual rows. Codes are pseudonymous, and these files contain sensitive information.</p>
    </details>
  </section>;
}
