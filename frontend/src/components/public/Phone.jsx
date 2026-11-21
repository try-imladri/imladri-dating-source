import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, RotateCcw } from "lucide-react";
import { useConfig, signupPath } from "./PublicLayout";
import { setOrigin } from "@/lib/api";
import { storeDemoSubject } from "@/lib/startDraft";

const PEOPLE = { him: "Jake", her: "Mia" };

const OPENING = ["hey stranger", "been a crazy week haha", "we should hang soon tho"];
const TREE = [
  { you: "I'd like that. Does Thursday work?", tone: "Direct and warm", them: ["ugh Thursday's packed", "next week maybe?"], next: [
    { you: "Sure. Which day works for you?", tone: "Easygoing, still clear", them: ["Tuesday! I'll pick the spot", "sorry for going quiet btw"], learn: "Asking for a real plan turned a vague \"soon\" into Tuesday." },
    { you: "Next week works. You plan it this time.", tone: "Confident, sets expectations", them: ["haha fair", "Tuesday, 7, that taco place?"], learn: "Handing over the plan showed how much effort Sam will put in." },
  ] },
  { you: "Ha, sounds good. Let me know.", tone: "Chill, a bit cool", them: ["will do!!", "how's your week been?"], next: [
    { you: "Busy but good. Still waiting on that plan ;)", tone: "Flirty nudge", them: ["ok ok you got me", "Saturday?"], learn: "A light nudge got a plan without chasing." },
    { you: "Good! Talk soon.", tone: "Friendly but distant", them: ["...", "hey stranger"], learn: "Matching the energy showed the pattern: \"soon\" didn't mean anything yet." },
  ] },
  { you: "Haha who is this?", tone: "Playful tease", them: ["wow ok I deserve that", "it's Sam. the one who went quiet"], next: [
    { you: "Ah, Sam. What changed?", tone: "Calm, wants answers", them: ["honestly I was overwhelmed", "can I make it up to you? dinner Friday?"], learn: "Asking what changed got an explanation and a real offer." },
    { you: "Lol I know. What's up?", tone: "Very friendly", them: ["nothing much", "just bored tbh"], learn: "Keeping it light revealed Sam was reaching out out of boredom." },
  ] },
];

function useClock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 15000);
    return () => clearInterval(id);
  }, []);
  return {
    time: now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
    date: now.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }),
  };
}

