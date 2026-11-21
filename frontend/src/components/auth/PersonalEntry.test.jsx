import { act } from "react";
import { createRoot } from "react-dom/client";
import { HandleSSOCallback, SignIn, SignUp, useAuth, useClerk, useSignIn, useSignUp } from "@clerk/react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { GOOGLE_SSO, LegacyGoogleReturn, PersonalEntry, ProviderAuthContinuation } from "./PersonalEntry";

jest.mock("@clerk/react", () => ({
  useAuth: jest.fn(), useClerk: jest.fn(), SignIn: jest.fn(() => null), SignUp: jest.fn(() => null), useSignIn: jest.fn(), useSignUp: jest.fn(), HandleSSOCallback: jest.fn(() => null),
}));
jest.mock("react-router-dom", () => ({
  Navigate: jest.fn(() => null), Link: jest.fn(() => null), useLocation: jest.fn(), useNavigate: jest.fn(),
}));

let root, element, navigate;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  element = document.createElement("div");
  root = createRoot(element);
  navigate = jest.fn();
  useLocation.mockReturnValue({ pathname: "/app/start" });
  useNavigate.mockReturnValue(navigate);
  useClerk.mockReturnValue({ client: {}, session: null });
  useAuth.mockReturnValue({ isLoaded: true, isSignedIn: false });
  useSignIn.mockReturnValue({ signIn: { status: "needs_identifier", id: undefined } });
  useSignUp.mockReturnValue({ signUp: { status: "missing_requirements", id: undefined } });
});
afterEach(() => { act(() => root.unmount()); });

const showStart = () => act(() => root.render(<PersonalEntry><p>Personal start</p></PersonalEntry>));

test("Google keeps its existing callback while direct legacy start remains available", () => {
  expect(GOOGLE_SSO).toEqual({ strategy: "oauth_google", redirectCallbackUrl: "/app/start?google_return=1", redirectUrl: "/app/start?google_return=1" });
  useAuth.mockReturnValue({ isLoaded: true, isSignedIn: true });
  showStart();
  expect(element.textContent).toBe("Personal start");
  expect(HandleSSOCallback).not.toHaveBeenCalled();
});

test.each(["complete", "transfer", "existing"])("finishes a %s Google return before the app guard redirects", (flow) => {
  const signUp = flow === "complete" ? { status: "complete" }
    : flow === "transfer" ? { isTransferable: true } : { existingSession: { sessionId: "fixture" } };
  useSignUp.mockReturnValue({ signUp });
  showStart();
  expect(Navigate).not.toHaveBeenCalled();
  expect(element.textContent).toContain("Finishing Google sign-in");
  const callback = HandleSSOCallback.mock.calls[0][0];
  callback.navigateToApp({ decorateUrl: path => path });
  expect(navigate).toHaveBeenCalledWith("/dating", { replace: true });
  useAuth.mockReturnValue({ isLoaded: true, isSignedIn: true });
  showStart();
  expect(element.textContent).toBe("Personal start");
});

test("a direct signed-out visit still prompts sign-in", () => {
  showStart();
  expect(Navigate.mock.calls[0][0]).toMatchObject({ to: "/sign-in", replace: true });
});

test("continues a real incomplete signup when Clerk has allocated an attempt", () => {
  useSignUp.mockReturnValue({ signUp: { id: "sua_fixture", status: "missing_requirements" } });
  showStart();
  expect(Navigate.mock.calls[0][0]).toMatchObject({ to: "/sign-up/continue", replace: true });
});

test("an old callback replaces the history entry and preserves Clerk's return data", () => {
  useLocation.mockReturnValue({ pathname: "/sso-callback", search: "?intent=signUp", hash: "#provider-return" });
  act(() => root.render(<LegacyGoogleReturn />));
  expect(Navigate.mock.calls[0][0]).toMatchObject({
    to: { pathname: "/app/start", search: "?intent=signUp&google_return=1", hash: "#provider-return" }, replace: true,
  });
});

test("a marked return without a hydrated attempt still mounts Clerk, then removes the marker on sign-in", () => {
  useLocation.mockReturnValue({ pathname: "/app/start", search: "?google_return=1" });
  showStart();
  expect(HandleSSOCallback).toHaveBeenCalled();
  expect(Navigate).not.toHaveBeenCalled();
  useAuth.mockReturnValue({ isLoaded: true, isSignedIn: true });
  showStart();
  expect(Navigate.mock.calls[0][0]).toMatchObject({ to: "/dating", replace: true });
});

