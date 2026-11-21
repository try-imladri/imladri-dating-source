import { useEffect, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useSignIn, useSignUp as useLegacySignUp } from "@clerk/react/legacy";
import { useAuth, useSignIn as useGoogleSignIn, useSignUp } from "@clerk/react";
import { Eye, EyeOff } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Silhouette } from "@/components/public/Silhouette";
import { GOOGLE_SSO } from "@/components/auth/PersonalEntry";
import { rememberPreference, setRememberPreference } from "@/lib/browserSession";
import { pendingCouplesInvite } from "@/lib/couplesInvite";

const clerkErr = (e) => e?.errors?.[0]?.longMessage || e?.errors?.[0]?.message || e?.longMessage || e?.message || "Something went wrong, please try again";

async function checkClerkResult(operation) {
  const { error } = await operation;
  if (error) throw error;
}

function startGoogle(attempt, resource) {
  if (!resource.id && !attempt.id) return checkClerkResult(attempt.sso(GOOGLE_SSO));
  // An explicit click starts over. Clerk's sso() can reuse an old attempt with no redirect.
  return resource.authenticateWithRedirect({
    strategy: GOOGLE_SSO.strategy,
    redirectUrl: GOOGLE_SSO.redirectCallbackUrl,
    redirectUrlComplete: new URL(GOOGLE_SSO.redirectUrl, window.location.origin).href,
  });
}

function finishAuth(navigate, decorateUrl, path) {
  const url = decorateUrl(path);
  if (url.startsWith("http")) window.location.assign(url);
  else navigate(url, { replace: true });
}

export const setRemember = setRememberPreference;
const dismissedKey = "imladri_dismissed_signup";

function Frame({ title, sub, children, art }) {
  return (
    <div className="h-[calc(100dvh-3.5rem)] overflow-y-auto px-5 sm:px-8 lg:px-12 grid lg:grid-cols-2 gap-10 items-center max-w-6xl mx-auto">
      {art ? (
        <div className="hidden lg:flex flex-col items-center justify-center" data-testid="auth-art">
          <div className="couple-enter"><Silhouette src={art} className="h-[min(34rem,66vh)] w-auto" testid="auth-silhouette" /></div>
        </div>
      ) : (
      <div className="hidden lg:block">
        <Link to="/" className="font-display font-black text-3xl uppercase tracking-tight" data-testid="auth-logo">Imladri.</Link>
        <p className="font-display font-black uppercase text-7xl xl:text-8xl leading-[.85] mt-6">{title}</p>
        <p className="text-[var(--muted)] text-lg mt-5 max-w-md">{sub}</p>
      </div>
      )}
      <div className="w-full max-w-md mx-auto py-6">
        {art && <div className="lg:hidden flex justify-center mb-5 couple-enter"><Silhouette src={art} className="h-40 sm:h-52 w-auto" testid="auth-silhouette-mobile" /></div>}
        {children}
      </div>
    </div>
  );
}

function GoogleButton({ onClick, disabled, onError }) {
  const [phase, setPhase] = useState("idle");
  useEffect(() => {
    if (phase !== "opening") return;
    // A resolved provider request can still leave the browser on this page.
    const timer = window.setTimeout(() => {
      setPhase("idle");
      onError("Google didn’t open. Please try again.");
    }, 6000);
    const leaving = () => {
      window.clearTimeout(timer);
      setPhase("idle");
    };
    window.addEventListener("pagehide", leaving);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("pagehide", leaving);
    };
  }, [phase, onError]);
  const start = async () => {
    setPhase("requesting");
    onError("");
    try {
      await onClick();
      setPhase("opening");
    } catch (error) {
      setPhase("idle");
      onError(clerkErr(error));
    }
  };
  return (
    <button type="button" disabled={disabled || phase !== "idle"} aria-busy={phase !== "idle"} onClick={start} className="w-full h-11 rounded-full border border-[var(--line)] flex items-center justify-center gap-3 text-sm font-semibold hover:border-[var(--ink)] transition-colors disabled:opacity-50" data-testid="auth-google-button">
      <svg width="18" height="18" viewBox="0 0 48 48"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" /><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" /><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" /><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.3-2.2 4.3-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" /></svg>
      {phase === "idle" ? "Continue with Google" : "Opening Google…"}
    </button>
  );
}

