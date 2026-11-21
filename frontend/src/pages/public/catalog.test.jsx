import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes, useParams, useSearchParams } from "react-router-dom";
import { publicApi } from "@/lib/api";
import { defaultPublicQuiz } from "@/lib/publicQuiz";
import { useConfig } from "@/components/public/PublicLayout";
import { ArchetypeCard } from "@/components/public/Bits";
import ArchetypePage from "./ArchetypePage";
import FigurePage from "./FigurePage";
import Figures from "./Figures";
import Types from "./Types";
import { usePublicPageHead } from "./publicHead";
import Home from "./Home";
import { Showcase, SHOWCASE_TABS } from "@/components/public/Showcase";
import { getCachedPublicCatalog, invalidatePublicCatalog, loadPublicCatalog, PUBLIC_CATALOG_TTL_MS } from "@/lib/publicCatalogCache";
import { useCatalogEntry } from "./catalog";

jest.mock("react-router-dom", () => {
  global.TextEncoder = require("util").TextEncoder;
  const router = jest.requireActual("react-router");
  return { ...router, useParams: jest.fn(), useSearchParams: jest.fn() };
});
jest.mock("@/lib/api", () => ({
  pad: n => String(n).padStart(2, "0"),
  publicApi: { get: jest.fn() },
  setOrigin: jest.fn(),
}));
jest.mock("@/components/public/PublicLayout", () => ({
  useConfig: jest.fn(),
  signupPath: (config, origin) => config.waitlist_mode ? "/waitlist?origin=" + encodeURIComponent(origin) : "/sign-up",
}));
jest.mock("@/components/public/HeroStage", () => ({
  HeroStage: () => <h1>Understand yourself, discover your type, and build better connections.</h1>,
}));

const locked = { number: 2, slug: "locked", unlocked: false };
const released = { number: 1, slug: "released", name: "Released", unlocked: true };
let root, element;

beforeEach(() => {
  invalidatePublicCatalog("/public/archetypes");
  invalidatePublicCatalog("/public/figures");
  global.IS_REACT_ACT_ENVIRONMENT = true;
  global.IntersectionObserver = class { observe() {} disconnect() {} };
  document.title = "Imladri Personal";
  publicApi.get.mockReset();
  useSearchParams.mockImplementation(jest.requireActual("react-router").useSearchParams);
  useConfig.mockReturnValue({});
  element = document.createElement("div");
  root = createRoot(element);
});
afterEach(() => {
  act(() => root.unmount());
  invalidatePublicCatalog("/public/archetypes");
  invalidatePublicCatalog("/public/figures");
  jest.restoreAllMocks();
});

const render = (component, path = "/") => act(async () => {
  root.render(<MemoryRouter initialEntries={[path]}>{component}</MemoryRouter>);
});
const hasLink = href => !!element.querySelector(`a[href="${href}"]`);

function CatalogProbe({ path, onRender = () => {} }) {
  const result = useCatalogEntry(path);
  onRender(result);
  return <button onClick={result.retry}>{result.failed ? "failed" : result.data ? JSON.stringify(result.data) : "loading"}</button>;
}

function CatalogShowcase() {
  const { data, failed, retry } = useCatalogEntry("/public/archetypes");
  const { public_quiz } = useConfig();
  const props = { types: (data || []).filter(type => type.unlocked), catalogState: failed ? "error" : data ? "ready" : "loading",
    retryCatalog: retry, publicQuiz: public_quiz, autoRotate: false };
  return <>{["types", "quiz"].map(tab => <Showcase key={tab} {...props} start={SHOWCASE_TABS.indexOf(tab)} />)}</>;
}

test("the fixed homepage does not wait for or fetch either public catalog", async () => {
  await render(<Home />);
  expect(element.querySelector("h1").textContent).toContain("build better connections");
  expect(publicApi.get).not.toHaveBeenCalled();
  expect(getCachedPublicCatalog("/public/archetypes")).toBeUndefined();
  expect(getCachedPublicCatalog("/public/figures")).toBeUndefined();
});

test("an expired directory remount shows loading, reports failure, and its manual retry fetches current data", async () => {
  let now = 1_000;
  jest.spyOn(Date, "now").mockImplementation(() => now);
  publicApi.get.mockResolvedValueOnce({ data: [released] });
  await render(<CatalogProbe path="/public/archetypes" />);
  await render(<main />);
  now += PUBLIC_CATALOG_TTL_MS;
  let fail;
  publicApi.get.mockReturnValueOnce(new Promise((_, reject) => { fail = reject; }));
  await render(<CatalogProbe path="/public/archetypes" />);
  expect(element.textContent).toBe("loading");
  await act(async () => fail(new Error("offline")));
  expect(element.textContent).toBe("failed");
  let finish;
  publicApi.get.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  await act(async () => element.querySelector("button").click());
  expect(element.textContent).toBe("loading");
  await act(async () => finish({ data: [] }));
  expect(element.textContent).toBe("[]");
  expect(publicApi.get).toHaveBeenCalledTimes(3);
});

