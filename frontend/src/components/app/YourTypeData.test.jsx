import { act } from "react";
import { createRoot } from "react-dom/client";
import YourTypeData from "./YourTypeData";

jest.mock("@/lib/api", () => ({ errMsg: e => e?.response?.data?.detail || e.message || "Request failed" }));
const ID = "00000000-0000-4000-8000-111111111111";
const receipt = (revision = 1, enabled = true) => ({ version: "your-type-research-v2", revision, enabled });
const row = (revision = 7, audience = "women", id = ID) => ({ id, revision, audience, status: "paused",
  updated_at: "2026-11-11T12:00:00Z", asset_edition: "bank-v1", state: { completed_crowds: 8 } });
function client() {
  return { get: jest.fn(async path => {
    if (path === "/your-type/research") return { data: receipt() };
    if (path === "/your-type/history") return { data: { sessions: [row()], next: null } };
    throw { response: { status: 402 } }; // Every paid endpoint is unavailable.
  }), patch: jest.fn(async (_, body) => ({ data: receipt(body.revision + 1, body.enabled) })),
  delete: jest.fn(async () => ({ data: { removed: true } })) };
}
let root, host;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; host = document.body.appendChild(document.createElement("div")); root = createRoot(host); });
afterEach(() => { act(() => root.unmount()); host.remove(); });
const click = node => act(async () => node.click());
const button = label => [...host.querySelectorAll("button")].find(node => node.textContent.includes(label));
const checkbox = () => host.querySelector('input[type="checkbox"]');
async function history() {
  await act(async () => { const node = host.querySelector('[data-testid="your-type-data-history"]'); node.open = true; node.dispatchEvent(new Event("toggle")); });
}

test("an expired member can withdraw research and delete from history revision without paid session reads", async () => {
  const api = client(); await act(async () => root.render(<YourTypeData api={api} owner="owner" />));
  expect(checkbox().checked).toBe(true); await click(checkbox());
  expect(api.patch).toHaveBeenCalledWith("/your-type/research", { version: "your-type-research-v2", expected_member: "owner", revision: 1, enabled: false, adult_confirmed: true });
  await history(); await click(button("Remove attempt")); await click(button("Confirm attempt removal"));
  expect(api.delete).toHaveBeenCalledWith(`/your-type/sessions/${ID}`, expect.objectContaining({ params: { expected_revision: 7 } }));
  expect(host.textContent).toContain("retained answer history removed");
  expect(api.get.mock.calls.every(([path]) => ["/your-type/research", "/your-type/history"].includes(path))).toBe(true);
  expect(host.querySelector("img,svg")).toBeNull();
});

test("missing age or blocked history never blocks research withdrawal", async () => {
  const api = client(); api.get.mockImplementation(async path => {
    if (path === "/your-type/research") return { data: receipt() };
    throw { response: { status: 403 } };
  });
  await act(async () => root.render(<YourTypeData api={api} owner="owner" />)); await history();
  expect(host.textContent).toContain("can still change the research permission above");
  await click(checkbox()); expect(api.patch.mock.calls[0][1].enabled).toBe(false);
});

test("permission failures are independent of history and do not prevent own deletion", async () => {
  const api = client(); api.get.mockImplementation(async path => {
    if (path === "/your-type/history") return { data: { sessions: [row()], next: null } };
    throw Error("permission unavailable");
  });
  await act(async () => root.render(<YourTypeData api={api} owner="owner" />)); await history();
  expect(host.textContent).toContain("research permission could not load");
  await click(button("Remove attempt")); await click(button("Confirm attempt removal"));
  expect(api.delete).toHaveBeenCalledTimes(1);
});

test("a changed attempt refuses deletion until an explicit refresh and new confirmation", async () => {
  const api = client(); let revision = 7;
  api.get.mockImplementation(async path => path === "/your-type/research" ? { data: receipt() } : { data: { sessions: [row(revision)], next: null } });
  api.delete.mockRejectedValueOnce({ response: { status: 409 } }).mockResolvedValueOnce({ data: { removed: true } });
  await act(async () => root.render(<YourTypeData api={api} owner="owner" />)); await history();
  await click(button("Remove attempt")); await click(button("Confirm attempt removal"));
  expect(host.textContent).toContain("changed elsewhere"); expect(api.delete).toHaveBeenCalledTimes(1);
  expect(button("Remove attempt").disabled).toBe(true);
  revision = 8; await click(button("Refresh attempt history"));
  await click(button("Remove attempt")); await click(button("Confirm attempt removal"));
  expect(api.delete.mock.calls[1][1].params.expected_revision).toBe(8);
});