function Password({ id, value, onChange, testid, placeholder, autoComplete }) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative mt-1.5">
      <Input id={id} type={show ? "text" : "password"} autoComplete={autoComplete} required value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className="h-11 pr-10" data-testid={testid} />
      <button type="button" onClick={() => setShow(!show)} className="absolute right-1 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-full text-[var(--muted)]" aria-label={show ? "Hide password" : "Show password"}>{show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}</button>
    </div>
  );
}

const Divider = () => <div className="flex items-center gap-3 my-5 text-xs text-[var(--muted)]"><div className="h-px flex-1 bg-[var(--line)]" />or<div className="h-px flex-1 bg-[var(--line)]" /></div>;

function Consent({ action }) {
  return (
    <p className="text-xs text-[var(--muted)] mt-4 text-center" data-testid="auth-consent">
      By {action}, you agree to our <Link to="/terms" className="underline text-[var(--ink)]">Terms</Link> and <Link to="/privacy" className="underline text-[var(--ink)]">Privacy Policy</Link>.
    </p>
  );
}

const SUB = "Bring a conversation, explore what it could mean, and decide what to do next.";

const SECOND_FACTOR_LABELS = {
  email_code: "Enter the code we emailed you",
  phone_code: "Enter the code we texted you",
  totp: "Enter your authenticator code",
  backup_code: "Enter your backup code",
};

const RESET_STRATEGY = "reset_password_email_code";
const CODE_HINT = "If this address can reset a password, check your email for a 6-digit code.";

export function AuthFormRoute({ children, signedInPath }) {
  const { isLoaded, isSignedIn } = useAuth();
  const { search, hash } = useLocation();
  const hasTicket = [search, hash].some(part => new URLSearchParams(part.slice(1)).has("__clerk_ticket"));
  if (isLoaded && isSignedIn && !hasTicket) return <Navigate to={signedInPath} replace />;
  return children;
}

