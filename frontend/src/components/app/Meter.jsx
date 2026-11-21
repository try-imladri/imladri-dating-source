export function Meter({ meter, size = 84, showMissing = false }) {
  const score = meter?.score ?? 0;
  const r = 34;
  const c = 2 * Math.PI * r;
  const level = score >= 70 ? "Rich profile" : score >= 35 ? "Taking shape" : "Gathering detail";
  return (
    <div className="flex items-center gap-4" data-testid="profile-detail-meter">
      <svg width={size} height={size} viewBox="0 0 80 80" className="shrink-0">
        <circle cx="40" cy="40" r={r} stroke="var(--line)" strokeWidth="7" fill="none" />
        <circle cx="40" cy="40" r={r} stroke="var(--emerald)" strokeWidth="7" fill="none" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c - (c * score) / 100} transform="rotate(-90 40 40)" style={{ transition: "stroke-dashoffset .8s" }} />
        <text x="40" y="45" textAnchor="middle" fontFamily="JetBrains Mono" fontSize="16" fill="currentColor" data-testid="meter-score">{score}%</text>
      </svg>
      <div className="min-w-0">
        <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--muted)]">Profile detail · completeness</p>
        <p className="font-semibold text-[var(--emerald)]">{level}</p>
        {showMissing && meter?.missing?.length > 0 && (
          <p className="text-xs text-[var(--muted)] mt-1" data-testid="meter-missing">Adds the most: {meter.missing.join(" · ")}</p>
        )}
      </div>
    </div>
  );
}

export function Ring({ score = 0, size = 72, stroke = 7, testid }) {
  const r = 34;
  const c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} viewBox="0 0 80 80" className="shrink-0" data-testid={testid}>
      <circle cx="40" cy="40" r={r} stroke="var(--line)" strokeWidth={stroke} fill="none" />
      <circle cx="40" cy="40" r={r} stroke="var(--ink)" strokeWidth={stroke} fill="none" strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c - (c * Math.min(100, score)) / 100} transform="rotate(-90 40 40)" style={{ transition: "stroke-dashoffset .8s" }} />
      <text x="40" y="46" textAnchor="middle" fontFamily="JetBrains Mono" fontSize="17" fill="currentColor">{score}%</text>
    </svg>
  );
}

const CONF_W = { low: 33, medium: 66, high: 100 };
export function ConfBar({ level }) {
  if (!CONF_W[level]) return null;
  return (
    <div className="flex items-center gap-2" data-testid="confidence-bar">
      <div className="h-1.5 flex-1 rounded-full bg-[var(--line)] overflow-hidden"><div className="h-full rounded-full bg-[var(--ink)] transition-all duration-700" style={{ width: `${CONF_W[level]}%` }} /></div>
      <span className="font-mono text-[10px] uppercase text-[var(--muted)] w-14 text-right">{level}</span>
    </div>
  );
}

export function Conf({ level }) {
  if (!level || level === "null") return null;
  const c = { high: "var(--emerald)", medium: "var(--amber)", low: "var(--crimson)" }[level] || "var(--muted)";
  return <span className="font-mono text-[10px] uppercase px-2 py-0.5 rounded-full border" style={{ color: c, borderColor: c }} data-testid="confidence-chip">{level} confidence</span>;
}
