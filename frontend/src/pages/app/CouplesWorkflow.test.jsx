import { act } from "react";
import { createRoot } from "react-dom/client";
import { useLocation, useOutletContext } from "react-router-dom";
import { clearCouplesInvite, pendingCouplesInvite, storeCouplesInvite } from "@/lib/couplesInvite";
import { CouplesAnswers } from "./CouplesAnswers";
import { CouplesPartner, JoinCouples } from "./CouplesPartner";
import { CouplesTogether, CouplesAgreements, CouplesLearnings } from "./CouplesTogether";
import { Couples } from "./Couples";

jest.mock("react-router-dom", () => ({
  Link: ({ children, to, ...props }) => (
    <a {...props} href={to}>
      {children}
    </a>
  ),
  useOutletContext: jest.fn(),
  useNavigate: () => jest.fn(),
  useLocation: jest.fn(),
  Outlet: () => <div data-testid="couples-nested-outlet">Nested Couples content</div>,
}));
jest.mock("@/lib/api", () => ({ errMsg: e => e.response?.data?.detail || "Request failed" }));

const id = "moment-151507f7-2d1b-43e4-8f12-d996ea970afb";
let host, root, ctx;
const el = (testid) => host.querySelector('[data-testid="' + testid + '"]');
const click = async (target) =>
  act(async () => {
    target.click();
  });
const byText = (text) => [...host.querySelectorAll("button")].find((b) => b.textContent === text);
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  global.crypto = { randomUUID: require("crypto").randomUUID };
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  ctx = {
    api: { get: jest.fn() },
    workspace: { state: "active", partner_name: "Jordan", writable: true, pair_id: "pairA" },
    refresh: jest.fn(),
    refreshUsage: jest.fn(),
    mutate: jest.fn(),
    actOnThread: jest.fn(),
    journey: {
      pair_id: "pairA",
      prompts: [],
      drafts: [{ key: id, version: 1, lines: ["Only me", "This line is okay", ""] }],
      share_versions: { [id]: 4 },
      threads: [],
    },
  };
  useOutletContext.mockImplementation(() => ctx);
  useLocation.mockReturnValue({ search: "?item=" + id });
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  clearCouplesInvite();
});

test("the wider My answers layout retains every available topic and private draft exactly once", async () => {
  useLocation.mockReturnValue({ pathname: "/app/couples/answers", search: "" });
  ctx.journey.prompts = ["communication", "repair", "support", "priorities"].map((topic, i) => ({
    id: "question-" + i, topic, question: "Available question " + i,
  }));
  ctx.journey.prompts.push({ id: "retired", topic: "support", question: "Retired prompt", retired: true },
    { id: "card-only", topic: "priorities", question: "Separate conversation card", card: "repair" });
  await act(async () => root.render(<CouplesAnswers />));
  for (let i = 0; i < 4; i++) {
    expect(host.querySelectorAll(`[data-testid="couples-answer-question-${i}"]`)).toHaveLength(1);
    expect(el("couples-answer-question-" + i).getAttribute("href")).toBe("/app/couples/answers?item=question-" + i);
  }
  expect(el("couples-answer-more-topics").contains(el("couples-answer-question-2"))).toBe(true);
  expect(el("couples-answer-more-topics").contains(el("couples-answer-question-3"))).toBe(true);
  expect(host.textContent).not.toContain("Retired prompt");
  expect(host.textContent).not.toContain("Separate conversation card");
  expect(host.querySelectorAll(`a[href="/app/couples/answers?item=${id}"]`)).toHaveLength(1);
  expect(el("couples-new-moment")).not.toBeNull();
  expect(el("couples-new-positive")).not.toBeNull();
  expect(ctx.mutate).not.toHaveBeenCalled();
  expect(ctx.api.get).not.toHaveBeenCalled();
});

