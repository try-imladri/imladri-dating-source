import { act } from "react";
import { createRoot } from "react-dom/client";
import { ResearchChoice } from "./YourType";

jest.mock("@/lib/api", () => ({ errMsg: e => e?.response?.data?.detail || e.message || "Request failed" }));

let root, host;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
const checkbox = () => host.querySelector('input[type="checkbox"]');
const click = node => act(async () => node.click());

async function renderChoice(initial, patch = jest.fn(async (_, body) => ({ data: { ...body, revision: body.revision + 1 } }))) {
  const api = { patch };
  const onChange = jest.fn();
  await act(async () => root.render(<ResearchChoice api={api} initial={initial} owner="owner" onChange={onChange} />));
  return { api, onChange };
}

test("expanded opt-in displays its sensitive scope without enrolling on render", async () => {
  const { api } = await renderChoice({ version: "your-type-research-v2", enabled: false, revision: 4 });
  expect(checkbox().checked).toBe(false);
  expect(host.querySelector("label").textContent).toContain("sexuality, viewer gender, image choices and visual features");
  expect(host.textContent).toContain("Older attempts stay unknown");
  expect(api.patch).not.toHaveBeenCalled();
});

test("enabling sends the fixed displayed notice marker rather than a server-supplied marker", async () => {
  const { api, onChange } = await renderChoice({ version: "your-type-research-v2", enabled: false, revision: 4,
    notice_version: "untrusted-server-notice" });
  await click(checkbox());
  expect(api.patch).toHaveBeenCalledTimes(1);
  expect(api.patch).toHaveBeenCalledWith("/your-type/research", { version: "your-type-research-v2", expected_member: "owner",
    revision: 4, adult_confirmed: true, enabled: true, notice_version: "your-type-sensitive-research-v2" });
  expect(onChange).toHaveBeenCalledTimes(1);
  expect(checkbox().checked).toBe(true);
});

test("withdrawal remains possible without claiming a new notice acknowledgment", async () => {
  const { api } = await renderChoice({ version: "your-type-research-v2", enabled: true, revision: 5 });
  await click(checkbox());
  expect(api.patch).toHaveBeenCalledWith("/your-type/research", { version: "your-type-research-v2", expected_member: "owner",
    revision: 5, adult_confirmed: true, enabled: false });
  expect(checkbox().checked).toBe(false);
});

test("a rejected notice remains off and does not automatically resubmit consent", async () => {
  const patch = jest.fn().mockRejectedValue({ response: { status: 409, data: { detail: "Review the expanded research notice." } } });
  const { onChange } = await renderChoice({ version: "your-type-research-v2", enabled: false, revision: 6 }, patch);
  await click(checkbox());
  expect(patch).toHaveBeenCalledTimes(1);
  expect(checkbox().checked).toBe(false);
  expect(host.querySelector('[role="alert"]').textContent).toBe("Review the expanded research notice.");
  expect(onChange).not.toHaveBeenCalled();
});
