import { clearPracticeTurn } from "@/lib/practiceTurn";
import { clearStartDraft } from "@/lib/startDraft";
import ContentImpact from "./ContentImpact";
import { useEffect, useRef, useState } from "react";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "../ui/alert-dialog";

// The API retries pending closures every minute and requests a ten-second wait between checks.
const POLL_INTERVAL = 10_000;
const MAX_CHECKS = 13;
const statusText = {
  submitting: "Submitting your account closure…",
  checking: "Checking your account closure status…",
  pending: "Your Personal account is closing. This page will return home when it’s finished.",
  paused: "Check the status again here or return to Settings later.",
};

export default function AccountClosure({ api, closing, memberId }) {
  const [phase, setPhase] = useState(closing ? "pending" : "ready");
  const [requestDetail, setRequestDetail] = useState("");
  const [checkDetail, setCheckDetail] = useState("");
  const submission = useRef(null);
  const monitoring = phase === "pending" || phase === "checking";
  useEffect(() => () => submission.current?.abort(), []);

  useEffect(() => {
    if (!monitoring) return;
    const controller = new AbortController();
    const { signal } = controller;
    let timer, checks = 0;
    setCheckDetail("");
    async function check() {
      try {
        const { data } = await api.get("/me", { signal, timeout: POLL_INTERVAL });
        if (signal.aborted) return;
        if (!data.closing) {
          setCheckDetail("Your Personal account is still open. You can request closure again.");
          setPhase("ready");
          return;
        }
        checks += 1;
        if (checks >= MAX_CHECKS) {
          setCheckDetail("Your Personal account is still closing.");
          setPhase("paused");
          return;
        }
        setPhase("pending");
        timer = setTimeout(check, POLL_INTERVAL);
      } catch (error) {
        if (signal.aborted) return;
        if (error.response?.status === 410) {
          clearStartDraft(); clearPracticeTurn();
          window.location.replace("/");
          return;
        }
        const reason = error.response?.data?.detail;
        setCheckDetail(typeof reason === "string" ? reason : "We couldn’t check your account closure status.");
        setPhase("paused");
      }
    }
    check();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, monitoring]);

  async function closeAccount() {
    if (phase !== "ready" || submission.current) return;
    const controller = new AbortController();
    submission.current = controller;
    setRequestDetail("");
    setCheckDetail("");
    setPhase("submitting");
    try {
      await api.delete("/me/account", { signal: controller.signal, timeout: 30_000, headers: {"X-Settings-Member": memberId} });
      if (controller.signal.aborted) return;
      clearStartDraft(); clearPracticeTurn();
      if (!controller.signal.aborted) window.location.replace("/");
    } catch (error) {
      if (controller.signal.aborted) return;
      const response = error.response;
      // A retryable lock is normal after a write. Keep genuine billing or provider details visible.
      if (response && !(response.status === 503 && response.headers?.["retry-after"])) {
        const reason = response.data?.detail;
        setRequestDetail(typeof reason === "string" ? reason : "We couldn’t confirm your account closure request.");
      }
      setPhase(!response || response.status >= 500 ? "checking" : "ready");
    } finally {
      submission.current = null;
    }
  }

  return <>
    <AlertDialog>
      <AlertDialogTrigger asChild><button disabled={phase !== "ready"} className="px-5 py-2 text-sm rounded-full border border-[var(--crimson)] text-[var(--crimson)]" data-testid="settings-close-account">{phase === "ready" ? "Close account" : "Closure requested"}</button></AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader><AlertDialogTitle>Close Imladri Personal?</AlertDialogTitle><AlertDialogDescription>This cancels Personal Plus and permanently removes your Personal records. Your shared Imladri sign-in and Market account stay active. Download your export first if you want a copy.</AlertDialogDescription></AlertDialogHeader>
        <ContentImpact api={api} action="close" />
        <AlertDialogFooter><AlertDialogCancel>Keep account</AlertDialogCancel><AlertDialogAction onClick={closeAccount} data-testid="settings-close-confirm">Permanently close account</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    <p role="status" aria-atomic="true" className="basis-full text-sm empty:sr-only">{[requestDetail, checkDetail, statusText[phase]].filter(Boolean).join(" ")}</p>
    {phase === "paused" && <button className="btn-ghost px-5 py-2 text-sm" onClick={() => setPhase("checking")}>Check closure status</button>}
  </>;
}
