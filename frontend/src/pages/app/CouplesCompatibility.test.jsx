import { act } from "react";
import { createRoot } from "react-dom/client";
import { useOutletContext } from "react-router-dom";
import { CouplesAssessmentSharing, CouplesCompatibility } from "./CouplesCompatibility";
import { mainConsentFixture } from "@/testFixtures/mainConsent";

jest.mock("react-router-dom", () => ({ useOutletContext: jest.fn(),
  useLocation: () => ({ pathname: "/app/couples/compatibility", search: "" }),
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a> }));
jest.mock("@/lib/api", () => ({ errMsg: () => "Request failed" }));

let host, root, api, context;
const pending = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function comparison(pair = "pair-a", revision = 0, shared = false) {
  return { version: "couples-compatibility-v1", pair_id: pair,
    sharing: { version: "couples-assessment-sharing-v1", revision, personality: shared, intimacy: shared },
    partner_sharing: { personality: shared, intimacy: shared },
    domains: Object.fromEntries(["personality", "intimacy"].map(scope => [scope, {
      status: shared ? "ready" : "awaiting_sharing", compared_questions: shared ? 1 : 0,
      items: shared ? [{ id: scope, title: scope === "intimacy" ? "Different boundaries" : "Communication pace",
        kind: scope === "intimacy" ? "boundary_difference" : "difference", me: "My saved choice", partner: "Their saved choice",
        summary: "Both preferences matter.", suggestion: "Ask about an option you both want.", evidence: { me: 1, partner: 1 } }] : [], notes: [],
    }])), notice: "Share my current and future saved answers and results in the selected categories with my partner and use them in Couples coaching until I withdraw sharing.",
    notice_version: "couples-assessment-sharing-v1", notes: [], context_signature: "context-" + pair + "-" + revision };
}
async function show(next, results = false) {
  if (next) context = next;
  useOutletContext.mockReturnValue(context);
  const settingsApi = { ...api, get: (path, options) => path === "/couples"
    ? context.fullUnavailable ? Promise.reject({ response: { status: 402 } }) : Promise.resolve({ data: context.workspace })
    : path === "/me/main-consent" ? Promise.resolve({ data: context.metadata || mainConsentFixture(context.me.user_id, { pairId: context.workspace.pair_id }) }) : api.get(path, options) };
  await act(async () => root.render(results ? <CouplesCompatibility /> : <CouplesAssessmentSharing api={settingsApi} me={context.me} />));
}
const element = id => host.querySelector(`[data-testid="${id}"]`);
const click = id => act(async () => element(id).click());
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.useFakeTimers();
  host = document.body.appendChild(document.createElement("div")); root = createRoot(host);
  api = { get: jest.fn(async () => ({ data: comparison() })), put: jest.fn(), post: jest.fn() };
  context = { api, me: { user_id: "member-a" }, workspace: { state: "active", adult: true, pair_id: "pair-a", partner_name: "Alex" } };
});
afterEach(() => { act(() => root.unmount()); host.remove(); jest.useRealTimers(); });

test("both sharing scopes start off and loading never grants sharing", async () => {
  await show();
  expect(api.get).toHaveBeenCalledWith("/couples/compatibility", { signal: expect.any(AbortSignal) });
  expect(api.put).not.toHaveBeenCalled();
  expect(element("compatibility-share-personality").checked).toBe(false);
  expect(element("compatibility-share-intimacy").checked).toBe(false);
  expect(element("compatibility-save-sharing").disabled).toBe(true);
  expect(host.textContent).toContain("current and future saved answers and results");
  expect(element("compatibility-sharing-notice").textContent).toBe(comparison().notice);
  expect(host.textContent).toContain("does not change your optional research choices");
  expect(host.querySelector('a[href="/app/settings#questionnaire-research"]')).not.toBeNull();
});

test("only explicit save sends owner, pair, revision and independently chosen scopes", async () => {
  api.get.mockResolvedValue({ data: comparison("pair-a", 7) });
  const request = pending(); api.put.mockReturnValue(request.promise);
  await show(); await click("compatibility-share-personality");
  expect(api.put).not.toHaveBeenCalled();
  await click("compatibility-save-sharing");
  expect(api.put).toHaveBeenCalledWith("/couples/compatibility/sharing", { pair_id: "pair-a", expected_member: "member-a",
    expected_revision: 7, notice_version: "couples-assessment-sharing-v1", personality: true, intimacy: false }, { signal: expect.any(AbortSignal) });
  expect(element("compatibility-save-sharing").disabled).toBe(true);
  await click("compatibility-save-sharing"); expect(api.put).toHaveBeenCalledTimes(1);
  const saved = comparison("pair-a", 8); saved.sharing.personality = true;
  await act(async () => request.resolve({ data: saved }));
  expect(host.textContent).toContain("Alex has not shared this category");
  expect(element("compatibility-item-personality")).toBeNull();
  expect(element("compatibility-share-intimacy").checked).toBe(false);
});

