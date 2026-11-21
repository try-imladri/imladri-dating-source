import { act } from "react";
import { createRoot } from "react-dom/client";
import { ShareButton } from "./Bits";
import { HeroStage } from "./HeroStage";

jest.mock("./Phone", () => ({ Phone: () => null }));
jest.mock("./Silhouette", () => ({ Silhouette: () => null }));
jest.mock("./PublicLayout", () => ({ useConfig: () => ({}) }));
jest.mock("@/lib/api", () => ({}));
jest.mock("react-router-dom", () => ({ Link: ({ to, children }) => <a href={to}>{children}</a> }));
jest.mock("framer-motion", () => {
  const React = require("react");
  const tag = name => React.forwardRef(({ layoutId, transition, initial, animate, exit, children, ...props }, ref) =>
    React.createElement(name, { ...props, ref }, children));
  return { AnimatePresence: ({ children }) => children, motion: { button: tag("button"), div: tag("div"), span: tag("span") } };
});

let element, root;
const originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
const originalShare = Object.getOwnPropertyDescriptor(navigator, "share");
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  element = document.body.appendChild(document.createElement("div"));
  root = createRoot(element);
});
afterEach(() => {
  act(() => root.unmount());
  element.remove();
  for (const [key, value] of [["share", originalShare], ["clipboard", originalClipboard]]) {
    if (value) Object.defineProperty(navigator, key, value);
    else delete navigator[key];
  }
});

test("a blocked clipboard offers a focused, selected URL for manual copying", async () => {
  Object.defineProperty(navigator, "share", { configurable: true, value: undefined });
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
    writeText: jest.fn().mockRejectedValue(new DOMException("Blocked", "NotAllowedError")),
  } });
  act(() => root.render(<ShareButton title="Archetype" />));
  await act(async () => element.querySelector("button").click());
  const input = element.querySelector("input");
  expect(input.value).toBe(window.location.href);
  expect(document.activeElement).toBe(input);
  expect(input.selectionEnd - input.selectionStart).toBe(input.value.length);
});

test("canceling native sharing leaves the clipboard untouched", async () => {
  Object.defineProperty(navigator, "share", { configurable: true, value: jest.fn().mockRejectedValue(new DOMException("Canceled", "AbortError")) });
  const writeText = jest.fn();
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  act(() => root.render(<ShareButton title="Figure" />));
  await act(async () => element.querySelector("button").click());
  expect(writeText).not.toHaveBeenCalled();
  expect(element.querySelector("input")).toBeNull();
});

test("the fixed landing message does not open a dialog or steal keyboard focus", () => {
  const previous = document.body.appendChild(document.createElement("button"));
  previous.focus();
  act(() => root.render(<HeroStage />));
  expect(document.activeElement).toBe(previous);
  expect(element.querySelector("button")).toBeNull();
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(element.querySelector("h1").textContent).toContain("Understand your fit");
  previous.remove();
});
