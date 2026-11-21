import { useEffect, useState } from "react";
import { toast } from "sonner";
import { errMsg } from "@/lib/api";

export function QuickQuestion({ api }) {
  const [q, setQ] = useState(null);
  const [done, setDone] = useState(false);
  useEffect(() => {
    api.get("/me/next-question").then((r) => setQ(r.data.question)).catch(() => {});
  }, [api]);
  if (!q || done) return null;
  const send = async (choice) => {
    try {
      await api.post("/me/quick-answer", { qid: q.id, choice, question: q.q, label: choice === null ? null : q.options[choice] });
      if (choice !== null) toast.success("Saved to Personal");
      setDone(true);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  return (
    <div className="rounded-2xl border border-dashed border-[var(--ink)]/40 p-3" data-testid="quick-question">
      <p className="text-sm"><span className="font-semibold">Quick one:</span> {q.q}</p>
      <div className="flex flex-wrap gap-1.5 mt-2">
        {q.options.map((o, i) => <button key={o} onClick={() => send(i)} className="px-3 py-1.5 rounded-full text-xs border border-[var(--line)] hover:border-[var(--ink)] transition-colors" data-testid={`quick-question-option-${i}`}>{o}</button>)}
        <button onClick={() => send(null)} className="px-2 py-1.5 text-xs text-[var(--muted)] hover:text-[var(--ink)]" data-testid="quick-question-skip">Skip</button>
      </div>
    </div>
  );
}
