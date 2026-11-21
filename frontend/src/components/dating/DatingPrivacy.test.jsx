import { act } from "react";
import { createRoot } from "react-dom/client";
import { DatingContext, useDatingResource } from "./DatingContext";
import { AuthorizedPhoto } from "./DatingUI";
import { Enrollment } from "@/pages/dating/DatingApp";
import DatingMessages, { Sharing } from "@/pages/dating/DatingMessages";
import { QuestionPreferences } from "@/pages/dating/DatingProfile";

jest.mock("@clerk/react", () => ({ useAuth: jest.fn(), useClerk: jest.fn() }));
jest.mock("@/lib/api", () => ({ useApi: jest.fn() }));
jest.mock("@/lib/theme", () => ({ ThemeToggle: () => null }));
jest.mock("react-router-dom", () => ({
  Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a>,
  NavLink: () => null, Navigate: () => null, Route: () => null, Routes: () => null,
  useNavigate: () => jest.fn(), useParams: () => ({ matchId: "connection-1" }), useLocation: () => ({ pathname: "/dating" }),
}));

let element, root, api, context;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  element = document.createElement("div"); document.body.append(element); root = createRoot(element);
  api = { get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() };
  context = { api, identity: "member-1:session-1", refresh: jest.fn(async () => {}), data: {
    member: { user_id: "member-1" }, launch: { city: "Chicago" }, consent: { version: "dating-enrollment-v1" },
    preferences: { revision: 0, personality: {}, intimacy: {}, personality_matching: false, intimacy_matching: false },
    catalog: { personality: [{ id: "everyday", label: "A free evening", options: ["quiet", "social"] }], intimacy: [{ id: "pace", label: "What pace feels comfortable?", options: ["slow", "discuss"] }] },
  } };
  URL.createObjectURL = jest.fn(() => "blob:local-test"); URL.revokeObjectURL = jest.fn();
});
afterEach(() => { act(() => root.unmount()); element.remove(); jest.restoreAllMocks(); });
const show = component => act(async () => root.render(<DatingContext.Provider value={context}>{component}</DatingContext.Provider>));
const click = control => act(async () => control.click());
const submit = form => act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
const write = (control, value) => act(() => {
  const prototype = control.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value").set.call(control, value);
  control.dispatchEvent(new Event("input", { bubbles: true }));
});

test("joining dating requires both explicit adult and purpose choices, even on form submit", async () => {
  api.post.mockResolvedValue({ data: { enrollment: { version: "dating-enrollment-v1" } } });
  await show(<Enrollment />);
  const form = element.querySelector("form"), button = element.querySelector('[data-testid="dating-enroll-button"]');
  expect(button.disabled).toBe(true); await submit(form); expect(api.post).not.toHaveBeenCalled();
  await click(element.querySelector('[data-testid="dating-adult-checkbox"]'));
  expect(button.disabled).toBe(true); await submit(form); expect(api.post).not.toHaveBeenCalled();
  await click(element.querySelector('[data-testid="dating-enrollment-checkbox"]'));
  expect(button.disabled).toBe(false); await submit(form);
  expect(api.post).toHaveBeenCalledWith("/dating/enrollment", { version: "dating-enrollment-v1", accepted: true, adult_confirmed: true });
  expect(context.refresh).toHaveBeenCalledTimes(1);
});

test("saving intimacy answers does not opt into matching or publish a match share", async () => {
  api.put.mockResolvedValue({ data: { preferences: {} } });
  await show(<QuestionPreferences scope="intimacy" />);
  const matching = element.querySelector('[data-testid="dating-intimacy-matching"]');
  expect(matching.checked).toBe(false);
  await act(async () => { const select = element.querySelector("select"); select.value = "slow"; select.dispatchEvent(new Event("change", { bubbles: true })); });
  await submit(element.querySelector("form"));
  expect(api.put).toHaveBeenCalledWith("/dating/preferences", expect.objectContaining({
    intimacy_matching: false, intimacy: { pace: { answer: "slow", acceptable: [], importance: "open" } }, expected_revision: 0,
  }));
  expect(api.post).not.toHaveBeenCalled();
  await click(matching); await submit(element.querySelector("form"));
  expect(api.put.mock.calls.at(-1)[1].intimacy_matching).toBe(true);
});

test("selected answer sharing needs a new explicit consent and supports revocation", async () => {
  context.data.preferences = { ...context.data.preferences, personality: { everyday: { self: "quiet" } }, intimacy: { pace: { answer: "slow" } } };
  let stored = { revision: 0, mine: [], partner: [] };
  api.get.mockImplementation(async () => ({ data: { sharing: stored } }));
  api.post.mockImplementation(async (_path, body) => {
    stored = { revision: 1, mine: body.answers, partner: [{ scope: "personality", question_id: "everyday", answer: "social", label: "A free evening" }] };
    return { data: { sharing: stored } };
  });
  api.delete.mockImplementation(async () => { stored = { revision: 2, mine: [], partner: [] }; return { data: { sharing: stored } }; });
  await show(<Sharing match={{ id: "connection-1", profile: { display_name: "Sam" } }} close={() => {}} />);
  const save = () => element.querySelector('[data-testid="dating-share-save"]');
  expect(save().disabled).toBe(true);
  await click(element.querySelectorAll(".dating-share-answer input")[0]);
  expect(save().disabled).toBe(true); await submit(element.querySelector("form")); expect(api.post).not.toHaveBeenCalled();
  await click(element.querySelector('[data-testid="dating-share-consent"]')); await submit(element.querySelector("form"));
  expect(api.post).toHaveBeenCalledWith("/dating/matches/connection-1/sharing", {
    answers: [{ scope: "personality", question_id: "everyday", answer: "quiet" }], expected_revision: 0,
  });
  expect(element.querySelector(".dating-partner-shares").textContent).toContain("social");
  expect(save().disabled).toBe(true);
  await click(element.querySelector('[data-testid="dating-share-revoke"]'));
  expect(api.delete).toHaveBeenCalledWith("/dating/matches/connection-1/sharing");
  expect(element.querySelector(".dating-partner-shares").textContent).not.toContain("social");
});

