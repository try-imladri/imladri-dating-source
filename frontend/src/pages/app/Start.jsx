import { clearQuizHandoff, readQuizHandoff } from "@/lib/quizHandoff";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useOutletContext } from "react-router-dom";
import { toast } from "sonner";
import { ArrowRight, ChevronDown, X } from "lucide-react";
import { clearStartDraft, readDemoSubject, readStartDraft, storeDemoSubject, storeStartDraft } from "@/lib/startDraft";
import { SafeShot } from "@/components/app/SafeShot";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ShowWhy, ActionPicker } from "@/components/app/ShowWhy";
import { IndividualTone } from "@/components/app/ToneCoach";
import { QuickQuestion } from "@/components/app/QuickQuestion";
import { LimitNotice, messageLimit, personLimit, useMessageSend } from "@/components/app/PlanUsage";
import { errMsg, REL_GROUPS, REL_LABEL, STAGE_LABEL, stagesFor } from "@/lib/api";

function SpeakerCheck({ lines, setLines, name }) {
  return (
    <div className="space-y-2" data-testid="start-speaker-check">
      <p className="text-sm font-semibold">Check who said what</p>
      <p className="text-xs text-[var(--muted)]">Choose the speaker for each message. Remove messages from other people.</p>
      <div className="space-y-2 max-h-80 overflow-y-auto p-1">
        {lines.map((l, i) => <div key={i} className="rounded-xl border border-[var(--line)] p-2 space-y-1">
          <div className="flex gap-2 items-center"><select aria-label={`Speaker for message ${i + 1}`} value={l.sure && ["me", "them"].includes(l.speaker) ? l.speaker : "unknown"}
            data-testid={`start-line-${i}`} data-speaker={l.speaker} className="rounded border border-[var(--line)] bg-[var(--bg)] px-2 py-1 text-sm"
            onChange={e => setLines(lines.map((line, j) => i === j ? { ...line, speaker: e.target.value, sure: e.target.value !== "unknown" } : line))}>
            <option value="unknown">Choose speaker</option><option value="me">You</option><option value="them">{name}</option>
          </select><button type="button" className="text-xs underline" onClick={() => setLines(lines.filter((_, j) => j !== i))}>Remove</button></div>
          <p className="text-sm">{l.text}</p>
        </div>)}
      </div>
    </div>
  );
}

function Result({ res, api, reload }) {
  const { person: p, exchange: ex } = res;
  return (
    <div className="space-y-4" data-testid="start-result">
      <div>
        <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)]">Your first read</p>
        <h1 className="font-display font-black uppercase text-4xl leading-none mt-1">{p.nickname}</h1>
      </div>
      <IndividualTone ex={ex} api={api} />
      <ShowWhy ex={ex} api={api} reload={reload} nickname={p.nickname} />
      <ActionPicker ex={ex} api={api} reload={reload} />
      <QuickQuestion api={api} />
      <div className="flex flex-wrap gap-2 pt-2">
        <Link to={`/app/people?chat=${p.id}`} className="px-5 py-2.5 rounded-full bg-[var(--ink)] text-[var(--bg)] font-bold text-sm inline-flex items-center gap-2" data-testid="start-go-chat">Keep talking about {p.nickname}<ArrowRight className="w-4 h-4" /></Link>
        <Link to={`/app/person/${p.id}`} className="px-5 py-2.5 rounded-full border border-[var(--line)] text-sm" data-testid="start-go-person">See {p.nickname}'s page</Link>
      </div>
    </div>
  );
}

export default function Start() {
  const { me } = useOutletContext();
  return <StartForm key={[me.user_id, me.created_at, me.content_revision].join("/")} />;
}

