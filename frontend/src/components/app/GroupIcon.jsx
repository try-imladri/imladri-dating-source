import { Heart, Home, Users, Briefcase, Sparkles } from "lucide-react";

const ICONS = { romantic: Heart, family: Home, friends: Users, work: Briefcase, other: Sparkles };

export function GroupIcon({ group, className = "w-4 h-4" }) {
  const I = ICONS[group] || Sparkles;
  return <I className={className} />;
}

export function Initials({ name, className = "" }) {
  return (
    <span className={`inline-flex items-center justify-center w-8 h-8 rounded-full border-2 border-[var(--bg)] bg-[var(--ink)] text-[var(--bg)] font-display font-black text-xs uppercase ${className}`} title={name}>
      {(name || "?").slice(0, 2)}
    </span>
  );
}

export function BasedOn({ items, testid = "based-on" }) {
  if (!items?.length) return null;
  return (
    <p className="text-[11px] font-mono text-[var(--muted)] leading-relaxed" data-testid={testid}>
      <span className="uppercase tracking-widest text-[var(--ink)]">Based on</span> · {items.join(" · ")}
    </p>
  );
}
