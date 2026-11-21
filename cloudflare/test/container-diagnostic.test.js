import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { webcrypto, timingSafeEqual } from "node:crypto";
import test from "node:test";

const source = await readFile(new URL("../src/container.js", import.meta.url), "utf8");
const fixtureSource = source.replace('import { DurableObject } from "cloudflare:workers";',
  "class DurableObject { constructor(ctx,env) {this.ctx=ctx;this.env=env;} }");
const { PersonalApi, parseDiagnosticOutput, parseReadinessDiagnostic } = await import("data:text/javascript;base64," + Buffer.from(fixtureSource).toString("base64"));
const hash = "a".repeat(64);
const manifest = { schema_version: 2, python: "3.12.16", openssl: "OpenSSL 4.0.3 29 Sep 2026",
  expat: "expat_2.10.0", sqlite: "3.54.1", os: {ID: "wolfi", VERSION_ID: "20230201"},
  os_inventory_supported: true, apk_database: {database_sha256: hash, count: 1, sha256: hash,
    packages: [{name: "python-3.12-base", version: "3.12.16_git20261030-r0", origin: "python-3.12"}]},
  uid: 65532, gid: 65532, effective_capabilities: "0000000000000000",
  python_distributions: {count: 70, sha256: hash},
  native_patches: {schema_version: 1, manifest_sha256: hash, file_count: 9, resolved_files: 9,
    files_sha256: hash, bytes_hashed: 4096, loaded_native_files: {mapped_files: 7, sha256: hash},
    components: ["glibc", "openssl", "cryptography", "sqlite", "ncurses"].map(name => ({name,
      mode: ["openssl", "cryptography"].includes(name) ? "custom_backport" : "signed_vendor",
      source_sha256: hash, artifact_sha256: hash, recipe_sha256: hash,
      patches: ["openssl", "cryptography"].includes(name) ? [hash] : [], cases: []}))},
  application_files: {count: 518, sha256: hash},
  shells: [], package_managers: [], privileged_helpers: [], pip_module: false, node: false };
const processContext = {uid: [65532,65532,65532,65532], gid: [65532,65532,65532,65532],
  effective_capabilities: "0000000000000000", permitted_capabilities: "0000000000000000",
  inheritable_capabilities: "0000000000000000", no_new_privileges: 1};
const context = {main_process: processContext, diagnostic_process: processContext, ready: true};
const output = (m=manifest,c=context,ip="203.0.113.4") => "IMLADRI_RUNTIME_FINGERPRINT_v2 " + JSON.stringify(m)
  + "\nIMLADRI_RUNTIME_CONTEXT_v2 " + JSON.stringify(c) + "\n" + ip + "\n";

test("gateway containers receive the scoped connection without retired Mongo or owner credentials", async () => {
  let started;
  const container = {running: false, images: {base: "immutable-fixture"},
    start: value => {started = value;}, setInactivityTimeout: async () => {}};
  const env = {IMLADRI_DATABASE_BACKEND: "render_gateway", DB_NAME: "imladri_personal_live",
    MONGO_URL: "mongodb+srv://retired-fixture.invalid", IMLADRI_POSTGRES_DSN: "owner-fixture",
    IMLADRI_POSTGRES_SHADOW_DSN: "owner-fixture", IMLADRI_MIGRATION_SOURCES_JSON: "private-fixture",
    IMLADRI_POSTGRES_GATEWAY_URL: "https://standby.example/api/internal/database",
    IMLADRI_POSTGRES_GATEWAY_SECRET: "gateway-fixture", IMLADRI_POSTGRES_NAMESPACE: "imladri_personal_live",
    RENDER_ORIGIN_SECRET: "origin-fixture",
    INTRO_CARD_FIRST_VERIFIED_STRATEGY: "managed-classic-discount-no-date-v1",
    INTRO_CARD_FIRST_PROVIDER_PROOF_SHA256: "a".repeat(64),
    INTRO_CARD_FIRST_COUPON_ID: "fixture-private-coupon",
    INTRO_CARD_FIRST_UNREVIEWED: "must-not-enter-container"};
  await new PersonalApi({container}, env).applyRelease();
  assert.equal(started.env.IMLADRI_DATABASE_BACKEND, "render_gateway");
  assert.equal(started.env.IMLADRI_POSTGRES_GATEWAY_URL, env.IMLADRI_POSTGRES_GATEWAY_URL);
  assert.equal(started.env.IMLADRI_POSTGRES_GATEWAY_SECRET, "gateway-fixture");
  for (const key of ["INTRO_CARD_FIRST_VERIFIED_STRATEGY", "INTRO_CARD_FIRST_PROVIDER_PROOF_SHA256", "INTRO_CARD_FIRST_COUPON_ID"]) {
    assert.equal(started.env[key], env[key]);
  }
  for (const key of ["MONGO_URL", "IMLADRI_POSTGRES_DSN", "IMLADRI_POSTGRES_SHADOW_DSN", "IMLADRI_MIGRATION_SOURCES_JSON", "INTRO_CARD_FIRST_UNREVIEWED"]) {
    assert.equal(Object.hasOwn(started.env, key), false);
  }
});

