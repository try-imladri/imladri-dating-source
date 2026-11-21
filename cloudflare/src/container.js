import { DurableObject } from "cloudflare:workers";

const portNumber = 8080;
const idleMilliseconds = 15 * 60_000;
const startupMilliseconds = 120_000;
const fingerprintPrefix = "IMLADRI_RUNTIME_FINGERPRINT_v2 ";
const runtimeContextPrefix = "IMLADRI_RUNTIME_CONTEXT_v2 ";
const readinessDiagnosticPrefix = "IMLADRI_RUNTIME_READINESS_v1 ";
const fingerprintKeys = ["schema_version", "python", "openssl", "expat", "sqlite", "os", "os_inventory_supported",
  "apk_database", "uid", "gid", "effective_capabilities", "python_distributions", "native_patches", "application_files",
  "shells", "package_managers", "privileged_helpers", "pip_module", "node"];
const exactKeys = (value, keys) => value && typeof value === "object" && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const integer = value => Number.isSafeInteger(value) && value >= 0;
const sha256 = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const countHash = value => exactKeys(value, ["count", "sha256"]) && integer(value.count) && sha256(value.sha256);
const publicToken = value => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._+~:-]{0,159}$/.test(value);
const safeList = value => Array.isArray(value) && value.length <= 1000
  && value.every(item => typeof item === "string" && /^[A-Za-z0-9/_.+-]{1,200}$/.test(item));
const processContext = value => exactKeys(value, ["uid", "gid", "effective_capabilities", "permitted_capabilities",
  "inheritable_capabilities", "no_new_privileges"])
  && ["uid", "gid"].every(key => Array.isArray(value[key]) && value[key].length === 4 && value[key].every(integer))
  && ["effective_capabilities", "permitted_capabilities", "inheritable_capabilities"].every(key =>
    typeof value[key] === "string" && /^[a-f0-9]{16}$/.test(value[key]))
  && [0, 1].includes(value.no_new_privileges);

function nativePatchManifest(value) {
  if (!exactKeys(value, ["schema_version", "manifest_sha256", "components", "file_count", "resolved_files",
      "files_sha256", "bytes_hashed", "loaded_native_files"])
      || value.schema_version !== 1 || !sha256(value.manifest_sha256) || !sha256(value.files_sha256)
      || !integer(value.file_count) || value.file_count < 7 || value.file_count > 1024
      || !integer(value.resolved_files) || value.resolved_files < 7 || value.resolved_files > value.file_count
      || !integer(value.bytes_hashed) || value.bytes_hashed < 1 || value.bytes_hashed > 256 * 1024 * 1024
      || !exactKeys(value.loaded_native_files, ["mapped_files", "sha256"])
      || !integer(value.loaded_native_files.mapped_files) || value.loaded_native_files.mapped_files < 7
      || value.loaded_native_files.mapped_files > value.resolved_files
      || !sha256(value.loaded_native_files.sha256)
      || !Array.isArray(value.components) || value.components.length !== 5) return false;
  const names = new Set(["glibc", "openssl", "cryptography", "sqlite", "ncurses"]);
  for (const component of value.components) {
    if (!exactKeys(component, ["name", "mode", "source_sha256", "artifact_sha256", "recipe_sha256", "patches", "cases"])
        || !names.delete(component.name)
        || !["signed_vendor", "custom_backport", "custom_rebuild"].includes(component.mode)
        || !["source_sha256", "artifact_sha256", "recipe_sha256"].every(key => sha256(component[key]))
        || !Array.isArray(component.patches) || component.patches.length > 64
        || !component.patches.every(sha256) || new Set(component.patches).size !== component.patches.length
        || component.mode === "custom_backport" && component.patches.length === 0
        || !Array.isArray(component.cases) || component.cases.length > 64
        || !component.cases.every(item => typeof item === "string" && /^CVE-\d{4}-\d{4,12}$/.test(item))
        || new Set(component.cases).size !== component.cases.length
        || component.name === "glibc" && (component.mode !== "signed_vendor"
          || component.patches.length !== 0 || component.cases.length !== 0)) return false;
  }
  return names.size === 0;
}

