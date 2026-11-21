import { useEffect, useRef, useState } from "react";
import { DemoFields } from "@/components/app/Demographics";
import { Link, useLocation, useNavigate, useOutletContext } from "react-router-dom";
import { RecordExportPreference } from "@/components/app/RecordExportPreference";
import ToneResearchPreference from "@/components/app/ToneResearchPreference";
import { clearCouplesInvite, pendingCouplesInvite, storeCouplesInvite } from "@/lib/couplesInvite";
import {
  BASE,
  Empty,
  Feedback,
  Heading,
  Panel,
  field,
  primary,
  quiet,
  secondary,
  subtle,
  useAction,
} from "./CouplesCommon";

function WorkspaceChangeNotice() {
  return <p className={quiet} data-testid="couples-workspace-change-notice">After the inviter’s final confirmation, both accounts switch to Couples only. My assessments keeps your own personality and sex &amp; intimacy questions available; sharing answers is a separate choice. Individual and Your Type are unavailable while you are paired. Your private history is retained. Leaving restores only the access covered by your existing membership or complimentary entitlement.</p>;
}

export function CouplesInviteEntry() {
  const nav = useNavigate();
  const { hash } = useLocation();
  const [invalid, setInvalid] = useState(false);
  useEffect(() => {
    if (storeCouplesInvite(hash.slice(1))) nav(BASE + "/join", { replace: true });
    else {
      clearCouplesInvite();
      setInvalid(true);
    }
  }, [hash, nav]);
  return (
    <main className="mx-auto max-w-lg px-5 py-12">
      <Heading>Couples invitation</Heading>
      {invalid ? (
        <p className={quiet + " mt-4"}>
          Open the complete invitation link from your partner.{" "}
          <Link className={subtle} to="/">
            Back to Imladri
          </Link>
        </p>
      ) : (
        <p className={quiet + " mt-4"} role="status">
          Opening your invitation…
        </p>
      )}
    </main>
  );
}

export function JoinCouples() {
  const { api, workspace, mutate, refresh } = useOutletContext();
  const token = pendingCouplesInvite();
  const [preview, setPreview] = useState(null),
    [failure, setFailure] = useState(false),
    [retry, setRetry] = useState(0);
  const task = useAction(refresh),
    nav = useNavigate();
  useEffect(() => {
    if (!token || workspace.state !== "solo") return;
    const controller = new AbortController();
    setFailure(false);
    setPreview(null);
    api
      .post("/couples/invite/preview", { token }, { signal: controller.signal })
      .then(({ data }) => {
        if (!controller.signal.aborted) setPreview(data);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailure(true);
      });
    return () => controller.abort();
  }, [api, token, workspace.state, retry]);
  const leavePreview = () => {
    clearCouplesInvite();
    nav(BASE, { replace: true });
  };
  return (
    <div className="max-w-lg space-y-5" data-testid="couples-join">
      <Heading>Partner invitation</Heading>
      {workspace.state !== "solo" ? (
        <Panel title="Your current connection">
          <p className={quiet}>Manage your current connection before accepting another invitation.</p>
          <button
            className={secondary}
            onClick={() => {
              clearCouplesInvite();
              nav(BASE + "/partner", { replace: true });
            }}
          >
            Manage connection
          </button>
        </Panel>
      ) : !token ? (
        <Empty
          title="Open your invitation link"
          text="Use the full link your partner gave you in this browser."
          to={BASE}
          action="Back to Couples"
        />
      ) : failure ? (
        <Panel title="Invitation unavailable">
          <p className={quiet}>Check that your link is current, or ask your partner for a new one.</p>
          <button onClick={() => setRetry((r) => r + 1)} className={secondary}>
            Check again
          </button>{" "}
          <button className={subtle} onClick={leavePreview}>
            Dismiss
          </button>
        </Panel>
      ) : !preview ? (
        <p className={quiet} role="status">
          Checking invitation…
        </p>
      ) : (
        <Panel title={"Connect with " + preview.inviter_name + "?"}>
          <p className={quiet}>
            They can confirm your account name after you accept. Your private answers stay yours. You decide
            what to share after the connection is confirmed.
          </p>
          {preview.inviter_code && (
            <p className="text-sm">
              Their connection code · <strong>{preview.inviter_code}</strong>
            </p>
          )}
          <WorkspaceChangeNotice />
          {preview.sponsored === true && <p className={quiet}>Your shared Couples workspace is covered. Each new AI coaching read uses one reply from your partner’s allowance unless you have your own membership.</p>}
          {preview.sponsored === false && <p className={quiet}>This invitation requires each partner to have an active Plus membership.</p>}
          <div className="flex flex-wrap gap-3">
            <button
              className={primary}
              disabled={task.busy || preview.can_join === false}
              data-testid="couples-accept-invite"
              onClick={() =>
                task.run(async () => {
                  await mutate("post", "/couples/invite/accept", { token });
                  clearCouplesInvite();
                  nav(BASE + "/partner", { replace: true });
                })
              }
            >
              Accept invitation
            </button>
            <button
              className={secondary}
              disabled={task.busy}
              onClick={() =>
                task.run(async () => {
                  await mutate("post", "/couples/invite/decline", { token });
                  leavePreview();
                })
              }
            >
              Decline
            </button>
          </div>
          <Feedback task={task} />
        </Panel>
      )}
    </div>
  );
}

