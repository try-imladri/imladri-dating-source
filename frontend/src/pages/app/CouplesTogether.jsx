import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useOutletContext } from "react-router-dom";
import CouplesTone from "./CouplesTone";
import { followUp } from "@/lib/couplesQuestions";
import {
  BASE,
  Empty,
  Feedback,
  Heading,
  Panel,
  agreementUrl,
  answerUrl,
  field,
  futureDate,
  labelDate,
  learningUrl,
  topics,
  primary,
  quiet,
  secondary,
  subtle,
  threadUrl,
  useAction,
} from "./CouplesCommon";

function PairNeeded() {
  return (
    <Empty
      title="Connect with a partner"
      text="You can write privately in My answers. Linking two accounts lets you share a conversation together."
      to={BASE + "/partner"}
      action="Open partner settings"
    />
  );
}
function ReadOnly({ workspace }) {
  return (
    !workspace.writable && (
      <p className="rounded-xl border border-[var(--line)] p-3 text-sm">
        Your shared space is read-only.{" "}
        <Link className={subtle} to={BASE + "/partner"}>
          Review sponsorship
        </Link>
      </p>
    )
  );
}
function itemFrom(search) {
  return new URLSearchParams(search).get("item");
}

function Conversation({ thread }) {
  const { workspace, me, refresh, actOnThread, toneHandoff } = useOutletContext(),
    task = useAction(refresh);
  const navigate = useNavigate();
  const [withdraw, setWithdraw] = useState(false);
  const wordsVersion = thread.words_version ?? thread.version;
  return (
    <Panel title={thread.title} id={"couples-thread-" + thread.id}>
      {thread.mutual ? (
        <>
          <div className="grid sm:grid-cols-2 gap-3">
            {thread.perspectives.map((p) => (
              <section
                key={p.mine ? "own" : "partner"}
                className="rounded-xl border border-[var(--line)] p-4 space-y-2"
              >
                <h3 className="font-semibold text-sm">
                  {p.mine ? "My words" : workspace.partner_name + "’s words"}
                </h3>
                {p.lines.map((line, i) => (
                  <p className="text-sm whitespace-pre-wrap" key={i}>
                    {line}
                  </p>
                ))}
              </section>
            ))}
          </div>
          <p className="text-sm font-semibold">{thread.card ? "Which parts of your answers could you build into one agreement?" : thread.discussion}</p>
          {!thread.card && workspace.writable && <CouplesTone key={`${workspace.pair_id}/${wordsVersion}`} mode="shared"
            sourceKey={thread.id} pairId={workspace.pair_id} version={wordsVersion}
            lines={thread.perspectives.flatMap(p => p.lines.map(text => ({ speaker: p.mine ? "me" : "them", text })))}
            onUseDraft={(text, _line, source) => {
              toneHandoff.current = { ...source, key: thread.id, pair: workspace.pair_id, member: me.user_id, version: wordsVersion, text };
              navigate(answerUrl(thread.id));
            }} />}
          <div className="flex flex-wrap gap-3">
            <Link className={primary} to={agreementUrl(thread.id)}>
              {thread.agreements.length ? "Open agreements" : "Try an agreement"}
            </Link>
            <Link className={secondary} to={answerUrl(thread.id)}>
              Review my words
            </Link>
          </div>
        </>
      ) : thread.my_share.length ? (
        <>
          <p className={quiet}>Your words are ready. Both perspectives appear after each person shares.</p>
          <Link className={secondary} to={answerUrl(thread.id)}>
            Review my words
          </Link>
        </>
      ) : (
        <>
          <p className={quiet}>
            Add a perspective of your own when you’re ready. You’ll preview the exact lines before sharing.
          </p>
          <Link className={primary} to={answerUrl(thread.id)}>
            Answer privately
          </Link>
        </>
      )}
      {!!thread.my_share.length && (
        <div>
          {!withdraw ? (
            <button className={subtle} onClick={() => setWithdraw(true)}>
              Withdraw my shared words
            </button>
          ) : (
            <div className="space-y-2">
              <p className={quiet}>
                This removes your shared words and dependent agreements, check-ins and saved learnings from
                this conversation.
              </p>
              <button
                data-testid="couples-withdraw-share"
                className={secondary}
                disabled={task.busy}
                onClick={() =>
                  task.run(async () => {
                    await actOnThread(thread.id, "withdraw");
                    setWithdraw(false);
                  })
                }
              >
                Confirm withdrawal
              </button>
              <button className={subtle + " ml-3"} onClick={() => setWithdraw(false)}>
                Keep shared
              </button>
            </div>
          )}
        </div>
      )}
      <Feedback task={task} />
    </Panel>
  );
}
export function CouplesTogether() {
  const { workspace, journey } = useOutletContext(),
    { search } = useLocation();
  const requested = itemFrom(search),
    selected = journey.threads.find((t) => t.id === requested);
  return (
    <div className="max-w-4xl space-y-5" data-testid="couples-together">
      <Heading note="A conversation opens when each of you chooses words to share on the same question or moment.">
        Together
      </Heading>
      {workspace.state !== "active" ? (
        <PairNeeded />
      ) : (
        <>
          <ReadOnly workspace={workspace} />
          {requested && (
            <Link className={subtle} to={BASE + "/together"}>
              All conversations
            </Link>
          )}
          {(requested ? (selected ? [selected] : []) : journey.threads).map((t) => (
            <Conversation key={t.id} thread={t} />
          ))}
          {(!journey.threads.length || (requested && !selected)) && (
            <Empty
              title={requested ? "Conversation unavailable" : "Start a conversation"}
              text="Choose an everyday question or bring a recent moment into the conversation."
              to={BASE + "/answers"}
              action="Open my answers"
            />
          )}
        </>
      )}
    </div>
  );
}