test("mutually shared responses show practical differences and respect refusals without a compatibility score", async () => {
  api.get.mockResolvedValue({ data: comparison("pair-a", 2, true) });
  await show(undefined, true);
  expect(element("compatibility-item-personality").textContent).toContain("My saved choice");
  expect(element("compatibility-item-personality").textContent).toContain("Their saved choice");
  expect(element("compatibility-item-intimacy").textContent).toContain("Respect each person’s no");
  expect(element("compatibility-item-intimacy").textContent).toContain("Ask about an option you both want");
  expect(host.textContent).not.toMatch(/\d+%/);
  expect(host.textContent).toContain("not a prediction of your relationship");
  expect(host.querySelector('a[href="/app/couples/intimacy"]')).not.toBeNull();
});

test("withdrawing one category redacts its comparison after the saved receipt and leaves the other scope intact", async () => {
  api.get.mockResolvedValue({ data: comparison("pair-a", 2, true) });
  const withdrawn = comparison("pair-a", 3, true); withdrawn.sharing.intimacy = false;
  withdrawn.domains.intimacy = { status: "awaiting_sharing", compared_questions: 0, items: [], notes: [] };
  api.put.mockResolvedValue({ data: withdrawn });
  await show(); await click("compatibility-share-intimacy"); await click("compatibility-save-sharing");
  expect(api.put.mock.calls[0][1]).toEqual({ pair_id: "pair-a", expected_member: "member-a", expected_revision: 2,
    notice_version: "couples-assessment-sharing-v1", personality: true, intimacy: false });
  expect(element("compatibility-item-intimacy")).toBeNull();
  expect(element("compatibility-share-personality").checked).toBe(true);
  expect(element("compatibility-share-intimacy").checked).toBe(false);
  expect(host.textContent).not.toContain("Their saved choice");
});

test("unexpected unshared partner data is rejected instead of rendered", async () => {
  const unsafe = comparison(); unsafe.domains.intimacy = comparison("pair-a", 0, true).domains.intimacy;
  api.get.mockResolvedValueOnce({ data: unsafe }).mockResolvedValue({ data: comparison() });
  await show();
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(host.textContent).not.toContain("Their saved choice");
  expect(element("compatibility-share-intimacy")).toBeNull();
  expect(api.put).not.toHaveBeenCalled();
  await click("compatibility-check-saved"); expect(element("compatibility-share-intimacy").checked).toBe(false);
});

test("an unknown or missing sharing notice cannot produce a consent write", async () => {
  const unknown = comparison(); unknown.notice_version = "future-unreviewed-notice";
  api.get.mockResolvedValue({ data: unknown });
  await show();
  expect(element("compatibility-share-personality")).toBeNull();
  expect(element("compatibility-save-sharing").disabled).toBe(true);
  expect(api.put).not.toHaveBeenCalled();
});

test.each([409, 503])("an uncertain %s sharing response hides comparisons and never automatically resends", async status => {
  api.get.mockResolvedValue({ data: comparison("pair-a", 3, true) });
  api.put.mockRejectedValue({ response: { status } });
  await show(); await click("compatibility-share-intimacy"); await click("compatibility-save-sharing");
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(element("compatibility-item-personality")).toBeNull();
  expect(element("compatibility-save-sharing").disabled).toBe(true);
  expect(api.put).toHaveBeenCalledTimes(1);
  api.get.mockResolvedValue({ data: comparison("pair-a", 4) });
  await click("compatibility-check-saved");
  expect(element("compatibility-share-intimacy").checked).toBe(false);
  expect(api.put).toHaveBeenCalledTimes(1);
});

test("refresh observes partner withdrawal and preserves a draft only while its saved revision stays current", async () => {
  api.get.mockResolvedValue({ data: comparison("pair-a", 3, true) });
  await show(); await click("compatibility-share-intimacy");
  await act(async () => window.dispatchEvent(new Event("focus")));
  expect(element("compatibility-share-intimacy").checked).toBe(false);
  const changed = comparison("pair-a", 4, true); changed.partner_sharing.intimacy = false;
  changed.domains.intimacy = { status: "awaiting_sharing", compared_questions: 0, items: [], notes: [] };
  api.get.mockResolvedValue({ data: changed });
  await act(async () => window.dispatchEvent(new Event("focus")));
  expect(element("compatibility-item-intimacy")).toBeNull();
  expect(element("compatibility-share-intimacy").checked).toBe(true);
  expect(api.put).not.toHaveBeenCalled();
});

test("a late old-pair read cannot publish the former partner’s data", async () => {
  const old = pending(); api.get.mockReturnValueOnce(old.promise).mockResolvedValue({ data: comparison("pair-b") });
  await show();
  await show({ ...context, me: { user_id: "member-b" }, workspace: { state: "active", adult: true, pair_id: "pair-b" } });
  await act(async () => old.resolve({ data: comparison("pair-a", 2, true) }));
  expect(host.textContent).not.toContain("Their saved choice");
  expect(element("compatibility-share-personality").checked).toBe(false);
});

