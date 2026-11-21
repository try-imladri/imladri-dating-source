import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";
import { Check, ArrowRight } from "lucide-react";

const STEPS = ["About you", "Add your first person", "Share a conversation", "Get the report"];
const HINT = { 1: "Answer the quick questions", 2: "Add the person on your mind", 3: "Share a conversation", 4: "Generate the report" };

function target(me) {
  const pid = me.guide_person_id;
  return [null, "/app/personal", "/app/people?add=1", pid ? `/app/people?chat=${pid}` : "/app/people", pid ? `/app/person/${pid}?tab=report` : "/app/people"][me.onboarding_step || 1];
}

function useMeRefresh(me, api, setMe) {
  const { pathname, search } = useLocation();
  useEffect(() => {
    api.get("/me").then((r) => setMe(r.data)).catch(() => {});
  }, [pathname, search]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (me.onboarded) return;
    const t = setInterval(() => api.get("/me").then((r) => setMe(r.data)).catch(() => {}), 8000);
    return () => clearInterval(t);
  }, [me.onboarded]); // eslint-disable-line react-hooks/exhaustive-deps
}

export function GuideBar({ me, api, setMe }) {
  useMeRefresh(me, api, setMe);
  if (me.onboarded) return null;
  const step = me.onboarding_step || 1;
  return (
    <div className="flex items-center gap-3 min-w-0 flex-1 justify-end lg:flex-none lg:absolute lg:left-1/2 lg:-translate-x-1/2 overflow-hidden" data-testid="guide-bar">
      <ol className="hidden lg:flex items-center gap-2 min-w-0">
        {STEPS.map((s, i) => {
          const n = i + 1;
          const done = n < step;
          return (
            <li key={s} className="flex items-center gap-1.5 shrink-0" data-testid={`guide-step-${n}`}>
              <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-mono border ${done ? "bg-[var(--ink)] text-[var(--bg)] border-[var(--ink)]" : n === step ? "border-[var(--ink)]" : "border-[var(--line)] text-[var(--muted)]"}`}>{done ? <Check className="w-3 h-3" /> : n}</span>
              <span className={`text-xs ${n === step ? "font-semibold" : "text-[var(--muted)] hidden 2xl:inline"}`}>{s}</span>
              {n < 4 && <span className="w-4 h-px bg-[var(--line)]" />}
            </li>
          );
        })}
      </ol>
      <span className="lg:hidden font-mono text-[10px] text-[var(--muted)] shrink-0 hidden sm:inline" data-testid="guide-step-mobile">{step}/4</span>
      <Link to={target(me)} className="btn-lime px-3 py-1.5 text-xs inline-flex items-center gap-1 min-w-0 shrink-0 sm:shrink lg:shrink-0" data-testid="guide-next"><span className="truncate hidden sm:inline">{HINT[step]}</span><span className="sm:hidden whitespace-nowrap">{step}/4</span><ArrowRight className="w-3.5 h-3.5 shrink-0 hidden sm:inline" /></Link>
    </div>
  );
}
