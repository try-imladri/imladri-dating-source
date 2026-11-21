import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import ToneResearchPreference from "@/components/app/ToneResearchPreference";
import ResearchTrends, { comparisonCsv } from "./ResearchTrends";

const variables = [{ id: "style", label: "Last style selected" }, { id: "sent_style", label: "Reply style reported sent" },
  { id: "helped", label: "Reported help" }, { id: "incoming", label: "Perceived incoming tone" }];
let host, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

test("a member can opt in and immediately withdraw the separate tone permission", async () => {
  const api = { patch: jest.fn(async (_, payload) => ({ data: { version: payload.version, enabled: payload.enabled, revision: payload.revision + 1 } })) };
  function SettingsFixture() {
    const [me, setMe] = useState({ user_id: "own", role: "member", tone_research: { enabled: false, revision: 0, version: "conversation-tone-v1" } });
    return <ToneResearchPreference me={me} api={api} setMe={setMe} />;
  }
  await act(async () => root.render(<SettingsFixture />));
  const consent = host.querySelector("input");
  expect(consent.checked).toBe(false);
  await act(async () => consent.click());
  expect(consent.checked).toBe(true);
  expect(api.patch).toHaveBeenLastCalledWith("/me/tone-research", { enabled: true, revision: 0,
    version: "conversation-tone-v1", expected_member: "own", adult_confirmed: true });
  await act(async () => consent.click());
  expect(consent.checked).toBe(false);
  expect(api.patch.mock.calls[1][1]).toMatchObject({ enabled: false, revision: 1 });
});

test("Admin Research loads protected tone comparisons on demand and exports their exact scope", async () => {
  let last;
  const api = { get: jest.fn(async (path, options) => {
    if (path !== "/admin/research/tone") return new Promise(() => {});
    last = { ...options.params, scope_label: options.params.unit === "couple" ? "Shared Couples conversations" : "Individual conversations",
      variables, tones: ["warm", "reserved"], minimum: 50, minimum_cell: 10, round_to: 5, x_choices: ["warm", "direct"],
      y_choices: ["Yes", "No"], available: !!options.params.incoming, sample_size: 60, cramers_v: 0,
      columns: [0, 1], rows: [{ index: 0, cells: [15,15], total: 30 }, { index: 1, cells: [15,15], total: 30 }] };
    return { data: last };
  }) };
  await act(async () => root.render(<ResearchTrends api={api} />));
  await act(async () => [...host.querySelectorAll("button")].find(node=>node.textContent === "Conversation tone").click());
  const summary = [...host.querySelectorAll("summary")].find(e => e.textContent === "Conversation tone research");
  const details = summary.parentElement;
  expect(api.get.mock.calls.map(([path]) => path)).not.toContain("/admin/research/tone");
  await act(async () => {
    details.open = true;
    details.dispatchEvent(new Event("toggle"));
  });
  expect(details.textContent).toContain("Comparison withheld");
  const change = async (label, value) => {
    const select = [...details.querySelectorAll("label")].find(l => l.textContent.startsWith(label)).querySelector("select");
    await act(async () => { select.value = value; select.dispatchEvent(new Event("change", { bubbles: true })); });
  };
  await change("Conversation source", "couple");
  await change("Incoming tone filter", "reserved");
  await act(async () => details.querySelector('button[type="submit"]').click());
  expect(last).toMatchObject({ unit: "couple", x: "sent_style", y: "helped", incoming: "reserved" });
  expect(details.querySelector("table").textContent).toContain("Shared Couples conversations · incoming tone reserved");
  expect(comparisonCsv(last)).toContain("Shared Couples conversations · incoming tone reserved");
});
