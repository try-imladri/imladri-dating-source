import { act } from "react";
import { createRoot } from "react-dom/client";
import { Silhouette } from "./Silhouette";

let mockTheme = "light";
jest.mock("@/lib/theme", () => ({ useTheme: () => ({ theme: mockTheme }) }));
const originals = {
  matchMedia: Object.getOwnPropertyDescriptor(window, "matchMedia"),
  IntersectionObserver: Object.getOwnPropertyDescriptor(window, "IntersectionObserver"),
  visibilityState: Object.getOwnPropertyDescriptor(document, "visibilityState"),
};
let element, root, reduced, visibility, listeners, media, observer;
const render = (props = {}) => {
  act(() => root.render(<Silhouette src="/img/couple-walk.webp" testid="walking" {...props} />));
  return element.querySelector("img");
};
const preference = value => act(() => {
  reduced = value;
  [...listeners].forEach(listener => listener({ matches: value }));
});
const page = value => act(() => {
  visibility = value;
  document.dispatchEvent(new Event("visibilitychange"));
});
const intersect = value => act(() => observer.callback([{ isIntersecting: value }]));

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  mockTheme = "light";
  reduced = false;
  visibility = "visible";
  listeners = new Set();
  media = {
    get matches() { return reduced; },
    addEventListener: jest.fn((type, listener) => listeners.add(listener)),
    removeEventListener: jest.fn((type, listener) => listeners.delete(listener)),
  };
  Object.defineProperty(window, "matchMedia", { configurable: true, value: jest.fn(() => media) });
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
  Object.defineProperty(window, "IntersectionObserver", { configurable: true, value: class {
    constructor(callback) {
      this.callback = callback;
      this.observe = jest.fn();
      this.disconnect = jest.fn();
      observer = this;
    }
  } });
  element = document.body.appendChild(document.createElement("div"));
  root = createRoot(element);
});
afterEach(() => {
  if (root) act(() => root.unmount());
  element.remove();
  for (const key of ["matchMedia", "IntersectionObserver"]) {
    if (originals[key]) Object.defineProperty(window, key, originals[key]);
    else delete window[key];
  }
  if (originals.visibilityState) Object.defineProperty(document, "visibilityState", originals.visibilityState);
  else delete document.visibilityState;
});

test("normal and fast playback preserve theme, decoration and generic sizing", () => {
  const image = render({ className: "hero-size" });
  expect(image.getAttribute("src")).toBe("/img/couple-walk.webp?v=5");
  expect(image.dataset.playing).toBe("true");
  expect(image.dataset.fast).toBe("false");
  expect(image.getAttribute("alt")).toBe("");
  expect(image.getAttribute("aria-hidden")).toBe("true");
  expect(image.className).toContain("hero-size");
  expect(image.hasAttribute("width")).toBe(false);
  expect(image.hasAttribute("height")).toBe(false);
  mockTheme = "dark";
  render({ fast: true });
  expect(image.getAttribute("src")).toBe("/img/couple-walk-fast-w.webp?v=5");
  expect(image.dataset.theme).toBe("dark");
  expect(image.dataset.fast).toBe("true");
  expect(observer.observe).toHaveBeenCalledWith(image);
});

test("a live reduced-motion preference swaps the animation for its themed still", () => {
  mockTheme = "dark";
  const image = render({ fast: true });
  preference(true);
  expect(image.dataset.playing).toBe("false");
  expect(image.getAttribute("src")).toBe("/img/couple-walk-still-w.png?v=5");
  preference(false);
  expect(image.dataset.playing).toBe("true");
  expect(image.getAttribute("src")).toBe("/img/couple-walk-fast-w.webp?v=5");
});

test("initial reduced motion starts with a still", () => {
  reduced = true;
  const image = render();
  expect(image.getAttribute("src")).toBe("/img/couple-walk-still.png?v=5");
  expect(image.dataset.playing).toBe("false");
});

test("legacy media listeners react and are removed on unmount", () => {
  media = {
    get matches() { return reduced; },
    addListener: jest.fn(listener => listeners.add(listener)),
    removeListener: jest.fn(listener => listeners.delete(listener)),
  };
  const image = render();
  preference(true);
  expect(image.dataset.playing).toBe("false");
  preference(false);
  expect(image.dataset.playing).toBe("true");
  expect(media.addListener).toHaveBeenCalledTimes(1);
  act(() => root.unmount());
  root = null;
  expect(media.removeListener).toHaveBeenCalledWith(media.addListener.mock.calls[0][0]);
  expect(listeners.size).toBe(0);
});

test("page visibility and intersection independently block resumption", () => {
  const image = render();
  page("hidden");
  expect(image.dataset.playing).toBe("false");
  expect(image.getAttribute("src")).toBe("/img/couple-walk-still.png?v=5");
  intersect(false);
  page("visible");
  expect(image.dataset.playing).toBe("false");
  intersect(true);
  expect(image.dataset.playing).toBe("true");
  preference(true);
  page("hidden");
  page("visible");
  expect(image.dataset.playing).toBe("false");
});

test("opening in a background page starts with a still", () => {
  visibility = "hidden";
  const image = render();
  expect(image.dataset.playing).toBe("false");
  page("visible");
  expect(image.dataset.playing).toBe("true");
});

test("the optional hero pause respects every other playback condition", () => {
  const image = render({ paused: true, fast: true });
  expect(image.dataset.playing).toBe("false");
  expect(image.getAttribute("src")).toBe("/img/couple-walk-still.png?v=5");
  render({ paused: false, fast: true });
  expect(image.getAttribute("src")).toBe("/img/couple-walk-fast.webp?v=5");
  intersect(false);
  render({ paused: true });
  render({ paused: false });
  expect(image.dataset.playing).toBe("false");
  intersect(true);
  expect(image.dataset.playing).toBe("true");
});

test("missing browser APIs leave other silhouette assets usable", () => {
  Object.defineProperty(window, "matchMedia", { configurable: true, value: undefined });
  Object.defineProperty(window, "IntersectionObserver", { configurable: true, value: undefined });
  const image = render({ src: "/img/couple-reunion.webp" });
  expect(image.getAttribute("src")).toBe("/img/couple-reunion.webp?v=5");
  page("hidden");
  expect(image.getAttribute("src")).toBe("/img/couple-reunion-still.png?v=5");
});

test("leaving the hero releases modern listeners and the observer", () => {
  const remove = jest.spyOn(document, "removeEventListener");
  render();
  const mediaListener = media.addEventListener.mock.calls[0][1];
  act(() => root.unmount());
  root = null;
  expect(media.removeEventListener).toHaveBeenCalledWith("change", mediaListener);
  expect(remove).toHaveBeenCalledWith("visibilitychange", expect.any(Function));
  expect(observer.disconnect).toHaveBeenCalledTimes(1);
  expect(listeners.size).toBe(0);
  remove.mockRestore();
});
