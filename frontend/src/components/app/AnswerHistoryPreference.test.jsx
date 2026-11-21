import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import AnswerHistoryPreference from "./AnswerHistoryPreference";

const pref = { version: "history-staff-v1", revision: 0, enabled: false, since: null };
const person = { user_id: "fictional-A", role: "member" };
function Screen({ api, member = person }) {
  const [me, setMe] = useState(member);
  return <AnswerHistoryPreference key={me.user_id} api={api} me={me} setMe={setMe} />;
}
let host, root;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; host = document.createElement("div"); root = createRoot(host); });
afterEach(() => { act(() => root.unmount()); delete global.IS_REACT_ACT_ENVIRONMENT; });

test("history permission starts off and each change binds the account and displayed revision", async () => {
  const api = { get: jest.fn(async () => ({ data: pref })), patch: jest.fn(async (_, body) => ({ data: { ...pref, enabled: body.enabled, revision: body.revision + 1 } })) };
  await act(async () => root.render(<Screen api={api} />));
  expect(host.querySelector("input").checked).toBe(false);
  await act(async () => host.querySelector("input").click());
  expect(api.patch.mock.calls[0][1]).toEqual({ expected_member: "fictional-A", version: "history-staff-v1", revision: 0, enabled: true, adult_confirmed: true });
  expect(host.querySelector("input").checked).toBe(true);
  expect(host.textContent).toContain("Earlier answers are not added automatically");
  await act(async () => host.querySelector("input").click());
  expect(api.patch.mock.calls[1][1]).toMatchObject({ revision: 1, enabled: false, adult_confirmed: false });
  expect(host.querySelector("input").checked).toBe(false);
});

test("a stale revision refreshes the permission without silently opting in", async () => {
  let reads = 0;
  const api = { get: jest.fn(async () => ({ data: { ...pref, revision: reads++ ? 8 : 0 } })),
    patch: jest.fn(async () => { throw { response: { status: 409, data: { detail: "Review your current permission." } } }; }) };
  await act(async () => root.render(<Screen api={api} />));
  await act(async () => host.querySelector("input").click());
  expect(api.get).toHaveBeenCalledTimes(2);
  expect(host.querySelector("input").checked).toBe(false);
  expect(host.textContent).toContain("Review your current permission.");
});

test("switching accounts ignores the previous member's late save", async () => {
  let finish;
  const api = { get: jest.fn(async () => ({ data: pref })), patch: jest.fn(() => new Promise(resolve => { finish = resolve; })) };
  await act(async () => root.render(<Screen key="A" api={api} />));
  await act(async () => host.querySelector("input").click());
  const request = api.patch.mock.calls[0][2].signal;
  await act(async () => root.render(<Screen key="B" api={api} member={{ ...person, user_id: "fictional-B" }} />));
  expect(request.aborted).toBe(true);
  await act(async () => finish({ data: { ...pref, enabled: true, revision: 1 } }));
  expect(host.querySelector("input").checked).toBe(false);
});

test("a failed load offers no unchecked guess that could overwrite a saved choice", async () => {
  const api = { get: jest.fn(async () => { throw new Error("offline"); }), patch: jest.fn() };
  await act(async () => root.render(<Screen api={api} />));
  expect(host.querySelector("input")).toBeNull();
  expect(host.textContent).toContain("could not be loaded");
  expect(api.patch).not.toHaveBeenCalled();
});

test("an account change clears a pending save even when the component is reused", async () => {
  let finish;
  const api = { get: jest.fn(async () => ({ data: pref })), patch: jest.fn(() => new Promise(resolve => { finish = resolve; })) };
  const setMe = jest.fn();
  await act(async () => root.render(<AnswerHistoryPreference api={api} me={person} setMe={setMe} />));
  await act(async () => host.querySelector("input").click());
  expect(host.querySelector("input").disabled).toBe(true);
  await act(async () => root.render(<AnswerHistoryPreference api={api} me={{ ...person, user_id: "fictional-B" }} setMe={setMe} />));
  expect(host.querySelector("input").disabled).toBe(false);
  await act(async () => finish({ data: { ...pref, enabled: true, revision: 1 } }));
  expect(host.querySelector("input").checked).toBe(false);
  expect(setMe).not.toHaveBeenCalled();
});