test("the wider Partner settings keeps connection controls separate from unchanged optional preferences", async () => {
  useLocation.mockReturnValue({ pathname: "/app/couples/partner", search: "", hash: "" });
  ctx.me = { user_id: "member", role: "member" };
  ctx.workspace = { ...ctx.workspace, adult: true, underage: false, can_sponsor: true,
    sponsor_me: true, reminders: false, analytics_opt_in: false, review_return_opt_in: false };
  ctx.mutate.mockResolvedValue({});
  await act(async () => root.render(<CouplesPartner />));
  expect(byText("Edit my questionnaire profile")).not.toBeUndefined();
  expect(byText("Leave this space")).not.toBeUndefined();
  expect(el("couples-analytics-consent").checked).toBe(false);
  expect(el("couples-review-return-consent").checked).toBe(false);
  expect(ctx.mutate).not.toHaveBeenCalled();
  await click(el("couples-analytics-consent"));
  expect(ctx.mutate.mock.calls).toEqual([["patch", "/couples/preferences", { analytics_opt_in: true }]]);
  expect(el("couples-review-return-consent").checked).toBe(false);
  expect(host.querySelector('a[href="/app/settings"]')).not.toBeNull();
});

test.each(["/app/couples", "/app/couples/partner", "/app/couples/partner/"])(
  "an underage paired member at %s can explicitly end the connection without adult or paid actions", async pathname => {
    let workspace = { state: "active", pair_id: "pairA", adult: true, underage: true, can_sponsor: true };
    ctx.me = { user_id: "age-corrected-member" };
    ctx.api.get.mockImplementation(async path => {
      expect(path).toBe("/couples");
      return { data: workspace };
    });
    ctx.api.post = jest.fn().mockResolvedValue({ data: {} });
    ctx.api.request = jest.fn(async request => {
      expect(request).toEqual({ method: "post", url: "/couples/leave", data: { pair_id: "pairA", block: false } });
      workspace = { state: "solo", pair_id: null, adult: false, underage: true, can_sponsor: false };
      return { data: workspace };
    });
    useLocation.mockReturnValue({ pathname, search: "" });
    await act(async () => root.render(<Couples />));
    expect(host.textContent).toContain("Adults only");
    expect(el("couples-nested-outlet")).toBeNull();
    expect(el("couples-underage-account").getAttribute("href")).toBe("/app/settings");
    expect(ctx.api.request).not.toHaveBeenCalled();
    expect(el("couples-underage-confirm-leave")).toBeNull();
    await click(el("couples-underage-review-leave"));
    expect(ctx.api.request).not.toHaveBeenCalled();
    expect(host.textContent).toContain("closes the shared workspace for both");
    expect(host.textContent).toContain("private questionnaire answers and drafts");
    expect(host.textContent).toContain("does not cancel either subscription");
    await click(byText("Keep connection"));
    expect(ctx.api.request).not.toHaveBeenCalled();
    await click(el("couples-underage-review-leave"));
    await click(el("couples-underage-confirm-leave"));
    expect(ctx.api.request).toHaveBeenCalledTimes(1);
    expect(ctx.api.get).toHaveBeenCalledTimes(2);
    expect(el("couples-underage-connection")).toBeNull();
    expect(el("couples-underage-account")).not.toBeNull();
    expect(host.textContent).toContain("Connection ended.");
    expect(host.textContent).not.toContain("I’m 18 or older");
    expect(host.textContent).not.toContain("View membership");
    expect(ctx.api.post.mock.calls.every(([path]) => path === "/me/couples-activity")).toBe(true);
  },
);

