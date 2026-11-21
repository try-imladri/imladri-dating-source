import { useEffect, useState } from "react";
import { useSignUp } from "@clerk/react/legacy";
import { Navigate } from "react-router-dom";
import { useConfig } from "@/components/public/PublicLayout";
import { pendingCouplesInvite } from "@/lib/couplesInvite";
import { publicApi } from "@/lib/api";

export default function RegistrationEntry({ children }) {
  const config = useConfig();
  const { isLoaded, signUp } = useSignUp();
  const invite = config.waitlist_mode && !signUp?.id ? pendingCouplesInvite() : "";
  const [checked, setChecked] = useState(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!invite) return;
    const request = new AbortController();
    setChecked(null);
    publicApi.post("/public/registration-invite", { token: invite }, { signal: request.signal }).then(
      ({ data }) => { if (!request.signal.aborted) setChecked({ invite, valid: data.eligible === true }); },
      error => { if (!request.signal.aborted) setChecked({ invite, error: error.response?.status !== 422 }); },
    );
    return () => request.abort();
  }, [invite, attempt]);
  if (signUp?.id || config.waitlist_mode === false || (invite && checked?.invite === invite && checked.valid)) return children;
  if (config.configError) return <p role="alert" className="px-5 py-10 text-sm">Registration information couldn’t load. <button type="button" className="underline" onClick={config.retry}>Try again</button></p>;
  if (invite && checked?.invite === invite && checked.error) return <p role="alert" className="px-5 py-10 text-sm">Your invitation couldn’t be checked. <button type="button" className="underline" onClick={() => setAttempt(n => n + 1)}>Try again</button></p>;
  if (config.waitlist_mode === undefined || !isLoaded || (invite && checked?.invite !== invite)) return <p role="status" className="px-5 py-10 text-sm">Checking registration…</p>;
  return <Navigate to="/waitlist?origin=sign-up" replace />;
}