export function parseReadinessDiagnostic(stderr) {
  if (typeof stderr !== "string" || stderr.length > 1024) return null;
  const line = stderr.trim();
  if (!line.startsWith(readinessDiagnosticPrefix) || line.includes("\n") || line.includes("\r")) return null;
  let value;
  try { value = JSON.parse(line.slice(readinessDiagnosticPrefix.length)); } catch { return null; }
  return exactKeys(value, ["status", "elapsed_ms", "error", "proxy_configured"])
    && (value.status === null || Number.isSafeInteger(value.status) && value.status >= 100 && value.status <= 599)
    && Number.isSafeInteger(value.elapsed_ms) && value.elapsed_ms >= 0 && value.elapsed_ms <= 120_000
    && ["none", "not_ready", "http_status", "timeout", "url_error", "network_error", "invalid_response", "body_too_large"].includes(value.error)
    && typeof value.proxy_configured === "boolean" ? value : null;
}

// Only this fixed public schema may reach Worker logs. Ignore all other process output.
export function parseDiagnosticOutput(stdout) {
  if (typeof stdout !== "string" || stdout.length > 64_000) return null;
  const lines = stdout.trim().split(/\r?\n/);
  if (lines.length !== 3 || !lines[0].startsWith(fingerprintPrefix)
      || !lines[1].startsWith(runtimeContextPrefix)) return null;
  let manifest, context;
  try {
    manifest = JSON.parse(lines[0].slice(fingerprintPrefix.length));
    context = JSON.parse(lines[1].slice(runtimeContextPrefix.length));
  } catch { return null; }
  if (!exactKeys(manifest, fingerprintKeys) || manifest.schema_version !== 2
      || !["python", "expat", "sqlite"].every(key => publicToken(manifest[key]))
      || typeof manifest.openssl !== "string" || !/^[A-Za-z0-9.+ ()_-]{1,160}$/.test(manifest.openssl)
      || !manifest.os || Object.keys(manifest.os).some(key => !["ID", "VERSION_ID", "VERSION_CODENAME"].includes(key))
      || !publicToken(manifest.os.ID) || !Object.values(manifest.os).every(publicToken)
      || manifest.os_inventory_supported !== true || !integer(manifest.uid) || !integer(manifest.gid)
      || typeof manifest.effective_capabilities !== "string" || !/^[a-f0-9]{16}$/.test(manifest.effective_capabilities)
      || !countHash(manifest.python_distributions) || !countHash(manifest.application_files)
      || !nativePatchManifest(manifest.native_patches)
      || !["shells", "package_managers", "privileged_helpers"].every(key => safeList(manifest[key]))
      || typeof manifest.pip_module !== "boolean" || typeof manifest.node !== "boolean") return null;
  const apk = manifest.apk_database;
  if (!exactKeys(apk, ["database_sha256", "count", "sha256", "packages"])
      || !sha256(apk.database_sha256) || !sha256(apk.sha256) || !integer(apk.count)
      || !Array.isArray(apk.packages) || apk.packages.length !== apk.count || apk.count > 1000
      || !apk.packages.every(row => exactKeys(row, ["name", "version", "origin"])
        && Object.values(row).every(publicToken))) return null;
  if (!exactKeys(context, ["main_process", "diagnostic_process", "ready"]) || typeof context.ready !== "boolean"
      || !processContext(context.main_process) || !processContext(context.diagnostic_process)) return null;
  const ip = lines[2];
  if (!/^(?:\d{1,3}\.){3}\d{1,3}$/.test(ip) || ip.split(".").some(part => Number(part) > 255)) return null;
  return { ip, manifest, context };
}
const appSettings = [
  "MONGO_URL", "DB_NAME", "PUBLIC_APP_URL", "ADMIN_EMAIL", "CLERK_PUBLISHABLE_KEY",
  "CLERK_SECRET_KEY", "CLERK_WEBHOOK_SIGNING_SECRET", "CLERK_AUTHORIZED_PARTIES", "CLERK_PROXY_URL",
  "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PORTAL_CONFIGURATION", "STRIPE_CHECKOUT_MODE",
  "DEEPSEEK_API_KEY", "DEEPSEEK_PRODUCTION_KEY_NAME", "DEEPSEEK_PRODUCTION_KEY_ID",
  "OPS_CHECK_SECRET", "CF_DIAG_EGRESS_ON_START", "PERSONAL_BILLING_MODE",
  "AI_MEMBER_HOURLY_CALL_LIMIT", "AI_DAILY_TOKEN_LIMIT",
  "INTRO_ACCESS_ENABLED", "INTRO_ABUSE_HMAC_SECRET", "INTRO_ELIGIBLE_SINCE",
  "INTRO_TURNSTILE_SITE_KEY", "INTRO_TURNSTILE_SECRET_KEY", "INTRO_ALLOWED_HOSTNAMES",
  "INTRO_DAILY_TOKEN_LIMIT", "INTRO_DAILY_BUDGET_CENTS", "INTRO_BLOCKED_EMAIL_DOMAINS",
  "INTRO_CARD_FIRST_VERIFIED_STRATEGY", "INTRO_CARD_FIRST_PROVIDER_PROOF_SHA256", "INTRO_CARD_FIRST_COUPON_ID",
  "P1_BILLING_ACCEPTANCE_MODE", "P1_BILLING_ACCEPTANCE_MEMBER_ID",
  "IMLADRI_DATABASE_BACKEND", "IMLADRI_POSTGRES_NAMESPACE", "IMLADRI_POSTGRES_GATEWAY_URL",
  "IMLADRI_POSTGRES_GATEWAY_SECRET", "RENDER_ORIGIN_SECRET", "IMLADRI_DATABASE_MAINTENANCE"
];

