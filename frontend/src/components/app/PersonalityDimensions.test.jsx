import { act } from "react";
import { createRoot } from "react-dom/client";
import { DimBars, pendingTypeText, RelationshipAxes, TypeCard, TypeCompare } from "./Personality";

jest.mock("@/lib/api", () => ({ pad: String }), { virtual: true });
jest.mock("@/components/ui/select", () => ({}), { virtual: true });
jest.mock("react-router-dom", () => ({Link:({children,to}) => <a href={to}>{children}</a>}));
jest.mock("recharts", () => ({}));

const dims = [
  { pair: "EI", letter: "I", first_pct: 25, answered: 1 },
  { pair: "NS", letter: "N", first_pct: 50, answered: 0 },
];
let root, container;

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  jest.restoreAllMocks();
});

test("quiz bars require an answer, while a chosen type is shown only as words", () => {
  act(() => root.render(<DimBars dims={dims} />));
  expect(container.textContent).toContain("Big picture / Details · Unanswered");
  expect(container.querySelector('[role="meter"]').getAttribute("aria-valuetext")).toBe("25 out of 100 toward Outgoing, based on 1 scored answer");
  expect(Array.from(container.querySelectorAll("[style]"), (el) => el.style.width)).toEqual(["25%"]);
  act(() => root.render(<DimBars dims={dims} selfSet />));
  expect(container.textContent).toContain("I · Reserved");
  expect(container.querySelector("[style]")).toBeNull();
});

test("equal archetype estimates share a rank and a tie at third changes the share label", async () => {
  const items = [91, 80, 80, 80, 65].map((score, index) => ({
    number:index + 1, score, slug:"entry-" + index, name:"Archetype " + index, line:"A different reason " + index,
  }));
  const api = { get:jest.fn(async () => ({data:{items}})) };
  await act(async () => root.render(<TypeCompare api={api} summary={{personality:{code:"ENFJ"}}} />));
  const rows = [...container.querySelectorAll("li")];
  expect(rows.map(el => el.value)).toEqual([1,2,2,2,5]);
  expect(rows.filter(el => el.textContent.includes("Tied"))).toHaveLength(3);
  expect(rows[3].textContent).toContain(items[3].line);
  expect(container.querySelector('[data-testid="type-compare-share"]').textContent).toBe("Share three matches");
});

test("the shareable card removes numerical bars when a member selects their own type", async () => {
  Object.defineProperty(document, "fonts", { value: { ready: Promise.resolve() }, configurable: true });
  const ctx = { fillRect: jest.fn(), fillText: jest.fn(), measureText: () => ({ width: 80 }) };
  jest.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
  jest.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue("data:image/png;base64,");
  const p = { code: "INFJ", name: "Confidant", line: "An interpretation", dims };
  const tags = [];
  try {
    await act(async () => root.render(<TypeCard p={p} tags={tags} />));
    expect(ctx.fillRect.mock.calls.filter(([, , , height]) => height === 18)).toHaveLength(2);
    expect(ctx.fillText.mock.calls.map(([word]) => word)).toContain("Big picture / Details · Unanswered");
    ctx.fillRect.mockClear();
    ctx.fillText.mockClear();
    await act(async () => root.render(<TypeCard p={{ ...p, self_set: true }} tags={tags} />));
    expect(ctx.fillText.mock.calls.map(([word]) => word)).toEqual(expect.arrayContaining(["MY CHOSEN TYPE", "I · Reserved"]));
    expect(ctx.fillRect.mock.calls.filter(([, , , height]) => height === 18)).toHaveLength(0);
  } finally {
    delete document.fonts;
  }
});

test("an evenly split dimension gets no implied type or highlighted pole", () => {
  const tied = [{ pair: "EI", letter: "", first_pct: 50, answered: 2 }];
  act(() => root.render(<DimBars dims={tied} />));
  expect(container.textContent).toContain("Evenly split");
  expect(container.querySelector("[style]")).toBeNull();
  expect(pendingTypeText({ dims: tied })).toMatch(/evenly split/);
  expect(pendingTypeText({ dims: [], guess: "ENFJ" })).not.toContain("ENFJ");
  act(() => root.render(<TypeCard p={{code: "", dims: tied}} tags={[]} />));
  expect(container.querySelector("a[download]")).toBeNull();
});

