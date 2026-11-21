export default function Stat({ label, value, sub, testid, square = false }) {
  return (
    <div className={`card min-w-0 ${square ? "aspect-square flex flex-col justify-between p-3.5 sm:p-4" : "px-4 py-3"}`} data-testid={testid}>
      <p className={`font-mono text-[10px] uppercase tracking-widest text-[var(--muted)] ${square ? "" : "truncate"}`}>{label}</p>
      <p className="font-display font-black text-3xl leading-none mt-1.5 break-words tabular-nums">{value}</p>
      {sub && <p className={`text-[11px] text-[var(--muted)] mt-1 ${square ? "" : "truncate"}`} title={sub}>{sub}</p>}
    </div>
  );
}
