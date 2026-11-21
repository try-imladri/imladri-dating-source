import assert from "node:assert/strict";
import test from "node:test";
import { checkOperations, handle } from "../src/edge.js";

test("PostgreSQL routing never requests Atlas ingress repair, including midnight and a failed primary", async () => {
  for (const ready of [true, false]) {
    const pending = [];
    const api = {fetch: async request => new URL(request.url).pathname === "/api/ready"
      ? Response.json({ready}) : new Response(null, {status: 200})};
    const env = {API: api, IMLADRI_DATABASE_BACKEND: "postgresql", OPS_CHECK_SECRET: "fixture-ops",
      RENDER_FAILOVER_MODE: "auto", RENDER_FAILOVER_ORIGIN: "https://imladri-personal-live-api.onrender.com",
      RENDER_ORIGIN_SECRET: "fixture-origin"};
    let repair = 0;
    await checkOperations(env, async request => {
      if (new URL(request.url).pathname === "/api/ops/repair-ingress") repair++;
      return new Response(null, {status: 200});
    }, {waitUntil: task => pending.push(task)}, Date.UTC(2026, 10, 11, 0, 5));
    await Promise.all(pending);
    assert.equal(repair, 0);
    assert.equal(pending.length, 0);
  }
});

const origin = "https://api.example.invalid";
const page = "https://imladri.com";

test("intro network signals use only the Cloudflare client address on both protected routes", async () => {
  for (const ready of [true, false]) {
    for (const trustedIP of ["198.51.100.24", null]) {
      let captured;
      const receive = request => {
        captured = new Headers(request.headers);
        return new Response("ok", {headers: {"X-Imladri-Origin": "never-public", "X-Imladri-Client-IP": "never-public"}});
      };
      const env = {API: {fetch: async request => new URL(request.url).pathname === "/api/ready"
        ? Response.json({ready}) : receive(request)}, RENDER_FAILOVER_MODE: "auto",
        RENDER_FAILOVER_ORIGIN: "https://imladri-personal-live-api.onrender.com", RENDER_ORIGIN_SECRET: "fixture-origin"};
      const headers = {"X-Imladri-Client-IP": "192.0.2.99", "X-Imladri-Origin": "spoofed", "X-Forwarded-For": "192.0.2.98"};
      if (trustedIP) headers["CF-Connecting-IP"] = trustedIP;
      const response = await handle(new Request(page + "/api/me/intro-access/claim", {method: "POST", headers, body: "{}"}), env,
        async request => receive(request));
      assert.equal(captured.get("X-Imladri-Client-IP"), trustedIP);
      assert.equal(captured.get("X-Forwarded-For"), trustedIP);
      assert.equal(captured.get("X-Imladri-Origin"), "fixture-origin");
      assert.equal(response.headers.get("X-Imladri-Origin"), null);
      assert.equal(response.headers.get("X-Imladri-Client-IP"), null);
    }
  }
});

