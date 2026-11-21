import assert from "node:assert/strict";
import test from "node:test";
import { handle } from "../src/edge.js";

const site = "https://imladri.com";
const shell = '<!doctype html><html><head><title>Imladri</title><meta name="description" content="Generic"/></head><body><div id="root"></div></body></html>';
const request = (path, method = "GET", origin = site) => new Request(origin + path, {method, headers: {Cookie: "fixture=private", Authorization: "Bearer fixture-only"}});
function services(read, template = shell) {
  const anonymous = r => {
    assert.equal(r.headers.get("cookie"), null);
    assert.equal(r.headers.get("authorization"), null);
  };
  return {ASSETS: {fetch: async r => { anonymous(r); return new Response(template); }},
    API: {fetch: async r => { anonymous(r); return read(new URL(r.url).pathname); }}};
}

test("anonymous first HTML uses the currently published article and escapes editorial punctuation", async () => {
  let name = 'M & "$"';
  const env = services(path => {
    assert.equal(path, "/api/public/figures/one-name");
    return Response.json({name, identity: 'A < B & C', photo_url: "/api/media/cover.jpg"});
  });
  const html = await (await handle(request("/figures/one-name?source=private"), env)).text();
  assert.ok(html.includes('<title>M &amp; &quot;$&quot; · Imladri Public Figures</title>'));
  assert.ok(html.includes('property="og:description" content="A &lt; B &amp; C"'));
  assert.ok(html.includes('rel="canonical" href="https://imladri.com/figures/one-name"'));
  assert.ok(html.includes('content="https://imladri.com/api/media/cover.jpg"'));
  assert.ok(html.includes('<main><h1>M &amp; &quot;$&quot;</h1>'));
  assert.ok(!html.includes("source=private"));
  name = "Edited today";
  assert.match(await (await handle(request("/figures/one-name"), env)).text(), /<h1>Edited today<\/h1>/);
  const head = await handle(request("/figures/one-name", "HEAD", "https://preview.example.test"), env);
  assert.equal(head.headers.get("X-Robots-Tag"), "noindex"); assert.equal(await head.text(), "");
});

test("sitemap is unique, excludes unlisted records and follows withdrawals; preview forbids crawling", async () => {
  const paths = [...Array.from({length:121}, (_,i) => "/types/type-"+i), ...Array.from({length:500}, (_,i) => "/figures/person-"+i)];
  const env = services(path => {
    assert.equal(path, "/api/public/catalog-paths");
    return Response.json([...paths, paths[0], "/app/people", "/dating/discover", "/legacy/plans", "/admin", "https://unrelated.invalid"]);
  });
  const html = await (await handle(request("/sitemap.xml"), env)).text();
  assert.equal([...html.matchAll(/<loc>/g)].length, 632);
  assert.ok(html.includes("https://imladri.com/learn"));
  assert.ok(!html.includes("/admin") && !html.includes("/app/") && !html.includes("/dating/") && !html.includes("/legacy/") && !html.includes("unrelated"));
  paths.pop();
  assert.ok(!(await (await handle(request("/sitemap.xml"), env)).text()).includes("/figures/person-499"));
  assert.match(await (await handle(request("/robots.txt"), env)).text(), /Sitemap: https:\/\/imladri.com\/sitemap.xml/);
  assert.match(await (await handle(request("/robots.txt", "GET", "https://preview.example.test"), env)).text(), /Disallow: \/\n$/);
});

test("an unpublished article is a real 404; a temporary origin failure is retryable", async () => {
  for (const [upstream, code] of [[404,404],[500,503]]) {
    const result = await handle(request("/types/example"), services(() => new Response(null, {status: upstream})));
    assert.equal(result.status, code); assert.equal(result.headers.get("X-Robots-Tag"), "noindex");
    if (code === 503) assert.equal(result.headers.get("Retry-After"), "30");
  }
  const result = await handle(request("/sitemap.xml"), services(() => new Response(null,{status:500})));
  assert.equal(result.status, 503);
});

test("same-named archetypes have edition-specific share titles and safe, accurate image descriptions", async () => {
  for (const [gender, photo, expectedPhoto, expectedAlt] of [
    ["female", "/api/media/portrait.png", "/api/media/portrait.png", "Example character"],
    ["male", "/img/../private/portrait.png", "/img/imladri-dating-social-v1.png", "Imladri — meet your type. Understand your fit. Intentional dating, your attraction, your preferences, your choice."],
  ]) {
    const data = {name: "The Advocate", gender, identity: "Asks for a change.", sections: {characters: [{name: "Example character", image_url: photo}]}};
    const result = await handle(request("/types/example"), services(() => Response.json(data)));
    const html = await result.text();
    assert.ok(html.includes("The Advocate · " + (gender === "female" ? "Female" : "Male") + " Archetype · Imladri"));
    assert.ok(html.includes('property="og:image" content="' + site + expectedPhoto + '"'));
    assert.ok(html.includes('property="og:image:alt" content="' + expectedAlt + '"'));
  }
});
