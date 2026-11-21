import { act } from "react";
import { createRoot } from "react-dom/client";
import { useAuth } from "@clerk/react";
import { Navigate, useLocation } from "react-router-dom";
import { AuthFormRoute } from "./AuthPages";

jest.mock("@clerk/react", () => ({ useAuth: jest.fn() }));
jest.mock("@clerk/react/legacy", () => ({}), { virtual: true });
jest.mock("react-router-dom", () => ({ useLocation: jest.fn(), Navigate: jest.fn(() => null) }));

let element, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  element = document.createElement("div");
  root = createRoot(element);
  useLocation.mockReturnValue({ search: "", hash: "" });
});
afterEach(() => { act(() => root.unmount()); });

function render(auth, path) {
  useAuth.mockReturnValue(auth);
  act(() => root.render(<AuthFormRoute signedInPath={path}><button>Continue</button></AuthFormRoute>));
}

test.each(["/app", "/app/start"])("an existing session goes straight to %s", path => {
  render({ isLoaded: true, isSignedIn: true }, path);
  expect(Navigate.mock.calls[0][0]).toMatchObject({ to: path, replace: true });
  expect(element.textContent).toBe("");
});

test("a signed-out visitor can use the form, and an explicit ticket gets its own handler", () => {
  render({ isLoaded: true, isSignedIn: false }, "/app");
  expect(element.textContent).toBe("Continue");
  useLocation.mockReturnValue({ search: "", hash: "#__clerk_ticket=fixture" });
  render({ isLoaded: true, isSignedIn: true }, "/app");
  expect(element.textContent).toBe("Continue");
  expect(Navigate).not.toHaveBeenCalled();
});