test("a late prior-pair write cannot clear a new pair’s write lock or publish its receipt", async () => {
  const first = pending(), second = pending();
  api.put.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  await show(); await click("compatibility-share-personality"); await click("compatibility-save-sharing");
  api.get.mockResolvedValue({ data: comparison("pair-b") });
  await show({ ...context, me: { user_id: "member-b" }, workspace: { state: "active", adult: true, pair_id: "pair-b" } });
  await click("compatibility-share-intimacy"); await click("compatibility-save-sharing");
  await act(async () => first.resolve({ data: comparison("pair-a", 3, true) }));
  const reads = api.get.mock.calls.length;
  await act(async () => window.dispatchEvent(new Event("focus")));
  expect(api.get).toHaveBeenCalledTimes(reads);
  expect(element("compatibility-save-sharing").disabled).toBe(true);
  const saved = comparison("pair-b", 1); saved.sharing.intimacy = true;
  await act(async () => second.resolve({ data: saved }));
  expect(element("compatibility-share-intimacy").checked).toBe(true);
  expect(host.textContent).not.toContain("Their saved choice");
  expect(api.put.mock.calls[1][1].expected_member).toBe("member-b");
});

test.each(["solo", "confirming"])("%s pairs cannot request or disclose a compatibility comparison", async state => {
  context.workspace.state = state; await show();
  expect(host.textContent).toContain("Connect before comparing");
  expect(api.get).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled();
});

test("the compatibility tab has saved status and a Settings link, without another consent form", async () => {
  await show(undefined, true);
  expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  expect(element("compatibility-save-sharing")).toBeNull();
  expect(element("compatibility-status-personality").textContent).toContain("You: private");
  expect(element("compatibility-manage-sharing").getAttribute("href")).toBe("/app/settings#partner-assessment-sharing");
  expect(host.textContent).toContain("You can change sharing in Settings");
  expect(api.put).not.toHaveBeenCalled();
});

test("Settings does not enable assessment sharing for an age-corrected underage member", async () => {
  context.workspace.underage = true;
  await show();
  expect(host.textContent).toContain("requires both partners to be adults");
  expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  expect(api.get).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled();
});

test("results refresh observes partner withdrawal without producing a sharing write", async () => {
  api.get.mockResolvedValue({ data: comparison("pair-a", 3, true) });
  await show(undefined, true);
  const changed = comparison("pair-a", 4, true); changed.partner_sharing.intimacy = false;
  changed.domains.intimacy = { status: "awaiting_sharing", compared_questions: 0, items: [], notes: [] };
  api.get.mockResolvedValue({ data: changed });
  await act(async () => window.dispatchEvent(new Event("focus")));
  expect(element("compatibility-item-intimacy")).toBeNull();
  expect(element("compatibility-item-personality")).not.toBeNull();
  expect(api.put).not.toHaveBeenCalled();
});

test("Settings can revoke existing assessment sharing when paid comparison access has expired", async () => {
  context.fullUnavailable = true;
  context.metadata = mainConsentFixture("member-a", { pairId: "pair-a", pairEnabled: true });
  api.post.mockImplementation(async () => {
    context.metadata.pair_sharing = { ...context.metadata.pair_sharing, revocable: false, revocation_revision: 3, personality: false, intimacy: false };
    return { data: context.metadata };
  });
  await show();
  expect(element("settings-revoke-assessment-sharing")).not.toBeNull(); expect(element("compatibility-share-personality")).toBeNull();
  await click("settings-revoke-assessment-sharing");
  expect(api.post).toHaveBeenCalledWith("/me/main-consent/partner-revocation", { expected_member: "member-a", pair_id: "pair-a",
    expected_revision: 2, notice_version: "couples-assessment-sharing-v1" }, { signal: expect.any(AbortSignal) });
  expect(api.get).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled();
  expect(element("settings-revoke-assessment-sharing")).toBeNull(); expect(host.textContent).toContain("Assessment sharing is off");
});

test("an unknown metadata-only withdrawal is not replayed and requires an explicit saved-status check", async () => {
  context.fullUnavailable = true; context.metadata = mainConsentFixture("member-a", { pairId: "pair-a", pairEnabled: true });
  api.post.mockRejectedValue(new Error("lost response")); await show(); await click("settings-revoke-assessment-sharing");
  expect(api.post).toHaveBeenCalledTimes(1); expect(element("settings-revoke-assessment-sharing")).toBeNull();
  context.metadata.pair_sharing = { ...context.metadata.pair_sharing, revocable: false, revocation_revision: 3, personality: false, intimacy: false };
  await act(async () => [...host.querySelectorAll("button")].find(button => button.textContent === "Check current connection").click());
  expect(api.post).toHaveBeenCalledTimes(1); expect(host.textContent).toContain("Assessment sharing is off");
});
