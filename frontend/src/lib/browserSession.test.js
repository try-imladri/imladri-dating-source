import { beginBrowserSession, endBrowserSession, hasBrowserSession, recordMemberVisit } from "./browserSession";

beforeEach(() => { endBrowserSession(); });
afterEach(() => { endBrowserSession(); });

test("an unchecked member remains signed in when a separate tab has no session storage", () => {
  beginBrowserSession();
  sessionStorage.clear();
  expect(hasBrowserSession()).toBe(true);
  expect(document.cookie).toContain("imladri_browser_session=1");
});

test("the first active old tab migrates its tab-local marker", () => {
  sessionStorage.setItem("imaldri_alive", "1");
  expect(hasBrowserSession()).toBe(true);
  expect(sessionStorage.getItem("imaldri_alive")).toBeNull();
  expect(document.cookie).toContain("imladri_browser_session=1");
});

test("ending a session clears the shared marker and no new marker is created on a cold visit", () => {
  beginBrowserSession();
  endBrowserSession();
  expect(hasBrowserSession()).toBe(false);
  expect(document.cookie).not.toContain("imladri_browser_session");
});

test("a visit is acknowledged per member and UTC day, and an uncertain request retries with the same ID", async () => {
  const original = global.crypto;
  global.crypto = { randomUUID: jest.fn().mockReturnValueOnce("first").mockReturnValueOnce("second").mockReturnValueOnce("third") };
  const api = { post: jest.fn().mockRejectedValueOnce(new Error("lost response")).mockResolvedValue({}) };
  try {
    await expect(recordMemberVisit(api, "a", "2026-11-08")).rejects.toThrow("lost response");
    await recordMemberVisit(api, "a", "2026-11-08");
    await recordMemberVisit(api, "a", "2026-11-08");
    await recordMemberVisit(api, "a", "2026-11-09");
    await recordMemberVisit(api, "b", "2026-11-09");
    expect(api.post.mock.calls.map(([, body]) => body.attempt_id)).toEqual(["first", "first", "second", "third"]);
  } finally {
    global.crypto = original;
  }
});