test("underage members without a pair retain Settings and export access without a membership detour", async () => {
  ctx.me = { user_id: "underage-unpaired" };
  ctx.api.get.mockResolvedValue({ data: { state: "solo", adult: false, underage: true, can_sponsor: false } });
  ctx.api.post = jest.fn().mockResolvedValue({ data: {} });
  ctx.api.request = jest.fn();
  useLocation.mockReturnValue({ pathname: "/app/couples", search: "" });
  await act(async () => root.render(<Couples />));
  expect(host.textContent).toContain("Adults only");
  expect(el("couples-underage-account").getAttribute("href")).toBe("/app/settings");
  expect(el("couples-underage-review-leave")).toBeNull();
  expect(host.textContent).not.toContain("View membership");
  expect(ctx.api.request).not.toHaveBeenCalled();
});

test("an uncertain leave result keeps account access and does not automatically retry or confirm adulthood", async () => {
  ctx.me = { user_id: "underage-paired" };
  ctx.api.get.mockResolvedValue({ data: { state: "active", pair_id: "pairA", adult: false, underage: true } });
  ctx.api.post = jest.fn().mockResolvedValue({ data: {} });
  ctx.api.request = jest.fn().mockRejectedValue(new Error("Connection lost"));
  useLocation.mockReturnValue({ pathname: "/app/couples", search: "" });
  await act(async () => root.render(<Couples />));
  await click(el("couples-underage-review-leave"));
  await click(el("couples-underage-confirm-leave"));
  expect(host.querySelector('[role="alert"]').textContent).toBe("Request failed");
  expect(ctx.api.request).toHaveBeenCalledTimes(1);
  expect(el("couples-underage-account")).not.toBeNull();
  expect(el("couples-underage-confirm-leave").disabled).toBe(false);
  expect(host.textContent).not.toContain("I’m 18 or older");
});

test("an adult Plus member can create an invitation even with cached enrollment data from the earlier release", async () => {
  ctx.workspace = { state: "solo", adult: true, can_sponsor: true, invite_access: { admitted: false } };
  ctx.mutate.mockResolvedValue({ token: "example-token", workspace: { pair_id: "one-pair" } });
  await act(async () => root.render(<CouplesPartner />));
  expect(el("couples-workspace-change-notice").textContent).toContain("both accounts switch to Couples only");
  expect(host.textContent).not.toContain("add Individual for $10");
  await click(el("couples-create-invite"));
  expect(ctx.mutate).toHaveBeenCalledWith("post", "/couples/invite", {});
});

test.each([true, false])("sponsored=%s invitation previews disclose the shared workspace restriction before accepting", async sponsored => {
  const token = "synthetic-invitation-token-at-least-24";
  expect(storeCouplesInvite(token)).toBe(true);
  ctx.workspace = { state: "solo", adult: true, can_sponsor: !sponsored };
  ctx.api.post = jest.fn(async () => ({ data: { sponsored, inviter_name: "Jordan", can_join: true } }));
  await act(async () => root.render(<JoinCouples />));
  const notice = el("couples-workspace-change-notice");
  expect(notice.textContent).toContain("After the inviter’s final confirmation");
  expect(notice.textContent).toContain("both accounts switch to Couples only");
  expect(notice.textContent).toContain("Individual and Your Type are unavailable");
  expect(notice.textContent).toContain("Your private history is retained");
  expect(notice.textContent).toContain("existing membership or complimentary entitlement");
  expect(el("couples-accept-invite")).not.toBeNull();
  expect(ctx.mutate).not.toHaveBeenCalled();
  await click(el("couples-accept-invite"));
  expect(ctx.mutate).toHaveBeenCalledWith("post", "/couples/invite/accept", { token });
  expect(ctx.mutate.mock.calls.some(([, path]) => path === "/couples/confirm")).toBe(false);
});

test("the inviter sees the access warning before a separate final confirmation", async () => {
  ctx.workspace = { state: "confirming", side: "inviter", partner_name: "Jordan", pair_id: "pairA" };
  await act(async () => root.render(<CouplesPartner />));
  expect(el("couples-workspace-change-notice").textContent).toContain("sharing answers is a separate choice");
  expect(el("couples-workspace-change-notice").textContent).toContain("Your Type are unavailable");
  expect(ctx.mutate).not.toHaveBeenCalled();
  await click(el("couples-confirm-pair"));
  expect(ctx.mutate).toHaveBeenCalledWith("post", "/couples/confirm", { pair_id: "pairA" });
});

