import { act } from "react";
import { createRoot } from "react-dom/client";
import { useSignIn, useSignUp as useLegacySignUp } from "@clerk/react/legacy";
import { useAuth, useSignIn as useGoogleSignIn, useSignUp } from "@clerk/react";
import { useNavigate } from "react-router-dom";
import { SignInPage, SignUpPage } from "./AuthPages";

jest.mock("@clerk/react/legacy", () => ({ useSignIn: jest.fn(), useSignUp: jest.fn() }), { virtual: true });
jest.mock("@clerk/react", () => ({ useAuth: jest.fn(), useSignIn: jest.fn(), useSignUp: jest.fn() }));
jest.mock("react-router-dom", () => ({
  Link: ({ children, to }) => <a href={to}>{children}</a>,
  useNavigate: jest.fn(),
}));

let host, root, signIn, signUp, googleSignIn, googleSignUp, setActive, navigate;
const googleReturn = "/app/start?google_return=1";
const freshGoogleRedirect = {
  strategy: "oauth_google",
  redirectUrl: googleReturn,
  redirectUrlComplete: new URL(googleReturn, window.location.origin).href,
};
const field = id => host.querySelector(`[data-testid="${id}"]`);
const type = (id, value) => act(async () => {
  const input = field(id);
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
});
const click = element => act(async () => element.click());
const submit = () => act(async () => {
  field("sign-in-page").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
});