test("browser security headers cover assets, pages, API errors and redirects without replacing upstream policies", async () => {
  const env = {ASSETS: {fetch: async () => new Response("asset", {headers:{"Content-Type":"text/plain"}})}};
  for (const path of ["/plans", "/static/app.js", "/api/public/plans", "/api/ops/repair-ingress", "/sso-callback"]) {
    const response = await handle(new Request(page+path), env);
    assert.equal(response.headers.get("X-Content-Type-Options"), "nosniff", path);
    assert.equal(response.headers.get("X-Frame-Options"), "SAMEORIGIN", path);
    assert.equal(response.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin", path);
    assert.equal(response.headers.get("Strict-Transport-Security"), "max-age=31536000", path);
    if (path === "/static/app.js") assert.equal(await response.text(), "asset");
  }
  const upstream = {ASSETS:{fetch:async()=>new Response("kept",{headers:{
    "X-Frame-Options":"DENY","Referrer-Policy":"no-referrer","Strict-Transport-Security":"max-age=63072000",
    "Set-Cookie":"test=one; HttpOnly; Secure; SameSite=Lax"
  }})}};
  const response=await handle(new Request(page+"/static/other.js"),upstream);
  assert.equal(response.headers.get("X-Frame-Options"),"DENY");
  assert.equal(response.headers.get("Referrer-Policy"),"no-referrer");
  assert.equal(response.headers.get("Strict-Transport-Security"),"max-age=63072000");
  assert.equal(response.headers.get("Set-Cookie"),"test=one; HttpOnly; Secure; SameSite=Lax");
  const local=await handle(new Request("http://localhost/static/app.js"),env);
  assert.equal(local.headers.get("Strict-Transport-Security"),null);
});

test("www canonicalizes every method, path and query before assets and the origin", async () => {
  for (const path of ["/", "/static/js/main.js?a=1", "/api/billing/webhook?test=one"]) {
    const res = await handle(new Request("http://www.imladri.com" + path, { method: "POST" }), {});
    assert.equal(res.status, 308);
    assert.equal(res.headers.get("Location"), page + path);
  }
});

test("links from the public sitemap work with generic GET and HEAD clients without catching missing assets or unknown paths", async () => {
  const env = {ASSETS: {fetch: async r => new Response(r.method === "HEAD" ? null : "shell", {status: new URL(r.url).pathname === "/" ? 200 : 404})}};
  for (const path of ["/plans", "/about/", "/terms", "/figures", "/types", "/how-it-works", "/support"]) {
    for (const method of ["GET", "HEAD"]) for (const headers of [{}, {Accept:"*/*"}]) {
      const res = await handle(new Request(page+path, {method, headers}), env);
      assert.equal(res.status, 200); assert.equal(await res.text(), method === "HEAD" ? "" : "shell");
    }
  }
  for (const path of ["/does-not-exist", "/static/absent.js"]) {
    assert.equal((await handle(new Request(page+path, {headers:{Accept:"*/*"}}), env)).status,404);
  }
  assert.equal((await handle(new Request(page+"/plans", {headers:{Accept:"application/json"}}), env)).status,404);
});

test("legacy Google callbacks redirect directly to onboarding, preserving Clerk query state", async () => {
  const query = "?state=a%2Fb&code=sample&state=2";
  for (const method of ["GET", "HEAD"]) {
    for (const source of [page + "/sso-callback", "http://www.imladri.com/sso-callback/"]) {
      const res = await handle(new Request(source + query, { method }), {});
      assert.equal(res.status, 307);
      assert.equal(res.headers.get("Location"), page + "/app/start" + query);
      assert.equal(await res.text(), "");
    }
  }
});

test("candidate permits public readiness and protected ops, concealing other live APIs", async () => {
  const env = { READINESS_ONLY: "true", API: { fetch: async () => new Response(null, { status: 204 }) } };
  for (const path of ["/api/", "/api/ready", "/api/public/archetypes", "/api/public/research/repeat", "/api/media/seed-id", "/api/clerk/config"]) {
    assert.equal((await handle(new Request(page + path), env)).status, 204);
  }
  assert.equal((await handle(new Request(page + "/api/ops/check", { method: "POST" }), env)).status, 204);
  assert.equal((await handle(new Request(page + "/api/upgrade"), env)).status, 404);
  assert.equal((await handle(new Request(page + "/api/public/archetypes", { method: "POST" }), env)).status, 404);
  assert.equal((await handle(new Request(page + "/api/"), {})).status, 503);
});

test("only an explicitly selected production Render service can take over private API traffic", async () => {
  const req = new Request(page + "/api/", { headers: { "X-Imladri-Origin": "spoofed" } });
  const service = { fetch: () => { throw new Error("unexpected container request"); } };
  const target = "https://imladri-personal-live-api.onrender.com";
  const env = { API: service, RENDER_FAILOVER_ORIGIN: target, RENDER_ORIGIN_SECRET: "private-standby-test" };
  const fetchOrigin = received => {
    assert.equal(new URL(received.url).origin, target);
    assert.equal(received.headers.get("X-Imladri-Origin"), "private-standby-test");
    return new Response(null, { status: 204 });
  };
  assert.equal((await handle(req, env, fetchOrigin)).status, 204);
  assert.equal((await handle(req, { ...env, RENDER_ORIGIN_SECRET: "" }, fetchOrigin)).status, 503);
  await checkOperations({ ...env, OPS_CHECK_SECRET: "test" }, fetchOrigin);
  for (const invalid of ["https://imladri-personal-pilot.onrender.com", "https://host.invalid", target + "/api", "invalid"]) {
    assert.equal((await handle(req, { ...env, RENDER_FAILOVER_ORIGIN: invalid }, fetchOrigin)).status, 503);
  }
});

for (const mode of ["render", "container"]) {
  test(`${mode} forwards auth, webhook bytes and separate cookies without caching`, async () => {
    const bytes = new Uint8Array([123, 34, 195, 169, 34, 58, 49, 125, 10]);
    const headers = {
      Authorization: "Bearer fixture", Cookie: "fixture=1", "Stripe-Signature": "fixture-signature",
      "Idempotency-Key": "fixture-retry", "X-Forwarded-Host": "wrong.example", "X-Forwarded-For": "192.0.2.2",
      "CF-Connecting-IP": "192.0.2.1", "X-Imladri-Origin": "spoofed", "Content-Type": "application/json"
    };
    const request = new Request(page + "/api/stripe/webhook?source=1", { method: "POST", headers, body: bytes });
    let calls = 0;
    const forward = async (received, options) => {
      calls++;
      assert.equal(received.url, (mode === "render" ? origin : page) + "/api/stripe/webhook?source=1");
      assert.equal(received.redirect, "manual");
      assert.deepEqual(new Uint8Array(await received.arrayBuffer()), bytes);
      for (const name of ["Authorization", "Cookie", "Stripe-Signature", "Idempotency-Key", "Content-Type"]) {
        assert.equal(received.headers.get(name), headers[name]);
      }
      assert.equal(received.headers.get("X-Forwarded-Host"), "imladri.com");
      assert.equal(received.headers.get("X-Imladri-Origin"), null);
      assert.equal(received.headers.get("X-Forwarded-For"), "192.0.2.1");
      if (mode === "render") assert.equal(options.cache, "no-store");
      const response = new Response(null, { status: 302, headers: { Location: (mode === "render" ? origin : "http://container") + "/api/" } });
      response.headers.append("Set-Cookie", "a=1; HttpOnly; Secure; SameSite=Lax");
      response.headers.append("Set-Cookie", "b=2; Expires=Fri, 02 Jan 2037 00:00:00 GMT; Path=/");
      return response;
    };
    const env = mode === "render" ? { API_ORIGIN: origin } : { API: { fetch: forward } };
    const response = await handle(request, env, forward);
    assert.equal(calls, 1);
    assert.equal(response.status, 302);
    assert.equal(response.headers.get("location"), page + "/api/");
    assert.equal(response.headers.getSetCookie().length, 2);
    assert.equal(response.headers.get("Cache-Control"), "private, no-store");
  });
}

test("automatic failover chooses once before passing any member or webhook data", async () => {
  const standby = "https://imladri-personal-live-api.onrender.com";
  for (const primaryHealthy of [true, false]) {
    const events = [];
    const upstream = async (request, name) => {
      events.push([name, new URL(request.url).pathname]);
      if (new URL(request.url).pathname === "/api/ready") {
        assert.equal(request.method, "GET");
        assert.equal([...request.headers].length, 0);
        return Response.json({ ready: primaryHealthy }, { status: primaryHealthy ? 200 : 503 });
      }
      assert.equal(request.headers.get("X-Imladri-Origin"), "standby-test");
      assert.equal(await request.text(), "signed original body");
      return new Response(null, { status: 502 });
    };
    const env = { RENDER_FAILOVER_MODE: "auto", RENDER_FAILOVER_ORIGIN: standby, RENDER_ORIGIN_SECRET: "standby-test",
      API: { fetch: req => upstream(req, "primary") } };
    const req = new Request(page + "/api/stripe/webhook", { method: "POST", body: "signed original body",
      headers: { Cookie: "session=test", Authorization: "Bearer example", "Stripe-Signature": "sig" } });
    const response = await handle(req, env, req => upstream(req, "standby"));
    assert.equal(response.status, 502);
    assert.deepEqual(events, [["primary", "/api/ready"], [primaryHealthy ? "primary" : "standby", "/api/stripe/webhook"]]);
  }
});

test("repair is rate-limited background work and the dedicated egress proof is candidate-only", async () => {
  const site = "https://imladri-personal-candidate.imladri.workers.dev";
  const standby = "https://imladri-personal-live-api.onrender.com";
  const probe = "/api/ops/container-egress";
  const service = { fetch: async req => {
    if (new URL(req.url).pathname === probe) {
      assert.equal(req.headers.get("Authorization"), "Bearer probe");
      assert.equal(req.headers.get("Cookie"), null);
      return Response.json({ egress_ipv4: "192.0.2.1" });
    }
    return new Response(null, { status: 503 });
  } };
  const env = { READINESS_ONLY: "true", API: service, RENDER_FAILOVER_MODE: "auto",
    RENDER_FAILOVER_ORIGIN: standby, OPS_CHECK_SECRET: "ops", RENDER_ORIGIN_SECRET: "standby" };
  const bodyless = base => new Request(base + probe, { method: "POST", headers: { Authorization: "Bearer probe", Cookie: "drop" } });
  assert.equal((await handle(bodyless(site), env)).status, 200);
  assert.equal((await handle(bodyless(page), env)).status, 404);
  assert.equal((await handle(bodyless(site), { ...env, READINESS_ONLY: "" })).status, 404);
  const pending = [], calls = [];
  const ctx = { waitUntil: task => pending.push(task) };
  const fetchOrigin = async req => {
    const path = new URL(req.url).pathname;
    calls.push(path);
    if (path === "/api/ops/repair-ingress") {
      assert.equal(req.method, "POST");
      assert.equal(req.headers.get("Authorization"), "Bearer ops");
      assert.equal(req.headers.get("X-Imladri-Origin"), "standby");
      assert.equal(req.headers.get("Cookie"), null);
    }
    return new Response(null, { status: 204 });
  };
  for (let i = 0; i < 2; i++) await handle(new Request(site + "/api/ready"), env, fetchOrigin, ctx);
  await Promise.all(pending);
  assert.deepEqual(calls, ["/api/ops/repair-ingress", "/api/ready", "/api/ready"]);
  assert.equal((await handle(new Request(site + "/api/ops/repair-ingress"), env)).status, 404);
});

test("signed-in page navigation reaches the SPA while an absent asset or API retains its 404", async () => {
  const env = { API_ORIGIN: origin, ASSETS: { fetch: req => new URL(req.url).pathname === "/"
    ? new Response("SPA") : new Response(null, { status: 404 }) } };
  const req = path => new Request(page + path, { headers: { Accept: "text/html" } });
  assert.equal(await (await handle(req("/app/settings/upgrade"), env)).text(), "SPA");
  assert.equal((await handle(req("/static/js/missing.js"), env)).status, 404);
  assert.equal((await handle(req("/img/missing"), env)).status, 404);
  const missing = await handle(req("/api/missing"), env, () => Response.json({ detail: "Not Found" }, { status: 404 }));
  assert.equal(missing.status, 404);
  assert.deepEqual(await missing.json(), { detail: "Not Found" });
});

test("only the scheduled check sends the operations secret", async () => {
  let calls = 0;
  const fetchOrigin = async req => {
    calls++;
    assert.equal(req.url, origin + "/api/ops/check");
    assert.equal(req.method, "POST");
    assert.equal(req.headers.get("Authorization"), "Bearer local-fixture");
    assert.equal(req.headers.get("Cookie"), null);
    return new Response(null, { status: 204 });
  };
  const env = { API_ORIGIN: origin, OPS_CHECK_SECRET: "local-fixture" };
  await checkOperations(env, fetchOrigin);
  await handle(new Request(page + "/api/ops/check", { method: "POST" }), env, req => {
    assert.equal(req.headers.get("Authorization"), null);
    return new Response(null, { status: 401 });
  });
  assert.equal(calls, 1);
  await assert.rejects(checkOperations(env, () => new Response("sensitive upstream detail", { status: 429 })),
    { message: "Operations check HTTP 429" });
  await assert.rejects(checkOperations({ API_ORIGIN: origin }, fetchOrigin), { message: "Operations check HTTP 503" });
});

const trustedStandby = "https://imladri-personal-live-api.onrender.com";

function automatic(service) {
  return { API: service, RENDER_FAILOVER_MODE: "auto", RENDER_FAILOVER_ORIGIN: trustedStandby, RENDER_ORIGIN_SECRET: "fixture-standby-key" };
}

test("a delayed older readiness result cannot reverse a newer routing decision", async () => {
  for (const healthy of [true, false]) {
    let finishFirst;
    let checks = 0;
    const service = { fetch: async req => {
      if (new URL(req.url).pathname !== "/api/ready") return new Response("primary");
      checks++;
      if (checks === 1) return new Promise(resolve => { finishFirst = resolve; });
      return Response.json({ready: healthy});
    }};
    const env = automatic(service);
    const read = async () => (await handle(new Request(page + "/api/me"), env,
      () => new Response("standby"))).text();
    const older = read();
    assert.equal(typeof finishFirst, "function");
    const expected = healthy ? "primary" : "standby";
    assert.equal(await read(), expected);
    finishFirst(Response.json({ready: !healthy}));
    assert.equal(await older, expected);
    assert.equal(await read(), expected);
    assert.equal(checks, 2);
  }
});

test("an unhealthy readiness response routes immediately even when its body will not cancel", {timeout: 1000}, async () => {
  let abandoned = 0;
  const failure = () => new Response(new ReadableStream({
    cancel() { abandoned++; return new Promise(() => {}); }
  }), { status: 503 });
  const primary = { fetch: async () => failure() };
  const env = { ...automatic(primary), RENDER_COLD_START_GUARD: "true" };
  let readyChecks = 0;
  const answer = await handle(new Request(page + "/api/public/config"), env, async req => {
    assert.equal(new URL(req.url).pathname, "/api/ready");
    readyChecks++;
    return failure();
  });
  assert.equal(readyChecks, 1);
  assert.equal(abandoned, 2);
  assert.equal(answer.status, 503);
  assert.equal(answer.headers.get("X-Imladri-Reconnecting"), "1");
});

test("a stale healthy probe recovers an allowlisted public read on Render and returns to the primary", async t => {
  let clock = 10_000;
  t.mock.method(Date, "now", () => clock);
  const events = [];
  let failure = false, discarded = 0;
  const primary = { fetch: async req => {
    const path = new URL(req.url).pathname;
    events.push("primary " + path);
    assert.equal(req.headers.get("X-Imladri-Origin"), path === "/api/ready" ? null : env.RENDER_ORIGIN_SECRET);
    if (path === "/api/ready") return Response.json({ ready: true });
    if (failure && path === "/api/public/config") {
      return new Response(new ReadableStream({
        cancel() {
          discarded++;
          return new Promise(() => {}); // A slow failed origin can also stall cancellation.
        }
      }), { status: 503 });
    }
    return new Response("primary");
  }};
  const env = automatic(primary);
  const fallback = async (req, options) => {
    events.push("standby " + new URL(req.url).pathname);
    assert.equal(new URL(req.url).origin, trustedStandby);
    assert.equal(req.redirect, "manual");
    assert.equal(options.cache, "no-store");
    assert.equal(req.headers.get("X-Imladri-Origin"), env.RENDER_ORIGIN_SECRET);
    assert.equal(req.headers.get("Authorization"), "Bearer synthetic");
    assert.equal(req.headers.get("Cookie"), "test-session=1");
    assert.equal(req.headers.get("X-Forwarded-Host"), "imladri.com");
    assert.equal(req.headers.get("X-Forwarded-For"), "192.0.2.12");
    assert.equal(req.headers.get("Forwarded"), null);
    const headers = new Headers({ Location: trustedStandby + "/api/public/catalog-paths?from=1", "X-Imladri-Origin": env.RENDER_ORIGIN_SECRET });
    headers.append("Set-Cookie", "example=test; HttpOnly");
    return new Response(null, { status: 307, headers });
  };
  const read = path => handle(new Request(page+path, { headers: { Authorization:"Bearer synthetic", Cookie:"test-session=1",
    "X-Imladri-Origin":"browser-header", Forwarded:"host=untrusted", "CF-Connecting-IP":"192.0.2.12" } }), env, fallback);
  assert.equal(await (await read("/api/public/config")).text(), "primary");
  failure = true;
  const retried = await read("/api/public/config");
  assert.equal(retried.status, 307);
  assert.equal(retried.headers.get("Location"), page+"/api/public/catalog-paths?from=1");
  assert.equal(retried.headers.get("X-Imladri-Origin"), null);
  assert.equal(retried.headers.getSetCookie().length, 1);
  assert.equal(discarded, 1);
  await read("/api/public/figures/example");
  // Reprobe private traffic once. A localized public catalog issue must not replay or divert a member read.
  assert.equal(await (await read("/api/me")).text(), "primary");
  assert.deepEqual(events, ["primary /api/ready","primary /api/public/config","primary /api/public/config",
    "standby /api/public/config","standby /api/public/figures/example","primary /api/ready","primary /api/me"]);
  clock += 5_001; failure = false;
  assert.equal(await (await read("/api/public/config")).text(), "primary");
  assert.deepEqual(events.slice(-2), ["primary /api/ready","primary /api/public/config"]);
});

test("only public config and catalog GET or HEAD requests replay on gateway failures", async () => {
  const examples = [
    ["GET", "/api/public/config", 502, true],
    ["HEAD","/api/public/figures", 504, true],
    ["GET", "/api/public/archetypes/example", "throw", true],
    ["GET", "/api/public/catalog-paths", 503, true],
    ["GET", "/api/public/config", 404, false],
    ["GET", "/api/public/config", 500, false],
    ["GET", "/api/public/config", 429, false],
    ["GET", "/api/public/config", 403, false],
    ["POST","/api/public/config", 502, false],
    ["POST","/api/stripe/webhook", "throw", false],
    ["GET", "/api/me", "throw", false],
    ["GET", "/api/admin/activity", 503, false],
    ["GET", "/api/public/figures/test/nested", 502, false],
    ["GET", "/api/public/research/repeat", 504, false],
  ];
  for (const [method,path,status,retry] of examples) {
    let primary = 0, standby = 0;
    const env = automatic({ fetch: async req => {
      if (new URL(req.url).pathname === "/api/ready") return Response.json({ready:true});
      primary++;
      if (status === "throw") throw Error("test-network-failure");
      return new Response(null, {status});
    }});
    const attempt = handle(new Request(page+path+"?from=fixture", {method}), env, async req => {
      standby++;
      assert.equal(req.method,method); assert.equal(req.url,trustedStandby+path+"?from=fixture");
      return new Response(null,{status:504});
    });
    if (!retry && status === "throw") await assert.rejects(attempt,/test-network-failure/);
    else assert.equal((await attempt).status,retry ? 504 : status);
    assert.equal(primary,1); assert.equal(standby,retry ? 1 : 0);
  }
});

test("standby errors after a public read fail safely, and client cancellations never retry", async () => {
  let standby = 0;
  const broken = async () => { standby++; throw Error("private provider details"); };
  const controller = new AbortController();
  let cancel = false;
  const primary = { fetch: async req => {
    if (new URL(req.url).pathname === "/api/ready") return Response.json({ready:true});
    if (cancel) controller.abort();
    throw Error("test interrupted");
  }};
  const req = () => new Request(page+"/api/public/config",{signal:controller.signal});
  const result = await handle(req(),automatic(primary),broken);
  assert.equal(result.status,503);assert.equal(result.headers.get("Cache-Control"),"private, no-store");
  assert.doesNotMatch(await result.text(),/private provider details|fixture-standby-key/);
  assert.equal(standby,1);
  cancel=true;
  await assert.rejects(handle(req(),automatic({fetch:primary.fetch}),broken));
  assert.equal(standby,1);
});

test("published pricing stays available while the isolated preview and protected standby sleep", async () => {
  const cold = async () => { throw Error("pricing contacted a sleeping service"); };
  for (const env of [{ API_ORIGIN: origin }, automatic({ fetch: cold })]) {
    const req = new Request(page + "/api/public/plans", {headers:{Cookie:"member",Authorization:"Bearer member"}});
    const res = await handle(req, {...env, RENDER_COLD_START_GUARD:"true"}, cold);
    assert.equal(res.status,200);
    const {plans} = await res.json();
    assert.deepEqual(plans.map(p => [p.price_cents, p.messages]), [[1200,100],[2000,250],[4000,500]]);
    assert.ok(plans.every(p => Object.keys(p).every(k => ["id","name","messages","price_cents"].includes(k))));
  }
});

test("a cold Render receives only its readiness probe, reopening later for one original member action", async () => {
  const env = {...automatic({fetch: async () => Response.json({ready:false})}), RENDER_COLD_START_GUARD:"true"};
  let ready = false, reads = 0, actions = 0;
  const up = async req => {
    assert.equal(req.headers.get("X-Imladri-Origin"), env.RENDER_ORIGIN_SECRET);
    if (new URL(req.url).pathname === "/api/ready") {
      reads++;
      assert.equal(req.method,"GET");assert.equal(req.headers.get("Cookie"),null);
      assert.equal(req.headers.get("Authorization"),null);
      return Response.json({ready});
    }
    actions++;
    assert.equal(req.headers.get("Authorization"),"Bearer private");
    assert.equal(await req.text(),"original");
    return new Response("saved", {headers: {"X-Imladri-Reconnecting":"forged"}});
  };
  const action = () => new Request(page + "/api/people", {method:"POST",
    headers:{Authorization:"Bearer private"}, body:"original"});
  const first = await handle(action(),env,up);
  assert.equal(first.status,503);assert.equal(first.headers.get("Retry-After"),"3");
  assert.equal(first.headers.get("X-Imladri-Reconnecting"),"1");
  assert.equal(first.headers.get("Cache-Control"),"private, no-store");assert.equal(actions,0);
  ready=true;
  const saved=await handle(action(),env,up);
  assert.equal(await saved.text(),"saved");assert.equal(saved.headers.get("X-Imladri-Reconnecting"),null);
  assert.equal(actions,1);assert.equal(reads,2);
});

test("an unresponsive preview probe is interrupted and cannot receive a signed webhook payload", async () => {
  let interrupted = false, calls = 0;
  const req = new Request(page + "/api/stripe/webhook", {method:"POST",headers:{"Stripe-Signature":"private"},body:"signed"});
  const env = {API_ORIGIN:"https://imladri-personal-pilot.onrender.com",RENDER_COLD_START_GUARD:"true"};
  const started = Date.now();
  const res = await handle(req,env,probe => {
    calls++;
    assert.equal(new URL(probe.url).pathname,"/api/ready");assert.equal(probe.headers.get("Stripe-Signature"),null);
    return new Promise((_,reject) => probe.signal.addEventListener("abort",()=>{interrupted=true;reject(Error("cancelled"));}));
  });
  assert.equal(res.status,503);assert.equal(interrupted,true);assert.equal(calls,1);
  assert.ok(Date.now()-started<7_000);
});

test("retired study links, submissions and invitation issuers cannot load an app or reach an origin", async () => {
  const unexpected = () => assert.fail("Closed study traffic reached a service");
  const env = { API_ORIGIN: origin, ASSETS: { fetch: unexpected } };
  for (const [method, path] of [
    ["GET", "/study/volunteer#private"], ["HEAD", "/study/volunteer/"],
    ["GET", "/api/studies/reader/A"], ["POST", "/api/studies/reader/B/open"],
    ["POST", "/api/studies/reader/V6/visit/submit"], ["GET", "/api/studies/first-visit/tasks"],
    ["POST", "/api/studies/volunteer/basics"], ["POST", "/api/studies/volunteer/wording"],
    ["POST", "/api/admin/research/field/links/A"], ["POST", "/api/admin/research/volunteer-links/basics"],
    ["GET", "/api/admin/research/volunteers"]
  ]) {
    const result = await handle(new Request(page + path, { method }), env, unexpected);
    assert.equal(result.status, 410);
    assert.equal(await result.text(), method === "HEAD" ? "" : "This study is closed.");
    assert.equal(result.headers.get("Referrer-Policy"), "no-referrer");
    assert.match(result.headers.get("Cache-Control"), /no-store/);
  }
});

test("a public config request that stalls after a healthy probe moves to standby once", async () => {
  let attempted = 0, fallback = 0;
  const env = automatic({fetch: req => {
    if (new URL(req.url).pathname === "/api/ready") return Response.json({ready: true});
    attempted++;
    return new Promise((_, reject) => req.signal.addEventListener("abort", () => reject(new Error("deadline"))));
  }});
  const result = await handle(new Request(page + "/api/public/config"), env, req => {
    fallback++;
    assert.equal(req.method, "GET");
    return Response.json({source: "standby"});
  });
  assert.deepEqual(await result.json(), {source: "standby"});
  assert.equal(attempted, 1);
  assert.equal(fallback, 1);
});

test("a client leaving during a public read cancels the request without starting a fallback", async () => {
  let started;
  const inFlight = new Promise(resolve => { started = resolve; });
  const abort = new AbortController();
  const env = automatic({fetch: req => {
    if (new URL(req.url).pathname === "/api/ready") return Response.json({ready: true});
    return new Promise((_, reject) => {
      req.signal.addEventListener("abort", () => reject(new Error("client left")));
      started();
    });
  }});
  const result = handle(new Request(page + "/api/public/config", {signal: abort.signal}), env, () => assert.fail("unexpected retry"));
  await inFlight;
  abort.abort();
  await assert.rejects(result, /client left/);
});
