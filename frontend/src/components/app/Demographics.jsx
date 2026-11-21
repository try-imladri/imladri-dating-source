import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export const DEMO_OPTS = {
  sex: ["Female", "Male", "Non-binary", "Prefer not to say"],
  ethnicity: ["Asian", "Black", "Hispanic / Latino", "Middle Eastern / North African", "White", "Mixed", "Other", "Prefer not to say"],
  education: ["High school", "Some college", "Bachelor's", "Master's", "Doctorate", "Trade / vocational", "Prefer not to say"],
};
export const SEXUALITY_OPTIONS = [["straight", "Straight"], ["bi", "Bisexual"], ["gay", "Gay"], ["lesbian", "Lesbian"]];
const ownOptions = {
  gender: ["Woman", "Man", "Non-binary", "Another gender", "Prefer not to say"],
  sex: DEMO_OPTS.sex,
  ethnicity: DEMO_OPTS.ethnicity,
  sexuality: SEXUALITY_OPTIONS.map(([value]) => value),
  education: DEMO_OPTS.education,
};
export const DEMO_LABELS = { gender: "Gender", sex: "Sex", ethnicity: "Ethnicity", sexuality: "Sexuality", education: "Education" };

export function DemoFields({ value, onChange, prefix, own = false, onCommit }) {
  const options = own ? ownOptions : DEMO_OPTS;
  const set = (k, v, commit = false) => {
    onChange({ ...value, [k]: v });
    if (commit) onCommit?.({ [k]: v });
  };
  return (
    <div id={`${prefix}-demographics`} className="grid min-w-0 grid-cols-2 gap-2" data-testid={`${prefix}-demographics`}>
      <div className="min-w-0">
        <label htmlFor={`${prefix}-age`} className="block text-[11px] text-[var(--muted)] mb-1">Age</label>
        <Input id={`${prefix}-age`} type="number" min={13} max={120} className="h-8 text-xs" value={value.age ?? ""} onChange={(e) => set("age", e.target.value ? Number(e.target.value) : null)} onBlur={() => onCommit?.({ age: value.age ?? null })} placeholder="e.g. 27" data-testid={`${prefix}-age`} />
      </div>
      {own && <div className="min-w-0">
        <label htmlFor={`${prefix}-height`} className="block text-[11px] text-[var(--muted)] mb-1">Height (cm)</label>
        <Input id={`${prefix}-height`} type="number" min={50} max={300} step={1} className="h-8 text-xs"
          value={value.height_cm ?? ""} onChange={e => set("height_cm", e.target.value ? Number(e.target.value) : null)}
          onBlur={() => onCommit?.({ height_cm: value.height_cm ?? null })} placeholder="e.g. 175" data-testid={`${prefix}-height`} />
      </div>}
      {Object.keys(options).map((k) => (
        <div key={k} className={`min-w-0 ${own && ["ethnicity", "sexuality", "education"].includes(k) ? "col-span-2" : ""}`}>
          <label htmlFor={`${prefix}-${k}`} className="block text-[11px] text-[var(--muted)] mb-1">{DEMO_LABELS[k]}</label>
          <Select value={value[k] || ""} onValueChange={v => set(k, k === "sexuality" && v === "clear" ? null : v, true)}>
            <SelectTrigger id={`${prefix}-${k}`} className="h-8 min-w-0 max-w-full text-xs" data-testid={`${prefix}-${k}`}><SelectValue placeholder="Choose" /></SelectTrigger>
            <SelectContent>
              {k === "sexuality" && <SelectItem value="clear" data-testid={`${prefix}-sexuality-clear`}>Prefer not to say</SelectItem>}
              {options[k].map((o) => <SelectItem key={o} value={o} data-testid={`${prefix}-${k}-${o.replace(/[^a-z]/gi, "").toLowerCase()}`}>
                {k === "sexuality" ? SEXUALITY_OPTIONS.find(([value]) => value === o)[1] : o}
              </SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      ))}
    </div>
  );
}
