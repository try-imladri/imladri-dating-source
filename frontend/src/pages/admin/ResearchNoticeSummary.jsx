import { useEffect, useState } from "react";

const scopes = [["questionnaire", "Questionnaires & intimacy"], ["your_type", "Your Type"],
  ["tone", "Conversation tone"], ["member_csv", "Individual CSV"], ["couple_csv", "Current Couples CSV"],
  ["staff_history", "Staff answer history"], ["trends", "Everyday statistics"],
  ["couples_statistics", "Couples statistics"], ["couples_return", "Couples return statistics"]];
const count = value => Number.isSafeInteger(value) && value >= 0;

function valid(data) {
  return data?.notice_version === "main-consent-v1" && /^[a-f0-9]{64}$/.test(data.notice_sha256)
    && ["members", "reviewed", "unreviewed", "current_context_complete"].every(key => count(data[key]))
    && data.reviewed + data.unreviewed === data.members && data.current_context_complete <= data.reviewed && scopes.every(([key]) => {
    const row = data.scopes?.[key];
    return row && ["eligible", "reviewed", "unreviewed", "enabled"].every(name => count(row[name]))
      && ["allow", "decline", "unavailable"].every(name => count(row.decisions?.[name]))
      && row.eligible <= data.members && row.reviewed + row.unreviewed === row.eligible
      && row.decisions.allow + row.decisions.decline + row.decisions.unavailable === row.reviewed
      && row.enabled <= row.eligible;
  });
}

export default function ResearchNoticeSummary({ api }) {
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError(false);
    api.get("/admin/main-consent", { signal: controller.signal }).then(({ data: result }) => {
      if (!valid(result)) throw Error("Invalid notice summary");
      if (!controller.signal.aborted) setData(result);
    }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [api, attempt]);

  return <section className="card space-y-3 p-4 sm:p-5" aria-label="Main data and research consent">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="font-display text-xl font-bold uppercase">Data & research consent</h2>
      <button type="button" className="btn-ghost px-3 py-1.5 text-sm" onClick={() => setAttempt(value => value + 1)}>Refresh consent counts</button>
    </div>
    <p className="text-sm text-[var(--muted)]">The main consent screen records each chosen purpose. Its earlier decisions and current permissions are separate: later changes in Settings can withdraw a grant. These operational totals do not contain answers or measure the research sample size. Current-partner assessment sharing has its own pair-specific decisions and is not counted here.</p>
    {!data && !error && <p role="status" className="text-sm">Loading consent counts…</p>}
    {error && <p role="alert" className="text-sm">Consent counts could not load. Refresh to try again.</p>}
    {data && <>
      <p className="text-xs text-[var(--muted)]">{data.members} current members · {data.reviewed} reviewed the current main notice · {data.unreviewed} not reviewed · {data.current_context_complete} reviewed for their current pairing context. Staff and closed accounts are excluded. Eligibility varies by purpose.</p>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {scopes.map(([key, label]) => {
          const row = data.scopes[key];
          return <div key={key} className="rounded-xl border border-[var(--line)] p-3 space-y-3">
            <h3 className="font-semibold">{label}</h3>
            <dl className="grid grid-cols-2 gap-2 text-sm">
              {[["Eligible", row.eligible], ["Reviewed", row.reviewed], ["Not reviewed", row.unreviewed], ["Currently enabled", row.enabled]].map(([title, value]) =>
                <div className="rounded-lg bg-[var(--surface)] p-3" key={title}><dt className="text-xs text-[var(--muted)]">{title}</dt><dd className="mt-1 font-display text-2xl font-bold">{value}</dd></div>)}
            </dl>
            <p className="text-xs text-[var(--muted)]">Earlier decisions among currently eligible members: {row.decisions.allow} allowed · {row.decisions.decline} declined · {row.decisions.unavailable} unavailable at review. A later change in Settings can change current permission without changing that earlier decision.</p>
          </div>;
        })}
      </div>
    </>}
  </section>;
}
