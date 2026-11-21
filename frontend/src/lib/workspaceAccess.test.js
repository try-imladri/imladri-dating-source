import { assessmentLocation, personalSectionRedirect, couplesOnly, pairedProductRedirect } from "./workspaceAccess";

const paired = { workspace_access: { version: "workspace-access-v1", mode: "couples", couples_only: true,
  pair_id: "accepted-pair", individual: false, your_type: false, assessments: true } };

test("only an authoritative actual pair snapshot locks Individual", () => {
  expect(couplesOnly(paired)).toBe(true);
  for (const access of [null, {}, { ...paired.workspace_access, pair_id: null },
    { ...paired.workspace_access, couples_only: false }, { ...paired.workspace_access, mode: "individual" },
    { ...paired.workspace_access, version: "future-unknown" }]) expect(couplesOnly({ workspace_access: access })).toBe(false);
});

test.each(["basics", "deeper", "further", "patterns", "results"])("paired Personal deep link keeps its %s section", tab => {
  expect(pairedProductRedirect(paired, "/app/personal", `?tab=${tab}&draft=untrusted`)).toBe(`/app/couples/personal?tab=${tab}`);
});

test("Your Type is excluded including its legacy Personal query", () => {
  expect(pairedProductRedirect(paired, "/app/your-type")).toBe("/app/couples");
  expect(pairedProductRedirect(paired, "/app/personal", "?tab=your-type")).toBe("/app/couples");
  expect(assessmentLocation("?tab=unknown")).toBe("/app/couples/personal");
});

test.each(["/app", "/app/start", "/app/people", "/app/person/private-id", "/app/practice"])("paired Individual route %s returns to Couples", path => {
  expect(pairedProductRedirect(paired, path)).toBe("/app/couples");
});

test.each(["/app/settings", "/app/settings/upgrade", "/app/upgrade", "/app/couples", "/app/couples/personal", "/app/couples/compatibility", "/admin"])("account and authorized route %s stays accessible", path => {
  expect(pairedProductRedirect(paired, path)).toBeNull();
});

test("pending or confirming members keep their Individual destination", () => {
  const pending = { ...paired, workspace_access: { ...paired.workspace_access, mode: "individual", couples_only: false } };
  expect(pairedProductRedirect(pending, "/app/your-type")).toBeNull();
  expect(pairedProductRedirect(pending, "/app/personal", "?tab=intimacy")).toBeNull();
});


test("old personality entries select Personal results in the correct workspace", () => {
  expect(personalSectionRedirect("?tab=personality&draft=discarded")).toBe("/app/personal?tab=results");
  expect(personalSectionRedirect("?tab=personality", true)).toBe("/app/couples/personal?tab=results");
  expect(pairedProductRedirect(paired, "/app/personal", "?tab=personality")).toBe("/app/couples/personal?tab=results");
  expect(pairedProductRedirect(paired, "/app/personality")).toBe("/app/couples/personal?tab=results");
  expect(pairedProductRedirect(paired, "/app/couples/personality")).toBeNull();
});

test.each(["intimacy"])("old and canonical %s links retain the correct workspace", tab => {
  expect(personalSectionRedirect(`?tab=${tab}&draft=discarded`)).toBe(`/app/${tab}`);
  expect(personalSectionRedirect(`?tab=${tab}`, true)).toBe(`/app/couples/${tab}`);
  expect(pairedProductRedirect(paired, "/app/personal", `?tab=${tab}`)).toBe(`/app/couples/${tab}`);
  expect(pairedProductRedirect(paired, `/app/${tab}`)).toBe(`/app/couples/${tab}`);
  expect(pairedProductRedirect(paired, `/app/couples/${tab}`)).toBeNull();
});

test("Quick Advice stays in the authoritative workspace", () => {
  expect(pairedProductRedirect(paired, "/app/quick-advice")).toBe("/app/couples/quick-advice");
  expect(pairedProductRedirect(paired, "/app/couples/quick-advice")).toBeNull();
  expect(pairedProductRedirect({}, "/app/quick-advice")).toBeNull();
});
