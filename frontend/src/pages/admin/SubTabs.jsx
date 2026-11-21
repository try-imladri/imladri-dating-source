export function SubTabs({ value, onChange, onIntent, items, testid }) {
  return (
    <div className="flex flex-wrap gap-1.5 shrink-0 max-w-full min-w-0" data-testid={testid}>
      {items.map(([k, label]) => (
        <button key={k} type="button" aria-pressed={value === k} onClick={() => onChange(k)} data-testid={`${testid}-${k}`}
          onPointerEnter={onIntent ? () => onIntent(k) : undefined} onFocus={onIntent ? () => onIntent(k) : undefined}
          onTouchStart={onIntent ? () => onIntent(k) : undefined}
          className={`px-4 py-1.5 rounded-full text-sm border !transition-none ${value === k ? "bg-[var(--ink)] text-[var(--bg)] border-[var(--ink)]" : "border-[var(--line)] hover:border-[var(--ink)]"}`}>{label}</button>
      ))}
    </div>
  );
}
