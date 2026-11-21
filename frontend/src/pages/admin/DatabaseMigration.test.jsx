import { act } from "react";
import { createRoot } from "react-dom/client";
import DatabaseMigration from "./DatabaseMigration";

const endpoint = "/admin/database-migration";
function report({ configured = false, snapshot = false, queued = false, total = 8 } = {}) {
  const counts = Object.fromEntries(["individual", "couples", "billing", "admin", "research"].map((key, index) => [key,
    snapshot ? { source_records: index + 2, target_records: index + 2, parity: true }
      : { source_records: null, target_records: null, parity: null }]));
  return { version: 1, mode: "mongo_authoritative",
    postgres: { configured, connected: false, connection_verification: "not_checked_in_request", cutover_eligible: false },
    latest_report: snapshot ? { status: "observed_parity_only", completed_at: "2026-11-10T12:00:00Z",
      observed_parity: true, cutover_eligible: false, remaining_gates: ["Test ongoing capture."] } : null,
    domains: counts, history: { domains: [
      { domain: "everyday", baseline: 3, new_observations: total - 3, total },
      { domain: "couples", baseline: 2, new_observations: 4, total: 6 }],
      pending_events: queued ? 4 : 0, redactions_pending: queued ? 1 : 0, database_tombstones: 7,
      sources: [{ source: "users", queued_documents: queued ? 2 : 0, pending_events: queued ? 4 : 0, redactions_pending: queued ? 1 : 0 }] },
    remaining_gates: ["MongoDB remains authoritative.", "Verify customer flows before cutover."], latest_repair: null };
}
let host, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; });
const render = api => act(async () => root.render(<DatabaseMigration api={api} />));
const button = label => [...host.querySelectorAll("button")].find(node => node.textContent === label);
const click = node => act(async () => node.click());
const table = label => [...host.querySelectorAll("table")].find(node => node.caption?.textContent === label);