beforeEach(async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  setActive = jest.fn();
  navigate = jest.fn();
  signIn = {
    create: jest.fn(), attemptFirstFactor: jest.fn(), resetPassword: jest.fn(),
    prepareSecondFactor: jest.fn(), attemptSecondFactor: jest.fn(),
    authenticateWithRedirect: jest.fn().mockResolvedValue(undefined),
  };
  signUp = { authenticateWithRedirect: jest.fn().mockResolvedValue(undefined) };
  googleSignIn = { sso: jest.fn().mockResolvedValue({ error: null }) };
  googleSignUp = { sso: jest.fn().mockResolvedValue({ error: null }) };
  useSignIn.mockReturnValue({ isLoaded: true, signIn, setActive });
  useLegacySignUp.mockReturnValue({ isLoaded: true, signUp });
  useAuth.mockReturnValue({ isLoaded: true, isSignedIn: false });
  useGoogleSignIn.mockReturnValue({ signIn: googleSignIn });
  useSignUp.mockReturnValue({ signUp: googleSignUp });
  useNavigate.mockReturnValue(navigate);
  await act(async () => root.render(<SignInPage />));
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

test("recovery verifies the email code before replacing the password and ending other sessions", async () => {
  signIn.create.mockResolvedValue({});
  signIn.attemptFirstFactor.mockRejectedValueOnce(new Error("Code incorrect")).mockResolvedValue({ status: "needs_new_password" });
  signIn.resetPassword.mockResolvedValue({ status: "complete", createdSessionId: "recovered" });
  await type("sign-in-email", "member@example.com");
  await click(field("forgot-password"));
  expect(field("sign-in-email").value).toBe("member@example.com");
  await submit();
  expect(signIn.create).toHaveBeenCalledWith({ identifier: "member@example.com", strategy: "reset_password_email_code" });
  await type("sign-in-code", "000000");
  await submit();
  expect(field("sign-in-error").textContent).toBe("Code incorrect");
  expect(field("reset-password")).toBeNull();
  await type("sign-in-code", "123456");
  await submit();
  expect(field("reset-password").autocomplete).toBe("new-password");
  await type("reset-password", "new-password-here");
  await type("reset-confirm", "different");
  await submit();
  expect(signIn.resetPassword).not.toHaveBeenCalled();
  await type("reset-confirm", "new-password-here");
  await submit();
  expect(signIn.resetPassword).toHaveBeenCalledWith({ password: "new-password-here", signOutOfOtherSessions: true });
  expect(setActive).toHaveBeenCalledWith({ session: "recovered" });
  expect(navigate).toHaveBeenCalledWith("/dating");
});

test("unknown addresses see the same recovery prompt and can return to sign-in", async () => {
  signIn.create.mockRejectedValue({ errors: [{ code: "form_identifier_not_found", message: "Unknown email" }] });
  await click(field("forgot-password"));
  await type("sign-in-email", "unknown@example.com");
  await submit();
  expect(host.querySelector('[role="status"]').textContent).toContain("If this address");
  expect(field("sign-in-error")).toBeNull();
  await click([...host.querySelectorAll("button")].find(button => button.textContent === "Resend code"));
  expect(signIn.create).toHaveBeenCalledTimes(2);
  await type("sign-in-code", "000000");
  await submit();
  expect(signIn.attemptFirstFactor).not.toHaveBeenCalled();
  expect(field("reset-password")).toBeNull();
  await click([...host.querySelectorAll("button")].find(button => button.textContent === "Back to sign in"));
  expect(field("sign-in-password")).toBeTruthy();
  expect(field("auth-google-button")).toBeTruthy();
});

test("password sign-in advances past a required email device challenge before activating a session", async () => {
  signIn.create.mockResolvedValue({ status: "needs_client_trust", supportedSecondFactors: [{ strategy: "email_code", emailAddressId: "address" }] });
  signIn.attemptSecondFactor.mockResolvedValue({ status: "complete", createdSessionId: "trusted" });
  await type("sign-in-email", "member@example.com");
  await type("sign-in-password", "valid-password");
  await submit();
  expect(signIn.prepareSecondFactor).toHaveBeenCalledWith({ strategy: "email_code", emailAddressId: "address" });
  expect(setActive).not.toHaveBeenCalled();
  await type("sign-in-code", "123456");
  await submit();
  expect(signIn.attemptSecondFactor).toHaveBeenCalledWith({ strategy: "email_code", code: "123456" });
  expect(setActive).toHaveBeenCalledWith({ session: "trusted" });
});

test("a reset that requires another factor must complete that factor before choosing a password", async () => {
  signIn.create.mockResolvedValue({});
  signIn.attemptFirstFactor.mockResolvedValue({ status: "needs_second_factor", supportedSecondFactors: [{ strategy: "totp" }] });
  signIn.attemptSecondFactor.mockResolvedValue({ status: "needs_new_password" });
  await click(field("forgot-password"));
  await type("sign-in-email", "member@example.com");
  await submit();
  await type("sign-in-code", "123456");
  await submit();
  expect(host.textContent).toContain("Enter your authenticator code");
  expect(field("reset-password")).toBeNull();
  expect(setActive).not.toHaveBeenCalled();
  await type("sign-in-code", "010101");
  await submit();
  expect(signIn.attemptSecondFactor).toHaveBeenCalledWith({ strategy: "totp", code: "010101" });
  expect(field("reset-password")).toBeTruthy();
  expect(setActive).not.toHaveBeenCalled();
});


test("a silent Google initiation makes a new attempt when the user retries", async () => {
  jest.useFakeTimers();
  try {
    const google = field("auth-google-button");
    await click(google);
    expect(googleSignIn.sso).toHaveBeenCalledWith({ strategy: "oauth_google", redirectUrl: googleReturn, redirectCallbackUrl: googleReturn });
    signIn.id = "sia_earlier_attempt";
    expect(google.disabled).toBe(true);
    expect(google.getAttribute("aria-busy")).toBe("true");
    await act(async () => jest.advanceTimersByTime(6000));
    expect(google.disabled).toBe(false);
    expect(field("sign-in-error").textContent).toMatch(/Google didn’t open/);
    await click(google);
    expect(googleSignIn.sso).toHaveBeenCalledTimes(1);
    expect(signIn.authenticateWithRedirect).toHaveBeenCalledWith(freshGoogleRedirect);
  } finally {
    jest.useRealTimers();
  }
});

test("a sign-in attempt restored after a reload is replaced on the first explicit click", async () => {
  googleSignIn.id = "sia_expired";
  googleSignIn.status = "needs_first_factor";
  await act(async () => root.render(<SignInPage key="reloaded" />));
  await click(field("auth-google-button"));
  expect(signIn.authenticateWithRedirect).toHaveBeenCalledWith(freshGoogleRedirect);
  expect(googleSignIn.sso).not.toHaveBeenCalled();
});

test("Start here keeps the normal Google sign-up shortcut for a new visitor", async () => {
  await act(async () => root.render(<SignUpPage />));
  await click(field("auth-google-button"));
  expect(googleSignUp.sso).toHaveBeenCalledWith({ strategy: "oauth_google", redirectUrl: googleReturn, redirectCallbackUrl: googleReturn });
  expect(signUp.authenticateWithRedirect).not.toHaveBeenCalled();
});

test("a stale sign-up starts a new handoff and a provider error permits another click", async () => {
  signUp.id = "sua_expired";
  signUp.authenticateWithRedirect.mockRejectedValueOnce(new Error("Google is unavailable"));
  await act(async () => root.render(<SignUpPage />));
  await click(field("auth-google-button"));
  expect(field("sign-up-error").textContent).toBe("Google is unavailable");
  await click(field("auth-google-button"));
  expect(signUp.authenticateWithRedirect).toHaveBeenCalledTimes(2);
  expect(signUp.authenticateWithRedirect).toHaveBeenCalledWith(freshGoogleRedirect);
  expect(googleSignUp.sso).not.toHaveBeenCalled();
  expect(field("sign-up-error")).toBeNull();
});

test("leaving for Google cancels the interruption notice and permits signing in after browser Back", async () => {
  jest.useFakeTimers();
  try {
    await click(field("auth-google-button"));
    await act(async () => window.dispatchEvent(new Event("pagehide")));
    await act(async () => jest.advanceTimersByTime(6000));
    expect(field("sign-in-error")).toBeNull();
    expect(field("auth-google-button").disabled).toBe(false);
  } finally {
    jest.useRealTimers();
  }
});