test("a private draft becomes a snapshot only after previewing and authorizing exact selected lines", async () => {
  await act(async () => root.render(<CouplesAnswers />));
  expect(ctx.actOnThread).not.toHaveBeenCalled();
  await click(el("couples-preview-share"));
  expect(host.textContent).toContain("What Jordan will see");
  await click(host.querySelector('input[type="checkbox"]'));
  await click(el("couples-confirm-share"));
  expect(ctx.actOnThread).toHaveBeenCalledWith(id, "share", {
    kind: "moment",
    lines: ["This line is okay"],
    expectedVersion: 4,
  });
  expect(ctx.api.get).not.toHaveBeenCalled();
});

test("a version change during the sharing preview requires a new review", async () => {
  await act(async () => root.render(<CouplesAnswers />));
  await click(el("couples-preview-share"));
  ctx = { ...ctx, journey: { ...ctx.journey, share_versions: { [id]: 5 } } };
  await act(async () => root.render(<CouplesAnswers />));
  expect(el("couples-confirm-share").disabled).toBe(true);
  expect(ctx.actOnThread).not.toHaveBeenCalled();
});

function mockTone(index) {
  const feedback = {};
  ctx.api.put = jest.fn().mockResolvedValue({});
  ctx.api.post = jest.fn(async () => ({ data: { id: "coach-id", tone_feedback: {}, tone: {
    status: "estimated", incoming: "warm", explanation: "The wording invites a response.", evidence: [index],
    drafts: { direct: "Can we talk after dinner?", warm: "I’d like to talk after dinner." },
  } } }));
  ctx.api.patch = jest.fn(async (_path, { change }) => {
    if (change.action === "select") feedback.style = change.style;
    return { data: { ...feedback } };
  });
}

test("private coaching previews only the chosen saved line, offers styles, and leaves sharing to its author", async () => {
  mockTone(0);
  await act(async () => root.render(<CouplesAnswers />));
  await click(byText("How might my draft come across?"));
  expect(host.textContent).toContain("one message from your sponsoring partner’s balance");
  const select = el("couples-tone").querySelector("select");
  await act(async () => { select.value = "1"; select.dispatchEvent(new Event("change", { bubbles: true })); });
  await click(byText("Read my tone"));
  expect(ctx.api.post).toHaveBeenCalledWith("/couples/tone/private/" + id, {
    pair_id: "pairA", version: 1, line: 1, goal: "",
  }, { headers: { "Idempotency-Key": expect.any(String) } });
  expect(ctx.refreshUsage).toHaveBeenCalledTimes(1);
  expect(el("tone-coach").textContent).toContain("This line is okay");
  expect(el("tone-coach").textContent).not.toContain("Only me");
  expect(host.textContent).not.toContain("Can we talk after dinner?");
  await click(byText("Direct"));
  expect(host.textContent).toContain("Can we talk after dinner?");
  await click(byText("Use this wording"));
  expect(el("couples-draft-line-0").value).toBe("Only me");
  expect(el("couples-draft-line-1").value).toBe("Can we talk after dinner?");
  expect(ctx.actOnThread).not.toHaveBeenCalled();
  expect(ctx.api.post).toHaveBeenCalledTimes(1);
  expect(ctx.refreshUsage).toHaveBeenCalledTimes(1);
});