test("loads on request, distinguishes baselines from new observations, and keeps every app area unchecked without a report", async () => {
  const api = { get: jest.fn(async () => ({ data: report() })) };
  await render(api);
  expect(api.get).not.toHaveBeenCalled();
  expect(host.textContent).toContain("No database switch is available on this page");
  await click(button("Load database status"));
  expect(api.get).toHaveBeenCalledWith(endpoint, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(host.textContent).toContain("Not configured");
  expect(host.textContent).toContain("Live application databaseMongoDB");
  expect(host.textContent).toContain("Application data sourceMigration in progress");
  expect(host.textContent).toContain("No reconciliation report has been recorded");
  const coverage = table("Recorded MongoDB to PostgreSQL coverage");
  expect(coverage.tBodies[0].rows).toHaveLength(5);
  expect([...coverage.tBodies[0].rows].map(row => row.cells[0].textContent)).toEqual(["Individual", "Couples", "Billing & message credits", "Admin", "Research"]);
  expect([...coverage.tBodies[0].rows].every(row => row.cells[3].textContent === "Not checked")).toBe(true);
  expect(host.textContent).toContain("Baseline observations5");
  expect(host.textContent).toContain("New history observations9");
  expect(host.textContent).toContain("they are not reconstructed earlier attempts");
  expect(host.textContent).toContain("7 database-wide erasure fences");
  expect(button("Process up to 50 pending records")).toBeUndefined();
  expect([...host.querySelectorAll("button")].some(node => /switch|cutover/i.test(node.textContent))).toBe(false);
});

test("a matched snapshot never claims a verified live PostgreSQL connection or completed workflow tests", async () => {
  const api = { get: jest.fn(async () => ({ data: report({ configured: true, snapshot: true }) })) };
  await render(api);
  await click(button("Load database status"));
  expect(host.textContent).toContain("Configured · connection not verified");
  expect(host.textContent).toContain("Observed parity snapshot");
  expect(host.textContent).toContain("does not test a live PostgreSQL connection");
  expect(host.textContent).toContain("matching records do not establish ongoing synchronization");
  expect(table("Recorded MongoDB to PostgreSQL coverage").tBodies[0].rows[0].textContent).toContain("Individual22Matched in snapshot");
  expect(host.querySelector("time").dateTime).toBe("2026-11-10T12:00:00Z");
  expect(host.textContent).toContain("Test ongoing capture.");
  expect(host.textContent).toContain("Live application databaseMongoDB");
  expect(host.textContent).not.toContain("Verified by this request");
});

test("PostgreSQL active mode identifies the current source and keeps saved snapshot evidence historical", async () => {
  const value = report({ configured: true, snapshot: true });
  value.mode = "postgres_authoritative";
  value.postgres = { configured: true, connected: true, connection_verification: "current_repository_queries", cutover_eligible: false };
  value.remaining_gates = [];
  const api = { get: jest.fn(async () => ({ data: value })) };
  await render(api);
  expect(api.get).not.toHaveBeenCalled();
  await click(button("Load database status"));
  expect(host.textContent).toContain("Live application databaseRender PostgreSQL");
  expect(host.textContent).toContain("Verified by this request");
  expect(host.textContent).toContain("Application data sourcePostgreSQL active");
  expect(host.textContent).toContain("These current history counts were read from PostgreSQL");
  expect(host.textContent).toContain("historical migration evidence");
  expect(host.textContent).toContain("Observed parity snapshot");
  expect(host.textContent).toContain("New history observations9");
  expect(host.textContent).not.toContain("MongoDB remains authoritative");
  expect(host.textContent).not.toContain("Test ongoing capture.");
  expect(host.textContent).not.toContain("does not test a live PostgreSQL connection");
  expect(host.textContent).not.toContain("Remaining migration checks");
  expect([...host.querySelectorAll("button")].some(node => /switch|cutover/i.test(node.textContent))).toBe(false);
  expect(api.get).toHaveBeenCalledTimes(1);
});

test("an active PostgreSQL source does not present an absent historical copy report as a failed connection", async () => {
  const value = report({ configured: true });
  value.mode = "postgres_authoritative";
  value.postgres.connected = true;
  value.postgres.connection_verification = "current_repository_queries";
  value.remaining_gates = [];
  const api = { get: jest.fn(async () => ({ data: value })) };
  await render(api);
  await click(button("Load database status"));
  expect(host.textContent).toContain("Render PostgreSQL");
  expect(host.textContent).toContain("Verified by this request");
  expect(host.textContent).toContain("No reconciliation report has been recorded");
  expect(host.querySelector("[role=alert]")).toBeNull();
  expect(table("History observations by area").tBodies[0].rows).toHaveLength(2);
  expect(table("Recorded MongoDB to PostgreSQL coverage").tBodies[0].rows[0].cells[3].textContent).toBe("Not checked");
});

test("labels every known history area and source distinctly from one status request", async () => {
  const value = report();
  const areas = ["everyday", "intimacy", "demographics", "research", "couples", "consent", "assessment_repeat", "wording_feedback", "profile", "person_intake"];
  const sourceNames = ["users", "intimacy_answers", "research_responses", "couples_drafts", "couples_pairs", "people"];
  value.history.domains = areas.map((domain, index) => ({ domain, baseline: index, new_observations: 1, total: index + 1 }));
  value.history.sources = sourceNames.map(source => ({ source, queued_documents: 0, pending_events: 0, redactions_pending: 0 }));
  const api = { get: jest.fn(async () => ({ data: value })) };
  await render(api);
  expect(api.get).not.toHaveBeenCalled();
  await click(button("Load database status"));
  const historyRows = [...table("History observations by area").tBodies[0].rows];
  expect(historyRows.map(row => row.cells[0].textContent)).toEqual([
    "Everyday answers", "Sex & Intimacy", "Demographic observations", "Research submissions", "Couples", "Consent & preferences",
    "Questionnaire retakes", "Intimacy wording feedback", "Profile descriptions", "Person intake observations"
  ]);
  expect(historyRows.map(row => row.cells[3].textContent)).toEqual(areas.map((_, index) => String(index + 1)));
  expect([...table("Answer history processing backlog").tBodies[0].rows].map(row => row.cells[0].textContent)).toEqual([
    "Profiles & Everyday", "Sex & Intimacy", "Research submissions", "Couples private answers", "Couples shared activity", "People & intake observations"
  ]);
  expect(host.textContent).not.toContain("Other history");
  expect(host.textContent).not.toContain("Other source");
  expect(api.get).toHaveBeenCalledTimes(1);
});

test("loading and failed refreshes clear stale counts, hide raw errors and can retry", async () => {
  let resolve;
  const api = { get: jest.fn().mockResolvedValueOnce({ data: report({ snapshot: true }) })
    .mockImplementationOnce(() => new Promise(done => { resolve = done; }))
    .mockRejectedValueOnce(new Error("postgres://private-credentials@database.invalid"))
    .mockResolvedValueOnce({ data: report() }) };
  await render(api);
  await click(button("Load database status"));
  await click(button("Refresh database status"));
  expect(host.textContent).toContain("Loading database status…");
  expect(host.querySelectorAll("table")).toHaveLength(0);
  expect(button("Load database status").disabled).toBe(true);
  await act(async () => resolve({ data: report({ snapshot: true }) }));
  await click(button("Refresh database status"));
  expect(host.querySelector("[role=alert]").textContent).toContain("Database status couldn’t load");
  expect(host.textContent).not.toContain("private-credentials");
  expect(host.querySelectorAll("table")).toHaveLength(0);
  await click(button("Retry database status"));
  expect(host.textContent).toContain("No reconciliation report has been recorded");
});

test("changing the admin API aborts stale reads so a previous environment cannot replace current counts", async () => {
  let resolve;
  const old = { get: jest.fn(() => new Promise(done => { resolve = done; })) };
  const current = { get: jest.fn(async () => ({ data: report({ total: 4 }) })) };
  await render(old);
  await click(button("Load database status"));
  const signal = old.get.mock.calls[0][1].signal;
  await render(current);
  expect(signal.aborted).toBe(true);
  expect(current.get).not.toHaveBeenCalled();
  await click(button("Load database status"));
  await act(async () => resolve({ data: report({ total: 999 }) }));
  expect(host.textContent).toContain("New history observations5");
  expect(host.textContent).not.toContain("999");
});

test("processing uses one bounded batch and refreshes the authoritative counts without any migration or cutover request", async () => {
  let release;
  const api = { get: jest.fn().mockResolvedValueOnce({ data: report({ queued: true }) }).mockResolvedValue({ data: report() }),
    post: jest.fn(() => new Promise(done => { release = done; })) };
  await render(api);
  await click(button("Load database status"));
  await click(button("Process up to 50 pending records"));
  expect(api.post).toHaveBeenCalledWith(endpoint + "/repair", { limit: 50 }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(button("Processing pending history…").disabled).toBe(true);
  expect(button("Refresh database status").disabled).toBe(true);
  await act(async () => release({ data: { attempted: 2, archived: 4, redacted: 1, failed: 0 } }));
  expect(api.get).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain("2 source records checked · 4 observations archived · 1 history records redacted · 0 failed");
  expect(host.textContent).toContain("Pending history events0");
  expect(button("Process up to 50 pending records")).toBeUndefined();
  expect(api.post).toHaveBeenCalledTimes(1);
});

test("a partial processing failure refreshes the remaining backlog and a retry can finish", async () => {
  const failure = { response: { status: 503, data: { detail: { message: "private diagnostic must not be displayed", counts: { attempted: 1, archived: 1, redacted: 0, failed: 1 } } } } };
  const api = { get: jest.fn().mockResolvedValueOnce({ data: report({ queued: true }) })
    .mockResolvedValueOnce({ data: report({ queued: true }) }).mockResolvedValue({ data: report() }),
    post: jest.fn().mockRejectedValueOnce(failure).mockResolvedValue({ data: { attempted: 1, archived: 3, redacted: 1, failed: 0 } }) };
  await render(api);
  await click(button("Load database status"));
  await click(button("Process up to 50 pending records"));
  expect(api.get).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain("Some queued history could not be processed");
  expect(host.textContent).toContain("1 failed");
  expect(host.textContent).not.toContain("private diagnostic");
  await click(button("Process up to 50 pending records"));
  expect(host.textContent).not.toContain("Some queued history could not be processed");
  expect(host.textContent).toContain("0 failed");
  expect(button("Process up to 50 pending records")).toBeUndefined();
});

test("an unsupported response becomes a retryable error instead of presenting unrelated data as migration status", async () => {
  const api = { get: jest.fn(async () => ({ data: { variables: [], available: true, sample_size: 50 } })) };
  await render(api);
  await click(button("Load database status"));
  expect(host.textContent).toContain("Database status couldn’t load");
  expect(host.querySelectorAll("table")).toHaveLength(0);
  expect(button("Retry database status")).toBeDefined();
});

test("an abandoned processing request cannot block or overwrite the next admin environment", async () => {
  let release;
  const old = { get: jest.fn(async () => ({ data: report({ queued: true }) })),
    post: jest.fn(() => new Promise(done => { release = done; })) };
  const current = { get: jest.fn().mockResolvedValueOnce({ data: report({ queued: true }) }).mockResolvedValue({ data: report() }),
    post: jest.fn(async () => ({ data: { attempted: 2, archived: 4, redacted: 1, failed: 0 } })) };
  await render(old);
  await click(button("Load database status"));
  await click(button("Process up to 50 pending records"));
  const signal = old.post.mock.calls[0][2].signal;
  await render(current);
  expect(signal.aborted).toBe(true);
  await click(button("Load database status"));
  await click(button("Process up to 50 pending records"));
  expect(current.post).toHaveBeenCalledTimes(1);
  await act(async () => release({ data: { attempted: 999, archived: 999, redacted: 0, failed: 0 } }));
  expect(host.textContent).not.toContain("999");
  expect(host.textContent).toContain("2 source records checked");
  expect(old.get).toHaveBeenCalledTimes(1);
});
