import { useEffect, useState } from "react";
import { HandleSSOCallback, SignIn, SignUp, useAuth, useClerk, useSignIn, useSignUp } from "@clerk/react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { pendingCouplesInvite } from "@/lib/couplesInvite";

export const PERSONAL_START = "/app/start";
export const MEMBER_HOME = "/dating";
const memberDestination = () => pendingCouplesInvite() ? "/app/couples/join" : MEMBER_HOME;
export const GOOGLE_SSO = {
  strategy: "oauth_google",
  redirectUrl: PERSONAL_START + "?google_return=1",
  redirectCallbackUrl: PERSONAL_START + "?google_return=1",
};

// Older callbacks may carry Clerk's intent or return parameters from before the app used Start.
export function LegacyGoogleReturn() {
  const { search, hash } = useLocation();
  const params = new URLSearchParams(search);
  params.set("google_return", "1");
  return <Navigate to={{ pathname: PERSONAL_START, search: "?" + params.toString(), hash }} replace />;
}

const signInSteps = {
  needs_first_factor: "/sign-in/factor-one",
  needs_second_factor: "/sign-in/factor-two",
  needs_client_trust: "/sign-in/client-trust",
  needs_new_password: "/sign-in/reset-password",
  needs_protect_check: "/sign-in/protect-check",
};

function signUpStep(attempt) {
  if (attempt?.status === "needs_protect_check" || attempt?.protectCheck) return "/sign-up/protect-check";
  if (!attempt?.missingFields?.length) {
    if (attempt?.unverifiedFields?.includes("email_address")) return "/sign-up/verify-email-address";
    if (attempt?.unverifiedFields?.includes("phone_number")) return "/sign-up/verify-phone-number";
  }
  return "/sign-up/continue";
}

// Clerk owns each verification step and reuses the existing attempt, including its verified Google identity.
export function ProviderAuthContinuation({ signUp = false }) {
  const { pathname, state } = useLocation();
  if (!signUp && pathname.replace(/\/$/, "") === "/sign-in/google-retry") {
    const messages = {
      expired: "This Google sign-in expired. Start a new sign-in to continue.",
      cancelled: "Google sign-in was canceled. You can start again when you’re ready.",
    };
    return <main className="mx-auto max-w-md px-5 py-12">
      <h1 className="font-display text-3xl">Google sign-in didn’t finish</h1>
      <p className="mt-3">{messages[state?.reason] || "Start a new sign-in to continue."}</p>
      <Link className="btn-lime mt-6 inline-flex" to="/sign-in" replace>Return to sign in</Link>
    </main>;
  }
  const Component = signUp ? SignUp : SignIn;
  return <main className="flex min-h-[65vh] justify-center px-4 py-12">
    <Component routing="path" path={signUp ? "/sign-up" : "/sign-in"} signInUrl="/sign-in" signUpUrl="/sign-up"
      forceRedirectUrl={memberDestination()} />
  </main>;
}

function CallbackStatus() {
  const [delayed, setDelayed] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setDelayed(true), 8000);
    return () => window.clearTimeout(timer);
  }, []);
  return <div className="p-6 text-center" role="status">
    {delayed ? <>If Google sign-in expired or was canceled, start again. <Link className="underline" to="/sign-in">Return to sign in</Link></> : "Finishing Google sign-in…"}
  </div>;
}

function StartSession({ children }) {
  const { isLoaded, isSignedIn } = useAuth();
  const clerk = useClerk();
  const { signIn } = useSignIn();
  const { signUp } = useSignUp();
  const { search } = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(search);
  const queryError = params.get("error");
  const isReturn = params.get("google_return") === "1" || ["signIn", "signUp"].includes(params.get("intent")) || Boolean(queryError);

  if (!isLoaded) return null;
  if (clerk.session?.currentTask) return <Navigate to="/sign-in/tasks" replace />;
  if (isSignedIn) return isReturn ? <Navigate to={memberDestination()} replace /> : children;

  const finalize = [signIn, signUp].some(flow => flow?.status === "complete" || flow?.isTransferable || flow?.existingSession || flow?.protectCheck);
  if (!finalize) {
    // Clerk keeps OAuth verification errors on the original attempt. A transferable attempt can
    // also carry an error and must complete through HandleSSOCallback before interpreting it.
    const verifications = [clerk.client?.signIn?.firstFactorVerification, clerk.client?.signUp?.verifications?.externalAccount];
    const failed = verifications.find(v => v?.strategy === "oauth_google" && (v.error || v.status === "expired" || v.status === "failed"));
    if (isReturn && (queryError || failed)) {
      const code = queryError || failed?.error?.code || failed?.status;
      const reason = /expir/i.test(code) ? "expired" : /denied|cancel/i.test(code) ? "cancelled" : "incomplete";
      return <Navigate to="/sign-in/google-retry" state={{ reason }} replace />;
    }
    if (signInSteps[signIn?.status]) return <Navigate to={signInSteps[signIn.status]} replace />;
    if (signUp?.id && signUp.status === "missing_requirements") return <Navigate to={signUpStep(signUp)} replace />;
    if (!isReturn) return <Navigate to="/sign-in" replace />;
  }

  return <>
    <HandleSSOCallback
      navigateToApp={({ session, decorateUrl }) => {
        const destination = decorateUrl(session?.currentTask ? "/sign-in/tasks" : memberDestination());
        if (/^https?:\/\//i.test(destination)) window.location.replace(destination);
        else navigate(destination, { replace: true });
      }}
      navigateToSignIn={() => navigate(signInSteps[clerk.client?.signIn?.status] || signInSteps[signIn?.status] || "/sign-in", { replace: true })}
      navigateToSignUp={() => navigate(signUpStep(clerk.client?.signUp || signUp), { replace: true })}
    />
    <CallbackStatus />
  </>;
}

export function PersonalEntry({ children }) {
  const { pathname } = useLocation();
  return pathname.replace(/\/$/, "") === PERSONAL_START ? <StartSession>{children}</StartSession> : children;
}