function QuestionnaireProfile({ api, refresh, owner }) {
  const { hash } = useLocation();
  const [editing, setEditing] = useState(false);
  const [fields, setFields] = useState(null);
  const [status, setStatus] = useState("");
  const [failure, setFailure] = useState("");
  const [failedEdits, setFailedEdits] = useState({});
  const saved = useRef({});
  const queue = useRef(Promise.resolve());

  useEffect(() => {
    if (hash !== "#my-questionnaire-profile") return;
    document.getElementById("my-questionnaire-profile")?.scrollIntoView?.({ block: "start" });
  }, [hash]);
  async function load() {
    setEditing(true);
    setFailure("");
    try {
      const { data } = await api.get("/couples/profile");
      if (data.user_id !== owner) {
        setFailure("Your signed-in account changed. Reload the page to open your own profile.");
        return;
      }
      saved.current = data.demographics;
      setFields(data.demographics);
    } catch {
      setFailure("Your profile couldn’t load. Try again.");
    }
  }
  function commit(patch) {
    const [key, value] = Object.entries(patch)[0];
    setStatus("Saving…");
    function clearFailure(previous) {
      if (!Object.hasOwn(previous, key)) return previous;
      const next = { ...previous };
      delete next[key];
      return next;
    }
    setFailedEdits(clearFailure);
    queue.current = queue.current.then(async () => {
      if ((saved.current[key] ?? null) !== value) {
        const { data } = await api.patch("/couples/profile", { expected_member: owner, ...patch });
        saved.current = data.demographics;
        if (key === "age" && data.demographics.age < 18) await refresh();
      }
      setFailedEdits(clearFailure);
      setStatus("Saved to your account.");
    }).catch(() => {
      setFailedEdits(previous => ({ ...previous, [key]: patch }));
      setStatus("");
    });
  }
  const retries = Object.values(failedEdits);
  return <Panel title="My questionnaire profile">
    <p className={quiet}>Your Individual and Couples questionnaires use the same profile. Only you can edit these details.</p>
    {!editing ? <button type="button" className={secondary} onClick={load}>Edit my questionnaire profile</button> :
      fields ? <DemoFields own prefix="couples-profile" value={fields} onChange={setFields} onCommit={commit} /> :
        failure ? <button type="button" className={subtle} onClick={load}>Retry loading profile</button> : <p role="status">Loading profile…</p>}
    {failure && <p role="alert" className="text-sm">{failure}</p>}
    {retries.length > 0 && <div role="alert" className="space-y-1 text-sm">
      <p>Your changes to {Object.keys(failedEdits).map(key => key === "height_cm" ? "height" : key).join(", ")} couldn’t be saved.</p>
      <button type="button" className={subtle} onClick={() => retries.forEach(commit)}>Retry saving changes</button>
    </div>}
    {status && retries.length === 0 && <p role="status" className={quiet}>{status}</p>}
    <Link className={subtle} to="/app/settings#questionnaire-research">Questionnaire research preferences</Link>
  </Panel>;
}

