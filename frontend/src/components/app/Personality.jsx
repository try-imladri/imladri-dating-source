import { useEffect, useRef, useState } from "react";
import { Download, Share2, RotateCw } from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer } from "recharts";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { pad } from "@/lib/api";

export const CODES = ["INFJ", "INFP", "ENFJ", "ENFP", "INTJ", "INTP", "ENTJ", "ENTP", "ISFJ", "ISFP", "ESFJ", "ESFP", "ISTJ", "ISTP", "ESTJ", "ESTP"];
const POLES = { EI: ["Outgoing", "Reserved"], NS: ["Big picture", "Details"], FT: ["Heart", "Head"], JP: ["Planner", "Go with the flow"] };

function dimensionCaption(d, selfSet) {
  const poles = POLES[d.pair];
  if (selfSet) return `${d.letter} · ${poles[d.pair.indexOf(d.letter)]}`;
  if (!d.answered || !Number.isFinite(d.first_pct)) return `${poles.join(" / ")} · Unanswered`;
  if (!d.letter && d.first_pct === 50) return `${poles.join(" / ")} · Evenly split`;
  return "";
}

export function pendingTypeText(p) {
  if (p.dims.some((d) => d.answered && !d.letter && d.first_pct === 50)) {
    return "Your answers are evenly split on at least one type preference.";
  }
  return p.dims.some((d) => d.answered)
    ? "Your type appears when your answers point to one side of each of the four preferences."
    : "Start with the four type questions in The basics to explore your preferences.";
}

export function RelationshipAxes({ axes, compact = false }) {
  const measured = axes.filter((row) => row.answered > 0 && Number.isFinite(row.score));
  if (!measured.length) return <p className="text-sm text-[var(--muted)]">Your relationship chart starts with an answer about how you communicate or relate.</p>;
  const evidence = <ul className="space-y-1 text-xs">
    {axes.map(({ axis, score, answered }) => <li key={axis} className="flex flex-wrap justify-between gap-x-2">
      <span>{axis}</span><span className="text-[var(--muted)]">{answered && Number.isFinite(score)
        ? `${score}/100 · ${answered} ${answered === 1 ? "answer" : "answers"}` : "Unanswered"}</span>
    </li>)}
  </ul>;
  if (measured.length < axes.length) return evidence;
  return <div className="w-full">
    <div className={compact ? "h-36" : "h-52"} aria-hidden="true">
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart data={axes} outerRadius={compact ? "50%" : "60%"}>
          <PolarGrid stroke="var(--line)" />
          <PolarAngleAxis dataKey="axis" tick={{ fill: "var(--muted)", fontSize: compact ? 9 : 10 }} />
          <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} />
          <Radar dataKey="score" stroke="var(--ink)" fill="var(--ink)" fillOpacity={0.25} />
        </RadarChart>
      </ResponsiveContainer>
    </div>
    <details className="text-xs"><summary className="cursor-pointer">Scores and answer counts</summary>{evidence}</details>
  </div>;
}

export function DimBars({ dims, selfSet = false }) {
  return (
    <div className="space-y-2.5" data-testid="personality-dims">
      {dims.map((d) => {
        const caption = dimensionCaption(d, selfSet);
        if (caption) return <p key={d.pair} className="text-xs text-[var(--muted)]">{caption}</p>;
        return <div key={d.pair}>
          <div className="flex justify-between text-[11px] mb-1"><span className={d.letter === d.pair[0] ? "font-semibold" : "text-[var(--muted)]"}>{d.pair[0]} · {POLES[d.pair][0]}</span><span className={d.letter === d.pair[1] ? "font-semibold" : "text-[var(--muted)]"}>{POLES[d.pair][1]} · {d.pair[1]}</span></div>
          <div role="meter" aria-label={POLES[d.pair].join(" / ")} aria-valuemin={0} aria-valuemax={100}
            aria-valuenow={d.first_pct} aria-valuetext={`${d.first_pct} out of 100 toward ${POLES[d.pair][0]}, based on ${d.answered} scored ${d.answered === 1 ? "answer" : "answers"}`}
            className="h-2 rounded-full bg-[var(--line)] relative overflow-hidden"><div className="absolute inset-y-0 left-0 bg-[var(--ink)] rounded-full transition-all duration-700" style={{ width: `${d.first_pct}%` }} /></div>
        </div>;
      })}
    </div>
  );
}