test("switching draft lines while confirming wording leaves both private lines as written", async () => {
  mockTone(0);
  await act(async () => root.render(<CouplesAnswers />));
  await click(byText("How might my draft come across?"));
  await click(byText("Read my tone"));
  await click(byText("Direct"));
  let finish;
  ctx.api.patch.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await click(byText("Use this wording"));
  const select = el("couples-tone").querySelector("select");
  await act(async () => { select.value = "1"; select.dispatchEvent(new Event("change", { bubbles: true })); });
  await act(async () => finish({ data: { style: "direct" } }));
  expect(el("couples-draft-line-0").value).toBe("Only me");
  expect(el("couples-draft-line-1").value).toBe("This line is okay");
  expect(ctx.actOnThread).not.toHaveBeenCalled();
});

test("paid members can check for a saved read after their last reply without attempting a new charge", async () => {
  ctx.workspace.can_sponsor = true;
  ctx.me = { usage: { messages_remaining: 0 } };
  mockTone(0);
  ctx.api.post.mockResolvedValueOnce({ data: { available: false } });
  await act(async () => root.render(<CouplesAnswers />));
  await click(byText("How might my draft come across?"));
  expect(host.textContent).toContain("A new read uses one message from your balance");
  expect(host.textContent).toContain("Your balance is zero");
  expect(byText("Read my tone")).toBeUndefined();
  await click(byText("Check for a saved read · free"));
  expect(host.textContent).toContain("No saved read matches this selection");
  await click(byText("Check for a saved read · free"));
  expect(ctx.api.post).toHaveBeenLastCalledWith("/couples/tone/private/" + id + "/saved", {
    pair_id:"pairA", version:1, line:0, goal:"",
  });
  expect(ctx.refreshUsage).not.toHaveBeenCalled();
  expect(el("tone-coach")).not.toBeNull();
});

test("a sponsored member sees a shared limit without a billing link and can continue editing their own answers", async () => {
  const detail = "The shared message allowance is used up for this billing period.";
  ctx.workspace.can_sponsor = false;
  mockTone(0);
  ctx.api.post.mockRejectedValueOnce({ response: { status: 402, data: { detail } } });
  await act(async () => root.render(<CouplesAnswers />));
  await click(byText("How might my draft come across?"));
  await click(byText("Read my tone"));
  expect(el("couples-tone").querySelector('[role="alert"]').textContent).toBe(detail);
  expect(el("couples-tone").querySelector("a")).toBeNull();
  expect(el("couples-draft-line-0").disabled).toBe(false);
  expect(ctx.refresh).toHaveBeenCalledTimes(1);
});

test("a shared coach belongs to the viewer and only crosses into My answers after the viewer chooses it", async () => {
  mockTone(1);
  ctx.me = { user_id: "me" };
  ctx.toneHandoff = { current: null };
  ctx.journey.threads = [{ id, kind: "moment", title: "A conversation", my_share: ["My words"],
    mutual: true, version: 4, words_version: 4, agreements: [], perspectives: [
      { mine: true, lines: ["My words"] }, { mine: false, lines: ["Can we talk about this?"] },
    ] }];
  await act(async () => root.render(<CouplesTogether />));
  await click(byText("How does this exchange read?"));
  expect(ctx.api.post).not.toHaveBeenCalled();
  await click(byText("Read the exchange"));
  expect(el("tone-coach").textContent).toContain("Can we talk about this?");
  ctx.journey.threads[0].version = 5; // The partner updates an agreement, keeping the same shared words.
  await act(async () => root.render(<CouplesTogether />));
  expect(el("tone-coach").textContent).toContain("Can we talk about this?");
  expect(ctx.api.post).toHaveBeenCalledTimes(1);
  expect(host.textContent).not.toContain("Can we talk after dinner?");
  await click(byText("Direct"));
  await click(byText("Use this wording"));
  expect(ctx.toneHandoff.current).toMatchObject({ key: id, member: "me", pair: "pairA", version: 4, text: "Can we talk after dinner?" });
  await act(async () => root.render(<CouplesAnswers />));
  expect(host.textContent).toContain("Suggested reply for my private draft");
  expect(el("couples-draft-line-0").value).toBe("Only me");
  expect(ctx.toneHandoff.current).toBe(null);
  await click([...host.querySelectorAll("button")].find(b => b.textContent.startsWith("Use for")));
  expect(el("couples-draft-line-0").value).toBe("Can we talk after dinner?");
  expect(ctx.actOnThread).not.toHaveBeenCalled();
});