function Preferences({ workspace, mutate, refresh }) {
  const task = useAction(refresh);
  const [hours, setHours] = useState(null);
  const start = hours ? hours.start : workspace.quiet_start;
  const end = hours ? hours.end : workspace.quiet_end;
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  function save(body) {
    return task.run(() => mutate("patch", "/couples/preferences", body), "Preferences saved.");
  }
  return (
    <Panel title="My preferences">
      <label className="flex gap-3 items-start text-sm">
        <input
          type="checkbox"
          checked={!!workspace.reminders}
          disabled={task.busy}
          onChange={(e) => save({ reminders: e.target.checked, timezone })}
          className="mt-1"
        />
        <span>Remind me in the app when an agreement is due</span>
      </label>
      {workspace.reminders && (
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            save({ quiet_start: start, quiet_end: end, timezone });
          }}
        >
          <p className={quiet}>Quiet hours · {timezone}</p>
          <div className="flex gap-3 flex-wrap">
            <label className="text-sm">
              Start
              <input
                className={field}
                type="time"
                required
                value={start || "22:00"}
                onChange={(e) => setHours({ start: e.target.value, end: end || "08:00" })}
              />
            </label>
            <label className="text-sm">
              End
              <input
                className={field}
                type="time"
                required
                value={end || "08:00"}
                onChange={(e) => setHours({ start: start || "22:00", end: e.target.value })}
              />
            </label>
            <button className={secondary + " self-end"} disabled={task.busy || !hours}>
              Save quiet hours
            </button>
          </div>
        </form>
      )}
      <div className="border-t border-[var(--line)] pt-3 space-y-2">
        <label className="flex gap-3 items-start text-sm">
          <input
            type="checkbox"
            data-testid="couples-analytics-consent"
            checked={!!workspace.analytics_opt_in}
            disabled={task.busy}
            className="mt-1"
            onChange={(e) => save({ analytics_opt_in: e.target.checked })}
          />
          <span>Include my shared check-in choices in Couples group statistics</span>
        </label>
        <p className={quiet}>
          Both partners must opt in. Only fixed result choices contribute to the group breakdown. You can
          withdraw consent here.
        </p>
        <label className="flex gap-3 items-start text-sm">
          <input type="checkbox" data-testid="couples-review-return-consent" className="mt-1"
            checked={!!workspace.review_return_opt_in} disabled={task.busy}
            onChange={e=>save({review_return_opt_in:e.target.checked})} />
          <span>Include our first scheduled review in return statistics</span>
        </label>
        <p className={quiet}>If you both choose this before agreeing, Imladri counts whether either of you opened the check-in on its due date or in the following seven days, and whether both shared a check-in. No wording is included. Either person can turn this off to remove the pair from future reports.</p>
      </div>
      <Feedback task={task} />
    </Panel>
  );
}

