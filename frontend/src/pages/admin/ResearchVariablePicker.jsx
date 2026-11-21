import { useState } from "react";

export const groupNames = {
  demographics: "Demographics",
  everyday: "Everyday personality",
  intimacy: "Sex & Intimacy",
  personal: "Personal profile",
  couples: "Couples",
};

const field = "block w-full rounded-lg border border-[var(--line)] bg-[var(--surface)] p-2 text-[var(--ink)] font-normal";

function normalize(text) {
  return text.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
}

export default function VariablePicker({ label, value, onChange, variables, condition, onCondition, conditions }) {
  const [filter, setFilter] = useState("");
  const terms = normalize(filter).trim().split(/\s+/).filter(Boolean);
  const selected = variables.find(variable => variable.id === value);
  const visible = variables.filter(variable => {
    const text = normalize(`${variable.label} ${variable.id} ${groupNames[variable.domain] || variable.domain}`);
    return terms.every(term => text.includes(term));
  });
  const keepSelected = selected && !visible.includes(selected);
  return <div className="min-w-0 space-y-2">
    <details className="text-xs">
      <summary className="cursor-pointer underline">Filter {label.toLowerCase()} by name or ID</summary>
      <input type="search" aria-label={`Filter ${label.toLowerCase()} by name or ID`}
        className={field + " mt-1"} value={filter} onChange={event => setFilter(event.target.value)} />
      {terms.length > 0 && <p role="status" className="mt-1">
        {visible.length === 0 ? "No matching variables." : `${visible.length} matching ${visible.length === 1 ? "variable" : "variables"}.`}
        {keepSelected && " Your current selection is retained."}
      </p>}
    </details>
    <label className="block space-y-1 text-sm font-semibold">{label}
      <select aria-label={label} value={value} onChange={event => onChange(event.target.value)} className={field}>
        {keepSelected && <optgroup label="Current selection"><option value={selected.id}>{selected.label}</option></optgroup>}
        {Object.entries(groupNames).map(([domain, name]) => {
          const options = visible.filter(variable => variable.domain === domain);
          return options.length > 0 && <optgroup key={domain} label={name}>
            {options.map(variable => <option key={variable.id} value={variable.id}>{variable.label}</option>)}
          </optgroup>;
        })}
      </select>
    </label>
    {selected?.domain === "intimacy" && <label className="block space-y-1 text-xs text-[var(--muted)]">{label} measure
      <select aria-label={label + " measure"} value={condition || ""} onChange={event => onCondition(event.target.value || undefined)} className={field}>
        <option value="">Answer choice</option>
        {conditions.map(tag => <option key={tag} value={tag}>Condition · {tag}</option>)}
      </select>
    </label>}
  </div>;
}