test("an author can intentionally restart a previously deleted draft", async () => {
  ctx.journey.drafts = [];
  ctx.journey.draft_versions = { [id]: 8 };
  ctx.mutate.mockResolvedValue({ drafts: [{ key: id, version: 9, lines: ["Restarted", "", ""] }] });
  await act(async () => root.render(<CouplesAnswers />));
  const field = el("couples-draft-line-0");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(field, "Restarted");
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click(el("couples-save-draft"));
  expect(ctx.mutate).toHaveBeenCalledWith("put", "/couples/drafts/" + id, {
    lines: ["Restarted", "", ""],
    version: 8,
  });
});

test("selecting a starter records the option immediately and keeps independently written words", async () => {
  const key = "support-1";
  useLocation.mockReturnValue({ search: "?item=" + key });
  ctx.me = { user_id: "member" };
  ctx.journey = { ...ctx.journey, prompts: [{ id: key, question: "What would help?", choices: ["Listening", "Quiet company"] }],
    drafts: [{ key, version: 1, lines: ["My own words"], choice: null }] };
  ctx.mutate.mockResolvedValue({ drafts: [{ key, version: 2, lines: ["My own words"], choice: "Listening" }] });
  ctx.api.put = jest.fn().mockResolvedValue({});
  await act(async () => root.render(<CouplesAnswers />));
  await click(byText("Listening"));
  expect(ctx.mutate).toHaveBeenCalledWith("put", "/couples/drafts/" + key, {
    choice: "Listening", lines: ["My own words"], version: 1, expected_member: "member",
  });
  expect(ctx.actOnThread).not.toHaveBeenCalled();
});

test("the sponsored partner can edit height from their own Couples profile", async () => {
  ctx.workspace = { ...ctx.workspace, adult: true, can_sponsor: false, side: "invitee" };
  ctx.me = { user_id: "guest" };
  ctx.api.get.mockResolvedValue({ data: { user_id: "guest", demographics: { age: 31 } } });
  ctx.api.patch = jest.fn().mockRejectedValueOnce(new Error("Offline")).mockResolvedValue({ data: { demographics: { age: 31, height_cm: 178 } } });
  await act(async () => root.render(<CouplesPartner />));
  await click(byText("Edit my questionnaire profile"));
  expect(ctx.api.get).toHaveBeenCalledWith("/couples/profile");
  const input = el("couples-profile-height");
  expect(el("couples-profile-gender")).not.toBeNull();
  expect(el("couples-profile-ethnicity")).not.toBeNull();
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "178");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => input.dispatchEvent(new Event("focusout", { bubbles: true })));
  expect(ctx.api.patch).toHaveBeenNthCalledWith(1, "/couples/profile", { expected_member: "guest", height_cm: 178 });
  expect(host.textContent).toContain("height couldn’t be saved.");
  await click(byText("Retry saving changes"));
  expect(ctx.api.patch).toHaveBeenNthCalledWith(2, "/couples/profile", { expected_member: "guest", height_cm: 178 });
  expect(host.textContent).toContain("Saved to your account.");
});

test("leaving a reflection flushes the last edit into the author's private draft", async () => {
  ctx.me = { user_id: "member" };
  ctx.api.put = jest.fn().mockResolvedValue({});
  await act(async () => root.render(<CouplesAnswers />));
  const input = el("couples-draft-line-2");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, "My last thought");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => root.render(<div />));
  expect(ctx.api.put).toHaveBeenCalledWith("/couples/drafts/" + id, {
    version: 1, lines: ["Only me", "This line is okay", "My last thought"], expected_member: "member",
  });
  expect(ctx.actOnThread).not.toHaveBeenCalled();
});

