/** @jest-environment ./src/pages/public/seo.testEnvironment.cjs */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { RouteIndexing, useArticleHead, usePublicPageHead } from "./publicHead";

jest.mock("react-router-dom", () => {
  global.TextEncoder = require("util").TextEncoder;
  return jest.requireActual("react-router");
});

let root, element, go;
function Navigation() { go = useNavigate(); return null; }
function PublicHead() { const { pathname, hash } = useLocation(); usePublicPageHead(pathname, hash); return null; }
function MissingArticle() { useArticleHead("figures", "missing", null, true); return null; }
const meta = key => document.head.querySelector('meta[' + key + ']');

beforeEach(() => {
  expect(window.location.hostname).toBe("imladri.com");
  global.IS_REACT_ACT_ENVIRONMENT = true;
  element = document.createElement("div");
  root = createRoot(element);
});
afterEach(() => { act(() => root.unmount()); document.head.querySelectorAll('meta,link[rel="canonical"],#imladri-seo-schema').forEach(node => node.remove()); });

test("public navigation fills complete sharing metadata then private navigation removes it without serializing user IDs", async () => {
  await act(async () => root.render(<MemoryRouter initialEntries={["/plans"]}><RouteIndexing /><Navigation /><PublicHead /></MemoryRouter>));
  expect(document.head.querySelector('link[rel="canonical"]').href).toBe("https://imladri.com/plans");
  expect(meta('name="robots"').content).toBe("index, follow, max-image-preview:large");
  expect(meta('name="twitter:title"').content).toBe(document.title);
  expect(JSON.parse(document.getElementById("imladri-seo-schema").textContent)["@graph"][2].url).toBe("https://imladri.com/plans");
  await act(async () => go("/app/person/private-person-id?thread=private-thread"));
  expect(meta('name="robots"').content).toBe("noindex, nofollow");
  expect(document.head.querySelector('link[rel="canonical"]')).toBeNull();
  expect(meta('property="og:title"')).toBeNull();
  expect(meta('name="twitter:title"')).toBeNull();
  expect(document.getElementById("imladri-seo-schema")).toBeNull();
  expect(document.head.innerHTML).not.toContain("private-person-id");
  expect(document.head.innerHTML).not.toContain("private-thread");
});

test("a private support fragment clears public metadata and never appears in the head", async () => {
  const receipt = "a".repeat(64);
  await act(async () => root.render(<MemoryRouter initialEntries={["/support"]}><RouteIndexing /><Navigation /><PublicHead /></MemoryRouter>));
  expect(document.head.querySelector('link[rel="canonical"]').href).toBe("https://imladri.com/support");
  expect(meta('name="robots"').content).toBe("index, follow, max-image-preview:large");
  await act(async () => go("/support#" + receipt));
  expect(document.title).toBe("Private support request | Imladri");
  expect(meta('name="robots"').content).toBe("noindex, nofollow");
  expect(document.head.querySelector('link[rel="canonical"]')).toBeNull();
  expect(document.head.innerHTML).not.toContain(receipt);
});

test("hash navigation cannot re-enable indexing for an unpublished article", async () => {
  await act(async () => root.render(<MemoryRouter initialEntries={["/figures/missing"]}><RouteIndexing /><Navigation /><MissingArticle /></MemoryRouter>));
  expect(meta('name="robots"').content).toBe("noindex, nofollow");
  await act(async () => go("/figures/missing#career"));
  expect(meta('name="robots"').content).toBe("noindex, nofollow");
  expect(document.head.querySelector('link[rel="canonical"]')).toBeNull();
});

test("the home share preview uses the new versioned artwork and matching copy on client navigation", async () => {
  await act(async () => root.render(<MemoryRouter initialEntries={["/"]}><RouteIndexing /><Navigation /><PublicHead /></MemoryRouter>));
  expect(document.title).toBe("Imladri | Meet Your Type. Understand Your Fit.");
  expect(meta('name="description"').content).toContain("intentional dating");
  for (const key of ['property="og:image"', 'name="twitter:image"']) expect(meta(key).content).toBe("https://imladri.com/img/imladri-dating-social-v1.png");
  expect(meta('name="twitter:card"').content).toBe("summary_large_image");
  await act(async () => go("/app"));
  expect(meta('property="og:image"')).toBeNull(); expect(meta('name="twitter:image"')).toBeNull();
});

test.each(["/dating/discover?member=private-id", "/legacy/plans", "/legacy/how-it-works"])("%s does not expose public search metadata", async path => {
  await act(async () => root.render(<MemoryRouter initialEntries={["/"]}><RouteIndexing /><Navigation /><PublicHead /></MemoryRouter>));
  await act(async () => go(path));
  expect(meta('name="robots"').content).toBe("noindex, nofollow");
  expect(document.head.querySelector('link[rel="canonical"]')).toBeNull();
  expect(meta('property="og:title"')).toBeNull();
  expect(document.head.innerHTML).not.toContain("private-id");
});