function wrap(x, text, maxW) {
  const out = [];
  let line = "";
  text.split(" ").forEach((w) => {
    const t = line ? `${line} ${w}` : w;
    if (x.measureText(t).width > maxW && line) {
      out.push(line);
      line = w;
    } else line = t;
  });
  if (line) out.push(line);
  return out;
}

export const CARD_STYLES = {
  noir: { label: "Noir", bg: "#0a0a0a", ink: "#f5f5f0" },
  paper: { label: "Paper", bg: "#f5f5f0", ink: "#0a0a0a" },
  blush: { label: "Blush", bg: "#f9d5dc", ink: "#3a0d18" },
  cherry: { label: "Cherry", bg: "#b3122e", ink: "#fff4f1" },
  lilac: { label: "Lilac", bg: "#d9ccf5", ink: "#1f1440" },
  sage: { label: "Sage", bg: "#cfdcc8", ink: "#16241a" },
};

function storedCardStyle() {
  try {
    const value = localStorage.getItem("imaldri_card_style");
    return Object.hasOwn(CARD_STYLES, value) ? value : "noir";
  } catch { return "noir"; }
}

async function drawCard(p, tags, style) {
  await document.fonts?.ready;
  const c = document.createElement("canvas");
  c.width = 1080;
  c.height = 1920;
  const x = c.getContext("2d");
  const { bg, ink } = CARD_STYLES[style];
  x.fillStyle = bg;
  x.fillRect(0, 0, 1080, 1920);
  x.fillStyle = ink;
  x.font = "900 64px 'Barlow Condensed'";
  x.fillText("IMLADRI.", 90, 160);
  x.font = "500 34px monospace";
  x.globalAlpha = 0.6;
  x.fillText(p.self_set ? "MY CHOSEN TYPE" : "MY TYPE", 90, 330);
  x.globalAlpha = 1;
  x.font = "900 360px 'Barlow Condensed'";
  x.fillText(p.code, 80, 650);
  x.font = "900 96px 'Barlow Condensed'";
  x.fillText(p.name.toUpperCase(), 90, 790);
  x.font = "400 46px sans-serif";
  wrap(x, p.line, 900).forEach((l, i) => x.fillText(l, 90, 890 + i * 62));
  let y = 1130;
  p.dims.forEach((d) => {
    x.font = "600 30px sans-serif";
    x.globalAlpha = 0.75;
    const caption = dimensionCaption(d, p.self_set);
    if (caption) x.fillText(caption, 90, y);
    else {
      x.fillText(`${d.pair[0]}  ${POLES[d.pair][0]}`, 90, y);
      const r = `${POLES[d.pair][1]}  ${d.pair[1]}`;
      x.fillText(r, 990 - x.measureText(r).width, y);
      x.globalAlpha = 0.2;
      x.fillRect(90, y + 20, 900, 18);
      x.globalAlpha = 1;
      x.fillRect(90, y + 20, 900 * d.first_pct / 100, 18);
    }
    x.globalAlpha = 1;
    y += 110;
  });
  x.font = "500 38px sans-serif";
  wrap(x, tags.slice(0, 4).join("  ·  "), 900).slice(0, 2).forEach((l, i) => x.fillText(l, 90, 1620 + i * 54));
  x.font = "900 54px 'Barlow Condensed'";
  x.fillText("WHAT'S YOUR TYPE? IMLADRI.COM", 90, 1800);
  return c;
}

