import { act } from "react";
import { createRoot } from "react-dom/client";
import YourTypeResearch, { YourTypeCatalog, YourTypeMember } from "./YourTypeResearch";
import QuizCatalog from "./QuizCatalog";

jest.mock("@/lib/api", () => ({ errMsg: e => e.message }));

let host, root;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; host = document.body.appendChild(document.createElement("div")); root = createRoot(host); });
afterEach(() => { act(() => root.unmount()); host.remove(); });
const click = node => act(async () => node.click());
const button = label => [...host.querySelectorAll("button")].find(node => node.textContent.includes(label));
const open = node => act(async () => { node.open = true; node.dispatchEvent(new Event("toggle")); });
const comparison = data => <p data-testid="comparison">{data.data.available ? "Available" : "Withheld"}</p>;
const report = () => ({ denominators: {}, edition_choices: [], edition_compatible: true, variables: [
  { id: "audience", label: "Chosen image audience" }, { id: "progress", label: "Crowds completed" }],
  minimum_paired: 50, minimum_cell: 10, retakes: "Latest eligible attempt per adult.", available: false });
const catalog = { assets: 640, cards_per_crowd: 12, recommended_crowds: 20, asset_edition: "original-v1",
  model_edition: "adaptive-v1", asset_manifest_sha256: "a".repeat(64), actions: ["like", "pass", "undo"],
  recorded: ["Pre-choice predictions", "Demographic snapshot reference"], features: ["Authored lighting"], rights: { original: true } };

test("Quizzes includes Your Type and loads its edition catalog only when selected", async () => {
  const api = { get: jest.fn().mockResolvedValue({ data: catalog }) };
  await act(async () => root.render(<QuizCatalog api={api}>Everyday</QuizCatalog>));
  expect(api.get).not.toHaveBeenCalled();
  await click(host.querySelector('[data-testid="admin-quiz-catalog-your_type"]'));
  expect(api.get.mock.calls[0][0]).toBe("/admin/your-type/catalog");
  expect(host.textContent).toContain("640");
  expect(host.textContent).toContain("Zero AI calls");
  expect(host.textContent).toContain("original-v1");
  expect(host.textContent).toContain("Undo adds a linked revision");
});

test("Research is lazy and shows true zero denominators without a population baseline", async () => {
  const api = { get: jest.fn().mockResolvedValue({ data: report() }) };
  await act(async () => root.render(<YourTypeResearch api={api} Comparison={comparison} />));
  expect(api.get).not.toHaveBeenCalled();
  await open(host.querySelector("details"));
  expect(api.get.mock.calls[0][0]).toBe("/admin/research/your-type");
  expect(host.textContent).toContain("off by default");
  expect(host.textContent).toContain("Population baseline unavailable");
  expect(host.querySelector('[data-testid="comparison"]').textContent).toBe("Withheld");
});

test("Research error can retry and never keeps a stale result", async () => {
  const api = { get: jest.fn().mockRejectedValueOnce(Error()).mockResolvedValueOnce({ data: report() }) };
  await act(async () => root.render(<YourTypeResearch api={api} Comparison={comparison} />));
  await open(host.querySelector("details"));
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  await click(button("Retry"));
  expect(host.querySelector('[role="alert"]')).toBeNull();
  expect(host.querySelector('[data-testid="comparison"]')).not.toBeNull();
});

test("Member details load only their requested owner and do not display private choices", async () => {
  const api = { get: jest.fn().mockResolvedValue({ data: { attempts: 2, research: { enabled: false },
    latest: { asset_edition: "a", model_edition: "m", state: { completed_crowds: 4 } }, private_choices_included: false } }) };
  await act(async () => root.render(<YourTypeMember api={api} userId="member-1" />));
  expect(api.get).not.toHaveBeenCalled();
  await open(host.querySelector("details"));
  expect(api.get.mock.calls[0][0]).toBe("/users/member-1/your-type");
  expect(host.textContent).toContain("2 attempts");
  expect(host.textContent).toContain("Research off");
  expect(host.textContent).toContain("Private choices stay out");
});

test("leaving a member row cancels its pending private CSV download", async () => {
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  const api = { get: jest.fn(path => path.endsWith(".csv") ? pending : Promise.resolve({ data: {
    attempts: 1, research: { enabled: true }, latest: null } })) };
  const oldCreate = URL.createObjectURL; URL.createObjectURL = jest.fn();
  try {
    await act(async () => root.render(<YourTypeMember api={api} userId="owner" />));
    await open(host.querySelector("details")); await click(button("Download consented"));
    const options = api.get.mock.calls.find(([path]) => path.endsWith(".csv"))[1];
    await act(async () => root.render(null));
    expect(options.signal.aborted).toBe(true);
    await act(async () => finish({ data: "fixture csv" }));
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  } finally { URL.createObjectURL = oldCreate; }
});