test("retrying a pending directory cannot be overwritten by its older response", async () => {
  let finishOld, finishNew;
  publicApi.get.mockReturnValueOnce(new Promise(resolve => { finishOld = resolve; }))
    .mockReturnValueOnce(new Promise(resolve => { finishNew = resolve; }));
  await render(<CatalogProbe path="/public/archetypes" />);
  await act(async () => element.querySelector("button").click());
  await act(async () => finishNew({ data: [{ name: "Current" }] }));
  await act(async () => finishOld({ data: [{ name: "Withdrawn" }] }));
  expect(element.textContent).toContain("Current");
  expect(element.textContent).not.toContain("Withdrawn");
  expect(getCachedPublicCatalog("/public/archetypes")).toEqual([{ name: "Current" }]);
});

test("leaving a pending directory keeps the next view intact and its shared request reusable", async () => {
  let finish;
  publicApi.get.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  await render(<CatalogProbe path="/public/archetypes" />);
  await render(<main>Next page</main>);
  const stillNeeded = loadPublicCatalog("/public/archetypes");
  await act(async () => finish({ data: [released] }));
  await expect(stillNeeded).resolves.toEqual([released]);
  expect(element.textContent).toBe("Next page");
  expect(publicApi.get).toHaveBeenCalledTimes(1);
});

test("a missing detail invalidates only its directory while detail requests always reach the server", async () => {
  publicApi.get.mockResolvedValueOnce({ data: [released] }).mockResolvedValueOnce({ data: [{ name: "Figure" }] });
  await loadPublicCatalog("/public/archetypes");
  await loadPublicCatalog("/public/figures");
  publicApi.get.mockRejectedValueOnce({ response: { status: 404 } });
  await render(<CatalogProbe path="/public/archetypes/released" />);
  expect(element.textContent).toBe("failed");
  expect(getCachedPublicCatalog("/public/archetypes")).toBeUndefined();
  expect(getCachedPublicCatalog("/public/figures")).toEqual([{ name: "Figure" }]);
  await render(<main />);
  publicApi.get.mockResolvedValueOnce({ data: released });
  await render(<CatalogProbe path="/public/archetypes/released" />);
  expect(element.textContent).toContain("Released");
  expect(publicApi.get.mock.calls.filter(([path]) => path === "/public/archetypes/released")).toHaveLength(2);
});

test("archetype card staggering keeps early timing and caps long directories at 300 ms", async () => {
  await render(<><ArchetypeCard a={released} i={2} /><ArchetypeCard a={{ ...released, number: 121 }} i={120} /></>);
  expect(element.querySelector('[data-testid="archetype-card-1"]').style.animationDelay).toBe("50ms");
  expect(element.querySelector('[data-testid="archetype-card-121"]').style.animationDelay).toBe("300ms");
});

test("edition choices work while archetypes load without showing false counts or duplicating the pending request", async () => {
  let finish;
  publicApi.get.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  await render(<Types />, "/types");
  expect(element.querySelector('[role="status"]').textContent).toBe("Loading archetypes…");
  expect(element.querySelector('[data-testid="gender-option-male"]').getAttribute("href")).toBe("/types?g=male");
  const female = element.querySelector('[data-testid="gender-option-female"]');
  expect(female.textContent).not.toContain("0 archetypes");
  await act(async () => female.click());
  expect(element.querySelector('[data-testid="types-title"]').textContent).toBe("Female");
  expect(element.querySelector('[data-testid="types-count"]').textContent).toBe("Loading archetypes…");
  expect(publicApi.get).toHaveBeenCalledTimes(1);
  await act(async () => finish({ data: [{ ...released, gender: "female" }] }));
  expect(element.querySelector('[data-testid="types-count"]').textContent).toBe("1 archetypes");
  expect(hasLink("/types/released")).toBe(true);
});

