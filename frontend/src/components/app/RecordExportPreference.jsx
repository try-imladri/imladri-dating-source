import { useEffect, useRef, useState } from "react";

export function RecordExportPreference({ scope, me, setMe, api, pairId }) {
  const pair = scope === "couple";
  const pref = me?.research_exports?.[scope];
  const owner = me?.user_id;
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const pending = useRef(null);
  const binding = pairId === undefined ? pref?.pair_id : pairId;
  const changedPair = pair && pairId !== undefined && !!pref && (pref.pair_id || null) !== (pairId || null);

  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => {
    if (!changedPair) return;
    const controller = new AbortController();
    api.get("/me", {signal: controller.signal}).then(({data}) => {
      if (!controller.signal.aborted && data.user_id === owner && data.research_exports) {
        setNotice("");
        setMe(previous => previous?.user_id === owner ? {...previous, research_exports:data.research_exports} : previous);
      }
    }).catch(() => { if (!controller.signal.aborted) setNotice("Your current couple’s permission could not be loaded."); });
    return () => controller.abort();
  }, [changedPair, api, owner, setMe, binding, reload]);

  if (!pref || me.role !== "member") return null;
  const enabled = !!pref.enabled && !changedPair;
  async function update(selected) {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setNotice("");
    try {
      const body = {enabled:selected, version:pref.version, revision:pref.revision,
        adult_confirmed:selected, expected_member:owner, ...(pair ? {expected_pair_id:binding || null} : {})};
      const {data} = await api.patch("/me/research-exports/" + scope, body, {signal:controller.signal});
      if (controller.signal.aborted) return;
      setMe(previous => previous?.user_id === owner ? {...previous, research_exports:{
        ...previous.research_exports, [scope]:data,
      }} : previous);
      setNotice(selected
        ? (pair ? "Your choice is saved. Both partners must opt in for this couple to appear." : "Your saved answers can now appear in staff CSV downloads.")
        : "Your records are excluded from future downloads for this permission.");
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error.response?.status === 409) {
        try {
          const {data} = await api.get("/me", {signal:controller.signal});
          if (!controller.signal.aborted && data.user_id === owner && data.research_exports) {
            setMe(previous => previous?.user_id === owner ? {...previous, research_exports:data.research_exports} : previous);
          }
        } catch { /* Keep the last confirmed choice visible. */ }
      }
      if (!controller.signal.aborted) setNotice(typeof error.response?.data?.detail === "string" ? error.response.data.detail : "Your choice could not be confirmed. Refresh this page before trying again.");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      pending.current = null;
    }
  }
  return <section className="space-y-2 border-t border-[var(--line)] pt-3 text-sm">
    <h3 className="font-semibold text-[var(--ink)]">{pair ? "Couples research CSV" : "Individual research CSV"}</h3>
    <p>{pair
      ? "If both partners agree, Imladri staff can download coded rows for this couple. These include each partner’s profile categories, age and height ranges, shared starter choices, joint milestones, check-in outcomes and activity dates."
      : "Imladri staff can download rows for your account, from the first saved answer, under a research code. They include your structured Individual and Sex & Intimacy answers, skips and conditions, plus any profile categories you provide, including ethnicity, gender, and age and height ranges."}</p>
    <p>{pair
      ? "If both partners also turn on tone research, staff can see later fixed-choice feedback on shared-exchange reads: whether a read fit, a reply was sent and a sent reply helped. Private draft coaching stays private."
      : "If you also turn on tone research, staff can see your later fixed-choice feedback about whether a read fit, you sent the reply and a sent reply helped."}</p>
    <p>Staff can follow the same code across downloads. If both partners enable both CSV permissions, staff can link each partner’s Individual rows to their Couples rows. Written answers, messages and private drafts stay outside these files.</p>
    <p>Turning this off stops future downloads of these rows. Earlier copies may still exist.</p>
    {pair && !binding && !enabled ? <p className="text-[var(--muted)]">This choice becomes available in an active couple.</p> :
      <label className="flex items-start gap-2 font-semibold text-[var(--ink)]">
        <input type="checkbox" className="mt-1 shrink-0" checked={enabled} disabled={busy || changedPair}
          onChange={event => update(event.target.checked)} />
        {pair ? "I’m 18 or older and agree to staff downloads for this couple." : "I’m 18 or older and agree to staff downloads of my Individual answers."}
      </label>}
    {changedPair && <button type="button" className="underline" disabled={busy} onClick={()=>setReload(n=>n+1)}>Refresh current couple</button>}
    {pair && pref.previous_pair_grant && !changedPair && <button type="button" className="underline" disabled={busy} onClick={()=>update(false)}>Remove previous couple’s permission</button>}
    {notice && <p role="status">{notice}</p>}
  </section>;
}