export function TypeCard({ p, tags }) {
  const [image, setImage] = useState(null);
  const [style, setStyle] = useState(storedCardStyle);
  const [attempt, setAttempt] = useState(0);
  const currentImage = image?.p === p && image.tags === tags && image.style === style && image.attempt === attempt ? image : null;
  const url = currentImage?.url;
  useEffect(() => {
    let current = true;
    try { localStorage.setItem("imaldri_card_style", style); } catch { /* Keep the session's selected color. */ }
    if (p.code) drawCard(p, tags, style).then((c) => {
      if (!current) return;
      const png = c.toDataURL("image/png");
      if (!png.startsWith("data:image/png")) throw new Error("Card image unavailable");
      setImage({ p, tags, style, attempt, url: png });
    }).catch(() => { if (current) setImage({ p, tags, style, attempt, error: true }); });
    return () => { current = false; };
  }, [p, tags, style, attempt]);
  if (!p.code) return <div className="aspect-[9/16] max-h-[440px] rounded-2xl border-2 border-dashed border-[var(--line)] flex items-center justify-center text-sm text-[var(--muted)] p-6 text-center" data-testid="type-card-empty">Your shareable card appears when the quiz suggests a four-letter type or you choose your own.</div>;
  return (
    <div className="space-y-3" data-testid="type-card">
      <div className="flex justify-center gap-1.5" data-testid="card-styles">
        {Object.entries(CARD_STYLES).map(([k, s]) => (
          <button key={k} onClick={() => setStyle(k)} title={s.label} aria-label={s.label} aria-pressed={style === k} data-testid={`card-style-${k}`}
            className={`w-7 h-7 rounded-full border-2 transition-transform hover:scale-110 ${style === k ? "border-[var(--ink)] scale-110" : "border-[var(--line)]"}`} style={{ background: `linear-gradient(135deg, ${s.bg} 50%, ${s.ink} 50%)` }} />
        ))}
      </div>
      {url && <img src={url} alt={`My type ${p.code}`} className="h-[min(380px,calc(100vh-22rem))] min-h-[260px] max-w-full aspect-[9/16] object-contain rounded-2xl border border-[var(--line)] mx-auto" data-testid="type-card-image" />}
      {currentImage?.error && <p role="alert" className="text-sm">Your card image couldn’t be created. <button type="button" className="underline" onClick={() => setAttempt(n => n + 1)}>Try again</button></p>}
      {!currentImage && <p role="status" className="text-sm">Preparing your card…</p>}
      {url && <div className="flex gap-2 justify-center">
        <a href={url} download={`my-type-${p.code}.png`} className="btn-lime px-4 py-2 text-sm inline-flex items-center gap-1.5" data-testid="type-card-download"><Download className="w-4 h-4" />Download</a>
        <button onClick={() => shareFile(url, `my-type-${p.code}.png`, `My type: ${p.code}`).catch(() => toast.error("Couldn’t share your card. Use Download to save it."))}
          className="btn-ghost px-4 py-2 text-sm inline-flex items-center gap-1.5" data-testid="type-card-share"><Share2 className="w-4 h-4" />Share card</button>
      </div>}
    </div>
  );
}

async function shareFile(url, name, title) {
  const blob = await (await fetch(url)).blob();
  const file = new File([blob], name, { type: "image/png" });
  if (navigator.canShare?.({ files: [file] })) {
    try { await navigator.share({ files: [file], title, text: "What's your type? https://imladri.com" }); }
    catch (error) { if (error.name !== "AbortError") throw error; }
  } else {
    toast.message("Sharing isn't supported here. The card was downloaded instead.");
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
  }
}

function matchGroup(score) {
  if (score >= 75) return "Closer fit";
  return score >= 45 ? "Some overlap" : "Less overlap";
}

function displayRanks(items) {
  let rank = 0;
  return items.map((item, i) => {
    if (items[i - 1]?.score !== item.score) rank = i + 1;
    const tied = items[i - 1]?.score === item.score || items[i + 1]?.score === item.score;
    return { ...item, rank, tied };
  });
}

