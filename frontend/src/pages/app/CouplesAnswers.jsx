import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useOutletContext } from "react-router-dom";
import { toast } from "sonner";
import { ArrowRight, Check, LockKeyhole } from "lucide-react";
import { reflections } from "@/lib/couplesQuestions";
import CouplesTone from "./CouplesTone";
import {
  BASE,
  Empty,
  Feedback,
  Heading,
  Panel,
  answerUrl,
  field,
  idFor,
  kindOf,
  primary,
  quiet,
  secondary,
  subtle,
  threadUrl,
  titleOf,
  topics,
  useAction,
  validKey,
} from "./CouplesCommon";

function IndividualAnswer({ api, onChoose }) {
  const [items, setItems] = useState(null),
    [picked, setPicked] = useState("");
  const task = useAction();
  if (items === null)
    return (
      <div className="space-y-2">
        <button
          type="button"
          className={subtle}
          disabled={task.busy}
          onClick={() =>
            task.run(async () => {
              const { data } = await api.get("/me/about");
              setItems(Object.entries(data.about?.answers || {}).filter(([, v]) => v.question && v.label));
            })
          }
        >
          Choose from my Individual answers
        </button>
        <Feedback task={task} />
      </div>
    );
  return (
    <div className="rounded-xl border border-[var(--line)] p-3 space-y-2">
      <label className="block text-sm font-semibold">
        Choose a saved everyday answer
        <select className={field + " mt-1"} value={picked} onChange={(e) => setPicked(e.target.value)}>
          <option value="">Select an answer</option>
          {items.map(([id, value]) => (
            <option key={id} value={id}>
              {value.question}
            </option>
          ))}
        </select>
      </label>
      {!items.length && <p className={quiet}>You have no saved everyday choices yet.</p>}
      {picked && (
        <>
          <p className="text-sm">{items.find(([id]) => id === picked)?.[1].label}</p>
          <button
            type="button"
            className={secondary}
            onClick={() => {
              const value = items.find(([id]) => id === picked)?.[1].label;
              if (value) onChoose(value);
              setItems(null);
              setPicked("");
            }}
          >
            Copy these words into my draft
          </button>
        </>
      )}
      <button
        type="button"
        className={subtle}
        onClick={() => {
          setItems(null);
          setPicked("");
        }}
      >
        Close
      </button>
    </div>
  );
}