function useChat() {
  const [msgs, setMsgs] = useState([]);
  const [typing, setTyping] = useState(false);
  const [path, setPath] = useState([]);
  const [waiting, setWaiting] = useState(true);
  const timers = useRef([]);
  const queue = (lines) => {
    setWaiting(true);
    lines.forEach((t, i) => {
      timers.current.push(setTimeout(() => setTyping(true), i * 1100 + 300));
      timers.current.push(setTimeout(() => {
        setTyping(false);
        setMsgs(messages => [...messages, { who: "them", t }]);
        if (i === lines.length - 1) setWaiting(false);
      }, i * 1100 + 1100));
    });
  };
  const reset = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    setMsgs([]); setPath([]); setTyping(false);
    queue(OPENING);
  };
  useEffect(() => {
    reset();
    return () => timers.current.forEach(clearTimeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const node = path.reduce((n, i) => n.next[i], { next: TREE });
  const pick = (i) => {
    const choice = node.next[i];
    setMsgs((m) => [...m, { who: "you", t: choice.you }]);
    setPath((p) => [...p, i]);
    queue(choice.them);
  };
  return { msgs, typing, options: !waiting && node.next ? node.next : [], learn: !waiting && node.learn, pick, reset };
}

function Bubble({ m }) {
  return <p className={`${m.who === "you" ? "bubble-you ml-auto" : "bubble-him"} px-3 py-2 text-[12px] leading-snug w-fit max-w-[82%]`}>{m.t}</p>;
}

export function Phone({ className = "" }) {
  const config = useConfig();
  const { time, date } = useClock();
  const [who, setWho] = useState("him");
  const name = PEOPLE[who];
  const say = (s) => s.replaceAll("Sam", name);
  const { msgs, typing, options, learn, pick, reset } = useChat();
  const switchTo = (w) => { if (w !== who) { setWho(w); reset(); } storeDemoSubject(w); };
  const end = useRef(null);
  useEffect(() => {
    if (end.current) { end.current.scrollTop = end.current.scrollHeight; }
  }, [msgs, typing, options.length, learn]);
  return (
    <div className={`relative aspect-[9/19] rounded-[2.75rem] border-[9px] border-[var(--ink)] bg-[var(--bg)] shadow-[0_50px_120px_-30px_rgba(0,0,0,.55)] overflow-hidden flex flex-col ${className}`} data-testid="home-phone">
      <div className="absolute top-2 left-1/2 -translate-x-1/2 w-12 h-4 sm:w-20 sm:h-5 rounded-full bg-[var(--ink)] z-10" />
      <div className="flex justify-between items-center px-4 sm:px-5 pt-2 sm:pt-2.5 font-mono text-[9px] sm:text-[10px] font-semibold shrink-0">
        <span data-testid="phone-time">{time}</span><span data-testid="phone-date">{date}</span>
      </div>
      <div className="flex items-center gap-2 px-4 pt-5 pb-2.5 border-b border-[var(--line)] shrink-0">
        <span className="w-7 h-7 sm:w-8 sm:h-8 shrink-0 rounded-full bg-[var(--ink)] text-[var(--bg)] font-display font-black inline-flex items-center justify-center">{name[0]}</span>
        <div className="leading-tight flex-1 min-w-0">
          <p className="text-xs sm:text-sm font-bold truncate" data-testid="phone-contact-name">{name}</p>
          <p className="hidden sm:block text-[10px] text-[var(--muted)]" data-testid="phone-status">{typing ? "typing..." : "online"}</p>
        </div>
        <div className="flex shrink-0 rounded-full border border-[var(--line)] p-0.5 text-[9px] sm:text-[10px] font-bold" role="tablist" data-testid="phone-gender-toggle">
          {Object.keys(PEOPLE).map((w) => (
            <button key={w} role="tab" aria-selected={who === w} onClick={() => switchTo(w)} className={`px-1.5 sm:px-2.5 py-0.5 sm:py-1 rounded-full capitalize transition-colors ${who === w ? "bg-[var(--ink)] text-[var(--bg)]" : "text-[var(--muted)] hover:text-[var(--ink)]"}`} data-testid={`phone-gender-${w}`}>{w}</button>
          ))}
        </div>
      </div>
      <div ref={end} className="flex-1 min-h-0 overflow-y-auto px-3 py-3 flex flex-col gap-1.5 [scrollbar-width:none]" data-testid="phone-thread">
        <div className="mt-auto" />
        {msgs.map((m, i) => (
          <motion.div key={i} initial={{ opacity: 0, y: 12, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ type: "spring", stiffness: 380, damping: 30 }} className={m.who === "you" ? "flex justify-end" : ""}>
            <Bubble m={{ ...m, t: say(m.t) }} />
          </motion.div>
        ))}
        {typing && <span className="typing bubble-him inline-flex gap-1 px-3 py-2.5 w-fit" data-testid="phone-typing"><span className="w-1.5 h-1.5 rounded-full bg-current" /><span className="w-1.5 h-1.5 rounded-full bg-current" /><span className="w-1.5 h-1.5 rounded-full bg-current" /></span>}
        {learn && (
          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="rounded-2xl border border-[var(--ink)] p-3 text-[11px] leading-snug mt-1" data-testid="phone-learn">
            <p className="font-mono text-[9px] uppercase tracking-widest text-[var(--muted)]">What you learned</p>
            <p className="mt-1">{say(learn)}</p>
          </motion.div>
        )}
      </div>
      <div className="shrink-0 border-t border-[var(--line)] p-2 sm:p-2.5 min-h-[3.25rem]">
        <AnimatePresence mode="wait">
          {options.length > 0 ? (
            <motion.div key={msgs.length} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="flex flex-col gap-1 sm:gap-1.5">
              <p className="nudge flex items-center gap-1 font-mono text-[8px] uppercase tracking-widest px-1 font-bold" data-testid="phone-pick-hint"><span className="w-1.5 h-1.5 rounded-full bg-[var(--ink)] animate-ping" />Your turn · pick a reply</p>
              {options.map((o, i) => (
                <button key={o.you} onClick={() => pick(i)} className="chase text-left text-[10px] sm:text-[11px] leading-snug rounded-2xl border border-[var(--ink)] px-2.5 sm:px-3 py-1 sm:py-1.5 hover:bg-[var(--ink)] hover:text-[var(--bg)] transition-colors" style={{ animationDelay: `${i * 0.25}s` }} data-testid={`phone-option-${i}`}>
                  <span className="block">{o.you}</span>
                  <span className="block font-mono text-[8px] uppercase tracking-widest opacity-60 mt-0.5" data-testid={`phone-option-${i}-tone`}>{o.tone}</span>
                </button>
              ))}
            </motion.div>
          ) : learn ? (
            <motion.div key="end" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="flex flex-col gap-1.5">
              <Link to={signupPath(config, "demo")} onClick={() => { setOrigin("demo"); storeDemoSubject(who); }} className="chase w-full h-9 rounded-full bg-[var(--ink)] text-[var(--bg)] text-[10px] sm:text-[11px] font-bold whitespace-nowrap inline-flex items-center justify-center gap-1.5" data-testid="phone-signup">{config.waitlist_mode ? "Join the waitlist" : "Get this for your real chats"}<ArrowRight className="w-3.5 h-3.5" /></Link>
              <button onClick={reset} className="w-full h-7 rounded-full border border-[var(--line)] text-[10px] font-semibold inline-flex items-center justify-center gap-1.5 hover:border-[var(--ink)] transition-colors" data-testid="phone-restart"><RotateCcw className="w-3 h-3" />Try another reply</button>
            </motion.div>
          ) : (
            <motion.div key="wait" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="h-8 rounded-full border border-[var(--line)] px-3 flex items-center text-[11px] text-[var(--muted)]">{name} is texting...</motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
