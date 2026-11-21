import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation, useOutletContext } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { errMsg } from "@/lib/api";
import { CouplesMembershipOffer } from "@/components/app/PartnerOffers";
import { couplesOnly } from "@/lib/workspaceAccess";
import {
  BASE,
  Empty,
  Feedback,
  Heading,
  Panel,
  agreementUrl,
  answerUrl,
  futureDate,
  labelDate,
  primary,
  quiet,
  secondary,
  threadUrl,
  useAction,
} from "./CouplesCommon";
export { ConversationCards } from "./CouplesCards";

function AgeConfirmation({ workspace, mutate, refresh }) {
  const task = useAction(refresh);
  const [leaving, setLeaving] = useState(false);
  if (workspace.underage)
    return (
      <div className="max-w-xl space-y-4">
        <Heading>Couples</Heading>
        <Empty
          title="Adults only"
          text="Couples requires both people to be 18 or older."
        />
        <Link to="/app/settings" className={secondary} data-testid="couples-underage-account">
          Account, export & deletion
        </Link>
        {workspace.pair_id && (
          <Panel title="Manage your existing connection" id="couples-underage-connection">
            {!leaving ? (
              <button type="button" className={secondary} onClick={() => setLeaving(true)}
                data-testid="couples-underage-review-leave">
                Review ending connection
              </button>
            ) : (
              <>
                <p id="couples-underage-leave-explanation" className={quiet}>
                  Ending this connection closes the shared workspace for both of you. You each keep your own
                  private questionnaire answers and drafts. This does not cancel either subscription.
                </p>
                <div className="flex flex-wrap gap-3">
                  <button type="button" className={secondary} disabled={task.busy}
                    aria-describedby="couples-underage-leave-explanation" data-testid="couples-underage-confirm-leave"
                    onClick={() => task.run(async () => {
                      await mutate("post", "/couples/leave", { pair_id: workspace.pair_id, block: false });
                      setLeaving(false);
                    }, "Connection ended.")}>
                    End shared workspace for both
                  </button>
                  <button type="button" className={secondary} disabled={task.busy} onClick={() => setLeaving(false)}>
                    Keep connection
                  </button>
                </div>
              </>
            )}
          </Panel>
        )}
        <Feedback task={task} />
      </div>
    );
  return (
    <div className="max-w-xl space-y-5">
      <Heading>Couples</Heading>
      <Panel title="Before you begin">
        <p className={quiet}>
          Both people must be 18 or older to use the partner workspace. Your drafts start private. You choose
          which words to share.
        </p>
        <button
          type="button"
          className={primary}
          disabled={task.busy}
          data-testid="couples-adult-confirm"
          onClick={() => task.run(() => mutate("post", "/couples/adult", { confirmed: true }))}
        >
          I’m 18 or older
        </button>
        <Feedback task={task} />
      </Panel>
    </div>
  );
}

