import { act } from "react";
import { createRoot } from "react-dom/client";
import { HeroStage } from "./HeroStage";
import { LANDING_HEADLINE, LANDING_SUMMARY } from "./landingCopy";

jest.mock("@/lib/theme", () => ({ useTheme: jest.fn(() => ({ theme: "light" })) }));
const { useTheme } = require("@/lib/theme");

let root, host, reduced, listeners, hidden, matchMedia;
const walking = () => host.querySelector('[data-testid="home-original-walking-couple"]');
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.useFakeTimers(); reduced = false; listeners = new Set();
  hidden = Object.getOwnPropertyDescriptor(document, "visibilityState");
  matchMedia = window.matchMedia;
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  window.matchMedia = jest.fn(() => ({ get matches() { return reduced; },
    addEventListener: (_type, fn) => listeners.add(fn), removeEventListener: (_type, fn) => listeners.delete(fn) }));
  useTheme.mockReturnValue({ theme: "light" });
  host = document.body.appendChild(document.createElement("div")); root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount()); host.remove(); window.matchMedia = matchMedia;
  if (hidden) Object.defineProperty(document, "visibilityState", hidden); else delete document.visibilityState;
  jest.useRealTimers();
});
const render = () => act(() => root.render(<HeroStage />));

test("one fixed message and the original walking couple replace the rotating stories without new controls", () => {
  render();
  expect(host.querySelectorAll("h1")).toHaveLength(1);
  expect(host.querySelector("h1").textContent).toBe(LANDING_HEADLINE);
  expect(host.querySelector('[data-testid="home-product-summary"]').textContent).toBe(LANDING_SUMMARY);
  const panel = host.querySelector('[data-testid="home-feature-container"]');
  expect([...panel.children].map(x => x.dataset.testid)).toEqual(["home-walking-scene", "home-feature-story"]);
  expect(walking().getAttribute("src")).toBe("/img/couple-walk.webp?v=5");
  expect(walking().dataset.fast).toBe("false");
  expect(host.querySelectorAll("button, [role=dialog], nav, [aria-live], [aria-keyshortcuts]")).toHaveLength(0);
  const before = host.innerHTML;
  act(() => jest.advanceTimersByTime(5200 * 10));
  expect(host.innerHTML).toBe(before);
  expect(jest.getTimerCount()).toBe(0);
});

test("the actual silhouette keeps its light and dark original animation assets", () => {
  render(); expect(walking().dataset.playing).toBe("true");
  useTheme.mockReturnValue({ theme: "dark" }); render();
  expect(walking().getAttribute("src")).toBe("/img/couple-walk-w.webp?v=5");
  expect(walking().getAttribute("aria-hidden")).toBe("true");
});

test("reduced motion switches to the existing themed still and can change without changing the message", () => {
  reduced = true; useTheme.mockReturnValue({ theme: "dark" }); render();
  expect(walking().getAttribute("src")).toBe("/img/couple-walk-still-w.png?v=5");
  expect(walking().dataset.playing).toBe("false");
  act(() => { reduced = false; listeners.forEach(fn => fn()); });
  expect(walking().getAttribute("src")).toBe("/img/couple-walk-w.webp?v=5");
  expect(host.querySelector("h1").textContent).toBe(LANDING_HEADLINE);
});

test("a hidden page pauses the original animation and resumes on return without story timers", () => {
  render();
  act(() => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" }); document.dispatchEvent(new Event("visibilitychange")); });
  expect(walking().getAttribute("src")).toBe("/img/couple-walk-still.png?v=5");
  act(() => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" }); document.dispatchEvent(new Event("visibilitychange")); });
  expect(walking().dataset.playing).toBe("true"); expect(jest.getTimerCount()).toBe(0);
});