function AnswerEditor({ id, question, draft, thread }) {
  const { api, me, journey, workspace, mutate, refresh, actOnThread, toneHandoff } = useOutletContext();
  const navigate = useNavigate(),
    task = useAction(refresh);
  const kind = question ? "prompt" : kindOf(id),
    labels = reflections[kind]?.questions || ["My answer"];
  const currentVersion = draft?.version ?? journey.draft_versions?.[id] ?? 0;
  const padded = (saved) => labels.map((_, i) => saved?.[i] || "");
  const savedChoice = draft?.choice ?? null;
  const [values, setValues] = useState(() => padded(draft?.lines));
  const [selection, setSelection] = useState(savedChoice);
  const [base, setBase] = useState(() => ({ version: currentVersion, lines: padded(draft?.lines), choice: savedChoice }));
  const [preview, setPreview] = useState(null),
    [confirmDelete, setConfirmDelete] = useState(false);
  const stale = currentVersion !== base.version;
  const lines = values.map((x) => x.trim()),
    nonempty = lines.filter(Boolean);
  const dirty = selection !== base.choice || lines.some((x, i) => x !== (base.lines[i] || ""));
  const [sharedMessage, setSharedMessage] = useState("");
  const [suggestion, setSuggestion] = useState(() => toneHandoff?.current);
  useEffect(() => { if (toneHandoff) toneHandoff.current = null; }, [toneHandoff]);
  const autoAttempt = useRef("");
  const pendingDraft = useRef(null);
  const leaving = useRef(null);
  const currentInputs = useRef();
  currentInputs.current = { values, selection };
  const autoSave = useRef();
  const update = (i, text) => {
    setValues((previous) => previous.map((x, pos) => (pos === i ? text : x)));
    setPreview(null);
    setSharedMessage("");
  };
  function save(nextValues = values, nextChoice = selection, skip = false) {
    const contents = nextValues.map(value => value.trim());
    return task.run(async () => {
      const body = {
        lines: skip ? [] : !contents.some(Boolean) && nextChoice ? [nextChoice] : contents, version: base.version,
        ...(question ? { choice: nextChoice } : {}), ...(skip ? { skipped: true } : {}),
        ...(me?.user_id ? { expected_member: me.user_id } : {}),
      };
      const request = mutate("put", "/couples/drafts/" + id, body)
        .then(state => state.drafts.find(row => row.key === id));
      pendingDraft.current = { request, body };
      let saved;
      try {
        saved = await request;
      } finally {
        pendingDraft.current = null;
      }
      setBase({ version: saved.version, lines: padded(saved.lines), choice: nextChoice });
      // A writer can be finishing another line while this request is in flight.
      if (skip || (currentInputs.current.values === nextValues && currentInputs.current.selection === nextChoice)) {
        setSelection(nextChoice);
        setValues(padded(saved.lines));
      }
      setPreview(null);
      setSharedMessage("");
    }, skip ? "Question skipped." : "Saved privately.");
  }
  autoSave.current = save;
  const autoKey = JSON.stringify([base.version, lines, selection]);
  const hasAnswer = nonempty.length > 0 || Boolean(selection);
  const shouldSave = dirty && hasAnswer && !stale;
  useEffect(() => {
    if (!shouldSave || task.busy || autoAttempt.current === autoKey) return;
    const timer = setTimeout(() => {
      autoAttempt.current = autoKey;
      autoSave.current();
    }, 600);
    return () => clearTimeout(timer);
  }, [autoKey, shouldSave, task.busy]);
  useEffect(() => {
    if (!dirty && !task.busy) return;
    const warn = event => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, task.busy]);
  leaving.current = shouldSave && me?.user_id ? {
    api, id, body: { lines: nonempty.length ? lines : [selection], version: base.version, ...(question ? { choice: selection } : {}), expected_member: me.user_id },
  } : null;
  useEffect(() => () => {
    const final = leaving.current;
    if (!final) return;
    const flush = version => final.api.put("/couples/drafts/" + final.id, { ...final.body, version })
      .catch(() => toast.error("Your last Couples answer couldn’t be saved."));
    const pending = pendingDraft.current;
    if (!pending) { flush(final.body.version); return; }
    pending.request.then(saved => {
      if (!saved.skipped && (JSON.stringify(saved.lines) !== JSON.stringify(final.body.lines) ||
          (saved.choice ?? null) !== (final.body.choice ?? null))) flush(saved.version);
    }, () => flush(final.body.version));
  }, []);
  const available = workspace.state === "active" && workspace.writable && !(question?.card && question.retired);
  const version = thread?.version ?? journey.share_versions?.[id] ?? 0;
  const ownPublished = thread?.my_share?.length > 0;
  const matchesShare =
    preview &&
    JSON.stringify(preview.lines.filter((_, i) => preview.selected[i])) ===
      JSON.stringify(thread?.my_share || []);
  return (
    <div className="max-w-2xl space-y-5" data-testid="couples-answer-editor">
      <Link className={subtle} to={BASE + "/answers"}>
        All my answers
      </Link>
      <Heading>{question?.question || titleOf(kind)}</Heading>
      <Panel>
        <div className="flex items-center gap-2 text-xs text-[var(--muted)]">
          <LockKeyhole size={15} aria-hidden="true" />
          Only me · Answers save as you go
        </div>
        {question?.choices?.length > 0 && (
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold">Choose an answer to save privately</legend>
            <div className="flex flex-wrap gap-2">
              {question.choices.map((choice) => (
                <button
                  type="button"
                  className={selection === choice ? primary : secondary}
                  aria-pressed={selection === choice}
                  disabled={task.busy || stale}
                  key={choice}
                  onClick={() => {
                    const words = values[0]?.trim();
                    const next = !words || question.choices.includes(words) ? [choice] : values;
                    setValues(next);
                    setSelection(choice);
                    setPreview(null);
                    save(next, choice);
                  }}
                >
                  {choice}
                </button>
              ))}
            </div>
            {selection && <button type="button" className={subtle} disabled={task.busy || stale}
              onClick={() => { setSelection(null); setPreview(null); }}>Use only my written words</button>}
          </fieldset>
        )}
        {suggestion && suggestion.member === me?.user_id && suggestion.key === id && suggestion.pair === workspace.pair_id &&
          thread?.mutual && suggestion.version === (thread.words_version ?? thread.version) && workspace.writable && <div className="rounded-xl border border-[var(--line)] p-3 space-y-2">
            <p className="text-sm font-semibold">Suggested reply for my private draft</p>
            <blockquote className="text-sm">{suggestion.text}</blockquote>
            {labels.map((label, i) => <button key={i} className={secondary} type="button"
              disabled={stale || task.busy} onClick={() => task.run(async () => {
                await api.patch("/couples/tone/shared/" + id, { pair_id: suggestion.pair, version: suggestion.version,
                  coach_id: suggestion.coachId, change: { action: "select", style: suggestion.style } });
                update(i, suggestion.text);
                setSuggestion(null);
              })}>
              {labels.length > 1 ? `Use for “${label}”` : "Use this wording in my draft"}
            </button>)}
            <button type="button" className={subtle} onClick={() => setSuggestion(null)}>Dismiss</button>
          </div>}
        {labels.map((label, i) => (
          <label className="block text-sm font-semibold space-y-1" key={label}>
            <span>{label}</span>
            <textarea
              data-testid={"couples-draft-line-" + i}
              className={field}
              value={values[i]}
              rows={i === 0 ? 3 : 2}
              maxLength={900}
              onBlur={event => {
                if (shouldSave && !task.busy && event.relatedTarget?.closest("a")) save();
              }}
              onChange={(e) => update(i, e.target.value)}
            />
          </label>
        ))}
        {kind === "prompt" && workspace.can_sponsor && <IndividualAnswer api={api} onChoose={(text) => update(0, text)} />}
        {stale && (
          <p role="status" className={quiet}>
            Your saved draft changed.{" "}
            <button
              className={subtle}
              onClick={() => {
                setValues(padded(draft?.lines));
                setBase({ version: currentVersion, lines: padded(draft?.lines), choice: savedChoice });
                setSelection(savedChoice);
                setPreview(null);
              }}
            >
              Load saved version
            </button>
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            data-testid="couples-save-draft"
            disabled={task.busy || stale || !hasAnswer || (!dirty && !!draft)}
            className={primary}
            onClick={() => save()}
          >
            Save privately
          </button>
          {!draft?.skipped && !hasAnswer && question && <button type="button"
            className={subtle} disabled={task.busy || stale} onClick={() => save([], null, true)}>Skip this question</button>}
          <Link to={BASE + "/answers"} className={subtle}>
            Return to my answers
          </Link>
          {draft && (
            <button className={subtle} onClick={() => setConfirmDelete(true)}>
              Delete draft
            </button>
          )}
        </div>
        {confirmDelete && (
          <div className="space-y-2 border-t border-[var(--line)] pt-3">
            <p className={quiet}>
              Delete your private draft? Shared words have their own withdrawal control in Together.
            </p>
            <button
              className={secondary}
              disabled={task.busy || stale}
              onClick={() =>
                task.run(async () => {
                  await mutate("delete", "/couples/drafts/" + id + "?version=" + base.version);
                  navigate(BASE + "/answers");
                })
              }
            >
              Delete my draft
            </button>
            <button className={subtle + " ml-3"} onClick={() => setConfirmDelete(false)}>
              Keep it
            </button>
          </div>
        )}
        {task.notice === "Saved privately." && (
          <p className={quiet}>A line you can use in conversation: “{nonempty[0]}”</p>
        )}
      </Panel>
      {!question?.card && draft && !draft.skipped && !stale && !dirty && nonempty.length > 0 &&
        (workspace.state === "active" ? workspace.writable : workspace.can_sponsor) &&
        <CouplesTone key={`${id}/${base.version}/${journey.pair_id || "solo"}`} mode="private"
          pairId={journey.pair_id} sourceKey={id} version={base.version}
          lines={values.map(text => ({ speaker: "me", text }))} onUseDraft={(text, i) => update(i, text)} />}
      {draft && !draft.skipped && (
        <Panel title={ownPublished ? "My shared answer" : "Share with my partner"}>
          {ownPublished && (
            <>
              <p className={quiet}>Currently shared with {workspace.partner_name}</p>
              {thread.my_share.map((line, i) => (
                <blockquote
                  className="text-sm whitespace-pre-wrap border-l-2 border-[var(--line)] pl-3"
                  key={i}
                >
                  {line}
                </blockquote>
              ))}
              <Link to={threadUrl(id)} className={subtle}>
                Manage shared conversation
              </Link>
            </>
          )}
          {available ? (
            <>
              <p className={quiet}>
                Review the saved lines you want to share. Both perspectives appear after each person shares on
                this conversation.
              </p>
              {!preview && (
                <button
                  data-testid="couples-preview-share"
                  type="button"
                  className={secondary}
                  disabled={dirty || stale || task.busy}
                  onClick={() => {
                    setSharedMessage("");
                    setPreview({ lines: nonempty, selected: nonempty.map(() => true), version });
                  }}
                >
                  Preview sharing
                </button>
              )}
              {preview && (
                <div className="space-y-3 border-t border-[var(--line)] pt-3">
                  <h2 className="font-semibold">What {workspace.partner_name} will see</h2>
                  {!question?.card && <p className={quiet}>After both of you share, either of you can ask DeepSeek for private tone feedback on the shared lines.</p>}
                  {preview.lines.map((line, i) => (
                    <label key={i} className="flex items-start gap-2 text-sm whitespace-pre-wrap">
                      <input
                        type="checkbox"
                        className="mt-1"
                        checked={preview.selected[i]}
                        onChange={(e) => {
                          const checked = e.target.checked;
                          setPreview((p) => ({
                            ...p,
                            selected: p.selected.map((v, k) => (k === i ? checked : v)),
                          }));
                        }}
                      />
                      {line}
                    </label>
                  ))}
                  {ownPublished && (
                    <p className={quiet}>
                      Replacing shared words clears the agreements, check-ins and learnings based on this
                      conversation.
                    </p>
                  )}
                  <button
                    data-testid="couples-confirm-share"
                    className={primary}
                    disabled={
                      matchesShare ||
                      task.busy ||
                      stale ||
                      version !== preview.version ||
                      !preview.selected.some(Boolean)
                    }
                    onClick={() =>
                      task.run(async () => {
                        await actOnThread(id, "share", {
                          kind,
                          ...(question ? { prompt_id: id } : {}),
                          lines: preview.lines.filter((_, i) => preview.selected[i]),
                          expectedVersion: preview.version,
                        });
                        setPreview(null);
                        setSharedMessage("Your selected words were shared.");
                      })
                    }
                  >
                    {ownPublished ? "Replace shared words" : "Share selected words"}
                  </button>
                  <button className={subtle + " ml-3"} onClick={() => setPreview(null)}>
                    Cancel preview
                  </button>
                  {version !== preview.version && (
                    <p role="status" className={quiet}>
                      The conversation changed. Close the preview and review it again.
                    </p>
                  )}
                </div>
              )}
            </>
          ) : question?.card && question.retired ? (
            <p className={quiet}>This draft belongs to an earlier shared space. You can keep or delete your words here.</p>
          ) : (
            <Link className={secondary} to={BASE + "/partner"}>
              {workspace.state === "active" ? "Review shared access" : "Connect my partner"}
            </Link>
          )}
          {sharedMessage && (
            <p role="status">
              <Check size={16} className="inline mr-1" />
              {sharedMessage}{" "}
              <Link className={subtle} to={threadUrl(id)}>
                Open Together
              </Link>
            </p>
          )}
        </Panel>
      )}
      <Feedback task={task} />
    </div>
  );
}

export function CouplesAnswers() {
  const { journey } = useOutletContext(),
    { search } = useLocation(),
    navigate = useNavigate();
  const id = new URLSearchParams(search).get("item");
  const drafts = journey.drafts;
  const question = journey.prompts.find((q) => q.id === id);
  if (id) {
    if (!validKey(id) || (!question && !/^(moment|positive)-[a-f0-9-]{36}$/.test(id)))
      return (
        <Empty
          title="Question unavailable"
          text="Choose one of your available prompts."
          to={BASE + "/answers"}
          action="My answers"
        />
      );
    return (
      <AnswerEditor
        key={id}
        id={id}
        question={question}
        draft={drafts.find((x) => x.key === id)}
        thread={journey.threads.find((x) => x.id === id)}
      />
    );
  }
  const published = journey.prompts.filter((q) => !q.retired && !q.card);
  const groups = [...new Set(published.map((q) => q.topic))];
  const topicPanel = group => <Panel key={group} title={topics[group] || group}>
    <ul className="divide-y divide-[var(--line)]">
      {published.filter(q => q.topic === group).map(q => <li key={q.id}>
        <Link data-testid={"couples-answer-" + q.id}
          className="flex items-center justify-between gap-3 py-3 text-sm hover:underline" to={answerUrl(q.id)}>
          <span className="min-w-0">{q.question}</span><ArrowRight size={15} className="shrink-0" />
        </Link>
      </li>)}
    </ul>
  </Panel>;
  return (
    <div className="w-full min-w-0 space-y-5" data-testid="couples-answers">
      <Heading note="Pick one question to begin. Save your own words before choosing whether to share them.">
        My answers
      </Heading>
      <Link className={subtle} to={BASE + "/partner#my-questionnaire-profile"}>My questionnaire profile</Link>
      <div className="grid items-start gap-3 xl:grid-cols-3" data-testid="couples-answer-topics-layout">
      <div className="grid min-w-0 gap-3 sm:grid-cols-2 xl:col-span-2">
        {["moment", "positive"].map((kind) => (
          <Panel key={kind} title={titleOf(kind)}>
            <p className={quiet}>
              {kind === "moment"
                ? "Describe what happened, what you needed and what you would try."
                : "Capture a moment you would like to repeat together."}
            </p>
            <button
              className={secondary}
              data-testid={"couples-new-" + kind}
              onClick={() => navigate(answerUrl(idFor(kind)))}
            >
              Start reflection
            </button>
          </Panel>
        ))}
        {groups.slice(0, 2).map(topicPanel)}
      </div>
      {groups.length > 2 && <div className="grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-1" data-testid="couples-answer-more-topics">
        {groups.slice(2).map(topicPanel)}
      </div>}
      </div>
      {drafts.length > 0 && (
        <Panel title="My saved drafts">
          <ul className="space-y-2">
            {drafts.map((d) => {
              const q = journey.prompts.find((row) => row.id === d.key);
              return (
                <li key={d.key}>
                  <Link className="block rounded-xl hover:bg-[var(--surface-2)] p-2" to={answerUrl(d.key)}>
                    <span className="block font-semibold text-sm">
                      {q?.question || titleOf(kindOf(d.key))}
                    </span>
                    <span className={quiet + " line-clamp-1"}>{d.skipped ? "Skipped" : d.lines.find(Boolean)}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </Panel>
      )}
    </div>
  );
}