export function Couples() {
  const { api, me, setMe, refreshUsage } = useOutletContext();
  const { pathname } = useLocation();
  const [state, setState] = useState(null),
    [failure, setFailure] = useState("");
  const generation = useRef(0);
  const pendingRead = useRef(null);
  const toneHandoff = useRef(null);
  const lastVisit = useRef("");
  const memberId = me?.user_id || "";
  const alreadyPaired = me?.workspace_access?.version === "workspace-access-v1" && me.workspace_access.mode === "couples";
  const invalidate = useCallback(() => {
    generation.current += 1;
    pendingRead.current?.abort();
  }, []);
  const refresh = useCallback(async () => {
    const request = ++generation.current;
    pendingRead.current?.abort();
    const controller = new AbortController();
    pendingRead.current = controller;
    try {
      const workspaceRequest = api.get("/couples", { signal: controller.signal });
      // A fresh shell already identifies an active pair. Both endpoints still
      // authorize independently, and neither result is rendered until the
      // current workspace and exact journey pair agree below.
      const journeyRequest = alreadyPaired ? api.get("/couples/journey", { signal: controller.signal })
        .then(response => ({ response }), error => ({ error })) : null;
      const { data: workspace } = await workspaceRequest;
      const visitKey = memberId + ":" + new Date().toISOString().slice(0, 10);
      if (request === generation.current && document.visibilityState === "visible" && lastVisit.current !== visitKey) {
        lastVisit.current = visitKey;
        api.post("/me/couples-activity", { action: "opened" }).catch(() => {
          if (lastVisit.current === visitKey) lastVisit.current = "";
        });
      }
      let journey = null;
      if (workspace.adult && !workspace.underage && (workspace.can_sponsor || workspace.state === "active")) {
        let response;
        try {
          if (journeyRequest) {
            const result = await journeyRequest;
            if (result.error) throw result.error;
            response = result.response;
          } else response = await api.get("/couples/journey", { signal: controller.signal });
        } catch (error) {
          if (![402, 403].includes(error.response?.status)) throw error;
          if (request === generation.current) {
            setState({ workspace, journey: null });
            setFailure("");
          }
          return;
        }
        journey = response.data;
        if ((journey.pair_id || null) !== (workspace.state === "active" ? workspace.pair_id : null)) {
          // A different device confirmed or closed the pair between these reads.
          const latest = await api.get("/couples", { signal: controller.signal });
          if ((journey.pair_id || null) !== (latest.data.state === "active" ? latest.data.pair_id : null))
            throw new Error("Workspace changed");
          if (request === generation.current) {
            setState({ workspace: latest.data, journey });
            setFailure("");
          }
          return;
        }
      }
      if (request === generation.current) {
        setState({ workspace, journey });
        setFailure("");
      }
    } catch (error) {
      if (request === generation.current) {
        setState(null);
        setFailure(errMsg(error));
      }
    } finally {
      controller.abort();
      if (pendingRead.current === controller) pendingRead.current = null;
    }
  }, [api, memberId, alreadyPaired]);
  useEffect(() => {
    refresh();
    const visible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    const interval = setInterval(visible, 20000);
    window.addEventListener("focus", visible);
    window.addEventListener("pageshow", visible);
    document.addEventListener("visibilitychange", visible);
    return () => {
      invalidate();
      clearInterval(interval);
      window.removeEventListener("focus", visible);
      window.removeEventListener("pageshow", visible);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [refresh, invalidate]);
  async function mutate(method, path, body) {
    const response = await api.request({ method, url: path, data: body });
    await refresh();
    return response.data;
  }
  async function actOnThread(id, action, payload = {}) {
    const thread = state.journey?.threads.find((item) => item.id === id);
    const { expectedVersion, ...fields } = payload;
    return mutate("post", "/couples/threads/" + encodeURIComponent(id), {
      ...fields,
      action,
      pair_id: state.workspace.pair_id,
      version: expectedVersion ?? thread?.version ?? state.journey?.share_versions?.[id] ?? 0,
      request_id: crypto.randomUUID(),
    });
  }
  if (failure)
    return (
      <div className="max-w-lg space-y-3">
        <Heading>Couples</Heading>
        <p role="alert" className={quiet}>
          The partner workspace couldn’t be loaded.
        </p>
        <button className={secondary} onClick={refresh}>
          Try again
        </button>
      </div>
    );
  if (!state)
    return (
      <p className={quiet} role="status">
        Loading Couples…
      </p>
    );
  const route = pathname.replace(/\/$/, "");
  const connectionSettings = route === BASE + "/partner";
  const joining = route === BASE + "/join";
  if (state.workspace.underage)
    return <AgeConfirmation {...state} mutate={mutate} refresh={refresh} />;
  const covered = state.workspace.can_sponsor || state.workspace.state === "active";
  if (!covered && !joining && !connectionSettings) return (
    <div className="max-w-xl space-y-4">
      <Heading>Couples</Heading>
      <Empty title={state.workspace.state === "confirming" ? "Waiting for confirmation" : "Connect with your partner"}
        text={state.workspace.state === "confirming" ? "The shared space opens when the inviter confirms the connection." : "One Plus membership covers the shared workspace. If your partner invited you, open the invitation link they sent."}
        to={state.workspace.pair_id ? BASE + "/partner" : "/app/settings/upgrade"}
        action={state.workspace.pair_id ? "View connection" : "View membership"} />
    </div>
  );
  if (
    !state.workspace.adult &&
    !(state.workspace.pair_id && connectionSettings)
  )
    return <AgeConfirmation {...state} mutate={mutate} refresh={refresh} />;
  if (route === BASE + "/conversation-cards" && state.journey) {
    if (!state.workspace.can_sponsor && !state.workspace.writable) return (
      <Empty title="Your shared space is read-only" text="A current Plus sponsor is required to start a new exercise." to={BASE + "/partner"} action="Review sponsorship" />
    );
  }
  if (!state.journey && !connectionSettings && !joining)
    return (
      <Empty
        title="Shared space unavailable"
        text="Open partner settings to review or leave your connection."
        to={BASE + "/partner"}
        action="Open partner settings"
      />
    );
  return (
    <Outlet
      key={
        (me?.user_id || "") + ":" + (state.workspace.state === "active" ? state.workspace.pair_id : "private")
      }
      context={{ ...state, me, setMe, api, refresh, refreshUsage, mutate, actOnThread, toneHandoff }}
    />
  );
}

export function CouplesOverview() {
  const { api, me, workspace, journey } = useOutletContext();
  const threads = journey.threads;
  const agreements = threads.flatMap((thread) => thread.agreements.map((value) => ({ thread, value })));
  const today = futureDate(0);
  const pendingReviews = agreements
    .filter(({ value }) => value.status === "active" && !value.my_checkin?.shared)
    .sort((a, b) => a.value.review_on.localeCompare(b.value.review_on));
  const open = pendingReviews.find(({ value }) => value.review_on <= today);
  const upcoming = pendingReviews.find(({ value }) => value.review_on > today);
  const proposal = agreements.find(({ value }) => value.status === "proposed" && !value.accepted_by_me);
  const response = threads.find((thread) => thread.partner_shared && !thread.my_share?.length);
  const mutual = threads.find((thread) => thread.mutual && !thread.agreements.length);
  const draft = journey.drafts[0];
  const pendingCount = pendingReviews.filter(({ value }) => value.review_on <= today).length
    + agreements.filter(({ value }) => value.status === "proposed" && !value.accepted_by_me).length
    + threads.filter(thread => thread.partner_shared && !thread.my_share?.length).length;
  const firstShared = threads.find(thread => thread.mutual) || threads[0];
  const firstAgreement = firstShared?.agreements.find(value => value.status === "active" || value.status === "retired");
  const progress = firstAgreement?.partner_checkin && firstAgreement?.my_checkin?.shared ? "Both check-ins shared"
    : firstAgreement ? "Agreement accepted" : firstShared?.mutual ? "Both answers shared"
    : firstShared ? "One answer shared" : draft ? "Private answer saved" : "Choose a conversation";
  let next = {
    title: "Start with one question",
    hint: "Pick an everyday preference or write about a recent moment. Save your answer privately.",
    to: BASE + "/answers",
    label: "Start my answers",
  };
  if (open)
    next = {
      title: "Check in on your agreement",
      hint: open.value.text,
      to: agreementUrl(open.thread.id, open.value),
      label: "Open agreement",
    };
  else if (proposal)
    next = {
      title: "Review a proposed agreement",
      hint: proposal.value.text,
      to: agreementUrl(proposal.thread.id, proposal.value),
      label: "Review proposal",
    };
  else if (response)
    next = {
      title: "A conversation is open",
      hint: response.title,
      to: answerUrl(response.id),
      label: "Add my perspective",
    };
  else if (workspace.state === "confirming" && workspace.side === "inviter")
    next = {
      title: "Confirm your partner",
      hint: "Check the name of the account that accepted your invitation.",
      to: BASE + "/partner",
      label: "Review connection",
    };
  else if (mutual)
    next = {
      title: "See both perspectives",
      hint: mutual.title,
      to: threadUrl(mutual.id),
      label: "Open conversation",
    };
  else if (upcoming)
    next = {
      title: "Your next check-in",
      hint: upcoming.value.text + " · " + labelDate(upcoming.value.review_on),
      to: agreementUrl(upcoming.thread.id, upcoming.value),
      label: "View agreement",
    };
  else if (draft && workspace.state === "solo")
    next = {
      title: "Your first answer is saved",
      hint: "Connect with your partner when you’re ready to share a conversation.",
      to: BASE + "/partner",
      label: "Invite my partner",
    };
  else if (draft)
    next = {
      title: "Your private answers",
      hint: "Choose a saved answer to revisit or share.",
      to: BASE + "/answers",
      label: "Open my answers",
    };
  return (
    <div className="max-w-4xl space-y-5" data-testid="couples-overview">
      <Heading
        note={
          workspace.state === "active"
            ? "Your shared space with " + workspace.partner_name + "."
            : "Start privately, then choose what you want to explore with your partner."
        }
      >
        Couples
      </Heading>
      {workspace.state === "active" && !couplesOnly(me) && <CouplesMembershipOffer api={api} />}
      {workspace.state === "active" && <Panel title="Understand yourselves, then compare">
        <p className={quiet}>Answer personality and intimacy questions privately. Compatibility uses only the categories both of you choose to share.</p>
        <div className="flex flex-wrap gap-3">
          <Link to={BASE + "/personal"} className={secondary}>My assessments</Link>
          <Link to={BASE + "/compatibility"} className={secondary}>Compatibility</Link>
        </div>
      </Panel>}
      {workspace.state === "active" && <p className={quiet}>
        {firstShared ? firstShared.title + " · " : ""}{progress} · {pendingCount} {pendingCount === 1 ? "item needs" : "items need"} your attention
      </p>}
      <Panel title={next.title}>
        <p className={quiet}>{next.hint}</p>
        <Link to={next.to} className={primary}>
          {next.label}
          <ArrowRight size={16} aria-hidden="true" />
        </Link>
      </Panel>
      <div className="grid sm:grid-cols-2 gap-3">
        <Panel title={workspace.state === "active" ? "Your partner" : "Partner invitation"}>
          <p className={quiet}>
            {workspace.state === "active"
              ? workspace.partner_name
              : workspace.state === "solo"
                ? "Invite someone to join with their own account."
                : "An invitation is in progress."}
          </p>
          <Link to={BASE + "/partner"} className={secondary}>
            Partner & settings
          </Link>
        </Panel>
        <Panel title="Conversation cards">
          <p className={quiet}>Write your own answers, agree on one change and check back together. You can read a sample first.</p>
          <Link to={BASE + "/conversation-cards"} className={secondary} data-testid="couples-open-cards">
            Explore conversation cards
          </Link>
        </Panel>
      </div>
    </div>
  );
}
