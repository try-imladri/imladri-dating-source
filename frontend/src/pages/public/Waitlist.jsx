import { useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { Input } from "@/components/ui/input";
import { publicApi, errMsg } from "@/lib/api";
import { useConfig } from "@/components/public/PublicLayout";

export default function Waitlist() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const cfg = useConfig();
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (cfg.configError) return <p role="alert" className="px-5 py-10">Registration information couldn’t load. <button className="underline" onClick={cfg.retry}>Try again</button></p>;
  if (cfg.waitlist_mode == null) return <p role="status" className="px-5 py-10">Checking registration…</p>;
  if (cfg.waitlist_mode === false) return <Navigate to="/sign-up" replace />;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await publicApi.post("/public/waitlist", { email, origin: params.get("origin") || "waitlist" });
      setDone(true);
    } catch (err) {
      if (err.response?.status === 409) navigate("/sign-up", { replace: true });
      else setError(errMsg(err));
    }
    setBusy(false);
  };

  return (
    <div className="px-5 sm:px-8 lg:px-12 py-20 max-w-3xl">
      <p className="eyebrow">Yes, it's an app</p>
      <h1 className="font-display font-black uppercase text-6xl mt-3" data-testid="waitlist-title">Get in early</h1>
      <p className="text-[var(--muted)] mt-3">Drop your email. We'll let you in as soon as your spot opens.</p>
      {error && <p role="alert" className="mt-4 text-sm text-[var(--crimson)]">{error}</p>}
      {done ? (
        <p role="status" className="card p-5 mt-8" data-testid="waitlist-success">You're on the list. We'll email you.</p>
      ) : (
        <form onSubmit={submit} className="flex flex-col sm:flex-row gap-3 mt-8">
          <Input type="email" aria-label="Email address" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@email.com" className="h-12 rounded-full px-5" data-testid="waitlist-email-input" />
          <button disabled={busy} className="btn-lime px-6 h-12" data-testid="waitlist-submit-button">{busy ? "…" : "Join"}</button>
        </form>
      )}
    </div>
  );
}
