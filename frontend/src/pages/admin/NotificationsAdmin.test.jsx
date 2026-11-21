import { act } from "react";
import { createRoot } from "react-dom/client";
import NotificationsAdmin from "./NotificationsAdmin";

jest.mock("@/lib/api", () => ({ errMsg: e => e.message }));

test("a lost response can be resumed from history with the same send ID", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.body.appendChild(document.createElement("div"));
  const root = createRoot(host);
  const key = "9859749e-919e-4f14-baa1-acb9928a25ad";
  const previous = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: { randomUUID: () => key } });
  let history = [];
  const api = {
    get: jest.fn(async () => ({ data: history })),
    post: jest.fn(async (path, message) => {
      if(path.endsWith("/preview")) return {data:{...message,as_of:"2026-11-08T12:00:00+00:00",eligible_inboxes:2}};
      if (!history.length) {
        history = [{ ...message, id: "announce:" + key, status: "pending", inboxes: 1, read: 0, created_at: "2026-11-08T12:00:00Z" }];
        throw Error("Delivery interrupted");
      }
      history = [{ ...history[0], status: "sent", inboxes: 2, read: 1 }];
      return { data: { ok: true } };
    }),
  };
  try {
    await act(async () => root.render(<NotificationsAdmin api={api} />));
    const title = host.querySelector('[data-testid="admin-announce-title"]');
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(title, "Update");
      title.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(host.textContent).toContain("2 eligible inboxes");
    expect(history).toHaveLength(0);
    await act(async () => host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(host.textContent).toContain("Delivery interrupted");
    await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent === "Resume delivery").click());
    expect(api.post.mock.calls[1]).toEqual(api.post.mock.calls[2]);
    expect(api.post.mock.calls[1][1].request_id).toBe(key);
    expect(host.textContent).toContain("2 current inboxes · 1 marked read");
    expect(title.value).toBe("");
  } finally {
    act(() => root.unmount());
    host.remove();
    if (previous) Object.defineProperty(globalThis, "crypto", previous);
    else delete globalThis.crypto;
  }
});
