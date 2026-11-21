import { act } from "react";
import { createRoot } from "react-dom/client";
import PromptLibrary from "./PromptLibrary";

jest.mock("@/lib/api", () => ({ errMsg: e => e.message }));
const library = {
  revision: "r1", prompts: { six_part: "Original guidance", starters: "Ask a short question." },
  areas: { Conversations: { six_part: "Individual reads and reply format", starters: "Suggested starters" } },
  max_length: 6000,
};

test("an admin previews and publishes a single instruction, then reads generation counts and the Couples catalog", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.body.appendChild(document.createElement("div"));
  const root = createRoot(host);
  const api = {
    get: jest.fn(async url => {
      if (url === "/admin/prompts") return { data: library };
      if (url === "/admin/prompts/usage") return { data: { start: "2026-11-01", end: "2026-11-07",
        counts: { starters: { succeeded: 2, failed: 1 } }, activity: {}, settled: {plan: 1, refill: 0},
        work: {background: {generations: 1, attempts: 2, priced: 0}} } };
      if (url === "/admin/couples/catalog") return { data: { draft: [], published: [], revision: 0 } };
      throw Error(url);
    }),
    post: jest.fn(async (url, body) => ({ data: url.endsWith("evaluate") ? {id:"observed",eligible:true,cases:[
      {feature:"six_part",input:"Can we confirm Friday?",answer:"Is Friday still good?",expectation:"Preserve the day.",passed:true}]} : { guidance: body.text, keys: [body.key] } })),
    put: jest.fn(async (_, body) => ({ data: { prompts: { ...library.prompts, ...body.prompts }, prompts_revision: "r2" } })),
  };
  const button = text => [...host.querySelectorAll("button")].find(e => e.textContent === text);
  const click = e => act(async () => e.click());
  try {
    await act(async () => root.render(<PromptLibrary api={api} />));
    const input = host.querySelector("textarea");
    expect(button("Publish instruction").disabled).toBe(true);
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, "A revised prompt");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await click(button("Preview guidance"));
    expect(host.querySelector('[data-testid="admin-prompt-preview"]').textContent).toContain("A revised prompt");
    expect(api.put).not.toHaveBeenCalled();
    expect(button("Publish instruction").disabled).toBe(true);
    await click(button("Evaluate draft with DeepSeek"));
    expect(host.textContent).toContain("Is Friday still good?");
    await click(host.querySelector('input[type="checkbox"]'));
    await click(button("Publish instruction"));
    expect(api.put).toHaveBeenCalledWith("/admin/settings", { prompts: { six_part: "A revised prompt" }, prompts_revision: "r1", reviewed_candidate:"observed" });
    expect(button("Publish instruction").disabled).toBe(true);
    await click(button("AI activity"));
    const rows = [...host.querySelectorAll("tbody tr")].map(row => [...row.children].map(cell => cell.textContent));
    expect(rows).toContainEqual(["Suggested starters", "2", "1"]);
    expect(rows).toContainEqual(["Background work", "1", "2", "0 / 2", "Unknown"]);
    expect(host.textContent).toContain("1 plan replies and 0 refill replies");
    await click(button("Couples starters"));
    expect(api.get).toHaveBeenCalledWith("/admin/couples/catalog", expect.anything());
    expect(host.textContent).toContain("Starter questions");
  } finally {
    act(() => root.unmount());
    host.remove();
    delete global.IS_REACT_ACT_ENVIRONMENT;
  }
});
