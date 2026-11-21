import { act } from "react";
import { createRoot } from "react-dom/client";
import { useAuth, useSignUp } from "@clerk/react";
import { useSignUp as useLegacySignUp } from "@clerk/react/legacy";
import { useNavigate } from "react-router-dom";
import { SignUpPage } from "./AuthPages";
import { ThemeProvider, ThemeToggle } from "@/lib/theme";
import { hasBrowserSession, rememberPreference } from "@/lib/browserSession";
import { readDemoSubject, storeDemoSubject } from "@/lib/startDraft";

jest.mock("@clerk/react", () => ({ useAuth: jest.fn(), useSignUp: jest.fn() }));
jest.mock("@clerk/react/legacy", () => ({ useSignUp: jest.fn() }), { virtual: true });
jest.mock("react-router-dom", () => ({
  Link: ({ children, to }) => <a href={to}>{children}</a>,
  useNavigate: jest.fn(),
}));

let host, root, resource, signUp, navigate;
const ok = { error: null };
const field = id => host.querySelector(`[data-testid="${id}"]`);
const render = () => act(async () => root.render(<SignUpPage />));
const click = button => act(async () => button.click());
const submit = id => act(async () => {
  field(id).dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
});
const type = (id, value) => act(async () => {
  const input = field(id);
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
});
const resend = () => click([...host.querySelectorAll("button")].find(b => b.textContent === "Resend code"));

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  localStorage.clear();
  sessionStorage.clear();
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  resource = {
    id: "signup_attempt", requiredFields: ["password", "email_address"], missingFields: [],
    status: "missing_requirements", emailAddress: "member@example.com",
    unverifiedFields: ["email_address"], verifications: { emailAddress: { strategy: "email_code" } },
  };
  signUp = {
    password: jest.fn().mockResolvedValue(ok),
    verifications: { sendEmailCode: jest.fn().mockResolvedValue(ok), verifyEmailCode: jest.fn().mockResolvedValue(ok) },
    reset: jest.fn().mockImplementation(async () => { resource.id = null; return ok; }),
    finalize: jest.fn().mockImplementation(async ({ navigate }) => { navigate({ decorateUrl: path => path }); return ok; }),
  };
  navigate = jest.fn();
  useNavigate.mockReturnValue(navigate);
  useSignUp.mockReturnValue({ signUp });
  useLegacySignUp.mockReturnValue({ signUp: resource });
  useAuth.mockReturnValue({ isLoaded: true });
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  jest.restoreAllMocks();
  localStorage.clear();
  document.cookie = "imladri_browser_session=; Max-Age=0; Path=/";
  document.cookie = "imaldri_remember=; Max-Age=0; Path=/";
  document.documentElement.classList.remove("dark");
});

test.each([true, false])("sign-up handles denied web storage when cookies are available: %s", async cookies => {
  resource.id = null;
  for (const operation of ["getItem", "setItem", "removeItem"]) {
    jest.spyOn(Storage.prototype, operation).mockImplementation(() => { throw new DOMException("Storage blocked", "SecurityError"); });
  }
  if (!cookies) {
    jest.spyOn(document, "cookie", "get").mockReturnValue("");
    jest.spyOn(document, "cookie", "set").mockImplementation(() => {});
  }
  await act(async () => root.render(<ThemeProvider><ThemeToggle /><SignUpPage /></ThemeProvider>));
  await click(field("theme-toggle"));
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  storeDemoSubject("her");
  expect(readDemoSubject()).toBe("");
  storeDemoSubject("");
  await type("sign-up-email", "member@example.com");
  await type("sign-up-password", "example-password");
  await type("sign-up-confirm", "example-password");
  await click(field("sign-up-remember"));
  await submit("sign-up-page");
  if (cookies) {
    expect(signUp.password).toHaveBeenCalledTimes(1);
    expect(signUp.verifications.sendEmailCode).toHaveBeenCalledTimes(1);
    expect(rememberPreference()).toBe(false);
    expect(hasBrowserSession()).toBe(true);
    expect(field("sign-up-code")).toBeTruthy();
  } else {
    expect(signUp.password).not.toHaveBeenCalled();
    expect(field("sign-up-error").textContent).toMatch(/Allow site data/);
    expect(field("sign-up-email").value).toBe("member@example.com");
  }
});