test("a waiting thread invites a private answer; the partner's words appear only after mutual release", async () => {
  const thread = {
    id,
    kind: "moment",
    title: "A recent moment",
    discussion: "What could help?",
    version: 3,
    mine: false,
    partner_shared: true,
    my_share: [],
    perspectives: [],
    mutual: false,
    agreements: [],
  };
  ctx.journey.threads = [thread];
  await act(async () => root.render(<CouplesTogether />));
  expect(host.textContent).toContain("Answer privately");
  expect(host.textContent).not.toContain("Jordan’s words");
  ctx.journey.threads = [
    {
      ...thread,
      mutual: true,
      my_share: ["A"],
      perspectives: [
        { mine: true, lines: ["A"] },
        { mine: false, lines: ["B"] },
      ],
    },
  ];
  await act(async () => root.render(<CouplesTogether />));
  expect(host.textContent).toContain("Jordan’s wordsB");
  expect(host.textContent).toContain("Try an agreement");
});

test("check-in sharing requires an explicit preview and sends only the selected result and note", async () => {
  const agreement = {
    id: "ag",
    revision: 1,
    text: "Talk after dinner",
    status: "active",
    review_on: "2027-02-02",
    accepted_by_me: true,
    accepted_by_partner: true,
    my_checkin: { result: "mixed", note: "Next week", shared: false },
    partner_checkin: null,
  };
  ctx.journey.threads = [{ id, title: "Moment", mutual: true, version: 5, agreements: [agreement] }];
  await act(async () => root.render(<CouplesAgreements />));
  expect(el("couples-share-checkin")).toBeNull();
  await click(byText("Preview shared check-in"));
  await click(el("couples-share-checkin"));
  expect(ctx.actOnThread).toHaveBeenCalledWith(id, "checkin", {
    agreement_id: "ag",
    result: "mixed",
    note: "Next week",
    share: true,
    expectedVersion: 5,
  });
});

test("a partner must approve the proposed learning before it is jointly saved", async () => {
  ctx.journey.threads = [
    {
      id,
      title: "Moment",
      mutual: true,
      version: 6,
      agreements: [
        {
          id: "ag",
          review_on: "2027-02-02",
          text: "Choose a time",
          pin: {
            text: "We value a heads-up",
            accepted_by_me: false,
            accepted_by_partner: true,
            active: false,
          },
        },
      ],
    },
  ];
  await act(async () => root.render(<CouplesLearnings />));
  expect(host.textContent).toContain("Jordan suggested saving this learning");
  await click(el("couples-accept-pin"));
  expect(ctx.actOnThread).toHaveBeenCalledWith(id, "accept_pin", { agreement_id: "ag" });
});

test("a reminder shows only the named revision and a retired link never opens a different agreement", async () => {
  const agreement = {id:"second", revision:2, text:"Friday heads-up", status:"active", review_on:"2027-02-02", accepted_by_me:true, accepted_by_partner:true};
  ctx.journey.threads = [{id, title:"Timing", mutual:true, version:4, agreements:[{...agreement,id:"first",text:"Unrelated dinner"},agreement]}];
  useLocation.mockReturnValue({search:"?item="+id+"&agreement=second&revision=2"});
  await act(async()=>root.render(<CouplesAgreements />));
  expect(host.textContent).toContain("Friday heads-up");
  expect(host.textContent).not.toContain("Unrelated dinner");
  useLocation.mockReturnValue({search:"?item="+id+"&agreement=second&revision=1"});
  await act(async()=>root.render(<CouplesAgreements />));
  expect(host.textContent).toContain("This agreement version is unavailable");
  expect(host.textContent).not.toContain("Friday heads-up");
});