test("a pre-cutover Mongo container still receives its required source connection", async () => {
  let started;
  const container = {running: false, images: {base: "immutable-fixture"},
    start: value => {started = value;}, setInactivityTimeout: async () => {}};
  const env = {MONGO_URL: "mongodb+srv://source-fixture.invalid", DB_NAME: "imladri_personal_live"};
  await new PersonalApi({container}, env).applyRelease();
  assert.equal(started.env.MONGO_URL, env.MONGO_URL);
});

test("the diagnostic accepts only fixed public metadata and an IPv4 result", () => {
  assert.deepEqual(parseDiagnosticOutput(output()), {ip: "203.0.113.4", manifest, context});
  for (const text of ["", "203.0.113.4", output()+"unexpected secret\n", output({...manifest, secrets: "private"}),
    output({...manifest, application_files: {count: 518, sha256: "bad"}}), output({...manifest, apk_database: {...manifest.apk_database, count: 2}}),
    output(manifest,{...context,env: "private"}), output(manifest,context,"999.0.0.1"), "x".repeat(64_001)]) {
    assert.equal(parseDiagnosticOutput(text), null);
  }
});

test("the diagnostic requires the exact bounded native inventory without private fields or held glibc claims", () => {
  assert.deepEqual(parseDiagnosticOutput(output()).manifest.native_patches, manifest.native_patches);
  const invalid = change => {
    const candidate = structuredClone(manifest);
    change(candidate);
    assert.equal(parseDiagnosticOutput(output(candidate)), null);
  };
  invalid(value => { delete value.native_patches; });
  invalid(value => { value.native_patches = null; });
  invalid(value => { delete value.native_patches.files_sha256; });
  invalid(value => { value.native_patches.secret = "private"; });
  invalid(value => { value.native_patches.schema_version = 2; });
  invalid(value => { value.native_patches.manifest_sha256 = "bad"; });
  invalid(value => { value.native_patches.files_sha256 = "bad"; });
  invalid(value => { value.native_patches.bytes_hashed = 0; });
  invalid(value => { value.native_patches.bytes_hashed = 256 * 1024 * 1024 + 1; });
  invalid(value => { value.native_patches.file_count = 1025; });
  invalid(value => { value.native_patches.resolved_files = 10; });
  invalid(value => { value.native_patches.loaded_native_files.mapped_files = 6; });
  invalid(value => { value.native_patches.loaded_native_files.mapped_files = 10; });
  invalid(value => { value.native_patches.loaded_native_files.sha256 = "bad"; });
  invalid(value => { value.native_patches.loaded_native_files.path = "/private"; });
  invalid(value => { value.native_patches.components.pop(); });
  invalid(value => { value.native_patches.components[4].name = "sqlite"; });
  invalid(value => { delete value.native_patches.components[1].recipe_sha256; });
  invalid(value => { value.native_patches.components[1].private_path = "/private"; });
  for (const key of ["source_sha256", "artifact_sha256", "recipe_sha256"]) {
    invalid(value => { value.native_patches.components[1][key] = "bad"; });
  }
  invalid(value => { value.native_patches.components[1].mode = "unreviewed"; });
  invalid(value => { value.native_patches.components[1].patches = []; });
  invalid(value => { value.native_patches.components[1].patches = ["bad"]; });
  invalid(value => { value.native_patches.components[1].patches = [hash, hash]; });
  invalid(value => { value.native_patches.components[1].cases = ["private"]; });
  invalid(value => { value.native_patches.components[1].cases = ["CVE-2026-88822", "CVE-2026-88822"]; });
  invalid(value => { value.native_patches.components[0].mode = "custom_backport"; });
  invalid(value => { value.native_patches.components[0].patches = [hash]; });
  invalid(value => { value.native_patches.components[0].cases = ["CVE-2026-100290"]; });
});