function Proposal({ thread, agreement, initialText = "", close, onSaved }) {
  const { actOnThread, refresh, workspace } = useOutletContext(),
    task = useAction(refresh);
  const [text, setText] = useState(agreement?.text || initialText),
    [date, setDate] = useState(agreement?.review_on || futureDate(7));
  const [baseVersion, setVersion] = useState(thread.version);
  const stale = baseVersion !== thread.version;
  return (
    <form
      className="space-y-3 rounded-xl border border-[var(--line)] p-4"
      onSubmit={(e) => {
        e.preventDefault();
        task.run(async () => {
          await actOnThread(thread.id, "propose", {
            ...(agreement ? { agreement_id: agreement.id } : {}),
            text: text.trim(),
            review_on: date,
            utc_offset_minutes: -new Date().getTimezoneOffset(),
            expectedVersion: baseVersion,
          });
          close();
          onSaved?.();
        });
      }}
    >
      <label className="block text-sm font-semibold">
        {followUp.agreement}
        <textarea
          data-testid="couples-agreement-text"
          required
          maxLength={900}
          rows={3}
          className={field + " mt-1"}
          placeholder="When … happens, we’ll try …"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </label>
      {!agreement && !text && thread.example_agreement && <button type="button" className={subtle}
        onClick={() => setText(thread.example_agreement)}>Use the card’s example agreement</button>}
      <label className="filter-field max-w-xs text-sm font-semibold">
        <span>{followUp.reviewDate}</span>
        <input
          type="date"
          data-testid="couples-review-on"
          min={futureDate(0)}
          required
          className={field + " filter-control"}
          value={date}
          onChange={(e) => setDate(e.target.value)}
        />
      </label>
      <div className="flex gap-3 text-sm">
        <button type="button" className={subtle} onClick={() => setDate(futureDate(3))}>
          In three days
        </button>
        <button type="button" className={subtle} onClick={() => setDate(futureDate(7))}>
          In a week
        </button>
      </div>
      {agreement && (
        <p className={quiet}>
          {agreement.status === "active"
            ? "The current agreement and its reviews will be saved in your history. Both of you will need to accept the new version."
            : "Both of you will need to accept the revised wording."}
        </p>
      )}
      <p className={quiet}>
        Sending your proposal counts as your acceptance. {workspace.partner_name} can accept, edit or pass.
      </p>
      {stale && (
        <p className={quiet} role="status">
          The conversation changed.{" "}
          <button
            type="button"
            className={subtle}
            onClick={() => {
              setVersion(thread.version);
              setText(agreement?.text || text);
              setDate(agreement?.review_on || date);
            }}
          >
            Review latest version
          </button>
        </p>
      )}
      <div className="flex gap-3 flex-wrap">
        <button
          type="submit"
          data-testid="couples-save-agreement"
          className={primary}
          disabled={task.busy || !workspace.writable || stale || !text.trim() || !date}
        >
          Send proposal
        </button>
        <button type="button" className={subtle} onClick={close}>
          Cancel
        </button>
      </div>
      <Feedback task={task} />
    </form>
  );
}