async function validBearer(value, secret) {
  if (!value || !secret) return false;
  const digest = text => crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return crypto.subtle.timingSafeEqual(await digest(value), await digest(`Bearer ${secret}`));
}

export class PersonalApi extends DurableObject {
  async startContainer() {
    this.booting ??= this.applyRelease().finally(() => { this.booting = undefined; });
    return this.booting;
  }

  async applyRelease() {
    const container = this.ctx.container;
    const release = this.env.API_RELEASE?.id;
    // DO-managed instances keep their old image and environment until restarted.
    // Drain the previous release before sending a request to a changed backend.
    if (container.running && release && await this.ctx.storage.get("api-release") !== release) {
      const stopped = container.monitor().catch(() => {});
      container.signal(15);
      await stopped;
    }
    if (!container.running) {
      const databaseMode = this.env.IMLADRI_DATABASE_BACKEND || "mongodb";
      if (databaseMode === "mongodb") {
        if (!this.env.MONGO_URL || !this.env.DB_NAME) throw new Error("Missing source database configuration");
      } else if (databaseMode === "render_gateway") {
        if (!this.env.IMLADRI_POSTGRES_GATEWAY_URL || !this.env.IMLADRI_POSTGRES_GATEWAY_SECRET
            || !this.env.IMLADRI_POSTGRES_NAMESPACE || !this.env.RENDER_ORIGIN_SECRET) {
          throw new Error("Missing Render database connection configuration");
        }
      } else {
        throw new Error("Unsupported Cloudflare application database configuration");
      }
      const env = { CORS_ORIGINS: "" };
      for (const name of appSettings) {
        // A PostgreSQL release must never inherit the retired source credential,
        // even while its old Worker binding awaits post-cutover deletion.
        if (name === "MONGO_URL" && databaseMode !== "mongodb") continue;
        if (this.env[name]) env[name] = this.env[name];
      }
      container.start({ image: container.images.base, instance: "basic", enableInternet: true, env });
      if (release) await this.ctx.storage.put("api-release", release);
    }
    await container.setInactivityTimeout(idleMilliseconds);
    return container;
  }

