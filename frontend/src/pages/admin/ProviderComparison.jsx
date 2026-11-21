import { useState } from "react";

const dollars = value => new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", maximumFractionDigits: 8
}).format(value);
const number = value => value.toLocaleString("en-US");
const signed = value => value.toLocaleString("en-US", { signDisplay: "exceptZero" });
const field = "block w-full rounded-xl border border-[var(--line)] bg-[var(--bg)] p-2";

function whole(value) {
  return /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) ? Number(value) : null;
}

function CountField({ label, value, onChange }) {
  return <label className="block text-sm">{label}<input className={field} type="number" min="0" step="1"
    value={value} onChange={event => onChange(event.target.value)} /></label>;
}

export default function ProviderComparison({ report }) {
  const [inputs, setInputs] = useState("");
  const [outputs, setOutputs] = useState("");
  const [charge, setCharge] = useState("");
  const [requests, setRequests] = useState("");
  const [tokens, setTokens] = useState("");
  const [other, setOther] = useState("");
  const [archived, setArchived] = useState("");
  const totals = Object.values(report.activity).reduce((all, row) => {
    for (const key of Object.keys(all)) all[key] += row[key] || 0;
    return all;
  }, {input_tokens:0, output_tokens:0, attempts:0, reported:0, total_only_reports:0, total_only_tokens:0, priced:0, low_nusd:0, high_nusd:0});
  const budget = report.daily_budget;
  const reservedUsage = report.reserved_day_usage;
  const local = budget?.reservations ?? whole(archived);
  const provider = whole(requests);
  const otherReservations = whole(other);
  const known = local !== null && otherReservations !== null && Number.isSafeInteger(local + otherReservations)
    ? local + otherReservations : null;
  const providerTotal = whole(tokens);
  const providerIn = whole(inputs);
  const providerOut = whole(outputs);
  const inconsistent = providerTotal !== null && providerIn !== null && providerOut !== null && providerTotal !== providerIn + providerOut;
  const measuredTotal = totals.input_tokens + totals.output_tokens + totals.total_only_tokens;
  const reservedTotal = reservedUsage && reservedUsage.input_tokens + reservedUsage.output_tokens + (reservedUsage.total_only_tokens || 0);
  const validCharge = /^\d+(\.\d+)?$/.test(charge) && Number.isFinite(Number(charge));
  return <details className="rounded-xl border border-[var(--line)] p-3">
    <summary className="cursor-pointer font-semibold">Compare a DeepSeek usage statement</summary>
    <p className="mt-2">In <a className="underline" href="https://platform.deepseek.com/usage">DeepSeek Usage</a>, select {report.start} in UTC+0 and the server key. The statement covers every deployment and tool using that key. Your entries stay in this view.</p>
    <div className="my-3">
      <p><strong>{report.environment || "Current"} environment</strong> · {number(totals.attempts)} detailed provider attempts · {number(totals.reported)} with input and output counts · {number(totals.priced)} with estimated prices.</p>
      {budget ? <p>This database recorded {number(budget.reservations)} budget reservations. {budget.budget_tokens != null && <>The budget counter contains {number(budget.budget_tokens)} tokens, including any unresolved reservations.</>}</p>
        : <p>The daily budget record is unavailable. Daily budget records expire after 32 days.</p>}
      {local !== null && <p>Local reservations minus detailed attempts · {signed(local - totals.attempts)}. Reservations are dated before dispatch and detailed attempts when a generation finishes.</p>}
      {reservedUsage && budget && <p>Of {number(budget.reservations)} requests reserved on this UTC day, {number(reservedUsage.usage_reports)} returned consistent token counts
        {" "}· {number(reservedUsage.input_tokens)} input and {number(reservedUsage.output_tokens)} output tokens. Replies received after midnight count toward the day they were reserved.</p>}
      {!!reservedUsage?.total_only_reports && <p>Reservations with a token total only · {number(reservedUsage.total_only_reports)}. Combined tokens · {number(reservedUsage.total_only_tokens)}.</p>}
      {totals.total_only_reports > 0 && <p>Detailed attempts with a token total only · {number(totals.total_only_reports)}. Combined tokens · {number(totals.total_only_tokens)}.</p>}
      {totals.reported > 0 && <p>Measured usage · {number(totals.input_tokens)} input tokens and {number(totals.output_tokens)} output tokens.</p>}
      {totals.priced > 0 && <p>Estimated cost for {number(totals.priced)} priced attempts · {dollars(totals.low_nusd/1e9)}–{dollars(totals.high_nusd/1e9)}.</p>}
      {totals.attempts > totals.reported + totals.total_only_reports && <p>Some calls lack counts. Their token usage and cost are unknown.</p>}
    </div>
    <div className="grid gap-2 sm:grid-cols-3">
      <CountField label="Provider requests" value={requests} onChange={setRequests}/>
      <CountField label="Provider total tokens" value={tokens} onChange={setTokens}/>
      <label className="block text-sm">Provider charge in USD<input className={field} type="number" min="0" step="any" value={charge} onChange={event=>setCharge(event.target.value)} /></label>
      <CountField label="Provider input tokens" value={inputs} onChange={setInputs}/>
      <CountField label="Provider output tokens" value={outputs} onChange={setOutputs}/>
      {!budget && <CountField label="Archived reservations for this database" value={archived} onChange={setArchived}/>}
      <CountField label="Verified reservations from other databases" value={other} onChange={setOther}/>
    </div>
    <p className="mt-2 text-xs text-[var(--muted)]">Include each distinct database once for the same key and UTC day. Enter zero if you verified there were none. Leave unknown counts blank.</p>
    <div className="mt-2 space-y-1" role="status">
      {provider !== null && <p>Key-wide requests minus detailed attempts in this environment · {signed(provider - totals.attempts)}.</p>}
      {provider !== null && known !== null && <>
        <p>Verified budget reservations across the entered databases · {number(known)}. Key-wide requests minus those reservations · {signed(provider - known)}.</p>
        {provider > 0 && <p>Reservation-to-request ratio · {number(known)} / {number(provider)} ({(100 * known / provider).toLocaleString("en-US", {maximumFractionDigits:1})}%).</p>}
        {provider < known && <p>Recorded reservations exceed provider requests. Check the selected key, UTC date, and whether a database was counted twice.</p>}
      </>}
      {provider !== null && known === null && <p>The cross-database comparison needs verified reservation counts for this database and the others.</p>}
      {inconsistent && <p>Check the provider statement. Its input and output counts must add up to its total before comparing tokens.</p>}
      {!inconsistent && providerTotal !== null && <p>Key-wide tokens minus locally measured tokens · {signed(providerTotal - measuredTotal)}.</p>}
      {!inconsistent && providerTotal !== null && reservedUsage && <p>Key-wide tokens minus measured tokens for this day’s reservations · {signed(providerTotal - reservedTotal)}.</p>}
      {!inconsistent && providerIn !== null && providerOut !== null && <p>Key-wide minus locally measured tokens · input {signed(providerIn - totals.input_tokens)} · output {signed(providerOut - totals.output_tokens)}.</p>}
      {validCharge && <p>Key-wide provider charge · {dollars(Number(charge))}. Production’s exact share is unknown on a shared key.</p>}
    </div>
  </details>;
}