async function shareTop3(code, top, cutoffTie) {
  await document.fonts?.ready;
  const c = document.createElement("canvas");
  c.width = 1080;
  c.height = 1920;
  const x = c.getContext("2d");
  const { bg, ink } = CARD_STYLES[storedCardStyle()];
  x.fillStyle = bg;
  x.fillRect(0, 0, 1080, 1920);
  x.fillStyle = ink;
  x.font = "900 64px 'Barlow Condensed'";
  x.fillText("IMLADRI.", 90, 160);
  x.font = "500 34px monospace";
  x.globalAlpha = 0.6;
  x.fillText(`${code} · ${cutoffTie ? "THREE OF MY CLOSEST MATCHES" : "MY TOP 3 MATCHES"}`, 90, 330);
  x.globalAlpha = 1;
  x.font = "900 150px 'Barlow Condensed'";
  x.fillText("MY BEST", 80, 500);
  x.fillText("FITS ARE", 80, 640);
  top.forEach((t, i) => {
    const y = 860 + i * 320;
    x.globalAlpha = 0.25;
    x.fillRect(90, y - 70, 900, 2);
    x.globalAlpha = 1;
    x.font = "900 120px 'Barlow Condensed'";
    x.fillText(`${t.rank}`, 90, y + 60);
    x.font = "900 72px 'Barlow Condensed'";
    x.fillText(`#${pad(t.number)} ${t.name}`.toUpperCase(), 200, y + 20, 790);
    x.font = "400 36px sans-serif";
    wrap(x, t.line, 780).slice(0, 2).forEach((l, k) => x.fillText(l, 200, y + 80 + k * 46));
    x.font = "600 40px monospace";
    x.fillText(`${matchGroup(t.score)}${t.tied ? " · Tied" : ""}`, 200, y + 195);
  });
  x.font = "900 54px 'Barlow Condensed'";
  x.fillText("WHO'S YOUR MATCH? IMLADRI.COM", 90, 1800);
  await shareFile(c.toDataURL("image/png"), `my-matches-${code}.png`, `My archetype matches as ${code}`);
}