test("a completed showcase quiz keeps all three choices when the archetype library is delayed and retries after an outage", async () => {
  let fail;
  publicApi.get.mockReturnValueOnce(new Promise((_, reject) => { fail = reject; }));
  await render(<CatalogShowcase />);
  for (let i = 0; i < 3; i++) {
    await act(async () => element.querySelector('[data-testid="showcase-quiz-option-0"]').click());
  }
  const result = () => element.querySelector('[data-testid="showcase-quiz-result"]');
  expect(result().textContent).toContain("Loading archetypes");
  await act(async () => fail(new Error("offline")));
  expect(result().querySelector('[role="alert"]').textContent).toContain("Archetypes couldn’t load");
  expect(result().textContent).not.toContain("not available yet");
  const slug = defaultPublicQuiz.outcomes[0][0][0];
  publicApi.get.mockResolvedValueOnce({ data: [{ ...released, slug }] });
  await act(async () => result().querySelector("button").click());
  expect(result().querySelector('[data-testid="showcase-quiz-read"]').getAttribute("href")).toBe("/types/" + slug);
});

test("a disputed lead links to the two readings and each cited career example", async () => {
  useParams.mockReturnValue({ slug: "example" });
  publicApi.get.mockResolvedValue({ data: {
    name: "Example", slug: "example", identity: "A public speaker", matches: [{ number: 1, why: "Public remarks." }],
    archetype_refs: { 1: released }, evidence_review: { status: "one", readers: [
      { label: "The Advocate", reason: "A request to change policy.", examples: [1] },
      { label: "Insufficient evidence", reason: "One speech gives limited context.", examples: [2] },
    ] }, sections: { communicates: { examples: ["First speech.", "Second speech."] },
      map: { closest: { number: 1 }, basis: { observation: "Spoke to legislators." } } },
  } });
  await render(<FigurePage />);
  expect(element.querySelector('[data-testid="figure-match-chips"]').textContent).toContain("Tentative match");
  const detail = element.querySelector('[data-testid="figure-evidence-review"]');
  expect(detail.textContent).toContain("Insufficient evidence");
  expect(hasLink("/types?q=The%20Advocate")).toBe(true);
  expect(hasLink("/types?q=Insufficient%20evidence")).toBe(false);
  for (const link of detail.querySelectorAll('a[href^="#"]')) expect(element.querySelector(link.hash)).toBeTruthy();
});

test("an example can open the checked page in an additional primary source", async () => {
  useParams.mockReturnValue({ slug: "example" });
  const url = "https://museum.example/biography";
  const alternate = "https://archive.example/speech.pdf";
  publicApi.get.mockResolvedValue({ data: {
    name: "Example", slug: "example", matches: [], source_locators_checked_on: "2026-11-09",
    source_locators: [{example:"Published speech.", url, locator:"electoral roll", page:17, passage_url:alternate}],
    sections:{communicates:{examples:["Published speech.", "Another speech."], example_sources:[
      {example:"Published speech.", url, label:"Museum"}, {example:"Another speech.", url, label:"Museum"},
      {example:"Published speech.", url:"https://catalog.example/record", label:"Catalog record"},
    ]}},
  }});
  await render(<FigurePage />);
  expect(hasLink(alternate + "#page=17")).toBe(true);
  expect([...element.querySelectorAll("#career-example-1 a")].map(link=>link.getAttribute("href")))
    .toEqual([url, alternate + "#page=17", "https://catalog.example/record"]);
  expect(element.querySelector("#career-example-1").textContent).toContain("PDF page 17");
  expect(element.querySelector("#career-example-2").textContent).toContain("still needs verification");
});

test("an unassessed lead is identified as proposed on both the card and the article", async () => {
  const f = { name:"New Figure", slug:"new-figure", matches:[{number:1}], sections:{communicates:{examples:["A dated speech."]}} };
  publicApi.get.mockResolvedValueOnce({data:[f]}).mockResolvedValueOnce({data:f});
  await render(<Figures />);
  expect(element.querySelector("#figure-lead-new-figure").textContent).toContain("Proposed lead");
  useParams.mockReturnValue({slug:f.slug});
  await render(<FigurePage />);
  const lead = element.querySelector('[data-testid="figure-match-chips"]');
  expect(lead.textContent).toContain("Proposed lead");
  expect(lead.textContent).toContain("awaiting its first recorded lead review");
  expect(element.querySelector(lead.querySelector("a").hash)).toBeTruthy();
});

test("an archetype search with a delayed response announces loading before reporting its real matches", async () => {
  let respond;
  publicApi.get.mockReturnValueOnce(new Promise(resolve => { respond = resolve; }));
  await render(<Types />, "/types?q=steady");
  expect(element.querySelector('[role="status"]').textContent).toBe("Loading archetypes…");
  await act(async () => respond({data:[{...released, name:"Steady", gender:"male"}]}));
  expect(element.querySelector('[role="status"]').textContent).toContain("1 matching archetype");
});

