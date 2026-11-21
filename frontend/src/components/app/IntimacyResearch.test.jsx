import { act } from "react";
import { createRoot } from "react-dom/client";
import { IntimacyResearch } from "./IntimacyResearch";
import { matchesResearchCatalog } from "./intimacyTopics";

jest.mock("./intimacyTopics", () => ({ matchesResearchCatalog: jest.fn() }));

const empty = { submitted: false, consent_version: "research-v1", revision: 0 };
const saved = { submitted: true, consent_version: "research-v1", revision: 3, answered: 4, skipped: 172,
  everyday_answered: 9, demographic_fields: ["education"], age_band: "25-34" };
let root, host;

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  matchesResearchCatalog.mockReset().mockResolvedValue(true);
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function render(api, props = {}) {
  act(() => root.render(<IntimacyResearch api={api} {...props} />));
}

function button(label) {
  return Array.from(host.querySelectorAll("button")).find((item) => item.textContent === label);
}

function checkbox(label) {
  return Array.from(host.querySelectorAll("label")).find((item) => item.textContent.includes(label))?.querySelector("input");
}

async function click(node) {
  expect(node).toBeDefined();
  await act(async () => node.click());
}

test("a later Settings visit can inspect and withdraw a saved record without starting the questionnaire", async () => {
  const api = { get: jest.fn().mockResolvedValue({ data: saved }), delete: jest.fn().mockResolvedValue({ data: { ...empty, revision: 4 } }) };
  render(api);
  expect(api.get).not.toHaveBeenCalled();
  await click(button("Manage research contribution"));
  expect(host.textContent).toContain("172 skipped");
  expect(host.textContent).toContain("Additional details included · Education");
  expect(host.querySelector("input")).toBeNull();
  await click(button("Withdraw contribution"));
  await click(button("Keep contribution"));
  expect(api.delete).not.toHaveBeenCalled();
  await click(button("Withdraw contribution"));
  await click(button("Confirm withdrawal"));
  expect(api.delete).toHaveBeenCalledWith("/me/research", { params: { revision: 3 } });
  expect(host.textContent).toContain("no saved research contribution");
});

test("replacement discloses only selected demographics and requires fresh consent after a selection changes", async () => {
  const about = { demographics: { sex: "Male", education: "Bachelor's", ethnicity: "Prefer not to say" } };
  const api = { get: jest.fn(async (path) => ({ data: path === "/me/about" ? { about } : saved })),
    put: jest.fn().mockResolvedValue({ data: { ...saved, revision: 4 } }) };
  render(api, { answers: { first: [null] }, catalogVersion: "catalog" });
  await click(button("Review research contribution"));
  expect(checkbox("Sex · Male").checked).toBe(false);
  expect(checkbox("Education · Bachelor's").checked).toBe(false);
  expect(checkbox("Ethnicity")).toBeUndefined();
  expect(checkbox("I agree").checked).toBe(false);
  await click(checkbox("I agree"));
  await click(checkbox("Sex · Male"));
  expect(button("Replace my contribution").disabled).toBe(true);
  await click(checkbox("I agree"));
  await click(button("Replace my contribution"));
  expect(api.put.mock.calls[0][1]).toEqual({ consent: true, consent_version: "research-v1", adult_confirmed: true,
    revision: 3, catalog_version: "catalog", profile_updated_at: null, answers: { first: [null] }, demographic_fields: ["sex"] });
});

test("a concurrent withdrawal forces a fresh status check and consent before a second submission", async () => {
  const api = { get: jest.fn().mockResolvedValueOnce({ data: saved }).mockResolvedValueOnce({ data: { about: {} } })
      .mockResolvedValueOnce({ data: { ...empty, revision: 4 } }).mockResolvedValueOnce({ data: { about: {} } }),
    put: jest.fn().mockRejectedValueOnce({ response: { status: 409, data: { detail: "Your contribution changed." } } }) };
  render(api, { answers: { first: [null] }, catalogVersion: "catalog" });
  await click(button("Review research contribution"));
  await click(checkbox("I agree"));
  await click(button("Replace my contribution"));
  expect(host.querySelector('[role="alert"]').textContent).toContain("Your contribution changed");
  await click(button("Check saved status"));
  expect(button("Submit my contribution").disabled).toBe(true);
  expect(checkbox("I agree").checked).toBe(false);
  expect(api.put).toHaveBeenCalledTimes(1);
});

test("a failed status read and an unrecognized consent version permit no submission", async () => {
  const api = { get: jest.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ data: { about: {} } })
      .mockResolvedValueOnce({ data: { ...saved, consent_version: "research-v2" } }).mockResolvedValueOnce({ data: { about: {} } }) };
  render(api, { answers: { first: [null] }, catalogVersion: "catalog" });
  await click(button("Review research contribution"));
  expect(host.textContent).toContain("couldn’t be loaded");
  await click(button("Check saved status"));
  expect(checkbox("I agree")).toBeUndefined();
  expect(button("Replace my contribution")).toBeUndefined();
  expect(button("Withdraw contribution")).toBeDefined();
});

test("a cached questionnaire version can still show a saved record and cannot contribute mismatched answers", async () => {
  matchesResearchCatalog.mockResolvedValue(false);
  const api = { get: jest.fn(async (path) => ({ data: path === "/me/about" ? { about: {} } : saved })) };
  render(api, { answers: { first: [null] }, catalogVersion: "newer" });
  await click(button("Review research contribution"));
  expect(host.textContent).toContain("couldn’t be matched");
  expect(checkbox("I agree")).toBeUndefined();
  expect(button("Withdraw contribution")).toBeDefined();
});
