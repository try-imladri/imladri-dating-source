import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { errMsg } from "@/lib/api";
import { reflections } from "@/lib/couplesQuestions";

export const BASE = "/app/couples";
export const primary =
  "btn-lime inline-flex items-center justify-center gap-2 px-5 py-2.5 text-sm disabled:opacity-50";
export const secondary =
  "btn-ghost inline-flex items-center justify-center gap-2 px-4 py-2 text-sm disabled:opacity-50";
export const subtle = "text-sm underline underline-offset-4 disabled:opacity-50";
export const field =
  "block w-full rounded-xl border border-[var(--line)] bg-[var(--bg)] px-3 py-2.5 text-base text-[var(--ink)]";
export const quiet = "text-sm leading-relaxed text-[var(--muted)]";
export const pageHeading = "font-display font-black uppercase leading-none break-words text-3xl min-[361px]:text-4xl sm:text-5xl";
export const topics = {
  communication: "Everyday communication",
  repair: "Conflict and repair",
  support: "Support and closeness",
  priorities: "Shared priorities",
};
export const kindOf = (key) =>
  key?.startsWith("moment-") ? "moment" : key?.startsWith("positive-") ? "positive" : "prompt";
export const titleOf = (kind) => (reflections[kind] || reflections.moment).title;
export const answerUrl = (key) => BASE + "/answers?item=" + encodeURIComponent(key);
export const threadUrl = (key) => BASE + "/together?item=" + encodeURIComponent(key);
const agreementTarget = (path, key, agreement) => {
  const query = new URLSearchParams({ item: key });
  if (agreement) {
    query.set("agreement", agreement.id);
    if (agreement.revision) query.set("revision", String(agreement.revision));
  }
  return BASE + path + "?" + query;
};
export const agreementUrl = (key, agreement) => agreementTarget("/agreements", key, agreement);
export const learningUrl = (key, agreement) => agreementTarget("/what-works", key, agreement);
export const validKey = (key) => /^[a-zA-Z0-9_-]{1,80}$/.test(key || "");
export const idFor = (kind) => kind + "-" + crypto.randomUUID();
export const labelDate = (d) =>
  d
    ? new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        timeZone: "UTC",
      }).format(new Date(d + "T12:00:00Z"))
    : "";
export function futureDate(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return [
    d.getFullYear(),
    String(d.getMonth() + 1).padStart(2, "0"),
    String(d.getDate()).padStart(2, "0"),
  ].join("-");
}
export function Heading({ children, note }) {
  const heading = useRef(null);
  const { pathname, search } = useLocation();
  useEffect(() => {
    // A member may already have opened notifications while the workspace was loading.
    if (document.querySelector('[role="dialog"][data-state="open"], [aria-haspopup="dialog"][aria-expanded="true"]')) return;
    heading.current?.focus({ preventScroll: true });
  }, [pathname, search]);
  return (
    <header>
      <h1
        ref={heading}
        tabIndex={-1}
        className={pageHeading}
      >
        {children}
      </h1>
      {note && <p className={quiet + " mt-2"}>{note}</p>}
    </header>
  );
}
export function Panel({ title, children, id }) {
  return (
    <section data-testid={id} className="card p-5 sm:p-6 space-y-3 min-w-0 break-words">
      {title && <h2 className="font-display font-black uppercase leading-tight text-2xl">{title}</h2>}
      {children}
    </section>
  );
}
export function Empty({ title, text, to, action }) {
  return (
    <Panel title={title}>
      <p className={quiet}>{text}</p>
      {to && (
        <Link className={secondary} to={to}>
          {action}
        </Link>
      )}
    </Panel>
  );
}
export function Feedback({ task }) {
  return task.error ? (
    <p role="alert" className="text-sm text-[var(--crimson)]">
      {task.error}
    </p>
  ) : task.notice ? (
    <p role="status" className="text-sm">
      {task.notice}
    </p>
  ) : null;
}
export function useAction(refresh) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const lock = useRef(false);
  async function run(fn, success = "") {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
      setNotice(success);
    } catch (e) {
      if (e?.response?.status === 409 && refresh) await refresh();
      setError(
        e?.response?.status === 409
          ? "This changed while you were here. Review the latest version and try again."
          : errMsg(e),
      );
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return { busy, error, notice, run };
}
