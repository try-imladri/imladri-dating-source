import { optimisticChoice, YourTypeQueue } from "./yourTypeQueue";

const clone = value => JSON.parse(JSON.stringify(value));
const base = () => ({ id: "attempt", revision: 0, likes: 0, required_likes: 30, current_crowd_choices: 0,
  cards: Array.from({ length: 12 }, (_, i) => ({ face_id: "c" + i })), replacement_queue: Array.from({ length: 12 }, (_, i) => ({ face_id: "q" + i })) });
function action(kind, face) { return { kind, face_id: face, viewed_face_ids: [face], dwell_ms: {} }; }
function accept(session, body) {
  let next = clone(session);
  for (const item of body.single ? [body.single] : body.actions) next = optimisticChoice(next, item);
  next.revision += body.single ? 1 : body.actions.length;
  return next;
}
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
let queues;
beforeEach(() => { queues = []; jest.useFakeTimers(); });
afterEach(() => { for (const q of queues) q.close(); jest.useRealTimers(); });
function create(options = {}) {
  let id = 0;
  const q = new YourTypeQueue({ session: base(), changed: jest.fn(), id: () => "stable-" + ++id,
    send: jest.fn(), ...options });
  queues.push(q); return q;
}

test("a choice immediately replaces only its selected slot from the preissued reserve", () => {
  const session = base();
  const q = create({ session });
  q.enqueue(action("like", "c4"));
  expect(q.snapshot().session.cards[4].face_id).toBe("q0");
  expect(q.snapshot().session.cards[3].face_id).toBe("c3");
  expect(session.cards[4].face_id).toBe("c4");
  expect(q.snapshot().session.likes).toBe(1);
});

test("frozen request survives an ACK loss while later taps stay in a distinct batch", async () => {
  const gate = deferred(); const bodies = []; let server = base();
  const send = jest.fn(async (_, body) => {
    bodies.push(clone(body));
    if (bodies.length === 1) return gate.promise;
    server = accept(server, body); return server;
  });
  const q = create({ send });
  q.enqueue(action("like", "c0"));
  const first = q.flush();
  await Promise.resolve();
  q.enqueue(action("pass", "c1"));
  gate.reject(Error("network ACK lost"));
  await expect(first).rejects.toThrow("ACK lost");
  await q.flush();
  expect(bodies[1]).toEqual(bodies[0]);
  expect(bodies[2].expected_revision).toBe(1);
  expect(bodies[2].actions[0].face_id).toBe("c1");
  expect(q.snapshot().pending).toBe(0);
  expect(q.snapshot().session.revision).toBe(2);
});

test("restoring an already committed frozen batch does not apply it twice and preserves pending taps", async () => {
  const frozen = { batch_id: "batch", expected_revision: 0, actions: [{ ...action("like", "c0"), action_id: "a" }] };
  const committed = accept(base(), frozen);
  let server = committed;
  const send = jest.fn(async (_, body) => {
    if (body.batch_id === "batch") return { ...server, duplicate: true, accepted_revision: 1 };
    server = accept(server, body); return server;
  });
  const q = create({ session: committed, send, restored: { base_revision: 0, frozen,
    pending: [{ ...action("like", "c1"), action_id: "b" }] } });
  expect(q.snapshot().session.likes).toBe(2);
  expect(q.snapshot().session.cards[0].face_id).toBe("q0");
  expect(q.enqueue(action("like", "c2"))).toBe(false); // confirmation before more taps
  await q.flush();
  expect(send.mock.calls[0][1]).toEqual(frozen);
  expect(q.snapshot().session.likes).toBe(2);
  expect(q.snapshot().session.revision).toBe(2);
});

test("a newer GET hides pending optimism but still permits exact frozen reconciliation", async () => {
  const frozen = { batch_id: "batch", expected_revision: 0, actions: [{ ...action("like", "c0"), action_id: "a" }] };
  const newer = { ...accept(base(), frozen), revision: 3 };
  const send = jest.fn().mockResolvedValue({ ...newer, duplicate: true, accepted_revision: 1 });
  const q = create({ session: newer, send, restored: { base_revision: 0, frozen,
    pending: [{ ...action("like", "c1"), action_id: "b" }] } });
  expect(q.snapshot().session.likes).toBe(1);
  expect(q.snapshot().conflict).toBe(false);
  await expect(q.flush()).rejects.toThrow("advanced elsewhere");
  expect(send).toHaveBeenCalledTimes(1);
  expect(send.mock.calls[0][1]).toEqual(frozen);
  expect(q.snapshot().conflict).toBe(true);
  expect(q.snapshot().session.likes).toBe(1);
});

test("unfrozen unsent taps retain their base revision and never rebase onto a newer GET", async () => {
  const send = jest.fn();
  const q = create({ session: { ...base(), revision: 2 }, send, restored: { base_revision: 0, frozen: null,
    pending: [{ ...action("like", "c0"), action_id: "a" }] } });
  expect(q.snapshot().conflict).toBe(true);
  expect(q.snapshot().session.likes).toBe(0);
  await expect(q.flush()).rejects.toThrow("Reload");
  expect(send).not.toHaveBeenCalled();
  q.discardConflict({ ...base(), revision: 2 });
  expect(q.snapshot().pending).toBe(0);
});

test("mutable caller view arrays cannot change an enqueued request", async () => {
  const body = action("like", "c0"); const send = jest.fn((_, request) => accept(base(), request));
  const q = create({ send });
  q.enqueue(body); body.viewed_face_ids[0] = "someone-else";
  await q.flush();
  expect(send.mock.calls[0][1].actions[0].viewed_face_ids).toEqual(["c0"]);
});

test("a synchronous transport failure retains a retryable frozen body", async () => {
  let fail = true;
  const send = jest.fn((_, body) => { if (fail) throw Error("offline"); return accept(base(), body); });
  const q = create({ send }); q.enqueue(action("like", "c0"));
  await expect(q.flush()).rejects.toThrow("offline");
  expect(q.running).toBeNull();
  const saved = clone(q.frozen); fail = false; await q.flush();
  expect(send.mock.calls[1][1]).toEqual(saved);
});

test("concurrent controls cannot overwrite a frozen control body", async () => {
  const gate = deferred(); const send = jest.fn(() => gate.promise);
  const q = create({ send });
  const first = q.control("pause");
  await Promise.resolve(); await Promise.resolve();
  await expect(q.control("undo")).rejects.toThrow("current control");
  gate.resolve({ ...base(), revision: 1 }); await first;
  expect(send).toHaveBeenCalledTimes(1);
  expect(send.mock.calls[0][1].single.kind).toBe("pause");
});

test.each([409, 410, 412, 422])("terminal status %s requires deliberate reload, never request rebasing", async status => {
  const send = jest.fn().mockRejectedValue({ response: { status } });
  const q = create({ send }); q.enqueue(action("like", "c0"));
  await expect(q.flush()).rejects.toEqual({ response: { status } });
  expect(q.snapshot().conflict).toBe(true);
  await expect(q.flush()).rejects.toThrow("Reload");
  expect(send).toHaveBeenCalledTimes(1);
});

test("closing a queue suppresses late state writes and retains its frozen buffer", async () => {
  const gate = deferred(); const changed = jest.fn(); const save = jest.fn();
  const q = create({ send: () => gate.promise, changed, save }); q.enqueue(action("like", "c0"));
  const running = q.flush(); await Promise.resolve();
  const before = [changed.mock.calls.length, save.mock.calls.length]; q.close();
  gate.resolve(accept(base(), q.frozen)); await running;
  expect([changed.mock.calls.length, save.mock.calls.length]).toEqual(before);
});