test("showcase previews and a three-tap quiz result never link to a locked match", async () => {
  publicApi.get.mockResolvedValue({ data: [{ ...locked, slug: "love-bomber" }, released] });
  await render(<CatalogShowcase />);
  expect(hasLink("/types/released")).toBe(true);
  for (let step = 0; step < 3; step++) {
    await act(async () => { element.querySelector('[data-testid="showcase-quiz-option-0"]').click(); });
  }
  expect(hasLink("/types/love-bomber")).toBe(false);
  expect(element.textContent).toContain("This type is not available yet");
  expect(hasLink("/types")).toBe(true);
});

test("the showcase uses the published questions and result mapping for all three taps", async () => {
  const quiz = JSON.parse(JSON.stringify(defaultPublicQuiz));
  quiz.questions[0].q = "Published first question?";
  quiz.outcomes[0][0][0] = "released";
  quiz.outcomes[0][0][1] = "alternative";
  useConfig.mockReturnValue({ public_quiz: quiz });
  publicApi.get.mockResolvedValue({ data: [released, { ...released, slug: "alternative", name: "Alternative" }] });
  await render(<CatalogShowcase />);
  expect(element.querySelector('[data-testid="showcase-quiz-question"]').textContent).toBe(quiz.questions[0].q);
  const tap = () => act(async () => element.querySelector('[data-testid="showcase-quiz-option-0"]').click());
  await tap();
  useConfig.mockReturnValue({});
  await render(<CatalogShowcase />);
  await tap();
  await tap();
  expect(element.querySelector('[data-testid="showcase-quiz-result"] a').getAttribute("href")).toBe("/types/released");
  const pane = element.querySelector('[data-testid="showcase-quiz-result"]');
  expect(hasLink("/types/alternative")).toBe(true);
  expect(pane.textContent).toContain(quiz.questions[2].a[1]);
  const action = name => [...pane.querySelectorAll("button")].find(b => b.textContent === name);
  const saved = () => JSON.parse(sessionStorage.getItem("imladri:public-quiz:handoff"));
  expect(saved()).toBeNull();
  await act(async () => action("Keep this result for my Start screen").click());
  expect(saved()).toMatchObject({ slug: "released", name: "Released" });
  expect(Object.keys(saved()).sort()).toEqual(["name", "slug", "until"]);
  await act(async () => action("Remove").click());
  expect(saved()).toBeNull();
  await act(async () => element.querySelector('[data-testid="showcase-quiz-restart"]').click());
  expect(element.querySelector('[data-testid="showcase-quiz-question"]').textContent).toBe(defaultPublicQuiz.questions[0].q);
});

test("locked catalog cards and related references cannot open an unavailable article", async () => {
  await render(<><ArchetypeCard a={locked} /><ArchetypeCard a={released} /></>);
  expect(hasLink("/types/locked")).toBe(false);
  expect(element.textContent).toContain("Not available yet");
  expect(hasLink("/types/released")).toBe(true);

  useParams.mockReturnValue({ slug: "released" });
  publicApi.get.mockResolvedValue({ data: {
    ...released, cousin_ref: locked, sections: { closest_cousin: { number: 2 } },
  } });
  await render(<ArchetypePage />);
  expect(hasLink("/types/locked")).toBe(false);
  expect(document.title).toBe("Released · Male Archetype · Imladri");

  useParams.mockReturnValue({ slug: "example" });
  publicApi.get.mockResolvedValue({ data: {
    name: "Example Figure", archetype_refs: { 1: released, 2: locked },
    sections: { map: { closest: { number: 1 }, cousin: { number: 2 } } },
  } });
  await render(<FigurePage />);
  expect(hasLink("/types/locked")).toBe(false);
  expect(hasLink("/types/released")).toBe(true);
  expect(document.title).toBe("Example Figure · Imladri Public Figures");
  publicApi.get.mockResolvedValue({ data: [] });
  await render(<Figures />);
  expect(document.title).toBe("Public figures — Imladri");
  await render(<main />);
  expect(document.title).toBe("Imladri Personal");
});