  async diagnoseEgress() {
    // Fixed command on the running Linux image. Only the authenticated operator
    // may call this while provisioning. It runs independently of Mongo startup.
    // Apply the current immutable image even if the previous release is warm.
    const container = await this.startContainer();
    const program = [
      "import json,sys,time,urllib.request,urllib.error",
      "from pathlib import Path",
      "sys.path.insert(0, '/app')",
      "from entrypoint import harden_process,observe_egress",
      "harden_process()",
      "from backend.runtime_manifest import emit_runtime_manifest",
      "emit_runtime_manifest()",
      "def process_context(path):",
      " status=dict(line.split(':',1) for line in Path(path).read_text().splitlines() if ':' in line)",
      " return {'uid':[int(v) for v in status['Uid'].split()],'gid':[int(v) for v in status['Gid'].split()],"
        + "'effective_capabilities':status['CapEff'].strip(),'permitted_capabilities':status['CapPrm'].strip(),"
        + "'inheritable_capabilities':status['CapInh'].strip(),'no_new_privileges':int(status['NoNewPrivs'].strip())}",
      "ready=False",
      "readiness={'status':None,'elapsed_ms':0,'error':'none','proxy_configured':any(bool(value) for key,value in urllib.request.getproxies().items() if key in ('http','https','all'))}",
      "started=time.monotonic()",
      "class NoRedirect(urllib.request.HTTPRedirectHandler):",
      " def redirect_request(self,req,fp,code,msg,headers,newurl): return None",
      "try:",
      " opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())",
      " with opener.open('http://127.0.0.1:8080/api/ready',timeout=5) as response:",
      "  readiness['status']=response.status",
      "  body=response.read(1025)",
      "  if len(body)>1024: readiness['error']='body_too_large'",
      "  else:",
      "   value=json.loads(body)",
      "   if not isinstance(value,dict): raise ValueError()",
      "   ready=response.status==200 and value.get('ready') is True",
      "   if not ready: readiness['error']='not_ready' if response.status==200 else 'http_status'",
      "except urllib.error.HTTPError as error:",
      " readiness['status']=error.code;readiness['error']='http_status';error.close()",
      "except TimeoutError: readiness['error']='timeout'",
      "except urllib.error.URLError as error: readiness['error']='timeout' if isinstance(error.reason,TimeoutError) else 'url_error'",
      "except OSError: readiness['error']='network_error'",
      "except (ValueError,TypeError): readiness['error']='invalid_response'",
      "readiness['elapsed_ms']=int((time.monotonic()-started)*1000)",
      "print('IMLADRI_RUNTIME_READINESS_v1 '+json.dumps(readiness,separators=(',',':')),file=sys.stderr,flush=True)",
      "print('IMLADRI_RUNTIME_CONTEXT_v2 '+json.dumps({'main_process':process_context('/proc/1/status'),"
        + "'diagnostic_process':process_context('/proc/self/status'),'ready':ready},separators=(',',':')),flush=True)",
      "print(observe_egress(),flush=True)",
    ].join("\n");
    for (let attempt = 0; attempt < 8; attempt++) {
      try {
        const process = await container.exec(["python", "-c", program], { user: "65532:65532", cwd: "/app" });
        const output = await process.output();
        const observed = parseDiagnosticOutput(new TextDecoder().decode(output.stdout));
        if (output.exitCode === 0 && observed) {
          console.log(fingerprintPrefix + JSON.stringify(observed.manifest));
          console.log(runtimeContextPrefix + JSON.stringify(observed.context));
          const readiness = parseReadinessDiagnostic(new TextDecoder().decode(output.stderr || new Uint8Array()));
          if (readiness) console.log(readinessDiagnosticPrefix + JSON.stringify(readiness));
          return Response.json({ egress_ipv4: observed.ip }, { headers: { "Cache-Control": "no-store" } });
        }
      } catch { /* The runtime may initially be allocating the container. */ }
      await scheduler.wait(750);
    }
    return new Response(null, { status: 503, headers: { "Retry-After": "5" } });
  }