test("research CAS conflict does not silently repeat the change with a new revision", async () => {
  const api = client(); let revision = 1;
  api.get.mockImplementation(async () => ({ data: receipt(revision) }));
  api.patch.mockRejectedValueOnce({ response: { status: 409, data: { detail: "Settings changed; refresh before continuing." } } })
    .mockResolvedValueOnce({ data: receipt(3, false) });
  await act(async () => root.render(<YourTypeData api={api} owner="owner" />)); await click(checkbox());
  expect(api.patch).toHaveBeenCalledTimes(1); expect(checkbox().checked).toBe(true);
  revision = 2; await click(button("Refresh research permission")); await click(checkbox());
  expect(api.patch.mock.calls[1][1].revision).toBe(2);
});

test("owner changes abort and ignore late permission and history responses", async () => {
  let releasePermission, releaseHistory;
  const oldPermission = new Promise(resolve => { releasePermission = resolve; });
  const oldHistory = new Promise(resolve => { releaseHistory = resolve; });
  const old = { get: jest.fn(path => path === "/your-type/research" ? oldPermission : oldHistory) };
  await act(async () => root.render(<YourTypeData api={old} owner="old" />)); await history();
  const fresh = client(); fresh.get.mockImplementation(async path => path === "/your-type/research"
    ? { data: receipt(0, false) } : { data: { sessions: [row(1, "men", "new-owner-attempt")], next: null } });
  await act(async () => root.render(<YourTypeData api={fresh} owner="new" />)); await history();
  for (const [, options] of old.get.mock.calls) expect(options.signal.aborted).toBe(true);
  await act(async () => { releasePermission({ data: receipt() }); releaseHistory({ data: { sessions: [row()], next: null } }); });
  expect(checkbox().checked).toBe(false);
  expect([...host.querySelectorAll('[data-testid="your-type-data-history"] strong')].map(node => node.textContent)).toEqual(["men"]);
  await click(checkbox());
  expect(fresh.patch.mock.calls[0][1].expected_member).toBe("new");
});

test("an unknown deletion is not claimed complete or automatically retried", async () => {
  const api = client(); api.delete.mockRejectedValue(Error("ACK unavailable"));
  await act(async () => root.render(<YourTypeData api={api} owner="owner" />)); await history();
  await click(button("Remove attempt")); await click(button("Confirm attempt removal"));
  expect(host.textContent).toContain("Removal was not confirmed");
  expect(api.delete).toHaveBeenCalledTimes(1); expect(button("Remove attempt").disabled).toBe(true);
});

test("an earlier positive permission requires an unchecked explicit opt-in to the expanded version", async () => {
  const api = client();
  api.get.mockResolvedValue({ data: { ...receipt(4, false), requires_expanded_consent: true } });
  await act(async () => root.render(<YourTypeData api={api} owner="owner" />));
  expect(checkbox().checked).toBe(false);
  expect(host.textContent).toContain("Research remains off until you review the new scope and opt in again");
  expect(api.patch).not.toHaveBeenCalled();
  await click(checkbox());
  expect(api.patch).toHaveBeenCalledWith("/your-type/research", { version: "your-type-research-v2",
    expected_member: "owner", revision: 4, enabled: true, adult_confirmed: true,
    notice_version: "your-type-sensitive-research-v2" });
});

test("an old response cannot be mistaken for current expanded permission", async () => {
  const api = client(); api.get.mockResolvedValue({ data: { version: "your-type-research-v1", revision: 4, enabled: true } });
  await act(async () => root.render(<YourTypeData api={api} owner="owner" />));
  expect(checkbox()).toBeNull();
  expect(host.textContent).toContain("The research permission could not load");
  expect(api.patch).not.toHaveBeenCalled();
});