function StartForm() {
  const { api, setMe, me } = useOutletContext();
  const [restored] = useState(() => readStartDraft(me));
  const send = useMessageSend();
  const limit = messageLimit(me.usage) || personLimit(me.usage);
  useEffect(() => {
    if (!limit && !me.onboarded) api.post("/me/event", {type: "onboarding_opened"}).catch(() => {});
  }, [api, limit, me.onboarded]);
  const nav = useNavigate();
  const [name, setName] = useState(restored.name ?? "");
  const [what, setWhat] = useState(restored.what ?? "");
  const [paste, setPaste] = useState(restored.paste ?? "");
  const [img, setImg] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [rel, setRel] = useState(restored.rel ?? "talking");
  const [stage, setStage] = useState(restored.stage ?? "early");
  const [lines, setLines] = useState(restored.lines ?? null);
  const uploadAttempt = useRef(restored.attempt || null);
  const request = useRef(restored.request || null);
  const [missingImage, setMissingImage] = useState(!!restored.hadImage && !restored.lines);
  const [sendError, setSendError] = useState("");
  const [demoSubject] = useState(readDemoSubject);
  const [who, setWho] = useState(restored.who || demoSubject);
  const fromDemo = !!demoSubject;
  const [quizResult, setQuizResult] = useState(readQuizHandoff);
  const pr = who === "her" ? "she's" : who === "him" ? "he's" : "they're";
  const tag = who === "her" ? "Her" : who === "him" ? "Him" : "Them";
  const draft = () => ({name, what, paste, rel, stage, who, lines, hadImage: !!img || missingImage,
    attempt: uploadAttempt.current, request: request.current});
  useEffect(() => {
    if (!res) storeStartDraft(me, {name, what, paste, rel, stage, who, lines, hadImage: !!img || missingImage,
      attempt: uploadAttempt.current, request: request.current});
  }, [name, what, paste, rel, stage, who, lines, img, missingImage, res, me]);
  const check = async () => {
    setBusy(true);
    try {
      uploadAttempt.current ??= crypto.randomUUID();
      const { data } = await api.post("/quickstart/parse", { nickname: name, conversation_text: img ? "" : paste, image_base64: img },
        { headers: { "X-Upload-Attempt": uploadAttempt.current } });
      setLines(data.lines);
    } catch (e) {
      toast.error(errMsg(e));
    }
    setBusy(false);
  };
  const hasConvo = !!img || !!paste.trim() || !!lines;
  const go = async (retrieve = false) => {
    setBusy(true);
    try {
      const body = retrieve ? JSON.parse(request.current.signature) : { nickname: name, what_happened: what, rel_type: rel, stage,
        gender: who === "him" ? "male" : who === "her" ? "female" : "", lines: hasConvo ? lines : [] };
      const signature = JSON.stringify(body);
      if (request.current?.signature !== signature) request.current = {signature, key: crypto.randomUUID()};
      storeStartDraft(me, draft());
      setSendError("");
      const { data } = await send("/quickstart", body, {
        ...(hasConvo && uploadAttempt.current ? {"X-Upload-Attempt": uploadAttempt.current} : {}),
        "Idempotency-Key": request.current.key
      });
      clearStartDraft();
      request.current = null;
      storeDemoSubject("");
      clearQuizHandoff(); setQuizResult(null);
      setRes(data);
      setMe((member) => ({ ...member, people_count: (member.people_count || 0) + 1 }));
    } catch (e) {
      setSendError(errMsg(e));
    }
    setBusy(false);
  };
  const reload = async () => {
    const { data } = await api.get(`/people/${res.person.id}/exchanges/${res.exchange.id}`);
    setRes({ ...res, exchange: data });
  };
  const unresolved = hasConvo && lines?.some(line => !line.sure || !["me", "them"].includes(line.speaker));
  return (
    <div className="max-w-2xl mx-auto" data-testid="start-page">
      <div className="card p-4 sm:p-6">
        {res ? <Result res={res} api={api} reload={reload} /> : limit ? <><LimitNotice reason={limit} />{request.current && <button type="button" disabled={busy} className="btn-ghost px-4 py-2 mt-2" onClick={() => go(true)}>Retrieve my last read</button>}{sendError && <p role="alert">{sendError}</p>}</> : (
          <form className="space-y-3 sm:space-y-4" onSubmit={(event) => {
            event.preventDefault();
            if (busy || missingImage || !name.trim() || !what.trim() || unresolved) return;
            if (hasConvo && !lines) check();
            else go();
          }}>
            <div>
              {quizResult && <aside className="text-sm mb-3 border-l-2 border-[var(--line)] pl-3"><p>You kept <a className="underline" href={"/types/"+quizResult.slug}>{quizResult.name}</a> from the three-question quiz.</p>
                <button type="button" className="underline" onClick={()=>{clearQuizHandoff();setQuizResult(null);}}>Remove this result</button></aside>}
              <h1 className="font-display font-black uppercase text-3xl sm:text-4xl leading-none" data-testid="start-title">Who's on your mind?</h1>
              <p className="text-sm text-[var(--muted)] mt-1.5">DeepSeek uses the details you share to suggest a next step.{me.usage && !me.usage.unlimited && " Your first read uses one reply."}</p>
            </div>
            <div>
              <label htmlFor="start-nickname" className="block text-sm font-semibold mb-1">Their nickname</label>
              <Input id="start-nickname" required value={name} maxLength={40} onChange={(e) => setName(e.target.value)} placeholder="e.g. Sam" data-testid="start-nickname" />
            </div>
            <div>
              <label htmlFor="start-what" className="block text-sm font-semibold mb-1">What happened?</label>
              <Textarea id="start-what" required rows={2} maxLength={1000} value={what} onChange={(e) => setWhat(e.target.value)} placeholder={`e.g. We had a great date, now ${pr} taking a day to reply`} data-testid="start-what" />
            </div>
            <div className="divide-y divide-[var(--line)] border-y border-[var(--line)]">
              <details className="group">
                <summary className="flex min-h-11 items-center justify-between gap-2 py-2 cursor-pointer list-none [&::-webkit-details-marker]:hidden" data-testid="start-relationship-toggle">
                  <span className="text-sm font-semibold">Relationship details <span className="text-xs text-[var(--muted)] font-normal">· {REL_LABEL[rel]}, {STAGE_LABEL[stage]}</span></span>
                  <ChevronDown aria-hidden="true" className="size-4 shrink-0 group-open:rotate-180" />
                </summary>
                <div className="grid sm:grid-cols-2 gap-3 pb-3">
                  <div><label htmlFor="start-rel" className="block text-sm font-semibold mb-1">Who are they to you?</label>
                    <select id="start-rel" value={rel} onChange={(e) => { setRel(e.target.value); setStage(stagesFor(e.target.value)[0].v); }} className="w-full h-10 rounded-md border border-[var(--line)] bg-transparent px-3 text-base sm:text-sm" data-testid="start-rel">
                      {REL_GROUPS.map((g) => <optgroup key={g.key} label={g.label}>{g.types.map((t) => <option key={t.v} value={t.v}>{t.l}</option>)}</optgroup>)}
                    </select></div>
                  <div><label htmlFor="start-stage" className="block text-sm font-semibold mb-1">Where things are</label>
                    <select id="start-stage" value={stage} onChange={(e) => setStage(e.target.value)} className="w-full h-10 rounded-md border border-[var(--line)] bg-transparent px-3 text-base sm:text-sm" data-testid="start-stage">
                      {stagesFor(rel).map((s) => <option key={s.v} value={s.v}>{s.l}</option>)}
                    </select></div>
                  <fieldset className="sm:col-span-2">
                    <legend className="text-sm font-semibold mb-1.5">You're texting {fromDemo && <span className="text-[var(--muted)] font-normal">(from the demo you tried)</span>}</legend>
                    <div className="flex flex-wrap gap-2" data-testid="start-who">
                      {[["him", "A guy"], ["her", "A girl"]].map(([v, l]) => (
                        <button key={v} type="button" aria-pressed={who === v} onClick={() => setWho(who === v ? "" : v)} className={`px-4 py-2 rounded-full border text-sm font-semibold transition-colors ${who === v ? "bg-[var(--ink)] text-[var(--bg)] border-[var(--ink)]" : "border-[var(--line)] hover:border-[var(--ink)]"}`} data-testid={`start-who-${v}`}>{l}</button>
                      ))}
                    </div>
                  </fieldset>
                </div>
              </details>
              <details className="group">
                <summary className="flex min-h-11 items-center justify-between gap-2 py-2 cursor-pointer list-none [&::-webkit-details-marker]:hidden" data-testid="start-conversation-toggle">
                  <span className="text-sm font-semibold">{hasConvo ? (img ? "Screenshot added" : "Messages added") : "Add messages or a screenshot"} <span className="text-xs text-[var(--muted)] font-normal">(optional)</span></span>
                  <ChevronDown aria-hidden="true" className="size-4 shrink-0 group-open:rotate-180" />
                </summary>
                <div className="pb-3">
                  {img ? (
                    <div className="relative inline-block" data-testid="start-image-preview">
                      <img src={img} alt="Screenshot selected for transcription" className="max-h-48 rounded-xl border border-[var(--line)]" />
                      <button type="button" onClick={() => { setImg(""); setLines(null); }} className="absolute -top-2 -right-2 w-7 h-7 rounded-full bg-[var(--ink)] text-[var(--bg)] grid place-items-center" aria-label="Remove screenshot" data-testid="start-image-remove"><X className="w-3.5 h-3.5" /></button>
                    </div>
                  ) : (
                    <>
                      <label htmlFor="start-paste" className="block text-sm font-semibold mb-1">Paste the conversation</label>
                      <Textarea id="start-paste" rows={3} maxLength={8000} value={paste} onChange={(e) => { setPaste(e.target.value); setMissingImage(false); setLines(null); }} placeholder={`${tag}: had fun last night\nMe: me too! same time next week?`} data-testid="start-paste" />
                      <div className="mt-2 [&_input[type=file]]:block [&_input[type=file]]:sr-only [&_label:focus-within]:outline" data-testid="start-screenshot"><SafeShot compact pickLabel="Or add a screenshot" label="Use this version" onConfirm={(v) => { setImg(v); setMissingImage(false); setLines(null); }} /></div>
                    </>
                  )}
                </div>
              </details>
            </div>
            {hasConvo && !lines && <p className="text-xs text-[var(--muted)]">The next step sends {img ? "the screenshot shown above" : "your pasted text and their nickname"} to DeepSeek to identify who said what.</p>}
            {lines && hasConvo && <SpeakerCheck lines={lines} setLines={setLines} name={name || "Them"} />}
            {missingImage && <p className="text-sm" role="status">Your text was restored. Choose your screenshot again, or <button type="button" className="underline" onClick={()=>setMissingImage(false)}>continue without it</button>.</p>}
            {sendError && <p role="alert" className="text-sm">{sendError} Your draft is ready to retry.</p>}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <button type="submit" disabled={busy || missingImage || !name.trim() || !what.trim() || unresolved} className="flex-auto py-3 px-4 rounded-full bg-[#15803d] text-white font-bold disabled:opacity-40 inline-flex items-center justify-center gap-2" data-testid="start-submit">
                {busy ? (hasConvo && !lines ? "Reading the messages…" : "Reading it…") : <>{hasConvo && !lines ? "Next: check who said what" : "Get my read"}<ArrowRight className="w-4 h-4" /></>}
              </button>
              <button type="button" onClick={async () => { const { data } = await api.patch("/me", { onboarded: true }); clearStartDraft(); setMe((member) => ({ ...member, ...data })); nav("/app"); }} className="min-h-11 text-xs text-[var(--muted)] hover:text-[var(--ink)]" data-testid="start-skip">Skip for now</button>
            </div>
            {(name || what || paste || lines) && <p className="text-xs text-[var(--muted)]">This tab keeps your text and speaker edits for one hour. <button type="button" className="underline" onClick={() => {
              clearStartDraft(); request.current = null; uploadAttempt.current = null;
              setName(""); setWhat(""); setPaste(""); setLines(null); setImg(""); setMissingImage(false);
              setRel("talking"); setStage("early"); setWho(""); setSendError("");
            }}>Discard draft</button></p>}
          </form>
        )}
      </div>
    </div>
  );
}
