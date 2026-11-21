import { act } from "react";
import { createRoot } from "react-dom/client";
import { StatedPreferences, YourTypeResults } from "./YourType";
import Portrait from "./YourTypePortrait";

jest.mock("@/lib/api", () => ({ errMsg: error => error.message }));
let host, root;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; host = document.body.appendChild(document.createElement("div")); root = createRoot(host); });
afterEach(() => { act(() => root.unmount()); host.remove(); });
const click = node => act(async () => node.click());
const button = text => [...host.querySelectorAll("button")].find(node => node.textContent.includes(text));
const openPreferences = () => act(async () => { const node = host.querySelector("details"); node.open = true; node.dispatchEvent(new Event("toggle")); });
const empty = () => ({ age_min: null, age_max: null, height_min_cm: null, height_max_cm: null });
const receipt = (revision = 0) => ({ version: "your-type-stated-preferences-v1", revision,
  by_audience: { women: empty(), men: empty() }, research_included: false, updated_at: null });
async function input(label, value) {
  const node = [...host.querySelectorAll("label")].find(item => item.textContent === label).querySelector("input");
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(node, value);
    node.dispatchEvent(new Event("input", { bubbles: true })); });
}
const face = (id, score) => ({ id, url: "/fictional.jpg", score, uncertainty: 0.5,
  sprite: { rect: { x: 0, y: 0, w: 100, h: 200 }, sheet_width: 100, sheet_height: 200 } });
const profile = (audience, overrides = {}) => ({ audience, results_available: true, signal: "developing", likes: 30,
  explicit_passes: 3, viewed_passes: 0, rankings: [face(audience, 0.6)], feature_preferences: [],
  prospective_pairwise: { pairs: 10, accuracy: audience === "men" ? 0.2 : 0.6 }, ...overrides });

test("both audiences show independent galleries, checks and insufficient evidence without pooled rankings", async () => {
  const result = { likes: 32, profiles_by_audience: { women: profile("women"), men: profile("men", {
    results_available: false, likes: 2, explicit_passes: 0, rankings: [face("must-not-show", 0.99)] }) }, rankings: [face("pooled", 0.9)] };
  await act(async () => root.render(<YourTypeResults result={result} onContinue={() => {}} />));
  const women = host.querySelector('[aria-label="Women preference profile"]');
  const men = host.querySelector('[aria-label="Men preference profile"]');
  expect(women.textContent).toContain("60% of 10"); expect(men.textContent).toContain("20% of 10");
  expect(men.textContent).toContain("Not enough choices for men");
  expect(men.querySelector("svg")).toBeNull(); expect(women.querySelector("svg")).not.toBeNull();
  expect(host.textContent).not.toContain("0.99"); expect(host.textContent).not.toContain("0.90");
  expect(host.textContent).toContain("do not compare your attraction across audiences");
  await click([...women.querySelectorAll("button")].find(node => node.textContent === "Your likes"));
  expect(women.querySelector('[aria-pressed="true"]').textContent).toBe("Your likes");
  expect(men.querySelector('[aria-pressed="true"]').textContent).toBe("Predicted favorites");
});

test("inapplicable, missing catalog coverage and insufficient variation stay distinct", async () => {
  const groups = [
    { id: "hair", label: "Facial hair", applicable: false, importance: 1, values: [{ feature: 0, label: "False beard preference", association_strength: 1 }] },
    { id: "body", label: "Build", applicable: true, coverage_available: false, covered_portraits: 0, total_portraits: 20, values: [] },
    { id: "nose", label: "Nose", applicable: true, coverage_available: true, variation_available: false, covered_portraits: 20, total_portraits: 20,
      values: [{ feature: 1, label: "Wide", sufficient_option_evidence: false, option_examples: 1, association_strength: null }] }];
  await act(async () => root.render(<YourTypeResults result={{ profiles_by_audience: { women: profile("women", { feature_preferences: groups }) } }} />));
  expect(host.textContent).toContain("Not applicable"); expect(host.textContent).toContain("Not covered in this edition");
  expect(host.textContent).toContain("Not enough variation"); expect(host.textContent).toContain("0 of 20 portraits");
  expect(host.textContent).not.toContain("False beard preference"); expect(host.textContent).not.toContain("100% relative weight");
});