export function SignInPage() {
  const { isLoaded, signIn, setActive } = useSignIn();
  const { signIn: googleSignIn } = useGoogleSignIn();
  const nav = useNavigate();
  const [f, setF] = useState(() => ({ email: "", password: "", remember: rememberPreference() }));
  const [step, setStep] = useState("credentials");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [factor, setFactor] = useState("");
  const [resetAvailable, setResetAvailable] = useState(false);
  const [notice, setNotice] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const changeFlow = (next) => {
    setStep(next);
    setErr("");
    setNotice("");
    setCode("");
    setNewPassword("");
    setConfirmation("");
    setResetAvailable(false);
    setF(current => ({ ...current, password: "" }));
  };
  const finish = async (r) => {
    setRemember(f.remember);
    await setActive({ session: r.createdSessionId });
    nav(pendingCouplesInvite() ? "/app/couples/join" : "/dating");
  };
  useEffect(() => {
    const m = (window.location.hash + window.location.search).match(/__clerk_ticket=([^&]+)/);
    if (!isLoaded || !m) return;
    signIn.create({ strategy: "ticket", ticket: decodeURIComponent(m[1]) }).then((r) => r.status === "complete" && finish(r)).catch((e2) => setErr(clerkErr(e2)));
  }, [isLoaded]); // eslint-disable-line react-hooks/exhaustive-deps

  const advance = async (result) => {
    if (result.status === "complete") return finish(result);
    if (result.status === "needs_new_password") {
      setStep("new-password");
      return;
    }
    if (["needs_second_factor", "needs_client_trust"].includes(result.status)) {
      const next = result.supportedSecondFactors?.find(item => SECOND_FACTOR_LABELS[item.strategy]);
      if (!next) throw new Error("Your account requires a different verification method. Contact support for help signing in.");
      if (next.strategy === "email_code") await signIn.prepareSecondFactor({ strategy: next.strategy, emailAddressId: next.emailAddressId });
      if (next.strategy === "phone_code") await signIn.prepareSecondFactor({ strategy: next.strategy, phoneNumberId: next.phoneNumberId });
      setFactor(next.strategy);
      setCode("");
      setStep("second-factor");
      return;
    }
    throw new Error("Sign-in could not be completed. Please start again.");
  };

  const sendReset = async () => {
    try {
      await signIn.create({ identifier: f.email.trim(), strategy: RESET_STRATEGY });
      setResetAvailable(true);
    } catch (error) {
      if (!error?.errors?.some(item => item.code === "form_identifier_not_found")) throw error;
      setResetAvailable(false);
    }
    setCode("");
    setStep("reset-code");
    setNotice(CODE_HINT);
  };

  const run = async (action) => {
    if (!isLoaded || busy) return;
    setBusy(true);
    setErr("");
    try {
      await action();
    } catch (error) {
      setErr(clerkErr(error));
    } finally {
      setBusy(false);
    }
  };
  const submit = (event) => {
    event.preventDefault();
    run(async () => {
      switch (step) {
        case "reset-email":
          return sendReset();
        case "reset-code":
          if (!resetAvailable) throw new Error("That code is incorrect or expired. Check your email and try again.");
          return advance(await signIn.attemptFirstFactor({ strategy: RESET_STRATEGY, code: code.trim() }));
        case "new-password":
          if (newPassword !== confirmation) throw new Error("Passwords don't match");
          return advance(await signIn.resetPassword({ password: newPassword, signOutOfOtherSessions: true }));
        case "second-factor":
          return advance(await signIn.attemptSecondFactor({ strategy: factor, code: code.trim() }));
        default:
          return advance(await signIn.create({ identifier: f.email.trim(), password: f.password }));
      }
    });
  };
  const google = () => {
    setRemember(f.remember);
    return startGoogle(googleSignIn, signIn);
  };
  const signingIn = step === "credentials";
  const titles = { credentials: "Sign in", "reset-email": "Reset password", "reset-code": "Check your email", "new-password": "New password", "second-factor": "Verify sign-in" };
  const actions = { credentials: "Sign in", "reset-email": "Send reset code", "reset-code": "Verify code", "new-password": "Save new password", "second-factor": "Verify & sign in" };
  return (
    <Frame title="Welcome back." sub={SUB} art="/img/couple-reunion.webp">
      <form onSubmit={submit} className="card p-6 sm:p-8" data-testid="sign-in-page">
        <h1 className="font-display font-black uppercase text-4xl text-center">{titles[step]}</h1>
        {signingIn && <>
          <div className="mt-5"><GoogleButton onClick={google} disabled={!isLoaded || busy} onError={setErr} /></div>
          <Divider />
        </>}
        {(signingIn || step === "reset-email") && <div className={signingIn ? "" : "mt-5"}>
          <Label htmlFor="si-email">Email</Label>
          <Input id="si-email" type="email" autoComplete="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} className="h-11 mt-1.5" data-testid="sign-in-email" />
        </div>}
        {signingIn && <>
          <div className="mt-4">
            <div className="flex items-baseline justify-between gap-2">
              <Label htmlFor="si-pass">Password</Label>
              <button type="button" onClick={() => changeFlow("reset-email")} disabled={busy} className="text-sm underline" data-testid="forgot-password">Forgot password?</button>
            </div>
            <Password id="si-pass" autoComplete="current-password" value={f.password} onChange={(v) => setF({ ...f, password: v })} testid="sign-in-password" />
          </div>
          <label className="flex items-center gap-2 mt-4 text-sm cursor-pointer"><Checkbox checked={f.remember} onCheckedChange={(v) => setF({ ...f, remember: !!v })} data-testid="sign-in-remember" />Remember me</label>
        </>}
        {step === "reset-code" && <p role="status" className="mt-3 text-center text-sm text-[var(--muted)]">{notice}</p>}
        {(step === "reset-code" || step === "second-factor") && (
          <div className="mt-4" data-testid="sign-in-code-step">
            <Label htmlFor="si-code">{step === "reset-code" ? "Password reset code" : SECOND_FACTOR_LABELS[factor]}</Label>
            <Input id="si-code" required autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} inputMode={step === "second-factor" && factor === "backup_code" ? "text" : "numeric"} className="h-11 mt-1.5 tracking-[.4em] text-center font-mono" data-testid="sign-in-code" />
          </div>
        )}
        {step === "new-password" && <>
          <div className="mt-5"><Label htmlFor="reset-password">New password</Label><Password id="reset-password" value={newPassword} autoComplete="new-password" onChange={setNewPassword} testid="reset-password" /></div>
          <div className="mt-4"><Label htmlFor="reset-confirm">Confirm new password</Label><Password id="reset-confirm" value={confirmation} autoComplete="new-password" onChange={setConfirmation} testid="reset-confirm" /></div>
          <p className="mt-3 text-sm text-[var(--muted)]">Saving your password signs you out on other devices.</p>
        </>}
        {err && <p role="alert" className="text-sm text-[var(--crimson)] mt-3" data-testid="sign-in-error">{err}</p>}
        <button disabled={!isLoaded || busy} className="btn-lime w-full h-11 mt-5" data-testid="sign-in-submit">{busy ? "Please wait…" : actions[step]}</button>
        {signingIn ? <>
          <Consent action="signing in" />
          <p className="text-sm text-center mt-4">New here? <Link to="/sign-up" className="font-semibold underline" data-testid="sign-in-to-sign-up">Create an account</Link></p>
        </> : <div className="flex flex-wrap justify-center gap-x-5 gap-y-2 mt-4 text-sm">
          {step === "reset-code" && <button type="button" className="underline" disabled={busy} onClick={() => run(sendReset)}>Resend code</button>}
          <button type="button" className="underline" disabled={busy} onClick={() => changeFlow("credentials")}>Back to sign in</button>
        </div>}
      </form>
    </Frame>
  );
}

