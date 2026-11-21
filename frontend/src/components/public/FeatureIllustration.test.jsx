import { act } from "react";
import { createRoot } from "react-dom/client";
import { FeatureIllustration } from "./FeatureIllustration";

jest.mock("./Silhouette", () => ({
  Silhouette: ({ src, paused, className, testid }) => <img src={src} alt="" className={className} data-testid={testid} data-paused={String(paused)} />,
}));

const originals = {
  matchMedia: Object.getOwnPropertyDescriptor(window, "matchMedia"),
  IntersectionObserver: Object.getOwnPropertyDescriptor(window, "IntersectionObserver"),
  visibilityState: Object.getOwnPropertyDescriptor(document, "visibilityState"),
};
let root, host, media, mediaListeners, reduced, visibility, observer;
const render = (props = {}) => {
  act(() => root.render(<FeatureIllustration feature="couples" testid="scene" {...props} />));
  return host.querySelector('[data-testid="scene"]');
};
const preference = value => act(() => {
  reduced = value;
  [...mediaListeners].forEach(listener => listener({ matches: value }));
});
const page = value => act(() => {
  visibility = value;
  document.dispatchEvent(new Event("visibilitychange"));
});
const intersect = value => act(() => observer.callback([{ isIntersecting: value }]));

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  reduced = false;
  visibility = "visible";
  mediaListeners = new Set();
  media = {
    get matches() { return reduced; },
    addEventListener: jest.fn((_type, listener) => mediaListeners.add(listener)),
    removeEventListener: jest.fn((_type, listener) => mediaListeners.delete(listener)),
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
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});

afterEach(() => {
  if (root) act(() => root.unmount());
  host.remove();
  for (const key of ["matchMedia", "IntersectionObserver"]) {
    if (originals[key]) Object.defineProperty(window, key, originals[key]);
    else delete window[key];
  }
  if (originals.visibilityState) Object.defineProperty(document, "visibilityState", originals.visibilityState);
  else delete document.visibilityState;
  jest.restoreAllMocks();
});

test.each([
  ["couples", "/img/features/couples-v1.png"],
  ["individual", "/img/features/individual-v1.png"],
  ["intimacy", "/img/features/intimacy-v1.png"],
  ["communication", "/img/features/communication-v1.png"],
])("%s selects its own generated illustration and decorative layers", (feature, asset) => {
  const scene = render({ feature, className: "hero-art" });
  const images = [...scene.querySelectorAll("img")];
  expect(scene.dataset.feature).toBe(feature);
  expect(scene.dataset.playing).toBe("true");
  expect(scene.classList.contains("hero-art")).toBe(true);
  expect(scene.getAttribute("aria-hidden")).toBe("true");
  expect(images).toHaveLength(2);
  expect(images.map(image => image.getAttribute("src"))).toEqual([asset, asset]);
  expect(images.every(image => image.getAttribute("alt") === "")).toBe(true);
  expect(scene.querySelector(".feature-reflection-glint") !== null).toBe(feature === "individual");
  expect(scene.querySelector(".feature-speech") !== null).toBe(feature === "communication");
});

test("Your Type delegates to the original walk and receives the combined motion state", () => {
  const scene = render({ feature: "your-type" });
  const original = scene.querySelector('[data-testid="home-original-walking-couple"]');
  expect(scene.dataset.feature).toBe("your-type");
  expect(original.getAttribute("src")).toBe("/img/couple-walk.webp");
  expect(original.dataset.paused).toBe("false");
  render({ feature: "your-type", paused: true });
  expect(scene.dataset.playing).toBe("false");
  expect(original.dataset.paused).toBe("true");
  expect(scene.querySelector(".feature-scene")).toBeNull();
});

test("an initial reduced-motion setting starts static and reacts to modern preference changes", () => {
  reduced = true;
  const scene = render();
  expect(scene.dataset.playing).toBe("false");
  preference(false);
  expect(scene.dataset.playing).toBe("true");
  preference(true);
  expect(scene.dataset.playing).toBe("false");
  expect(media.addEventListener).toHaveBeenCalledWith("change", expect.any(Function));
});

test("legacy reduced-motion changes work and their listener is released", () => {
  media = {
    get matches() { return reduced; },
    addListener: jest.fn(listener => mediaListeners.add(listener)),
    removeListener: jest.fn(listener => mediaListeners.delete(listener)),
  };
  const scene = render();
  preference(true);
  expect(scene.dataset.playing).toBe("false");
  preference(false);
  expect(scene.dataset.playing).toBe("true");
  const listener = media.addListener.mock.calls[0][0];
  act(() => root.unmount());
  root = null;
  expect(media.removeListener).toHaveBeenCalledWith(listener);
  expect(mediaListeners.size).toBe(0);
});

test("opening in a hidden document stays static until that document is visible", () => {
  visibility = "hidden";
  const scene = render();
  expect(scene.dataset.playing).toBe("false");
  page("visible");
  expect(scene.dataset.playing).toBe("true");
});

test("visibility and intersection must both allow motion before it can resume", () => {
  const scene = render();
  expect(observer.observe).toHaveBeenCalledWith(scene);
  page("hidden");
  expect(scene.dataset.playing).toBe("false");
  intersect(false);
  page("visible");
  expect(scene.dataset.playing).toBe("false");
  intersect(true);
  expect(scene.dataset.playing).toBe("true");
  preference(true);
  page("hidden");
  page("visible");
  expect(scene.dataset.playing).toBe("false");
});

test("removing explicit pause cannot override an offscreen or reduced-motion condition", () => {
  const scene = render({ paused: true });
  expect(scene.dataset.playing).toBe("false");
  intersect(false);
  render({ paused: false });
  expect(scene.dataset.playing).toBe("false");
  intersect(true);
  expect(scene.dataset.playing).toBe("true");
  preference(true);
  render({ paused: true });
  render({ paused: false });
  expect(scene.dataset.playing).toBe("false");
});

test("missing optional browser APIs preserve the illustration and document visibility protection", () => {
  Object.defineProperty(window, "matchMedia", { configurable: true, value: undefined });
  Object.defineProperty(window, "IntersectionObserver", { configurable: true, value: undefined });
  const scene = render({ feature: "communication" });
  expect(scene.querySelectorAll("img")).toHaveLength(2);
  expect(scene.dataset.playing).toBe("true");
  page("hidden");
  expect(scene.dataset.playing).toBe("false");
});

test("unmount removes the actual media and document listeners and disconnects its observer", () => {
  const remove = jest.spyOn(document, "removeEventListener");
  render();
  const mediaListener = media.addEventListener.mock.calls[0][1];
  act(() => root.unmount());
  root = null;
  expect(media.removeEventListener).toHaveBeenCalledWith("change", mediaListener);
  expect(remove).toHaveBeenCalledWith("visibilitychange", expect.any(Function));
  expect(observer.disconnect).toHaveBeenCalledTimes(1);
  expect(mediaListeners.size).toBe(0);
});
