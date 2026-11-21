import { act, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { useOutletContext } from "react-router-dom";
import Start from "@/pages/app/Start";
import { UploadBody } from "./UploadWizard";
import { Starters } from "./Starters";

jest.mock("react-router-dom", () => ({ Link: () => null, useNavigate: () => jest.fn(), useOutletContext: jest.fn() }));
jest.mock("@clerk/react", () => ({}));
jest.mock("axios", () => ({ create: () => ({ interceptors: { response: { use: jest.fn() } } }) }));
jest.mock("@/components/app/QuickQuestion", () => ({ QuickQuestion: () => null }));
jest.mock("@/components/app/ShowWhy", () => ({ ShowWhy: () => null, ActionPicker: () => null }));
jest.mock("@/components/app/SafeShot", () => ({ SafeShot: ({ onConfirm }) =>
  <button data-testid="fixture-shot" onClick={() => onConfirm("fixture-image")}>Read</button> }));

const paid = { plan: "plus", period_verified: true, messages_remaining: 10 };
const person = { id: "one", nickname: "Alex", stage: "early", rel_type: "talking" };
let host, root, api, originalCrypto;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  originalCrypto = global.crypto;
  let n = 0;
  global.crypto = { randomUUID: () => String(++n) };
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  api = { post: jest.fn(async path => {
    if (path === "/quickstart/parse") return { data: { lines: [{ speaker: "me", text: "Hi", sure: true }] } };
    if (path === "/quickstart") return { data: { person, exchange: { id: "read" } } };
    return { data: {} };
  }) };
  useOutletContext.mockReturnValue({ me: {usage: paid}, api, setMe: jest.fn(), refreshUsage: jest.fn() });
});
afterEach(() => { act(() => root.unmount()); host.remove(); global.crypto = originalCrypto; });

async function fill(id, value) {
  const field = host.querySelector("#" + id);
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(field), "value").set.call(field, value);
  await act(async () => field.dispatchEvent(new Event("input", {bubbles: true})));
}

test("a blocked wizard records no start and repeat mounts use one attempt per eligible wizard", async () => {
  const show = usage => act(async () => root.render(<StrictMode><UploadBody api={api} me={{usage}} person={person} /></StrictMode>));
  await show({...paid, messages_remaining: 0});
  expect(api.post).not.toHaveBeenCalled();
  await show(paid);
  const starts = api.post.mock.calls.map(([, payload]) => payload);
  expect(starts.length).toBeGreaterThan(0);
  expect(new Set(starts.map(s => s.attempt_id)).size).toBe(1);
  expect(starts[0]).toEqual({type: "upload_started", attempt_id: expect.any(String)});
});

test("quick start sends the same upload ID from transcription through the saved read", async () => {
  await act(async () => root.render(<Start />));
  expect(api.post).toHaveBeenCalledWith("/me/event", { type: "onboarding_opened" });
  await fill("start-nickname", "Alex");
  await fill("start-what", "What changed?");
  await fill("start-paste", "Me: Hi");
  const submit = () => act(async () => host.querySelector("form").dispatchEvent(new Event("submit", {bubbles: true, cancelable: true})));
  await submit();
  await submit();
  const parse = api.post.mock.calls.find(([url]) => url === "/quickstart/parse");
  const save = api.post.mock.calls.find(([url]) => url === "/quickstart");
  expect(parse[2].headers["X-Upload-Attempt"]).toEqual(expect.any(String));
  expect(save[2].headers["X-Upload-Attempt"]).toBe(parse[2].headers["X-Upload-Attempt"]);
  expect(save[2].headers["Idempotency-Key"]).toEqual(expect.any(String));
});

test("a paid screenshot read uses the wizard's upload correlation on the OCR request", async () => {
  await act(async () => root.render(<UploadBody api={api} me={{usage: paid}} person={person} />));
  const attempt = api.post.mock.calls.find(([path]) => path === "/me/event")[1].attempt_id;
  api.post.mockResolvedValueOnce({data:{lines:[{speaker:"me",text:"Ready?",sure:true}]}});
  await act(async () => host.querySelector('[data-testid="upload-mode-shot"]').click());
  await act(async () => host.querySelector('[data-testid="fixture-shot"]').click());
  expect(api.post).toHaveBeenCalledWith("/ocr", { image_base64:"fixture-image", nickname:person.nickname },
    {headers:{"X-Upload-Attempt":attempt}});
});

test("only choosing a starter forwards the batch that generated its suggestions", async () => {
  const onPick = jest.fn();
  const service = {get:jest.fn().mockResolvedValue({data:{starters:["Ask about plans?"],batch_id:"batch-one"}})};
  await act(async () => root.render(<Starters api={service} url="/starters" onPick={onPick}/>));
  expect(onPick).not.toHaveBeenCalled();
  await act(async () => host.querySelector('[data-testid="chat-starter-0"]').click());
  expect(onPick).toHaveBeenCalledWith("Ask about plans?", "batch-one");
});