  async ensureReady() {
    const container = await this.startContainer();
    const port = container.getTcpPort(portNumber);
    const deadline = Date.now() + startupMilliseconds;
    do {
      try {
        const probe = await port.fetch("http://container/api/ready", { signal: AbortSignal.timeout(5000) });
        const reader = probe.body?.getReader();
        let ready = false;
        try {
          if (probe.status === 200 && reader) {
            const bytes = new Uint8Array(1024);
            let length = 0;
            while (true) {
              const chunk = await reader.read();
              if (chunk.done) break;
              if (length + chunk.value.byteLength > bytes.byteLength) throw new Error("Readiness body exceeds bound");
              bytes.set(chunk.value, length);
              length += chunk.value.byteLength;
            }
            const value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(0, length)));
            ready = value !== null && typeof value === "object" && !Array.isArray(value)
              && Object.hasOwn(value, "ready") && value.ready === true;
          }
        } finally {
          await reader?.cancel();
        }
        if (ready) return;
      } catch {
        // Readiness also works while ordinary API work is fenced for migration.
      }
      await scheduler.wait(250);
    } while (container.running && Date.now() < deadline);
    throw new Error("API readiness timed out");
  }

  async fetch(request) {
    if (new URL(request.url).pathname === "/api/ops/container-egress") {
      if (request.method !== "POST" || !await validBearer(request.headers.get("Authorization"), this.env.CF_EGRESS_PROBE_SECRET)) {
        return new Response(null, { status: 401, headers: { "Cache-Control": "no-store" } });
      }
      // Share concurrent observations within this single container. Return only its measured IPv4.
      this.observation ??= this.diagnoseEgress().then(async response => ({
        body: await response.text(), status: response.status
      })).finally(() => { this.observation = undefined; });
      const observed = await this.observation;
      return new Response(observed.body, { status: observed.status, headers: {
        "Content-Type": "application/json", "Cache-Control": "no-store"
      } });
    }
    const cold = !this.ctx.container.running || !this.healthy;
    const diagnostic = request.headers.get("X-Imladri-Egress-Diagnostic") === "1";
    if (diagnostic && this.env.CF_DIAG_EGRESS_ON_START !== "true") return new Response(null, { status: 404 });
    if (this.env.CF_DIAG_EGRESS_ON_START === "true" && (diagnostic || (cold && !this.starting))) {
      // Only the operator's existing bearer-protected check may initiate the diagnostic.
      const expected = this.env.OPS_CHECK_SECRET;
      const received = request.headers.get("Authorization");
      if (!expected || !received || request.method !== "POST" || new URL(request.url).pathname !== "/api/ops/check") {
        return new Response(null, { status: diagnostic ? 401 : 503 });
      }
      if (!await validBearer(received, expected)) {
        return new Response(null, { status: 401 });
      }
      if (diagnostic) return this.diagnoseEgress();
    }
    if (cold) {
      this.healthy = false;
      this.starting ??= this.ensureReady().then(() => { this.healthy = true; }).finally(() => { this.starting = undefined; });
      try {
        await this.starting;
      } catch {
        return Response.json({ detail: "API is starting. Try again shortly." }, { status: 503, headers: { "Retry-After": "5" } });
      }
    }
    const target = new URL(request.url);
    target.protocol = "http:";
    target.host = "container";
    const forwarded = new Request(target, request);
    forwarded.headers.delete("host");
    // A request with a body is only sent once. Clients own subsequent idempotent retries.
    return this.ctx.container.getTcpPort(portNumber).fetch(forwarded);
  }

}

export default {
  fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path !== "/api" && !path.startsWith("/api/")) return new Response(null, { status: 404 });
    return env.CONTAINER.getByName("personal-live", { locationHint: "enam" }).fetch(request);
  }
};
