import { act } from "react";
import { createRoot } from "react-dom/client";
import ProviderComparison from "./ProviderComparison";

async function withReport(report, check) {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.body.appendChild(document.createElement("div"));
  const root = createRoot(host);
  try {
    await act(async()=>root.render(<ProviderComparison report={report} />));
    const enter = async (label, value) => {
      const input = [...host.querySelectorAll("label")].find(el => el.textContent.trim() === label).querySelector("input");
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,value);
        input.dispatchEvent(new Event("input",{bubbles:true}));
      });
    };
    await check(host, enter);
  } finally { act(()=>root.unmount()); host.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; }
}

test("reconciles a closed shared-key day across distinct databases while keeping spend unattributed", async () => {
  await withReport({environment:"live", start:"2026-11-08", activity:{}, daily_budget:{reservations:6,budget_tokens:9406}}, async (host, enter) => {
    await enter("Provider requests", "839");
    await enter("Provider total tokens", "1176245");
    await enter("Provider charge in USD", "0.36");
    expect(host.textContent).toContain("comparison needs verified reservation counts");
    await enter("Verified reservations from other databases", "56");
    expect(host.textContent).toContain("Key-wide requests minus those reservations · +777");
    expect(host.textContent).toContain("62 / 839 (7.4%)");
    expect(host.textContent).toContain("Production’s exact share is unknown");
    expect(host.textContent).toContain("Key-wide tokens minus locally measured tokens · +1,176,245");
    expect(host.textContent).not.toContain("Estimated cost for");
    await enter("Verified reservations from other databases", "900");
    expect(host.textContent).toContain("Recorded reservations exceed provider requests");
  });
});

test("missing budget records, zero counts, and unpriced attempts have distinct meanings", async () => {
  const activity = {read:{attempts:2,reported:1,priced:1,input_tokens:900,output_tokens:10,low_nusd:20000,high_nusd:40000}};
  await withReport({activity, daily_budget:null}, async (host, enter) => {
    expect(host.textContent).toContain("daily budget record is unavailable");
    expect(host.textContent).toContain("Some calls lack counts");
    await enter("Provider requests", "0");
    await enter("Verified reservations from other databases", "0");
    expect(host.textContent).not.toContain("Key-wide requests minus those reservations");
    await enter("Archived reservations for this database", "0");
    expect(host.textContent).toContain("Key-wide requests minus those reservations · 0");
    expect(host.textContent).not.toContain("Reservation-to-request ratio");
    await enter("Provider input tokens", "910");
    await enter("Provider output tokens", "0");
    expect(host.textContent).toContain("input +10 · output -10");
  });
});

test("statement compares the measured portion of requests reserved on the selected UTC day", async () => {
  await withReport({activity:{}, daily_budget:{reservations:3,budget_tokens:8000},
    reserved_day_usage:{usage_reports:2,input_tokens:15,output_tokens:5}}, async (host, enter) => {
    await enter("Provider total tokens", "21");
    expect(host.textContent).toContain("Of 3 requests reserved on this UTC day, 2 returned consistent token counts");
    expect(host.textContent).toContain("Key-wide tokens minus measured tokens for this day’s reservations · +1");
  });
});

test("includes total-only measurements and flags a contradictory statement", async () => {
  const measured = {attempts:3,reported:1,input_tokens:4,output_tokens:5,total_only_reports:2,total_only_tokens:17};
  await withReport({activity:{read:measured},daily_budget:{reservations:3,budget_tokens:26},
    reserved_day_usage:{usage_reports:1,input_tokens:4,output_tokens:5,total_only_reports:2,total_only_tokens:17}}, async (host, enter) => {
    await enter("Provider total tokens", "30");
    expect(host.textContent).toContain("minus locally measured tokens · +4");
    expect(host.textContent).toContain("minus measured tokens for this day’s reservations · +4");
    expect(host.textContent).not.toContain("Some calls lack counts");
    await enter("Provider input tokens", "20");
    await enter("Provider output tokens", "12");
    expect(host.textContent).toContain("must add up");
    expect(host.textContent).not.toContain("minus locally measured tokens");
    await enter("Provider total tokens", "32");
    expect(host.textContent).toContain("minus locally measured tokens · +6");
  });
});