test("metadata parsing preserves observed privileges for explicit verification", () => {
  const native = {...manifest, effective_capabilities: "000001ffffffffff"};
  const primary = {...context, main_process: {...context.main_process, effective_capabilities: "000001ffffffffff"}, ready: false};
  assert.deepEqual(parseDiagnosticOutput(output(native,primary)).manifest, native);
  assert.deepEqual(parseDiagnosticOutput(output(native,primary)).context, primary);
});

test("one fixed direct Python process logs validated metadata while returning the original IP-only contract", async () => {
  const calls = [], logs = [];
  const previous = console.log;
  console.log = value => logs.push(value);
  try {
    const container = {running: true, exec: async (command, options) => {
      calls.push({command, options});
      return {output: async () => ({exitCode: 0, stdout: new TextEncoder().encode(output()), stderr: new ArrayBuffer(0)})};
    }};
    const api = new PersonalApi({container}, {});
    let releases = 0;
    api.startContainer = async () => { releases++; return container; };
    const response = await api.diagnoseEgress();
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.deepEqual(await response.json(), {egress_ipv4: "203.0.113.4"});
    assert.equal(calls.length, 1);
    assert.equal(releases, 1);
    assert.deepEqual(calls[0].command.slice(0,2), ["python","-c"]);
    assert.deepEqual(calls[0].options, {user: "65532:65532", cwd: "/app"});
    const program = calls[0].command[2];
    assert.ok(program.includes("emit_runtime_manifest()"));
    assert.ok(program.includes("harden_process()"));
    assert.ok(program.indexOf("harden_process()") < program.indexOf("from backend.runtime_manifest"));
    assert.ok(program.indexOf("emit_runtime_manifest()") < program.indexOf("print(observe_egress()"));
    assert.ok(program.includes("/proc/1/status"));
    assert.ok(program.includes("/proc/self/status"));
    assert.ok(program.includes("NoNewPrivs"));
    assert.ok(program.includes("CapPrm") && program.includes("CapInh"));
    assert.ok(program.includes("http://127.0.0.1:8080/api/ready"));
    assert.ok(program.includes("timeout=5"));
    assert.ok(program.includes("urllib.request.ProxyHandler({})"));
    assert.ok(program.includes("class NoRedirect(urllib.request.HTTPRedirectHandler)"));
    assert.ok(program.includes("def redirect_request(self,req,fp,code,msg,headers,newurl): return None"));
    assert.ok(program.includes("urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())"));
    assert.ok(program.includes("opener.open('http://127.0.0.1:8080/api/ready',timeout=5)"));
    assert.ok(program.includes("file=sys.stderr"));
    assert.ok(!program.includes("environ") && !program.includes("ops_check") && !program.includes("subprocess"));
    assert.equal(logs.length, 2);
    assert.ok(logs[0].startsWith("IMLADRI_RUNTIME_FINGERPRINT_v2 "));
    assert.ok(logs[1].startsWith("IMLADRI_RUNTIME_CONTEXT_v2 "));
  } finally { console.log = previous; }
});

const readinessPrefix = "IMLADRI_RUNTIME_READINESS_v1 ";
const readiness = {status: 200, elapsed_ms: 34, error: "none", proxy_configured: true};
test("readiness operational stderr accepts only the bounded fixed public schema", () => {
  assert.deepEqual(parseReadinessDiagnostic(readinessPrefix + JSON.stringify(readiness) + "\n"), readiness);
  assert.deepEqual(parseReadinessDiagnostic(readinessPrefix + JSON.stringify({status: null, elapsed_ms: 5001,
    error: "timeout", proxy_configured: false})), {status: null, elapsed_ms: 5001, error: "timeout", proxy_configured: false});
  for (const value of [
    {...readiness, status: true}, {...readiness, status: 600}, {...readiness, status: 200.1},
    {...readiness, elapsed_ms: -1}, {...readiness, elapsed_ms: 120001}, {...readiness, elapsed_ms: "34"},
    {...readiness, error: "private error with URL https://secret.invalid"}, {...readiness, proxy_configured: "true"},
    {...readiness, response_body: "private"}, {...readiness, proxy_url: "http://private.invalid"},
  ]) assert.equal(parseReadinessDiagnostic(readinessPrefix + JSON.stringify(value)), null);
  for (const raw of ["", "private raw stderr", readinessPrefix + "{}", "x".repeat(1025),
    readinessPrefix + JSON.stringify(readiness) + "\nprivate stderr", readinessPrefix + "{bad JSON}"])
    assert.equal(parseReadinessDiagnostic(raw), null);
});