test("blocked preferences and a slow replacement image cannot share an outdated type card", async () => {
  jest.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("Blocked"); });
  jest.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Blocked"); });
  const ctx = { fillRect: jest.fn(), fillText: jest.fn(), measureText: () => ({ width: 80 }) };
  jest.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
  jest.spyOn(HTMLCanvasElement.prototype, "toDataURL")
    .mockReturnValueOnce("data:image/png;base64,b2xk").mockReturnValueOnce("data:image/png;base64,bmV3");
  const p = {code:"INFJ", name:"Confidant", line:"An interpretation", dims};
  const tags = [];
  await act(async () => root.render(<TypeCard p={p} tags={tags} />));
  expect(container.querySelector("a[download]").download).toBe("my-type-INFJ.png");
  let ready;
  Object.defineProperty(document, "fonts", { configurable:true, value:{ready:new Promise(resolve => {ready = resolve;})} });
  try {
    await act(async () => root.render(<TypeCard p={{...p, code:"ISFJ"}} tags={tags} />));
    expect(container.querySelector("a[download]")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("Preparing your card");
    await act(async () => ready());
    const image = container.querySelector("a[download]");
    expect(image.download).toBe("my-type-ISFJ.png");
    expect(image.href).toBe("data:image/png;base64,bmV3");
  } finally { delete document.fonts; }
});

test("an obsolete card style defaults to Noir and a failed image can retry", async () => {
  jest.spyOn(Storage.prototype, "getItem").mockReturnValue("no-longer-published");
  const ctx = { fillRect: jest.fn(), fillText: jest.fn(), measureText: () => ({ width: 80 }) };
  jest.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValueOnce(null).mockReturnValueOnce(ctx);
  jest.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue("data:image/png;base64,cmV0cnk=");
  await act(async () => root.render(<TypeCard p={{code:"INFJ",name:"Confidant",line:"An interpretation",dims}} tags={[]} />));
  expect(container.querySelector('[aria-label="Noir"]').getAttribute("aria-pressed")).toBe("true");
  expect(container.querySelector("a[download]")).toBeNull();
  await act(async () => container.querySelector('[role="alert"] button').click());
  expect(container.querySelector("a[download]").download).toBe("my-type-INFJ.png");
});

test("partial relationship results visibly distinguish a measured low answer from an unanswered axis", () => {
  const axes = [{axis: "Reassurance", score: 0, answered: 1}, {axis: "Directness", score: null, answered: 0}];
  act(() => root.render(<RelationshipAxes axes={axes} />));
  expect(container.textContent).toContain("0/100 · 1 answer");
  expect(container.textContent).toContain("DirectnessUnanswered");
});

test("an interrupted ranking can retry after someone removes their saved type", async () => {
  const api = {get:jest.fn().mockRejectedValueOnce({response:{status:409}})
    .mockResolvedValueOnce({data:{code:"",items:[]}})};
  await act(async () => root.render(<TypeCompare api={api} summary={{personality:{code:"ENFJ"}}} />));
  expect(container.textContent).toContain("changed while this comparison was running");
  await act(async () => [...container.querySelectorAll("button")].find(b=>b.textContent==="Try again").click());
  expect(container.textContent).toContain("Your saved profile no longer has a type");
  expect(container.querySelector('[data-testid="type-compare-share"]')).toBeNull();
});

test("a ranking based on a type changed elsewhere identifies the saved type used", async () => {
  const api = {get:jest.fn(async () => ({data:{code:"INFJ", items:[
    {number:1,score:80,slug:"sample",name:"Sample",line:"Shared interests."},
  ]}}))};
  await act(async () => root.render(<TypeCompare api={api} summary={{personality:{code:"ENFJ"}}} />));
  expect(container.querySelector('[role="status"]').textContent).toContain("These matches use INFJ");
  expect(container.querySelector("li").textContent).toContain("Shared interests.");
});
