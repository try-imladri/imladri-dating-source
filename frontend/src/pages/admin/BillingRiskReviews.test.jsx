import { act } from "react";
import { createRoot } from "react-dom/client";
import BillingRiskReviews from "./BillingRiskReviews";

jest.mock("@/lib/api", () => ({ errMsg: error => error.message || "Review changed. Refresh before acting." }));

let host, root, api, item, previousCrypto;
const pending = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const button = text => [...host.querySelectorAll("button")].find(node => node.textContent === text);
const click = node => act(async () => node.click());
const checkbox = text => [...host.querySelectorAll("label")].find(node => node.textContent === text)?.querySelector("input");
const rows = () => [...host.querySelectorAll('[data-testid="billing-risk-row"]')];
async function mount() { await act(async () => root.render(<BillingRiskReviews api={api} />)); }
async function load() { await mount(); await click(button("Refresh reviews")); }

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  previousCrypto = global.crypto;
  global.crypto = { randomUUID: jest.fn(() => "8f232fb9-c39a-48f5-ade3-7af81640f23e") };
  item = { member_id: "reviewed-member", review_id: "opaque-review", revision: 3, hold_scopes: [],
    counts: { unresolved_disputes: 2, identity_conflicts: 0 }, recent_actions: [] };
  api = { get: jest.fn(async () => ({ data: { items: [item], next: null } })), patch: jest.fn(async () => ({})) };
  host = document.body.appendChild(document.createElement("div")); root = createRoot(host);
});
afterEach(() => { if (root) act(() => root.unmount()); host?.remove(); global.crypto = previousCrypto; });

test("review signals load on demand and never apply a default hold", async () => {
  await mount(); expect(api.get).not.toHaveBeenCalled(); expect(api.patch).not.toHaveBeenCalled();
  const details = host.querySelector("details");
  await act(async () => { details.open = true; details.dispatchEvent(new Event("toggle")); });
  expect(api.get).toHaveBeenCalledWith("/admin/billing-risk/reviews", { params: { limit: 25 }, signal: expect.any(AbortSignal) });
  expect(rows()).toHaveLength(1);
  expect([...rows()[0].querySelectorAll('input[type="checkbox"]')].every(node => !node.checked)).toBe(true);
  expect(button("Apply selected hold").disabled).toBe(true);
  expect(button("Release selected hold").disabled).toBe(true);
  expect(host.textContent).toContain("A lawful refund request alone does not create an abuse hold");
  expect(host.textContent).toContain("Review signals are not proof of fraud");
  expect(api.patch).not.toHaveBeenCalled();
});

test("a hold submits only explicitly selected scopes with exact owner, revision, reason and unique request", async () => {
  await load(); await click(checkbox("New AI generations")); await click(checkbox("New purchases"));
  const reason = host.querySelector("select");
  act(() => { reason.value = "repeated_disputes"; reason.dispatchEvent(new Event("change", { bubbles: true })); });
  item = { ...item, revision: 4, hold_scopes: ["new_ai", "new_purchase"] };
  await click(button("Apply selected hold"));
  expect(api.patch).toHaveBeenCalledWith("/admin/billing-risk/reviews/opaque-review", {
    expected_member: "reviewed-member", revision: 3, request_id: "8f232fb9-c39a-48f5-ade3-7af81640f23e",
    action: "hold", scopes: ["new_ai", "new_purchase"], reason: "repeated_disputes",
  });
  expect(api.get).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain("Staff hold: New AI generations, New purchases");
  expect(checkbox("New AI generations").checked).toBe(false);
  expect(api.patch.mock.calls.every(([path]) => path.startsWith("/admin/billing-risk/reviews/"))).toBe(true);
});

test("releasing a selected hold names only that scope instead of requesting a blanket release", async () => {
  item.hold_scopes = ["new_ai", "new_purchase"];
  await load(); await click(checkbox("New AI generations"));
  item = { ...item, revision: 4, hold_scopes: ["new_purchase"] };
  await click(button("Release selected hold"));
  expect(api.patch.mock.calls[0][1]).toMatchObject({ action: "release", scopes: ["new_ai"], reason: "resolved", revision: 3 });
  expect(host.textContent).toContain("Staff hold: New purchases");
});

test("marking reviewed has no selected scopes and leaves an existing hold visible", async () => {
  item.hold_scopes = ["new_intro"];
  await load();
  item = { ...item, revision: 4 };
  await click(button("Mark reviewed"));
  expect(api.patch.mock.calls[0][1]).toMatchObject({ action: "dismiss", scopes: [], reason: "resolved" });
  expect(host.textContent).toContain("Staff hold: New introductory access");
  expect(host.textContent).toContain("Marking reviewed does not remove a hold");
  expect(host.textContent).toContain("do not charge, refund, cancel a subscription");
});

test("an uncertain action reads saved status once without automatically sending another decision", async () => {
  await load(); await click(checkbox("New AI generations"));
  const readback = pending(); api.get.mockReturnValueOnce(readback.promise);
  api.patch.mockRejectedValueOnce(new Error("Connection interrupted"));
  await click(button("Apply selected hold"));
  expect(api.patch).toHaveBeenCalledTimes(1); expect(api.get).toHaveBeenCalledTimes(2);
  expect(host.querySelector('[role="alert"]').textContent).toBe("Connection interrupted");
  item = { ...item, revision: 4, hold_scopes: ["new_ai"] };
  await act(async () => readback.resolve({ data: { items: [item], next: null } }));
  expect(host.textContent).toContain("Staff hold: New AI generations");
  expect(api.patch).toHaveBeenCalledTimes(1);
});

test("one review action remains single-flight across rapid clicks before React rerenders", async () => {
  await load(); await click(checkbox("New AI generations"));
  const write = pending(); api.patch.mockReturnValue(write.promise);
  const apply = button("Apply selected hold");
  await act(async () => { apply.click(); apply.click(); });
  expect(api.patch).toHaveBeenCalledTimes(1);
  await act(async () => write.resolve({}));
});

test("pagination reads an opaque cursor without mixing it into a mutation", async () => {
  api.get.mockResolvedValueOnce({ data: { items: [item], next: "opaque-next" } })
    .mockResolvedValueOnce({ data: { items: [{ ...item, review_id: "review-2", member_id: "second-member" }], next: null } });
  await load(); await click(button("Load more reviews"));
  expect(api.get.mock.calls[1][1].params).toEqual({ limit: 25, after: "opaque-next" });
  expect(rows()).toHaveLength(2); expect(api.patch).not.toHaveBeenCalled();
});

test("a superseded read cannot replace the refreshed review revision", async () => {
  await load();
  const old = pending(); api.get.mockReturnValueOnce(old.promise).mockResolvedValueOnce({ data: {
    items: [{ ...item, revision: 7, hold_scopes: ["new_purchase"] }], next: null } });
  await click(button("Refresh reviews"));
  await click(button("Mark reviewed"));
  await act(async () => old.resolve({ data: { items: [item], next: null } }));
  expect(host.textContent).toContain("Revision 7");
  expect(host.textContent).toContain("Staff hold: New purchases");
  expect(api.patch).toHaveBeenCalledTimes(1);
});

test("a late action on an unmounted review cannot refresh the next admin screen", async () => {
  await load(); const write = pending(); api.patch.mockReturnValue(write.promise);
  await click(button("Mark reviewed"));
  await act(async () => root.render(<p>Another screen</p>));
  await act(async () => write.resolve({}));
  expect(api.get).toHaveBeenCalledTimes(1); expect(host.textContent).toBe("Another screen");
});
