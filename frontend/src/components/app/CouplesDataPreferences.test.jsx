import { act } from "react";
import { createRoot } from "react-dom/client";
import CouplesDataPreferences from "./CouplesDataPreferences";

let host, root, api, me, setMe;
const workspace = (changes = {}) => ({ adult: true, underage: false, analytics_opt_in: false, review_return_opt_in: false, ...changes });
const pending = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
const render = async () => act(async () => root.render(<CouplesDataPreferences api={api} me={me} setMe={setMe} />));
const checkbox = field => host.querySelector('[data-testid="settings-' + field + '"]');
const click = async target => act(async () => target.click());
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true; host = document.body.appendChild(document.createElement("div")); root = createRoot(host);
  me = { user_id: "fictional-a", couples_preferences: { reminders: false, review_return_since: "historical" } };
  setMe = jest.fn(); api = { get: jest.fn(async () => ({ data: workspace() })), patch: jest.fn() };
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

test("Settings loads both saved data choices without enabling either or reminders", async () => {
  await render(); expect(checkbox("analytics_opt_in").checked).toBe(false); expect(checkbox("review_return_opt_in").checked).toBe(false);
  expect(api.patch).not.toHaveBeenCalled(); expect(host.textContent).toContain("independent of partner assessment sharing and reminders");
});
test.each(["analytics_opt_in", "review_return_opt_in"])("changing %s sends only that purpose and uses the confirmed response", async field => {
  api.patch.mockResolvedValue({ data: workspace({ [field]: true, review_return_since: "2026-11-16T01:00:00Z" }) });
  await render(); await click(checkbox(field));
  expect(api.patch).toHaveBeenCalledWith("/couples/preferences", { [field]: true }, { signal: expect.any(AbortSignal) });
  expect(checkbox(field).checked).toBe(true); const updated = setMe.mock.calls[0][0](me);
  expect(updated.couples_preferences.reminders).toBe(false); expect(updated.couples_preferences[field]).toBe(true);
  if (field === "review_return_opt_in") expect(updated.couples_preferences.review_return_since).toBe("2026-11-16T01:00:00Z");
});
test("withdrawal removes the local prospective timestamp without changing reminders", async () => {
  api.get.mockResolvedValue({ data: workspace({ review_return_opt_in: true }) }); api.patch.mockResolvedValue({ data: workspace() });
  await render(); await click(checkbox("review_return_opt_in")); const updated = setMe.mock.calls[0][0](me);
  expect(updated.couples_preferences.review_return_since).toBeUndefined(); expect(updated.couples_preferences.reminders).toBe(false);
  expect(api.patch.mock.calls[0][1]).toEqual({ review_return_opt_in: false });
});
test("an unknown response disables changes until an explicit GET and does not replay the write", async () => {
  api.patch.mockRejectedValue(new Error("lost response")); await render(); await click(checkbox("analytics_opt_in"));
  expect(checkbox("analytics_opt_in").disabled).toBe(true); expect(api.get).toHaveBeenCalledTimes(1);
  api.get.mockResolvedValue({ data: workspace({ analytics_opt_in: true }) });
  await click([...host.querySelectorAll("button")].find(button => button.textContent === "Check saved Couples choices"));
  expect(api.patch).toHaveBeenCalledTimes(1); expect(checkbox("analytics_opt_in").checked).toBe(true);
});
test("pending changes are single-flight", async () => {
  const request = pending(); api.patch.mockReturnValue(request.promise); await render(); await click(checkbox("analytics_opt_in")); await click(checkbox("review_return_opt_in"));
  expect(api.patch).toHaveBeenCalledTimes(1); await act(async () => request.resolve({ data: workspace({ analytics_opt_in: true }) }));
});
test("an age-corrected member may withdraw an existing choice but cannot enable another", async () => {
  api.get.mockResolvedValue({ data: workspace({ underage: true, analytics_opt_in: true }) });
  api.patch.mockResolvedValue({ data: workspace({ underage: true }) }); await render();
  expect(checkbox("analytics_opt_in").disabled).toBe(false); expect(checkbox("review_return_opt_in").disabled).toBe(true);
  await click(checkbox("analytics_opt_in")); expect(api.patch.mock.calls[0][1]).toEqual({ analytics_opt_in: false });
});
test("changing accounts aborts an old write and cannot publish its state or timestamp", async () => {
  const request = pending(); api.patch.mockReturnValue(request.promise); await render(); await click(checkbox("review_return_opt_in"));
  const signal = api.patch.mock.calls[0][2].signal; me = { user_id: "fictional-b" }; await render(); expect(signal.aborted).toBe(true);
  await act(async () => request.resolve({ data: workspace({ review_return_opt_in: true, review_return_since: "old-account" }) }));
  expect(checkbox("review_return_opt_in").checked).toBe(false); expect(setMe).not.toHaveBeenCalled();
});
