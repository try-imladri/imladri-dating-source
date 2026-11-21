import { act } from "react";
import { createRoot } from "react-dom/client";
import { Phone } from "./Phone";

jest.mock("./PublicLayout", () => ({ useConfig: () => ({}), signupPath: () => "/sign-up" }));
jest.mock("@/lib/api", () => ({ setOrigin: jest.fn() }));
jest.mock("react-router-dom", () => ({ Link: ({ to, children }) => <a href={to}>{children}</a> }));
jest.mock("framer-motion", () => ({
  AnimatePresence: ({ children }) => children,
  motion: { div: ({ initial, animate, exit, transition, ...props }) => <div {...props} /> },
}));

let host, root;
const find = id => host.querySelector('[data-testid="phone-' + id + '"]');
const advance = ms => act(() => jest.advanceTimersByTime(ms));
const click = id => act(() => find(id).click());

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.useFakeTimers();
  host = document.createElement("div");
  root = createRoot(host);
  act(() => root.render(<Phone />));
});
afterEach(() => { act(() => root.unmount()); jest.useRealTimers(); });

test.each([[0,0], [0,1], [1,0], [1,1], [2,0], [2,1]])(
  "the complete response arrives before offering the next choice or outcome on path %i / %i", (first, second) => {
    advance(2200);
    expect(find("option-0")).toBeNull();
    advance(1100);
    click("option-" + first);
    advance(1100);
    expect(find("option-0")).toBeNull();
    advance(1100);
    click("option-" + second);
    advance(1100);
    expect(find("learn")).toBeNull();
    advance(1100);
    expect(find("learn")).toBeTruthy();
    expect(find("thread").querySelectorAll("p.bubble-him")).toHaveLength(7);
  }
);

test("switching contacts during a reply clears pending lines and restarts the opening", () => {
  advance(3300);
  click("option-0");
  advance(1100);
  click("gender-her");
  advance(2200);
  expect(find("thread").textContent).not.toContain("next week maybe?");
  expect(find("option-0")).toBeNull();
  advance(1100);
  expect(find("option-0")).toBeTruthy();
  expect(find("contact-name").textContent).toBe("Mia");
});
