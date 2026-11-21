import { beginEntryRead, consumeEntryRead } from "./entryRead";
import { trackProfileWrite } from "./profileWriteBarrier";

const owner = "fictional-member";
const member = change => ({ user_id: owner, role: "member", personal_access: true,
  workspace_access: { version: "workspace-access-v1", mode: "individual" },
  main_consent: { reviewed: true, welcome_complete: true, pair_id: null, pair_sharing: { available: false } }, ...change });
const tick = () => Promise.resolve();
let entries;
const start = (...args) => { const entry = beginEntryRead(...args); if (entry) entries.push(entry); return entry; };
beforeEach(() => { entries = []; });
afterEach(() => { entries.forEach(entry => entry.discard()); jest.useRealTimers(); });

test("initial own data overlaps account loading but is consumed only once after fresh authorization", async () => {
  const response = { data: { owner_id: owner, about: {} } };
  const api = { get: jest.fn(async () => response) };
  const entry = start(api, owner, "/app/personal", () => true);
  await tick();
  expect(api.get).toHaveBeenCalledTimes(1);
  expect(api.get.mock.calls[0]).toEqual(["/me/assessments", expect.objectContaining({ headers: { "X-Questionnaire-Owner": owner } })]);
  expect(consumeEntryRead(api, owner, "/me/assessments")).toBeNull();
  entry.authorize(member());
  expect(await consumeEntryRead(api, owner, "/me/assessments")).toBe(response);
  expect(consumeEntryRead(api, owner, "/me/assessments")).toBeNull();
  expect(api.get).toHaveBeenCalledTimes(1);
});

test.each([
  { personal_access: false }, { closing: true }, { user_id: "someone-else" }, { role: "admin" },
  { workspace_access: { version: "workspace-access-v1", mode: "couples", pair_id: "pair" } },
  { main_consent: { reviewed: false, welcome_complete: false, pair_id: null } },
])("denied, closing, changed or unreviewed entry cannot expose prefetched data: %j", async change => {
  const api = { get: jest.fn(async () => ({ data: { private: true } })) };
  const entry = start(api, owner, "/app/your-type", () => true); await tick();
  entry.authorize(member(change));
  expect(entry.controller.signal.aborted).toBe(true);
  expect(consumeEntryRead(api, owner, "/your-type")).toBeNull();
  entry.authorize(member());
  expect(consumeEntryRead(api, owner, "/your-type")).toBeNull();
});

test("route change discards even a completed response before another route can consume it", async () => {
  let current = true;
  const api = { get: jest.fn(async () => ({ data: {} })) };
  const entry = start(api, owner, "/app/personal", () => current); await tick();
  entry.authorize(member()); current = false;
  expect(consumeEntryRead(api, owner, "/me/assessments")).toBeNull();
  expect(entry.controller.signal.aborted).toBe(true);
});

test("same-user replacement API session and another owner never consume the previous request", async () => {
  const api = { get: jest.fn(async () => ({ data: {} })) }, replacement = { get: jest.fn() };
  const entry = start(api, owner, "/app/personal", () => true); await tick(); entry.authorize(member());
  expect(consumeEntryRead(replacement, owner, "/me/assessments")).toBeNull();
  expect(consumeEntryRead(api, "other", "/me/assessments")).toBeNull();
  entry.discard(); expect(consumeEntryRead(api, owner, "/me/assessments")).toBeNull();
});

test("a profile write invalidates the older read even after its write barrier settles", async () => {
  const api = { get: jest.fn(async () => ({ data: {} })) };
  const entry = start(api, owner, "/app/personal", () => true); await tick(); entry.authorize(member());
  await trackProfileWrite(api, owner, Promise.resolve());
  expect(consumeEntryRead(api, owner, "/me/assessments")).toBeNull();
  expect(entry.controller.signal.aborted).toBe(true);
});

test("a server denial is consumed as the same failure without replaying the request", async () => {
  const failure = { response: { status: 403 } };
  let reject;
  const api = { get: jest.fn(() => new Promise((_, fail) => {reject=fail;})) };
  const entry = start(api, owner, "/app/your-type", () => true); await tick(); entry.authorize(member());
  const consumed=consumeEntryRead(api, owner, "/your-type"); reject(failure);
  await expect(consumed).rejects.toBe(failure);
  expect(api.get).toHaveBeenCalledTimes(1);
});

test.each([403,503])("a speculative %s before authorization is discarded so the later page reads fresh", async status => {
  const failure={response:{status}};
  const api={get:jest.fn(async () => {throw failure;})};
  const entry=start(api, owner, "/app/personal", () => true);
  await expect(entry.promise).rejects.toBe(failure);
  entry.authorize(member());
  expect(consumeEntryRead(api, owner, "/me/assessments")).toBeNull();
  expect(entry.controller.signal.aborted).toBe(true);
});

test("slow initial gates expire instead of keeping a completed private payload", async () => {
  jest.useFakeTimers(); const api = { get: jest.fn(async () => ({ data: {} })) };
  const entry = start(api, owner, "/app/intimacy", () => true); await tick();
  jest.advanceTimersByTime(20_000); entry.authorize(member());
  expect(entry.controller.signal.aborted).toBe(true);
  expect(consumeEntryRead(api, owner, "/me/assessments?include_intimacy=true")).toBeNull();
});

test("StrictMode cleanup abandons the first read and only the new effect can be consumed", async () => {
  const api = { get: jest.fn(async () => ({ data: {} })) };
  const first = start(api, owner, "/app/personal", () => true); first.discard();
  const second = start(api, owner, "/app/personal", () => true); await tick(); second.authorize(member());
  expect(first.controller.signal.aborted).toBe(true);
  expect(await consumeEntryRead(api, owner, "/me/assessments")).toEqual({ data: {} });
  expect(api.get).toHaveBeenCalledTimes(1);
});

test("unmount after consumption aborts the outstanding request", async () => {
  const api = { get: jest.fn(() => new Promise(() => {})) };
  const entry = start(api, owner, "/app/personal", () => true); await tick(); entry.authorize(member());
  const controller = new AbortController(); consumeEntryRead(api, owner, "/me/assessments", controller.signal);
  controller.abort(); expect(entry.controller.signal.aborted).toBe(true);
});

test.each(["/admin/research", "/app/couples/personal", "/app/couples/compatibility", "/app/settings", "/app/quick-advice"])("does not speculate staff, partner or unrelated routes: %s", path => {
  const api = { get: jest.fn() }; expect(start(api, owner, path, () => true)).toBeNull(); expect(api.get).not.toHaveBeenCalled();
});