test("Incompatible editions require a recorded edition and scan truncation is explicit", async () => {
  const api = { get: jest.fn().mockResolvedValue({ data: { ...report(), edition_compatible: false, truncated: true,
    edition_choices: [{ id: "first", asset_edition: "a", model_edition: "m" }, { id: "second", asset_edition: "b", model_edition: "m" }] } }) };
  await act(async () => root.render(<YourTypeResearch api={api} Comparison={comparison} />));
  await open(host.querySelector("details"));
  expect(host.textContent).toContain("Incompatible editions are not pooled");
  expect(host.querySelector('[role="alert"]').textContent).toContain("safety limit");
});

test("Member preference snapshot is labeled as self-reported at attempt start and remains permission dependent", async () => {
  const data = { attempts: 1, research: { enabled: true }, latest: { asset_edition: "a", model_edition: "m",
    state: { completed_crowds: 4 }, attempt_code: "coded-attempt", preference_snapshot: {
      version: "your-type-preferences-v1", sexuality: "bi", viewer_gender: "Man", rated_audience: "both" } } };
  const api = { get: jest.fn().mockResolvedValue({ data }) };
  await act(async () => root.render(<YourTypeMember api={api} userId="owner" />));
  await open(host.querySelector("details"));
  expect(host.textContent).toContain("Self-reported at this attempt’s start: bi · Man · both portraits");
  expect(host.textContent).toContain("coded-attempt");
  expect(host.textContent).toContain("both current research and Individual CSV permissions");
  expect(host.textContent).toContain("Older attempts remain unknown");
});

test("Research explains preference snapshot provenance without replacing missing observations", async () => {
  const api = { get: jest.fn().mockResolvedValue({ data: { ...report(),
    preference_metadata: "Explicit attempt snapshots only. Current profile edits never relabel older attempts.",
    variables: [...report().variables, { id: "sexuality", label: "Self-reported sexuality at attempt start" }] } }) };
  await act(async () => root.render(<YourTypeResearch api={api} Comparison={comparison} />));
  await open(host.querySelector("details"));
  expect(host.textContent).toContain("Current profile edits never relabel older attempts");
  expect([...host.querySelectorAll("option")].some(option => option.value === "sexuality")).toBe(true);
  expect(host.querySelector('[data-testid="comparison"]').textContent).toBe("Withheld");
});

test("catalog separates audience applicability, missing annotation coverage and available variation", async () => {
  const api = { get: jest.fn().mockResolvedValue({ data: { ...catalog, catalog_schema_version: 2,
    research_exclusions: ["Private stated age and height are excluded."], feature_groups: [{ id: "facial_hair", label: "Facial hair",
      audience_coverage: { women: { applicable: false }, men: { applicable: true, covered_portraits: 32, total_portraits: 40, coverage_available: true, variation_available: true } } },
    { id: "body", label: "Build", audience_coverage: { women: { applicable: true, covered_portraits: 0, total_portraits: 40, coverage_available: false, variation_available: false } } }] } }) };
  await act(async () => root.render(<YourTypeCatalog api={api} />));
  const facial = host.querySelector('[aria-label="Facial hair catalog coverage"]');
  expect(facial.textContent).toContain("Not applicable to this audience"); expect(facial.textContent).toContain("32 of 40");
  expect(host.querySelector('[aria-label="Build catalog coverage"]').textContent).toContain("missing labels are not negatives");
  expect(host.textContent).toContain("Private stated age and height are excluded");
  expect(host.textContent).toContain("models and checks remain separate");
});

test("research preserves audience-qualified variable labels and excludes stated preferences", async () => {
  const api = { get: jest.fn().mockResolvedValue({ data: { ...report(), variables: [
    { id: "feature:men:0", label: "Men portrait choices · Nose · Broad" },
    { id: "feature:women:0", label: "Women portrait choices · Nose · Broad" }] } }) };
  await act(async () => root.render(<YourTypeResearch api={api} Comparison={comparison} />)); await open(host.querySelector("details"));
  expect([...host.querySelectorAll("option")].map(node => node.value)).toContain("feature:men:0");
  expect([...host.querySelectorAll("option")].map(node => node.value)).toContain("feature:women:0");
  expect(host.textContent).toContain("not a measure of relative attraction across audiences");
  expect(host.textContent).toContain("Private stated age and height ranges are excluded");
});

test("switching editions resets a feature query before loading a different schema", async () => {
  const api = { get: jest.fn().mockResolvedValue({ data: { ...report(), variables: [...report().variables,
    { id: "feature:0", label: "Legacy feature" }], edition_choices: [
    { id: "v1", asset_edition: "v1", model_edition: "v1" }, { id: "v2", asset_edition: "v2", model_edition: "v2" }] } }) };
  await act(async () => root.render(<YourTypeResearch api={api} Comparison={comparison} />)); await open(host.querySelector("details"));
  const select = async (node, value) => act(async () => { node.value = value; node.dispatchEvent(new Event("change", { bubbles: true })); });
  await select(host.querySelectorAll("select")[0], "feature:0");
  await select(host.querySelectorAll("select")[2], "v2");
  await click(button("Compare Your Type"));
  expect(api.get.mock.calls[api.get.mock.calls.length - 1][1].params).toEqual({ x: "audience", y: "progress", edition: "v2" });
});