export function TypeCompare({ api, summary }) {
  const code = summary.personality.code;
  const [comparison, setComparison] = useState(null);
  const [g, setG] = useState("male");
  const [retry, setRetry] = useState(0);
  const d = comparison?.summary === summary && comparison.gender === g && comparison.api === api && comparison.retry === retry
    ? comparison.result : null;
  useEffect(() => {
    let active = true;
    const receive = result => {
      if (active) setComparison({result, summary, gender:g, api, retry});
    };
    if (code) api.get(`/me/type-compare?gender=${g}`).then(
      ({ data }) => receive(data),
      (error) => receive({error:error.response?.status || "load"}),
    );
    return () => { active = false; };
  }, [api, summary, code, g, retry]);
  const ranked = displayRanks(d?.items || []);
  const cutoffTie = ranked.length > 3 && ranked[2].rank === ranked[3].rank;
  return (
    <section className="card p-5 flex flex-col min-h-0 h-full" data-testid="type-compare">
      <div className="flex items-start justify-between gap-3">
        <div><p className="font-display font-black uppercase text-xl">Your type vs every archetype</p>
        <p className="text-[11px] text-[var(--muted)] mb-2">Ordered by estimated fit with your saved type and profile. Equal estimates share a rank and appear in archetype number order. Open an archetype for examples and a related type.</p>
        <span className="inline-flex p-0.5 rounded-full border border-[var(--line)] mb-3" data-testid="type-compare-gender">
          {[["male", "Men"], ["female", "Women"]].map(([k, l]) => <button key={k} type="button" aria-pressed={g === k} onClick={() => setG(k)} className={`px-3 py-0.5 rounded-full text-[11px] font-semibold transition-colors ${g === k ? "bg-[var(--ink)] text-[var(--bg)]" : "text-[var(--muted)]"}`} data-testid={`type-compare-gender-${k}`}>{l}</button>)}
        </span></div>
        {ranked.length >= 3 && <button onClick={() => shareTop3(d?.code || code, ranked.slice(0, 3), cutoffTie).catch(() => toast.error("Couldn’t share these matches. Try again."))} className="btn-lime px-3 py-1.5 text-xs inline-flex items-center gap-1.5 shrink-0" data-testid="type-compare-share"><Share2 className="w-3.5 h-3.5" />{cutoffTie ? "Share three matches" : "Share my top 3"}</button>}
      </div>
      {d?.code && d.code !== code && <p role="status" className="mb-2 text-sm">Your saved type changed to {d.code}. These matches use {d.code}. Reopen your portrait to see your updated type.</p>}
      {!code ? <p className="text-sm text-[var(--muted)]" data-testid="type-compare-empty">Reveal your type first.</p>
        : !d ? <p className="text-sm text-[var(--muted)] font-mono" data-testid="type-compare-loading">Ranking all archetypes for {code}… (about 20s the first time)</p>
        : d.error ? <div className="space-y-2 text-sm" role="status">
          {d.error === 402 ? <p>Your reply allowance is used up. <Link className="underline" to="/app/settings/upgrade">View plans</Link></p>
            : <><p>{d.error === 409 ? "Your profile or the archetype directory changed while this comparison was running." : "The full comparison couldn’t be loaded."}</p><button type="button" className="btn-ghost px-3 py-1.5" onClick={() => setRetry((value) => value + 1)}>Try again</button></>}
        </div>
        : d.code === "" ? <p className="text-sm text-[var(--muted)]">Your saved profile no longer has a type. Return to Personal to update it.</p>
        : !d.items.length ? <p className="text-sm text-[var(--muted)]">No archetypes have been released for this group yet.</p> : (
          <ol className="overflow-auto flex-1 min-h-0 space-y-1.5 pr-1 max-h-[420px] lg:max-h-none">
            {ranked.map(x => (
              <li key={x.number} value={x.rank} data-testid={`type-compare-${x.number}`}>
                <Link to={`/types/${x.slug}`} className="flex items-center gap-3 p-2 rounded-xl hover:bg-[var(--surface-2)] transition-colors">
                  <span className="font-mono text-[10px] w-8 shrink-0 text-[var(--muted)]"><span className="sr-only">Rank </span>{x.rank}{x.tied && <span className="block">Tied</span>}</span>
                  <span className="min-w-0 flex-1"><span className="block text-sm font-semibold break-words">#{pad(x.number)} {x.name}</span><span className="block text-[11px] text-[var(--muted)]">{x.line}</span>
                    {x.people?.length > 0 && <span className="flex flex-wrap gap-1 mt-1">{x.people.map((pp) => <span key={pp.id} className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--emerald)] text-[var(--on-accent)] font-semibold" data-testid={`type-compare-person-${pp.id}`}>{pp.nickname}</span>)}</span>}</span>
                  <span className="w-20 shrink-0 text-right text-xs">{matchGroup(x.score)}</span>
                </Link>
              </li>
            ))}
          </ol>
        )}
    </section>
  );
}

function FlipCard({ summary, radar, onPick }) {
  const p = summary.personality;
  const [back, setBack] = useState(false);
  const frontTitle = useRef(null), backTitle = useRef(null), turned = useRef(false);
  useEffect(() => {
    if (turned.current) (back ? backTitle : frontTitle).current?.focus({preventScroll:true});
  }, [back]);
  function turn(showBack) {
    turned.current = true;
    setBack(showBack);
  }
  const face = "absolute inset-0 card p-5 [backface-visibility:hidden] flex flex-col overflow-auto";
  return (
    <div className="[perspective:1600px] h-[560px] lg:h-full" data-testid="personality-flip">
      <div className={`relative w-full h-full transition-transform duration-700 [transform-style:preserve-3d] ${back ? "[transform:rotateY(180deg)]" : ""}`}>
        <section inert={back} className={`${face} gap-2.5 pb-4`} data-testid="personality-front">
          <h2 ref={frontTitle} tabIndex={-1} className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)]">{p.self_set ? "The type you picked" : "From your answers"}</h2>
          {p.code ? (
            <div><p className="font-display font-black text-5xl 2xl:text-6xl leading-none" data-testid="personality-code">{p.code}</p><p className="font-display font-black uppercase text-2xl mt-1" data-testid="personality-name">{p.name}</p><p className="text-sm text-[var(--muted)] mt-1">{p.line}</p></div>
          ) : <p className="text-sm text-[var(--muted)]" data-testid="personality-pending">{pendingTypeText(p)}</p>}
          <DimBars dims={p.dims} selfSet={p.self_set} />
          <div>
            <p className="text-xs font-semibold mb-1.5">Already know your type?</p>
            <Select value={p.self_set ? p.code : "quiz"} onValueChange={(v) => onPick(v === "quiz" ? "" : v)}>
              <SelectTrigger className="h-9" data-testid="personality-select"><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value="quiz">Use my quiz result</SelectItem>{CODES.map((c) => <SelectItem key={c} value={c} data-testid={`personality-opt-${c}`}>{c}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <button onClick={() => turn(true)} className="mt-auto btn-ghost py-2 text-sm inline-flex items-center justify-center gap-2 shrink-0" data-testid="personality-flip-to-back"><RotateCw className="w-4 h-4" />See how you relate</button>
        </section>
        <section inert={!back} className={`${face} [transform:rotateY(180deg)]`} data-testid="personality-radar">
          <h2 ref={backTitle} tabIndex={-1} className="font-display font-black uppercase text-xl">How you relate</h2>
          <div className="flex-1 min-h-[240px] pt-3"><RelationshipAxes axes={radar} /></div>
          <div className="flex flex-wrap gap-1 mb-3">{summary.tags.map((t) => <span key={t} className="text-[11px] px-2 py-0.5 rounded-full border border-[var(--line)]">{t}</span>)}</div>
          <button onClick={() => turn(false)} className="btn-ghost py-2 text-sm inline-flex items-center justify-center gap-2" data-testid="personality-flip-to-front"><RotateCw className="w-4 h-4" />Back to my type</button>
        </section>
      </div>
    </div>
  );
}

function ScoreSources({ quiz, savedAnswers }) {
  if (!quiz || !savedAnswers) return null;
  const groups = new Map();
  for (const set of ["short", "extended", "further"]) {
    for (const question of quiz[set] || []) {
      const option = question.options[savedAnswers[question.id]?.choice];
      if (!option) continue;
      for (const [axis, weight] of [[question.axis, option.axis_score], [question.dim, option.type_score]]) {
        if (!axis || !Number.isInteger(weight)) continue;
        if (!groups.has(axis)) groups.set(axis, []);
        groups.get(axis).push({ id:question.id, prompt:question.q, answer:option.label, weight });
      }
    }
  }
  return <details className="card p-4 mt-4 text-sm" data-testid="score-sources">
    <summary className="font-semibold cursor-pointer">Answers behind your scores</summary>
    <p className="text-xs mt-2 text-[var(--muted)]">Scoring version {quiz.scoring_version || 1}. Each selected answer below contributes 1–4 points. Unanswered and descriptive choices contribute nothing. A score describes your current responses.</p>
    {[...groups].map(([name, rows]) => <details key={name} className="mt-2"><summary className="cursor-pointer">{name} · {rows.length} scored {rows.length === 1 ? "answer" : "answers"}</summary>
      <ul className="mt-1 pl-4 space-y-1">{rows.map(r => <li key={r.id}>{r.prompt} <strong>{r.answer}</strong> · {r.weight}/4</li>)}</ul></details>)}
    {!groups.size && <p className="mt-2">Your scored answers will appear here as you save them.</p>}
  </details>;
}

export function PersonalityPanel({ summary, radar, onPick, api, quiz, savedAnswers }) {
  const p = summary.personality;
  return (
    <><div className="grid grid-cols-1 lg:grid-cols-[1fr_1.2fr_auto] gap-4 items-stretch [&>*]:min-w-0 lg:h-[calc(100vh-15.5rem)] 2xl:h-[calc(100vh-14.5rem)] lg:min-h-[440px] lg:[&>*]:min-h-0" data-testid="personality-panel">
      <FlipCard summary={summary} radar={radar} onPick={onPick} />
      <TypeCompare api={api} summary={summary} />
      <section className="card p-4 h-full"><TypeCard p={p} tags={summary.tags} /></section>
    </div><ScoreSources quiz={quiz} savedAnswers={savedAnswers} /></>
  );
}