function Checkin({ thread, agreement }) {
  const { api, workspace, refresh, actOnThread } = useOutletContext(),
    task = useAction(refresh);
  const checkinPanel = useRef(null);
  useEffect(() => {
    if (!workspace.writable || agreement.status !== "active" || !window.IntersectionObserver) return;
    const panel = checkinPanel.current;
    let sent = false, visible = false;
    const observe = () => {
      const today = new Date().toISOString().slice(0, 10);
      if (sent || !visible || document.visibilityState !== "visible" || today < agreement.review_on) return;
      sent = true;
      api.post("/couples/threads/" + encodeURIComponent(thread.id) + "/review-visit/" + encodeURIComponent(agreement.id) + "/" + agreement.revision)
        .catch(() => { sent = false; });
    };
    const observer = new IntersectionObserver(entries => { visible = entries.some(entry => entry.isIntersecting); observe(); });
    observer.observe(panel);
    document.addEventListener("visibilitychange", observe);
    return () => { observer.disconnect(); document.removeEventListener("visibilitychange", observe); };
  }, [api, workspace.writable, thread.id, agreement.status, agreement.id, agreement.revision, agreement.review_on]);
  const own = agreement.my_checkin;
  const [result, setResult] = useState(own?.result || ""),
    [note, setNote] = useState(own?.note || ""),
    [preview, setPreview] = useState(null);
  const dirty = result !== own?.result || note.trim() !== (own?.note || "");
  const current = agreement.status === "active" && workspace.writable;
  const submit = (share, version, selected = result) =>
    task.run(
      async () => {
        await actOnThread(thread.id, "checkin", {
          agreement_id: agreement.id,
          result: selected,
          note: note.trim(),
          share,
          expectedVersion: version,
        });
        setPreview(null);
      },
      share ? "Check-in shared." : "Check-in saved privately.",
    );
  return (
    <section ref={checkinPanel} className="border-t border-[var(--line)] pt-4 space-y-3">
      <h3 className="text-base font-bold">My check-in</h3>
      {thread.card && <p className={quiet}>{thread.discussion}</p>}
      {current ? (
        <>
          <label className="block text-sm">
            {followUp.checkin}
            <select
              data-testid="couples-checkin-result"
              className={field + " mt-1"}
              value={result}
              disabled={task.busy}
              onChange={(e) => {
                const selected = e.target.value;
                setResult(selected);
                setPreview(null);
                if (selected && !own?.shared) submit(false, thread.version, selected);
              }}
            >
              <option value="">Choose a result</option>
              {Object.entries(followUp.choices).map(([key, value]) => (
                <option value={key} key={key}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            {followUp.note}
            <textarea
              data-testid="couples-checkin-note"
              className={field + " mt-1"}
              rows={2}
              maxLength={900}
              value={note}
              disabled={task.busy}
              onBlur={event => {
                if (event.relatedTarget?.closest("[data-checkin-action]")) return;
                if (result && dirty && !own?.shared && !task.busy) submit(false, thread.version);
              }}
              onChange={(e) => {
                setNote(e.target.value);
                setPreview(null);
              }}
            />
          </label>
          {own?.shared && dirty && (
            <p className={quiet}>
              Saving an edit privately withdraws your shared check-in. Changing it also clears any learning
              based on it.
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              className={secondary}
              data-testid="couples-save-checkin"
              data-checkin-action="save"
              disabled={task.busy || !result || (!dirty && !!own)}
              onClick={() => submit(false, thread.version)}
            >
              Save privately
            </button>
            <button
              className={secondary}
              data-checkin-action="preview"
              disabled={task.busy || !result || (!dirty && own?.shared)}
              onClick={() => setPreview({ version: thread.version })}
            >
              Preview shared check-in
            </button>
          </div>
          {preview && (
            <div className="rounded-xl border border-[var(--line)] p-3 space-y-2">
              <h4 className="font-semibold text-sm">
                What {workspace.partner_name} can see after both share
              </h4>
              <p>{followUp.choices[result]}</p>
              {note.trim() && <p className="text-sm whitespace-pre-wrap">{note.trim()}</p>}
              {preview.version !== thread.version && (
                <p className={quiet}>The conversation changed. Open a fresh preview.</p>
              )}
              <button
                className={primary}
                data-testid="couples-share-checkin"
                disabled={task.busy || preview.version !== thread.version}
                onClick={() => submit(true, preview.version)}
              >
                Share this check-in
              </button>
              <button className={subtle + " ml-3"} onClick={() => setPreview(null)}>
                Cancel
              </button>
            </div>
          )}
        </>
      ) : (
        own && (
          <p className="text-sm whitespace-pre-wrap">
            {followUp.choices[own.result]} {own.note}
          </p>
        )
      )}
      {own && (
        <div className="flex flex-wrap gap-3 items-center">
          <span className={quiet}>{own.shared ? "Shared check-in" : "Private check-in"}</span>
          <button
            className={subtle}
            disabled={task.busy}
            onClick={() =>
              task.run(
                () => actOnThread(thread.id, "withdraw_checkin", { agreement_id: agreement.id }),
                "Check-in removed.",
              )
            }
          >
            Remove my check-in
          </button>
        </div>
      )}
      {agreement.partner_checkin && (
        <div className="rounded-xl border border-[var(--line)] p-3 space-y-2">
          <h4 className="font-semibold">{workspace.partner_name}’s check-in</h4>
          <p>{followUp.choices[agreement.partner_checkin.result]}</p>
          <p className="text-sm whitespace-pre-wrap">{agreement.partner_checkin.note}</p>
          <Link className={subtle} to={learningUrl(thread.id, agreement)}>
            Save what worked for us
          </Link>
        </div>
      )}
      <Feedback task={task} />
    </section>
  );
}

const statusLabels = {
  proposed: "Awaiting approval",
  active: "Agreed",
  passed: "Passed",
  retired: "Retired",
};
function Agreement({ thread, agreement }) {
  const { workspace, refresh, actOnThread } = useOutletContext(),
    task = useAction(refresh);
  const [edit, setEdit] = useState(false),
    [retiring, setRetiring] = useState(false);
  const change = (action) => task.run(() => actOnThread(thread.id, action, { agreement_id: agreement.id }));
  return (
    <div className="space-y-3 rounded-xl border border-[var(--line)] p-4">
      <p className={quiet}>
        {statusLabels[agreement.status]} · Check in {labelDate(agreement.review_on)}
      </p>
      <p className="whitespace-pre-wrap font-semibold">{agreement.text}</p>
      {agreement.status === "proposed" && (
        <p className={quiet}>
          {agreement.accepted_by_me
            ? "You approved this wording."
            : workspace.partner_name + " proposed this wording."}
        </p>
      )}
      {workspace.writable && !edit && (
        <div className="flex items-center gap-3 flex-wrap">
          {agreement.status === "proposed" && !agreement.accepted_by_me && (
            <button
              className={primary}
              data-testid="couples-accept-agreement"
              disabled={task.busy}
              onClick={() => change("accept")}
            >
              Accept agreement
            </button>
          )}
          {agreement.status === "proposed" && (
            <button className={secondary} disabled={task.busy} onClick={() => change("pass")}>
              Pass
            </button>
          )}
          {agreement.status !== "retired" && (
            <button className={subtle} data-testid="couples-edit-agreement" onClick={() => setEdit(true)}>
              Propose an edit
            </button>
          )}
          {agreement.status === "active" && (
            <button className={subtle} onClick={() => setRetiring(true)}>
              Retire agreement
            </button>
          )}
        </div>
      )}
      {edit && <Proposal thread={thread} agreement={agreement} close={() => setEdit(false)} />}
      {retiring && (
        <div className="space-y-2">
          <p className={quiet}>End this agreement for both of you and keep its history?</p>
          <button
            className={secondary}
            disabled={task.busy}
            onClick={() =>
              task.run(async () => {
                await actOnThread(thread.id, "retire", { agreement_id: agreement.id });
                setRetiring(false);
              })
            }
          >
            Confirm retirement
          </button>
          <button className={subtle + " ml-3"} onClick={() => setRetiring(false)}>
            Keep active
          </button>
        </div>
      )}
      {(agreement.status === "active" || agreement.my_checkin || agreement.partner_checkin) && (
        <Checkin
          key={agreement.id + ":" + agreement.revision + ":" + JSON.stringify(agreement.my_checkin)}
          thread={thread}
          agreement={agreement}
        />
      )}
      <Feedback task={task} />
    </div>
  );
}

function ThreadAgreements({ thread, selectedAgreement }) {
  const { workspace } = useOutletContext(),
    [creating, setCreating] = useState(false);
  return (
    <Panel title={thread.title}>
      <Link to={threadUrl(thread.id)} className={subtle}>
        Read both perspectives
      </Link>
      {(selectedAgreement ? [selectedAgreement] : thread.agreements).map((ag) => (
        <Agreement key={ag.id} thread={thread} agreement={ag} />
      ))}
      {creating ? (
        <Proposal thread={thread} close={() => setCreating(false)} />
      ) : (
        workspace.writable && !selectedAgreement && (
          <button data-testid="couples-propose" className={secondary} onClick={() => setCreating(true)}>
            Propose an agreement
          </button>
        )
      )}
    </Panel>
  );
}
export function CouplesAgreements() {
  const { workspace, journey } = useOutletContext(),
    { search } = useLocation();
  const id = itemFrom(search),
    mutual = journey.threads.filter((t) => t.mutual && (!id || t.id === id));
  const params = new URLSearchParams(search);
  const targetId = params.get("agreement"), revision = params.get("revision");
  const selected = id && targetId && mutual[0]?.agreements.find(a => a.id === targetId && (!revision || String(a.revision) === revision));
  return (
    <div className="max-w-3xl space-y-5" data-testid="couples-agreements">
      <Heading note="Agree on one change, then come back to see how it felt for each of you.">
        Agreements & check-ins
      </Heading>
      {workspace.state !== "active" ? (
        <PairNeeded />
      ) : (
        <>
          <ReadOnly workspace={workspace} />
          {id && (
            <Link to={BASE + "/agreements"} className={subtle}>
              All agreements
            </Link>
          )}
          {targetId && !selected ? (
            <Empty title="This agreement version is unavailable" text="Open your current agreements to see the latest shared versions." to={BASE + "/agreements"} action="View current agreements" />
          ) : mutual.length ? (
            mutual.map((t) => <ThreadAgreements key={t.id} thread={t} selectedAgreement={selected} />)
          ) : (
            <Empty
              title="Start with two perspectives"
              text="When both of you share on a conversation, either of you can propose something to try."
              to={BASE + "/together"}
              action="Open Together"
            />
          )}
        </>
      )}
    </div>
  );
}

function Learning({ thread, agreement }) {
  const { workspace, refresh, actOnThread } = useOutletContext(),
    task = useAction(refresh);
  const [text, setText] = useState(""),
    [removing, setRemoving] = useState(false);
  const pin = agreement.pin;
  const [reusing, setReusing] = useState(false);
  const navigate = useNavigate();
  return (
    <Panel title={thread.title}>
      <p className={quiet}>Scheduled check-in · {labelDate(agreement.review_on)}</p>
      <p className="text-sm whitespace-pre-wrap">{agreement.text}</p>
      {pin ? (
        <>
          <blockquote className="text-base font-medium whitespace-pre-wrap border-l-2 pl-3 border-[var(--ink)]">
            {pin.text}
          </blockquote>
          {!pin.active && (
            <p className={quiet}>
              {pin.accepted_by_me
                ? "Awaiting your partner’s approval."
                : workspace.partner_name + " suggested saving this learning."}
            </p>
          )}
          {!pin.active && !pin.accepted_by_me && workspace.writable && (
            <button
              className={primary}
              disabled={task.busy}
              data-testid="couples-accept-pin"
              onClick={() =>
                task.run(() => actOnThread(thread.id, "accept_pin", { agreement_id: agreement.id }))
              }
            >
              Agree and save
            </button>
          )}
          {!removing ? (
            <button className={subtle + " ml-2"} onClick={() => setRemoving(true)}>
              {pin.active ? "Remove learning" : "Dismiss proposal"}
            </button>
          ) : (
            <div className="space-y-2">
              <p className={quiet}>Remove this sentence from both partners’ shared learnings?</p>
              <button
                className={secondary}
                disabled={task.busy}
                onClick={() =>
                  task.run(async () => {
                    await actOnThread(thread.id, "unpin", { agreement_id: agreement.id });
                    setRemoving(false);
                  })
                }
              >
                Remove sentence
              </button>
              <button className={subtle + " ml-3"} onClick={() => setRemoving(false)}>
                Keep it
              </button>
            </div>
          )}
        </>
      ) : (
        workspace.writable && (
          <form
            className="space-y-2"
            onSubmit={(e) => {
              e.preventDefault();
              task.run(async () => {
                await actOnThread(thread.id, "pin", { agreement_id: agreement.id, text: text.trim() });
                setText("");
              });
            }}
          >
            <label className="block text-sm font-semibold">
              {followUp.learning}
              <textarea
                data-testid="couples-pinned-text"
                className={field + " mt-1"}
                rows={2}
                maxLength={900}
                value={text}
                onChange={(e) => setText(e.target.value)}
                required
              />
            </label>
            <p className={quiet}>This sentence goes to your partner for approval.</p>
            <button
              className={secondary}
              disabled={task.busy || !text.trim()}
              data-testid="couples-propose-pin"
            >
              Propose learning
            </button>
          </form>
        )
      )}
      {pin?.active && workspace.writable && (reusing
        ? <Proposal thread={thread} initialText={pin.text} close={() => setReusing(false)}
            onSaved={() => navigate(agreementUrl(thread.id))} />
        : <button type="button" className={secondary} onClick={() => setReusing(true)}>Try this learning again</button>)}
      <Link className={"block " + subtle} to={agreementUrl(thread.id, agreement)}>
        View agreement & check-ins
      </Link>
      <Feedback task={task} />
    </Panel>
  );
}
export function CouplesLearnings() {
  const { workspace, journey } = useOutletContext();
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  const agreementId = params.get("agreement"), threadId = params.get("item"), revision = params.get("revision");
  const [topic, setTopic] = useState("");
  const topicFor = thread => thread.card || journey.prompts?.find(prompt => prompt.id === thread.prompt_id)?.topic || thread.kind;
  const reviews = journey.threads.flatMap((thread) =>
    thread.agreements
      .filter((ag) => ag.pin || (ag.my_checkin?.shared && ag.partner_checkin))
      .map((agreement) => ({ thread, agreement })),
  );
  const options = [...new Set(reviews.map(({ thread }) => topicFor(thread)).filter(Boolean))];
  const filtered = agreementId ? reviews.filter(({ thread, agreement }) => thread.id === threadId &&
    agreement.id === agreementId && (!revision || String(agreement.revision) === revision)) :
    reviews.filter(({ thread }) => !topic || topicFor(thread) === topic);
  filtered.sort((a, b) => Number(!!b.agreement.pin?.active) - Number(!!a.agreement.pin?.active));
  return (
    <div className="max-w-3xl space-y-5" data-testid="couples-what-works">
      <Heading note="Save a sentence you both want to remember from an agreement you tried.">
        What works for us
      </Heading>
      {workspace.state !== "active" ? (
        <PairNeeded />
      ) : (
        <>
          <ReadOnly workspace={workspace} />
          {agreementId && <Link to={BASE + "/what-works"} className={subtle}>All shared learnings</Link>}
          {!agreementId && options.length > 0 && <label className="block text-sm">Topic
            <select className={field} value={topic} onChange={e => setTopic(e.target.value)}>
              <option value="">All topics</option>{options.map(k => <option key={k} value={k}>{topics[k] || ({moment:"Difficult moments",positive:"Positive moments",prompt:"Starters"})[k] || k}</option>)}
            </select></label>}
          {agreementId && !filtered.length ? (
            <Empty title="This shared learning is unavailable" text="The agreement version or its shared check-ins have changed."
              to={BASE + "/what-works"} action="View shared learnings" />
          ) : filtered.length ? (
            filtered.map(({ thread, agreement }) => (
              <Learning key={agreement.id} thread={thread} agreement={agreement} />
            ))
          ) : (
            <Empty
              title="Your shared learnings"
              text="After you both share a check-in, suggest a sentence. It becomes a saved learning once both of you approve it."
              to={BASE + "/agreements"}
              action="View agreements & check-ins"
            />
          )}
        </>
      )}
    </div>
  );
}