for (const valid of [true, false]) {
  test(`only validated readiness stderr reaches Worker logs (${valid ? "valid" : "private rejected"})`, async () => {
    const logs = [], previous = console.log;
    const stderr = valid ? readinessPrefix + JSON.stringify(readiness) + "\n" : "private raw error https://secret.invalid";
    console.log = value => logs.push(value);
    try {
      const container = {exec: async () => ({output: async () => ({exitCode: 0,
        stdout: new TextEncoder().encode(output(manifest, {...context, ready: false})),
        stderr: new TextEncoder().encode(stderr)})})};
      const api = new PersonalApi({container}, {});api.startContainer = async () => container;
      assert.equal((await api.diagnoseEgress()).status, 200);
      assert.equal(logs.length, valid ? 3 : 2);
      assert.deepEqual(JSON.parse(logs[1].slice("IMLADRI_RUNTIME_CONTEXT_v2 ".length)), {...context, ready: false});
      assert.ok(logs.every(line => !line.includes("secret.invalid")));
      if (valid) assert.deepEqual(JSON.parse(logs[2].slice(readinessPrefix.length)), readiness);
    } finally { console.log = previous; }
  });
}

test("the dedicated diagnostic still rejects absent or incorrect authorization before execution", async () => {
  const previous = globalThis.crypto;
  Object.defineProperty(globalThis, "crypto", {configurable: true, value: {subtle: {
    digest: webcrypto.subtle.digest.bind(webcrypto.subtle),
    timingSafeEqual: (a,b) => timingSafeEqual(Buffer.from(a),Buffer.from(b))}}});
  try {
    let called = 0;
    const api = new PersonalApi({container: {running: true}}, {CF_EGRESS_PROBE_SECRET: "fixture-secret"});
    api.diagnoseEgress = async () => {called++;return Response.json({egress_ipv4: "203.0.113.4"});};
    for (const headers of [{}, {Authorization: "Bearer wrong"}]) {
      const result = await api.fetch(new Request("https://container.internal/api/ops/container-egress", {method:"POST",headers}));
      assert.equal(result.status, 401);
    }
    assert.equal(called,0);
    const valid = await api.fetch(new Request("https://container.internal/api/ops/container-egress", {method:"POST",headers:{Authorization:"Bearer fixture-secret"}}));
    assert.equal(valid.status,200); assert.equal(called,1);
    const wrongMethod = await api.fetch(new Request("https://container.internal/api/ops/container-egress", {headers:{Authorization:"Bearer fixture-secret"}}));
    assert.equal(wrongMethod.status,401); assert.equal(called,1);
  } finally { Object.defineProperty(globalThis, "crypto", {configurable: true, value: previous}); }
});

test("a cold paused Mongo container becomes ready while ordinary API calls stay fenced", async () => {
  const paths = [];
  const container = {running: false, images: {base: "immutable-fixture"},
    start: () => {container.running = true;}, setInactivityTimeout: async () => {},
    getTcpPort: port => {
      assert.equal(port, 8080);
      return {fetch: async (request, options) => {
        const url = new URL(typeof request === "string" ? request : request.url);
        paths.push(url.pathname);
        if (typeof request === "string") assert.ok(options.signal instanceof AbortSignal);
        return url.pathname === "/api/ready" ? Response.json({ready: true, maintenance: true})
          : Response.json({detail: "The database is temporarily paused for maintenance."}, {status: 503});
      }};
    }};
  const api = new PersonalApi({container}, {MONGO_URL: "mongodb+srv://fixture.invalid", DB_NAME: "fixture",
    IMLADRI_DATABASE_MAINTENANCE: "paused"});
  const ready = await api.fetch(new Request("https://fixture.invalid/api/ready"));
  assert.equal(ready.status, 200);
  assert.deepEqual(await ready.json(), {ready: true, maintenance: true});
  const ordinary = await api.fetch(new Request("https://fixture.invalid/api/people"));
  assert.equal(ordinary.status, 503);
  assert.deepEqual(paths, ["/api/ready", "/api/ready", "/api/people"]);
});

