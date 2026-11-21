import assert from "node:assert/strict";
import test from "node:test";
import { handle } from "../src/edge.js";
import { publicPages } from "../../frontend/src/lib/publicSeo.mjs";

const origin = "https://imladri.com";
const shell = '<!doctype html><html lang="en"><head><title>Old title</title><meta name="description" content="old"/><meta name="robots" content="noindex"/><link rel="canonical" href="https://old.invalid"/><meta property="og:title" content="old"/><meta name="twitter:title" content="old"/><script type="application/ld+json" id="imladri-seo-schema">{}</script></head><body><div id="root"></div></body></html>';
const request = (path, method = "GET", host = origin) => new Request(host + path, {method,
  headers: {Accept: "text/html", Cookie: "private=session", Authorization: "Bearer private"}});
const services = () => ({ASSETS: {fetch: async req => {
  assert.equal(new URL(req.url).pathname, "/");
  assert.equal(req.method, "GET");
  assert.equal(req.headers.get("Cookie"), null);
  assert.equal(req.headers.get("Authorization"), null);
  return new Response(shell, {headers: {ETag: "stale-shell", "Content-Length": String(shell.length)}});
}}});

test("every public static route sends unique complete metadata before JavaScript, with HEAD parity", async () => {
  const titles = new Set();
  for (const [path, [title, description]] of Object.entries(publicPages)) {
    const res = await handle(request(path + "?source=private"), services());
    assert.equal(res.status, 200);
    assert.equal(res.headers.get("X-Robots-Tag"), null);
    assert.equal(res.headers.get("ETag"), null);
    assert.equal(res.headers.get("Content-Length"), null);
    const html = await res.text();
    assert.equal([...html.matchAll(/<title>/g)].length, 1);
    assert.equal([...html.matchAll(/rel="canonical"/g)].length, 1);
    assert.equal([...html.matchAll(/id="imladri-seo-schema"/g)].length, 1);
    assert.ok(html.includes(title.replace(/&/g, "&amp;")));
    assert.ok(html.includes(description));
    assert.ok(html.includes('href="' + origin + path + '"'));
    assert.ok(html.includes('content="index, follow, max-image-preview:large"'));
    for (const field of ["title", "description", "image", "image:alt"]) assert.ok(html.includes('name="twitter:' + field + '"'));
    assert.ok(!html.includes("source=private") && !html.includes("old.invalid"));
    const schema = JSON.parse(html.match(/id="imladri-seo-schema">([\s\S]*?)<\/script>/)[1]);
    assert.equal(schema["@graph"][2].url, origin + path);
    assert.ok(!JSON.stringify(schema).includes("address"));
    assert.ok(!JSON.stringify(schema).includes("telephone"));
    titles.add(title);
    const head = await handle(request(path, "HEAD"), services());
    assert.equal(head.status, 200);
    assert.equal(await head.text(), "");
  }
  assert.equal(titles.size, Object.keys(publicPages).length);
});

test("private workspaces, admin, auth continuations, invitations and waitlist remain functional and non-indexable", async () => {
  for (const path of ["/app", "/app/your-type", "/app/couples/answers", "/dating", "/dating/messages/private-id", "/legacy", "/legacy/plans", "/admin/research", "/sign-in", "/sign-up/verify-email-address", "/login", "/signup", "/waitlist", "/upgrade", "/couples/invite"]) {
    const res = await handle(request(path + "?token=private-id"), services());
    assert.equal(res.status, 200, path);
    assert.equal(res.headers.get("X-Robots-Tag"), "noindex, nofollow");
    const html = await res.text();
    assert.ok(html.includes('name="robots" content="noindex, nofollow"'));
    for (const secret of ["canonical", "og:", "twitter:", "private-id", "imladri-seo-schema"]) assert.ok(!html.includes(secret), path + ": " + secret);
    assert.ok(html.includes('<div id="root"></div>'));
  }
});

test("preview static and private documents cannot be indexed", async () => {
  for (const path of ["/", "/plans", "/privacy", "/app/personal", "/admin"]) {
    const res = await handle(request(path, "GET", "https://imladri-personal-preview.imladri.workers.dev"), services());
    assert.equal(res.status, 200);
    assert.equal(res.headers.get("X-Robots-Tag"), "noindex, nofollow");
    assert.ok((await res.text()).includes('name="robots" content="noindex, nofollow"'));
  }
});

test("unknown HTML routes have a genuine noindex404 instead of a successful home-page shell", async () => {
  for (const method of ["GET", "HEAD"]) {
    const res = await handle(request("/missing-route", method), services());
    assert.equal(res.status, 404);
    assert.equal(res.headers.get("X-Robots-Tag"), "noindex, nofollow");
    assert.equal((await res.text()).includes("Page not found"), method === "GET");
  }
});

test("article metadata replaces static defaults and safely encodes structured data", async () => {
  const item = {name: 'Name </script><script>alert("x")</script>', identity: "Career & communication", photo_url: "/img/couple.jpg"};
  const env = services();
  env.API = {fetch: async () => Response.json(item)};
  const res = await handle(request("/figures/example?tracking=private"), env);
  const html = await res.text();
  assert.equal(res.status, 200);
  assert.equal([...html.matchAll(/<title>/g)].length, 1);
  assert.equal([...html.matchAll(/property="og:title"/g)].length, 1);
  assert.equal([...html.matchAll(/name="twitter:title"/g)].length, 1);
  assert.equal([...html.matchAll(/rel="canonical"/g)].length, 1);
  assert.ok(!html.includes('<script>alert("x")'));
  const data = JSON.parse(html.match(/id="imladri-seo-schema">([\s\S]*?)<\/script>/)[1]);
  assert.equal(data["@graph"][2].name, item.name + " · Imladri Public Figures");
  assert.ok(!html.includes("tracking=private"));
});

test("HTTP apex redirects to the canonical HTTPS URL with its path and query intact", async () => {
  const res = await handle(new Request("http://imladri.com/plans?from=share"), {});
  assert.equal(res.status, 308);
  assert.equal(res.headers.get("Location"), origin + "/plans?from=share");
});

test("the homepage social card is versioned, shared by OG and Twitter, and available before JavaScript", async () => {
  const res = await handle(request("/"), services()); const html = await res.text();
  const image = origin + "/img/imladri-dating-social-v1.png";
  assert.ok(html.includes('<meta property="og:image" content="' + image + '"/>'));
  assert.ok(html.includes('<meta name="twitter:image" content="' + image + '"/>'));
  assert.ok(html.includes("Imladri | Meet Your Type. Understand Your Fit."));
  assert.ok(html.includes("intentional dating built around attraction"));
  assert.ok(!html.includes("/img/couple.jpg"));
});