export function SignUpPage() {
  const { isLoaded } = useAuth();
  const { signUp, errors } = useSignUp();
  const { signUp: signUpResource } = useLegacySignUp();
  const nav = useNavigate();
  const [f, setF] = useState(() => ({
    email: "", password: "", confirm: "", remember: rememberPreference(),
  }));
  const [code, setCode] = useState("");
  const [submittedEmail, setSubmittedEmail] = useState("");
  const [notice, setNotice] = useState("");
  const [dismissedAttempt, setDismissedAttempt] = useState(() => {
    try { return sessionStorage.getItem(dismissedKey); } catch { return null; }
  });
  // Clerk can omit hasPassword on reload even after accepting the required password.
  const hasPassword = signUpResource?.hasPassword ?? (signUpResource?.requiredFields?.includes("password")
    && Array.isArray(signUpResource.missingFields) && !signUpResource.missingFields.includes("password"));
  const pendingEmailCode = signUpResource?.id && signUpResource.id !== dismissedAttempt && hasPassword
    && signUpResource.status === "missing_requirements" && signUpResource.unverifiedFields?.includes("email_address")
    && signUpResource.verifications?.emailAddress?.strategy === "email_code"
    && !signUpResource.externalAccount && !signUpResource.verifications?.externalAccount?.strategy;
  const verificationEmail = submittedEmail || (pendingEmailCode ? signUpResource.emailAddress : "");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (fn) => {
    setBusy(true);
    setErr("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setErr(clerkErr(e));
    }
    setBusy(false);
  };
  const submit = (e) => {
    e.preventDefault();
    if (f.password !== f.confirm) return setErr("Passwords don't match");
    if (!isLoaded) return;
    run(async () => {
      const email = f.email.trim();
      setRemember(f.remember);
      await checkClerkResult(signUp.password({ emailAddress: email, password: f.password }));
      try { sessionStorage.removeItem(dismissedKey); } catch { /* Component state remains usable. */ }
      setDismissedAttempt(null);
      setSubmittedEmail(email);
      setF(current => ({ ...current, password: "", confirm: "" }));
      await checkClerkResult(signUp.verifications.sendEmailCode());
    });
  };
  const verify = (e) => {
    e.preventDefault();
    run(async () => {
      await checkClerkResult(signUp.verifications.verifyEmailCode({ code }));
      if (signUp.status !== "complete") return nav("/sign-up/continue", { replace: true });
      setRemember(f.remember);
      await checkClerkResult(signUp.finalize({ navigate: ({ decorateUrl }) => finishAuth(nav, decorateUrl, pendingCouplesInvite() ? "/app/couples/join" : "/dating") }));
    });
  };
  const google = () => {
    setRemember(f.remember);
    return startGoogle(signUp, signUpResource);
  };
  const errorMessage = err || errors?.global?.[0]?.message;
  return (
    <Frame title="Start here." sub={SUB} art="/img/couple-dance.webp">
      {verificationEmail ? (
        <form onSubmit={verify} className="card p-6 sm:p-8" data-testid="sign-up-verify">
          <h1 className="font-display font-black uppercase text-4xl text-center">Check your email</h1>
          <p className="text-sm text-[var(--muted)] mt-2 text-center">Enter the 6-digit code from the email to {verificationEmail}.</p>
          <div className="mt-5">
            <Label htmlFor="su-code">Email verification code</Label>
            <Input id="su-code" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} required className="h-11 mt-1.5 tracking-[.4em] text-center font-mono" data-testid="sign-up-code" />
          </div>
          {notice && <p role="status" className="text-sm text-[var(--muted)] mt-3">{notice}</p>}
          {errorMessage && <p role="alert" className="text-sm text-[var(--crimson)] mt-3" data-testid="sign-up-error">{errorMessage}</p>}
          <button disabled={busy} className="btn-lime w-full h-11 mt-5" data-testid="sign-up-verify-submit">{busy ? "Checking…" : "Verify & continue"}</button>
          <button type="button" disabled={!isLoaded || busy} onClick={() => run(async () => {
            await checkClerkResult(signUp.verifications.sendEmailCode());
            setCode("");
            setNotice("A new verification code has been sent.");
          })} className="w-full text-sm mt-3 underline">Resend code</button>
          <button type="button" disabled={busy} onClick={() => run(async () => {
            const previousId = signUpResource?.id;
            await checkClerkResult(signUp.reset());
            if (previousId) {
              try { sessionStorage.setItem(dismissedKey, previousId); } catch { /* Keep the current page usable. */ }
              setDismissedAttempt(previousId);
            }
            setSubmittedEmail("");
            setCode("");
            setF(current => ({ ...current, email: "", password: "", confirm: "" }));
          })} className="w-full text-sm mt-3 underline" data-testid="sign-up-back">Use a different email</button>
        </form>
      ) : (
        <form onSubmit={submit} className="card p-6 sm:p-8" data-testid="sign-up-page">
          <h1 className="font-display font-black uppercase text-4xl text-center">Create account</h1>
          <p className="mt-2 text-center text-sm text-[var(--muted)]">{pendingCouplesInvite() ? "Create your own sign-in to accept the invitation. Your partner covers Couples access. While they sponsor you, each new AI coaching reply you request uses one of their replies." : <>Monthly <Link to="/plans" className="underline">plans</Link> start at $12 for 100 replies across Individual and Couples. Membership starts when you subscribe.</>}</p>
          <div className="mt-5"><GoogleButton onClick={google} disabled={!isLoaded || busy} onError={setErr} /></div>
          <Divider />
          <Label htmlFor="su-email">Email</Label>
          <Input id="su-email" type="email" autoComplete="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} className="h-11 mt-1.5" data-testid="sign-up-email" />
          <div className="grid sm:grid-cols-2 gap-3 mt-4">
            <div><Label htmlFor="su-pass">Password</Label><Password id="su-pass" autoComplete="new-password" value={f.password} onChange={(v) => setF({ ...f, password: v })} testid="sign-up-password" /></div>
            <div><Label htmlFor="su-confirm">Confirm password</Label><Password id="su-confirm" autoComplete="new-password" value={f.confirm} onChange={(v) => setF({ ...f, confirm: v })} testid="sign-up-confirm" /></div>
          </div>
          <label className="flex items-center gap-2 mt-4 text-sm cursor-pointer"><Checkbox checked={f.remember} onCheckedChange={(v) => setF({ ...f, remember: !!v })} data-testid="sign-up-remember" />Remember me</label>
          <div id="clerk-captcha" className="mt-3" />
          {errorMessage && <p role="alert" className="text-sm text-[var(--crimson)] mt-3" data-testid="sign-up-error">{errorMessage}</p>}
          <button disabled={!isLoaded || busy} className="btn-lime w-full h-11 mt-5" data-testid="sign-up-submit">{busy ? "Creating…" : "Create account"}</button>
          <Consent action="creating an account" />
          <p className="text-sm text-center mt-4">Already have an account? <Link to="/sign-in" className="font-semibold underline" data-testid="sign-up-to-sign-in">Sign in</Link></p>
        </form>
      )}
    </Frame>
  );
}