test("leaving an unavailable slug and late responses cannot replace the current article", async () => {
  useParams.mockReturnValue({ slug: "missing" });
  publicApi.get.mockRejectedValueOnce({ response: { status: 404 } });
  await render(<ArchetypePage />);
  expect(element.querySelector('[data-testid="archetype-missing"]')).toBeTruthy();
  let finishSlowRequest;
  useParams.mockReturnValue({ slug: "slow" });
  publicApi.get.mockReturnValueOnce(new Promise(resolve => { finishSlowRequest = resolve; }));
  await render(<ArchetypePage />);
  expect(element.querySelector('[data-testid="loading"]')).toBeTruthy();
  useParams.mockReturnValue({ slug: "released" });
  publicApi.get.mockResolvedValueOnce({ data: released });
  await render(<ArchetypePage />);
  await act(async () => { finishSlowRequest({ data: { number: 3, name: "Previous" } }); });
  expect(element.querySelector("h1").textContent).toBe("Released");
  expect(document.title).toBe("Released · Male Archetype · Imladri");
});

test.each([
  { slug: "drake", name: "Drake", subject: "He", possessive: "His", object: "Him" },
  { slug: "serena-williams", name: "Serena Williams", gender: "woman", subject: "She", possessive: "Her", object: "Her" },
])("a figure article retains the seven sections, navigation and CTA for $name", async ({ slug, name, gender, subject, possessive, object }) => {
  useParams.mockReturnValue({ slug });
  const localPortrait = "/img/figures/serena-williams.webp";
  const source = "https://www.wtatennis.com/legends/230234/name/record";
  const credit = { artist: "Photographer", source: "https://commons.wikimedia.org/", license: "CC BY 4.0", license_url: "https://creativecommons.org/licenses/by/4.0/" };
  const photo = gender ? { photo_url: localPortrait, photo_attribution: credit, sources: [{ label: "WTA records", url: source }] } : { photo_url: "https://example.com/drake.jpg", photo_credit: "Original image credit" };
  publicApi.get.mockResolvedValue({ data: {
    name, slug, gender, ...photo, matches: [{ number: 1, why: "A public pattern" }], archetype_refs: { 1: released },
    sections: { pattern: ["A documented pattern"], communicates: { examples: ["A communication example"] }, map: { closest: { number: 1 } } },
  } });
  await render(<FigurePage />);
  const sections = [...element.querySelectorAll('section[data-testid^="sec-"]')];
  expect(sections.map(node => node.id)).toEqual(["pattern", "communicates", "spectrum", "handling", "power", "leverage", "map"]);
  expect(sections.map(node => node.querySelector("h2").textContent)).toEqual([
    "The Pattern", `How ${subject} Communicates`, "Interested vs. Invested vs. Done", `Handling ${object}`, `${possessive} Power Moves`, "Your Leverage", "Archetype Map",
  ]);
  for (const id of ["communicates", "handling", "power"]) {
    const heading = element.querySelector(`#${id} h2`).textContent;
    expect(element.querySelector(`[data-testid="toc-${id}"]`).textContent).toBe(heading);
    expect(element.querySelector('[data-testid="doc-topbar"]').textContent).toContain(heading);
  }
  expect(element.querySelector("#pattern").textContent).toContain("A documented pattern");
  expect(hasLink("/types/released")).toBe(true);
  expect(element.querySelector('[data-testid="cta-bridge"]').textContent).toContain(`Not a celebrity — ${object.toLowerCase()}.`);
  expect(element.querySelector('[data-testid="cta-bridge"] a').textContent).toContain(`Make ${possessive.toLowerCase()} profile`);
  if (gender) {
    expect(element.querySelector('[data-testid="figure-sourcing"] a').getAttribute("href")).toBe(source);
    expect(element.querySelector('[data-testid="figure-photo"]').getAttribute("src")).toBe(localPortrait);
    expect(element.querySelector('[data-testid="doc-compact-photo"]').getAttribute("src")).toBe(localPortrait);
    expect(hasLink(credit.source) && hasLink(credit.license_url)).toBe(true);
  } else {
    expect(element.querySelector('[data-testid="figure-sourcing"]').textContent).toContain("Individual claims are not yet linked to sources");
    expect(element.textContent).toContain("Original image credit");
  }
});

test.each([
  [FigurePage, "spectrum"],
  [ArchetypePage, "flags"],
])("dating copy and its navigation appear immediately before the relationship cues (%s)", async (Page, nextId) => {
  useParams.mockReturnValue({ slug: "profile" });
  const dating = ["At the bookshop you both pick a book for the other.", "On the train home you trade notes."];
  publicApi.get.mockResolvedValue({ data: { ...released, slug: "profile", sections: { dating } } });
  await render(<Page />);
  const article = element.querySelector("#dating");
  expect(article.querySelector("h2").textContent).toBe("What it’s like to date them");
  expect([...article.querySelectorAll("p")].map(p => p.textContent)).toEqual(expect.arrayContaining(dating));
  expect(article.nextElementSibling.id).toBe(nextId);
  expect(element.querySelector('[data-testid="toc-dating"]').textContent).toBe("What it’s like to date them");
  expect(element.querySelector('[data-testid="doc-topbar"]').textContent).toContain("What it’s like to date them");
  if (Page === FigurePage) expect(article.textContent).toContain("These scenes are fictional");
});