test("a cold gateway container uses readiness before forwarding its first request", async () => {
  let started;
  const paths = [];
  const container = {running: false, images: {base: "immutable-fixture"}, start: value => {started = value;container.running = true;},
    setInactivityTimeout: async () => {}, getTcpPort: () => ({fetch: async request => {
      const path = new URL(typeof request === "string" ? request : request.url).pathname;
      paths.push(path);
      return Response.json(path === "/api/ready" ? {ready: true} : {ok: true});
    }})};
  const api = new PersonalApi({container}, {IMLADRI_DATABASE_BACKEND: "render_gateway",
    IMLADRI_POSTGRES_GATEWAY_URL: "https://fixture.onrender.com", IMLADRI_POSTGRES_GATEWAY_SECRET: "fixture-secret",
    IMLADRI_POSTGRES_NAMESPACE: "imladri_personal_live", RENDER_ORIGIN_SECRET: "fixture-origin", MONGO_URL: "retired-fixture"});
  assert.equal((await api.fetch(new Request("https://fixture.invalid/api/people"))).status, 200);
  assert.deepEqual(paths, ["/api/ready", "/api/people"]);
  assert.equal(Object.hasOwn(started.env, "MONGO_URL"), false);
});

test("cold gateway readiness tolerates a healthy slow PG response within the fixed five-second probe", async () => {
  const originalTimeout = AbortSignal.timeout;
  const observed = [];
  // Scale only test time. A former 1.5-second allowance aborts at15ms; the
  // modeled healthy PG request takes25ms and fits the five-second allowance.
  AbortSignal.timeout = milliseconds => {observed.push(milliseconds);return originalTimeout(milliseconds / 100);};
  const container = {running: true, getTcpPort: () => ({fetch: async (_url, {signal}) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => resolve(Response.json({ready: true})), 25);
    signal.addEventListener("abort", () => {clearTimeout(timer);container.running = false;reject(signal.reason);}, {once: true});
  })})};
  try {
    const api = new PersonalApi({container}, {});
    api.startContainer = async () => container;
    await api.ensureReady();
    assert.deepEqual(observed, [5000]);
  } finally {AbortSignal.timeout = originalTimeout;}
});

test("a PG readiness request that never answers still aborts and fails closed", async () => {
  const originalTimeout = AbortSignal.timeout, previousScheduler = globalThis.scheduler;
  const observed = [];
  let aborted = false;
  AbortSignal.timeout = milliseconds => {observed.push(milliseconds);return originalTimeout(milliseconds / 100);};
  globalThis.scheduler = {wait: async milliseconds => {assert.equal(milliseconds, 250);}};
  const container = {running: true, getTcpPort: () => ({fetch: async (_url, {signal}) => new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => {aborted = true;container.running = false;reject(signal.reason);}, {once: true});
  })})};
  const keepAlive = setTimeout(() => {}, 500);
  try {
    const api = new PersonalApi({container}, {});
    api.startContainer = async () => container;
    await assert.rejects(api.ensureReady(), /API readiness timed out/);
    assert.equal(aborted, true);
    assert.deepEqual(observed, [5000]);
  } finally {clearTimeout(keepAlive);AbortSignal.timeout = originalTimeout;globalThis.scheduler = previousScheduler;}
});

for (const [label, body, status] of [
  ["false", {ready: false}, 200], ["string", {ready: "true"}, 200], ["missing", {}, 200],
  ["non200", {ready: true}, 503], ["invalidJSON", "not JSON", 200],
  ["oversized", " ".repeat(1025), 200],
]) {
  test(`cold readiness rejects ${label} and closes the probe body`, async () => {
    let cancelled = 0;
    const previous = globalThis.scheduler;
    globalThis.scheduler = {wait: async milliseconds => {assert.equal(milliseconds, 250);}};
    try {
      const container = {running: true, getTcpPort: () => ({fetch: async url => {
        assert.equal(url, "http://container/api/ready");
        container.running = false;
        const payload = typeof body === "string" ? body : JSON.stringify(body);
        return {status, body: {getReader: () => {
          let read = false;
          return {read: async () => read ? {done: true} : (read = true, {done: false, value: new TextEncoder().encode(payload)}),
            cancel: async () => {cancelled++;}};
        }}};
      }})};
      const api = new PersonalApi({container}, {});
      api.startContainer = async () => container;
      await assert.rejects(api.ensureReady(), /API readiness timed out/);
      assert.equal(cancelled, 1);
    } finally {globalThis.scheduler = previous;}
  });
}
