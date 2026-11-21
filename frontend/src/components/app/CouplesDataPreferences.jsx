import { useCallback, useEffect, useRef, useState } from "react";

const valid = value => typeof value?.analytics_opt_in === "boolean" && typeof value.review_return_opt_in === "boolean"
  && typeof value.adult === "boolean";

export default function CouplesDataPreferences({ api, me, setMe }) {
  return <CouplesDataPreferencesWorkspace key={me.user_id} api={api} me={me} setMe={setMe} />;
}

function CouplesDataPreferencesWorkspace({ api, me, setMe }) {
  const owner = me.user_id;
  const [record, setRecord] = useState(null);
  const [phase, setPhase] = useState("loading");
  const [message, setMessage] = useState("");
  const pending = useRef(null);
  const alive = useRef(false);
  const load = useCallback(async () => {
    if (pending.current) return;
    const controller = new AbortController(); pending.current = controller; setPhase("loading"); setMessage("");
    try {
      const { data } = await api.get("/couples", { signal: controller.signal });
      if (!valid(data)) throw Error("Invalid preference state");
      if (alive.current && !controller.signal.aborted) { setRecord(data); setPhase("ready"); }
    } catch {
      if (alive.current && !controller.signal.aborted) { setPhase("uncertain"); setMessage("Your Couples data choices could not be confirmed. Check the saved choices before making a change."); }
    } finally { if (pending.current === controller) pending.current = null; }
  }, [api]);
  useEffect(() => {
    alive.current = true; setRecord(null); load();
    return () => { alive.current = false; pending.current?.abort(); pending.current = null; };
  }, [load, owner]);
  async function save(field, enabled) {
    if (pending.current || phase !== "ready" || !record || enabled && (!record.adult || record.underage)) return;
    const controller = new AbortController(); pending.current = controller; setPhase("saving"); setMessage("");
    try {
      const { data } = await api.patch("/couples/preferences", { [field]: enabled }, { signal: controller.signal });
      if (!valid(data) || data[field] !== enabled) throw Error("Unconfirmed preference");
      if (alive.current && !controller.signal.aborted) {
        setRecord(data); setPhase("ready"); setMessage("Couples data choice saved.");
        setMe(previous => {
          if (previous?.user_id !== owner) return previous;
          const preferences = { ...previous.couples_preferences, analytics_opt_in: data.analytics_opt_in, review_return_opt_in: data.review_return_opt_in };
          if (!data.review_return_opt_in) delete preferences.review_return_since;
          else if (typeof data.review_return_since === "string") preferences.review_return_since = data.review_return_since;
          return { ...previous, couples_preferences: preferences };
        });
      }
    } catch {
      if (alive.current && !controller.signal.aborted) { setPhase("uncertain"); setMessage("Your change could not be confirmed. Check saved choices; nothing will be resent automatically."); }
    } finally { if (pending.current === controller) pending.current = null; }
  }
  return <section className="space-y-3 border-t border-[var(--line)] pt-3 text-sm" aria-label="Couples data statistics">
    <h3 className="font-semibold text-[var(--ink)]">Couples statistics &amp; return tracking</h3>
    <p>These choices are independent of partner assessment sharing and reminders. Both partners must opt in for joint reports. Private messages and drafts stay outside group statistics.</p>
    {record && [
      ["analytics_opt_in", "Include my coded Couples activity and outcomes in group statistics"],
      ["review_return_opt_in", "Include future eligible Couples visits in return statistics"],
    ].map(([field, label]) => <label key={field} className="flex items-start gap-2 font-semibold text-[var(--ink)]">
      <input type="checkbox" checked={record[field]} disabled={phase !== "ready" || !record[field] && (!record.adult || record.underage)}
        className="mt-1 shrink-0" data-testid={"settings-" + field} onChange={event => save(field, event.target.checked)} />
      <span>{label}</span>
    </label>)}
    <p>Return tracking begins when you enable it. Earlier agreements are not newly enrolled. Turn either choice off to stop its use in future reports.</p>
    {phase === "loading" && <p role="status">Loading Couples data choices…</p>}
    {phase === "uncertain" ? <div className="space-y-2"><p role="alert">{message}</p><button type="button" className="underline" onClick={load}>Check saved Couples choices</button></div>
      : message && <p role="status">{message}</p>}
  </section>;
}