test("a temporary catalog outage offers a retry and recovers the article", async () => {
  useParams.mockReturnValue({ slug: "released" });
  publicApi.get.mockRejectedValueOnce({ response: { status: 503 } });
  await render(<ArchetypePage />);
  expect(element.querySelector('[role="alert"]')).toBeTruthy();
  expect(element.querySelector('[data-testid="archetype-missing"]')).toBeNull();
  publicApi.get.mockResolvedValueOnce({ data: released });
  await act(async () => element.querySelector('[role="alert"] button').click());
  expect(element.querySelector("h1").textContent).toBe("Released");
});

test("a career citation stays with the right example after staff reorder or rewrite the cards", async () => {
  const citations = ["Original anecdote", "Unchanged anecdote", "Moved anecdote"].map((example, i) => ({
    example, label: `Reference ${i}`, url: `https://example.org/source-${i}`,
  }));
  useParams.mockReturnValue({ slug: "figure" });
  publicApi.get.mockResolvedValue({ data: {
    slug: "figure", name: "Figure", sources: citations, sections: { communicates: {
      examples: ["Moved anecdote", "A staff rewrite", "Unchanged anecdote"], example_sources: citations,
    } },
  } });
  await render(<FigurePage />);
  const cards = [...element.querySelectorAll("#communicates li")];
  expect(cards.map(card => card.querySelector("a")?.getAttribute("href"))).toEqual([citations[2].url, undefined, citations[1].url]);
  expect(cards[1].textContent).toBe("A staff rewrite");
});