export function CouplesPartner() {
  const { workspace, mutate, refresh, api, me, setMe } = useOutletContext(),
    task = useAction(refresh);
  const [localLink, setLocalLink] = useState(null),
    [leaving, setLeaving] = useState(false),
    [block, setBlock] = useState(false);
  const invite = async () => {
    const data = await mutate("post", "/couples/invite", {});
    setLocalLink({
      pair: data.workspace.pair_id,
      url: window.location.origin + "/couples/invite#" + data.token,
    });
  };
  const active = workspace.state === "active",
    inviter = workspace.side === "inviter";
  const joined = workspace.state === "confirming",
    invited = workspace.state === "invited";
  const shownLink = invited && localLink?.pair === workspace.pair_id && localLink.url;
  return (
    <div className="w-full min-w-0 space-y-5" data-testid="couples-partner">
      <Heading>Partner & settings</Heading>
      <div className="grid items-start gap-5 lg:grid-cols-2" data-testid="couples-partner-layout">
      <div className="min-w-0 space-y-5">
      <Panel
        title={
          active
            ? "Connected with " + workspace.partner_name
            : joined
              ? "Confirm your connection"
              : invited
                ? "Invitation pending"
                : "Invite my partner"
        }
      >
        {!active && <WorkspaceChangeNotice />}
        {workspace.state === "solo" &&
          (workspace.can_sponsor ? (
            <>
              <p className={quiet}>
                {workspace.invite_access?.sponsored_seat_used
                  ? "You’ve already used your sponsored seat. You can connect with another paying member while both memberships are active."
                  : "Your Plus plan covers one confirmed partner in Couples. Each new AI coaching read they request uses one of your replies until they have their own membership and reply allowance."}
                {" "}The link expires after seven days.
              </p>
              <button
                className={primary}
                disabled={task.busy}
                data-testid="couples-create-invite"
                onClick={() => task.run(invite)}
              >
                Create invitation link
              </button>
            </>
          ) : (
            <>
              <p className={quiet}>
                With an active Plus membership, you can invite a partner. You can also join Couples using an invitation from your partner.
              </p>
              <Link className={secondary} to="/app/settings/upgrade">
                See my plan
              </Link>
            </>
          ))}
        {invited && (
          <>
            <p className={quiet}>
              Share the link with the person you intend to connect with. You’ll confirm the account before any
              partner sharing begins.
            </p>
            {shownLink && (
              <>
                <label className="block text-sm">
                  Invitation link
                  <input
                    className={field + " mt-1"}
                    readOnly
                    value={shownLink}
                    data-testid="couples-invite-link"
                    onFocus={(e) => e.target.select()}
                  />
                </label>
                <button
                  className={primary}
                  disabled={task.busy}
                  onClick={() =>
                    task.run(() => navigator.clipboard.writeText(shownLink), "Invitation link copied.")
                  }
                >
                  Copy link
                </button>
              </>
            )}
            {workspace.expires_at && (
              <p className={quiet}>Expires {new Date(workspace.expires_at).toLocaleDateString()}.</p>
            )}
            <button className={secondary} disabled={task.busy} onClick={() => task.run(invite)}>
              Replace invitation link
            </button>
          </>
        )}
        {joined && (
          <>
            <p>
              {inviter
                ? workspace.partner_name + " accepted your invitation. Confirm this is your intended partner."
                : "Your invitation was accepted. Your partner will confirm this connection."}
            </p>
            {inviter && <p className={quiet}>If you sponsor your partner, each new AI coaching read they request uses one of your available messages until they have their own membership.</p>}
            {inviter && (
              <button
                className={primary}
                disabled={task.busy}
                data-testid="couples-confirm-pair"
                onClick={() =>
                  task.run(() => mutate("post", "/couples/confirm", { pair_id: workspace.pair_id }))
                }
              >
                Confirm partner
              </button>
            )}
          </>
        )}
        {active && (
          <>
            <p className={quiet}>
              {workspace.writable
                ? workspace.sponsor_me
                  ? "Your Plus plan covers this shared space. If your partner has no membership of their own, their new AI coaching reads use your available messages too."
                  : workspace.can_sponsor
                    ? "Your partner sponsors this space. Your new AI coaching reads use your own monthly replies."
                    : "Your partner sponsors this space. Your new AI coaching reads use their monthly replies."
                : "The shared space is read-only until a partner with an active Plus plan sponsors it. You can still withdraw shared material or leave."}
            </p>
            {workspace.can_sponsor && !workspace.sponsor_me && (
              <button
                className={secondary}
                disabled={task.busy}
                onClick={() =>
                  task.run(
                    () => mutate("post", "/couples/sponsor", { pair_id: workspace.pair_id }),
                    "Sponsorship updated.",
                  )
                }
              >
                Use my Plus plan
              </button>
            )}
          </>
        )}
        {(joined || active || invited) && workspace.my_code && (
          <p className={quiet}>
            Your connection code · <strong>{workspace.my_code}</strong>
            {workspace.partner_code && (
              <>
                {" "}
                · Their code · <strong>{workspace.partner_code}</strong>
              </>
            )}
          </p>
        )}
        {workspace.pair_id && (
          <div className="space-y-2 pt-2">
            {!leaving ? (
              <button className={subtle} onClick={() => setLeaving(true)}>
                {active ? "Leave this space" : "Cancel connection"}
              </button>
            ) : (
              <>
                <p className="text-sm">
                  {active
                    ? "Leaving removes access to shared work for both of you. Each person keeps their own private drafts."
                    : "Cancel this connection and invalidate the invitation?"}
                </p>
                {(active || joined) && (
                  <label className="block text-sm">
                    <input type="checkbox" checked={block} onChange={(e) => setBlock(e.target.checked)} />{" "}
                    Block future invitations from this account
                  </label>
                )}
                <div className="flex flex-wrap gap-3">
                  <button
                    className={secondary}
                    disabled={task.busy}
                    data-testid="couples-confirm-leave"
                    onClick={() =>
                      task.run(async () => {
                        await mutate("post", "/couples/leave", { pair_id: workspace.pair_id, block });
                        setLeaving(false);
                        setBlock(false);
                        setLocalLink(null);
                      })
                    }
                  >
                    {active ? "Leave shared space" : "Cancel connection"}
                  </button>
                  <button className={subtle} onClick={() => setLeaving(false)}>
                    Keep connection
                  </button>
                </div>
              </>
            )}
          </div>
        )}
        <Feedback task={task} />
      </Panel>
      {workspace.adult && (workspace.can_sponsor || workspace.writable) && <div id="my-questionnaire-profile"><QuestionnaireProfile key={me?.user_id} api={api} refresh={refresh} owner={me?.user_id} /></div>}
      </div>
      <div className="min-w-0 space-y-5">
      {workspace.adult && !workspace.underage && (
        <Preferences workspace={workspace} mutate={mutate} refresh={refresh} />
      )}
      {active && workspace.adult && !workspace.underage && <RecordExportPreference key={`pair-rows-${me?.user_id}-${workspace.pair_id}`}
        scope="couple" me={me} setMe={setMe} api={api} pairId={workspace.pair_id} />}
      {workspace.adult && !workspace.underage && <ToneResearchPreference key={me?.user_id} me={me} setMe={setMe} api={api} />}
      </div>
      </div>
      <div className="flex flex-wrap gap-3">
        <Link className={subtle} to="/app/settings">
          Account, export & deletion
        </Link>
        <Link className={subtle} to={BASE + "/answers"}>
          My answers
        </Link>
      </div>
    </div>
  );
}