test("reusing an accepted learning opens a fresh proposal and retains the original", async () => {
  const ag = {id:"first-agreement",revision:1,status:"active",text:"Original",review_on:"2027-03-01",pin:{active:true, accepted_by_me:true, accepted_by_partner:true,text:"Check the calendar first"}};
  ctx.journey.threads=[{id,title:"Scheduling",kind:"moment",mutual:true,version:7,agreements:[ag]}];
  await act(async()=>root.render(<CouplesLearnings />));
  await click(byText("Try this learning again"));
  expect(ctx.actOnThread).not.toHaveBeenCalled();
  expect(el("couples-agreement-text").value).toBe("Check the calendar first");
  await act(async()=>{host.querySelector("form").dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));});
  expect(ctx.actOnThread.mock.calls[0]).toEqual([id,"propose",expect.objectContaining({text:"Check the calendar first",expectedVersion:7})]);
  expect(ctx.actOnThread.mock.calls[0][2]).not.toHaveProperty("agreement_id");
  expect(ag.text).toBe("Original");
});

test("only complete URL-safe invite tokens persist for auth, within the current tab and bounded lifetime", () => {
  expect(storeCouplesInvite("javascript:alert(1)")).toBe(false);
  const token = "fixture_123456789012345678901234567890";
  expect(storeCouplesInvite(token)).toBe(true);
  expect(pendingCouplesInvite()).toBe(token);
  const original = Date.now;
  try {
    Date.now = () => original() + 8 * 86400000;
    expect(pendingCouplesInvite()).toBe("");
  } finally {
    Date.now = original;
  }
});

test("check-in autosave protects note entry and moving to preview preserves the exact typed note", async () => {
  ctx.journey.threads = [{ id, title: "Card question", mutual: true, version: 3, agreements: [{
    id: "ag", revision: 1, text: "Agree", status: "active", review_on: "2027-02-02",
    accepted_by_me: true, accepted_by_partner: true, my_checkin: null, partner_checkin: null,
  }] }];
  let finish;
  ctx.actOnThread.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await act(async () => root.render(<CouplesAgreements />));
  const select = el("couples-checkin-result");
  act(() => { select.value = "mixed"; select.dispatchEvent(new Event("change", { bubbles: true })); });
  expect(el("couples-checkin-note").disabled).toBe(true);
  await act(async () => { finish(); });
  const input = el("couples-checkin-note");
  expect(input.disabled).toBe(false);
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, "Keep this note");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const preview = byText("Preview shared check-in");
  act(() => input.dispatchEvent(new FocusEvent("focusout", { bubbles: true, relatedTarget: preview })));
  await click(preview);
  expect(ctx.actOnThread).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain("Keep this note");
  await click(el("couples-share-checkin"));
  expect(ctx.actOnThread).toHaveBeenLastCalledWith(id, "checkin", expect.objectContaining({ note: "Keep this note", share: true }));
});

test("a card learning link shows its exact shared revision and clears it after withdrawal", async () => {
  const pin = {text:"Both chose Friday",active:true,accepted_by_me:true,accepted_by_partner:true};
  const agreement = {id:"one",revision:2,text:"Weekly plans",review_on:"2027-02-02",pin};
  ctx.journey.threads = [{id,kind:"prompt",title:"Timing",agreements:[agreement,
    {...agreement,id:"two",pin:{...pin,text:"Other shared note"}}]}];
  useLocation.mockReturnValue({search:`?item=${id}&agreement=one&revision=2`});
  await act(async()=>root.render(<CouplesLearnings />));
  expect(host.textContent).toContain("Both chose Friday");
  expect(host.textContent).not.toContain("Other shared note");
  ctx.journey.threads[0].agreements = [...ctx.journey.threads[0].agreements.slice(1)];
  await act(async()=>root.render(<CouplesLearnings />));
  expect(host.textContent).toContain("This shared learning is unavailable");
  expect(host.textContent).not.toContain("Both chose Friday");
  expect(host.textContent).not.toContain("Other shared note");
});