test("legacy combined results remain labeled and are not fabricated into audience profiles", async () => {
  await act(async () => root.render(<YourTypeResults result={{ ...profile("both"), profiles_by_audience: {}, legacy_combined_profile: true }} />));
  expect(host.textContent).toContain("Historical combined profile");
  expect(host.querySelector('[aria-label="Women preference profile"]')).toBeNull();
  expect(host.querySelector('[aria-label="Men preference profile"]')).toBeNull();
  expect(host.querySelector('[aria-label="Portrait preference profile"]')).not.toBeNull();
});

test("explicitly contained headshots and full bodies fit while legacy portraits retain their original crop", async () => {
  await act(async () => root.render(<><Portrait face={{ ...face("body", 0), image_kind: "full_body" }} />
    <Portrait face={{ ...face("explicit-head", 0), display_fit: "contain" }} label="Contained head" />
    <Portrait face={face("legacy", 0)} /></>));
  expect(host.querySelector('[aria-label="Original synthetic adult full-body image"]').getAttribute("preserveAspectRatio")).toBe("xMidYMid meet");
  expect(host.querySelector('[aria-label="Original synthetic adult portrait"]').getAttribute("preserveAspectRatio")).toBe("xMidYMid slice");
  expect(host.querySelector('[aria-label="Contained head"]').getAttribute("preserveAspectRatio")).toBe("xMidYMid meet");
  for (const svg of host.querySelectorAll("svg")) expect(svg.querySelector("image").getAttribute("clip-path")).toBe(`url(#${svg.querySelector("clipPath").id})`);
});

test("stated preferences load only on opening, validate complete ranges and save independent exact owner-bound values", async () => {
  const api = { get: jest.fn(async () => ({ data: receipt(2) })), put: jest.fn(async (_, body) => ({ data: { ...receipt(3), by_audience: body.by_audience } })) };
  await act(async () => root.render(<StatedPreferences api={api} owner="owner" />)); expect(api.get).not.toHaveBeenCalled();
  await openPreferences(); expect(api.get.mock.calls[0][0]).toBe("/your-type/preferences");
  await input("Women: Minimum age", "17"); await click(button("Save stated preferences"));
  expect(api.put).not.toHaveBeenCalled(); expect(host.querySelector('[role="alert"]').textContent).toContain("18 to 100");
  await input("Women: Minimum age", "18"); await click(button("Save stated preferences"));
  expect(api.put).not.toHaveBeenCalled(); expect(host.textContent).toContain("enter both ends");
  await input("Women: Maximum age", "100"); await input("Men: Minimum height (cm)", "120"); await input("Men: Maximum height (cm)", "230");
  await click(button("Save stated preferences"));
  expect(api.put).toHaveBeenCalledWith("/your-type/preferences", { version: receipt().version, revision: 2, expected_member: "owner",
    by_audience: { women: { ...empty(), age_min: 18, age_max: 100 }, men: { ...empty(), height_min_cm: 120, height_max_cm: 230 } } }, expect.any(Object));
  expect(host.querySelector('[role="status"]').textContent).toContain("were saved");
  expect(host.querySelector('input[type="checkbox"]')).toBeNull(); expect(host.textContent).toContain("excluded from the current optional research permissions");
});

test("an unconfirmed preference save blocks replay until reload and never changes research permissions", async () => {
  const api = { get: jest.fn(async () => ({ data: receipt() })), put: jest.fn().mockRejectedValue({ response: { status: 409 } }) };
  await act(async () => root.render(<StatedPreferences api={api} owner="owner" />)); await openPreferences();
  await click(button("Save stated preferences")); expect(button("Save stated preferences").disabled).toBe(true);
  expect(host.textContent).toContain("changed elsewhere");
  await click(button("Reload saved preferences")); expect(button("Save stated preferences").disabled).toBe(false);
  expect(api.get.mock.calls.every(([path]) => path === "/your-type/preferences")).toBe(true);
});

test("switching owners aborts pending preference reads and ignores their late private values", async () => {
  let finish; const old = { get: jest.fn(() => new Promise(resolve => { finish = resolve; })) };
  const fresh = { get: jest.fn(async () => ({ data: receipt() })) };
  await act(async () => root.render(<StatedPreferences api={old} owner="old" />)); await openPreferences();
  const signal = old.get.mock.calls[0][1].signal;
  await act(async () => root.render(<StatedPreferences api={fresh} owner="new" />)); expect(signal.aborted).toBe(true);
  await act(async () => finish({ data: { ...receipt(), by_audience: { men: { ...empty(), age_min: 50, age_max: 60 }, women: empty() } } }));
  expect(host.querySelector('input')).toBeNull(); await openPreferences();
  expect([...host.querySelectorAll('input')].every(node => node.value === "")).toBe(true);
});