test("reload resumes the same code attempt and handles rejected and expired codes, resend and completion", async () => {
  localStorage.setItem("imaldri_remember", "0");
  await render();
  const code = field("sign-up-code");
  expect(code.labels[0].textContent).toBe("Email verification code");
  expect(code.autocomplete).toBe("one-time-code");
  expect(field("sign-up-verify").textContent).toContain(resource.emailAddress);
  expect(signUp.password).not.toHaveBeenCalled();
  expect(signUp.verifications.sendEmailCode).not.toHaveBeenCalled();
  for (const message of ["Incorrect code", "Expired code"]) {
    signUp.verifications.verifyEmailCode.mockResolvedValueOnce({ error: { message } });
    await type("sign-up-code", "000000");
    await submit("sign-up-verify");
    expect(host.querySelector('[role="alert"]').textContent).toBe(message);
    expect(signUp.finalize).not.toHaveBeenCalled();
  }
  signUp.verifications.sendEmailCode.mockResolvedValueOnce({ error: { message: "Please wait before resending." } });
  await resend();
  expect(field("sign-up-error").textContent).toBe("Please wait before resending.");
  await resend();
  expect(field("sign-up-code").value).toBe("");
  expect(field("sign-up-error")).toBeNull();
  expect(host.querySelector('[role="status"]').textContent).toContain("A new verification code has been sent");
  await type("sign-up-code", "123456");
  signUp.status = "complete";
  await submit("sign-up-verify");
  expect(signUp.verifications.verifyEmailCode).toHaveBeenLastCalledWith({ code: "123456" });
  expect(navigate).toHaveBeenCalledWith("/app/start", { replace: true });
  expect(localStorage.getItem("imaldri_remember")).toBe("0");
});

test("a failed initial send stays recoverable and remembers an unchecked preference before completion", async () => {
  resource.id = null;
  signUp.verifications.sendEmailCode.mockRejectedValueOnce(new Error("Could not send the code"));
  await render();
  await type("sign-up-email", "member@example.com");
  await type("sign-up-password", "example-password");
  await type("sign-up-confirm", "example-password");
  await click(field("sign-up-remember"));
  await submit("sign-up-page");
  expect(field("sign-up-error").textContent).toBe("Could not send the code");
  expect(localStorage.getItem("imaldri_remember")).toBe("0");
  expect(Object.values(localStorage)).not.toContain("example-password");
  await resend();
  expect(signUp.password).toHaveBeenCalledTimes(1);
  expect(signUp.verifications.sendEmailCode).toHaveBeenCalledTimes(2);
  expect(field("sign-up-error")).toBeNull();
});

test("changing address resets the existing attempt and clears the verification form", async () => {
  await render();
  await type("sign-up-code", "123456");
  await click(field("sign-up-back"));
  expect(signUp.reset).toHaveBeenCalledTimes(1);
  expect(field("sign-up-code")).toBeNull();
  expect(field("sign-up-email").value).toBe("");
  expect(field("sign-up-password").value).toBe("");
  expect(signUp.verifications.sendEmailCode).not.toHaveBeenCalled();
  // The provider may rehydrate its previous attempt even after a local reset.
  resource.id = "signup_attempt";
  act(() => root.unmount());
  root = createRoot(host);
  await render();
  expect(field("sign-up-email").value).toBe("");
  expect(field("sign-up-code")).toBeNull();
});

test("another unfinished account requirement continues after a successful email check", async () => {
  signUp.status = "missing_requirements";
  await render();
  await type("sign-up-code", "123456");
  await submit("sign-up-verify");
  expect(navigate).toHaveBeenCalledWith("/sign-up/continue", { replace: true });
  expect(signUp.finalize).not.toHaveBeenCalled();
});

test.each([
  { hasPassword: false },
  { id: null },
  { status: "complete" },
  { missingFields: ["password"] },
  { requiredFields: ["email_address"] },
  { externalAccount: { provider: "oauth_google" } },
  { unverifiedFields: [] },
  { verifications: { emailAddress: { strategy: "email_link" } } },
  { verifications: { emailAddress: { strategy: "email_code" }, externalAccount: { strategy: "oauth_google" } } },
])("does not adopt a different verification flow %#", async changes => {
  Object.assign(resource, changes);
  await render();
  expect(field("sign-up-page")).toBeTruthy();
  expect(field("sign-up-code")).toBeNull();
  expect(signUp.verifications.sendEmailCode).not.toHaveBeenCalled();
});