test("the directory finds accented and hyphenated names, then restores the full list", async () => {
  publicApi.get.mockResolvedValue({ data: [{ slug: "celine-sciamma", name: "Céline Sciamma" }, { slug: "yo-yo-ma", name: "Yo-Yo Ma" }] });
  await render(<Figures />);
  const input = element.querySelector("input[type=search]");
  const type = value => act(() => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(element.querySelectorAll('a[href^="/figures/"]')).toHaveLength(2);
  expect(element.querySelector('a[href="/figures/celine-sciamma"]').getAttribute("aria-label")).toBe("Céline Sciamma");
  type("celine");
  expect(element.querySelectorAll('a[href^="/figures/"]')).toHaveLength(1);
  expect(hasLink("/figures/celine-sciamma")).toBe(true);
  type("yoyo");
  expect(hasLink("/figures/yo-yo-ma")).toBe(true);
  type("absent");
  expect(element.textContent).toContain("No matching names");
  await act(() => element.querySelector("button").click());
  expect(element.querySelectorAll('a[href^="/figures/"]')).toHaveLength(2);
});

test("name searches cover the entire directory, across initials, word order and transliterations", async () => {
  const firstPage = Array.from({ length: 48 }, (_, i) => ({ name: `Author ${i}`, slug: `author-${i}` }));
  publicApi.get.mockResolvedValue({ data: [...firstPage, { name: "Franklin Delano Roosevelt", slug: "franklin-delano-roosevelt" }, { name: "Gro Harlem Brundtland", slug: "gro-harlem-brundtland" }, { name: "Lech Wałęsa", slug: "lech-wałęsa" }] });
  await render(<Figures />);
  expect(hasLink("/figures/franklin-delano-roosevelt")).toBe(false);
  await act(() => element.querySelector("button").click());
  expect(hasLink("/figures/franklin-delano-roosevelt")).toBe(true);
  const input = element.querySelector('[role="search"] input');
  for (const [query, slug] of [["brundtland gro", "gro-harlem-brundtland"], ["F. Roosevelt", "franklin-delano-roosevelt"], ["FDR", "franklin-delano-roosevelt"], ["Franklin Roosevelt", "franklin-delano-roosevelt"], ["walesa", "lech-wałęsa"]]) {
    act(() => {
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(input, query);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(element.querySelector('[role="status"]').textContent).toBe("1 of 51 profiles");
    expect(hasLink(`/figures/${slug}`)).toBe(true);
  }
});

test("a local-script name, field and archetype filter combine on a shared link", async () => {
  const row = {name: "Benazir Bhutto", slug: "benazir-bhutto", field: "Politics", name_variants: ["بینظیر بھٹو"], matches: [{number:1,name:"The Advocate"}]};
  publicApi.get.mockResolvedValue({data:[row,{...row,slug:"different-field",field:"Film"},{...row,slug:"different-style",matches:[{number:2,name:"The Leader"}]}]});
  await render(<Figures />, "/figures?" + new URLSearchParams({q:"بینظیر بھٹو",field:"Politics",style:"The Advocate"}));
  expect(hasLink("/figures/benazir-bhutto")).toBe(true);
  expect(hasLink("/figures/different-field")).toBe(false);
  expect(hasLink("/figures/different-style")).toBe(false);
});

test("a shared directory search opens with the requested name and matching profile", async () => {
  publicApi.get.mockResolvedValue({ data: [{ name: "Benazir Bhutto", slug: "benazir-bhutto" }, { name: "Barbara Jordan", slug: "barbara-jordan" }] });
  await render(<Figures />, "/figures?q=bhutto");
  expect(element.querySelector("input[type=search]").value).toBe("bhutto");
  expect(hasLink("/figures/benazir-bhutto")).toBe(true);
  expect(hasLink("/figures/barbara-jordan")).toBe(false);
});

test("a reader can finish a figure and return to the same filters and expanded page", async () => {
  useParams.mockImplementation(jest.requireActual("react-router").useParams);
  const rows = Array.from({ length: 50 }, (_, i) => ({
    name: "Author " + i, slug: "author-" + i, field: "Writing", matches: [{ number: 1, name: "Guide" }],
  }));
  publicApi.get.mockImplementation(async path => ({ data: path.endsWith("/author-49") ? rows[49] : rows }));
  const path = "/figures?q=Author&field=Writing&archetype=1&role=lead&page=2";
  await render(<Routes><Route path="/figures" element={<Figures />} /><Route path="/figures/:slug" element={<FigurePage />} /></Routes>, path);
  await act(async () => element.querySelector('a[href="/figures/author-49"]').click());
  const back = element.querySelector('[data-testid="back-link"]');
  expect(back.textContent).toBe("Back to results");
  expect(back.getAttribute("href")).toBe(path);
  expect(element.querySelector('[data-testid="back-link-mobile"]').getAttribute("href")).toBe(path);
  await act(async () => back.click());
  expect(element.querySelector("input[type=search]").value).toBe("Author");
  expect(element.querySelectorAll('a[href^="/figures/"]')).toHaveLength(50);
});

test("rapid typing keeps every character while URL replacement is pending", async () => {
  const replace = jest.fn();
  useSearchParams.mockImplementation(() => [new URLSearchParams(), replace]);
  publicApi.get.mockResolvedValue({ data: [{ name: "Franklin D. Roosevelt", slug: "franklin-d-roosevelt" }] });
  await render(<Figures />);
  const input = element.querySelector("input[type=search]");
  for (const letter of "FDR") {
    act(() => {
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(input, input.value + letter);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
  expect(input.value).toBe("FDR");
  expect(replace.mock.calls.at(-1)[0].get("q")).toBe("FDR");
  expect(hasLink("/figures/franklin-d-roosevelt")).toBe(true);
});


test("archetype search matches behavior tags across editions and excludes unreleased results", async () => {
  publicApi.get.mockResolvedValue({ data: [
    { ...released, slug: "alpha", gender: "male", name: "Alpha", texting_tags: ["over-texter"] },
    { ...released, slug: "beta", gender: "female", name: "Beta", texting_tags: ["over-texter"] },
    { ...locked, gender: "female", name: "Draft", texting_tags: ["over-texter"] },
  ] });
  await render(<Types />, "/types?q=ovér%20texter");
  expect(element.querySelector('section[aria-label="Male edition"] a').getAttribute("href")).toBe("/types/alpha");
  expect(element.querySelector('section[aria-label="Female edition"] a').getAttribute("href")).toBe("/types/beta");
  expect(element.querySelector('[role="status"]').textContent).toContain("2 matching archetypes");
});

test("a figure link from an archetype keeps its edition and distinguishes lead and secondary matches", async () => {
  const male = {number:1, name:"The Advocate", edition:"male"};
  const female = {number:101, name:"The Advocate", edition:"female"};
  const other = {number:150, name:"The Reframer", edition:"female"};
  publicApi.get.mockResolvedValue({data:[
    {name:"Al",slug:"al",matches:[male]},
    {name:"Bo",slug:"bo",matches:[female],lead_review_status:"insufficient"},
    {name:"Cy",slug:"cy",matches:[other, female]},
  ]});
  await render(<Figures/>, "/figures?archetype=101");
  expect(hasLink("/figures/al")).toBe(false);
  expect(hasLink("/figures/cy")).toBe(true);
  const lead = element.querySelector("#figure-lead-bo");
  expect(lead.textContent).toContain("limited evidence");
  expect(element.querySelector('[aria-label=Bo]').getAttribute("aria-describedby")).toBe(lead.id);
  await act(() => element.querySelector('[type=checkbox]').click());
  expect(hasLink("/figures/cy")).toBe(false);
  expect(hasLink("/figures/bo")).toBe(true);
  expect(element.querySelector('[role=status]').textContent).toBe("1 of 3 profiles");
});

test("a shared red-flag filter restores its pressed state and a closed panel has no keyboard stops", async () => {
  publicApi.get.mockResolvedValue({data:[
    {...released,slug:"visible",gender:"male",red_flag_tags:["dismissive"]},
    {...released,number:2,slug:"other",gender:"male",red_flag_tags:["withdraws"]},
  ]});
  await render(<Types/>, "/types?g=male&flag=dismissive");
  expect(element.querySelector('[data-testid=redflag-dismissive]').getAttribute("aria-pressed")).toBe("true");
  expect(hasLink("/types/visible")).toBe(true);
  expect(hasLink("/types/other")).toBe(false);
  await act(() => element.querySelector('[data-testid=redflag-toggle]').click());
  expect(element.querySelector("#archetype-flags").hidden).toBe(true);
  expect(element.querySelector("#archetype-flags button")).toBeNull();
});

test("published metadata follows article changes and resets when leaving the public catalog", async () => {
  const read = key => document.head.querySelector('meta[property="og:' + key + '"]')?.content;
  const canonical = () => document.head.querySelector('link[rel="canonical"]')?.href;
  function StaticPage({ path }) {
    usePublicPageHead(path);
    return null;
  }
  const source = { name: "First", identity: "Source one.", photo_url: "/img/first.webp" };
  useParams.mockReturnValue({ slug: "first" });
  publicApi.get.mockResolvedValue({ data: source });
  await render(<FigurePage />);
  expect(canonical()).toBe("https://imladri.com/figures/first");
  expect(read("image")).toBe("https://imladri.com/img/first.webp");
  useParams.mockReturnValue({ slug: "next" });
  publicApi.get.mockResolvedValue({ data: { ...source, name: "Next", identity: "Source two." } });
  await render(<FigurePage />);
  expect(canonical()).toBe("https://imladri.com/figures/next");
  expect(read("title")).toContain("Next");
  expect(read("description")).toBe("Source two.");
  await render(<StaticPage path="/plans" />);
  expect(canonical()).toBe("https://imladri.com/plans");
  expect(read("type")).toBe("website");
  expect(read("title")).toContain("Plans");
  expect(read("image")).not.toContain("first.webp");
  await render(<StaticPage path="/sign-in" />);
  expect(canonical()).toBeUndefined();
  expect(read("type")).toBeUndefined();
});

test.each(["?g=female&q=steady&flag=slow&flag=distant", "?q=steady"])(
  "a reader comparing a neighboring archetype can resume the original results (%s)", async search => {
    useParams.mockImplementation(jest.requireActual("react-router").useParams);
    const guide = { ...released, number: 2, name: "Guide", slug: "guide", gender: "female" };
    const mentor = { ...released, name: "Steady mentor", slug: "mentor", gender: "female",
      red_flag_tags: ["slow", "distant"], sections: { closest_cousin: { number: 2, tell: "Look for a suggestion." } }, cousin_ref: guide };
    publicApi.get.mockImplementation(async path => ({ data: path.endsWith("/guide") ? guide :
      path.endsWith("/mentor") ? mentor : [mentor, guide] }));
    await render(<Routes><Route path="/types" element={<Types />} /><Route path="/types/:slug" element={<ArchetypePage />} /></Routes>, "/types" + search);
    await act(async () => element.querySelector('a[href="/types/mentor"]').click());
    await act(async () => element.querySelector('[data-testid="cousin-link"]').click());
    expect(element.querySelector("h1").textContent).toBe("Guide");
    const back = element.querySelector('[data-testid="back-link"]');
    expect(back.textContent).toBe("Back to results");
    expect(back.getAttribute("href")).toBe("/types" + search);
    await act(async () => back.click());
    expect(element.querySelector('input[type="search"]').value).toBe("steady");
    if (search.includes("flag=")) {
      for (const flag of ["slow", "distant"]) {
        expect(element.querySelector('[data-testid="redflag-' + flag + '"]').getAttribute("aria-pressed")).toBe("true");
      }
    } else {
      expect(element.querySelector('[role="status"]').textContent).toContain("across both editions");
    }
  }
);