test.each([
  ["needs_first_factor", "/sign-in/factor-one"],
  ["needs_client_trust", "/sign-in/client-trust"],
  ["needs_second_factor", "/sign-in/factor-two"],
  ["needs_new_password", "/sign-in/reset-password"],
])("resumes %s on its native verification step", (status, to) => {
  useSignIn.mockReturnValue({ signIn: { id: "sia_fixture", status } });
  showStart();
  expect(Navigate.mock.calls[0][0]).toMatchObject({ to, replace: true });
});

test("a signup-to-signin transfer continues the live second factor returned by Clerk", () => {
  useSignUp.mockReturnValue({ signUp: { isTransferable: true } });
  const clerk = { client: { signIn: { status: "needs_identifier" } } };
  useClerk.mockReturnValue(clerk);
  showStart();
  clerk.client.signIn.status = "needs_client_trust";
  HandleSSOCallback.mock.calls[0][0].navigateToSignIn();
  expect(navigate).toHaveBeenCalledWith("/sign-in/client-trust", { replace: true });
});

test.each([
  [["legal_accepted"], [], "/sign-up/continue"],
  [[], ["email_address"], "/sign-up/verify-email-address"],
  [[], ["phone_number"], "/sign-up/verify-phone-number"],
])("continues Clerk's specific incomplete signup", (missingFields, unverifiedFields, to) => {
  useSignUp.mockReturnValue({ signUp: { id: "sua_fixture", status: "missing_requirements", missingFields, unverifiedFields } });
  showStart();
  expect(Navigate.mock.calls[0][0]).toMatchObject({ to, replace: true });
});

test("an active session task is completed before entering the application", () => {
  useClerk.mockReturnValue({ session: { currentTask: { key: "setup-mfa" } } });
  showStart();
  expect(Navigate.mock.calls[0][0]).toMatchObject({ to: "/sign-in/tasks", replace: true });
});

test("finalize routes pending tasks through Clerk", () => {
  useSignIn.mockReturnValue({ signIn: { status: "complete" } });
  showStart();
  HandleSSOCallback.mock.calls[0][0].navigateToApp({ session: { currentTask: { key: "reset-password" } }, decorateUrl: path => path });
  expect(navigate).toHaveBeenCalledWith("/sign-in/tasks", { replace: true });
});

test.each([[false, "/sign-in"], [true, "/sign-up"]])("the provider continuation uses Clerk path routing", (signUp, path) => {
  act(() => root.render(<ProviderAuthContinuation signUp={signUp} />));
  expect((signUp ? SignUp : SignIn).mock.calls[0][0]).toMatchObject({ routing: "path", path, forceRedirectUrl: "/dating" });
});


test.each([["access_denied", "cancelled"], ["state_token_expired", "expired"]])("handles a provider %s without forwarding raw return data", (error, reason) => {
  useLocation.mockReturnValue({ pathname: "/app/start", search: "?google_return=1&error=" + error + "&state=fixture" });
  showStart();
  expect(Navigate.mock.calls[0][0]).toMatchObject({ to: "/sign-in/google-retry", state: { reason }, replace: true });
  expect(JSON.stringify(Navigate.mock.calls[0][0])).not.toContain("fixture");
});

test("a Clerk verification failure also reaches a friendly recovery screen", () => {
  useLocation.mockReturnValue({ pathname: "/app/start", search: "?intent=signUp" });
  useClerk.mockReturnValue({ client: { signUp: { verifications: { externalAccount: { strategy: "oauth_google", status: "failed", error: { code: "oauth_state_expired" } } } } } });
  showStart();
  expect(Navigate.mock.calls[0][0]).toMatchObject({ to: "/sign-in/google-retry", state: { reason: "expired" } });
  useLocation.mockReturnValue({ pathname: "/sign-in/google-retry", state: { reason: "expired" } });
  act(() => root.render(<ProviderAuthContinuation />));
  expect(element.textContent).toContain("This Google sign-in expired");
  expect(SignIn).not.toHaveBeenCalled();
});