test("authenticated images reject external URLs and revoke blobs on an account switch", async () => {
  api.get.mockResolvedValue({ data: new Blob(["image"]) });
  await show(<AuthorizedPhoto photo={{ url: "https://elsewhere.example/secret.jpg" }} alt="Private photo" />);
  expect(api.get).not.toHaveBeenCalled(); expect(element.querySelector("img")).toBeNull();
  await show(<AuthorizedPhoto photo={{ url: "/api/dating/photos/photo-1/content" }} alt="Private photo" />);
  expect(api.get).toHaveBeenCalledWith("/dating/photos/photo-1/content", expect.objectContaining({ responseType: "blob" }));
  expect(element.querySelector("img").src).toBe("blob:local-test");
  context = { ...context, identity: "member-2:session-2", api: { get: jest.fn(() => new Promise(() => {})) } };
  await show(<AuthorizedPhoto photo={{ url: "/api/dating/photos/photo-1/content" }} alt="Private photo" />);
  expect(element.querySelector("img")).toBeNull(); expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:local-test");
});

test("a late private resource cannot repopulate the next account's view", async () => {
  let resolveOld; api.get.mockImplementation(() => new Promise(resolve => { resolveOld = resolve; }));
  function Probe() { const resource = useDatingResource("/dating/private"); return <p>{resource.data?.secret || "Waiting"}</p>; }
  await show(<Probe />);
  context = { ...context, identity: "member-2:session-2", api: { get: jest.fn(async () => ({ data: { secret: "Second account" } })) } };
  await show(<Probe />); expect(element.textContent).toBe("Second account");
  await act(async () => resolveOld({ data: { secret: "First account private data" } }));
  expect(element.textContent).toBe("Second account");
});

test("a human message retry preserves its idempotency key after an uncertain response", async () => {
  const match = { id: "connection-1", profile: { id: "profile-2", display_name: "Sam", city: "Chicago", intent: "long_term", photos: [] } };
  api.get.mockImplementation(async path => ({ data: path === "/dating/matches" ? { items: [match] } : { items: [], next_cursor: null } }));
  let attempts = 0;
  api.post.mockImplementation(async (path, body) => {
    if (path.endsWith("/read")) return { data: { ok: true } };
    if (++attempts === 1) throw new Error("Connection interrupted. Try again.");
    return { data: { message: { id: "message-1", sender_id: "member-1", text: body.text, created_at: "2026-11-19T12:00:00Z" } } };
  });
  await show(<DatingMessages />);
  const textarea = element.querySelector(".dating-message-composer textarea");
  write(textarea, "Hello Sam"); await submit(element.querySelector(".dating-message-composer"));
  expect(textarea.value).toBe("Hello Sam"); expect(element.textContent).toContain("Connection interrupted");
  const first = api.post.mock.calls.find(([path]) => path.endsWith("/messages"))[1];
  await submit(element.querySelector(".dating-message-composer"));
  const sent = api.post.mock.calls.filter(([path]) => path.endsWith("/messages"));
  expect(sent).toHaveLength(2); expect(sent[1][1]).toEqual(first); expect(first.request_id).toBeTruthy();
  expect(textarea.value).toBe(""); expect(element.querySelectorAll(".dating-message-list li")).toHaveLength(1);
});

test("a delivered message does not erase the next draft typed while it was sending", async () => {
  const match = { id: "connection-1", profile: { id: "profile-2", display_name: "Sam", city: "Chicago", photos: [] } };
  api.get.mockImplementation(async path => ({ data: path === "/dating/matches" ? { items: [match] } : { items: [] } }));
  let delivered;
  api.post.mockImplementation(path => path.endsWith("/messages") ? new Promise(resolve => { delivered = resolve; }) : Promise.resolve({ data: { ok: true } }));
  await show(<DatingMessages />);
  const textarea = element.querySelector(".dating-message-composer textarea");
  write(textarea, "My first hello"); await submit(element.querySelector(".dating-message-composer"));
  write(textarea, "A second thought");
  expect(element.querySelector(".dating-message-composer button").disabled).toBe(true);
  await act(async () => delivered({ data: { message: { id: "message-1", sender_id: "member-1", text: "My first hello", created_at: "2026-11-19T12:00:00Z" } } }));
  expect(textarea.value).toBe("A second thought");
  expect(element.querySelector(".dating-message-list").textContent).toContain("My first hello");
  expect(element.querySelector(".dating-message-composer button").disabled).toBe(false);
});
