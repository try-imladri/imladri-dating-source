import { act } from "react";
import { createRoot } from "react-dom/client";
import YourType, { clearYourTypePendingExcept } from "./YourType";
import { trackProfileWrite } from "@/lib/profileWriteBarrier";

jest.mock("@/lib/api", () => ({ errMsg: e => e?.response?.data?.detail || e.message || "Request failed" }));
const PREFIX = "imladri.your-type.pending.v1:";
const clone = value => JSON.parse(JSON.stringify(value));
const face = id => ({ face_id: id, id, url: "/your-type/sheet.jpg", sprite: { rect: { x: 0, y: 0, w: 192, h: 256 }, sheet_width: 1536, sheet_height: 1024 } });
function dto(overrides = {}) {
  return { id: "attempt", audience: "women", revision: 0, crowd: 1, completed_crowds: 0, next_milestone_crowds: 20,
    likes: 0, committed_likes: 0, required_likes: 30, likes_remaining: 30, status: "active", undo_available: false,
    current_crowd_choices: 0, max_choices_per_crowd: 64, updated_at: "2026-11-01T00:00:00Z", asset_edition: "original-v1",
    cards: Array.from({ length: 12 }, (_, i) => face("c" + i)), replacement_queue: Array.from({ length: 12 }, (_, i) => face("q" + i)), ...overrides };
}
const permission = () => ({ enabled: false, revision: 0, version: "your-type-research-v1" });
function client(initial = dto(), gender = null, sexuality = null) {
  let current = clone(initial);
  const api = {
    get: jest.fn(async path => {
      if (path === "/your-type") return { data: { audiences: [{ id: "women" }, { id: "men" }, { id: "both" }],
        profile: { adult_eligible: true, gender, sexuality }, sessions: [{ ...clone(current), state: { completed_crowds: current.completed_crowds } }], history_next: null } };
      if (path === "/your-type/research") return { data: permission() };
      if (path.startsWith("/your-type/history")) return { data: { sessions: [{ ...current, state: { completed_crowds: current.completed_crowds } }], next: null } };
      if (path === "/your-type/sessions/attempt") return { data: clone(current) };
      if (path === "/your-type/sessions/attempt/results") return { data: { ...clone(current),
        rankings: [], picked_photos: [], explicitly_passed_photos: [], feature_preferences: [], signal: "developing",
        prospective_pairwise: { pairs: 0 }, model_edition: "model-v1", source_revision: current.revision,
        interpretation: "Patterns within original synthetic adult portraits." } };
      throw Error("Unexpected fixture GET " + path);
    }),
    post: jest.fn(async (path, body) => {
      if (path === "/your-type/sessions") { current = { ...current, audience: body.audience }; return { data: clone(current) }; }
      const commands = path.endsWith("/batch") ? body.actions : [body];
      for (const command of commands) {
        if (["like", "pass"].includes(command.kind)) {
          const index = current.cards.findIndex(item => item.face_id === command.face_id);
          current.cards[index] = current.replacement_queue.shift();
          current.likes += Number(command.kind === "like"); current.likes_remaining = 30 - current.likes;
          current.current_crowd_choices += 1;
        } else if (command.kind === "next_crowd") {
          current.completed_crowds++; current.crowd++; current.current_crowd_choices = 0;
          // Reissue the same IDs deliberately to prove dwell starts fresh.
        } else if (command.kind === "pause") current.status = "paused";
        else if (command.kind === "resume") current.status = "active";
        else if (command.kind === "finish") current.status = "finished";
        current.revision++; current.undo_available = true; current.updated_at = "2026-11-02T00:00:00Z";
      }
      return { data: clone(current) };
    }),
    patch: jest.fn(async (_, body) => ({ data: { ...body, revision: body.revision + 1 } })),
    delete: jest.fn(async () => ({ data: { removed: true } })),
  };
  return api;
}
let host, root, images, observers, originalImage, originalObserver;
function button(text) { return [...host.querySelectorAll("button")].find(node => node.textContent.includes(text)); }
const click = node => act(async () => node.click());
const tick = ms => act(async () => jest.advanceTimersByTime(ms));
async function openAttempt() { await click(button("Open")); }
async function loadImages() { await act(async () => { for (const image of [...images]) image.onload?.(); }); }
async function visible(id) {
  await act(async () => { for (const observer of observers) if (observer.node?.dataset.faceId === id)
    observer.callback([{ isIntersecting: true, intersectionRatio: 1 }]); });
}
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true; jest.useFakeTimers(); sessionStorage.clear();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  let n = 0; Object.defineProperty(window, "crypto", { configurable: true, value: { randomUUID: () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}` } });
  images = []; observers = []; originalImage = window.Image; originalObserver = window.IntersectionObserver;
  window.Image = class { constructor() { images.push(this); } set src(value) { this.url = value; } };
  window.IntersectionObserver = class {
    constructor(callback) { this.callback = callback; observers.push(this); }
    observe(node) { this.node = node; } unobserve() {} disconnect() { this.node = null; }
  };
  host = document.body.appendChild(document.createElement("div")); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); window.Image = originalImage; window.IntersectionObserver = originalObserver; jest.useRealTimers(); });

test("known minor opens no endpoints and clears private browser recovery", async () => {
  sessionStorage.setItem(PREFIX + "owner", "private"); sessionStorage.setItem(PREFIX + "someone", "private");
  const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" underage />));
  expect(api.get).not.toHaveBeenCalled(); expect(sessionStorage.length).toBe(0);
  expect(host.textContent).toContain("available from age 18");
});

test("missing age403 links to profile without weakening the subscription gate", async () => {
  const api = client(); api.get.mockRejectedValue({ response: { status: 403, data: { detail: "Confirm profile age" } } });
  await act(async () => root.render(<YourType api={api} owner="owner" />));
  expect(host.querySelector('a[href="/app/personal?tab=basics"]')).not.toBeNull();
  expect(api.post).not.toHaveBeenCalled();
});

test("eligible bootstrap loads profile and attempts in one request without another adult attestation", async () => {
  const api = client(dto(), "Man", "straight");
  await act(async () => root.render(<YourType api={api} owner="owner" />));
  expect(api.get.mock.calls.map(([path]) => path)).toEqual(["/your-type"]);
  expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  expect(host.textContent).not.toContain("Browser storage is unavailable");
  expect(host.querySelector('[data-testid="your-type-stated-preferences"]')).toBeNull();
  expect(host.textContent).not.toContain("Your stated age & height preferences");
  expect(button("Straight")).toBeUndefined();
  await click(button("Start Your Type"));
  expect(api.post.mock.calls[0][1]).toEqual(expect.objectContaining({ audience: "women", viewer_gender: "Man" }));
  expect(api.post.mock.calls[0][1]).not.toHaveProperty("adult_confirmed");
  expect(api.get.mock.calls.some(([path]) => path === "/your-type/preferences")).toBe(false);
});

test("bootstrap waits for this member's pending profile save before reading validated age", async () => {
  let finish;
  const api = client(dto(), "Man");
  trackProfileWrite(api, "owner", new Promise(resolve => { finish = resolve; }));
  await act(async () => root.render(<YourType api={api} owner="owner" />));
  expect(api.get).not.toHaveBeenCalled();
  expect(host.textContent).toContain("Loading Your Type");
  await act(async () => finish());
  expect(api.get.mock.calls.map(([path]) => path)).toEqual(["/your-type"]);
  expect(host.textContent).toContain("Discover Your Type");
});

test("an old member's pending profile save neither delays nor later loads into a new owner", async () => {
  let finish;
  const api = client();
  trackProfileWrite(api, "old-owner", new Promise(resolve => { finish = resolve; }));
  await act(async () => root.render(<YourType api={api} owner="old-owner" />));
  expect(api.get).not.toHaveBeenCalled();
  await act(async () => root.render(<YourType api={api} owner="new-owner" />));
  expect(api.get.mock.calls.map(([path]) => path)).toEqual(["/your-type"]);
  await act(async () => finish());
  expect(api.get.mock.calls.map(([path]) => path)).toEqual(["/your-type"]);
  await click(button("women")); await click(button("Start Your Type"));
  expect(api.post.mock.calls[0][1].expected_member).toBe("new-owner");
});

test.each([false, null, undefined])("an ineligible or malformed profile marker %s cannot open the start controls", async adultEligible => {
  const api = client(); const get = api.get.getMockImplementation();
  api.get.mockImplementation(async path => path === "/your-type"
    ? { data: { audiences: [{ id: "women" }], profile: { adult_eligible: adultEligible }, sessions: [], history_next: null } }
    : get(path));
  await act(async () => root.render(<YourType api={api} owner="owner" />));
  expect(button("Start Your Type")).toBeUndefined();
  expect(host.querySelector('a[href="/app/personal?tab=basics"]')).not.toBeNull();
  expect(api.get.mock.calls.map(([path]) => path)).toEqual(["/your-type"]);
  expect(api.post).not.toHaveBeenCalled();
});

test("an older server falls back to saved profile and history without manufacturing age confirmation", async () => {
  const api = client(); const get = api.get.getMockImplementation();
  api.get.mockImplementation(async path => {
    if (path === "/your-type") return { data: { audiences: [{ id: "women" }, { id: "men" }] } };
    if (path === "/me/about") return { data: { about: { demographics: { age: 30, gender: "Woman", sexuality: "straight" } } } };
    return get(path);
  });
  await act(async () => root.render(<YourType api={api} owner="owner" />));
  expect(api.get.mock.calls.map(([path]) => path)).toEqual(["/your-type", "/me/about", "/your-type/history"]);
  await click(button("Start Your Type"));
  expect(api.post.mock.calls[0][1]).toEqual(expect.objectContaining({ audience: "men", viewer_gender: "Woman" }));
  expect(api.post.mock.calls[0][1]).not.toHaveProperty("adult_confirmed");
});

test.each([17, null])("legacy fallback age %s stays gated rather than accepting a checkbox", async age => {
  const api = client(); const get = api.get.getMockImplementation();
  api.get.mockImplementation(async path => path === "/your-type" ? { data: { audiences: [{ id: "women" }] } }
    : path === "/me/about" ? { data: { about: { demographics: { age } } } } : get(path));
  await act(async () => root.render(<YourType api={api} owner="owner" />));
  expect(button("Start Your Type")).toBeUndefined();
  expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  expect(api.post).not.toHaveBeenCalled();
});

test("history pages keep four attempts visible and open the selected later record", async () => {
  const api = client(); const get = api.get.getMockImplementation();
  api.get.mockImplementation(async path => {
    if (path === "/your-type") {
      const value = await get(path);
      value.data.sessions = Array.from({ length: 7 }, (_, i) => ({ ...dto(), id: `saved-${i}`, state: { completed_crowds: i } }));
      return value;
    }
    if (path === "/your-type/sessions/saved-4") return { data: dto({ id: "saved-4" }) };
    return get(path);
  });
  await act(async () => root.render(<YourType api={api} owner="owner" />));
  const history = host.querySelector('[aria-label="Your Type history"]');
  expect([...history.querySelectorAll("button")].filter(node => node.textContent === "Open")).toHaveLength(4);
  expect(button("Previous attempts").disabled).toBe(true);
  await click(button("Next attempts"));
  expect(history.textContent).toContain("Page 2 of 2");
  expect([...history.querySelectorAll("button")].filter(node => node.textContent === "Open")).toHaveLength(3);
  expect(button("Next attempts").disabled).toBe(true);
  await click(button("Open"));
  expect(api.get).toHaveBeenCalledWith("/your-type/sessions/saved-4");
  expect(host.querySelector('[aria-label="Your Type portrait selection"]')).not.toBeNull();
});

test("quick explicit tap declares only its selected loaded face and replaces it instantly", async () => {
  const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" />)); await openAttempt();
  expect(host.querySelector('[aria-label="Like face 1 of 12"]').disabled).toBe(true);
  await loadImages(); await click(host.querySelector('[aria-label="Like face 1 of 12"]'));
  expect(host.querySelector('[data-face-id="q0"]')).not.toBeNull();
  await tick(121);
  const command = api.post.mock.calls.find(([path]) => path.endsWith("/batch"))[1].actions[0];
  expect(command.viewed_face_ids).toEqual(["c0"]);
  expect(command.dwell_ms).toEqual({});
  expect(sessionStorage.getItem(PREFIX + "owner")).toBeNull();
});

test.each([[3, "120px 120px 120px"], [4, "180px 180px 180px 180px"]])("arrow navigation follows the actual %i-column portrait grid", async (columns, gridTemplateColumns) => {
  const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" />));
  await openAttempt(); await loadImages();
  const computed = jest.spyOn(window, "getComputedStyle").mockReturnValue({ gridTemplateColumns });
  try {
    const choices = [...host.querySelectorAll("[data-portrait-choice]")];
    choices[0].focus();
    await act(async () => choices[0].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })));
    expect(document.activeElement).toBe(choices[columns]);
    await act(async () => choices[columns].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true })));
    expect(document.activeElement).toBe(choices[0]);
    expect(api.post).not.toHaveBeenCalled();
  } finally { computed.mockRestore(); }
});

test("the small pass control records a pass without bubbling into a like", async () => {
  const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" />));
  await openAttempt(); await loadImages();
  await click(host.querySelector('[aria-label="Pass face 1 of 12"]')); await tick(121);
  const actions = api.post.mock.calls.find(([path]) => path.endsWith("/batch"))[1].actions;
  expect(actions).toHaveLength(1); expect(actions[0].kind).toBe("pass");
  expect(actions[0].face_id).toBe("c0");
});

test("scrolling the portrait grid never advances a crowd or records implicit passes", async () => {
  const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" />));
  await openAttempt(); await loadImages();
  const grid = host.querySelector('[aria-label^="Twelve adult portraits"]');
  await act(async () => {
    const start = new Event("touchstart", { bubbles: true }); start.touches = [{ clientX: 100, clientY: 300 }];
    const end = new Event("touchend", { bubbles: true }); end.changedTouches = [{ clientX: 100, clientY: 100 }];
    grid.dispatchEvent(start); grid.dispatchEvent(end);
  });
  await tick(150);
  expect(api.post).not.toHaveBeenCalled();
  expect(host.querySelector('[aria-label="Your Type history"]').textContent).toContain("0 crowds");
});

test("next crowd counts only loaded visible dwell and updates history on acknowledgment", async () => {
  const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" />)); await openAttempt();
  await loadImages(); await visible("c0"); await tick(300); await click(button("Next Crowd"));
  const body = api.post.mock.calls.find(([path]) => path.endsWith("/batch"))[1];
  expect(body.actions[0].viewed_face_ids).toEqual(["c0"]);
  expect(body.actions[0].dwell_ms.c0).toBeGreaterThanOrEqual(250);
  expect(host.querySelector('[aria-label="Your Type history"]').textContent).toContain("1 crowd");
  expect(api.get.mock.calls.filter(([path]) => path.startsWith("/your-type/history"))).toHaveLength(0);
});

test("reissued identical face IDs begin fresh visibility instead of carrying older dwell", async () => {
  const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" />)); await openAttempt();
  await loadImages(); await visible("c0"); await tick(300); await click(button("Next Crowd"));
  await loadImages(); await click(button("Next Crowd"));
  const calls = api.post.mock.calls.filter(([path]) => path.endsWith("/batch"));
  expect(calls[0][1].actions[0].viewed_face_ids).toEqual(["c0"]);
  expect(calls[1][1].actions[0].viewed_face_ids).toEqual([]);
});

test("hidden time is not reported as viewed dwell", async () => {
  const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" />)); await openAttempt();
  await loadImages(); await visible("c0"); await tick(100);
  await act(async () => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" }); document.dispatchEvent(new Event("visibilitychange")); });
  await tick(1000);
  await act(async () => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" }); document.dispatchEvent(new Event("visibilitychange")); });
  await visible("c0"); await tick(100); await click(button("Next Crowd"));
  expect(api.post.mock.calls[0][1].actions[0].viewed_face_ids).toEqual([]);
});

test("without IntersectionObserver the viewport fallback still excludes offscreen portraits", async () => {
  window.IntersectionObserver = undefined;
  const bounds = jest.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function () {
    const top = this.dataset.faceId === "c0" ? 0 : 900;
    return { top, bottom: top + 256, left: 0, right: 192, width: 192, height: 256 };
  });
  try {
    const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" />)); await openAttempt();
    await loadImages(); await tick(300); await click(button("Next Crowd"));
    expect(api.post.mock.calls[0][1].actions[0].viewed_face_ids).toEqual(["c0"]);
  } finally { bounds.mockRestore(); }
});

test("pending start has explicit exact-body recovery rather than silently changing audience", async () => {
  const body = { request_id: "00000000-0000-4000-8000-111111111111", expected_member: "owner", audience: "men", adult_confirmed: true };
  sessionStorage.setItem(PREFIX + "owner", JSON.stringify({ owner: "owner", start: body }));
  const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" />));
  expect(button("Start Your Type").disabled).toBe(true);
  await click(button("Recover previous start"));
  expect(api.post.mock.calls[0]).toEqual(["/your-type/sessions", body]);
  expect(host.textContent).toContain("Rating men");
  expect(sessionStorage.getItem(PREFIX + "owner")).toBeNull();
});

test("a preference start recovery keeps its exact original snapshot request after profile gender changes", async () => {
  const body = { request_id: "00000000-0000-4000-8000-222222222222", expected_member: "owner",
    audience: "women", adult_confirmed: true, sexuality: "straight", viewer_gender: "Man" };
  sessionStorage.setItem(PREFIX + "owner", JSON.stringify({ owner: "owner", start: body }));
  const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" profileGender="Woman" />));
  await click(button("Recover previous start"));
  expect(api.post.mock.calls[0]).toEqual(["/your-type/sessions", body]);
  expect(api.patch).not.toHaveBeenCalled();
});

test.each([undefined, "another-owner"])("a recovered start with missing or wrong owner %s is never rewritten or posted", async expected => {
  const body = { request_id: "00000000-0000-4000-8000-111111111111", audience: "men", adult_confirmed: true,
    ...(expected ? { expected_member: expected } : {}) };
  const raw = JSON.stringify({ owner: "owner", start: body });
  sessionStorage.setItem(PREFIX + "owner", raw);
  const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" />));
  expect(host.textContent).toContain("could not be verified");
  expect(button("Recover previous start")).toBeUndefined();
  expect(api.post).not.toHaveBeenCalled(); expect(sessionStorage.getItem(PREFIX + "owner")).toBe(raw);
  await click(button("Discard browser buffer")); expect(sessionStorage.getItem(PREFIX + "owner")).toBeNull();
});

test("corrupt browser recovery can be discarded without deleting a server attempt", async () => {
  sessionStorage.setItem(PREFIX + "owner", "{not-json");
  const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" />));
  expect(host.textContent).toContain("could not be verified");
  await click(button("Discard browser buffer"));
  expect(sessionStorage.getItem(PREFIX + "owner")).toBeNull();
  expect(api.delete).not.toHaveBeenCalled();
  expect(host.querySelector('[aria-label="Your Type history"]').textContent).toContain("women");
});

test("an unavailable saved session exposes an explicit buffer discard without deleting other history", async () => {
  sessionStorage.setItem(PREFIX + "owner", JSON.stringify({owner:"owner",session_id:"attempt",
    payload:{base_revision:0,frozen:null,pending:[{action_id:"tap",kind:"like",face_id:"c0",viewed_face_ids:["c0"],dwell_ms:{}}]}}));
  const api = client(); const get = api.get.getMockImplementation();
  api.get.mockImplementation(path => path === "/your-type/sessions/attempt" ?
    Promise.reject({response:{status:404,data:{detail:"Your Type session not found."}}}) : get(path));
  await act(async () => root.render(<YourType api={api} owner="owner" />));
  expect(host.querySelector('[aria-label="Browser recovery"]')).not.toBeNull();
  await click(button("Discard browser buffer"));
  expect(sessionStorage.getItem(PREFIX + "owner")).toBeNull();
  expect(api.delete).not.toHaveBeenCalled();
  expect(host.querySelector('[aria-label="Your Type history"]').textContent).toContain("women");
});

test("an immediate fresh attempt records its link to the previous attempt", async () => {
  const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" />));
  await openAttempt(); await click(button("Start a fresh attempt"));
  await click(button("women")); await click(button("Start Your Type"));
  const body = api.post.mock.calls.find(([path]) => path === "/your-type/sessions")[1];
  expect(body.retake_of).toBe("attempt"); expect(body.audience).toBe("women");
  expect(body).not.toHaveProperty("adult_confirmed");
});

test("fresh attempt controls stay beneath the selected history row without scrolling to Discover", async () => {
  const previous = HTMLElement.prototype.scrollIntoView;
  HTMLElement.prototype.scrollIntoView = jest.fn();
  try {
    const api = client(dto(), "Man", "straight");
    await act(async () => root.render(<YourType api={api} owner="owner" />));
    await openAttempt(); await tick(20);
    HTMLElement.prototype.scrollIntoView.mockClear();
    await click(button("Start a fresh attempt")); await tick(20);
    const row = host.querySelector('[data-attempt-id="attempt"]');
    const controls = row.querySelector('[aria-label="Fresh attempt controls"]');
    expect(controls).not.toBeNull();
    expect(controls.contains(button("Start Your Type"))).toBe(true);
    expect(controls.contains(button("Continue this attempt"))).toBe(true);
    expect(host.querySelector('.your-type-discover button')).toBeNull();
    expect(HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled();
    await click(button("Continue this attempt"));
    expect(host.querySelector('[aria-label="Fresh attempt controls"]')).toBeNull();
    expect(host.querySelector('[aria-label="Your Type portrait selection"]')).not.toBeNull();
    expect(api.post).not.toHaveBeenCalled();
  } finally { HTMLElement.prototype.scrollIntoView = previous; }
});

test("failed inline restart recovers the exact request beneath its original attempt after reload", async () => {
  const api = client(dto(), "Man", "straight");
  const post = api.post.getMockImplementation();
  api.post.mockRejectedValueOnce(Error("Connection interrupted")).mockImplementation(post);
  await act(async () => root.render(<YourType api={api} owner="owner" />));
  await click(button("Restart")); await click(button("Start Your Type"));
  const original = clone(api.post.mock.calls[0][1]);
  expect(original.retake_of).toBe("attempt");
  const inline = () => host.querySelector('[data-attempt-id="attempt"] [aria-label="Fresh attempt controls"]');
  expect(inline().textContent).toContain("Connection interrupted");
  expect(inline().contains(button("Recover previous start"))).toBe(true);
  expect(button("Continue this attempt").disabled).toBe(true);
  expect(button("Start Your Type").disabled).toBe(true);
  await act(async () => { root.unmount(); root = createRoot(host); root.render(<YourType api={api} owner="owner" />); });
  expect(inline().contains(button("Recover previous start"))).toBe(true);
  await click(button("Recover previous start"));
  expect(api.post.mock.calls.map(([, body]) => body)).toEqual([original, original]);
  expect(sessionStorage.getItem(PREFIX + "owner")).toBeNull();
});

test("selecting a later history row restarts only that attempt and clears selection on owner change", async () => {
  const api = client(dto(), "Man", "straight"), get = api.get.getMockImplementation();
  api.get.mockImplementation(async path => {
    const response = await get(path);
    if (path === "/your-type") response.data.sessions = Array.from({ length: 7 }, (_, i) => ({ ...dto(), id: `saved-${i}` }));
    return response;
  });
  await act(async () => root.render(<YourType api={api} owner="owner" />));
  await click(button("Next attempts")); await click(button("Restart"));
  expect(host.querySelector('[data-attempt-id="saved-4"] [aria-label="Fresh attempt controls"]')).not.toBeNull();
  expect(host.querySelector('[data-attempt-id="saved-5"] [aria-label="Fresh attempt controls"]')).toBeNull();
  await click(button("Start Your Type"));
  expect(api.post.mock.calls[0][1].retake_of).toBe("saved-4");
  await click(button("Start a fresh attempt"));
  await act(async () => root.render(<YourType api={api} owner="other-owner" />));
  expect(host.querySelector('[aria-label="Fresh attempt controls"]')).toBeNull();
  expect(host.querySelector('.your-type-discover button')).not.toBeNull();
});

test("unsaved choices cannot be discarded by a history restart", async () => {
  const api = client(); api.post.mockRejectedValue(Error("Offline"));
  await act(async () => root.render(<YourType api={api} owner="owner" />));
  await openAttempt(); await loadImages(); await click(host.querySelector('[aria-label="Like face 1 of 12"]'));
  await tick(121);
  expect(button("Restart").disabled).toBe(true);
  expect(button("Start a fresh attempt").disabled).toBe(true);
  await click(button("Restart"));
  expect(host.querySelector('[aria-label="Fresh attempt controls"]')).toBeNull();
  expect(sessionStorage.getItem(PREFIX + "owner")).not.toBeNull();
});

test("disabled browser storage does not become a fake corrupt buffer or block server use", async () => {
  const read = jest.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw Error("Storage disabled"); });
  try {
    const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" />));
    expect(host.querySelector('[aria-label="Browser recovery"]')).toBeNull();
    await click(button("women")); await click(button("Start Your Type"));
    expect(api.post.mock.calls[0][0]).toBe("/your-type/sessions");
    expect(api.post.mock.calls[0][1].expected_member).toBe("owner");
  } finally { read.mockRestore(); }
});

test("owner change discards another account buffer and ignores late private responses", async () => {
  let release;
  const delayed = new Promise(resolve => { release = resolve; });
  const first = { get: jest.fn(() => delayed) };
  const second = client();
  await act(async () => root.render(<YourType api={first} owner="old" />));
  sessionStorage.setItem(PREFIX + "old", JSON.stringify({ owner: "old", start: { audience: "PRIVATE-OLD" } }));
  await act(async () => root.render(<YourType api={second} owner="new" />));
  expect(sessionStorage.getItem(PREFIX + "old")).toBeNull();
  await act(async () => release({ data: { sessions: [{ audience: "PRIVATE-OLD" }], audiences: [{ id: "PRIVATE-OLD" }] } }));
  expect(host.textContent).not.toContain("PRIVATE-OLD");
  expect(host.textContent).toContain("Discover Your Type");
});

test("rating contains no inline research control and does not load or change research permission", async () => {
  const api = client(); await act(async () => root.render(<YourType api={api} owner="owner" />));
  expect(host.textContent).not.toContain("Your Type data & research");
  expect(api.get.mock.calls.some(([path]) => path === "/your-type/research")).toBe(false);
  expect(api.patch).not.toHaveBeenCalled();
});

test.each([
  ["Man", "Straight", "straight", "women"], ["Woman", "Straight", "straight", "men"],
  ["Man", "Gay", "gay", "men"], ["Woman", "Gay", "gay", "women"],
  ["Woman", "Lesbian", "lesbian", "women"], ["Man", "Lesbian", "lesbian", "women"],
  ["Man", "Bisexual", "bi", "both"], ["Non-binary", "Bisexual", "bi", "both"],
])("saved %s profile and %s sexuality sends %s preferences with %s portraits", async (gender, label, sexuality, audience) => {
  const api = client(dto(), gender, sexuality); await act(async () => root.render(<YourType api={api} owner="owner" profileGender={gender} />));
  expect(button(label)).toBeUndefined();
  await click(button("Start Your Type"));
  expect(api.post.mock.calls[0]).toEqual(["/your-type/sessions", expect.objectContaining({
    expected_member: "owner", sexuality, viewer_gender: gender, audience, use_saved_profile: true,
  })]);
  expect(api.patch).not.toHaveBeenCalled();
  if (audience === "both") expect(host.textContent).toContain("Rating women and men");
});

test.each([null, "Non-binary", "Unknown legacy gender"])("unknown or nonbinary profile %s requires explicit target rather than guessing", async gender => {
  const api = client(dto(), gender, "gay"); await act(async () => root.render(<YourType api={api} owner="owner" profileGender={gender} />));
  expect(button("Start Your Type").disabled).toBe(true);
  expect(api.post).not.toHaveBeenCalled();
  await click(button("women and men")); await click(button("Start Your Type"));
  expect(api.post.mock.calls[0][1]).toEqual(expect.objectContaining({sexuality: "gay", audience: "both",
    viewer_gender: gender === "Non-binary" ? gender : null}));
});

test("sexuality can remain unreported with an explicit legacy audience, without gender inference", async () => {
  const api = client(dto(), "Man"); await act(async () => root.render(<YourType api={api} owner="owner" profileGender="Man" />));
  expect(button("Start Your Type").disabled).toBe(true);
  await click([...host.querySelectorAll("button")].find(node => node.textContent === "men")); await click(button("Start Your Type"));
  const body = api.post.mock.calls[0][1];
  expect(body.audience).toBe("men"); expect(body).not.toHaveProperty("sexuality"); expect(body).not.toHaveProperty("viewer_gender");
  expect(api.patch).not.toHaveBeenCalled();
});

test("finished attempt offers resume instead of a rejected pause or active portrait grid", async () => {
  const api = client(dto({ status: "finished", completed_crowds: 20, likes: 30 }));
  await act(async () => root.render(<YourType api={api} owner="owner" />)); await openAttempt();
  expect(host.textContent).toContain("Finished. Resume whenever");
  expect(host.querySelector('[aria-label="Like face 1 of 12"]')).toBeNull();
  await click(button("Resume")); expect(api.post.mock.calls[0][1].kind).toBe("resume");
});

test("results record one explicit finish and refining resumes without implicit passes", async () => {
  const api = client(dto({ results_available: true, completed_crowds: 20, likes: 30, committed_likes: 30 }));
  await act(async () => root.render(<YourType api={api} owner="owner" />)); await openAttempt();
  await click(button("View your results"));
  expect(host.querySelector('[data-testid="your-type-results"]')).not.toBeNull();
  expect(api.post.mock.calls.map(([, body]) => body.kind)).toEqual(["finish"]);
  expect(api.post.mock.calls[0][1].viewed_face_ids).toEqual([]);
  await click(button("Keep refining"));
  expect(api.post.mock.calls.map(([, body]) => body.kind)).toEqual(["finish", "resume"]);
  expect(host.querySelector('[data-testid="your-type-results"]')).toBeNull();
});

test("viewing an already finished result appends no repeated finish action", async () => {
  const api = client(dto({ status: "finished", results_available: true, completed_crowds: 20, likes: 30 }));
  await act(async () => root.render(<YourType api={api} owner="owner" />)); await openAttempt();
  await click(button("View your results"));
  expect(host.querySelector('[data-testid="your-type-results"]')).not.toBeNull();
  expect(api.post).not.toHaveBeenCalled();
});

test("constant feature results show insufficient variation and never format null as a score", async () => {
  const api = client(dto({ status: "finished", results_available: true, completed_crowds: 20, likes: 30 }));
  const get = api.get.getMockImplementation();
  api.get.mockImplementation(async path => {
    const response = await get(path);
    if (path.endsWith("/results")) response.data.feature_preferences = [{ id: "facial_hair", label: "Facial hair",
      importance: 0, variation_available: false, values: [{ feature: 0, label: "None", direction: "undetermined",
        association_strength: null, standard_error: null, sufficient_option_evidence: false, option_examples: 120 }] }];
    return response;
  });
  await act(async () => root.render(<YourType api={api} owner="owner" />)); await openAttempt(); await click(button("View your results"));
  expect(host.textContent).toContain("Not enough variation");
  expect(host.textContent).toContain("Limited evidence");
  expect(host.textContent).not.toContain("NaN");
  expect(host.textContent).not.toContain("0% relative weight");
});

test("constant features show insufficient variation and never render null associations as preferences", async () => {
  const api = client(dto({status:"finished",results_available:true,completed_crowds:20,likes:30}));
  const get = api.get.getMockImplementation();
  api.get.mockImplementation(async path => {
    const response = await get(path);
    if (path.endsWith("/results")) response.data.feature_preferences = [{id:"facial_hair",label:"Facial hair",importance:0,
      variation_available:false,values:[{feature:0,label:"None",direction:"undetermined",sufficient_option_evidence:false,
        association_strength:null,standard_error:null,option_examples:202}]}];
    return response;
  });
  await act(async () => root.render(<YourType api={api} owner="owner" />)); await openAttempt();
  await click(button("View your results"));
  expect(host.textContent).toContain("Not enough variation");
  expect(host.textContent).toContain("Limited evidence");
  expect(host.textContent).not.toContain("0% relative weight");
  expect(host.textContent).not.toContain("association 0.00");
});

test("global signout cleanup removes only Your Type recovery keys", () => {
  sessionStorage.setItem(PREFIX + "owner", "private"); sessionStorage.setItem("unrelated", "keep");
  clearYourTypePendingExcept(null);
  expect(sessionStorage.getItem(PREFIX + "owner")).toBeNull(); expect(sessionStorage.getItem("unrelated")).toBe("keep");
});

test("per-audience progress never opens results from aggregate likes when the server still withholds them", async () => {
  const api = client(dto({ audience: "both", completed_crowds: 20, likes: 60, committed_likes: 60, results_available: false,
    audience_progress: { women: { completed_crowds: 20, committed_likes: 40, negative_examples: 5, results_available: true },
      men: { completed_crowds: 10, committed_likes: 20, negative_examples: 1, results_available: false } } }));
  await act(async () => root.render(<YourType api={api} owner="owner" />)); await openAttempt();
  const progress = host.querySelector('[aria-label="Progress by portrait audience"]');
  expect(progress.textContent).toContain("Women: 20 crowds · 40 saved likes · 5 passes · Profile available");
  expect(progress.textContent).toContain("Men: 10 crowds · 20 saved likes · 1 passes · Needs 20 crowds");
  expect(button("Keep rating:").disabled).toBe(true);
  expect(api.get.mock.calls.some(([path]) => path.endsWith("/results"))).toBe(false);
});
